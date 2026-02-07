#!/usr/bin/env python3
"""Check what's inside playwright_stealth.stealth module"""

import inspect
import playwright_stealth
from playwright_stealth import stealth

print("Checking playwright_stealth.stealth module...")
print("=" * 60)

# List all available functions/classes in the stealth module
print("Available in playwright_stealth.stealth:")
for name, obj in inspect.getmembers(stealth):
    if not name.startswith('_'):
        print(f"  - {name}: {type(obj).__name__}")

print()

# Check for common stealth function names
for name in ['stealth', 'stealth_async', 'async_stealth', 'Stealth']:
    if hasattr(stealth, name):
        obj = getattr(stealth, name)
        print(f"✅ Found {name}:")
        print(f"   Type: {type(obj)}")
        if inspect.isfunction(obj):
            sig = inspect.signature(obj)
            print(f"   Signature: {sig}")
        elif inspect.isclass(obj):
            print(f"   Class methods:")
            for m_name, m_obj in inspect.getmembers(obj):
                if not m_name.startswith('_') and inspect.ismethod(m_obj) or inspect.isfunction(m_obj):
                    print(f"     - {m_name}")
        print()

# Check if Stealth class exists
print("Checking playwright_stealth.Stealth class:")
if hasattr(playwright_stealth, 'Stealth'):
    Stealth = playwright_stealth.Stealth
    print(f"  ✅ Stealth class exists")
    print(f"   Type: {type(Stealth)}")
    print(f"   Constructor: {inspect.signature(Stealth.__init__)}")
    print()
    print("  Stealed class methods:")
    for name, obj in inspect.getmembers(Stealth, predicate=inspect.isfunction):
        if not name.startswith('_'):
            print(f"     - {name}{inspect.signature(obj)}")
