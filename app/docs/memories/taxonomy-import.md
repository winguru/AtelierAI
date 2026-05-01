# Taxonomy Import & Tag Management

## Design Decisions

### Tag imports never create concepts
Tag imports create `authority_terms` only. When no matching `Concept` exists, the term is created with `concept_id=None`. Concepts must be created manually via the `/taxonomy/concepts` endpoint and associated with tags later.

This was changed because auto-creating concepts from tag imports produced root-level concepts with no children — violating the constraint that root concepts must never be created automatically.

### Root concepts must never be created automatically
A hard constraint. The only way to create a concept is via the explicit `POST /taxonomy/concepts` endpoint. No background process, import, or enrichment may create concepts.

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

## Gotchas
- `AuthorityTerm.concept_id` is `nullable=True` — terms can exist without concepts
- `_upsert_civitai_authority_terms()` already creates terms with `concept_id=None` (correct behavior)
- `main.py` contains duplicate copies of the import functions — changes must be applied to both
