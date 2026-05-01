# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/parity-workbench.md
# ──────────────────────────────────────────────────────────────────────────────
"""Generation-related routes.

Extracted from main.py:
  - Lines ~13842–17286: lab pages, ComfyUI proxy, expression-sets, tasks,
    generation-prototype, A1111 bridge, parity workbench, dataset quality,
    ComfyUI generate-and-compare, generation templates, perceptual lab,
    model prototype, task retry variants.

All non-trivial handlers delegate to the corresponding main.py function via
lazy import; they will be moved to services/generation_service.py in a future
phase.

Self-contained handlers:
  - Lab page FileResponse routes
  - /tasks/ CRUD (uses task_manager directly)
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from sqlalchemy.orm import Session

from core.lifespan import task_manager
from database import get_db

router = APIRouter(tags=["generation"])


# ---------------------------------------------------------------------------
# ComfyUI proxy
# ---------------------------------------------------------------------------


@router.post("/comfyui-proxy/upload/image")
async def comfyui_proxy_upload_image(
    request: Request,
    target: str = Query(..., description="ComfyUI base URL, e.g. http://127.0.0.1:8188"),
):
    from main import comfyui_proxy_upload_image as _impl  # noqa: PLC0415

    return await _impl(request=request, target=target)


@router.post("/comfyui-proxy/prompt")
async def comfyui_proxy_prompt(
    target: str = Query(..., description="ComfyUI base URL"),
    prompt: str = Body(..., media_type="application/json"),
):
    from main import comfyui_proxy_prompt as _impl  # noqa: PLC0415

    return await _impl(target=target, prompt=prompt)


# ---------------------------------------------------------------------------
# Expression sets
# ---------------------------------------------------------------------------


@router.get("/expression-sets")
def get_expression_sets():
    from main import get_expression_sets as _impl  # noqa: PLC0415

    return _impl()


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------


@router.get("/tasks/", response_model=list[dict])
def list_background_tasks(limit: int = 20):
    capped_limit = max(1, min(int(limit), 50))
    return task_manager.list_tasks(limit=capped_limit)


@router.get("/tasks/{task_id}", response_model=dict)
def get_background_task(task_id: str):
    try:
        return task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/cancel", response_model=dict)
def cancel_background_task(task_id: str):
    try:
        return task_manager.cancel_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/tasks/{task_id}/retry_failed",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_failed_items_from_task(task_id: str):
    from main import retry_failed_items_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


@router.post(
    "/tasks/{task_id}/retry-missing",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_missing_failures_from_task(task_id: str):
    from main import retry_missing_failures_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


@router.post(
    "/tasks/{task_id}/retry-temporary",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def retry_temporary_failures_from_task(task_id: str):
    from main import retry_temporary_failures_from_task as _impl  # noqa: PLC0415

    return _impl(task_id=task_id)


# ---------------------------------------------------------------------------
# Generation prototype
# ---------------------------------------------------------------------------


@router.get("/generation-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_generation_prototype(image_id: int):
    from main import get_civitai_generation_prototype as _impl  # noqa: PLC0415

    return _impl(image_id=image_id)


@router.get("/images/{file_hash}/generation-prototype", response_model=dict)
def get_local_generation_prototype(file_hash: str, db: Session = Depends(get_db)):
    from main import get_local_generation_prototype as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-workspace", response_model=dict
)
def get_civitai_generation_comfy_workspace(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-workflow-raw", response_model=dict
)
def get_civitai_generation_comfy_workflow_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_workflow_raw as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/generation-prototype/civitai/{image_id}/comfy-prompt-raw", response_model=dict
)
def get_civitai_generation_comfy_prompt_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_civitai_generation_comfy_prompt_raw as _impl  # noqa: PLC0415

    return _impl(
        image_id=image_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-workspace", response_model=dict
)
def get_local_generation_comfy_workspace(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-workflow-raw", response_model=dict
)
def get_local_generation_comfy_workflow_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_workflow_raw as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


@router.get(
    "/images/{file_hash}/generation-prototype/comfy-prompt-raw", response_model=dict
)
def get_local_generation_comfy_prompt_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    from main import get_local_generation_comfy_prompt_raw as _impl  # noqa: PLC0415

    return _impl(
        file_hash=file_hash,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
        db=db,
    )


# ---------------------------------------------------------------------------
# A1111 bridge / parity workbench
# ---------------------------------------------------------------------------


@router.post("/generation-prototype/a1111-bridge/analyze", response_model=dict)
def analyze_a1111_bridge(request: Request, db: Session = Depends(get_db)):
    from main import analyze_a1111_bridge as _impl  # noqa: PLC0415

    return _impl(request=request, db=db)


@router.post(
    "/generation-prototype/parity-workbench/candidate-audit", response_model=dict
)
@router.post("/generation-audit/analyze", response_model=dict)
def analyze_parity_candidate(request: Request, db: Session = Depends(get_db)):
    from main import analyze_parity_candidate as _impl  # noqa: PLC0415

    return _impl(request=request, db=db)


@router.post("/generation-prototype/a1111-bridge/save-analysis", response_model=dict)
def save_a1111_bridge_analysis(payload: Any = Body(...)):
    from main import save_a1111_bridge_analysis as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get(
    "/generation-prototype/a1111-bridge/dataset-quality", response_model=dict
)
def get_a1111_bridge_dataset_quality_report():
    from main import get_a1111_bridge_dataset_quality_report as _impl  # noqa: PLC0415

    return _impl()


# ---------------------------------------------------------------------------
# ComfyUI generate-and-compare
# ---------------------------------------------------------------------------


@router.post("/generation-prototype/comfy/generate-and-compare", response_model=dict)
def generate_and_compare_comfy_workspace(payload: Any = Body(...)):
    from main import generate_and_compare_comfy_workspace as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get("/generation-prototype/comfy/attempts", response_model=dict)
def list_comfy_generation_match_attempts(
    limit: int = Query(default=20, ge=1, le=200),
):
    from main import list_comfy_generation_match_attempts as _impl  # noqa: PLC0415

    return _impl(limit=limit)


# ---------------------------------------------------------------------------
# Generation templates
# ---------------------------------------------------------------------------


@router.post("/generation-templates/import-workspace", response_model=dict)
def import_generation_template_workspace(payload: Any = Body(...)):
    from main import import_generation_template_workspace as _impl  # noqa: PLC0415

    return _impl(payload=payload)


@router.get("/generation-templates", response_model=dict)
def list_generation_templates(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import list_generation_templates as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        db=db,
    )


@router.get("/generation-templates/token-preview", response_model=dict)
def preview_generation_template_tokens(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import preview_generation_template_tokens as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        db=db,
    )


@router.get("/generation-templates/{template_id}", response_model=dict)
def get_generation_template(template_id: int, db: Session = Depends(get_db)):
    from main import get_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, db=db)


@router.put("/generation-templates/{template_id}", response_model=dict)
def update_generation_template(
    template_id: int, payload: Any = Body(...), db: Session = Depends(get_db)
):
    from main import update_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, payload=payload, db=db)


@router.delete("/generation-templates/{template_id}", response_model=dict)
def delete_generation_template(template_id: int, db: Session = Depends(get_db)):
    from main import delete_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, db=db)


@router.post("/generation-templates/{template_id}/resolve", response_model=dict)
def resolve_generation_template(
    template_id: int, payload: Any = Body(default=None), db: Session = Depends(get_db)
):
    from main import resolve_generation_template as _impl  # noqa: PLC0415

    return _impl(template_id=template_id, payload=payload, db=db)


# ---------------------------------------------------------------------------
# Perceptual lab
# ---------------------------------------------------------------------------


@router.get("/images/{file_hash}/perceptual-lab/analyze", response_model=dict)
def analyze_local_image_perceptual_hashes(
    file_hash: str, db: Session = Depends(get_db)
):
    from main import analyze_local_image_perceptual_hashes as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get("/images/{file_hash}/perceptual-lab/similarity", response_model=dict)
def search_perceptual_similarity(
    file_hash: str,
    threshold: float = Query(default=0.9, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    from main import search_perceptual_similarity as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, threshold=threshold, limit=limit, db=db)


# ---------------------------------------------------------------------------
# Model prototype
# ---------------------------------------------------------------------------


@router.get("/model-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_model_prototype(image_id: int):
    from main import get_civitai_model_prototype as _impl  # noqa: PLC0415

    return _impl(image_id=image_id)


@router.get("/images/{file_hash}/model-prototype", response_model=dict)
def get_local_model_prototype(file_hash: str, db: Session = Depends(get_db)):
    from main import get_local_model_prototype as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.get("/model-prototype/catalog", response_model=dict)
def get_model_catalog_prototype(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    from main import get_model_catalog_prototype as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )


@router.get("/model-prototype/local-match-preview", response_model=dict)
def get_model_prototype_local_match_preview(
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
):
    from main import get_model_prototype_local_match_preview as _impl  # noqa: PLC0415

    return _impl(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
    )


@router.post("/model-prototype/local-model-download", response_model=dict)
def trigger_model_prototype_local_model_download(payload: dict = Body(...)):
    from main import trigger_model_prototype_local_model_download as _impl  # noqa: PLC0415

    return _impl(payload=payload)
