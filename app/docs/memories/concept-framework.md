# Concept Framework Memory

- Concept review APIs include profile CRUD, authority weights, review evidence, bulk observation updates, and weighting summary.
- Scoring endpoint: `GET /api/concepts/{concept_id}/scored-images` computes per-image scores from observations and review evidence.
- Scoring components follow plan defaults: identity (0.55), attributes (0.25), context (0.15), style (0.05), anomaly penalty multiplier.
- Attribute evidence uses authority-weighted aggregation and weighted geometric mean across attribute profiles.
- Missing signals stay fail-open: no profiles -> attribute score `1.0`; no anomaly evidence -> anomaly penalty `1.0`.
- Review workflow should be process-oriented: use `review_session` for a batch of images and `review_assessment` for one structured grading row per image.
- Keep reviewer identity out of the core assessment shape for now; capture process/session data first and add human provenance later if needed.
- Expected style should be modeled as a preferred concept attribute on style-sensitive concepts, not as a separate hard requirement.
- Image style should be treated like an overrideable metadata field, similar to NSFW handling: store a best guess, let reviewers correct it.
- Style-fit should be computed from expected concept style vs. actual image style; if style is irrelevant to a concept, keep it N/A.
- Style concepts already exist in taxonomy and should be grouped/refined instead of inventing a parallel style system; use hierarchy where useful (e.g. canvas → watercolor/oil painting).
- The reviewer questionnaire should stay plain-English and cover predominance, quality, accuracy, attribute support, context incongruencies, and anomaly presence.
- Reframe the context prompt as "Are there contextual incongruencies depicted for this concept?" with follow-up types like anachronistic, anatopismic, nonsensical/inconsistent, and anomalous-form cases.
