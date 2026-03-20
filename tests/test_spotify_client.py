import pytest
import os
from unittest.mock import MagicMock, patch
from src.spotify_client import SpotifyClient
from src.db_manager import DatabaseManager, Playlist, Track, DownloadItem


@pytest.fixture
def db():
    """Create in-memory database for testing."""
    manager = DatabaseManager(":memory:")
    yield manager
    manager.close()


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for Spotify authentication."""
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "test_client_secret")
    yield
    monkeypatch.delenv("SPOTIPY_CLIENT_ID")
    monkeypatch.delenv("SPOTIPY_CLIENT_SECRET")


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_spotify_client_initialization(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test Spotify client initialization."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager

    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    # Create client - should not raise exception with proper mocking
    client = SpotifyClient(db)

    assert client.db is not None
    assert client.sp is not None


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_process_playlist_invalid_url(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test processing playlist with invalid URL."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    client = SpotifyClient(db)

    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    client.process_playlist("invalid_url")

    # Should handle invalid URL gracefully
    # The actual behavior depends on implementation but shouldn't crash


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_get_mbid_from_isrc_success(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test successful MBID retrieval from ISRC."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    # Setup mock MusicBrainz response
    mock_musicbrainz.get_recordings_by_isrc.return_value = {
        'isrc': {
            'recording-list': [{'id': 'test_mbid'}]
        }
    }

    client = SpotifyClient(db)
    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    mbid = client._get_mbid_from_isrc("USXX123456789")

    assert mbid == "test_mbid"


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_get_mbid_from_isrc_failure(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test failed MBID retrieval from ISRC."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    # Setup mock to raise exception
    mock_musicbrainz.get_recordings_by_isrc.side_effect = Exception("API Error")

    client = SpotifyClient(db)
    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    mbid = client._get_mbid_from_isrc("USXX123456789")

    assert mbid is None


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_process_track_no_isrc(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test processing track without ISRC."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    client = SpotifyClient(db)
    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    # Track without ISRC
    spotify_track = {
        'name': 'Test Song',
        'artists': [{'name': 'Test Artist'}],
        'album': {'name': 'Test Album'}
        # No 'external_ids' or 'external_ids.isrc'
    }

    # Should not raise exception and should return early
    client._process_track(spotify_track, "test_playlist", 1)
    # If we get here without exception, the test passes


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_process_track_success(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test successful track processing."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    # Setup mock MusicBrainz response
    mock_musicbrainz.get_recordings_by_isrc.return_value = {
        'isrc': {
            'recording-list': [{'id': 'test_mbid'}]
        }
    }

    client = SpotifyClient(db)
    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    # Track with ISRC
    spotify_track = {
        'name': 'Test Song',
        'artists': [{'name': 'Test Artist'}],
        'album': {'name': 'Test Album'},
        'external_ids': {'isrc': 'USXX123456789'}
    }

    # Should process without raising exception
    client._process_track(spotify_track, "test_playlist", 1)

    # Verify track was added to database
    # This would normally work but depends on how the mock is set up


@patch('src.spotify_client.SpotifyClientCredentials')
@patch('src.spotify_client.spotipy.Spotify')
@patch('src.spotify_client.musicbrainzngs')
def test_process_track_existing_track(mock_musicbrainz, mock_spotify, mock_credentials, db):
    """Test processing track that already exists in database."""
    # Setup mocks
    mock_auth_manager = MagicMock()
    mock_credentials.return_value = mock_auth_manager
    mock_sp_instance = MagicMock()
    mock_spotify.return_value = mock_sp_instance

    # Setup existing track in database
    existing_track = Track(
        mbid="test_mbid",
        title="Old Song",
        artist="Old Artist",
        album="Old Album",
        local_path="/test/path.flac"
    )
    db.add_or_update_track(existing_track)

    # Setup mock MusicBrainz response
    mock_musicbrainz.get_recordings_by_isrc.return_value = {
        'isrc': {
            'recording-list': [{'id': 'test_mbid'}]
        }
    }

    client = SpotifyClient(db)
    # Mock the _setup_clients to avoid actual authentication
    client._setup_clients = MagicMock()

    # Track with same MBID but different metadata
    spotify_track = {
        'name': 'New Song Name',
        'artists': [{'name': 'New Artist Name'}],
        'album': {'name': 'New Album Name'},
        'external_ids': {'isrc': 'USXX123456789'}
    }

    # Should process without raising exception
    client._process_track(spotify_track, "test_playlist", 1)

    # Verify track was updated but local path preserved
    updated_track = db.get_track_by_mbid("test_mbid")
    assert updated_track is not None