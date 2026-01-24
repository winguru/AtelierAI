import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

# Get the database URL from an environment variable or use a default
# This makes it easy to switch to a different database like PostgreSQL later
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/image_db.sqlite")

# Define the path to the image library inside the container
IMAGE_LIBRARY_PATH = os.getenv("IMAGE_LIBRARY_PATH", "/var/lib/atelierai/image_library")

# Create the SQLAlchemy engine
# The `check_same_thread=False` is necessary for SQLite to work with FastAPI
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

# Create a configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create a Base class that our models will inherit from
Base = declarative_base()


# Dependency to get a DB session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- NEW: Add a function to test the DB connection ---
def test_db_connection():
    """Tests the database connection and prints a helpful error message."""
    try:
        # Attempt to connect to the database
        with engine.connect() as connection:
            print("✅ Database connection successful.")
            return True
    except OperationalError as e:
        print("❌ FATAL: Could not connect to the database.")
        print(f"   Error: {e}")
        print(f"   DATABASE_URL: {DATABASE_URL}")
        print("\n   Troubleshooting:")
        print("   1. Ensure the directory for the database file exists and is writable.")
        print("   2. If using Docker, check your volume mounts in docker-compose.yml.")
        print("   3. If running locally, check file/directory permissions.")
        return False
