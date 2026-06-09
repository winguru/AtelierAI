"""backfill_civitai_tags.py

Backfill image_concept_observations for images that have a civitai_image_id but
no existing concept observations (typically ~10K images imported via the recent
collection-import path that skipped tag.getVotableTags).

Three-tier resolution strategy (cheapest first):
  Tier 1 – Page-file index: scan saved getInfinite page archives under
            --page-dirs for {civitai_image_id → [tagIds]}.  Zero API calls.
    Tier 1b – Optional collection crawl: query your local collection membership,
                        crawl CivitAI collection feeds (image.getInfinite and optional
                        post.getInfinite fallback), and use per-image tagIds in bulk.
  Tier 2 – DB cache: call api.fetch_image_tag_records_cached(cache_only=True)
            for images still missing after Tier 1.  Zero API calls.
  Tier 3 – Live API: call api.fetch_image_tag_records_cached() (live fallback)
            for images still missing after Tier 2.  Capped by --api-limit
            (default 100).  Each response is written to the DB cache by the
            Phase-2 write-through, so future runs can skip this tier.

For every resolved tagIds list we do a single SQL join against authority_terms
WHERE external_tag_id IN (...) to find which tag IDs are already in the taxonomy,
then insert image_concept_observations (one row per matched authority_term).

Usage:
  python backfill_civitai_tags.py [--dry-run] [--api-limit 100]
      [--prefill-collections]
      [--page-dirs app/data/getinfinite_main-images_month_newest_...
                   app/data/getinfinite_day_...]
      [--commit-every 250]

Run from the repo root with the virtualenv active.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Path setup — same pattern as other scripts in app/scripts/
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))
from path_setup import PROJECT_ROOT  # noqa: F401,E402  adds src/ to sys.path

from atelierai.civitai.civitai_api import CivitaiAPI  # noqa: E402
from atelierai.civitai.http_client import CivitaiHttpClient  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CIVITAI_AUTHORITY_ID = 1   # tag_authorities.id WHERE name='civitai'
_OBSERVATION_SOURCE_IMPORT = 1
_OBSERVATION_CERTAINTY_LIKELY = 1

# Default page-archive directories to scan (relative to repo root)
_DEFAULT_PAGE_DIRS = [
    Path("app/data/getinfinite_main-images_month_newest_20260323_210022"),
    Path("app/data/getinfinite_day_20260323_141850"),
    Path("app/data/getinfinite_day_20260323_141251"),
    Path("app/data/getinfinite_main-images_week_most-reactions_20260426_124922"),
]

# ---------------------------------------------------------------------------
# Tier 1 – page-file index builder
# ---------------------------------------------------------------------------

def _build_page_index(page_dirs: list[Path]) -> dict[int, list[int]]:
    """Scan getInfinite page files and return {civitai_image_id: [tagId, ...]}.

    Reads each page_*.json file and extracts the tagIds list per image item.
    Only entries with a non-empty tagIds list are included.
    """
    index: dict[int, list[int]] = {}
    for page_dir in page_dirs:
        if not page_dir.is_dir():
            continue
        page_files = sorted(page_dir.glob("page_*.json"))
        print(f"  Scanning {len(page_files):,} page files in {page_dir.name} …")
        for pf in page_files:
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = data.get("items", [])
            if not items:
                # tRPC envelope format
                items = (
                    data.get("result", {})
                    .get("data", {})
                    .get("json", {})
                    .get("items", [])
                )
            for item in items:
                civ_id = item.get("id")
                tag_ids = item.get("tagIds", [])
                if civ_id is not None and tag_ids:
                    index[int(civ_id)] = [int(t) for t in tag_ids if t is not None]
    return index


def _remaining_collection_ids(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return CivitAI collection IDs that contain images missing CivitAI observations."""
    rows = conn.execute(
        """
        SELECT DISTINCT m.civitai_collection_id, c.name
        FROM collection_civitai_mappings m
        JOIN collections c ON c.id = m.collection_id
        JOIN image_collections ic ON ic.collection_id = c.id
        JOIN images i ON i.id = ic.image_id
                WHERE i.civitai_image_id IS NOT NULL
                    AND NOT EXISTS (
                            SELECT 1
                            FROM image_concept_observations o
                            WHERE o.image_id = i.id
                                AND o.authority_id = 1
                    )
        UNION
        SELECT DISTINCT c.civitai_collection_id, c.name
        FROM collections c
        JOIN image_collections ic ON ic.collection_id = c.id
        JOIN images i ON i.id = ic.image_id
                WHERE c.civitai_collection_id IS NOT NULL
                    AND i.civitai_image_id IS NOT NULL
                    AND NOT EXISTS (
                            SELECT 1
                            FROM image_concept_observations o
                            WHERE o.image_id = i.id
                                AND o.authority_id = 1
                    )
                    AND c.civitai_collection_id NOT IN (
                        SELECT civitai_collection_id FROM collection_civitai_mappings
                    )
        ORDER BY 2
        """
    ).fetchall()
    return [(int(cid), str(name or f"collection_{cid}")) for cid, name in rows]


def _extract_tag_ids(item: dict[str, Any]) -> list[int]:
    raw = item.get("tagIds")
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for tag_id in raw:
        try:
            out.append(int(tag_id))
        except (TypeError, ValueError):
            continue
    return out


def _build_collection_type_map(
    api: CivitaiAPI, verbose: bool = False
) -> dict[int, str]:
    """Query collection.getAllUser to get collection types.

    Returns {collection_id: type}, e.g. {16393827: "Post", 11904849: "Image"}.
    """
    collection_map: dict[int, str] = {}
    try:
        payload = {"authed": True}
        response = api.get_cached_or_fetch("collection.getAllUser", payload)
        if isinstance(response, list):
            for coll in response:
                if isinstance(coll, dict):
                    coll_id = coll.get("id")
                    coll_type = coll.get("type", "Unknown")
                    if coll_id is not None:
                        collection_map[int(coll_id)] = str(coll_type)
                        if verbose:
                            print(f"    Collection {coll_id}: type={coll_type}")
    except Exception as e:
        if verbose:
            print(f"    Warning: Failed to fetch collection types: {e}")
    return collection_map


def _prefill_from_collections(
    *,
    conn: sqlite3.Connection,
    api: CivitaiAPI,
    need: dict[int, int],
    now_str: str,
    commit_every: int,
    request_delay: float,
    dry_run: bool,
    verbose: bool,
    collection_type_map: dict[int, str],
) -> tuple[int, int, int, int, int]:
    """Prefill remaining images by crawling CivitAI collections.

    Uses collection_type_map to determine per-collection endpoint:
    - Type "Image": use image.getInfinite
    - Type "Post": use post.getInfinite then image.getInfinite per post
    - Unknown type: tries image.getInfinite first

    Returns a tuple: (resolved_images, inserted_observations, api_calls,
                      images_from_images, images_from_posts).
    """
    collections = _remaining_collection_ids(conn)
    if not collections:
        return (0, 0, 0, 0, 0)

    resolved = 0
    inserted = 0
    api_calls = 0
    writes_since_commit = 0
    images_from_images = 0
    images_from_posts = 0

    print(
        f"Tier 1b — collection crawl prefill over {len(collections)} collections …"
    )

    for coll_idx, (collection_id, collection_name) in enumerate(collections, start=1):
        if not need:
            break

        collection_type = collection_type_map.get(int(collection_id), "Unknown")

        if verbose:
            print(
                f"  [Collection {coll_idx}/{len(collections)}] "
                f"{collection_name} ({collection_id}) [type={collection_type}]"
            )

        # Determine which endpoint(s) to try based on collection type
        use_post_api_first = collection_type == "Post"

        # If Post type, try post.getInfinite directly.
        # Otherwise, try image.getInfinite.
        # Unknown types default to image.getInfinite first.

        if use_post_api_first:
            # Post-type collection: crawl posts then their images
            posts_payload: dict[str, Any] = {
                **api.default_params,
                "collectionId": int(collection_id),
                "sort": "Newest",
            }
            posts_payload.pop("cursor", None)
            post_cursor: Optional[str] = None

            while need:
                if not dry_run:
                    conn.commit()

                started = time.monotonic()
                if post_cursor is not None:
                    posts_payload["cursor"] = post_cursor
                posts_response = api.get_cached_or_fetch(
                    "post.getInfinite", posts_payload
                )
                api_calls += 1
                elapsed = time.monotonic() - started

                if not isinstance(posts_response, dict):
                    break

                posts = posts_response.get("items", [])
                if not posts:
                    break

                for post in posts:
                    post_id = post.get("id")
                    try:
                        post_id_int = int(post_id)
                    except (TypeError, ValueError):
                        continue

                    images_payload: dict[str, Any] = {
                        **api.default_params,
                        "postId": post_id_int,
                        "cursor": None,
                    }
                    images_payload.pop("collectionId", None)

                    while need:
                        if not dry_run:
                            conn.commit()

                        started_img = time.monotonic()
                        post_images_response = api.get_cached_or_fetch(
                            "image.getInfinite", images_payload
                        )
                        api_calls += 1
                        elapsed_img = time.monotonic() - started_img

                        if not isinstance(post_images_response, dict):
                            break
                        post_images = api._find_deep_image_list(post_images_response)
                        if not post_images:
                            break

                        for item in post_images:
                            if not isinstance(item, dict):
                                continue
                            civ_id_raw = item.get("id")
                            try:
                                civ_id = int(civ_id_raw)
                            except (TypeError, ValueError):
                                continue
                            img_id = need.get(civ_id)
                            if img_id is None:
                                continue
                            tag_ids = _extract_tag_ids(item)
                            if not tag_ids:
                                continue
                            n = _insert_observations(
                                conn, img_id, civ_id, tag_ids, now_str, dry_run
                            )
                            inserted += n
                            resolved += 1
                            images_from_posts += 1
                            writes_since_commit += 1
                            need.pop(civ_id, None)
                            if not dry_run and writes_since_commit % commit_every == 0:
                                conn.commit()

                        next_img_cursor = post_images_response.get("nextCursor")
                        if not next_img_cursor:
                            break
                        images_payload["cursor"] = next_img_cursor

                        remaining_img = request_delay - elapsed_img
                        if remaining_img > 0:
                            time.sleep(remaining_img)

                post_cursor = posts_response.get("nextCursor")
                if not post_cursor:
                    break

                remaining_post = request_delay - elapsed
                if remaining_post > 0:
                    time.sleep(remaining_post)
        else:
            # Image-type or unknown collection: use image.getInfinite
            payload: dict[str, Any] = {
                **api.default_params,
                "collectionId": int(collection_id),
                "cursor": None,
            }

            pages = 0
            while need:
                if not dry_run:
                    conn.commit()

                started = time.monotonic()
                response = api.get_cached_or_fetch("image.getInfinite", payload)
                api_calls += 1
                elapsed = time.monotonic() - started

                if verbose:
                    info = CivitaiHttpClient.get_last_request_info()
                    if info:
                        print(
                            f"    GET {info['url']}  "
                            f"(Collection {collection_id})  "
                            f"HTTP {info['status_code']}  "
                            f"{elapsed*1000:.0f}ms"
                        )

                if not isinstance(response, dict):
                    break

                items = api._find_deep_image_list(response)
                if not items:
                    break

                pages += 1
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    civ_id_raw = item.get("id")
                    try:
                        civ_id = int(civ_id_raw)
                    except (TypeError, ValueError):
                        continue

                    img_id = need.get(civ_id)
                    if img_id is None:
                        continue

                    tag_ids = _extract_tag_ids(item)
                    if not tag_ids:
                        continue

                    n = _insert_observations(
                        conn, img_id, civ_id, tag_ids, now_str, dry_run
                    )
                    inserted += n
                    resolved += 1
                    images_from_images += 1
                    writes_since_commit += 1
                    need.pop(civ_id, None)

                    if not dry_run and writes_since_commit % commit_every == 0:
                        conn.commit()

                next_cursor = response.get("nextCursor")
                if not next_cursor:
                    break
                payload["cursor"] = next_cursor

                remaining = request_delay - elapsed
                if remaining > 0:
                    time.sleep(remaining)

    if not dry_run and writes_since_commit:
        conn.commit()

    print(
        f"  Tier 1b done: {resolved:,} images resolved "
        f"({images_from_images:,} from image API, {images_from_posts:,} from posts), "
        f"{len(need):,} remaining, {api_calls:,} collection API calls\n"
    )
    return (resolved, inserted, api_calls, images_from_images, images_from_posts)


# ---------------------------------------------------------------------------
# Observation writer (raw sqlite3 for speed — avoids ORM overhead on 10K rows)
# ---------------------------------------------------------------------------

def _insert_observations(
    conn: sqlite3.Connection,
    image_id: int,
    civitai_image_id: int,
    tag_ids: list[int],
    now_str: str,
    dry_run: bool,
) -> int:
    """Insert image_concept_observations for tag_ids that exist in authority_terms.

    Returns the number of rows inserted (or that would be inserted in dry-run).
    Skips tag IDs that have no matching authority_term, or whose authority_term
    has no concept_id.  Idempotent: existing rows are silently skipped.
    """
    if not tag_ids:
        return 0

    placeholders = ",".join("?" * len(tag_ids))
    # concept_id is optional organisational structure — include terms with concept_id=NULL
    terms = conn.execute(
        f"""
        SELECT id, concept_id
        FROM authority_terms
        WHERE authority_id = {_CIVITAI_AUTHORITY_ID}
          AND external_tag_id IN ({placeholders})
        """,
        tag_ids,
    ).fetchall()

    if not terms:
        return 0

    inserted = 0
    for term_id, concept_id in terms:
        # concept_id may be None — that's valid; the unique constraint is on
        # (image_id, authority_term_id), not on concept_id.
        if not dry_run:
            conn.execute(
                """
                INSERT OR IGNORE INTO image_concept_observations
                  (image_id, concept_id, authority_id, authority_term_id,
                   source_type, certainty_label, is_present, is_curated,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    image_id,
                    concept_id,  # may be None
                    _CIVITAI_AUTHORITY_ID,
                    term_id,
                    _OBSERVATION_SOURCE_IMPORT,
                    _OBSERVATION_CERTAINTY_LIKELY,
                    now_str,
                    now_str,
                ),
            )
        inserted += 1

    return inserted


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def run_backfill(
    *,
    db_path: Path,
    page_dirs: list[Path],
    skip_tier1: bool,
    prefill_collections: bool,
    api_limit: int,
    commit_every: int,
    request_delay: float,
    verbose: bool,
    dry_run: bool,
) -> None:
    started_at = time.monotonic()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Load affected images: have civitai_image_id, no CivitAI observations
    # ------------------------------------------------------------------
    print("Loading affected images …")
    affected = conn.execute(
        """
        SELECT i.id, i.civitai_image_id
        FROM images i
                WHERE i.civitai_image_id IS NOT NULL
                    AND NOT EXISTS (
                            SELECT 1
                            FROM image_concept_observations o
                            WHERE o.image_id = i.id
                                AND o.authority_id = 1
                    )
        ORDER BY i.civitai_image_id
        """
    ).fetchall()
    print(f"  {len(affected):,} images need observations\n")

    if not affected:
        print("Nothing to do.")
        conn.close()
        return

    # index: {civitai_image_id: db_image_id}
    need: dict[int, int] = {civ_id: img_id for img_id, civ_id in affected}

    stats: dict[str, int] = {
        "tier1_resolved": 0,
        "tier1b_resolved": 0,
        "tier1b_api_calls": 0,
        "tier1b_images_from_images": 0,
        "tier1b_images_from_posts": 0,
        "tier2_resolved": 0,
        "tier3_resolved": 0,
        "tier3_api_calls": 0,
        "obs_inserted": 0,
        "no_tags_found": 0,
        "images_with_no_terms": 0,
    }
    timings: dict[str, float] = {
        "tier1_seconds": 0.0,
        "tier1b_seconds": 0.0,
        "tier2_seconds": 0.0,
        "tier3_seconds": 0.0,
        "total_seconds": 0.0,
    }

    # ------------------------------------------------------------------
    # Tier 1 – Page-file index
    # ------------------------------------------------------------------
    tier_started = time.monotonic()
    if skip_tier1:
        print("Tier 1 — skipped by --skip-tier1\n")
    else:
        print("Tier 1 — scanning page-file archives …")
        page_index = _build_page_index(page_dirs)
        print(f"  Page index built: {len(page_index):,} civitai image IDs with tagIds\n")

        tier1_hits: dict[int, list[int]] = {}   # {civitai_image_id: [tagIds]}
        for civ_id in list(need.keys()):
            if civ_id in page_index:
                tier1_hits[civ_id] = page_index[civ_id]

        if tier1_hits:
            print(f"Tier 1 writing observations for {len(tier1_hits):,} images …")
            batch = 0
            for civ_id, tag_ids in tier1_hits.items():
                img_id = need.pop(civ_id)
                n = _insert_observations(conn, img_id, civ_id, tag_ids, now_str, dry_run)
                stats["obs_inserted"] += n
                if n == 0:
                    stats["images_with_no_terms"] += 1
                stats["tier1_resolved"] += 1
                batch += 1
                if not dry_run and batch % commit_every == 0:
                    conn.commit()
                    print(f"  … committed {batch:,} / {len(tier1_hits):,}")
            if not dry_run:
                conn.commit()
            print(f"  Tier 1 done: {stats['tier1_resolved']:,} images resolved\n")
        else:
            print("  Tier 1: no page-file matches found\n")
    timings["tier1_seconds"] = time.monotonic() - tier_started

    if not need:
        timings["total_seconds"] = time.monotonic() - started_at
        _print_summary(stats, timings, dry_run)
        conn.close()
        return

    # ------------------------------------------------------------------
    # Tier 1b – Optional collection crawl prefill
    # ------------------------------------------------------------------
    tier_started = time.monotonic()
    api = CivitaiAPI.get_instance()

    # Build collection type map for intelligent endpoint selection
    collection_type_map: dict[int, str] = {}
    if prefill_collections:
        print("Tier 1b — fetching collection metadata …")
        collection_type_map = _build_collection_type_map(api, verbose=verbose)
        print()

    if prefill_collections:
        (
            resolved_1b,
            inserted_1b,
            api_calls_1b,
            images_from_images_1b,
            images_from_posts_1b,
        ) = _prefill_from_collections(
            conn=conn,
            api=api,
            need=need,
            now_str=now_str,
            commit_every=commit_every,
            request_delay=request_delay,
            dry_run=dry_run,
            verbose=verbose,
            collection_type_map=collection_type_map,
        )
        stats["tier1b_resolved"] = resolved_1b
        stats["tier1b_api_calls"] = api_calls_1b
        stats["tier1b_images_from_images"] = images_from_images_1b
        stats["tier1b_images_from_posts"] = images_from_posts_1b
        stats["obs_inserted"] += inserted_1b
    else:
        print("Tier 1b — collection crawl prefill disabled\n")
    timings["tier1b_seconds"] = time.monotonic() - tier_started

    if not need:
        timings["total_seconds"] = time.monotonic() - started_at
        _print_summary(stats, timings, dry_run)
        conn.close()
        return

    # ------------------------------------------------------------------
    # Tier 2 – DB cache (cache_only=True)
    # ------------------------------------------------------------------
    tier_started = time.monotonic()
    print(f"Tier 2 — DB cache lookup for {len(need):,} remaining images …")
    tier2_hits = 0
    still_need: list[tuple[int, int]] = []  # [(civitai_image_id, db_image_id)]

    for civ_id, img_id in need.items():
        tag_records = api.fetch_image_tag_records_cached(civ_id, cache_only=True)
        if tag_records:
            tag_ids = [int(t["id"]) for t in tag_records if isinstance(t.get("id"), (int, float, str))]
            n = _insert_observations(conn, img_id, civ_id, tag_ids, now_str, dry_run)
            stats["obs_inserted"] += n
            if n == 0:
                stats["images_with_no_terms"] += 1
            tier2_hits += 1
        else:
            still_need.append((civ_id, img_id))

    stats["tier2_resolved"] = tier2_hits
    if not dry_run and tier2_hits:
        conn.commit()
    print(f"  Tier 2 done: {tier2_hits:,} images resolved, {len(still_need):,} still need API calls\n")
    timings["tier2_seconds"] = time.monotonic() - tier_started

    if not still_need:
        timings["total_seconds"] = time.monotonic() - started_at
        _print_summary(stats, timings, dry_run)
        conn.close()
        return

    # ------------------------------------------------------------------
    # Tier 3 – Live API (capped at api_limit)
    # ------------------------------------------------------------------
    tier_started = time.monotonic()
    tier3_candidates = still_need[:api_limit]
    skipped = len(still_need) - len(tier3_candidates)
    print(
        f"Tier 3 — live API for {len(tier3_candidates):,} images "
        f"(limit={api_limit}, {skipped:,} deferred to next run) …"
    )

    batch = 0
    for idx, (civ_id, img_id) in enumerate(tier3_candidates, start=1):
        # Release the SQLite write lock BEFORE the API call so the
        # CivitAI client's separate SessionLocal can write to civitai_api_cache
        # without hitting a 30-second busy_timeout.
        if not dry_run:
            conn.commit()

        call_start = time.monotonic()
        try:
            tag_records = api.fetch_image_tag_records_cached(civ_id)
        except Exception as exc:
            print(f"  [{idx}/{len(tier3_candidates)}] civitai_id={civ_id}: API error — {exc}")
            stats["no_tags_found"] += 1
            if request_delay > 0:
                time.sleep(request_delay)
            continue

        elapsed = time.monotonic() - call_start
        stats["tier3_api_calls"] += 1

        if verbose:
            info = CivitaiHttpClient.get_last_request_info()
            if info:
                cl = info["content_length"]
                cl_str = f"{cl:,}B" if cl < 1024 else f"{cl/1024:.1f}KB"
                http_elapsed = info["elapsed_seconds"]
                http_str = f"{http_elapsed*1000:.0f}ms" if http_elapsed is not None else "?ms"
                print(
                    f"  [{idx}/{len(tier3_candidates)}] "
                    f"GET {info['url']}  "
                    f"(Image {civ_id})  "
                    f"HTTP {info['status_code']}  "
                    f"{cl_str}  "
                    f"{http_str} (http) / {elapsed*1000:.0f}ms (wall)"
                )

        if not tag_records:
            print(f"  [{idx}/{len(tier3_candidates)}] civitai_id={civ_id}: no tags ({elapsed:.1f}s)")
            stats["no_tags_found"] += 1
        else:
            tag_ids = [int(t["id"]) for t in tag_records if isinstance(t.get("id"), (int, float, str))]
            n = _insert_observations(conn, img_id, civ_id, tag_ids, now_str, dry_run)
            stats["obs_inserted"] += n
            if n == 0:
                stats["images_with_no_terms"] += 1
            stats["tier3_resolved"] += 1

        if idx % 10 == 0:
            print(f"  [{idx}/{len(tier3_candidates)}] api_calls={stats['tier3_api_calls']} obs_inserted={stats['obs_inserted']} last={elapsed:.1f}s")

        batch += 1
        if not dry_run and batch % commit_every == 0:
            conn.commit()

        # Pace requests to avoid triggering CivitAI's rate-limit cooldown.
        # Without pacing, rapid-fire requests trigger a 30s global backoff
        # that compounds across calls and can turn 100 calls into 10+ minutes.
        remaining = request_delay - elapsed
        if remaining > 0 and idx < len(tier3_candidates):
            time.sleep(remaining)

    if not dry_run and batch:
        conn.commit()

    if skipped:
        print(f"\n  NOTE: {skipped:,} images deferred — re-run to continue (each run caches responses).\n")

    timings["tier3_seconds"] = time.monotonic() - tier_started
    timings["total_seconds"] = time.monotonic() - started_at
    _print_summary(stats, timings, dry_run)
    conn.close()


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remainder = seconds - (minutes * 60)
    return f"{minutes}m {remainder:.1f}s"


def _print_summary(stats: dict[str, int], timings: dict[str, float], dry_run: bool) -> None:
    mode = "[DRY RUN] " if dry_run else ""
    print(f"\n{'='*60}")
    print(f"{mode}Backfill summary:")
    print(f"  Tier 1 (page files):  {stats['tier1_resolved']:>6,} images")
    print(
        f"  Tier 1b (collections):{stats['tier1b_resolved']:>6,} images  "
        f"({stats['tier1b_api_calls']} API calls) [{stats['tier1b_images_from_images']:,} images, {stats['tier1b_images_from_posts']:,} posts]"
    )
    print(f"  Tier 2 (DB cache):    {stats['tier2_resolved']:>6,} images")
    print(f"  Tier 3 (live API):    {stats['tier3_resolved']:>6,} images  ({stats['tier3_api_calls']} API calls)")
    total = (
        stats["tier1_resolved"]
        + stats["tier1b_resolved"]
        + stats["tier2_resolved"]
        + stats["tier3_resolved"]
    )
    print(f"  Total resolved:       {total:>6,} images")
    print(f"  No tags found:        {stats['no_tags_found']:>6,} images")
    print(f"  No matching terms:    {stats['images_with_no_terms']:>6,} images (tagIds not in authority_terms)")
    print(f"  Observations {'(would insert)' if dry_run else 'inserted'}: {stats['obs_inserted']:>6,}")
    print("  Timing:")
    print(f"    Tier 1:             {_format_duration(timings['tier1_seconds'])}")
    print(f"    Tier 1b:            {_format_duration(timings['tier1b_seconds'])}")
    print(f"    Tier 2:             {_format_duration(timings['tier2_seconds'])}")
    print(f"    Tier 3:             {_format_duration(timings['tier3_seconds'])}")
    print(f"    Total:              {_format_duration(timings['total_seconds'])}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill image_concept_observations from CivitAI tag data."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("app/image_db.sqlite"),
        help="Path to the SQLite database (default: app/image_db.sqlite)",
    )
    parser.add_argument(
        "--page-dirs",
        nargs="*",
        type=Path,
        default=_DEFAULT_PAGE_DIRS,
        help="getInfinite page archive directories to scan for Tier 1.",
    )
    parser.add_argument(
        "--skip-tier1",
        action="store_true",
        help="Skip Tier 1 page-file scanning and start at Tier 2 cache lookup.",
    )
    parser.add_argument(
        "--prefill-collections",
        action="store_true",
        help=(
            "Enable Tier 1b collection crawl prefill: crawls image.getInfinite first, "
            "then automatically crawls post.getInfinite for post-type collections. "
            "Runs before Tier 2/3."
        ),
    )

    parser.add_argument(
        "--api-limit",
        type=int,
        default=100,
        help="Maximum live API calls to make (Tier 3). Default: 100.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=250,
        help="Commit to DB every N images processed. Default: 250.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=2.0,
        help=(
            "Minimum seconds between Tier 3 live API calls. "
            "Prevents triggering CivitAI's 30s global rate-limit cooldown. "
            "Default: 2.0 (100 calls ≈ 3-4 min). Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print HTTP details (URL, status, content length, timing) for each Tier 3 API call.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and count without writing any DB rows.",
    )
    args = parser.parse_args()

    est_seconds = args.api_limit * args.request_delay
    est_display = f"{est_seconds / 60:.1f} min" if est_seconds >= 60 else f"{est_seconds:.0f}s"
    print(f"{'[DRY RUN] ' if args.dry_run else ''}CivitAI tag backfill")
    print(f"  DB:            {args.db}")
    print(f"  API limit:     {args.api_limit}")
    print(f"  Skip Tier 1:   {args.skip_tier1}")
    print(f"  Prefill collections: {args.prefill_collections}")
    print(f"  Request delay: {args.request_delay}s  (est. {est_display} for Tier 3)")
    print(f"  Dry run:       {args.dry_run}\n")

    run_backfill(
        db_path=args.db,
        page_dirs=args.page_dirs,
        skip_tier1=args.skip_tier1,
        prefill_collections=args.prefill_collections,
        api_limit=args.api_limit,
        commit_every=args.commit_every,
        request_delay=args.request_delay,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
