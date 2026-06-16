#!/usr/bin/env python3
"""CLIP concept discrimination analysis.

This script measures how well a concept's CLIP prototype discriminates
between images that genuinely contain the concept and images that don't.

It produces:
  1. A histogram of CLIP cosine similarity scores, split by ground-truth
     label (has the concept vs doesn't).
  2. ROC/PR curves and AUC metrics.
  3. Optimal thresholds at various operating points.
  4. A "training curve" — how discrimination quality changes as the
     prototype is built from N images (10, 20, 50, 100, 200, all).

Usage
-----
    cd /workspace/app
    PYTHONPATH=app/src:app/backend python scripts/clip_discrimination_analysis.py \
        --concept "shion (tensura)" \
        --output /workspace/app/data/clip_discrimination_shion.json

Ground-truth labels come from ``ImageConceptObservation`` rows: an image
is a **positive** if it has an observation linking it to the concept (or
to an authority_term that maps to the concept), and a **negative**
otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pickle
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

# ── Path setup ──────────────────────────────────────────────────────────────
# Ensure backend modules are importable when running from app/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import IMAGE_LIBRARY_PATH  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    Concept,
    ConceptAlias,
    ImageConceptObservation,
    ImageModel,
    ImageCollectionMembership,
    CollectionModel,
)
from services.clip_provider import (  # noqa: E402
    get_clip_provider,
    set_clip_provider,
    cosine_similarity,
    LocalCLIPProvider,
)

logger = logging.getLogger("clip_discrimination")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


# ═══════════════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ImageScore:
    """A single image with its CLIP score and ground-truth label."""
    image_id: int
    file_hash: str
    score: float
    is_positive: bool  # True = has the concept observation


@dataclass
class ThresholdMetrics:
    """Metrics at a specific threshold."""
    threshold: float
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float
    accuracy: float


@dataclass
class DiscriminationResult:
    """Full analysis result for one prototype configuration."""
    concept_name: str
    source_count: int  # number of images used to build prototype
    total_images: int
    total_positives: int
    total_negatives: int
    scores: list[ImageScore] = field(default_factory=list)
    auc_roc: float = 0.0
    auc_pr: float = 0.0
    best_f1_threshold: Optional[ThresholdMetrics] = None
    youdens_j_threshold: Optional[ThresholdMetrics] = None
    # Separation stats
    positive_mean: float = 0.0
    positive_std: float = 0.0
    negative_mean: float = 0.0
    negative_std: float = 0.0
    separation_gap: float = 0.0  # (pos_mean - neg_mean) / (pos_std + neg_std)
    # Percentiles for threshold guidance
    pos_percentiles: dict[str, float] = field(default_factory=dict)
    neg_percentiles: dict[str, float] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Database helpers
# ═══════════════════════════════════════════════════════════════════════════════

def resolve_concept(db: Session, concept_name: str) -> Concept:
    """Find a concept by canonical name or alias."""
    concept = (
        db.query(Concept)
        .filter(Concept.canonical_name == concept_name)
        .first()
    )
    if concept:
        return concept

    # Try alias
    alias = (
        db.query(ConceptAlias)
        .filter(ConceptAlias.normalized_alias == concept_name.lower().strip())
        .first()
    )
    if alias:
        return alias.concept

    # Fuzzy: ILIKE match
    concept = (
        db.query(Concept)
        .filter(Concept.canonical_name.ilike(f"%{concept_name}%"))
        .first()
    )
    if concept:
        return concept

    raise ValueError(f"Concept '{concept_name}' not found by name or alias")


def get_positive_image_ids(
    db: Session,
    concept_id: int,
    collection_name: Optional[str] = None,
) -> set[int]:
    """Get all image_ids linked to this concept.

    Combines two sources of ground truth:
    1. ``ImageConceptObservation`` rows for the concept.
    2. (Optional) Images in a named collection — every image in the
       concept's collection is a positive.
    """
    ids: set[int] = set()

    # Source 1: observations
    rows = (
        db.query(ImageConceptObservation.image_id)
        .filter(
            ImageConceptObservation.concept_id == concept_id,
            ImageConceptObservation.is_present == True,  # noqa: E712
        )
        .distinct()
        .all()
    )
    ids.update(r[0] for r in rows)

    # Source 2: collection membership (if provided)
    if collection_name:
        coll_rows = (
            db.query(ImageCollectionMembership.image_id)
            .join(CollectionModel,
                  CollectionModel.id == ImageCollectionMembership.collection_id)
            .filter(CollectionModel.name.ilike(f"%{collection_name}%"))
            .distinct()
            .all()
        )
        coll_ids = {r[0] for r in coll_rows}
        logger.info(
            "  Collection '%s' adds %d positive images (union with %d observations → %d total)",
            collection_name, len(coll_ids), len(ids), len(ids | coll_ids),
        )
        ids.update(coll_ids)

    return ids


def get_all_images_with_local_files(db: Session) -> list[tuple[int, str]]:
    """Get all active images that have a local file on disk.

    Returns list of (image_id, file_hash).
    """
    rows = (
        db.query(ImageModel.id, ImageModel.file_hash)
        .filter(ImageModel.image_status == "active")
        .all()
    )
    result = []
    library = Path(IMAGE_LIBRARY_PATH)
    for img_id, file_hash in rows:
        if not file_hash:
            continue
        found = False
        for ext in IMAGE_EXTENSIONS:
            if (library / f"{file_hash}{ext}").is_file():
                found = True
                break
        if found:
            result.append((img_id, file_hash))
    return result


def resolve_local_image_path(file_hash: str) -> Optional[str]:
    """Resolve local file path for a file hash."""
    library = Path(IMAGE_LIBRARY_PATH)
    for ext in IMAGE_EXTENSIONS:
        candidate = library / f"{file_hash}{ext}"
        if candidate.is_file():
            return str(candidate)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# CLIP encoding & scoring
# ═══════════════════════════════════════════════════════════════════════════════

async def encode_all_images(
    provider: LocalCLIPProvider,
    images: list[tuple[int, str]],
    batch_size: int = 64,
) -> dict[int, np.ndarray]:
    """Encode all images via CLIP, returning {image_id: embedding}.

    Processes in batches to avoid memory issues.
    """
    embeddings: dict[int, np.ndarray] = {}
    total = len(images)
    
    for start in range(0, total, batch_size):
        batch = images[start:start + batch_size]
        paths = []
        ids = []
        for img_id, file_hash in batch:
            path = resolve_local_image_path(file_hash)
            if path:
                paths.append(path)
                ids.append(img_id)
        
        if not paths:
            continue
        
        emb = await provider.encode_image_paths(paths)
        for i, img_id in enumerate(ids):
            if i < emb.shape[0]:
                embeddings[img_id] = emb[i]
        
        done = start + len(batch)
        logger.info(f"  Encoded {done}/{total} images ({len(embeddings)} successful)")
    
    return embeddings


async def build_prototype_from_embeddings(
    embeddings: np.ndarray,
) -> np.ndarray:
    """Build a centroid prototype from a set of embeddings."""
    centroid = embeddings.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm
    return centroid


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics computation
# ═══════════════════════════════════════════════════════════════════════════════

def compute_threshold_metrics(
    scores: list[ImageScore],
    threshold: float,
) -> ThresholdMetrics:
    """Compute TP/FP/FN/TN/Precision/Recall/F1 at a given threshold."""
    tp = fp = fn = tn = 0
    for s in scores:
        predicted_positive = s.score >= threshold
        if predicted_positive and s.is_positive:
            tp += 1
        elif predicted_positive and not s.is_positive:
            fp += 1
        elif not predicted_positive and s.is_positive:
            fn += 1
        else:
            tn += 1
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = (tp + tn) / len(scores) if scores else 0.0
    
    return ThresholdMetrics(
        threshold=threshold, tp=tp, fp=fp, fn=fn, tn=tn,
        precision=precision, recall=recall, f1=f1, accuracy=accuracy,
    )


def compute_auc_roc(scores: list[ImageScore]) -> float:
    """Compute ROC AUC using the trapezoidal rule on the ROC curve."""
    total_pos = sum(1 for s in scores if s.is_positive)
    total_neg = len(scores) - total_pos
    if total_pos == 0 or total_neg == 0:
        return 0.0
    
    # Sort by score descending
    sorted_scores = sorted(scores, key=lambda s: -s.score)
    
    tp = fp = 0
    prev_tpr = 0.0
    prev_fpr = 0.0
    auc = 0.0
    
    for s in sorted_scores:
        if s.is_positive:
            tp += 1
        else:
            fp += 1
        tpr = tp / total_pos
        fpr = fp / total_neg
        # Trapezoidal area increment
        auc += prev_tpr * (fpr - prev_fpr)
        prev_tpr = tpr
        prev_fpr = fpr
    
    return auc


def compute_auc_pr(scores: list[ImageScore]) -> float:
    """Compute Precision-Recall AUC."""
    total_pos = sum(1 for s in scores if s.is_positive)
    if total_pos == 0:
        return 0.0
    
    sorted_scores = sorted(scores, key=lambda s: -s.score)
    
    tp = fp = 0
    prev_recall = 0.0
    auc = 0.0
    
    for s in sorted_scores:
        if s.is_positive:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / total_pos
        # Average precision approximation
        auc += precision * (recall - prev_recall)
        prev_recall = recall
    
    return auc


def compute_percentiles(values: list[float]) -> dict[str, float]:
    """Compute key percentiles."""
    if not values:
        return {}
    arr = np.array(values)
    return {
        "min": float(arr.min()),
        "p5": float(np.percentile(arr, 5)),
        "p10": float(np.percentile(arr, 10)),
        "p25": float(np.percentile(arr, 25)),
        "p50": float(np.percentile(arr, 50)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
    }


def analyze_discrimination(
    concept_name: str,
    source_count: int,
    embeddings: dict[int, np.ndarray],
    positive_ids: set[int],
    prototype: np.ndarray,
) -> DiscriminationResult:
    """Analyze how well the prototype discriminates positives from negatives."""
    scores: list[ImageScore] = []
    
    for img_id, emb in embeddings.items():
        sim = float(cosine_similarity(prototype, emb.reshape(1, -1))[0])
        scores.append(ImageScore(
            image_id=img_id,
            file_hash="",  # not needed for analysis
            score=sim,
            is_positive=img_id in positive_ids,
        ))
    
    pos_scores = [s.score for s in scores if s.is_positive]
    neg_scores = [s.score for s in scores if not s.is_positive]
    
    result = DiscriminationResult(
        concept_name=concept_name,
        source_count=source_count,
        total_images=len(scores),
        total_positives=len(pos_scores),
        total_negatives=len(neg_scores),
        scores=scores,
        auc_roc=compute_auc_roc(scores),
        auc_pr=compute_auc_pr(scores),
    )
    
    # Distribution stats
    result.positive_mean = float(np.mean(pos_scores)) if pos_scores else 0.0
    result.positive_std = float(np.std(pos_scores)) if pos_scores else 0.0
    result.negative_mean = float(np.mean(neg_scores)) if neg_scores else 0.0
    result.negative_std = float(np.std(neg_scores)) if neg_scores else 0.0
    denom = result.positive_std + result.negative_std
    result.separation_gap = (
        (result.positive_mean - result.negative_mean) / denom if denom > 0 else 0.0
    )
    result.pos_percentiles = compute_percentiles(pos_scores)
    result.neg_percentiles = compute_percentiles(neg_scores)
    
    # Find best F1 threshold
    all_thresholds = sorted(set(s.score for s in scores))
    best_f1 = -1.0
    best_f1_metrics = None
    for t in all_thresholds:
        m = compute_threshold_metrics(scores, t)
        if m.f1 > best_f1:
            best_f1 = m.f1
            best_f1_metrics = m
    result.best_f1_threshold = best_f1_metrics
    
    # Youden's J (maximises TPR - FPR = recall + specificity - 1)
    best_j = -2.0
    best_j_metrics = None
    for t in all_thresholds:
        m = compute_threshold_metrics(scores, t)
        specificity = m.tn / (m.tn + m.fp) if (m.tn + m.fp) > 0 else 0.0
        j = m.recall + specificity - 1.0
        if j > best_j:
            best_j = j
            best_j_metrics = m
    result.youdens_j_threshold = best_j_metrics
    
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Training curve analysis
# ═══════════════════════════════════════════════════════════════════════════════

async def training_curve_analysis(
    provider: LocalCLIPProvider,
    all_embeddings: dict[int, np.ndarray],
    positive_ids: set[int],
    concept_name: str,
    max_source_counts: list[int],
) -> list[DiscriminationResult]:
    """Build prototypes from different numbers of source images and measure discrimination.
    
    For each N in max_source_counts:
      - Randomly sample N positive images
      - Build a prototype from their embeddings
      - Score ALL images against that prototype
      - Measure AUC, separation, etc.
    
    This shows how prototype quality scales with training data.
    """
    positive_embeddings = {
        img_id: emb for img_id, emb in all_embeddings.items()
        if img_id in positive_ids
    }
    
    if not positive_embeddings:
        logger.error("No positive embeddings found for training curve")
        return []
    
    total_positives = len(positive_embeddings)
    logger.info(f"Training curve: {total_positives} positive images available")
    
    results = []
    
    for n in max_source_counts:
        actual_n = min(n, total_positives)
        if actual_n == 0:
            continue
        
        # Run multiple trials and average (for stability when n is small)
        num_trials = 5 if actual_n < total_positives else 1
        trial_results = []
        
        for trial in range(num_trials):
            if actual_n >= total_positives:
                sampled_ids = list(positive_embeddings.keys())
            else:
                sampled_ids = random.sample(
                    list(positive_embeddings.keys()), actual_n
                )
            
            # Build prototype from sampled positives
            sampled_embs = np.stack([positive_embeddings[i] for i in sampled_ids])
            prototype = await build_prototype_from_embeddings(sampled_embs)
            
            # Score ALL images (including the training ones — that's fine,
            # we're measuring how well the prototype discriminates)
            result = analyze_discrimination(
                concept_name, actual_n, all_embeddings, positive_ids, prototype
            )
            trial_results.append(result)
        
        # Average across trials
        avg_result = DiscriminationResult(
            concept_name=concept_name,
            source_count=actual_n,
            total_images=trial_results[0].total_images,
            total_positives=trial_results[0].total_positives,
            total_negatives=trial_results[0].total_negatives,
            auc_roc=float(np.mean([r.auc_roc for r in trial_results])),
            auc_pr=float(np.mean([r.auc_pr for r in trial_results])),
            positive_mean=float(np.mean([r.positive_mean for r in trial_results])),
            positive_std=float(np.mean([r.positive_std for r in trial_results])),
            negative_mean=float(np.mean([r.negative_mean for r in trial_results])),
            negative_std=float(np.mean([r.negative_std for r in trial_results])),
            separation_gap=float(np.mean([r.separation_gap for r in trial_results])),
            scores=trial_results[0].scores,  # keep one set of scores for reference
            best_f1_threshold=trial_results[0].best_f1_threshold,
            youdens_j_threshold=trial_results[0].youdens_j_threshold,
            pos_percentiles=trial_results[0].pos_percentiles,
            neg_percentiles=trial_results[0].neg_percentiles,
        )
        results.append(avg_result)
        
        logger.info(
            f"  N={actual_n:4d}: AUC_ROC={avg_result.auc_roc:.4f}  "
            f"AUC_PR={avg_result.auc_pr:.4f}  "
            f"sep_gap={avg_result.separation_gap:.3f}  "
            f"pos_mean={avg_result.positive_mean:.4f}  "
            f"neg_mean={avg_result.negative_mean:.4f}"
        )
    
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Output
# ═══════════════════════════════════════════════════════════════════════════════

def result_to_dict(r: DiscriminationResult) -> dict:
    """Serialize a DiscriminationResult to JSON."""
    def tm_to_dict(tm: Optional[ThresholdMetrics]) -> Optional[dict]:
        if tm is None:
            return None
        return {
            "threshold": round(tm.threshold, 6),
            "tp": tm.tp, "fp": tm.fp, "fn": tm.fn, "tn": tm.tn,
            "precision": round(tm.precision, 4),
            "recall": round(tm.recall, 4),
            "f1": round(tm.f1, 4),
            "accuracy": round(tm.accuracy, 4),
        }
    
    return {
        "concept_name": r.concept_name,
        "source_count": r.source_count,
        "total_images": r.total_images,
        "total_positives": r.total_positives,
        "total_negatives": r.total_negatives,
        "auc_roc": round(r.auc_roc, 6),
        "auc_pr": round(r.auc_pr, 6),
        "separation_gap": round(r.separation_gap, 4),
        "positive_mean": round(r.positive_mean, 6),
        "positive_std": round(r.positive_std, 6),
        "negative_mean": round(r.negative_mean, 6),
        "negative_std": round(r.negative_std, 6),
        "pos_percentiles": {k: round(v, 6) for k, v in r.pos_percentiles.items()},
        "neg_percentiles": {k: round(v, 6) for k, v in r.neg_percentiles.items()},
        "best_f1_threshold": tm_to_dict(r.best_f1_threshold),
        "youdens_j_threshold": tm_to_dict(r.youdens_j_threshold),
    }


def print_summary(full_result: DiscriminationResult, training_results: list[DiscriminationResult]):
    """Print human-readable summary to stdout."""
    print("\n" + "=" * 80)
    print(f"CLIP DISCRIMINATION ANALYSIS: {full_result.concept_name}")
    print("=" * 80)
    
    print(f"\n{'Dataset:':<25} {full_result.total_images} images "
          f"({full_result.total_positives} positive, {full_result.total_negatives} negative)")
    
    print(f"\n{'Score Distributions:'}")
    print(f"  {'':>15} {'Mean':>8} {'Std':>8} {'P5':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P95':>8}")
    pp = full_result.pos_percentiles
    np_ = full_result.neg_percentiles
    print(f"  {'Positive':>15} {full_result.positive_mean:>8.4f} {full_result.positive_std:>8.4f} "
          f"{pp.get('p5',0):>8.4f} {pp.get('p25',0):>8.4f} {pp.get('p50',0):>8.4f} "
          f"{pp.get('p75',0):>8.4f} {pp.get('p95',0):>8.4f}")
    print(f"  {'Negative':>15} {full_result.negative_mean:>8.4f} {full_result.negative_std:>8.4f} "
          f"{np_.get('p5',0):>8.4f} {np_.get('p25',0):>8.4f} {np_.get('p50',0):>8.4f} "
          f"{np_.get('p75',0):>8.4f} {np_.get('p95',0):>8.4f}")
    print(f"\n  Separation gap (Cohen's d-like): {full_result.separation_gap:.3f}")
    
    print(f"\n{'AUC Metrics:'}")
    print(f"  ROC AUC: {full_result.auc_roc:.4f}  (>0.9 = excellent, >0.8 = good, >0.7 = fair)")
    print(f"  PR  AUC: {full_result.auc_pr:.4f}  (baseline = {full_result.total_positives / full_result.total_images:.4f})")
    
    print(f"\n{'Optimal Thresholds:'}")
    if full_result.best_f1_threshold:
        bf = full_result.best_f1_threshold
        print(f"  Best F1:     threshold={bf.threshold:.4f}  "
              f"F1={bf.f1:.4f}  P={bf.precision:.4f}  R={bf.recall:.4f}  "
              f"(TP={bf.tp} FP={bf.fp} FN={bf.fn} TN={bf.tn})")
    if full_result.youdens_j_threshold:
        yj = full_result.youdens_j_threshold
        print(f"  Youden's J:  threshold={yj.threshold:.4f}  "
              f"F1={yj.f1:.4f}  P={yj.precision:.4f}  R={yj.recall:.4f}  "
              f"(TP={yj.tp} FP={yj.fp} FN={yj.fn} TN={yj.tn})")
    
    print(f"\n{'Recommended CLIP Thresholds for Scoring:'}")
    # The p95 of negatives = "95% of true negatives score below this"
    neg_p95 = np_.get('p95', 0)
    neg_p90 = np_.get('p90', 0)
    # The p5 of positives = "5% of true positives score below this"
    pos_p5 = pp.get('p5', 0)
    pos_p10 = pp.get('p10', 0)
    print(f"  Floor (neg p90):     {neg_p90:.4f}  — scores below this are confidently negative")
    print(f"  Floor (neg p95):     {neg_p95:.4f}  — stricter; rejects more false positives")
    print(f"  Ceiling (pos p10):   {pos_p10:.4f}  — scores above this are confidently positive")
    print(f"  Ceiling (pos p5):    {pos_p5:.4f}  — stricter; fewer false negatives")
    mid = (neg_p95 + pos_p5) / 2
    print(f"  Suggested midpoint:  {mid:.4f}  — halfway between neg-p95 and pos-p5")
    
    if training_results:
        print(f"\n{'Training Curve (prototype quality vs source count):'}")
        print(f"  {'N':>6} {'AUC_ROC':>10} {'AUC_PR':>10} {'Sep_Gap':>10} "
              f"{'Pos_Mean':>10} {'Neg_Mean':>10} {'Diff':>10}")
        for r in training_results:
            diff = r.positive_mean - r.negative_mean
            print(f"  {r.source_count:>6} {r.auc_roc:>10.4f} {r.auc_pr:>10.4f} "
                  f"{r.separation_gap:>10.3f} {r.positive_mean:>10.4f} "
                  f"{r.negative_mean:>10.4f} {diff:>10.4f}")
        
        # Find inflection point
        if len(training_results) >= 3:
            aucs = [r.auc_roc for r in training_results]
            ns = [r.source_count for r in training_results]
            print("\n  AUC improvement analysis:")
            for i in range(1, len(aucs)):
                delta = aucs[i] - aucs[i-1]
                print(f"    {ns[i-1]:>5}→{ns[i]:<5}: ΔAUC = {delta:+.4f}")
            
            # Find where improvement drops below 1% relative
            for i in range(1, len(aucs)):
                if aucs[i-1] > 0 and (aucs[i] - aucs[i-1]) / aucs[i-1] < 0.01:
                    print(f"\n  ⚠ Diminishing returns after N={ns[i-1]} "
                          f"(AUC improvement < 1%)")
                    break
    
    print("\n" + "=" * 80)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(
        description="Analyze CLIP concept discrimination quality"
    )
    parser.add_argument(
        "--concept", required=True,
        help="Concept name or alias (e.g. 'shion (tensura)', 'shion')"
    )
    parser.add_argument(
        "--output", default=None,
        help="Path to save JSON results"
    )
    parser.add_argument(
        "--collection-name", default=None,
        help="Collection name (or substring) for ground-truth positives — "
             "all images in this collection count as positive matches"
    )
    parser.add_argument(
        "--training-sizes", type=str, default="5,10,20,50,100,200,500",
        help="Comma-separated source counts for training curve"
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for sampling"
    )
    parser.add_argument(
        "--cache-path", default=None,
        help="Path to pickle cache for embeddings (saves re-encoding on re-runs)"
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable embedding cache (always re-encode)"
    )
    args = parser.parse_args()
    
    random.seed(args.seed)
    np.random.seed(args.seed)
    
    # ── Connect to DB ──
    db = SessionLocal()
    
    # ── Resolve concept ──
    concept = resolve_concept(db, args.concept)
    logger.info(f"Concept: {concept.canonical_name} (id={concept.id})")
    
    # ── Get ground truth ──
    positive_ids = get_positive_image_ids(
        db, concept.id, collection_name=args.collection_name,
    )
    all_images = get_all_images_with_local_files(db)
    logger.info(f"Ground truth: {len(positive_ids)} positive images, "
                f"{len(all_images)} total images with local files")
    
    # ── Get CLIP provider ──
    provider = get_clip_provider()
    if provider is None:
        logger.info("CLIP provider not set, initializing LocalCLIPProvider...")
        from config import CLIP_MODEL_NAME, CLIP_PRETRAINED, CLIP_FORCE_CPU
        try:
            provider = LocalCLIPProvider(
                model_name=CLIP_MODEL_NAME,
                pretrained=CLIP_PRETRAINED,
                force_cpu=CLIP_FORCE_CPU,
            )
            set_clip_provider(provider)
            logger.info(f"CLIP ready: model={CLIP_MODEL_NAME}, pretrained={CLIP_PRETRAINED}")
        except Exception as exc:
            logger.error(f"Failed to initialize CLIP: {exc}")
            return
    
    # ── Encode all images (with optional cache) ──
    use_cache = (
        not args.no_cache
        and args.cache_path
        and os.path.exists(args.cache_path)
    )
    if use_cache:
        logger.info(f"Loading embeddings from cache: {args.cache_path}")
        with open(args.cache_path, "rb") as f:
            cached = pickle.load(f)
        # Only keep images that are still in our current set
        embeddings = {
            img_id: emb for img_id, emb in cached.items()
            if img_id in {i[0] for i in all_images}
        }
        # Check if we need to encode any new images
        missing_ids = {i[0] for i in all_images} - set(embeddings.keys())
        if missing_ids:
            logger.info(f"Encoding {len(missing_ids)} new images not in cache...")
            missing_images = [i for i in all_images if i[0] in missing_ids]
            new_embs = await encode_all_images(provider, missing_images)
            embeddings.update(new_embs)
        logger.info(f"Total embeddings: {len(embeddings)}")
    else:
        logger.info(f"Encoding {len(all_images)} images via CLIP...")
        embeddings = await encode_all_images(provider, all_images)
        logger.info(f"Successfully encoded {len(embeddings)} images")
    
    # Save cache
    if not args.no_cache and args.cache_path:
        with open(args.cache_path, "wb") as f:
            pickle.dump(embeddings, f)
        logger.info(f"Saved embeddings cache to {args.cache_path}")
    
    # ── Build prototype from ALL positives ──
    positive_embeddings = {
        img_id: emb for img_id, emb in embeddings.items()
        if img_id in positive_ids
    }
    
    if len(positive_embeddings) < 2:
        logger.error(f"Only {len(positive_embeddings)} positive images with embeddings — "
                     f"need at least 2")
        return
    
    logger.info(f"Building prototype from {len(positive_embeddings)} positive images...")
    all_pos_embs = np.stack(list(positive_embeddings.values()))
    full_prototype = await build_prototype_from_embeddings(all_pos_embs)
    
    # ── Full discrimination analysis ──
    logger.info("Computing discrimination metrics...")
    full_result = analyze_discrimination(
        concept.canonical_name,
        len(positive_embeddings),
        embeddings,
        positive_ids,
        full_prototype,
    )
    
    # ── Training curve ──
    training_sizes = [int(x) for x in args.training_sizes.split(",")]
    logger.info(f"Running training curve analysis with sizes: {training_sizes}")
    training_results = await training_curve_analysis(
        provider, embeddings, positive_ids,
        concept.canonical_name, training_sizes,
    )
    
    # ── Print summary ──
    print_summary(full_result, training_results)
    
    # ── Save JSON ──
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "concept": concept.canonical_name,
            "concept_id": concept.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "full_analysis": result_to_dict(full_result),
            "training_curve": [result_to_dict(r) for r in training_results],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output_data, indent=2))
        logger.info(f"Results saved to {output_path}")
    
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
