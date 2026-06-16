# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/taxonomy-import.md
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
import enum

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    expected_file_size = Column(Integer, nullable=True, comment="Size (bytes) declared by source API (e.g. CivitAI metadata.size). NULL for non-CivitAI images or before enrichment.")
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
    civitai_post_id = Column(Integer, nullable=True, index=True)
    civitai_post_title = Column(String, nullable=True, comment="Title of the CivitAI post this image belongs to")
    civitai_post_index = Column(Integer, nullable=True, comment="Position of this image within its CivitAI post (0-based)")
    civitai_deleted_at = Column(DateTime, nullable=True, comment="When CivitAI confirmed this image no longer exists (404 from image.get)")
    civitai_cdn_url = Column(Text, nullable=True, comment="Actual CivitAI CDN URL used to download the image (may differ from source_url)")
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

    # User-defined image style override (best-guess can be corrected by review UI)
    user_image_style_concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=True)
    user_image_style_source = Column(String, nullable=True)  # guessed | review | imported
    user_image_style_confidence = Column(Float, nullable=True)

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

    # Health / integrity flags
    is_corrupt = Column(Boolean, nullable=False, default=False, comment="True when PIL verify() fails — truncated, bad header, or undecodable image data")

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
    user_image_style_concept = relationship("Concept", foreign_keys=[user_image_style_concept_id])
    review_assessments = relationship("ConceptReviewAssessment", back_populates="image")

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
            "expected_file_size": self.expected_file_size,
            "is_corrupt": self.is_corrupt or False,
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
            "civitai_post_id": self.civitai_post_id,
            "civitai_post_title": self.civitai_post_title,
            "civitai_post_index": self.civitai_post_index,
            "civitai_deleted_at": (
                self.civitai_deleted_at.isoformat() if self.civitai_deleted_at is not None else None
            ),
            "collection_type": self.collection_type,
            "artist": artist_info,
            "license": license_info,
            "exif_data": exif_data,
            "json_metadata": json_metadata,
            "user_nsfw_rating": self.user_nsfw_rating,
            "user_nsfw_safety_class": self.user_nsfw_safety_class,
            "user_image_style_concept_id": self.user_image_style_concept_id,
            "user_image_style_source": self.user_image_style_source,
            "user_image_style_confidence": self.user_image_style_confidence,
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
    # --- Prototype (CLIP visual centroid) ---
    concept_type = Column(String, nullable=True, default=None)
    prototype_vector = Column(LargeBinary, nullable=True, default=None)
    prototype_source_count = Column(Integer, nullable=True, default=None)
    prototype_updated_at = Column(DateTime, nullable=True, default=None)
    # --- Timestamps ---
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    parent = relationship("Concept", remote_side=[id], back_populates="children")
    children = relationship("Concept", back_populates="parent")
    aliases = relationship("ConceptAlias", back_populates="concept")
    authority_terms = relationship("AuthorityTerm", back_populates="concept")
    observations = relationship("ImageConceptObservation", back_populates="concept")
    review_sessions = relationship("ConceptReviewSession", back_populates="concept")
    review_assessments = relationship(
        "ConceptReviewAssessment",
        back_populates="concept",
        foreign_keys="ConceptReviewAssessment.concept_id",
    )
    group_memberships = relationship("ConceptGroupMembership", back_populates="concept")
    # Attributes of this concept (e.g. "purple hair" is an attribute of "Shion")
    attributes = relationship(
        "ConceptAttributeProfile",
        back_populates="concept",
        foreign_keys="ConceptAttributeProfile.concept_id",
        cascade="all, delete-orphan",
    )
    # Concepts this concept is an attribute of (reverse direction)
    attribute_of = relationship(
        "ConceptAttributeProfile",
        back_populates="attribute_concept",
        foreign_keys="ConceptAttributeProfile.attribute_concept_id",
        cascade="all, delete-orphan",
        overlaps="attributes,concept",
    )


class ConceptAttributeProfile(Base):
    """Links a concept to one of its attributes (another concept).

    For example, "Shion" has an attribute "purple hair" with:
      - attribute_kind = 'visual'  (observable in the image)
      - invariance = 'invariant'   (always present for Shion)
      - consistency_score = 0.95   (seen in 95% of Shion images)

    The attribute itself is a full Concept, so it can have its own aliases,
    hierarchy, and even its own attributes.
    """

    __tablename__ = "concept_attribute_profiles"

    concept_id = Column(
        Integer, ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True, index=True,
        comment="The concept that HAS this attribute (e.g. Shion)",
    )
    attribute_concept_id = Column(
        Integer, ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True, index=True,
        comment="The concept that IS the attribute (e.g. 'purple hair')",
    )
    attribute_kind = Column(
        String, nullable=False, default="visual",
        comment="How the attribute manifests: 'visual' (observable in image) or 'semantic' (abstract property)",
    )
    invariance = Column(
        String, nullable=False, default="variable",
        comment="Whether this attribute is always present: 'invariant' (defining) or 'variable' (contextual)",
    )
    consistency_score = Column(
        Float, nullable=True, default=None,
        comment="Fraction of reference images where this attribute is observed (0.0–1.0)",
    )
    notes = Column(Text, nullable=True)

    concept = relationship(
        "Concept", back_populates="attributes",
        foreign_keys=[concept_id],
        overlaps="attribute_of,concept",
    )
    attribute_concept = relationship(
        "Concept", back_populates="attribute_of",
        foreign_keys=[attribute_concept_id],
        overlaps="attributes,attribute_concept",
    )


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

    # Observation weighting fields (for concept training and prototype construction)
    observation_weight = Column(Float, nullable=True, default=None, comment="Overall weight of this observation during training (0-1)")
    review_confidence = Column(Float, nullable=True, default=None, comment="Reviewer confidence in this observation (0-1)")
    training_role = Column(String, nullable=True, default=None, comment="Training role: positive_exemplar, hard_negative, style_ref, context_ref, anomaly")
    concept_strength_weight = Column(Float, nullable=True, default=None, comment="How completely the target concept is supported by its attributes (0-1)")

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


class CollectionCivitaiMapping(Base):
    """Junction table: many-to-many between local collections and CivitAI collection IDs.

    A single local ``CollectionModel`` may be associated with multiple CivitAI
    collection IDs (e.g. an image-collection and a post-collection that share
    the same name).  Conversely, each CivitAI collection ID maps to exactly
    one local collection (``civitai_collection_id`` is UNIQUE).
    """

    __tablename__ = "collection_civitai_mappings"

    collection_id = Column(Integer, ForeignKey("collections.id"), primary_key=True)
    civitai_collection_id = Column(Integer, primary_key=True, unique=True, index=True)


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


# ---------------------------------------------------------------------------
# Sync Sessions — resumable sync-lab workflow state
# ---------------------------------------------------------------------------

class SyncSession(Base):
    """Persists sync-lab workflow state so jobs can resume after page refresh,
    server restart, or step failures."""

    __tablename__ = "sync_sessions"

    id = Column(String, primary_key=True)  # UUID4 string

    # Collection identity (set at creation)
    collection_id = Column(Integer, nullable=False, index=True)
    collection_type = Column(String, nullable=False, default="image")  # "image" | "post"
    collection_name = Column(String, nullable=True)

    # Step statuses: "pending" | "in_progress" | "complete" | "failed" | "cancelled"
    step_3_status = Column(String, nullable=False, default="pending")  # fetch items
    step_4_status = Column(String, nullable=False, default="pending")  # analyze local
    step_5_status = Column(String, nullable=False, default="pending")  # fetch metadata
    step_6_status = Column(String, nullable=False, default="pending")  # download
    step_7_status = Column(String, nullable=False, default="pending")  # ingest

    # Step output data (JSON blobs persisted after each step completes)
    step_3_data = Column(JSON, nullable=True)  # {image_ids, total_items, ...}
    step_4_data = Column(JSON, nullable=True)  # {existing, new, tombstoned, ...}
    step_5_data = Column(JSON, nullable=True)  # {results by image_id, ...}
    step_6_data = Column(JSON, nullable=True)  # {downloaded_ids, failed_ids, temp_paths, ...}
    step_7_data = Column(JSON, nullable=True)  # {ingested_ids, failed_ids, ...}

    # Prepared imports serialized for step 6→7 handoff (survives restart)
    prepared_imports = Column(JSON, nullable=True)

    # Overall session state
    active_step = Column(Integer, nullable=True)  # last step that was running
    error_message = Column(Text, nullable=True)
    is_complete = Column(Boolean, nullable=False, default=False, index=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_sync_sessions_active", "is_complete", "updated_at"),
    )


# ---------------------------------------------------------------------------
# CivitAI API Response Cache
# ---------------------------------------------------------------------------


class CivitaiApiCacheEntry(Base):
    """Append-on-change cache for CivitAI tRPC API responses.

    Each row represents one fetch of a specific (endpoint, request_key) pair.
    Only the most recent row per (endpoint, request_key) has is_latest=True.
    A new row is appended only when the response hash differs from the current
    latest — identical re-fetches update fetched_at on the existing row.

    request_key is a human-readable canonical key string (e.g. "id=12345" or
    "id=12345&type=Image") derived from the request payload by the service
    layer.  response_hash is a SHA-256 over the sorted-keys JSON serialization
    of response_json.
    """

    __tablename__ = "civitai_api_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    endpoint = Column(Text, nullable=False)
    request_key = Column(Text, nullable=False)
    request_payload = Column(JSON, nullable=True)
    response_json = Column(JSON, nullable=True)
    response_hash = Column(Text, nullable=True)
    http_status = Column(Integer, nullable=True)
    fetched_at = Column(DateTime, nullable=False)
    is_latest = Column(Boolean, nullable=False, default=True)
    prev_id = Column(Integer, ForeignKey("civitai_api_cache.id"), nullable=True)

    prev_entry = relationship(
        "CivitaiApiCacheEntry",
        remote_side=[id],
        foreign_keys=[prev_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "endpoint", "request_key", "response_hash",
            name="uq_civitai_cache_endpoint_key_hash",
        ),
        Index(
            "ix_civitai_cache_endpoint_key_fetched",
            "endpoint", "request_key", "fetched_at",
        ),
        Index("ix_civitai_cache_latest", "endpoint", "is_latest"),
        Index("ix_civitai_cache_fetched_at", "fetched_at"),
    )


# ---------------------------------------------------------------------------
# Pending User Bindings (staging for snapshot import)
# ---------------------------------------------------------------------------


class PendingUserBinding(Base):
    """User tag bindings imported from a snapshot that don't yet match an image.

    The image ingestion pipeline checks this table when new images arrive and
    auto-applies matching user tags, then sets ``applied_at``.
    """
    __tablename__ = "pending_user_bindings"

    id = Column(Integer, primary_key=True, index=True)
    file_hash = Column(String, nullable=False, index=True)
    file_path = Column(String, nullable=True)
    user_tags = Column(JSON, nullable=True)
    user_negative_tags = Column(JSON, nullable=True)
    source_snapshot = Column(String, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_pending_bindings_file_hash", "file_hash"),
    )




# ---------------------------------------------------------------------------
# Concept Review and Training Support Models
# ---------------------------------------------------------------------------


class ConceptAttributeTermProfile(Base):
    """Defines attribute expectations for a concept based on authority terms.

    This model captures what attributes a concept should have and how they should
    appear when expressed via authority_term entries. It supports the attribute
    discrimination model with modes (boolean, countable, exclusive), families
    (mutually exclusive alternatives), and cardinality constraints.

    This is separate from ConceptAttributeProfile which links concepts to other
    concepts. This table links concepts to authority_terms for fine-grained
    attribute tracking.

    Attributes:
        concept_id: The concept being profiled
        attribute_term_id: The attribute authority_term this profile references
        consistency_score: How consistently this attribute appears (0-1)
        invariance: Whether this attribute is invariant (always present)
        attribute_mode: "boolean", "countable", or "exclusive"
        attribute_family: Family name for exclusive attributes (e.g., "hair_color")
        cardinality_min: Minimum count for countable attributes
        cardinality_max: Maximum count for countable attributes (NULL = unlimited)
    """
    __tablename__ = "concept_attribute_term_profiles"

    concept_id = Column(
        Integer, ForeignKey("concepts.id"), nullable=False, primary_key=True
    )
    attribute_term_id = Column(
        Integer, ForeignKey("authority_terms.id"), nullable=False, primary_key=True
    )
    consistency_score = Column(
        Float, nullable=True, default=None,
        comment="Consistency of this attribute across instances (0-1)"
    )
    invariance = Column(
        Boolean, nullable=False, default=False,
        comment="Whether this attribute is invariant (always present)"
    )
    attribute_mode = Column(
        String, nullable=False, default="boolean",
        comment="Attribute mode: boolean, countable, or exclusive"
    )
    attribute_family = Column(
        String, nullable=True, default=None,
        comment="Family name for exclusive attributes (e.g., 'hair_color')"
    )
    cardinality_min = Column(
        Integer, nullable=True, default=None,
        comment="Minimum count for countable attributes"
    )
    cardinality_max = Column(
        Integer, nullable=True, default=None,
        comment="Maximum count for countable attributes (NULL = unlimited)"
    )
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    concept = relationship("Concept")
    attribute_term = relationship("AuthorityTerm")

    __table_args__ = (
        # Supports fast lookup: all attributes for a concept
        Index("ix_attr_term_profile_concept", "concept_id"),
        # Supports fast lookup: all concepts with a given attribute
        Index("ix_attr_term_profile_term", "attribute_term_id"),
    )


class ConceptAttributeAuthorityWeight(Base):
    """Authority trust weights for attribute evidence per concept.

    Different tag authorities may be more or less reliable for different
    concepts and attributes. This model stores both base weights (configured)
    and learned weights (updated from review outcomes).

    Attributes:
        concept_id: The concept being evaluated
        attribute_term_id: The attribute being evaluated
        authority_id: The tag authority providing evidence
        base_weight: Configured base trust weight (0-1, NULL = use global default)
        learned_weight: Learned weight from review feedback (NULL = use base_weight)
        updated_at: When learned_weight was last updated
    """
    __tablename__ = "concept_attribute_authority_weights"

    concept_id = Column(
        Integer, ForeignKey("concepts.id"), nullable=False, primary_key=True
    )
    attribute_term_id = Column(
        Integer, ForeignKey("authority_terms.id"), nullable=False, primary_key=True
    )
    authority_id = Column(
        Integer, ForeignKey("tag_authorities.id"), nullable=False, primary_key=True
    )
    base_weight = Column(
        Float, nullable=True, default=None,
        comment="Configured base trust weight (0-1, NULL = use global default)"
    )
    learned_weight = Column(
        Float, nullable=True, default=None,
        comment="Learned weight from review feedback (NULL = use base_weight)"
    )
    updated_at = Column(DateTime, nullable=True)

    concept = relationship("Concept")
    attribute_term = relationship("AuthorityTerm")
    authority = relationship("TagAuthority")

    __table_args__ = (
        # Supports fast lookup: all authorities for a concept/attribute pair
        Index("ix_auth_weight_concept_attr", "concept_id", "attribute_term_id"),
        # Supports fast lookup: all attributes for a concept/authority pair
        Index("ix_auth_weight_concept_auth", "concept_id", "authority_id"),
    )


class ConceptReviewEvidence(Base):
    """Human review evidence for concept-image-attribute relationships.

    This model captures structured human judgments used to:
    1. Validate automated concept detection
    2. Learn authority weights
    3. Train the composite scoring algorithm
    4. Build high-quality training corpuses

    Evidence is categorized by kind (identity, attribute, context, style, anomaly)
    and verdict (supports, contradicts, unknown). Each evidence point can reference
    a specific attribute or apply to the overall concept presence.

    Attributes:
        concept_id: The concept being evaluated
        image_id: The image being evaluated
        attribute_term_id: Optional specific attribute being evaluated
        evidence_kind: "identity", "attribute", "context", "style", or "anomaly"
        verdict: "supports", "contradicts", or "unknown"
        confidence: Reviewer confidence in this judgment (0-1)
        notes: Free-text notes explaining the judgment
        reviewer: Identifier of the reviewer (user or system)
        created_at: When this evidence was recorded
    """
    __tablename__ = "concept_review_evidence"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(
        Integer, ForeignKey("concepts.id"), nullable=False, index=True
    )
    image_id = Column(
        Integer, ForeignKey("images.id"), nullable=False, index=True
    )
    attribute_term_id = Column(
        Integer, ForeignKey("authority_terms.id"), nullable=True, index=True
    )
    evidence_kind = Column(
        String, nullable=False, index=True,
        comment="Evidence kind: identity, attribute, context, style, anomaly"
    )
    verdict = Column(
        String, nullable=False, index=True,
        comment="Verdict: supports, contradicts, unknown"
    )
    confidence = Column(
        Float, nullable=True, default=None,
        comment="Reviewer confidence in this judgment (0-1)"
    )
    notes = Column(Text, nullable=True)
    reviewer = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    concept = relationship("Concept")
    image = relationship("ImageModel")
    attribute_term = relationship("AuthorityTerm")

    __table_args__ = (
        # Supports fast lookup: all review evidence for a concept-image pair
        Index("ix_review_ev_concept_image", "concept_id", "image_id"),
        # Supports fast lookup: all review evidence for an image-attribute pair
        Index("ix_review_ev_image_attribute", "image_id", "attribute_term_id"),
    )

class ConceptReviewSession(Base):
    """Process-oriented review pass for grading many images of one concept."""

    __tablename__ = "concept_review_sessions"

    id = Column(Integer, primary_key=True, index=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="open")  # open | completed | abandoned
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)

    concept = relationship("Concept", back_populates="review_sessions")
    assessments = relationship(
        "ConceptReviewAssessment",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class ConceptReviewAssessment(Base):
    """Single structured grading record for one image within a review session."""

    __tablename__ = "concept_review_assessments"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("concept_review_sessions.id"), nullable=False, index=True)
    concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=False, index=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False, index=True)

    predominance_rating = Column(Integer, nullable=True)
    quality_rating = Column(Integer, nullable=True)
    accuracy_rating = Column(Integer, nullable=True)
    attribute_support_rating = Column(Integer, nullable=True)

    context_incongruent = Column(Boolean, nullable=False, default=False)
    context_anachronistic = Column(Boolean, nullable=False, default=False)
    context_anatopismic = Column(Boolean, nullable=False, default=False)
    context_nonsensical = Column(Boolean, nullable=False, default=False)
    context_anomalous_form = Column(Boolean, nullable=False, default=False)

    anomaly_present = Column(Boolean, nullable=False, default=False)
    anomaly_kind = Column(String, nullable=True)
    anomaly_degree = Column(Integer, nullable=True)

    # Concept deviations: intentional creative departures from canonical form
    # (as opposed to technical generation anomalies).
    deviation_present = Column(Boolean, nullable=False, default=False)
    deviation_body_variant = Column(Boolean, nullable=False, default=False)
    deviation_exaggerated = Column(Boolean, nullable=False, default=False)
    deviation_extra_feature = Column(Boolean, nullable=False, default=False)
    deviation_fusion = Column(Boolean, nullable=False, default=False)
    deviation_kind = Column(String, nullable=True)
    deviation_degree = Column(Integer, nullable=True)

    image_style_concept_id = Column(Integer, ForeignKey("concepts.id"), nullable=True)
    image_style_source = Column(String, nullable=True)  # guessed | review | imported
    image_style_confidence = Column(Float, nullable=True)

    # JSON dict: { attribute_concept_id: "present" | "absent" | "not_visible" }
    attribute_checks = Column(JSON, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, nullable=True)

    session = relationship("ConceptReviewSession", back_populates="assessments")
    concept = relationship("Concept", back_populates="review_assessments", foreign_keys=[concept_id])
    image = relationship("ImageModel", back_populates="review_assessments")
    image_style_concept = relationship("Concept", foreign_keys=[image_style_concept_id])

    __table_args__ = (
        UniqueConstraint("session_id", "image_id", name="uq_review_assessment_session_image"),
        Index("ix_review_assessment_session", "session_id"),
        Index("ix_review_assessment_concept_image", "concept_id", "image_id"),
    )


# ---------------------------------------------------------------------------
# CivitAI Search Lab — preference tracking
# ---------------------------------------------------------------------------
# These tables let the user mark images as keep/discard/skip from the search
# lab, and then hide previously-discarded images from future search results.

class CivitaiSearchImage(Base):
    """A CivitAI image seen in a search-lab session.

    Stores enough metadata to identify and display the image without requiring
    a live API call.  Multiple search sessions may reference the same image.
    """

    __tablename__ = "civitai_search_images"

    id = Column(Integer, primary_key=True, index=True)
    civitai_image_id = Column(Integer, nullable=False, index=True)
    post_id = Column(Integer, nullable=True)
    artist_id = Column(Integer, nullable=True)
    artist_name = Column(String, nullable=True)
    file_name = Column(String, nullable=True)
    blurhash = Column(String, nullable=True)
    uuid = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    image_url = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True)
    generation_prompt = Column(Text, nullable=True)
    generation_models = Column(JSON, nullable=True)
    reactions = Column(Integer, nullable=True)
    likes = Column(Integer, nullable=True)

    search_links = relationship(
        "CivitaiSearchImageLink",
        back_populates="image",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "civitai_image_id", name="uq_civitai_search_images_civitai_image_id"
        ),
    )


class CivitaiSearchRecord(Base):
    """A single search-lab query execution.

    Records the query terms, filters, and how many results were returned so
    the user can review their search history.
    """

    __tablename__ = "civitai_search_records"

    id = Column(Integer, primary_key=True, index=True)
    search_text = Column(Text, nullable=True)
    search_terms = Column(JSON, nullable=True)  # structured: tags, base_models, etc.
    search_rating = Column(String, nullable=True)  # free-form label
    result_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    image_links = relationship(
        "CivitaiSearchImageLink",
        back_populates="search",
        cascade="all, delete-orphan",
    )


class CivitaiSearchImageLink(Base):
    """Join table: which images appeared in which search and the user's rating.

    ``rating`` is one of ``keep``, ``discard``, or ``skip``.
    """

    __tablename__ = "civitai_search_image_links"

    id = Column(Integer, primary_key=True, index=True)
    search_id = Column(
        Integer, ForeignKey("civitai_search_records.id"), nullable=False, index=True
    )
    image_id = Column(
        Integer,
        ForeignKey("civitai_search_images.id"),
        nullable=False,
        index=True,
    )
    position = Column(Integer, nullable=True)
    rating = Column(String, nullable=True)  # "keep" | "discard" | "skip"
    is_excluded = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    search = relationship("CivitaiSearchRecord", back_populates="image_links")
    image = relationship("CivitaiSearchImage", back_populates="search_links")

    __table_args__ = (
        UniqueConstraint(
            "search_id", "image_id", name="uq_civitai_search_link_search_image"
        ),
    )


class CivitaiArtistPreference(Base):
    """Aggregated keep/discard counts per artist from search-lab sessions.

    Populated incrementally as images are rated, enabling future artist-level
    filtering (e.g. hide artists with high discard ratios).
    """

    __tablename__ = "civitai_artist_preferences"

    id = Column(Integer, primary_key=True, index=True)
    artist_id = Column(Integer, nullable=True, index=True)
    artist_name = Column(String, nullable=False, index=True)
    keeps = Column(Integer, nullable=False, default=0)
    discards = Column(Integer, nullable=False, default=0)
    is_blocked = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "artist_id", "artist_name", name="uq_civitai_artist_pref_artist"
        ),
    )
