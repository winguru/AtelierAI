#!/usr/bin/env python3
"""Test different ways to call playwright_stealth"""

import asyncio
from playwright.async_api import async_playwright

print("Testing different playwright-stealth approaches...")
print("=" * 60)

async def test_approaches():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Approach 1: Try from playwright_stealth import stealth_async (original way)
        print("\n1. Testing: from playwright_stealth import stealth_async")
        try:
            from playwright_stealth import stealth_async
            await stealth_async(page)
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        # Approach 2: Try playwright_stealth.stealth_async
        print("\n2. Testing: playwright_stealth.stealth_async(page)")
        try:
            import playwright_stealth
            await playwright_stealth.stealth_async(page)
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        # Approach 3: Try Stealth class instance
        print("\n3. Testing: Stealth(page).apply()")
        try:
            from playwright_stealth import Stealth
            await Stealth(page).apply()
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        # Approach 4: Try Stealth() then apply method
        print("\n4. Testing: Stealth(page)()")
        try:
            from playwright_stealth import Stealth
            await Stealth(page)()
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        # Approach 5: Try stealth module with .apply_async
        print("\n5. Testing: stealth.apply_async(page)")
        try:
            from playwright_stealth import stealth
            await stealth.apply_async(page)
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        # Approach 6: Try stealth module with sync apply
        print("\n6. Testing: stealth.apply(page) (sync)")
        try:
            from playwright_stealth import stealth
            stealth.apply(page)
            print("   ✅ Works!")
        except Exception as e:
            print(f"   ❌ Failed: {e}")

        await browser.close()

asyncio.run(test_approaches())
print()
print("=" * 60)
print("Test complete!")
