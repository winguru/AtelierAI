# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
"""Image display-item builders, variant helpers, and shared DB query expressions.

TODO Phase 3: Extract from main.py lines 10620–11999:
  - _build_local_image_variant
  - _build_civitai_video_variant
  - _build_image_variants
  - _build_display_items_for_image
  - _load_filtered_image_keys
  - _load_display_image_items
  - _get_image_or_404
  - _sync_user_tag_observations
"""

from __future__ import annotations

from models import ImageModel


def _active_image_filter():
    """SQLAlchemy filter expression: only active (non-deleted) images."""
    return (ImageModel.image_status.is_(None)) | (ImageModel.image_status == "active")
