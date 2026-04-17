#!/usr/bin/env python3
"""
CivitAI API singleton for managing all CivitAI API calls.
Refer to CIVITAI_API_REFERENCE.md for details on endpoints and usage.
"""

import os
import json
import random
import threading
import time
from importlib import import_module
from pathlib import Path
from typing import Dict, List, Optional, Any

from .http_client import CivitaiHttpClient, CivitaiRequestError


def _get_config_value(name: str) -> Optional[str]:
    """Load a config value from either local runtime or packaged app layout."""
    module_names = [
        "atelierai.config",
        "config",
        "backend.config",
        "app.backend.config",
        "examples.config_example",
        "app.examples.config_example",
    ]

    for module_name in module_names:
        try:
            mod = import_module(module_name)
        except ModuleNotFoundError:
            continue

        value = getattr(mod, name, None)
        if value is not None:
            return value

    return None


class CivitaiAPI:
    """Singleton class for managing CivitAI API calls.

    Handles all API communication with CivitAI, including authentication,
    request management, and data fetching.

    Usage:
        api = CivitaiAPI.get_instance()
        basic_info = api.fetch_basic_info(image_id)
        generation_data = api.fetch_generation_data(image_id)
    """

    _instance: Optional["CivitaiAPI"] = None
    _initialized: bool
    _TRPC_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    _TRPC_MAX_ATTEMPTS = 4
    _TRPC_BACKOFF_BASE_SECONDS = 0.75

    def __new__(cls, *args, **kwargs):
        """Singleton pattern - ensure only one instance exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, session_cookie=None, auto_authenticate=False):
        """Initialize the API singleton.

        Args:
            session_cookie: Optional session cookie. If None and auto_authenticate=True,
                          will try to retrieve automatically.
            auto_authenticate: If True, attempts to get session token automatically.
        """
        if self._initialized:
            return

        # Initialize session and authentication
        if session_cookie:
            self.session_cookie = session_cookie
        elif auto_authenticate:
            self.session_cookie = self._get_auto_session_token()
        else:
            # Non-auto mode still prefers the current cache-file value.
            self.session_cookie = self._get_session_token_from_cache() or ""

        config_trpc_url = _get_config_value("CIVITAI_TRPC_BASE_URL")
        config_web_url = _get_config_value("CIVITAI_WEB_BASE_URL")
        config_archive_url = _get_config_value("CIVITAIARCHIVE_BASE_URL")
        self.base_url = config_trpc_url or "https://civitai.red/api/trpc"
        self._web_base_url = config_web_url or "https://civitai.red"
        self._archive_base_url = config_archive_url or "https://civitaiarchive.com"
        self.http_client = CivitaiHttpClient(headers_factory=self._get_headers)

        # Default parameters based on CivitAI API
        self.default_params = {
            "collectionId": 10842247,
            "period": "AllTime",
            "sort": "Newest",
            "browsingLevel": 31,
            "include": ["cosmetics"],
            "excludedTagIds": [
                415792,
                426772,
                5188,
                5249,
                130818,
                130820,
                133182,
                5351,
                306619,
                154326,
                161829,
                163032,
            ],
            "disablePoi": True,
            "disableMinor": True,
            "cursor": None,
            "authed": True,
        }

        self.default_meta = {"meta": {"values": {"cursor": ["undefined"]}}}

        # Response cache (optional - can be enabled later)
        self._cache: Dict = {}
        self._retry_metrics_lock = threading.Lock()
        self._payload_retry_metrics: Dict[str, Any] = {
            "total": 0,
            "by_status": {},
            "by_endpoint": {},
        }
        self._image_uuid_index: Dict[int, str] = {}
        self._api_archive_lock = threading.Lock()

        self._initialized = True

    @classmethod
    def get_instance(cls) -> "CivitaiAPI":
        """Get the singleton instance of CivitaiAPI.

        Returns:
            CivitaiAPI instance
        """
        if cls._instance is None:
            cls._instance = cls(auto_authenticate=True)
        return cls._instance

    def update_session_cookie(self, new_cookie: str) -> None:
        """Update the session cookie on the singleton at runtime.

        Persists the new cookie to the cache file so it survives across
        requests without requiring a server restart.

        Args:
            new_cookie: The new __Secure-civitai-token value.
        """
        if not new_cookie or len(new_cookie) < 100:
            raise ValueError("Session cookie appears too short to be valid.")

        self.session_cookie = new_cookie

        # Persist to the session cache file.
        cache_path = _get_config_value("CIVITAI_SESSION_CACHE")
        if cache_path:
            try:
                from .civitai_auth import _save_token_to_cache

                _save_token_to_cache(new_cookie, cache_path)
            except Exception as exc:
                print(f"⚠️  Could not persist cookie to cache: {exc}")
        print("✅ CivitAI session cookie updated.")

    def _get_session_token_from_cache(self) -> Optional[str]:
        """Try to get session token from cache file."""
        CIVITAI_SESSION_CACHE = _get_config_value("CIVITAI_SESSION_CACHE")
        if not CIVITAI_SESSION_CACHE:
            return None

        if os.path.exists(CIVITAI_SESSION_CACHE):
            try:
                with open(CIVITAI_SESSION_CACHE, "r") as f:
                    token = f.read().strip()
                if token and len(token) > 100:
                    print(f"✅ Using cached session token from {CIVITAI_SESSION_CACHE}")
                    return token
            except Exception:
                pass
        return None

    def _get_session_token_from_auth(self) -> Optional[str]:
        """Try to get session token using Playwright authentication."""
        CIVITAI_SESSION_CACHE = _get_config_value("CIVITAI_SESSION_CACHE")
        if not CIVITAI_SESSION_CACHE:
            return None

        print("ℹ️  No valid session token found in cache")
        print("   Attempting automatic authentication...")
        try:
            try:
                auth_mod = import_module("atelierai.civitai.civitai_auth")
            except ModuleNotFoundError:
                auth_mod = import_module("app.src.atelierai.civitai.civitai_auth")

            get_cached_or_refresh_session_token = getattr(
                auth_mod, "get_cached_or_refresh_session_token"
            )
            return get_cached_or_refresh_session_token(
                cache_file=CIVITAI_SESSION_CACHE, headless=True
            )
        except ImportError:
            print("Warning: civitai_auth module not available")
        except Exception as e:
            print(f"Warning: Auto-authentication failed ({e})")
        return None

    def _get_auto_session_token(self) -> str:
        """
        Attempts to automatically retrieve a session token.
        Priority: cache file > Playwright auth refresh
        """
        token = (
            self._get_session_token_from_cache() or self._get_session_token_from_auth()
        )

        if token:
            return token

        raise Exception(
            "No valid session token available. Please run "
            "'python scripts/setup_session_token.py' to set up your token."
        )

    def _get_headers(self) -> Dict:
        """Returns standard headers for requests.

        Sends both cookie names that CivitAI may use for authentication
        (``__Secure-civitai-token`` and ``__Secure-next-auth.session-token``)
        because CivitAI has changed cookie naming across different flows.
        """
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Cookie": (
                f"__Secure-civitai-token={self.session_cookie}; "
                f"__Secure-next-auth.session-token={self.session_cookie}"
            ),
            "Referer": "https://civitai.com/",
        }

    def _build_trpc_payload(self, input_json: Dict) -> str:
        """
        Wraps the input JSON into the structure required by Civitai's tRPC API.
        Structure: {"json": {your_data}, "meta": {"values": {"cursor": ["undefined"]}}}

        The key insight: when the meta values cursor is "undefined", the API will only
        return the first page of values, regardless of the actual cursor value
        sent in the json input parameters. So we should *only* send ["undefined"] when the cursor
        is first null/absent, otherwise do not send any meta data.
        """
        input_meta = self.default_meta if input_json.get("cursor") is None else {}
        return json.dumps({"json": input_json, **input_meta}, separators=(",", ":"))

    def _make_raw_request(
        self,
        endpoint: str,
        payload_data: Dict,
        *,
        strict: bool = False,
    ) -> Optional[Dict]:
        """Make a raw request to CivitAI's tRPC API."""
        url = f"{self.base_url}/{endpoint}"
        params = {"input": self._build_trpc_payload(payload_data)}

        try:
            return self.http_client.request_json("GET", url, params=params)
        except CivitaiRequestError as e:
            if e.status_code == 403:
                backoff = self.http_client.activate_global_backoff(
                    90.0, reason="HTTP 403 (Cloudflare)"
                )
                print(
                    f"🚫 CivitAI returned HTTP 403 (Cloudflare challenge); pausing all requests for {backoff:.0f}s"
                )
            if strict:
                raise
            status_text = (
                f" (HTTP {e.status_code})" if e.status_code is not None else ""
            )
            print(f"❌ API request error{status_text}: {e}")
            return None

    def _retry_delay(self, attempt: int) -> float:
        """Compute exponential backoff with small jitter for retry loops."""
        jitter = random.uniform(0.0, 0.35)
        return self._TRPC_BACKOFF_BASE_SECONDS * (2 ** max(0, attempt - 1)) + jitter

    def _record_payload_retry(self, endpoint: str, status_code: Optional[int]) -> None:
        """Track payload-level retry attempts for observability across long-running jobs."""
        endpoint_key = str(endpoint or "unknown")
        status_key = str(status_code) if status_code is not None else "unknown"
        with self._retry_metrics_lock:
            self._payload_retry_metrics["total"] = (
                int(self._payload_retry_metrics.get("total", 0)) + 1
            )

            by_status = self._payload_retry_metrics.get("by_status")
            if not isinstance(by_status, dict):
                by_status = {}
                self._payload_retry_metrics["by_status"] = by_status
            by_status[status_key] = int(by_status.get(status_key, 0)) + 1

            by_endpoint = self._payload_retry_metrics.get("by_endpoint")
            if not isinstance(by_endpoint, dict):
                by_endpoint = {}
                self._payload_retry_metrics["by_endpoint"] = by_endpoint
            by_endpoint[endpoint_key] = int(by_endpoint.get(endpoint_key, 0)) + 1

    def get_payload_retry_metrics_snapshot(self) -> Dict[str, Any]:
        """Return a copy of payload-level retry metrics collected by this singleton."""
        with self._retry_metrics_lock:
            by_status = self._payload_retry_metrics.get("by_status")
            by_endpoint = self._payload_retry_metrics.get("by_endpoint")
            return {
                "total": int(self._payload_retry_metrics.get("total", 0)),
                "by_status": dict(by_status) if isinstance(by_status, dict) else {},
                "by_endpoint": (
                    dict(by_endpoint) if isinstance(by_endpoint, dict) else {}
                ),
            }

    def _make_request(
        self, endpoint: str, payload_data: Dict, *, strict: bool = False
    ) -> Optional[Dict]:
        """Make a request to CivitAI API.

        Args:
            endpoint: API endpoint (e.g., "image.get", "image.getGenerationData")
            payload_data: Data to send in request

        Returns:
            Parsed JSON response, or None if request fails
        """
        last_error: Optional[CivitaiRequestError] = None
        max_attempts = max(1, int(self._TRPC_MAX_ATTEMPTS))

        for attempt in range(1, max_attempts + 1):
            data = self._make_raw_request(endpoint, payload_data, strict=strict)
            if not data:
                return None

            if not isinstance(data, dict):
                return data

            error_payload = data.get("error")
            if isinstance(error_payload, dict):
                raw_error_json = error_payload.get("json")
                error_json = raw_error_json if isinstance(raw_error_json, dict) else {}
                raw_error_data = error_json.get("data")
                error_data = raw_error_data if isinstance(raw_error_data, dict) else {}
                status_code = error_data.get("httpStatus")
                message = (
                    error_json.get("message")
                    or error_payload.get("message")
                    or "CivitAI tRPC request failed"
                )

                detail = message
                if status_code is not None:
                    detail = f"CivitAI request failed with HTTP {status_code}: {json.dumps(data, separators=(',', ':'))}"
                elif error_data.get("path"):
                    detail = f"{message} ({error_data.get('path')})"

                normalized_status = (
                    status_code if isinstance(status_code, int) else None
                )
                retryable = bool(normalized_status in self._TRPC_RETRYABLE_STATUS_CODES)
                if normalized_status == 429:
                    backoff_seconds = self.http_client.activate_global_backoff(
                        30.0,
                        reason="tRPC payload 429",
                    )
                    if not strict:
                        print(
                            f"⏳ CivitAI payload rate limit; pausing all CivitAI requests for {backoff_seconds:.1f}s"
                        )
                exc = CivitaiRequestError(
                    detail,
                    status_code=normalized_status,
                    retryable=retryable,
                )

                if retryable and attempt < max_attempts:
                    self._record_payload_retry(endpoint, normalized_status)
                    if not strict:
                        status_text = (
                            f" (HTTP {exc.status_code})"
                            if exc.status_code is not None
                            else ""
                        )
                        print(
                            f"⚠️  API request retry {attempt}/{max_attempts - 1}{status_text}: {message}"
                        )
                    time.sleep(self._retry_delay(attempt))
                    last_error = exc
                    continue

                if strict:
                    raise exc

                status_text = (
                    f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
                )
                print(f"❌ API request error{status_text}: {exc}")
                return None

            result_wrapper = data.get("result")
            if not isinstance(result_wrapper, dict):
                return None

            result_data = result_wrapper.get("data")
            if isinstance(result_data, dict) and "json" in result_data:
                result_json = result_data["json"]
            else:
                result_json = result_data

            self._archive_metadata_response(
                endpoint=endpoint,
                payload_data=payload_data,
                response_json=result_json,
            )
            return result_json

        if strict and last_error is not None:
            raise last_error
        return None

    def _archive_root(self) -> Path:
        image_resources_path = (
            _get_config_value("IMAGE_RESOURCES_PATH") or "image_resources"
        )
        return Path(image_resources_path) / "civitai_api_responses"

    def _extract_uuid_from_hash(self, hash_value: Optional[str]) -> Optional[str]:
        if not hash_value:
            return None
        cleaned = str(hash_value).strip()
        if not cleaned:
            return None
        first = cleaned.split("/", 1)[0].strip()
        if len(first) > 8:
            return first
        return None

    def _archive_json_file(self, filename: str, payload: Dict[str, Any]) -> None:
        root = self._archive_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / filename
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def _archive_metadata_response(
        self, *, endpoint: str, payload_data: Dict[str, Any], response_json: Any
    ) -> None:
        # Capture metadata fetches globally so import/check/probe paths all persist payloads.
        if endpoint not in {
            "image.get",
            "image.getGenerationData",
            "image.getInfinite",
        }:
            return

        try:
            with self._api_archive_lock:
                if endpoint == "image.get" and isinstance(response_json, dict):
                    image_id = payload_data.get("id")
                    uuid_value = self._extract_uuid_from_hash(response_json.get("url"))
                    key = None
                    if uuid_value:
                        key = uuid_value
                    elif image_id is not None:
                        key = f"imageid_{int(image_id)}"
                    if key is None:
                        return

                    if image_id is not None and uuid_value:
                        self._image_uuid_index[int(image_id)] = uuid_value

                    self._archive_json_file(
                        f"civitai_image_get_{key}.json", response_json
                    )
                    return

                if endpoint == "image.getGenerationData" and isinstance(
                    response_json, dict
                ):
                    image_id = payload_data.get("id")
                    uuid_value = None
                    if image_id is not None:
                        uuid_value = self._image_uuid_index.get(int(image_id))
                    key = uuid_value or (
                        f"imageid_{int(image_id)}" if image_id is not None else None
                    )
                    if key is None:
                        return
                    self._archive_json_file(
                        f"civitai_image_getGenerationData_{key}.json", response_json
                    )
                    return

                if endpoint == "image.getInfinite" and isinstance(response_json, dict):
                    items = self._find_deep_image_list(response_json)
                    if not items:
                        return
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        item_id = item.get("id")
                        uuid_value = self._extract_uuid_from_hash(item.get("url"))
                        key = uuid_value or (
                            f"imageid_{int(item_id)}" if item_id is not None else None
                        )
                        if key is None:
                            continue
                        if item_id is not None and uuid_value:
                            self._image_uuid_index[int(item_id)] = uuid_value
                        self._archive_json_file(
                            f"civitai_image_getInfinite_{key}.json", item
                        )
        except Exception:
            # Archival should never break API fetch behavior.
            pass

    def is_rate_limited(self) -> bool:
        """Return True when global CivitAI cooldown is active."""
        return self.http_client.is_global_backoff_active()

    def rate_limit_remaining_seconds(self) -> float:
        """Return remaining global CivitAI cooldown in seconds."""
        return self.http_client.get_global_backoff_remaining_seconds()

    # ===== Image API Methods =====

    def fetch_image_tag_records(self, image_id: int) -> List[Dict[str, Any]]:
        """Fetch tag records for a specific image using ID-first payloads.

        Uses tag.getVotableTags endpoint and returns stable dict records sorted
        by relevance score (highest first), preferring numeric tag IDs.
        """
        response = self._make_request(
            endpoint="tag.getVotableTags",
            payload_data={"id": int(image_id), "type": "image", "authed": True},
        )

        if not response or not isinstance(response, list):
            return []

        sorted_tags = sorted(response, key=lambda t: t.get("score", 0), reverse=True)
        out: List[Dict[str, Any]] = []
        for tag in sorted_tags:
            if not isinstance(tag, dict):
                continue
            tag_name = tag.get("name")
            tag_id = tag.get("id")
            if tag_id is None and not isinstance(tag_name, str):
                continue

            normalized: Dict[str, Any] = {}
            if tag_id is not None:
                try:
                    normalized["id"] = int(tag_id)
                except (TypeError, ValueError):
                    normalized["id"] = str(tag_id)
            if isinstance(tag_name, str) and tag_name.strip():
                normalized["name"] = tag_name.strip()
            if tag.get("score") is not None:
                normalized["score"] = tag.get("score")
            if tag.get("type") is not None:
                normalized["type"] = tag.get("type")
            if tag.get("nsfwLevel") is not None:
                normalized["nsfwLevel"] = tag.get("nsfwLevel")
            if tag.get("automated") is not None:
                normalized["automated"] = tag.get("automated")
            if tag.get("concrete") is not None:
                normalized["concrete"] = tag.get("concrete")

            out.append(normalized)

        return out

    def fetch_image_tags(self, image_id: int) -> List[str]:
        """Fetch tag names for a specific image.

        Compatibility wrapper over fetch_image_tag_records.
        """
        records = self.fetch_image_tag_records(image_id)
        return [
            str(tag.get("name")) for tag in records if isinstance(tag.get("name"), str)
        ]

    def fetch_basic_info(
        self, image_id: int, *, strict: bool = False
    ) -> Optional[Dict]:
        """Fetch basic image information (URL, author, NSFW, created_at).

        Uses image.get endpoint.

        Args:
            image_id: CivitAI image ID

        Returns:
            Dictionary with basic image info, or None if not found
        """
        return self._make_request(
            endpoint="image.get",
            payload_data={"id": int(image_id), "authed": True},
            strict=strict,
        )

    def fetch_generation_data(
        self, image_id: int, *, strict: bool = False
    ) -> Optional[Dict]:
        """Fetch detailed generation data for a single image.

        Uses image.getGenerationData endpoint.

        Args:
            image_id: CivitAI image ID

        Returns:
            Dictionary with generation data (prompts, models, parameters), or None
        """
        return self._make_request(
            endpoint="image.getGenerationData",
            payload_data={"id": int(image_id), "authed": True},
            strict=strict,
        )

    def fetch_image_data(self, image_id: int) -> Dict:
        """Fetch both basic info and generation data for an image.

        Combines fetch_basic_info() and fetch_generation_data().

        Args:
            image_id: CivitAI image ID

        Returns:
            Dictionary with both sources:
            {
                "basic_info": {...},  # From image.get
                "generation_data": {...}  # From image.getGenerationData
            }
        """
        basic_info = self.fetch_basic_info(image_id)
        generation_data = self.fetch_generation_data(image_id)

        return {"basic_info": basic_info, "generation_data": generation_data}

    def check_model_availability(
        self, model_id: int, model_version_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Check if a model/version is available on CivitAI or has been deleted.

        Uses modelVersion.getById API endpoint to check model status.

        Args:
            model_id: The CivitAI model ID
            model_version_id: Optional model version ID (required for accurate check)

        Returns:
            Dictionary with availability status:
            {
                "available": bool,  # True if model exists, False if deleted
                "model_id": int,
                "model_version_id": Optional[int],
                "civitai_url": str,
                "archive_url": str,
                "status_code": Optional[int],  # HTTP status code
                "error": Optional[str],
                "model_status": Optional[str]  # "Published", "Deleted", etc.
            }
        """
        result = {
            "available": False,
            "model_id": model_id,
            "model_version_id": model_version_id,
            "civitai_url": f"{self._web_base_url}/models/{model_id}",
            "archive_url": f"{self._archive_base_url}/models/{model_id}",
            "status_code": None,
            "error": None,
            "model_status": None,
        }

        # Build URLs with version ID if provided
        if model_version_id:
            result["civitai_url"] = (
                f"{result['civitai_url']}?modelVersionId={model_version_id}"
            )
            result["archive_url"] = (
                f"{result['archive_url']}?modelVersionId={model_version_id}"
            )

        try:
            # Use modelVersion.getById endpoint to get model version details
            response = (
                self._make_request(
                    endpoint="modelVersion.getById",
                    payload_data={"id": int(model_version_id), "authed": True},
                )
                if model_version_id
                else None
            )

            if response:
                # Check the model status
                model_info = response.get("model", {})
                model_status = model_info.get("status", "Unknown")
                result["model_status"] = model_status

                if model_status == "Deleted":
                    result["available"] = False
                    result["error"] = "Model has been deleted from Civitai"
                else:
                    result["available"] = True
                    result["status_code"] = 200
            else:
                # If no version_id provided, we can't check accurately
                if not model_version_id:
                    result["error"] = (
                        "No model_version_id provided - cannot verify availability"
                    )
                    result["available"] = None  # Unknown
                else:
                    result["available"] = False
                    result["status_code"] = 404
                    result["error"] = "Model version not found"

        except Exception as e:
            result["available"] = False
            result["error"] = str(e)

        return result

    # ===== Collection API Methods =====

    def fetch_collection_items(self, collection_id: int) -> List[Dict]:
        """Fetch the list of all items in a collection.

        Uses image.getInfinite endpoint.

        Args:
            collection_id: CivitAI collection ID

        Returns:
            List of collection items (dictionaries), or empty list
        """
        payload_data = {**self.default_params}
        payload_data["collectionId"] = int(collection_id)
        payload_data["cursor"] = None

        response = self._make_request(
            endpoint="image.getInfinite", payload_data=payload_data
        )

        result = None
        if response:
            result = self._find_deep_image_list(response)
        return result if result is not None else []

    def fetch_collection_with_details(
        self, collection_id: int, limit: Optional[int] = 50
    ) -> List[Dict]:
        """Fetch collection items with full generation details.

        Args:
            collection_id: CivitAI collection ID
            limit: Maximum number of items to fetch (default: 50)
                   Use -1 for all items (with pagination)

        Returns:
            List of merged image data (basic_info + generation_data)
        """
        items = []
        cursor = None
        fetched = 0

        while True:
            payload_data = {**self.default_params}
            payload_data["collectionId"] = int(collection_id)
            payload_data["cursor"] = cursor

            response = self._make_request(
                endpoint="image.getInfinite", payload_data=payload_data
            )

            if not response:
                break

            # Find image list in response
            page_items = self._find_deep_image_list(response)
            if not page_items:
                break

            # Fetch generation data for each item
            for item in page_items:
                if limit is not None and limit >= 0 and fetched >= limit:
                    break

                img_id = item.get("id")
                generation_data = self.fetch_generation_data(img_id)

                if generation_data:
                    items.append(
                        {"collection_item": item, "generation_data": generation_data}
                    )
                else:
                    print(f"⚠️  Failed to fetch data for image {img_id}")

                fetched += 1

            # Check for more items (pagination)
            # Note: Simplified for now - assumes one page unless explicit pagination support
            if len(page_items) < 50 or (
                limit is not None and limit >= 0 and fetched >= limit
            ):
                break

            # For production: need to extract next cursor from response
            # For now, stop after first page
            break

        return items

    # ===== Helper Methods =====

    def _is_image_list(self, obj: List) -> bool:
        """Check if a list contains gallery media objects from image.getInfinite."""
        return (
            len(obj) > 0
            and isinstance(obj[0], dict)
            and "id" in obj[0]
            and (obj[0].get("type") in {"image", "video"} or bool(obj[0].get("url")))
        )

    def _search_list(self, obj: List, depth: int) -> Optional[List]:
        """Search for image list within a list."""
        for item in obj:
            result = self._find_deep_image_list(item, depth + 1)
            if result:
                return result
        return None

    def _search_dict_keys(self, obj: Dict, depth: int) -> Optional[List]:
        """Search for image list in specific dictionary keys."""
        for key in ["items", "pages"]:
            if key in obj and isinstance(obj[key], list):
                result = self._find_deep_image_list(obj[key], depth + 1)
                if result:
                    return result
        return None

    def _search_dict_values(self, obj: Dict, depth: int) -> Optional[List]:
        """Recursively search all dictionary values."""
        for key, value in obj.items():
            result = self._find_deep_image_list(value, depth + 1)
            if result:
                return result
        return None

    def _find_deep_image_list(self, obj: Dict, depth: int = 0) -> Optional[List]:
        """Recursively finds the list of image objects in complex tRPC JSON."""
        if depth > 10:
            return None

        if isinstance(obj, list):
            if self._is_image_list(obj):
                return obj
            return self._search_list(obj, depth)

        if isinstance(obj, dict):
            result = self._search_dict_keys(obj, depth)
            if result:
                return result
            return self._search_dict_values(obj, depth)

        return None


# ===== Singleton Instance =====


def get_api_instance() -> CivitaiAPI:
    """Convenience function to get CivitaiAPI singleton instance."""
    return CivitaiAPI.get_instance()
