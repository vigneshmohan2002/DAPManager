"""Focused characterization for repository behavior not covered elsewhere."""

from datetime import datetime

import pytest

from src.catalog_sync import CatalogClient, PLAYLIST_PUSH_STATE_KEY
from src.contribution_sync import (
    CONTRIBUTE_STATE_KEY,
    _stamp_cursor as stamp_contribution,
)
from src.db_manager import Track
from src.db_repositories.downloads import DownloadRepository
from src.db_repositories.library import LibraryRepository
from src.inventory_sync import (
    INVENTORY_REPORT_STATE_KEY,
    _stamp_cursor as stamp_inventory,
)


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


def test_library_repository_lists_all_live_track_credit_artists_once(db):
    for mbid, artist, path in (
        ("primary-one", "Primary Artist", "/music/10-primary.flac"),
        (
            "featured",
            "Primary Artist featuring Guest",
            "/music/00-featured.flac",
        ),
        ("primary-two", "Primary Artist", "/music/20-primary.flac"),
        (
            "deleted-credit",
            "Primary Artist featuring Retired Guest",
            "/music/30-deleted.flac",
        ),
    ):
        db.add_or_update_track(Track(
            mbid=mbid,
            title=mbid,
            artist=artist,
            album="One Release",
            local_path=path,
            release_mbid="release-id",
        ))
    db.soft_delete_track("deleted-credit")

    albums = LibraryRepository(db.conn).list_albums()

    assert len(albums) == 1
    assert albums[0]["id"] == "release-id"
    assert albums[0]["track_count"] == 3
    # Keep the historical bare-column value selected alongside MIN(local_path).
    assert albums[0]["artist"] == "Primary Artist featuring Guest"
    assert albums[0]["primary_artist"] == "Primary Artist"
    assert albums[0]["credited_artists"] == [
        "Primary Artist",
        "Primary Artist featuring Guest",
    ]


def test_library_repository_primary_artist_ties_ignore_insertion_order(db):
    tracks = (
        ("first-zulu", "Zulu", "tie-first"),
        ("first-alpha", "alpha", "tie-first"),
        ("second-alpha", "alpha", "tie-second"),
        ("second-zulu", "Zulu", "tie-second"),
        ("case-lower", "alpha", "tie-case"),
        ("case-upper", "Alpha", "tie-case"),
    )
    for mbid, artist, release_id in tracks:
        db.add_or_update_track(Track(
            mbid=mbid,
            title=mbid,
            artist=artist,
            album="Tied Credits",
            local_path=f"/music/{mbid}.flac",
            release_mbid=release_id,
        ))

    albums = {
        album["id"]: album
        for album in LibraryRepository(db.conn).list_albums()
    }

    assert albums["tie-first"]["primary_artist"] is None
    assert albums["tie-second"]["primary_artist"] is None
    assert albums["tie-case"]["primary_artist"] is None


def test_library_repository_single_credit_is_primary_artist(db):
    db.add_or_update_track(Track(
        mbid="solo-track",
        title="Solo Track",
        artist="Solo Artist",
        album="Solo Album",
        local_path="/music/solo.flac",
        release_mbid="solo-release",
    ))

    album = LibraryRepository(db.conn).list_albums()[0]

    assert album["primary_artist"] == "Solo Artist"
    assert album["credited_artists"] == ["Solo Artist"]


def test_artist_tag_payload_keeps_non_string_tag_failure(db):
    with pytest.raises(AttributeError):
        db.apply_artist_tags_row({
            "artist_name": "Artist",
            "tags": [{"tag": 123, "weight": 50}],
        })

    assert db.get_top_tags_for_artist("Artist") == []


def test_library_facade_identity_liked_summary_and_lyrics_delete(db):
    db.add_or_update_track(Track(
        mbid="older-liked",
        title="Older",
        artist="Artist",
        album="Album",
        local_path="/music/older.flac",
    ))
    db.add_or_update_track(Track(
        mbid="newer-liked",
        title="Newer",
        artist="Artist",
        album="Album",
        local_path="/music/newer.flac",
        release_mbid="release-id",
    ))
    db.set_track_liked("older-liked", True)
    db.set_track_liked("newer-liked", True)
    db.conn.execute(
        "UPDATE tracks SET updated_at = ? WHERE mbid = ?",
        ("2026-01-01 00:00:00", "older-liked"),
    )
    db.conn.execute(
        "UPDATE tracks SET updated_at = ? WHERE mbid = ?",
        ("2026-01-02 00:00:00", "newer-liked"),
    )
    db.conn.commit()

    assert db.get_live_track_identity("newer-liked") == {
        "title": "Newer",
        "artist": "Artist",
        "album": "Album",
    }
    assert db.get_liked_tracks_summary(limit=1) == {
        "total": 2,
        "preview": [{
            "mbid": "newer-liked",
            "title": "Newer",
            "artist": "Artist",
            "album": "Album",
            "album_id": "release-id",
        }],
    }

    db.upsert_lyrics("newer-liked", "lyrics", False, "manual")
    db.delete_lyrics("newer-liked")
    db.delete_lyrics("newer-liked")
    assert db.get_lyrics("newer-liked") is None

    db.soft_delete_track("newer-liked")
    assert db.get_live_track_identity("newer-liked") is None
    assert db.get_liked_tracks_summary()["total"] == 1


def test_track_mapper_seams_remain_late_bound(db, monkeypatch):
    db.add_or_update_track(Track(
        mbid="mapped-track",
        title="Mapped",
        artist="Artist",
        album="Album",
        local_path="/music/mapped.flac",
        tag_tier="yellow",
    ))
    mapped_rows = []

    def map_row(row):
        mapped_rows.append(row["mbid"])
        return f"mapped:{row['mbid']}"

    monkeypatch.setattr(db, "_row_to_track", map_row)

    assert db.get_all_tracks() == ["mapped:mapped-track"]
    assert db.get_tracks_needing_tag_review() == ["mapped:mapped-track"]
    assert db.get_contributable_tracks() == ["mapped:mapped-track"]
    assert db.search_tracks("Mapped") == ["mapped:mapped-track"]
    assert mapped_rows == ["mapped-track"] * 4


def test_duplicate_repository_round_trip_and_clear(db, tmp_path):
    first = str(tmp_path / "folder" / ".." / "first.flac")
    second = str(tmp_path / "second.flac")

    db.log_duplicate("recording", first)
    db.log_duplicate("recording", first)
    db.log_duplicate("recording", second)

    duplicates = db.get_all_duplicates()
    assert duplicates == {
        "recording": [str(tmp_path / "first.flac"), second]
    }

    db.clear_duplicate("recording")
    assert db.get_all_duplicates() == {}


def test_missing_path_cleanup_preserves_dry_run_then_applies(db, tmp_path):
    existing = tmp_path / "existing.flac"
    existing.write_bytes(b"audio")
    missing_one = tmp_path / "missing-one.flac"
    missing_two = tmp_path / "missing-two.flac"
    for mbid, path in (
        ("existing", existing),
        ("missing-one", missing_one),
        ("missing-two", missing_two),
    ):
        db.add_or_update_track(Track(
            mbid=mbid,
            title=mbid,
            artist="Artist",
            local_path=str(path),
        ))

    preview = db.clear_missing_local_paths()
    assert preview == {
        "dry_run": True,
        "scanned": 3,
        "cleared": 2,
        "fraction": 0.667,
        "sample": [str(missing_one), str(missing_two)],
    }
    assert db.get_track_local_path("missing-one") == str(missing_one)

    applied = db.clear_missing_local_paths(dry_run=False)
    assert applied["dry_run"] is False
    assert applied["cleared"] == 2
    assert db.get_track_local_path("existing") == str(existing)
    assert db.get_track_local_path("missing-one") is None
    assert db.get_track_local_path("missing-two") is None


def test_update_local_path_releases_previous_owner_and_map_keeps_orphans(
    db,
    tmp_path,
):
    shared = str(tmp_path / "shared.flac")
    db.add_or_update_track(Track(
        mbid="old-owner",
        title="Old",
        artist="Artist",
        local_path=shared,
    ))
    db.add_or_update_track(Track(
        mbid="new-owner",
        title="New",
        artist="Artist",
        local_path=str(tmp_path / "other.flac"),
    ))

    db.update_track_local_path("new-owner", shared)
    db.soft_delete_track("new-owner")

    assert db.get_track_by_mbid("old-owner").local_path is None
    assert db.get_mbid_to_track_path_map() == {"new-owner": shared}


def test_album_merge_counts_and_split_dismissal_round_trip(db):
    db.update_album_metadata("source-release", "Source Album", 2)
    db.update_album_metadata("target-release", "Target Album", 3)
    db.add_or_update_track(Track(
        mbid="source-track",
        title="Track",
        artist="Artist",
        album="Source Album",
        local_path="/music/source.flac",
        release_mbid="source-release",
        track_number=1,
    ))

    assert db.get_incomplete_albums() == [{
        "artist": "Artist",
        "album": "Source Album",
        "mbid": "source-release",
        "have": 1,
        "total": 2,
        "missing": 1,
    }]
    assert db.merge_albums("source-release", "target-release") is True
    merged = db.get_track_by_mbid("source-track")
    assert merged.release_mbid == "target-release"
    assert merged.album == "Target Album"

    db.dismiss_split_album("incident")
    db.dismiss_split_album("incident")
    assert db.get_dismissed_split_albums() == {"incident"}
    db.undismiss_split_album("incident")
    assert db.get_dismissed_split_albums() == set()


def test_current_timestamp_facade_and_sync_callers(db, monkeypatch):
    timestamp = db.get_current_timestamp()
    assert timestamp is not None
    datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")

    fixed = "2026-07-15 12:34:56"
    calls = []

    def current_timestamp():
        calls.append("timestamp")
        return fixed

    monkeypatch.setattr(db, "get_current_timestamp", current_timestamp)
    stamp_inventory(db)
    stamp_contribution(db)
    push = CatalogClient(db, "http://master.invalid").push_playlists()

    assert calls == ["timestamp", "timestamp", "timestamp"]
    assert db.get_sync_state(INVENTORY_REPORT_STATE_KEY) == fixed
    assert db.get_sync_state(CONTRIBUTE_STATE_KEY) == fixed
    assert db.get_sync_state(PLAYLIST_PUSH_STATE_KEY) == fixed
    assert push["as_of"] == fixed


def test_playlist_prefix_operations_filter_count_and_purge(db):
    db.add_or_update_track(Track(
        mbid="mix-track",
        title="Track",
        artist="Artist",
    ))
    db.ensure_system_playlist("daily_mix_1", "Daily Mix 1: Rock")
    db.ensure_system_playlist("daily_mix_2", "Daily Mix 2: Jazz")
    regular_id = db.create_playlist("Regular")
    db.link_track_to_playlist("daily_mix_1", "mix-track", 0)
    db.soft_delete_playlist("daily_mix_2")

    assert db.list_playlists_by_prefix("daily_mix_") == [{
        "playlist_id": "daily_mix_1",
        "name": "Daily Mix 1: Rock",
        "track_count": 1,
    }]

    db.purge_playlists_by_prefix("daily_mix_")

    assert db.list_playlists_by_prefix("daily_mix_") == []
    assert db.get_playlist(regular_id) is not None
    remaining_memberships = db.conn.execute(
        "SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id LIKE ?",
        ("daily_mix_%",),
    ).fetchone()[0]
    assert remaining_memberships == 0
