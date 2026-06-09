# Taxonomy Concept Transfer Implementation Plan

This plan translates the API contract into implementation steps for backend routes, services, and tests.

## Scope

Implement:

- `GET /taxonomy/concepts/export`
- `POST /taxonomy/concepts/import`
- `POST /taxonomy/concepts/import_file`

With:

- dry-run support
- root policy (`strict`, `permissive`)
- deterministic graft merge behavior
- conflict reporting and summary counters

## Target Files

Primary:

- `app/backend/routers/taxonomy.py`
- `app/backend/schemas.py`
- `app/backend/services/taxonomy_service.py` (optional extraction of merge helpers)

Tests:

- `app/tests/test_taxonomy_concept_transfer.py`

Docs alignment:

- `app/docs/api/TAXONOMY_CONCEPT_TRANSFER_API.md`

## Data/Domain Constraints To Preserve

- Local tree structure is authoritative on collisions.
- Tag collisions do not overwrite local association.
- Single association per authority-term remains enforced.
- Default import mode should not auto-create roots (`strict`) unless explicitly set to `permissive`.

## Proposed Implementation Steps

1. Add request/response schemas.
- Define Pydantic models for transfer node/document, import request, summary, conflicts, action logs.
- Add validation for required node fields and recursive children shape.

2. Add export route.
- Build full concept tree from existing concepts.
- Attach aliases and authority terms grouped by authority name.
- Return canonical transfer document with `version` and `exported_at`.

3. Add import route (JSON).
- Parse and validate transfer document.
- Resolve imported roots by matching rules (slug -> normalized name -> alias).
- Apply `strict`/`permissive` root policy.
- Recursively graft child branches.
- Add aliases and attempt tag associations with conflict skip behavior.
- Collect summary and conflict/action entries.
- Wrap in transaction; rollback when `dry_run=true`.

4. Add import_file route.
- Read uploaded file and decode JSON.
- Delegate to same import executor as JSON route.

5. Add helper functions.
- Concept matcher.
- Child matcher under local parent.
- Tag association helper with collision checks.
- Summary/conflict recorder.

6. Add tests.
- export includes hierarchy and grouped tags.
- strict mode skips non-overlap roots.
- permissive mode creates non-overlap roots.
- branch collision grafts into existing local concept.
- nested branch collision pruning + graft behavior.
- tag collision is skipped and reported.
- dry-run returns counts without persistence.

## Suggested Test Matrix

- `test_export_concept_transfer_document_shape`
- `test_import_strict_skips_new_root`
- `test_import_permissive_creates_new_root`
- `test_import_graft_on_root_collision`
- `test_import_nested_collision_prunes_and_grafts`
- `test_import_skips_tag_conflicts`
- `test_import_dry_run_rolls_back`

## Rollout Notes

- Keep initial endpoint hidden from UI toggles until tests pass.
- Start with small taxonomy fixtures for deterministic branch collisions.
- Document conflict report semantics for UI consumers.
