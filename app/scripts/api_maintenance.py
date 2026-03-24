#!/usr/bin/env python3
"""Small CLI for driving AtelierAI maintenance operations through the HTTP API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Optional

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_WAIT_POLL_SECONDS = 2.0
_TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}


class ApiCommandError(RuntimeError):
    """Raised when an API command fails in a user-actionable way."""


def _normalize_base_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ApiCommandError("Base URL is required.")
    return text.rstrip("/")


def _build_url(base_url: str, path: str) -> str:
    return f"{_normalize_base_url(base_url)}{path}"


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _request_json(
    session: requests.Session,
    *,
    method: str,
    url: str,
    timeout_seconds: float,
    json_payload: Optional[dict[str, Any]] = None,
) -> Any:
    try:
        response = session.request(
            method=method,
            url=url,
            json=json_payload,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        raise ApiCommandError(f"Request failed: {exc}") from exc

    if not response.ok:
        message = _extract_error_message(response)
        raise ApiCommandError(f"{response.request.method} {url} failed: {message}")

    if not response.content:
        return None

    try:
        return response.json()
    except ValueError as exc:
        raise ApiCommandError(f"{response.request.method} {url} returned invalid JSON.") from exc


def _print_payload(payload: Any, *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str))


def _coerce_task_id(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    task = payload.get("task")
    if isinstance(task, dict):
        task_id = task.get("id")
        if isinstance(task_id, str) and task_id.strip():
            return task_id.strip()
    task_id = payload.get("id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id.strip()
    return None


def _wait_for_task(
    session: requests.Session,
    *,
    base_url: str,
    task_id: str,
    timeout_seconds: float,
    poll_seconds: float,
    pretty: bool,
) -> int:
    started_monotonic = time.monotonic()
    last_status_line: Optional[str] = None

    while True:
        payload = _request_json(
            session,
            method="GET",
            url=_build_url(base_url, f"/tasks/{task_id}"),
            timeout_seconds=max(30.0, poll_seconds + 5.0),
        )
        status = str((payload or {}).get("status") or "unknown")
        message = str((payload or {}).get("message") or "")
        progress_current = int((payload or {}).get("progress_current") or 0)
        progress_total = int((payload or {}).get("progress_total") or 0)
        status_line = f"[{status}] {message} ({progress_current}/{progress_total})"
        if status_line != last_status_line:
            print(status_line, file=sys.stderr)
            last_status_line = status_line

        if status in _TERMINAL_TASK_STATES:
            _print_payload(payload, pretty=pretty)
            return 0 if status == "completed" else 1

        if timeout_seconds > 0 and (time.monotonic() - started_monotonic) >= timeout_seconds:
            raise ApiCommandError(f"Timed out while waiting for task {task_id}.")

        time.sleep(max(0.2, poll_seconds))


def _maybe_wait_for_task(
    payload: Any,
    *,
    args: argparse.Namespace,
    session: requests.Session,
) -> int:
    _print_payload(payload, pretty=args.pretty)
    if not getattr(args, "wait", False):
        return 0

    task_id = _coerce_task_id(payload)
    if not task_id:
        raise ApiCommandError("Response did not include a task id to wait on.")

    return _wait_for_task(
        session,
        base_url=args.base_url,
        task_id=task_id,
        timeout_seconds=args.wait_timeout,
        poll_seconds=args.poll_interval,
        pretty=args.pretty,
    )


def cmd_scan_library(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, "/scan_library/"),
        timeout_seconds=args.timeout,
    )
    _print_payload(payload, pretty=args.pretty)
    return 0


def cmd_rescan_image(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, f"/images/{args.file_hash}/rescan"),
        timeout_seconds=args.timeout,
    )
    _print_payload(payload, pretty=args.pretty)
    return 0


def cmd_list_tasks(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="GET",
        url=_build_url(args.base_url, f"/tasks/?limit={args.limit}"),
        timeout_seconds=args.timeout,
    )
    _print_payload(payload, pretty=args.pretty)
    return 0


def cmd_get_task(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="GET",
        url=_build_url(args.base_url, f"/tasks/{args.task_id}"),
        timeout_seconds=args.timeout,
    )
    _print_payload(payload, pretty=args.pretty)
    return 0


def cmd_cancel_task(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, f"/tasks/{args.task_id}/cancel"),
        timeout_seconds=args.timeout,
    )
    _print_payload(payload, pretty=args.pretty)
    return 0


def cmd_retry_failed(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, f"/tasks/{args.task_id}/retry_failed"),
        timeout_seconds=args.timeout,
    )
    return _maybe_wait_for_task(payload, args=args, session=session)


def cmd_import_civitai(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, "/import_civitai/"),
        timeout_seconds=args.timeout,
        json_payload={
            "import_type": args.import_type,
            "value": args.value,
            "limit": args.limit,
        },
    )
    return _maybe_wait_for_task(payload, args=args, session=session)


def cmd_sync_civitai_collections(args: argparse.Namespace, session: requests.Session) -> int:
    payload = _request_json(
        session,
        method="POST",
        url=_build_url(args.base_url, "/collections/sync/civitai"),
        timeout_seconds=args.timeout,
        json_payload={
            "limit": args.limit,
        },
    )
    return _maybe_wait_for_task(payload, args=args, session=session)


def cmd_wait_task(args: argparse.Namespace, session: requests.Session) -> int:
    return _wait_for_task(
        session,
        base_url=args.base_url,
        task_id=args.task_id,
        timeout_seconds=args.wait_timeout,
        poll_seconds=args.poll_interval,
        pretty=args.pretty,
    )


def cmd_doctor(args: argparse.Namespace, session: requests.Session) -> int:
    checks = [
        {
            "name": "root",
            "method": "GET",
            "path": "/",
            "expect_json": False,
        },
        {
            "name": "tasks",
            "method": "GET",
            "path": "/tasks/?limit=1",
            "expect_json": True,
        },
        {
            "name": "collections",
            "method": "GET",
            "path": "/collections/",
            "expect_json": True,
        },
    ]

    results: list[dict[str, Any]] = []
    had_failure = False

    for check in checks:
        url = _build_url(args.base_url, str(check["path"]))
        try:
            response = session.request(
                method=str(check["method"]),
                url=url,
                timeout=args.timeout,
            )
            result: dict[str, Any] = {
                "name": check["name"],
                "method": check["method"],
                "url": url,
                "ok": response.ok,
                "status_code": response.status_code,
                "content_type": response.headers.get("content-type"),
            }

            if response.ok and bool(check["expect_json"]):
                try:
                    payload = response.json()
                    if isinstance(payload, list):
                        result["json_kind"] = "list"
                        result["json_length"] = len(payload)
                    elif isinstance(payload, dict):
                        result["json_kind"] = "object"
                        result["json_keys"] = sorted(payload.keys())[:10]
                    else:
                        result["json_kind"] = type(payload).__name__
                except ValueError:
                    result["ok"] = False
                    result["error"] = "Expected JSON response but response body was not valid JSON."

            if not result["ok"]:
                had_failure = True
                if "error" not in result:
                    result["error"] = _extract_error_message(response)

            results.append(result)
        except requests.RequestException as exc:
            had_failure = True
            results.append(
                {
                    "name": check["name"],
                    "method": check["method"],
                    "url": url,
                    "ok": False,
                    "error": str(exc),
                }
            )

    payload = {
        "ok": not had_failure,
        "base_url": _normalize_base_url(args.base_url),
        "checks": results,
    }
    _print_payload(payload, pretty=args.pretty)
    return 0 if not had_failure else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive AtelierAI maintenance operations through the backend API.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON instead of pretty-printed JSON.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_WAIT_POLL_SECONDS,
        help=f"Task wait polling interval in seconds (default: {DEFAULT_WAIT_POLL_SECONDS}).",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=0.0,
        help="Maximum seconds to wait for task completion when --wait is used. 0 means no timeout.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_library_parser = subparsers.add_parser(
        "scan-library",
        help="Run a full library scan via POST /scan_library/.",
    )
    scan_library_parser.set_defaults(handler=cmd_scan_library)

    rescan_image_parser = subparsers.add_parser(
        "rescan-image",
        help="Rescan one existing image by file hash.",
    )
    rescan_image_parser.add_argument("file_hash", help="Local file hash for the image to rescan.")
    rescan_image_parser.set_defaults(handler=cmd_rescan_image)

    list_tasks_parser = subparsers.add_parser(
        "list-tasks",
        help="List recent background tasks.",
    )
    list_tasks_parser.add_argument("--limit", type=int, default=20, help="Maximum tasks to return (default: 20).")
    list_tasks_parser.set_defaults(handler=cmd_list_tasks)

    get_task_parser = subparsers.add_parser(
        "get-task",
        help="Fetch one background task by task id.",
    )
    get_task_parser.add_argument("task_id", help="Background task id.")
    get_task_parser.set_defaults(handler=cmd_get_task)

    cancel_task_parser = subparsers.add_parser(
        "cancel-task",
        help="Cancel a background task by task id.",
    )
    cancel_task_parser.add_argument("task_id", help="Background task id.")
    cancel_task_parser.set_defaults(handler=cmd_cancel_task)

    retry_failed_parser = subparsers.add_parser(
        "retry-failed",
        help="Retry failed items from a compatible background task.",
    )
    retry_failed_parser.add_argument("task_id", help="Source task id with retryable failed items.")
    retry_failed_parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll the returned task until it reaches a terminal state.",
    )
    retry_failed_parser.set_defaults(handler=cmd_retry_failed)

    import_civitai_parser = subparsers.add_parser(
        "import-civitai",
        help="Queue a CivitAI image or collection import task.",
    )
    import_civitai_parser.add_argument(
        "import_type",
        choices=("image", "collection"),
        help="Requested import type. URLs are auto-detected by the backend when possible.",
    )
    import_civitai_parser.add_argument(
        "value",
        help="CivitAI image/collection URL or numeric ID.",
    )
    import_civitai_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional backend limit for collection imports.",
    )
    import_civitai_parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll the queued import task until it reaches a terminal state.",
    )
    import_civitai_parser.set_defaults(handler=cmd_import_civitai)

    sync_civitai_parser = subparsers.add_parser(
        "sync-civitai-collections",
        help="Queue a sync of the current user's CivitAI collections.",
    )
    sync_civitai_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional backend limit for collection sync.",
    )
    sync_civitai_parser.add_argument(
        "--wait",
        action="store_true",
        help="Poll the queued sync task until it reaches a terminal state.",
    )
    sync_civitai_parser.set_defaults(handler=cmd_sync_civitai_collections)

    wait_task_parser = subparsers.add_parser(
        "wait-task",
        help="Poll a background task until it completes, fails, or is cancelled.",
    )
    wait_task_parser.add_argument("task_id", help="Background task id.")
    wait_task_parser.set_defaults(handler=cmd_wait_task)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run basic API connectivity and JSON endpoint checks.",
    )
    doctor_parser.set_defaults(handler=cmd_doctor)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.pretty = not bool(args.compact)

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    try:
        return int(args.handler(args, session))
    except ApiCommandError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())