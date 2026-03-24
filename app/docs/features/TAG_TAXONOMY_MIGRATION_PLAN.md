# Tag Taxonomy Migration Plan

This plan introduces a concept-first taxonomy while keeping current `tags` and `image_tags` behavior stable.

## Recommendation

Use a database-first model as the source of truth, with optional JSON snapshots for portability and backup.

Why this is the best fit for AtelierAI:
- You need canonicalization, aliases, external authority mapping, and provenance. Those are relational by nature.
- You need reversible grouping and merge/split history without losing evidence.
- You need confidence-scored observations from both humans and AI agents.
- Your app already has resilient DB patterns (SQLite WAL, lock retries, schema version checks).

JSON is still valuable, but best used as:
- Import seed files for authorities/aliases.
- Periodic export snapshots for review/versioning.
- Backup/portability format, not primary transactional store.

## What Was Added

New SQLAlchemy tables in `app/backend/models.py`:
- `tag_authorities`
- `concepts`
- `concept_aliases`
- `authority_terms`
- `concept_groups`
- `concept_group_memberships`
- `image_concept_observations`

Also seeded initial authorities in startup data (`app/backend/main.py`):
- `civitai`
- `danbooru`
- `user`
- `ai_agent`

## Non-Breaking Strategy

This rollout does not modify existing `tags`/`image_tags` tables.
Current features continue to work while new taxonomy features are implemented.

## Phase Plan

1. Phase 1: Schema bootstrap (done)
- Create new taxonomy tables via `Base.metadata.create_all(checkfirst=True)`.
- Keep old tag endpoints and relationships unchanged.

2. Phase 2: Canonical concept seeding
- Create concepts for existing local tags.
- Create normalized aliases for existing names.
- Seed authoritative source records where available.

Current utility script:
- `app/scripts/backfill_tag_taxonomy.py`

Example commands:
- Dry run: `cd app && python scripts/backfill_tag_taxonomy.py --dry-run`
- First 100 tags only: `cd app && python scripts/backfill_tag_taxonomy.py --dry-run --limit 100`
- Commit changes: `cd app && python scripts/backfill_tag_taxonomy.py`

3. Phase 3: Bridge reads/writes
- New write path: store observations in `image_concept_observations`.
- Compatibility bridge: optionally mirror canonical concept names back into `tags` for legacy queries.

4. Phase 4: External authority import
- Add importer for CivitAI and Danbooru tags into `authority_terms`.
- Keep unresolved terms with `concept_id = NULL` for curator review.

5. Phase 5: Curation tools
- Alias management UI/API (merge/split, canonical rename, group assignment).
- Review queue for unresolved terms and low-confidence AI observations.

6. Phase 6: Full migration
- Shift frontend/API filtering to concept IDs and dimensions.
- Decommission legacy tag writes once all consumers are moved.

## JSON Sync Pattern (Optional)

Use JSON as append-only interchange and snapshot format.

Recommended contract:
- `taxonomy/authorities/*.json`: authority term imports.
- `taxonomy/concepts/*.json`: curated canonical concepts and aliases.
- `taxonomy/exports/taxonomy_snapshot_YYYYMMDD.json`: full export snapshot.

Sync direction:
- Primary: DB -> JSON snapshot on demand.
- Secondary: JSON -> DB import for bootstrap or bulk updates.

Conflict policy:
- DB wins by default.
- JSON import runs in dry-run mode first and emits a diff report.

## Suggested Management APIs

- `POST /taxonomy/concepts`
- `PATCH /taxonomy/concepts/{concept_id}`
- `POST /taxonomy/concepts/{concept_id}/aliases`
- `POST /taxonomy/authority_terms/import`
- `POST /taxonomy/observations`
- `GET /taxonomy/review/unresolved_terms`
- `POST /taxonomy/review/resolve_term`
- `POST /taxonomy/export`
- `POST /taxonomy/import?dry_run=true`

Implemented management APIs (current):
- `GET /taxonomy/review/summary`
- `GET /taxonomy/review/potential_duplicates`
- `GET /taxonomy/concepts`
- `POST /taxonomy/concepts`
- `POST /taxonomy/concepts/{concept_id}/aliases`
- `POST /taxonomy/review/merge_concepts` (supports `dry_run`)
- `POST /taxonomy/bootstrap/import` (JSON/CSV raw text; supports `dry_run`)
- `GET /taxonomy/tree`
- `POST /taxonomy/concepts/{concept_id}/parent` (supports `dry_run`)

## Migration/Backfill Checklist

1. Verify new tables exist and authorities are seeded.
2. Backfill existing `tags` into `concepts` + `concept_aliases`.
3. Add bridge mapping table or process for legacy compatibility.
4. Add test fixtures for synonym collapsing:
- `bikini` canonical
- `two-piece`, `separates` aliases to `bikini`
5. Add confidence and dimension tests for AI observations.
6. Add unresolved-term review tests.

## Resilience Notes

SQLite is fine for this stage if WAL and retry logic stay enabled.
If concurrent annotation jobs become heavy, migrate to Postgres with the same schema.

## Schema Versioning Note

No schema version bump was made in this step to avoid forcing a reset in environments where `ALLOW_SCHEMA_RESET=false`.
Once migration scripts/tests are ready, bump `CURRENT_SCHEMA_VERSION` and add a structured migration script.
