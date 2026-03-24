from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class ScanRequest(BaseModel):
    folder_path: str


# Valid values for user NSFW overrides; empty string clears the override.
_VALID_USER_NSFW_RATINGS = {"pg", "pg13", "r", "x", "xxx"}
_VALID_USER_NSFW_SAFETY_CLASSES = {"safe", "mature", "explicit"}


class ImageUpdateRequest(BaseModel):
    source_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_profile: Optional[str] = None
    user_negative_tags: Optional[list[str]] = None
    # User NSFW overrides. Set to a valid value to override the source rating;
    # set to "" (empty string) to clear a previously set override.
    user_nsfw_rating: Optional[str] = None
    user_nsfw_safety_class: Optional[str] = None


class CivitaiImportRequest(BaseModel):
    import_type: Literal["collection", "image"]
    value: str
    limit: Optional[int] = None


class CivitaiCollectionSyncRequest(BaseModel):
    limit: Optional[int] = None


class CivitaiNsfwBackfillRequest(BaseModel):
    limit: Optional[int] = None
    reimport_if_missing: bool = False


class CollectionCreateRequest(BaseModel):
    name: str


class CollectionRenameRequest(BaseModel):
    name: str


class CollectionBulkMembershipRequest(BaseModel):
    file_hashes: list[str]


class TaxonomyAliasCreateRequest(BaseModel):
    alias: str
    alias_type: str = "synonym"
    is_preferred: bool = False
    authority_name: Optional[str] = None
    external_tag_id: Optional[str] = None


class TaxonomyMergeRequest(BaseModel):
    source_concept_id: int
    target_concept_id: int
    create_source_alias: bool = True
    deactivate_source: bool = True
    dry_run: bool = False


class TaxonomyParentUpdateRequest(BaseModel):
    parent_concept_id: Optional[int] = None
    dry_run: bool = False


class TaxonomyConceptCreateRequest(BaseModel):
    canonical_name: str
    parent_concept_id: Optional[int] = None
    description: Optional[str] = None


class TaxonomyPurgeRootsRequest(BaseModel):
    dry_run: bool = True


class TaxonomyConceptUpdateRequest(BaseModel):
    canonical_name: Optional[str] = None
    description: Optional[str] = None


class TaxonomyBootstrapImportRequest(BaseModel):
    authority_name: str = "user"
    format: str = "json"
    raw_text: str
    create_missing_concepts: bool = True
    dry_run: bool = True


class TaxonomyTagAssociationRequest(BaseModel):
    authority_term_id: int
    concept_id: int


class TaxonomyTagDetailsUpdateRequest(BaseModel):
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    implies: Optional[list[str]] = None
    examples: Optional[list[str]] = None
