# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/model-reference.md
# ──────────────────────────────────────────────────────────────────────────────
"""Models tree hierarchy API routes.

Provides endpoints for the /frontend/models.html page:
- POST /api/models/tree/state  — hierarchy + scope-aware usage counts
- GET /api/models/tree/data   — detailed model/versions for a given type pane

The tree shows 4 hierarchy levels: base_model → model → version → precision,
split across two type panes: checkpoints and loras.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import (
    CivitaiModel,
    CivitaiModelVersion,
    CivitaiModelVersionFile,
    ImageModel,
    ModelObservation,
)
from services.model_reference_service import ModelReferenceService
from services.query_model import GalleryFilter, ModelsTreeStateRequest

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


def _resolve_gallery_usage(
    db: Session,
    gallery_filter: GalleryFilter,
    search: Optional[str],
) -> dict[str, dict[str, int]]:
    """Resolve gallery-scope usage counts via the shared constrained-IDs cache.

    Reuses the same cache populated by ``POST /api/query`` so that the
    expensive filter-resolution is only done once per unique filter.
    """
    from services.query_model import filter_cache_key  # noqa: PLC0415
    from utils.cache import (  # noqa: PLC0415
        _build_search_cache_key,
        _search_cache_get,
    )

    # Try the shared constrained-IDs cache first.
    fkey = filter_cache_key(gallery_filter, search)
    cids_cache_key = _build_search_cache_key(
        "constrained_ids",
        payload={"filter_key": fkey},
    )
    cached_cids = _search_cache_get(cids_cache_key)

    constrained_ids: Optional[set[int]] = None
    if cached_cids is not None:
        if isinstance(cached_cids, frozenset):
            constrained_ids = set(cached_cids)
        elif cached_cids != "unfiltered":
            constrained_ids = cached_cids
        # "unfiltered" → constrained_ids stays None (all images)
    else:
        # Cache miss — resolve via GalleryQuery (which also caches).
        # Import here to avoid circular imports at module level.
        from main import _make_gallery_query  # type: ignore[import-untyped]  # noqa: PLC0415

        gq = _make_gallery_query(db)
        constrained_ids = gq._resolve_filter(gallery_filter, search)

    return _build_usage_map(db, constrained_ids)


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

    Counts are sourced from the ``model_observations`` relational table joined
    with ``civitai_model_versions`` for hierarchy components.
    """
    if image_ids is not None and not image_ids:
        return {"checkpoint": {}, "lora": {}}

    # Single query: model_observations JOIN civitai_model_versions
    # Returns one row per (resource_type, base_model, civitai_model_id, civitai_version_id, image_id).
    query = db.query(
        ModelObservation.resource_type,
        CivitaiModelVersion.base_model,
        CivitaiModelVersion.civitai_model_id,
        CivitaiModelVersion.civitai_version_id,
        ModelObservation.image_id,
    ).join(
        CivitaiModelVersion,
        CivitaiModelVersion.civitai_version_id == ModelObservation.civitai_version_id,
    )
    if image_ids is not None:
        query = query.filter(ModelObservation.image_id.in_(image_ids))

    rows = query.all()

    # Accumulate image ID sets for deduplication at each hierarchy level.
    base_images: dict[str, dict[str, set[int]]] = {
        "checkpoint": defaultdict(set),
        "lora": defaultdict(set),
    }
    model_images: dict[str, dict[str, set[int]]] = {
        "checkpoint": defaultdict(set),
        "lora": defaultdict(set),
    }
    version_images: dict[str, dict[str, set[int]]] = {
        "checkpoint": defaultdict(set),
        "lora": defaultdict(set),
    }

    for resource_type, raw_base, model_id, version_id, img_id in rows:
        pane = "checkpoint" if resource_type == "checkpoint" else "lora"
        base_key = _normalize_base_model(raw_base)
        model_key = f"{base_key}.{model_id}"
        version_key = f"{model_key}.{version_id}"

        version_images[pane][version_key].add(img_id)
        model_images[pane][model_key].add(img_id)
        base_images[pane][base_key].add(img_id)

    # Build final usage map with unique counts at all three levels.
    usage: dict[str, dict[str, int]] = {"checkpoint": {}, "lora": {}}
    for pane in ("checkpoint", "lora"):
        for key, ids in version_images[pane].items():
            usage[pane][key] = len(ids)
        for key, ids in model_images[pane].items():
            usage[pane][key] = len(ids)
        for key, ids in base_images[pane].items():
            usage[pane][key] = len(ids)

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
# POST /api/models/tree/state
# ---------------------------------------------------------------------------


@router.post("/state")
def get_models_tree_state(
    body: ModelsTreeStateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return the models tree hierarchy with scope-aware usage counts.

    Accepts a JSON body with ``filter`` (GalleryFilter) and ``search`` to
    define the "gallery" scope via the shared constrained-IDs cache, instead
    of passing a long list of image keys as a CGI parameter.

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

    # "gallery" scope — resolve via shared constrained-IDs cache
    gallery_usage = _resolve_gallery_usage(db, body.filter, body.search)

    # "selected" scope — resolve from optional image keys
    selected_usage: dict[str, dict[str, int]] = {"checkpoint": {}, "lora": {}}
    if body.selected_keys:
        s_ids = _image_id_set_from_keys(db, body.selected_keys)
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
                    tree[pane][base_key][m.civitai_model_id][
                        v.civitai_version_id
                    ].append(prec)
            else:
                tree[pane][base_key][m.civitai_model_id][v.civitai_version_id].append(
                    "default"
                )

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
                            version_node["children"].append(
                                {
                                    "key": prec,
                                    "label": prec,
                                    "children": [],
                                }
                            )
                    model_node["children"].append(version_node)
                base_node["children"].append(model_node)
            result[pane].append(base_node)

    return result
