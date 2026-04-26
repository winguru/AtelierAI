#!/usr/bin/env python3
"""Repair CivitAI authority_terms that are missing external_tag_id.

Two classes of broken terms exist in the DB:

  Class A — ID already known via alternate_external_tag_ids:
    The correct CivitAI tag ID was discovered via a name-collision path and
    parked in metadata_json["alternate_external_tag_ids"] but never promoted
    to external_tag_id.  Fix: promote alt[0] → external_tag_id.

  Class B — ID completely unknown:
    The term was imported from a raw tag-name string (no numeric ID was
    available at import time).  Fix: find a linked gallery image that has a
    CivitAI image ID, call tag.getVotableTags for that image, and match the
    response entry by normalized name to discover the tag ID.

In both cases the script optionally enriches the term with metadata fetched
via tag.getById (type, nsfwLevel, automated, concrete).

Usage:
    # Discover and preview what would be fixed (dry-run)
    python scripts/repair_civitai_external_ids.py --dry-run

    # Fix everything and enrich metadata via API
    python scripts/repair_civitai_external_ids.py --enrich

    # Fix only Class-A promotions without any API calls
    python scripts/repair_civitai_external_ids.py --skip-discovery --skip-enrich

    # Full run with explicit output paths
    python scripts/repair_civitai_external_ids.py \\
        --enrich \\
        --report-json data/repair_civitai_external_ids_report.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from path_setup import PROJECT_ROOT  # noqa: F401 - adds src/ to sys.path
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.http_client import CivitaiHttpClient, CivitaiRequestError


# ---------------------------------------------------------------------------
# Utilities
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


def _coerce_meta(value: Any) -> dict[str, Any]:
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


def _extract_trpc_result(raw: Any) -> Any:
    """Extract the tRPC result payload from a raw envelope dict."""
    if not isinstance(raw, dict):
        return None
    wrapper = raw.get("result")
    if not isinstance(wrapper, dict):
        return None
    data = wrapper.get("data")
    if isinstance(data, dict) and "json" in data:
        return data["json"]
    return data


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@dataclass
class _TermRecord:
    term_id: int
    external_tag_id: int | None
    external_name: str
    normalized_name: str
    concept_id: int | None
    metadata: dict[str, Any]
    # Populated during discovery:
    discovered_tag_id: int | None = None
    discovery_source: str = ""  # "alt_promotion" | "votable_match" | "unresolved"
    api_metadata: dict[str, Any] = field(default_factory=dict)
    first_civitai_image_id: int | None = None


def _load_broken_terms(conn: sqlite3.Connection, authority_name: str) -> list[_TermRecord]:
    """Load all civitai authority_terms where external_tag_id IS NULL/empty."""
    rows = conn.execute(
        """
        SELECT at.id, at.external_tag_id, at.external_name,
               at.normalized_external_name, at.concept_id, at.metadata_json
        FROM authority_terms at
        JOIN tag_authorities ta ON ta.id = at.authority_id
        WHERE lower(ta.name) = lower(?)
          AND (at.external_tag_id IS NULL OR CAST(at.external_tag_id AS TEXT) = '')
        ORDER BY at.id ASC
        """,
        (authority_name,),
    ).fetchall()
    out: list[_TermRecord] = []
    for term_id, ext_id, name, norm_name, concept_id, meta_raw in rows:
        out.append(
            _TermRecord(
                term_id=int(term_id),
                external_tag_id=_parse_int(ext_id),
                external_name=str(name or ""),
                normalized_name=str(norm_name or _normalize_name(name)),
                concept_id=_parse_int(concept_id),
                metadata=_coerce_meta(meta_raw),
            )
        )
    return out


def _load_existing_external_ids(conn: sqlite3.Connection, authority_name: str) -> set[int]:
    """Return all non-null external_tag_ids already committed for this authority."""
    rows = conn.execute(
        """
        SELECT at.external_tag_id
        FROM authority_terms at
        JOIN tag_authorities ta ON ta.id = at.authority_id
        WHERE lower(ta.name) = lower(?)
          AND at.external_tag_id IS NOT NULL
        """,
        (authority_name,),
    ).fetchall()
    return {int(r[0]) for r in rows if _parse_int(r[0]) is not None}


def _find_linked_civitai_image_id(
    conn: sqlite3.Connection, term_id: int
) -> int | None:
    """Return the CivitAI image ID for the first gallery image linked to this term."""
    row = conn.execute(
        """
        SELECT i.civitai_image_id
        FROM image_concept_observations ico
        JOIN images i ON i.id = ico.image_id
        WHERE ico.authority_term_id = ?
          AND i.civitai_image_id IS NOT NULL
        ORDER BY i.id ASC
        LIMIT 1
        """,
        (term_id,),
    ).fetchone()
    return _parse_int(row[0]) if row else None


# ---------------------------------------------------------------------------
# Phase A: alt_id promotion (no API calls needed)
# ---------------------------------------------------------------------------


def _resolve_class_a(
    terms: list[_TermRecord],
    existing_ids: set[int],
) -> None:
    """Promote alternate_external_tag_ids[0] → discovered_tag_id for Class-A terms."""
    for term in terms:
        alts = term.metadata.get("alternate_external_tag_ids")
        if not isinstance(alts, list) or not alts:
            continue  # Class B
        candidate = _parse_int(alts[0])
        if candidate is None:
            term.discovery_source = "unresolved"
            continue
        if candidate in existing_ids:
            # The target ID is already owned by a different term — can't promote.
            term.discovery_source = "unresolved"
            term.api_metadata["promotion_conflict"] = candidate
            continue
        term.discovered_tag_id = candidate
        term.discovery_source = "alt_promotion"


# ---------------------------------------------------------------------------
# Phase B: getVotableTags discovery (Class-B and unenriched Class-A)
# ---------------------------------------------------------------------------


def _resolve_class_b(
    conn: sqlite3.Connection,
    terms: list[_TermRecord],
    existing_ids: set[int],
    sleep_ms: int,
) -> None:
    """Discover tag IDs for Class-B terms via tag.getVotableTags."""
    api = CivitaiAPI.get_instance()
    sleep_seconds = max(0, sleep_ms) / 1000.0
    pending = [t for t in terms if t.discovery_source == "" and t.discovered_tag_id is None]
    if not pending:
        return

    print(f"  Class-B discovery: {len(pending)} terms via getVotableTags")
    for idx, term in enumerate(pending, start=1):
        civ_image_id = _find_linked_civitai_image_id(conn, term.term_id)
        term.first_civitai_image_id = civ_image_id
        if civ_image_id is None:
            term.discovery_source = "unresolved"
            print(f"    [{idx}/{len(pending)}] {term.external_name!r}: no linked gallery image — skipped")
            continue

        try:
            raw = api._make_raw_request(
                endpoint="tag.getVotableTags",
                payload_data={"id": int(civ_image_id), "type": "image", "authed": True},
            )
            response = _extract_trpc_result(raw)
            if not isinstance(response, list):
                term.discovery_source = "unresolved"
                print(
                    f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                    f"getVotableTags returned non-list ({type(response).__name__})"
                )
                continue

            # Match by normalized name within the response entries.
            target = _normalize_name(term.external_name)
            matched: dict[str, Any] | None = None
            for entry in response:
                if not isinstance(entry, dict):
                    continue
                if _normalize_name(entry.get("name")) == target:
                    matched = entry
                    break

            if matched is None:
                term.discovery_source = "unresolved"
                all_names = [e.get("name") for e in response if isinstance(e, dict)]
                print(
                    f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                    f"not found in {len(all_names)}-entry response for image {civ_image_id}"
                )
                continue

            tag_id = _parse_int(matched.get("id"))
            if tag_id is None:
                term.discovery_source = "unresolved"
                print(
                    f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                    f"matched entry has no id field: {matched}"
                )
                continue

            if tag_id in existing_ids:
                term.discovery_source = "unresolved"
                term.api_metadata["discovery_conflict"] = tag_id
                print(
                    f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                    f"discovered id={tag_id} conflicts with existing term — skipped"
                )
                continue

            term.discovered_tag_id = tag_id
            term.discovery_source = "votable_match"
            term.api_metadata = {
                "type": matched.get("type"),
                "nsfw_level": matched.get("nsfwLevel"),
                "automated": matched.get("automated"),
                "concrete": matched.get("concrete"),
            }
            print(
                f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                f"id={tag_id} type={matched.get('type')!r}"
            )

        except CivitaiRequestError as exc:
            status = exc.status_code
            print(
                f"    [{idx}/{len(pending)}] {term.external_name!r}: "
                f"API error HTTP {status}: {exc}"
            )
            if status == 403:
                backoff = CivitaiHttpClient.activate_global_backoff(60.0, reason="HTTP 403")
                print(f"    403 backoff: {backoff:.0f}s enforced before next request")
            term.discovery_source = "unresolved"

        if sleep_seconds > 0 and idx < len(pending):
            time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# Phase C: enrich via tag.getById (optional, for all resolved terms)
# ---------------------------------------------------------------------------


def _enrich_resolved_terms(
    terms: list[_TermRecord],
    sleep_ms: int,
) -> None:
    """Call tag.getById for each resolved term to fill type/nsfwLevel/automated/concrete."""
    api = CivitaiAPI.get_instance()
    sleep_seconds = max(0, sleep_ms) / 1000.0
    resolved = [t for t in terms if t.discovered_tag_id is not None]
    # Skip enrichment for terms where votable already gave us metadata.
    needs_enrich = [t for t in resolved if not t.api_metadata.get("type")]
    if not needs_enrich:
        print("  Enrichment: nothing to enrich (votable metadata already covers all resolved terms)")
        return

    print(f"  Enrichment: calling tag.getById for {len(needs_enrich)} terms")
    for idx, term in enumerate(needs_enrich, start=1):
        assert term.discovered_tag_id is not None
        try:
            raw = api._make_raw_request(
                endpoint="tag.getById",
                payload_data={"id": int(term.discovered_tag_id), "authed": True},
            )
            result = _extract_trpc_result(raw)
            if isinstance(result, dict):
                term.api_metadata["type"] = result.get("type") or term.api_metadata.get("type")
                # tag.getById does not return nsfwLevel/automated/concrete — leave as-is.
        except CivitaiRequestError as exc:
            status = exc.status_code
            print(f"    [{idx}] tag {term.discovered_tag_id}: API error HTTP {status}: {exc}")
            if status == 403:
                backoff = CivitaiHttpClient.activate_global_backoff(60.0, reason="HTTP 403")
                print(f"    403 backoff: {backoff:.0f}s enforced")

        if sleep_seconds > 0 and idx < len(needs_enrich):
            time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# Phase D: write DB updates
# ---------------------------------------------------------------------------


def _apply_updates(
    conn: sqlite3.Connection,
    terms: list[_TermRecord],
    commit_every: int,
    dry_run: bool,
) -> dict[str, int]:
    stats = {
        "promoted": 0,       # Class A: alt → external_tag_id
        "discovered": 0,     # Class B: getVotableTags → external_tag_id
        "unresolved": 0,
        "skipped_conflict": 0,
    }
    now_iso = _utcnow_iso()
    batch = 0

    for term in terms:
        tag_id = term.discovered_tag_id
        if tag_id is None:
            stats["unresolved"] += 1
            continue

        # Merge API metadata into existing metadata_json.
        meta = term.metadata.copy()
        if term.api_metadata:
            for k, v in term.api_metadata.items():
                if v is not None:
                    meta[k] = v
        # Clean up: once the ID is promoted, remove it from alternate list to avoid duplication.
        alts: list[str] = meta.get("alternate_external_tag_ids") or []
        alts = [a for a in alts if _parse_int(a) != tag_id]
        if alts:
            meta["alternate_external_tag_ids"] = alts
        else:
            meta.pop("alternate_external_tag_ids", None)
        meta["external_id_source"] = term.discovery_source

        conn.execute(
            """
            UPDATE authority_terms
            SET external_tag_id = ?,
                metadata_json = ?,
                updated_at = ?,
                last_seen_at = ?
            WHERE id = ?
            """,
            (
                tag_id,
                json.dumps(meta, ensure_ascii=False),
                now_iso,
                now_iso,
                term.term_id,
            ),
        )

        if term.discovery_source == "alt_promotion":
            stats["promoted"] += 1
        else:
            stats["discovered"] += 1

        batch += 1
        if commit_every > 0 and batch >= commit_every:
            if dry_run:
                conn.rollback()
            else:
                conn.commit()
            batch = 0

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Repair CivitAI authority_terms that are missing external_tag_id by promoting "
            "known alternate IDs and discovering unknown IDs via tag.getVotableTags."
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
        "--skip-discovery",
        action="store_true",
        help=(
            "Only process Class-A terms (alt_id promotion). "
            "Skip Class-B getVotableTags discovery entirely."
        ),
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "Call tag.getById for resolved Class-A terms that lack type/nsfwLevel metadata. "
            "(Class-B terms are enriched automatically from the getVotableTags response.)"
        ),
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=100,
        help="Milliseconds to sleep between API requests (default: 100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover IDs and prepare updates but roll back all DB writes.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=50,
        help="Commit every N updates (default: 50)",
    )
    parser.add_argument(
        "--report-json",
        help=(
            "Optional path to write a full JSON report of what was (or would be) changed "
            "(default: <main-db-dir>/repair_civitai_external_ids_report.json)"
        ),
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
    if args.commit_every < 1:
        raise ValueError("--commit-every must be >= 1")

    print(f"Main DB:          {main_db}")
    print(f"Authority:        {args.authority_name}")
    print(f"Mode:             {'dry-run (rollback)' if args.dry_run else 'commit'}")

    conn = sqlite3.connect(main_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        broken = _load_broken_terms(conn, args.authority_name)
        existing_ids = _load_existing_external_ids(conn, args.authority_name)
        print(f"Terms to repair:  {len(broken)}  (existing external IDs: {len(existing_ids)})")

        class_a = [t for t in broken if isinstance(t.metadata.get("alternate_external_tag_ids"), list)
                   and t.metadata["alternate_external_tag_ids"]]
        class_b = [t for t in broken if t not in class_a]
        print(f"  Class A (alt promotion):   {len(class_a)}")
        print(f"  Class B (API discovery):   {len(class_b)}")

        # Phase A: promote alt IDs (free — no API calls).
        print("\nPhase A: alt_id promotion")
        _resolve_class_a(broken, existing_ids)
        promoted = sum(1 for t in broken if t.discovery_source == "alt_promotion")
        conflicts = sum(1 for t in broken if t.api_metadata.get("promotion_conflict"))
        print(f"  Promotable:    {promoted}")
        if conflicts:
            print(f"  Conflicts:     {conflicts} (existing term already owns that ID — skipped)")

        # Phase B: discover IDs for Class-B terms via getVotableTags.
        if not args.skip_discovery:
            print("\nPhase B: Class-B ID discovery via tag.getVotableTags")
            _resolve_class_b(conn, broken, existing_ids, args.sleep_ms)
        else:
            for term in broken:
                if term.discovery_source == "":
                    term.discovery_source = "unresolved"

        # Phase C: enrich Class-A terms via tag.getById (opt-in).
        if args.enrich:
            print("\nPhase C: metadata enrichment via tag.getById")
            _enrich_resolved_terms(broken, args.sleep_ms)

        # Summarise before writing.
        n_resolved = sum(1 for t in broken if t.discovered_tag_id is not None)
        n_unresolved = sum(1 for t in broken if t.discovered_tag_id is None)
        print(f"\nResolved:         {n_resolved}")
        print(f"Unresolved:       {n_unresolved}")

        if n_unresolved > 0:
            unresolved_names = [t.external_name for t in broken if t.discovered_tag_id is None]
            preview = ", ".join(repr(n) for n in unresolved_names[:10])
            suffix = " ..." if len(unresolved_names) > 10 else ""
            print(f"Unresolved names: {preview}{suffix}")

        # Phase D: apply DB updates.
        print(f"\nPhase D: writing updates ({'dry-run' if args.dry_run else 'commit'})")
        stats = _apply_updates(conn, broken, args.commit_every, args.dry_run)
        print(f"  Class-A promoted:  {stats['promoted']}")
        print(f"  Class-B discovered:{stats['discovered']}")
        print(f"  Unresolved:        {stats['unresolved']}")

        # Report JSON.
        report_path = (
            Path(args.report_json).expanduser().resolve()
            if args.report_json
            else main_db.parent / "repair_civitai_external_ids_report.json"
        )
        report = {
            "main_db": str(main_db),
            "authority_name": args.authority_name,
            "dry_run": args.dry_run,
            "stats": stats,
            "terms": [
                {
                    "term_id": t.term_id,
                    "external_name": t.external_name,
                    "discovered_tag_id": t.discovered_tag_id,
                    "discovery_source": t.discovery_source,
                    "first_civitai_image_id": t.first_civitai_image_id,
                    "api_metadata": t.api_metadata,
                    "promotion_conflict": t.api_metadata.get("promotion_conflict"),
                    "discovery_conflict": t.api_metadata.get("discovery_conflict"),
                }
                for t in broken
            ],
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nReport JSON:      {report_path}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
