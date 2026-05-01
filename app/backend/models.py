# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/taxonomy-import.md
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
import enum

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    DateTime,
    JSON,
    Enum,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import relationship
from database import Base


# ---------------------------------------------------------------------------
# IntEnum types for ImageConceptObservation compact storage
# ---------------------------------------------------------------------------

class ObservationSource(enum.IntEnum):
    """Origin of a tag observation on an image."""
    IMPORT = 1       # tag imported during scan/ingestion/backfill
    # Future: RESCAN = 2, USER = 3, ANALYSIS = 4


class ObservationCertainty(enum.IntEnum):
    """How confident we are that this observation is correct."""
    UNSPECIFIED = 0
    LIKELY = 1       # default — tag came from external source
    CONFIRMED = 2    # user verified
    # Future: UNCERTAIN = 3

# 1. Define the values in a single, constant tuple. This is the source of truth.
COLLECTION_TYPE_VALUES = ("public", "paid", "private", "user_created")

# 2. Dynamically create the Enum class from the tuple.
#    This is the clean and correct way.
CollectionType = Enum("CollectionType", " ".join(COLLECTION_TYPE_VALUES), type=str)


class ImageModel(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(Text, unique=True, index=True, nullable=False)
    file_name = Column(String, nullable=False)
    original_file_name = Column(String, nullable=True)
    file_hash = Column(String, index=True, nullable=False)  # Not unique — CivitAI duplicate assets can share the same SHA256
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    mimetype = Column(String, nullable=True)
    date_created = Column(DateTime)
    date_modified = Column(DateTime)
    image_status = Column(String, nullable=False, default="active")
    status_reason = Column(String, nullable=True)
    replaced_by_image_id = Column(Integer, ForeignKey("images.id"), nullable=True)
    variant_group_key = Column(String, nullable=True, index=True)
    variant_sort_index = Column(Integer, nullable=True)
    variant_role = Column(String, nullable=True)

    # Attribution & Licensing
    source_url = Column(Text)
    source_site = Column(String)
    civitai_image_id = Column(Integer, nullable=True, index=True)
    civitai_uuid = Column(String, nullable=True, index=True)
    civitai_hash = Column(String, nullable=True, index=True)
    blurhash = Column(String, nullable=True)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=True)

    # 3. Use the same tuple to define the database column.
    collection_type = Column(Enum(*COLLECTION_TYPE_VALUES, name="collectiontype"))

    # Metadata
    exif_data = Column(JSON)
    json_metadata = Column(JSON)

    # User-defined NSFW overrides (take priority over source-derived ratings)
    user_nsfw_rating = Column(String, nullable=True)       # pg | pg13 | r | x | xxx
    user_nsfw_safety_class = Column(String, nullable=True)  # safe | mature | explicit

    # User-defined tags (persisted in both DB column and sidecar JSON)
    user_tags = Column(JSON, nullable=True)

    # User-defined negative tags (things the user explicitly wants excluded)
    user_negative_tags = Column(JSON, nullable=True)

    # Promoted metadata columns (authoritative; backfilled from json_metadata/sidecar)
    generation_software = Column(String, nullable=True)
    civitai_nsfw_level = Column(Integer, nullable=True)  # 1=PG, 2=PG13, 4=R, 8=X, 16=XXX

    # A1111 / ComfyUI feature flags (detected from EXIF at import time)
    has_a1111_metadata = Column(Boolean, nullable=False, default=False)
    a1111_hires = Column(Boolean, nullable=False, default=False)
    a1111_regional_prompter = Column(Boolean, nullable=False, default=False)
    a1111_adetailer = Column(Boolean, nullable=False, default=False)
    has_comfyui_metadata = Column(Boolean, nullable=False, default=False)
    has_generation_prompt = Column(Boolean, nullable=False, default=False)

    # Relationships
    license = relationship("License", back_populates="images")
    analysis_data = relationship("AnalysisData", back_populates="images")
    tags = relationship("Tag", secondary="image_tags", back_populates="images")
    datasets = relationship(
        "Dataset", secondary="dataset_images", back_populates="images"
    )
    collections = relationship(
        "CollectionModel", secondary="image_collections", back_populates="images"
    )
    artist = relationship("Artist", back_populates="images")
    variant_groups = relationship(
        "VariantGroup", secondary="image_variant_groups", back_populates="images"
    )
    generation_processes = relationship(
        "GenerationProcess",
        back_populates="image",
        cascade="all, delete-orphan",
        order_by="GenerationProcess.id",
    )

    def to_dict(self) -> dict:
        """
        Returns a dictionary representation of the image, suitable for an API response.
        """
        # Safely access the artist information
        artist_info = None
        if self.artist:
            artist_info = {
                "id": self.artist.id,
                "name": self.artist.name,
                "nickname": self.artist.nickname,
                "civitai_user_id": self.artist.civitai_user_id,
                "civitai_user_deleted": self.artist.civitai_user_deleted,
                "civitai_user_original_name": self.artist.civitai_user_original_name,
            }

        # Safely access the license information
        license_info = None
        if self.license:
            license_info = {
                "id": self.license.id,
                "short_name": self.license.short_name,
                "name": self.license.name,
            }

        exif_data = None
        if self.exif_data is not None:
            exif_data = self.exif_data

        json_metadata = None
        if self.json_metadata is not None:
            json_metadata = self.json_metadata

        return {
            "id": self.id,
            "file_name": self.file_name,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "width": self.width,
            "height": self.height,
            "mimetype": self.mimetype,
            "date_created": (
                self.date_created.isoformat() if self.date_created is not None else None
            ),
            "date_modified": (
                self.date_modified.isoformat()
                if self.date_modified is not None
                else None
            ),
            "image_status": self.image_status or "active",
            "status_reason": self.status_reason,
            "replaced_by_image_id": self.replaced_by_image_id,
            "variant_group_key": self.variant_group_key,
            "variant_sort_index": self.variant_sort_index,
            "variant_role": self.variant_role,
            "source_url": self.source_url,
            "source_site": self.source_site,
            "civitai_uuid": self.civitai_uuid,
            "civitai_hash": self.civitai_hash,
            "collection_type": self.collection_type,
            "artist": artist_info,
            "license": license_info,
            "exif_data": exif_data,
            "json_metadata": json_metadata,
            "user_nsfw_rating": self.user_nsfw_rating,
            "user_nsfw_safety_class": self.user_nsfw_safety_class,
            "user_tags": self.user_tags,
        }


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    # Relationships
    images = relationship("ImageModel", secondary="image_tags", back_populates="tags")


class ImageTag(Base):
    __tablename__ = "image_tags"

    image_id = Column(Integer, ForeignKey("images.id"), primary_key=True)
    tag_id = Column(Integer, ForeignKey("tags.id"), primary_key=True)


class TagAuthority(Base):
    __tablename__ = "tag_authorities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    is_external = Column(Boolean, nullable=False, default=True)
    base_url = Column(String)

    authority_terms = relationship("AuthorityTerm", back_populates="authority")
    aliases = relationship("ConceptAlias", back_populates="authority")
    observations = relationship("ImageConceptObservation", back_populates="authority")


class Concept(Base):
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True, index=True)
    canonical_name = Column(String, unique=True, index=True, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    status = Column(String, nullable=False, default="active")
    parent_concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    parent = relationship("Concept", remote_side=[id], back_populates="children")
    children = relationship("Concept", back_populates="parent")
    aliases = relationship("ConceptAlias", back_populates="concept")
    authority_terms = relationship("AuthorityTerm", back_populates="concept")
    observations = relationship("ImageConceptObservation", back_populates="concept")
    group_memberships = relationship("ConceptGroupMembership", back_populates="concept")


class ConceptAlias(Base):
    __tablename__ = "concept_aliases"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    alias = Column(String, nullable=False)
    normalized_alias = Column(String, nullable=False, index=True)
    alias_type = Column(String, nullable=False, default="synonym")
    is_preferred = Column(Boolean, nullable=False, default=False)
    authority_id = Column(Integer, ForeignKey("tag_authorities.id"), nullable=True)
    external_tag_id = Column(Integer, nullable=True)
    notes = Column(Text)

    concept = relationship("Concept", back_populates="aliases")
    authority = relationship("TagAuthority", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("concept_id", "normalized_alias", name="uq_concept_alias_normalized"),
    )


class AuthorityTerm(Base):
    __tablename__ = "authority_terms"

    id = Column(Integer, primary_key=True, index=True)
    authority_id = Column(Integer, ForeignKey("tag_authorities.id"), nullable=False, index=True)
    external_tag_id = Column(Integer, nullable=True)
    external_name = Column(String, nullable=False)
    normalized_external_name = Column(String, nullable=False, index=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=True, index=True)
    metadata_json = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)
    last_seen_at = Column(DateTime)

    authority = relationship("TagAuthority", back_populates="authority_terms")
    concept = relationship("Concept", back_populates="authority_terms")
    observations = relationship("ImageConceptObservation", back_populates="authority_term")

    __table_args__ = (
        UniqueConstraint("authority_id", "external_tag_id", name="uq_authority_external_id"),
        UniqueConstraint("authority_id", "normalized_external_name", name="uq_authority_external_name"),
    )


class ConceptGroup(Base):
    __tablename__ = "concept_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    status = Column(String, nullable=False, default="active")
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    memberships = relationship("ConceptGroupMembership", back_populates="group")


class ConceptGroupMembership(Base):
    __tablename__ = "concept_group_memberships"

    group_id = Column(Integer, ForeignKey("concept_groups.id"), primary_key=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), primary_key=True)
    role = Column(String, nullable=False, default="member")
    confidence = Column(Float, nullable=True)

    group = relationship("ConceptGroup", back_populates="memberships")
    concept = relationship("Concept", back_populates="group_memberships")


class ImageConceptObservation(Base):
    __tablename__ = "image_concept_observations"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False, index=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=True, index=True)
    authority_id = Column(Integer, ForeignKey("tag_authorities.id"), nullable=True, index=True)
    authority_term_id = Column(Integer, ForeignKey("authority_terms.id"), nullable=True, index=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=True, index=True)
    analysis_data_id = Column(Integer, ForeignKey("analysis_data.id"), nullable=True, index=True)
    source_type = Column(Integer, nullable=False, default=ObservationSource.IMPORT)
    certainty_label = Column(Integer, nullable=False, default=ObservationCertainty.LIKELY)
    is_present = Column(Boolean, nullable=False, default=True)
    is_curated = Column(Boolean, nullable=False, default=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    image = relationship("ImageModel")
    concept = relationship("Concept", back_populates="observations")
    authority = relationship("TagAuthority", back_populates="observations")
    authority_term = relationship("AuthorityTerm", back_populates="observations")
    tool = relationship("Tool")
    analysis_data = relationship("AnalysisData")

    __table_args__ = (
        UniqueConstraint(
            "image_id", "concept_id", "authority_id",
            name="uq_obs_image_concept_authority",
        ),
        # Partial unique: one observation per (image, term) — null term allowed
        Index(
            "uq_obs_image_authority_term",
            "image_id", "authority_term_id",
            unique=True,
            sqlite_where=text("authority_term_id IS NOT NULL"),
        ),
        # Covering: term → image (tag filtering/counting without table lookup)
        Index("ix_obs_term_image", "authority_term_id", "image_id"),
        # Covering: concept+authority → image (concept-branch filtering)
        Index("ix_obs_concept_auth_image", "concept_id", "authority_id", "image_id"),
        # Covering: authority+term → image (gallery tag counting by source)
        Index("ix_obs_authority_term_image", "authority_id", "authority_term_id", "image_id"),
    )


class Tool(Base):
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    version = Column(String)

    # Relationships
    analysis_data = relationship("AnalysisData", back_populates="tool")


class AnalysisData(Base):
    __tablename__ = "analysis_data"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False)
    dataset_id = Column(
        Integer, ForeignKey("datasets.id"), nullable=True
    )  # Nullable if user-generated
    data = Column(Text, nullable=False)
    is_curated = Column(Boolean, default=False)
    created_at = Column(DateTime, nullable=False)

    # Relationships
    images = relationship("ImageModel", back_populates="analysis_data")
    tool = relationship("Tool", back_populates="analysis_data")
    dataset = relationship("Dataset", back_populates="analysis_data")


class License(Base):
    __tablename__ = "licenses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    short_name = Column(String, unique=True, index=True, nullable=False)
    url = Column(String)
    allows_commercial_use = Column(Boolean, default=False)
    requires_attribution = Column(Boolean, default=True)

    # Relationships
    images = relationship("ImageModel", back_populates="license")


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    creator_user_id = Column(
        String
    )  # Could be a simple string or a foreign key to a users table later
    version = Column(String)
    export_date = Column(DateTime)

    # Relationships
    analysis_data = relationship("AnalysisData", back_populates="dataset")
    images = relationship(
        "ImageModel", secondary="dataset_images", back_populates="datasets"
    )


class DatasetImage(Base):
    __tablename__ = "dataset_images"

    dataset_id = Column(Integer, ForeignKey("datasets.id"), primary_key=True)
    image_id = Column(Integer, ForeignKey("images.id"), primary_key=True)


class CollectionModel(Base):
    __tablename__ = "collections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    source = Column(String, nullable=False, default="user")
    civitai_collection_id = Column(Integer, nullable=True)
    civitai_head_fingerprint = Column(Text, nullable=True)
    civitai_head_item_count = Column(Integer, nullable=True)
    civitai_head_has_more = Column(Boolean, nullable=True)
    civitai_last_full_item_count = Column(Integer, nullable=True)
    civitai_last_synced_at = Column(DateTime, nullable=True)
    civitai_last_full_scan_at = Column(DateTime, nullable=True)

    images = relationship(
        "ImageModel", secondary="image_collections", back_populates="collections"
    )


class ImageCollectionMembership(Base):
    __tablename__ = "image_collections"

    image_id = Column(Integer, ForeignKey("images.id"), primary_key=True)
    collection_id = Column(Integer, ForeignKey("collections.id"), primary_key=True)


class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    nickname = Column(String)
    deviantart_url = Column(String)
    civitai_url = Column(String)
    pixiv_url = Column(String)
    # CivitAI user identity — survives account deletion
    civitai_user_id = Column(Integer, unique=True, nullable=True, index=True)
    civitai_user_deleted = Column(Boolean, nullable=True, default=False)
    civitai_user_original_name = Column(String, nullable=True)

    # Relationship back to images
    images = relationship("ImageModel", back_populates="artist")


class GenerationProcess(Base):
    __tablename__ = "generation_processes"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False, index=True)
    source_type = Column(String, nullable=False, default="unknown", index=True)
    source_label = Column(String, nullable=True)
    is_preferred = Column(Boolean, nullable=False, default=False, index=True)
    is_user_supplied = Column(Boolean, nullable=False, default=False)
    platform_name = Column(String, nullable=True, index=True)
    platform_version = Column(String, nullable=True)
    method_family = Column(String, nullable=True, index=True)
    method_variant = Column(String, nullable=True)
    stage_count = Column(Integer, nullable=False, default=0)
    has_embedded_sources = Column(Boolean, nullable=False, default=False)
    has_refiners = Column(Boolean, nullable=False, default=False)
    has_video_generation = Column(Boolean, nullable=False, default=False)
    raw_payload_json = Column(JSON)
    workflow_json = Column(JSON)
    compatibility_json = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    image = relationship("ImageModel", back_populates="generation_processes")
    stages = relationship(
        "GenerationStage",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationStage.stage_index",
    )
    prompts = relationship(
        "GenerationPrompt",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationPrompt.id",
    )
    resources = relationship(
        "GenerationResource",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationResource.id",
    )
    source_assets = relationship(
        "GenerationSourceAsset",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationSourceAsset.id",
    )
    field_values = relationship(
        "GenerationFieldValue",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationFieldValue.id",
    )
    provenance_records = relationship(
        "GenerationProvenanceRecord",
        back_populates="process",
        cascade="all, delete-orphan",
        order_by="GenerationProvenanceRecord.id",
    )

    __table_args__ = (
        Index("ix_generation_process_image_preferred", "image_id", "is_preferred"),
    )


class GenerationStage(Base):
    __tablename__ = "generation_stages"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_index = Column(Integer, nullable=False)
    stage_role = Column(String, nullable=False, default="base", index=True)
    stage_label = Column(String, nullable=True)
    method_family = Column(String, nullable=True, index=True)
    method_variant = Column(String, nullable=True)
    input_image_id = Column(Integer, ForeignKey("images.id"), nullable=True, index=True)
    input_asset_ref = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    base_width = Column(Integer, nullable=True)
    base_height = Column(Integer, nullable=True)
    sampler_name = Column(String, nullable=True, index=True)
    scheduler_name = Column(String, nullable=True, index=True)
    steps = Column(Integer, nullable=True)
    cfg_scale = Column(Float, nullable=True)
    seed = Column(String, nullable=True, index=True)
    clip_skip = Column(Integer, nullable=True)
    strength = Column(Float, nullable=True)
    denoise_strength = Column(Float, nullable=True)
    guidance_notes = Column(Text, nullable=True)
    compatibility_json = Column(JSON)
    raw_stage_json = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="stages")
    input_image = relationship("ImageModel")
    prompts = relationship(
        "GenerationPrompt",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="GenerationPrompt.id",
    )
    resources = relationship(
        "GenerationResource",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="GenerationResource.id",
    )
    source_assets = relationship(
        "GenerationSourceAsset",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="GenerationSourceAsset.id",
    )
    field_values = relationship(
        "GenerationFieldValue",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="GenerationFieldValue.id",
    )
    provenance_records = relationship(
        "GenerationProvenanceRecord",
        back_populates="stage",
        cascade="all, delete-orphan",
        order_by="GenerationProvenanceRecord.id",
    )

    __table_args__ = (
        UniqueConstraint("process_id", "stage_index", name="uq_generation_stage_index"),
        Index("ix_generation_stage_process_role", "process_id", "stage_role"),
    )


class GenerationPrompt(Base):
    __tablename__ = "generation_prompts"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_id = Column(
        Integer, ForeignKey("generation_stages.id"), nullable=True, index=True
    )
    prompt_role = Column(String, nullable=False, default="positive", index=True)
    prompt_text = Column(Text, nullable=False)
    prompt_style = Column(String, nullable=True, index=True)
    source_type = Column(String, nullable=True, index=True)
    token_count = Column(Integer, nullable=True)
    parsed_concepts_json = Column(JSON)
    parsed_phrases_json = Column(JSON)
    raw_prompt_json = Column(JSON)
    created_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="prompts")
    stage = relationship("GenerationStage", back_populates="prompts")

    __table_args__ = (
        Index("ix_generation_prompt_process_role", "process_id", "prompt_role"),
        Index("ix_generation_prompt_stage_role", "stage_id", "prompt_role"),
    )


class GenerationResource(Base):
    __tablename__ = "generation_resources"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_id = Column(
        Integer, ForeignKey("generation_stages.id"), nullable=True, index=True
    )
    resource_role = Column(String, nullable=False, default="reference", index=True)
    resource_type = Column(String, nullable=False, default="other", index=True)
    display_name = Column(String, nullable=True)
    normalized_name = Column(String, nullable=True, index=True)
    version_name = Column(String, nullable=True)
    base_model_name = Column(String, nullable=True, index=True)
    strength_model = Column(Float, nullable=True)
    strength_clip = Column(Float, nullable=True)
    strength_text_encoder = Column(Float, nullable=True)
    civitai_model_id = Column(Integer, nullable=True, index=True)
    civitai_model_version_id = Column(Integer, nullable=True, index=True)
    source_identifier = Column(String, nullable=True, index=True)
    is_primary = Column(Boolean, nullable=False, default=False, index=True)
    raw_resource_json = Column(JSON)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="resources")
    stage = relationship("GenerationStage", back_populates="resources")

    __table_args__ = (
        Index(
            "ix_generation_resource_process_type_role",
            "process_id",
            "resource_type",
            "resource_role",
        ),
    )


class GenerationSourceAsset(Base):
    __tablename__ = "generation_source_assets"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_id = Column(
        Integer, ForeignKey("generation_stages.id"), nullable=True, index=True
    )
    asset_role = Column(String, nullable=False, default="input", index=True)
    source_image_id = Column(Integer, ForeignKey("images.id"), nullable=True, index=True)
    source_url = Column(Text, nullable=True)
    encoded_payload_ref = Column(Text, nullable=True)
    mime_type = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    metadata_json = Column(JSON)
    created_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="source_assets")
    stage = relationship("GenerationStage", back_populates="source_assets")
    source_image = relationship("ImageModel")

    __table_args__ = (
        Index("ix_generation_source_asset_process_role", "process_id", "asset_role"),
    )


class GenerationFieldValue(Base):
    __tablename__ = "generation_field_values"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_id = Column(
        Integer, ForeignKey("generation_stages.id"), nullable=True, index=True
    )
    field_name = Column(String, nullable=False, index=True)
    field_value_text = Column(Text, nullable=True)
    field_value_number = Column(Float, nullable=True)
    field_value_json = Column(JSON)
    value_type = Column(String, nullable=False, default="text")
    source_type = Column(String, nullable=True, index=True)
    is_preferred = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="field_values")
    stage = relationship("GenerationStage", back_populates="field_values")

    __table_args__ = (
        Index("ix_generation_field_process_name", "process_id", "field_name"),
        Index("ix_generation_field_stage_name", "stage_id", "field_name"),
    )


class GenerationProvenanceRecord(Base):
    __tablename__ = "generation_provenance_records"

    id = Column(Integer, primary_key=True, index=True)
    process_id = Column(
        Integer, ForeignKey("generation_processes.id"), nullable=False, index=True
    )
    stage_id = Column(
        Integer, ForeignKey("generation_stages.id"), nullable=True, index=True
    )
    scope_type = Column(String, nullable=False, default="process", index=True)
    scope_id = Column(Integer, nullable=True)
    source_type = Column(String, nullable=False, default="unknown", index=True)
    source_label = Column(String, nullable=True)
    confidence_label = Column(String, nullable=True, index=True)
    is_preferred = Column(Boolean, nullable=False, default=False, index=True)
    raw_fragment_json = Column(JSON)
    notes = Column(Text)
    created_at = Column(DateTime)

    process = relationship("GenerationProcess", back_populates="provenance_records")
    stage = relationship("GenerationStage", back_populates="provenance_records")

    __table_args__ = (
        Index(
            "ix_generation_provenance_process_scope",
            "process_id",
            "scope_type",
            "source_type",
        ),
    )


class GenerationTemplate(Base):
    __tablename__ = "generation_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    workflow_json = Column(JSON, nullable=False)
    mappings_json = Column(JSON, nullable=False)
    default_tokens_json = Column(JSON, nullable=False)
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class GenerationMatchAttempt(Base):
    __tablename__ = "generation_match_attempts"

    id = Column(Integer, primary_key=True, index=True)
    reference_file_hash = Column(String, nullable=False, index=True)
    comfy_prompt_id = Column(String, nullable=True, index=True)
    attempt_index = Column(Integer, nullable=False, default=1)
    tweak_label = Column(String, nullable=True)
    tweak_parameters_json = Column(JSON)
    effective_parameters_json = Column(JSON)
    generated_outputs_json = Column(JSON)
    best_output_filename = Column(String, nullable=True)
    best_phash_distance = Column(Integer, nullable=True)
    best_similarity = Column(Float, nullable=True, index=True)
    threshold_used = Column(Float, nullable=False, default=0.95)
    is_matched = Column(Boolean, nullable=False, default=False, index=True)
    is_fundamental_generation_issue = Column(Boolean, nullable=False, default=False, index=True)
    notes = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime)

    __table_args__ = (
        Index("ix_generation_match_attempt_ref_created", "reference_file_hash", "created_at"),
    )


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version_num = Column(String, primary_key=True)


class VariantGroup(Base):
    """A named group of related images (hash duplicates, img2img pairs, seed families, etc.).

    Images can belong to multiple groups via the image_variant_groups junction table.
    Each group has a unique group_key for deterministic identity and a group_type
    describing how the relationship was established.
    """
    __tablename__ = "variant_groups"

    id = Column(Integer, primary_key=True, index=True)
    group_key = Column(String, unique=True, index=True, nullable=False)
    group_type = Column(String, index=True, nullable=False)  # "hash_duplicate" | "civitai_multi_resource" | "img2img" | "seed_family" | "manual"
    group_label = Column(String, nullable=True)
    cover_image_id = Column(Integer, ForeignKey("images.id"), nullable=True)
    cover_preference = Column(String, nullable=False, default="sort_order")  # "sort_order" | "video_first" | "base_first" | "manual"
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relationships
    images = relationship(
        "ImageModel", secondary="image_variant_groups", back_populates="variant_groups"
    )


class ImageVariantGroupMembership(Base):
    """Junction table linking images to variant groups with role and source metadata."""
    __tablename__ = "image_variant_groups"

    image_id = Column(Integer, ForeignKey("images.id"), primary_key=True)
    group_id = Column(Integer, ForeignKey("variant_groups.id"), primary_key=True)
    role_in_group = Column(String, nullable=False, default="member")  # "primary" | "base" | "result" | "seed_variant" | "member"
    sort_index = Column(Integer, nullable=False, default=0)
    source = Column(String, nullable=False, default="manual")  # "auto_hash" | "auto_seed" | "auto_img2img" | "auto_civitai" | "manual"


# ---------------------------------------------------------------------------
# CivitAI User Table
# ---------------------------------------------------------------------------


class CivitaiUser(Base):
    """CivitAI user/creator accounts.

    Normalized from the denormalized civitai_user_id/username/deleted columns on
    CivitaiModel.  Populated during model sync (upsert_model_list_item / upsert_model_detail).
    """
    __tablename__ = "civitai_users"

    civitai_user_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    deleted_at = Column(DateTime, nullable=True)  # When the CivitAI account was deleted
    original_name = Column(String, nullable=True)  # Name at time of deletion
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, nullable=True)  # When we last saw this user

    # Relationships
    models = relationship(
        "CivitaiModel",
        back_populates="creator",
        foreign_keys="[CivitaiModel.creator_id]",
    )


# ---------------------------------------------------------------------------
# CivitAI Model Catalog Tables
# ---------------------------------------------------------------------------

class CivitaiModel(Base):
    """Master table for CivitAI models (Checkpoints, LoRAs, etc.)."""
    __tablename__ = "civitai_models"

    civitai_model_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    type = Column(String, nullable=False, index=True)  # "Checkpoint", "LORA", etc.
    description = Column(Text, nullable=True)  # Phase 2: model-level fallback; version.description is authoritative
    checkpoint_type = Column(String, nullable=True)  # "Merge", "Trained", null for non-checkpoints
    nsfw = Column(Boolean, nullable=False, default=False)
    nsfw_level = Column(Integer, nullable=False, default=1)  # Phase 2: model-level fallback; version.nsfw_level is authoritative
    sfw_only = Column(Boolean, nullable=False, default=False)
    poi = Column(Boolean, nullable=False, default=False)
    minor = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False, default="Published")  # Phase 2: model-level fallback; version.status is authoritative
    availability = Column(String, nullable=False, default="Public")
    upload_type = Column(String, nullable=True)  # Phase 2: model-level fallback; version.upload_type is authoritative
    locked = Column(Boolean, nullable=False, default=False)
    allow_no_credit = Column(Boolean, nullable=True)
    allow_commercial_use = Column(JSON, nullable=True)  # Array of allowed commercial types
    allow_derivatives = Column(Boolean, nullable=True)
    allow_different_license = Column(Boolean, nullable=True)
    civitai_user_id = Column(Integer, nullable=False, index=True)  # Legacy: kept for backward compat; prefer creator FK
    civitai_username = Column(String, nullable=False)  # Legacy: kept for backward compat; prefer creator FK
    civitai_user_deleted = Column(Boolean, nullable=False, default=False)  # Legacy: kept for backward compat; prefer creator FK
    creator_id = Column(Integer, ForeignKey("civitai_users.civitai_user_id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=True)
    published_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    last_version_at = Column(DateTime, nullable=True)
    early_access_deadline = Column(DateTime, nullable=True)
    latest_version_id = Column(Integer, ForeignKey("civitai_model_versions.civitai_version_id"), nullable=True)
    scraped_at = Column(DateTime, nullable=True)  # When we last did getById
    list_scraped_at = Column(DateTime, nullable=True)  # When we last saw this in getAll

    # Relationships
    versions = relationship(
        "CivitaiModelVersion",
        back_populates="model",
        cascade="all, delete-orphan",
        foreign_keys="[CivitaiModelVersion.civitai_model_id]",
    )
    tags = relationship("CivitaiModelTag", back_populates="model", cascade="all, delete-orphan")
    rank = relationship("CivitaiModelRank", uselist=False, back_populates="model", cascade="all, delete-orphan")
    creator = relationship("CivitaiUser", back_populates="models", foreign_keys=[creator_id])


class CivitaiModelVersion(Base):
    """Versions of a CivitAI model.

    Version-level fields (description, nsfw_level, status, upload_type) are
    authoritative.  When null, fall back to the parent CivitaiModel's value.
    """
    __tablename__ = "civitai_model_versions"

    civitai_version_id = Column(Integer, primary_key=True, index=True)
    civitai_model_id = Column(Integer, ForeignKey("civitai_models.civitai_model_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)  # Authoritative; fallback to model.description
    base_model = Column(String, nullable=False, index=True)
    base_model_type = Column(String, nullable=True)
    base_model_id = Column(Integer, ForeignKey("civitai_base_models.id"), nullable=True, index=True)
    nsfw_level = Column(Integer, nullable=False, default=1)  # Authoritative; fallback to model.nsfw_level
    status = Column(String, nullable=False, default="Published")  # Authoritative; fallback to model.status
    availability = Column(String, nullable=False, default="Public")
    upload_type = Column(String, nullable=True)  # Authoritative; fallback to model.upload_type
    clip_skip = Column(Integer, nullable=True)
    steps = Column(Integer, nullable=True)  # Training steps
    epochs = Column(Integer, nullable=True)  # Training epochs
    trained_words = Column(JSON, nullable=True)  # Trigger phrases as array
    training_status = Column(String, nullable=True)
    require_auth = Column(Boolean, nullable=False, default=False)
    usage_control = Column(String, nullable=True)
    early_access_config = Column(JSON, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, nullable=True)

    # Relationships
    model = relationship("CivitaiModel", back_populates="versions", foreign_keys=[civitai_model_id])
    files = relationship("CivitaiModelVersionFile", back_populates="version", cascade="all, delete-orphan")
    rank = relationship("CivitaiModelRank", uselist=False, back_populates="version", cascade="all, delete-orphan")
    base_model_ref = relationship("CivitaiBaseModel", foreign_keys=[base_model_id])


class CivitaiModelVersionFile(Base):
    """Files associated with a model version."""
    __tablename__ = "civitai_model_version_files"

    civitai_file_id = Column(Integer, primary_key=True, index=True)
    civitai_version_id = Column(Integer, ForeignKey("civitai_model_versions.civitai_version_id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "Model", "VAE", etc.
    size_kb = Column(Float, nullable=False)
    download_url = Column(Text, nullable=True)
    visibility = Column(String, nullable=True)
    format = Column(String, nullable=True)  # "SafeTensor", etc.
    fp = Column(String, nullable=True)  # "fp16", "fp32", etc.
    size_label = Column(String, nullable=True)  # "pruned", "full", etc.
    pickle_scan_result = Column(String, nullable=True)
    virus_scan_result = Column(String, nullable=True)
    scanned_at = Column(DateTime, nullable=True)
    metadata_json = Column(JSON, nullable=True)

    # Relationships
    version = relationship("CivitaiModelVersion", back_populates="files", foreign_keys=[civitai_version_id])
    hashes = relationship("CivitaiModelFileHash", back_populates="file", cascade="all, delete-orphan")


class CivitaiModelFileHash(Base):
    """Hashes for model files (SHA256, AutoV2, CRC32, BLAKE3, etc.)."""
    __tablename__ = "civitai_model_file_hashes"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("civitai_model_version_files.civitai_file_id"), nullable=False, index=True)
    hash_type = Column(String, nullable=False)  # "SHA256", "AutoV2", "CRC32", "BLAKE3", "AutoV1", "AutoV3"
    hash_value = Column(String, nullable=False, index=True)

    # Relationships
    file = relationship("CivitaiModelVersionFile", back_populates="hashes", foreign_keys=[file_id])

    __table_args__ = (
        UniqueConstraint("file_id", "hash_type", name="uq_file_hash_type"),
        Index("ix_hash_type_value", "hash_type", "hash_value"),
    )


class CivitaiModelRank(Base):
    """Ranking and statistics for models and versions."""
    __tablename__ = "civitai_model_ranks"

    id = Column(Integer, primary_key=True, index=True)
    model_id = Column(Integer, ForeignKey("civitai_models.civitai_model_id"), nullable=True, index=True)
    version_id = Column(Integer, ForeignKey("civitai_model_versions.civitai_version_id"), nullable=True, index=True)
    scope = Column(String, nullable=False, default="allTime")  # "allTime" or "currentPeriod"
    download_count = Column(Integer, nullable=False, default=0)
    thumbs_up_count = Column(Integer, nullable=False, default=0)
    thumbs_down_count = Column(Integer, nullable=False, default=0)
    comment_count = Column(Integer, nullable=True)  # Model-level only
    collected_count = Column(Integer, nullable=True)  # Model-level only
    tipped_amount_count = Column(Integer, nullable=True)  # Model-level only
    generation_count = Column(Integer, nullable=True)  # Version-level only
    earned_amount = Column(Integer, nullable=True)  # Version-level only
    scraped_at = Column(DateTime, nullable=False)

    # Relationships
    model = relationship("CivitaiModel", back_populates="rank", foreign_keys=[model_id])
    version = relationship("CivitaiModelVersion", back_populates="rank", foreign_keys=[version_id])


class CivitaiModelTag(Base):
    """Tags applied to CivitAI models."""
    __tablename__ = "civitai_model_tags"

    id = Column(Integer, primary_key=True, index=True)
    civitai_model_id = Column(Integer, ForeignKey("civitai_models.civitai_model_id"), nullable=False, index=True)
    civitai_tag_id = Column(Integer, nullable=False)
    tag_name = Column(String, nullable=False)
    is_category = Column(Boolean, nullable=False, default=False)
    authority_term_id = Column(Integer, ForeignKey("authority_terms.id"), nullable=True)  # Future: link to taxonomy

    # Relationships
    model = relationship("CivitaiModel", back_populates="tags", foreign_keys=[civitai_model_id])
    authority_term = relationship("AuthorityTerm", foreign_keys=[authority_term_id])


# ---------------------------------------------------------------------------
# CivitAI Base Model Normalization Table
# ---------------------------------------------------------------------------


class CivitaiBaseModel(Base):
    """Canonical base model definitions (SD 1.5, SDXL, Flux, etc.).

    Normalized from the raw base_model strings on CivitaiModelVersion.
    Populated by backfill script and kept in sync during model upsert.
    """
    __tablename__ = "civitai_base_models"

    id = Column(Integer, primary_key=True, index=True)
    canonical_key = Column(String, unique=True, nullable=False, index=True)  # "sdxl", "sd15", "flux", etc.
    label = Column(String, nullable=False)  # Display label: "SDXL", "SD 1.5", "Flux"
    base_model_type = Column(String, nullable=True)  # "sd", "sdxl", etc. from CivitAI API
    family = Column(String, nullable=True)  # Grouping: "stable-diffusion", "flux", "pony"
    sort_order = Column(Integer, nullable=False, default=0)  # Display ordering
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationships
    versions = relationship(
        "CivitaiModelVersion",
        back_populates="base_model_ref",
        foreign_keys="[CivitaiModelVersion.base_model_id]",
    )


# ---------------------------------------------------------------------------
# Model Observation Table (Phase 3)
# ---------------------------------------------------------------------------


class ModelObservation(Base):
    """Tracks which CivitAI models/versions appear in which images.

    Normalized from the denormalized json_metadata.civitai.models/loras arrays.
    Provides structured querying of model usage across the image library.
    """
    __tablename__ = "model_observations"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(
        Integer, ForeignKey("images.id"), nullable=False, index=True
    )
    civitai_model_id = Column(
        Integer, ForeignKey("civitai_models.civitai_model_id"),
        nullable=True, index=True,
    )
    civitai_version_id = Column(
        Integer, ForeignKey("civitai_model_versions.civitai_version_id"),
        nullable=True, index=True,
    )
    # "checkpoint", "lora", "vae", etc.
    resource_type = Column(
        String, nullable=False, default="unknown", index=True
    )
    generation_stage = Column(String, nullable=True)  # Stage label when known
    # Primary checkpoint vs secondary
    is_primary = Column(
        Boolean, nullable=False, default=False, index=True
    )
    # "metadata", "user", "analysis"
    source_type = Column(
        String, nullable=False, default="metadata", index=True
    )
    confidence = Column(Float, nullable=True)  # Future: weight/strength
    strength = Column(Float, nullable=True)  # LoRA weight / CFG scale
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)

    # Relationships
    image = relationship("ImageModel", foreign_keys=[image_id])
    civitai_model = relationship(
        "CivitaiModel", foreign_keys=[civitai_model_id]
    )
    civitai_version = relationship(
        "CivitaiModelVersion", foreign_keys=[civitai_version_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "image_id", "civitai_version_id", "generation_stage",
            name="uq_model_obs_image_version_stage",
        ),
        # Covering: model → image (which images use this model)
        Index("ix_model_obs_model_image", "civitai_model_id", "image_id"),
        # Covering: version → image (which images use this specific version)
        Index("ix_model_obs_version_image", "civitai_version_id", "image_id"),
        # Covering: type+primary → image (find primary checkpoints)
        Index(
            "ix_model_obs_type_primary_image",
            "resource_type", "is_primary", "image_id",
        ),
    )
