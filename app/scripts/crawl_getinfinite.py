#!/usr/bin/env python3
"""Crawl CivitAI image.getInfinite pages and save raw page responses.

This script crawls CivitAI galleries by starting URL and saves one formatted
JSON file per image.getInfinite page. Individual image metadata is not fetched
separately; only the paged getInfinite results are written.

Supported gallery URLs:
    https://civitai.com/images
    https://civitai.com/videos
    https://civitai.com/user/<username>/images
    https://civitai.com/user/<username>/videos
    https://civitai.com/collections/<collection_id>
    https://civitai.com/models/<model_id>
    https://civitai.com/models/<model_id>?modelVersionId=<model_version_id>
    https://civitai.com/models/<model_id>?modelversion=<model_version_id>
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from path_setup import PROJECT_ROOT  # noqa: F401 - adds src/ to sys.path
from atelierai.civitai.civitai_api import CivitaiAPI

DEFAULT_SOURCE_URL = "https://civitai.com/images"
DEFAULT_ORDER = "Newest"
DEFAULT_PERIOD = "Day"
DEFAULT_TYPES = ["image", "video"]
DEFAULT_INCLUDE = ["cosmetics"]
DEFAULT_BROWSING_LEVEL = 63
DEFAULT_PERIOD_MODE = "published"
_TRPC_MAX_RETRIES = 3
_EMPTY_FIRST_PAGE_RETRIES = 3
_DEFAULT_UNDEFINED_NEXT_CURSOR_RETRIES = 2
_STATE_FILE_NAME = "crawl_state.json"
_PAGE_FILE_RE = re.compile(r"^page_(\d+)\.json$")

PERIOD_ALIASES = {
    "all": "AllTime",
    "alltime": "AllTime",
    "day": "Day",
    "week": "Week",
    "month": "Month",
    "year": "Year",
}

ORDER_ALIASES = {
    "newest": "Newest",
    "oldest": "Oldest",
    "mostreactions": "Most Reactions",
    "mostcomments": "Most Comments",
    "mostcollected": "Most Collected",
    "random": "Random",
}

COMMON_ORDERS = {
    "Newest",
    "Oldest",
    "Most Reactions",
    "Most Comments",
    "Most Collected",
    "Random",
}

ALLOWED_ORDERS = {
    "main": COMMON_ORDERS,
    "user": COMMON_ORDERS,
    "collection": COMMON_ORDERS,
    "model": COMMON_ORDERS,
}


@dataclass(frozen=True)
class GalleryConfig:
    gallery_type: str
    source_url: str
    path_label: str
    media_types: list[str]
    payload_extras: dict[str, Any]
    order: str
    period: str


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "crawl"


def _normalize_period(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    period = PERIOD_ALIASES.get(key)
    if not period:
        allowed = ", ".join(sorted(PERIOD_ALIASES))
        raise ValueError(f"Unsupported period '{value}'. Expected one of: {allowed}")
    return period


def _normalize_order(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    order = ORDER_ALIASES.get(key)
    if not order:
        allowed = ", ".join(
            [
                "Newest",
                "Oldest",
                "Most Reactions",
                "Most Comments",
                "Most Collected",
                "Random",
            ]
        )
        raise ValueError(f"Unsupported order '{value}'. Expected one of: {allowed}")
    return order


def _normalize_source_url(source_url: str) -> str:
    raw = str(source_url or "").strip()
    if not raw:
        return DEFAULT_SOURCE_URL
    if raw.startswith("civitai.com/"):
        raw = f"https://{raw}"
    elif raw.startswith("www.civitai.com/"):
        raw = f"https://{raw}"
    return raw


def _parse_source_url(source_url: str, period: str, order: str) -> GalleryConfig:
    normalized_url = _normalize_source_url(source_url)
    parsed = urlparse(normalized_url)
    if parsed.netloc not in {"civitai.com", "www.civitai.com"}:
        raise ValueError(f"Unsupported host '{parsed.netloc}'. Expected civitai.com")

    parts = [part for part in parsed.path.split("/") if part]
    query = parse_qs(parsed.query)

    if not parts:
        parts = ["images"]

    if parts[0] in {"images", "videos"}:
        media_type = "video" if parts[0] == "videos" else "image"
        return GalleryConfig(
            gallery_type="main",
            source_url=normalized_url,
            path_label=f"main-{media_type}s",
            media_types=[media_type],
            payload_extras={},
            order=order,
            period=period,
        )

    if len(parts) >= 3 and parts[0] == "user" and parts[2] in {"images", "videos"}:
        username = parts[1]
        media_type = "video" if parts[2] == "videos" else "image"
        return GalleryConfig(
            gallery_type="user",
            source_url=normalized_url,
            path_label=f"user-{username}-{media_type}s",
            media_types=[media_type],
            payload_extras={"username": username},
            order=order,
            period=period,
        )

    if len(parts) >= 2 and parts[0] == "collections":
        try:
            collection_id = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"Invalid collection id in URL '{normalized_url}'") from exc
        return GalleryConfig(
            gallery_type="collection",
            source_url=normalized_url,
            path_label=f"collection-{collection_id}",
            media_types=list(DEFAULT_TYPES),
            payload_extras={"collectionId": collection_id},
            order=order,
            period=period,
        )

    if len(parts) >= 2 and parts[0] == "models":
        try:
            model_id = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"Invalid model id in URL '{normalized_url}'") from exc

        version_values = (
            query.get("modelVersionId")
            or query.get("modelversion")
            or query.get("modelVersion")
            or []
        )
        payload_extras: dict[str, Any] = {"modelId": model_id}
        label = f"model-{model_id}"
        if version_values:
            try:
                model_version_id = int(version_values[0])
            except ValueError as exc:
                raise ValueError(f"Invalid model version id in URL '{normalized_url}'") from exc
            payload_extras["modelVersionId"] = model_version_id
            label = f"model-{model_id}-version-{model_version_id}"

        return GalleryConfig(
            gallery_type="model",
            source_url=normalized_url,
            path_label=label,
            media_types=list(DEFAULT_TYPES),
            payload_extras=payload_extras,
            order=order,
            period=period,
        )

    raise ValueError(
        "Unsupported CivitAI gallery URL. Supported paths are /images, /videos, "
        "/user/<username>/images, /user/<username>/videos, /collections/<id>, and /models/<id>."
    )


def _validate_gallery_order(config: GalleryConfig) -> None:
    allowed = ALLOWED_ORDERS.get(config.gallery_type, set())
    if config.order not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(
            f"Order '{config.order}' is not supported for {config.gallery_type} galleries. "
            f"Allowed: {allowed_text}"
        )


def _build_payload(config: GalleryConfig, cursor: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "period": config.period,
        "periodMode": DEFAULT_PERIOD_MODE,
        "sort": config.order,
        "types": list(config.media_types),
        "withMeta": False,
        "followed": False,
        "useIndex": True,
        "browsingLevel": DEFAULT_BROWSING_LEVEL,
        "include": list(DEFAULT_INCLUDE),
        "excludedTagIds": [],
        "disablePoi": False,
        "disableMinor": False,
        "authed": True,
        "cursor": cursor,
    }
    payload.update(config.payload_extras)
    return payload


def _unwrap_trpc(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    result = raw.get("result")
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            result_json = data.get("json")
            if isinstance(result_json, dict):
                return result_json
    return None


def _trpc_error_status(raw: dict[str, Any]) -> int | None:
    err = raw.get("error")
    if not isinstance(err, dict):
        return None
    err_json = err.get("json") or {}
    err_data = err_json.get("data") or {}
    return err_data.get("httpStatus") or -1


def _raw_next_cursor_is_undefined(raw: dict[str, Any]) -> bool:
    """Detect tRPC meta encoding where nextCursor is explicitly undefined."""
    result = raw.get("result")
    if not isinstance(result, dict):
        return False
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return False
    values = meta.get("values")
    if not isinstance(values, dict):
        return False
    encoded = values.get("nextCursor")
    return isinstance(encoded, list) and len(encoded) == 1 and encoded[0] == "undefined"


def _default_output_dir(config: GalleryConfig) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = _slugify(config.path_label)
    period = _slugify(config.period)
    order = _slugify(config.order)
    return Path(__file__).resolve().parent.parent / "data" / f"getinfinite_{label}_{period}_{order}_{ts}"


def _compact_json(value: Any, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except TypeError:
        text = repr(value)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _page_sort_key(path: Path) -> tuple[int, str]:
    match = _PAGE_FILE_RE.match(path.name)
    if not match:
        return (0, path.name)
    return (int(match.group(1)), path.name)


def _page_files(output_dir: Path) -> list[Path]:
    return sorted(output_dir.glob("page_*.json"), key=_page_sort_key)


def _validate_resume_source(saved_source: dict[str, Any] | None, config: GalleryConfig, label: str) -> None:
    if not isinstance(saved_source, dict):
        return

    current_source = asdict(config)
    mismatches: list[str] = []
    for key in (
        "source_url",
        "gallery_type",
        "path_label",
        "media_types",
        "payload_extras",
        "order",
        "period",
    ):
        if saved_source.get(key) != current_source.get(key):
            mismatches.append(key)

    if mismatches:
        mismatch_text = ", ".join(mismatches)
        raise ValueError(
            f"Existing crawl state in '{label}' does not match the requested crawl configuration "
            f"(mismatched: {mismatch_text})."
        )


def _resume_state_from_checkpoint(output_dir: Path, config: GalleryConfig) -> dict[str, Any] | None:
    state_path = output_dir / _STATE_FILE_NAME
    if not state_path.exists():
        return None

    payload = _load_json_file(state_path)
    if not isinstance(payload, dict):
        raise ValueError(f"Checkpoint file '{state_path}' is not a JSON object")

    _validate_resume_source(payload.get("source"), config, str(state_path))

    seen_cursors = [str(value) for value in payload.get("seen_cursors") or [] if value]
    cursor_to_request = payload.get("cursor_to_request")
    return {
        "state_source": "checkpoint",
        "state_path": state_path,
        "crawl_started_at": payload.get("crawl_started_at") or payload.get("started_at"),
        "pages_requested": int(payload.get("pages_requested") or 0),
        "pages_fetched": int(payload.get("pages_fetched") or 0),
        "total_items": int(payload.get("total_items") or 0),
        "empty_first_page_retries": int(payload.get("empty_first_page_retries") or 0),
        "undefined_next_cursor_retries": int(payload.get("undefined_next_cursor_retries") or 0),
        "anomaly_count": int(payload.get("anomaly_count") or 0),
        "anomalies": list(payload.get("anomalies") or []),
        "cursor_to_request": str(cursor_to_request) if cursor_to_request else None,
        "seen_cursors": seen_cursors,
        "completed": bool(payload.get("completed")),
        "last_page_file": payload.get("last_page_file"),
    }


def _resume_state_from_page_files(output_dir: Path, config: GalleryConfig) -> dict[str, Any] | None:
    page_files = _page_files(output_dir)
    if not page_files:
        return None

    summary_path = output_dir / "summary.json"
    summary_payload = _load_json_file(summary_path) if summary_path.exists() else None
    if isinstance(summary_payload, dict):
        _validate_resume_source(summary_payload.get("source"), config, str(summary_path))

    pages_fetched = 0
    total_items = 0
    seen_cursors: list[str] = []
    seen_cursor_set: set[str] = set()
    last_page_payload: dict[str, Any] | None = None
    last_page_path: Path | None = None

    for page_file in page_files:
        payload = _load_json_file(page_file)
        if not isinstance(payload, dict):
            raise ValueError(f"Page file '{page_file}' is not a JSON object")

        page_source_url = payload.get("source_url")
        if page_source_url and page_source_url != config.source_url:
            raise ValueError(
                f"Existing page file '{page_file}' belongs to '{page_source_url}', "
                f"not '{config.source_url}'."
            )

        file_match = _PAGE_FILE_RE.match(page_file.name)
        fallback_page_number = int(file_match.group(1)) if file_match else 0
        pages_fetched = max(pages_fetched, int(payload.get("page") or fallback_page_number))
        item_count = payload.get("items_count")
        if item_count is None and isinstance(payload.get("items"), list):
            item_count = len(payload["items"])
        total_items += int(item_count or 0)

        next_cursor = payload.get("next_cursor")
        if next_cursor:
            next_cursor_text = str(next_cursor)
            if next_cursor_text not in seen_cursor_set:
                seen_cursor_set.add(next_cursor_text)
                seen_cursors.append(next_cursor_text)

        last_page_payload = payload
        last_page_path = page_file

    assert last_page_payload is not None
    cursor_to_request = last_page_payload.get("next_cursor")

    return {
        "state_source": "page_files",
        "state_path": last_page_path,
        "crawl_started_at": (
            summary_payload.get("crawl_started_at")
            if isinstance(summary_payload, dict)
            else None
        )
        or (
            summary_payload.get("started_at")
            if isinstance(summary_payload, dict)
            else None
        ),
        "pages_requested": int(
            summary_payload.get("pages_requested")
            if isinstance(summary_payload, dict) and summary_payload.get("pages_requested") is not None
            else pages_fetched
        ),
        "pages_fetched": pages_fetched,
        "total_items": total_items,
        "empty_first_page_retries": int(
            summary_payload.get("empty_first_page_retries")
            if isinstance(summary_payload, dict) and summary_payload.get("empty_first_page_retries") is not None
            else 0
        ),
        "undefined_next_cursor_retries": int(
            summary_payload.get("undefined_next_cursor_retries")
            if isinstance(summary_payload, dict)
            and summary_payload.get("undefined_next_cursor_retries") is not None
            else 0
        ),
        "anomaly_count": int(
            summary_payload.get("anomaly_count")
            if isinstance(summary_payload, dict) and summary_payload.get("anomaly_count") is not None
            else 0
        ),
        "anomalies": list(summary_payload.get("anomalies") or []) if isinstance(summary_payload, dict) else [],
        "cursor_to_request": str(cursor_to_request) if cursor_to_request else None,
        "seen_cursors": seen_cursors,
        "completed": not bool(cursor_to_request),
        "last_page_file": str(last_page_path) if last_page_path else None,
    }


def _load_resume_state(output_dir: Path, config: GalleryConfig) -> dict[str, Any]:
    checkpoint_state = _resume_state_from_checkpoint(output_dir, config)
    page_file_state = _resume_state_from_page_files(output_dir, config)

    if checkpoint_state and page_file_state:
        if page_file_state["pages_fetched"] > checkpoint_state["pages_fetched"]:
            return page_file_state
        return checkpoint_state

    if checkpoint_state:
        return checkpoint_state
    if page_file_state:
        return page_file_state

    raise ValueError(
        f"No existing crawl pages or checkpoint found in '{output_dir}'. "
        "Use --output-dir with a previous crawl directory when resuming."
    )


def _write_crawl_state(
    *,
    output_dir: Path,
    config: GalleryConfig,
    crawl_started_at: str,
    invocation_started_at: str,
    pages_requested: int,
    pages_fetched: int,
    total_items: int,
    empty_first_page_retries: int,
    undefined_next_cursor_retries: int,
    undefined_next_cursor_retries_max: int,
    anomaly_count: int,
    anomaly_events: list[dict[str, Any]],
    seen_cursors: set[str],
    cursor_to_request: str | None,
    max_pages: int | None,
    inter_page_delay_s: float,
    completed: bool,
    stop_reason: str | None,
    last_page_file: Path | None,
) -> Path:
    state_path = output_dir / _STATE_FILE_NAME
    payload = {
        "schema_version": 1,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "completed": completed,
        "stop_reason": stop_reason,
        "source": asdict(config),
        "crawl_started_at": crawl_started_at,
        "invocation_started_at": invocation_started_at,
        "pages_requested": pages_requested,
        "pages_fetched": pages_fetched,
        "total_items": total_items,
        "empty_first_page_retries": empty_first_page_retries,
        "undefined_next_cursor_retries": undefined_next_cursor_retries,
        "undefined_next_cursor_retries_max": undefined_next_cursor_retries_max,
        "anomaly_count": anomaly_count,
        "anomalies": anomaly_events,
        "seen_cursors": sorted(seen_cursors),
        "cursor_to_request": cursor_to_request,
        "last_page_file": str(last_page_file) if last_page_file else None,
        "max_pages_limit": max_pages,
        "inter_page_delay_ms": round(inter_page_delay_s * 1000),
        "payload_template": _build_payload(config, None),
    }
    state_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return state_path


def crawl(
    config: GalleryConfig,
    output_dir: Path,
    max_pages: int | None,
    inter_page_delay_s: float,
    no_empty_results: bool,
    undefined_next_cursor_retries_max: int,
    resume: bool,
) -> None:
    api = CivitaiAPI.get_instance()
    output_created = resume and output_dir.exists()

    def ensure_output_dir() -> None:
        nonlocal output_created
        if not output_created:
            output_dir.mkdir(parents=True, exist_ok=True)
            output_created = True

    invocation_started_at = time.monotonic()
    invocation_started_wall = datetime.now(timezone.utc).isoformat()
    crawl_started_wall = invocation_started_wall
    cursor: str | None = None
    seen_cursors: set[str] = set()
    pages_requested = 0
    pages_fetched = 0
    total_items = 0
    total_items_before_run = 0
    empty_first_page_retries = 0
    undefined_next_cursor_retries = 0
    anomaly_count = 0
    anomaly_events: list[dict[str, Any]] = []
    resume_info: dict[str, Any] | None = None
    last_page_file: Path | None = None
    stop_reason: str | None = None
    last_fetch_elapsed_s = 0.0
    last_fetch_rate = 0.0

    if resume:
        resume_state = _load_resume_state(output_dir, config)
        crawl_started_wall = resume_state.get("crawl_started_at") or invocation_started_wall
        cursor = resume_state.get("cursor_to_request")
        seen_cursors = set(resume_state.get("seen_cursors") or [])
        pages_requested = int(resume_state.get("pages_requested") or 0)
        pages_fetched = int(resume_state.get("pages_fetched") or 0)
        total_items = int(resume_state.get("total_items") or 0)
        total_items_before_run = total_items
        empty_first_page_retries = int(resume_state.get("empty_first_page_retries") or 0)
        anomaly_count = int(resume_state.get("anomaly_count") or 0)
        anomaly_events = list(resume_state.get("anomalies") or [])
        last_page_file_value = resume_state.get("last_page_file")
        last_page_file = Path(last_page_file_value) if last_page_file_value else None
        resume_info = {
            "state_source": resume_state.get("state_source"),
            "state_path": str(resume_state.get("state_path")) if resume_state.get("state_path") else None,
            "pages_fetched_before_resume": pages_fetched,
            "total_items_before_resume": total_items,
            "cursor_to_request": cursor,
        }

        if resume_state.get("completed") and not cursor:
            elapsed = time.monotonic() - invocation_started_at
            print(f"Source:     {config.source_url}")
            print(f"Output:     {output_dir}")
            print(f"Resume:     existing crawl is already complete ({pages_fetched} pages, {total_items} items)")
            print(f"Checked:    {invocation_started_wall}")
            print(f"Time:       {elapsed:.1f}s")
            return

        if max_pages is not None and pages_fetched >= max_pages:
            elapsed = time.monotonic() - invocation_started_at
            print(f"Source:     {config.source_url}")
            print(f"Output:     {output_dir}")
            print(
                f"Resume:     existing crawl already reached --max-pages {max_pages} "
                f"({pages_fetched} pages, {total_items} items)"
            )
            print(f"Checked:    {invocation_started_wall}")
            print(f"Time:       {elapsed:.1f}s")
            return

    session_started_at = time.monotonic()
    started_at = time.monotonic()

    def persist_state(*, completed: bool = False) -> None:
        if not output_created:
            return
        _write_crawl_state(
            output_dir=output_dir,
            config=config,
            crawl_started_at=crawl_started_wall,
            invocation_started_at=invocation_started_wall,
            pages_requested=pages_requested,
            pages_fetched=pages_fetched,
            total_items=total_items,
            empty_first_page_retries=empty_first_page_retries,
            undefined_next_cursor_retries=undefined_next_cursor_retries,
            undefined_next_cursor_retries_max=undefined_next_cursor_retries_max,
            anomaly_count=anomaly_count,
            anomaly_events=anomaly_events,
            seen_cursors=seen_cursors,
            cursor_to_request=cursor,
            max_pages=max_pages,
            inter_page_delay_s=inter_page_delay_s,
            completed=completed,
            stop_reason=stop_reason,
            last_page_file=last_page_file,
        )

    def build_summary() -> dict[str, Any]:
        elapsed = time.monotonic() - started_at
        session_elapsed = time.monotonic() - session_started_at
        session_items = total_items - total_items_before_run
        final_rate = session_items / session_elapsed if session_elapsed > 0 else 0.0
        return {
            "started_at": invocation_started_wall,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "crawl_started_at": crawl_started_wall,
            "session_elapsed_seconds": round(session_elapsed, 2),
            "session_items": session_items,
            "pages_requested": pages_requested,
            "pages_fetched": pages_fetched,
            "total_items": total_items,
            "items_per_page_avg": round(total_items / pages_fetched, 1) if pages_fetched else 0,
            "rate_items_per_sec": round(final_rate, 2),
            "last_fetch_elapsed_seconds": round(last_fetch_elapsed_s, 3),
            "last_fetch_rate_items_per_sec": round(last_fetch_rate, 2),
            "cursor_to_request": cursor,
            "max_pages_limit": max_pages,
            "inter_page_delay_ms": round(inter_page_delay_s * 1000),
            "empty_first_page_retries": empty_first_page_retries,
            "undefined_next_cursor_retries": undefined_next_cursor_retries,
            "undefined_next_cursor_retries_max": undefined_next_cursor_retries_max,
            "anomaly_count": anomaly_count,
            "anomalies": anomaly_events,
            "resume": resume_info,
            "stop_reason": stop_reason,
            "source": asdict(config),
            "payload_template": _build_payload(config, None),
        }

    def write_summary_and_state() -> Path | None:
        summary_file: Path | None = None
        if output_created:
            summary = build_summary()
            summary_file = output_dir / "summary.json"
            summary_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            persist_state(completed=stop_reason == "end_of_results")
        return summary_file

    def print_summary(summary_file: Path | None) -> None:
        summary = build_summary()
        print()
        print("Summary:")
        print(f"  Stop:         {stop_reason or 'unknown'}")
        print(f"  Pages:        {pages_fetched} fetched / {pages_requested} requested")
        print(f"  Items:        {total_items} total ({summary['session_items']} new this run)")
        print(
            f"  Last fetch:   {last_fetch_elapsed_s:.2f}s  ({last_fetch_rate:.1f} items/s)  "
            f"next={cursor or '<end>'}"
        )
        print(f"  Session:      {summary['session_elapsed_seconds']:.1f}s  ({summary['rate_items_per_sec']:.1f} new items/s)")
        print(f"  Anomalies:    {anomaly_count}")
        if output_created:
            print(f"  Output:       {output_dir}")
            print(f"  Summary:      {summary_file}")
        else:
            print("  Output:       <not written>")

    def capture_anomaly(
        *,
        reason: str,
        payload: dict[str, Any],
        raw_response: Any,
        cursor_used: str | None,
        page_items: int | None,
        next_cursor: Any,
        allow_create_output: bool,
    ) -> None:
        nonlocal anomaly_count
        anomaly_count += 1
        event = {
            "id": anomaly_count,
            "reason": reason,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source_url": config.source_url,
            "gallery_type": config.gallery_type,
            "period": config.period,
            "order": config.order,
            "page_requested": pages_requested,
            "page_fetched": pages_fetched,
            "cursor_used": cursor_used,
            "items_count": page_items,
            "next_cursor": next_cursor,
            "payload": payload,
            "raw_response": raw_response,
        }

        if output_created or allow_create_output:
            ensure_output_dir()
            anomalies_dir = output_dir / "anomalies"
            anomalies_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"anomaly_{anomaly_count:03d}_{_slugify(reason)}.json"
            file_path = anomalies_dir / file_name
            file_path.write_text(json.dumps(event, indent=2, ensure_ascii=False), encoding="utf-8")
            event["file"] = str(file_path)
            print(f"[anomaly] Captured {reason} -> {file_path}")
        else:
            print(
                f"[anomaly] {reason} | items={page_items} | next_cursor={next_cursor} | "
                f"payload={_compact_json(payload)} | raw={_compact_json(raw_response)}"
            )

        anomaly_events.append(
            {
                "id": event["id"],
                "reason": event["reason"],
                "captured_at": event["captured_at"],
                "page_requested": event["page_requested"],
                "page_fetched": event["page_fetched"],
                "cursor_used": event["cursor_used"],
                "items_count": event["items_count"],
                "next_cursor": event["next_cursor"],
                "file": event.get("file"),
            }
        )
        if output_created or allow_create_output:
            persist_state(completed=False)

    print(f"Source:     {config.source_url}")
    print(f"Gallery:    {config.gallery_type}")
    print(f"Period:     {config.period}")
    print(f"Order:      {config.order}")
    print(f"Types:      {', '.join(config.media_types)}")
    print(f"Output:     {output_dir}")
    if resume_info:
        print(
            f"Resume:     yes ({resume_info['state_source']}; "
            f"starting after page {resume_info['pages_fetched_before_resume']} at cursor {resume_info['cursor_to_request']})"
        )
    else:
        print("Resume:     no")
    print(f"Max pages:  {max_pages if max_pages is not None else 'unlimited'}")
    print(f"Page delay: {inter_page_delay_s * 1000:.0f} ms")
    print(f"Started:    {invocation_started_wall}")
    print()
    print(
        f"{'Page':>6}  {'Items':>6}  {'Total':>9}  {'NextCursor':>18}  "
        f"{'Fetch':>8}  {'FetchRate':>11}  {'Elapsed':>8}  {'Rate':>10}"
    )
    print("-" * 109)

    try:
        while True:
            page_fetch_started_at = time.monotonic()
            pages_requested += 1
            payload = _build_payload(config, cursor)

            raw = None
            for attempt in range(1, _TRPC_MAX_RETRIES + 1):
                raw = api._make_raw_request("image.getInfinite", payload)
                if raw is None:
                    print(f"\n[page {pages_requested}] Empty response on attempt {attempt}.")
                    if attempt < _TRPC_MAX_RETRIES:
                        time.sleep(2.0 * attempt)
                    continue

                status = _trpc_error_status(raw)
                if status is None:
                    break

                if status == 429:
                    backoff = api.http_client.activate_global_backoff(
                        30.0, reason=f"crawl tRPC 429 (page {pages_requested})"
                    )
                    print(
                        f"\n[page {pages_requested}] tRPC 429 - global backoff {backoff:.1f}s "
                        f"(attempt {attempt}/{_TRPC_MAX_RETRIES})"
                    )
                else:
                    print(
                        f"\n[page {pages_requested}] tRPC error HTTP {status} "
                        f"(attempt {attempt}/{_TRPC_MAX_RETRIES}): "
                        f"{json.dumps(raw.get('error'), separators=(',', ':'))}"
                    )
                    if attempt < _TRPC_MAX_RETRIES:
                        time.sleep(2.0 * attempt)

            if raw is None:
                print(f"\n[page {pages_requested}] No response after {_TRPC_MAX_RETRIES} attempts. Stopping.")
                stop_reason = "no_response"
                break

            if _trpc_error_status(raw) is not None:
                print(f"\n[page {pages_requested}] Unrecoverable tRPC error. Stopping.")
                stop_reason = "trpc_error"
                break

            page_data = _unwrap_trpc(raw)
            if not isinstance(page_data, dict):
                print(f"\n[page {pages_requested}] Unexpected response shape; keys={list(raw.keys())}. Stopping.")
                stop_reason = "unexpected_shape"
                break

            items = page_data.get("items") or []
            next_cursor = page_data.get("nextCursor")
            page_count = len(items) if isinstance(items, list) else 0
            next_cursor_undefined = next_cursor is None and _raw_next_cursor_is_undefined(raw)

            # The endpoint occasionally returns an empty first page with no cursor.
            # Retry the first-page request a few times before treating it as real empty output.
            if page_count == 0 and cursor is None and not next_cursor and empty_first_page_retries < _EMPTY_FIRST_PAGE_RETRIES:
                capture_anomaly(
                    reason="first_page_empty_retry",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=not no_empty_results,
                )
                empty_first_page_retries += 1
                backoff_s = min(2.0 * empty_first_page_retries, 8.0)
                print(
                    f"\n[page 1] Empty first-page payload (attempt {empty_first_page_retries}/{_EMPTY_FIRST_PAGE_RETRIES}); "
                    f"retrying in {backoff_s:.1f}s to avoid transient index gaps."
                )
                time.sleep(backoff_s)
                continue

            if page_count == 0 and total_items == 0 and pages_fetched == 0 and no_empty_results and not next_cursor:
                capture_anomaly(
                    reason="first_page_empty_final",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=False,
                )
                print("\nNo records returned and --no-empty-results is set; skipping output directory/files.")
                stop_reason = "empty_results_skipped"
                break

            # Mid-run empty pages with nextCursor encoded as undefined are often transient.
            # Retry the same cursor a few times before treating it as true exhaustion.
            if (
                page_count == 0
                and cursor is not None
                and next_cursor_undefined
                and undefined_next_cursor_retries < undefined_next_cursor_retries_max
            ):
                undefined_next_cursor_retries += 1
                capture_anomaly(
                    reason="next_cursor_undefined_retry",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=not no_empty_results,
                )
                backoff_s = min(2.0 * undefined_next_cursor_retries, 6.0)
                print(
                    f"\n[page {pages_requested}] nextCursor=undefined on empty page "
                    f"(attempt {undefined_next_cursor_retries}/{undefined_next_cursor_retries_max}); "
                    f"retrying same cursor in {backoff_s:.1f}s."
                )
                time.sleep(backoff_s)
                continue

            if page_count == 0 and cursor is not None and next_cursor_undefined:
                capture_anomaly(
                    reason="next_cursor_undefined_final",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=not no_empty_results,
                )

            pages_fetched += 1
            total_items += page_count

            ensure_output_dir()
            page_file = output_dir / f"page_{pages_fetched:06d}.json"
            page_out = {
                "page": pages_fetched,
                "source_url": config.source_url,
                "gallery_type": config.gallery_type,
                "cursor_used": cursor,
                "next_cursor": next_cursor,
                "items_count": page_count,
                "items": items,
            }
            page_file.write_text(json.dumps(page_out, indent=4, ensure_ascii=False), encoding="utf-8")
            last_page_file = page_file
            persist_state(completed=False)

            last_fetch_elapsed_s = time.monotonic() - page_fetch_started_at
            last_fetch_rate = page_count / last_fetch_elapsed_s if last_fetch_elapsed_s > 0 else 0.0
            elapsed = time.monotonic() - session_started_at
            session_items = total_items - total_items_before_run
            rate = session_items / elapsed if elapsed > 0 else 0.0
            print(
                f"{pages_fetched:>6}  {page_count:>6}  {total_items:>9}  "
                f"{str(next_cursor):>18}  {last_fetch_elapsed_s:>7.1f}s  {last_fetch_rate:>9.1f}/s  "
                f"{elapsed:>7.1f}s  {rate:>8.1f}/s"
            )

            if pages_fetched == 1 and page_count == 0 and not next_cursor:
                capture_anomaly(
                    reason="first_page_empty_no_cursor",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=not no_empty_results,
                )
                print(
                    "\nWarning: first page returned 0 items with no nextCursor. "
                    "This is likely a transient API/index condition rather than true exhaustion."
                )

            if not next_cursor:
                capture_anomaly(
                    reason="next_cursor_none",
                    payload=payload,
                    raw_response=raw,
                    cursor_used=cursor,
                    page_items=page_count,
                    next_cursor=next_cursor,
                    allow_create_output=not no_empty_results,
                )
                print("\nNo nextCursor - end of results.")
                stop_reason = "end_of_results"
                cursor = None
                break

            # Any valid nextCursor means the transient undefined-cursor streak recovered.
            if undefined_next_cursor_retries > 0:
                undefined_next_cursor_retries = 0

            next_cursor_text = str(next_cursor)
            if next_cursor_text in seen_cursors:
                print(f"\nCursor loop detected at {next_cursor_text}. Stopping.")
                stop_reason = "cursor_loop"
                cursor = next_cursor_text
                break

            if max_pages is not None and pages_fetched >= max_pages:
                print(f"\nReached --max-pages {max_pages}. Stopping.")
                stop_reason = "max_pages"
                cursor = next_cursor_text
                break

            seen_cursors.add(next_cursor_text)
            cursor = next_cursor_text

            if inter_page_delay_s > 0:
                time.sleep(inter_page_delay_s)
    except KeyboardInterrupt:
        stop_reason = "keyboard_interrupt"
        print("\nInterrupted by user (Ctrl-C). Saving checkpoint and printing summary...")
    finally:
        summary_file = write_summary_and_state()
        print_summary(summary_file)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl CivitAI image.getInfinite for a gallery URL and save raw page responses."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_SOURCE_URL,
        help="Starting gallery URL (default: https://civitai.com/images)",
    )
    parser.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help="Time period: day, week, month, year, or alltime (default: Day)",
    )
    parser.add_argument(
        "--order",
        default=DEFAULT_ORDER,
        help=(
            "Sort order. Common: Newest, Oldest, Most Reactions, Most Comments, Most Collected. "
            "Verified live values: Newest, Oldest, Most Reactions, Most Comments, Most Collected, Random."
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N pages (default: run until end of results)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="Directory to write page_*.json and summary.json (default: auto-timestamped under app/data/)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume an interrupted crawl from an existing --output-dir. "
            "Loads crawl_state.json when available, otherwise reconstructs state from page_*.json."
        ),
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=0,
        metavar="MS",
        help="Milliseconds to sleep between each page request (default: 0)",
    )
    parser.add_argument(
        "--no-empty-results",
        action="store_true",
        help=(
            "If the crawl returns zero total records, do not create output directory/files. "
            "Useful for retry loops where empty payloads are treated as transient failures."
        ),
    )
    parser.add_argument(
        "--undefined-next-cursor-retries",
        type=int,
        default=_DEFAULT_UNDEFINED_NEXT_CURSOR_RETRIES,
        metavar="N",
        help=(
            "Retry budget when a non-first page returns empty items and nextCursor is encoded as undefined "
            f"(default: {_DEFAULT_UNDEFINED_NEXT_CURSOR_RETRIES})."
        ),
    )
    args = parser.parse_args()

    if args.undefined_next_cursor_retries < 0:
        parser.error("--undefined-next-cursor-retries must be >= 0")

    if args.resume and args.output_dir is None:
        parser.error("--resume requires --output-dir")

    period = _normalize_period(args.period)
    order = _normalize_order(args.order)
    config = _parse_source_url(args.url, period=period, order=order)
    _validate_gallery_order(config)

    output_dir = args.output_dir or _default_output_dir(config)
    existing_page_files = output_dir.exists() and bool(_page_files(output_dir))
    if existing_page_files and not args.resume:
        parser.error(
            f"Output directory '{output_dir}' already contains crawl pages. "
            "Use --resume to continue that crawl or choose a different --output-dir."
        )

    crawl(
        config=config,
        output_dir=output_dir,
        max_pages=args.max_pages,
        inter_page_delay_s=args.delay_ms / 1000.0,
        no_empty_results=args.no_empty_results,
        undefined_next_cursor_retries_max=args.undefined_next_cursor_retries,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()