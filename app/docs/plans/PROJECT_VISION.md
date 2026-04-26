# AtelierAI — Project Vision & Roadmap

**Date:** 2026-04-25  
**Status:** Active guiding document

---

## Mission

AtelierAI is a platform for measuring **temporal, spatial, and semantic coherence** in generative image models (Stable Diffusion, LoRAs, and beyond). The goal is to move from "guess and retry" to **measurable, reproducible** generation workflows.

Instead of generating numerous failed renderings hoping to catch lightning, AtelierAI helps answer:

- Does the model understand what I'm asking? (**semantic coherence**)
- Does it place elements correctly in composition? (**spatial coherence**)
- Does it produce consistent results across seeds and runs? (**temporal coherence**)

---

## Architecture Overview

```
Tag & metadata foundation (current work)
         │
         ▼
┌─────────────────────┐     ┌──────────────────┐
│  Curated image DB    │────►│  ComfyUI / tools  │
│  with known tags,    │     │  for controlled   │
│  weights, params     │     │  re-generation    │
└─────────────────────┘     └────────┬──────────┘
                                     │
                            ┌────────▼──────────┐
                            │  Coherence analysis │
                            │  - Vary seed        │
                            │  - Vary weights     │
                            │  - Vary ordering    │
                            │  - Compare results  │
                            └────────────────────┘
```

---

## Tag System Architecture

### Current State: Two Storage Models

| Model | Storage | Tag Types |
|-------|---------|-----------|
| **Flat JSON** | `images.user_tags` column | User-assigned tags |
| **Relational taxonomy** | `concepts`, `authority_terms`, `image_concept_observations` | CivitAI, Danbooru, Prompt |

### Four Tag Authorities

| Authority | Origin | Example |
|-----------|--------|---------|
| **user** | Manual / sidecar files | `["bay (nikke)", "draft"]` |
| **civitai** | CivitAI API `tags[]` | `{"name": "blue eyes", "weight": 0.9}` |
| **danbooru** | Danbooru tag DB | `{"name": "1girl", "danbooru_tag_id": 42}` |
| **prompt** | Parsed from A1111/ComfyUI prompts | `"masterpiece, best quality, 1girl"` |

### Tag Design Principles

1. **User tags are not fundamentally different** — they supplement existing tags where they are missing, incorrect, or ambiguous. They should normalize into the relational taxonomy as a `user` authority.

2. **User tags follow Danbooru format** but are not guaranteed to be identical — they classify visual representation only, not tool/quality metadata.

3. **Tag weights** are a planned feature:
   - CivitAI already provides tag weights
   - Danbooru tags alone don't convey strength, only prompt usage does
   - User-assigned weights should be supported
   - The `confidence` column on `ImageConceptObservation` is the natural storage location

4. **Prompt weights** (the `(tag:1.4)` syntax) are currently discarded during parsing. Capturing them is a future enhancement.

---

## Planned Features

### Near-Term

- [ ] **Normalize user_tags into relational taxonomy** — Migrate flat `user_tags` JSON entries into `Concept` + `AuthorityTerm("user")` + `ImageConceptObservation` rows. This unifies query paths and enables deduplication.

- [ ] **Tag weight support** — Add weight/confidence to observations. CivitAI weights are the first source; user-assigned weights follow the same schema.

- [ ] **Capture prompt weights** — Preserve `(tag:weight)` syntax during prompt parsing in `prompt_phrases.py` instead of discarding them.

### Medium-Term

- [ ] **ComfyUI as test harness** — Not just "send an image" but reproduce with parameter variations and measure differences. The ComfyUI Lab is the starting point.

- [ ] **Coherence metrics** — Quantitative measures for:
  - Seed variation stability (same prompt, different seed → how different?)
  - Weight sensitivity (vary tag weights → measure output change)
  - Phrase ordering sensitivity (reorder prompt → measure output change)

- [ ] **Visual parity verification** — Confirm that re-generation with known parameters produces visually similar results to the original.

### Long-Term

- [ ] **Multi-tool integration** — ComfyUI is first; support for other generation tools.
- [ ] **Model/LoRA coherence reports** — "How well does model X understand concept Y?"
- [ ] **Automated coherence testing** — Batch variations + automated comparison.

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite with `json_each()` for user_tags | Pragmatic short-term; relational migration planned |
| `ImageConceptObservation.confidence` for weights | Already exists as a float column |
| `TagAuthority` enum for source tracking | Clean separation without schema changes |
| SSE for long-running operations (sync-lab) | Real-time progress without polling |
| Cursor-based pagination | Stable pagination for large result sets |

---

## Related Documentation

- `app/docs/plans/PARITY_UX_REFACTOR_PLAN.md` — Parity workbench UX improvements
- `app/docs/features/` — Current feature documentation
- `AGENTS.md` — Project structure and coding conventions
- `.github/instructions/code-review.instructions.md` — Code review standards
