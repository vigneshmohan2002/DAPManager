import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch
from src.clear_dupes import (
    build_duplicate_resolution_plan,
    find_and_resolve_duplicates,
    get_duplicates_for_ui,
    get_file_score,
    resolve_duplicates,
)
from src.db_manager import DatabaseManager, Track


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def temp_dir():
    """Create temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_get_file_score_flac():
    """Test file score calculation for FLAC files."""
    path = "/test/Song.flac"
    score = get_file_score(path)
    assert score == 110  # Base 100 + 10 for .flac


def test_get_file_score_mp3():
    """Test file score calculation for MP3 files."""
    path = "/test/Song.mp3"
    score = get_file_score(path)
    assert score == 101  # Base 100 + 1 for .mp3


def test_get_file_score_windows_copy():
    """Test file score calculation for Windows copy files."""
    path = '/test/Song (1).flac'
    score = get_file_score(path)
    assert score == 90  # Base 100 - 20 for copy pattern + 10 for .flac


def test_get_file_score_track_number():
    """Test file score calculation for track numbered files."""
    path = '/test/01 - Song.flac'
    score = get_file_score(path)
    assert score == 100  # Base 100 - 10 for track number pattern + 10 for .flac


def test_get_duplicates_for_ui_empty(db):
    """Test getting duplicates UI data when no duplicates exist."""
    duplicates = get_duplicates_for_ui(db)
    assert duplicates == []


def test_get_duplicates_for_ui_with_data(db):
    """Test getting duplicates UI data with duplicates."""
    # Add a track to database
    track = Track(
        mbid="test_mbid",
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        local_path="/test/path.flac"
    )
    db.add_or_update_track(track)

    # Mock duplicates in database
    with patch.object(db, 'get_all_duplicates') as mock_get_all:
        mock_get_all.return_value = {
            "test_mbid": ["/test/path1.flac", "/test/path2.flac"]
        }

        duplicates = get_duplicates_for_ui(db)
        assert len(duplicates) == 1
        assert duplicates[0]["mbid"] == "test_mbid"
        assert len(duplicates[0]["candidates"]) == 2


def test_resolve_duplicates_success(db, temp_dir):
    """Test resolving duplicates successfully."""
    # Create test files
    file1 = os.path.join(temp_dir, "file1.flac")
    file2 = os.path.join(temp_dir, "file2.flac")

    with open(file1, 'w') as f:
        f.write("test content 1")
    with open(file2, 'w') as f:
        f.write("test content 2")

    db.add_or_update_track(Track(
        mbid="test_mbid",
        title="Track",
        artist="Artist",
        local_path=file2,
    ))
    db.log_duplicate("test_mbid", file1)
    db.log_duplicate("test_mbid", file2)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        result = resolve_duplicates(db, "test_mbid", file1, [file2])

    assert result == {
        "deleted": [file2],
        "errors": [],
        "missing": [],
        "remaining": [],
        "resolved": True,
    }
    assert not os.path.exists(file2)
    assert db.get_track_by_mbid("test_mbid").local_path == file1
    assert db.get_all_duplicates() == {}


def test_resolve_duplicates_file_not_found(db, temp_dir):
    """Test resolving duplicates with non-existent file."""
    file1 = os.path.join(temp_dir, "file1.flac")
    file2 = os.path.join(temp_dir, "file2.flac")  # Doesn't exist

    with open(file1, 'w') as f:
        f.write("test content")

    db.add_or_update_track(Track(
        mbid="test_mbid",
        title="Track",
        artist="Artist",
        local_path=file1,
    ))
    db.log_duplicate("test_mbid", file1)
    db.log_duplicate("test_mbid", file2)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        result = resolve_duplicates(db, "test_mbid", file1, [file2])

    assert result["resolved"] is True
    assert result["errors"] == []
    assert result["missing"] == [file2]
    assert db.get_all_duplicates() == {}


def _duplicate_group(db, keep_path, delete_path):
    db.add_or_update_track(Track(
        mbid="test_mbid",
        title="Track",
        artist="Artist",
        local_path=keep_path,
    ))
    db.log_duplicate("test_mbid", keep_path)
    db.log_duplicate("test_mbid", delete_path)


def test_resolve_rejects_delete_path_outside_group_before_mutation(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    unrelated = os.path.join(temp_dir, "unrelated.flac")
    for path in (keep, duplicate, unrelated):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        with pytest.raises(ValueError, match="not a member"):
            resolve_duplicates(db, "test_mbid", keep, [unrelated])

    assert os.path.exists(duplicate)
    assert os.path.exists(unrelated)
    assert set(db.get_all_duplicates()["test_mbid"]) == {keep, duplicate}


def test_resolve_rejects_keeper_in_delete_list(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    for path in (keep, duplicate):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        with pytest.raises(ValueError, match="includes keep_path"):
            resolve_duplicates(db, "test_mbid", keep, [keep, duplicate])

    assert os.path.exists(keep)
    assert os.path.exists(duplicate)


def test_resolve_rejects_hardlink_alias_of_keeper(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    alias = os.path.join(temp_dir, "alias.flac")
    with open(keep, "w") as handle:
        handle.write("audio")
    os.link(keep, alias)
    _duplicate_group(db, keep, alias)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        with pytest.raises(ValueError, match="aliases the kept file"):
            resolve_duplicates(db, "test_mbid", keep, [alias])

    assert os.path.exists(keep)
    assert os.path.exists(alias)


def test_resolve_rejects_mismatched_embedded_identity(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    for path in (keep, duplicate):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    def identity(path):
        return "wrong_mbid" if path == duplicate else "test_mbid"

    with patch("src.clear_dupes.get_mbid_from_tags", side_effect=identity):
        with pytest.raises(ValueError, match="does not match"):
            resolve_duplicates(db, "test_mbid", keep, [duplicate])

    assert os.path.exists(duplicate)


def test_resolve_rejects_same_recording_from_different_album_editions(
    db,
    temp_dir,
):
    keep = os.path.join(temp_dir, "keep.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    for path in (keep, duplicate):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    def release_identity(path):
        return "release-a" if path == keep else "release-b"

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"), \
         patch(
             "src.clear_dupes.get_release_mbid_from_tags",
             side_effect=release_identity,
         ):
        with pytest.raises(ValueError, match="different MusicBrainz releases"):
            resolve_duplicates(db, "test_mbid", keep, [duplicate])

    assert os.path.exists(keep)
    assert os.path.exists(duplicate)
    assert set(db.get_all_duplicates()["test_mbid"]) == {keep, duplicate}


def test_resolve_rejects_symlink_candidate(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    target = os.path.join(temp_dir, "target.flac")
    link = os.path.join(temp_dir, "link.flac")
    for path in (keep, target):
        with open(path, "w") as handle:
            handle.write("audio")
    os.symlink(target, link)
    _duplicate_group(db, keep, link)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        with pytest.raises(ValueError, match="non-symlink"):
            resolve_duplicates(db, "test_mbid", keep, [link])

    assert os.path.islink(link)
    assert os.path.exists(target)


def test_partial_delete_failure_keeps_group_for_retry(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    for path in (keep, duplicate):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"), \
         patch("src.clear_dupes.os.remove", side_effect=PermissionError("denied")):
        result = resolve_duplicates(db, "test_mbid", keep, [duplicate])

    assert result["resolved"] is False
    assert result["remaining"] == [keep, duplicate]
    assert len(result["errors"]) == 1
    assert set(db.get_all_duplicates()["test_mbid"]) == {keep, duplicate}


def test_unselected_duplicate_path_remains_visible(db, temp_dir):
    keep = os.path.join(temp_dir, "keep.flac")
    delete = os.path.join(temp_dir, "delete.flac")
    untouched = os.path.join(temp_dir, "untouched.flac")
    for path in (keep, delete, untouched):
        with open(path, "w") as handle:
            handle.write("audio")
    _duplicate_group(db, keep, delete)
    db.log_duplicate("test_mbid", untouched)

    with patch("src.clear_dupes.get_mbid_from_tags", return_value="test_mbid"):
        result = resolve_duplicates(db, "test_mbid", keep, [delete])

    assert result["resolved"] is False
    assert result["remaining"] == [keep, untouched]
    assert set(db.get_all_duplicates()["test_mbid"]) == {keep, untouched}


def test_plan_rejects_missing_keeper(db, temp_dir):
    keep = os.path.join(temp_dir, "missing.flac")
    duplicate = os.path.join(temp_dir, "duplicate.flac")
    with open(duplicate, "w") as handle:
        handle.write("audio")
    _duplicate_group(db, keep, duplicate)

    with pytest.raises(ValueError, match="keep_path is missing"):
        build_duplicate_resolution_plan(db, "test_mbid", keep, [duplicate])


def test_find_and_resolve_duplicates(caplog):
    """Test finding and resolving duplicates."""
    import logging
    with caplog.at_level(logging.INFO):
        find_and_resolve_duplicates(None)
    assert "Use the Web UI for duplicate management." in caplog.text
