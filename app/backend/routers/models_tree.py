# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/model-reference.md
# ──────────────────────────────────────────────────────────────────────────────
"""Models tree hierarchy API routes.

Provides endpoints for the /frontend/models.html page:
- GET /api/models/tree/state  — hierarchy + scope-aware usage counts
- GET /api/models/tree/data   — detailed model/versions for a given type pane

The tree shows 4 hierarchy levels: base_model → model → version → precision,
split across two type panes: checkpoints and loras.
"""

from __future__ import annotations

import json as _json
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    CivitaiModel,
    CivitaiModelVersion,
    CivitaiModelVersionFile,
    ImageModel,
)
from services.model_reference_service import ModelReferenceService

router = APIRouter(prefix="/models/tree", tags=["models-tree"])

_svc = ModelReferenceService()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_base_model(raw: Optional[str]) -> str:
    """Normalize CivitAI base_model strings into short canonical keys.

    CivitAI uses labels like 'SD 1.5', 'SDXL 1.0', 'Pony Diffusion V6 XL',
    etc. We collapse these into stable lowercase tokens for grouping.
    """
    if not raw:
        return "unknown"
    text = raw.strip().lower()
    # Common normalizations
    if "sdxl" in text:
        return "sdxl"
    if text in ("sd 1.5", "sd1.5", "sd15", "stable diffusion 1.5"):
        return "sd15"
    if "pony" in text:
        return "pony"
    if "illustrious" in text:
        return "illustrious"
    if "noobai" in text:
        return "noobai"
    if "flux" in text:
        return "flux"
    if "sd 2.1" in text or "sd2.1" in text:
        return "sd21"
    if "sd 3" in text or text == "sd3" or "stable diffusion 3" in text:
        return "sd3"
    if "animagine" in text:
        return "animagine"
    if "cyberrealistic" in text:
        return "cyberrealistic"
    return text


def _precision_label(file: CivitaiModelVersionFile) -> str:
    """Build a precision label from file metadata (fp + size_label)."""
    parts: list[str] = []
    fp = (file.fp or "").strip().lower()
    size_label = (file.size_label or "").strip().lower()

    if fp:
        parts.append(fp)
    if size_label and size_label not in ("full", ""):
        parts.append(size_label)

    if not parts:
        # Default label based on type
        return "default"
    return " ".join(parts) if parts else "default"


def _image_id_set_from_keys(
    db: Session, image_keys: list[str] | None
) -> set[int] | None:
    """Resolve gallery/selected image keys to a set of DB image IDs.

    Returns ``None`` when *image_keys* is ``None`` or empty (meaning "all
    images").  Used to scope usage counts.
    """
    if not image_keys:
        return None

    # Build a mapping of key → id from the known key formats.
    # Keys follow the pattern in main.js toClientImage():
    #   gallery_item_key | file_hash:file_path | file_hash:id:<id> | file_path | ...
    ids: set[int] = set()
    hash_path_pairs: list[tuple[str, str]] = []
    hash_only: list[str] = []

    for key in image_keys:
        if not key:
            continue
        # Try "file_hash:id:<id>" format first
        if ":id:" in key:
            _hash_part, _, id_str = key.partition(":id:")
            try:
                ids.add(int(id_str))
            except ValueError:
                pass
            continue
        # Try "file_hash:file_path" format
        if ":" in key:
            h, _, p = key.partition(":")
            if h and p:
                hash_path_pairs.append((h, p))
                continue
        # Fallback: could be file_path, file_hash, or name:...
        hash_only.append(key)

    # Resolve hash:path pairs
    if hash_path_pairs:
        rows = (
            db.query(ImageModel.id)
            .filter(
                func.lower(ImageModel.file_hash).in_(
                    [h.lower() for h, _ in hash_path_pairs]
                )
            )
            .all()
        )
        ids.update(r[0] for r in rows)

    # Resolve remaining keys by hash or path
    if hash_only:
        rows = (
            db.query(ImageModel.id)
            .filter(
                ImageModel.file_path.in_(hash_only)
                | ImageModel.file_hash.in_(hash_only)
            )
            .all()
        )
        ids.update(r[0] for r in rows)

    return ids if ids else None


def _build_usage_map(
    db: Session,
    image_ids: set[int] | None,
) -> dict[str, dict[str, int]]:
    """Return {type_pane: {usage_key: count}} for images in *image_ids*.

    *type_pane* is ``"checkpoint"`` or ``"lora"``.
    Keys are stored at all three hierarchy levels so that parent nodes use
    pre-computed unique-image counts rather than summing (and thus
    double-counting) children:

      ``"base_key"``                    → unique images using any model in that base
      ``"base_key.model_id"``           → unique images using any version of that model
      ``"base_key.model_id.version_id"``→ unique images using that specific version

    Counts are sourced from ``json_metadata.civitai.models`` (checkpoints) and
    ``json_metadata.civitai.loras`` (LoRAs), keyed by ``modelVersionId``.
    """
    # Fetch image id + json_metadata rows, optionally filtered by scope
    query = db.query(ImageModel.id, ImageModel.json_metadata).filter(
        ImageModel.json_metadata.isnot(None),
        ImageModel.image_status == "active",
    )
    if image_ids is not None:
        if not image_ids:
            return {"checkpoint": {}, "lora": {}}
        query = query.filter(ImageModel.id.in_(image_ids))

    rows = query.all()

    # version_id → set of image_ids that reference it
    checkpoint_vid_images: dict[int, set[int]] = defaultdict(set)
    lora_vid_images: dict[int, set[int]] = defaultdict(set)

    for image_id, raw_meta in rows:
        meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
        if isinstance(raw_meta, str):
            try:
                meta = _json.loads(raw_meta)
            except Exception:
                continue
        civitai = meta.get("civitai", {}) if isinstance(meta, dict) else {}
        if not isinstance(civitai, dict):
            continue

        for m in civitai.get("models") or []:
            if isinstance(m, dict):
                vid = m.get("modelVersionId")
                if vid:
                    checkpoint_vid_images[int(vid)].add(image_id)

        for lora in civitai.get("loras") or []:
            if isinstance(lora, dict):
                vid = lora.get("modelVersionId")
                if vid:
                    lora_vid_images[int(vid)].add(image_id)

    all_version_ids = set(checkpoint_vid_images.keys()) | set(lora_vid_images.keys())
    if not all_version_ids:
        return {"checkpoint": {}, "lora": {}}

    # Batch-resolve version IDs to hierarchy components
    version_rows = (
        db.query(
            CivitaiModelVersion.civitai_version_id,
            CivitaiModelVersion.civitai_model_id,
            CivitaiModelVersion.base_model,
        )
        .filter(CivitaiModelVersion.civitai_version_id.in_(all_version_ids))
        .all()
    )
    version_map = {
        r.civitai_version_id: r for r in version_rows
    }

    # Build per-level image ID sets for deduplication before converting to counts.
    # model_key → set[image_id], base_key → set[image_id]
    base_images: dict[str, dict[str, set[int]]] = {
        "checkpoint": defaultdict(set),
        "lora": defaultdict(set),
    }
    model_images: dict[str, dict[str, set[int]]] = {
        "checkpoint": defaultdict(set),
        "lora": defaultdict(set),
    }

    usage: dict[str, dict[str, int]] = {"checkpoint": {}, "lora": {}}

    for pane, vid_images in (("checkpoint", checkpoint_vid_images), ("lora", lora_vid_images)):
        for vid, img_ids in vid_images.items():
            ver = version_map.get(vid)
            if ver is None:
                continue
            base_key = _normalize_base_model(ver.base_model)
            model_key = f"{base_key}.{ver.civitai_model_id}"
            version_key = f"{model_key}.{vid}"

            # Version-level: exact unique count for this version
            usage[pane][version_key] = len(img_ids)

            # Accumulate image ID sets for parent levels
            model_images[pane][model_key].update(img_ids)
            base_images[pane][base_key].update(img_ids)

    # Write pre-computed unique counts at model and base levels
    for pane in ("checkpoint", "lora"):
        for model_key, img_ids in model_images[pane].items():
            usage[pane][model_key] = len(img_ids)
        for base_key, img_ids in base_images[pane].items():
            usage[pane][base_key] = len(img_ids)

    return usage


def _precision_label_from_fields(fp: Optional[str], size_label: Optional[str]) -> str:
    """Build precision label from raw fp/size_label fields."""
    parts: list[str] = []
    fp_clean = (fp or "").strip().lower()
    sl_clean = (size_label or "").strip().lower()
    if fp_clean:
        parts.append(fp_clean)
    if sl_clean and sl_clean not in ("full", ""):
        parts.append(sl_clean)
    return " ".join(parts) if parts else "default"


# ---------------------------------------------------------------------------
# GET /api/models/tree/state
# ---------------------------------------------------------------------------


@router.get("/state")
def get_models_tree_state(
    gallery_keys: Optional[str] = Query(
        None,
        description="Comma-separated gallery image keys for gallery scope counts",
    ),
    selected_keys: Optional[str] = Query(
        None,
        description="Comma-separated selected image keys for selected scope counts",
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the models tree hierarchy with scope-aware usage counts.

    Response shape::

        {
          "hierarchy": {
            "checkpoint": [
              {
                "key": "sdxl",
                "label": "SDXL",
                "children": [
                  {
                    "key": "12345",      # civitai_model_id
                    "label": "Model Name",
                    "children": [
                      {
                        "key": "67890",  # civitai_version_id
                        "label": "Version Name",
                        "children": [
                          {
                            "key": "fp16",
                            "label": "fp16",
                            "children": []
                          }
                        ]
                      }
                    ]
                  }
                ]
              }
            ],
            "lora": [...]
          },
          "usage_by_scope": {
            "all":       {"checkpoint": {"sdxl.12345.67890.fp16": 5, ...}, "lora": {...}},
            "gallery":   {"checkpoint": {...}, "lora": {...}},
            "selected":  {"checkpoint": {...}, "lora": {...}}
          }
        }
    """
    # 1. Build the hierarchy from the CivitAI catalog
    hierarchy = _build_hierarchy(db)

    # 2. Build usage maps for each scope
    # "all" scope: every image
    all_usage = _build_usage_map(db, None)

    # "gallery" scope
    gallery_usage = {"checkpoint": {}, "lora": {}}
    if gallery_keys:
        gk_list = [k.strip() for k in gallery_keys.split(",") if k.strip()]
        g_ids = _image_id_set_from_keys(db, gk_list)
        gallery_usage = _build_usage_map(db, g_ids)

    # "selected" scope
    selected_usage = {"checkpoint": {}, "lora": {}}
    if selected_keys:
        sk_list = [k.strip() for k in selected_keys.split(",") if k.strip()]
        s_ids = _image_id_set_from_keys(db, sk_list)
        selected_usage = _build_usage_map(db, s_ids)

    return {
        "hierarchy": hierarchy,
        "usage_by_scope": {
            "all": all_usage,
            "gallery": gallery_usage,
            "selected": selected_usage,
        },
    }


def _build_hierarchy(db: Session) -> dict[str, list[dict]]:
    """Build base_model → model → version → precision tree from catalog.

    Returns ``{"checkpoint": [...], "lora": [...]}`` where each value is a
    nested list of hierarchy nodes.
    """
    # Load all models with versions and files in two queries to avoid
    # massive joined cartesian products.
    models = (
        db.query(CivitaiModel)
        .filter(CivitaiModel.type.in_(["Checkpoint", "LORA"]))
        .all()
    )

    model_ids = [m.civitai_model_id for m in models]
    if not model_ids:
        return {"checkpoint": [], "lora": []}

    versions = (
        db.query(CivitaiModelVersion)
        .filter(CivitaiModelVersion.civitai_model_id.in_(model_ids))
        .all()
    )

    version_ids = [v.civitai_version_id for v in versions]
    files_map: dict[int, list[CivitaiModelVersionFile]] = {}
    if version_ids:
        files = (
            db.query(CivitaiModelVersionFile)
            .filter(CivitaiModelVersionFile.civitai_version_id.in_(version_ids))
            .all()
        )
        for f in files:
            files_map.setdefault(f.civitai_version_id, []).append(f)

    # Group versions by model
    versions_by_model: dict[int, list[CivitaiModelVersion]] = {}
    for v in versions:
        versions_by_model.setdefault(v.civitai_model_id, []).append(v)

    # Build tree per type pane
    result: dict[str, list[dict]] = {"checkpoint": [], "lora": []}
    # Intermediate structure: {pane: {base_key: {model_id: {version_id: [precision]}}}}
    tree: dict[str, dict[str, dict[int, dict[int, list[str]]]]] = {
        "checkpoint": defaultdict(lambda: defaultdict(lambda: defaultdict(list))),
        "lora": defaultdict(lambda: defaultdict(lambda: defaultdict(list))),
    }

    # Labels for display
    base_labels: dict[str, str] = {}
    model_names: dict[int, str] = {}
    version_names: dict[int, str] = {}

    for m in models:
        pane = "checkpoint" if m.type == "Checkpoint" else "lora"
        model_names[m.civitai_model_id] = m.name
        for v in versions_by_model.get(m.civitai_model_id, []):
            base_key = _normalize_base_model(v.base_model)
            # Preserve the original label for display
            if base_key not in base_labels and v.base_model:
                base_labels[base_key] = v.base_model
            version_names[v.civitai_version_id] = v.name

            vfiles = files_map.get(v.civitai_version_id, [])
            if vfiles:
                for f in vfiles:
                    prec = _precision_label(f)
                    tree[pane][base_key][m.civitai_model_id][v.civitai_version_id].append(prec)
            else:
                tree[pane][base_key][m.civitai_model_id][v.civitai_version_id].append("default")

    # Convert to nested dicts for JSON
    for pane in ("checkpoint", "lora"):
        for base_key, models_dict in sorted(tree[pane].items()):
            base_node: dict[str, Any] = {
                "key": base_key,
                "label": base_labels.get(base_key, base_key.upper()),
                "children": [],
            }
            for mid, versions_dict in sorted(models_dict.items()):
                model_node: dict[str, Any] = {
                    "key": str(mid),
                    "label": model_names.get(mid, f"Model {mid}"),
                    "children": [],
                }
                for vid, precisions in sorted(versions_dict.items()):
                    version_node: dict[str, Any] = {
                        "key": str(vid),
                        "label": version_names.get(vid, f"Version {vid}"),
                        "children": [],
                    }
                    seen_precisions = set()
                    for prec in precisions:
                        if prec not in seen_precisions:
                            seen_precisions.add(prec)
                            version_node["children"].append({
                                "key": prec,
                                "label": prec,
                                "children": [],
                            })
                    model_node["children"].append(version_node)
                base_node["children"].append(model_node)
            result[pane].append(base_node)

    return result
