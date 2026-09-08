"""Durable, redacted request/response archives for CivitAI calls."""

# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "cookies",
    "key",
    "meilisearch_key",
    "session",
    "session_cookie",
    "token",
}
_archive_lock = threading.Lock()


def _config_value(name: str) -> Any:
    for module_name in ("atelierai.config", "backend.config", "config"):
        try:
            module = import_module(module_name)
        except ModuleNotFoundError:
            continue
        value = getattr(module, name, None)
        if value is not None:
            return value
    return None


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_.-")
    return cleaned[:80] or "request"


def shard_parts(key: str) -> tuple[str, str]:
    """Return (level1, level2) shard dir names derived from an entity key.

    Keys are either UUID-ish hex strings (image uuid from the response URL hash)
    or ``imageid_<n>`` fallbacks. Sharding is stable and deterministic so both
    writers and all readers can compute a file's location without an index:

    - hex-ish key  → first 2 chars, next 2 chars (padded if short)
    - imageid_<n>  → zero-padded last 2 digits, previous 2 digits
    """
    key = key.strip()
    if key.startswith("imageid_"):
        digits = key[len("imageid_"):].lstrip("0") or "0"
        padded = digits.zfill(4)
        return padded[-2:], padded[-4:-2]
    hexish = re.sub(r"[^0-9a-fA-F]", "", key)[:4].lower()
    hexish = hexish.ljust(4, "0")
    return hexish[:2], hexish[2:4]


def shard_root(base: Path, key: str) -> Path:
    """Return ``base`` joined with the two shard directories for ``key``."""
    level1, level2 = shard_parts(key)
    return base / level1 / level2


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


class CivitaiResponseArchive:
    """Write sharded, redacted latest-response snapshots for CivitAI calls.

    Layout (no date/time segments; deterministic from the call itself)::

        latest/<endpoint-slug>/<key[0:2]>/<key[2:4]>/<kind>_<endpoint>_<sha16>.json

    ``history/`` writes were retired 2026-09-06 (date-keyed dirs removed by
    design; ``latest/`` snapshots are overwritten in place and remain the
    read path for cache replays).
    """

    def __init__(self, root: Path | str | None = None) -> None:
        resources = root or _config_value("IMAGE_RESOURCES_PATH") or "image_resources"
        self.root = Path(resources) / "civitai_api_responses"

    def record(
        self,
        *,
        kind: str,
        endpoint: str,
        request: Any,
        response: Any = None,
        method: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        error: str | None = None,
        queue_wait_seconds: float | None = None,
        elapsed_seconds: float | None = None,
    ) -> Path:
        now = datetime.now(timezone.utc)
        safe_request = _sanitize(request)
        request_key = hashlib.sha256(
            json.dumps(safe_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        stem = f"{_slug(kind)}_{_slug(endpoint)}_{request_key}"
        shard1, shard2 = shard_parts(request_key)
        payload = {
            "recorded_at": now.isoformat(),
            "kind": kind,
            "endpoint": endpoint,
            "method": method,
            "url": url,
            "request": safe_request,
            "success": error is None and (status_code is None or status_code < 400),
            "status_code": status_code,
            "error": error,
            "queue_wait_seconds": queue_wait_seconds,
            "elapsed_seconds": elapsed_seconds,
            "response": _sanitize(response),
        }
        latest_path = (
            self.root / "latest" / _slug(endpoint) / shard1 / shard2 / f"{stem}.json"
        )
        with _archive_lock:
            _atomic_json_write(latest_path, payload)
        return latest_path

    def read_latest(self, *, kind: str, endpoint: str, request: Any) -> dict[str, Any] | None:
        safe_request = _sanitize(request)
        request_key = hashlib.sha256(
            json.dumps(safe_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        shard1, shard2 = shard_parts(request_key)
        path = (
            self.root
            / "latest"
            / _slug(endpoint)
            / shard1
            / shard2
            / f"{_slug(kind)}_{_slug(endpoint)}_{request_key}.json"
        )
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
