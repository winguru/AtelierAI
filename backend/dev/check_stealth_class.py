#!/usr/bin/env python3
"""Deep dive into Stealth class"""

import inspect
from playwright_stealth import Stealth

print("Investigating Stealth class...")
print("=" * 60)

# Check __init__ signature
print("1. Stealth.__init__ signature:")
try:
    init_sig = inspect.signature(Stealth.__init__)
    print(f"   {init_sig}")
except Exception as e:
    print(f"   Error: {e}")

print()

# Check if __call__ exists
print("2. Is Stealth callable? (has __call__)")
if hasattr(Stealth, '__call__'):
    print("   ✅ Yes!")
    try:
        call_sig = inspect.signature(Stealth.__call__)
        print(f"   Signature: {call_sig}")
    except Exception as e:
        print(f"   Error: {e}")
else:
    print("   ❌ No")

print()

# List all methods (excluding special ones)
print("3. All public methods:")
for name, obj in inspect.getmembers(Stealth, predicate=inspect.isfunction):
    if not name.startswith('_'):
        try:
            sig = inspect.signature(obj)
            print(f"   - {name}{sig}")
        except:
            print(f"   - {name}")

print()

# Try instantiating without arguments
print("4. Can we instantiate Stealth() with no args?")
try:
    stealth_obj = Stealth()
    print("   ✅ Yes! Created instance")
    print(f"   Instance type: {type(stealth_obj)}")

    # What methods does it have now?
    print("   Instance methods:")
    for name, obj in inspect.getmembers(stealth_obj):
        if not name.startswith('_') and (inspect.ismethod(obj) or inspect.isfunction(obj)):
            print(f"     - {name}")
except Exception as e:
    print(f"   ❌ No: {e}")

print()

# Try instantiating with a page (to see what it expects)
print("5. What if we pass a page object?")
print("   (We'll create a fake page to check)")
class FakePage:
    pass

try:
    stealth_obj = Stealth(FakePage())
    print("   ✅ Stealth accepts page object")
except TypeError as e:
    print(f"   ❌ TypeError: {e}")
    print("   This suggests Stealth might be a decorator or context manager")
