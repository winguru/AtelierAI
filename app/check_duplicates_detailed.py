#!/usr/bin/env python
import sqlite3

db_path = '/Users/winguru/Sources/AtelierAI/app/image_db.sqlite'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Find ALL duplicate (authority_id, normalized_external_name) combinations
query = """
SELECT 
    authority_id, 
    normalized_external_name, 
    COUNT(*) as count
FROM authority_terms
GROUP BY authority_id, normalized_external_name
HAVING count > 1
ORDER BY count DESC
"""

print("=== Checking for true duplicates (same authority_id, normalized_external_name) ===\n")
duplicates = conn.execute(query).fetchall()

if not duplicates:
    print("No true duplicates found.\n")
else:
    for dup in duplicates:
        print(f"Authority {dup['authority_id']}: '{dup['normalized_external_name']}' has {dup['count']} records")
        # Show all records
        detail_query = """
        SELECT id, external_tag_id, external_name
        FROM authority_terms
        WHERE authority_id = ? AND normalized_external_name = ?
        """
        for detail in conn.execute(detail_query, (dup['authority_id'], dup['normalized_external_name'])):
            print(f"  ID {detail['id']}: tag_id='{detail['external_tag_id']}', name='{detail['external_name']}'")
        print()

# Also check specifically for the problem IDs' authority
print("\n=== Check authority_id=5 (prompt) - checking for any unusual state ===\n")
query = """
SELECT id, external_tag_id, external_name, normalized_external_name 
FROM authority_terms 
WHERE authority_id = 5
ORDER BY id DESC
LIMIT 20
"""
print("Last 20 prompt authority entries:")
for row in conn.execute(query):
    print(f"  ID {row['id']}: tag_id='{row['external_tag_id']}' | name='{row['external_name']}' | norm='{row['normalized_external_name']}'")

conn.close()
