#!/usr/bin/env python3
"""Compare extracted getInfinite tag IDs against known CivitAI tags in main DB.

This script reads tag IDs from an extracted getInfinite SQLite dataset and compares
those IDs against the application's main authority tag mapping (authority_terms for
CivitAI). It can optionally probe CivitAI `tag.getById` for an unknown tag ID.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from path_setup import PROJECT_ROOT  # noqa: F401 - adds src/ to sys.path
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.http_client import CivitaiHttpClient, CivitaiRequestError


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


def _get_extracted_tag_ids(extracted_db_path: Path, table_name: str) -> set[int]:
    image_tags_table = f"{table_name}_image_tags"

    conn = sqlite3.connect(extracted_db_path)
    try:
        # Preferred source: normalized image-tag bridge table.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (image_tags_table,),
        ).fetchone()
        if row:
            rows = conn.execute(
                f"SELECT DISTINCT tag_id FROM {image_tags_table} WHERE tag_id IS NOT NULL"
            ).fetchall()
            return {int(tag_id) for (tag_id,) in rows if _parse_int(tag_id) is not None}

        # Fallback source: JSON-encoded tagIds field in base table.
        base_rows = conn.execute(
            f"SELECT tagIds FROM {table_name} WHERE tagIds IS NOT NULL"
        ).fetchall()
        out: set[int] = set()
        for (tag_ids_json,) in base_rows:
            if not isinstance(tag_ids_json, str) or not tag_ids_json.strip():
                continue
            try:
                values = json.loads(tag_ids_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(values, list):
                continue
            for value in values:
                tag_id = _parse_int(value)
                if tag_id is not None:
                    out.add(tag_id)
        return out
    finally:
        conn.close()


def _get_extracted_tag_counts(extracted_db_path: Path, table_name: str) -> dict[int, int]:
    image_tags_table = f"{table_name}_image_tags"

    conn = sqlite3.connect(extracted_db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (image_tags_table,),
        ).fetchone()
        if row:
            rows = conn.execute(
                f"""
                SELECT tag_id, COUNT(*)
                FROM {image_tags_table}
                WHERE tag_id IS NOT NULL
                GROUP BY tag_id
                """
            ).fetchall()
            out: dict[int, int] = {}
            for raw_tag_id, raw_count in rows:
                tag_id = _parse_int(raw_tag_id)
                if tag_id is None:
                    continue
                out[tag_id] = int(raw_count or 0)
            return out

        # Fallback source: JSON-encoded tagIds field in base table.
        base_rows = conn.execute(
            f"SELECT tagIds FROM {table_name} WHERE tagIds IS NOT NULL"
        ).fetchall()
        out: dict[int, int] = {}
        for (tag_ids_json,) in base_rows:
            if not isinstance(tag_ids_json, str) or not tag_ids_json.strip():
                continue
            try:
                values = json.loads(tag_ids_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(values, list):
                continue
            for value in values:
                tag_id = _parse_int(value)
                if tag_id is None:
                    continue
                out[tag_id] = int(out.get(tag_id, 0)) + 1
        return out
    finally:
        conn.close()


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


def _iter_civitai_tag_names_from_metadata(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    civitai_payload = metadata.get("civitai")
    if not isinstance(civitai_payload, dict):
        return []

    raw_tags = civitai_payload.get("tags")
    if not isinstance(raw_tags, list):
        return []

    names: list[str] = []
    for item in raw_tags:
        if isinstance(item, str):
            text = item.strip()
            if text:
                names.append(text)
            continue
        if isinstance(item, dict):
            raw_name = item.get("name")
            if isinstance(raw_name, str):
                text = raw_name.strip()
                if text:
                    names.append(text)
    return names


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


def _get_main_civitai_tag_counts(main_db_path: Path) -> tuple[dict[int, int], dict[str, int]]:
    conn = sqlite3.connect(main_db_path)
    try:
        rows = conn.execute(
            "SELECT json_metadata FROM images WHERE json_metadata IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    id_counts: dict[int, int] = {}
    name_counts: dict[str, int] = {}
    for (raw_metadata,) in rows:
        metadata = _coerce_metadata_dict(raw_metadata)
        if not metadata:
            continue

        tag_ids = _iter_civitai_tag_ids_from_metadata(metadata)
        if tag_ids:
            for tag_id in tag_ids:
                id_counts[tag_id] = int(id_counts.get(tag_id, 0)) + 1
            continue

        for tag_name in _iter_civitai_tag_names_from_metadata(metadata):
            normalized = _normalize_name(tag_name)
            if not normalized:
                continue
            name_counts[normalized] = int(name_counts.get(normalized, 0)) + 1
    return id_counts, name_counts


def _sync_civitai_term_counts(
    *,
    main_db_path: Path,
    authority_name: str,
    extracted_tag_counts: dict[int, int],
    main_civitai_id_counts: dict[int, int],
    main_civitai_name_counts: dict[str, int],
    dry_run: bool,
    commit_every: int,
) -> dict[str, int]:
    stats = {
        "terms_scanned": 0,
        "terms_updated": 0,
        "transactions_committed": 0,
    }

    conn = sqlite3.connect(main_db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        rows = conn.execute(
            """
            SELECT at.id, at.external_tag_id, at.normalized_external_name, at.metadata_json
            FROM authority_terms at
            JOIN tag_authorities ta ON ta.id = at.authority_id
            WHERE lower(ta.name) = lower(?)
            ORDER BY at.id ASC
            """,
            (authority_name,),
        ).fetchall()

        batch_count = 0
        for term_id, external_tag_id, normalized_external_name, metadata_json in rows:
            stats["terms_scanned"] += 1

            tag_id = _parse_int(external_tag_id)
            extracted_count = int(extracted_tag_counts.get(tag_id, 0)) if tag_id is not None else 0

            normalized_name = _normalize_name(str(normalized_external_name or ""))
            main_count_by_id = int(main_civitai_id_counts.get(int(tag_id), 0)) if tag_id is not None else 0
            main_count_by_name = int(main_civitai_name_counts.get(normalized_name, 0)) if normalized_name else 0
            main_count = main_count_by_id if main_count_by_id > 0 else main_count_by_name

            metadata = _coerce_metadata_dict(metadata_json)
            old_post = _parse_int(metadata.get("post_count"))
            old_extracted = _parse_int(metadata.get("extracted_post_count"))
            old_main = _parse_int(metadata.get("main_gallery_count"))

            metadata["post_count"] = extracted_count
            metadata["extracted_post_count"] = extracted_count
            metadata["main_gallery_count"] = main_count
            metadata["count_source"] = "getinfinite_extracted+main_gallery_id_first_name_fallback"

            changed = (
                old_post != extracted_count
                or old_extracted != extracted_count
                or old_main != main_count
            )
            if changed:
                conn.execute(
                    """
                    UPDATE authority_terms
                    SET metadata_json = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (json.dumps(metadata, ensure_ascii=False), _utcnow_iso(), int(term_id)),
                )
                stats["terms_updated"] += 1

            batch_count += 1
            if commit_every > 0 and batch_count >= commit_every:
                if dry_run:
                    conn.rollback()
                else:
                    conn.commit()
                    stats["transactions_committed"] += 1
                batch_count = 0

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
            if batch_count > 0:
                stats["transactions_committed"] += 1

        return stats
    finally:
        conn.close()


def _get_known_civitai_tag_ids(main_db_path: Path, authority_name: str) -> set[int]:
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

        # Also consider collision-resolved alternate IDs tracked in metadata.
        if isinstance(metadata_json, str) and metadata_json.strip():
            try:
                metadata = json.loads(metadata_json)
            except json.JSONDecodeError:
                metadata = None
            if isinstance(metadata, dict):
                alt_ids = metadata.get("alternate_external_tag_ids")
                if isinstance(alt_ids, list):
                    for alt in alt_ids:
                        alt_parsed = _parse_int(alt)
                        if alt_parsed is not None:
                            out.add(alt_parsed)
    return out


def _probe_tag_get_by_id(tag_id: int, output_path: Path) -> dict[str, Any]:
    api = CivitaiAPI.get_instance()
    payload = {"id": int(tag_id), "authed": True}

    parsed_response = api._make_request(endpoint="tag.getById", payload_data=payload)
    raw_response = api._make_raw_request(endpoint="tag.getById", payload_data=payload)

    result = {
        "tag_id": int(tag_id),
        "endpoint": "tag.getById",
        "payload": payload,
        "parsed_response": parsed_response,
        "raw_response": raw_response,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _find_first_image_for_tag(extracted_db_path: Path, table_name: str, tag_id: int) -> int | None:
    image_tags_table = f"{table_name}_image_tags"

    conn = sqlite3.connect(extracted_db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (image_tags_table,),
        ).fetchone()
        if row:
            found = conn.execute(
                f"""
                SELECT image_id
                FROM {image_tags_table}
                WHERE tag_id = ?
                ORDER BY image_id ASC
                LIMIT 1
                """,
                (int(tag_id),),
            ).fetchone()
            if found:
                return int(found[0])

        # Fallback if normalized bridge table does not exist.
        rows = conn.execute(
            f"SELECT id, tagIds FROM {table_name} WHERE tagIds IS NOT NULL ORDER BY id ASC"
        ).fetchall()
        for image_id, tag_ids_json in rows:
            if not isinstance(tag_ids_json, str) or not tag_ids_json.strip():
                continue
            try:
                values = json.loads(tag_ids_json)
            except json.JSONDecodeError:
                continue
            if not isinstance(values, list):
                continue
            for value in values:
                parsed = _parse_int(value)
                if parsed == int(tag_id):
                    return _parse_int(image_id)
        return None
    finally:
        conn.close()


def _probe_tag_get_votable_tags(
    *,
    image_id: int,
    target_tag_id: int,
    output_path: Path,
) -> dict[str, Any]:
    api = CivitaiAPI.get_instance()
    payload = {"id": int(image_id), "type": "image", "authed": True}

    parsed_response = api._make_request(endpoint="tag.getVotableTags", payload_data=payload)
    raw_response = api._make_raw_request(endpoint="tag.getVotableTags", payload_data=payload)

    matched_tag = None
    if isinstance(parsed_response, list):
        for entry in parsed_response:
            if not isinstance(entry, dict):
                continue
            if _parse_int(entry.get("id")) == int(target_tag_id):
                matched_tag = entry
                break

    result = {
        "image_id": int(image_id),
        "target_tag_id": int(target_tag_id),
        "endpoint": "tag.getVotableTags",
        "payload": payload,
        "matched_tag": matched_tag,
        "parsed_response": parsed_response,
        "raw_response": raw_response,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _sample_unknown_tags_with_metadata(
    *,
    extracted_db: Path,
    table_name: str,
    unknown_tag_ids: list[int],
    sample_count: int,
    sleep_ms: int,
    output_path: Path,
) -> dict[str, Any]:
    api = CivitaiAPI.get_instance()
    sample_ids = list(unknown_tag_ids[: max(0, sample_count)])
    sleep_seconds = max(0, sleep_ms) / 1000.0

    rows: list[dict[str, Any]] = []
    coverage = {
        "sample_size": len(sample_ids),
        "with_first_image": 0,
        "getById_success": 0,
        "votable_match_success": 0,
        "with_type": 0,
        "with_nsfwLevel": 0,
        "with_automated": 0,
        "with_concrete": 0,
    }

    for idx, tag_id in enumerate(sample_ids, start=1):
        try:
            image_id = _find_first_image_for_tag(extracted_db, table_name, tag_id)
            if image_id is not None:
                coverage["with_first_image"] += 1

            # getById probe
            get_by_id_payload = {"id": int(tag_id), "authed": True}
            get_by_id = api._make_request(endpoint="tag.getById", payload_data=get_by_id_payload, strict=True)
            if isinstance(get_by_id, dict):
                coverage["getById_success"] += 1

            # getVotableTags probe via first image
            votable_payload: dict[str, Any] | None = None
            votable_response: Any = None
            votable_match: dict[str, Any] | None = None
            if image_id is not None:
                votable_payload = {"id": int(image_id), "type": "image", "authed": True}
                votable_response = api._make_request(endpoint="tag.getVotableTags", payload_data=votable_payload, strict=True)
                if isinstance(votable_response, list):
                    for entry in votable_response:
                        if not isinstance(entry, dict):
                            continue
                        if _parse_int(entry.get("id")) == int(tag_id):
                            votable_match = entry
                            coverage["votable_match_success"] += 1
                            break

            merged = {
                "id": tag_id,
                "name": None,
                "type": None,
                "nsfwLevel": None,
                "automated": None,
                "concrete": None,
            }

            # Base from getById
            if isinstance(get_by_id, dict):
                merged["name"] = get_by_id.get("name")
                merged["type"] = get_by_id.get("type")

            # Rich metadata from votable match preferred when available
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
                    "first_image_id": image_id,
                    "getById": get_by_id,
                    "votable_payload": votable_payload,
                    "votable_match": votable_match,
                    "merged": merged,
                }
            )

            if idx % 25 == 0 or idx == len(sample_ids):
                print(f"Sample progress:       {idx}/{len(sample_ids)}")

            if sleep_seconds > 0 and idx < len(sample_ids):
                time.sleep(sleep_seconds)
        except CivitaiRequestError as exc:
            status = exc.status_code
            print(f"\n[tag {tag_id}] API error (HTTP {status}): {exc}. Skipping tag.")
            if status == 403:
                backoff = CivitaiHttpClient.activate_global_backoff(60.0, reason="HTTP 403")
                print(f"⏳ 403 detected; enforcing {backoff:.0f}s backoff before next request.")
            continue
        except KeyboardInterrupt:
            print(f"\nInterrupted after {len(rows)}/{len(sample_ids)} sampled tags. Saving partial results.")
            break

    out = {
        "sampled_unknown_tag_ids": sample_ids,
        "coverage": coverage,
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _validate_sampled_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    required_fields = ["id", "name", "type", "nsfwLevel", "automated", "concrete"]
    coverage = {field: 0 for field in required_fields}
    mismatched_ids = 0

    for row in rows:
        tag_id = _parse_int(row.get("tag_id"))
        merged = row.get("merged")
        if not isinstance(merged, dict):
            continue

        merged_id = _parse_int(merged.get("id"))
        if tag_id is not None and merged_id != tag_id:
            mismatched_ids += 1

        for field in required_fields:
            if merged.get(field) is not None:
                coverage[field] += 1

    total = len(rows)
    complete_rows = 0
    for row in rows:
        merged = row.get("merged")
        if not isinstance(merged, dict):
            continue
        if all(merged.get(field) is not None for field in required_fields):
            complete_rows += 1

    return {
        "sample_size": total,
        "required_fields": required_fields,
        "coverage": coverage,
        "complete_rows": complete_rows,
        "missing_any_required": total - complete_rows,
        "mismatched_ids": mismatched_ids,
    }


def _ensure_authority(conn: sqlite3.Connection, authority_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM tag_authorities WHERE lower(name)=lower(?)",
        (authority_name,),
    ).fetchone()
    if row:
        return int(row[0])

    now_iso = _utcnow_iso()
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

    # Fallback read in case SQLite doesn't return rowid on this table.
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
        cur_authority_id = row[1]
        cur_external = row[2]
        cur_is_preferred = row[3]
        changed = False
        if cur_authority_id is None:
            conn.execute(
                "UPDATE concept_aliases SET authority_id = ? WHERE id = ?",
                (authority_id, alias_id),
            )
            changed = True
        if (cur_external is None or str(cur_external).strip() == "") and external_tag_id:
            conn.execute(
                "UPDATE concept_aliases SET external_tag_id = ? WHERE id = ?",
                (external_tag_id, alias_id),
            )
            changed = True
        if not bool(cur_is_preferred):
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

        current_meta_obj: dict[str, Any] = {}
        raw_meta = by_name[2]
        if isinstance(raw_meta, str) and raw_meta.strip():
            try:
                parsed_meta = json.loads(raw_meta)
                if isinstance(parsed_meta, dict):
                    current_meta_obj = parsed_meta
            except json.JSONDecodeError:
                current_meta_obj = {}

        alt_ids = current_meta_obj.get("alternate_external_tag_ids")
        if not isinstance(alt_ids, list):
            alt_ids = []

        ext_id_text = str(external_tag_id)
        if ext_id_text not in alt_ids:
            alt_ids.append(ext_id_text)
            current_meta_obj["alternate_external_tag_ids"] = alt_ids
            changed = True

        merged_meta_json = json.dumps(current_meta_obj, ensure_ascii=False)
        conn.execute(
            """
            UPDATE authority_terms
            SET concept_id = ?,
                metadata_json = ?,
                last_seen_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (concept_id, merged_meta_json, now_iso, now_iso, term_id),
        )
        return "updated" if changed else "seen"

    conn.execute(
        """
        INSERT INTO authority_terms (
            authority_id,
            external_tag_id,
            external_name,
            normalized_external_name,
            concept_id,
            metadata_json,
            created_at,
            updated_at,
            last_seen_at
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


def _import_sampled_rows_into_main_db(
    *,
    main_db_path: Path,
    authority_name: str,
    rows: list[dict[str, Any]],
    extracted_tag_counts: dict[int, int],
    main_civitai_id_counts: dict[int, int],
    main_civitai_name_counts: dict[str, int],
    dry_run: bool,
    commit_every: int,
    create_concepts: bool,
) -> dict[str, Any]:
    stats = {
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

        def find_existing_concept_id(tag_id: int, normalized_name: str) -> int | None:
            by_external = conn.execute(
                """
                SELECT concept_id
                FROM authority_terms
                WHERE authority_id = ? AND external_tag_id = ? AND concept_id IS NOT NULL
                LIMIT 1
                """,
                (authority_id, str(tag_id)),
            ).fetchone()
            if by_external and _parse_int(by_external[0]) is not None:
                return int(by_external[0])

            by_name = conn.execute(
                """
                SELECT concept_id
                FROM authority_terms
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

                tag_id = _parse_int(merged.get("id") if merged.get("id") is not None else row.get("tag_id"))
                name = str(merged.get("name") or "").strip()
                normalized_name = _normalize_name(name)
                if tag_id is None or not normalized_name:
                    stats["rows_skipped"] += 1
                    continue

                concept_id = find_existing_concept_id(tag_id, normalized_name)
                if concept_id is not None:
                    stats["concepts_reused"] += 1
                elif create_concepts:
                    concept_id, created = _get_or_create_concept(conn, normalized_name)
                    if created:
                        stats["concepts_created"] += 1
                    else:
                        stats["concepts_reused"] += 1

                if concept_id is not None:
                    alias_changed = _ensure_alias(
                        conn,
                        concept_id=concept_id,
                        alias=name,
                        authority_id=authority_id,
                        external_tag_id=str(tag_id),
                    )
                    if alias_changed:
                        stats["aliases_changed"] += 1

                metadata = {
                    "tag_type": merged.get("type"),
                    "nsfw_level": merged.get("nsfwLevel"),
                    "automated": merged.get("automated"),
                    "concrete": merged.get("concrete"),
                    "source": "getById+getVotableTags_sample",
                    "first_image_id": row.get("first_image_id"),
                    # Keep Danbooru-style key so UI count rendering can reuse the same field.
                    "post_count": int(extracted_tag_counts.get(int(tag_id), 0)),
                    "extracted_post_count": int(extracted_tag_counts.get(int(tag_id), 0)),
                    "main_gallery_count": (
                        int(main_civitai_id_counts.get(int(tag_id), 0))
                        if int(main_civitai_id_counts.get(int(tag_id), 0)) > 0
                        else int(main_civitai_name_counts.get(normalized_name, 0))
                    ),
                    "count_source": "getinfinite_extracted+main_gallery_id_first_name_fallback",
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
            print("\nInterrupted during import. Committing pending batch before exit.")

        # Flush final/pending batch
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare getInfinite extracted tag IDs against known CivitAI authority tags "
            "in main database, and optionally probe tag.getById for an unknown ID."
        )
    )
    parser.add_argument(
        "--extracted-db",
        required=True,
        help="Path to extracted getInfinite SQLite DB (extracted_getinfinite_metadata.sqlite)",
    )
    parser.add_argument(
        "--table-name",
        default="civitai_getinfinite_images",
        help="Base extracted table name (default: civitai_getinfinite_images)",
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
        help="Optional path to write unknown tag IDs as JSON",
    )
    parser.add_argument(
        "--probe-api",
        action="store_true",
        help="Run a test tag.getById call after diffing (uses first unknown if no explicit id)",
    )
    parser.add_argument(
        "--probe-tag-id",
        type=int,
        help="Tag ID to probe with tag.getById (default: first unknown ID)",
    )
    parser.add_argument(
        "--probe-output",
        help="Optional path to write probe response JSON",
    )
    parser.add_argument(
        "--probe-votable-tags",
        action="store_true",
        help=(
            "After selecting a probe tag, find the first image where the tag appears in extracted DB "
            "and call tag.getVotableTags for that image."
        ),
    )
    parser.add_argument(
        "--probe-image-id",
        type=int,
        help=(
            "Image ID to use for --probe-votable-tags. "
            "If omitted, script selects the first image containing the probe tag."
        ),
    )
    parser.add_argument(
        "--probe-votable-output",
        help="Optional path to write tag.getVotableTags probe JSON",
    )
    parser.add_argument(
        "--sample-unknown-count",
        type=int,
        default=0,
        help=(
            "Sample first N unknown tag IDs and enrich each using getById + getVotableTags "
            "via the first image containing that tag (default: 0 = disabled)."
        ),
    )
    parser.add_argument(
        "--sample-sleep-ms",
        type=int,
        default=75,
        help="Milliseconds to sleep between sampled tag requests (default: 75)",
    )
    parser.add_argument(
        "--sample-output",
        help="Optional path to write sampled unknown-tag enrichment JSON",
    )
    parser.add_argument(
        "--import-main-db",
        action="store_true",
        help=(
            "Upsert sampled unknown tags into main DB taxonomy tables "
            "(concepts/concept_aliases/authority_terms)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="When importing, perform all DB writes but rollback at the end.",
    )
    parser.add_argument(
        "--import-sample-json",
        help=(
            "Path to existing unknown_tag_sample_enrichment_*.json. "
            "If provided, import/validation use this file instead of newly sampled rows."
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
        help="Optional path to write field coverage validation report JSON",
    )
    parser.add_argument(
        "--create-concepts",
        action="store_true",
        help=(
            "Opt-in: create new concepts for imported tags that do not already map to a concept. "
            "Default behavior is to import authority terms without creating new root concepts."
        ),
    )

    args = parser.parse_args()

    try:
        _run(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        raise SystemExit(130)


def _run(args: argparse.Namespace) -> None:
    extracted_db = Path(args.extracted_db).expanduser().resolve()
    main_db = Path(args.main_db).expanduser().resolve()

    if not extracted_db.exists():
        raise FileNotFoundError(f"Extracted DB not found: {extracted_db}")
    if not main_db.exists():
        raise FileNotFoundError(f"Main DB not found: {main_db}")

    extracted_tag_ids = _get_extracted_tag_ids(extracted_db, args.table_name)
    extracted_tag_counts = _get_extracted_tag_counts(extracted_db, args.table_name)
    known_tag_ids = _get_known_civitai_tag_ids(main_db, args.authority_name)
    main_civitai_id_counts, main_civitai_name_counts = _get_main_civitai_tag_counts(main_db)

    unknown_tag_ids = sorted(extracted_tag_ids - known_tag_ids)
    known_in_extract = len(extracted_tag_ids) - len(unknown_tag_ids)
    extracted_tag_occurrences = sum(int(v or 0) for v in extracted_tag_counts.values())
    main_civitai_tag_occurrences = (
        sum(int(v or 0) for v in main_civitai_id_counts.values())
        + sum(int(v or 0) for v in main_civitai_name_counts.values())
    )

    print(f"Extracted DB:          {extracted_db}")
    print(f"Main DB:               {main_db}")
    print(f"Authority:             {args.authority_name}")
    print(f"Extracted unique tags: {len(extracted_tag_ids)}")
    print(f"Extracted tag count sum (all scope): {extracted_tag_occurrences}")
    print(f"Main DB civitai tag count sum (all scope): {main_civitai_tag_occurrences}")
    print(f"Known in main DB:      {known_in_extract}")
    print(f"Unknown tags:          {len(unknown_tag_ids)}")

    if unknown_tag_ids:
        preview = ", ".join(str(v) for v in unknown_tag_ids[:30])
        print(f"Unknown tag preview:   {preview}{' ...' if len(unknown_tag_ids) > 30 else ''}")

    unknown_json_path = (
        Path(args.unknown_json).expanduser().resolve()
        if args.unknown_json
        else extracted_db.parent / "unknown_tag_ids.json"
    )
    unknown_payload = {
        "extracted_db": str(extracted_db),
        "main_db": str(main_db),
        "authority_name": args.authority_name,
        "extracted_unique_tag_count": len(extracted_tag_ids),
        "extracted_tag_count_sum": extracted_tag_occurrences,
        "main_civitai_tag_count_sum": main_civitai_tag_occurrences,
        "known_in_main_db_count": known_in_extract,
        "unknown_count": len(unknown_tag_ids),
        "unknown_tag_ids": unknown_tag_ids,
    }
    unknown_json_path.write_text(
        json.dumps(unknown_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Unknown JSON:          {unknown_json_path}")

    loaded_sample_from_file: dict[str, Any] | None = None
    if args.import_sample_json:
        sample_path = Path(args.import_sample_json).expanduser().resolve()
        if not sample_path.exists():
            raise FileNotFoundError(f"Import sample JSON not found: {sample_path}")
        loaded = json.loads(sample_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("--import-sample-json must contain a JSON object")
        loaded_sample_from_file = loaded
        print(f"Loaded sample JSON:    {sample_path}")

    if not args.probe_api:
        if args.sample_unknown_count <= 0 and not loaded_sample_from_file and not args.import_main_db:
            return

    if args.sample_unknown_count < 0:
        raise ValueError("--sample-unknown-count must be >= 0")

    probe_tag_id: int | None = None
    if args.probe_tag_id is not None:
        probe_tag_id = int(args.probe_tag_id)
    elif unknown_tag_ids:
        probe_tag_id = int(unknown_tag_ids[0])
    elif args.probe_api or args.probe_votable_tags:
        print("Probe skipped: no unknown tags found and no --probe-tag-id provided.")

    run_sample_rows: list[dict[str, Any]] = []

    if args.probe_api:
        if probe_tag_id is None:
            print("Probing tag.getById:   skipped")
        else:
            probe_output = (
                Path(args.probe_output).expanduser().resolve()
                if args.probe_output
                else extracted_db.parent / f"tag_getById_probe_{probe_tag_id}.json"
            )
            print(f"Probing tag.getById:   {probe_tag_id}")
            probe = _probe_tag_get_by_id(probe_tag_id, probe_output)
            parsed = probe.get("parsed_response")
            if isinstance(parsed, dict):
                print(f"Probe parsed keys:     {', '.join(sorted(parsed.keys())[:20])}")
            elif parsed is None:
                print("Probe parsed response: <none>")
            else:
                print(f"Probe parsed type:     {type(parsed).__name__}")
            print(f"Probe JSON:            {probe_output}")

    if not args.probe_votable_tags:
        if args.sample_unknown_count <= 0 and not loaded_sample_from_file and not args.import_main_db:
            return

    if args.probe_votable_tags and probe_tag_id is None:
        print("Votable probe skipped: no probe tag ID available.")
        probe_image_id = None
    elif args.probe_votable_tags:
        if args.probe_image_id is not None:
            probe_image_id = int(args.probe_image_id)
        else:
            probe_image_id = _find_first_image_for_tag(extracted_db, args.table_name, probe_tag_id)
            if probe_image_id is None:
                print(f"Votable probe skipped: no image found for tag {probe_tag_id} in extracted DB.")
                probe_image_id = None
    else:
        probe_image_id = None

    if args.probe_votable_tags and probe_image_id is not None and probe_tag_id is not None:
        votable_output = (
            Path(args.probe_votable_output).expanduser().resolve()
            if args.probe_votable_output
            else extracted_db.parent / f"tag_getVotableTags_probe_tag-{probe_tag_id}_image-{probe_image_id}.json"
        )
        print(f"Probing tag.getVotableTags on image: {probe_image_id}")
        votable_probe = _probe_tag_get_votable_tags(
            image_id=probe_image_id,
            target_tag_id=probe_tag_id,
            output_path=votable_output,
        )

        matched = votable_probe.get("matched_tag")
        if isinstance(matched, dict):
            interesting = {
                "id": matched.get("id"),
                "name": matched.get("name"),
                "type": matched.get("type"),
                "nsfwLevel": matched.get("nsfwLevel"),
                "automated": matched.get("automated"),
                "concrete": matched.get("concrete"),
                "score": matched.get("score"),
            }
            print(f"Votable matched tag:   {json.dumps(interesting, ensure_ascii=False)}")
        else:
            print(f"Votable matched tag:   <not found for tag {probe_tag_id} on image {probe_image_id}>")
        print(f"Votable probe JSON:    {votable_output}")

    if args.sample_unknown_count <= 0 and not loaded_sample_from_file and not args.import_main_db:
        return

    sample_output = (
        Path(args.sample_output).expanduser().resolve()
        if args.sample_output
        else extracted_db.parent / f"unknown_tag_sample_enrichment_{args.sample_unknown_count}.json"
    )
    if args.sample_unknown_count > 0:
        print(f"Sampling unknown tags: {args.sample_unknown_count}")
        sampled = _sample_unknown_tags_with_metadata(
            extracted_db=extracted_db,
            table_name=args.table_name,
            unknown_tag_ids=unknown_tag_ids,
            sample_count=args.sample_unknown_count,
            sleep_ms=args.sample_sleep_ms,
            output_path=sample_output,
        )
        cov_obj = sampled.get("coverage") if isinstance(sampled, dict) else {}
        cov = cov_obj if isinstance(cov_obj, dict) else {}
        print(f"Sample output:         {sample_output}")
        print(
            "Sample coverage:       "
            f"type={cov.get('with_type')}/{cov.get('sample_size')}, "
            f"nsfwLevel={cov.get('with_nsfwLevel')}/{cov.get('sample_size')}, "
            f"automated={cov.get('with_automated')}/{cov.get('sample_size')}, "
            f"concrete={cov.get('with_concrete')}/{cov.get('sample_size')}"
        )
        run_rows_obj = sampled.get("rows") if isinstance(sampled, dict) else []
        if isinstance(run_rows_obj, list):
            run_sample_rows = [r for r in run_rows_obj if isinstance(r, dict)]

    rows_for_validation_import: list[dict[str, Any]] = []
    if loaded_sample_from_file:
        loaded_rows = loaded_sample_from_file.get("rows")
        if isinstance(loaded_rows, list):
            rows_for_validation_import = [r for r in loaded_rows if isinstance(r, dict)]
    elif run_sample_rows:
        rows_for_validation_import = run_sample_rows

    if rows_for_validation_import:
        validation = _validate_sampled_rows(rows_for_validation_import)
        coverage_obj = validation.get("coverage")
        coverage = coverage_obj if isinstance(coverage_obj, dict) else {}
        sample_size = int(validation.get("sample_size") or 0)
        print(
            "Validation coverage:   "
            f"id={sample_size - int(validation.get('mismatched_ids') or 0)}/{sample_size}, "
            f"name={coverage.get('name', 0)}/{sample_size}, "
            f"type={coverage.get('type', 0)}/{sample_size}, "
            f"nsfwLevel={coverage.get('nsfwLevel', 0)}/{sample_size}, "
            f"automated={coverage.get('automated', 0)}/{sample_size}, "
            f"concrete={coverage.get('concrete', 0)}/{sample_size}"
        )
        missing_any = int(validation.get("missing_any_required") or 0)
        print(f"Validation missing-any: {missing_any}")

        if args.validation_report_json:
            report_path = Path(args.validation_report_json).expanduser().resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"Validation report:    {report_path}")
    else:
        validation = None

    if not args.import_main_db:
        return

    if args.import_commit_every < 1:
        raise ValueError("--import-commit-every must be >= 1")

    print(
        f"Syncing civitai counts: extracted_ids={len(extracted_tag_counts)}, "
        f"main_ids={len(main_civitai_id_counts)}, "
        f"main_names={len(main_civitai_name_counts)}"
    )
    count_sync_stats = _sync_civitai_term_counts(
        main_db_path=main_db,
        authority_name=args.authority_name,
        extracted_tag_counts=extracted_tag_counts,
        main_civitai_id_counts=main_civitai_id_counts,
        main_civitai_name_counts=main_civitai_name_counts,
        dry_run=args.dry_run,
        commit_every=args.import_commit_every,
    )
    print(f"Count-sync terms scanned: {count_sync_stats['terms_scanned']}")
    print(f"Count-sync terms updated: {count_sync_stats['terms_updated']}")
    if not args.dry_run:
        print(f"Count-sync commits:       {count_sync_stats['transactions_committed']}")
    else:
        print("Count-sync commits:       0 (dry-run)")

    if not rows_for_validation_import:
        print("Import rows:            skipped (no sampled rows provided)")
        return

    if validation and int(validation.get("missing_any_required") or 0) > 0:
        print(
            "Warning: some sampled rows are missing required merged fields; "
            "partial metadata rows may still be imported."
        )

    print(
        f"Import mode:           {'dry-run (rollback)' if args.dry_run else 'commit'} "
        f"for {len(rows_for_validation_import)} sampled rows"
    )
    print(f"Create concepts:       {'yes' if args.create_concepts else 'no (default)'}")
    import_stats = _import_sampled_rows_into_main_db(
        main_db_path=main_db,
        authority_name=args.authority_name,
        rows=rows_for_validation_import,
        extracted_tag_counts=extracted_tag_counts,
        main_civitai_id_counts=main_civitai_id_counts,
        main_civitai_name_counts=main_civitai_name_counts,
        dry_run=args.dry_run,
        commit_every=args.import_commit_every,
        create_concepts=args.create_concepts,
    )
    print(f"Import rows scanned:   {import_stats['rows_scanned']}")
    print(f"Import rows skipped:   {import_stats['rows_skipped']}")
    print(f"Concepts created:      {import_stats['concepts_created']}")
    print(f"Concepts reused:       {import_stats['concepts_reused']}")
    print(f"Aliases changed:       {import_stats['aliases_changed']}")
    print(f"Authority terms created: {import_stats['authority_terms_created']}")
    print(f"Authority terms updated: {import_stats['authority_terms_updated']}")
    print(f"Authority terms seen:    {import_stats['authority_terms_seen']}")
    if not args.dry_run:
        print(f"Transactions committed: {import_stats['transactions_committed']}")
    else:
        print("Transactions committed: 0 (dry-run)")


if __name__ == "__main__":
    main()
