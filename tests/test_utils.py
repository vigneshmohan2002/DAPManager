import pytest
import time
from unittest.mock import patch, MagicMock
from mediafile import UnreadableFileError
from src.utils import RateLimiter, EnvironmentManager, get_mbid_from_tags, write_mbid_to_file, find_mbid_by_fingerprint


def test_rate_limiter_wait_if_needed():
    """Test RateLimiter wait_if_needed method."""
    limiter = RateLimiter(calls_per_second=1.0)  # 1 call per second

    # First call should not wait
    start_time = time.time()
    limiter.wait_if_needed()
    end_time = time.time()

    # Should be almost immediate (less than 0.1 seconds)
    assert end_time - start_time < 0.1

    # Second call should wait approximately 1 second
    start_time = time.time()
    limiter.wait_if_needed()
    end_time = time.time()

    # Should have waited close to 1 second
    assert end_time - start_time >= 0.9


def test_rate_limiter_decorator():
    """Test RateLimiter as decorator."""
    limiter = RateLimiter(calls_per_second=10.0)  # 10 calls per second

    @limiter
    def test_function():
        return "test_result"

    # Should work without exceptions
    result = test_function()
    assert result == "test_result"


def test_environment_manager_validate_environment():
    """Test EnvironmentManager validate_environment method."""
    with patch('os.environ.get') as mock_get:
        # Test with both variables set
        mock_get.side_effect = lambda key, default=None: {
            'SPOTIPY_CLIENT_ID': 'test_id',
            'SPOTIPY_CLIENT_SECRET': 'test_secret'
        }.get(key, default)

        result = EnvironmentManager.validate_environment()
        # Should return truthy value (the SPOTIPY_CLIENT_SECRET)
        assert result == 'test_secret'

        # Test with missing client ID
        mock_get.side_effect = lambda key, default=None: {
            'SPOTIPY_CLIENT_ID': None,
            'SPOTIPY_CLIENT_SECRET': 'test_secret'
        }.get(key, default)

        result = EnvironmentManager.validate_environment()
        # Should return falsy value (None)
        assert result is None

        # Test with missing client secret
        mock_get.side_effect = lambda key, default=None: {
            'SPOTIPY_CLIENT_ID': 'test_id',
            'SPOTIPY_CLIENT_SECRET': None
        }.get(key, default)

        result = EnvironmentManager.validate_environment()
        # Should return falsy value (None)
        assert result is None


@patch('src.utils.MediaFile')
def test_get_mbid_from_tags_success(mock_mediafile):
    """Test successful MBID retrieval from tags."""
    mock_file = MagicMock()
    mock_file.mb_trackid = "test_mbid"
    mock_mediafile.return_value = mock_file

    mbid = get_mbid_from_tags("/test/file.flac")
    assert mbid == "test_mbid"


@patch('src.utils.MediaFile')
def test_get_mbid_from_tags_failure(mock_mediafile):
    """Test failed MBID retrieval from tags."""
    mock_mediafile.side_effect = UnreadableFileError("/test/file.flac", "corrupt file")

    mbid = get_mbid_from_tags("/test/file.flac")
    assert mbid is None


@patch('src.utils.MediaFile')
def test_write_mbid_to_file_success(mock_mediafile):
    """Test successful MBID writing to file."""
    mock_file = MagicMock()
    mock_mediafile.return_value = mock_file

    result = write_mbid_to_file("/test/file.flac", "test_mbid")
    assert result is True
    mock_file.save.assert_called_once()


@patch('src.utils.MediaFile')
def test_write_mbid_to_file_failure(mock_mediafile):
    """Test failed MBID writing to file."""
    mock_file = MagicMock()
    mock_file.save.side_effect = OSError("Save error")
    mock_mediafile.return_value = mock_file

    result = write_mbid_to_file("/test/file.flac", "test_mbid")
    assert result is False


@patch('src.utils.get_config')
@patch('src.utils.acoustid')
@patch('src.utils.write_mbid_to_file')
def test_find_mbid_by_fingerprint_success(mock_write, mock_acoustid, mock_get_config):
    """Test successful MBID finding by fingerprint."""
    # Setup mocks
    mock_config = MagicMock()
    mock_config.get.return_value = "test_api_key"
    mock_get_config.return_value = mock_config

    mock_acoustid.match.return_value = [(0.9, "test_mbid", "Test Title", "Test Artist")]
    mock_write.return_value = True

    result = find_mbid_by_fingerprint("/test/file.flac")
    assert result == "test_mbid"


@patch('src.utils.get_config')
def test_find_mbid_by_fingerprint_no_api_key(mock_get_config):
    """Test MBID finding with no API key."""
    # Setup mock
    mock_config = MagicMock()
    mock_config.get.return_value = None  # No API key
    mock_get_config.return_value = mock_config

    result = find_mbid_by_fingerprint("/test/file.flac")
    assert result is None


@patch('src.utils.get_config')
@patch('src.utils.acoustid')
def test_find_mbid_by_fingerprint_no_match(mock_acoustid, mock_get_config):
    """Test MBID finding with no match."""
    # Setup mocks
    mock_config = MagicMock()
    mock_config.get.return_value = "test_api_key"
    mock_get_config.return_value = mock_config

    mock_acoustid.match.return_value = []  # No matches

    result = find_mbid_by_fingerprint("/test/file.flac")
    assert result is None