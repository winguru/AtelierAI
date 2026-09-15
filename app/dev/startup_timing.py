# Startup timing probe — wraps lifespan steps to report durations.
# Usage: PYTHONPATH=app/src:app/backend python -m dev.startup_timing
import os
import time

os.chdir(os.path.join(os.path.dirname(__file__), ".."))

t0 = time.perf_counter()

import backend.main as m  # noqa: E402

t_import = time.perf_counter() - t0
print(f"[timing] module import: {t_import:.2f}s")

# Wrap the heavy migration functions with timing
import backend.services.db_migrations as mig  # noqa: E402


def _timed(name, fn):
    def wrapper(*a, **k):
        s = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            print(f"[timing] {name}: {time.perf_counter() - s:.2f}s", flush=True)

    return wrapper


_TIMED_FNS = [
    "create_initial_data",
    "_ensure_image_lifecycle_columns",
    "_ensure_collection_sync_columns",
    "_ensure_civitai_search_media_columns",
    "_ensure_collection_civitai_mappings_table",
    "_ensure_user_nsfw_columns",
    "_ensure_civitai_uuid_column",
    "_ensure_civitai_hash_column",
    "_ensure_user_tags_column",
    "_ensure_user_negative_tags_column",
    "_ensure_observation_authority_term_unique_index",
    "_backfill_user_tags_to_observations",
    "_ensure_image_variant_columns",
    "_ensure_promoted_metadata_columns",
    "_ensure_original_file_name_column",
    "_ensure_blurhash_column",
    "_ensure_civitai_image_id_column",
    "_ensure_civitai_post_id_column",
    "_ensure_civitai_deleted_at_column",
    "_ensure_civitai_post_title_index_columns",
    "_ensure_civitai_cdn_url_column",
    "_ensure_civitai_user_columns",
    "_ensure_civitai_user_banned_at_column",
    "_ensure_civitai_creator_id_column",
    "_ensure_base_model_id_column",
    "_seed_civitai_base_models",
    "_backfill_civitai_base_model_ids",
    "_backfill_civitai_users",
    "_ensure_observation_unique_constraint",
    "_ensure_file_hash_nonunique",
    "_ensure_is_corrupt_column",
    "_ensure_expected_file_size_column",
    "_ensure_concept_prototype_columns",
    "_ensure_clip_embedding_columns",
    "_ensure_artist_preference_skips_column",
    "_ensure_artist_preference_blocked_column",
    "_ensure_search_link_search_id_nullable",
    "rebuild_artist_preference_counters",
]

for _name in _TIMED_FNS:
    _fn = getattr(m, _name, None) or getattr(mig, _name, None)
    if _fn is None:
        continue
    # Patch in main module namespace (lifespan calls them unqualified)
    setattr(m, _name, _timed(_name, _fn))

# Time CLIP provider construction + eager load separately
from services.clip_provider import LocalCLIPProvider, set_clip_provider  # noqa: E402
from config import CLIP_LOCAL_ENABLED, CLIP_FORCE_CPU, CLIP_MODEL_NAME, CLIP_PRETRAINED  # noqa: E402

if CLIP_LOCAL_ENABLED:
    s = time.perf_counter()
    try:
        provider = LocalCLIPProvider(
            model_name=CLIP_MODEL_NAME,
            pretrained=CLIP_PRETRAINED,
            force_cpu=CLIP_FORCE_CPU,
        )
        t_ctor = time.perf_counter() - s
        s = time.perf_counter()
        provider.ensure_loaded()
        print(
            f"[timing] LocalCLIPProvider ctor: {t_ctor:.2f}s  "
            f"ensure_loaded: {time.perf_counter() - s:.2f}s"
        )
        set_clip_provider(provider)
    except Exception as exc:
        print(f"[timing] LocalCLIPProvider failed: {exc}")
else:
    print("[timing] CLIP_LOCAL_ENABLED=false")

# Now run the real lifespan startup portion by invoking it
import asyncio  # noqa: E402


async def run_lifespan():
    s = time.perf_counter()
    async with m.lifespan(m.app):
        print(f"[timing] lifespan startup total: {time.perf_counter() - s:.2f}s")


asyncio.run(run_lifespan())
print(f"[timing] TOTAL (import + startup): {time.perf_counter() - t0:.2f}s")
