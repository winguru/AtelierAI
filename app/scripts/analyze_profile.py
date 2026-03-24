#!/usr/bin/env python3
"""Analyze the logged-in CivitAI profile and classify accessible collections.

Outputs collection metadata with IDs and URLs so the results can be fed into:
- backend import endpoint (`/import_civitai/`, import_type=collection)
- tag bootstrap export workflows (`export_civitai_tags_for_taxonomy.py`)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

# Ensure local src imports resolve when script is executed directly.
from path_setup import PROJECT_ROOT  # noqa: F401
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.console_utils import ConsoleFormatter


def _slugify(text: str) -> str:
    base = (text or "").strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", base)
    return re.sub(r"-+", "-", base).strip("-")


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _try_fetch_profile(api: CivitaiAPI) -> dict[str, Any]:
    """Best-effort profile fetch; silently returns empty dict if endpoint is unavailable."""
    try:
        response = api.session.get(
            f"{api.base_url}/session.get",
            headers=api._get_headers(),
            params={"input": api._build_trpc_payload({})},
            timeout=20,
        )
    except requests.RequestException:
        return {}

    if response.status_code != 200:
        return {}

    try:
        data = response.json()
    except ValueError:
        return {}

    payload = data.get("result", {}).get("data", {}).get("json")
    user = payload.get("user") if isinstance(payload, dict) else None
    if not isinstance(user, dict):
        return {}

    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("name"),
        "image": user.get("image"),
    }


def _normalize_collection_row(row: dict[str, Any]) -> dict[str, Any] | None:
    collection_id = _safe_int(row.get("id"))
    if collection_id is None:
        return None

    name = str(row.get("name") or "").strip() or f"Collection {collection_id}"
    civitai_url = f"https://civitai.com/collections/{collection_id}"

    cover = row.get("image") if isinstance(row.get("image"), dict) else {}

    collection_type = str(row.get("type") or "").strip()
    is_image_collection = collection_type.lower() == "image"

    # Value can be either ID or full URL for /import_civitai/. URL is more user-facing.
    import_value = civitai_url

    import_request: dict[str, Any] | None = None
    tag_bootstrap_hint: dict[str, Any] | None = None
    if is_image_collection:
        import_request = {
            "import_type": "collection",
            "value": import_value,
            "limit": None,
        }
        tag_bootstrap_hint = {
            "collection_id": collection_id,
            "example_command": (
                "python scripts/export_civitai_tags_for_taxonomy.py "
                f"--collection-id {collection_id} --output data/civitai_tags_{collection_id}.json"
            ),
        }

    return {
        "id": collection_id,
        "name": name,
        "description": row.get("description"),
        "type": collection_type,
        "is_image_collection": is_image_collection,
        "supports_image_import": is_image_collection,
        "supports_tag_bootstrap": is_image_collection,
        "is_owner": bool(row.get("isOwner")) if row.get("isOwner") is not None else None,
        "read": row.get("read"),
        "write": row.get("write"),
        "user_id": _safe_int(row.get("userId")),
        "cover_image_url": cover.get("url") if isinstance(cover, dict) else None,
        "url": civitai_url,
        "import_request": import_request,
        "tag_bootstrap_hint": tag_bootstrap_hint,
    }


def _fetch_collections(api: CivitaiAPI) -> list[dict[str, Any]]:
    data = api._make_request("collection.getAllUser", {"authed": True})
    if not isinstance(data, list):
        return []

    normalized: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        item = _normalize_collection_row(row)
        if item is not None:
            normalized.append(item)

    normalized.sort(key=lambda x: str(x.get("name") or "").lower())
    return normalized


def _fallback_profile_from_collections(collections: list[dict[str, Any]]) -> dict[str, Any]:
    user_id_set: set[int] = set()
    for collection in collections:
        raw_user_id = collection.get("user_id")
        if isinstance(raw_user_id, int):
            user_id_set.add(raw_user_id)

    user_ids = sorted(user_id_set)
    return {
        "id": user_ids[0] if len(user_ids) == 1 else None,
        "username": None,
        "name": None,
        "image": None,
        "user_ids_seen_in_collections": user_ids,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List the logged-in CivitAI user's collections with IDs and URLs."
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/profile_collections.json",
        help="Output JSON path (default: data/profile_collections.json)",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Also print the full JSON payload to stdout.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Show all collection types. Default shows only image collections.",
    )
    parser.add_argument(
        "-s",
        "--short",
        action="store_true",
        help="Show shortened URL paths without the https://civitai.com prefix.",
    )
    return parser.parse_args()


def _collection_type_counts(collections: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for collection in collections:
        key = str(collection.get("type") or "Unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: kv[0].lower()))


def _print_profile_report(
    formatter: ConsoleFormatter,
    profile: dict[str, Any],
    payload: dict[str, Any],
    collections_to_print: list[dict[str, Any]],
    output_path: str,
    short_urls: bool,
    show_all: bool,
) -> None:
    username = profile.get("username") if isinstance(profile, dict) else None
    user_id = profile.get("id") if isinstance(profile, dict) else None

    formatter.print_header("CivitAI Profile Collection Analysis")
    summary_rows = [
        ["Profile User", username or "unknown"],
        ["Profile User ID", user_id if user_id is not None else "unknown"],
        ["Collections Found", payload.get("collection_count", 0)],
        ["Image Collections", payload.get("image_collection_count", 0)],
        ["Output File", output_path],
    ]
    formatter.print_table(headers=["Metric", "Value"], rows=summary_rows)
    formatter.print_blank()

    type_rows = [
        [type_name, count]
        for type_name, count in dict(payload.get("collection_type_counts") or {}).items()
    ]
    formatter.print_subheader("Collection Types")
    if type_rows:
        formatter.print_table(headers=["Type", "Count"], rows=type_rows)
    else:
        formatter.print_info_item("No type data available.")
    formatter.print_blank()

    formatter.print_subheader("Collections")
    if not collections_to_print:
        formatter.print_warning("No collections to display for the selected filter.")
        return

    collection_rows: list[list[Any]] = []
    for col in collections_to_print:
        full_url = str(col.get("url") or "")
        display_url = full_url.replace("https://civitai.com", "") if short_urls else full_url
        if not display_url:
            display_url = full_url
        if show_all:
            collection_rows.append(
                [
                    col.get("id"),
                    col.get("type") or "Unknown",
                    col.get("name") or "",
                    display_url,
                ]
            )
        else:
            # Image-only mode: type/image columns are redundant.
            collection_rows.append(
                [
                    col.get("id"),
                    col.get("name") or "",
                    display_url,
                ]
            )

    formatter.print_table(
        headers=(
            ["ID", "Type", "Name", "URL" if not short_urls else "URL Path"]
            if show_all
            else ["ID", "Name", "URL" if not short_urls else "URL Path"]
        ),
        rows=collection_rows,
        keep_headers=True,
    )


def main() -> None:
    args = parse_args()
    api = CivitaiAPI.get_instance()

    profile = _try_fetch_profile(api)
    collections = _fetch_collections(api)
    if not profile:
        profile = _fallback_profile_from_collections(collections)

    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source": "civitai.collection.getAllUser",
        "profile": profile,
        "collection_count": len(collections),
        "collection_type_counts": _collection_type_counts(collections),
        "image_collection_count": sum(1 for c in collections if c.get("is_image_collection") is True),
        "image_collection_ids": [c["id"] for c in collections if c.get("is_image_collection") is True],
        "collections": collections,
        "image_collections": [c for c in collections if c.get("is_image_collection") is True],
    }

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    collections_to_print = (
        collections
        if args.all
        else [c for c in collections if c.get("is_image_collection") is True]
    )

    formatter = ConsoleFormatter()
    _print_profile_report(
        formatter=formatter,
        profile=profile,
        payload=payload,
        collections_to_print=collections_to_print,
        output_path=args.output,
        short_urls=args.short,
        show_all=args.all,
    )
    formatter.print_blank()
    formatter.print_info_item(
        "Import hint: POST /import_civitai/ with {\"import_type\":\"collection\",\"value\":\"<collection_url_or_id>\"}."
    )

    if args.print_json:
        print()
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
