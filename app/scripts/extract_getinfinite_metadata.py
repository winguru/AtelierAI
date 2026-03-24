#!/usr/bin/env python3
"""Extract normalized image metadata from crawl_getinfinite page JSON files.

By default, this script writes to a dedicated SQLite database to keep the
high-volume CivitAI crawl dataset separate from the main application database.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FIELDS = [
    "id",
    "reactionCount",
    "commentCount",
    "collectedCount",
    "index",
    "postId",
    "url",
    "nsfwLevel",
    "width",
    "height",
    "hash",
    "type",
    "userId",
    "onSite",
    "baseModel",
    "modelVersionId",
    "toolIds",
    "techniqueIds",
    "tagIds",
    "hasPositivePrompt",
    "createdAt",
    "mimeType",
]


@dataclass(frozen=True)
class ExtractStats:
    page_files_scanned: int
    records_read: int
    records_written: int
    image_tag_links_written: int
    unique_tags_written: int


CHECKPOINT_FILE_NAME = "extraction_checkpoint.json"


@dataclass
class ExtractionCheckpoint:
    schema_version: int = 1
    started_at: str = ""
    updated_at: str = ""
    input_dir: str = ""
    processed_files: list[str] = field(default_factory=list)
    records_committed: int = 0
    image_tag_links_committed: int = 0


@dataclass
class ExtractProgress:
    started_at: str
    started_monotonic: float
    phase: str = "initializing"
    stop_reason: str | None = None
    page_files_total: int = 0
    page_files_scanned: int = 0
    records_read: int = 0
    records_written: int = 0
    image_tag_links_written: int = 0
    unique_tags_written: int = 0
    session_records: int = 0


@dataclass(frozen=True)
class TagInfo:
    tag_id: int
    name: str | None
    description: str | None
    nsfw_level: int | None
    tag_type: str | None
    metadata_json: str | None
    source: str


def _sorted_page_files(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("page_*.json"))


def _as_json_text(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _extract_model_version_id(item: dict[str, Any]) -> Any:
    model_version_id = item.get("modelVersionId")
    if model_version_id is not None:
        return model_version_id

    candidates = item.get("modelVersionIds")
    if isinstance(candidates, list) and len(candidates) == 1:
        return candidates[0]
    return None


def _parse_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _load_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _extract_nsfw_from_metadata(metadata: dict[str, Any] | None) -> int | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("nsfw_level", "nsfwLevel"):
        parsed = _parse_int(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _load_tag_lookup_from_bootstrap(bootstrap_path: Path) -> dict[int, TagInfo]:
    if not bootstrap_path.exists():
        return {}

    payload = _load_json_file(bootstrap_path)
    terms = payload.get("terms") if isinstance(payload, dict) else None
    if not isinstance(terms, list):
        return {}

    out: dict[int, TagInfo] = {}
    for term in terms:
        if not isinstance(term, dict):
            continue

        tag_id = _parse_int(term.get("external_tag_id"))
        if tag_id is None:
            continue

        name = str(term.get("name") or "").strip() or None
        description = str(term.get("description") or "").strip() or None
        nsfw_level = _parse_int(term.get("nsfw_level") if "nsfw_level" in term else term.get("nsfwLevel"))
        tag_type = str(term.get("tag_type") or "").strip() or None

        metadata = {
            "tag_type": tag_type,
            "automated": term.get("automated"),
            "concrete": term.get("concrete"),
            "needs_review": term.get("needs_review"),
            "score_max": term.get("score_max"),
            "score_sum": term.get("score_sum"),
            "seen_count": term.get("seen_count"),
        }
        metadata_json = json.dumps(metadata, ensure_ascii=False)

        out[tag_id] = TagInfo(
            tag_id=tag_id,
            name=name,
            description=description,
            nsfw_level=nsfw_level,
            tag_type=tag_type,
            metadata_json=metadata_json,
            source="bootstrap",
        )

    return out


def _load_tag_lookup_from_authority_db(authority_db_path: Path) -> dict[int, TagInfo]:
    if not authority_db_path.exists():
        return {}

    conn = sqlite3.connect(authority_db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                at.external_tag_id,
                at.external_name,
                at.metadata_json,
                c.description
            FROM authority_terms at
            JOIN tag_authorities ta ON ta.id = at.authority_id
            LEFT JOIN concepts c ON c.id = at.concept_id
            WHERE lower(ta.name) = 'civitai'
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    out: dict[int, TagInfo] = {}
    for external_tag_id, external_name, metadata_json, concept_description in rows:
        tag_id = _parse_int(external_tag_id)
        if tag_id is None:
            continue

        parsed_meta: dict[str, Any] | None = None
        if isinstance(metadata_json, str) and metadata_json.strip():
            try:
                raw_meta = json.loads(metadata_json)
                if isinstance(raw_meta, dict):
                    parsed_meta = raw_meta
            except json.JSONDecodeError:
                parsed_meta = None

        out[tag_id] = TagInfo(
            tag_id=tag_id,
            name=str(external_name).strip() if external_name is not None else None,
            description=(
                str(concept_description).strip() if concept_description is not None else None
            ),
            nsfw_level=_extract_nsfw_from_metadata(parsed_meta),
            tag_type=(
                str(parsed_meta.get("tag_type")).strip()
                if isinstance(parsed_meta, dict) and parsed_meta.get("tag_type") is not None
                else None
            ),
            metadata_json=(
                json.dumps(parsed_meta, ensure_ascii=False) if isinstance(parsed_meta, dict) else None
            ),
            source="authority_db",
        )

    return out


def _merge_tag_lookups(*lookups: dict[int, TagInfo]) -> dict[int, TagInfo]:
    merged: dict[int, TagInfo] = {}
    for lookup in lookups:
        for tag_id, incoming in lookup.items():
            existing = merged.get(tag_id)
            if existing is None:
                merged[tag_id] = incoming
                continue

            merged[tag_id] = TagInfo(
                tag_id=tag_id,
                name=incoming.name or existing.name,
                description=incoming.description or existing.description,
                nsfw_level=incoming.nsfw_level if incoming.nsfw_level is not None else existing.nsfw_level,
                tag_type=incoming.tag_type or existing.tag_type,
                metadata_json=incoming.metadata_json or existing.metadata_json,
                source=incoming.source,
            )

    return merged


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "reactionCount": item.get("reactionCount"),
        "commentCount": item.get("commentCount"),
        "collectedCount": item.get("collectedCount"),
        "index": item.get("index"),
        "postId": item.get("postId"),
        "url": item.get("url"),
        "nsfwLevel": item.get("nsfwLevel"),
        "width": item.get("width"),
        "height": item.get("height"),
        "hash": item.get("hash"),
        "type": item.get("type"),
        "userId": item.get("userId"),
        "onSite": item.get("onSite"),
        "baseModel": item.get("baseModel"),
        "modelVersionId": _extract_model_version_id(item),
        "toolIds": item.get("toolIds") if isinstance(item.get("toolIds"), list) else [],
        "techniqueIds": (
            item.get("techniqueIds") if isinstance(item.get("techniqueIds"), list) else []
        ),
        "tagIds": item.get("tagIds") if isinstance(item.get("tagIds"), list) else [],
        "hasPositivePrompt": item.get("hasPositivePrompt"),
        "createdAt": item.get("createdAt"),
        "mimeType": item.get("mimeType"),
    }


def _iter_records(page_files: Iterable[Path], progress: ExtractProgress) -> Iterable[dict[str, Any]]:
    for page_file in page_files:
        progress.phase = f"reading {page_file.name}"
        with page_file.open("r", encoding="utf-8") as f:
            payload = json.load(f)

        progress.page_files_scanned += 1

        items = payload.get("items")
        if not isinstance(items, list):
            continue

        for item in items:
            if isinstance(item, dict):
                progress.records_read += 1
                yield _normalize_item(item)


def _read_page_file(page_file: Path) -> list[dict[str, Any]]:
    """Read and normalize all image records from a single page JSON file."""
    with page_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [_normalize_item(item) for item in items if isinstance(item, dict)]


def _init_sqlite(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY,
            reactionCount INTEGER,
            commentCount INTEGER,
            collectedCount INTEGER,
            itemIndex INTEGER,
            postId INTEGER,
            url TEXT,
            nsfwLevel INTEGER,
            width INTEGER,
            height INTEGER,
            hash TEXT,
            type TEXT,
            userId INTEGER,
            onSite INTEGER,
            baseModel TEXT,
            modelVersionId INTEGER,
            toolIds TEXT,
            techniqueIds TEXT,
            tagIds TEXT,
            hasPositivePrompt INTEGER,
            createdAt TEXT,
            mimeType TEXT
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_createdAt ON {table_name}(createdAt)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_postId ON {table_name}(postId)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_userId ON {table_name}(userId)"
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name}_tags (
            tag_id INTEGER PRIMARY KEY,
            name TEXT,
            description TEXT,
            nsfw_level INTEGER,
            tag_type TEXT,
            metadata_json TEXT,
            source TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name}_image_tags (
            image_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (image_id, tag_id)
        )
        """
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_image_tags_tag ON {table_name}_image_tags(tag_id)"
    )


def _open_sqlite(db_path: Path, table_name: str) -> sqlite3.Connection:
    """Open and initialise the SQLite output database, returning the open connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _init_sqlite(conn, table_name)
    conn.commit()
    return conn


def _write_sqlite_batch(
    conn: sqlite3.Connection,
    records: list[dict[str, Any]],
    table_name: str,
    tag_lookup: dict[int, TagInfo],
) -> tuple[int, int, int]:
    """Write one batch of records to an already-open SQLite connection and commit.

    Returns (rows_written, image_tag_links_written, unique_tag_ids_in_batch).
    """
    sql = f"""
        INSERT INTO {table_name} (
            id, reactionCount, commentCount, collectedCount, itemIndex, postId, url,
            nsfwLevel, width, height, hash, type, userId, onSite, baseModel,
            modelVersionId, toolIds, techniqueIds, tagIds, hasPositivePrompt,
            createdAt, mimeType
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            reactionCount=excluded.reactionCount,
            commentCount=excluded.commentCount,
            collectedCount=excluded.collectedCount,
            itemIndex=excluded.itemIndex,
            postId=excluded.postId,
            url=excluded.url,
            nsfwLevel=excluded.nsfwLevel,
            width=excluded.width,
            height=excluded.height,
            hash=excluded.hash,
            type=excluded.type,
            userId=excluded.userId,
            onSite=excluded.onSite,
            baseModel=excluded.baseModel,
            modelVersionId=excluded.modelVersionId,
            toolIds=excluded.toolIds,
            techniqueIds=excluded.techniqueIds,
            tagIds=excluded.tagIds,
            hasPositivePrompt=excluded.hasPositivePrompt,
            createdAt=excluded.createdAt,
            mimeType=excluded.mimeType
    """
    rows = [
        (
            record["id"],
            record["reactionCount"],
            record["commentCount"],
            record["collectedCount"],
            record["index"],
            record["postId"],
            record["url"],
            record["nsfwLevel"],
            record["width"],
            record["height"],
            record["hash"],
            record["type"],
            record["userId"],
            None if record["onSite"] is None else int(bool(record["onSite"])),
            record["baseModel"],
            record["modelVersionId"],
            _as_json_text(record["toolIds"]),
            _as_json_text(record["techniqueIds"]),
            _as_json_text(record["tagIds"]),
            None if record["hasPositivePrompt"] is None else int(bool(record["hasPositivePrompt"])),
            record["createdAt"],
            record["mimeType"],
        )
        for record in records
        if record.get("id") is not None
    ]
    conn.executemany(sql, rows)

    unique_tag_ids: set[int] = set()
    image_tag_links: list[tuple[int, int]] = []
    for record in records:
        image_id = record.get("id")
        if image_id is None:
            continue
        for raw_tag_id in record.get("tagIds") or []:
            tag_id = _parse_int(raw_tag_id)
            if tag_id is None:
                continue
            unique_tag_ids.add(tag_id)
            image_tag_links.append((int(image_id), tag_id))

    now_iso = datetime.now(timezone.utc).isoformat()
    tag_rows = []
    for tag_id in sorted(unique_tag_ids):
        info = tag_lookup.get(tag_id)
        if info is None:
            info = TagInfo(
                tag_id=tag_id,
                name=None,
                description=None,
                nsfw_level=None,
                tag_type=None,
                metadata_json=None,
                source="unresolved",
            )
        tag_rows.append(
            (
                info.tag_id,
                info.name,
                info.description or info.name,
                info.nsfw_level,
                info.tag_type,
                info.metadata_json,
                info.source,
                now_iso,
            )
        )

    conn.executemany(
        f"""
        INSERT INTO {table_name}_tags (
            tag_id, name, description, nsfw_level, tag_type, metadata_json, source, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tag_id) DO UPDATE SET
            name=COALESCE(excluded.name, {table_name}_tags.name),
            description=COALESCE(excluded.description, {table_name}_tags.description),
            nsfw_level=COALESCE(excluded.nsfw_level, {table_name}_tags.nsfw_level),
            tag_type=COALESCE(excluded.tag_type, {table_name}_tags.tag_type),
            metadata_json=COALESCE(excluded.metadata_json, {table_name}_tags.metadata_json),
            source=excluded.source,
            updated_at=excluded.updated_at
        """,
        tag_rows,
    )
    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {table_name}_image_tags (image_id, tag_id)
        VALUES (?, ?)
        """,
        image_tag_links,
    )
    conn.commit()
    return len(rows), len(image_tag_links), len(unique_tag_ids)


def _write_sqlite(
    records: list[dict[str, Any]],
    db_path: Path,
    table_name: str,
    tag_lookup: dict[int, TagInfo],
    progress: ExtractProgress,
) -> tuple[int, int, int]:
    conn = _open_sqlite(db_path, table_name)
    try:
        progress.phase = f"writing sqlite {db_path.name}"
        written, tag_links, unique_tags = _write_sqlite_batch(conn, records, table_name, tag_lookup)
        progress.records_written = max(progress.records_written, written)
        progress.image_tag_links_written = max(progress.image_tag_links_written, tag_links)
        progress.unique_tags_written = max(progress.unique_tags_written, unique_tags)
        return written, tag_links, unique_tags
    finally:
        conn.close()


def _write_csv(records: list[dict[str, Any]], csv_path: Path, progress: ExtractProgress) -> int:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    progress.phase = f"writing csv {csv_path.name}"
    count = 0
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for record in records:
            if record.get("id") is None:
                continue

            row = dict(record)
            row["toolIds"] = _as_json_text(row.get("toolIds"))
            row["techniqueIds"] = _as_json_text(row.get("techniqueIds"))
            row["tagIds"] = _as_json_text(row.get("tagIds"))
            writer.writerow(row)
            count += 1
            progress.records_written = max(progress.records_written, count)
    return count


def _write_jsonl(records: list[dict[str, Any]], jsonl_path: Path, progress: ExtractProgress) -> int:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    progress.phase = f"writing jsonl {jsonl_path.name}"
    count = 0
    with jsonl_path.open("w", encoding="utf-8") as f:
        for record in records:
            if record.get("id") is None:
                continue
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
            progress.records_written = max(progress.records_written, count)
    return count


def _default_output_base(input_dir: Path) -> Path:
    return input_dir / "extracted_getinfinite_metadata"


def _load_checkpoint(checkpoint_path: Path) -> ExtractionCheckpoint | None:
    if not checkpoint_path.exists():
        return None
    try:
        with checkpoint_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None
        return ExtractionCheckpoint(
            schema_version=int(payload.get("schema_version") or 1),
            started_at=str(payload.get("started_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            input_dir=str(payload.get("input_dir") or ""),
            processed_files=list(payload.get("processed_files") or []),
            records_committed=int(payload.get("records_committed") or 0),
            image_tag_links_committed=int(payload.get("image_tag_links_committed") or 0),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _save_checkpoint(checkpoint: ExtractionCheckpoint, checkpoint_path: Path) -> None:
    payload = {
        "schema_version": checkpoint.schema_version,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "started_at": checkpoint.started_at,
        "updated_at": checkpoint.updated_at,
        "input_dir": checkpoint.input_dir,
        "processed_files": checkpoint.processed_files,
        "records_committed": checkpoint.records_committed,
        "image_tag_links_committed": checkpoint.image_tag_links_committed,
    }
    checkpoint_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _print_progress_header() -> None:
    print(
        f"{'File':>9}  {'Items':>7}  {'Committed':>11}  "
        f"{'T/File':>8}  {'F.Rate':>9}  {'Elapsed':>9}  {'S.Rate':>9}"
    )
    print("-" * 82)


def _print_progress_line(
    *,
    file_num: int,
    total_files: int,
    file_items: int,
    committed: int,
    file_elapsed: float,
    file_rate: float,
    session_elapsed: float,
    session_rate: float,
) -> None:
    file_label = f"{file_num}/{total_files}"
    print(
        f"{file_label:>9}  {file_items:>7}  {committed:>11}  "
        f"{file_elapsed:>7.2f}s  {file_rate:>7.1f}/s  "
        f"{session_elapsed:>7.1f}s  {session_rate:>7.1f}/s"
    )


def run(
    input_dir: Path,
    table_name: str,
    to_sqlite: bool,
    sqlite_path: Path,
    to_csv: bool,
    csv_path: Path,
    to_jsonl: bool,
    jsonl_path: Path,
    bootstrap_path: Path,
    authority_db_path: Path,
    progress: ExtractProgress,
    allow_resume: bool = True,
) -> ExtractStats:
    checkpoint_path = input_dir / CHECKPOINT_FILE_NAME

    # Load or create checkpoint.
    checkpoint = _load_checkpoint(checkpoint_path) if allow_resume else None
    if checkpoint is None:
        checkpoint = ExtractionCheckpoint(
            started_at=progress.started_at,
            input_dir=str(input_dir),
        )
    already_processed: set[str] = set(checkpoint.processed_files)
    initial_done_count = len(already_processed)

    if initial_done_count > 0:
        print(
            f"Resuming: {initial_done_count} file(s) already processed "
            f"({checkpoint.records_committed} records committed)."
        )

    all_page_files = _sorted_page_files(input_dir)
    if not all_page_files:
        raise FileNotFoundError(f"No page_*.json files found under '{input_dir}'.")

    files_to_process = [f for f in all_page_files if f.name not in already_processed]
    total_files = len(all_page_files)
    progress.page_files_total = total_files
    progress.page_files_scanned = initial_done_count

    if not files_to_process:
        print("All page files already processed. Nothing to do.")
        progress.phase = "completed"
        progress.records_written = checkpoint.records_committed
        progress.image_tag_links_written = checkpoint.image_tag_links_committed
        return ExtractStats(
            page_files_scanned=total_files,
            records_read=0,
            records_written=checkpoint.records_committed,
            image_tag_links_written=checkpoint.image_tag_links_committed,
            unique_tags_written=progress.unique_tags_written,
        )

    # Load tag lookups once before the per-file loop.
    progress.phase = "loading tags"
    authority_lookup = _load_tag_lookup_from_authority_db(authority_db_path)
    bootstrap_lookup = _load_tag_lookup_from_bootstrap(bootstrap_path)
    tag_lookup = _merge_tag_lookups(authority_lookup, bootstrap_lookup)

    # Open persistent outputs.
    sqlite_conn: sqlite3.Connection | None = None
    csv_file: Any = None
    csv_writer: Any = None
    jsonl_file: Any = None

    if to_sqlite:
        sqlite_conn = _open_sqlite(sqlite_path, table_name)
    if to_csv:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        open_mode = "a" if allow_resume and csv_path.exists() else "w"
        csv_file = csv_path.open(open_mode, encoding="utf-8", newline="")
        csv_writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        if open_mode == "w":
            csv_writer.writeheader()
    if to_jsonl:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        open_mode = "a" if allow_resume and jsonl_path.exists() else "w"
        jsonl_file = jsonl_path.open(open_mode, encoding="utf-8")

    # Print progress table header.
    print(f"\nInput:  {input_dir}")
    outputs = ", ".join(
        filter(
            None,
            [
                "SQLite" if to_sqlite else None,
                f"CSV ({csv_path.name})" if to_csv else None,
                f"JSONL ({jsonl_path.name})" if to_jsonl else None,
            ],
        )
    )
    print(f"Output: {outputs or '(none)'}")
    print(f"Files:  {len(files_to_process)} to process / {total_files} total\n")
    _print_progress_header()

    records_committed = checkpoint.records_committed
    tag_links_committed = checkpoint.image_tag_links_committed

    try:
        for loop_idx, page_file in enumerate(files_to_process):
            file_num = initial_done_count + loop_idx + 1
            file_start = time.monotonic()

            records = _read_page_file(page_file)
            file_items = len(records)
            progress.records_read += file_items

            if sqlite_conn is not None:
                written, tag_links, unique_tags = _write_sqlite_batch(
                    sqlite_conn, records, table_name, tag_lookup
                )
                records_committed += written
                tag_links_committed += tag_links
                progress.unique_tags_written = max(progress.unique_tags_written, unique_tags)
            else:
                written = sum(1 for r in records if r.get("id") is not None)
                records_committed += written

            if csv_writer is not None:
                for record in records:
                    if record.get("id") is None:
                        continue
                    row = dict(record)
                    row["toolIds"] = _as_json_text(row.get("toolIds"))
                    row["techniqueIds"] = _as_json_text(row.get("techniqueIds"))
                    row["tagIds"] = _as_json_text(row.get("tagIds"))
                    csv_writer.writerow(row)
                csv_file.flush()

            if jsonl_file is not None:
                for record in records:
                    if record.get("id") is None:
                        continue
                    jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                jsonl_file.flush()

            progress.session_records += written
            progress.records_written = records_committed
            progress.image_tag_links_written = tag_links_committed
            progress.page_files_scanned += 1

            # Update checkpoint.
            checkpoint.processed_files.append(page_file.name)
            checkpoint.records_committed = records_committed
            checkpoint.image_tag_links_committed = tag_links_committed
            checkpoint.updated_at = datetime.now(timezone.utc).isoformat()
            _save_checkpoint(checkpoint, checkpoint_path)

            file_elapsed = time.monotonic() - file_start
            session_elapsed = time.monotonic() - progress.started_monotonic
            file_rate = file_items / file_elapsed if file_elapsed > 0 else 0.0
            session_rate = (
                progress.session_records / session_elapsed if session_elapsed > 0 else 0.0
            )
            _print_progress_line(
                file_num=file_num,
                total_files=total_files,
                file_items=file_items,
                committed=records_committed,
                file_elapsed=file_elapsed,
                file_rate=file_rate,
                session_elapsed=session_elapsed,
                session_rate=session_rate,
            )

    finally:
        if sqlite_conn is not None:
            sqlite_conn.close()
        if csv_file is not None:
            csv_file.close()
        if jsonl_file is not None:
            jsonl_file.close()

    progress.phase = "completed"
    return ExtractStats(
        page_files_scanned=progress.page_files_scanned,
        records_read=progress.records_read,
        records_written=records_committed,
        image_tag_links_written=tag_links_committed,
        unique_tags_written=progress.unique_tags_written,
    )


def _print_summary(
    *,
    input_dir: Path,
    progress: ExtractProgress,
    sqlite_enabled: bool,
    sqlite_path: Path,
    table_name: str,
    bootstrap_path: Path,
    authority_db_path: Path,
    to_csv: bool,
    csv_path: Path,
    to_jsonl: bool,
    jsonl_path: Path,
    checkpoint_path: Path,
) -> None:
    elapsed = time.monotonic() - progress.started_monotonic
    session_rate = progress.session_records / elapsed if elapsed > 0 else 0.0

    print()
    print("Summary:")
    print(f"  Stop:         {progress.stop_reason or 'unknown'}")
    print(f"  Phase:        {progress.phase}")
    print(f"  Input dir:    {input_dir}")
    print(f"  Page files:   {progress.page_files_scanned}/{progress.page_files_total}")
    print(f"  New records:  {progress.session_records}")
    print(f"  Total:        {progress.records_written} committed")
    print(f"  Session rate: {session_rate:.1f} records/s")
    print(f"  Elapsed:      {elapsed:.1f}s")
    if checkpoint_path.exists():
        print(f"  Checkpoint:   {checkpoint_path}")
    if sqlite_enabled:
        print(f"  SQLite:       {sqlite_path}")
        print(f"  Table:        {table_name}")
        print(f"  Tag table:    {table_name}_tags ({progress.unique_tags_written} rows)")
        print(f"  Image-tags:   {table_name}_image_tags ({progress.image_tag_links_written} rows)")
        print(f"  Bootstrap:    {bootstrap_path if bootstrap_path.exists() else 'not found'}")
        print(f"  Authority DB: {authority_db_path if authority_db_path.exists() else 'not found'}")
    if to_csv:
        print(f"  CSV:          {csv_path}")
    if to_jsonl:
        print(f"  JSONL:        {jsonl_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract normalized image metadata from crawl_getinfinite page JSON files. "
            "Defaults to writing a separate SQLite dataset for scale and isolation."
        )
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        help="Directory containing page_*.json files produced by crawl_getinfinite.py",
    )
    parser.add_argument(
        "--table-name",
        default="civitai_getinfinite_images",
        help="SQLite table name when --sqlite is enabled",
    )
    parser.add_argument(
        "--bootstrap-tags",
        help=(
            "Optional civitai tag bootstrap JSON for tag nsfw/name enrichment "
            "(default: app/data/civitai_tags_bootstrap.json if present)"
        ),
    )
    parser.add_argument(
        "--authority-db-path",
        help=(
            "Optional path to existing app SQLite DB to enrich tags from authority_terms "
            "(default: app/image_db.sqlite if present)"
        ),
    )

    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="Write to SQLite output (enabled by default unless explicitly disabled)",
    )
    parser.add_argument(
        "--no-sqlite",
        action="store_true",
        help="Disable SQLite output",
    )
    parser.add_argument(
        "--sqlite-path",
        help="Path to SQLite DB file (default: <input-dir>/extracted_getinfinite_metadata.sqlite)",
    )

    parser.add_argument("--csv", action="store_true", help="Also write CSV output")
    parser.add_argument(
        "--csv-path",
        help="Path to CSV output (default: <input-dir>/extracted_getinfinite_metadata.csv)",
    )

    parser.add_argument("--jsonl", action="store_true", help="Also write JSONL output")
    parser.add_argument(
        "--jsonl-path",
        help="Path to JSONL output (default: <input-dir>/extracted_getinfinite_metadata.jsonl)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Ignore any existing checkpoint and process all page files from scratch. "
            "Overwrites existing output files."
        ),
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    output_base = _default_output_base(input_dir)
    app_root = Path(__file__).resolve().parent.parent

    sqlite_enabled = not args.no_sqlite
    if args.sqlite:
        sqlite_enabled = True

    sqlite_path = Path(args.sqlite_path).expanduser().resolve() if args.sqlite_path else Path(
        str(output_base) + ".sqlite"
    )
    csv_path = Path(args.csv_path).expanduser().resolve() if args.csv_path else Path(
        str(output_base) + ".csv"
    )
    jsonl_path = (
        Path(args.jsonl_path).expanduser().resolve()
        if args.jsonl_path
        else Path(str(output_base) + ".jsonl")
    )

    bootstrap_path = (
        Path(args.bootstrap_tags).expanduser().resolve()
        if args.bootstrap_tags
        else (app_root / "data" / "civitai_tags_bootstrap.json")
    )
    authority_db_path = (
        Path(args.authority_db_path).expanduser().resolve()
        if args.authority_db_path
        else (app_root / "image_db.sqlite")
    )

    if not any([sqlite_enabled, args.csv, args.jsonl]):
        raise ValueError("No output selected. Enable SQLite (default), or pass --csv and/or --jsonl.")

    progress = ExtractProgress(
        started_at=datetime.now(timezone.utc).isoformat(),
        started_monotonic=time.monotonic(),
    )

    try:
        run(
            input_dir=input_dir,
            table_name=args.table_name,
            to_sqlite=sqlite_enabled,
            sqlite_path=sqlite_path,
            to_csv=args.csv,
            csv_path=csv_path,
            to_jsonl=args.jsonl,
            jsonl_path=jsonl_path,
            bootstrap_path=bootstrap_path,
            authority_db_path=authority_db_path,
            progress=progress,
            allow_resume=not args.no_resume,
        )
        progress.stop_reason = "completed"
    except KeyboardInterrupt:
        progress.stop_reason = "keyboard_interrupt"
        print("\nInterrupted by user (Ctrl-C). Printing summary...")
    finally:
        _print_summary(
            input_dir=input_dir,
            progress=progress,
            sqlite_enabled=sqlite_enabled,
            sqlite_path=sqlite_path,
            table_name=args.table_name,
            bootstrap_path=bootstrap_path,
            authority_db_path=authority_db_path,
            to_csv=args.csv,
            csv_path=csv_path,
            to_jsonl=args.jsonl,
            jsonl_path=jsonl_path,
            checkpoint_path=input_dir / CHECKPOINT_FILE_NAME,
        )


if __name__ == "__main__":
    main()
