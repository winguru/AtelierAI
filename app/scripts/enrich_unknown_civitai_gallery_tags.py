#!/usr/bin/env python3
"""Identify unknown CivitAI tags in the gallery and enrich them with metadata.

Scans `images.json_metadata` to collect all CivitAI tag IDs present in the
gallery, compares them against `authority_terms` for the CivitAI authority,
then fetches metadata for unrecognised tags via tag.getById + tag.getVotableTags
and optionally imports them into the taxonomy tables.

Typical workflow:

    # 1. Discover how many unknown tags exist (writes unknown_gallery_tag_ids.json)
    python scripts/enrich_unknown_civitai_gallery_tags.py

    # 2. Discover and sample the top 100 by gallery frequency
    python scripts/enrich_unknown_civitai_gallery_tags.py --sample-count 100

    # 3. Review enrichment JSON, then import
    python scripts/enrich_unknown_civitai_gallery_tags.py \\
        --import-sample-json data/unknown_gallery_tag_enrichment_100.json \\
        --import-main-db

    # 4. Sample and import in one pass
    python scripts/enrich_unknown_civitai_gallery_tags.py \\
        --sample-count 100 --import-main-db

    # 5. Also create Concept rows for tags not yet linked to a concept
    python scripts/enrich_unknown_civitai_gallery_tags.py \\
        --sample-count 100 --import-main-db --create-concepts
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from path_setup import PROJECT_ROOT  # noqa: F401 - adds src/ to sys.path
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.http_client import CivitaiHttpClient, CivitaiRequestError


# ---------------------------------------------------------------------------
# Shared low-level utilities
# (Duplicated from compare_getinfinite_tags_against_main_db.py to keep each
# script self-contained. If these grow substantially, extract to a shared module.)
# ---------------------------------------------------------------------------


def _parse_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_name(value: str | None) -> str:
    text = (value or "").strip().replace("_", " ")
    return " ".join(text.split()).lower()


def _slugify(value: str) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    collapsed = "-".join(part for part in base.split("-") if part)
    return collapsed or "concept"


def _coerce_metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _iter_civitai_tag_ids_from_metadata(metadata: Any) -> list[int]:
    if not isinstance(metadata, dict):
        return []
    civitai_payload = metadata.get("civitai")
    if not isinstance(civitai_payload, dict):
        return []
    raw_tags = civitai_payload.get("tags")
    if not isinstance(raw_tags, list):
        return []
    out: list[int] = []
    for item in raw_tags:
        if not isinstance(item, dict):
            continue
        parsed = _parse_int(item.get("id"))
        if parsed is not None:
            out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# Taxonomy DB helpers (shared pattern with compare_getinfinite_tags_against_main_db.py)
# ---------------------------------------------------------------------------


def _ensure_authority(conn: sqlite3.Connection, authority_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM tag_authorities WHERE lower(name)=lower(?)",
        (authority_name,),
    ).fetchone()
    if row:
        return int(row[0])

    defaults = {
        "civitai": (
            "CivitAI native tag authority and IDs.",
            1,
            "https://civitai.com",
        )
    }
    description, is_external, base_url = defaults.get(
        authority_name.lower(),
        (f"Auto-created authority '{authority_name}'.", 1, None),
    )
    cur = conn.execute(
        """
        INSERT INTO tag_authorities (name, description, is_external, base_url)
        VALUES (?, ?, ?, ?)
        """,
        (authority_name, description, is_external, base_url),
    )
    if cur.lastrowid is not None:
        return int(cur.lastrowid)
    # Fallback read in case SQLite doesn't return lastrowid on this table.
    row = conn.execute(
        "SELECT id FROM tag_authorities WHERE lower(name)=lower(?)",
        (authority_name,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to create/read authority '{authority_name}'")
    return int(row[0])


def _ensure_unique_slug(conn: sqlite3.Connection, base_slug: str) -> str:
    slug = base_slug
    idx = 2
    while True:
        row = conn.execute("SELECT id FROM concepts WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return slug
        slug = f"{base_slug}-{idx}"
        idx += 1


def _get_or_create_concept(conn: sqlite3.Connection, canonical_name: str) -> tuple[int, bool]:
    row = conn.execute(
        "SELECT id FROM concepts WHERE canonical_name = ?",
        (canonical_name,),
    ).fetchone()
    if row:
        return int(row[0]), False
    now_iso = _utcnow_iso()
    slug = _ensure_unique_slug(conn, _slugify(canonical_name))
    cur = conn.execute(
        """
        INSERT INTO concepts (canonical_name, slug, status, created_at, updated_at)
        VALUES (?, ?, 'active', ?, ?)
        """,
        (canonical_name, slug, now_iso, now_iso),
    )
    if cur.lastrowid is None:
        raise RuntimeError(f"Failed to create concept for '{canonical_name}'")
    return int(cur.lastrowid), True


def _ensure_alias(
    conn: sqlite3.Connection,
    *,
    concept_id: int,
    alias: str,
    authority_id: int,
    external_tag_id: str,
) -> bool:
    normalized_alias = _normalize_name(alias)
    row = conn.execute(
        """
        SELECT id, authority_id, external_tag_id, is_preferred
        FROM concept_aliases
        WHERE concept_id = ? AND normalized_alias = ?
        """,
        (concept_id, normalized_alias),
    ).fetchone()
    if row:
        alias_id = int(row[0])
        changed = False
        if row[1] is None:
            conn.execute(
                "UPDATE concept_aliases SET authority_id = ? WHERE id = ?",
                (authority_id, alias_id),
            )
            changed = True
        if (row[2] is None or str(row[2]).strip() == "") and external_tag_id:
            conn.execute(
                "UPDATE concept_aliases SET external_tag_id = ? WHERE id = ?",
                (external_tag_id, alias_id),
            )
            changed = True
        if not bool(row[3]):
            conn.execute(
                "UPDATE concept_aliases SET is_preferred = 1 WHERE id = ?",
                (alias_id,),
            )
            changed = True
        return changed
    conn.execute(
        """
        INSERT INTO concept_aliases (
            concept_id, alias, normalized_alias, alias_type, is_preferred, authority_id, external_tag_id
        ) VALUES (?, ?, ?, 'synonym', 1, ?, ?)
        """,
        (concept_id, alias, normalized_alias, authority_id, external_tag_id),
    )
    return True


def _upsert_authority_term(
    conn: sqlite3.Connection,
    *,
    authority_id: int,
    external_tag_id: str,
    external_name: str,
    normalized_external_name: str,
    concept_id: int | None,
    metadata: dict[str, Any],
) -> str:
    now_iso = _utcnow_iso()
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    by_external = conn.execute(
        """
        SELECT id, external_name, normalized_external_name, concept_id, metadata_json
        FROM authority_terms
        WHERE authority_id = ? AND external_tag_id = ?
        """,
        (authority_id, external_tag_id),
    ).fetchone()
    if by_external:
        term_id = int(by_external[0])
        changed = (
            by_external[1] != external_name
            or by_external[2] != normalized_external_name
            or _parse_int(by_external[3]) != concept_id
            or (by_external[4] or "") != metadata_json
        )
        if changed:
            conn.execute(
                """
                UPDATE authority_terms
                SET external_name = ?,
                    normalized_external_name = ?,
                    concept_id = ?,
                    metadata_json = ?,
                    updated_at = ?,
                    last_seen_at = ?
                WHERE id = ?
                """,
                (
                    external_name,
                    normalized_external_name,
                    concept_id,
                    metadata_json,
                    now_iso,
                    now_iso,
                    term_id,
                ),
            )
        else:
            # Touch last_seen_at only — no data changed, so updated_at must not advance.
            conn.execute(
                "UPDATE authority_terms SET last_seen_at = ? WHERE id = ?",
                (now_iso, term_id),
            )
        return "updated" if changed else "seen"

    by_name = conn.execute(
        """
        SELECT id, concept_id, metadata_json
        FROM authority_terms
        WHERE authority_id = ? AND normalized_external_name = ?
        """,
        (authority_id, normalized_external_name),
    ).fetchone()
    if by_name:
        term_id = int(by_name[0])
        changed = _parse_int(by_name[1]) != concept_id
        current_meta: dict[str, Any] = {}
        raw_meta = by_name[2]
        if isinstance(raw_meta, str) and raw_meta.strip():
            try:
                parsed_meta = json.loads(raw_meta)
                if isinstance(parsed_meta, dict):
                    current_meta = parsed_meta
            except json.JSONDecodeError:
                pass
        alt_ids = current_meta.get("alternate_external_tag_ids")
        if not isinstance(alt_ids, list):
            alt_ids = []
        if str(external_tag_id) not in alt_ids:
            alt_ids.append(str(external_tag_id))
            current_meta["alternate_external_tag_ids"] = alt_ids
            changed = True
        conn.execute(
            """
            UPDATE authority_terms
            SET concept_id = ?,
                metadata_json = ?,
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (concept_id, json.dumps(current_meta, ensure_ascii=False), now_iso, now_iso, term_id),
        )
        return "updated" if changed else "seen"

    conn.execute(
        """
        INSERT INTO authority_terms (
            authority_id, external_tag_id, external_name, normalized_external_name,
            concept_id, metadata_json, created_at, updated_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            authority_id,
            external_tag_id,
            external_name,
            normalized_external_name,
            concept_id,
            metadata_json,
            now_iso,
            now_iso,
            now_iso,
        ),
    )
    return "created"


# ---------------------------------------------------------------------------
# Gallery scanning
# ---------------------------------------------------------------------------


@dataclass
class _GalleryTagEntry:
    """Aggregated data about a CivitAI tag ID seen in the gallery."""

    count: int = 0
    first_civitai_image_id: int | None = None


def _get_gallery_civitai_tag_data(
    main_db_path: Path,
) -> dict[int, _GalleryTagEntry]:
    """Scan images.json_metadata and collect tag IDs with gallery occurrence counts.

    Returns a dict keyed by CivitAI tag ID, each entry recording:
      - count: how many gallery images reference the tag
      - first_civitai_image_id: the CivitAI image ID of the first image containing
        the tag (used as the image parameter for tag.getVotableTags lookups).

    Rows are streamed to avoid pulling all json_metadata blobs into memory at once.
    """
    entries: dict[int, _GalleryTagEntry] = {}
    conn = sqlite3.connect(main_db_path)
    try:
        cursor = conn.execute(
            "SELECT civitai_image_id, json_metadata FROM images WHERE json_metadata IS NOT NULL"
        )
        for civitai_image_id, raw_metadata in cursor:
            metadata = _coerce_metadata_dict(raw_metadata)
            if not metadata:
                continue
            tag_ids = _iter_civitai_tag_ids_from_metadata(metadata)
            for tag_id in tag_ids:
                entry = entries.get(tag_id)
                if entry is None:
                    entry = _GalleryTagEntry()
                    entries[tag_id] = entry
                entry.count += 1
                if entry.first_civitai_image_id is None:
                    parsed_civ_id = _parse_int(civitai_image_id)
                    if parsed_civ_id is not None:
                        entry.first_civitai_image_id = parsed_civ_id
    finally:
        conn.close()
    return entries


def _get_known_civitai_term_ids(main_db_path: Path, authority_name: str) -> set[int]:
    """Return all CivitAI tag IDs already mapped in authority_terms (including alt IDs)."""
    conn = sqlite3.connect(main_db_path)
    try:
        rows = conn.execute(
            """
            SELECT at.external_tag_id, at.metadata_json
            FROM authority_terms at
            JOIN tag_authorities ta ON ta.id = at.authority_id
            WHERE lower(ta.name) = lower(?)
            """,
            (authority_name,),
        ).fetchall()
    finally:
        conn.close()
    out: set[int] = set()
    for external_tag_id, metadata_json in rows:
        parsed = _parse_int(external_tag_id)
        if parsed is not None:
            out.add(parsed)
        # Also cover collision-resolved alternate IDs stored in metadata.
        if isinstance(metadata_json, str) and metadata_json.strip():
            try:
                meta = json.loads(metadata_json)
            except json.JSONDecodeError:
                meta = None
            if isinstance(meta, dict):
                alt_ids = meta.get("alternate_external_tag_ids")
                if isinstance(alt_ids, list):
                    for alt in alt_ids:
                        alt_parsed = _parse_int(alt)
                        if alt_parsed is not None:
                            out.add(alt_parsed)
    return out


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _enrich_unknown_tags(
    *,
    unknown_tags: dict[int, _GalleryTagEntry],
    sample_count: int,
    sleep_ms: int,
    output_path: Path,
) -> dict[str, Any]:
    """Fetch tag.getById + tag.getVotableTags for a sample of unknown tag IDs.

    Unknown tags are sampled in descending gallery-count order so the most
    frequently seen unknowns are enriched first.

    Produces the same row structure as compare_getinfinite_tags_against_main_db.py
    so the output JSON is compatible with --import-sample-json on either script.
    """
    api = CivitaiAPI.get_instance()
    # Highest-frequency unknowns first.
    ordered_ids = sorted(
        unknown_tags.keys(), key=lambda tid: unknown_tags[tid].count, reverse=True
    )
    sample_ids = ordered_ids[:max(0, sample_count)]
    sleep_seconds = max(0, sleep_ms) / 1000.0

    rows: list[dict[str, Any]] = []
    coverage: dict[str, Any] = {
        "sample_size": len(sample_ids),
        "with_first_civitai_image": 0,
        "getById_success": 0,
        "votable_match_success": 0,
        "with_type": 0,
        "with_nsfwLevel": 0,
        "with_automated": 0,
        "with_concrete": 0,
    }

    for idx, tag_id in enumerate(sample_ids, start=1):
        try:
            entry = unknown_tags[tag_id]
            civitai_image_id = entry.first_civitai_image_id
            if civitai_image_id is not None:
                coverage["with_first_civitai_image"] += 1

            # tag.getById — cache-first fetch; falls back to live on cache miss.
            get_by_id = api.get_cached_or_fetch(
                "tag.getById", {"id": int(tag_id), "authed": True}
            )
            if isinstance(get_by_id, dict):
                coverage["getById_success"] += 1

            # tag.getVotableTags — possible only when we have a CivitAI image ID.
            votable_payload: dict[str, Any] | None = None
            votable_match: dict[str, Any] | None = None
            if civitai_image_id is not None:
                votable_payload = {"id": int(civitai_image_id), "type": "image", "authed": True}
                votable_response = api.get_cached_or_fetch(
                    "tag.getVotableTags", votable_payload
                )
                if isinstance(votable_response, list):
                    for item in votable_response:
                        if not isinstance(item, dict):
                            continue
                        if _parse_int(item.get("id")) == int(tag_id):
                            votable_match = item
                            coverage["votable_match_success"] += 1
                            break

            # Merge: getById provides name + type; votable match adds richer metadata.
            merged: dict[str, Any] = {
                "id": tag_id,
                "name": None,
                "type": None,
                "nsfwLevel": None,
                "automated": None,
                "concrete": None,
            }
            if isinstance(get_by_id, dict):
                merged["name"] = get_by_id.get("name")
                merged["type"] = get_by_id.get("type")
            if isinstance(votable_match, dict):
                merged["name"] = votable_match.get("name") or merged["name"]
                merged["type"] = votable_match.get("type") or merged["type"]
                merged["nsfwLevel"] = votable_match.get("nsfwLevel")
                merged["automated"] = votable_match.get("automated")
                merged["concrete"] = votable_match.get("concrete")

            if merged.get("type") is not None:
                coverage["with_type"] += 1
            if merged.get("nsfwLevel") is not None:
                coverage["with_nsfwLevel"] += 1
            if merged.get("automated") is not None:
                coverage["with_automated"] += 1
            if merged.get("concrete") is not None:
                coverage["with_concrete"] += 1

            rows.append(
                {
                    "tag_id": int(tag_id),
                    "gallery_count": int(entry.count),
                    # first_image_id keeps the same key as compare script for cross-compatibility.
                    "first_image_id": civitai_image_id,
                    "first_civitai_image_id": civitai_image_id,
                    "getById": get_by_id,
                    "votable_payload": votable_payload,
                    "votable_match": votable_match,
                    "merged": merged,
                }
            )

            if idx % 25 == 0 or idx == len(sample_ids):
                print(f"  Enrichment progress: {idx}/{len(sample_ids)}")

            if sleep_seconds > 0 and idx < len(sample_ids):
                time.sleep(sleep_seconds)

        except CivitaiRequestError as exc:
            status = exc.status_code
            print(f"\n[tag {tag_id}] API error (HTTP {status}): {exc}. Skipping.")
            if status == 403:
                backoff = CivitaiHttpClient.activate_global_backoff(60.0, reason="HTTP 403")
                print(f"  403 detected; enforcing {backoff:.0f}s backoff before next request.")
            continue
        except KeyboardInterrupt:
            print(
                f"\nInterrupted after {len(rows)}/{len(sample_ids)} enriched tags. "
                "Saving partial results."
            )
            break

    out: dict[str, Any] = {
        "sampled_unknown_tag_ids": sample_ids,
        "coverage": coverage,
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_enriched_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # "id" is always set unconditionally in the merged dict so it adds no signal.
    # Only track fields that may genuinely be absent from the API response.
    required_fields = ["name", "type", "nsfwLevel", "automated", "concrete"]
    coverage = {field: 0 for field in required_fields}
    mismatched_ids = 0

    for row in rows:
        tag_id = _parse_int(row.get("tag_id"))
        merged = row.get("merged")
        if not isinstance(merged, dict):
            continue
        if tag_id is not None and _parse_int(merged.get("id")) != tag_id:
            mismatched_ids += 1
        for field in required_fields:
            if merged.get(field) is not None:
                coverage[field] += 1

    total = len(rows)
    complete_rows = sum(
        1
        for row in rows
        if isinstance(row.get("merged"), dict)
        and all(row["merged"].get(f) is not None for f in required_fields)
    )
    return {
        "sample_size": total,
        "required_fields": required_fields,
        "coverage": coverage,
        "complete_rows": complete_rows,
        "missing_any_required": total - complete_rows,
        "mismatched_ids": mismatched_ids,
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _import_enriched_rows(
    *,
    main_db_path: Path,
    authority_name: str,
    rows: list[dict[str, Any]],
    gallery_tag_data: dict[int, _GalleryTagEntry],
    dry_run: bool,
    commit_every: int,
    create_concepts: bool,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "rows_scanned": 0,
        "rows_skipped": 0,
        "concepts_created": 0,
        "concepts_reused": 0,
        "aliases_changed": 0,
        "authority_terms_created": 0,
        "authority_terms_updated": 0,
        "authority_terms_seen": 0,
        "transactions_committed": 0,
    }

    conn = sqlite3.connect(main_db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        authority_id = _ensure_authority(conn, authority_name)

        def _find_existing_concept(tag_id: int, normalized_name: str) -> int | None:
            by_external = conn.execute(
                """
                SELECT concept_id FROM authority_terms
                WHERE authority_id = ? AND external_tag_id = ? AND concept_id IS NOT NULL
                LIMIT 1
                """,
                (authority_id, str(tag_id)),
            ).fetchone()
            if by_external and _parse_int(by_external[0]) is not None:
                return int(by_external[0])
            by_name = conn.execute(
                """
                SELECT concept_id FROM authority_terms
                WHERE authority_id = ? AND normalized_external_name = ? AND concept_id IS NOT NULL
                LIMIT 1
                """,
                (authority_id, normalized_name),
            ).fetchone()
            if by_name and _parse_int(by_name[0]) is not None:
                return int(by_name[0])
            by_concept = conn.execute(
                "SELECT id FROM concepts WHERE canonical_name = ? LIMIT 1",
                (normalized_name,),
            ).fetchone()
            if by_concept and _parse_int(by_concept[0]) is not None:
                return int(by_concept[0])
            return None

        batch_count = 0
        interrupted = False
        try:
            for row in rows:
                stats["rows_scanned"] += 1
                merged = row.get("merged")
                if not isinstance(merged, dict):
                    stats["rows_skipped"] += 1
                    continue

                tag_id = _parse_int(
                    merged.get("id") if merged.get("id") is not None else row.get("tag_id")
                )
                name = str(merged.get("name") or "").strip()
                normalized_name = _normalize_name(name)
                if tag_id is None or not normalized_name:
                    stats["rows_skipped"] += 1
                    continue

                gallery_entry = gallery_tag_data.get(tag_id)
                gallery_count = int(gallery_entry.count) if gallery_entry is not None else 0

                concept_id = _find_existing_concept(tag_id, normalized_name)
                if concept_id is not None:
                    stats["concepts_reused"] += 1
                elif create_concepts:
                    concept_id, created = _get_or_create_concept(conn, normalized_name)
                    if created:
                        stats["concepts_created"] += 1
                    else:
                        stats["concepts_reused"] += 1

                if concept_id is not None:
                    if _ensure_alias(
                        conn,
                        concept_id=concept_id,
                        alias=name,
                        authority_id=authority_id,
                        external_tag_id=str(tag_id),
                    ):
                        stats["aliases_changed"] += 1

                metadata: dict[str, Any] = {
                    "tag_type": merged.get("type"),
                    "nsfw_level": merged.get("nsfwLevel"),
                    "automated": merged.get("automated"),
                    "concrete": merged.get("concrete"),
                    "source": "gallery_scan+getById+getVotableTags",
                    # post_count mirrors gallery_count so the tag-list UI can sort/display
                    # it consistently with Danbooru and getInfinite-sourced terms.
                    "post_count": gallery_count,
                    "gallery_count": gallery_count,
                    "count_source": "gallery_scan",
                }
                term_status = _upsert_authority_term(
                    conn,
                    authority_id=authority_id,
                    external_tag_id=str(tag_id),
                    external_name=name,
                    normalized_external_name=normalized_name,
                    concept_id=concept_id,
                    metadata=metadata,
                )
                if term_status == "created":
                    stats["authority_terms_created"] += 1
                elif term_status == "updated":
                    stats["authority_terms_updated"] += 1
                else:
                    stats["authority_terms_seen"] += 1

                batch_count += 1
                if commit_every > 0 and batch_count >= commit_every:
                    if dry_run:
                        conn.rollback()
                    else:
                        conn.commit()
                        stats["transactions_committed"] += 1
                    batch_count = 0

        except KeyboardInterrupt:
            interrupted = True
            print("\nInterrupted. Committing pending batch before exit.")

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            if batch_count > 0:
                stats["transactions_committed"] += 1

        if interrupted:
            raise KeyboardInterrupt

        return stats
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Identify CivitAI tags referenced in the gallery but absent from authority_terms, "
            "then fetch their metadata from CivitAI and optionally import them into the taxonomy."
        )
    )
    parser.add_argument(
        "--main-db",
        default=str(Path(__file__).resolve().parent.parent / "image_db.sqlite"),
        help="Path to main app SQLite DB (default: app/image_db.sqlite)",
    )
    parser.add_argument(
        "--authority-name",
        default="civitai",
        help="Authority name in main DB (default: civitai)",
    )
    parser.add_argument(
        "--unknown-json",
        help=(
            "Optional path to write the unknown tag IDs summary JSON "
            "(default: <main-db-dir>/unknown_gallery_tag_ids.json)"
        ),
    )
    parser.add_argument(
        "--sample-count",
        type=int,
        default=0,
        help=(
            "Enrich the top N unknown tags by gallery frequency via "
            "tag.getById + tag.getVotableTags (default: 0 = discovery only)"
        ),
    )
    parser.add_argument(
        "--sample-sleep-ms",
        type=int,
        default=75,
        help="Milliseconds to sleep between API requests during sampling (default: 75)",
    )
    parser.add_argument(
        "--sample-output",
        help=(
            "Optional path to write the enrichment JSON "
            "(default: <main-db-dir>/unknown_gallery_tag_enrichment_<N>.json)"
        ),
    )
    parser.add_argument(
        "--import-sample-json",
        help=(
            "Path to an existing enrichment JSON produced by a previous --sample-count run "
            "(or compatible output from compare_getinfinite_tags_against_main_db.py). "
            "When provided, skips new sampling and uses these rows for import/validation."
        ),
    )
    parser.add_argument(
        "--import-main-db",
        action="store_true",
        help=(
            "Upsert enriched tags into the taxonomy tables "
            "(authority_terms, and optionally concepts + concept_aliases). "
            "Requires sampled rows from --sample-count or --import-sample-json."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform all DB writes but roll back at the end (for --import-main-db).",
    )
    parser.add_argument(
        "--create-concepts",
        action="store_true",
        help=(
            "Opt-in: create new Concept rows for imported tags not already linked to a concept. "
            "Default: import authority_terms only without creating new root concepts."
        ),
    )
    parser.add_argument(
        "--import-commit-every",
        type=int,
        default=250,
        help="Commit every N imported rows (default: 250)",
    )
    parser.add_argument(
        "--validation-report-json",
        help="Optional path to write field-coverage validation report JSON",
    )

    args = parser.parse_args()
    try:
        _run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)


def _run(args: argparse.Namespace) -> None:
    main_db = Path(args.main_db).expanduser().resolve()
    if not main_db.exists():
        raise FileNotFoundError(f"Main DB not found: {main_db}")
    if args.import_commit_every < 1:
        raise ValueError("--import-commit-every must be >= 1")
    if args.sample_count < 0:
        raise ValueError("--sample-count must be >= 0")

    print(f"Main DB:                 {main_db}")
    print(f"Authority:               {args.authority_name}")
    print("Scanning gallery...      ", end="", flush=True)
    gallery_tag_data = _get_gallery_civitai_tag_data(main_db)
    print(f"{len(gallery_tag_data)} unique tag IDs found")

    known_ids = _get_known_civitai_term_ids(main_db, args.authority_name)

    # Sort unknown IDs by descending gallery count (most-used first).
    unknown_ids_sorted = sorted(
        (tid for tid in gallery_tag_data if tid not in known_ids),
        key=lambda tid: gallery_tag_data[tid].count,
        reverse=True,
    )
    known_in_gallery = len(gallery_tag_data) - len(unknown_ids_sorted)
    gallery_occurrences = sum(e.count for e in gallery_tag_data.values())

    print(f"Gallery tag occurrences: {gallery_occurrences}")
    print(f"Known in authority_terms:{known_in_gallery}")
    print(f"Unknown tags:            {len(unknown_ids_sorted)}")
    if unknown_ids_sorted:
        preview = ", ".join(
            f"{tid}(×{gallery_tag_data[tid].count})" for tid in unknown_ids_sorted[:20]
        )
        suffix = " ..." if len(unknown_ids_sorted) > 20 else ""
        print(f"Unknown preview:         {preview}{suffix}")

    # Always write the discovery summary JSON.
    unknown_json_path = (
        Path(args.unknown_json).expanduser().resolve()
        if args.unknown_json
        else main_db.parent / "unknown_gallery_tag_ids.json"
    )
    unknown_payload = {
        "main_db": str(main_db),
        "authority_name": args.authority_name,
        "gallery_unique_tag_count": len(gallery_tag_data),
        "gallery_tag_occurrence_sum": gallery_occurrences,
        "known_count": known_in_gallery,
        "unknown_count": len(unknown_ids_sorted),
        "unknown_tags": [
            {
                "tag_id": tid,
                "gallery_count": gallery_tag_data[tid].count,
                "first_civitai_image_id": gallery_tag_data[tid].first_civitai_image_id,
            }
            for tid in unknown_ids_sorted
        ],
    }
    unknown_json_path.write_text(
        json.dumps(unknown_payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Unknown JSON:            {unknown_json_path}")

    # Resolve rows for import/validation — prefer file over fresh sampling.
    loaded_rows: list[dict[str, Any]] | None = None
    if args.import_sample_json:
        sample_path = Path(args.import_sample_json).expanduser().resolve()
        if not sample_path.exists():
            raise FileNotFoundError(f"--import-sample-json not found: {sample_path}")
        loaded = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("--import-sample-json must contain a JSON object")
        raw_rows = loaded.get("rows")
        loaded_rows = (
            [r for r in raw_rows if isinstance(r, dict)]
            if isinstance(raw_rows, list)
            else []
        )
        print(f"Loaded sample JSON:      {sample_path} ({len(loaded_rows)} rows)")

    if args.sample_count <= 0 and loaded_rows is None and not args.import_main_db:
        return

    sampled_rows: list[dict[str, Any]] = []
    if args.sample_count > 0:
        sample_output = (
            Path(args.sample_output).expanduser().resolve()
            if args.sample_output
            else main_db.parent / f"unknown_gallery_tag_enrichment_{args.sample_count}.json"
        )
        unknown_dict = {tid: gallery_tag_data[tid] for tid in unknown_ids_sorted}
        print(f"Enriching {args.sample_count} unknown tags via CivitAI API...")
        sampled = _enrich_unknown_tags(
            unknown_tags=unknown_dict,
            sample_count=args.sample_count,
            sleep_ms=args.sample_sleep_ms,
            output_path=sample_output,
        )
        cov = sampled.get("coverage") if isinstance(sampled, dict) else {}
        if not isinstance(cov, dict):
            cov = {}
        n = cov.get("sample_size", 0)
        print(
            f"Enrichment coverage:     "
            f"type={cov.get('with_type')}/{n}, "
            f"nsfwLevel={cov.get('with_nsfwLevel')}/{n}, "
            f"automated={cov.get('with_automated')}/{n}, "
            f"concrete={cov.get('with_concrete')}/{n}"
        )
        print(f"Enrichment JSON:         {sample_output}")
        raw_sampled = sampled.get("rows") if isinstance(sampled, dict) else []
        sampled_rows = (
            [r for r in raw_sampled if isinstance(r, dict)]
            if isinstance(raw_sampled, list)
            else []
        )

    rows_for_import = loaded_rows if loaded_rows is not None else sampled_rows

    if rows_for_import:
        validation = _validate_enriched_rows(rows_for_import)
        cov2 = validation.get("coverage") or {}
        n2 = int(validation.get("sample_size") or 0)
        print(
            f"Validation coverage:     "
            f"name={cov2.get('name', 0)}/{n2}, "
            f"type={cov2.get('type', 0)}/{n2}, "
            f"nsfwLevel={cov2.get('nsfwLevel', 0)}/{n2}, "
            f"automated={cov2.get('automated', 0)}/{n2}, "
            f"concrete={cov2.get('concrete', 0)}/{n2}"
        )
        missing_any = int(validation.get("missing_any_required") or 0)
        if missing_any > 0:
            print(f"Validation warning:      {missing_any} rows missing at least one required field")
        if args.validation_report_json:
            report_path = Path(args.validation_report_json).expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"Validation report:       {report_path}")
    else:
        validation = None

    if not args.import_main_db:
        return

    if not rows_for_import:
        print(
            "Import skipped:          no enriched rows available "
            "(use --sample-count or --import-sample-json)"
        )
        return

    print(
        f"Import mode:             {'dry-run (rollback)' if args.dry_run else 'commit'} "
        f"for {len(rows_for_import)} rows"
    )
    print(f"Create concepts:         {'yes' if args.create_concepts else 'no (default)'}")
    import_stats = _import_enriched_rows(
        main_db_path=main_db,
        authority_name=args.authority_name,
        rows=rows_for_import,
        gallery_tag_data=gallery_tag_data,
        dry_run=args.dry_run,
        commit_every=args.import_commit_every,
        create_concepts=args.create_concepts,
    )
    print(f"Rows scanned:            {import_stats['rows_scanned']}")
    print(f"Rows skipped:            {import_stats['rows_skipped']}")
    print(f"Concepts created:        {import_stats['concepts_created']}")
    print(f"Concepts reused:         {import_stats['concepts_reused']}")
    print(f"Aliases changed:         {import_stats['aliases_changed']}")
    print(f"Terms created:           {import_stats['authority_terms_created']}")
    print(f"Terms updated:           {import_stats['authority_terms_updated']}")
    print(f"Terms seen:              {import_stats['authority_terms_seen']}")
    if not args.dry_run:
        print(f"Transactions committed:  {import_stats['transactions_committed']}")
    else:
        print("Transactions committed:  0 (dry-run)")


if __name__ == "__main__":
    main()
