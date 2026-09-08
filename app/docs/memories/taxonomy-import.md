# Taxonomy Import & Tag Management

## Design Decisions

### Tag imports never create concepts
Tag imports create `authority_terms` only. When no matching `Concept` exists, the term is created with `concept_id=None`. Concepts must be created manually via the `/taxonomy/concepts` endpoint and associated with tags later.

This was changed because auto-creating concepts from tag imports produced root-level concepts with no children — violating the constraint that root concepts must never be created automatically.

### Root concepts must never be created automatically
A hard constraint. The only way to create a concept is via the explicit `POST /taxonomy/concepts` endpoint. No background process, import, or enrichment may create concepts.

### Concept transfer import/export endpoints
Concept transfer now has dedicated routes:

- `GET /taxonomy/concepts/export`
- `POST /taxonomy/concepts/import`
- `POST /taxonomy/concepts/import_file`

Import is a graft merge, keyed by slug/name/alias matching rather than IDs. Local taxonomy remains authoritative on collisions.

### Root policy during concept transfer import
Concept transfer import supports two policies:

- `strict` (default): imported roots without overlap are skipped.
- `permissive`: imported roots without overlap may be created.

This preserves the default no-auto-root behavior while allowing explicit migration workflows.

### Tag collision behavior for concept transfer
Authority-term collisions do not overwrite existing concept associations.
If an imported authority term is already linked to a different local concept, the imported link is skipped and recorded as a conflict.

### Tag storage: two models
1. **Flat JSON** on `images.user_tags` column
2. **Relational taxonomy**: `tag_authorities` → `authority_terms` → `concepts` → `image_concept_observations`

Four authorities: `civitai`, `danbooru`, `prompt`, `user`.

### Tag weights are planned
Storage uses the existing `confidence` float on `ImageConceptObservation`. Sources: (1) CivitAI tag weights, (2) prompt `(tag:weight)` syntax, (3) user-assigned weights. Prompt weights are currently discarded during parsing.

## Key Files
- `app/backend/services/taxonomy_service.py` — `get_or_create_concept()`, `ensure_alias_for_concept()`
- `app/backend/routers/taxonomy.py` — `_execute_taxonomy_bootstrap_import()`, `_upsert_civitai_authority_terms()`
- `app/backend/schemas.py` — `TaxonomyBootstrapImportRequest`
- `app/backend/models.py` — `Concept`, `ConceptAlias`, `AuthorityTerm`, `ImageConceptObservation`
- `app/frontend/js/tag-maint.js` — tag maintenance UI import handler

## Snapshot Portability
- Full taxonomy snapshot export: `GET /taxonomy/snapshot/export`
- Full taxonomy snapshot import: `POST /taxonomy/snapshot/import`
- Snapshot includes authorities, concepts, aliases, concept groups, memberships, authority terms, and optional `user_bindings` keyed by `file_hash`.
- Import conflict policy: imported values win for direct field conflicts; metadata maps are merged with imported keys overriding existing keys.
- Relationship safety: parent cycles and missing parent references keep existing parent links and report conflicts.
- Post-import actions can rebuild source authority terms and observations, backfill missing CivitAI tag IDs from sidecars, fetch missing CivitAI metadata, and restore user bindings by hash.

## Gotchas
- `AuthorityTerm.concept_id` is `nullable=True` — terms can exist without concepts
- `_upsert_civitai_authority_terms()` already creates terms with `concept_id=None` (correct behavior)
- `main.py` contains duplicate copies of the import functions — changes must be applied to both
- Snapshot import can be expensive on large catalogs because post-import rebuild options scan all images/sidecars.
- `tag-maint.js` is a single IIFE; an unmatched event-handler delimiter prevents parsing and leaves the table at `Loading...` with all controls inert.
- **Never-null Ext ID guard**: all three authority-term upsert paths (`_upsert_civitai_authority_terms` in `taxonomy.py` ~line 1036 and `main.py` ~line 8698, `_upsert_authority_terms` in `scan_missing_service.py` ~line 121) must never overwrite an existing `external_tag_id` with `None`. Id-less tier-1 sidecar payloads (only ~193/5125 sidecars carry tag ids) repeatedly nulled IDs resolved from richer sources — root cause of recurring "missing Ext ID" regressions. Guard shape: `if external_tag_id is not None and getattr(term, "external_tag_id", None) != external_tag_id:`.

## CivitAI Ext ID Backfill

### Why sidecars can't fix it
Only 193/5125 sidecars carry `civitai.tags[].id`; the sidecar-based backfill (`POST /taxonomy/tag-maint/civitai/backfill-tag-ids`) resolves ~0. CivitAI has no live name→ID lookup (REST v1 `/api/v1/tags?query=` returns names/links only, no ids; `tag.getById` needs a known id; Meilisearch maps names→images only).

### Cache resolver (the efficient path)
`POST /taxonomy/tag-maint/civitai/backfill-tag-ids-from-cache?dry_run=` builds a normalized name→id map from all cached `tag.getVotableTags` rows in `civitai_api_cache` (12,288 rows → 4,790 unique names, 0 conflicts) and fills NULL ext-ids on CivitAI authority_terms. Zero network calls. UI: "Resolve Ext IDs (cache)" button in tag-maint step 1.

### Legitimately ID-less pseudo-tags (do NOT chase)
~13 CivitAI terms have no tag IDs to fetch — moderation/rating pseudo-tags from CivitAI's votable payload: `book`, `child - 13`, `child - 15`, `explicit`, `exposed buttocks or anus`, `exposed female genitalia`, `holding cat`, `pg`, `pg-13`, `publication`, `r`, `x`, `xxx`. Enrichment (`tag.getById`) can never resolve these; safe to ignore.


## Observation ↔ Concept Linkage

### The concept_id gap
`ImageConceptObservation` rows get `authority_term_id` during import but `concept_id` is not automatically populated from the authority_term's `concept_id`. This causes the concept search pipeline's `resolve_candidate_images` stage to find zero candidates for any concept.

**Fix**: The `POST /api/taxonomy/concept-search/rescan` endpoint:
1. Deletes null-concept observations that would violate the UNIQUE constraint on `(image_id, concept_id, authority_id)` 
2. Deletes same-batch conflicts (two null obs for same image+authority that would get the same concept_id)
3. Backfills `concept_id` from `authority_terms.concept_id`

The "Rescan Associations" button in the Concept Search Lab triggers this endpoint and refreshes the coverage index.

### Key files
- `app/backend/services/concept_search_service.py` — search pipeline (decompose → resolve → CLIP score)
- `app/backend/routers/taxonomy.py` — rescan endpoint + all search endpoints
- `app/frontend/js/concept-search-lab.js` — rescan button handler
- `app/frontend/concept-search-lab.html` — rescan button markup
