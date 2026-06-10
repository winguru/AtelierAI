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
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from models import Concept
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
