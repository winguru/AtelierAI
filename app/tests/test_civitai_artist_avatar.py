"""Tests for the Search Lab CivitAI artist avatar cache."""

from __future__ import annotations

import sys
import base64
import io
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from atelierai.civitai.civitai_api import CivitaiAPI  # noqa: E402
from database import Base  # noqa: E402
from models import CivitaiArtistProfile  # noqa: E402
from routers.civitai import search  # noqa: E402


@pytest.fixture()
def database(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    checkout_state = {"count": 0}

    @event.listens_for(engine, "checkout")
    def _track_checkout(*_args):
        checkout_state["count"] += 1

    @event.listens_for(engine, "checkin")
    def _track_checkin(*_args):
        checkout_state["count"] -= 1

    monkeypatch.setattr(search, "SessionLocal", session_factory)
    try:
        yield engine, session_factory, checkout_state
    finally:
        engine.dispose()


class FakeCivitaiApi:
    def __init__(self, images, profiles=None):
        self.images = images
        self.profiles = profiles or {}
        self.calls = 0

    def fetch_basic_info(self, image_id):
        self.calls += 1
        return self.images[image_id]

    def fetch_user_by_id(self, user_id):
        return self.profiles.get(user_id, {})

    def fetch_generation_data(self, _image_id):
        return {}

    def fetch_image_tag_records(self, _image_id):
        return []


def _basic_info(image_id=101, artist_id=7, avatar_url="https://image.civitai.com/avatar.webp"):
    return {
        "id": image_id,
        "user": {
            "id": artist_id,
            "username": "artist-name",
            "image": avatar_url,
        },
    }


def _avatar_bytes(size=(320, 180), color=(80, 130, 190)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


def _decode_data_uri(data_uri):
    header, encoded = data_uri.split(",", 1)
    return header, base64.b64decode(encoded)


def test_compacts_avatar_to_small_square_webp():
    compact, mime_type = search._compact_artist_avatar(_avatar_bytes())

    assert mime_type == "image/webp"
    assert len(compact) <= search._ARTIST_AVATAR_INLINE_MAX_BYTES
    with Image.open(io.BytesIO(compact)) as image:
        assert image.size == (96, 96)


def test_cache_miss_returns_inline_uri_and_persists_compact_blob(database, monkeypatch):
    api = FakeCivitaiApi({101: _basic_info()})
    monkeypatch.setattr(search, "_download_artist_avatar", lambda _url: (_avatar_bytes(), "image/png"))

    artist_key, data_uri = search._resolve_inline_artist_avatar(api, _basic_info())

    assert artist_key == "id:7"
    header, decoded = _decode_data_uri(data_uri)
    assert header == "data:image/webp;base64"
    assert len(decoded) <= search._ARTIST_AVATAR_INLINE_MAX_BYTES
    with database[1]() as db:
        profile = db.query(CivitaiArtistProfile).one()
        assert profile.avatar_data == decoded
        assert profile.avatar_mime_type == "image/webp"


def test_cached_inline_avatar_skips_upstream(database, monkeypatch):
    api = FakeCivitaiApi({101: _basic_info()})
    monkeypatch.setattr(search, "_download_artist_avatar", lambda _url: (_avatar_bytes(), "image/png"))
    first = search._resolve_inline_artist_avatar(api, _basic_info())

    monkeypatch.setattr(search, "_download_artist_avatar", lambda _url: pytest.fail("download called"))
    second = search._resolve_inline_artist_avatar(api, _basic_info())

    assert second == first


def test_bulk_map_deduplicates_artist(database):
    compact, mime_type = search._compact_artist_avatar(_avatar_bytes())
    with database[1]() as db:
        db.add(
            CivitaiArtistProfile(
                artist_key="id:7",
                artist_id=7,
                artist_name="artist-name",
                avatar_data=compact,
                avatar_mime_type=mime_type,
                avatar_source_url="https://image.civitai.com/avatar.webp",
            )
        )
        db.commit()
        hits = [_basic_info(101), _basic_info(102)]
        hits = [{"id": hit["id"], "user": hit["user"]} for hit in hits]
        avatars = search._attach_cached_artist_avatars(db, hits)

    assert list(avatars) == ["id:7"]
    assert all(hit["artistAvatarKey"] == "id:7" for hit in hits)


def test_remote_download_runs_without_checked_out_connection(database, monkeypatch):
    _engine, _session_factory, checkout_state = database
    api = FakeCivitaiApi({101: _basic_info()})

    def download(_url):
        assert checkout_state["count"] == 0
        return _avatar_bytes(), "image/png"

    monkeypatch.setattr(search, "_download_artist_avatar", download)
    search._resolve_inline_artist_avatar(api, _basic_info())

    assert checkout_state["count"] == 0


def test_profile_picture_metadata_supplies_missing_user_image(database, monkeypatch):
    api = FakeCivitaiApi(
        {101: _basic_info(avatar_url="")},
        profiles={
            7: {
                "username": "artist-name",
                "image": None,
                "profilePicture": {
                    "url": "9052aecb-a18c-483d-ac0c-48a503731287",
                    "name": "avatar image.png",
                },
            }
        },
    )
    downloaded_urls = []
    monkeypatch.setattr(
        search,
        "_download_artist_avatar",
        lambda url: (downloaded_urls.append(url) or _avatar_bytes(), "image/png"),
    )

    artist_key, data_uri = search._resolve_inline_artist_avatar(api, _basic_info(avatar_url=""))

    assert artist_key == "id:7"
    assert data_uri.startswith("data:image/webp;base64,")
    assert downloaded_urls == [
        "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/"
        "9052aecb-a18c-483d-ac0c-48a503731287/width=256/avatar%20image.png"
    ]


def test_rejects_non_civitai_or_insecure_avatar_urls():
    assert search._is_allowed_artist_avatar_url("https://image.civitai.com/avatar.webp")
    assert not search._is_allowed_artist_avatar_url("http://image.civitai.com/avatar.webp")
    assert not search._is_allowed_artist_avatar_url("https://example.com/avatar.webp")
    with pytest.raises(ValueError, match="approved CivitAI"):
        search._download_artist_avatar("https://example.com/avatar.webp")


def test_missing_avatar_returns_key_without_data(database):
    api = FakeCivitaiApi({101: _basic_info(avatar_url="")})

    assert search._resolve_inline_artist_avatar(api, _basic_info(avatar_url="")) == ("id:7", None)


def test_single_image_response_contains_inline_avatar(database, monkeypatch):
    api = FakeCivitaiApi({101: _basic_info()})
    monkeypatch.setattr(CivitaiAPI, "get_instance", lambda: api)
    monkeypatch.setattr(search, "_download_artist_avatar", lambda _url: (_avatar_bytes(), "image/png"))

    response = search.civitai_search_single_image(101)

    assert response["hit"]["artistAvatarKey"] == "id:7"
    assert response["artist_avatars"]["id:7"].startswith("data:image/webp;base64,")


def test_trpc_normalization_preserves_artist_id():
    hit = search._build_hit_from_trpc(_basic_info(), {}, [])
    assert hit["user"]["id"] == 7
