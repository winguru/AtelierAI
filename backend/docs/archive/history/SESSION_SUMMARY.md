# Session Summary: Console Formatter & Collection Analyzer

## What Was Built

### 1. ConsoleFormatter Utility (`console_utils.py`)

A centralized, configurable console formatting utility for consistent output across all test scripts.

**Key Features:**
- Configurable line length (default: 70 chars)
- Separator lines (headers, subheaders, custom)
- Status messages (success ✅, error ❌, warning ⚠️, info ℹ️)
- Key-value pairs with alignment
- Tables with auto-calculated column widths
- Numbered lists
- Both instance and static method support

**Files Refactored:**
- `test_private_access.py`
- `test_collection_12176069_fixed.py`
- `test_original_collection.py`
- `test_correct_cookie.py`

**Benefits:**
- Consistent formatting across all scripts
- Easy to maintain (change in one place)
- Dynamic table sizing
- Professional, clean output

### 2. Collection Analyzer (`analyze_collection.py`)

A tool that scrapes Civitai collections and analyzes common patterns across all images.

**Analysis Capabilities:**

1. **Model Statistics**
   - Top models used in collection
   - Model version breakdown
   - Usage percentages

2. **Generation Parameters**
   - Sampler distribution (Euler a, DPM++, etc.)
   - Steps count analysis
   - CFG scale distribution

3. **LoRA Analysis**
   - Most frequently used LoRAs
   - Average weight per LoRA
   - LoRA combinations per image

4. **Intelligent Prompt Analysis**
   Automatically detects two prompt styles:

   **Tag-Style** (Danbooru-style):
   - Comma-separated tags
   - Handles weights (`:0.5`, `:1.2`)
   - Removes brackets/parentheses

   **NLP-Style** (Natural Language):
   - Sentence and comma splitting
   - Filters out stop words (determiners, prepositions)
   - Extracts meaningful 2-4 word phrases

5. **Pattern Detection**
   - Common positive concepts (words/phrases)
   - Common negative concepts
   - Phrase sequences (without overlapping nonsense)
   - Percentage usage across collection

6. **Sample Prompts**
   - Shows images with most LoRAs
   - Displays full LoRA stacks
   - Shows prompt previews

**Usage:**
```bash
# Basic analysis
python analyze_collection.py <collection_id>

# Save to JSON
python analyze_collection.py <collection_id> --save

# Custom line width
python analyze_collection.py <collection_id> --line-length 100
```

**Output:**
- Real-time console analysis with formatted tables
- JSON export with full scraped data and analysis results

## Test Results

### Collection 12176069 (50 images)
- **Models**: WAI-illustrious-SDXL (12%), iLustMix (10%)
- **Samplers**: 11 different samplers used
- **LoRAs**: 217 total uses across 50 images
- **Top Concepts**: masterpiece (80%), best quality (72%), absurdres (56%)
- **Common Phrases**: "looking at viewer", "depth of field", "long hair"

### Collection 11035255 (21 images)
- **Models**: AutismMix SDXL (38%), Pony Diffusion V6 XL (19%)
- **Samplers**: Euler a dominant (76%)
- **LoRAs**: 83 total uses
- **Top Concepts**: score_8_up (90%), 1girl (67%), cleavage (62%)
- **Style**: More focused on character-based prompts

## Key Improvements

### 1. Smarter Phrase Extraction
**Before:** Extracted every overlapping subsequence
- "looking at" (19), "at viewer" (19), "looking at viewer" (16)
- "depth of" (15), "of field" (15), "depth of field" (15)
- "with a" (14), "in the" (12), "and the" (10)

**After:** Only meaningful phrases
- "looking at viewer" (15)
- "depth of field" (14)
- Stop-word-only phrases filtered out

### 2. Stop Word Filtering
Removed 100+ common words from phrase extraction:
- Articles: a, an, the
- Conjunctions: and, or, but
- Prepositions: in, on, at, to, from, with, by
- Pronouns: it, this, that, his, her, their
- Quality meta-tags for NLP prompts

### 3. Consistent Formatting
All test scripts now use `ConsoleFormatter` for:
- Headers and section separators
- Status messages (✅/❌)
- Aligned key-value pairs
- Auto-sized tables

## Files Created

### Core Utilities
1. **`console_utils.py`** - ConsoleFormatter class
2. **`analyze_collection.py`** - CollectionAnalyzer class and CLI

### Test Scripts Refactored
3. **`test_private_access.py`** - Permission/access testing
4. **`test_collection_12176069_fixed.py`** - Fixed cookie validation
5. **`test_original_collection.py`** - Scraper validation
6. **`test_correct_cookie.py`** - Cookie comparison test

### Documentation
7. **`CONSOLE_FORMATTER_GUIDE.md`** - Full ConsoleFormatter docs
8. **`CONSOLE_FORMATTER_QUICK_REF.md`** - Quick API reference
9. **`COLLECTION_ANALYZER_GUIDE.md`** - Analyzer usage guide
10. **`demo_console_utils.py`** - All features demo
11. **`check_prompts.py`** - Prompt debugging helper
12. **`check_keys.py`** - Data structure checker

### Analysis Output
13. **`collection_12176069_analysis.json`** (430KB) - Full analysis data
14. **`collection_11035255_analysis.json`** (131KB) - Full analysis data

## Usage Examples

### Find common themes in your collection
```bash
python analyze_collection.py 12345
```

### Optimize your model choices
```bash
python analyze_collection.py 12345
# See: Which models you use most, what samplers work best
```

### Discover best LoRA weights
```bash
python analyze_collection.py 12345 --save
# See: Top LoRAs, average weights per LoRA
```

### Extract prompt patterns
```bash
python analyze_collection.py 12345
# See: Common concepts, successful phrase structures
```

## Technical Highlights

### Dual-Style Prompt Analysis
- Automatically detects tag-style vs NLP-style prompts
- Applies appropriate extraction method per prompt
- Filters stop words intelligently
- Preserves contextual meaning

### Phrase Extraction Improvements
- Uses natural delimiters (commas, periods)
- Filters out stop-word-only phrases
- Requires at least one meaningful word
- Avoids over-splitting (no overlapping n-grams)

### Consistent Output
- All scripts use ConsoleFormatter
- Configurable line length per script
- Professional, aligned tables
- Emoji status indicators

## Next Steps

The analyzer is production-ready and can be used to:
- Discover collection themes
- Optimize generation parameters
- Identify successful prompt patterns
- Compare different collections
- Build prompt libraries from successful images

JSON export enables further analysis in Excel, Python, or visualization tools.
