#!/usr/bin/env python3
"""Replace fetch_collection_items function in civitai.py"""

with open('civitai.py', 'r') as f:
    content = f.read()

# Find the function
old_func_start = '    def fetch_collection_items(self, collection_id, limit=None):'
old_func_end = '    def fetch_image_basic_info(self, image_id):'

if old_func_start not in content or old_func_end not in content:
    print("Could not find fetch_collection_items function")
    exit(1)

# Calculate positions
start_pos = content.find(old_func_start)
end_pos = content.find(old_func_end)

if start_pos == -1 or end_pos == -1:
    print(f"start_pos={start_pos}, end_pos={end_pos}")
    print("Could not locate function boundaries")
    exit(1)

# Extract content before and after
before = content[:start_pos]
after = content[end_pos:]

# Build new function
new_function = '''    def fetch_collection_items(self, collection_id, limit=None):
        """
        Fetches list of all items in a collection using image.getInfinite.
        Handles pagination automatically.
        """
        endpoint = "image.getInfinite"
        items = []
        cursor = None

        print(f"Fetching collection items for ID: {collection_id}")

        while True:
            # 1. Prepare Payload
            # Merge defaults with current cursor and collection ID
            payload_data = {**self.default_params}
            payload_data["collectionId"] = int(collection_id)
            if cursor:
                payload_data["cursor"] = cursor
            else:
                payload_data["cursor"] = None

            # 2. Build params WITHOUT meta wrapper to avoid overriding cursor
            # If we include meta with cursor: ["undefined"], it will override our cursor value
            params = {"input": json.dumps({"json": payload_data})}

            # 3. Make Request
            response = requests.get(
                f"{self.base_url}/{endpoint}",
                headers=self._get_headers(),
                params=params,
            )

            if response.status_code != 200:
                print(f"Error fetching collection page: {response.status_code}")
                break

            # 4. Parse Data
            data = response.json()
            page_items = self._find_deep_image_list(data)

            if not page_items:
                break  # No more items found

            items.extend(page_items)

            # 5. Check for next cursor
            # tRPC infinite scroll usually returns metadata for the next page
            try:
                # Look for nextCursor in the response structure
                next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
                if next_cursor and next_cursor != cursor:
                    cursor = next_cursor
                else:
                    break  # No more pages
            except Exception:
                # Can't find cursor, stop for stability
                break

        print(f"Found {len(items)} total items.")
        return items
'''

# Write new content
new_content = before + new_function + after

with open('civitai.py', 'w') as f:
    f.write(new_content)

print("✅ Successfully replaced fetch_collection_items function")
print("   - Removed meta wrapper that was overriding cursor")
print("   - Cursor pagination should now work correctly")
