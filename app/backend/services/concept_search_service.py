# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/concept-search.md
# ──────────────────────────────────────────────────────────────────────────────
"""Concept-based search pipeline.

Decomposes a natural-language query into concept identity + context text,
retrieves candidate images via ``ImageConceptObservation``, and ranks them
using batch CLIP visual scoring (prototype identity × text context).

Pipeline stages
---------------
1. **decompose_query**  — match query against concept surface forms
2. **resolve_candidate_images** — pre-filter via DB observations
3. **visual_score_candidates** — batch CLIP encode + score in numpy

Each stage is exposed independently through API endpoints for lab debugging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    Concept,
    ConceptAlias,
    ImageConceptObservation,
    ImageModel,
)
from services.clip_provider import (
    get_clip_provider,
    cosine_similarity,
    decode_prototype_from_blob,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DecomposedQuery:
    """Result of query decomposition."""
    original_query: str
    matched_concepts: list[dict] = field(default_factory=list)
    # Each dict: {"concept_id": int, "surface_form": str, "concept_name": str,
    #             "match_type": "canonical" | "alias"}
    context_text: str = ""
    total_surface_forms: int = 0


@dataclass
class ScoredCandidate:
    """A candidate image with CLIP scores."""
    image_id: int
    file_name: str
    thumbnail_url: Optional[str] = None
    source_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    identity_score: Optional[float] = None
    context_score: Optional[float] = None
    composite_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ConceptSearchService:
    """Full concept-based search pipeline.

    Typical usage::

        svc = ConceptSearchService(db)
        decomposed = svc.decompose_query("Shion on a beach")
        candidates = svc.resolve_candidate_images(decomposed.matched_concepts, limit=90)
        scored = await svc.visual_score_candidates(candidates, decomposed)
    """

    def __init__(self, db: Session):
        self._db = db

    # =======================================================================
    # Stage 1: Query decomposition
    # =======================================================================

    def decompose_query(self, query: str) -> DecomposedQuery:
        """Match a query string against all concept surface forms.

        Strategy: load all canonical names + aliases, sort by length
        descending (longest match first), then check if each surface form
        appears as a substring in the lowercased query.

        Returns a ``DecomposedQuery`` with matched concepts and remaining
        context text.
        """
        # Build surface-form index
        surface_forms = self._load_surface_forms()
        total = len(surface_forms)

        if not surface_forms or not query.strip():
            return DecomposedQuery(
                original_query=query,
                context_text=query.strip(),
                total_surface_forms=total,
            )

        q_lower = query.lower()
        matched_concept_ids: set[int] = set()
        matched_concepts: list[dict] = []
        consumed_spans: list[tuple[int, int]] = []  # (start, end) in q_lower

        # Sort by length descending → longest match first
        for sf, concept_id, concept_name, match_type in surface_forms:
            sf_lower = sf.lower()
            start = q_lower.find(sf_lower)
            if start < 0:
                continue
            if concept_id in matched_concept_ids:
                continue
            # Check overlap with already-consumed spans
            end = start + len(sf_lower)
            if any(s < end and e > start for s, e in consumed_spans):
                continue

            consumed_spans.append((start, end))
            matched_concept_ids.add(concept_id)
            matched_concepts.append({
                "concept_id": concept_id,
                "surface_form": sf,
                "concept_name": concept_name,
                "match_type": match_type,
            })

        # Extract context text (remaining unmatched text)
        context_text = self._extract_context(query, consumed_spans)

        return DecomposedQuery(
            original_query=query,
            matched_concepts=matched_concepts,
            context_text=context_text,
            total_surface_forms=total,
        )

    def _load_surface_forms(self) -> list[tuple[str, int, str, str]]:
        """Load all concept surface forms from DB.

        Returns list of (surface_form, concept_id, concept_name, match_type).
        """
        results: list[tuple[str, int, str, str]] = []

        # Canonical names
        concepts = (
            self._db.query(Concept.id, Concept.canonical_name)
            .filter(Concept.status == "active")
            .all()
        )
        for row in concepts:
            if row.canonical_name:
                results.append((row.canonical_name, row.id, row.canonical_name, "canonical"))

        # Aliases
        aliases = (
            self._db.query(ConceptAlias.alias, ConceptAlias.concept_id, Concept.canonical_name)
            .join(Concept, ConceptAlias.concept_id == Concept.id)
            .filter(Concept.status == "active")
            .all()
        )
        for row in aliases:
            if row.alias:
                results.append((row.alias, row.concept_id, row.canonical_name, "alias"))

        # Sort by length descending (longest match first)
        results.sort(key=lambda x: len(x[0]), reverse=True)
        return results

    def _extract_context(self, query: str, spans: list[tuple[int, int]]) -> str:
        """Extract unmatched text from query after consuming matched spans."""
        if not spans:
            return query.strip()

        # Sort spans by position
        sorted_spans = sorted(spans, key=lambda s: s[0])
        parts: list[str] = []
        prev_end = 0

        for start, end in sorted_spans:
            if start > prev_end:
                chunk = query[prev_end:start].strip()
                if chunk:
                    parts.append(chunk)
            prev_end = max(prev_end, end)

        # Trailing text
        if prev_end < len(query):
            chunk = query[prev_end:].strip()
            if chunk:
                parts.append(chunk)

        return " ".join(parts)

    # =======================================================================
    # Stage 2: Candidate image retrieval
    # =======================================================================

    def resolve_candidate_images(
        self,
        concept_ids: list[int],
        pool_multiplier: int = 3,
        limit: int = 30,
    ) -> list[ScoredCandidate]:
        """Find candidate images linked to the given concepts.

        Queries ``ImageConceptObservation`` for images where any of the
        specified concepts is observed as present.  Returns up to
        ``limit * pool_multiplier`` candidates for re-ranking.

        Only includes images with an accessible URL (civitai_cdn_url or
        source_url) since CLIP needs to download thumbnails.
        """
        if not concept_ids:
            return []

        max_pool = limit * pool_multiplier

        # Distinct image IDs linked to these concepts
        image_ids_q = (
            self._db.query(ImageConceptObservation.image_id)
            .filter(
                ImageConceptObservation.concept_id.in_(concept_ids),
                ImageConceptObservation.is_present.is_(True),
            )
            .distinct()
            .limit(max_pool)
            .subquery()
        )

        # Join with ImageModel for metadata
        rows = (
            self._db.query(
                ImageModel.id,
                ImageModel.file_name,
                ImageModel.civitai_cdn_url,
                ImageModel.source_url,
                ImageModel.width,
                ImageModel.height,
            )
            .filter(ImageModel.id.in_(image_ids_q))
            .all()
        )

        candidates: list[ScoredCandidate] = []
        for r in rows:
            thumbnail = r.civitai_cdn_url or r.source_url
            candidates.append(ScoredCandidate(
                image_id=r.id,
                file_name=r.file_name,
                thumbnail_url=thumbnail,
                source_url=r.source_url,
                width=r.width,
                height=r.height,
            ))

        return candidates

    # =======================================================================
    # Stage 3: Visual scoring (batch CLIP)
    # =======================================================================

    async def visual_score_candidates(
        self,
        candidates: list[ScoredCandidate],
        concept_ids: list[int],
        context_text: str = "",
        limit: int = 30,
    ) -> list[ScoredCandidate]:
        """Score candidates using batch CLIP encoding.

        1. Load prototype vectors for all matched concepts.
        2. Batch-encode all candidate thumbnails in one CLIP call.
        3. Encode context text once.
        4. Compute identity (max cos vs prototypes) and context scores.
        5. Rank by composite (identity × context, or identity alone).

        Modifies candidates in-place and returns the top ``limit`` sorted
        by composite score.
        """
        provider = get_clip_provider()

        if provider is None or not candidates:
            # No CLIP — return candidates unscored
            return candidates[:limit]

        # Load prototype vectors
        prototypes = self._load_prototype_vectors(concept_ids)
        if not prototypes:
            logger.warning("visual_score: no prototypes found for concepts %s", concept_ids)
            return candidates[:limit]

        # Build URL list for batch encoding
        urls: list[str] = []
        url_to_idx: dict[str, int] = {}
        for i, c in enumerate(candidates):
            url = c.thumbnail_url or ""
            if url:
                urls.append(url)
                url_to_idx[url] = i

        if not urls:
            return candidates[:limit]

        # Batch encode all images
        logger.info("visual_score: batch-encoding %d images", len(urls))
        img_embeddings = await provider.encode_image_urls(urls)
        if img_embeddings.shape[0] == 0:
            logger.warning("visual_score: no images encoded")
            return candidates[:limit]

        # Stack prototype vectors: shape (num_prototypes, dim)
        proto_matrix = np.stack(prototypes)

        # Encode context text once (if provided)
        context_emb: Optional[np.ndarray] = None
        if context_text.strip():
            text_results = await provider.encode_text([context_text.strip()])
            if text_results.shape[0] > 0:
                context_emb = text_results[0]

        # Score each successfully-encoded image
        for emb_idx, url in enumerate(urls):
            cand_idx = url_to_idx.get(url)
            if cand_idx is None:
                continue
            candidate = candidates[cand_idx]
            img_vec = img_embeddings[emb_idx]

            # Identity: max cosine similarity against all prototype vectors
            id_scores = cosine_similarity(img_vec, proto_matrix)
            identity = float(np.max(id_scores))
            candidate.identity_score = identity

            # Context: cosine similarity against context text embedding
            if context_emb is not None:
                ctx_score = float(cosine_similarity(context_emb, img_vec.reshape(1, -1))[0])
                candidate.context_score = ctx_score
                candidate.composite_score = identity * ctx_score
            else:
                candidate.composite_score = identity

        # Sort by composite score descending
        scored = sorted(
            candidates,
            key=lambda c: c.composite_score if c.composite_score is not None else -1.0,
            reverse=True,
        )

        return scored[:limit]

    def _load_prototype_vectors(self, concept_ids: list[int]) -> list[np.ndarray]:
        """Load prototype vectors for the given concept IDs."""
        concepts = (
            self._db.query(Concept.id, Concept.prototype_vector)
            .filter(
                Concept.id.in_(concept_ids),
                Concept.prototype_vector.isnot(None),
            )
            .all()
        )

        vectors: list[np.ndarray] = []
        for row in concepts:
            if row.prototype_vector:
                vec = decode_prototype_from_blob(row.prototype_vector)
                # L2-normalise (should already be, but safety check)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                vectors.append(vec)

        return vectors

    # =======================================================================
    # Concepts index (for lab audit)
    # =======================================================================

    def get_concepts_index(self) -> list[dict]:
        """Return all concepts with surface forms, prototype status, and
        observation counts — for the lab coverage audit."""
        # Observation counts per concept
        obs_counts = dict(
            self._db.query(
                ImageConceptObservation.concept_id,
                func.count(ImageConceptObservation.image_id.distinct()),
            )
            .filter(ImageConceptObservation.is_present.is_(True))
            .group_by(ImageConceptObservation.concept_id)
            .all()
        )

        concepts = (
            self._db.query(Concept)
            .filter(Concept.status == "active")
            .order_by(Concept.canonical_name)
            .all()
        )

        result: list[dict] = []
        for c in concepts:
            aliases = [a.alias for a in c.aliases]
            result.append({
                "concept_id": c.id,
                "canonical_name": c.canonical_name,
                "slug": c.slug,
                "concept_type": c.concept_type,
                "aliases": aliases,
                "has_prototype": c.prototype_vector is not None,
                "prototype_source_count": c.prototype_source_count,
                "prototype_updated_at": (
                    c.prototype_updated_at.isoformat()
                    if c.prototype_updated_at else None
                ),
                "observation_count": obs_counts.get(c.id, 0),
            })

        return result
