#!/usr/bin/env python
import os, sys, json, sqlite3
os.chdir('/Users/winguru/Sources/AtelierAI/app')
sys.path.insert(0, 'backend')

from main import SessionLocal, ImageModel
from image_collection import ImageCollection

# The 3 failed image IDs
failed_ids = [4573, 6381, 6389]

db = SessionLocal()
try:
    for img_id in failed_ids:
        image = db.query(ImageModel).filter(ImageModel.id == img_id).first()
        if image:
            print(f"\n=== Image ID {img_id} ===")
            print(f"File Hash: {image.file_hash}")
            print(f"File Name: {image.file_name}")
            
            # Get the EXIF prompt
            exif = image.exif_data if isinstance(image.exif_data, dict) else (json.loads(image.exif_data) if image.exif_data else {})
            prompt = exif.get('Prompt', '')
            print(f"Raw Prompt Length: {len(prompt)}")
            print(f"Raw Prompt Preview: {str(prompt)[:200]}")
            
            # Try to extract/trim the prompt
            collector = ImageCollection(db)
            trimmed = collector._trim_prompt_payload(prompt)
            print(f"Trimmed Prompt Length: {len(trimmed)}")
            print(f"Trimmed Prompt: {trimmed[:300]}")
finally:
    db.close()
