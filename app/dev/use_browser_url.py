#!/usr/bin/env python3
"""
Use the EXACT URL and headers from your browser.
Copy all cookies from your browser for this to work.
"""

import requests
import json
from urllib.parse import unquote, parse_qs, urlparse

print("=" * 70)
print("Browser URL Scraper")
print("=" * 70)
print()

# The EXACT URL from your browser
url = "https://civitai.com/api/trpc/image.getInfinite?input=%7B%22json%22%3A%7B%22collectionId%22%3A12176069%2C%22period%22%3A%22AllTime%22%2C%22sort%22%3A%22Newest%22%2C%22browsingLevel%22%3A31%2C%22include%22%3A%5B%22cosmetics%22%5D%2C%22excludedTagIds%22%3A%5B415792%2C426772%2C5188%2C5249%2C130818%2C130820%2C133182%2C5351%2C306619%2C154326%2C161829%2C163032%5D%2C%22disablePoi%22%3Atrue%2C%22disableMinor%22%3Atrue%2C%22cursor%22%3Anull%2C%22authed%22%3Atrue%7D%2C%22meta%22%3A%7B%22values%22%3A%7B%22cursor%22%3A%5B%22undefined%22%5D%7D%7D%7D"

print("Option 1: Try without any cookies (public access)")
print("-" * 70)

headers_basic = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://civitai.com/",
}

response = requests.get(url, headers=headers_basic)

if response.status_code == 200:
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    
    print(f"Status: {response.status_code}")
    print(f"Items found: {len(items)}")
    
    if len(items) > 0:
        print()
        print("✅ SUCCESS! Found items without authentication!")
        print()
        print("Sample item:")
        print(json.dumps(items[0], indent=2))
        
        # Save to file
        with open("test_output.json", "w") as f:
            json.dump(data, f, indent=2)
        print()
        print("Full response saved to test_output.json")
    else:
        print()
        print("❌ No items found")
        print("Full response:")
        print(json.dumps(data, indent=2))
else:
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500]}")

print()
print("=" * 70)
print()

print("Option 2: Try with session token from cache")
print("-" * 70)

import os
from config import CIVITAI_SESSION_CACHE

if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, 'r') as f:
        token = f.read().strip()
else:
    from config import MY_SESSION_COOKIE
    token = MY_SESSION_COOKIE

token='eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..wKtqbAqhC3tk5uxc.o983_52dL5d7H38EMPkyeJjTZUT1zmhY9ij5cyKxDQpy8XuM79uyvIESSc-kHgLQWS6Vl8-p-6seLChqeOYws9fk8wLoFDcOGkx2rFUkTDeLLpmXmChIv1GA6gDK3IoxB0ehIAfzg39pid_snVZH4X4ApuI-B5BZCQt3ygRsfk6bV-6VnxRPuBNzXnLsbdIQOzCnn2_QNdzuvfobCkvOkqDG9T4xBp5IU2-WSIo_7eyUxLDhr3XgXA2NrSULTBR852y89bUlBgaUDX_PXCEvpWgi7NLAWgCPP9cW5WjbX5eftOOAqLaZR5h1PTZ21QV0U__64Lo1zxhlK0wrZ8aNF-0JoNrimB3fqijrpKVuBXu3JcR_NUAwWBHo-Fl5V_KXX_5S3yrt7pkyO8moEXFJW7aTOy3fNJ7a6A7d4at-kKVHSF4VK6kU2NiFXMRbzl-Zx_rZNcLGiSdqdxYIIbHWjCLWj9lZKZN5mvZ4TrxAE9clgpuM2fiqZTBVhxdgv0xI4AWOp-oY82Nv3fiOyCQJ2SeSlfoCSRj0NzL8n9f8rlqbzU6JTmCkO9tEqW0WBlzNNmnVtFxWHT6LGEFPVw0-fMUKDlg2ZWEj3rXSiatTQ3Kf_GVuLCK5_b4zb4XTQWLYaPgBmqhcLjHXOhMIufvUosWyEl-_N3GamSUpgZIi_F0YAZ5aySQ0wo_vG4LZkqLov_pkPdBOaVtbYHPXM0UtkpyyVnSyLNW7gxI4kcEVIiFM4IJkFRbg07z3E4NhcEE54GglWZaLcrD98mwsenhdRGsc4SD_aaLgUH77oDn-rsW1b0tFvmr3K1nDD9dbAQmZTz0kKRPtxzUkiSx-Tn2VDh_X57Dwwib3oav0zymCrDh6kRWapkjvw00TsFQWe99NJhc6GcDeFE3WS-oXkQfDXYjLGDTWLQ8gkB14bymQvgYfsxAh7l87QNuomRIVmYswUfJpxsAEwvBzzKBaoLuIETfHFYGHB_UHQiIL1_bCDNXPWX6dx53xsXh6NFHhbk7YMA9msi4Tng5FjvTnzLNaD1RFoXitai9ebJ_7PPPYfdJwSf_ey4DMjAldiUFLGY9YY_TddgPnIi-Hnbk0jgpL1thOW1p4K6QnvvdZDRrs62CDV1h0muk77WdQ3zHptJ9cWjVqIv1hbbn2Jp--eKyFL0eTWVF8p5vhSDTyfic9dbviaMNucyx1Nxg_0v736g4KMaZj__fNFa07403ePY3sk9dt34_7mq70J32O6nWPtjWnvUyXDZfqtkk9BHYASoMdH0NGP-rur4cVJMil6ASBVJq94PkQU0QWWhsPgQn47UpETa7PS6Zxku-FpBeRaRuMnA.O7060zbJLjE4jIRLZf3mqw'
headers_with_token = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://civitai.com/",
    "Cookie": f"____Secure-civitai-token={token}",
}

response = requests.get(url, headers=headers_with_token)

if response.status_code == 200:
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    
    print(f"Status: {response.status_code}")
    print(f"Items found: {len(items)}")
    
    if len(items) > 0:
        print()
        print("✅ SUCCESS! Found items with session token!")
        print()
        print("Sample item:")
        print(json.dumps(items[0], indent=2))
        
        # Save to file
        with open("test_output.json", "w") as f:
            json.dump(data, f, indent=2)
        print()
        print("Full response saved to test_output.json")
    else:
        print()
        print("❌ No items found")
else:
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500]}")

print()
print("=" * 70)
