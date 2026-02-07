#!/usr/bin/env python3
"""
Civitai API singleton for managing all Civitai API calls.
Refer to CIVITAI_API_REFERENCE.md for details on endpoints and usage.
"""

import os
import requests
import json
from typing import Dict, List, Optional, Any


class CivitaiAPI:
    """Singleton class for managing Civitai API calls.

    Handles all API communication with Civitai, including authentication,
    request management, and data fetching.

    Usage:
        api = CivitaiAPI.get_instance()
        basic_info = api.fetch_basic_info(image_id)
        generation_data = api.fetch_generation_data(image_id)
    """

    _instance = None

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
            # Fallback to environment variable or config
            try:
                from config import CIVITAI_SESSION_COOKIE  # pyright: ignore[reportMissingImports]
            except (ModuleNotFoundError, ImportError):
                try:
                    from config_example import CIVITAI_SESSION_COOKIE  # pyright: ignore[reportAttributeAccessIssue]
                except (ModuleNotFoundError, ImportError):
                    CIVITAI_SESSION_COOKIE = None

            self.session_cookie = CIVITAI_SESSION_COOKIE

        self.base_url = "https://civitai.com/api/trpc"
        self.session = requests.Session()  # Reuse connection

        # Default parameters based on Civitai API
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

    def _get_session_token_from_cache(self) -> Optional[str]:
        """Try to get session token from cache file."""
        try:
            from config import CIVITAI_SESSION_CACHE  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError:
            from config_example import CIVITAI_SESSION_CACHE  # pyright: ignore[reportAttributeAccessIssue]

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

    def _get_session_token_from_env(self) -> Optional[str]:
        """Try to get session token from environment variables."""
        env_token = os.environ.get("CIVITAI_SESSION_COOKIE") or os.environ.get(
            "CIVITAI_SESSION_TOKEN"
        )
        if env_token and len(env_token) > 100:
            print("✅ Using session token from environment variable")
            return env_token
        return None

    def _get_session_token_from_auth(self) -> Optional[str]:
        """Try to get session token using Playwright authentication."""
        try:
            from config import CIVITAI_SESSION_CACHE  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError:
            from config_example import CIVITAI_SESSION_CACHE  # pyright: ignore[reportAttributeAccessIssue]

        print("ℹ️  No valid session token found in cache or environment")
        print("   Attempting automatic authentication...")
        try:
            from civitai_auth import get_cached_or_refresh_session_token
            return get_cached_or_refresh_session_token(
                cache_file=CIVITAI_SESSION_CACHE, headless=True
            )
        except ImportError:
            print("Warning: civitai_auth module not available")
        except Exception as e:
            print(f"Warning: Auto-authentication failed ({e})")
        return None

    def _get_session_token_from_config(self) -> Optional[str]:
        """Try to get session token from config file."""
        try:
            from config import CIVITAI_SESSION_COOKIE  # pyright: ignore[reportMissingImports]
        except ModuleNotFoundError:
            from config_example import CIVITAI_SESSION_COOKIE  # pyright: ignore[reportAttributeAccessIssue]

        if CIVITAI_SESSION_COOKIE and len(CIVITAI_SESSION_COOKIE) > 100:
            print("Using session token from config.py")
            return CIVITAI_SESSION_COOKIE
        return None

    def _get_auto_session_token(self) -> str:
        """
        Attempts to automatically retrieve a session token.
        Priority: cache file > environment variable > Playwright auth > config.py
        """
        token = (
            self._get_session_token_from_cache()
            or self._get_session_token_from_env()
            or self._get_session_token_from_auth()
            or self._get_session_token_from_config()
        )

        if token:
            return token

        raise Exception(
            "No valid session token available. Please run "
            "'python scripts/setup_session_token.py' to set up your token, "
            "or set CIVITAI_SESSION_COOKIE in your .env file."
        )

    def _get_headers(self) -> Dict:
        """Returns standard headers for requests."""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cookie": f"__Secure-civitai-token={self.session_cookie}",
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

    def _make_request(self, endpoint: str, payload_data: Dict) -> Optional[Dict]:
        """Make a request to Civitai API.

        Args:
            endpoint: API endpoint (e.g., "image.get", "image.getGenerationData")
            payload_data: Data to send in request

        Returns:
            Parsed JSON response, or None if request fails
        """
        url = f"{self.base_url}/{endpoint}"
        params = {"input": self._build_trpc_payload(payload_data)}

        try:
            response = self.session.get(
                url, headers=self._get_headers(), params=params, timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                # Navigate tRPC response structure
                if "result" in data and "data" in data["result"]:
                    result_data = data["result"]["data"]
                    if "json" in result_data:
                        return result_data["json"]
                    return result_data
                return data
            else:
                print(f"⚠️  API request failed: {response.status_code}")
                return None

        except Exception as e:
            print(f"❌ API request error: {e}")
            return None

    # ===== Image API Methods =====

    def fetch_image_tags(self, image_id: int) -> List[str]:
        """Fetch tags for a specific image.

        Uses tag.getVotableTags endpoint which returns tags with scores.

        Args:
            image_id: Civitai image ID

        Returns:
            List of tag strings sorted by relevance score (highest first),
            or empty list if not found
        """
        # Use tag.getVotableTags endpoint
        response = self._make_request(
            endpoint="tag.getVotableTags",
            payload_data={"id": int(image_id), "type": "image", "authed": True},
        )

        if response and isinstance(response, list):
            # Sort tags by score (highest first) and return names
            sorted_tags = sorted(
                response, key=lambda t: t.get("score", 0), reverse=True
            )
            return [tag.get("name") for tag in sorted_tags if tag.get("name")]

        return []

    def fetch_basic_info(self, image_id: int) -> Optional[Dict]:
        """Fetch basic image information (URL, author, NSFW, created_at).

        Uses image.get endpoint.

        Args:
            image_id: Civitai image ID

        Returns:
            Dictionary with basic image info, or None if not found
        """
        return self._make_request(
            endpoint="image.get", payload_data={"id": int(image_id), "authed": True}
        )

    def fetch_generation_data(self, image_id: int) -> Optional[Dict]:
        """Fetch detailed generation data for a single image.

        Uses image.getGenerationData endpoint.

        Args:
            image_id: Civitai image ID

        Returns:
            Dictionary with generation data (prompts, models, parameters), or None
        """
        return self._make_request(
            endpoint="image.getGenerationData",
            payload_data={"id": int(image_id), "authed": True},
        )

    def fetch_image_data(self, image_id: int) -> Dict:
        """Fetch both basic info and generation data for an image.

        Combines fetch_basic_info() and fetch_generation_data().

        Args:
            image_id: Civitai image ID

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
        """Check if a model/version is available on Civitai or has been deleted.

        Uses modelVersion.getById API endpoint to check model status.

        Args:
            model_id: The Civitai model ID
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
            "civitai_url": f"https://civitai.com/models/{model_id}",
            "archive_url": f"https://civitaiarchive.com/models/{model_id}",
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
            response = self._make_request(
                endpoint="modelVersion.getById",
                payload_data={"id": int(model_version_id), "authed": True}
            ) if model_version_id else None

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
                    result["error"] = "No model_version_id provided - cannot verify availability"
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
            collection_id: Civitai collection ID

        Returns:
            List of collection items (dictionaries), or empty list
        """
        payload_data = {**self.default_params}
        payload_data["collectionId"] = int(collection_id)
        payload_data["cursor"] = None

        response = self._make_request(
            endpoint="image.getInfinite", payload_data=payload_data
        )

        if response:
            result = self._find_deep_image_list(response)
        return result if result is not None else []

    def fetch_collection_with_details(
        self, collection_id: int, limit: Optional[int] = 50
    ) -> List[Dict]:
        """Fetch collection items with full generation details.

        Args:
            collection_id: Civitai collection ID
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
        """Check if a list contains image objects."""
        return (
            len(obj) > 0
            and isinstance(obj[0], dict)
            and "id" in obj[0]
            and obj[0].get("type") == "image"
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
