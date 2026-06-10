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
│  │ SURFACE FORMS          │  "Shion", "Shion Tensura",           │
│  │ (semantic aliases)     │  "シオン", "Shion (Tensei Shitara    │
│  │                       │  Slime Datta Ken)"                    │
│  │ Nearby in text         │                                      │
│  │ embedding space        │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                   │
│  ┌───────────▼───────────┐                                      │
│  │ ATTRIBUTES             │                                      │
│  │ (decomposable          │  visual:  purple hair, oni horn,     │
│  │  features)             │           purple eyes, tall build    │
│  │                       │  semantic: anime character, female,   │
│  │ Each attribute is a    │           demon/oni type             │
│  │ cluster of synonyms:   │                                      │
│  │  "horn" ≈ "oni horn"  │  Invariant: horn, purple hair        │
│  │            ≈ "demon    │  Variable:  beach, armor, cooking    │
│  │               horn"    │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                   │
│  ┌───────────▼───────────┐                                      │
│  │ VISUAL PROTOTYPE       │  Aggregated CLIP embeddings from     │
│  │ (what it LOOKS LIKE)   │  example images. Preserves invariant │
│  │                       │  features, cancels variable ones.     │
│  │ Independent of any     │                                      │
│  │ particular instance    │                                      │
│  └───────────┬───────────┘                                      │
│              │                                                   │
│  ┌───────────▼───────────┐                                      │
│  │ COMPOSITION INTERFACE  │  "Shion" × "beach"                  │
│  │ (how concepts combine) │  "Shion" × "sword" × "armor"        │
│  │                       │  identity × context → compositional   │
│  │                       │  scoring                              │
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
| **Composition rules** | How this concept combines with others | **NEW** — metadata or relationship |

### Proposed Schema Extensions

```sql
-- Add to concepts table
ALTER TABLE concepts ADD COLUMN concept_type VARCHAR DEFAULT 'object';
-- Values: 'character', 'object', 'style', 'scene', 'abstract'

ALTER TABLE concepts ADD COLUMN prototype_vector BLOB;  -- CLIP embedding (numpy → bytes)
ALTER TABLE concepts ADD COLUMN prototype_source_count INTEGER DEFAULT 0;  -- how many images contributed
ALTER TABLE concepts ADD COLUMN prototype_updated_at DATETIME;

-- Concept attributes with invariance classification
-- (Could reuse existing authority_terms + add metadata, or new table)
CREATE TABLE concept_attribute_profiles (
    concept_id INTEGER REFERENCES concepts(id),
    attribute_term_id INTEGER REFERENCES authority_terms(id),
    consistency_score REAL,  -- CTC: fraction of reference images with this attribute
    invariance VARCHAR DEFAULT 'variable',  -- 'invariant' or 'variable'
    PRIMARY KEY (concept_id, attribute_term_id)
);
```

---

## Practical Pipeline: Concept-Based Search

This is the immediate application — improving search quality.

```
User query: "Shion on a beach"
        ↓
┌─────────────────────────────────────┐
│ 1. PARSE                            │
│    Decompose into:                   │
│    identity_concept = "Shion"        │
│    context = "on a beach"            │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ 2. RESOLVE                          │
│    Look up "Shion" in concept DB     │
│    → surface forms for text search   │
│    → prototype vector for matching   │
│    → attribute profile for fast filter│
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ 3. CANDIDATE RETRIEVAL              │
│    Meilisearch: broad text match     │
│    using surface forms               │
│    → 158 candidates                  │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ 4. FAST PRE-FILTER (tag-based)      │
│    Attribute profile as filter:      │
│    require: any(purple hair variants)│
│    require: any(horn variants)       │
│    → ~60 candidates                  │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ 5. VISUAL SCORING (CLIP-based)      │
│    Download thumbnails (~2-3s)       │
│    CLIP encode each thumbnail        │
│    identity = cos(img, prototype)    │
│    context  = cos(img, text("beach"))│
│    final    = identity × context     │
│    → ranked results                  │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│ 6. PRESENT + LEARN                  │
│    Show ranked results with scores   │
│    User marks relevant/not relevant  │
│    → refine attribute profile        │
│    → optionally add to reference set │
└─────────────────────────────────────┘
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
│  SQLite DB, Meilisearch   │  /api/clip/health ─→│  CUDA: ~5ms/image             │
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

### Phase 3: Concept-Based Search
**Goal:** Use concepts to improve search quality.

- [ ] Search decomposition: parse queries into concept + context
- [ ] Tag pre-filter using attribute profile
- [ ] Visual scoring pipeline (download → encode → score → rank)
- [ ] Search results UI with identity/context score breakdown
- [ ] Feedback loop: mark results to refine concept profile

### Phase 4: Concept Composition Analysis
**Goal:** Understand how concepts combine and improve generation coherence.

- [ ] Analyze how "Shion" prototype varies across contexts (beach, armor, cooking)
- [ ] Measure concept consistency: same prompt + different seed → how stable is the concept?
- [ ] Detect concept drift: when does adding context break identity?
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
