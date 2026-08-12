"""Cache-through preservation for Search Lab original media."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
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


def is_safe_preserved_path(path: Path) -> bool:
    try:
        path.resolve().relative_to(_cache_root.resolve())
    except ValueError:
        return False
    return path.is_file()
