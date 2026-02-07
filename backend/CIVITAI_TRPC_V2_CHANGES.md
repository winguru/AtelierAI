# CivitAI tRPC Client v2 - Changes Summary

## Overview
Enhanced the CivitAI tRPC client with a robust browsing preferences system that integrates with the API's browsing settings.

## Key Changes

### 1. Browsing Preferences System

#### Initialization (`__init__`)
- Added `self.browsing_prefs` dictionary with sensible defaults:
  ```python
  self.browsing_prefs = {
      "period": "AllTime",
      "sort": "Newest",
      "browsingLevel": 1,
      "include": ["cosmetics"],
      "excludedTagIds": [],
      "disablePoi": True,
      "disableMinor": False,
  }
  ```

- Added `auto_load_settings` parameter to automatically load preferences from API on startup

#### New Methods

**`load_browsing_settings(preset_type="some")`**
- Fetches browsing presets from `system.getBrowsingSettingAddons`
- Selects a preset type ("none", "some", etc.)
- **Key Fix**: Calculates `browsingLevel` as **sum** of nsfwLevels array
  - Example: `[1, 2, 4, 8, 16]` → `browsingLevel = 31`
  - This is a bitwise OR of all allowed NSFW categories
- Updates `excludedTagIds`, `disablePoi`, `disableMinor` from preset
- Extracts `generationDefaultValues` into `gen_*` prefixed keys

**`set_browsing_prefs(**kwargs)`**
- Manually override any browsing preference
- Example: `client.set_browsing_prefs(sort="MostLiked", period="Week")`

**`get_browsing_prefs()`**
- Returns a copy of current browsing preferences
- Useful for inspection or debugging

**`_explain_browsing_level(level)`** (helper)
- Explains which NSFW categories are enabled
- Uses bitwise AND to check individual flags:
  - `1` - Mature
  - `2` - Sexual
  - `4` - Violence
  - `8` - Hate
  - `16` - Gore
- Returns dictionary of enabled categories

### 2. Enhanced `get_infinite_images()`

#### Changes:
- Now uses `self.browsing_prefs` as default values
- All parameters are **optional** (None = use preference default)
- Explicit parameters override preferences (None values don't overwrite)
- Accepts `**other_params` for additional API parameters
- Verbose output shows which preferences are active
- Smart cleanup: removes None values and empty collectionId

#### Example Usage:
```python
# Use all defaults from preferences
images = client.get_infinite_images(collection_id=10842247)

# Override specific parameters
images = client.get_infinite_images(
    collection_id=10842247,
    sort="MostLiked",  # Override default sort
    browsing_level=15,  # Override default level
)

# Additional parameters via **other_params
images = client.get_infinite_images(
    collection_id=10842247,
    disablePoi=False,  # Custom param not in signature
    limit=20,
)
```

### 3. Command-Line Arguments

New arguments added:
```bash
--auto-load-settings    # Load browsing settings from API on startup
--verbose / -v         # Show request/response data
--limit N              # Limit images returned to N
```

## Browsing Level Details

The CivitAI API uses **bitwise flags** for NSFW levels:

| Flag | Value | Category | Description |
|-------|--------|-----------|-------------|
| 1     | Mature  | General mature content |
| 2     | Sexual  | Sexual content |
| 4     | Violence| Violence/gore content |
| 8     | Hate    | Hate speech content |
| 16    | Gore    | Extreme gore content |

**Calculation**: Sum the flags to get the browsing level:
- Level 1: Only mature content
- Level 3: Mature + Sexual
- Level 15: All except gore
- Level 31: All categories enabled

## Migration from v1

### Before (v1):
```python
client = CivitaiTrpcClient()
images = client.get_infinite_images(
    collection_id=10842247,
    period="AllTime",  # Required
    sort="Newest",      # Required
    browsing_level=1,     # Required
)
```

### After (v2):
```python
client = CivitaiTrpcClient(auto_load_settings=True)
images = client.get_infinite_images(collection_id=10842247)  # All optional!
```

## Benefits

1. **Cleaner API**: Don't need to repeat defaults for every call
2. **Consistency**: All calls use the same browsing settings
3. **Flexibility**: Can override per-call when needed
4. **API Integration**: Uses real settings from CivitAI
5. **Self-documenting**: Browsing level explanation helps understand what's filtered
6. **Less Code**: Fewer parameters required in common use cases

## Example: Full Workflow

```python
from civitai_trpc_v2 import CivitaiTrpcClient

# Initialize with auto-loaded settings
client = CivitaiTrpcClient(verbose=True, auto_load_settings=True)

# Override a specific preference
client.set_browsing_prefs(sort="MostLiked")

# Get images (uses preferences)
images = client.get_infinite_images(collection_id=10842247, limit=10)

# Check what browsing level means
current_prefs = client.get_browsing_prefs()
explanation = client._explain_browsing_level(current_prefs['browsingLevel'])
print(f"Allowed NSFW: {', '.join([cat for cat, enabled in explanation.items() if enabled])}")
```

## Backward Compatibility

- Existing code continues to work (all parameters have sensible defaults)
- Can migrate incrementally - just remove hardcoded values
- No breaking changes to public API
