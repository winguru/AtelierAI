import re
from importlib import import_module
from typing import Any, Optional
from urllib.parse import urlparse


_CIVITAI_IMAGE_PATH_RE = re.compile(r"^/images/(?P<image_id>\d+)(?:/.*)?$")


def is_civitai_image_url(source_url: Optional[str]) -> bool:
    """Return True when the URL points to a CivitAI image page."""
    if not source_url:
        return False

    parsed = urlparse(source_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False

    hostname = (parsed.hostname or "").lower()
    if hostname not in {"civitai.com", "www.civitai.com"}:
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
            api=api,
        )

        data = image.to_dict(include_full_url=True)

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
            data["author_profile"] = f"https://civitai.com/user/{author_name}"
        if author_id is not None:
            data["author_id"] = author_id

        data["source_url"] = source_url
        data["image_id"] = image_id
        if isinstance(basic_info, dict):
            image_name = basic_info.get("name")
            if isinstance(image_name, str) and image_name.strip():
                data["image_name"] = image_name.strip()
        return data
    except Exception as e:
        # Enrichment should not block uploads, so fail open.
        print(f"Warning: CivitAI enrichment failed for '{source_url}': {e}")
        return None
