#!/usr/bin/env python3
"""Profile GalleryQuery pipeline step by step.

Runs each section independently to find bottlenecks:
  1. Filter resolution (parse + apply → constrained_ids)
  2. Summary computation (total_images, total_tags, top_tags)
  3. Image page fetch (DB query + display-item building)
  4. Tag details

Usage:
    cd app/
    PYTHONPATH='.:backend/:src/:dev/' python3 scripts/profile_gallery_query.py
"""

import os
import sys
import time
import statistics

# ── Setup ────────────────────────────────────────────────────────────────────
_app_dir = os.path.dirname(os.path.abspath(__file__)) + "/.."
os.chdir(_app_dir)
sys.path.insert(0, ".")
sys.path.insert(0, "backend")
sys.path.insert(0, "src")


def _time_fn(label, fn, iterations=3):
    """Run fn N times, report stats."""
    times = []
    result = None
    for i in range(iterations):
        t0 = time.perf_counter()
        result = fn()
        t1 = time.perf_counter()
        elapsed = (t1 - t0) * 1000
        times.append(elapsed)

    med = statistics.median(times)
    mn = min(times)
    mx = max(times)
    print(f"  {label:50s}  min={mn:7.1f}ms  med={med:7.1f}ms  max={mx:7.1f}ms")
    return result


def profile():
    from backend.database import SessionLocal
    from backend.services.gallery_query import GalleryQuery
    from backend.services.query_model import (
        GalleryFilter,
        GalleryQueryRequest,
        ImagePageSpec,
        SummarySpec,
        TagDetailSpec,
    )
    import backend.main as main_module

    db = SessionLocal()
    gq = GalleryQuery(
        db=db,
        query_service=main_module.image_query_service,
        image_library_path=main_module.IMAGE_LIBRARY_PATH,
        image_resources_path=main_module.IMAGE_RESOURCES_PATH,
        active_image_filter=main_module._active_image_filter,
        apply_image_list_filters=main_module._apply_image_list_filters,
        build_display_items_for_image=main_module._build_display_items_for_image,
        merge_duplicate_grouped_items=main_module._merge_duplicate_grouped_items,
        read_nsfw_ratings_for_image=main_module._read_nsfw_ratings_for_image,
        get_video_poster_path=main_module.get_video_poster_path,
        get_video_thumbnail_path=main_module.get_video_thumbnail_path,
        image_data_from_db=main_module.ImageData.from_db_record,
    )

    # ── Scenarios ────────────────────────────────────────────────────────────
    scenarios = [
        {
            "name": "No filter",
            "filter": GalleryFilter(),
            "search": None,
        },
        {
            "name": "Filter: collection=MLP, tag=anthro, exclude=futa, hide nsfwLevel:1",
            "filter": GalleryFilter(
                included={"collection": "MLP", "tag": "anthro"},
                excluded={"tag": "futa"},
                hidden={"nsfwLevel": "1"},
            ),
            "search": None,
        },
    ]

    for scenario in scenarios:
        print()
        print("=" * 78)
        print(f"📋 {scenario['name']}")
        print("=" * 78)

        f = scenario["filter"]
        search = scenario["search"]

        # ── Step 1: Filter resolution ────────────────────────────────────────
        print("\n── Step 1: Filter resolution ──")
        constrained_ids = _time_fn(
            "_resolve_filter()", lambda: gq._resolve_filter(f, search)
        )
        if constrained_ids is not None:
            print(f"    → constrained_ids: {len(constrained_ids)} images")
        else:
            print(f"    → constrained_ids: None (all images)")

        # ── Step 2: Summary sub-queries ──────────────────────────────────────
        print("\n── Step 2: Summary sub-queries ──")
        _time_fn(
            "_count_images()",
            lambda: gq._count_images(constrained_ids, search),
        )
        _time_fn(
            "_query_top_tags(limit=20)",
            lambda: gq._query_top_tags(constrained_ids, 20),
        )
        _time_fn(
            "_compute_summary() [all fields]",
            lambda: gq._compute_summary(
                SummarySpec(fields=["total_images", "total_tags", "top_tags"]),
                constrained_ids,
                search,
            ),
        )

        # ── Step 3: Image page ──────────────────────────────────────────────
        print("\n── Step 3: Image page ──")
        spec = ImagePageSpec(limit=50, group_variants=True)
        _time_fn(
            "_fetch_image_page(limit=50, grouped)",
            lambda: gq._fetch_image_page(spec, constrained_ids, search),
        )

        # ── Step 4: Tag details ─────────────────────────────────────────────
        print("\n── Step 4: Tag details ──")
        _time_fn(
            "_compute_tag_details()",
            lambda: gq._compute_tag_details(TagDetailSpec(), constrained_ids),
        )

        # ── Full pipeline combos ────────────────────────────────────────────
        print("\n── Full pipeline combos ──")

        req_images = GalleryQueryRequest(
            filter=f, search=search,
            images=ImagePageSpec(limit=50, group_variants=True),
        )
        _time_fn(
            "execute() — images only",
            lambda: gq.execute(req_images),
        )

        req_images_summary = GalleryQueryRequest(
            filter=f, search=search,
            summary=SummarySpec(fields=["total_images", "total_tags", "top_tags"]),
            images=ImagePageSpec(limit=50, group_variants=True),
        )
        _time_fn(
            "execute() — images + summary",
            lambda: gq.execute(req_images_summary),
        )

        req_all = GalleryQueryRequest(
            filter=f, search=search,
            summary=SummarySpec(fields=["total_images", "total_tags", "top_tags"]),
            images=ImagePageSpec(limit=50, group_variants=True),
            tags=TagDetailSpec(),
        )
        _time_fn(
            "execute() — images + summary + tags",
            lambda: gq.execute(req_all),
        )

    db.close()
    print("\n✅ Profiling complete.")


if __name__ == "__main__":
    profile()
