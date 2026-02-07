#!/usr/bin/env python3
"""Test script to verify Unicode table formatting."""

from src.console_utils import ConsoleFormatter, get_display_width

# Test display width calculations
print("=== Display Width Tests ===")
print(f"len('abc'): {len('abc')}, display_width: {get_display_width('abc')}")
print(f"len('全身貞操帯'): {len('全身貞操帯')}, display_width: {get_display_width('全身貞操帯')}")
print(f"len('Chastity Belt + 全身貞操帯'): {len('Chastity Belt + 全身貞操帯')}, display_width: {get_display_width('Chastity Belt + 全身貞操帯')}")
print()

# Test table formatting with Unicode
fmt = ConsoleFormatter(line_length=120)

print("=== Table with Japanese Characters ===")
headers = ["LoRA Name", "Usage", "Avg Weight"]
rows = [
    ["Chastity Belt + Chastity bra / 全身貞操帯", "50", "0.93"],
    ["chastity belt thin/ cable style / anus cutout", "16", "0.95"],
    ["Safety Mittens & Restraining Booties / 医療用安全ミトン - 院拘束ブーツ (ABDL/BDSM) [Illustrious]", "2", "1.00"],
    ["Imari Kurumi - Bible Black / 伊万里胡桃 - バイブルブラック", "6", "0.70"],
]

fmt.print_table(headers, rows)
print()

# Test truncation
print("=== Truncation Test ===")
from src.console_utils import truncate_to_width
long_text = "Safety Mittens & Restraining Booties / 医療用安全ミトン - 院拘束ブーツ (ABDL/BDSM) [Illustrious]"
truncated = truncate_to_width(long_text, 40)
print(f"Original: {long_text}")
print(f"Original len: {len(long_text)}, display_width: {get_display_width(long_text)}")
print(f"Truncated (max 40): {truncated}")
print(f"Truncated display_width: {get_display_width(truncated)}")
