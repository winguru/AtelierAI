#!/usr/bin/env python3
"""Migrate civitai_api_responses/ to the 2-level sharded layout.

Usage (repo root or app/, venv activated):
    python scripts/migrate_civitai_api_responses_sharded.py           # dry-run
    python scripts/migrate_civitai_api_responses_sharded.py --apply   # execute

What it does:
1. Flat writer-1 files at the archive root move under endpoint/shard dirs:
       civitai_image_get_{key}.json                   -> image.get/{s1}/{s2}/
       civitai_image_getGenerationData_{key}.json     -> image.getGenerationData/{s1}/{s2}/
       civitai_image_getInfinite_{key}.json           -> image.getInfinite/{s1}/{s2}/
       civitai_image_tag_getVotableTags_{key}.json    -> tag.getVotableTags/{s1}/{s2}/
   (s1, s2) = shard_parts(key), matching the writers in
   atelierai.civitai.civitai_api._archive_json_file exactly.

2. Legacy flat latest/ snapshots move to latest/{endpoint-slug}/{req16[:2]}/{req16[2:4]}/.
   The endpoint comes from the JSON payload's "endpoint" field (robust); stem
   parsing is only a fallback. Filenames are unchanged.

3. history/ is retired via a single reversible rename:
       history -> ../civitai_api_responses_history_retired_<yyyymmdd>
   Nothing is deleted; remove the retired dir manually once satisfied.

Idempotent: a file already at its sharded destination is skipped; a flat copy
whose contents match the sharded one is removed (dedup), mismatched duplicates
are reported as conflicts and left in place. Files vanishing mid-run (the live
server writes the new layout after reload) are tolerated and counted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root or app/ without pre-set PYTHONPATH.
_APP = Path(__file__).resolve().parent.parent
for _candidate in (_APP / "src", _APP):
    if (_candidate / "atelierai").is_dir():
        sys.path.insert(0, str(_candidate))
        break

from atelierai.civitai.response_archive import _slug, shard_parts

# Longest prefix first so "getGenerationData_" wins over "get_".
ENDPOINT_BY_FILE_PREFIX = sorted(
    {
        "get_": "image.get",
        "getGenerationData_": "image.getGenerationData",
        "getInfinite_": "image.getInfinite",
        "tag_getVotableTags_": "tag.getVotableTags",
    }.items(),
    key=lambda kv: -len(kv[0]),
)
REQ16_TAIL_RE = re.compile(r"_(?P<req16>[0-9a-f]{16})\.json$")


def resolve_archive_root(cli_root: str | None) -> Path:
    """Mirror CivitaiResponseArchive root resolution."""
    if cli_root:
        base = Path(cli_root)
    else:
        env = os.environ.get("IMAGE_RESOURCES_PATH") or ""
        base = Path(env) if env else Path("image_resources")
    return base.expanduser().resolve() / "civitai_api_responses"


def count_files(root: Path) -> tuple[int, int]:
    files = dirs = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirs += len(dirnames)
        files += len(filenames)
    return files, dirs


def iter_dir_files(directory: Path) -> Iterator[Path]:
    """Yield plain files directly inside directory (no recursion)."""
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except FileNotFoundError:
        return
    for entry in entries:
        if entry.is_file(follow_symlinks=False):
            yield Path(entry.path)


def destination_for_flat(name: str) -> tuple[str, str, str] | None:
    """Return (endpoint_dir, shard1, shard2) for a legacy flat filename."""
    if not (name.startswith("civitai_image_") and name.endswith(".json")):
        return None
    rest = name[len("civitai_image_") : -len(".json")]
    for prefix, endpoint in ENDPOINT_BY_FILE_PREFIX:
        if rest.startswith(prefix):
            key = rest[len(prefix) :]
            if not key:
                return None
            s1, s2 = shard_parts(key)
            return _slug(endpoint), s1, s2
    return None


def files_identical(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def migrate_one(
    path: Path, dest: Path, apply: bool, stats: dict, label: str
) -> None:
    try:
        if dest.exists():
            if files_identical(path, dest):
                stats["dedup_removed"] += 1
                if apply:
                    path.unlink()
            else:
                stats["conflicts"] += 1
                stats.setdefault("conflict_labels", []).append(label)
            return
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.rename(path, dest)
        stats["moved"] += 1
    except FileNotFoundError:
        # Vanished mid-run (live server churn) — acceptable.
        stats["vanished"] += 1
    except OSError as exc:
        stats["errors"] += 1
        stats.setdefault("error_labels", []).append(f"{label}: {exc}")


def migrate_flat(root: Path, apply: bool, stats: dict) -> None:
    for path in iter_dir_files(root):
        target = destination_for_flat(path.name)
        if target is None:
            if path.suffix == ".json":
                stats["root_unmatched"] += 1
            continue
        endpoint_dir, s1, s2 = target
        dest = root / endpoint_dir / s1 / s2 / path.name
        migrate_one(path, dest, apply, stats, label=path.name)
        stats["flat_seen"] += 1


def endpoint_from_payload(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(payload, dict):
        endpoint = payload.get("endpoint")
        if isinstance(endpoint, str) and endpoint:
            return endpoint
    return None


def endpoint_from_stem(path: Path) -> str | None:
    # {kind}_{endpoint}_{req16} with a single-token kind and an underscore-free
    # endpoint slug splits into exactly 3 parts.
    parts = path.stem.split("_")
    if len(parts) == 3:
        return parts[1]
    return None


def migrate_legacy_latest(root: Path, apply: bool, stats: dict) -> None:
    latest = root / "latest"
    for path in iter_dir_files(latest):
        match = REQ16_TAIL_RE.search(path.name)
        if match is None:
            stats["latest_unmatched"] += 1
            continue
        req16 = match.group("req16")
        endpoint = endpoint_from_payload(path) or endpoint_from_stem(path)
        if endpoint is None:
            stats["latest_unresolved"] += 1
            stats.setdefault("unresolved_labels", []).append(path.name)
            continue
        dest = latest / _slug(endpoint) / req16[:2] / req16[2:4] / path.name
        migrate_one(path, dest, apply, stats, label=path.name)
        stats["latest_seen"] += 1


def retire_history(root: Path, apply: bool, stats: dict) -> None:
    history = root / "history"
    if not history.is_dir():
        stats["history_present"] = False
        return
    stats["history_present"] = True
    stats["history_files"], _dirs = count_files(history)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    retired = root.parent / f"civitai_api_responses_history_retired_{stamp}"
    if apply:
        if retired.exists():
            stats["history_rename_conflict"] = retired.name
            return
        os.rename(history, retired)
    stats["history_retired_to"] = retired.name


def prune_empty_dirs(root: Path) -> None:
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if Path(dirpath) == root:
            continue
        try:
            os.rmdir(dirpath)  # succeeds only when empty
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate civitai_api_responses/ to 2-level sharded layout."
    )
    parser.add_argument(
        "--apply", action="store_true", help="execute (default: dry-run)"
    )
    parser.add_argument(
        "--root",
        default=None,
        help="image_resources dir (default: $IMAGE_RESOURCES_PATH or ./image_resources)",
    )
    args = parser.parse_args(argv)

    root = resolve_archive_root(args.root)
    if not root.is_dir():
        print(f"error: archive root not found: {root}")
        return 1

    stats: dict = defaultdict(int)
    pre_files, pre_dirs = count_files(root)
    print(f"archive root: {root}")
    print(f"pre:  files={pre_files:,} dirs={pre_dirs:,}")
    print(f"mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    migrate_flat(root, args.apply, stats)
    migrate_legacy_latest(root, args.apply, stats)
    retire_history(root, args.apply, stats)
    if args.apply:
        prune_empty_dirs(root)

    post_files, post_dirs = count_files(root)
    print(f"post: files={post_files:,} dirs={post_dirs:,}")

    # Accounting: expected post = pre - dedup_removed - history_files (+0 new).
    if args.apply:
        expected = (
            pre_files
            - stats.get("dedup_removed", 0)
            - stats.get("history_files", 0)
        )
        if post_files != expected:
            print(
                f"WARNING: post file count {post_files:,} != expected {expected:,} "
                "(live-server churn can explain small deltas)"
            )
        else:
            print(f"accounting OK: {post_files:,} == {expected:,}")

    print(json.dumps(stats, indent=2, default=str))
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
