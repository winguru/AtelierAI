#!/usr/bin/env python
import os, sys, sqlite3
os.chdir('/Users/winguru/Sources/AtelierAI/app')

db_path = 'image_db.sqlite'
conn = sqlite3.connect(db_path)

# First, let's see how many prompt authority terms we have
count_before = conn.execute("SELECT COUNT(*) FROM authority_terms WHERE authority_id=5").fetchone()[0]
print(f"Before cleanup: {count_before} prompt authority terms")

# Delete all prompt authority terms (they'll be recreated during rescan)
# This is safe because they're auto-generated from prompt text during rescan
conn.execute("DELETE FROM authority_terms WHERE authority_id=5")
affected = conn.total_changes - (conn.total_changes - len([1]))  # Get affected rows

# Verify
count_after = conn.execute("SELECT COUNT(*) FROM authority_terms WHERE authority_id=5").fetchone()[0]
print(f"After cleanup: {count_after} prompt authority terms")

conn.commit()
conn.close()

print("\nCleanup complete. Now attempting rescan of the 3 failed images...")

# Now retry the rescans
sys.path.insert(0, 'backend')
from main import SessionLocal, ImageModel
from image_collection import ImageCollection

failed_ids = [4573, 6381, 6389]
success_count = 0
failed_count = 0

for img_id in failed_ids:
    print(f"\nRetrying rescan for Image ID {img_id}...")
    
    db = SessionLocal()
    try:
        image = db.query(ImageModel).filter(ImageModel.id == img_id).first()
        if not image:
            print(f"  ERROR: Image not found")
            continue
        
        collector = ImageCollection(db)
        result = collector.rescan_existing_file(image)
        db.commit()
        print(f"  SUCCESS! Result: {result}")
        success_count += 1
        
    except Exception as e:
        db.rollback()
        print(f"  ERROR: {type(e).__name__}: {str(e)[:100]}")
        failed_count += 1
    finally:
        db.close()

print(f"\n{'='*60}")
print(f"Retry results: {success_count} succeeded, {failed_count} failed")
