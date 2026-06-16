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
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import (
    Concept,
    ConceptAlias,
    ImageConceptObservation,
    ImageModel,
)
from config import IMAGE_LIBRARY_PATH
from services.clip_provider import (
    CLIPProvider,
    get_clip_provider,
    cosine_similarity,
    decode_prototype_from_blob,
)

logger = logging.getLogger(__name__)

# Supported image file extensions in the library
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# Soft-threshold for rescaling CLIP cosine similarities. CLIP scores are
# highly compressed (typically 0.75–0.95), so we linearly rescale scores
# so that values below _CLIP_FLOOR map to 0 and values at/above
# _CLIP_CEILING map to 1 before computing multi-concept identity.
_CLIP_FLOOR = 0.80
_CLIP_CEILING = 0.95


def _resolve_local_image_path(file_hash: Optional[str]) -> Optional[str]:
    """Resolve a local file path for *file_hash* in the image library.

    Returns the first matching file path, or ``None`` if no file is found.
    """
    if not file_hash:
        return None
    library = Path(IMAGE_LIBRARY_PATH)
    for ext in _IMAGE_EXTENSIONS:
        candidate = library / f"{file_hash}{ext}"
        if candidate.is_file():
            return str(candidate)
    return None


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
    file_hash: Optional[str] = None
    thumbnail_url: Optional[str] = None
    source_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    identity_score: Optional[float] = None
    context_score: Optional[float] = None
    composite_score: Optional[float] = None
    # Per-concept similarity scores: {concept_id: cos_sim}
    concept_scores: dict[int, float] = field(default_factory=dict)


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

            # Word-boundary check: the match must not be a substring
            # of a longer word (e.g. "w" inside "with").
            end = start + len(sf_lower)
            if start > 0 and q_lower[start - 1].isalnum():
                continue
            if end < len(q_lower) and q_lower[end].isalnum():
                continue

            # Check overlap with already-consumed spans
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
        image_ids_sub = (
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
                ImageModel.file_hash,
                ImageModel.civitai_cdn_url,
                ImageModel.source_url,
                ImageModel.width,
                ImageModel.height,
            )
            .filter(ImageModel.id.in_(select(image_ids_sub.c.image_id)))
            .all()
        )

        candidates: list[ScoredCandidate] = []
        for r in rows:
            thumbnail = r.civitai_cdn_url or r.source_url
            if not thumbnail and r.file_hash:
                thumbnail = f"/api/images/{r.file_hash}/thumb"
            candidates.append(ScoredCandidate(
                image_id=r.id,
                file_name=r.file_name,
                file_hash=r.file_hash,
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

        # Load prototype vectors keyed by concept_id
        proto_map = self._load_prototype_vectors(concept_ids)
        if not proto_map:
            logger.warning("visual_score: no prototypes found for concepts %s", concept_ids)
            return candidates[:limit]

        # Batch-encode all candidate images (local files preferred, URLs as
        # fallback for images with real CDN URLs but no local file).
        embeddings_by_idx = await self._batch_encode_candidates(provider, candidates)
        if not embeddings_by_idx:
            return candidates[:limit]

        # Build ordered concept_id / prototype-vector arrays
        concept_id_arr = list(proto_map.keys())
        proto_matrix = np.stack([proto_map[cid] for cid in concept_id_arr])

        # Encode context text once (if provided)
        context_emb: Optional[np.ndarray] = None
        if context_text.strip():
            text_results = await provider.encode_text([context_text.strip()])
            if text_results.shape[0] > 0:
                context_emb = text_results[0]

        # Score each successfully-encoded image
        # Soft threshold for per-concept scores: CLIP cosine similarities are
        # highly compressed (typically 0.75–0.95). We linearly rescale so
        # that scores below _CLIP_FLOOR map to 0 and scores at/above
        # _CLIP_CEILING map to 1. This stretches the discriminative range.
        for cand_idx, img_vec in embeddings_by_idx.items():
            candidate = candidates[cand_idx]

            # Per-concept cosine similarities
            all_sims = cosine_similarity(img_vec, proto_matrix)
            per_concept = {
                concept_id_arr[i]: float(all_sims[i])
                for i in range(len(concept_id_arr))
            }
            candidate.concept_scores = per_concept

            # Rescale each concept score via soft-thresholding
            scaled_scores = [
                max(0.0, (per_concept[cid] - _CLIP_FLOOR) / (_CLIP_CEILING - _CLIP_FLOOR))
                for cid in concept_id_arr
            ]

            # Identity: geometric mean of scaled scores for multi-concept
            # (soft-AND — penalises any weak concept), or the scaled score
            # itself for single-concept queries.
            if len(scaled_scores) > 1:
                product = 1.0
                for s in scaled_scores:
                    product *= max(s, 1e-6)
                identity = product ** (1.0 / len(scaled_scores))
            else:
                identity = scaled_scores[0]
            candidate.identity_score = identity

            # Context: cosine similarity against context text embedding
            if context_emb is not None:
                ctx_score = float(cosine_similarity(context_emb, img_vec.reshape(1, -1))[0])
                candidate.context_score = ctx_score
                # Weighted sum: identity is strongly dominant (0.9) so images
                # matching all concepts rank highest; context acts as a minor
                # tiebreaker (0.1) to differentiate images with similar identity.
                candidate.composite_score = (identity * 0.9) + (ctx_score * 0.1)
            else:
                candidate.composite_score = identity

        # Sort by composite score descending
        scored = sorted(
            candidates,
            key=lambda c: c.composite_score if c.composite_score is not None else -1.0,
            reverse=True,
        )

        return scored[:limit]

    async def _batch_encode_candidates(
        self,
        provider: CLIPProvider,
        candidates: list[ScoredCandidate],
    ) -> dict[int, np.ndarray]:
        """Batch-encode candidate images via CLIP.

        Local files are preferred (this lab stores all images locally).
        Falls back to URL encoding for candidates that have a real CDN URL
        but no local file.

        Returns a mapping of ``candidate_index → embedding``.
        """
        local_paths: list[str] = []
        path_to_idx: dict[str, int] = {}
        url_candidates: list[tuple[int, str]] = []  # (cand_idx, url)

        for i, c in enumerate(candidates):
            local = _resolve_local_image_path(c.file_hash)
            if local:
                local_paths.append(local)
                path_to_idx[local] = i
            elif c.thumbnail_url and c.thumbnail_url.startswith(("http://", "https://")):
                url_candidates.append((i, c.thumbnail_url))

        if not local_paths and not url_candidates:
            logger.warning("visual_score: no encodable images found")
            return {}

        embeddings_by_idx: dict[int, np.ndarray] = {}

        # Batch-encode local images (preferred)
        if local_paths:
            logger.info("visual_score: batch-encoding %d local images", len(local_paths))
            local_emb = await provider.encode_image_paths(local_paths)
            for emb_idx, path in enumerate(local_paths):
                embeddings_by_idx[path_to_idx[path]] = local_emb[emb_idx]

        # Batch-encode URL images (fallback for external CDN URLs)
        if url_candidates:
            urls = [u for _, u in url_candidates]
            logger.info("visual_score: batch-encoding %d URL images", len(urls))
            url_emb = await provider.encode_image_urls(urls)
            for emb_idx, (cand_idx, _) in enumerate(url_candidates):
                embeddings_by_idx[cand_idx] = url_emb[emb_idx]

        return embeddings_by_idx

    def _load_prototype_vectors(self, concept_ids: list[int]) -> dict[int, np.ndarray]:
        """Load prototype vectors for the given concept IDs.

        Returns a mapping of ``concept_id → L2-normalised vector``.
        """
        concepts = (
            self._db.query(Concept.id, Concept.prototype_vector)
            .filter(
                Concept.id.in_(concept_ids),
                Concept.prototype_vector.isnot(None),
            )
            .all()
        )

        vectors: dict[int, np.ndarray] = {}
        for row in concepts:
            if row.prototype_vector:
                vec = decode_prototype_from_blob(row.prototype_vector)
                # L2-normalise (should already be, but safety check)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                vectors[row.id] = vec

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
