"""Concept taxonomy and tag management routes.

TODO: Extract from main.py (lines ~20328–22226).

Route prefixes:
  GET  /taxonomy/tree/state
  GET  /taxonomy/tree/tags/{source}
  POST /taxonomy/concepts/
  PUT  /taxonomy/concepts/{concept_id}
  ...
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["taxonomy"])

# TODO: routes to be extracted from main.py
