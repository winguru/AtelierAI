#!/usr/bin/env python3
"""CivitAI Search Client — dual-backend proxy (Meilisearch + REST API).

**Meilisearch** (preferred): Full-text search with tag/NSFW filtering,
facets, and offset pagination against ``search-new.civitai.com``.
Requires ``CIVITAI_MEILISEARCH_KEY`` (static public key; auto-scraped
from the Civitai frontend JS bundle when absent).

**REST API** (fallback): ``GET /api/v1/images`` — no auth required but
supports fewer filters and cursor-based pagination only.

Refer to ``civitai_search_spec.yaml`` and ``CIVITAI_API_REFERENCE.md`` for
full endpoint documentation.
"""

from __future__ import annotations

import os
import re
import threading
from importlib import import_module
from typing import Any, Optional

import requests

from .http_client import CivitaiRequestError

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


def _get_config_value(name: str) -> Any:
    """Load a config value from the first available config module."""
    for module_name in (
        "atelierai.config",
        "backend.config",
        "config",
    ):
        try:
            mod = import_module(module_name)
        except ModuleNotFoundError:
            continue
        value = getattr(mod, name, None)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Search client
# ---------------------------------------------------------------------------

_SEARCH_BASE_URL = "https://search-new.civitai.com"
_DEFAULT_INDEX = "images_v6"
_DEFAULT_FACETS = [
    "aspectRatio",
    "baseModel",
    "createdAtUnix",
    "tagNames",
    "techniqueNames",
    "toolNames",
    "type",
    "user.username",
]
_DEFAULT_LIMIT = 51
_DEFAULT_SORT = "stats.reactionCountAllTime:desc"

_REST_API_BASE = "https://civitai.com/api/v1"

# Map Meilisearch sort keys to REST API sort param values.
_SORT_MAP = {
    "stats.reactionCountAllTime:desc": "Most Reactions",
    "stats.commentCountAllTime:desc": "Most Comments",
    "createdAt:desc": "Newest",
    "stats.collectedCountAllTime:desc": "Most Collected",
}

# Regex to extract NEXT_PUBLIC_SEARCH_CLIENT_KEY from Civitai's _app JS chunk.
_MEILI_KEY_RE = re.compile(
    r'NEXT_PUBLIC_SEARCH_CLIENT_KEY:"([0-9a-f]{64})"'
)


class CivitaiSearchClient:
    """High-level client supporting both Meilisearch and REST API backends.

    **Meilisearch** (preferred) offers tag/NSFW filtering, facets, and offset
    pagination.  Requires ``CIVITAI_MEILISEARCH_KEY`` (static public key
    embedded in the Civitai frontend; auto-scraped when absent).

    **REST API** (fallback) uses ``/api/v1/images`` — no key required but
    supports fewer filters and cursor-based pagination only.

    Usage::

        from atelierai.civitai.civitai_search import CivitaiSearchClient

        client = CivitaiSearchClient()
        results = client.search_images(
            query="bikini",
            tags=["bikini"],
            sort_by="stats.reactionCountAllTime:desc",
            limit=40,
            offset=0,
        )
    """

    def __init__(
        self,
        *,
        meili_key: Optional[str] = None,
        session_cookie: Optional[str] = None,
        timeout: float = 30.0,
        backend: str = "auto",  # "auto" | "meilisearch" | "rest"
    ) -> None:
        self._meili_key = meili_key
        self._session_cookie = session_cookie
        self._timeout = timeout
        self._backend = backend

    # ------------------------------------------------------------------
    # Key acquisition
    # ------------------------------------------------------------------

    def _get_meili_key(self) -> Optional[str]:
        """Return a Meilisearch search key, trying multiple sources.

        Priority:
        1. Explicit ``meili_key`` constructor argument.
        2. ``CIVITAI_MEILISEARCH_KEY`` config / env var.
        3. Auto-scrape from Civitai frontend JS bundle (cached).
        """
        if self._meili_key:
            return self._meili_key

        # Try config module or environment.
        key = (
            _get_config_value("CIVITAI_MEILISEARCH_KEY")
            or os.environ.get("CIVITAI_MEILISEARCH_KEY")
        )
        if key:
            self._meili_key = key
            return key

        # Auto-scrape from the Civitai _app chunk (cached in module-level var).
        key = _scrape_meili_key()
        if key:
            self._meili_key = key
            return key

        return None

    # ------------------------------------------------------------------
    # Public search dispatcher
    # ------------------------------------------------------------------

    def search_images(
        self,
        *,
        query: str = "",
        tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None,
        sort_by: str = _DEFAULT_SORT,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        nsfw_levels: Optional[list[int]] = None,
        base_models: Optional[list[str]] = None,
        exclude_poi: bool = True,
        exclude_minor: bool = True,
        username: Optional[str] = None,
        facets: Optional[list[str]] = None,
        extra_filters: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Search for images using Meilisearch (preferred) or REST API fallback.

        Returns a normalised result dict with keys:
        ``hits``, ``estimatedTotalHits``, ``offset``, ``limit``,
        ``processingTimeMs``, ``facetDistribution``, ``facetStats``,
        ``backend`` (``"meilisearch"`` or ``"rest"``).
        """
        backend = self._backend

        if backend in ("auto", "meilisearch"):
            key = self._get_meili_key()
            if key:
                try:
                    result = self._meili_search(
                        key=key,
                        query=query,
                        tags=tags,
                        exclude_tags=exclude_tags,
                        sort_by=sort_by,
                        limit=limit,
                        offset=offset,
                        nsfw_levels=nsfw_levels,
                        base_models=base_models,
                        exclude_poi=exclude_poi,
                        exclude_minor=exclude_minor,
                        username=username,
                        facets=facets,
                        extra_filters=extra_filters,
                    )
                    result["backend"] = "meilisearch"
                    return result
                except CivitaiRequestError:
                    if backend == "meilisearch":
                        raise
                    # Fall through to REST API.

        # REST API path.
        result = self._rest_search(
            query=query,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            nsfw_levels=nsfw_levels,
            base_models=base_models,
            username=username,
        )
        result["backend"] = "rest"
        return result

    # ------------------------------------------------------------------
    # Meilisearch backend
    # ------------------------------------------------------------------

    def _meili_search(
        self,
        *,
        key: str,
        query: str,
        tags: Optional[list[str]],
        exclude_tags: Optional[list[str]],
        sort_by: str,
        limit: int,
        offset: int,
        nsfw_levels: Optional[list[int]],
        base_models: Optional[list[str]],
        exclude_poi: bool,
        exclude_minor: bool,
        username: Optional[str],
        facets: Optional[list[str]],
        extra_filters: Optional[list[str]],
    ) -> dict[str, Any]:
        """Execute a Meilisearch ``/multi-search`` request."""
        filters = _build_meili_filters(
            tags=tags,
            exclude_tags=exclude_tags,
            nsfw_levels=nsfw_levels,
            base_models=base_models,
            exclude_poi=exclude_poi,
            exclude_minor=exclude_minor,
            username=username,
            extra_filters=extra_filters,
        )

        search_query = {
            "q": query,
            "indexUid": _DEFAULT_INDEX,
            "facets": facets or _DEFAULT_FACETS,
            "attributesToRetrieve": ["*"],
            "attributesToHighlight": [],
            "highlightPreTag": "__ais-highlight__",
            "highlightPostTag": "__/ais-highlight__",
            "limit": limit,
            "offset": offset,
            "filter": filters,
            "sort": [sort_by],
        }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Origin": "https://civitai.com",
            "Referer": "https://civitai.com/",
            "x-meilisearch-client": (
                "Meilisearch instant-meilisearch (v0.13.5) ; "
                "Meilisearch JavaScript (v0.34.0)"
            ),
        }

        url = f"{_SEARCH_BASE_URL}/multi-search"
        payload = {"queries": [search_query]}

        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise CivitaiRequestError(
                f"Meilisearch request failed: {exc}", retryable=True,
            ) from exc

        if resp.status_code != 200:
            raise CivitaiRequestError(
                f"Meilisearch returned HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        body = resp.json()
        results = body.get("results", [])

        if not results:
            return {
                "hits": [],
                "estimatedTotalHits": 0,
                "offset": offset,
                "limit": 0,
                "processingTimeMs": 0,
                "facetDistribution": None,
                "facetStats": None,
            }

        return results[0]

    # ------------------------------------------------------------------
    # REST API backend
    # ------------------------------------------------------------------

    def _rest_search(
        self,
        *,
        query: str = "",
        sort_by: str = _DEFAULT_SORT,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
        nsfw_levels: Optional[list[int]] = None,
        base_models: Optional[list[str]] = None,
        username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search via ``GET /api/v1/images`` (no auth required)."""
        params: dict[str, Any] = {"limit": limit}

        if query:
            params["q"] = query

        # Map Meilisearch sort key to REST sort value.
        params["sort"] = _SORT_MAP.get(sort_by, "Most Reactions")

        if username:
            params["username"] = username

        if base_models:
            # REST API accepts a single baseModel; use the first.
            params["baseModel"] = base_models[0]

        # NSFW: if any level ≥ 4 present, include NSFW.
        if nsfw_levels:
            has_nsfw = any(lv >= 4 for lv in nsfw_levels)
            params["nsfw"] = "true" if has_nsfw else "false"

        url = f"{_REST_API_BASE}/images"

        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise CivitaiRequestError(
                f"REST search request failed: {exc}", retryable=True,
            ) from exc

        if resp.status_code != 200:
            raise CivitaiRequestError(
                f"REST search returned HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        body = resp.json()
        items = body.get("items", [])
        metadata = body.get("metadata", {})

        # Transform REST items into Meilisearch-compatible hits.
        hits = [_rest_item_to_meili_hit(item) for item in items]

        return {
            "hits": hits,
            "estimatedTotalHits": None,  # REST API doesn't expose total
            "offset": offset,
            "limit": limit,
            "processingTimeMs": 0,
            "facetDistribution": None,
            "facetStats": None,
            "nextCursor": metadata.get("nextCursor"),
            "nextPage": metadata.get("nextPage"),
        }


# ---------------------------------------------------------------------------
# Module-level key scraper (called once, cached)
# ---------------------------------------------------------------------------

_scraped_key: Optional[str] = None
_scrape_lock = threading.Lock()


def _scrape_meili_key() -> Optional[str]:
    """Scrape the Meilisearch public key from Civitai's frontend JS.

    The key is embedded as ``NEXT_PUBLIC_SEARCH_CLIENT_KEY`` in the ``_app``
    chunk.  Result is cached for the process lifetime.
    """
    global _scraped_key

    if _scraped_key:
        return _scraped_key

    with _scrape_lock:
        if _scraped_key:
            return _scraped_key

        try:
            # Fetch the homepage to discover JS chunk URLs.
            resp = requests.get(
                "https://civitai.com/",
                timeout=10,
                headers={"User-Agent": "AtelierAI/1.0"},
            )
            resp.raise_for_status()

            # Find the _app chunk (may be under pages/ or directly in chunks/).
            app_chunks = re.findall(
                r'src="(/_next/static/chunks/(?:pages/)?_app-[^"]+\.js)"',
                resp.text,
            )
            if not app_chunks:
                return None

            app_url = f"https://civitai.com{app_chunks[0]}"
            app_resp = requests.get(app_url, timeout=10)
            app_resp.raise_for_status()

            match = _MEILI_KEY_RE.search(app_resp.text)
            if match:
                _scraped_key = match.group(1)
                return _scraped_key

        except Exception:
            pass

        return None


# ---------------------------------------------------------------------------
# Filter & mapping helpers
# ---------------------------------------------------------------------------


def _build_meili_filters(
    *,
    tags: Optional[list[str]] = None,
    exclude_tags: Optional[list[str]] = None,
    nsfw_levels: Optional[list[int]] = None,
    base_models: Optional[list[str]] = None,
    exclude_poi: bool = True,
    exclude_minor: bool = True,
    username: Optional[str] = None,
    extra_filters: Optional[list[str]] = None,
) -> list[str]:
    """Build Meilisearch filter expressions from simplified parameters."""
    filters: list[str] = []

    # Tag inclusion filters.
    for tag in tags or []:
        filters.append(f'"tagNames"="{tag}"')

    # Tag exclusion filters.
    for tag in exclude_tags or []:
        filters.append(f'"tagNames"!="{tag}"')

    # NSFW level filter.
    if nsfw_levels:
        level_expr = " OR ".join(f"nsfwLevel={lv}" for lv in nsfw_levels)
        filters.append(f"({level_expr})")

    # Base model filter.
    if base_models:
        model_expr = " OR ".join(f'baseModel="{m}"' for m in base_models)
        filters.append(f"({model_expr})")

    # POI / minor exclusion.
    poi_minor_parts: list[str] = []
    if exclude_poi and not username:
        poi_minor_parts.append("poi != true")
    elif exclude_poi and username:
        poi_minor_parts.append(f"(poi != true OR user.username = {username})")
    if exclude_minor:
        poi_minor_parts.append("minor != true")
    if poi_minor_parts:
        filters.append(" AND ".join(poi_minor_parts))

    # Extra raw filters (power-user passthrough).
    for ef in extra_filters or []:
        filters.append(ef)

    return filters


def _rest_item_to_meili_hit(item: dict[str, Any]) -> dict[str, Any]:
    """Map a REST API ``/api/v1/images`` item to a Meilisearch-like hit."""
    meta = item.get("meta") or {}
    stats = item.get("stats") or {}

    return {
        "id": item.get("id"),
        "url": item.get("url", ""),
        "hash": item.get("hash", ""),
        "width": item.get("width"),
        "height": item.get("height"),
        "nsfwLevel": item.get("nsfwLevel"),
        "type": item.get("type", "image"),
        "baseModel": item.get("baseModel"),
        "username": item.get("username", ""),
        "postId": item.get("postId"),
        "createdAt": item.get("createdAt", ""),
        "browsingLevel": item.get("browsingLevel"),
        "stats": {
            "reactionCountAllTime": (
                stats.get("likeCount", 0)
                + stats.get("heartCount", 0)
                + stats.get("laughCount", 0)
                + stats.get("cryCount", 0)
            ),
            "commentCountAllTime": stats.get("commentCount", 0),
            "collectedCountAllTime": 0,
        },
        "meta": meta,
        "prompt": meta.get("prompt", ""),
        "tagNames": [],  # REST API doesn't return tag names in list form.
        "generationProcess": meta.get("Version") or "Unknown",
    }
