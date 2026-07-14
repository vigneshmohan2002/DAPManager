"""Integration coverage for split-album planning and mutation execution."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.split_album_detector import (
    consolidate_editions,
    detect_split_albums,
    merge_album_groups,
    retag_files_from_db,
)


@pytest.fixture
def db():
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


def _add_track(
    db,
    *,
    mbid,
    album,
    artist="Artist",
    release_mbid=None,
    local_path=None,
    track_number=1,
):
    db.add_or_update_track(Track(
        mbid=mbid,
        title=f"Track {track_number}",
        artist=artist,
        album=album,
        release_mbid=release_mbid,
        local_path=local_path,
        track_number=track_number,
    ))


def test_detect_split_album_deduplicates_and_honours_dismissal(db):
    _add_track(
        db,
        mbid="standard",
        album="Album",
        local_path="/music/album/01.flac",
    )
    _add_track(
        db,
        mbid="deluxe",
        album="Album (Deluxe)",
        release_mbid="release-deluxe",
        local_path="/music/album/02.flac",
        track_number=2,
    )

    incidents = detect_split_albums(db)

    assert len(incidents) == 1
    assert incidents[0]["type"] == "folder"
    assert incidents[0]["directory"] == "/music/album"
    db.dismiss_split_album(incidents[0]["key"])
    assert detect_split_albums(db) == []
    assert len(detect_split_albums(db, include_dismissed=True)) == 1


def test_reassignment_facade_commits_before_loading_tag_paths():
    events = []
    cursor = MagicMock()
    cursor.rowcount = 1
    cursor.fetchall.side_effect = [
        [("source",)],
        [{"mbid": "source", "local_path": "/music/source.flac"}],
    ]

    def record_execute(sql, _params):
        normalized = " ".join(sql.split())
        if "SELECT mbid, local_path" in normalized:
            events.append("paths")
        elif normalized.startswith("SELECT mbid FROM tracks"):
            events.append("select")
        else:
            events.append("update")

    cursor.execute.side_effect = record_execute
    connection = MagicMock()
    connection.cursor.return_value = cursor
    connection.commit.side_effect = lambda: events.append("commit")
    fake_db = SimpleNamespace(conn=connection)

    result = DatabaseManager.reassign_album_group_tracks(
        fake_db,
        "Album|Artist",
        "Album (Deluxe)",
        "Artist",
        "release-deluxe",
    )

    assert events == ["select", "update", "commit", "paths"]
    assert result == {
        "matched": 1,
        "moved": 1,
        "tracks": [{"mbid": "source", "local_path": "/music/source.flac"}],
    }


def test_merge_executes_db_plan_then_updates_file_tags(db, tmp_path):
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"not parsed because the tag writer is mocked")
    _add_track(
        db,
        mbid="canonical",
        album="Album (Deluxe)",
        release_mbid="release-deluxe",
    )
    _add_track(
        db,
        mbid="source",
        album="Album",
        artist="Artist feat. Guest",
        local_path=str(source_path),
    )

    with patch("src.tag_service.update_album_tags") as update_tags:
        result = merge_album_groups(
            db,
            "release-deluxe",
            "Album|Artist feat. Guest",
            "Album (Deluxe)",
            "Artist",
            "release-deluxe",
        )

    assert result == {"merged": 1, "tagged": 1, "tag_errors": []}
    moved = db.get_track_by_mbid("source")
    assert moved.album == "Album (Deluxe)"
    assert moved.release_mbid == "release-deluxe"
    # A release-MBID target keeps track-level featured-artist credits.
    assert moved.artist == "Artist feat. Guest"
    update_tags.assert_called_once_with(
        str(source_path),
        album="Album (Deluxe)",
        album_artist="Artist",
        release_mbid="release-deluxe",
    )


def test_merge_keeps_committed_db_change_when_one_file_tag_fails(db, tmp_path):
    source_path = tmp_path / "source.flac"
    source_path.write_bytes(b"tag writer is mocked")
    _add_track(
        db,
        mbid="source",
        album="Album",
        local_path=str(source_path),
    )

    with patch(
        "src.tag_service.update_album_tags", side_effect=RuntimeError("bad tag")
    ):
        result = merge_album_groups(
            db,
            "target",
            "Album|Artist",
            "Album (Deluxe)",
            "Artist",
            "release-deluxe",
        )

    assert result["merged"] == 1
    assert result["tagged"] == 0
    assert result["tag_errors"] == ["source.flac: bad tag"]
    assert db.get_track_by_mbid("source").release_mbid == "release-deluxe"


def test_consolidate_dry_run_then_apply_preserves_summary_and_artist(db):
    _add_track(
        db,
        mbid="base-1",
        album="Album",
        artist="Artist feat. One",
        local_path="/missing/base-1.flac",
    )
    _add_track(
        db,
        mbid="base-2",
        album="Album",
        artist="Artist feat. One",
        local_path="/missing/base-2.flac",
        track_number=2,
    )
    _add_track(
        db,
        mbid="deluxe",
        album="Album (Deluxe)",
        artist="Artist feat. One",
        release_mbid="release-deluxe",
        local_path="/missing/deluxe.flac",
    )

    preview = consolidate_editions(db, dry_run=True)

    assert preview["albums_merged"] == 1
    assert preview["tracks_reassigned"] == 2
    assert db.get_track_by_mbid("base-1").release_mbid is None

    applied = consolidate_editions(db, dry_run=False)

    assert applied["albums_merged"] == 1
    assert applied["tracks_reassigned"] == 2
    assert applied["files_tagged"] == 0
    moved = db.get_track_by_mbid("base-1")
    assert moved.album == "Album (Deluxe)"
    assert moved.release_mbid == "release-deluxe"
    assert moved.artist == "Artist feat. One"


def test_retag_uses_facade_snapshot_and_preserves_skip_policy(db, tmp_path):
    path = tmp_path / "song.flac"
    path.write_bytes(b"tag reader and writer are mocked")
    _add_track(
        db,
        mbid="song",
        album="Album",
        release_mbid="release",
        local_path=str(path),
    )

    with patch("src.tag_service.read_current_tags") as read_tags, patch(
        "src.tag_service.update_album_tags"
    ) as update_tags:
        read_tags.return_value = {"album": "Album", "release_mbid": "release"}
        result = retag_files_from_db(db, only_mismatched=True)

    assert result == {"tagged": 0, "skipped": 1, "errors": []}
    update_tags.assert_not_called()
