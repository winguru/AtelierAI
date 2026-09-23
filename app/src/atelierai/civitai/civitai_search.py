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

import json
import logging
import os
import re
import threading
import time
from importlib import import_module
from typing import Any, Optional

import requests

from .http_client import CivitaiRequestError
from .response_archive import CivitaiResponseArchive

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


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


_SEARCH_BASE_URL = (
    _get_config_value("CIVITAI_SEARCH_BASE_URL") or "https://search-new.civitai.com"
)
_DEFAULT_INDEX = "images_v6"
_DEFAULT_FACETS = [
    "aspectRatio",
    "baseModel",
    "createdAtUnix",
    "nsfwLevel",
    "tagNames",
    "techniqueNames",
    "toolNames",
    "type",
    "user.username",
]
_DEFAULT_LIMIT = 51
_DEFAULT_SORT = "stats.reactionCountAllTime:desc"

_REST_API_BASE = (
    _get_config_value("CIVITAI_REST_BASE_URL") or "https://civitai.red/api/v1"
)

# Map Meilisearch sort keys to REST API sort param values.
_SORT_MAP = {
    "stats.reactionCountAllTime:desc": "Most Reactions",
    "stats.commentCountAllTime:desc": "Most Comments",
    "createdAt:desc": "Newest",
    "stats.collectedCountAllTime:desc": "Most Collected",
}

# Regex to extract NEXT_PUBLIC_SEARCH_CLIENT_KEY from Civitai's _app JS chunk.
_MEILI_KEY_RE = re.compile(r'NEXT_PUBLIC_SEARCH_CLIENT_KEY:"([0-9a-f]{64})"')


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
        timeout: float = 60.0,
        backend: str = "auto",  # "auto" | "meilisearch" | "rest"
    ) -> None:
        self._meili_key = meili_key
        self._session_cookie = session_cookie
        self._timeout = timeout
        self._backend = backend
        self._response_archive = CivitaiResponseArchive()

    def _record_search_response(
        self,
        *,
        endpoint: str,
        method: str,
        url: str,
        request: dict[str, Any],
        response: Any = None,
        status_code: int | None = None,
        error: str | None = None,
    ) -> None:
        try:
            self._response_archive.record(
                kind="search",
                endpoint=endpoint,
                method=method,
                url=url,
                request=request,
                response=response,
                status_code=status_code,
                error=error,
            )
        except Exception:
            pass

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
        key = _get_config_value("CIVITAI_MEILISEARCH_KEY") or os.environ.get(
            "CIVITAI_MEILISEARCH_KEY"
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

    def _invalidate_meili_key(self) -> None:
        """Clear the cached Meilisearch key so the next call re-scrapes."""
        global _scraped_key
        self._meili_key = None
        with _scrape_lock:
            _scraped_key = None
        _log.info("Invalidated cached Meilisearch key.")

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
        matching_strategy: Optional[str] = None,
        users: Optional[list[str]] = None,
        viewer_username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search for images using Meilisearch (preferred) or REST API fallback.

        ``viewer_username`` is the logged-in CivitAI user (if known).  It is
        used in the POI filter so the viewer can still see their own POI
        images while hiding everyone else's.

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
                        matching_strategy=matching_strategy,
                        users=users,
                        viewer_username=viewer_username,
                    )
                    result["backend"] = "meilisearch"
                    return result
                except CivitaiRequestError as exc:
                    if backend == "meilisearch":
                        raise
                    is_timeout = (
                        "timed out" in str(exc).lower()
                        or getattr(exc, "status_code", None) == 408
                    )
                    _log.warning(
                        "Meilisearch request failed (offset=%d): %s.%s",
                        offset,
                        exc,
                        (" Attempting retry with longer timeout…"
                         if is_timeout
                         else " Attempting retry with fresh key…"),
                    )

                    # For timeout errors, retry once with a longer timeout
                    # before trying key refresh or REST fallback.
                    if is_timeout:
                        saved_timeout = self._timeout
                        # Brief pause so the upstream has a moment to
                        # recover before we hit it with a longer-timeout
                        # retry (helps with transient 408s).
                        time.sleep(1.0)
                        try:
                            self._timeout = 120.0
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
                                matching_strategy=matching_strategy,
                                users=users,
                                viewer_username=viewer_username,
                            )
                            result["backend"] = "meilisearch"
                            _log.info(
                                "Meilisearch retry succeeded (extended timeout)."
                            )
                            return result
                        except CivitaiRequestError as retry_exc:
                            _log.warning(
                                "Meilisearch retry also failed: %s",
                                retry_exc,
                            )
                        finally:
                            self._timeout = saved_timeout
                    else:
                        # Non-timeout error — invalidate cached key and
                        # retry once with a freshly scraped key.
                        self._invalidate_meili_key()
                        retry_key = self._get_meili_key()
                        if retry_key and retry_key != key:
                            try:
                                result = self._meili_search(
                                    key=retry_key,
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
                                    matching_strategy=matching_strategy,
                                    users=users,
                                    viewer_username=viewer_username,
                                )
                                result["backend"] = "meilisearch"
                                _log.info(
                                    "Meilisearch retry succeeded (fresh key)."
                                )
                                return result
                            except CivitaiRequestError as retry_exc:
                                _log.warning(
                                    "Meilisearch retry also failed: %s",
                                    retry_exc,
                                )
                        else:
                            _log.warning(
                                "No fresh Meilisearch key available for retry; "
                                "proceeding to REST fallback check."
                            )

                    # tRPC search (site's own path) supports cursor
                    # pagination — try it BEFORE the offset-limited REST
                    # fallback. It covers paginated queries too.
                    try:
                        result = self._trpc_search(
                            query=query,
                            tags=tags,
                            sort_by=sort_by,
                            limit=limit,
                            nsfw_levels=nsfw_levels,
                            base_models=base_models,
                            exclude_poi=exclude_poi,
                            exclude_minor=exclude_minor,
                            username=username,
                        )
                        result["backend"] = "trpc"
                        _log.warning(
                            "Meilisearch unusable (index config changed); "
                            "served by tRPC image.getInfinite fallback."
                        )
                        return result
                    except CivitaiRequestError as trpc_exc:
                        _log.warning(
                            "tRPC search fallback also failed: %s", trpc_exc
                        )

                    # REST API does not support offset pagination — falling
                    # back for offset > 0 would return wrong (first-page)
                    # results.  Raise the original error instead.
                    if offset and offset > 0:
                        _log.error(
                            "Refusing REST fallback for paginated query "
                            "(offset=%d): REST API ignores offset and would "
                            "return wrong results.",
                            offset,
                        )
                        raise

                    _log.warning(
                        "Falling back to REST API for first-page query "
                        "(offset=0). Some filters will be lost."
                    )

        # REST API path (only reached for offset=0 after Meilisearch failure).
        # REST API supports only a single username; use the first if provided.
        rest_username = None
        if username:
            rest_username = username
        elif users:
            rest_username = users[0] if users else None

        result = self._rest_search(
            query=query,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
            nsfw_levels=nsfw_levels,
            base_models=base_models,
            username=rest_username,
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
        matching_strategy: Optional[str] = None,
        users: Optional[list[str]] = None,
        viewer_username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Execute a Meilisearch ``/multi-search`` request.

        Usernames are added as ``user.username`` filter expressions inside
        the request body (see :func:`_build_meili_filters`).
        """
        filters = _build_meili_filters(
            tags=tags,
            exclude_tags=exclude_tags,
            nsfw_levels=nsfw_levels,
            base_models=base_models,
            exclude_poi=exclude_poi,
            exclude_minor=exclude_minor,
            username=username,
            extra_filters=extra_filters,
            users=users,
            viewer_username=viewer_username,
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
            "sort": [sort_by] if sort_by else [],
        }
        if matching_strategy in ("last", "all", "frequency"):
            search_query["matchingStrategy"] = matching_strategy

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
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            self._record_search_response(
                endpoint="meilisearch.multi-search",
                method="POST",
                url=url,
                request=payload,
                error=str(exc),
            )
            raise CivitaiRequestError(
                f"Meilisearch request failed: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code != 200:
            self._record_search_response(
                endpoint="meilisearch.multi-search",
                method="POST",
                url=url,
                request=payload,
                response=resp.text[:500],
                status_code=resp.status_code,
                error=f"HTTP {resp.status_code}",
            )
            raise CivitaiRequestError(
                f"Meilisearch returned HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        try:
            body = resp.json()
        except requests.JSONDecodeError as exc:
            self._record_search_response(
                endpoint="meilisearch.multi-search",
                method="POST",
                url=url,
                request=payload,
                response=resp.text[:500],
                status_code=resp.status_code,
                error=f"Invalid JSON: {exc}",
            )
            raise CivitaiRequestError(
                f"Meilisearch returned invalid JSON: {exc}",
                status_code=resp.status_code,
            ) from exc
        self._record_search_response(
            endpoint="meilisearch.multi-search",
            method="POST",
            url=url,
            request=payload,
            response=body,
            status_code=resp.status_code,
        )
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

    def _trpc_search(
        self,
        *,
        query: str = "",
        tags: Optional[list[str]] = None,
        sort_by: str = _DEFAULT_SORT,
        limit: int = _DEFAULT_LIMIT,
        nsfw_levels: Optional[list[int]] = None,
        base_models: Optional[list[str]] = None,
        exclude_poi: bool = True,
        exclude_minor: bool = True,
        username: Optional[str] = None,
        cursor: Optional[int] = None,
    ) -> dict[str, Any]:
        """Search via tRPC ``image.getInfinite`` — the site's own search path.

        As of 2026-09-23 CivitAI dropped Meilisearch filterable attributes
        (the images_v6 index config was wiped: every filter expression now
        400s).  The site's own frontend now searches through
        ``/api/trpc/image.getInfinite`` with a ``query`` param and standard
        browse parameters — this method mirrors that call.  Requires the
        session cookie (same as the browser lane); tags/base-model filters
        are folded into the free-text query (best effort).

        Returns a Meilisearch-shaped result dict with ``backend``-neutral
        keys (``hits``, ``nextCursor``, no facets — facet counts are not
        available from tRPC).
        """
        payload: dict[str, Any] = {
            "query": query or "",
            "authed": True,
            "sort": _SORT_MAP.get(sort_by, "Most Reactions"),
            "period": "AllTime",
            "browsingLevel": max(nsfw_levels or [31]),
            "include": ["cosmetics"],
            "limit": max(1, min(int(limit), 200)),
        }
        if cursor is not None:
            payload["cursor"] = cursor
        if tags:
            payload["query"] = " ".join(filter(None, [query or "", *tags])).strip()
        if base_models:
            payload["baseModels"] = base_models

        url = (
            f"https://civitai.red/api/trpc/image.getInfinite"
            f"?input={requests.utils.quote(json.dumps({'json': payload}))}"
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://civitai.red",
            "Referer": "https://civitai.red/",
        }
        cookie = self._session_cookie
        if cookie:
            # The site's session cookie is __Secure-civ-token (JWT); older
            # fallbacks kept civitai_session_token. Send the current name.
            headers["Cookie"] = f"__Secure-civ-token={cookie}"

        try:
            resp = requests.get(url, headers=headers, timeout=self._timeout)
        except requests.RequestException as exc:
            raise CivitaiRequestError(
                f"tRPC search request failed: {exc}", retryable=True
            ) from exc

        self._record_search_response(
            endpoint="trpc.image.getInfinite",
            method="GET",
            url=url,
            request=payload,
            response=resp.text[:2000],
            status_code=resp.status_code,
        )
        if resp.status_code != 200:
            raise CivitaiRequestError(
                f"tRPC search returned HTTP {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise CivitaiRequestError(
                f"tRPC search returned invalid JSON: {exc}",
                status_code=resp.status_code,
            ) from exc

        parsed = self._trpc_parse_infinite(body)
        hits = [_trpc_item_to_meili_hit(item) for item in parsed["items"]]
        next_cursor = parsed.get("nextCursor")
        return {
            "hits": hits,
            "estimatedTotalHits": None,
            "offset": 0,
            "limit": limit,
            "processingTimeMs": 0,
            "facetDistribution": None,
            "facetStats": None,
            "nextCursor": next_cursor,
            "backend": "trpc",
        }

    @staticmethod
    def _trpc_parse_infinite(body: dict[str, Any]) -> dict[str, Any]:
        """Parse an ``image.getInfinite`` envelope into ``{items, nextCursor}``.

        The columnar double-encoded format (``result.data`` is a stringified
        flat array) is delegated to ``CivitaiAPI._deserialize_trpc_flat_array``
        — the same deserializer the harvester feed path uses.  ``nextCursor``
        is a feed-cursor STRING like ``"feed:27405:12097475"`` (opaque; pass
        back verbatim for the next page), NOT an int.
        """
        if not isinstance(body, dict):
            return {"items": [], "nextCursor": None}
        try:
            from .civitai_api import CivitaiAPI

            parsed = CivitaiAPI._deserialize_trpc_flat_array(body)
            if isinstance(parsed, dict):
                return {
                    "items": [i for i in (parsed.get("items") or []) if isinstance(i, dict)],
                    "nextCursor": parsed.get("nextCursor"),
                }
        except Exception:  # noqa: BLE001, S110 — best-effort parse
            pass
        return {"items": [], "nextCursor": None}

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
            self._record_search_response(
                endpoint="rest.images",
                method="GET",
                url=url,
                request=params,
                error=str(exc),
            )
            raise CivitaiRequestError(
                f"REST search request failed: {exc}",
                retryable=True,
            ) from exc

        if resp.status_code != 200:
            self._record_search_response(
                endpoint="rest.images",
                method="GET",
                url=url,
                request=params,
                response=resp.text[:500],
                status_code=resp.status_code,
                error=f"HTTP {resp.status_code}",
            )
            raise CivitaiRequestError(
                f"REST search returned HTTP {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=resp.status_code >= 500,
            )

        try:
            body = resp.json()
        except requests.JSONDecodeError as exc:
            self._record_search_response(
                endpoint="rest.images",
                method="GET",
                url=url,
                request=params,
                response=resp.text[:500],
                status_code=resp.status_code,
                error=f"Invalid JSON: {exc}",
            )
            raise CivitaiRequestError(
                f"REST search returned invalid JSON: {exc}",
                status_code=resp.status_code,
            ) from exc
        self._record_search_response(
            endpoint="rest.images",
            method="GET",
            url=url,
            request=params,
            response=body,
            status_code=resp.status_code,
        )
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


# Base models that are subject to additional NSFW restrictions on CivitAI.
# Images generated with these models are excluded from higher NSFW levels
# (4, 8, 16, 32) even when the viewer's browsing level allows them, matching
# CivitAI's server-side safety filter.
_RESTRICTED_BASE_MODELS = [
    "SD 3",
    "SD 3.5",
    "SD 3.5 Medium",
    "SD 3.5 Large",
    "SD 3.5 Large Turbo",
    "SDXL Turbo",
    "SVD",
    "SVD XT",
    "Stable Cascade",
    "Ideogram 4.0",
]


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
    users: Optional[list[str]] = None,
    viewer_username: Optional[str] = None,
) -> list[str]:
    """Build Meilisearch filter expressions from simplified parameters.

    Usernames are added as ``user.username`` filter expressions.  A single
    username produces ``user.username = "alice"``; multiple usernames
    produce ``(user.username = "alice" OR user.username = "bob")`` so
    Meilisearch scopes results to images by any of the listed artists.

    ``viewer_username`` is the logged-in CivitAI user (if known).  It is used
    in the POI filter so the viewer can still see their *own* POI images
    while hiding everyone else's — matching CivitAI's server-side behaviour:
    ``(poi != true OR user.username = "<viewer>")``.
    """
    filters: list[str] = []

    # Build the combined user list from legacy ``username`` and ``users``.
    all_users: list[str] = []
    if username:
        all_users.append(username)
    if users:
        all_users.extend(u for u in users if u)

    has_username = bool(all_users)

    # Tag inclusion / exclusion filters.
    for tag in tags or []:
        filters.append(f'"tagNames"="{tag}"')
    for tag in exclude_tags or []:
        filters.append(f'"tagNames"!="{tag}"')

    # POI / minor exclusion.
    poi_filter = _build_poi_minor_filter(
        exclude_poi=exclude_poi,
        exclude_minor=exclude_minor,
        has_username=has_username,
        viewer_username=viewer_username,
    )
    if poi_filter:
        filters.append(poi_filter)

    # NSFW level + restricted-base-model cross-filter.
    nsfw_filter = _build_nsfw_filter(nsfw_levels)
    if nsfw_filter:
        filters.append(nsfw_filter)

    # Explicit base-model filter (user selection).
    if base_models:
        model_expr = " OR ".join(f'baseModel="{m}"' for m in base_models)
        filters.append(f"({model_expr})")

    # Username filter — scope results to images by the listed artist(s).
    if all_users:
        if len(all_users) == 1:
            filters.append(f'user.username = "{all_users[0]}"')
        else:
            or_expr = " OR ".join(
                f'user.username = "{u}"' for u in all_users
            )
            filters.append(f"({or_expr})")

    # Extra raw filters (power-user passthrough).
    for ef in extra_filters or []:
        filters.append(ef)

    return filters


def _build_poi_minor_filter(
    *,
    exclude_poi: bool,
    exclude_minor: bool,
    has_username: bool,
    viewer_username: Optional[str],
) -> str:
    """Build the POI + minor safety filter expression.

    CivitAI combines these into one AND-expression::

        (poi != true OR user.username = "<viewer>") AND (minor != true)

    When the viewer is logged in, their own POI images remain visible.
    When filtering by a specific artist, POI exclusion is skipped entirely
    so that artist's full gallery (including POI) is shown.
    """
    parts: list[str] = []
    if exclude_poi:
        if viewer_username:
            parts.append(
                f'(poi != true OR user.username = "{viewer_username}")'
            )
        elif not has_username:
            parts.append("poi != true")
        # else: has_username and no viewer → skip POI filter
    if exclude_minor:
        parts.append("minor != true")
    return " AND ".join(parts)


def _build_nsfw_filter(
    nsfw_levels: Optional[list[int]],
) -> str:
    """Build the NSFW level + restricted-base-model cross-filter.

    CivitAI applies a safety filter that excludes higher NSFW levels
    (4, 8, 16, 32) for certain base models (SD 3, SD 3.5 variants, SDXL
    Turbo, SVD, Stable Cascade, Ideogram) regardless of browsing level.
    This is combined with the user's NSFW level preference in a single
    AND-expression::

        NOT (nsfwLevel IN [4, 8, 16, 32] AND baseModel IN [...])
        AND (nsfwLevel=1 OR nsfwLevel=2 OR ...)

    Returns an empty string when *nsfw_levels* is empty/None.
    """
    if not nsfw_levels:
        return ""

    parts: list[str] = []

    # Safety cross-filter — only needed when restricted levels are present.
    if any(lv in (4, 8, 16, 32) for lv in nsfw_levels):
        models_quoted = ", ".join(f'"{m}"' for m in _RESTRICTED_BASE_MODELS)
        parts.append(
            f"NOT (nsfwLevel IN [4, 8, 16, 32] "
            f"AND baseModel IN [{models_quoted}])"
        )

    # User's browsing-level preference.
    level_expr = " OR ".join(f"nsfwLevel={lv}" for lv in nsfw_levels)
    parts.append(f"({level_expr})")

    return " AND ".join(parts)


def _trpc_item_to_meili_hit(item: dict[str, Any]) -> dict[str, Any]:
    """Map a tRPC ``image.getInfinite`` item to a Meilisearch-like hit.

    Field names mirror the endpoint's own schema (id/name/url/nsfwLevel/
    width/height/hash/type/postId/baseModel/user/stats/tags/reactions);
    stats arrive as flat ``*AllTime`` keys.
    """
    user = item.get("user") or {}
    stats = item.get("stats") or {}
    meta = item.get("meta") or {}

    def _reactions(stats: dict[str, Any]) -> int:
        return sum(
            int(stats.get(k) or 0)
            for k in ("likeCountAllTime", "laughCountAllTime",
                      "heartCountAllTime", "cryCountAllTime")
        )

    # tags may be a list, a columnar sentinel (-1), or absent — coerce.
    raw_tags = item.get("tags")
    if not isinstance(raw_tags, list):
        raw_tags = []
    tag_names = [
        t.get("name") if isinstance(t, dict) else t
        for t in raw_tags if t
    ]

    return {
        "id": item.get("id"),
        "url": item.get("url", ""),
        "hash": item.get("hash", ""),
        "width": item.get("width"),
        "height": item.get("height"),
        "nsfwLevel": item.get("nsfwLevel"),
        "type": item.get("type", "image"),
        "baseModel": item.get("baseModel"),
        "username": user.get("username", ""),
        "postId": item.get("postId"),
        "createdAt": item.get("createdAt", ""),
        "browsingLevel": item.get("nsfwLevel"),
        "stats": {
            "reactionCountAllTime": _reactions(stats),
            "commentCountAllTime": stats.get("commentCountAllTime", 0),
            "collectedCountAllTime": stats.get("collectedCountAllTime", 0),
            "likeCountAllTime": stats.get("likeCountAllTime", 0),
        },
        "reactions": item.get("reactions"),
        "meta": meta,
        "prompt": meta.get("prompt", ""),
        "tagNames": tag_names,
        "generationProcess": meta.get("Version") or "Unknown",
    }


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
