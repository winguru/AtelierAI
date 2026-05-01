# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# 📄 docs: app/docs/memories/taxonomy-import.md
# ──────────────────────────────────────────────────────────────────────────────
"""CivitAI download, ingest, and taxonomy-sync service.

TODO Phase 3: Extract from main.py:
  - Dataclasses: _CivitaiImageData, _PreparedCivitaiImport, etc. (lines 198–266)
  - Download/ingest helpers: _fetch_civitai_*, _download_civitai_* (lines 2311–3530)
  - Import pipeline: _import_civitai_image, _upsert_civitai_* (lines 8033–10465)
"""

from __future__ import annotations
