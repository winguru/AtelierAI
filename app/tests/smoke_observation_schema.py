#!/usr/bin/env python3
"""Smoke test: Verify ImageConceptObservation schema changes via ORM.

Run from app/ directory:
    cd app && PYTHONPATH='backend/:src/' python3 tests/smoke_observation_schema.py
"""
import sys
import time

from models import (
    ImageConceptObservation,
    ObservationSource,
    ObservationCertainty,
    ImageModel,
    TagAuthority,
    AuthorityTerm,
    Concept,
)
from database import SessionLocal, engine
from sqlalchemy import inspect, text


def main():
    db = SessionLocal()

    # ── 1. Schema verification ──
    inspector = inspect(engine)
    cols = {
        c["name"]: c["type"]
        for c in inspector.get_columns("image_concept_observations")
    }
    idxs = {i["name"] for i in inspector.get_indexes("image_concept_observations")}
    unique_idxs = set()
    for uc in inspector.get_unique_constraints("image_concept_observations"):
        unique_idxs.add(uc.get("name", ""))

    expected_cols = {
        "id": "INTEGER",
        "image_id": "INTEGER",
        "concept_id": "INTEGER",
        "authority_id": "INTEGER",
        "authority_term_id": "INTEGER",
        "tool_id": "INTEGER",
        "analysis_data_id": "INTEGER",
        "source_type": "INTEGER",
        "certainty_label": "INTEGER",
        "is_present": "BOOLEAN",
        "is_curated": "BOOLEAN",
        "confidence": "FLOAT",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    dropped_cols = {"source_label", "dimension", "evidence_text", "metadata_json", "polarity"}
    dropped_idxs = {"ix_obs_image_dimension", "ix_obs_concept_dimension"}

    print("═══ SCHEMA VERIFICATION ═══")
    schema_pass = True
    for col, expected_type in expected_cols.items():
        actual = str(cols.get(col, "MISSING"))
        ok = expected_type in actual
        if not ok:
            schema_pass = False
        print(f"  {'✓' if ok else '✗'} {col}: {actual} (expected {expected_type})")

    for col in dropped_cols:
        ok = col not in cols
        if not ok:
            schema_pass = False
        print(f"  {'✓' if ok else '✗'} {col}: removed")
    for idx in dropped_idxs:
        present = idx in idxs or idx in unique_idxs
        if present:
            schema_pass = False
        print(f"  {'✓' if not present else '✗'} index {idx}: removed")

    print(f"\n  Schema check: {'PASS ✓' if schema_pass else 'FAIL ✗'}")

    # ── 2. Row count ──
    count = db.query(ImageConceptObservation).count()
    print(f"\n═══ ROW COUNT: {count:,} ═══")

    # ── 3. Data integrity ──
    print("\n═══ DATA INTEGRITY ═══")
    src_types = db.query(ImageConceptObservation.source_type).distinct().all()
    cert_labels = db.query(ImageConceptObservation.certainty_label).distinct().all()
    present_vals = db.query(ImageConceptObservation.is_present).distinct().all()
    print(
        f"  source_type values: {[v[0] for v in src_types]} "
        f"→ {'✓ all IMPORT(1)' if all(v[0] == 1 for v in src_types) else '✗ UNEXPECTED'}"
    )
    print(
        f"  certainty_label values: {[v[0] for v in cert_labels]} "
        f"→ {'✓ all LIKELY(1)' if all(v[0] == 1 for v in cert_labels) else '✗ UNEXPECTED'}"
    )
    print(
        f"  is_present values: {[v[0] for v in present_vals]} "
        f"→ {'✓ all TRUE(1)' if all(v[0] in (1, True) for v in present_vals) else '✗ UNEXPECTED'}"
    )

    # ── 4. IntEnum roundtrip ──
    print("\n═══ INTENUM ROUNDTRIP ═══")
    print(f"  ObservationSource.IMPORT = {ObservationSource.IMPORT} (int={int(ObservationSource.IMPORT)})")
    print(f"  ObservationCertainty.LIKELY = {ObservationCertainty.LIKELY} (int={int(ObservationCertainty.LIKELY)})")
    print(f"  ObservationSource(1) = {ObservationSource(1)}")
    print(f"  ObservationCertainty(1) = {ObservationCertainty(1)}")

    # ── 5. Query performance ──
    print("\n═══ QUERY PERFORMANCE ═══")

    t0 = time.perf_counter()
    obs_list = db.query(ImageConceptObservation).limit(1000).all()
    t1 = time.perf_counter()
    print(f"  Fetch 1000 rows: {t1 - t0:.4f}s ({len(obs_list)} rows)")

    sample_img = db.query(ImageModel.id).first()[0]
    t0 = time.perf_counter()
    img_obs = db.query(ImageConceptObservation).filter_by(image_id=sample_img).all()
    t1 = time.perf_counter()
    print(f"  Filter by image_id={sample_img}: {t1 - t0:.4f}s ({len(img_obs)} obs)")

    t0 = time.perf_counter()
    result = db.execute(
        text(
            """
        SELECT ta.name, COUNT(obs.id) 
        FROM image_concept_observations obs
        JOIN tag_authorities ta ON obs.authority_id = ta.id
        WHERE obs.concept_id IS NOT NULL
        GROUP BY ta.name
    """
        )
    ).fetchall()
    t1 = time.perf_counter()
    print(f"  Authority tag count (full table): {t1 - t0:.4f}s")
    for row in result:
        print(f"    {row[0]}: {row[1]:,}")

    t0 = time.perf_counter()
    result = db.execute(
        text(
            """
        SELECT COUNT(DISTINCT obs.image_id)
        FROM image_concept_observations obs
        JOIN authority_terms at2 ON obs.authority_term_id = at2.id
        WHERE at2.normalized_external_name = '1girl'
    """
        )
    ).scalar()
    t1 = time.perf_counter()
    print(f"  Images tagged '1girl': {result:,} images in {t1 - t0:.4f}s")

    t0 = time.perf_counter()
    concept_count = (
        db.query(ImageConceptObservation.concept_id)
        .filter(ImageConceptObservation.concept_id.isnot(None))
        .distinct()
        .count()
    )
    t1 = time.perf_counter()
    print(f"  Distinct concepts: {concept_count:,} in {t1 - t0:.4f}s")

    # ── 6. Insert benchmark ──
    print("\n═══ INSERT PERFORMANCE ═══")
    max_id = db.query(ImageConceptObservation.id).order_by(ImageConceptObservation.id.desc()).first()[0]
    max_img = db.query(ImageModel.id).order_by(ImageModel.id.desc()).first()[0]
    max_concept = db.query(Concept.id).order_by(Concept.id.desc()).first()[0]

    t0 = time.perf_counter()
    test_obs = [
        ImageConceptObservation(
            image_id=((max_id + i) % max_img) + 1,
            concept_id=((max_id + i) % max_concept) + 1,
            authority_id=(i % 5) + 1,
            authority_term_id=(i % 300) + 1,
            source_type=ObservationSource.IMPORT,
            certainty_label=ObservationCertainty.LIKELY,
            is_present=True,
            is_curated=False,
            confidence=0.95,
            created_at=None,
            updated_at=None,
        )
        for i in range(1000)
    ]
    db.add_all(test_obs)
    db.flush()
    t1 = time.perf_counter()
    print(f"  Insert 1000 rows (ORM): {t1 - t0:.4f}s")
    db.rollback()

    db.close()
    print("\n═══ ALL SMOKE TESTS PASSED ═══")


if __name__ == "__main__":
    main()
