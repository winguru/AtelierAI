#!/usr/bin/env python3
"""Analyse how image framing (subject size, composition) affects CLIP scores.

Groups positive Shion images into score bands and samples from each band
so we can visually inspect whether framing correlates with CLIP similarity.
"""

from __future__ import annotations

import pickle
import random
import sys
from pathlib import Path

import numpy as np

from database import SessionLocal
from models import (
    CollectionModel,
    ImageCollectionMembership,
    ImageModel,
)
from config import IMAGE_LIBRARY_PATH

CACHE_PATH = "/workspace/app/data/clip_embeddings_cache.pkl"
IMAGE_EXTS = [".png", ".jpg", ".jpeg", ".webp"]


def main() -> None:
    with open(CACHE_PATH, "rb") as f:
        embeddings: dict[int, np.ndarray] = pickle.load(f)

    db = SessionLocal()

    # Positive IDs from Shion collection
    coll_rows = (
        db.query(ImageCollectionMembership.image_id)
        .join(
            CollectionModel,
            CollectionModel.id == ImageCollectionMembership.collection_id,
        )
        .filter(CollectionModel.name.ilike("%shion%"))
        .distinct()
        .all()
    )
    positive_ids = {r[0] for r in coll_rows}

    # Build prototype from all positives
    pos_embs = np.stack([embeddings[i] for i in positive_ids if i in embeddings])
    centroid = pos_embs.mean(axis=0)
    norm = np.linalg.norm(centroid)
    prototype = centroid / norm if norm > 0 else centroid

    # Score every positive
    scores: dict[int, float] = {}
    for img_id in positive_ids:
        if img_id in embeddings:
            emb = embeddings[img_id]
            sim = float(prototype @ emb)
            scores[img_id] = sim

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x])

    bands = [
        ("Low (0.60-0.78)", 0.60, 0.78),
        ("Med-Low (0.78-0.84)", 0.78, 0.84),
        ("Med (0.84-0.88)", 0.84, 0.88),
        ("Med-High (0.88-0.91)", 0.88, 0.91),
        ("High (0.91-0.95)", 0.91, 0.95),
    ]

    lib = Path(IMAGE_LIBRARY_PATH)
    random.seed(42)

    print(f"Total positive images scored: {len(scores)}")
    print(f"Score range: {min(scores.values()):.4f} - {max(scores.values()):.4f}")
    print(f"Mean: {np.mean(list(scores.values())):.4f}  "
          f"Std: {np.std(list(scores.values())):.4f}")
    print()

    for label, lo, hi in bands:
        band_ids = [i for i in sorted_ids if lo <= scores[i] < hi]
        print(f"=== {label}: {len(band_ids)} images ===")
        sample = random.sample(band_ids, min(4, len(band_ids)))
        for sid in sorted(sample, key=lambda x: scores[x]):
            img = db.query(ImageModel).filter(ImageModel.id == sid).first()
            if img:
                path = None
                for ext in IMAGE_EXTS:
                    p = lib / f"{img.file_hash}{ext}"
                    if p.exists():
                        path = str(p)
                        break
                print(f"  id={sid:5d}  score={scores[sid]:.4f}  {path}")
        print()

    # Also look at the highest-scoring negatives (false positives)
    print("=== TOP-SCORING NEGATIVES (potential false positives) ===")
    neg_scores = {}
    for img_id, emb in embeddings.items():
        if img_id not in positive_ids:
            neg_scores[img_id] = float(prototype @ emb)

    neg_sorted = sorted(neg_scores.keys(), key=lambda x: neg_scores[x], reverse=True)
    print(f"Top 10 negatives by score:")
    for sid in neg_sorted[:10]:
        img = db.query(ImageModel).filter(ImageModel.id == sid).first()
        if img:
            path = None
            for ext in IMAGE_EXTS:
                p = lib / f"{img.file_hash}{ext}"
                if p.exists():
                    path = str(p)
                    break
            print(f"  id={sid:5d}  score={neg_scores[sid]:.4f}  {path}")

    db.close()


if __name__ == "__main__":
    sys.exit(main())
