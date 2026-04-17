#!/usr/bin/env python
import sqlite3

db_path = "/Users/winguru/Sources/AtelierAI/app/image_db.sqlite"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Find duplicate normalized_external_names per authority
query = """
SELECT 
    authority_id, 
    normalized_external_name, 
    COUNT(*) as count,
    GROUP_CONCAT(id) as ids,
    GROUP_CONCAT(external_name) as names
FROM authority_terms
GROUP BY authority_id, normalized_external_name
HAVING count > 1
ORDER BY count DESC
LIMIT 50
"""

print("=== Duplicate normalized_external_names in authority_terms ===\n")
for row in conn.execute(query):
    print(
        f"Authority {row['authority_id']}: '{row['normalized_external_name']}' appears {row['count']} times"
    )
    print(f"  IDs: {row['ids']}")
    print(f"  Names: {row['names']}")
    print()

# Now specifically check for the ones mentioned in the error
print("\n=== Specific checks for problem tags ===\n")
problem_tags = ["black_penis", "blurry_background", "nipple_slip", "large_breasts"]
for tag in problem_tags:
    query = f"SELECT id, authority_id, external_name, normalized_external_name FROM authority_terms WHERE normalized_external_name = ?"
    results = conn.execute(query, (tag,)).fetchall()
    if results:
        print(f"Tag '{tag}': {len(results)} record(s)")
        for r in results:
            print(
                f"  ID {r['id']}: authority={r['authority_id']}, name={r['external_name']}"
            )
    print()

conn.close()
