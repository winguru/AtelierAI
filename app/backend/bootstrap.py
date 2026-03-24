from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from database import SessionLocal
from models import License, TagAuthority, Tool


_INITIAL_TOOLS = [
    {
        "name": "WD14 Tagger",
        "description": "A fine-tuned CLIP model for generating Danbooru-style tags.",
        "version": "v1.4",
    },
    {
        "name": "BLIP",
        "description": "A multimodal model for generating natural language image captions.",
        "version": "v1",
    },
    {
        "name": "GPT-4-Vision",
        "description": "OpenAI's multimodal model for advanced image analysis and description.",
        "version": "gpt-4-vision-preview",
    },
    {
        "name": "Custom Caption",
        "description": "A user-curated, manually entered description.",
        "version": "user",
    },
]

_INITIAL_LICENSES = [
    {
        "name": "Creative Commons Attribution 4.0 International",
        "short_name": "CC BY 4.0",
        "url": "https://creativecommons.org/licenses/by/4.0/",
        "allows_commercial_use": True,
        "requires_attribution": True,
    },
    {
        "name": "Creative Commons Attribution-NonCommercial 4.0 International",
        "short_name": "CC BY-NC 4.0",
        "url": "https://creativecommons.org/licenses/by-nc/4.0/",
        "allows_commercial_use": False,
        "requires_attribution": True,
    },
    {
        "name": "Public Domain Dedication (CC0)",
        "short_name": "CC0 1.0",
        "url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "allows_commercial_use": True,
        "requires_attribution": False,
    },
    {
        "name": "All Rights Reserved",
        "short_name": "ARR",
        "url": "",
        "allows_commercial_use": False,
        "requires_attribution": False,
    },
]

_INITIAL_AUTHORITIES = [
    {
        "name": "civitai",
        "description": "CivitAI native tag authority and IDs.",
        "is_external": True,
        "base_url": "https://civitai.com",
    },
    {
        "name": "danbooru",
        "description": "Danbooru tag authority and IDs.",
        "is_external": True,
        "base_url": "https://danbooru.donmai.us",
    },
    {
        "name": "user",
        "description": "User-curated local tags and concepts.",
        "is_external": False,
        "base_url": None,
    },
    {
        "name": "ai_agent",
        "description": "Local or remote AI-generated concept observations.",
        "is_external": False,
        "base_url": None,
    },
]


def _seed_tools(db: Session) -> None:
    for tool_data in _INITIAL_TOOLS:
        existing = db.query(Tool).filter(Tool.name == tool_data["name"]).first()
        if existing:
            continue
        db_tool = Tool(**tool_data)
        db.add(db_tool)
        print(f"  - Created tool: {db_tool.name}")


def _seed_licenses(db: Session) -> None:
    for license_data in _INITIAL_LICENSES:
        existing = db.query(License).filter(License.short_name == license_data["short_name"]).first()
        if existing:
            continue
        db_license = License(**license_data)
        db.add(db_license)
        print(f"  - Created license: {db_license.short_name}")


def _seed_authorities(db: Session) -> None:
    for authority_data in _INITIAL_AUTHORITIES:
        existing = db.query(TagAuthority).filter(TagAuthority.name == authority_data["name"]).first()
        if existing:
            continue
        db_authority = TagAuthority(**authority_data)
        db.add(db_authority)
        print(f"  - Created tag authority: {db_authority.name}")


def populate_initial_data(session_factory: Callable[[], Session] = SessionLocal) -> None:
    """Idempotent seed data bootstrap used at startup."""
    db = session_factory()
    try:
        _seed_tools(db)
        _seed_licenses(db)
        _seed_authorities(db)
        db.commit()
        print("Initial data population complete.")
    except Exception as exc:
        print(f"An error occurred during initial data creation: {exc}")
        db.rollback()
    finally:
        db.close()
