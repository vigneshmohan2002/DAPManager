from typing import Any, Dict, List, Optional

from src.services.library_service import (
    build_artist_radio_payload,
    list_local_album_tracks,
    list_public_albums,
    query_public_tracks,
)


def _track(mbid: str, **overrides: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "mbid": mbid,
        "title": f"Track {mbid}",
        "artist": "Artist",
        "album": "Album",
        "track_number": 1,
        "disc_number": 1,
        "album_id": "album-1",
        "local_path": None,
        "dap_path": None,
        "is_liked": 0,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


class FakeLibraryStore:
    def __init__(self) -> None:
        self.albums: List[dict] = []
        self.all_tracks: List[dict] = []
        self.filtered_tracks: List[dict] = []
        self.album_tracks: List[dict] = []
        self.radio: dict = {
            "tracks": [],
            "top_tag": None,
            "seed_count": 0,
            "related_count": 0,
        }
        self.calls: List[tuple] = []

    def list_albums(self) -> List[dict]:
        self.calls.append(("list_albums",))
        return self.albums

    def list_all_tracks(self) -> List[dict]:
        self.calls.append(("list_all_tracks",))
        return self.all_tracks

    def list_tracks_filtered(
        self,
        playlist_id: Optional[str] = None,
        local_only: bool = False,
        include_orphans: bool = False,
    ) -> List[dict]:
        self.calls.append(
            (
                "list_tracks_filtered",
                playlist_id,
                local_only,
                include_orphans,
            )
        )
        return self.filtered_tracks

    def list_album_tracks(self, album_id: str) -> List[dict]:
        self.calls.append(("list_album_tracks", album_id))
        return self.album_tracks

    def build_artist_radio(
        self,
        artist_name: str,
        limit: int = 50,
    ) -> dict:
        self.calls.append(("build_artist_radio", artist_name, limit))
        return self.radio


def test_list_public_albums_preserves_wire_shape_and_strips_cover_path():
    store = FakeLibraryStore()
    store.albums = [{
        "id": "release-1",
        "title": "Album",
        "artist": "Artist",
        "track_count": 12,
        "cover_path": "/private/music/track.flac",
    }]

    assert list_public_albums(store) == {
        "success": True,
        "albums": [{
            "id": "release-1",
            "title": "Album",
            "artist": "Artist",
            "track_count": 12,
        }],
    }
    assert store.calls == [("list_albums",)]


def test_unfiltered_track_query_uses_legacy_query_and_drops_unavailable():
    store = FakeLibraryStore()
    store.all_tracks = [
        _track("local", local_path="/music/local.flac", is_liked=1),
        _track("missing"),
    ]

    result = query_public_tracks(store, has_master=False)

    assert [track["mbid"] for track in result["tracks"]] == ["local"]
    assert result["tracks"][0]["availability"] == "local"
    assert result["tracks"][0]["is_liked"] is True
    assert "local_path" not in result["tracks"][0]
    assert store.calls == [("list_all_tracks",)]


def test_filtered_track_query_keeps_only_requested_unavailable_orphans():
    store = FakeLibraryStore()
    store.filtered_tracks = [
        _track("orphan", deleted_at="2026-07-01 10:00:00"),
        _track("missing-live"),
        _track("drive", dap_path="/Volumes/DAP/drive.flac"),
    ]

    result = query_public_tracks(
        store,
        playlist_id="playlist-1",
        local_only=True,
        include_orphans=True,
        has_master=False,
    )

    assert [(row["mbid"], row["orphan"]) for row in result["tracks"]] == [
        ("orphan", True),
        ("drive", False),
    ]
    assert result["tracks"][0]["availability"] == "unavailable"
    assert store.calls == [
        ("list_tracks_filtered", "playlist-1", True, True),
    ]


def test_local_album_tracks_preserve_order_override_id_and_playability():
    store = FakeLibraryStore()
    store.album_tracks = [
        _track("disc-one", album_id="stale", local_path="/music/one.flac"),
        _track("unavailable", album_id="stale"),
        _track("disc-two", album_id="stale", dap_path="/dap/two.flac"),
    ]

    result = list_local_album_tracks(
        store,
        "requested-album",
        has_master=False,
    )

    assert [track["mbid"] for track in result["tracks"]] == [
        "disc-one",
        "disc-two",
    ]
    assert all(
        track["album_id"] == "requested-album"
        for track in result["tracks"]
    )
    assert store.calls == [("list_album_tracks", "requested-album")]


def test_artist_radio_payload_filters_tracks_without_changing_db_counts():
    store = FakeLibraryStore()
    store.radio = {
        "tracks": [
            _track("seed", local_path="/music/seed.flac"),
            _track("unavailable"),
        ],
        "top_tag": "indie",
        "seed_count": 1,
        "related_count": 1,
    }

    result = build_artist_radio_payload(
        store,
        "Artist",
        limit=75,
        has_master=False,
    )

    assert [track["mbid"] for track in result["tracks"]] == ["seed"]
    assert result["top_tag"] == "indie"
    assert result["seed_count"] == 1
    assert result["related_count"] == 1
    assert store.calls == [("build_artist_radio", "Artist", 75)]
