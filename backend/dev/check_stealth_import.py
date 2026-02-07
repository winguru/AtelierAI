#!/usr/bin/env python3
"""Check what's available in playwright-stealth"""

import playwright_stealth
import inspect

print("Checking playwright-stealth module structure...")
print("=" * 60)

# Check module's __all__ if it exists
if hasattr(playwright_stealth, '__all__'):
    print(f"__all__ exports: {playwright_stealth.__all__}")
    print()

# List all available functions/classes in the module
print("Available functions/classes:")
for name, obj in inspect.getmembers(playwright_stealth):
    if not name.startswith('_'):
        print(f"  - {name}: {type(obj).__name__}")

print()

# Check if stealth_async exists
print("Checking for stealth_async:")
if hasattr(playwright_stealth, 'stealth_async'):
    print("  ✅ stealth_async exists!")
    print(f"     Type: {type(playwright_stealth.stealth_async)}")
    if inspect.isfunction(playwright_stealth.stealth_async):
        sig = inspect.signature(playwright_stealth.stealth_async)
        print(f"     Signature: {sig}")
else:
    print("  ❌ stealth_async NOT found")

# Check if stealth exists
print()
print("Checking for stealth:")
if hasattr(playwright_stealth, 'stealth'):
    print("  ✅ stealth exists!")
    print(f"     Type: {type(playwright_stealth.stealth)}")
    if inspect.isfunction(playwright_stealth.stealth):
        sig = inspect.signature(playwright_stealth.stealth)
        print(f"     Signature: {sig}")
else:
    print("  ❌ stealth NOT found")
