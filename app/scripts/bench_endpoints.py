#!/usr/bin/env python3
"""Benchmark: GET /api/images/ vs POST /api/query.

Runs each endpoint N times with equivalent parameters, reports
min/mean/median/p95/stdev of response time and payload size.

Usage:
    cd app/
    PYTHONPATH='.:backend/:src/:dev/' python3 scripts/bench_endpoints.py [--n 10] [--limit 50]
"""

import argparse
import json
import statistics
import sys
import time
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8003"

# ── Test scenarios ──────────────────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "no-filter (limit=50)",
        "get_url": "/api/images/?limit=50",
        "post_body": {"images": {"limit": 50}},
    },
    {
        "name": "filter: collection MLP + tag anthro, exclude tag futa, hide nsfwLevel 1",
        "get_url": (
            "/api/images/?included=collection:MLP"
            "&included=tag:anthro"
            "&excluded=tag:futa"
            "&hidden=nsfwLevel:1"
            "&limit=50"
        ),
        "post_body": {
            "filter": {
                "included": {
                    "collection": "MLP",
                    "tag": "anthro",
                },
                "excluded": {
                    "tag": "futa",
                },
                "hidden": {
                    "nsfwLevel": "1",
                },
            },
            "images": {"limit": 50},
        },
    },
    {
        "name": "filter + summary + tags",
        "get_url": (
            "/api/images/state?included=collection:MLP"
            "&included=tag:anthro"
            "&excluded=tag:futa"
            "&hidden=nsfwLevel:1"
            "&limit=50"
        ),
        "post_body": {
            "filter": {
                "included": {
                    "collection": "MLP",
                    "tag": "anthro",
                },
                "excluded": {
                    "tag": "futa",
                },
                "hidden": {
                    "nsfwLevel": "1",
                },
            },
            "summary": {},
            "images": {"limit": 50},
            "tags": {},
        },
    },
]


def _timed_get(url: str) -> tuple[float, int, int]:
    """Return (elapsed_s, http_status, response_bytes)."""
    t0 = time.perf_counter()
    with urlopen(BASE + url) as resp:
        body = resp.read()
    elapsed = time.perf_counter() - t0
    return elapsed, resp.status, len(body)


def _timed_post(path: str, body: dict) -> tuple[float, int, int]:
    """Return (elapsed_s, http_status, response_bytes)."""
    data = json.dumps(body).encode()
    req = Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urlopen(req) as resp:
        resp_body = resp.read()
    elapsed = time.perf_counter() - t0
    return elapsed, resp.status, len(resp_body)


def _stats(samples: list[float]) -> dict:
    """Compute summary stats."""
    s = sorted(samples)
    n = len(s)
    p95_idx = int(n * 0.95)
    return {
        "min": min(s),
        "mean": statistics.mean(s),
        "median": statistics.median(s),
        "p95": s[min(p95_idx, n - 1)],
        "stdev": statistics.stdev(s) if n > 1 else 0.0,
        "max": max(s),
    }


def fmt_ms(seconds: float) -> str:
    return f"{seconds * 1000:.1f}ms"


def main():
    parser = argparse.ArgumentParser(description="Benchmark GET /api/images vs POST /api/query")
    parser.add_argument("--n", type=int, default=10, help="Iterations per scenario")
    parser.add_argument("--limit", type=int, default=None, help="Override limit for scenario 1")
    args = parser.parse_args()

    n = args.n
    print(f"⏱  Benchmarking {len(SCENARIOS)} scenarios × {n} iterations each")
    print(f"   Server: {BASE}\n")

    for scenario in SCENARIOS:
        name = scenario["name"]
        print(f"{'═' * 72}")
        print(f"📋 {name}")
        print(f"{'─' * 72}")

        # ── Warm-up (1 call, discard) ──
        try:
            _timed_get(scenario["get_url"])
            _timed_post("/api/query", scenario["post_body"])
        except Exception as e:
            print(f"❌ Warm-up failed: {e}")
            continue

        # ── GET benchmark ──
        get_times: list[float] = []
        get_sizes: list[int] = []
        get_ok = 0
        for _ in range(n):
            try:
                elapsed, status, size = _timed_get(scenario["get_url"])
                get_times.append(elapsed)
                get_sizes.append(size)
                if status == 200:
                    get_ok += 1
            except Exception as e:
                print(f"  GET error: {e}")

        # ── POST benchmark ──
        post_times: list[float] = []
        post_sizes: list[int] = []
        post_ok = 0
        for _ in range(n):
            try:
                elapsed, status, size = _timed_post("/api/query", scenario["post_body"])
                post_times.append(elapsed)
                post_sizes.append(size)
                if status == 200:
                    post_ok += 1
            except Exception as e:
                print(f"  POST error: {e}")

        # ── Report ──
        if get_times:
            gs = _stats(get_times)
            print(
                f"  GET  /api/images  → {get_ok}/{n} OK  "
                f"median={fmt_ms(gs['median'])}  mean={fmt_ms(gs['mean'])}  "
                f"p95={fmt_ms(gs['p95'])}  min={fmt_ms(gs['min'])}  max={fmt_ms(gs['max'])}  "
                f"size={statistics.mean(get_sizes):.0f}B"
            )
        else:
            print("  GET  → no successful runs")

        if post_times:
            ps = _stats(post_times)
            print(
                f"  POST /api/query   → {post_ok}/{n} OK  "
                f"median={fmt_ms(ps['median'])}  mean={fmt_ms(ps['mean'])}  "
                f"p95={fmt_ms(ps['p95'])}  min={fmt_ms(ps['min'])}  max={fmt_ms(ps['max'])}  "
                f"size={statistics.mean(post_sizes):.0f}B"
            )
        else:
            print("  POST → no successful runs")

        # ── Comparison ──
        if get_times and post_times:
            get_med = _stats(get_times)["median"]
            post_med = _stats(post_times)["median"]
            if get_med > 0:
                ratio = post_med / get_med
                delta_ms = (post_med - get_med) * 1000
                if ratio > 1:
                    print(f"  📊 POST is {ratio:.2f}× slower ({delta_ms:+.1f}ms)")
                else:
                    print(f"  📊 POST is {1 / ratio:.2f}× faster ({delta_ms:+.1f}ms)")
        print()

    print(f"{'═' * 72}")
    print("Done.")


if __name__ == "__main__":
    main()
