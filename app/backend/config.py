import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from known .env locations so launch cwd does not matter.
_CONFIG_DIR = Path(__file__).resolve().parent
_DOTENV_CANDIDATES = [
    _CONFIG_DIR.parent / ".env",        # app/.env
    _CONFIG_DIR.parent.parent / ".env", # repo-root/.env
    _CONFIG_DIR.parent / ".vscode" / ".env",        # app/.vscode/.env
    _CONFIG_DIR.parent.parent / ".vscode" / ".env", # repo-root/.vscode/.env
    Path.cwd() / ".env",                # current working directory
    Path.cwd() / ".vscode" / ".env",   # cwd/.vscode/.env
]

_seen_dotenv_paths: set[Path] = set()
for _dotenv_path in _DOTENV_CANDIDATES:
    _resolved = _dotenv_path.resolve()
    if _resolved in _seen_dotenv_paths:
        continue
    _seen_dotenv_paths.add(_resolved)
    if _resolved.exists() and _resolved.is_file():
        load_dotenv(dotenv_path=_resolved, override=False)

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


def _env_float(name: str, default: float) -> float:
    """Parse an environment variable into a float, with fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default

# --- File System Configuration ---
# This is the path inside the Docker container where images are stored.
# Resolve and create it at import time so downstream code can rely on it.
_raw_library_path = os.getenv("IMAGE_LIBRARY_PATH", "image_library").strip()
if not _raw_library_path:
    _raw_library_path = "image_library"

IMAGE_LIBRARY_PATH = str(Path(_raw_library_path).expanduser())

_raw_resources_path = os.getenv("IMAGE_RESOURCES_PATH", "image_resources").strip()
if not _raw_resources_path:
    _raw_resources_path = "image_resources"

IMAGE_RESOURCES_PATH = str(Path(_raw_resources_path).expanduser())

try:
    Path(IMAGE_LIBRARY_PATH).mkdir(parents=True, exist_ok=True)
except OSError as e:
    raise RuntimeError(f"Could not create image library directory '{IMAGE_LIBRARY_PATH}': {e}") from e

try:
    resources_root = Path(IMAGE_RESOURCES_PATH)
    resources_root.mkdir(parents=True, exist_ok=True)
    (resources_root / "video_posters").mkdir(parents=True, exist_ok=True)
    (resources_root / "video_thumbnails").mkdir(parents=True, exist_ok=True)
    (resources_root / "thumbnails").mkdir(parents=True, exist_ok=True)
    (resources_root / "extracted_metadata").mkdir(parents=True, exist_ok=True)
except OSError as e:
    raise RuntimeError(f"Could not create image resources directory '{IMAGE_RESOURCES_PATH}': {e}") from e



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

# --- ComfyUI Configuration ---
ATELIER_COMFYUI_BASE_URL = os.getenv("ATELIER_COMFYUI_BASE_URL", "").strip()
ATELIER_COMFY_MATCH_THRESHOLD = max(0.0, min(1.0, _env_float("ATELIER_COMFY_MATCH_THRESHOLD", 0.95)))
