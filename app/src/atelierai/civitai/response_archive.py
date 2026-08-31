"""Durable, redacted request/response archives for CivitAI calls."""

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
    """Write immutable history records and stable latest snapshots."""

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
        history_path = (
            self.root
            / "history"
            / now.strftime("%Y-%m-%d")
            / f"{stem}_{now.strftime('%H%M%S_%f')}_{uuid4().hex[:8]}.json"
        )
        latest_path = self.root / "latest" / f"{stem}.json"
        with _archive_lock:
            _atomic_json_write(history_path, payload)
            _atomic_json_write(latest_path, payload)
        return history_path

    def read_latest(self, *, kind: str, endpoint: str, request: Any) -> dict[str, Any] | None:
        safe_request = _sanitize(request)
        request_key = hashlib.sha256(
            json.dumps(safe_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        path = self.root / "latest" / f"{_slug(kind)}_{_slug(endpoint)}_{request_key}.json"
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
