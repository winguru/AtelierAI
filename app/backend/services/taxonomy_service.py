from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Concept, ConceptAlias, TagAuthority


class TaxonomyService:
    """Encapsulates taxonomy normalization and helper operations."""

    @staticmethod
    def normalize_text(value: str) -> str:
        normalized = (value or "").strip().replace("_", " ").lower()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized

    def duplicate_key(self, value: str) -> str:
        lowered = self.normalize_text(value)
        return re.sub(r"[^a-z0-9]+", "", lowered)

    def slugify_concept_name(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.normalize_text(value)).strip("-")
        return slug or "concept"

    @staticmethod
    def ensure_unique_concept_slug(db: Session, base_slug: str) -> str:
        slug = base_slug
        idx = 2
        while db.query(Concept.id).filter(Concept.slug == slug).first() is not None:
            slug = f"{base_slug}-{idx}"
            idx += 1
        return slug

    def get_or_create_authority(self, db: Session, authority_name: str) -> TagAuthority:
        normalized = (authority_name or "user").strip().lower() or "user"
        authority = db.query(TagAuthority).filter(func.lower(TagAuthority.name) == normalized).first()
        if authority is not None:
            return authority

        defaults = {
            "civitai": {
                "description": "CivitAI native tag authority and IDs.",
                "is_external": True,
                "base_url": "https://civitai.com",
            },
            "danbooru": {
                "description": "Danbooru tag authority and IDs.",
                "is_external": True,
                "base_url": "https://danbooru.donmai.us",
            },
            "user": {
                "description": "User-curated local tags and concepts.",
                "is_external": False,
                "base_url": None,
            },
        }
        config = defaults.get(
            normalized,
            {
                "description": f"Imported taxonomy authority: {normalized}",
                "is_external": False,
                "base_url": None,
            },
        )
        authority = TagAuthority(name=normalized, **config)
        db.add(authority)
        db.flush()
        return authority

    def get_or_create_concept(self, db: Session, canonical_name: str) -> Concept:
        normalized_name = self.normalize_text(canonical_name)
        concept = db.query(Concept).filter(Concept.canonical_name == normalized_name).first()
        if concept is not None:
            return concept

        slug = self.ensure_unique_concept_slug(db, self.slugify_concept_name(normalized_name))
        concept = Concept(
            canonical_name=normalized_name,
            slug=slug,
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(concept)
        db.flush()
        return concept

    def ensure_alias_for_concept(
        self,
        db: Session,
        concept_id: int,
        alias_text: str,
        alias_type: str = "synonym",
        authority_id: Optional[int] = None,
        external_tag_id: Optional[str] = None,
    ) -> bool:
        normalized_alias = self.normalize_text(alias_text)
        if not normalized_alias:
            return False
        existing = (
            db.query(ConceptAlias)
            .filter(
                ConceptAlias.concept_id == concept_id,
                ConceptAlias.normalized_alias == normalized_alias,
            )
            .first()
        )
        if existing is not None:
            return False

        db.add(
            ConceptAlias(
                concept_id=concept_id,
                alias=alias_text,
                normalized_alias=normalized_alias,
                alias_type=alias_type,
                is_preferred=alias_type == "canonical",
                authority_id=authority_id,
                external_tag_id=external_tag_id,
            )
        )
        db.flush()
        return True

    def parse_bootstrap_terms(self, format_name: str, raw_text: str) -> list[dict]:
        fmt = (format_name or "json").strip().lower()
        if fmt not in {"json", "csv"}:
            raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")

        if fmt == "json":
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

            if isinstance(data, dict) and isinstance(data.get("terms"), list):
                data = data["terms"]
            if not isinstance(data, list):
                raise HTTPException(status_code=400, detail="JSON must be a list or an object with a 'terms' list")

            rows: list[dict] = []
            for item in data:
                if isinstance(item, str):
                    rows.append({"name": item})
                elif isinstance(item, dict):
                    rows.append(item)
            return rows

        lines = [line for line in (raw_text or "").splitlines() if line.strip()]
        if not lines:
            return []

        reader = csv.DictReader(lines)
        if not reader.fieldnames:
            raise HTTPException(status_code=400, detail="CSV must include a header row")
        rows = []
        for row in reader:
            rows.append(row)
        return rows
