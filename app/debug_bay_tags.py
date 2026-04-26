#!/usr/bin/env python3
"""Quick script to check which tag source has 'bay (nikke)'."""
import sys
sys.path.insert(0, ".")
from backend.database import SessionLocal
from sqlalchemy import text

db = SessionLocal()

print("=== Tags matching '%bay%' ===")
tags = db.execute(text("SELECT id, name FROM tags WHERE LOWER(name) LIKE '%bay%'")).fetchall()
print(f"Count: {len(tags)}")
for r in tags:
    img_count = db.execute(text("SELECT COUNT(*) FROM image_tags WHERE tag_id = :tid"), {"tid": r[0]}).scalar()
    print(f"  Tag id={r[0]} name='{r[1]}' images={img_count}")

print("\n=== Concepts matching '%bay%' ===")
concepts = db.execute(text("SELECT id, canonical_name FROM concepts WHERE LOWER(canonical_name) LIKE '%bay%'")).fetchall()
print(f"Count: {len(concepts)}")
for r in concepts:
    img_count = db.execute(text("SELECT COUNT(DISTINCT image_id) FROM image_concept_observations WHERE concept_id = :cid"), {"cid": r[0]}).scalar()
    print(f"  Concept id={r[0]} name='{r[1]}' images={img_count}")

print("\n=== ConceptAlias matching '%bay%' ===")
aliases = db.execute(text("SELECT id, concept_id, normalized_alias FROM concept_aliases WHERE LOWER(normalized_alias) LIKE '%bay%'")).fetchall()
print(f"Count: {len(aliases)}")
for r in aliases:
    img_count = db.execute(text("SELECT COUNT(DISTINCT image_id) FROM image_concept_observations WHERE concept_id = :cid"), {"cid": r[1]}).scalar()
    print(f"  Alias id={r[0]} concept_id={r[1]} alias='{r[2]}' images={img_count}")

print("\n=== AuthorityTerm matching '%bay%' ===")
auths = db.execute(text("SELECT id, external_name FROM authority_terms WHERE LOWER(external_name) LIKE '%bay%'")).fetchall()
print(f"Count: {len(auths)}")
for r in auths:
    img_count = db.execute(text("SELECT COUNT(DISTINCT image_id) FROM image_concept_observations WHERE authority_term_id = :aid"), {"aid": r[0]}).scalar()
    print(f"  Auth id={r[0]} name='{r[1]}' images={img_count}")

print("\n=== user_tags JSON matching '%bay%' ===")
user_tag_count = 0
rows = db.execute(text("SELECT id, user_tags FROM images WHERE user_tags IS NOT NULL")).fetchall()
bay_images = []
for row in rows:
    ut = row[1]
    if ut and isinstance(ut, list):
        for tag in ut:
            if isinstance(tag, str) and "bay" in tag.lower():
                bay_images.append((row[0], tag))
                user_tag_count += 1
                break
print(f"Images with user_tag containing 'bay': {len(bay_images)}")
for img_id, tag in bay_images[:10]:
    print(f"  Image id={img_id} tag='{tag}'")

db.close()
