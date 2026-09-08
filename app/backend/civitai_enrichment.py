# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# 📄 docs: app/docs/memories/civitai-cache.md
# ──────────────────────────────────────────────────────────────────────────────
import logging
import re
from datetime import timedelta
from importlib import import_module
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

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

# Sentinel max_age meaning "accept any cached row regardless of age; fall
# back to a live API call only on a cache miss".  CivitAI image metadata
# (image.get / image.getGenerationData / tag.getVotableTags) is effectively
# immutable once published, so re-fetching identical payloads on every scan
# is wasted rate limit.  Pass this to fetch_civitai_image_data(max_age=...)
# to make enrichment cache-first.  ``None`` keeps the legacy always-live
# behaviour for callers that genuinely need fresh data (user-forced refresh).
CIVITAI_ANY_AGE: timedelta = timedelta.max


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


def _try_resolve_deleted_username(
    user_id: int,
    *,
    image_id: Optional[int] = None,
) -> Optional[str]:
    """Resolve the historical username for a deleted/banned CivitAI user.

    CivitAI's ``user.getById`` returns the real username for *banned* accounts
    (``deletedAt`` is null) but returns ``null`` for *truly deleted* accounts.
    This helper tries several sources in order of reliability:

    1. ``user.getById`` API — works for banned accounts and active accounts.
    2. Local Search Lab data — ``CivitaiSearchImage.artist_name`` captured the
       username at scrape time, even for users later fully deleted from CivitAI.

    Args:
        user_id: CivitAI numeric user ID.
        image_id: Optional CivitAI image ID to narrow the Search Lab lookup.

    Returns:
        The resolved username, or ``None`` if no source yields a result.
    """
    # 1. Try the live API — works for banned/active users.
    try:
        from atelierai.civitai.civitai_api import CivitaiAPI

        api = CivitaiAPI.get_instance()
        user_data = api.fetch_user_by_id(user_id)
        if isinstance(user_data, dict):
            username = user_data.get("username")
            if isinstance(username, str) and username.strip():
                return username.strip()
    except Exception as exc:  # noqa: BLE001 — enrichment must fail open
        logger.debug("user.getById failed for uid=%s: %s", user_id, exc)

    # 2. Try local Search Lab data — has historical name from scrape time.
    #    IMPORTANT: import as ``database`` / ``models`` (not ``backend.database`` /
    #    ``backend.models``) to match the app's import convention and avoid creating
    #    a second SQLAlchemy ``Base`` / ``MetaData`` instance (which causes
    #    "Table already defined" / "Multiple classes found" errors at query time).
    try:
        from database import SessionLocal
        from models import CivitaiSearchImage

        db = SessionLocal()
        try:
            query = db.query(CivitaiSearchImage.artist_name).filter(
                CivitaiSearchImage.artist_id == user_id,
                CivitaiSearchImage.artist_name.isnot(None),
                CivitaiSearchImage.artist_name != "",
            )
            if image_id is not None:
                # Prefer the exact image, then fall back to any image by this user.
                exact = query.filter(
                    CivitaiSearchImage.civitai_image_id == image_id
                ).first()
                if exact and not exact[0].startswith("[deleted:"):
                    return exact[0]
            row = query.first()
            if row and not row[0].startswith("[deleted:"):
                return row[0]
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Search Lab lookup failed for uid=%s: %s", user_id, exc)

    return None


def _build_fallback_data_from_search_lab(
    image_id: int,
) -> Optional[dict[str, Any]]:
    """Build enrichment data from local Search Lab when the live API fails.

    When a CivitAI image is deleted, ``image.get`` returns HTTP 404 and the
    normal enrichment path has no data to work with.  This helper queries the
    ``CivitaiSearchImage`` table — populated during search-lab sessions — and
    reconstructs a minimal enrichment dict with tags, prompt, models, and
    artist info captured at scrape time.

    Returns ``None`` when no Search Lab record exists for *image_id*.
    """
    # IMPORTANT: import as ``database`` / ``models`` (not ``backend.database`` /
    # ``backend.models``) to match the app's import convention and avoid creating
    # a second SQLAlchemy ``Base`` / ``MetaData`` instance.
    try:
        from database import SessionLocal
        from models import CivitaiSearchImage
    except ImportError:
        return None

    try:
        db = SessionLocal()
        try:
            row = (
                db.query(CivitaiSearchImage)
                .filter(CivitaiSearchImage.civitai_image_id == image_id)
                .first()
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — enrichment must fail open
        return None

    if row is None:
        return None

    data: dict[str, Any] = {}

    # ── Tags ──
    # Search Lab stores tags as a simple list of tag-name strings.
    # Convert to the dict-based format that downstream consumers expect
    # (``_upsert_civitai_authority_terms``, ``GalleryTagService``, etc.).
    if row.tags:
        data["tags"] = [{"name": str(t), "id": None} for t in row.tags if t]
        data["tag_names"] = [str(t) for t in row.tags if t]

    # ── Generation prompt ──
    if row.generation_prompt:
        data["prompt"] = row.generation_prompt

    # ── Generation models ──
    if row.generation_models:
        data["models"] = list(row.generation_models) if isinstance(
            row.generation_models, list
        ) else row.generation_models

    # ── Artist / author ──
    if row.artist_name and not str(row.artist_name).startswith("[deleted:"):
        data["author_name"] = row.artist_name
        data["author_profile"] = f"{_CIVITAI_WEB_BASE_URL}/user/{row.artist_name}"
    elif row.artist_name:
        data["author_name"] = row.artist_name
    if row.artist_id is not None:
        data["author_id"] = row.artist_id

    data["author_deleted"] = True
    data["author_banned"] = False
    data["source_url"] = f"{_CIVITAI_WEB_BASE_URL}/images/{image_id}"
    data["image_id"] = image_id

    if row.uuid:
        data["civitai_uuid"] = row.uuid
    if row.blurhash:
        data["blurhash"] = row.blurhash

    return data


def fetch_civitai_image_data(
    source_url: Optional[str],
    *,
    max_age: Optional[timedelta] = None,
) -> Optional[dict[str, Any]]:
    """Fetch and normalize CivitAI data for a source URL.

    Args:
        source_url: CivitAI image page URL.
        max_age: When provided, serve from cache if a row exists within this
            age and only call the live API when the cache is stale or absent.
            ``None`` (default) always fetches live, preserving prior behaviour
            for all existing call sites.  Pass ``CIVITAI_ANY_AGE`` to accept
            any cached row regardless of age (cache-first, live on miss) —
            recommended for backfill/scan paths where CivitAI metadata is
            effectively immutable.

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
        if max_age is not None:
            basic_info = api.fetch_basic_info_cached(image_id, max_age=max_age)
            generation_data = api.fetch_generation_data_cached(image_id, max_age=max_age)
        else:
            basic_info = api.fetch_basic_info(image_id)
            generation_data = api.fetch_generation_data(image_id)

        if not basic_info and not generation_data:
            # The image is likely deleted from CivitAI (HTTP 404).  Fall
            # back to local Search Lab data so we can still populate tags,
            # prompt, models, and artist info from what was captured at
            # scrape time.  Without this fallback, deleted images lose all
            # CivitAI metadata including tags.
            fallback = _build_fallback_data_from_search_lab(image_id)
            if fallback is None:
                return None
            return fallback

        image = CivitaiImage.from_single_image(
            basic_info=basic_info or {"id": image_id},
            generation_data=generation_data or {},
            api=None,
        )

        data = image.to_dict(include_full_url=True)

        # Store CivitAI tags as ID-first records for stable uniqueness.
        # Keep a tag_names list as a compatibility fallback for older consumers.
        if max_age is not None:
            tag_records = api.fetch_image_tag_records_cached(image_id, max_age=max_age)
        else:
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

        # Detect deleted CivitAI accounts — username becomes "[deleted]"
        # but the user ID and deletedAt timestamp remain.
        #
        # Banned accounts are distinguishable from deleted ones in that they
        # still report full user data (username, profile, etc.) while deleted
        # accounts have a null username and a set deletedAt.  The CivitAI
        # image API does not expose an explicit "banned" flag, so banned
        # status is inferred from other signals (e.g. user.getById) or set
        # manually.  See app/docs/memories/civitai-integration.md.
        deleted_at = (
            basic_user.get("deletedAt") if isinstance(basic_user, dict) else None
        )
        if deleted_at is not None:
            data["author_deleted"] = True
            data["author_banned"] = False
            # Preserve the original username before CivitAI replaced it.
            if author_name and author_name != "[deleted]":
                data["author_original_name"] = author_name
            # Build a synthetic name for fully scrubbed accounts (username null).
            # Try to resolve the historical username from the API (banned users)
            # or Search Lab data (truly deleted users) before falling back.
            if not data.get("author_name") and author_id is not None:
                resolved = _try_resolve_deleted_username(
                    author_id, image_id=image_id
                )
                if resolved:
                    data["author_name"] = resolved
                    data["author_original_name"] = resolved
                    data["author_profile"] = (
                        f"{_CIVITAI_WEB_BASE_URL}/user/{resolved}"
                    )
                else:
                    data["author_name"] = f"[deleted:{author_id}]"
        else:
            data["author_deleted"] = False
            data["author_banned"] = False

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

            # Persist declared file size from CivitAI metadata for size-mismatch detection
            metadata = basic_info.get("metadata")
            if isinstance(metadata, dict):
                raw_size = metadata.get("size")
                try:
                    declared_file_size = int(raw_size) if raw_size is not None else None
                except (TypeError, ValueError):
                    declared_file_size = None
                if declared_file_size is not None:
                    data["declared_file_size"] = declared_file_size

        return data
    except Exception as e:
        # Enrichment should not block uploads, so fail open.
        print(f"Warning: CivitAI enrichment failed for '{source_url}': {e}")
        return None
