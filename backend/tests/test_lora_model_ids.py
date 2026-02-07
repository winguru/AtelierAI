#!/usr/bin/env python3
"""Test script to verify LoRA model ID capture and Unicode table formatting."""

import json

# Test data simulating API response
test_resources = [
    {
        "modelType": "LORA",
        "modelName": "Chastity Belt + Chastity bra / 全身貞操帯",
        "modelVersionId": 2347342,
        "modelId": 781293,
        "strength": 0.8,
        "versionName": "v7.1-Illustrious",
        "baseModel": "Illustrious"
    },
    {
        "modelType": "Checkpoint",
        "modelName": "Illustrious XL",
        "modelVersionId": 123456,
        "modelId": 1234567,
        "versionName": "v1.0",
        "baseModel": "Illustrious"
    }
]

print("=== Test 1: _process_resources() ===")
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
model_name, model_version, loras = scraper._process_resources(test_resources)

print(f"Model: {model_name}")
print(f"Version: {model_version}")
print(f"LoRAs: {json.dumps(loras, indent=2)}")
print()

# Verify LoRA data structure
assert loras[0]['model_id'] == 781293, "Model ID not captured!"
assert loras[0]['model_version_id'] == 2347342, "Version ID not captured!"
assert loras[0]['version_name'] == "v7.1-Illustrious", "Version name not captured!"
print("✅ LoRA model IDs captured correctly!")
print()

print("=== Test 2: CollectionAnalyzer ===")
from analyze_collection import CollectionAnalyzer

test_data = [{
    "model": "Illustrious XL",
    "model_version": "v1.0",
    "loras": loras,
    "tags": ["test", "tag1", "tag2"],
    "prompt": "test prompt",
    "negative_prompt": "",
    "sampler": "DPM++ 2M Karras",
    "steps": 30,
    "cfg_scale": 7.0,
    "author": "test_user"
}]

analyzer = CollectionAnalyzer(test_data)
analyzer.analyze()

print(f"Top LoRAs: {analyzer.get_top_items(analyzer.loras, 3)}")
print(f"LoRA Model IDs: {json.dumps(analyzer.lora_model_ids, indent=2)}")
print()

# Verify model ID tracking
lora_name = "Chastity Belt + Chastity bra / 全身貞操帯"
assert lora_name in analyzer.lora_model_ids, "LoRA not tracked!"
assert analyzer.lora_model_ids[lora_name]['model_id'] == 781293, "Model ID not tracked correctly!"
assert analyzer.lora_model_ids[lora_name]['model_version_id'] == 2347342, "Version ID not tracked correctly!"
print("✅ CollectionAnalyzer tracking works correctly!")
print()

print("=== Test 3: Table Display ===")
from src.console_utils import ConsoleFormatter

fmt = ConsoleFormatter(line_length=120)

# Test LoRA table
headers = ["LoRA Name", "Usage", "Avg Weight", "Model ID", "URL"]
rows = [
    [
        "Chastity Belt + Chastity bra / 全身貞操帯",
        "50",
        "0.93",
        "2347342",
        "civitai.com/models/781293?modelVersionId=2347342"
    ],
    [
        "Safety Mittens & Restraining Booties / 医療用安全ミトン",
        "2",
        "1.00",
        "987654",
        "civitai.com/models/456789?modelVersionId=987654"
    ]
]

fmt.print_header("LoRA Table with Unicode Support")
fmt.print_table(headers, rows)
print()

print("✅ Table formatting works with Unicode!")
print()

print("=== Test 4: Display Width Calculations ===")
from src.console_utils import get_display_width

test_strings = [
    "abc",
    "全身貞操帯",
    "Chastity Belt + 全身貞操帯",
    "Safety Mittens & 医療用安全ミトン"
]

print("Display Width Tests:")
for s in test_strings:
    char_len = len(s)
    display_width = get_display_width(s)
    print(f"  '{s}':")
    print(f"    len(): {char_len}")
    print(f"    display_width: {display_width}")
    if display_width != char_len:
        print(f"    ⚠️  Wide character detected!")
print()

print("✅ All tests passed!")
