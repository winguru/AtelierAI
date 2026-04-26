"""Image browsing, metadata, and management routes.

TODO: Extract from main.py (lines ~17287–19059).

Route prefixes:
  GET  /images/
  GET  /images/{image_id}
  PUT  /images/{image_id}
  GET  /images/{image_id}/thumbnail
  GET  /images/{image_id}/media
  POST /images/scan
  ...
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["images"])

# TODO: routes to be extracted from main.py
