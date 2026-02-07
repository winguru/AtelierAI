from typing import Any, Dict, List, Optional

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
        # Build payload from browsing preferences, with parameter overrides
        payload = {
            **self.browsing_prefs,  # Start with defaults from browsing settings
            "collectionId": collection_id,  # Collection filter (from param)
            "cursor": cursor,  # Pagination cursor
            "authed": True,  # Always authenticated
            **other_params,  # Additional explicit params (e.g., disablePoi=True)
        }

        # Override with explicit parameters (None values won't overwrite defaults)
        for key, value in [
            ("period", period),
            ("sort", sort),
            ("browsingLevel", browsing_level),
            ("excludedTagIds", excluded_tag_ids),
        ]:
            if value is not None:
                payload[key] = value

        # Clean up None keys to keep payload minimal
        payload = {k: v for k, v in payload.items() if v is not None}

        # Special handling: exclude keys that shouldn't be sent if None
        # (already handled above, but ensure we don't send empty collectionId)
        if payload.get("collectionId") is None:
            payload.pop("collectionId", None)

        if self.verbose:
            print(f"📊 Using browsing preferences:")
            print(f"   period: {payload.get('period')}")
            print(f"   sort: {payload.get('sort')}")
            print(f"   browsingLevel: {payload.get('browsingLevel')}")
            print(f"   excludedTagIds: {len(payload.get('excludedTagIds', []))} tags")

        # Handle pagination based on limit
        if limit == -1 or limit is None:
            # No limit - return all from first page
            response = self._request("image.getInfinite", payload)
            return response

        # Accumulate items across pages to reach requested limit
        all_items = []
        current_cursor = None
        total_fetched = 0
        pages_fetched = 0

        while total_fetched < limit:
            # Use cursor for subsequent pages
            if current_cursor:
                payload["cursor"] = current_cursor

            # Fetch this page
            response = self._request("image.getInfinite", payload)
            items = response.get("items", [])
            pages_fetched += 1

            if self.verbose and pages_fetched > 1:
                print(f"📄 Fetching page {pages_fetched}...")

            # Add items to our collection
            all_items.extend(items)
            total_fetched += len(items)

            # Check if we're done
            if total_fetched >= limit:
                # Trim excess items if we overshot
                excess = total_fetched - limit
                if excess > 0:
                    all_items = all_items[:-excess]
                    if self.verbose:
                        print(f"✅ Reached limit of {limit} items (fetched {total_fetched - excess} + {excess} on last page)")
                break

            # Check for next page
            current_cursor = response.get("nextCursor")
            if not current_cursor:
                if self.verbose:
                    print(f"✅ No more pages available (fetched {total_fetched} total items)")
                break

            # Safety check to prevent infinite loops
            if pages_fetched >= max_pages:
                if self.verbose:
                    print(f"⚠️  Stopping after {pages_fetched} pages (max_pages limit)")
                break

        # Return aggregated results
        return {
            "items": all_items,
            "nextCursor": current_cursor,  # Still valid if there are more items
            "totalFetched": total_fetched,
            "pagesFetched": pages_fetched,
        }
