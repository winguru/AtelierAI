# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""CivitAI data/backfill routes.

Extracted from main.py (lines ~20135–20157).

Routes:
  POST /civitai/backfill/nsfw-levels      (202 Accepted)

NOTE: POST /collections/sync/civitai lives in routers/collections.py.

TODO: Move _run_civitai_nsfw_backfill_job
      from main.py (line ~13081) into services/civitai_service.py and
      import it here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from core.lifespan import task_manager
from database import get_db
from schemas import CivitaiNsfwBackfillRequest

router = APIRouter(prefix="/civitai", tags=["civitai-api"])


@router.post(
    "/backfill/nsfw-levels",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def backfill_civitai_nsfw_levels(
    payload: CivitaiNsfwBackfillRequest,
    db: Session = Depends(get_db),
):
    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    # _run_civitai_nsfw_backfill_job is still defined in main.py; imported
    # lazily to avoid a circular import until it is moved to civitai_service.
    from main import _run_civitai_nsfw_backfill_job  # noqa: PLC0415

    task = task_manager.create_task(
        kind="civitai-nsfw-backfill",
        title="Backfill CivitAI NSFW Levels",
        metadata={
            "limit": payload.limit,
            "reimport_if_missing": payload.reimport_if_missing,
        },
        runner=lambda context: _run_civitai_nsfw_backfill_job(
            context,
            limit=payload.limit,
            reimport_if_missing=payload.reimport_if_missing,
        ),
    )

    return {
        "message": "CivitAI NSFW backfill task queued.",
        "task": task,
    }
