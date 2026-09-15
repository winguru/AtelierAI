# CLIP Provider

## Design Decisions

### Lazy weight loading (2026-09)
`LocalCLIPProvider.__init__` is cheap — it only records model config. Torch +
open_clip imports and ViT-B/32 weight loading (~4s: ~1.6s torch import, rest
open_clip import + weights) happen on first use via `_ensure_loaded()`, which:

- Uses a `threading.Lock` so concurrent first-requests wait instead of racing.
- Records the load error and re-raises it on subsequent attempts (no retry
  storms on a broken model).
- Is invoked at the top of `encode_image_urls` / `encode_image_paths` /
  `encode_text` (methods then use `self._torch`, not a local import).

Public hooks:
- `ensure_loaded()` — eager load for scripts and warm-up.
- `loaded` property — whether weights are resident.

### Lifespan integration
`main.py` lifespan constructs the provider (fast), calls `set_clip_provider`,
then spawns a daemon `clip-warmup` thread calling `ensure_loaded()`. Readiness
is never blocked by CLIP; a warm-up failure is fail-open (error surfaces at
first real use instead).

### Health endpoint semantics
`/api/clip/health` never forces a load:
- weights resident → `status: "ok"` + device/vram info
- load failed → `status: "error"` + recorded error
- still loading → `status: "loading"` (model/pretrained only)

### Consumers
- `scripts/backfill_clip_embeddings.py` and
  `scripts/clip_discrimination_analysis.py` construct `LocalCLIPProvider`
  directly and read `provider._device` — they must call `ensure_loaded()`
  before using internals. `backfill_clip_embeddings.py` does this; if you add
  new scripts, follow that pattern.

## Gotchas
- First-ever `open_clip` weight load downloads ~600MB from HuggingFace — a
  cold container can take much longer than 4s. Set `HF_HOME` to a persistent
  path (or provide `HF_TOKEN`) to avoid re-downloads.
- `CLIP_LOCAL_ENABLED` defaults to true (`config.py`); on CPU-only hosts the
  provider still works but inference is slow — that's expected, not a bug.
- Module docstring in `clip_provider.py` claims GPU auto-detection picks the
  provider at startup; with lazy loading the *construction* is what happens
  at startup — selection order (local → remote → none) is unchanged.
