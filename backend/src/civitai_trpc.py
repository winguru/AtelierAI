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

    def _request(self, procedure: str, payload: Dict[str, Any]) -> Any:
        """
        Internal method to call a tRPC procedure.
        """
        url = f"{self.BASE_URL}/{procedure}"

        # Add dynamic timestamp header if needed (observed in curl)
        headers = {
            **self.default_headers,
            "x-client-date": str(int(time.time() * 1000)),
        }

        # Construct the 'input' query parameter
        params = {"input": self._prepare_input_param(payload)}

        # Verbose output: Print request URL (pre-URL-encoded)
        if self.verbose:
            print(f"\n{'='*70}")
            print(f"📡 REQUEST: {procedure}")
            print(f"{'='*70}")
            # Build pre-encoded URL for display
            display_params = {**params}
            pretty_url = f"{url}?input={display_params['input']}"
            print(f"URL: {pretty_url}")
            print(f"Payload: {json.dumps(payload, indent=2)}")

        # FIX: Use GET instead of POST - CivitAI tRPC API uses GET requests
        response = self.session.get(url, headers=headers, params=params)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise CivitaiTrpcError(
                f"HTTP Error: {e.response.status_code}",
                status_code=e.response.status_code,
                raw_body=e.response.text,
            )

        data = response.json()

        # Verbose output: Print response data
        if self.verbose:
            print(f"\n📥 RESPONSE (Status {response.status_code}):")
            print(f"{'='*70}")
            print(json.dumps(data, indent=2))
            print(f"{'='*70}\n")

        # tRPC error handling
        # The error structure in tRPC is often: { "error": { "json": { "message": "...", "code": ... } } }
        # Response can be either a list or a dict
        if isinstance(data, list):
            if len(data) > 0 and "error" in data[0]:
                error_data = data[0]["error"].get("json", {})
                raise CivitaiTrpcError(
                    f"tRPC Error: {error_data.get('message', 'Unknown error')}"
                )
        elif isinstance(data, dict) and "error" in data:
            error_data = data["error"]
            raise CivitaiTrpcError(
                f"tRPC Error: {error_data.get('message', 'Unknown error')}"
            )

        # Successful response usually looks like: { "result": { "data": { "json": ... } } }
        # Or sometimes: [{ "result": { "data": { "json": ... } } }]
        # We unwrap this to return the actual data directly.
        if isinstance(data, list) and len(data) > 0:
            return data[0].get("result", {}).get("data", {}).get("json")
        elif isinstance(data, dict):
            return data.get("result", {}).get("data", {}).get("json")

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

    def _find_preset(self, settings: List[Dict[str, Any]], preset_type: str) -> Dict[str, Any]:
        """
        Find a preset by type, or return the first available preset.

        :param settings: List of available presets
        :param preset_type: The type of preset to find
        :return: The chosen preset dictionary
        """
        for preset in settings:
            if preset.get("type") == preset_type:
                return preset

        if not settings:
            return {}

        if self.verbose:
            print(f"⚠️  Preset type '{preset_type}' not found, using first available")
        return settings[0]

    def _apply_preset_to_prefs(self, preset: Dict[str, Any]) -> None:
        """
        Apply preset values to browsing preferences.

        :param preset: The preset dictionary from the API
        """
        # Map preset keys to preference keys
        key_mapping = {
            "nsfwLevels": "browsingLevel",
            "excludedTagIds": "excludedTagIds",
            "disablePoi": "disablePoi",
            "disableMinor": "disableMinor"
        }

        for api_key, pref_key in key_mapping.items():
            if api_key not in preset:
                continue

            if pref_key == "browsingLevel" and isinstance(preset[api_key], list) and preset[api_key]:
                self.browsing_prefs[pref_key] = preset[api_key][0]
            else:
                self.browsing_prefs[pref_key] = preset[api_key]

        # Apply generation defaults
        if "generationDefaultValues" in preset:
            for key, value in preset["generationDefaultValues"].items():
                self.browsing_prefs[f"gen_{key}"] = value

    def _print_loaded_prefs(self, preset_type: str) -> None:
        """
        Print loaded preferences in verbose mode.

        :param preset_type: The type of preset that was loaded
        """
        if not self.verbose:
            return

        print(f"✅ Loaded browsing preferences from '{preset_type}' preset:")
        print(f"   browsingLevel: {self.browsing_prefs.get('browsingLevel')}")
        print(f"   excludedTagIds: {len(self.browsing_prefs.get('excludedTagIds', []))} tags")
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
        chosen_preset = self._find_preset(settings, preset_type or "some")
        self._apply_preset_to_prefs(chosen_preset)
        self._print_loaded_prefs(preset_type or "some")

    def set_browsing_prefs(self, **kwargs) -> None:
        """
        Manually override browsing preferences.

        Any key passed here will update the internal preferences.
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

    def get_infinite_images(
        self,
        collection_id: Optional[int] = None,
        period: str = "AllTime",
        sort: str = "Newest",
        browsing_level: int = 1,
        excluded_tag_ids: Optional[List[int]] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Corresponds to 'image.getInfinite'

        :param cursor: The pagination cursor. Pass None for the first request.
                       Pass the 'nextCursor' from the previous response for subsequent pages.
        :param limit: Optional maximum number of items to return. If None, returns all items.
        """
        payload = {
            "collectionId": collection_id,
            "period": period,
            "sort": sort,
            "browsingLevel": browsing_level,
            "include": ["cosmetics"],  # Observed in curl
            "excludedTagIds": excluded_tag_ids or [],
            "disablePoi": True,  # Observed in curl
            "disableMinor": False,  # Observed in curl (unless overridden)
            "cursor": cursor,
            "authed": True,
        }

        # Clean up None keys to keep payload minimal
        payload = {k: v for k, v in payload.items() if v is not None}

        response = self._request("image.getInfinite", payload)

        # Apply limit if specified
        if limit is not None and response and "items" in response:
            items = response["items"]
            if len(items) > limit:
                response["items"] = items[:limit]
                # Clear nextCursor if we're limiting results
                response["nextCursor"] = None
                if self.verbose:
                    print(f"⚠️  Limited results to {limit} items (out of {len(items)} total)")

        return response


# --- Example Usage ---

if __name__ == "__main__":
    # Version marker to verify we're running updated code
    print("🚀 CivitAI tRPC Client v1.1 (with verbose and limit support)")
    print("=" * 70)

    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="CivitAI tRPC API Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python civitai_trpc.py

  # Verbose mode (show request URLs and response data)
  python civitai_trpc.py --verbose
  python civitai_trpc.py -v

  # Limit number of images returned
  python civitai_trpc.py --limit 10

  # Combine options
  python civitai_trpc.py -v --limit 5
        """
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Output request URLs and formatted JSON response data"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of images to fetch (default: all)"
    )
    args = parser.parse_args()

    # 1. Initialize with your session token (from the curl 'cookie' header)
    # Note: This token expires. You need to grab a fresh one from your browser.
    SESSION_TOKEN = CIVITAI_SESSION_COOKIE or "eyJ...[paste_your_long_token_here]..."

    # Optional: The fingerprint hash. If you omit this, some requests might fail 403/400.
    # You can extract this from the 'x-fingerprint' header in your browser DevTools.
    FINGERPRINT = "48cc7067da9614a09cdfa515bb51ec3d8d362efa293d0d8f9d15f7c9919bac80cbdaff9cd1e91cd02902e40dd02b38d8"

    client = CivitaiTrpcClient(
        session_token=CIVITAI_SESSION_COOKIE,
        x_fingerprint=FINGERPRINT,
        verbose=args.verbose
    )

    try:
        # 2. Get Browsing Settings (to see what defaults/IDs are available)
        print("Fetching browsing settings...")
        settings = client.get_browsing_settings()
        # print(json.dumps(settings, indent=2))

        # 3. Get Collection Details
        collection_id = 10842247
        print(f"\nFetching collection {collection_id}...")
        collection_data = client.get_collection_by_id(collection_id)
        print(f"Collection Name: {collection_data.get('name')}")

        # 4. Get Infinite Images (First Page)
        print(f"\nFetching images for collection {collection_id}...")
        images_data = client.get_infinite_images(
            collection_id=collection_id,
            period="AllTime",
            sort="Newest",
            limit=args.limit
        )

        items = images_data.get("items", [])
        print(f"Found {len(items)} images.")

        # Example of how to handle pagination
        next_cursor = images_data.get("nextCursor")
        if next_cursor:
            print(f"Next cursor available: {next_cursor}")
            # To get the next page, you would call:
            # images_data_page_2 = client.get_infinite_images(collection_id=collection_id, cursor=next_cursor)

    except CivitaiTrpcError as e:
        print(f"An API error occurred: {e}")
        print(f"Status Code: {e.status_code}")
