"""Tests for CivitAI tag observation insertion (concept-keyed dedupe).

Covers the sync-path IntegrityError where two tags in one image's batch map
to authority_terms that share the same concept_id. The UNIQUE constraint on
image_concept_observations is (image_id, concept_id, authority_id) —
authority_term_id is NOT part of it — so inserting one observation per term
collides when several terms are aliases of one concept (e.g. "forest" and
"full moon" both mapped to the same concept under the civitai authority).
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from database import Base  # noqa: E402
from models import (  # noqa: E402
    AuthorityTerm,
    Concept,
    ImageConceptObservation,
    ImageModel,
    TagAuthority,
)
from main import _insert_tag_observations_for_image  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False)
    session = TestSession()
    yield session
    session.close()
    engine.dispose()


def _seed(db, tag_pairs):
    """Seed authorities/terms/concepts and an image; return (image_id, records).

    tag_pairs: list of (external_tag_id, name, concept) — pass the SAME Concept
    object across pairs that should collide on the concept-keyed constraint.
    """
    authority = TagAuthority(name="civitai")
    db.add(authority)
    db.flush()

    image = ImageModel(
        file_path="x.png",
        file_name="x.png",
        file_hash="hash-1",
        file_size=100,
    )
    db.add(image)
    db.flush()

    records = []
    for ext_id, name, concept in tag_pairs:
        if concept.id is None:
            db.add(concept)
            db.flush()
        db.add(
            AuthorityTerm(
                authority_id=authority.id,
                external_tag_id=ext_id,
                external_name=name,
                normalized_external_name=name.lower(),
                concept_id=concept.id,
            )
        )
        records.append({"id": ext_id, "name": name})

    db.flush()
    return image.id, records


def test_two_terms_same_concept_insert_single_observation(db):
    """Two tags whose terms share one concept must yield ONE observation."""
    concept = Concept(canonical_name="indoors", slug="indoors")
    image_id, records = _seed(
        db,
        [
            (2304, "forest", concept),
            (2574, "full moon", concept),
        ],
    )

    inserted = _insert_tag_observations_for_image(
        db, image_db_id=image_id, tag_records=records
    )

    observations = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.image_id == image_id)
        .all()
    )
    assert inserted == 1
    assert len(observations) == 1
    assert observations[0].concept_id == concept.id
    # The first tag wins; the alias is skipped, not duplicated.
    forest_term = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.external_tag_id == 2304)
        .one()
    )
    assert observations[0].authority_term_id == forest_term.id


def test_second_call_is_idempotent(db):
    """Re-running the insert for the same tags must not duplicate rows."""
    concept = Concept(canonical_name="indoors", slug="indoors")
    image_id, records = _seed(
        db,
        [
            (2304, "forest", concept),
            (2574, "full moon", concept),
        ],
    )

    _insert_tag_observations_for_image(db, image_db_id=image_id, tag_records=records)
    inserted_second = _insert_tag_observations_for_image(
        db, image_db_id=image_id, tag_records=records
    )

    observations = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.image_id == image_id)
        .all()
    )
    assert inserted_second == 0
    assert len(observations) == 1


def test_replay_after_rollback_does_not_poison(db):
    """A conflicting observation insert rolled back, then a retry through the
    helper must insert cleanly (no IntegrityError, no PendingRollbackError)."""
    concept = Concept(canonical_name="indoors", slug="indoors")
    image_id, records = _seed(
        db,
        [
            (2304, "forest", concept),
            (2574, "full moon", concept),
        ],
    )
    # Commit the seed so the rollback below only discards the conflicting
    # observation, not the image/terms/concept the retry depends on.
    db.commit()
    # Add an observation that would collide on (image, concept, authority),
    # via the first batch term itself, flush it, then roll the transaction
    # back entirely.
    first_term = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.external_tag_id == 2304)
        .one()
    )
    db.add(
        ImageConceptObservation(
            image_id=image_id,
            concept_id=concept.id,
            authority_id=1,
            authority_term_id=first_term.id,
            source_type=1,
            certainty_label=1,
            is_present=True,
            is_curated=False,
        )
    )
    db.flush()
    db.rollback()

    inserted = _insert_tag_observations_for_image(
        db, image_db_id=image_id, tag_records=records
    )
    count = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.image_id == image_id)
        .count()
    )
    assert inserted == 1
    assert count == 1


def test_distinct_concepts_insert_both(db):
    """Tags mapping to distinct concepts must each get an observation."""
    concept_a = Concept(canonical_name="indoors", slug="indoors")
    concept_b = Concept(canonical_name="outdoors", slug="outdoors")
    image_id, records = _seed(
        db,
        [
            (2304, "forest", concept_a),
            (2946, "indoors", concept_b),
        ],
    )

    inserted = _insert_tag_observations_for_image(
        db, image_db_id=image_id, tag_records=records
    )

    observations = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.image_id == image_id)
        .all()
    )
    assert inserted == 2
    assert len(observations) == 2
