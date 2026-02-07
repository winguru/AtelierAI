# Civitai Collection Analyzer

## Overview

The Collection Analyzer is a tool that scrapes a Civitai collection and analyzes the common patterns, tags, models, and prompt elements across all images in the collection.

## Features

### 1. **Automatic Collection Scraping**
- Scrapes all images from a private collection using existing scraper
- Fetches detailed metadata for each image
- Extracts models, LoRAs, prompts, and generation parameters

### 2. **Intelligent Prompt Analysis**
The analyzer automatically detects prompt styles and extracts concepts differently:

#### Tag-Style Prompts (Danbooru-style)
```
1girl, solo, long hair, blue eyes, masterpiece, best quality
```
- Splits by commas
- Removes weights (`:0.5`, `:1.2`)
- Removes bracketed content
- Treats each tag as a concept

#### NLP-Style Prompts (Natural Language)
```
A beautiful woman with long blonde hair standing in a forest, dramatic lighting
```
- Splits by sentences and commas
- Removes common stop words (determiners, prepositions)
- Extracts meaningful phrases
- Preserves contextual relationships

### 3. **Pattern Detection**

#### Models & Versions
- Tracks which models are used most frequently
- Identifies specific model versions
- Calculates usage percentages

#### Generation Parameters
- Sampler distribution (Euler a, DPM++, etc.)
- Steps count analysis
- CFG scale distribution
- Common parameter combinations

#### LoRAs (Style/Character Models)
- Most frequently used LoRAs
- Average weight per LoRA
- LoRA combinations

#### Prompt Concepts
- Common descriptive words/phrases
- Concept usage frequency
- Percentage of images using each concept

#### Negative Prompts
- Common negative tags
- Quality control patterns
- Avoided elements

## Usage

### Basic Analysis
```bash
python analyze_collection.py <collection_id>
```

### Save Results to JSON
```bash
python analyze_collection.py <collection_id> --save
```

This saves detailed results to `collection_<id>_analysis.json`

### Custom Line Width
```bash
python analyze_collection.py <collection_id> --line-length 100
```

## Example Output

### Overview Section
```
======================================================================
Collection Analysis: 12176069
======================================================================

----------------------------------------------------------------------
Overview
----------------------------------------------------------------------
Total Images: 50
Unique Models: 23
Unique Samplers: 11
Total LoRAs Used: 217
```

### Top Models
```
----------------------------------------------------------------------
Top Models
----------------------------------------------------------------------
Model                                               Count  Percentage
--------------------------------------------------  -----  ----------
WAI-illustrious-SDXL                                6      12.0%
iLustMix                                            5      10.0%
CelestReal - Anime / semi-reality fusion            4      8.0%
```

### Common Concepts
```
----------------------------------------------------------------------
Most Common Positive Concepts
----------------------------------------------------------------------
Concept              Occurrences  Percentage
-------------------  -----------  ----------
masterpiece          40           80.0%
best quality         36           72.0%
1girl                16           32.0%
looking at viewer    15           30.0%
depth of field       14           28.0%
```

### Common Phrases
```
----------------------------------------------------------------------
Most Common Positive Phrase Sequences
----------------------------------------------------------------------
Phrase             Occurrences
-----------------  -----------
best quality       36
very aesthetic     25
looking at viewer  15
depth of field     14
long hair          20
```

### LoRA Analysis
```
----------------------------------------------------------------------
Top LoRAs
----------------------------------------------------------------------
LoRA Name               Usage  Avg Weight
--------------------  -----  -------
Detailer IL             8      0.95
DetailedEyes_XL          6      1.00
Cleavage Slider - Pony    6      1.00
Breasts size slider      6      1.00
```

## Output Files

### Console Output
- Real-time analysis progress
- Formatted tables using `ConsoleFormatter`
- Top results for each category

### JSON Analysis File (with `--save`)
Complete analysis data including:
- Collection metadata
- Top models/versions/samplers
- Top LoRAs with average weights
- Top concepts and phrases (positive & negative)
- Full scraped data for all images

## Real-World Use Cases

### 1. **Discover Collection Themes**
Find what makes your collection unique:
```bash
python analyze_collection.py 12345
# See: Common concepts, dominant styles, recurring subjects
```

### 2. **Model Selection**
Which models perform best for your style:
```bash
python analyze_collection.py 12345
# See: Top models, sampler choices, parameter ranges
```

### 3. **LoRA Optimization**
Which LoRAs you use most and their optimal weights:
```bash
python analyze_collection.py 12345
# See: Top LoRAs, average weights, combinations
```

### 4. **Prompt Engineering**
Extract successful prompt patterns from your best images:
```bash
python analyze_collection.py 12345 --save
# See: Common concepts, phrase sequences, quality tags
```

### 5. **Collection Comparison**
Compare different collections or time periods:
```bash
# Analyze old collection
python analyze_collection.py old_collection_id --save > old.txt

# Analyze new collection
python analyze_collection.py new_collection_id --save > new.txt

# Compare patterns manually
```

## Technical Details

### Concept Extraction Algorithm

1. **Style Detection**
   - Counts commas relative to words
   - Checks for weight syntax (`:0.5`)
   - Checks for bracketed LoRA tags (`<lora:name:0.5>`)

2. **Tag-Style Processing**
   - Split by commas
   - Remove weight modifiers
   - Strip parentheses and brackets
   - Lowercase and trim

3. **NLP-Style Processing**
   - Split by sentence endings (`.`, `!`, `?`)
   - Split by commas for phrases
   - Remove 100+ stop words (determiners, prepositions, etc.)
   - Extract 2-4 word meaningful sequences

### Phrase Extraction

- Uses natural delimiters (commas, periods)
- Filters out stop-word-only phrases
- Requires at least one meaningful word per phrase
- Avoids over-splitting (no overlapping subsequences)

### Stop Word Filtering

Automatically filters out:
- Articles: `a`, `an`, `the`
- Conjunctions: `and`, `or`, `but`
- Prepositions: `in`, `on`, `at`, `to`, `from`, `with`, `by`
- Pronouns: `it`, `this`, `that`, `his`, `her`, `their`
- Common modifiers: `very`, `too`, `quite`, `just`, `only`
- Quality meta-words: `masterpiece`, `best quality`, `high quality` (for NLP prompts only)

## Integration with Other Tools

The analyzer uses:
- **CivitaiPrivateScraper** - For fetching collection data
- **ConsoleFormatter** - For consistent, formatted output
- **JSON export** - For further analysis or visualization

Example: Import and analyze in Python:
```python
from analyze_collection import CollectionAnalyzer
import json

# Load previously scraped data
with open('collection_12176069_analysis.json', 'r') as f:
    analysis = json.load(f)

# Access data programmatically
for model, count in analysis['top_models']:
    print(f"{model}: {count} images")
```

## Tips for Best Results

1. **Larger Collections** = Better Patterns
   - Small collections (<10 images) may not show significant patterns
   - 20+ images recommended for meaningful statistics

2. **Mixed Prompts May Confuse Style Detection**
   - If a collection has both tag-style and NLP prompts
   - Results will be a mix of both extraction methods

3. **Use JSON Export for Deep Analysis**
   - `--save` flag exports complete data
   - Filter/sort/visualize in other tools (Excel, Python, etc.)

4. **Compare Multiple Collections**
   - Analyze different themed collections separately
   - Compare concept distributions to find unique elements

## Future Enhancements

Potential additions:
- Visual trend charts (matplotlib/plotly)
- Cluster analysis for prompt similarities
- Model vs quality correlation
- Time-based analysis (when were images added?)
- Export to CSV/Excel format
- Prompt similarity scoring between images
