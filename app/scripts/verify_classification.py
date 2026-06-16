"""Verify classification results."""
from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: import path setup

from backend.database import SessionLocal
from backend.models import Concept
from sqlalchemy import func

db = SessionLocal()

# Check all super-categories
supers = db.query(Concept).filter(Concept.id >= 7236, Concept.id <= 7250).order_by(Concept.id).all()
print("Super-category parent concepts:")
for s in supers:
    print(f"  id={s.id}  type={s.concept_type or '(none)'}  slug={s.slug}  name={s.canonical_name}")

print()

# Count children per super-category
print("Children per super-category:")
for s in supers:
    count = db.query(func.count(Concept.id)).filter(Concept.parent_concept_id == s.id).scalar()
    print(f"  {s.canonical_name}: {count} children")

print()

# Total concepts with parents
with_parent = db.query(func.count(Concept.id)).filter(Concept.parent_concept_id.isnot(None)).scalar()
total = db.query(func.count(Concept.id)).scalar()
orphans = db.query(func.count(Concept.id)).filter(Concept.parent_concept_id.is_(None)).scalar()
print(f"Total concepts: {total}")
print(f"With parent: {with_parent}")
print(f"Orphans (no parent): {orphans}")

db.close()
