import os
import requests
import json
import time
from typing import Dict, List, Optional

class CivitaiPrivateScraper:
    """
    High-level scraper for Civitai collections.
    
    Uses CivitaiAPI for all API communication and focuses on:
    - Collection pagination
    - Data merging (basic info + generation data)
    - Resource processing (models, LoRAs)
    
    Usage:
        scraper = CivitaiPrivateScraper(auto_authenticate=True)
        data = scraper.scrape(collection_id, limit=50)
    """

    def __init__(self, session_cookie=None, auto_authenticate=False):
        """Initialize the scraper with the user's session cookie.

        Args:
            session_cookie: Optional session cookie. If None and auto_authenticate=True,
                          will try to retrieve automatically.
            auto_authenticate: If True, attempts to get session token automatically.
        """
        from src.civitai_api import CivitaiAPI
        
        # Use CivitaiAPI for all API communication
        self.api = CivitaiAPI(session_cookie=session_cookie, auto_authenticate=auto_authenticate)

    # ==========================================
    # Collection Fetching with Pagination
    # ==========================================

    def fetch_collection_items(self, collection_id: int, limit: Optional[int] = None) -> List[Dict]:
        """Fetch collection items with full pagination support.

        Args:
            collection_id: The Civitai collection ID
            limit: Maximum number of items to fetch (None = all)

        Returns:
            List of collection items
        """
        endpoint = "image.getInfinite"
        items = []
        cursor = None
        page_count = 0
        seen_item_ids = set()  # Track seen items to detect duplicates

        print(f"Fetching collection items for ID: {collection_id}")

        while True:
            # Check if we've hit the limit
            if limit is not None and len(items) >= limit:
                print(f"  Reached limit of {limit} items.")
                break

            # 1. Prepare Payload
            payload_data = {**self.api.default_params}
            payload_data["collectionId"] = int(collection_id)
            payload_data["cursor"] = cursor

            params = {"input": self._build_trpc_payload(payload_data)}

            # 2. Make Request
            response = requests.get(
                f"{self.api.base_url}/{endpoint}",
                headers=self.api._get_headers(),
                params=params,
            )

            if response.status_code != 200:
                print(f"Error fetching collection page: {response.status_code}")
                break

            # 3. Parse Data
            data = response.json()
            page_items = self._find_deep_image_list(data)

            if not page_items:
                break  # No more items found

            # Check for duplicates (sign of cursor bug)
            new_item_ids = {item.get("id") for item in page_items}
            duplicate_count = len(new_item_ids & seen_item_ids)

            if duplicate_count > 0:
                print(
                    f"  ⚠️  Cursor pagination bug detected: {duplicate_count}/{len(page_items)} items are duplicates."
                )
                print(
                    f"  Stopping at {len(items)} unique items to avoid infinite loop."
                )
                break

            # Add new items to tracking set
            seen_item_ids.update(new_item_ids)

            # 4. Add items (respect limit)
            remaining = limit - len(items) if limit else None
            if remaining is not None and len(page_items) > remaining:
                page_items = page_items[:remaining]
                items.extend(page_items)
                print(f"  Reached limit of {limit} items.")
                break

            items.extend(page_items)
            page_count += 1
            print(f"  Page {page_count}: Fetched {len(page_items)} items (total: {len(items)})")

            # 5. Check for next cursor
            try:
                next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
                if next_cursor and next_cursor != cursor:
                    cursor = next_cursor
                else:
                    break  # No more pages or cursor stuck
            except Exception:
                break  # Can't find cursor, stop for stability

        print(f"Found {len(items)} total items ({len(seen_item_ids)} unique).")
        return items

    # ==========================================
    # Scraping with Limit Support
    # ==========================================

    def scrape(self, collection_id: int, limit: Optional[int] = None) -> List[Dict]:
        """Scrape collection items with full details.

        Args:
            collection_id: The Civitai collection ID
            limit: Maximum number of items to fetch (None = all)

        Returns:
            List of curated image data with tags and full generation info
        """
        collection_items = self.fetch_collection_items(collection_id, limit)
        if not collection_items:
            return []

        curated_data = []
        print(f"Fetching details for {len(collection_items)} images...")

        for idx, item in enumerate(collection_items):
            img_id = item.get("id")
            print(f"  [{idx+1}/{len(collection_items)}] Processing ID {img_id}...")

            details = self.api.fetch_generation_data(img_id)

            if details:
                merged = self._merge_data(item, details)

                # Fetch tags for this image using CivitaiAPI
                tags = self.api.fetch_image_tags(img_id)
                merged["tags"] = tags
                if tags:
                    print(f"    - Found {len(tags)} tags")

                curated_data.append(merged)

            time.sleep(0.2)

        return curated_data

    # ==========================================
    # Helper Methods
    # ==========================================

    def _build_trpc_payload(self, input_json: Dict) -> str:
        """
        Wraps the input JSON into the structure required by Civitai's tRPC API.
        Structure: {"json": {your_data}, "meta": {"values": {"cursor": ["undefined"]}}}

        The key insight from user's testing is that when the meta values cursor is "undefined",
        the API will only return the first page of values, regardless of the actual cursor value
        sent in the json input paramters. So we should *only* send ["undefined"] when the cursor
        is first null/absent, otherwise do not send any meta data.
        """
        meta_data = (
            {"meta": {"values": {"cursor": ["undefined"]}}}
            if input_json.get("cursor") is None
            else {}
        )
        params = {"input": json.dumps({"json": input_json, **meta_data})}
        return json.dumps(params)

    def _find_deep_image_list(self, obj, depth: int = 0) -> Optional[List]:
        """Recursively finds the list of image objects in the complex tRPC JSON."""
        if depth > 10:
            return None

        # Check if obj is a list of image dictionaries
        if isinstance(obj, list) and len(obj) > 0 and isinstance(obj[0], dict):
            if "id" in obj[0] and obj[0].get("type") == "image":
                return obj

        # Search in list
        if isinstance(obj, list):
            for item in obj:
                res = self._find_deep_image_list(item, depth + 1)
                if res:
                    return res
            return None

        # Search in dict
        if isinstance(obj, dict):
            # Check common keys first
            for key in ["items", "pages"]:
                if key in obj and isinstance(obj[key], list):
                    res = self._find_deep_image_list(obj[key], depth + 1)
                    if res:
                        return res
            # Recursively check all keys
            for key, value in obj.items():
                res = self._find_deep_image_list(value, depth + 1)
                if res:
                    return res
            return None

        return None

    # ==========================================
    # Data Processing Methods
    # ==========================================

    def _get_extension_from_mime(self, mime_type: str) -> str:
        """Maps CivitAI mime types to the desired file extension."""
        if not mime_type:
            return ".jpeg"  # Default fallback

        mime_lower = mime_type.lower()

        if "png" in mime_lower:
            return ".png"
        elif "webp" in mime_lower:
            return ".webp"
        elif "tiff" in mime_lower or "tif" in mime_lower:
            return ".tif"
        elif "mp4" in mime_lower:
            return ".mp4"
        elif "jpeg" in mime_lower or "jpg" in mime_lower:
            return ".jpeg"
        else:
            # Fallback for unknown types
            return ".jpeg"

    def _sanitize_filename_extension(self, name: str, mime_type: str) -> str:
        """
        Checks if the filename already has an extension.
        If it does, it checks if it matches the mime type.
        If it doesn't match or is missing, it appends the correct one.
        """
        if not name:
            return f"unknown{self._get_extension_from_mime(mime_type)}"

        # Get the expected extension based on the API mime type
        target_ext = self._get_extension_from_mime(mime_type)

        # Split the current name into root and extension
        base_name, current_ext = os.path.splitext(name)

        # Normalize current extension to lowercase for comparison
        if current_ext:
            current_ext = current_ext.lower()

        # Scenario 1: No extension exists (e.g. "image_name")
        if not current_ext:
            return f"{base_name}{target_ext}"

        # Scenario 2: Extension matches target (e.g. name="img.jpeg", mime="image/jpeg")
        if current_ext == target_ext:
            return name

        # Scenario 3: Extension mismatch (e.g. name="img.jpeg", mime="image/png")
        # We strip the wrong extension and add the correct one derived from mime type
        return f"{base_name}{target_ext}"

    def _get_resource_type(self, res: Dict) -> str:
        """Determines the resource type with fallback detection."""
        type_ = res.get("modelType")
        if type_:
            return type_.lower()

        type_ = res.get("type")
        if type_:
            return type_.lower()

        name_res = res.get("modelName", "").lower()
        if "lora" in name_res:
            return "lora"
        elif "checkpoint" in name_res:
            return "checkpoint"

        return "model"

    def _process_resources(self, resources: List[Dict]) -> tuple:
        """Extracts model and lora information from resources list.

        Returns:
            tuple: (model_name, model_version, loras_list)
        """
        model_name = "Unknown"
        model_version = ""
        loras = []

        for res in resources:
            type_lower = self._get_resource_type(res)
            name_res = res.get("modelName")
            weight = res.get("strength") or 1.0
            version_name = res.get("versionName")
            model_id = res.get("modelId")
            model_version_id = res.get("modelVersionId") or res.get("versionId")

            if type_lower == "lora":
                loras.append(
                    {
                        "name": name_res,
                        "weight": weight,
                        "model_id": model_id,
                        "model_version_id": model_version_id,
                        "version_name": version_name,
                    }
                )
            elif type_lower == "checkpoint":
                model_name = name_res
                model_version = version_name or ""
            elif type_lower == "model" and (not model_name or model_name == "Unknown"):
                model_name = name_res
                model_version = version_name or ""

        return model_name, model_version, loras

    def _merge_data(self, collection_item: Dict, generation_data: Dict) -> Dict:
        """Merges the lite collection item with the full generation data.

        Args:
            collection_item: Basic image data from collection
            generation_data: Full generation data from API

        Returns:
            Merged dictionary with all image information
        """
        # Extract Base Info
        image_id = collection_item.get("id")
        image_hash = collection_item.get("url")
        raw_name = collection_item.get("name")
        mime_type = collection_item.get("mimeType", "image/jpeg")

        # Use the helper to ensure we have exactly one correct extension
        safe_name = self._sanitize_filename_extension(raw_name, mime_type)

        # Construct Image URL
        image_url = f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{image_hash}/original=true/quality=90/{safe_name}"

        # Get author - try multiple fields
        author_name = (
            collection_item.get("username")
            or collection_item.get("user", {}).get("username")
            or collection_item.get("account", {}).get("username")
            or "Unknown"
        )

        # Extract Meta & Resources
        meta = generation_data.get("meta") or {}
        resources = generation_data.get("resources", [])

        # Inject resources for easier processing
        meta["resources"] = resources

        # Process Resources (Models/Loras)
        model_name, model_version, loras = self._process_resources(resources)

        return {
            "image_id": image_id,
            "image_url": image_url,
            "author": author_name,
            "tags": [],  # Will be populated by scrape() method
            "prompt": meta.get("prompt", ""),
            "negative_prompt": meta.get("negativePrompt", ""),
            "model": model_name,
            "model_version": model_version,
            "loras": loras,
            "sampler": meta.get("sampler", ""),
            "steps": meta.get("steps", ""),
            "cfg_scale": meta.get("cfgScale", ""),
            "seed": meta.get("seed", ""),
            "raw_meta_json": meta,
        }


# --- USAGE EXAMPLE ---
if __name__ == "__main__":
    # 1. Configuration
    MY_COLLECTION_ID = 11035255

    # Use automatic authentication
    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # 3. Run Scrape
    data = scraper.scrape(MY_COLLECTION_ID, limit=50)

    # 4. Output
    if data:
        print("\n--- SUCCESS ---")
        print(f"Scraped {len(data)} images.")

        URLs = [item["image_url"] for item in data]
        print("Image URLs:")
        for url in URLs:
            print(url)
    else:
        print("No data found.")
