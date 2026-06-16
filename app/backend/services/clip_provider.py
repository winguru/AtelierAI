# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/clip-provider.md
# ──────────────────────────────────────────────────────────────────────────────
"""CLIP inference provider abstraction.

Implements the Provider pattern: a unified ``CLIPProvider`` interface that hides
whether inference runs locally (OpenCLIP in-process) or remotely (HTTP to a
peer AtelierAI instance).

Startup auto-detection (in ``main.py``) picks the right implementation:

* GPU available  → ``LocalCLIPProvider``  (fast, ~5 ms/image on RTX 3090)
* No GPU + peer  → ``RemoteCLIPProvider`` (forwards to GPU peer)
* Neither        → provider is ``None``   (graceful degradation)

Usage in services::

    from services.clip_provider import get_clip_provider

    provider = get_clip_provider()
    if provider is None:
        return None  # CLIP unavailable — degrade to tag-only

    embeddings = await provider.encode_image_urls(["https://..."])
"""

from __future__ import annotations

import io
import logging
import struct
from typing import Optional, Protocol, runtime_checkable

import httpx
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global provider singleton — set once during application startup.
# ---------------------------------------------------------------------------
_clip_provider: Optional[CLIPProvider] = None


def get_clip_provider() -> Optional[CLIPProvider]:
    """Return the active CLIP provider, or ``None`` if CLIP is unavailable."""
    return _clip_provider


def set_clip_provider(provider: Optional[CLIPProvider]) -> None:
    """Set the global CLIP provider.  Called once during ``lifespan`` startup."""
    global _clip_provider
    _clip_provider = provider


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 512  # ViT-B/32 output dimensionality


def cosine_similarity(query: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a single query and N candidates.

    Parameters
    ----------
    query : np.ndarray, shape ``(D,)``
        L2-normalised query vector.
    candidates : np.ndarray, shape ``(N, D)``
        L2-normalised candidate matrix.

    Returns
    -------
    np.ndarray, shape ``(N,)``
        Similarity scores in ``[-1, 1]``.
    """
    if query.ndim != 1:
        raise ValueError(f"query must be 1-D, got shape {query.shape}")
    if candidates.ndim != 2:
        raise ValueError(f"candidates must be 2-D, got shape {candidates.shape}")
    # With L2-normalised vectors, cosine similarity = dot product.
    return candidates @ query


def encode_prototype_to_blob(vector: np.ndarray) -> bytes:
    """Serialise a float32 embedding vector to a BLOB for SQLite storage."""
    return struct.pack(f"{len(vector)}f", *vector.astype(np.float32))


def decode_prototype_from_blob(blob: bytes) -> np.ndarray:
    """Deserialise a BLOB back to a float32 numpy vector."""
    count = len(blob) // 4
    return np.array(struct.unpack(f"{count}f", blob), dtype=np.float32)


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CLIPProvider(Protocol):
    """Interface for CLIP inference — local or remote."""

    async def encode_image_urls(self, urls: list[str]) -> np.ndarray:
        """Encode images from URLs into L2-normalised embeddings.

        Returns
        -------
        np.ndarray, shape ``(N, 512)``
        """
        ...

    async def encode_image_paths(self, paths: list[str]) -> np.ndarray:
        """Encode images from local file paths into L2-normalised embeddings.

        Returns
        -------
        np.ndarray, shape ``(N, 512)``
        """
        ...

    async def encode_text(self, texts: list[str]) -> np.ndarray:
        """Encode text strings into L2-normalised embeddings.

        Returns
        -------
        np.ndarray, shape ``(N, 512)``
        """
        ...

    async def health(self) -> dict:
        """Return a health/status dict for monitoring."""
        ...


# ---------------------------------------------------------------------------
# Local provider (OpenCLIP in-process)
# ---------------------------------------------------------------------------


class LocalCLIPProvider:
    """In-process CLIP using OpenCLIP directly.

    Works on GPU (fast) or CPU (slow but functional).  Proves the pipeline
    works on a single device during development.
    """

    def __init__(self, model_name: str, pretrained: str, force_cpu: bool = False):
        import open_clip
        import torch

        self._torch = torch
        self._model_name = model_name
        self._pretrained = pretrained

        device_str = "cpu" if force_cpu else ("cuda" if torch.cuda.is_available() else "cpu")
        self._device = torch.device(device_str)

        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        self._model.to(self._device)
        self._model.eval()

        self._tokenizer = open_clip.get_tokenizer(model_name)

        vram_gb: Optional[float] = None
        if self._device.type == "cuda":
            vram_gb = torch.cuda.memory_allocated(self._device) / 1e9

        logger.info(
            "LocalCLIPProvider ready: model=%s pretrained=%s device=%s vram=%.2fGB",
            model_name,
            pretrained,
            device_str,
            vram_gb or 0,
        )

    # -- image encoding -----------------------------------------------------

    async def encode_image_urls(self, urls: list[str]) -> np.ndarray:
        """Download images and encode them in a single batch."""
        import torch

        images: list[Image.Image] = []
        for url in urls:
            img = await _download_image(url)
            if img is not None:
                images.append(img)

        if not images:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

        tensors = [self._preprocess(img) for img in images]
        batch = torch.stack(tensors).to(self._device)

        with torch.no_grad():
            features = self._model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy().astype(np.float32)

    async def encode_image_paths(self, paths: list[str]) -> np.ndarray:
        """Load images from local file paths and encode them in a single batch."""
        import torch

        _IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

        images: list[Image.Image] = []
        for path in paths:
            if not path.lower().endswith(_IMAGE_EXTENSIONS):
                logger.debug("Skipping non-image file: %s", path)
                continue
            try:
                img = Image.open(path).convert("RGB")
                images.append(img)
            except Exception as exc:
                logger.warning("Failed to load local image %s: %s", path, exc)

        if not images:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

        tensors = [self._preprocess(img) for img in images]
        batch = torch.stack(tensors).to(self._device)

        with torch.no_grad():
            features = self._model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy().astype(np.float32)

    # -- text encoding ------------------------------------------------------

    async def encode_text(self, texts: list[str]) -> np.ndarray:
        """Encode text strings into L2-normalised embeddings."""
        import torch

        if not texts:
            return np.empty((0, EMBEDDING_DIM), dtype=np.float32)

        tokens = self._tokenizer(texts).to(self._device)

        with torch.no_grad():
            features = self._model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy().astype(np.float32)

    # -- health -------------------------------------------------------------

    async def health(self) -> dict:
        gpu_mem: Optional[float] = None
        if self._device.type == "cuda":
            gpu_mem = self._torch.cuda.memory_allocated(self._device) / 1e9
        return {
            "status": "ok",
            "mode": "local",
            "model": self._model_name,
            "pretrained": self._pretrained,
            "device": str(self._device),
            "gpu_memory_used_gb": gpu_mem,
        }


# ---------------------------------------------------------------------------
# Remote provider (HTTP client to peer AtelierAI)
# ---------------------------------------------------------------------------


class RemoteCLIPProvider:
    """Forwards CLIP requests to a peer AtelierAI instance.

    Used on low-power devices (e.g. Pi 5) that delegate inference to a GPU
    peer running the same AtelierAI codebase.
    """

    def __init__(self, peer_url: str):
        self._peer_url = peer_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._peer_url,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        logger.info("RemoteCLIPProvider ready: peer=%s", self._peer_url)

    async def encode_image_urls(self, urls: list[str]) -> np.ndarray:
        resp = await self._http.post(
            "/api/clip/encode/images",
            json={"urls": urls},
        )
        resp.raise_for_status()
        data = resp.json()
        return np.array(data["embeddings"], dtype=np.float32)

    async def encode_text(self, texts: list[str]) -> np.ndarray:
        resp = await self._http.post(
            "/api/clip/encode/text",
            json={"texts": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return np.array(data["embeddings"], dtype=np.float32)

    async def health(self) -> dict:
        try:
            resp = await self._http.get("/api/clip/health")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            return {"status": "error", "mode": "remote", "peer": self._peer_url, "error": str(exc)}

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Image download helper
# ---------------------------------------------------------------------------

_http_client: Optional[httpx.AsyncClient] = None


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    return _http_client


async def _download_image(url: str) -> Optional[Image.Image]:
    """Download an image from a URL and return a PIL Image, or None on failure.

    Handles absolute HTTP/HTTPS URLs and local API paths (``/api/...``).
    Local API paths are resolved against ``http://localhost:8000``.
    """
    # Resolve local API paths to full localhost URLs
    fetch_url = url
    if url.startswith("/api/"):
        fetch_url = f"http://localhost:8000{url}"

    client = await _get_http_client()
    try:
        resp = await client.get(fetch_url, follow_redirects=True)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception as exc:
        logger.warning("Failed to download image %s: %s", url, exc)
        return None


async def close_http_client() -> None:
    """Clean up the shared HTTP client.  Called during shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
