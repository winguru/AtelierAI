# Changes Summary

## Unicode Display Width Fix & LoRA Model IDs

### Changes Made

#### 1. `console_utils.py` - Unicode Display Width Support

**Problem:** Japanese/Chinese characters were causing table misalignment because they take 2 display columns in monospace terminals, but the code was counting them as 1 character using `len()`.

**Solution:** Added `wcwidth` library to calculate actual display width.

**New Functions Added:**
- `get_display_width(text)` - Calculates terminal display width accounting for wide Unicode chars
- `truncate_to_width(text, max_width)` - Truncates text to fit within display width
- `pad_to_width(text, target_width)` - Pads text with spaces to reach target width

**Methods Updated:**
- `print_table()` - Now uses display width calculations for column sizing and padding
- `print_wrapped_text()` - Now uses display width for line wrapping

**Example:**
```python
# Before (incorrect)
len("全身貞操帯") = 4  # 4 characters

# After (correct)
get_display_width("全身貞操帯") = 10  # 10 display columns
```

---

#### 2. `civitai.py` - LoRA Model ID Capture

**Problem:** LoRA data only included name and weight, not model ID or version ID.

**Solution:** Updated `_process_resources()` to capture additional LoRA metadata.

**Changed Code:**
```python
# Before
loras.append({"name": name_res, "weight": weight})

# After
loras.append({
    "name": name_res,
    "weight": weight,
    "model_id": model_id,
    "model_version_id": model_version_id,
    "version_name": version_name
})
```

---

#### 3. `analyze_collection.py` - LoRA Model ID Tracking & Display

**Problem:** No way to see which LoRA version was used or link to the model page.

**Solution:** Track and display LoRA model IDs and Civitai URLs.

**New Attribute:**
```python
self.lora_model_ids = {}  # Track model_id and version_id for LoRAs
```

**Updated Table:**
```python
headers = ["LoRA Name", "Usage", "Avg Weight", "Model ID", "URL"]
```

**URL Format:**
```
civitai.com/models/{model_id}?modelVersionId={version_id}
```

**Example Output:**
```
Chastity Belt + Chastity bra / 全身貞操帯  50  0.93  2347342  civitai.com/models/781293?modelVersionId=2347342
```

**Sample Prompts Section Updated:**
Now includes LoRA URLs for easy navigation:
```
- Chastity Belt + Chastity bra / 全身貞操帯 (weight: 0.8) → civitai.com/models/781293?modelVersionId=2347342
```

**JSON Export Updated:**
Added `lora_model_ids` to saved analysis data for programmatic access.

---

## Usage

### Command Line

```bash
# Run collection analysis with Unicode support
python analyze_collection.py --limit -1 --line-length 120 14949699

# Save to JSON with LoRA model IDs
python analyze_collection.py 14949699 --limit 50 --save
```

### Programmatic Access

```python
from analyze_collection import CollectionAnalyzer
from civitai import CivitaiPrivateScraper

# Scrape collection
scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(14949699)

# Analyze
analyzer = CollectionAnalyzer(data)
analyzer.analyze()

# Access LoRA model IDs
lora_info = analyzer.lora_model_ids.get("Chastity Belt + Chastity bra / 全身貞操帯")
print(f"Model ID: {lora_info['model_id']}")
print(f"Version ID: {lora_info['model_version_id']}")
print(f"URL: civitai.com/models/{lora_info['model_id']}?modelVersionId={lora_info['model_version_id']}")
```

---

## Testing

### Unicode Display Test
```bash
python test_unicode_table.py
```

Expected output:
- Table columns properly aligned with Japanese/Chinese characters
- Display width calculations correct

### LoRA Model ID Test
```bash
python analyze_collection.py 14949699 --limit 5 --line-length 120
```

Expected output:
- LoRA table includes "Model ID" and "URL" columns
- URLs are properly formatted and linkable
- Sample prompts section shows LoRA URLs

---

## Dependencies

### New Dependency
```bash
pip install wcwidth
```

**Purpose:** Calculate terminal display width for Unicode characters.

**Why needed:** Japanese, Chinese, and other CJK characters take 2 display columns in monospace terminals, not 1. Standard `len()` function doesn't account for this.

---

## Files Modified

1. **console_utils.py**
   - Added Unicode display width helper functions
   - Updated `print_table()` to use display width
   - Updated `print_wrapped_text()` to use display width

2. **civitai.py**
   - Updated `_process_resources()` to capture LoRA model IDs and version IDs

3. **analyze_collection.py**
   - Added `lora_model_ids` tracking
   - Updated LoRA table to show Model ID and URL
   - Updated sample prompts to show LoRA URLs
   - Updated JSON export to include LoRA model IDs

---

## Example Output

### Before (Unicode Misalignment)
```
Top LoRAs
--------------------------------------------------------------------------------------
LoRA Name                                     Usage  Avg Weight
--------------------------------------------  -----  ----------
Chastity Belt + Chastity bra / 全身貞操帯     50     0.93      ← MISALIGNED
```

### After (Proper Alignment with Model IDs)
```
Top LoRAs
------------------------------------------------------------------------------------------------------------------------
LoRA Name                                                                                         Usage  Avg Weight  Model ID  URL
------------------------------------------------------------------------------------------------  -----  ----------  --------  --------------------
Chastity Belt + Chastity bra / 全身貞操帯                                                         50     0.93        2347342   civitai.com/models/781293?modelVersionId=2347342
chastity belt thin/ cable style / anus cutout                                                     16     0.95        1234567   civitai.com/models/123456?modelVersionId=1234567
```

---

## Notes

1. **Display Width vs Character Length:**
   - Character length (`len()`): Counts code points
   - Display width (`wcswidth()`): Counts terminal columns
   - Important for: CJK characters, emojis, combining diacritics

2. **LoRA ID Priority:**
   - Model ID: Base model identifier (same across all versions)
   - Version ID: Specific version identifier
   - Civitai URL needs both for version-specific links

3. **Backward Compatibility:**
   - Existing code continues to work
   - New fields optional (graceful degradation if missing)
   - JSON export includes new fields alongside existing ones

---

## Future Enhancements

Potential additions:
- Clickable URLs in terminal (requires terminal support)
- Model ID column sortable/filtered
- Direct download links for LoRA models
- Version comparison across collection
- LoRA version tracking over time
