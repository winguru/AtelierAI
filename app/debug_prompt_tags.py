#!/usr/bin/env python
import os, sys, json
from typing import Any
os.chdir('/Users/winguru/Sources/AtelierAI/app')
sys.path.insert(0, 'backend')

from main import SessionLocal, ImageModel
from image_collection import ImageCollection
from atelierai.utils.prompt_phrases import build_prompt_tag_payload

# The 3 failed image IDs
failed_ids = [4573, 6381, 6389]

db = SessionLocal()
try:
    for img_id in failed_ids:
        image = db.query(ImageModel).filter(ImageModel.id == img_id).first()
        if not image:
            print(f"Image {img_id} not found")
            continue
            
        print(f"\n{'='*80}")
        print(f"Image ID {img_id}: {image.file_name}")
        
        # Get and trim the prompt
        raw_exif: Any = image.exif_data
        exif = raw_exif if isinstance(raw_exif, dict) else (json.loads(raw_exif) if raw_exif else {})
        prompt = exif.get('Prompt', '')
        
        collector = ImageCollection(db)
        trimmed = collector._trim_prompt_payload(prompt)
        
        print(f"Trimmed prompt length: {len(trimmed)}")
        print(f"Trimmed prompt:\n{trimmed}\n")
        
        # Build prompt tag payload
        if trimmed:
            try:
                payload = build_prompt_tag_payload(trimmed)
                print(f"Payload generated:")
                print(f"  Tags count: {len(payload.get('tags', []))}")
                if payload.get('tags'):
                    print(f"  First 10 tags:")
                    for tag in payload['tags'][:10]:
                        print(f"    - {tag.get('tag', 'N/A')} (type: {tag.get('type', 'N/A')})")
                print(f"  Concepts count: {len(payload.get('concepts', []))}")
            except Exception as e:
                print(f"ERROR building payload: {e}")
        else:
            print("No trimmed prompt to process")
            
finally:
    db.close()
