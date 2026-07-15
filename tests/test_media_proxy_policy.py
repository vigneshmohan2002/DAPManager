from typing import Any, Dict, Optional

import requests

from src.services.media_proxy_service import (
    BufferedProxyResult,
    FileStreamResolution,
    LocalAlbumCoverResolution,
    LocalAlbumTracksResolution,
    MasterAlbumCoverResolution,
    MasterAlbumTracksResolution,
    MasterStreamResolution,
    MissingMediaResolution,
    resolve_album_cover,
    resolve_album_tracks,
    resolve_stream_source,
)


class FakeMediaStore:
    def __init__(
        self,
        *,
        cover_path: Optional[str] = None,
        sources: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cover_path = cover_path
        self.sources = sources
        self.calls = []

    def get_album_cover_path(self, album_id: str) -> Optional[str]:
        self.calls.append(("get_album_cover_path", album_id))
        return self.cover_path

    def get_track_sources(self, mbid: str) -> Optional[dict]:
        self.calls.append(("get_track_sources", mbid))
        return self.sources


def test_album_cover_prefers_local_extraction_over_configured_master():
    store = FakeMediaStore(cover_path="/music/track.flac")
    extracted = []

    def extract(path: str):
        extracted.append(path)
        return b"cover", "image/jpeg"

    result = resolve_album_cover(
        store,
        "album-1",
        config_values={
            "master_url": " https://master.test/// ",
            "api_token": " secret ",
        },
        extract_cover=extract,
    )

    assert result == LocalAlbumCoverResolution(b"cover", "image/jpeg")
    assert extracted == ["/music/track.flac"]
    assert store.calls == [("get_album_cover_path", "album-1")]


def test_album_cover_falls_back_to_normalized_master_after_local_miss():
    store = FakeMediaStore(cover_path="/music/track.flac")

    result = resolve_album_cover(
        store,
        "album-1",
        config_values={
            "master_url": " https://master.test/// ",
            "api_token": " secret ",
        },
        extract_cover=lambda _path: None,
    )

    assert result == MasterAlbumCoverResolution(
        master_url="https://master.test",
        api_token="secret",
    )


def test_album_cover_returns_missing_without_local_art_or_real_master_config():
    store = FakeMediaStore()

    result = resolve_album_cover(
        store,
        "album-1",
        config_values="https://not-a-config.test",
        extract_cover=lambda _path: None,
    )

    assert result == MissingMediaResolution(404)


def test_album_tracks_master_response_is_authoritative_without_local_db_call():
    upstream = BufferedProxyResult(
        404,
        "application/json",
        b'{"success": false}',
    )
    requested = []

    def request_tracks(master_url: str, album_id: str, *, api_token: str = ""):
        requested.append((master_url, album_id, api_token))
        return upstream

    def fail_if_loaded(_album_id: str, *, has_master: bool):
        raise AssertionError("local replica must not be queried")

    result = resolve_album_tracks(
        "album/one",
        config_values={
            "master_url": " https://master.test/ ",
            "api_token": " token ",
        },
        load_local_tracks=fail_if_loaded,
        request_tracks=request_tracks,
    )

    assert result == MasterAlbumTracksResolution(upstream)
    assert requested == [("https://master.test", "album/one", "token")]


def test_album_tracks_request_exception_falls_back_to_replica():
    loaded = []

    def unavailable(*_args, **_kwargs):
        raise requests.ConnectionError("offline")

    def load(album_id: str, *, has_master: bool):
        loaded.append((album_id, has_master))
        return {"success": True, "tracks": [{"mbid": "local"}]}

    result = resolve_album_tracks(
        "album-1",
        config_values={"master_url": "https://master.test"},
        load_local_tracks=load,
        request_tracks=unavailable,
    )

    assert result == LocalAlbumTracksResolution(
        {"success": True, "tracks": [{"mbid": "local"}]}
    )
    assert loaded == [("album-1", True)]


def test_album_tracks_server_error_falls_back_to_replica():
    loaded = []

    def load(album_id: str, *, has_master: bool):
        loaded.append((album_id, has_master))
        return {"success": True, "tracks": []}

    result = resolve_album_tracks(
        "album-1",
        config_values={"master_url": "https://master.test"},
        load_local_tracks=load,
        request_tracks=lambda *_args, **_kwargs: BufferedProxyResult(
            503,
            "application/json",
            b"",
        ),
    )

    assert result == LocalAlbumTracksResolution(
        {"success": True, "tracks": []}
    )
    assert loaded == [("album-1", True)]


def test_album_tracks_without_master_loads_local_with_remote_disabled():
    loaded = []

    def load(album_id: str, *, has_master: bool):
        loaded.append((album_id, has_master))
        return {"success": True, "tracks": []}

    def fail_if_requested(*_args, **_kwargs):
        raise AssertionError("master transport must not be called")

    result = resolve_album_tracks(
        "album-1",
        config_values={},
        load_local_tracks=load,
        request_tracks=fail_if_requested,
    )

    assert result == LocalAlbumTracksResolution(
        {"success": True, "tracks": []}
    )
    assert loaded == [("album-1", False)]


def test_stream_resolution_prefers_existing_local_file_before_dap():
    store = FakeMediaStore(sources={
        "local_path": "/music/local.flac",
        "dap_path": "/Volumes/DAP/track.mp3",
    })
    checked = []

    def exists(path: str) -> bool:
        checked.append(path)
        return True

    result = resolve_stream_source(
        store,
        "track-1",
        config_values={"master_url": "https://master.test"},
        file_exists=exists,
    )

    assert result == FileStreamResolution(
        path="/music/local.flac",
        content_type="audio/flac",
        source="local",
    )
    assert checked == ["/music/local.flac"]


def test_stream_resolution_uses_dap_then_normalized_master():
    store = FakeMediaStore(sources={
        "local_path": "/music/missing.flac",
        "dap_path": "/Volumes/DAP/track.mp3",
    })

    dap_result = resolve_stream_source(
        store,
        "track-1",
        config_values={"master_url": "https://master.test"},
        file_exists=lambda path: path.startswith("/Volumes"),
    )
    master_result = resolve_stream_source(
        store,
        "track-1",
        config_values={
            "master_url": " https://master.test/// ",
            "api_token": " secret ",
        },
        file_exists=lambda _path: False,
    )

    assert dap_result == FileStreamResolution(
        path="/Volumes/DAP/track.mp3",
        content_type="audio/mpeg",
        source="drive",
    )
    assert master_result == MasterStreamResolution(
        master_url="https://master.test",
        api_token="secret",
    )


def test_stream_resolution_returns_missing_for_unknown_or_unreachable_track():
    unknown = resolve_stream_source(
        FakeMediaStore(sources=None),
        "unknown",
        config_values={"master_url": "https://master.test"},
        file_exists=lambda _path: True,
    )
    unreachable = resolve_stream_source(
        FakeMediaStore(sources={"local_path": None, "dap_path": None}),
        "known",
        config_values={},
        file_exists=lambda _path: False,
    )

    assert unknown == MissingMediaResolution(404)
    assert unreachable == MissingMediaResolution(404)
