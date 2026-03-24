#!/usr/bin/env python3
"""Export CivitAI tags to taxonomy bootstrap JSON.

This script uses CivitAI tRPC API calls to gather tags and emit a JSON file
compatible with `/taxonomy/bootstrap/import` (format=json).

Primary flow:
1) Paginate collection images via `image.getInfinite`
2) Fetch tags per image via `tag.getVotableTags`
3) Aggregate and deduplicate tags
4) Write taxonomy bootstrap JSON (`terms` list)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# Ensure local src imports resolve when script is executed directly.
from path_setup import PROJECT_ROOT  # noqa: F401
from atelierai.civitai.civitai_api import CivitaiAPI


def _normalize_tag_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _fetch_collection_image_ids(
    api: CivitaiAPI,
    collection_id: int,
    max_pages: int | None,
    max_images: int | None,
) -> list[int]:
    image_ids: list[int] = []
    seen: set[int] = set()
    cursor: str | None = None
    page = 0

    while True:
        page += 1
        payload = {
            **api.default_params,
            "collectionId": int(collection_id),
            "cursor": cursor,
        }
        response = api._make_request("image.getInfinite", payload)
        if not isinstance(response, dict):
            break

        items = response.get("items") or []
        if not isinstance(items, list) or not items:
            break

        for item in items:
            raw_id = item.get("id") if isinstance(item, dict) else None
            try:
                if raw_id is None:
                    continue
                image_id = int(str(raw_id))
            except (TypeError, ValueError):
                continue

            if image_id in seen:
                continue

            seen.add(image_id)
            image_ids.append(image_id)
            if max_images is not None and len(image_ids) >= max_images:
                return image_ids

        next_cursor = response.get("nextCursor")
        if not next_cursor:
            break

        cursor = str(next_cursor)
        if max_pages is not None and page >= max_pages:
            break

    return image_ids


def _get_collection_details(api: CivitaiAPI, collection_id: int) -> dict[str, Any]:
    data = api._make_request("collection.getById", {"id": int(collection_id), "authed": True})
    if isinstance(data, dict) and isinstance(data.get("collection"), dict):
        return data["collection"]
    if isinstance(data, dict):
        return data
    return {}


def _export_tags(
    api: CivitaiAPI,
    image_ids: list[int],
    sleep_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sleep_seconds = max(0, sleep_ms) / 1000.0

    # Keyed by authoritative external ID when available, otherwise by normalized name.
    by_key: dict[str, dict[str, Any]] = {}

    metrics: dict[str, Any] = {
        "images_scanned": len(image_ids),
        "images_with_tags": 0,
        "tag_rows_seen": 0,
        "tag_terms_exported": 0,
        "images_failed": 0,
    }

    for index, image_id in enumerate(image_ids, start=1):
        raw_tags = api._make_request(
            "tag.getVotableTags",
            {"id": int(image_id), "type": "image", "authed": True},
        )

        if not isinstance(raw_tags, list):
            metrics["images_failed"] += 1
            continue

        if raw_tags:
            metrics["images_with_tags"] += 1

        for tag in raw_tags:
            if not isinstance(tag, dict):
                continue

            raw_name = str(tag.get("name") or "").strip()
            if not raw_name:
                continue

            raw_external_id = tag.get("id")
            external_tag_id = str(raw_external_id).strip() if raw_external_id is not None else ""
            normalized_name = _normalize_tag_name(raw_name)
            if not normalized_name:
                continue

            key = f"id:{external_tag_id}" if external_tag_id else f"name:{normalized_name}"
            row = by_key.get(key)
            if row is None:
                row = {
                    "name": raw_name,
                    "external_tag_id": external_tag_id or f"name:{normalized_name}",
                    "concept_name": normalized_name,
                    "tag_type": tag.get("type"),
                    "nsfw_level": tag.get("nsfwLevel"),
                    "automated": bool(tag.get("automated")) if tag.get("automated") is not None else None,
                    "concrete": bool(tag.get("concrete")) if tag.get("concrete") is not None else None,
                    "needs_review": bool(tag.get("needsReview")) if tag.get("needsReview") is not None else None,
                    "seen_count": 0,
                    "score_sum": 0,
                    "score_max": None,
                }
                by_key[key] = row

            score = tag.get("score")
            try:
                if score is None:
                    raise ValueError("score missing")
                score_int = int(str(score))
            except (TypeError, ValueError):
                score_int = 0

            row["seen_count"] += 1
            row["score_sum"] += score_int
            row["score_max"] = score_int if row["score_max"] is None else max(row["score_max"], score_int)
            metrics["tag_rows_seen"] += 1

        if sleep_seconds > 0 and index < len(image_ids):
            time.sleep(sleep_seconds)

        if index % 50 == 0:
            print(f"Processed {index}/{len(image_ids)} images...")

    terms = sorted(by_key.values(), key=lambda t: (str(t.get("name") or "").lower(), str(t.get("external_tag_id") or "")))
    metrics["tag_terms_exported"] = len(terms)
    return terms, metrics


def _build_output(
    authority_name: str,
    collection_ids: list[int],
    terms: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    return {
        "authority_name": authority_name,
        "exported_at": now,
        "source": {
            "provider": "civitai",
            "collection_ids": collection_ids,
            "notes": "Generated via image.getInfinite + tag.getVotableTags",
        },
        "stats": metrics,
        "terms": terms,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export CivitAI tags as taxonomy bootstrap JSON."
    )
    parser.add_argument(
        "--collection-id",
        type=int,
        action="append",
        required=True,
        help="Collection ID to scan. Provide multiple times for multiple collections.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/civitai_tags_bootstrap.json",
        help="Output JSON path (default: data/civitai_tags_bootstrap.json)",
    )
    parser.add_argument(
        "--authority-name",
        type=str,
        default="civitai",
        help="Authority name to include in output metadata (default: civitai)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional page cap per collection.",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="Optional global cap on number of images scanned.",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=50,
        help="Delay between tag requests in milliseconds (default: 50).",
    )
    parser.add_argument(
        "--allow-non-image-collections",
        action="store_true",
        help="Include non-image collections (not recommended). Default behavior skips them.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api = CivitaiAPI.get_instance()

    all_image_ids: list[int] = []
    seen_ids: set[int] = set()
    scanned_collection_ids: list[int] = []
    skipped_non_image: list[dict[str, Any]] = []

    for collection_id in args.collection_id:
        details = _get_collection_details(api, int(collection_id))
        collection_type = str(details.get("type") or "").strip() if details else ""
        collection_name = str(details.get("name") or f"Collection {collection_id}")

        is_image_collection = collection_type.lower() == "image"
        if not is_image_collection and not args.allow_non_image_collections:
            skipped_non_image.append(
                {
                    "collection_id": int(collection_id),
                    "collection_name": collection_name,
                    "collection_type": collection_type or "Unknown",
                    "reason": "Non-image collection type.",
                }
            )
            print(
                f"Skipping collection {collection_id} ({collection_name}) because type is '{collection_type or 'Unknown'}'."
            )
            continue

        scanned_collection_ids.append(int(collection_id))
        print(f"Fetching image IDs for collection {collection_id}...")
        ids = _fetch_collection_image_ids(
            api=api,
            collection_id=int(collection_id),
            max_pages=args.max_pages,
            max_images=args.max_images,
        )

        for image_id in ids:
            if image_id in seen_ids:
                continue
            seen_ids.add(image_id)
            all_image_ids.append(image_id)
            if args.max_images is not None and len(all_image_ids) >= args.max_images:
                break

        if args.max_images is not None and len(all_image_ids) >= args.max_images:
            break

    print(f"Collected {len(all_image_ids)} unique image IDs across collections.")
    print("Fetching tags and building export...")

    terms, metrics = _export_tags(
        api=api,
        image_ids=all_image_ids,
        sleep_ms=args.sleep_ms,
    )

    output_payload = _build_output(
        authority_name=args.authority_name,
        collection_ids=scanned_collection_ids,
        terms=terms,
        metrics=metrics,
    )
    output_payload["source"]["requested_collection_ids"] = [int(x) for x in args.collection_id]
    output_payload["source"]["skipped_non_image_collections"] = skipped_non_image

    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    print(f"Wrote {metrics['tag_terms_exported']} terms to {args.output}")
    print("Import example:")
    print("  1) Open taxonomy admin bootstrap import")
    print("  2) Set format=json, authority_name=civitai")
    print("  3) Paste this JSON as raw_text and run dry-run first")


if __name__ == "__main__":
    main()
