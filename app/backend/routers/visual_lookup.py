"""Visual lookup router — kNN concept association via CLIP embeddings.

Provides endpoints to:
  * **Lookup** neighbours: find images visually similar to a seed image and
    return their aggregated concepts.
  * **Hydrate**: persist a VISUAL_CLIP observation linking an image to a concept.
  * **Score concept**: cosine similarity between an image's embedding and a
    concept's prototype vector.

These endpoints power the concept-association studio's ability to work with
ground-zero images (no existing tags) by leveraging CLIP visual similarity.

── Memory ───────────────────────────────────────────────────────────────────
📄 docs: app/docs/memories/concept-association.md
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import struct
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Concept,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
)
from services.clip_provider import cosine_similarity, decode_prototype_from_blob

router = APIRouter()

# Default number of nearest neighbours to return
_DEFAULT_K = 20
# Minimum cosine similarity threshold for considering a neighbour
_MIN_SIMILARITY = 0.75


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class NeighbourConcept(BaseModel):
    concept_id: int
    canonical_name: str
    concept_type: str | None = None
    match_count: int
    max_confidence: float | None = None


class VisualNeighbour(BaseModel):
    image_id: int
    file_hash: str
    similarity: float
    concepts: list[NeighbourConcept]


class VisualLookupResponse(BaseModel):
    file_hash: str
    query_image_id: int
    k: int
    has_embedding: bool
    neighbours: list[VisualNeighbour]
    aggregated_concepts: list[NeighbourConcept]


class HydrateRequest(BaseModel):
    concept_id: int = Field(..., description="Concept to associate with the image")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="Optional confidence override")
    is_curated: bool = Field(default=True, description="Mark as user-curated")
    certainty_label: int = Field(default=ObservationCertainty.LIKELY, description="ObservationCertainty enum value")


class HydrateResponse(BaseModel):
    message: str
    observation_id: int
    image_id: int
    concept_id: int
    source_type: str


class ScoreConceptRequest(BaseModel):
    concept_id: int = Field(..., description="Concept whose prototype to score against")


class ScoreConceptResponse(BaseModel):
    file_hash: str
    image_id: int
    concept_id: int
    concept_name: str
    cosine_similarity: float
    has_embedding: bool
    has_prototype: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_image_embedding(raw: bytes | None) -> np.ndarray | None:
    """Decode a raw BLOB embedding into a 1-D float32 numpy array."""
    if raw is None:
        return None
    try:
        count = len(raw) // 4
        return np.array(struct.unpack(f"{count}f", raw), dtype=np.float32)
    except (struct.error, ValueError):
        return None


def _load_all_embeddings(db: Session) -> list[tuple[int, str, np.ndarray]]:
    """Load all image embeddings from the database.

    Returns a list of ``(image_id, file_hash, embedding)`` tuples.
    """
    rows = (
        db.query(ImageModel.id, ImageModel.file_hash, ImageModel.clip_embedding)
        .filter(ImageModel.clip_embedding.isnot(None))
        .all()
    )
    result = []
    for img_id, file_hash, raw in rows:
        vec = _decode_image_embedding(raw)
        if vec is not None:
            result.append((img_id, file_hash, vec))
    return result


def _aggregate_concepts_for_image(db: Session, image_id: int) -> list[NeighbourConcept]:
    """Get aggregated concepts for a single image's observations."""
    obs_rows = (
        db.query(
            Concept.id,
            Concept.canonical_name,
            Concept.concept_type,
            ImageConceptObservation.confidence,
        )
        .join(ImageConceptObservation, ImageConceptObservation.concept_id == Concept.id)
        .filter(ImageConceptObservation.image_id == image_id)
        .filter(ImageConceptObservation.is_present.is_(True))
        .all()
    )

    concept_map: dict[int, dict[str, Any]] = {}
    for concept_id, name, ctype, confidence in obs_rows:
        if concept_id not in concept_map:
            concept_map[concept_id] = {
                "concept_id": concept_id,
                "canonical_name": name,
                "concept_type": ctype,
                "match_count": 0,
                "max_confidence": confidence,
            }
        concept_map[concept_id]["match_count"] += 1
        if confidence is not None:
            current_max = concept_map[concept_id]["max_confidence"]
            if current_max is None or confidence > current_max:
                concept_map[concept_id]["max_confidence"] = confidence

    return [NeighbourConcept(**v) for v in concept_map.values()]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/visual-lookup/{file_hash}",
    response_model=VisualLookupResponse,
    summary="kNN visual lookup — find similar images and their concepts",
)
def visual_lookup(
    file_hash: str,
    k: int = _DEFAULT_K,
    db: Session = Depends(get_db),
):
    """Find the *k* nearest neighbours to the image identified by ``file_hash``.

    Returns each neighbour's similarity score and its aggregated concepts.
    This is the core kNN lookup for the concept-association studio.
    """
    query_image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
    if query_image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    query_vec = _decode_image_embedding(query_image.clip_embedding)
    if query_vec is None:
        return VisualLookupResponse(
            file_hash=file_hash,
            query_image_id=query_image.id,
            k=k,
            has_embedding=False,
            neighbours=[],
            aggregated_concepts=[],
        )

    # Load all embeddings and compute similarities
    all_embeddings = _load_all_embeddings(db)
    if not all_embeddings:
        return VisualLookupResponse(
            file_hash=file_hash,
            query_image_id=query_image.id,
            k=k,
            has_embedding=True,
            neighbours=[],
            aggregated_concepts=[],
        )

    # Build candidate matrix (exclude the query image itself)
    candidates = [(img_id, fh) for img_id, fh, _ in all_embeddings if img_id != query_image.id]
    if not candidates:
        return VisualLookupResponse(
            file_hash=file_hash,
            query_image_id=query_image.id,
            k=k,
            has_embedding=True,
            neighbours=[],
            aggregated_concepts=[],
        )

    candidate_matrix = np.array([vec for _, _, vec in all_embeddings if _[0] != query_image.id], dtype=np.float32)
    sims = cosine_similarity(query_vec, candidate_matrix)

    # Rank by similarity (descending) and take top-k
    ranked_indices = np.argsort(sims)[::-1][:k]

    neighbours: list[VisualNeighbour] = []
    agg_concept_map: dict[int, dict[str, Any]] = {}

    for idx in ranked_indices:
        sim = float(sims[idx])
        if sim < _MIN_SIMILARITY:
            continue

        nb_image_id, nb_file_hash = candidates[idx]
        nb_concepts = _aggregate_concepts_for_image(db, nb_image_id)

        neighbours.append(VisualNeighbour(
            image_id=nb_image_id,
            file_hash=nb_file_hash,
            similarity=sim,
            concepts=nb_concepts,
        ))

        # Merge into global aggregation
        for nc in nb_concepts:
            if nc.concept_id not in agg_concept_map:
                agg_concept_map[nc.concept_id] = {
                    "concept_id": nc.concept_id,
                    "canonical_name": nc.canonical_name,
                    "concept_type": nc.concept_type,
                    "match_count": 0,
                    "max_confidence": nc.max_confidence,
                }
            agg_concept_map[nc.concept_id]["match_count"] += nc.match_count
            current_max = agg_concept_map[nc.concept_id]["max_confidence"]
            if nc.max_confidence is not None:
                if current_max is None or nc.max_confidence > current_max:
                    agg_concept_map[nc.concept_id]["max_confidence"] = nc.max_confidence

    aggregated = sorted(
        [NeighbourConcept(**v) for v in agg_concept_map.values()],
        key=lambda c: c.match_count,
        reverse=True,
    )

    return VisualLookupResponse(
        file_hash=file_hash,
        query_image_id=query_image.id,
        k=k,
        has_embedding=True,
        neighbours=neighbours,
        aggregated_concepts=aggregated,
    )


@router.post(
    "/visual-lookup/{file_hash}/hydrate",
    response_model=HydrateResponse,
    summary="Persist a VISUAL_CLIP concept observation",
)
def hydrate_observation(
    file_hash: str,
    payload: HydrateRequest,
    db: Session = Depends(get_db),
):
    """Create or update an ``ImageConceptObservation`` with ``source_type=VISUAL_CLIP``.

    This is how the studio records that a user confirmed a concept applies to
    a ground-zero image, based on visual similarity evidence.
    """
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    concept = db.query(Concept).filter(Concept.id == payload.concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Check for existing observation (same image + concept + no authority)
    existing = (
        db.query(ImageConceptObservation)
        .filter(
            ImageConceptObservation.image_id == image.id,
            ImageConceptObservation.concept_id == payload.concept_id,
            ImageConceptObservation.authority_id.is_(None),
        )
        .first()
    )

    if existing:
        existing.source_type = ObservationSource.VISUAL_CLIP
        existing.is_curated = payload.is_curated
        existing.is_present = True
        if payload.confidence is not None:
            existing.confidence = payload.confidence
        existing.certainty_label = payload.certainty_label
        db.commit()
        db.refresh(existing)
        obs = existing
    else:
        obs = ImageConceptObservation(
            image_id=image.id,
            concept_id=payload.concept_id,
            source_type=ObservationSource.VISUAL_CLIP,
            authority_id=None,
            is_present=True,
            is_curated=payload.is_curated,
            confidence=payload.confidence,
            certainty_label=payload.certainty_label,
        )
        db.add(obs)
        db.commit()
        db.refresh(obs)

    return HydrateResponse(
        message="Observation hydrated",
        observation_id=obs.id,
        image_id=image.id,
        concept_id=payload.concept_id,
        source_type="VISUAL_CLIP",
    )


@router.post(
    "/visual-lookup/{file_hash}/score-concept",
    response_model=ScoreConceptResponse,
    summary="Score an image against a concept prototype",
)
def score_concept(
    file_hash: str,
    payload: ScoreConceptRequest,
    db: Session = Depends(get_db),
):
    """Compute cosine similarity between an image's CLIP embedding and a concept's prototype vector.

    Useful for evaluating how strongly a concept applies to a ground-zero image.
    """
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    concept = db.query(Concept).filter(Concept.id == payload.concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    query_vec = _decode_image_embedding(image.clip_embedding)
    if query_vec is None:
        return ScoreConceptResponse(
            file_hash=file_hash,
            image_id=image.id,
            concept_id=concept.id,
            concept_name=concept.canonical_name,
            cosine_similarity=0.0,
            has_embedding=False,
            has_prototype=concept.prototype_embedding is not None,
        )

    proto_vec = decode_prototype_from_blob(concept.prototype_embedding)
    if proto_vec is None:
        return ScoreConceptResponse(
            file_hash=file_hash,
            image_id=image.id,
            concept_id=concept.id,
            concept_name=concept.canonical_name,
            cosine_similarity=0.0,
            has_embedding=True,
            has_prototype=False,
        )

    sim = float(cosine_similarity(query_vec, proto_vec.reshape(1, -1))[0])

    return ScoreConceptResponse(
        file_hash=file_hash,
        image_id=image.id,
        concept_id=concept.id,
        concept_name=concept.canonical_name,
        cosine_similarity=round(sim, 6),
        has_embedding=True,
        has_prototype=True,
    )
