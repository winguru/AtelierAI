"""Collection management routes.

TODO: Extract from main.py (lines ~19518–19731, ~22629–23380).

Route prefixes:
  GET    /collections/
  POST   /collections/
  GET    /collections/{collection_id}
  PUT    /collections/{collection_id}
  DELETE /collections/{collection_id}
  POST   /collections/{collection_id}/sync
  ...
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["collections"])

# TODO: routes to be extracted from main.py
