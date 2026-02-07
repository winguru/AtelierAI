import os
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

# --- Database Configuration ---
# Get the database URL from an environment variable or use a default
# This makes it easy to switch to a different database like PostgreSQL later
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////var/lib/atelierai/image_db.sqlite")

# --- File System Configuration ---
# This is the path inside the Docker container where images are stored.
IMAGE_LIBRARY_PATH = os.getenv("IMAGE_LIBRARY_PATH", "/var/lib/atelierai/image_library")

# --- Schema Versioning ---
CURRENT_SCHEMA_VERSION = "1.2"  # Increment this when you make schema changes

# --- CivitAI Configuration ---
CIVITAI_API_KEY = os.getenv("CIVITAI_API_KEY", "")

# Civitai credentials for automatic authentication (Playwright)
CIVITAI_USERNAME = os.getenv("CIVITAI_USERNAME", "")
CIVITAI_PASSWORD = os.getenv("CIVITAI_PASSWORD", "")

# Session cache file for automatic authentication
CIVITAI_SESSION_CACHE = os.getenv("CIVITAI_SESSION_CACHE", ".civitai_session")

# Session cookie for Civitai API authentication
# Can be set in .env file or via scripts/setup_session_token.py
CIVITAI_SESSION_COOKIE = os.getenv("CIVITAI_SESSION_COOKIE", "")
