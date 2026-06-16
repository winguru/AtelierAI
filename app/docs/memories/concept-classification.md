# Concept Classification System

## Overview
A keyword-based classification system that automatically assigns orphan root concepts into 15 super-category parent concepts. Implemented in `app/scripts/classify_orphan_concepts.py`.

## Design Decisions

### 15 super-categories as organizational roots
Rather than manually curating thousands of orphan root concepts, 15 broad super-categories were created to provide initial top-level organization:
1. anatomy & body (969)
2. hair & head (558)
3. facial expression & emotion (89)
4. clothing & fashion (1,317)
5. actions & poses (549)
6. objects & props (890)
7. creatures & species (313)
8. setting & background (190)
9. franchise & fandom (27)
10. art style & technique (115)
11. seasons & events (27)
12. text & language (30)
13. lighting & effects (27)
14. composition & format (21)
15. relationships & demographics (55)

### Classification method
`SuperCategory` dataclass with `exact` (set), `prefixes`, `suffixes`, `contains`, `regex_patterns`. Categories checked **sequentially, first match wins** — order is critical.

### 74.6% auto-classification coverage
5,177 of 6,938 orphan concepts classified automatically. Remaining ~1,761 are: named characters, years, franchise references, abstract concepts, ultra-niche terms.

### Integration path for UI import workflow
The `classify_concept()` function and `CATEGORIES` list are designed to be extracted into `app/src/atelierai/` as a shared module. During tag import, call `classify_concept(tag_name)` to auto-assign `parent_concept_id` to the matching super-category. Unclassified concepts left as orphans for manual review.

## Key Files
- `app/scripts/classify_orphan_concepts.py` — batch script with `--apply`, `--create-parents`, `--verbose`, `--category`, `--limit`
- `app/backend/models.py` → `Concept` class (~line 252)
- `app/backend/services/taxonomy_service.py` → `slugify_concept_name()`, `ensure_unique_concept_slug()`

## Gotchas
- Classification order matters (sequential, first match wins). Anatomy first catches body parts before clothing tries to claim them.
- 11 original tree roots (IDs: 1174, 1332, 1970, 2210, 3160, 3577, 4767, 5341, 5943, 6151, 6985) excluded from classification.
- **15 super-category parents (IDs 7236–7250) also excluded** from `get_orphan_concepts()` to prevent self-classification bug.
- **Bug fixed (2026-06-11)**: Running `--create-parents --apply` classified the super-categories themselves. Fixed by: (1) adding super-category IDs to `EXISTING_TREE_ROOT_IDS`, (2) adding `slug.startswith("super-")` exclusion filter.
- `get_orphan_concepts()` has SAWarning about Subquery coercion — low priority.
- Super-category parents use `concept_type="super_category"`.
- "Root concepts must never be created automatically" constraint still holds for tag imports — this classification is an explicit batch operation.

## Potential integration points for auto-classification on import
- `app/backend/services/taxonomy_service.py` — concept creation logic
- `app/backend/civitai_enrichment.py` — tag import during CivitAI enrichment
- `app/backend/image_processor.py` — concept creation during image scanning
