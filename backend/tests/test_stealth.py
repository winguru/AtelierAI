#!/usr/bin/env python3
"""Quick test to verify playwright-stealth installation"""

import inspect

print("Testing playwright-stealth installation...")
print("=" * 60)

# Test 1: Import check
try:
    import playwright_stealth

    # Try to find the stealth function
    if hasattr(playwright_stealth, 'stealth_async'):
        stealth_async = playwright_stealth.stealth_async
        print("✅ playwright-stealth.stealth_async found")
    elif hasattr(playwright_stealth, 'stealth'):
        stealth_obj = playwright_stealth.stealth
        if hasattr(stealth_obj, '__call__'):
            # It's callable - might be function or class
            async def stealth_async(page):
                # Wrapper to make it async
                if inspect.iscoroutinefunction(stealth_obj):
                    return await stealth_obj(page)
                else:
                    return stealth_obj(page)
            print("✅ playwright-stealth.stealth found (callable)")
        elif hasattr(stealth_obj, 'async_stealth'):
            stealth_async = stealth_obj.async_stealth
            print("✅ playwright-stealth.stealth.async_stealth found")
        else:
            print("❌ Cannot determine how to use stealth")
            exit(1)
    else:
        print("❌ No stealth function found in playwright-stealth")
        exit(1)
except ImportError as e:
    print(f"❌ Cannot import playwright-stealth: {e}")
    print("\nInstall it with: pip install playwright-stealth")
    exit(1)

# Test 2: Check version
try:
    import playwright_stealth
    print(f"✅ playwright-stealth version: {playwright_stealth.__version__}")
except:
    print("⚠️  Could not get version (this is OK)")

# Test 3: Test with a simple page
print("\nTesting stealth mode with Playwright...")
print("=" * 60)

import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # Apply stealth
        await stealth_async(page)
        print("✅ Stealth mode applied successfully")

        # Navigate to a page
        await page.goto("https://bot.sannysoft.com/", timeout=30000)
        print("✅ Navigated to bot detection test page")

        # Wait a moment
        await asyncio.sleep(2)

        # Check result
        content = await page.content()
        if "You are not detected as a bot" in content or "not detected" in content.lower():
            print("✅ Stealth mode working - not detected as bot!")
        else:
            print("⚠️  May still be detected as bot")

        await browser.close()

asyncio.run(test())

print("\n" + "=" * 60)
print("Test complete!")
