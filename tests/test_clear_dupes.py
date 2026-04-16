import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch
from src.clear_dupes import get_file_score, get_duplicates_for_ui, resolve_duplicates, find_and_resolve_duplicates
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

    result = resolve_duplicates(db, "test_mbid", file1, [file2])

    assert "deleted" in result
    assert "errors" in result
    # file2 should be deleted
    assert not os.path.exists(file2)


def test_resolve_duplicates_file_not_found(db, temp_dir):
    """Test resolving duplicates with non-existent file."""
    file1 = os.path.join(temp_dir, "file1.flac")
    file2 = os.path.join(temp_dir, "file2.flac")  # Doesn't exist

    with open(file1, 'w') as f:
        f.write("test content")

    result = resolve_duplicates(db, "test_mbid", file1, [file2])

    assert "deleted" in result
    assert "errors" in result
    assert len(result["errors"]) > 0


def test_find_and_resolve_duplicates(caplog):
    """Test finding and resolving duplicates."""
    import logging
    with caplog.at_level(logging.INFO):
        find_and_resolve_duplicates(None)
    assert "Use the Web UI for duplicate management." in caplog.text