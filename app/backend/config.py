import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from a .env file if it exists
load_dotenv()

# --- Database Configuration ---
# Get the database URL from an environment variable or use a default
# This makes it easy to switch to a different database like PostgreSQL later
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///image_db.sqlite")


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse an environment variable into a boolean."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# --- File System Configuration ---
# This is the path inside the Docker container where images are stored.
# Resolve and create it at import time so downstream code can rely on it.
_raw_library_path = os.getenv("IMAGE_LIBRARY_PATH", "image_library").strip()
if not _raw_library_path:
    _raw_library_path = "image_library"

IMAGE_LIBRARY_PATH = str(Path(_raw_library_path).expanduser())

try:
    Path(IMAGE_LIBRARY_PATH).mkdir(parents=True, exist_ok=True)
except OSError as e:
    raise RuntimeError(f"Could not create image library directory '{IMAGE_LIBRARY_PATH}': {e}") from e



# --- Schema Versioning ---
CURRENT_SCHEMA_VERSION = "1.3"  # Increment this when you make schema changes
ALLOW_SCHEMA_RESET = _env_bool("ALLOW_SCHEMA_RESET", default=False)

# --- CivitAI Configuration ---
CIVITAI_API_KEY = os.getenv("CIVITAI_API_KEY", "")

# CivitAI credentials for automatic authentication (Playwright)
CIVITAI_USERNAME = os.getenv("CIVITAI_USERNAME", "")
CIVITAI_PASSWORD = os.getenv("CIVITAI_PASSWORD", "")

# Session cache file for automatic authentication
CIVITAI_SESSION_CACHE = os.getenv("CIVITAI_SESSION_CACHE", ".civitai_session")

# Session cookie for CivitAI API authentication
# Can be set in .env file or via scripts/setup_session_token.py
CIVITAI_SESSION_COOKIE = os.getenv("CIVITAI_SESSION_COOKIE", "")
