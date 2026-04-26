"""FastAPI middleware registration for AtelierAI.

Call ``configure_middleware(app)`` once from the application factory
(``main.py``) before any routes are registered.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware


def configure_middleware(app: FastAPI) -> None:
    """Attach all standard middleware to *app* in priority order."""
    # Compress large JSON responses (e.g. taxonomy tree payloads).
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    # Allow local cross-origin fetches so the gallery UI can run from file://.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
    )
