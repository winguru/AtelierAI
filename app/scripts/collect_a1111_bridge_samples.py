#!/usr/bin/env python3
"""Collect A1111 user_comment samples and compare with Comfy workflow exports.

This script is a one-off discovery helper for Generation Lab's A1111 Bridge flow.
It scans local images for likely A1111 metadata, optionally loads matching Comfy
workflow JSON files by file hash, calls the API analysis endpoint, and writes
JSONL results for review.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect A1111 bridge analysis samples.")
    parser.add_argument(
        "--db-path",
        default="image_db.sqlite",
        help="Path to SQLite DB (default: image_db.sqlite)",
    )
    parser.add_argument(
        "--api-base",
        default="http://localhost:8000",
        help="Base URL for AtelierAI API (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--workflow-dir",
        default="",
        help="Optional directory of Comfy workflow JSON files named <file_hash>.json",
    )
    parser.add_argument("--limit", type=int, default=100, help="Max number of samples to process")
    parser.add_argument("--offset", type=int, default=0, help="Row offset when scanning images table")
    parser.add_argument(
        "--include-generation-payload",
        action="store_true",
        help="Include full generation payload in API response output",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSONL path (default: data/a1111_bridge_samples_<timestamp>.jsonl)",
    )
    return parser.parse_args()


def parse_json_maybe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def looks_like_a1111_text(text: str) -> bool:
    sample = str(text or "").strip().lower()
    return bool(sample) and (
        "negative prompt:" in sample
        or "steps:" in sample
        or "cfg scale:" in sample
        or "sampler:" in sample
    )


def extract_candidate_text(exif_data: Any) -> str:
    payload = parse_json_maybe(exif_data)
    if not isinstance(payload, dict):
        return ""

    keys = [
        "UserComment",
        "user_comment",
        "parameters",
        "Parameters",
    ]
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and looks_like_a1111_text(text):
            return text

    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return ""


def load_workflow_json(workflow_dir: Path, file_hash: str) -> dict[str, Any] | None:
    if not workflow_dir:
        return None
    candidate_path = workflow_dir / f"{file_hash}.json"
    if not candidate_path.exists() or not candidate_path.is_file():
        return None
    try:
        parsed = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            content = response.read().decode("utf-8", errors="replace")
            data = json.loads(content) if content else {}
            return data if isinstance(data, dict) else {"ok": False, "detail": "Unexpected response payload."}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        detail = raw
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and parsed.get("detail"):
                detail = str(parsed["detail"])
        except json.JSONDecodeError:
            pass
        return {"ok": False, "detail": f"HTTP {exc.code}: {detail.strip() or exc.reason}"}
    except URLError as exc:
        return {"ok": False, "detail": f"Request failed: {exc.reason}"}


def iter_candidate_rows(db_path: Path, limit: int, offset: int):
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(
            """
            SELECT id, file_hash, source_url, file_name, exif_data
            FROM images
            WHERE image_status = 'active'
              AND exif_data IS NOT NULL
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (max(1, int(limit)), max(0, int(offset))),
        )
        for row in cursor:
            yield row
    finally:
        connection.close()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db_path).expanduser().resolve()
    if not db_path.exists():
        print(f"error: db not found: {db_path}")
        return 1

    output_path = Path(args.output).expanduser() if args.output else Path(
        f"data/a1111_bridge_samples_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    if not output_path.is_absolute():
        output_path = (Path.cwd() / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workflow_dir = Path(args.workflow_dir).expanduser().resolve() if args.workflow_dir else None

    endpoint = args.api_base.rstrip("/") + "/generation-prototype/a1111-bridge/analyze"

    processed = 0
    matched_user_comment = 0
    workflow_attached = 0
    api_ok = 0

    with output_path.open("w", encoding="utf-8") as handle:
        for row in iter_candidate_rows(db_path, args.limit, args.offset):
            file_hash = str(row["file_hash"] or "").strip()
            if not file_hash:
                continue
            exif_text = extract_candidate_text(row["exif_data"])
            if not exif_text:
                continue

            matched_user_comment += 1
            payload: dict[str, Any] = {
                "file_hash": file_hash,
                "include_generation_payload": bool(args.include_generation_payload),
            }

            workflow_json = load_workflow_json(workflow_dir, file_hash) if workflow_dir else None
            if workflow_json:
                payload["comfy_workflow_json"] = workflow_json
                workflow_attached += 1

            analysis = post_json(endpoint, payload)
            if bool(analysis.get("ok")):
                api_ok += 1

            record = {
                "image": {
                    "id": row["id"],
                    "file_hash": file_hash,
                    "file_name": row["file_name"],
                    "source_url": row["source_url"],
                },
                "input": {
                    "has_user_comment_candidate": True,
                    "workflow_attached": bool(workflow_json),
                },
                "analysis": analysis,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            processed += 1

    print(f"output: {output_path}")
    print(f"rows_scanned_limit: {args.limit}")
    print(f"candidate_rows: {matched_user_comment}")
    print(f"processed: {processed}")
    print(f"workflow_attached: {workflow_attached}")
    print(f"api_ok: {api_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
