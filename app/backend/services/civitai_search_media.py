"""Cache-through preservation for Search Lab original media."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import atelierai.config as app_config
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.http_client import CivitaiRequestError

_cache_root = Path(app_config.IMAGE_RESOURCES_PATH) / "civitai_search_media"
_locks_guard = threading.Lock()
_image_locks: dict[int, threading.Lock] = {}
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}")
_ALLOWED_MEDIA_HOSTS = {
    "image.civitai.com",
    "image.civitai.red",
    "image-b2.civitai.com",
}


@dataclass(frozen=True)
class PreservedSearchMedia:
    image_id: int
    relative_path: str
    mime_type: str
    sha256: str
    source_url: str
    size: int
    saved_at: str

    @property
    def absolute_path(self) -> Path:
        return Path(app_config.IMAGE_RESOURCES_PATH) / self.relative_path


def _image_lock(image_id: int) -> threading.Lock:
    with _locks_guard:
        return _image_locks.setdefault(image_id, threading.Lock())


def _sidecar_path(image_id: int) -> Path:
    return _cache_root / str(image_id) / "media.json"


def _load_preserved(image_id: int) -> PreservedSearchMedia | None:
    try:
        payload = json.loads(_sidecar_path(image_id).read_text(encoding="utf-8"))
        record = PreservedSearchMedia(**payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return record if record.absolute_path.is_file() else None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _uuid_from_metadata(metadata: dict[str, Any]) -> str | None:
    direct = str(metadata.get("uuid") or "").strip()
    if direct and "/" not in direct:
        return direct
    for value in (direct, metadata.get("url"), metadata.get("image_url")):
        match = _UUID_RE.search(str(value or ""))
        if match:
            return match.group(0)
    return None


def _candidate_urls(metadata: dict[str, Any]) -> list[str]:
    candidates = [
        str(metadata.get("preserved_source_url") or "").strip(),
        str(metadata.get("image_url") or metadata.get("url") or "").strip(),
    ]
    uuid = _uuid_from_metadata(metadata)
    if uuid:
        cdn = getattr(
            app_config,
            "CIVITAI_CDN_BASE_URL",
            "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
        )
        name = str(metadata.get("name") or metadata.get("file_name") or uuid).rsplit("/", 1)[-1]
        candidates.append(f"{cdn}/{uuid}/original=true/{name}")
        for width in (2048, 1600, 1200, 1024, 800, 450):
            candidates.append(f"{cdn}/{uuid}/width={width}/{name}")
        alternate = getattr(
            app_config,
            "CIVITAI_CDN_ALT_BASE_URL",
            "https://image-b2.civitai.com",
        )
        candidates.append(f"{alternate}/file/civitai-media-cache/{uuid}/original")
    return list(
        dict.fromkeys(
            url
            for url in candidates
            if url and _is_allowed_media_url(url)
        )
    )


def _is_allowed_media_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and (parsed.hostname or "").lower() in _ALLOWED_MEDIA_HOSTS
    )


_SUFFIX_BY_MIME: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def _normalize_media_mime(content_type: str) -> str:
    """Normalize a Content-Type header value to a bare media mime type."""
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return base if base in _SUFFIX_BY_MIME else ""


_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "video/webm"),  # refined below via WEBP/label check
)


def _sniff_media_ok(data: bytes, mime_type: str) -> bool:
    """True when ``data`` starts with a plausible magic for ``mime_type``.

    Guards against caching HTML error pages / JSON blobs served with a
    media Content-Type by intermediaries.
    """
    if not data:
        return False
    head = data[:16]
    if mime_type == "image/png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return head.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    if mime_type == "image/gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if mime_type == "video/mp4":
        return len(data) > 12 and head[4:8] == b"ftyp"
    if mime_type == "video/webm":
        return head.startswith(b"\x1aE\xdf\xa3")
    if mime_type == "image/avif":
        return head.startswith(b"\x00\x00\x00") and b"ftyp" in data[:32]
    return False  # unknown mime — refuse


def _detect_media(path: Path) -> tuple[str, str]:
    header = path.read_bytes()[:32]
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"avif", b"avis"}:
            return "image/avif", ".avif"
        return "video/mp4", ".mp4"
    if header.startswith(b"\x1aE\xdf\xa3"):
        return "video/webm", ".webm"
    raise ValueError("Downloaded CivitAI media has an unsupported file signature")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preserve_search_media(
    image_id: int,
    metadata: dict[str, Any],
) -> PreservedSearchMedia:
    """Return a local original, downloading and validating it once if needed."""
    with _image_lock(image_id):
        existing = _load_preserved(image_id)
        if existing is not None:
            return existing

        candidates = _candidate_urls(metadata)
        if not candidates:
            raise ValueError("CivitAI image metadata has no downloadable URL")

        directory = _cache_root / str(image_id)
        directory.mkdir(parents=True, exist_ok=True)
        client = CivitaiAPI.get_instance().http_client
        last_error: Exception | None = None
        for source_url in candidates:
            temporary: Path | None = None
            try:
                temporary = client.download_to_temp(
                    source_url,
                    output_dir=directory,
                    prefix="download_",
                    suffix=".bin",
                )
                mime_type, suffix = _detect_media(temporary)
                checksum = _sha256(temporary)
                destination = directory / f"original{suffix}"
                os.replace(temporary, destination)
                record = PreservedSearchMedia(
                    image_id=image_id,
                    relative_path=str(destination.relative_to(app_config.IMAGE_RESOURCES_PATH)),
                    mime_type=mime_type,
                    sha256=checksum,
                    source_url=source_url,
                    size=destination.stat().st_size,
                    saved_at=datetime.now(timezone.utc).isoformat(),
                )
                _atomic_json(_sidecar_path(image_id), asdict(record))
                return record
            except (CivitaiRequestError, OSError, ValueError) as exc:
                last_error = exc
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

        raise CivitaiRequestError(
            f"Could not preserve CivitAI image {image_id}: {last_error}",
            status_code=getattr(last_error, "status_code", None),
            retryable=getattr(last_error, "retryable", False),
        )


def get_preserved_search_media(image_id: int) -> PreservedSearchMedia | None:
    return _load_preserved(image_id)


def preserve_search_media_via_bridge(
    image_id: int,
    metadata: dict[str, Any],
) -> PreservedSearchMedia | None:
    """Preserve an original through the CDP sidecar browser session.

    The browser lane carries the real Chromium TLS fingerprint and session
    cookies, and — critically — fetches EXACTLY ONE url: the original the
    user is already viewing in fullscreen (no candidate-URL ladder, so the
    historical 9-URL preserve floods cannot recur). Returns None (never
    raises) when the bridge is unavailable or the fetch fails — callers
    fall back to the plain CDN path only on explicit user Save.
    """
    # Respect the existing cache — a hit means no fetch at all.
    existing = _load_preserved(image_id)
    if existing is not None:
        return existing

    # Single URL: build the CDN original from the uuid (the same URL the
    # fullscreen preview displays — never a candidate ladder).
    uuid = _uuid_from_metadata(metadata)
    if not uuid:
        return None
    cdn = getattr(
        app_config,
        "CIVITAI_CDN_BASE_URL",
        "https://image.civitai.com/xG1nkqKTMzGDvpLrqDT7WA",
    )
    name = str(metadata.get("name") or metadata.get("file_name") or uuid).rsplit("/", 1)[-1]
    url = f"{cdn}/{uuid}/original=true/{name}"
    if not _is_allowed_media_url(url):
        return None

    import asyncio

    from atelierai.civitai.browser_bridge import get_browser_bridge

    async def _fetch() -> dict[str, Any] | None:
        bridge = get_browser_bridge()
        return await bridge.fetch_binary(url)

    try:
        result = asyncio.run(_fetch())
    except Exception:  # noqa: BLE001 — browser lane is best-effort
        return None
    if not isinstance(result, dict) or not result.get("ok"):
        return None

    data: bytes = result.get("data") or b""
    mime_type = _normalize_media_mime(result.get("content_type") or "")
    if not data or not mime_type:
        return None

    # Verify the bytes actually decode as the claimed media type before
    # caching (guards against error pages / HTML bodies).
    suffix = _SUFFIX_BY_MIME.get(mime_type, ".bin")
    if not _sniff_media_ok(data, mime_type):
        return None

    with _image_lock(image_id):
        # Re-check under the lock — a concurrent preserve may have won.
        existing = _load_preserved(image_id)
        if existing is not None:
            return existing

        directory = _cache_root / str(image_id)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"original{suffix}"
        destination.write_bytes(data)
        import hashlib as _hashlib

        record = PreservedSearchMedia(
            image_id=image_id,
            relative_path=str(destination.relative_to(app_config.IMAGE_RESOURCES_PATH)),
            mime_type=mime_type,
            sha256=_hashlib.sha256(data).hexdigest(),
            source_url=url,
            size=len(data),
            saved_at=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(_sidecar_path(image_id), asdict(record))

        # Mirror the path/mime columns onto the search-images row so the
        # review view serves preserved bytes immediately.
        try:
            from database import SessionLocal
            from models import CivitaiSearchImage

            with SessionLocal() as db:
                row = (
                    db.query(CivitaiSearchImage)
                    .filter(CivitaiSearchImage.civitai_image_id == image_id)
                    .first()
                )
                if row is not None:
                    row.preserved_media_path = record.relative_path
                    row.preserved_media_mime = record.mime_type
                    row.preserved_media_sha256 = record.sha256
                    row.preserved_source_url = record.source_url
                    db.commit()
        except Exception:  # noqa: BLE001, S110 — DB mirror is best-effort
            pass
        return record


def record_ingested_media(
    image_id: int,
    media_path: Path,
    mime_type: str,
    sha256: str,
    source_url: str,
) -> PreservedSearchMedia:
    """Write-through: register an already-downloaded/ingested asset in the cache.

    Used by the Sync Lab ingest pipeline so a download performed for library
    ingestion is immediately reusable as a cache hit for Search Lab views,
    review flows, and any later re-download — without a second CDN fetch.
    Copies the bytes under the per-image cache dir (the ingest pipeline MOVES
    its temp file into the library, so a reference would dangle).
    """
    with _image_lock(image_id):
        existing = _load_preserved(image_id)
        if existing is not None:
            return existing

        directory = _cache_root / str(image_id)
        directory.mkdir(parents=True, exist_ok=True)
        suffix = Path(media_path).suffix.lower() or ".bin"
        destination = directory / f"original{suffix}"
        shutil.copy2(media_path, destination)
        record = PreservedSearchMedia(
            image_id=image_id,
            relative_path=str(destination.relative_to(app_config.IMAGE_RESOURCES_PATH)),
            mime_type=mime_type,
            sha256=sha256,
            source_url=source_url,
            size=destination.stat().st_size,
            saved_at=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_json(_sidecar_path(image_id), asdict(record))
        return record


def get_cached_media_path(image_id: int) -> tuple[Path, str] | None:
    """Return (cached_media_path, mime_type) for an image, or None on miss.

    Ingest-side read accessor: a hit means the bytes are already on disk and
    verified (sidecar sha256 recorded at preserve/record time), so the CDN
    download can be skipped entirely.
    """
    record = _load_preserved(image_id)
    if record is None:
        return None
    return record.absolute_path, record.mime_type


def is_safe_preserved_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(_cache_root.resolve())
    except ValueError:
        return False
    return path.is_file()
