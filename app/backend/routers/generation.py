"""Generation-related routes (templates, A1111 bridge, ComfyUI, parity lab).

TODO: Extract from main.py (lines ~13885–17236).

Route prefixes:
  GET  /generation/templates/
  POST /generation/templates/
  GET  /generation/templates/{template_id}
  POST /api/a1111/bridge/analyze
  POST /api/a1111/bridge/save
  POST /api/comfy/generate-compare
  GET  /api/tasks/
  GET  /api/tasks/{task_id}
  ...
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["generation"])

# TODO: routes to be extracted from main.py
