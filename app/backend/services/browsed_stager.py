# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Stage harvested CivitAI feed captures into the search-lab review tables.

Reads harvested ``image.getInfinite`` responses from the CivitAI response
archive (written by the browser-bridge harvester), decodes them with the
existing flat-array deserializer, and upserts:

- ``CivitaiSearchImage`` rows (metadata for thumbnails/details), and
- standalone ``CivitaiSearchImageLink`` rows (``search_id=NULL``,
  ``rating=NULL``) marking each browsed image as *unrated*.

Unrated images then flow through the existing search-lab review machinery:
the ``/rated?rating=unrated`` view, keep/skip/discard rating, hide-filter
presets, and the import batch endpoint. Zero requests to CivitAI.

Idempotent: re-staging the same capture is a no-op (upsert by
``civitai_image_id``; links created only when missing). A small state file
records which archive files were already staged so ticks don't re-decode
old captures.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from models import CivitaiSearchImage, CivitaiSearchImageLink
from sqlalchemy import text
from sqlalchemy.orm import Session

_STAGE_LOCK = threading.Lock()


def _resolve_tag_names(db: Session, tag_ids: list[int]) -> list[str]:
    """Resolve civitai tag ids to names via concept_aliases (local, no network).

    Browse feeds send numeric ``tagIds`` with ``tags: -1`` (CivitAI's null
    encoding). The concept-alias table already maps thousands of civitai tag
    ids to names from past taxonomy imports; unmatched ids are skipped
    silently — names appear later when taxonomy knowledge grows.
    """
    names: list[str] = []
    for tid in tag_ids:
        if not isinstance(tid, int) or tid < 0:
            continue
        row = (
            db.execute(
                text(
                    "SELECT alias FROM concept_aliases WHERE external_tag_id = :tid LIMIT 1"
                ),
                {"tid": tid},
            )
            .fetchone()
        )
        if row and row[0]:
            names.append(row[0])
    return names


def _stager_state_path() -> Path:
    from atelierai.civitai.response_archive import CivitaiResponseArchive

    root = CivitaiResponseArchive().root
    return root / ".." / "browsed_staging_state.json"


def _norm(path: Path | str) -> str:
    """Canonical staging key for an archive file (absolute, resolved).

    Early state files stored absolute /workspace/... paths while the scan
    produced relative ones (cwd-dependent) — the mismatch made every pass
    re-decode all files. Normalizing both sides keeps the state file stable
    regardless of process cwd.
    """
    return str(Path(path).resolve())


def _load_staged_paths() -> set[str]:
    path = _stager_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {_norm(p) for p in data.get("staged_files", [])}
    except (OSError, json.JSONDecodeError):
        return set()


def _save_staged_paths(paths: set[str]) -> None:
    path = _stager_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"staged_files": sorted(paths)}), encoding="utf-8"
    )


def _decode_feed_items(response: Any) -> list[dict[str, Any]]:
    """Decode an image.getInfinite tRPC response into feed item dicts.

    Uses the existing CivitaiAPI flat-array deserializer; returns [] when
    the response isn't decodable (defensive — capture format may drift).
    """
    from atelierai.civitai.civitai_api import CivitaiAPI

    try:
        # Static pure function — no instance state needed. (A previous version
        # called CivitaiAPI.__new__ directly, which registers the singleton
        # WITHOUT running __init__ and poisoned get_instance() for the whole
        # process — see the self-heal note in CivitaiAPI.get_instance.)
        parsed = CivitaiAPI._deserialize_trpc_flat_array(response)
    except Exception:  # noqa: BLE001 — malformed capture, skip it
        return []
    if not isinstance(parsed, dict):
        return []
    items = parsed.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _upsert_browsed_image(db: Session, item: dict[str, Any]) -> CivitaiSearchImage | None:
    """Insert or update a CivitaiSearchImage row from a feed item.

    Mirrors the search-lab rate endpoint's upsert, but only fills fields the
    feed carries — existing values (e.g. tags from a later rating action)
    are preserved.
    """
    civitai_image_id = item.get("id")
    if not isinstance(civitai_image_id, int):
        return None

    img = (
        db.query(CivitaiSearchImage)
        .filter(CivitaiSearchImage.civitai_image_id == civitai_image_id)
        .first()
    )
    if img is None:
        img = CivitaiSearchImage(civitai_image_id=civitai_image_id)
        db.add(img)
        db.flush()

    # Fill-if-absent fields (never clobber richer data from other sources).
    if img.post_id is None:
        img.post_id = item.get("postId")
    if img.uuid is None or not img.uuid:
        img.uuid = item.get("url")  # feed 'url' is the bare uuid/url-hash
    if img.blurhash is None or not img.blurhash:
        img.blurhash = item.get("hash")
    if img.file_name is None or not img.file_name:
        img.file_name = item.get("name")
    # Artist identity lives in a nested user object in browse feeds
    # (item["user"]["username"]); some captures also carry a legacy
    # top-level username.
    user_obj = item.get("user")
    if img.artist_id is None or not img.artist_id:
        img.artist_id = user_obj.get("id") if isinstance(user_obj, dict) else None
    if img.artist_name is None or not img.artist_name:
        img.artist_name = (
            user_obj.get("username")
            if isinstance(user_obj, dict)
            else item.get("username")
        )
    stats = item.get("stats")
    if isinstance(stats, dict):
        if img.reactions is None:
            img.reactions = item.get("reactionCount")
        if img.likes is None:
            img.likes = stats.get("likeCountAllTime")
    # Tags: browse feeds carry tagIds (numbers), tags resolves to -1/null.
    # Resolve via local concept-aliases; store as plain name strings (the
    # same shape the search-lab rate flow stores).
    if not img.tags:
        tag_ids = item.get("tagIds")
        if isinstance(tag_ids, list) and tag_ids:
            img.tags = _resolve_tag_names(db, tag_ids)
    db.flush()
    return img


def stage_harvested_feeds(
    db: Session, *, limit_files: int = 200, re_enrich: bool = False
) -> dict[str, Any]:
    """Scan the archive for unstaged harvested feed captures and stage them.

    Returns counts: files scanned/staged, images upserted, links created,
    errors. Safe to run repeatedly; errors never abort the batch.

    ``re_enrich=True`` reprocesses ALL archive files (clearing the staged-
    files state) so improved field mappings can fill gaps on rows staged by
    an older version — the upsert is fill-if-absent, so nothing already
    populated gets clobbered.
    """
    from atelierai.civitai.response_archive import CivitaiResponseArchive

    archive = CivitaiResponseArchive()
    feed_dir = archive.root / "latest" / "image.getInfinite"
    if not feed_dir.is_dir():
        return {"ok": True, "files_staged": 0, "images_upserted": 0, "links_created": 0, "errors": []}

    with _STAGE_LOCK:
        staged_paths = set() if re_enrich else _load_staged_paths()
        # Stage from ALL image.getInfinite captures — browser-harvested
        # (harvested_*) AND direct-lane (trpc_*) records. Both contain
        # browsable feed items; request-hash keying means identical inputs
        # map to the same archive file, and fill-if-absent upserts keep
        # richer existing data safe.
        files = sorted(
            set(feed_dir.rglob("harvested_*.json"))
            | set(feed_dir.rglob("trpc_*.json"))
        )

        files_staged = 0
        images_upserted = 0
        links_created = 0
        images_new = 0
        images_already_reviewed = 0
        errors: list[str] = []

        for fp in files[:limit_files]:
            rel = _norm(fp)
            if rel in staged_paths:
                continue
            try:
                record = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{fp.name}: {type(exc).__name__}")
                continue

            items = _decode_feed_items(record.get("response"))
            for item in items:
                img = _upsert_browsed_image(db, item)
                if img is None:
                    continue
                images_upserted += 1
                # Standalone unrated link (browse marker). Only create when
                # the image has no link at all — if it already has one (from
                # search or a previous rating) the rating semantics win.
                existing_link = (
                    db.query(CivitaiSearchImageLink)
                    .filter(CivitaiSearchImageLink.image_id == img.id)
                    .first()
                )
                if existing_link is None:
                    db.add(
                        CivitaiSearchImageLink(
                            image_id=img.id,
                            search_id=None,
                            rating=None,
                            is_excluded=False,
                        )
                    )
                    links_created += 1
                    images_new += 1
                else:
                    images_already_reviewed += 1

            staged_paths.add(rel)
            files_staged += 1

        db.commit()
        _save_staged_paths(staged_paths)

    return {
        "ok": True,
        "files_staged": files_staged,
        "images_upserted": images_upserted,
        "links_created": links_created,
        "images_new": images_new,
        "images_already_reviewed": images_already_reviewed,
        "errors": errors,
    }
