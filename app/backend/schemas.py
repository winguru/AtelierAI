# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/taxonomy-import.md
# ──────────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import Any, ClassVar, Literal, Optional

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    folder_path: str


# Valid values for user NSFW overrides; empty string clears the override.
_VALID_USER_NSFW_RATINGS = {"pg", "pg13", "r", "x", "xxx"}
_VALID_USER_NSFW_SAFETY_CLASSES = {"safe", "mature", "explicit"}


class ImageUpdateRequest(BaseModel):
    source_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_profile: Optional[str] = None
    user_tags: Optional[list[str]] = None
    user_negative_tags: Optional[list[str]] = None
    # User NSFW overrides. Set to a valid value to override the source rating;
    # set to "" (empty string) to clear a previously set override.
    user_nsfw_rating: Optional[str] = None
    user_nsfw_safety_class: Optional[str] = None


class CivitaiImportRequest(BaseModel):
    import_type: Literal["collection", "image", "post"]
    value: str
    limit: Optional[int] = None


class CivitaiCollectionSyncRequest(BaseModel):
    limit: Optional[int] = None


class CivitaiNsfwBackfillRequest(BaseModel):
    limit: Optional[int] = None
    reimport_if_missing: bool = False


class CivitaiCookieRequest(BaseModel):
    cookie: str = Field(..., min_length=100, description="The __Secure-civitai-token cookie value (JWT-like string starting with eyJ).")


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
    external_tag_id: Optional[int] = None


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


# ---------------------------------------------------------------------------
# Unified Gallery Filter
# ---------------------------------------------------------------------------

# Valid term type prefixes for the 4-concept gallery filter.
# Format: "type:value" e.g. "tag:portrait", "artist:Rembrandt"
INCLUDED_TERM_TYPES = frozenset({
    "tag", "artist", "collection", "model", "source", "software", "mimetype", "feature",
    "status",
})
EXCLUDED_TERM_TYPES = INCLUDED_TERM_TYPES  # Same types allowed for both directions
HIDDEN_TERM_TYPES = frozenset({"nsfw", "nsfw_safety"})
MISSING_TERMS = frozenset({
    "artist", "prompt", "tags", "model", "checkpoint", "lora",
    "nsfw", "generation", "exif", "source", "civitai_meta",
})


class FilterTerm(BaseModel):
    """A single typed filter term parsed from 'type:value' format."""
    type: str
    value: str
    qualifier: Optional[str] = None  # e.g. "checkpoint" for missing:feature:checkpoint

    class Config:
        frozen = True


class ParsedGalleryFilter(BaseModel):
    """Parsed representation of the 4-concept gallery filter.

    All lists are grouped by type for efficient dispatch to filter functions.
    """
    # Included terms grouped by type: {"tag": ["portrait", "landscape"], "artist": ["Rembrandt"]}
    included_by_type: dict[str, list[str]] = {}
    # Excluded terms grouped by type (same structure)
    excluded_by_type: dict[str, list[str]] = {}
    # Hidden terms grouped by type: {"nsfw": ["xxx"], "nsfw_safety": ["explicit"]}
    hidden_by_type: dict[str, list[str]] = {}
    # Missing keys as a flat set: {"artist", "model", "checkpoint"}
    missing_keys: set[str] = set()
    # Feature-specific missing: {"a1111_hires", "a1111_regional_prompter"}
    missing_features: set[str] = set()
    format: str = "json"
    raw_text: str
    dry_run: bool = True

    # Relational filter types — these modify the Query object directly
    # (source, mimetype, artist, collection, model, software) rather than
    # returning ID sets.
    _RELATIONAL_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"source", "mimetype", "artist", "collection", "model", "software"}
    )

    def has_relational_filters(self) -> bool:
        """Return True if any relational filter clauses are present."""
        for t in self._RELATIONAL_TYPES:
            if self.included_by_type.get(t) or self.excluded_by_type.get(t):
                return True
        return False


class TaxonomyTagAssociationRequest(BaseModel):
    authority_term_id: int
    concept_id: int
    tag_name: Optional[str] = None
    tag_source: Optional[str] = None


class TaxonomyTagDetailsUpdateRequest(BaseModel):
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    implies: Optional[list[str]] = None
    examples: Optional[list[str]] = None


class TaxonomyTagMaintUpdateRequest(BaseModel):
    authority_term_id: int
    field: Literal["external_name", "external_tag_id", "concept_id"]
    value: Any = None


class TaxonomyTagMaintBulkDeleteRequest(BaseModel):
    authority_term_ids: list[int] = Field(max_length=500)
    dry_run: bool = True


class TaxonomyTagMaintPurgeRequest(BaseModel):
    dry_run: bool = True


class GenerationTemplatePathMapping(BaseModel):
    token: str
    target_path: str
    required: bool = True
    value_type: Literal["auto", "string", "integer", "number", "boolean", "json"] = "auto"
    default_value: Optional[Any] = None


# --- Variant Group Schemas ---


class VariantGroupCreateRequest(BaseModel):
    group_type: str  # "hash_duplicate" | "civitai_multi_resource" | "img2img" | "manual"
    group_label: Optional[str] = None
    image_ids: list[int] = Field(default_factory=list, max_length=500)


class VariantGroupUpdateRequest(BaseModel):
    group_label: Optional[str] = None
    cover_image_id: Optional[int] = None
    cover_preference: Optional[str] = None


class VariantGroupAddMembersRequest(BaseModel):
    image_ids: list[int] = Field(min_length=1, max_length=500)
    role_in_group: Optional[str] = "member"


class GenerationTemplateImportRequest(BaseModel):
    name: str
    description: Optional[str] = None
    workflow_json: dict[str, Any]
    mappings: list[GenerationTemplatePathMapping] = Field(default_factory=list)
    default_tokens: dict[str, Any] = Field(default_factory=dict)


class GenerationTemplateResolveRequest(BaseModel):
    source_mode: Literal["local", "civitai"]
    file_hash: Optional[str] = None
    image_id: Optional[int] = None
    token_overrides: dict[str, Any] = Field(default_factory=dict)
    include_generation_payload: bool = False


class GenerationTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    workflow_json: Optional[dict[str, Any]] = None
    mappings: Optional[list[GenerationTemplatePathMapping]] = None
    default_tokens: Optional[dict[str, Any]] = None


class A1111BridgeAnalyzeRequest(BaseModel):
    file_hash: str
    comfy_workflow_json: Optional[dict[str, Any]] = None
    include_generation_payload: bool = False


class A1111BridgeSaveRequest(BaseModel):
    analysis_payload: dict[str, Any]
    file_name: Optional[str] = None


class ComfyGenerateCompareRequest(BaseModel):
    workflow_json: dict[str, Any]
    reference_file_hash: str
    include_all_workspace_images: bool = False
    match_threshold_override: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    tweak_label: Optional[str] = None
    tweaked_parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: Optional[int] = Field(default=120, ge=10, le=600)
    poll_interval_seconds: Optional[float] = Field(default=1.25, ge=0.2, le=10.0)


class ParityCandidateAuditRequest(BaseModel):
    file_hash: str
    comfy_workflow_json: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# CivitAI Search
# ---------------------------------------------------------------------------


class CivitaiSearchRequest(BaseModel):
    """Simplified search request proxied to the CivitAI Meilisearch host."""

    query: str = ""
    tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    sort_by: str = "stats.reactionCountAllTime:desc"
    limit: int = Field(default=51, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    nsfw_levels: Optional[list[int]] = None
    base_models: Optional[list[str]] = None
    exclude_poi: bool = True
    exclude_minor: bool = True
    username: Optional[str] = None
    facets: Optional[list[str]] = None
    extra_filters: Optional[list[str]] = None


class SyncLabAnalyzeRequest(BaseModel):
    """Sync Lab: analyze-local and fetch-metadata step payload."""
    image_ids: list[int]


class SyncLabIngestRequest(BaseModel):
    """Sync Lab: ingest step payload."""
    image_ids: list[int]
    collection_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Sync Session — resumable workflow state
# ---------------------------------------------------------------------------

class SyncSessionCreateRequest(BaseModel):
    """Create a new sync session."""
    collection_id: int
    collection_type: str = "image"  # "image" | "post"
    collection_name: Optional[str] = None


class SyncSessionStepUpdateRequest(BaseModel):
    """Update a step's status and optional data for a sync session."""
    step: int  # 3–7
    status: str  # "pending" | "in_progress" | "complete" | "failed" | "cancelled"
    data: Optional[dict] = None
    error_message: Optional[str] = None


class SyncSessionResponse(BaseModel):
    """API response shape for a sync session."""
    id: str
    collection_id: int
    collection_type: str
    collection_name: Optional[str] = None
    step_3_status: str = "pending"
    step_4_status: str = "pending"
    step_5_status: str = "pending"
    step_6_status: str = "pending"
    step_7_status: str = "pending"
    step_3_data: Optional[dict] = None
    step_4_data: Optional[dict] = None
    step_5_data: Optional[dict] = None
    step_6_data: Optional[dict] = None
    step_7_data: Optional[dict] = None
    active_step: Optional[int] = None
    error_message: Optional[str] = None
    is_complete: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True
