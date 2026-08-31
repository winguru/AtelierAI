# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Analyze CivitAI transport logs to evaluate throttling strategy.

Reads the daily JSONL files produced by ``atelierai.civitai.transport_log``
and reports:

- Latency percentiles (queue wait, HTTP elapsed, total duration)
- Outcome / status-code distribution with per-attempt breakdown
- Correlation of 429/503/403-Cloudflare events with observed RPM at dispatch
  and queue depth
- Backoff and pacing wait impact (how much time requests spend waiting)
- Timeline of rate-limit events to spot burst patterns

Usage:
    python scripts/analyze_civitai_logs.py [--log-dir DIR] [--date YYYY-MM-DD]
        [--last N] [--by-type] [--timeline] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import path_setup  # noqa: F401  (sys.path bootstrap for atelierai imports)

from atelierai.civitai.transport_log import _resolve_log_root

RATE_LIMIT_OUTCOMES = {"http_429", "http_503", "http_403_cloudflare"}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct / 100.0), len(ordered) - 1)
    return ordered[idx]


def _fmt_pct(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (
        f"p50={_percentile(values, 50):8.3f}  p90={_percentile(values, 90):8.3f}  "
        f"p99={_percentile(values, 99):8.3f}  max={max(values):8.3f}"
    )


def load_entries(log_dir: Path, date: str | None, last: int) -> list[dict[str, Any]]:
    """Load log entries from *log_dir* (latest ``last`` files if set)."""
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("civitai_transport_*.jsonl"))
    if date:
        files = [p for p in files if p.stem == f"civitai_transport_{date}"]
    elif last:
        files = files[-last:]
    entries: list[dict[str, Any]] = []
    for path in files:
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
        except OSError as exc:
            print(f"⚠️ could not read {path}: {exc}", file=sys.stderr)
    return entries


def final_outcome(entry: dict[str, Any]) -> str:
    """Derive a single outcome label for an entry from its attempts log."""
    attempts = entry.get("attempts") or []
    if attempts:
        last = attempts[-1]
        return str(last.get("outcome") or "unknown")
    if entry.get("error"):
        return "exception"
    return str(entry.get("final_status_code") or "success")


def analyze(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute summary statistics over *entries*."""
    stats: dict[str, Any] = {
        "count": len(entries),
        "queue_wait": [],
        "http_elapsed": [],
        "total_duration": [],
        "pacing_wait": [],
        "backoff_wait": [],
        "outcomes": Counter(),
        "outcomes_by_type": defaultdict(Counter),
        "rpm_at_dispatch_on_ratelimit": [],
        "rpm_at_dispatch_on_success": [],
        "queue_depth_on_ratelimit": [],
        "attempts_distribution": Counter(),
        "ratelimit_events": [],
    }
    for entry in entries:
        outcome = final_outcome(entry)
        stats["outcomes"][outcome] += 1
        rtype = entry.get("request_type") or "unknown"
        stats["outcomes_by_type"][rtype][outcome] += 1
        stats["attempts_distribution"][entry.get("attempts_used") or 1] += 1

        for key, target in (
            ("queue_wait_seconds", "queue_wait"),
            ("http_elapsed_seconds", "http_elapsed"),
            ("total_duration_seconds", "total_duration"),
            ("pacing_wait_seconds", "pacing_wait"),
            ("backoff_wait_seconds", "backoff_wait"),
        ):
            value = entry.get(key)
            if isinstance(value, (int, float)):
                stats[target].append(float(value))

        is_ratelimit = outcome in RATE_LIMIT_OUTCOMES or any(
            (a.get("outcome") in RATE_LIMIT_OUTCOMES) for a in entry.get("attempts") or []
        )
        rpm = entry.get("rpm_at_dispatch")
        if isinstance(rpm, int):
            if is_ratelimit:
                stats["rpm_at_dispatch_on_ratelimit"].append(rpm)
            elif outcome == "success":
                stats["rpm_at_dispatch_on_success"].append(rpm)
        depth = entry.get("queue_depth")
        if isinstance(depth, int) and is_ratelimit:
            stats["queue_depth_on_ratelimit"].append(depth)

        if is_ratelimit:
            seen_outcomes = sorted(
                {
                    str(a.get("outcome"))
                    for a in entry.get("attempts") or []
                    if a.get("outcome") in RATE_LIMIT_OUTCOMES
                }
            )
            stats["ratelimit_events"].append(
                {
                    "timestamp": entry.get("timestamp"),
                    "request_type": rtype,
                    "endpoint": entry.get("endpoint"),
                    "outcome": outcome,
                    "ratelimit_outcomes": seen_outcomes,
                    "rpm_at_dispatch": rpm,
                    "queue_depth": depth,
                    "backoff_wait_seconds": entry.get("backoff_wait_seconds"),
                    "attempts_used": entry.get("attempts_used"),
                }
            )
    return stats


def print_report(stats: dict[str, Any], by_type: bool, timeline: bool) -> None:
    """Print a human-readable report from *stats*."""
    count = stats["count"]
    print(f"\n{'=' * 72}")
    print(f"CivitAI Transport Log Analysis — {count} requests")
    print(f"{'=' * 72}")

    if count == 0:
        print("No entries to analyze.")
        return

    print("\n── Latency (seconds) " + "─" * 50)
    print(f"  queue wait : {_fmt_pct(stats['queue_wait'])}")
    print(f"  http elapsed: {_fmt_pct(stats['http_elapsed'])}")
    print(f"  total     : {_fmt_pct(stats['total_duration'])}")
    if stats["pacing_wait"]:
        total_pacing = sum(stats["pacing_wait"])
        print(
            f"  pacing wait : {_fmt_pct(stats['pacing_wait'])}  "
            f"(sum={total_pacing:.1f}s)"
        )
    if stats["backoff_wait"]:
        total_backoff = sum(stats["backoff_wait"])
        print(
            f"  backoff wait: {_fmt_pct(stats['backoff_wait'])}  "
            f"(sum={total_backoff:.1f}s over {len(stats['backoff_wait'])} requests)"
        )

    print("\n── Outcome distribution " + "─" * 47)
    for outcome, n in stats["outcomes"].most_common():
        pct = 100.0 * n / count
        print(f"  {outcome:<24} {n:>7}  ({pct:.1f}%)")

    print("\n── Attempts per request " + "─" * 48)
    for attempts, n in sorted(stats["attempts_distribution"].items()):
        print(f"  {attempts} attempt(s): {n}")

    if stats["rpm_at_dispatch_on_ratelimit"]:
        print("\n── RPM at dispatch when rate-limited " + "─" * 33)
        print(f"  {_fmt_pct(stats['rpm_at_dispatch_on_ratelimit'])}")
    if stats["rpm_at_dispatch_on_success"]:
        print("\n── RPM at dispatch on success " + "─" * 42)
        print(f"  {_fmt_pct(stats['rpm_at_dispatch_on_success'])}")
    if stats["queue_depth_on_ratelimit"]:
        print("\n── Queue depth when rate-limited " + "─" * 38)
        print(f"  {_fmt_pct([float(v) for v in stats['queue_depth_on_ratelimit']])}")

    if by_type:
        print("\n── Outcomes by request type " + "─" * 44)
        for rtype, counter in sorted(stats["outcomes_by_type"].items()):
            total = sum(counter.values())
            print(f"\n  [{rtype}] — {total} requests")
            for outcome, n in counter.most_common():
                pct = 100.0 * n / total
                print(f"    {outcome:<22} {n:>7}  ({pct:.1f}%)")

    if timeline and stats["ratelimit_events"]:
        print("\n── Rate-limit event timeline " + "─" * 43)
        for ev in stats["ratelimit_events"]:
            ts = str(ev.get("timestamp") or "?")[11:19]
            hits = ",".join(ev.get("ratelimit_outcomes") or ["?"])
            print(
                f"  {ts}  {hits:<18} {ev.get('request_type')!s:<10} "
                f"rpm={ev.get('rpm_at_dispatch')} depth={ev.get('queue_depth')} "
                f"backoff={ev.get('backoff_wait_seconds')}s "
                f"final={ev['outcome']}"
            )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Directory containing civitai_transport_*.jsonl files "
        "(default: resolved from environment/config)",
    )
    parser.add_argument("--date", type=str, default=None, help="Analyze a single date (YYYY-MM-DD)")
    parser.add_argument("--last", type=int, default=None, help="Analyze the last N daily files")
    parser.add_argument("--by-type", action="store_true", help="Break down outcomes by request type")
    parser.add_argument("--timeline", action="store_true", help="Print rate-limit event timeline")
    parser.add_argument("--json", action="store_true", help="Emit raw JSON instead of a report")
    args = parser.parse_args()

    log_dir = args.log_dir or _resolve_log_root()
    entries = load_entries(log_dir, args.date, args.last)
    stats = analyze(entries)

    if args.json:
        payload = {
            "log_dir": str(log_dir),
            "count": stats["count"],
            "outcomes": dict(stats["outcomes"]),
            "attempts_distribution": {
                str(k): v for k, v in stats["attempts_distribution"].items()
            },
            "latency": {
                "queue_wait": stats["queue_wait"],
                "http_elapsed": stats["http_elapsed"],
                "total_duration": stats["total_duration"],
            },
            "rpm_at_dispatch_on_ratelimit": stats["rpm_at_dispatch_on_ratelimit"],
            "queue_depth_on_ratelimit": stats["queue_depth_on_ratelimit"],
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Log directory: {log_dir}")
    print_report(stats, by_type=args.by_type, timeline=args.timeline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
