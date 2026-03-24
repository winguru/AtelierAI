#!/usr/bin/env python
import os, sys, json, traceback
os.chdir('/Users/winguru/Sources/AtelierAI/app')
sys.path.insert(0, 'backend')

from main import SessionLocal, ImageModel
from image_collection import ImageCollection

# The 3 failed image IDs
failed_ids = [4573, 6381, 6389]

for img_id in failed_ids:
    print(f"\n{'='*80}")
    print(f"Attempting rescan for Image ID {img_id}")
    print('='*80)
    
    db = SessionLocal()
    try:
        image = db.query(ImageModel).filter(ImageModel.id == img_id).first()
        if not image:
            print(f"Image {img_id} not found")
            continue
            
        print(f"File: {image.file_name}")
        
        collector = ImageCollection(db)
        result = collector.rescan_existing_file(image)
        db.commit()
        
        print(f"Success! Result: {result}")
        
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        print(f"Error type: {type(e).__name__}")
        traceback.print_exc()
    finally:
        db.close()
