"""Focused characterization for repository behavior not covered elsewhere."""

import pytest

from src.db_repositories.downloads import DownloadRepository


def _normalize_query(value: str) -> str:
    return " ".join(value.lower().split()) if value else ""


def test_download_repository_active_lookup_prefers_mbid_then_normalized_query(db):
    repository = DownloadRepository(db.conn)
    normalized_match = repository.queue(
        "  Artist   -   Title  ",
        "CATALOG",
        "different-mbid",
        "pending",
    )
    mbid_match = repository.queue(
        "Another Artist - Another Title",
        "CATALOG",
        "recording-mbid",
        "failed",
    )

    assert repository.get_active_download_id(
        "recording-mbid",
        "artist - title",
        _normalize_query,
    ) == mbid_match
    assert repository.get_active_download_id(
        "missing-mbid",
        "artist - title",
        _normalize_query,
    ) == normalized_match


def test_playlist_membership_uses_facade_timestamp_hook(db, monkeypatch):
    playlist_id = db.create_playlist("Hooked playlist")
    db.conn.execute(
        "INSERT INTO tracks (mbid, title, artist) VALUES (?, ?, ?)",
        ("track-mbid", "Title", "Artist"),
    )
    db.conn.commit()
    bumped = []
    monkeypatch.setattr(
        db,
        "_bump_playlist_updated_at",
        lambda value: bumped.append(value),
    )

    db.link_track_to_playlist(playlist_id, "track-mbid", 0)
    assert bumped == [playlist_id]

    bumped.clear()
    assert db.unlink_track_from_playlist(playlist_id, "track-mbid") is True
    assert bumped == [playlist_id]


def test_pushed_playlist_uses_facade_apply_hook(db, monkeypatch):
    incoming = {"playlist_id": "remote-playlist", "name": "Remote"}
    applied = []

    def apply_row(row):
        applied.append(row)
        return "hook-result"

    monkeypatch.setattr(db, "apply_playlist_row", apply_row)

    assert db.apply_pushed_playlist_row(incoming) == "hook-result"
    assert applied == [incoming]


def test_artist_radio_uses_facade_top_tags_hook(db, monkeypatch):
    requested = []

    def get_top_tags(artist_name, limit=5):
        requested.append((artist_name, limit))
        return []

    monkeypatch.setattr(db, "get_top_tags_for_artist", get_top_tags)

    result = db.build_artist_radio("Seed Artist", limit=12)

    assert requested == [("Seed Artist", 1)]
    assert result == {
        "tracks": [],
        "top_tag": None,
        "seed_count": 0,
        "related_count": 0,
    }


def test_artist_tag_payload_keeps_non_string_tag_failure(db):
    with pytest.raises(AttributeError):
        db.apply_artist_tags_row({
            "artist_name": "Artist",
            "tags": [{"tag": 123, "weight": 50}],
        })

    assert db.get_top_tags_for_artist("Artist") == []
