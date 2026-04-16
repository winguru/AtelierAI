import re
from importlib import import_module
from typing import Any, Optional
from urllib.parse import urlparse

# Load configured base domain for URL construction.
def _get_config_value(name: str, default: str = "") -> str:
    for mod_name in ("atelierai.config", "config", "backend.config"):
        try:
            mod = import_module(mod_name)
        except ModuleNotFoundError:
            continue
        val = getattr(mod, name, None)
        if val is not None:
            return val
    return default

_CIVITAI_BASE_DOMAIN = _get_config_value("CIVITAI_BASE_DOMAIN", "civitai.red")
_CIVITAI_WEB_BASE_URL = _get_config_value("CIVITAI_WEB_BASE_URL", "https://civitai.red")

_CIVITAI_IMAGE_PATH_RE = re.compile(r"^/images/(?P<image_id>\d+)(?:/.*)?$")


def _valid_civitai_hosts() -> set[str]:
    """Return the set of valid CivitAI hostnames for URL validation.

    Accepts both legacy civitai.com and the configured base domain so that
    existing DB records continue to resolve while new imports use the
    current domain.
    """
    return {"civitai.com", "www.civitai.com", _CIVITAI_BASE_DOMAIN, f"www.{_CIVITAI_BASE_DOMAIN}"}


def is_civitai_image_url(source_url: Optional[str]) -> bool:
    """Return True when the URL points to a CivitAI image page."""
    if not source_url:
        return False

    parsed = urlparse(source_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    if hostname not in _valid_civitai_hosts():
        return False

    return _CIVITAI_IMAGE_PATH_RE.match(parsed.path or "") is not None


def extract_civitai_image_id(source_url: str) -> Optional[int]:
    """Extract the numeric image ID from a CivitAI image URL."""
    parsed = urlparse(source_url.strip())
    match = _CIVITAI_IMAGE_PATH_RE.match(parsed.path or "")
    if not match:
        return None

    try:
        return int(match.group("image_id"))
    except (TypeError, ValueError):
        return None


def _extract_civitai_uuid_from_url_hash(url_hash: Optional[str]) -> Optional[str]:
    """Extract UUID/GUID-like key from CivitAI's image url hash field."""
    if not url_hash:
        return None

    text_hash = str(url_hash).strip()
    if not text_hash:
        return None

    parts = text_hash.split("/")
    if parts:
        candidate = parts[0].strip()
        if candidate and len(candidate) > 8:
            return candidate
    return text_hash if len(text_hash) > 8 else None


def extract_civitai_uuid(payload: Optional[dict[str, Any]]) -> Optional[str]:
    """Read civitai UUID/GUID key from normalized or raw payloads."""
    if not isinstance(payload, dict):
        return None

    direct = payload.get("civitai_uuid")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    raw_url = payload.get("url")
    if isinstance(raw_url, str):
        uuid_value = _extract_civitai_uuid_from_url_hash(raw_url)
        if uuid_value:
            return uuid_value

    image_payload = payload.get("image")
    if isinstance(image_payload, dict):
        image_url = image_payload.get("url")
        if isinstance(image_url, str):
            uuid_value = _extract_civitai_uuid_from_url_hash(image_url)
            if uuid_value:
                return uuid_value

    return None


def extract_civitai_hash(payload: Optional[dict[str, Any]]) -> Optional[str]:
    """Read civitai perceptual hash-like value from normalized or raw payloads."""
    if not isinstance(payload, dict):
        return None

    for key in ("civitai_hash", "hash"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_hash = metadata.get("hash")
        if isinstance(metadata_hash, str) and metadata_hash.strip():
            return metadata_hash.strip()

    image_payload = payload.get("image")
    if isinstance(image_payload, dict):
        image_hash = image_payload.get("hash")
        if isinstance(image_hash, str) and image_hash.strip():
            return image_hash.strip()

    return None


def fetch_civitai_image_data(source_url: Optional[str]) -> Optional[dict[str, Any]]:
    """Fetch and normalize CivitAI data for a source URL.

    Returns None when URL is not a CivitAI image URL or when enrichment fails.
    """
    if not source_url or not is_civitai_image_url(source_url):
        return None

    image_id = extract_civitai_image_id(source_url)
    if image_id is None:
        return None

    try:
        try:
            civitai_api_mod = import_module("atelierai.civitai.civitai_api")
            civitai_image_mod = import_module("atelierai.civitai.civitai_image")
        except ModuleNotFoundError:
            civitai_api_mod = import_module("app.src.atelierai.civitai.civitai_api")
            civitai_image_mod = import_module("app.src.atelierai.civitai.civitai_image")

        CivitaiAPI = getattr(civitai_api_mod, "CivitaiAPI")
        CivitaiImage = getattr(civitai_image_mod, "CivitaiImage")

        api = CivitaiAPI.get_instance()
        basic_info = api.fetch_basic_info(image_id)
        generation_data = api.fetch_generation_data(image_id)

        if not basic_info and not generation_data:
            return None

        image = CivitaiImage.from_single_image(
            basic_info=basic_info or {"id": image_id},
            generation_data=generation_data or {},
            api=None,
        )

        data = image.to_dict(include_full_url=True)

        # Store CivitAI tags as ID-first records for stable uniqueness.
        # Keep a tag_names list as a compatibility fallback for older consumers.
        tag_records = api.fetch_image_tag_records(image_id)
        if tag_records:
            data["tags"] = tag_records
            data["tag_names"] = [
                str(tag.get("name"))
                for tag in tag_records
                if isinstance(tag.get("name"), str) and str(tag.get("name")).strip()
            ]

        # Normalize author fields for downstream metadata consumers.
        # Keep a stable naming scheme and avoid the ambiguous `author` key.
        basic_user = basic_info.get("user", {}) if isinstance(basic_info, dict) else {}
        author_name = data.pop("author", None)
        if not author_name and isinstance(basic_user, dict):
            author_name = basic_user.get("username")

        author_id = basic_user.get("id") if isinstance(basic_user, dict) else None
        if author_id is not None:
            try:
                author_id = int(author_id)
            except (TypeError, ValueError):
                author_id = None

        if author_name:
            data["author_name"] = author_name
            data["author_profile"] = f"{_CIVITAI_WEB_BASE_URL}/user/{author_name}"
        if author_id is not None:
            data["author_id"] = author_id

        data["source_url"] = source_url
        data["image_id"] = image_id
        if isinstance(basic_info, dict):
            image_name = basic_info.get("name")
            if isinstance(image_name, str) and image_name.strip():
                data["image_name"] = image_name.strip()

            civitai_uuid = extract_civitai_uuid(basic_info)
            if civitai_uuid:
                data["civitai_uuid"] = civitai_uuid

            civitai_hash = extract_civitai_hash(basic_info)
            if civitai_hash:
                data["civitai_hash"] = civitai_hash

        return data
    except Exception as e:
        # Enrichment should not block uploads, so fail open.
        print(f"Warning: CivitAI enrichment failed for '{source_url}': {e}")
        return None
