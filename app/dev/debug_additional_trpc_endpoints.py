#!/usr/bin/env python3
"""Probe additional CivitAI tRPC endpoints and summarize payload/response shape."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Ensure local package imports work when script is run from app/.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from atelierai.civitai.civitai_api import CivitaiAPI  # noqa: E402


def summarize_response(data: Any) -> dict[str, Any]:
    """Create a concise structural summary for terminal output."""
    summary: dict[str, Any] = {"type": type(data).__name__}
    if isinstance(data, dict):
        keys = list(data.keys())
        summary["keys"] = keys[:25]
        summary["key_count"] = len(keys)
        for key in ("nextCursor", "cursor", "items", "collections"):
            if key in data:
                value = data.get(key)
                summary[f"{key}_type"] = type(value).__name__
                if isinstance(value, list):
                    summary[f"{key}_count"] = len(value)
    elif isinstance(data, list):
        summary["count"] = len(data)
        if data:
            summary["item_type"] = type(data[0]).__name__
            if isinstance(data[0], dict):
                summary["first_item_keys"] = list(data[0].keys())[:25]
    return summary


def save_debug_output(endpoint: str, payload: dict[str, Any], response: Any) -> Path:
    """Persist response payloads for later inspection."""
    output_dir = PROJECT_ROOT / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_endpoint = endpoint.replace(".", "_")
    output_path = output_dir / f"debug_{safe_endpoint}_response.json"

    dump = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "payload": payload,
        "response": response,
    }

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(dump, f, indent=2)

    return output_path


def try_endpoint(api: CivitaiAPI, endpoint: str, payload_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Try payload variants until one returns data."""
    print("\n" + "=" * 78)
    print(f"Endpoint: {endpoint}")
    print("=" * 78)

    for idx, payload in enumerate(payload_candidates, start=1):
        print(f"\nAttempt {idx}: payload={payload}")
        response = api._make_request(endpoint=endpoint, payload_data=payload)

        if response is None:
            print("  -> No data (request failed or endpoint/payload rejected)")
            continue

        summary = summarize_response(response)
        print(f"  -> Success: {summary}")
        output_path = save_debug_output(endpoint, payload, response)
        print(f"  -> Saved full response to: {output_path}")
        return {
            "endpoint": endpoint,
            "success": True,
            "payload": payload,
            "summary": summary,
            "output_file": str(output_path),
        }

    return {
        "endpoint": endpoint,
        "success": False,
        "payload": None,
        "summary": None,
        "output_file": None,
    }


def main() -> int:
    api = CivitaiAPI.get_instance()

    tests: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "system.getBrowsingSettingAddons",
            [
                {"authed": True},
                {},
            ],
        ),
        (
            "hiddenPreferences.getHidden",
            [
                {"authed": True},
                {"type": "image", "authed": True},
                {},
            ],
        ),
        (
            "collection.getAllUser",
            [
                {"authed": True},
                {"limit": 50, "authed": True},
                {"cursor": None, "authed": True},
                {},
            ],
        ),
    ]

    results: list[dict[str, Any]] = []
    for endpoint, payloads in tests:
        results.append(try_endpoint(api, endpoint, payloads))

    print("\n" + "=" * 78)
    print("RESULT SUMMARY")
    print("=" * 78)
    for item in results:
        if item["success"]:
            print(f"[OK] {item['endpoint']} payload={item['payload']} file={item['output_file']}")
        else:
            print(f"[FAIL] {item['endpoint']} (no successful payload variant)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
