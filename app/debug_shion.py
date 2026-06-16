import sys
sys.path.insert(0, "src")
sys.path.insert(0, "backend")

from database import SessionLocal
from models import AuthorityTerm, ImageConceptObservation, Concept, ConceptAlias, TagAuthority
from sqlalchemy import func

db = SessionLocal()

print("=== Concepts matching shion ===")
for c in db.query(Concept).filter(Concept.canonical_name.ilike("%shion%")).all():
    print(f"  id={c.id} name={c.canonical_name} status={c.status}")

print("\n=== Authority terms matching shion ===")
for t in db.query(AuthorityTerm).filter(AuthorityTerm.external_name.ilike("%shion%")).all():
    auth = db.query(TagAuthority).get(t.authority_id)
    print(f"  id={t.id} name={t.external_name} auth={auth.name} concept_id={t.concept_id}")

print("\n=== Observations for shion concepts ===")
for c in db.query(Concept).filter(Concept.canonical_name.ilike("%shion%")).all():
    cnt = db.query(ImageConceptObservation).filter(ImageConceptObservation.concept_id == c.id).count()
    print(f"  concept {c.canonical_name} (id={c.id}): {cnt} obs")

print("\n=== Observations via authority terms ===")
for t in db.query(AuthorityTerm).filter(AuthorityTerm.external_name.ilike("%shion%")).all():
    cnt = db.query(ImageConceptObservation).filter(ImageConceptObservation.authority_term_id == t.id).count()
    print(f"  term {t.external_name} (id={t.id}): {cnt} obs")

print("\n=== Prompt authority terms stats ===")
pa = db.query(TagAuthority).filter(TagAuthority.name == "prompt").first()
if pa:
    total = db.query(AuthorityTerm).filter(AuthorityTerm.authority_id == pa.id).count()
    with_c = db.query(AuthorityTerm).filter(AuthorityTerm.authority_id == pa.id, AuthorityTerm.concept_id.isnot(None)).count()
    print(f"  Total: {total}, With concept: {with_c}, Without: {total - with_c}")

print("\n=== All authorities ===")
for a in db.query(TagAuthority).all():
    tc = db.query(AuthorityTerm).filter(AuthorityTerm.authority_id == a.id).count()
    wc = db.query(AuthorityTerm).filter(AuthorityTerm.authority_id == a.id, AuthorityTerm.concept_id.isnot(None)).count()
    print(f"  {a.name}: {tc} terms, {wc} with concept")

# Also check: how many images have "shion" in their prompt tags (via prompt_phrases)?
print("\n=== Images with 'shion' prompt tag name ===")
from models import ImageModel
images_with_shion = db.query(ImageModel).filter(ImageModel.prompt_tags.cast(db.String).ilike("%shion%")).count()
print(f"  Images with shion in prompt_tags JSON: {images_with_shion}")

db.close()
