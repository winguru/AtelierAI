# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Non-blocking JSONL transport log for CivitAI HTTP requests.

Records one line per dispatched request (aggregating retries into a single
record) so throttling behavior can be investigated offline: latency
percentiles, 503/429 correlation with observed RPM, queue waits, and pacing
effects. Complements ``response_archive`` (which snapshots full payloads for
tRPC calls) by covering *all* request types — including CDN downloads — with
timing telemetry.

Design constraints:
- Must never slow or break the consumer thread: every write goes through a
  buffered background writer; recording failures are swallowed after one
  console warning.
- Files are daily JSONL under ``IMAGE_RESOURCES_PATH/civitai_transport_logs``.
- URLs are recorded without query strings (they may embed auth tokens).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse


# Lifetime cap on retained daily log files (bounded disk usage).
_MAX_LOG_FILES = int(os.environ.get("CIVITAI_TRANSPORT_LOG_MAX_FILES", "14"))


def _resolve_log_root() -> Path:
    """Resolve the transport-log root from environment/config, fail-safe."""
    root = None
    for env_name in ("CIVITAI_TRANSPORT_LOG_PATH", "IMAGE_RESOURCES_PATH"):
        value = os.environ.get(env_name)
        if value:
            root = value
            break
    if not root:
        # Mirror response_archive's config-module fallback (works in backend
        # and standalone-script contexts) without importing backend code.
        try:
            from importlib import import_module

            for module_name in ("atelierai.config", "backend.config", "config"):
                try:
                    module = import_module(module_name)
                except ModuleNotFoundError:
                    continue
                value = getattr(module, "IMAGE_RESOURCES_PATH", None)
                if value:
                    root = value
                    break
        except Exception:
            root = None
    return Path(root or "image_resources") / "civitai_transport_logs"


def sanitize_url(url: str | None) -> str | None:
    """Return *url* without its query string (query may embed tokens)."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        return url.split("?", 1)[0]


class CivitaiTransportLog:
    """Buffered, thread-safe JSONL writer for transport telemetry.

    ``record()`` only appends to an in-memory buffer (cheap — safe to call
    from the consumer thread); a daemon thread flushes to disk. Disable with
    ``CIVITAI_TRANSPORT_LOG=0``.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root else _resolve_log_root()
        self.enabled = os.environ.get("CIVITAI_TRANSPORT_LOG", "1") != "0"
        self._buffer: list[dict[str, Any]] = []
        _lock = threading.Lock()
        self._lock = _lock
        self._flush_event = threading.Event()
        self._write_lock = threading.Lock()
        self._warned = False
        self._started = False
        self._start_thread()

    # ── Background flush thread ───────────────────────────────────────────

    def _start_thread(self) -> None:
        if self._started or not self.enabled:
            return
        thread = threading.Thread(
            target=self._flush_loop,
            name="civitai-transport-log-writer",
            daemon=True,
        )
        self._started = True
        thread.start()

    def _flush_loop(self) -> None:
        while True:
            self._flush_event.wait(timeout=1.0)
            self._flush_event.clear()
            self._flush()

    # ── Public API ────────────────────────────────────────────────────────

    def record(self, entry: dict[str, Any]) -> None:
        """Buffer one telemetry entry. Never raises.

        Missing ``timestamp`` is filled in; the URL query string is stripped.
        """
        if not self.enabled:
            return
        try:
            prepared = dict(entry)
            prepared.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            if "url" in prepared:
                prepared["url"] = sanitize_url(prepared.get("url"))
            with self._lock:
                self._buffer.append(prepared)
                should_flush = len(self._buffer) >= 128
            if should_flush:
                self._flush_event.set()
        except Exception as exc:
            self._warn_once(exc)

    def flush(self, timeout: float | None = None) -> None:
        """Flush buffered entries synchronously (used by tests/shutdown)."""
        self._flush_event.set()
        if timeout is None:
            self._flush()
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = len(self._buffer)
            if pending == 0:
                return
            self._flush()
            time.sleep(0.01)

    def read_entries(self, date: str | None = None) -> list[dict[str, Any]]:
        """Read parsed entries from log file(s); ``date`` is ``YYYY-MM-DD``."""
        entries: list[dict[str, Any]] = []
        for path in self._iter_log_files():
            if date is not None and path.stem != f"civitai_transport_{date}":
                continue
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            except OSError:
                continue
        return entries

    # ── Internals ─────────────────────────────────────────────────────────

    def _iter_log_files(self) -> list[Path]:
        try:
            return sorted(
                p for p in self.root.glob("civitai_transport_*.jsonl") if p.is_file()
            )
        except OSError:
            return []

    def _flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            pending = self._buffer
            self._buffer = []
        with self._write_lock:
            try:
                self._write_entries(pending)
            except Exception as exc:
                self._warn_once(exc)

    def _write_entries(self, entries: list[dict[str, Any]]) -> None:
        # Group by the entry's own date (entries may straddle midnight).
        by_date: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            date_key = str(entry.get("timestamp", ""))[:10] or datetime.now(
                timezone.utc
            ).strftime("%Y-%m-%d")
            by_date.setdefault(date_key, []).append(entry)
        for date_key, group in by_date.items():
            path = self.root / f"civitai_transport_{date_key}.jsonl"
            self._write_file(path, group)
        self._prune_old_files()

    def _write_file(self, path: Path, entries: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e, sort_keys=True, default=str) for e in entries]
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _prune_old_files(self) -> None:
        files = self._iter_log_files()
        excess = len(files) - _MAX_LOG_FILES
        for path in files[:excess]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _warn_once(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            try:
                print(
                    f"⚠️ CivitAI transport log write failed ({exc}); "
                    "continuing without transport logging"
                )
            except Exception:
                pass


# Module-level singleton, mirroring http_client's _SINGLETON_REF pattern.
_TRANSPORT_LOG_REF: list[CivitaiTransportLog | None] = [None]
_TRANSPORT_LOG_LOCK = threading.Lock()


def get_transport_log() -> CivitaiTransportLog:
    """Return the process-wide transport log instance (lazily created)."""
    if _TRANSPORT_LOG_REF[0] is None:
        with _TRANSPORT_LOG_LOCK:
            if _TRANSPORT_LOG_REF[0] is None:
                _TRANSPORT_LOG_REF[0] = CivitaiTransportLog()
    return _TRANSPORT_LOG_REF[0]


def record_transport_event(entry: dict[str, Any]) -> None:
    """Module-level convenience: buffer one entry on the shared log."""
    get_transport_log().record(entry)
