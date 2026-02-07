#!/usr/bin/env python3
"""Test script to ensure civitai_trpc module is reloaded with latest changes."""

import sys
import importlib.util

# Force clear any cached imports
if 'civitai_trpc' in sys.modules:
    del sys.modules['civitai_trpc']

# Load the module from file
spec = importlib.util.spec_from_file_location("civitai_trpc", "src/civitai_trpc.py")
if spec is None:
    raise ImportError("Could not find civitai_trpc module at src/civitai_trpc.py")
if spec.loader is None:
    raise ImportError("Could not load civitai_trpc module: no loader available")
module = importlib.util.module_from_spec(spec)
sys.modules['civitai_trpc'] = module
spec.loader.exec_module(module)

# Now run the main code from the module
if __name__ == "__main__":
    # Simulate command line args
    sys.argv = ["test_civitai_trpc.py", "--verbose"]

    # Run the module's main code
    # The __main__ block in civitai_trpc.py won't run since we imported it as a module
    # So we'll create a client and test it manually

    from civitai_trpc import CivitaiTrpcClient, CivitaiTrpcError
    from config import CIVITAI_SESSION_COOKIE

    print("🚀 TEST: CivitAI tRPC Client (fresh import)")
    print("=" * 70)

    FINGERPRINT = "48cc7067da9614a09cdfa515bb51ec3d8d362efa293d0d8f9d15f7c9919bac80cbdaff9cd1e91cd02902e40dd02b38d8"

    client = CivitaiTrpcClient(
        session_token=CIVITAI_SESSION_COOKIE,
        x_fingerprint=FINGERPRINT,
        verbose=True
    )

    try:
        # Test collection by id
        collection_id = 10842247
        print(f"\n📋 Testing collection.getByID({collection_id})...")
        collection_data = client.get_collection_by_id(collection_id)
        print(f"\n✅ Collection Name: {collection_data.get('name')}")
        print(f"✅ Collection Data Type: {type(collection_data)}")
        print(f"✅ Collection Keys: {list(collection_data.keys()) if collection_data else 'N/A'}")

        # Test infinite images
        print("\n🖼️  Testing image.getInfinite (limit=2)...")
        images_data = client.get_infinite_images(
            collection_id=collection_id,
            period="AllTime",
            sort="Newest",
            limit=2
        )

        items = images_data.get("items", [])
        print(f"✅ Found {len(items)} images")

        if items:
            print(f"✅ First image ID: {items[0].get('id')}")
            print(f"✅ First image name: {items[0].get('name')}")

    except CivitaiTrpcError as e:
        print(f"❌ An API error occurred: {e}")
        print(f"❌ Status Code: {e.status_code}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
