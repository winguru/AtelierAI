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
CURRENT_SCHEMA_VERSION = "1.5"  # Increment this when you make schema changes
ALLOW_SCHEMA_RESET = _env_bool("ALLOW_SCHEMA_RESET", default=False)

# --- CivitAI Domain Configuration ---
# CivitAI split into two domains: civitai.com (sanitized) and civitai.red (all existing content).
# Set CIVITAI_BASE_DOMAIN to choose which domain to use for API, auth, and web URLs.
CIVITAI_BASE_DOMAIN = os.getenv("CIVITAI_BASE_DOMAIN", "civitai.red").strip()
CIVITAI_TRPC_BASE_URL = f"https://{CIVITAI_BASE_DOMAIN}/api/trpc"
CIVITAI_REST_BASE_URL = f"https://{CIVITAI_BASE_DOMAIN}/api/v1"
CIVITAI_WEB_BASE_URL = f"https://{CIVITAI_BASE_DOMAIN}"

# CDN domains — unchanged in the civitai.com → civitai.red split, but configurable
# in case they diverge in the future.
CIVITAI_CDN_BASE_URL = os.getenv(
    "CIVITAI_CDN_BASE_URL", "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA"
).strip()
CIVITAI_CDN_ALT_BASE_URL = os.getenv(
    "CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com"
).strip()

# Search service — separate subdomain, may or may not follow the base domain.
CIVITAI_SEARCH_BASE_URL = os.getenv(
    "CIVITAI_SEARCH_BASE_URL", "https://search-new.civitai.com"
).strip()

# CivitAI Archive — third-party mirror, unaffected by the domain split.
CIVITAIARCHIVE_BASE_URL = os.getenv(
    "CIVITAIARCHIVE_BASE_URL", "https://civitaiarchive.com"
).strip()

# --- CivitAI Authentication Configuration ---
CIVITAI_API_KEY = os.getenv("CIVITAI_API_KEY", "")

# Meilisearch public search key (static, embedded in CivitAI frontend JS).
# Auto-scraped from _app JS chunk when empty.
CIVITAI_MEILISEARCH_KEY = os.getenv("CIVITAI_MEILISEARCH_KEY", "")

# CivitAI credentials for automatic authentication (Playwright)
CIVITAI_USERNAME = os.getenv("CIVITAI_USERNAME", "")
CIVITAI_PASSWORD = os.getenv("CIVITAI_PASSWORD", "")

# Session cache file for automatic authentication
CIVITAI_SESSION_CACHE = os.getenv("CIVITAI_SESSION_CACHE", ".civitai_session")

def _read_civitai_session_cookie_from_cache(cache_path: str) -> str:
    """Read current CivitAI session cookie from the cache file.

    This keeps runtime behavior aligned with token refresh flows, where the
    authoritative value lives in `.civitai_session`.
    """
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token and len(token) > 100:
            return token
    except OSError:
        pass
    return ""


# Session cookie for CivitAI API authentication.
# Source of truth is the session cache file, not environment variables.
CIVITAI_SESSION_COOKIE = _read_civitai_session_cookie_from_cache(CIVITAI_SESSION_CACHE)

# Optional Chrome profile overrides for OAuth fallback behavior.
# Leave unset to use the project-local .civitai_chrome_profile directory.
CIVITAI_CHROME_USER_DATA_DIR = os.getenv("CIVITAI_CHROME_USER_DATA_DIR", "").strip()
CIVITAI_CHROME_PROFILE_DIRECTORY = os.getenv("CIVITAI_CHROME_PROFILE_DIRECTORY", "").strip()

# --- ComfyUI Configuration ---
ATELIER_COMFYUI_BASE_URL = os.getenv("ATELIER_COMFYUI_BASE_URL", "").strip()
ATELIER_COMFY_MATCH_THRESHOLD = max(0.0, min(1.0, _env_float("ATELIER_COMFY_MATCH_THRESHOLD", 0.95)))
