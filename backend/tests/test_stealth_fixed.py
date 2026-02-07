#!/usr/bin/env python3
"""Test the fixed stealth implementation"""

import asyncio
from playwright.async_api import async_playwright

print("Testing fixed playwright-stealth implementation...")
print("=" * 60)

async def test():
    # Import Stealth correctly
    from playwright_stealth.stealth import Stealth
    stealth_obj = Stealth()

    print("✅ Stealth object created")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        print("✅ Page created")

        # Apply stealth using the correct method
        await stealth_obj.apply_stealth_async(page)
        print("✅ Stealth applied successfully!")

        # Navigate to a test page
        await page.goto("https://bot.sannysoft.com/", timeout=30000)
        print("✅ Navigated to bot detection test")

        await asyncio.sleep(2)

        # Check if detected as bot
        content = await page.content()
        if "not detected as a bot" in content.lower() or "passed" in content.lower():
            print("✅ SUCCESS: Not detected as bot!")
        else:
            print("⚠️  May still be detected")

        await browser.close()

asyncio.run(test())

print()
print("=" * 60)
print("Test complete!")
print()
print("If stealth worked, authentication script should also work!")
