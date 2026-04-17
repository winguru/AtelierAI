#!/usr/bin/env python3
"""
Smoke Test: Verify CivitAI Image ID ordering vs createdAt timestamps.

Assumption: Lower numeric CivitAI image ID = earlier upload (lower createdAt).
If this holds, the "original" is always the one with the lower ID.

Usage:
    cd app/
    PYTHONPATH='.:backend/:src/' python3 scripts/smoke_test_civitai_id_ordering.py

    # Test specific known duplicate pair:
    PYTHONPATH='.:backend/:src/' python3 scripts/smoke_test_civitai_id_ordering.py \
        --ids 105601398 105601477

    # Test a random sample from the local DB:
    PYTHONPATH='.:backend/:src/' python3 scripts/smoke_test_civitai_id_ordering.py \
        --from-db 20

    # Test random CivitAI IDs (no local DB needed):
    PYTHONPATH='.:backend/:src/' python3 scripts/smoke_test_civitai_id_ordering.py \
        --random 15
"""

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────────────
# Ensure atelierai is importable
_app_dir = Path(__file__).resolve().parent.parent  # app/
if str(_app_dir) not in sys.path:
    sys.path.insert(0, str(_app_dir))
_src_dir = _app_dir / "src"
if str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))
_backend_dir = _app_dir / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))


def parse_iso_timestamp(ts_str: str) -> datetime:
    """Parse CivitAI ISO8601 timestamp to UTC datetime."""
    if not ts_str:
        return None
    # CivitAI returns ISO format like "2025-03-14T22:30:00.000Z"
    # or "2025-03-14T22:30:00.0000000Z"
    ts_str = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        # Try stripping fractional seconds
        parts = ts_str.split(".")
        if len(parts) == 2:
            ts_str = parts[0] + "+00:00"
            return datetime.fromisoformat(ts_str)
        raise


def fetch_created_ats(image_ids: list[int], api) -> dict[int, dict]:
    """Fetch createdAt for a list of image IDs. Returns {id: {created_at, status}}."""
    results = {}
    for img_id in image_ids:
        try:
            basic_info = api.fetch_basic_info(img_id)
            if basic_info:
                created_at_str = basic_info.get("createdAt", "")
                created_at = parse_iso_timestamp(created_at_str) if created_at_str else None
                results[img_id] = {
                    "created_at": created_at,
                    "created_at_raw": created_at_str,
                    "status": "ok",
                    "url": basic_info.get("url", ""),
                }
            else:
                results[img_id] = {"created_at": None, "created_at_raw": None, "status": "no_data", "url": ""}
        except Exception as e:
            results[img_id] = {"created_at": None, "created_at_raw": None, "status": f"error: {e}", "url": ""}
    return results


def check_pair_ordering(id_a: int, id_b: int, data: dict) -> dict:
    """Check if lower ID has earlier createdAt for a pair."""
    a = data.get(id_a, {})
    b = data.get(id_b, {})

    if a.get("status") != "ok" or b.get("status") != "ok":
        return {"pair": (id_a, id_b), "verdict": "incomplete", "reason": f"a={a.get('status')} b={b.get('status')}"}

    a_time = a["created_at"]
    b_time = b["created_at"]

    if a_time is None or b_time is None:
        return {"pair": (id_a, id_b), "verdict": "missing_timestamp", "reason": f"a_ts={a_time} b_ts={b_time}"}

    lower_id = min(id_a, id_b)
    higher_id = max(id_a, id_b)
    lower_time = a_time if id_a < id_b else b_time
    higher_time = b_time if id_a < id_b else a_time

    if lower_time < higher_time:
        verdict = "PASS"
    elif lower_time == higher_time:
        verdict = "TIE"
    else:
        verdict = "FAIL"

    return {
        "pair": (id_a, id_b),
        "lower_id": lower_id,
        "higher_id": higher_id,
        "lower_created_at": lower_time.isoformat() if lower_time else None,
        "higher_created_at": higher_time.isoformat() if higher_time else None,
        "delta_seconds": (higher_time - lower_time).total_seconds(),
        "verdict": verdict,
    }


def get_ids_from_db(n: int) -> list[int]:
    """Get n CivitAI image IDs from the local DB."""
    from database import SessionLocal
    from models import ImageModel
    from backend.civitai_enrichment import extract_civitai_image_id

    db = SessionLocal()
    try:
        images = (
            db.query(ImageModel.source_url)
            .filter(ImageModel.source_url.like("%civitai%"))
            .filter(ImageModel.source_url.like("%/images/%"))
            .all()
        )
        ids = []
        for (url,) in images:
            img_id = extract_civitai_image_id(url)
            if img_id:
                ids.append(img_id)

        if len(ids) < n:
            print(f"  Only {len(ids)} CivitAI images in DB, using all of them.")
            return ids

        return sorted(random.sample(ids, n))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Smoke test: CivitAI ID ordering vs createdAt")
    parser.add_argument("--ids", nargs="+", type=int, help="Specific CivitAI image IDs to test")
    parser.add_argument("--from-db", type=int, metavar="N", help="Sample N image IDs from local DB")
    parser.add_argument("--random", type=int, metavar="N", help="Generate N random CivitAI IDs (range 1M-150M)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--pair-all", action="store_true", help="Compare all pairs (O(n²)) instead of sequential")
    args = parser.parse_args()

    random.seed(args.seed)

    # ── Determine image IDs ─────────────────────────────────────────────
    if args.ids:
        image_ids = sorted(args.ids)
        source = "manual"
    elif args.from_db:
        print(f"Sampling {args.from_db} image IDs from local DB...")
        image_ids = get_ids_from_db(args.from_db)
        source = "local_db"
    elif args.random:
        # CivitAI image IDs are currently in the ~10M to ~150M range
        image_ids = sorted(random.sample(range(1_000_000, 150_000_000), args.random))
        source = "random"
    else:
        # Default: the known duplicate pair + a small DB sample
        image_ids = [105601398, 105601477]
        source = "default_known_pair"

    print(f"\n{'='*70}")
    print(f"  CivitAI ID vs createdAt Ordering Smoke Test")
    print(f"  Source: {source}  |  IDs: {len(image_ids)}")
    print(f"  Seed: {args.seed}")
    print(f"{'='*70}\n")

    if not image_ids:
        print("No image IDs to test. Exiting.")
        return

    print(f"Image IDs: {image_ids[:20]}{'...' if len(image_ids) > 20 else ''}\n")

    # ── Fetch createdAt from CivitAI API ────────────────────────────────
    from atelierai.civitai.civitai_api import CivitaiAPI

    print("Connecting to CivitAI API...")
    api = CivitaiAPI(auto_authenticate=True)

    print(f"Fetching createdAt for {len(image_ids)} images...\n")
    data = fetch_created_ats(image_ids, api)

    # ── Display raw results ─────────────────────────────────────────────
    print("  ID          | createdAt (UTC)           | Status")
    print("  " + "-" * 62)
    for img_id in sorted(data.keys()):
        d = data[img_id]
        if d["created_at"]:
            ts = d["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts = "(none)"
        print(f"  {img_id:<12}| {ts:<26}| {d['status']}")

    # ── Check ordering ──────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  Ordering Check: lower ID should have earlier (or equal) createdAt")
    print(f"{'='*70}\n")

    passes = 0
    fails = 0
    ties = 0
    incomplete = 0

    if len(image_ids) >= 2:
        if args.pair_all and len(image_ids) <= 50:
            # All pairs
            pairs = []
            for i in range(len(image_ids)):
                for j in range(i + 1, len(image_ids)):
                    pairs.append((image_ids[i], image_ids[j]))
        else:
            # Sequential pairs (sorted neighbors)
            pairs = [(image_ids[i], image_ids[i + 1]) for i in range(len(image_ids) - 1)]

        for id_a, id_b in pairs:
            result = check_pair_ordering(id_a, id_b, data)
            v = result["verdict"]
            if v == "PASS":
                passes += 1
                delta = result.get("delta_seconds", 0)
                print(f"  ✅ ({id_a}, {id_b}): lower ID is earlier by {delta:.1f}s")
            elif v == "TIE":
                ties += 1
                print(f"  ➖ ({id_a}, {id_b}): same timestamp")
            elif v == "FAIL":
                fails += 1
                delta = result.get("delta_seconds", 0)
                print(f"  ❌ ({id_a}, {id_b}): VIOLATION — higher ID is earlier by {abs(delta):.1f}s")
                print(f"     lower_id={result['lower_id']} created_at={result['lower_created_at']}")
                print(f"     higher_id={result['higher_id']} created_at={result['higher_created_at']}")
            else:
                incomplete += 1
                print(f"  ⚠️  ({id_a}, {id_b}): {result.get('reason', 'incomplete')}")

    # ── Summary ─────────────────────────────────────────────────────────
    total = passes + fails + ties
    print(f"\n{'='*70}")
    print(f"  Summary: {passes} pass, {fails} fail, {ties} tie, {incomplete} incomplete")
    if total > 0:
        pct = passes / total * 100
        print(f"  Pass rate: {pct:.1f}%")
    if fails == 0 and total > 0:
        print(f"\n  ✅ ASSUMPTION VALIDATED: Lower CivitAI ID = earlier upload (in this sample)")
    elif fails > 0:
        print(f"\n  ⚠️  ASSUMPTION HAS EXCEPTIONS: {fails} pairs violate the ordering")
        print(f"     The 'lower ID = original' heuristic is NOT always reliable.")
        print(f"     Consider comparing createdAt timestamps directly.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
