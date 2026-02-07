from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text, DateTime, JSON, Enum
from sqlalchemy.orm import relationship
from database import Base

# 1. Define the values in a single, constant tuple. This is the source of truth.
COLLECTION_TYPE_VALUES = ('public', 'paid', 'private', 'user_created')

# 2. Dynamically create the Enum class from the tuple.
#    This is the clean and correct way.
CollectionType = Enum('CollectionType', ' '.join(COLLECTION_TYPE_VALUES), type=str)


class ImageModel(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    file_path = Column(Text, unique=True, index=True, nullable=False)
    file_name = Column(String, nullable=False)
    file_hash = Column(String, unique=True, index=True, nullable=False)
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    date_created = Column(DateTime)
    date_modified = Column(DateTime)

    # Attribution & Licensing
    source_url = Column(Text)
    source_site = Column(String)
    artist_id = Column(Integer, ForeignKey("artists.id"), nullable=True)
    license_id = Column(Integer, ForeignKey("licenses.id"), nullable=True)

    # 3. Use the same tuple to define the database column.
    collection_type = Column(Enum(*COLLECTION_TYPE_VALUES, name="collectiontype"))

    # Metadata
    exif_data = Column(JSON)
    json_metadata = Column(JSON)

    # Relationships
    license = relationship("License", back_populates="images")
    analysis_data = relationship("AnalysisData", back_populates="images")
    tags = relationship("Tag", secondary="image_tags", back_populates="images")
    datasets = relationship("Dataset", secondary="dataset_images", back_populates="images")
    artist = relationship("Artist", back_populates="images")

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
                "nickname": self.artist.nickname
            }

        # Safely access the license information
        license_info = None
        if self.license:
            license_info = {
                "id": self.license.id,
                "short_name": self.license.short_name,
                "name": self.license.name
            }

        return {
            "id": self.id,
            "file_name": self.file_name,
            "file_hash": self.file_hash,
            "artist": artist_info,
            "license": license_info
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
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=True)  # Nullable if user-generated
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
    creator_user_id = Column(String)  # Could be a simple string or a foreign key to a users table later
    version = Column(String)
    export_date = Column(DateTime)

    # Relationships
    analysis_data = relationship("AnalysisData", back_populates="dataset")
    images = relationship("ImageModel", secondary="dataset_images", back_populates="datasets")


class DatasetImage(Base):
    __tablename__ = "dataset_images"

    dataset_id = Column(Integer, ForeignKey("datasets.id"), primary_key=True)
    image_id = Column(Integer, ForeignKey("images.id"), primary_key=True)


class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    nickname = Column(String)
    deviantart_url = Column(String)
    civitai_url = Column(String)
    pixiv_url = Column(String)
    # Add any other social/handle URLs you want

    # Relationship back to images
    images = relationship("ImageModel", back_populates="artist")


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version_num = Column(String, primary_key=True)
