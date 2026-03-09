# Model Availability Detection Feature

## Overview
Added functionality to detect if LoRA models have been deleted or removed from Civitai, and automatically provide links to the Civitai Archive site (civitaiarchive.com) for those models.

## Changes Made

### 1. `civitai_api.py`
Added new method `check_model_availability()`:
- Uses the `modelVersion.getById` API endpoint to check model status
- Returns availability information including:
  - `available`: boolean indicating if model is still available
  - `model_status`: the actual status from Civitai (e.g., "Published", "Deleted")
  - `civitai_url`: link to the model on Civitai
  - `archive_url`: link to the model on civitaiarchive.com
  - `error`: any error message if the check failed

### 2. `analyze_collection.py`

#### `CollectionAnalyzer` class:
- Added `deleted_models` list to track unavailable models
- Added `check_lora_availability()` method that:
  - Iterates through all LoRAs in the collection
  - Checks each one using the API
  - Deduplicates checks based on (model_id, version_id) pairs
  - Returns list of deleted models with usage counts

#### New `_print_deleted_models_section()` function:
- Displays deleted models with clear formatting
- Shows model name, status, IDs, and usage count
- Provides both Civitai URL and Archive URL
- Includes helpful tip about the archive site

#### Updated `print_analysis_report()`:
- Added call to `_print_deleted_models_section()` after the LoRAs section

#### Updated `main()`:
- Added model availability checking after analysis
- Prints summary of findings (deleted models found vs all available)

#### Updated JSON export:
- Added `deleted_models` field to saved analysis data

## Usage

The feature is automatically enabled when running `analyze_collection.py`:

```bash
# Basic usage
python analyze_collection.py <collection_id>

# With limit and wide output
python analyze_collection.py <collection_id> --limit 50 --wide

# Save to JSON (includes deleted models info)
python analyze_collection.py <collection_id> --save
```

## Example Output

When deleted models are found, you'll see:

```
================================================================================
Deleted/Unavailable Models
--------------------------------------------------------------------------------
⚠️  Found 1 model(s) that have been removed from Civitai:

  🗑️  Deepthroat slider Pony/IllustriousXL
    Status: Deleted
    Model ID: 871004
    Version ID: 1498821
    Usage Count: 3
    Civitai URL: https://civitai.com/models/871004?modelVersionId=1498821
    📦 Archive URL: https://civitaiarchive.com/models/871004?modelVersionId=1498821

💡 Tip: The archive site (civitaiarchive.com) may have backups of deleted models.
```

## Technical Details

### API Endpoint Used
```
https://civitai.com/api/trpc/modelVersion.getById?input={"json":{"id":<version_id>,"authed":true}}
```

### Status Detection
The code checks the `model.status` field in the API response:
- `"Published"` - Model is available
- `"Deleted"` - Model has been removed from Civitai
- Other statuses are treated accordingly

### Optimization
- Models are checked only once per unique (model_id, model_version_id) pair
- Uses the existing authenticated API session
- Results are cached in the `CollectionAnalyzer.deleted_models` list

## Future Enhancements

Possible improvements:
- Add `--skip-availability-check` flag to skip this step for faster analysis
- Add progress indicator during model checking
- Support checking base models/checkpoints in addition to LoRAs
- Add ability to export deleted models to a separate CSV file
