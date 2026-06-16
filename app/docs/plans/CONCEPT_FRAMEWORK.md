# Concept Framework — Working Toward Coherent Generation

**Date:** 2026-06-09
**Status:** Design phase — architectural exploration
**Supersedes:** `search-relevance-scoring.md` (evolved beyond tag-only scoring)
**Related:** `PROJECT_VISION.md`, `TAXONOMY_CONCEPT_TRANSFER_IMPLEMENTATION_PLAN.md`

---

## The Problem We're Solving

Current diffusion art is **token-level, not concept-level**. The model doesn't understand what "Shion" is — it knows the statistical correlation of the token "Shion" with pixel patterns. This leads to:

| Symptom | Root Cause |
|---|---|
| 1 good image out of 20+ | No grounding in what the concept actually looks like |
| Seed sensitivity | Statistical correlation shifts with different noise |
| Word order matters | Cross-attention is positional, not semantic |
| CLIP is "finicky" | The pipeline lacks a concept-level understanding layer |
| Synonym blindness | "violet hair" ≠ "purple hair" to the model |

**The unit of meaning should be the concept, not the token.**

---

## What Is a Concept?

A concept is a structured, multi-modal representation of an idea that the system can reason about. It is NOT:
- A tag (too flat — no structure, no visual grounding)
- A text embedding vector (too abstract — no decomposition into attributes)
- A single image (too specific — no generality, no invariants)

A concept HAS:
- **Surface forms** — the many names by which humans refer to it
- **Attributes** — decomposable features with synonym clusters
- **A visual prototype** — what the concept looks like, aggregated from examples
- **Composition semantics** — how it combines with other concepts

```
┌─────────────────────────────────────────────────────────────────┐
│  CONCEPT                                                        │
│                                                                 │
│  ┌───────────────────────┐                                      │
│  │ SURFACE FORMS         │  "Shion", "Shion Tensura",           │
│  │ (semantic aliases)    │  "シオン", "Shion (Tensei Shitara     │
│  │                       │  Slime Datta Ken)"                   │
│  │ Nearby in text        │                                      │
│  │ embedding space       │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                  │
│  ┌───────────▼───────────┐                                      │
│  │ ATTRIBUTES            │                                      │
│  │ (decomposable         │  visual:  purple hair, oni horn,     │
│  │  features)            │           purple eyes, tall build    │
│  │                       │  semantic: anime character, female,  │
│  │ Each attribute is a   │           demon/oni type             │
│  │ cluster of synonyms:  │                                      │
│  │  "horn" ≈ "oni horn"  │  Invariant: horn, purple hair        │
│  │            ≈ "demon   │  Variable:  beach, armor, cooking    │
│  │               horn"   │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                  │
│  ┌───────────▼───────────┐                                      │
│  │ VISUAL PROTOTYPE      │  Aggregated CLIP embeddings from     │
│  │ (what it LOOKS LIKE)  │  example images. Preserves invariant │
│  │                       │  features, cancels variable ones.    │
│  │ Independent of any    │                                      │
│  │ particular instance   │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                  │
│  ┌───────────▼───────────┐                                      │
│  │ COMPOSITION INTERFACE │  "Shion" × "beach"                   │
│  │ (how concepts combine)│  "Shion" × "sword" × "armor"         │
│  │                       │  identity × context → compositional  │
│  │                       │  scoring                             │
│  └───────────────────────┘                                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Concept Types

Not all concepts are the same kind of thing. The framework should accommodate different types, each with its own attribute schema and composition rules:

### Character Concept
A person, creature, or entity with a recognizable visual identity.

| Aspect | Example (Shion) |
|---|---|
| Surface forms | "Shion", "Shion Tensura", "シオン" |
| Invariant attributes | purple hair, oni horn, purple eyes, body type |
| Variable attributes | outfit, pose, setting, activity, other characters |
| Composition | identity × context (Shion on a beach, Shion cooking) |
| Visual prototype | CLIP centroid of reference images |
| Example images needed | 20-100+ for reliable prototype |

### Object Concept
A physical thing with visual properties.

| Aspect | Example (Red Ball) |
|---|---|
| Surface forms | "red ball", "crimson sphere", "red orb" |
| Attributes | red, spherical, round, shiny/matte (variable) |
| Composition | object × scene (red ball on grass, red ball in water) |
| Visual prototype | CLIP centroid of red ball images |
| Example images needed | 10-50 for reliable prototype |

### Style Concept
An artistic approach, aesthetic, or visual quality.

| Aspect | Example (Watercolor) |
|---|---|
| Surface forms | "watercolor", "watercolour", "aquarelle" |
| Attributes | soft edges, visible paper texture, color bleeding |
| Composition | style × subject (watercolor portrait, watercolor landscape) |
| Visual prototype | CLIP centroid of watercolor images |

### Scene/Setting Concept
An environment or spatial context.

| Aspect | Example (Sunset) |
|---|---|
| Surface forms | "sunset", "dusk", "golden hour", "夕暮れ" |
| Attributes | warm colors, low angle light, long shadows, orange/purple sky |
| Composition | scene × subject (sunset over ocean, sunset portrait) |

The framework is **open** — new concept types can be added as needed. The core structure (surface forms + attributes + prototype + composition) is shared.

---

## The Visual Prototype

This is the key innovation. A concept's visual prototype captures **what the concept looks like** independent of any particular instance.

### How It Works

```
55 reference images of Shion
        ↓
CLIP image encoder (ViT-B/32 or larger)
        ↓
55 embedding vectors (each 512-dim for ViT-B/32)
        ↓
shion_prototype = mean(vectors)     ← the centroid
        ↓
Optional: covariance matrix         ← which features are stable vs. variable
```

### Why the Centroid Works

This is **Collection Tag Consistency (CTC)** operating in visual feature space:

- Features present in **most** reference images (hair, horn, face, build) → **strong signal** in the centroid
- Features present in **few** reference images (beach, armor, cooking, other characters) → **weak signal**, cancelled by averaging
- The prototype automatically separates **invariant identity** from **variable context**

No manual attribute curation needed for the visual signal. The prototype IS the concept's visual fingerprint.

### Advanced: Beyond Simple Centroid

The centroid is the starting point. More sophisticated options:

| Approach | Benefit | When to Use |
|---|---|---|
| **Simple centroid** | Zero cost, good baseline | Starting point |
| **Weighted centroid** | Weight by image quality or relevance | When reference images vary in quality |
| **Multiple prototypes** | Cluster reference images into sub-types | When a concept has distinct "looks" (e.g., Shion casual vs. Shion armor) |
| **Covariance matrix** | Understand which dimensions are stable | For confidence scoring and attribute discovery |
| **Fine-tuned encoder** | Higher accuracy for specific domain | When you have 500+ labeled images |

---

## Compositional Scoring

This is where concept-level understanding becomes practically useful.

### The Decomposition

A query like "Shion on a beach" is actually TWO independent questions:

1. **Identity**: Does this image look like Shion?
2. **Context**: Does this image depict a beach?

These are scored independently and combined multiplicatively:

```
identity_score = cos(CLIP_img(candidate), shion_prototype)
context_score  = cos(CLIP_img(candidate), CLIP_text("on a beach"))
final_score    = identity_score × context_score
```

**Why multiplication, not addition?** Because we want BOTH conditions to be true. Addition would allow a high context score to compensate for a low identity score (excellent beach photo, but not Shion).

### Composition Patterns

| Query | Identity | Context | Result |
|---|---|---|---|
| "Shion" | shion_prototype | none (score=1) | Pure identity matching |
| "Shion on a beach" | shion_prototype | CLIP_text("on a beach") | Identity × scene |
| "Shion holding a sword" | shion_prototype | CLIP_text("holding a sword") | Identity × action |
| "Shion cooking" | shion_prototype | CLIP_text("cooking") | Identity × activity |
| "Shion and Rimuru" | shion_prototype | rimuru_prototype | Identity × identity (multi-character) |

### Multi-Concept Composition

For queries involving multiple concepts ("Shion and Rimuru at sunset"):

```
identity_1 = cos(candidate, shion_prototype)
identity_2 = cos(candidate, rimuru_prototype)
context    = cos(candidate, CLIP_text("at sunset"))
final      = identity_1 × identity_2 × context
```

Note: multi-character detection in a single image is harder — the candidate may contain both characters, but CLIP encodes the whole image as one vector. This is a known limitation to address later (e.g., with object detection + per-region encoding).

---

## Relationship to Existing AtelierAI Infrastructure

### Concept Model (Already Exists)

The `Concept` table in the DB already provides:
- `canonical_name` + `slug` → maps to surface forms
- `ConceptAlias` → already stores semantic aliases
- `parent_concept_id` → supports hierarchical concepts (e.g., "Shion" → "Slime Tensei characters")
- `AuthorityTerm` → connects concepts to tags from multiple sources (civitai, danbooru, prompt, user)
- `ImageConceptObservation` → links concepts to images with confidence scores

**This is the taxonomy backbone.** The concept framework adds a visual/multi-modal layer on top.

### What the Framework Adds to the Existing Model

| Existing | Framework Addition | Storage |
|---|---|---|
| `Concept.canonical_name` | Already works as primary surface form | Existing |
| `ConceptAlias.alias` | Already stores alternate names | Existing |
| `AuthorityTerm → Concept` | Tag-to-concept mapping (attributes) | Existing |
| **Visual prototype** | CLIP embedding vector of the concept | **NEW** — column or sidecar |
| **Concept type** | character / object / style / scene | **NEW** — column on `Concept` |
| **Invariant vs. variable attributes** | Tags partitioned by consistency | **NEW** — metadata or tag groups |
| **Expected style** | Preferred style archetype for a concept (e.g. anime, cartoon, watercolor) | **NEW** — concept attribute |
| **Attribute cardinality** | Expected count/range for an attribute | **NEW** — metadata on concept attributes |
| **Attribute family semantics** | Boolean, countable, or mutually exclusive group behavior | **NEW** — metadata on concept attributes or groups |
| **Authority weighting** | Relative trust assigned to each tag/evidence authority | **NEW** — evidence weighting metadata |
| **Review session** | Process-oriented review pass over a set of images | **NEW** — session table |
| **Review assessment** | One structured grading record per image within a session | **NEW** — assessment table |
| **Review evidence** | Explicit user-reviewed confirmations/contradictions | **NEW** — separate review evidence records |
| **Observation weighting** | Per-image strength of support or contradiction for a concept/attribute | **NEW** — weighted observation metadata |
| **Composition rules** | How this concept combines with others | **NEW** — metadata or relationship |

### Proposed Schema Extensions

```sql
-- Add to concepts table
ALTER TABLE concepts ADD COLUMN concept_type VARCHAR DEFAULT 'object';
-- Values: 'character', 'object', 'style', 'scene', 'abstract'

ALTER TABLE concepts ADD COLUMN prototype_vector BLOB;  -- CLIP embedding (numpy → bytes)
ALTER TABLE concepts ADD COLUMN prototype_source_count INTEGER DEFAULT 0;  -- how many images contributed
ALTER TABLE concepts ADD COLUMN prototype_updated_at DATETIME;

-- Concepts can declare an expected stylistic archetype.
-- This is a preferred attribute, not a hard requirement.
-- Example: Shion -> anime; a ball concept may have no expected style.

-- Concept attributes with invariance classification
-- (Could reuse existing authority_terms + add metadata, or new table)
CREATE TABLE concept_attribute_profiles (
    concept_id INTEGER REFERENCES concepts(id),
    attribute_term_id INTEGER REFERENCES authority_terms(id),
    consistency_score REAL,  -- CTC: fraction of reference images with this attribute
    invariance VARCHAR DEFAULT 'variable',  -- 'invariant' or 'variable'
    attribute_mode VARCHAR DEFAULT 'boolean',  -- 'boolean', 'countable', 'exclusive'
    attribute_family VARCHAR NULL,  -- e.g. 'hair_color', 'eye_color', 'horn_count'
    cardinality_min INTEGER NULL,  -- minimum expected count when countable
    cardinality_max INTEGER NULL,  -- maximum expected count; NULL means unbounded
    PRIMARY KEY (concept_id, attribute_term_id)
);

CREATE TABLE concept_attribute_authority_weights (
    concept_id INTEGER REFERENCES concepts(id),
    attribute_term_id INTEGER REFERENCES authority_terms(id),
    authority_id INTEGER REFERENCES tag_authorities(id),
    base_weight REAL NOT NULL DEFAULT 1.0,
    learned_weight REAL NULL,
    updated_at DATETIME,
    PRIMARY KEY (concept_id, attribute_term_id, authority_id)
);

CREATE TABLE concept_review_evidence (
    id INTEGER PRIMARY KEY,
    concept_id INTEGER REFERENCES concepts(id),
    image_id INTEGER REFERENCES images(id),
    attribute_term_id INTEGER NULL REFERENCES authority_terms(id),
    evidence_kind VARCHAR NOT NULL,  -- 'identity', 'attribute', 'context', 'style', 'anomaly'
    verdict VARCHAR NOT NULL,  -- 'supports', 'contradicts', 'unknown'
    confidence REAL NULL,
    notes TEXT,
    reviewer TEXT NULL,
    created_at DATETIME
);

  CREATE TABLE concept_review_sessions (
    id INTEGER PRIMARY KEY,
    concept_id INTEGER REFERENCES concepts(id),
    status VARCHAR NOT NULL DEFAULT 'open',  -- open | completed | abandoned
    notes TEXT,
    created_at DATETIME,
    updated_at DATETIME,
    closed_at DATETIME
  );

  CREATE TABLE concept_review_assessments (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES concept_review_sessions(id),
    concept_id INTEGER REFERENCES concepts(id),
    image_id INTEGER REFERENCES images(id),
    predominance_rating INTEGER NULL,  -- 1..5
    quality_rating INTEGER NULL,  -- 1..5
    accuracy_rating INTEGER NULL,  -- 1..5
    attribute_support_rating INTEGER NULL,  -- 1..5
    context_fit_rating INTEGER NULL,  -- 1..5
    anomaly_present INTEGER NOT NULL DEFAULT 0,
    anomaly_kind VARCHAR NULL,
    anomaly_degree INTEGER NULL,  -- conditional 1..4 scale
    image_style_concept_id INTEGER NULL REFERENCES concepts(id),
    image_style_source VARCHAR NULL,  -- guessed | review | imported
    image_style_confidence REAL NULL,
    notes TEXT,
    created_at DATETIME,
    updated_at DATETIME,
    UNIQUE(session_id, image_id)
  );

-- Existing image-concept observations should support weighting semantics.
-- This can extend the current table or be modeled as a richer sibling table.
ALTER TABLE image_concept_observations
    ADD COLUMN observation_weight REAL NULL;  -- how strongly this image supports the concept/attribute

ALTER TABLE image_concept_observations
    ADD COLUMN review_confidence REAL NULL;  -- curator confidence in the observation itself

ALTER TABLE image_concept_observations
    ADD COLUMN training_role VARCHAR NULL;  -- 'positive', 'negative', 'hard_negative', 'style_reference', 'context_reference', etc.

ALTER TABLE image_concept_observations
    ADD COLUMN concept_strength_weight REAL NULL;  -- how completely the target concept is supported by its attributes in this image
```

Recommended observation semantics:

- `observation_weight`: how strongly this image should influence the concept or attribute during training
- `review_confidence`: how certain the reviewer is that the observation is correct
- `training_role`: whether the image is a positive exemplar, hard negative, style reference, context reference, anomaly example, etc.
- `concept_strength_weight`: how completely the target concept is supported by its attributes within this image

Recommended review workflow semantics:

- `review_session`: the process container for grading a batch of images for one concept
- `review_assessment`: one row per image in a session, capturing the simple questionnaire answers
- `image_style_concept_id`: the best-known style archetype for an image, overrideable during review
- expected style is modeled as a concept attribute on the reviewed concept, not as a separate style-only system

Review Lab questionnaire (Phase 3) should stay deliberately plain-English:

- How predominant is this concept?
- Is this image good quality for training?
- Is the concept depicted accurately?
- How strongly do the visible attributes support the concept?
- Are there contextual incongruencies depicted for this concept?
  - If yes, classify kind(s):
    - out of place in time (anachronistic)
    - out of place in location (anatopismic)
    - nonsensical or inconsistent
    - out of expected form (anomalous)
- Are there noticeable visual anomalies? If yes, classify the anomaly type and degree

Style handling should be mostly computed:

- concept-side expected style: preferred concept attribute for style archetypes
- image-side actual style: best-guess image metadata, overrideable during review
- style fit: computed from expected style vs actual image style
- if style is not relevant to a concept, it should stay N/A instead of forcing a rating

This separates two ideas that often get conflated:

- correctness: is the observation true?
- usefulness: how much should this image influence training for that concept or attribute?

Recommended authority roles:

- `user` authority tags: curated but still tag-like, can be weighted and revised
- `prompt` authority tags: useful but noisy due to prompt decoherence
- `civitai` authority tags: useful metadata, often incomplete or weakly discriminative
- `danbooru` authority tags: strong taxonomy backbone, but not always present in source metadata
- `review` evidence: explicit human judgment, stored separately from tags and treated as the highest-quality signal

Recommended initial authority weight ordering:

- review evidence > user tags > danbooru mappings > prompt tags > civitai tags

These are starting priors only. Review cycles should tune them per concept and per attribute.

Recommended cardinality shorthands:

- `*` or `0..*`: unconstrained or informational only
- `1..1`: exactly one required instance
- `1..*`: one or more required instances
- `0..1`: optional singleton when count matters but absence is allowed

Recommended attribute modes:

- `boolean`: simple present / absent / unknown evidence
- `countable`: attribute supports cardinality constraints such as `1..1` or `1..*`
- `exclusive`: attribute belongs to a mutually exclusive family where one member should dominate

Examples:

- Shion -> `single horn`: `1..1`
- Shuna -> `white horns`: `1..*` or exactly `2..2` if the system eventually supports stricter counts
- "companion character": `0..*`
- "greatsword": `0..1`
- Shion -> `light purple hair`: family `hair_color`, mode `exclusive`
- Shuna -> `pink eyes`: family `eye_color`, mode `exclusive`

---

## Practical Pipeline: Concept-Based Search

This is the immediate application — improving search quality.

```
User query: "Shion on a beach"
        ↓
┌──────────────────────────────────────┐
│ 1. PARSE                             │
│    Decompose into:                   │
│    identity_concept = "Shion"        │
│    context = "on a beach"            │
└──────────────┬───────────────────────┘
               ↓
┌────────────────────────────────────────┐
│ 2. RESOLVE                             │
│    Look up "Shion" in concept DB       │
│    → surface forms for text search     │
│    → prototype vector for matching     │
│    → attribute profile for fast filter │
└──────────────┬─────────────────────────┘
               ↓
┌──────────────────────────────────────┐
│ 3. CANDIDATE RETRIEVAL               │
│    Meilisearch: broad text match     │
│    using surface forms               │
│    → 158 candidates                  │
└──────────────┬───────────────────────┘
               ↓
┌──────────────────────────────────────┐
│ 4. FAST PRE-FILTER (tag-based)       │
│    Attribute profile as filter:      │
│    require: any(purple hair variants)│
│    require: any(horn variants)       │
│    → ~60 candidates                  │
└──────────────┬───────────────────────┘
               ↓
┌──────────────────────────────────────┐
│ 5. VISUAL SCORING (CLIP-based)       │
│    Download thumbnails (~2-3s)       │
│    CLIP encode each thumbnail        │
│    identity = cos(img, prototype)    │
│    context  = cos(img, text("beach"))│
│    final    = identity × context     │
│    → ranked results                  │
└──────────────┬───────────────────────┘
               ↓
┌──────────────────────────────────────┐
│ 6. PRESENT + LEARN                   │
│    Show ranked results with scores   │
│    User marks relevant/not relevant  │
│    → refine attribute profile        │
│    → optionally add to reference set │
└──────────────────────────────────────┘
```

### Compute Costs (Estimates)

| Step | CPU | GPU | Notes |
|---|---|---|---|
| Text search (Meilisearch) | <50ms | N/A | Fast, always first |
| Tag pre-filter | <10ms | N/A | Pure string matching |
| Thumbnail download (60 images) | ~2-3s | N/A | Parallel, Civitai CDN |
| CLIP encode (60 images) | ~3-5s | ~0.3s | ViT-B/32 estimates |
| Scoring (60 cosine similarities) | <10ms | <1ms | Trivial |
| **Total** | ~6-8s | ~0.5s | GPU makes this interactive |

---

## Concept Training And Feedback Cycle

Concept quality should improve through repeated review, not one-shot labeling. The goal is not only to decide whether an image matches a concept, but to capture why it matches, why it fails, and what kind of failure occurred.

### Review Dimensions

Each reviewed image can contribute signal in several independent dimensions:

1. **Identity**
   - Is this the target concept at all?
   - Example: "This is Shion", "This is Shuna", "This is neither"

2. **Supporting attributes**
   - Which identity-defining traits are present, missing, or contradicted?
   - Example: single black horn, light purple hair, purple eyes, large breasts

3. **Contextual attributes**
   - What non-essential but informative context is present?
   - Example: business suit, greatsword, shrine maiden outfit, beach, kitchen, battle scene

4. **Style and rendering**
   - What visual style or rendering mode is the image using?
   - Example: anime screenshot, stylized fanart, painterly, chibi, sketch, 3D render

5. **Anomalies and drift**
   - What went wrong relative to the intended concept?
   - Example: missing horn, wrong eye color, wrong outfit, identity bleed, merged characters, prompt decoherence

### Review Rubric

To make review actionable, the system should capture a small set of scored dimensions rather than only freeform notes.

#### Concept Predominance

How dominant is the target concept within the image composition?

- **High predominance**: the concept is the main subject and occupies most of the semantic attention of the image
- **Medium predominance**: the concept is clearly present but shares focus with other subjects or scene elements
- **Low predominance**: the concept is present but peripheral, occluded, tiny, or compositionally secondary

Use predominance to influence:

- prototype contribution weight
- whether the image should contribute to identity training
- whether the image is a strong reference or only weak supporting evidence

#### Concept Quality

How technically suitable is this image for training? Does it have the visual clarity, sharpness, and absence of artifacts needed to be a useful exemplar?

- **High quality**: clear, sharp, artifact-free image with good visual fidelity for training
- **Medium quality**: mostly clear but with minor blur, compression, or rendering artifacts that don't obscure the concept
- **Low quality**: blurry, heavily compressed, contains distracting artifacts, or has technical issues that would harm model training

Quality is independent of the training task. The same image is either technically suitable or not.

#### Concept Accuracy

How accurately does the image represent the intended concept?

- **High accuracy**: the concept is depicted correctly with no meaningful contradictions
- **Medium accuracy**: the concept is mostly correct but has noticeable drift, omission, or stylization-induced ambiguity
- **Low accuracy**: the concept is misrepresented or significantly contradicted

Accuracy should influence:

- whether the image is accepted as a positive exemplar
- whether contradictions are recorded as anomalies
- whether the image is downgraded to context/style-only use instead of identity use

Quality should influence:

- whether the image contributes to prototype construction
- how much weight it receives during concept refinement
- whether it should be filtered from training at all (if quality is too low)

#### Concept Strength

How strongly is the target concept supported by the presence or absence of its supporting attributes within this image?

- **High strength**: most or all of the target concept's supporting attributes are present and visually coherent
- **Medium strength**: some attributes are present, others are missing or ambiguous, but the concept is still recognizable
- **Low strength**: few attributes are present, or many are contradicted or absent

Concept Strength reflects how completely this image demonstrates the target concept through its attributes.

Example for Shion:

- High strength: horn, purple hair, purple eyes, business suit all present
- Medium strength: horn and hair present, but in different outfit or partially visible
- Low strength: some attributes missing, or contradictory attributes present (e.g., pink hair)

This should influence both concept-level and attribute-level observation weight.

#### Suggested Weight Mapping

These review dimensions can be mapped into numeric weights for training:

- high -> `1.0`
- medium -> `0.6`
- low -> `0.25`

One practical composition for a reviewed identity-training example is:

$$
\omega_k = w_{\text{pred},k} \cdot w_{\text{qual},k} \cdot w_{\text{acc},k} \cdot w_{\text{conf},k}
$$

Where:

- $w_{\text{pred},k}$ comes from concept predominance
- $w_{\text{qual},k}$ comes from concept quality
- $w_{\text{acc},k}$ comes from concept accuracy
- $w_{\text{conf},k}$ comes from reviewer confidence

For attribute-level training, concept strength can be included as an additional factor.

#### Practical Interpretation

- A canonical portrait of Shion with clear horn, hair, eyes, and outfit should score high on predominance, relevance, and accuracy.
- A group shot where Shion appears in the background may still be accurate, but low predominance should reduce prototype influence.
- A stylized image with correct identity but distorted details may remain relevant for style while receiving reduced accuracy for identity training.
- A confusable image of Shuna may have high visual similarity but low concept accuracy for Shion, making it a useful hard negative rather than a positive reference.

### Feedback Loop

The concept-training loop should work like this:

```
Seed collection / search results
  ↓
User review pass
  ↓
Mark identity, attributes, context, style, anomalies
  ↓
Update concept profile
  - strengthen invariant attributes
  - add or demote variable attributes
  - split style/context from identity
  - record common failure modes
  ↓
Rebuild prototype / scoring rules
  ↓
Run search again and compare errors
  ↓
Repeat until the concept is stable
```

### Why This Matters

- A false positive is not just "wrong"; it often reveals a missing discriminator.
- A false negative is not just "missed"; it may reveal framing bias, style bias, or an over-strict attribute rule.
- Style, outfit, and setting should not be conflated with identity unless the user explicitly wants that narrower concept.
- Failure cases should be modeled as first-class concepts or annotations when they recur often enough to matter.

### Practical Outcome

Over time, a concept becomes more than a name:

- **Identity layer**: who or what the concept is
- **Attribute layer**: the traits that define or distinguish it
- **Context layer**: outfits, settings, companions, roles, and scenes
- **Style layer**: how the concept is rendered
- **Anomaly layer**: how generation or classification commonly fails

This makes concept training useful for both search quality and model understanding. Instead of only asking "did the model get Shion right?", the system can ask "did the model preserve Shion's identity, attributes, context, and style without introducing known failure modes?"

---

## Scoring Algorithm Specification

This section defines the target scoring model for concept search and concept review. The aim is to separate identity from supporting evidence so the system can explain both strong matches and failure cases.

### Inputs

For a candidate image $x$ and a query containing one or more identity concepts $C = \{c_1, \dots, c_n\}$:

- $e(x)$: CLIP image embedding for the candidate image
- $p_i$: prototype embedding for concept $c_i$
- $t$: optional context text embedding for the non-identity portion of the query
- $A_i$: attribute set for concept $c_i$
- $R_i$: optional style/render expectations for concept $c_i$
- $D_i$: anomaly or contradiction signals for concept $c_i$
- $H$: available evidence authorities (user, prompt, civitai, danbooru, review, detector, etc.)
- $W_i$: weighted observation set for concept $c_i$

All score components are normalized to $[0, 1]$ before combination.

### 1. Identity Similarity

For each concept $c_i$, start with raw cosine similarity:

$$
s_i(x) = \cos(e(x), p_i)
$$

Convert the raw cosine into a concept-local normalized identity score using learned floor and ceiling parameters:

$$
\hat{s}_i(x) = \operatorname{clamp}\left(\frac{s_i(x) - f_i}{u_i - f_i}, 0, 1\right)
$$

Where:

- $f_i$ is the concept-specific floor, below which identity evidence is treated as non-discriminative
- $u_i$ is the concept-specific upper anchor, above which identity evidence is treated as fully saturated

Recommended initial defaults:

- Use empirical per-concept thresholds when labeled data exists
- Fall back to global defaults only when concept-specific data is missing

#### Weighted Prototype Construction

Concept prototypes should not assume every supporting image contributes equally.

For reviewed or observed positive examples $x_k$ with embedding $e(x_k)$ and total training weight $\omega_k > 0$, define the prototype as a weighted centroid:

$$
p_i = \operatorname{normalize}\left(\sum_k \omega_k e(x_k)\right)
$$

Where $\omega_k$ can be derived from:

- observation weight
- review confidence
- concept strength weight
- training role

One practical factorization is:

$$
\omega_k = w_{\text{obs},k} \cdot w_{\text{conf},k} \cdot w_{\text{str},k} \cdot w_{\text{role},k}
$$

Interpretation:

- a canonical single-character reference image can carry more weight than a noisy group shot
- a hard negative should not contribute positively to the prototype centroid
- a style reference may contribute to style modeling but not to core identity prototype updates

### 2. Attribute Evidence

Each concept has attributes $a_{ij} \in A_i$ with:

- weight $w_{ij}$
- invariance flag: invariant or variable
- mode: boolean, countable, or exclusive
- optional family label $F_{ij}$
- evidence score $m_{ij}(x) \in [0, 1]$
- optional cardinality constraint $[\ell_{ij}, u_{ij}]$
- per-authority evidence contributions $m_{ij}^{(h)}(x)$ for $h \in H$

The evidence score is computed from tags, metadata, explicit review labels, or future visual attribute detectors.

#### Evidence Provenance And Authority Weighting

Attributes are concepts, but the system rarely observes them directly. It observes evidence from one or more authorities. Those authorities should not be treated equally.

For each authority $h$, define:

- $m_{ij}^{(h)}(x) \in [0, 1]$: the authority-local evidence for attribute $a_{ij}$ on image $x$
- $\lambda_{ij}^{(h)} > 0$: the authority weight for concept $c_i$, attribute $a_{ij}$, and authority $h$

Then aggregate authority-local evidence into a single attribute evidence score:

$$
m_{ij}(x) = \frac{\sum_{h \in H} \lambda_{ij}^{(h)} m_{ij}^{(h)}(x)}{\sum_{h \in H} \lambda_{ij}^{(h)}}
$$

Interpretation:

- high-confidence review evidence should dominate weaker imported metadata
- user tags can be strong but still revisable
- prompt-derived evidence should start useful but lower-trust
- missing authorities do not hurt the score; they simply contribute nothing

Recommended initial semantics:

- `review` evidence is not just another tag source; it is direct human judgment and should generally carry the highest baseline weight
- `user` tags are curated authority-tag evidence and should start strong
- `danbooru` mappings are structurally valuable and often more discriminative than raw source tags
- `prompt` and `civitai` authorities should usually start lower because they are often incomplete or noisy

#### Observation-Weighted Attribute Evidence

When evidence comes from reviewed observations rather than only tags, the attribute score should also respect how important that observation is within the image.

For observation-level evidence items $o \in O_{ij}(x)$ supporting or contradicting attribute $a_{ij}$, let each observation carry:

- a local evidence value $v_o \in [0, 1]$
- an observation weight $\omega_o > 0$

Then the review-derived attribute evidence can be aggregated as:

$$
m_{ij}^{(\text{review})}(x) = \frac{\sum_{o \in O_{ij}(x)} \omega_o v_o}{\sum_{o \in O_{ij}(x)} \omega_o}
$$

This allows review to express not just "the horn exists" but also "the horn is central to why this image should train Shion strongly".

Interpretation:

- $m_{ij}(x) = 1.0$: attribute clearly present and supportive
- $m_{ij}(x) = 0.5$: unknown or no usable evidence
- $m_{ij}(x) = 0.0$: contradicted by evidence

#### Attribute Modes

Attributes are not all interpreted the same way. The scoring system should distinguish three common cases.

##### Boolean attributes

These are ordinary supportive traits where the main question is presence, absence, or contradiction.

Examples:

- business suit
- greatsword
- hair ribbon

For boolean attributes, use the base evidence score $m_{ij}(x)$ directly.

##### Countable attributes

These are attributes where the number of observed instances matters.

Examples:

- single horn
- white horns
- companion characters
- swords

Countable attributes use the cardinality term $c_{ij}(x)$ defined below.

##### Mutually Exclusive Attribute Families

Some attributes belong to a family where one attribute being strongly present should weaken competing members of that same family.

Examples:

- hair color: pink hair, light purple hair, blonde hair
- eye color: pink eyes, purple eyes, blue eyes
- horn color: black horns, white horns

Represent this with a family label such as `hair_color` or `eye_color`.

For a family $F$, let the evidence scores for its members be $m_{F,1}(x), m_{F,2}(x), \dots$. The expected attribute for concept $c_i$ should receive a family agreement multiplier that rewards dominance over alternatives:

$$
e_{ij}(x) =
\begin{cases}
1 & \text{if family evidence is unavailable} \\
\frac{m_{ij}(x)}{\max(\epsilon, \max_{k \in F_{ij}} m_{ik}(x))} & \text{if family evidence exists}
\end{cases}
$$

Interpretation:

- expected hair color is strongest in its family -> multiplier near $1$
- competing color dominates -> multiplier drops toward $0$
- no family evidence -> neutral

This helps express the idea that "pink hair" is not merely missing support for Shion; it is positive support for a competing identity signal.

Recommended default weights:

- invariant attribute: $w_{ij} = 1.0$
- variable attribute: $w_{ij} = 0.5$
- user-promoted discriminator: $w_{ij} > 1.0$

#### Attribute Cardinality

Some attributes are not just boolean. They carry count semantics that matter for discrimination.

Examples:

- single horn: exactly one
- twin tails: two
- group shot: one or more companions
- sword: zero or one in most character portraits

Represent this as an allowed cardinality interval:

$$
[\ell_{ij}, u_{ij}]
$$

Where $u_{ij} = \infty$ means unbounded.

If the system has an observed or estimated count $n_{ij}(x)$ for the attribute, compute a cardinality agreement score:

$$
c_{ij}(x) =
\begin{cases}
1 & \text{if } \ell_{ij} \le n_{ij}(x) \le u_{ij} \\
\gamma & \text{if count evidence exists and violates the constraint} \\
0.5 & \text{if count is unknown}
\end{cases}
$$

Where $\gamma$ is a contradiction penalty such as $0.1$ to $0.3$ depending on how strict the attribute is.

Then fold cardinality into the attribute evidence term:

$$
  ilde{m}_{ij}(x) = m_{ij}(x) \cdot c_{ij}(x) \cdot e_{ij}(x)
$$

Where:

- for boolean attributes, use $c_{ij}(x)=1$ and $e_{ij}(x)=1$
- for countable attributes, use the learned or configured cardinality score $c_{ij}(x)$
- for exclusive-family attributes, use the family agreement score $e_{ij}(x)$

Use $\tilde{m}_{ij}(x)$ instead of $m_{ij}(x)$ in the aggregate attribute score.

Combine attribute evidence with a weighted geometric mean so one contradicted invariant attribute hurts, but unknown evidence remains near-neutral:

$$
g_i(x) = \exp\left(\frac{\sum_j w_{ij} \log(\max(\epsilon, \tilde{m}_{ij}(x)))}{\sum_j w_{ij}}\right)
$$

With $\epsilon$ as a small floor such as $0.05$ to avoid total collapse from a single zero.

This gives a soft-discrimination behavior:

- missing horn does not automatically eliminate Shion
- two horns should score materially worse against Shion's `single horn` attribute
- pink hair should count as positive competing evidence against Shion's `light purple hair`, not merely as absence
- wrong hair color and wrong eye color together should strongly reduce confidence

### 3. Context Score

If the query contains a non-identity context phrase, compute text-image similarity and normalize it:

$$
k(x) = \operatorname{clamp}\left(\frac{\cos(e(x), t) - f_t}{u_t - f_t}, 0, 1\right)
$$

If no context text is provided, set:

$$
k(x) = 1
$$

Context should refine ranking, not override identity. A beach image of the wrong character should not outrank the right character in the wrong setting.

### 4. Style Score

Style and rendering are separate from identity. If the user or concept requests style-sensitive matching, compute an optional style score:

$$
r_i(x) \in [0, 1]
$$

Examples:

- anime screenshot
- stylized fanart
- painterly
- 3D render

If style is not part of the query or no style evidence exists, set:

$$
r_i(x) = 1
$$

### 5. Anomaly Penalty

Anomalies represent contradictions or failure modes rather than missing positive evidence. For anomaly indicators $d_{ik} \in D_i$, define penalties $q_{ik}(x) \in [0, 1]$ and combine them multiplicatively:

$$
q_i(x) = \prod_k q_{ik}(x)^{\beta_{ik}}
$$

Examples:

- missing horn
- wrong eye color
- merged identities
- outfit drift
- prompt decoherence

If no anomaly evidence exists, set:

$$
q_i(x) = 1
$$

### 6. Per-Concept Composite Score

Combine identity, attributes, context, style, and anomaly signals with a weighted product:

$$
S_i(x) = \hat{s}_i(x)^{\alpha_s} \cdot g_i(x)^{\alpha_a} \cdot k(x)^{\alpha_k} \cdot r_i(x)^{\alpha_r} \cdot q_i(x)
$$

Recommended starting exponents:

- $\alpha_s = 0.55$ identity prototype evidence
- $\alpha_a = 0.25$ attribute evidence
- $\alpha_k = 0.15$ context evidence
- $\alpha_r = 0.05$ style evidence

Rationale:

- identity remains dominant
- attributes provide discrimination among visually similar candidates
- context improves rank order without overpowering identity
- style is optional and low-weight by default
- anomalies are penalties, not positive rewards

### 7. Multi-Concept Queries

For a multi-concept identity query such as "Shion and Shuna", compute one per-concept score for each requested identity concept and aggregate with a geometric mean:

$$
S_{\text{identity}}(x) = \left(\prod_{i=1}^{n} S_i(x)\right)^{1/n}
$$

This prevents one concept from dominating the result and requires all requested identities to be represented.

### 8. Final Ranking Score

The default final ranking score is:

$$
S_{\text{final}}(x) = S_{\text{identity}}(x)
$$

For implementation convenience, the system may also expose the component scores separately:

- identity prototype score
- attribute evidence score
- context score
- style score
- anomaly penalty
- final composite score

This makes review and debugging possible without collapsing everything into a single opaque number.

### Missing-Signal Fallback Rules

When information is missing:

- no prototype: concept cannot receive visual identity score; fall back to text/tag-only retrieval
- no context: use $k(x)=1$
- no style expectation: use $r_i(x)=1$
- no anomaly evidence: use $q_i(x)=1$
- no attribute evidence: use neutral attribute evidence, not contradiction
- no count estimate for a countable attribute: use neutral cardinality evidence, not contradiction

The system should fail open rather than silently over-penalize candidates with incomplete metadata.

### Feedback-Driven Parameter Updates

User review should update both concept metadata and scoring parameters.

#### Observation weight updates

Review should be able to change how much each image influences training, not just whether it is labeled positive or negative.

Examples:

- increase weight for a canonical, centered, high-confidence identity image
- reduce weight for a stylized, ambiguous, or partially occluded example
- assign a hard-negative role to confusable non-matches such as visually similar characters
- assign style-reference or context-reference roles when an image is useful for one layer of the concept but not for core identity

This means a review pass is doing two things at once:

- deciding which elements of an image are significant
- deciding how much the model should rely on that image or attribute during future training

#### Attribute consistency

For attribute $a_{ij}$ reviewed across accepted positives:

$$
\operatorname{consistency}(a_{ij}) = \frac{\#\text{accepted positives with attribute present}}{\#\text{accepted positives reviewed for that attribute}}
$$

This value should drive:

- invariant vs. variable classification
- default weight $w_{ij}$
- explanatory confidence in UI

#### Authority weight calibration

Authority weights should begin with strong priors and then be updated by review outcomes.

For an authority $h$ supporting attribute $a_{ij}$:

- increase $\lambda_{ij}^{(h)}$ when that authority consistently aligns with accepted review outcomes
- decrease $\lambda_{ij}^{(h)}$ when that authority frequently produces misleading support or contradictions

This tuning can happen:

- globally per authority
- per concept
- per concept-attribute pair

Per concept-attribute tuning is the most expressive. For example:

- prompt tags may be reasonably useful for character names but weak for eye color
- user tags may be excellent for identity attributes but inconsistent for background/style descriptors
- danbooru mappings may be especially strong for outfit or body-feature attributes

#### Review evidence policy

Explicit review evidence should be stored separately from weighted user tags.

Rationale:

- a user tag is still tag-shaped metadata and may be edited, imported, merged, or normalized
- a review judgment is an auditable training event with a reviewer, timestamp, confidence, and outcome
- keeping review evidence separate preserves provenance and allows the system to distinguish "the user tagged this image" from "the user explicitly confirmed or rejected this attribute during a review pass"

In scoring, review evidence should usually override weaker authorities when it exists for the same image and concept/attribute.

#### Prototype rebuild policy

When rebuilding a concept prototype or related attribute/reference models:

- use weighted positive observations for the core centroid
- exclude hard negatives from positive prototype aggregation
- optionally maintain separate weighted centroids for identity, style, context, or anomaly reference sets

This keeps the training signal aligned with the actual purpose of each reviewed image.

#### Attribute cardinality calibration

For countable attributes, review can also refine the permitted range:

- repeated accepted positives with exactly one observed instance suggest `1..1`
- repeated accepted positives with one or more observed instances suggest `1..*`
- attributes used only as descriptive metadata can remain `0..*`

This matters for discriminators like horn count, eye count, visible weapons, or number of companion characters.

#### Exclusive family calibration

For mutually exclusive families, review can refine both the family definition and the contradiction strength:

- accepted positives for Shion that repeatedly show `light purple hair` and reject `pink hair` strengthen the `hair_color` family as a discriminator
- accepted positives with stylized or ambiguous coloring may weaken family penalties so stylization does not overrule stronger identity evidence

This is especially useful for identity-adjacent traits like hair color, eye color, horn color, outfit role, or companion role.

#### Concept threshold calibration

For each concept, learn $f_i$ and optionally $u_i$ from labeled data:

- optimize for target precision/recall depending on workflow
- or optimize a simple objective such as F1 on reviewed positives and negatives

#### Anomaly penalties

If a recurrent anomaly strongly predicts false positives, increase its penalty weight $\beta_{ik}$.

If an anomaly often appears in accepted positives due to model quirks or stylization, weaken the penalty or reclassify it as a context/style variation rather than an anomaly.

### Design Constraints

- Identity and context must remain separable.
- Missing evidence is not the same as contradictory evidence.
- Style and outfit cues help ranking, but should not redefine identity unless the user explicitly narrows the concept.
- The scoring model must expose intermediate components so users can understand why a result ranked where it did.

---

## Distributed Compute Architecture

AtelierAI runs on a **Raspberry Pi 5** (ARM, no CUDA) — far too slow for interactive CLIP inference (~500ms+/image). Rather than a separate CLIP service, every AtelierAI instance has the **same codebase and same API endpoints**, adapting behavior based on available hardware.

This is the **symmetric peer** pattern: each instance can be both a client (forwarding requests to a peer) and a server (handling requests locally or from peers).

### Hardware Topology

```
┌───────────────────────────┐         LAN          ┌───────────────────────────────┐
│  Raspberry Pi 5           │◄────────────────────►│  RTX 3090 Workstation         │
│  (Edge Node)              │   Same AtelierAI     │  (GPU Peer)                   │
│                           │   HTTP REST :8000    │                               │
│  AtelierAI (FastAPI)      │                      │  AtelierAI (FastAPI)          │
│  CLIPProvider → Remote    │  /api/clip/encode ─→ │  CLIPProvider → Local (CUDA)  │
│  (forwards to workstation)│  /api/clip/text ───→ │  OpenCLIP ViT-B/32            │
│  SQLite DB, Meilisearch   │  /api/clip/health ─→ │  CUDA: ~5ms/image             │
│  Image files              │                      │  WSL2 (Ubuntu), 24GB VRAM     │
└───────────────────────────┘                      └───────────────────────────────┘

Development (single device):
┌──────────────────────────────────────┐
│  Any machine (laptop, Pi, desktop)   │
│  AtelierAI (FastAPI)                 │
│  CLIPProvider → Local (CPU)          │
│  Slow (~500ms/image) but functional  │
└──────────────────────────────────────┘
```

### The Provider Pattern

The key abstraction. A `CLIPProvider` interface hides whether inference is local or remote:

```python
class CLIPProvider(Protocol):
    """Interface for CLIP inference — local or remote."""
    async def encode_images(self, urls: list[str]) -> np.ndarray: ...
    async def encode_text(self, texts: list[str]) -> np.ndarray: ...
    async def health(self) -> dict: ...


class LocalCLIPProvider:
    """In-process CLIP using OpenCLIP directly.
    
    Used when GPU is available (fast) or as CPU fallback (slow but functional).
    Proves the pipeline works on a single device during development.
    """
    def __init__(self, model_name: str, pretrained: str):
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        if torch.cuda.is_available():
            self.model.to("cuda")
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"


class RemoteCLIPProvider:
    """Forwards CLIP requests to a peer AtelierAI instance.
    
    Used on low-power devices (Pi 5) that delegate to a GPU peer.
    """
    def __init__(self, peer_url: str):
        self._http = httpx.AsyncClient(base_url=peer_url, timeout=30.0)

    async def encode_images(self, urls: list[str]) -> np.ndarray:
        resp = await self._http.post("/api/clip/encode/images", json={"urls": urls})
        return np.array(resp.json()["embeddings"])
    # ... similar for encode_text, health
```

**Startup auto-detection:**

```python
# In main.py application startup:
if config.clip_local_enabled and (torch.cuda.is_available() or config.clip_force_cpu):
    provider = LocalCLIPProvider(config.clip_model_name, config.clip_pretrained)
    logger.info(f"CLIP: local mode ({provider.device})")
elif config.clip_peer_url:
    provider = RemoteCLIPProvider(config.clip_peer_url)
    logger.info(f"CLIP: remote mode → {config.clip_peer_url}")
else:
    provider = None
    logger.warning("CLIP: unavailable (no GPU, no peer configured)")
```

### CLIP API Endpoints (Built Into AtelierAI)

CLIP endpoints are part of the main FastAPI app, not a separate service. They serve dual purpose:
- **Self-use**: The local `ConceptPrototypeService` calls the provider directly (no HTTP overhead)
- **Peer service**: When a remote Pi calls these endpoints, they use the local `LocalCLIPProvider`

```
GET  /api/clip/health
→ {"status":"ok", "model":"ViT-B-32", "device":"cuda:0", "gpu_memory_used_gb":1.2,
   "peer_available": true}

POST /api/clip/encode/images
Body: {"urls": ["https://image.civitai.com/...", "http://peer:8000/api/images/.../file"]}
→ {"embeddings": [[...512 floats...] ...], "shape": [N, 512]}

POST /api/clip/encode/text
Body: {"texts": ["a purple-haired anime character", "on a beach"]}
→ {"embeddings": [[...512 floats...] ...], "shape": [N, 512]}

POST /api/clip/score/similarity
Body: {"query_vector": [...], "candidate_vectors": [[...] ...], "metric": "cosine"}
→ {"scores": [0.82, 0.31, ...]}
```

**Design properties:**
- **URL-based image input**: Peers send CDN URLs or their own static file URLs — no base64, no shared filesystem
- **Batch-friendly**: `/encode/images` accepts multiple URLs, processes in a single CUDA batch
- **L2-normalized**: All embeddings are normalized before returning
- **Single codebase**: No separate project to keep in sync — Pi and workstation run identical code

### Configuration

```python
# backend/config.py additions
CLIP_LOCAL_ENABLED: bool = True       # Enable local CLIP (GPU or CPU)
CLIP_FORCE_CPU: bool = False          # Force CPU even if GPU available (debug)
CLIP_PEER_URL: str = ""               # URL of peer AtelierAI for CLIP forwarding
CLIP_MODEL_NAME: str = "ViT-B-32"     # OpenCLIP model architecture
CLIP_PRETRAINED: str = "laion2b_s34b_b79k"  # Pretrained weights
```

**Deployment configs:**

| Scenario | `CLIP_LOCAL_ENABLED` | `CLIP_PEER_URL` | Behavior |
|---|---|---|---|
| Pi 5 (production) | `false` | `http://workstation:8000` | Remote: forwards to GPU peer |
| Pi 5 (dev/prototype) | `true` | empty | Local CPU: slow but proves the pipeline |
| RTX 3090 workstation | `true` | empty | Local CUDA: serves self + Pi peers |
| Cloud GPU pod | `true` | empty | Local CUDA: batch processing only |

### Image Access Patterns

Peers have local image files; the GPU peer needs to access them for encoding:

| Strategy | When | Notes |
|---|---|---|
| **Civitai CDN URL** | Prototype building from Civitai collections | Best option — GPU peer downloads directly |
| **Peer static file URL** | Local-only images | `http://pi-host:8000/api/images/{id}/file` — GPU peer fetches from Pi |
| **Thumbnail URL** | Scoring candidates during search | Small and fast to transfer |

The sender uses whatever URL it has (`civitai_cdn_url` preferred, fallback to peer-served file URL).

### Graceful Degradation

When CLIP is unavailable (no GPU, no peer, peer down), the system fails open:

1. **Search**: Falls back to tag-only scoring (existing pipeline). Visual scoring is skipped — results are lower quality but never empty.
2. **Prototype building**: Returns `None`. Concepts without prototypes still have surface forms and attributes — they work for text search, just not visual matching.
3. **Health monitoring**: Each instance exposes `/api/clip/health`. UI shows CLIP status indicator. Logs warn on degradation.

### Peer Coordination (Future: Multi-Instance Sync)

The symmetric architecture naturally supports library sharing between instances:

```
Peer A (Pi 5)                          Peer B (RTX 3090)
  GET /api/concepts/export ──────────→  POST /api/concepts/import
  GET /api/images/{id}/file ─────────→  (fetches image for CLIP encoding)
  POST /api/clip/encode/images ──────→  (returns embeddings)
```

**No new protocol needed** — peer sync is just two AtelierAI instances calling each other's existing REST API. Export/import is a natural extension of the concept management endpoints (Phase 2).

### Scaling Path

| Tier | When | How |
|---|---|---|
| **Local CPU** (development) | Dev/testing on any machine | Same code, `CLIP_LOCAL_ENABLED=true`, runs on CPU |
| **RTX 3090 peer** (Phase 1) | Production, LAN | Same code, `CLIP_LOCAL_ENABLED=true`, CUDA auto-detected |
| **Cloud GPU pod** (Phase 3+) | Large batch jobs (1000+ images) | Same code deployed to RunPod/Vast.ai — acts as peer |
| **Multi-GPU** (future) | High throughput needed | Ray Serve or Triton behind the same `/api/clip/*` endpoints |

**The same AtelierAI codebase runs everywhere.** Only configuration differs.

---

## Phased Implementation Plan

### Phase 1: Concept Prototype Service
**Goal:** Build the core prototype creation and matching pipeline with the Provider pattern for CLIP inference.

- [ ] **CLIP Provider** (core abstraction):
  - [ ] `CLIPProvider` protocol in `app/backend/services/clip_provider.py`
  - [ ] `LocalCLIPProvider` — OpenCLIP in-process (CPU or CUDA)
  - [ ] `RemoteCLIPProvider` — HTTP client to peer AtelierAI
  - [ ] Startup auto-detection in `main.py` (GPU → local, no GPU → remote or None)
  - [ ] Configuration: `CLIP_LOCAL_ENABLED`, `CLIP_PEER_URL`, `CLIP_MODEL_NAME` in `config.py`
- [ ] **CLIP API router** (serve peers):
  - [ ] `app/backend/routers/clip_router.py` — `/api/clip/health`, `/encode/images`, `/encode/text`, `/score/similarity`
  - [ ] URL-based image input with async download + batch processing
  - [ ] L2-normalized embeddings in all responses
- [ ] **Dependencies**:
  - [ ] Add `open_clip_torch`, `torch` to requirements (with CPU-only fallback for Pi)
  - [ ] Add `httpx` to requirements for `RemoteCLIPProvider`
- [ ] **Schema extensions**:
  - [ ] Add `concept_type`, `prototype_vector` (BLOB), `prototype_source_count`, `prototype_updated_at` columns to `Concept`
  - [ ] Alembic migration or manual ALTER TABLE
- [ ] **ConceptPrototypeService**:
  - [ ] `build_prototype(concept_id, image_urls)` → provider.encode_images → centroid → store BLOB
  - [ ] `score_identity(image_url, prototype_vector)` → provider.encode_images → cosine similarity
  - [ ] `score_context(image_url, context_text)` → provider for both → cosine similarity
  - [ ] `score_composite(image_url, concept, context_text)` → identity × context
  - [ ] Graceful fallback when provider is `None` (returns None scores)
- [ ] Integration with existing `GalleryTagService` for attribute profile extraction
- [ ] **Dev validation**: Test full pipeline on Pi in CPU mode — slow but functionally identical to GPU

### Phase 2: Concept Management API + UI
**Goal:** Let users create and manage concepts through the interface.

- [ ] API: `POST /concepts` with type, surface forms, seed collection
- [ ] API: `GET /concepts/{id}/profile` → attribute analysis + prototype stats
- [ ] API: `POST /concepts/{id}/rebuild-prototype` → recompute from current images
- [ ] API: `GET /concepts/export` + `POST /concepts/import` → peer library sharing
- [ ] UI: Concept editor — surface forms, attributes (invariant/variable), example images
- [ ] UI: Concept gallery — browse concepts, see prototypes, manage reference images
- [ ] UI: Review workflow — mark identity, attributes, context, style, and anomaly/failure labels during curation

### Phase 3: Concept-Based Search
**Goal:** Use concepts to improve search quality.

- [ ] Search decomposition: parse queries into concept + context
- [ ] Tag pre-filter using attribute profile
- [ ] Implement scoring algorithm: per-concept normalization, attribute evidence, context weighting, style weighting, anomaly penalties
- [ ] Visual scoring pipeline (download → encode → score → rank)
- [ ] Search results UI with identity/context score breakdown
- [ ] Feedback loop: mark results to refine concept profile
- [ ] Separate identity errors from style/context/anomaly errors in review results

### Phase 4: Concept Composition Analysis
**Goal:** Understand how concepts combine and improve generation coherence.

- [ ] Analyze how "Shion" prototype varies across contexts (beach, armor, cooking)
- [ ] Measure concept consistency: same prompt + different seed → how stable is the concept?
- [ ] Detect concept drift: when does adding context break identity?
- [ ] Track recurrent anomaly patterns (missing traits, wrong colors, merged identities, outfit drift)
- [ ] Report: "Model X understands concept Y with Z% coherence"

### Phase 5: Generation Guidance (Long-term)
**Goal:** Use concept knowledge to improve generation, not just search/analysis.

- [ ] Concept-informed prompt construction
- [ ] Candidate re-ranking for batch generation
- [ ] Concept-aware weight/schedule suggestions
- [ ] Integration with ComfyUI workflows

---

## Research Questions

### Q1: Prototype Quality vs. Reference Count
- How many images are needed for a reliable prototype?
- Does quality matter more than quantity?
- How to detect and handle outlier images in the reference set?

### Q2: Prototype Granularity
- One prototype per concept? Or sub-prototypes for different "looks"?
- When should a concept be split? (Is "Shion casual" a separate concept from "Shion armor"?)

### Q3: Attribute Discovery
- Can we automatically discover attributes from the prototype + reference images?
- E.g., compute which visual features are most stable → suggest as invariant attributes
- This is the inverse of the tag → attribute mapping; it's attribute → prototype validation

### Q4: Cross-Modal Consistency
- Does the text embedding of "Shion" (from CLIP text encoder) align with the visual prototype?
- If not, which is more useful for search? For generation guidance?
- Can we learn a mapping between text surface forms and visual prototype space?

### Q5: Concept Hierarchies
- How do prototypes compose hierarchically?
- "Slime Tensei character" → broad prototype (anime, fantasy)
- "Shion" → specific prototype (purple, horn, etc.)
- Can hierarchical search narrow from general to specific?

### Q6: Model-Specific Concepts
- Some concepts are model-specific (a character looks different in SD1.5 vs. SDXL)
- Should prototypes be per-model? Or model-agnostic?
- How does concept coherence vary across models?

---

## Implementation Dependencies

| Dependency | Purpose | Status |
|---|---|---|
| OpenCLIP (`open_clip_torch`) | CLIP model (ViT-B/32) — loaded by `LocalCLIPProvider` | Not yet installed |
| PyTorch (`torch`) | Tensor ops + CUDA inference for `LocalCLIPProvider` | Not yet installed |
| `httpx` | Async HTTP client for `RemoteCLIPProvider` + image download | Already available |
| numpy | Vector operations, cosine similarity | Already available |
| pillow | Image preprocessing (CLIP input) | Already available |
| FastAPI + uvicorn | CLIP API router (already part of app) | Already available |
| Existing `Concept` / `ConceptAlias` / `AuthorityTerm` tables | Concept taxonomy backbone | Already in DB |

---

## Terminology

| Term | Definition |
|---|---|
| **Concept** | A structured, multi-modal representation of an idea with surface forms, attributes, a visual prototype, and composition semantics |
| **Surface form** | A name or phrase that refers to a concept (e.g., "Shion", "シオン") |
| **Attribute** | A decomposable feature of a concept (e.g., "purple hair" is an attribute of Shion) |
| **Invariant attribute** | An attribute present in most/all instances of a concept (defines identity) |
| **Variable attribute** | An attribute present in some instances but not essential to identity (context-dependent) |
| **Visual prototype** | A vector in CLIP embedding space representing what a concept looks like |
| **CTC (Collection Tag Consistency)** | A metric: fraction of reference images with a given tag. Extends to visual features. |
| **Compositional scoring** | identity_score × context_score — combining two independent similarity measures |
| **Concept type** | A category (character, object, style, scene) with type-specific attribute schemas |

---

## Cutting With the Grain

The metaphor matters. CLIP is a powerful tool, but today's diffusion pipeline uses it like a hammer — smash tokens into pixels and hope for the best. The concept framework is about building a **structure** that lets CLIP (and future models) work with the natural grain of human understanding:

1. **People think in concepts, not tokens.** The system should too.
2. **Identity and context are separate.** Mixing them is the source of incoherence.
3. **Examples teach better than descriptions.** The visual prototype learns from images, not text.
4. **Composition is multiplicative.** "Shion on a beach" requires BOTH Shion AND beach, not either/or.
5. **Understanding is measurable.** "How coherent is this model for this concept?" should be answerable.

This doesn't replace CLIP or diffusion models — it builds a concept-level layer on top that makes them more effective.

---

*Created: 2026-06-09*
*Authors: Design discussion between user and AI assistant*
