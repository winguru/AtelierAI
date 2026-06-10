# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/clip-provider.md
# ──────────────────────────────────────────────────────────────────────────────
"""CLIP inference API endpoints.

These endpoints are part of the main AtelierAI FastAPI application.  On GPU
machines they serve requests locally via ``LocalCLIPProvider``.  On low-power
devices, the same endpoints can be called by a remote ``RemoteCLIPProvider``
on a peer instance.

Routes
------
GET  /api/clip/health           — provider status and device info
POST /api/clip/encode/images    — encode images from URLs
POST /api/clip/encode/text      — encode text strings
POST /api/clip/score/similarity — cosine similarity between vectors
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import numpy as np

from services.clip_provider import get_clip_provider, cosine_similarity

router = APIRouter(prefix="/clip", tags=["clip"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class EncodeImagesRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=64,
                            description="Image URLs to encode (CDN or peer-served)")


class EncodeTextRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=64,
                             description="Text strings to encode")


class ScoreSimilarityRequest(BaseModel):
    query_vector: list[float] = Field(..., min_length=1,
                                      description="Single query embedding")
    candidate_vectors: list[list[float]] = Field(..., min_length=1,
                                                 description="Candidate embeddings")
    metric: str = Field(default="cosine", description="Similarity metric (currently only 'cosine')")


class EmbeddingResponse(BaseModel):
    embeddings: list[list[float]]
    shape: list[int]


class ScoreResponse(BaseModel):
    scores: list[float]


class HealthResponse(BaseModel):
    status: str
    mode: str | None = None
    model: str | None = None
    pretrained: str | None = None
    device: str | None = None
    gpu_memory_used_gb: float | None = None
    peer: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _require_provider():
    """Return the active provider or raise 503."""
    provider = get_clip_provider()
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="CLIP provider is not available (no GPU and no peer configured)",
        )
    return provider


@router.get("/health", response_model=HealthResponse)
async def clip_health():
    """Return CLIP provider status."""
    provider = get_clip_provider()
    if provider is None:
        return HealthResponse(status="unavailable", mode="none")
    result = await provider.health()
    return HealthResponse(**result)


@router.post("/encode/images", response_model=EmbeddingResponse)
async def encode_images(req: EncodeImagesRequest):
    """Encode images from URLs into CLIP embeddings."""
    provider = _require_provider()
    embeddings = await provider.encode_image_urls(req.urls)
    return EmbeddingResponse(
        embeddings=embeddings.tolist(),
        shape=list(embeddings.shape),
    )


@router.post("/encode/text", response_model=EmbeddingResponse)
async def encode_text(req: EncodeTextRequest):
    """Encode text strings into CLIP embeddings."""
    provider = _require_provider()
    embeddings = await provider.encode_text(req.texts)
    return EmbeddingResponse(
        embeddings=embeddings.tolist(),
        shape=list(embeddings.shape),
    )


@router.post("/score/similarity", response_model=ScoreResponse)
async def score_similarity(req: ScoreSimilarityRequest):
    """Compute cosine similarity between a query and candidate vectors."""
    if req.metric != "cosine":
        raise HTTPException(status_code=400, detail=f"Unsupported metric: {req.metric}")

    query = np.array(req.query_vector, dtype=np.float32)
    candidates = np.array(req.candidate_vectors, dtype=np.float32)

    # L2-normalise inputs for safety
    q_norm = np.linalg.norm(query)
    if q_norm > 0:
        query = query / q_norm

    c_norms = np.linalg.norm(candidates, axis=-1, keepdims=True)
    c_norms = np.where(c_norms == 0, 1.0, c_norms)
    candidates = candidates / c_norms

    scores = cosine_similarity(query, candidates)
    return ScoreResponse(scores=scores.tolist())
