#!/usr/bin/env python3
"""Try using stealth as decorator or old API"""

import asyncio
from playwright.async_api import async_playwright

print("Testing stealth as decorator/context manager...")
print("=" * 60)

# Try import from stealth module (deprecated but might work)
try:
    from playwright_stealth.stealth import async_stealth
    print("✅ Found playwright_stealth.stealth.async_stealth")

    async def test():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            # Apply stealth
            await async_stealth(page)
            print("✅ async_stealth(page) works!")
            await browser.close()

    asyncio.run(test())

except ImportError as e:
    print(f"❌ async_stealth import failed: {e}")

print()

# Try checking if there's a __all__ that tells us the public API
try:
    from playwright_stealth import stealth
    if hasattr(stealth, '__all__'):
        print(f"stealth.__all__ = {stealth.__all__}")
except:
    pass

print()

# Try the Stealth class properly
try:
    from playwright_stealth import Stealth

    print("Stealth class info:")
    import inspect
    print(f"  __init__ signature: {inspect.signature(Stealth.__init__)}")

    # Check if it's a decorator
    if inspect.isclass(Stealth):
        print("  Stealth is a class")

        # Try to see what it expects in __init__
        init_sig = inspect.signature(Stealth.__init__)
        params = list(init_sig.parameters.keys())
        print(f"  __init__ expects: {params}")
