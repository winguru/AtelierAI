#!/usr/bin/env python3
"""Compare json_metadata prompt tag counts vs DB observation counts."""
import sqlite3
import json
from collections import Counter

conn = sqlite3.connect("image_db.sqlite")

# First: get prompt tags from json_metadata
prompt_counts = Counter()

rows = conn.execute(
    "SELECT json_metadata FROM images WHERE image_status = 'active'"
).fetchall()
for (jm,) in rows:
    if jm is None:
        continue
    if isinstance(jm, str):
        try:
            jm = json.loads(jm)
        except (json.JSONDecodeError, ValueError):
            continue
    if not isinstance(jm, dict):
        continue
    pt = jm.get("prompt_tags")
    if not isinstance(pt, list):
        continue
    for item in pt:
        name = None
        if isinstance(item, dict):
            name = item.get("name") or item.get("normalized_name")
        elif isinstance(item, str):
            name = item
        if name:
            prompt_counts[name.strip().lower()] += 1

# Now get observation counts from DB
obs_counts = Counter()
obs_rows = conn.execute("""
    SELECT c.canonical_name, COUNT(DISTINCT ico.image_id)
    FROM concepts c
    JOIN image_concept_observations ico ON ico.concept_id = c.id
    GROUP BY c.id
""").fetchall()
for name, count in obs_rows:
    obs_counts[name.strip().lower()] = count

# Find discrepancies
print("Tags in json_metadata.prompt_tags with different counts in DB observations:")
print(f"{'Tag':<45} {'JSON':>6} {'DB':>6}")
print("-" * 60)
discrepancies = 0
missing_in_db = 0
for tag, json_count in prompt_counts.most_common(100):
    db_count = obs_counts.get(tag, 0)
    if db_count != json_count:
        discrepancies += 1
        if db_count == 0:
            missing_in_db += 1
        print(f"{tag:<45} {json_count:>6} {db_count:>6}")

print(f"\nTotal prompt tags from json: {len(prompt_counts)}")
print(f"Total concept names from DB: {len(obs_counts)}")
print(f"Discrepancies (top 100 checked): {discrepancies}")
print(f"Missing from DB entirely: {missing_in_db}")

# Also count tags in DB observations but NOT in json
print("\nTags in DB observations but missing from json_metadata prompt_tags (sample):")
count = 0
for tag, db_count in obs_counts.most_common(100):
    if tag not in prompt_counts:
        count += 1
        if count <= 10:
            print(f"  {tag}: {db_count} obs, 0 in json")
if count > 10:
    print(f"  ... and {count - 10} more")

conn.close()
