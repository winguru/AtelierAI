import argparse
import json
import time
from typing import Any, Optional, List, Dict
import httpx
from config import CIVITAI_SESSION_COOKIE

# Force reload by setting this environment variable
# This ensures any cached bytecode is ignored
import importlib

importlib.invalidate_caches()


class CivitaiTrpcError(Exception):
    """Custom exception for tRPC errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        raw_body: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.raw_body = raw_body


class CivitaiTrpcClient:
    """
    Python client for CivitAI's internal tRPC endpoints.
    Designed for authenticated scraping using session cookies.
    """

    BASE_URL = "https://civitai.com/api/trpc"

    def __init__(
        self,
        session_token: str = CIVITAI_SESSION_COOKIE,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        x_fingerprint: Optional[str] = None,
        verbose: bool = False,
        auto_load_settings: bool = False,
    ):
        """
        :param session_token: The value of the '__Secure-civitai-token' cookie.
        :param x_fingerprint: The 'x-fingerprint' header value. If None, we attempt to send the request without it,
                             though CivitAI may require it for some endpoints.
        :param verbose: If True, print request URLs and response data for debugging.
        :param auto_load_settings: If True, automatically load browsing settings from API on init.
        """
        self.session = httpx.Client(cookies={"__Secure-civitai-token": session_token})
        self.verbose = verbose
        self.default_headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "Accept": "*/*",
            # Static headers observed in browser requests
            "x-client": "web",
            "x-client-version": "5.0.1401",
        }
        if x_fingerprint:
            self.default_headers["x-fingerprint"] = x_fingerprint

        # Initialize browsing preferences with sensible defaults
        self.browsing_prefs: Dict[str, Any] = {
            "period": "AllTime",
            "sort": "Newest",
            "browsingLevel": 1,
            "include": ["cosmetics"],
            "excludedTagIds": [],
            "disablePoi": True,
            "disableMinor": False,
        }

        # Optionally load browsing settings from API
        if auto_load_settings:
            self.load_browsing_settings()

    def _prepare_input_param(self, data: Dict[str, Any]) -> str:
        """
        Wraps the data dictionary in the tRPC JSON structure and stringifies it.
        Format: {"json": { ...actual params... }}
        """
        # tRPC expects the query param 'input' to be a URL-encoded JSON string
        # The content of that JSON string is {"json": <your_params>}
        wrapped = {"json": data}
        return json.dumps(wrapped)

    def _build_headers(self) -> Dict[str, str]:
        return {
            **self.default_headers,
            "x-client-date": str(int(time.time() * 1000)),
        }

    def _build_params(self, payload: Dict[str, Any]) -> Dict[str, str]:
        return {"input": self._prepare_input_param(payload)}

    def _maybe_print_request(
        self,
        procedure: str,
        url: str,
        params: Dict[str, str],
        payload: Dict[str, Any],
    ) -> None:
        if not self.verbose:
            return
        print(f"\n{'='*70}")
        print(f"📡 REQUEST: {procedure}")
        print(f"{'='*70}")
        pretty_url = f"{url}?input={params['input']}"
        print(f"URL: {pretty_url}")
        print(f"Payload: {json.dumps(payload, indent=2)}")

    def _maybe_print_response(
        self,
        response: httpx.Response,
        data: Any,
        max_items_for_display: Optional[int],
    ) -> None:
        if not self.verbose:
            return
        print(f"\n📥 RESPONSE (Status {response.status_code}):")
        print(f"{'='*70}")
        display_data = data
        if max_items_for_display is not None:
            display_data = self._truncate_response_for_display(
                data, max_items_for_display
            )
        print(json.dumps(display_data, indent=2))
        print(f"{'='*70}\n")
        if max_items_for_display is not None and len(json.dumps(data)) > len(
            json.dumps(display_data)
        ):
            print(
                f"📊 ⚠️  Response truncated to {max_items_for_display} items for display"
            )

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise CivitaiTrpcError(
                f"HTTP Error: {e.response.status_code}",
                status_code=e.response.status_code,
                raw_body=e.response.text,
            )

    def _raise_for_trpc_error(self, data: Any) -> None:
        if isinstance(data, list):
            if len(data) > 0 and "error" in data[0]:
                error_data = data[0]["error"].get("json", {})
                raise CivitaiTrpcError(
                    f"tRPC Error: {error_data.get('message', 'Unknown error')}"
                )
            return
        if isinstance(data, dict) and "error" in data:
            error_data = data["error"]
            raise CivitaiTrpcError(
                f"tRPC Error: {error_data.get('message', 'Unknown error')}"
            )

    def _unwrap_trpc_result(self, data: Any) -> Any:
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("result", {}).get("data", {}).get("json")
        if isinstance(data, dict):
            return data.get("result", {}).get("data", {}).get("json")
        return data

    def _request(
        self,
        procedure: str,
        payload: Dict[str, Any],
        max_items_for_display: Optional[int] = None,
    ) -> Any:
        """
        Internal method to call a tRPC procedure.

        :param max_items_for_display: Optional limit for truncating verbose JSON output
        """
        url = f"{self.BASE_URL}/{procedure}"
        headers = self._build_headers()
        params = self._build_params(payload)

        self._maybe_print_request(procedure, url, params, payload)

        response = self.session.get(url, headers=headers, params=params)
        self._raise_for_status(response)

        data = response.json()
        self._maybe_print_response(response, data, max_items_for_display)

        self._raise_for_trpc_error(data)
        return self._unwrap_trpc_result(data)

    def _truncate_response_for_display(self, data: Any, max_items: int) -> Any:
        """
        Truncate response data for verbose display when limiting results.

        :param data: The full response data
        :param max_items: Maximum number of items to display
        :return: Truncated copy of data (or original if no items to truncate)
        """
        # Handle tRPC response structure: { "result": { "data": { "json": {...} } } }
        if isinstance(data, dict):
            # Check if this is an image.getInfinite response with items
            result = data.get("result", {})
            if result:
                result_data = result.get("data", {})
                if result_data:
                    json_data = result_data.get("json", {})
                    # If json_data has items array, truncate it
                    if isinstance(json_data, dict) and "items" in json_data:
                        items = json_data.get("items", [])
                        if len(items) > max_items:
                            # Create a truncated copy
                            truncated = json_data.copy()
                            truncated["items"] = items[:max_items]
                            # Also truncate nextCursor if needed
                            if "nextCursor" in truncated:
                                truncated["nextCursor"] = None
                            # Rebuild the response structure
                            return {"result": {"data": {"json": truncated}}}
        return data

    def get_collection_by_id(self, collection_id: int) -> Dict[str, Any]:
        """
        Corresponds to 'collection.getById'
        """
        payload = {"id": collection_id, "authed": True}
        response = self._request("collection.getById", payload)

        # The response is wrapped in a 'collection' key
        if response and isinstance(response, dict):
            return response.get("collection", {})
        return {}

    def get_browsing_settings(self) -> List[Dict[str, Any]]:
        """
        Corresponds to 'system.getBrowsingSettingAddons'
        Returns a list of setting presets.
        """
        payload = {"authed": True}
        return self._request("system.getBrowsingSettingAddons", payload)

    def _find_preset(
        self, settings: List[Dict[str, Any]], preset_type: Optional[str]
    ) -> Dict[str, Any]:
        """
        Find a preset by type from the settings list.

        :param settings: List of available presets
        :param preset_type: The type of preset to find
        :return: The preset dictionary, or empty dict if not found
        """
        for preset in settings:
            if preset.get("type") == preset_type:
                return preset

        if self.verbose:
            print(f"⚠️  Preset type '{preset_type}' not found, using first available")
        return settings[0] if settings else {}

    def _update_prefs_from_preset(self, preset: Dict[str, Any]) -> None:
        """
        Update browsing preferences from a preset dictionary.

        :param preset: The preset dictionary containing settings
        """
        # Update browsing preferences with values from preset
        for key in ["nsfwLevels", "excludedTagIds", "disablePoi", "disableMinor"]:
            if key in preset:
                # Map to our preference keys
                pref_key = {
                    "nsfwLevels": "browsingLevel",
                    "excludedTagIds": "excludedTagIds",
                    "disablePoi": "disablePoi",
                    "disableMinor": "disableMinor",
                }.get(key, key)

                if (
                    pref_key == "browsingLevel"
                    and isinstance(preset[key], list)
                    and preset[key]
                ):
                    # Sum all nsfw levels (bitwise OR of flags)
                    # Values like [1, 2, 4, 8, 16] should sum to 31
                    self.browsing_prefs[pref_key] = sum(preset[key])
                else:
                    self.browsing_prefs[pref_key] = preset[key]

        # Check for generation defaults
        if "generationDefaultValues" in preset:
            gen_defaults = preset["generationDefaultValues"]
            for key, value in gen_defaults.items():
                self.browsing_prefs[f"gen_{key}"] = value

    def _print_loaded_settings(
        self, preset_type: Optional[str], preset: Dict[str, Any]
    ) -> None:
        """
        Print verbose output about loaded browsing settings.

        :param preset_type: The type of preset that was loaded
        :param preset: The preset dictionary
        """
        if not self.verbose:
            return
        print(f"✅ Loaded browsing preferences from '{preset_type}' preset:")
        bl = self.browsing_prefs.get("browsingLevel")
        print(
            f"   browsingLevel: {bl} (sum of nsfwLevels: {preset.get('nsfwLevels', [])})"
        )
        print(
            f"   excludedTagIds: {len(self.browsing_prefs.get('excludedTagIds', []))} tags"
        )
        print(f"   disablePoi: {self.browsing_prefs.get('disablePoi')}")
        print(f"   disableMinor: {self.browsing_prefs.get('disableMinor')}")

    def load_browsing_settings(self, preset_type: Optional[str] = "some") -> None:
        """
        Load browsing settings from API and update internal preferences.

        The API returns multiple preset types (e.g., "none", "some").
        This method picks one to apply as defaults.

        :param preset_type: The type of preset to use ("none", "some", etc.).
                         Defaults to "some" which includes sensible defaults.
        """
        settings = self.get_browsing_settings()
        chosen_preset = self._find_preset(settings, preset_type)
        self._update_prefs_from_preset(chosen_preset)
        self._print_loaded_settings(preset_type, chosen_preset)

    def set_browsing_prefs(self, **kwargs) -> None:
        """
        Manually override browsing preferences.

        Any key passed here will update internal preferences.
        These are used as defaults in get_infinite_images().

        :param kwargs: Key-value pairs of preferences to override
        """
        for key, value in kwargs.items():
            if value is not None:
                self.browsing_prefs[key] = value
                if self.verbose:
                    print(f"🔧 Set preference: {key} = {value}")

    def get_browsing_prefs(self) -> Dict[str, Any]:
        """
        Get current browsing preferences.

        :return: Dictionary of current browsing preferences
        """
        return self.browsing_prefs.copy()

    def _explain_browsing_level(self, level: int) -> Dict[str, bool]:
        """
        Explain which NSFW categories are enabled in a browsing level.

        :param level: The browsing level value (sum of flags)
        :return: Dictionary of category -> enabled status
        """
        return {
            "PG (1)": bool(level & 1),
            "PG-13 (2)": bool(level & 2),
            "R (4)": bool(level & 4),
            "X (8)": bool(level & 8),
            "XXX (16)": bool(level & 16),
            "All NSFW (32)": bool(level & 32),
        }

    def _build_infinite_images_payload(
        self,
        collection_id: Optional[int],
        period: Optional[str],
        sort: Optional[str],
        browsing_level: Optional[int],
        excluded_tag_ids: Optional[List[int]],
        cursor: Optional[str],
        other_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = {
            **self.browsing_prefs,
            "collectionId": collection_id,
            "cursor": cursor,
            "authed": True,
            **other_params,
        }

        for key, value in [
            ("period", period),
            ("sort", sort),
            ("browsingLevel", browsing_level),
            ("excludedTagIds", excluded_tag_ids),
        ]:
            if value is not None:
                payload[key] = value

        payload = {k: v for k, v in payload.items() if v is not None}

        if payload.get("collectionId") is None:
            payload.pop("collectionId", None)

        return payload

    def _maybe_print_browsing_prefs(self, payload: Dict[str, Any]) -> None:
        if not self.verbose:
            return
        print(f"📊 Using browsing preferences:")
        print(f"   period: {payload.get('period')}")
        print(f"   sort: {payload.get('sort')}")
        print(f"   browsingLevel: {payload.get('browsingLevel')}")
        print(f"   excludedTagIds: {len(payload.get('excludedTagIds', []))} tags")

    def _is_unlimited(self, limit: Optional[int]) -> bool:
        """
        Check if fetching should continue until no more cursors.

        :param limit: The limit value from arguments
        :return: True if should paginate until done (limit=-1), False otherwise
        """
        return limit == -1

    def _fetch_with_pagination_limit(
        self,
        limit: int,
        max_pages: int,
    ) -> bool:
        """
        Check if we should use pagination loop based on limit value.

        :param limit: The limit value
        :param max_pages: Maximum pages allowed
        :return: True if pagination loop should be used
        """
        # -1 means fetch ALL (use pagination until no cursor)
        # None means default behavior (one page, no pagination)
        # 1-50 means small limit (one page, no pagination)
        # >50 means use pagination to reach limit
        return limit == -1 or limit > 50

    def _fetch_infinite_images_page(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("image.getInfinite", payload)

    def _handle_page_limit(
        self,
        all_items: List[Any],
        total_fetched: int,
        limit: int,
    ) -> tuple[bool, List[Any]]:
        """
        Check if limit has been reached and trim excess items.

        :return: Tuple of (should_break, trimmed_items)
        """
        if total_fetched >= limit:
            excess = total_fetched - limit
            if excess > 0:
                all_items = all_items[:-excess]
                if self.verbose:
                    print(
                        f"✅ Reached limit of {limit} items (fetched {total_fetched - excess} + {excess} on last page)"
                    )
            return True, all_items
        return False, all_items

    def _handle_pagination_state(
        self,
        response: Dict[str, Any],
        pages_fetched: int,
        max_pages: int,
    ) -> tuple[Optional[str], bool]:
        """
        Determine next cursor and whether to continue pagination.

        :return: Tuple of (next_cursor, should_break)
        """
        current_cursor = response.get("nextCursor")
        if not current_cursor:
            if self.verbose:
                print(
                    f"✅ No more pages available (fetched {pages_fetched} pages total)"
                )
            return current_cursor, True

        if pages_fetched >= max_pages:
            if self.verbose:
                print(f"⚠️  Stopping after {pages_fetched} pages (max_pages limit)")
            return current_cursor, True

        return current_cursor, False

    def _paginate_infinite_images(
        self,
        payload: Dict[str, Any],
        limit: int,
        max_pages: int,
    ) -> Dict[str, Any]:
        all_items: List[Any] = []
        current_cursor = None
        total_fetched = 0
        pages_fetched = 0

        while total_fetched < limit:
            if current_cursor:
                payload["cursor"] = current_cursor

            response = self._fetch_infinite_images_page(payload)
            items = response.get("items", [])
            pages_fetched += 1

            if self.verbose and pages_fetched > 1:
                print(f"📄 Fetching page {pages_fetched}...")

            all_items.extend(items)
            total_fetched += len(items)

            should_break, all_items = self._handle_page_limit(all_items, total_fetched, limit)
            if should_break:
                break

            current_cursor, should_break = self._handle_pagination_state(
                response, pages_fetched, max_pages
            )
            if should_break:
                break

        return {
            "items": all_items,
            "nextCursor": current_cursor,
            "totalFetched": total_fetched,
            "pagesFetched": pages_fetched,
        }

    def get_infinite_images(
        self,
        collection_id: Optional[int] = None,
        period: Optional[str] = None,
        sort: Optional[str] = None,
        browsing_level: Optional[int] = None,
        excluded_tag_ids: Optional[List[int]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        max_pages: int = 10,
        **other_params: Any,
    ) -> Dict[str, Any]:
        """
        Corresponds to 'image.getInfinite'

        Uses browsing preferences as defaults, but allows override via parameters.

        :param collection_id: Filter by specific collection ID
        :param period: Time period (e.g., "AllTime", "Day", "Week", "Month")
        :param sort: Sort order (e.g., "Newest", "MostLiked", "MostComments")
        :param browsing_level: NSFW browsing level (1-31, higher = more permissive)
        :param excluded_tag_ids: List of tag IDs to exclude
        :param cursor: Pagination cursor (None for first page, cursor for next pages)
        :param limit: Optional maximum number of items to return.
                       - Default: 50 (one page)
                       - -1: Fetch all items (unlimited)
                       - >50: Use pagination to fetch multiple pages
        :param max_pages: Maximum number of pages to fetch (safety limit, default: 10)
        :param other_params: Additional parameters to pass to API (disablePoi, disableMinor, etc.)

        :return: Dictionary with 'items' list, optional 'nextCursor', and metadata
        """
        payload = self._build_infinite_images_payload(
            collection_id=collection_id,
            period=period,
            sort=sort,
            browsing_level=browsing_level,
            excluded_tag_ids=excluded_tag_ids,
            cursor=cursor,
            other_params=other_params,
        )

        self._maybe_print_browsing_prefs(payload)

        if self._is_unlimited(limit):
            return self._fetch_infinite_images_page(payload)

        # Default to 50 if limit is None
        actual_limit = limit if limit is not None else 50

        return self._paginate_infinite_images(
            payload=payload,
            limit=actual_limit,
            max_pages=max_pages,
        )


# --- Example Usage ---

if __name__ == "__main__":
    # Version marker to verify we're running updated code
    print("🚀 CivitAI tRPC Client v1.2 (with browsing preferences support)")
    print("=" * 70)

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="CivitAI tRPC API Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python civitai_trpc_v2.py

  # Auto-load browsing settings from API
  python civitai_trpc_v2.py --auto-load-settings
  python civitai_trpc_v2.py -a

  # Verbose mode (show request URLs and response data)
  python civitai_trpc_v2.py --verbose
  python civitai_trpc_v2.py -v

  # Limit number of images returned
  python civitai_trpc_v2.py --limit 10
  python civitai_trpc_v2.py -l 10

  # Combine options
  python civitai_trpc_v2.py -v --auto-load-settings --limit 5
        """,
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Output request URLs and formatted JSON response data",
    )
    parser.add_argument(
        "-a",
        "--auto-load-settings",
        action="store_true",
        help="Automatically load browsing settings from API on startup",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help="Limit the number of images to fetch (default: all)",
    )
    args = parser.parse_args()

    # Initialize with your session token (from the curl 'cookie' header)
    # Note: This token expires. You need to grab a fresh one from your browser.
    SESSION_TOKEN = CIVITAI_SESSION_COOKIE or "eyJ...[paste_your_long_token_here]..."

    # Optional: The fingerprint hash. If you omit this, some requests might fail 403/400.
    # You can extract this from the 'x-fingerprint' header in your browser DevTools.
    FINGERPRINT = "48cc7067da9614a09cdfa515bb51ec3d8d362efa293d0d8f9d15f7c9919bac80cbdaff9cd1e91cd02902e40dd02b38d8"

    client = CivitaiTrpcClient(
        session_token=CIVITAI_SESSION_COOKIE,
        x_fingerprint=FINGERPRINT,
        verbose=args.verbose,
        auto_load_settings=args.auto_load_settings,
    )

    try:
        # 1. If not auto-loaded, manually load browsing settings
        if not args.auto_load_settings:
            print("Fetching browsing settings...")
            settings = client.get_browsing_settings()
            print(f"Found {len(settings)} preset types available")
            # Show available preset types
            for preset in settings:
                nsfw_levels = preset.get("nsfwLevels", [])
                nsfw_sum = (
                    sum(nsfw_levels) if isinstance(nsfw_levels, list) else nsfw_levels
                )
                print(
                    f"  - {preset.get('type', 'unknown')}: nsfwLevels={nsfw_levels} (sum={nsfw_sum})"
                )
            print()

            # Load the "some" preset by default
            print("Loading 'some' preset for defaults...")
            client.load_browsing_settings("some")
            print()

        # 2. Get Collection Details
        collection_id = 10842247
        print(f"Fetching collection {collection_id}...")
        collection_data = client.get_collection_by_id(collection_id)
        print(f"✅ Collection Name: {collection_data.get('name')}")
        print(f"   NSFW Level: {collection_data.get('nsfwLevel')}")
        # Explain what that level means
        bl = collection_data.get("nsfwLevel", 0)
        explanation = client._explain_browsing_level(bl)
        print(
            f"   NSFW Flags: {', '.join([cat for cat, enabled in explanation.items() if enabled])}"
        )
        print()

        # 3. Get Infinite Images (First Page) - using browsing preferences
        print(f"Fetching images for collection {collection_id}...")
        images_data = client.get_infinite_images(
            collection_id=collection_id, limit=args.limit
        )

        items = images_data.get("items", [])
        print(f"✅ Found {len(items)} images")

        # Show first image details as example
        if items:
            first_img = items[0]
            print(f"\n📸 Example image:")
            print(f"   ID: {first_img.get('id')}")
            print(f"   URL: https://civitai.com/images/{first_img.get('id')}")
            print(f"   Name: {first_img.get('name')}")
            nsfwLevel = first_img.get("nsfwLevel", 0)
            print(f"   NSFW Level: {nsfwLevel}")
            explanation = client._explain_browsing_level(nsfwLevel)
            print(
                f"   NSFW Flags: {', '.join([cat for cat, enabled in explanation.items() if enabled])}"
            )
            print(f"   Width: {first_img.get('width')}")
            print(f"   Height: {first_img.get('height')}")

        # Example of how to handle pagination
        next_cursor = images_data.get("nextCursor")
        if next_cursor:
            print(f"\n📄 Next cursor available: {next_cursor}")
            # To get the next page, you would call:
            # images_data_page_2 = client.get_infinite_images(collection_id=collection_id, cursor=next_cursor)

    except CivitaiTrpcError as e:
        print(f"❌ An API error occurred: {e}")
        print(f"❌ Status Code: {e.status_code}")
