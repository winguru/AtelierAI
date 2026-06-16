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


class CivitaiBatchImportRequest(BaseModel):
    """Import multiple CivitAI images by their IDs in a single batch.

    The endpoint chunks IDs internally (20 per sub-batch) so the frontend
    can send any number of IDs in one request.
    """

    civitai_image_ids: list[int] = Field(
        ..., min_length=1, description="CivitAI image IDs to import"
    )
    collection_id: Optional[int] = Field(
        None, description="Existing collection ID to add imported images to"
    )
    create_collection_name: Optional[str] = Field(
        None, description="Name for a new collection to create and add images to"
    )


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
    concept_type: Optional[str] = None
    status: Optional[str] = None


class TaxonomyBootstrapImportRequest(BaseModel):
    authority_name: str = "user"


# ---------------------------------------------------------------------------
# Concept Prototype & Visual Similarity (Phase 2)
# ---------------------------------------------------------------------------


class BuildPrototypeRequest(BaseModel):
    """Request body for POST /concepts/{id}/build-prototype."""
    image_urls: list[str] = Field(
        ..., min_length=1, max_length=64,
        description="Reference image URLs to build the prototype from",
    )


class ScoreImageRequest(BaseModel):
    """Request body for GET /concepts/{id}/score (query-param alternative)."""
    image_url: str = Field(..., description="URL of the candidate image to score")
    context_text: Optional[str] = Field(
        None, description="Optional context text for compositional scoring",
    )


class ConceptAttributeEntry(BaseModel):
    """A single attribute linked to a concept."""
    concept_id: int
    attribute_concept_id: int
    attribute_concept_name: str
    attribute_kind: str = "visual"
    invariance: str = "variable"
    consistency_score: Optional[float] = None
    notes: Optional[str] = None


class ConceptAttributeAddRequest(BaseModel):
    """Add an attribute to a concept."""
    attribute_concept_id: int = Field(..., description="ID of the concept to use as an attribute")
    attribute_kind: Literal["visual", "semantic"] = "visual"
    invariance: Literal["invariant", "variable"] = "variable"
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    notes: Optional[str] = None


class ConceptAttributeUpdateRequest(BaseModel):
    """Update an existing attribute link."""
    attribute_kind: Optional[Literal["visual", "semantic"]] = None
    invariance: Optional[Literal["invariant", "variable"]] = None
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    notes: Optional[str] = None


class ConceptProfileResponse(BaseModel):
    """Rich concept profile with prototype stats and linked authority terms."""
    id: int
    canonical_name: str
    slug: str
    description: Optional[str] = None
    status: str
    concept_type: Optional[str] = None
    parent_concept_id: Optional[int] = None
    prototype: Optional[dict] = None
    aliases: list[dict] = []
    authority_terms: list[dict] = []
    attributes: list[ConceptAttributeEntry] = []
    parent_concept: Optional[dict] = None
    children: list[dict] = []


class ScoreImageResponse(BaseModel):
    """Score result for a candidate image against a concept."""
    concept_id: int
    image_url: str
    identity_score: Optional[float] = None
    context_score: Optional[float] = None
    composite_score: Optional[float] = None
    clip_available: bool


# ---------------------------------------------------------------------------
# Prototype Lab
# ---------------------------------------------------------------------------


class AutoBuildPrototypeResponse(BaseModel):
    """Result of auto-building a prototype from observed images."""
    concept_id: int
    concept_name: Optional[str] = None
    status: str  # "built" | "not_found" | "no_images" | "clip_failed"
    source_count: int = 0
    message: str = ""


class BatchBuildRequest(BaseModel):
    """Request body for POST /prototypes/batch-build."""
    concept_ids: list[int] = Field(
        ..., min_length=1, max_length=100,
        description="Concept IDs to build prototypes for",
    )
    max_images: int = Field(
        10, ge=1, le=64,
        description="Max observed images to use per prototype",
    )


class BatchBuildResponse(BaseModel):
    """Result of batch prototype building."""
    total_requested: int
    built: int
    failed: int
    results: list[AutoBuildPrototypeResponse]


class StreamBuildRequest(BaseModel):
    """Request body for POST /prototypes/stream-build (SSE)."""
    concept_ids: list[int] = Field(
        ..., min_length=1, max_length=5000,
        description="Concept IDs to build prototypes for (results stream back via SSE)",
    )
    max_images: int = Field(
        10, ge=1, le=64,
        description="Max observed images to use per prototype",
    )


class PrototypeStatsResponse(BaseModel):
    """Global prototype coverage statistics."""
    total_concepts: int
    with_observations: int
    with_prototypes: int
    observation_brackets: dict[str, int] = {}
    prototype_brackets: dict[str, int] = {}


class TaxonomyConceptTransferImportRequest(BaseModel):
    document: dict[str, Any]
    mode: Literal["graft"] = "graft"
    root_policy: Literal["strict", "permissive"] = "strict"
    dry_run: bool = True


class TaxonomySnapshotImportResponse(BaseModel):
    """Response from the snapshot import endpoint."""
    status: str  # "completed" | "dry_run" | "aborted"
    snapshot_format: str
    snapshot_version: int
    source_file: str
    backup_path: Optional[str] = None
    imported: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Concept Search Pipeline (Phase 3A)
# ---------------------------------------------------------------------------


class ConceptSearchRequest(BaseModel):
    """Request body for POST /concept-search."""
    query: str = Field(..., min_length=1, max_length=500, description="Natural language search query")
    limit: int = Field(30, ge=1, le=100, description="Maximum results to return")
    pool_multiplier: int = Field(
        3, ge=1, le=10,
        description="How many candidates to pre-filter per result slot",
    )


class ConceptSearchMatchedConcept(BaseModel):
    """A concept matched during query decomposition."""
    concept_id: int
    surface_form: str
    concept_name: str
    match_type: str  # "canonical" | "alias"


class DecomposeResponse(BaseModel):
    """Result of query decomposition (lab debug endpoint)."""
    original_query: str
    matched_concepts: list[ConceptSearchMatchedConcept] = []
    context_text: str = ""
    total_surface_forms: int = 0


class ConceptSearchResultItem(BaseModel):
    """A single scored image result."""
    image_id: int
    file_name: str
    file_hash: Optional[str] = None
    thumbnail_url: Optional[str] = None
    source_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    identity_score: Optional[float] = None
    context_score: Optional[float] = None
    composite_score: Optional[float] = None
    # Per-concept similarity scores: {concept_id: cos_sim}
    concept_scores: dict[int, float] = {}


class ConceptSearchResponse(BaseModel):
    """Full search result with decomposition and scored candidates."""
    query: str
    decomposition: DecomposeResponse
    candidates_total: int
    clip_available: bool
    results: list[ConceptSearchResultItem]


class ConceptIndexEntry(BaseModel):
    """Single concept in the concepts index (lab audit endpoint)."""
    concept_id: int
    canonical_name: str
    slug: str
    concept_type: Optional[str] = None
    aliases: list[str] = []
    has_prototype: bool = False
    prototype_source_count: Optional[int] = None
    prototype_updated_at: Optional[str] = None
    observation_count: int = 0


class ConceptIndexResponse(BaseModel):
    """Response for GET /concept-search/concepts-index."""
    total_concepts: int
    concepts: list[ConceptIndexEntry]


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
    matching_strategy: Optional[str] = None  # "last" | "all" | "frequency"


class SyncLabAnalyzeRequest(BaseModel):
    """Sync Lab: analyze-local and fetch-metadata step payload."""
    image_ids: list[int]
    collection_id: Optional[int] = None
    is_retry_run: bool = False


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


# ---------------------------------------------------------------------------
# Concept Review and Training Support (Phase 1)
# ---------------------------------------------------------------------------


class ConceptAttributeTermProfileCreateRequest(BaseModel):
    """Create or update a concept → authority_term attribute profile."""
    concept_id: int = Field(..., description="Target concept ID")
    attribute_term_id: int = Field(..., description="Authority term to track as an attribute")
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="How consistently this attribute appears (0-1)")
    invariance: bool = Field(False, description="Whether this attribute is invariant (always present)")
    attribute_mode: Literal["boolean", "countable", "exclusive"] = Field("boolean", description="How the attribute manifests")
    attribute_family: Optional[str] = Field(None, description="Family for exclusive attributes (e.g., 'hair_color')")
    cardinality_min: Optional[int] = Field(None, ge=0, description="Minimum count for countable attributes")
    cardinality_max: Optional[int] = Field(None, ge=0, description="Maximum count for countable attributes (NULL = unlimited)")


class ConceptAttributeTermProfileUpdateRequest(BaseModel):
    """Update an existing attribute term profile."""
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    invariance: Optional[bool] = None
    attribute_mode: Optional[Literal["boolean", "countable", "exclusive"]] = None
    attribute_family: Optional[str] = None
    cardinality_min: Optional[int] = Field(None, ge=0)
    cardinality_max: Optional[int] = Field(None, ge=0)


class ConceptAttributeTermProfileResponse(BaseModel):
    """Response model for concept → authority_term attribute profile."""
    concept_id: int
    attribute_term_id: int
    consistency_score: Optional[float] = None
    invariance: bool
    attribute_mode: str
    attribute_family: Optional[str] = None
    cardinality_min: Optional[int] = None
    cardinality_max: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    concept_name: Optional[str] = None  # Joined from concepts table
    attribute_term_name: Optional[str] = None  # Joined from authority_terms table
    authority_name: Optional[str] = None  # Joined from tag_authorities table

    class Config:
        from_attributes = True


class ConceptAttributeAuthorityWeightCreateRequest(BaseModel):
    """Create or update authority-specific weights for an attribute."""
    concept_id: int = Field(..., description="Target concept ID")
    attribute_term_id: int = Field(..., description="Authority term ID")
    authority_id: int = Field(..., description="Tag authority ID")
    base_weight: Optional[float] = Field(None, ge=0.0, le=1.0, description="Configured trust weight (NULL = use global default)")
    learned_weight: Optional[float] = Field(None, ge=0.0, le=1.0, description="Learned weight from review (NULL = use base_weight)")


class ConceptAttributeAuthorityWeightUpdateRequest(BaseModel):
    """Update authority-specific weights."""
    base_weight: Optional[float] = Field(None, ge=0.0, le=1.0)
    learned_weight: Optional[float] = Field(None, ge=0.0, le=1.0)


class ConceptAttributeAuthorityWeightResponse(BaseModel):
    """Response model for authority-specific attribute weights."""
    concept_id: int
    attribute_term_id: int
    authority_id: int
    base_weight: Optional[float] = None
    learned_weight: Optional[float] = None
    updated_at: Optional[str] = None
    concept_name: Optional[str] = None
    attribute_term_name: Optional[str] = None
    authority_name: Optional[str] = None
    effective_weight: Optional[float] = None  # learned_weight or base_weight

    class Config:
        from_attributes = True


class ConceptReviewEvidenceCreateRequest(BaseModel):
    """Create a new human review evidence record."""
    concept_id: int = Field(..., description="Target concept ID")
    image_id: int = Field(..., description="Image ID being reviewed")
    attribute_term_id: Optional[int] = Field(None, description="Authority term ID (optional for concept-level reviews)")
    evidence_kind: Literal["identity", "attribute", "context", "style", "anomaly"] = Field(..., description="Type of evidence")
    verdict: Literal["supports", "contradicts", "unknown"] = Field(..., description="Reviewer judgment")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Reviewer confidence (0-1)")
    notes: Optional[str] = Field(None, max_length=1000, description="Additional notes from reviewer")
    reviewer: Optional[str] = Field(None, max_length=100, description="Reviewer identifier")


class ConceptReviewEvidenceUpdateRequest(BaseModel):
    """Update an existing review evidence record."""
    verdict: Optional[Literal["supports", "contradicts", "unknown"]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    notes: Optional[str] = Field(None, max_length=1000)


class ConceptReviewEvidenceResponse(BaseModel):
    """Response model for review evidence records."""
    id: int
    concept_id: int
    image_id: int
    attribute_term_id: Optional[int] = None
    evidence_kind: str
    verdict: str
    confidence: Optional[float] = None
    notes: Optional[str] = None
    reviewer: Optional[str] = None
    created_at: Optional[str] = None
    # Optional joined fields
    concept_name: Optional[str] = None
    image_file_name: Optional[str] = None
    image_thumbnail_url: Optional[str] = None
    attribute_term_name: Optional[str] = None

    class Config:
        from_attributes = True


class ConceptObservationUpdateRequest(BaseModel):
    """Update observation weighting fields on an existing observation."""
    observation_weight: Optional[float] = Field(None, ge=0.0, le=1.0, description="Overall weight for training (0-1)")
    review_confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Reviewer confidence (0-1)")
    training_role: Optional[Literal["positive_exemplar", "hard_negative", "style_ref", "context_ref", "anomaly"]] = Field(None)
    concept_strength_weight: Optional[float] = Field(None, ge=0.0, le=1.0, description="How completely concept is supported by attributes (0-1)")


class ConceptObservationResponse(BaseModel):
    """Response model for observation with weighting fields."""
    id: int
    image_id: int
    concept_id: int
    authority_id: Optional[int] = None
    authority_term_id: Optional[int] = None
    tool_id: Optional[int] = None
    analysis_data_id: Optional[int] = None
    source_type: Optional[int] = None
    certainty_label: Optional[int] = None
    is_present: Optional[bool] = None
    is_curated: Optional[bool] = None
    confidence: Optional[float] = None
    # Weighting fields
    observation_weight: Optional[float] = None
    review_confidence: Optional[float] = None
    training_role: Optional[str] = None
    concept_strength_weight: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Optional joined fields
    concept_name: Optional[str] = None
    image_file_name: Optional[str] = None
    image_thumbnail_url: Optional[str] = None
    authority_name: Optional[str] = None

    class Config:
        from_attributes = True


class ConceptProfileWeightingSummary(BaseModel):
    """Aggregated weighting statistics for a concept's observations."""
    concept_id: int
    concept_name: Optional[str] = None
    total_observations: int = 0
    weighted_observations: int = 0
    positive_exemplars: int = 0
    hard_negatives: int = 0
    style_refs: int = 0
    context_refs: int = 0
    anomalies: int = 0
    avg_observation_weight: Optional[float] = None
    avg_concept_strength: Optional[float] = None
    avg_review_confidence: Optional[float] = None


class ConceptScoringConfig(BaseModel):
    """Scoring exponents and numeric stability settings."""
    alpha_identity: float = Field(0.55, ge=0.0, le=1.0)
    alpha_attribute: float = Field(0.25, ge=0.0, le=1.0)
    alpha_context: float = Field(0.15, ge=0.0, le=1.0)
    alpha_style: float = Field(0.05, ge=0.0, le=1.0)
    epsilon: float = Field(0.05, ge=0.000001, le=0.5)


class ConceptScoredImage(BaseModel):
    """Per-image scoring breakdown for a concept."""
    image_id: int
    image_file_name: Optional[str] = None
    image_file_hash: Optional[str] = None
    image_thumbnail_url: Optional[str] = None
    image_style_concept_id: Optional[int] = None
    image_style_concept_name: Optional[str] = None
    image_style_source: Optional[str] = None
    image_style_confidence: Optional[float] = None
    observation_count: int = 0
    identity_score: float
    attribute_score: float
    context_score: float
    style_score: float
    anomaly_penalty: float
    final_score: float


class ConceptScoringResponse(BaseModel):
    """Scored images and component breakdown for one concept."""
    concept_id: int
    concept_name: Optional[str] = None
    total_images: int
    scoring: ConceptScoringConfig
    results: list[ConceptScoredImage]


class ConceptReviewSessionCreateRequest(BaseModel):
    """Create a new concept review session."""
    notes: Optional[str] = Field(None, max_length=2000)


class ConceptReviewSessionUpdateRequest(BaseModel):
    """Update review session metadata/status."""
    status: Optional[Literal["open", "completed", "abandoned"]] = None
    notes: Optional[str] = Field(None, max_length=2000)


class ConceptReviewSessionResponse(BaseModel):
    """Response model for review sessions."""
    id: int
    concept_id: int
    concept_name: Optional[str] = None
    status: str
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None
    assessment_count: int = 0

    class Config:
        from_attributes = True


class ConceptReviewAssessmentUpsertRequest(BaseModel):
    """Upsert one structured image assessment in a review session."""
    image_id: int = Field(..., description="Image being assessed")
    predominance_rating: Optional[int] = Field(None, ge=1, le=5)
    quality_rating: Optional[int] = Field(None, ge=1, le=5)
    accuracy_rating: Optional[int] = Field(None, ge=1, le=5)
    attribute_support_rating: Optional[int] = Field(None, ge=1, le=5)

    context_incongruent: bool = False
    context_anachronistic: bool = False
    context_anatopismic: bool = False
    context_nonsensical: bool = False
    context_anomalous_form: bool = False

    anomaly_present: bool = False
    anomaly_kind: Optional[str] = Field(None, max_length=100)
    anomaly_degree: Optional[int] = Field(None, ge=1, le=4)

    deviation_present: bool = False
    deviation_body_variant: bool = False
    deviation_exaggerated: bool = False
    deviation_extra_feature: bool = False
    deviation_fusion: bool = False
    deviation_kind: Optional[str] = Field(None, max_length=100)
    deviation_degree: Optional[int] = Field(None, ge=1, le=4)

    image_style_concept_id: Optional[int] = None
    image_style_source: Optional[Literal["guessed", "review", "imported"]] = None
    image_style_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # { attribute_concept_id(str): "present" | "absent" | "not_visible" }
    attribute_checks: Optional[dict[str, str]] = None
    notes: Optional[str] = Field(None, max_length=2000)


class ConceptReviewAssessmentResponse(BaseModel):
    """Response model for one image assessment."""
    id: int
    session_id: int
    concept_id: int
    concept_name: Optional[str] = None
    image_id: int
    image_file_name: Optional[str] = None
    image_thumbnail_url: Optional[str] = None

    predominance_rating: Optional[int] = None
    quality_rating: Optional[int] = None
    accuracy_rating: Optional[int] = None
    attribute_support_rating: Optional[int] = None

    context_incongruent: bool = False
    context_anachronistic: bool = False
    context_anatopismic: bool = False
    context_nonsensical: bool = False
    context_anomalous_form: bool = False

    anomaly_present: bool = False
    anomaly_kind: Optional[str] = None
    anomaly_degree: Optional[int] = None

    deviation_present: bool = False
    deviation_body_variant: bool = False
    deviation_exaggerated: bool = False
    deviation_extra_feature: bool = False
    deviation_fusion: bool = False
    deviation_kind: Optional[str] = None
    deviation_degree: Optional[int] = None

    image_style_concept_id: Optional[int] = None
    image_style_concept_name: Optional[str] = None
    image_style_source: Optional[str] = None
    image_style_confidence: Optional[float] = None

    attribute_checks: Optional[dict[str, str]] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class BulkReviewEvidenceRequest(BaseModel):
    """Bulk create review evidence records."""
    evidence_records: list[ConceptReviewEvidenceCreateRequest] = Field(..., min_length=1, max_length=100, description="Evidence records to create")


class BulkObservationWeightUpdateRequest(BaseModel):
    """Bulk update observation weights."""
    observation_updates: list[dict] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Updates: [{id, observation_weight, review_confidence, training_role, concept_strength_weight}]"
    )


# ---------------------------------------------------------------------------
# CivitAI Search Lab — image preference tracking
# ---------------------------------------------------------------------------

class CivitaiImageRatingRequest(BaseModel):
    """Rate a CivitAI image seen in the search lab.

    ``rating`` is one of ``keep``, ``discard``, or ``skip``.
    The endpoint upserts the image record, records a search-image link, and
    increments artist preference counters when applicable.
    """
    civitai_image_id: int
    rating: Literal["keep", "discard", "skip"]
    # Optional metadata so we can store a useful image record
    post_id: Optional[int] = None
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    file_name: Optional[str] = None
    blurhash: Optional[str] = None
    uuid: Optional[str] = None
    file_size: Optional[int] = None
    image_url: Optional[str] = None
    tags: Optional[list[str]] = None
    generation_prompt: Optional[str] = None
    generation_models: Optional[list[dict]] = None
    reactions: Optional[int] = None
    likes: Optional[int] = None
    position: Optional[int] = None
    search_id: Optional[int] = None


class CivitaiSearchRecordRequest(BaseModel):
    """Record that a search was performed."""
    search_text: Optional[str] = None
    search_terms: Optional[dict] = None
    search_rating: Optional[str] = None
    result_count: int = 0


class CivitaiImageRatingResponse(BaseModel):
    status: str
    rating: str
    is_excluded: bool


# ---------------------------------------------------------------------------
# CivitAI Search Lab — artist preference summary & blocking
# ---------------------------------------------------------------------------

class CivitaiArtistSummaryItem(BaseModel):
    """One row in the artist score summary."""
    artist_id: Optional[int] = None
    artist_name: str
    keeps: int = 0
    discards: int = 0
    score: int = 0
    is_blocked: bool = False


class CivitaiArtistBlockRequest(BaseModel):
    """Toggle blocked status for one artist."""
    artist_name: str
    artist_id: Optional[int] = None
    is_blocked: bool
