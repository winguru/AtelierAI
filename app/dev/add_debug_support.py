#!/usr/bin/env python3
"""Add debug support to civitai.py"""

import sys

# Read the file
with open('civitai.py', 'r') as f:
    content = f.read()

# 1. Add debug parameter to fetch_collection_items method signature
content = content.replace(
    'def fetch_collection_items(self, collection_id: int, limit: Optional[int] = None) -> List[Dict]:',
    'def fetch_collection_items(self, collection_id: int, limit: Optional[int] = None, debug: bool = False) -> List[Dict]:'
)

# 2. Add debug parameter to scrape method signature
content = content.replace(
    'def scrape(self, collection_id: int, limit: Optional[int] = None) -> List[Dict]:',
    'def scrape(self, collection_id: int, limit: Optional[int] = None, debug: bool = False) -> List[Dict]:'
)

# 3. Add debug printing in fetch_collection_items after payload_data preparation
debug_print = '''
            # DEBUG: Print request details
            if debug:
                print(f"  DEBUG: Request URL: {self.api.base_url}/{endpoint}")
                print(f"  DEBUG: Payload: {json.dumps(payload_data, indent=2)}")
                print(f"  DEBUG: TRPC Payload: {self._build_trpc_payload(payload_data)[:200]}...")
'''

content = content.replace(
    '# 2. Make Request',
    f'# 2. Make Request{debug_print}'
)

# 4. Add session validation check at start of fetch_collection_items
session_check = '''
        # DEBUG: Validate session token
        if debug:
            if not self.api.session_cookie or len(self.api.session_cookie) < 100:
                print(f"  DEBUG: ⚠️  Session token invalid! Length: {len(self.api.session_cookie)}")
            else:
                print(f"  DEBUG: ✅ Session token valid. Length: {len(self.api.session_cookie)}")
'''

content = content.replace(
    'print(f"Fetching collection items for ID: {collection_id}")',
    f'print(f"Fetching collection items for ID: {collection_id}"){session_check}'
)

# Write back
with open('civitai.py', 'w') as f:
    f.write(content)

print("✅ Debug support added to civitai.py")
