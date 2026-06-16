# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/clip-provider.md
# 📄 docs: app/docs/memories/concept-prototype.md
# ──────────────────────────────────────────────────────────────────────────────
"""Concept prototype creation and visual matching service.

Uses the CLIPProvider abstraction to build visual prototypes from reference
images and score candidate images against them.  Falls back gracefully when
CLIP is unavailable (returns ``None`` scores, callers use tag-only matching).

Key operations
--------------
* ``build_prototype``     — average CLIP embeddings of reference images → centroid
* ``score_identity``      — cosine(candidate, prototype) — is this the concept?
* ``score_context``       — cosine(candidate, CLIP_text(query)) — does it match the context?
* ``score_composite``     — identity × context — combined compositional score
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Concept, ImageConceptObservation, ImageModel
from services.clip_provider import (
    get_clip_provider,
    encode_prototype_to_blob,
    decode_prototype_from_blob,
    cosine_similarity,
)

logger = logging.getLogger(__name__)


class ConceptPrototypeService:
    """Service for building and querying concept visual prototypes."""

    def __init__(self, db: Session):
        self._db = db

    # =======================================================================
    # Prototype building
    # =======================================================================

    async def build_prototype(
        self,
        concept_id: int,
        image_urls: list[str],
    ) -> Optional[np.ndarray]:
        """Build a visual prototype for a concept from reference image URLs.

        The prototype is the **centroid** (element-wise mean) of the CLIP
        embeddings of the reference images, then L2-normalised.  This
        preserves invariant features (e.g. character identity) and averages
        out variable features (e.g. pose, background).

        The prototype is stored as a BLOB on the ``Concept`` row.

        Parameters
        ----------
        concept_id : int
            The concept to update.
        image_urls : list[str]
            URLs of reference images (CDN URLs preferred).

        Returns
        -------
        np.ndarray or None
            The prototype vector (also persisted to DB), or None if CLIP is
            unavailable or no images could be encoded.
        """
        provider = get_clip_provider()
        if provider is None:
            logger.warning("build_prototype: CLIP unavailable — skipping concept %d", concept_id)
            return None

        if not image_urls:
            logger.warning("build_prototype: no URLs provided for concept %d", concept_id)
            return None

        # Encode all reference images
        embeddings = await provider.encode_image_urls(image_urls)
        if embeddings.shape[0] == 0:
            logger.warning("build_prototype: no images encoded for concept %d", concept_id)
            return None

        # Compute centroid
        centroid = embeddings.mean(axis=0)
        # L2-normalise
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        # Persist to DB
        concept = self._db.query(Concept).filter(Concept.id == concept_id).first()
        if concept is None:
            logger.error("build_prototype: concept %d not found in DB", concept_id)
            return None

        concept.prototype_vector = encode_prototype_to_blob(centroid)
        concept.prototype_source_count = embeddings.shape[0]
        concept.prototype_updated_at = datetime.now(timezone.utc)
        self._db.commit()

        logger.info(
            "build_prototype: concept %d — %d/%d images encoded, prototype stored",
            concept_id,
            embeddings.shape[0],
            len(image_urls),
        )
        return centroid

    async def _build_prototype_from_paths(
        self,
        concept_id: int,
        image_paths: list[str],
    ) -> Optional[np.ndarray]:
        """Build a visual prototype from local image file paths.

        Like ``build_prototype`` but reads from disk instead of downloading.
        """
        provider = get_clip_provider()
        if provider is None:
            logger.warning("_build_prototype_from_paths: CLIP unavailable — skipping concept %d", concept_id)
            return None

        if not image_paths:
            return None

        embeddings = await provider.encode_image_paths(image_paths)
        if embeddings.shape[0] == 0:
            logger.warning("_build_prototype_from_paths: no images encoded for concept %d", concept_id)
            return None

        # Compute centroid
        centroid = embeddings.mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm

        # Persist to DB
        concept = self._db.query(Concept).filter(Concept.id == concept_id).first()
        if concept is None:
            return None

        concept.prototype_vector = encode_prototype_to_blob(centroid)
        concept.prototype_source_count = embeddings.shape[0]
        concept.prototype_updated_at = datetime.now(timezone.utc)
        self._db.commit()

        logger.info(
            "_build_prototype_from_paths: concept %d — %d/%d images encoded",
            concept_id,
            embeddings.shape[0],
            len(image_paths),
        )
        return centroid

    # =======================================================================
    # Scoring
    # =======================================================================

    async def score_identity(
        self,
        image_url: str,
        prototype_vector: np.ndarray,
    ) -> Optional[float]:
        """Score how well a candidate image matches a concept prototype.

        Parameters
        ----------
        image_url : str
            URL of the candidate image to score.
        prototype_vector : np.ndarray
            The concept's prototype vector (L2-normalised).

        Returns
        -------
        float or None
            Cosine similarity in ``[-1, 1]``, or None if CLIP unavailable.
        """
        provider = get_clip_provider()
        if provider is None:
            return None

        embedding = await provider.encode_image_urls([image_url])
        if embedding.shape[0] == 0:
            return None

        return float(cosine_similarity(prototype_vector, embedding)[0])

    async def score_context(
        self,
        image_url: str,
        context_text: str,
    ) -> Optional[float]:
        """Score how well a candidate image matches a text context.

        Parameters
        ----------
        image_url : str
            URL of the candidate image.
        context_text : str
            Text description of the desired context (e.g. "on a beach").

        Returns
        -------
        float or None
            Cosine similarity in ``[-1, 1]``, or None if CLIP unavailable.
        """
        provider = get_clip_provider()
        if provider is None:
            return None

        img_emb = await provider.encode_image_urls([image_url])
        txt_emb = await provider.encode_text([context_text])
        if img_emb.shape[0] == 0 or txt_emb.shape[0] == 0:
            return None

        return float(cosine_similarity(txt_emb[0], img_emb)[0])

    async def score_composite(
        self,
        image_url: str,
        prototype_vector: np.ndarray,
        context_text: str,
    ) -> Optional[dict]:
        """Compute the full compositional score: identity × context.

        Returns
        -------
        dict or None
            ``{"identity": float, "context": float, "composite": float}``
            or None if CLIP unavailable.
        """
        provider = get_clip_provider()
        if provider is None:
            return None

        # Batch: encode image once, text once
        img_emb = await provider.encode_image_urls([image_url])
        txt_emb = await provider.encode_text([context_text])
        if img_emb.shape[0] == 0 or txt_emb.shape[0] == 0:
            return None

        identity = float(cosine_similarity(prototype_vector, img_emb)[0])
        context = float(cosine_similarity(txt_emb[0], img_emb)[0])
        composite = identity * context

        return {"identity": identity, "context": context, "composite": composite}

    # =======================================================================
    # DB helpers
    # =======================================================================

    def get_prototype_vector(self, concept_id: int) -> Optional[np.ndarray]:
        """Load a concept's prototype vector from the database.

        Returns
        -------
        np.ndarray or None
            The prototype vector, or None if not built yet.
        """
        concept = self._db.query(Concept).filter(Concept.id == concept_id).first()
        if concept is None or concept.prototype_vector is None:
            return None
        return decode_prototype_from_blob(concept.prototype_vector)

    # =======================================================================
    # Auto-build (from observed images)
    # =======================================================================

    def _get_observed_image_paths(
        self,
        concept_id: int,
        max_images: int = 10,
    ) -> list[str]:
        """Return local file paths of images observed as present for a concept.

        Resolves ``ImageModel.file_path`` relative to the image library
        directory.  Only returns paths that exist on disk and have a known
        image extension (video files and sidecar JSON are skipped).
        """
        import os

        _IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

        # Image library is at <app_root>/image_library/
        # This file is at <app_root>/backend/services/ — need to go up 2 levels
        image_lib = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "image_library")

        sub = (
            self._db.query(ImageConceptObservation.image_id)
            .filter(
                ImageConceptObservation.concept_id == concept_id,
                ImageConceptObservation.is_present.is_(True),
            )
            .distinct()
            .subquery()
        )

        rows = (
            self._db.query(ImageModel.id, ImageModel.file_path)
            .filter(ImageModel.id.in_(select(sub.c.image_id)))
            .filter(ImageModel.file_path.isnot(None))
            .limit(max_images * 2)  # over-fetch in case some files don't exist
            .all()
        )

        paths: list[str] = []
        for r in rows:
            full_path = os.path.join(image_lib, r.file_path)
            if (
                os.path.isfile(full_path)
                and full_path.lower().endswith(_IMAGE_EXTENSIONS)
            ):
                paths.append(full_path)
            if len(paths) >= max_images:
                break
        return paths

    async def auto_build_prototype(
        self,
        concept_id: int,
        max_images: int = 10,
    ) -> dict[str, Any]:
        """Auto-build a prototype from observed images for a concept.

        Returns
        -------
        dict
            ``{"concept_id", "concept_name", "status", "source_count",
            "message"}``
        """
        concept = self._db.query(Concept).filter(Concept.id == concept_id).first()
        if concept is None:
            return {
                "concept_id": concept_id,
                "concept_name": None,
                "status": "not_found",
                "source_count": 0,
                "message": f"Concept {concept_id} not found",
            }

        paths = self._get_observed_image_paths(concept_id, max_images)
        if not paths:
            return {
                "concept_id": concept_id,
                "concept_name": concept.canonical_name,
                "status": "no_images",
                "source_count": 0,
                "message": "No observed images with local files",
            }

        prototype = await self._build_prototype_from_paths(concept_id, paths)
        if prototype is None:
            return {
                "concept_id": concept_id,
                "concept_name": concept.canonical_name,
                "status": "clip_failed",
                "source_count": 0,
                "message": "CLIP encoding failed or unavailable",
            }

        # Refresh to get updated fields
        self._db.refresh(concept)
        return {
            "concept_id": concept_id,
            "concept_name": concept.canonical_name,
            "status": "built",
            "source_count": concept.prototype_source_count,
            "message": f"Prototype built from {concept.prototype_source_count} images",
        }

    async def batch_build_prototypes(
        self,
        concept_ids: list[int],
        max_images: int = 10,
    ) -> list[dict[str, Any]]:
        """Build prototypes for multiple concepts sequentially.

        Returns a result dict per concept (see ``auto_build_prototype``).
        """
        results: list[dict[str, Any]] = []
        for cid in concept_ids:
            result = await self.auto_build_prototype(cid, max_images)
            results.append(result)
        return results

    async def stream_build_prototypes(
        self,
        concept_ids: list[int],
        max_images: int = 10,
    ):
        """Async generator that builds prototypes and yields SSE event dicts.

        Yields ``(event_name, data_dict)`` tuples suitable for SSE streaming.

        Events:
          - ``progress``: after each concept build attempt
          - ``complete``: final summary with totals
        """
        import asyncio

        built = 0
        failed = 0
        total = len(concept_ids)

        yield ("progress", {"type": "start", "total": total})

        for idx, cid in enumerate(concept_ids, 1):
            try:
                result = await self.auto_build_prototype(cid, max_images)
                if result.get("status") == "built":
                    built += 1
                else:
                    failed += 1
                yield (
                    "progress",
                    {
                        "type": "result",
                        "index": idx,
                        "total": total,
                        "concept_id": cid,
                        "status": result.get("status", "unknown"),
                        "source_count": result.get("source_count", 0),
                        "message": result.get("message", ""),
                        "built": built,
                        "failed": failed,
                    },
                )
            except Exception as exc:
                failed += 1
                logger.exception("stream_build: failed for concept %s", cid)
                yield (
                    "progress",
                    {
                        "type": "error",
                        "index": idx,
                        "total": total,
                        "concept_id": cid,
                        "status": "error",
                        "message": str(exc),
                        "built": built,
                        "failed": failed,
                    },
                )

            # Yield control to the event loop between builds
            await asyncio.sleep(0)

        yield (
            "complete",
            {
                "type": "complete",
                "total": total,
                "built": built,
                "failed": failed,
            },
        )

    # =======================================================================
    # Prototype stats
    # =======================================================================

    def get_prototype_stats(self) -> dict[str, Any]:
        """Return global prototype coverage statistics."""
        from sqlalchemy import func

        total = self._db.query(func.count(Concept.id)).scalar()
        with_proto = (
            self._db.query(func.count(Concept.id))
            .filter(Concept.prototype_vector.isnot(None))
            .scalar()
        )

        # Concepts with observations
        obs_counts = dict(
            self._db.query(
                ImageConceptObservation.concept_id,
                func.count(ImageConceptObservation.image_id.distinct()),
            )
            .filter(ImageConceptObservation.is_present.is_(True))
            .group_by(ImageConceptObservation.concept_id)
            .all()
        )
        with_observations = len(obs_counts)

        # Bracket breakdown: how many concepts have N observations
        brackets = {
            "1_to_5": 0,
            "6_to_20": 0,
            "21_to_50": 0,
            "51_to_100": 0,
            "101_plus": 0,
        }
        for count in obs_counts.values():
            if count <= 5:
                brackets["1_to_5"] += 1
            elif count <= 20:
                brackets["6_to_20"] += 1
            elif count <= 50:
                brackets["21_to_50"] += 1
            elif count <= 100:
                brackets["51_to_100"] += 1
            else:
                brackets["101_plus"] += 1

        # Count prototypes in each bracket
        proto_brackets = {
            "1_to_5": 0,
            "6_to_20": 0,
            "21_to_50": 0,
            "51_to_100": 0,
            "101_plus": 0,
        }
        concept_ids_with_proto = set(
            row[0]
            for row in self._db.query(Concept.id)
            .filter(Concept.prototype_vector.isnot(None))
            .all()
        )
        for cid, count in obs_counts.items():
            if cid in concept_ids_with_proto:
                if count <= 5:
                    proto_brackets["1_to_5"] += 1
                elif count <= 20:
                    proto_brackets["6_to_20"] += 1
                elif count <= 50:
                    proto_brackets["21_to_50"] += 1
                elif count <= 100:
                    proto_brackets["51_to_100"] += 1
                else:
                    proto_brackets["101_plus"] += 1

        return {
            "total_concepts": total,
            "with_observations": with_observations,
            "with_prototypes": with_proto,
            "observation_brackets": brackets,
            "prototype_brackets": proto_brackets,
        }
