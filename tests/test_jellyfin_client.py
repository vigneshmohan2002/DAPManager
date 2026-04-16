import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.jellyfin_client import JellyfinClient


@pytest.fixture
def jf_client(tmp_path):
    db = DatabaseManager(":memory:")
    client = JellyfinClient(
        db=db,
        base_url="http://jf.local:8096",
        api_key="key",
        user_id="uid",
        music_library_path=str(tmp_path),
    )
    yield client
    db.close()


def _jf_item(
    item_id="i1",
    mbid="mb1",
    title="Song",
    artist="Artist",
    album="Album",
    container="flac",
    codec="flac",
    bitrate=1_000_000,
    sample_rate=44100,
    bit_depth=16,
):
    return {
        "Id": item_id,
        "Name": title,
        "AlbumArtist": artist,
        "Album": album,
        "IndexNumber": 1,
        "ParentIndexNumber": 1,
        "ProviderIds": {"MusicBrainzTrack": mbid} if mbid else {},
        "MediaSources": [
            {
                "Container": container,
                "MediaStreams": [
                    {
                        "Type": "Audio",
                        "Codec": codec,
                        "BitRate": bitrate,
                        "SampleRate": sample_rate,
                        "BitDepth": bit_depth,
                    }
                ],
            }
        ],
    }


def test_should_pull_when_no_local_track(jf_client):
    assert jf_client._should_pull(_jf_item(), None) is True


def test_should_pull_when_local_file_missing(jf_client, tmp_path):
    missing = str(tmp_path / "gone.flac")
    track = Track(mbid="mb1", title="t", artist="a", local_path=missing)
    assert jf_client._should_pull(_jf_item(), track) is True


def test_should_pull_when_jellyfin_strictly_better(jf_client, tmp_path):
    # Local file exists; we mock out MediaFile-based inspection to look worse.
    existing = tmp_path / "file.mp3"
    existing.write_bytes(b"")
    track = Track(mbid="mb1", title="t", artist="a", local_path=str(existing))

    with patch.object(
        JellyfinClient,
        "_local_stream",
        return_value={
            "Codec": "mp3",
            "BitRate": 192_000,
            "SampleRate": 44100,
            "BitDepth": 0,
        },
    ):
        assert jf_client._should_pull(_jf_item(), track) is True


def test_should_not_pull_when_local_equal_or_better(jf_client, tmp_path):
    existing = tmp_path / "file.flac"
    existing.write_bytes(b"")
    track = Track(mbid="mb1", title="t", artist="a", local_path=str(existing))

    with patch.object(
        JellyfinClient,
        "_local_stream",
        return_value={
            "Codec": "flac",
            "BitRate": 1_000_000,
            "SampleRate": 44100,
            "BitDepth": 16,
        },
    ):
        assert jf_client._should_pull(_jf_item(), track) is False


def test_extract_mbid_from_provider_ids():
    item = _jf_item(mbid="abc-123")
    assert JellyfinClient._extract_mbid(item) == "abc-123"


def test_extract_mbid_returns_none_when_missing():
    item = _jf_item(mbid=None)
    assert JellyfinClient._extract_mbid(item) is None


def test_destination_path_sanitizes_and_uses_container(jf_client, tmp_path):
    item = _jf_item(
        title="So/ng", artist="Ar:tist", album="Al*bum", container="flac"
    )
    path = jf_client._destination_path(item)
    assert path.startswith(str(tmp_path))
    assert "Ar_tist" in path
    assert "Al_bum" in path
    assert path.endswith("So_ng.flac")


def test_pull_all_registers_tracks_and_mirrors_playlists(tmp_path):
    db = DatabaseManager(":memory:")
    client = JellyfinClient(
        db=db,
        base_url="http://jf.local:8096",
        api_key="key",
        user_id="uid",
        music_library_path=str(tmp_path),
    )

    audio_item = _jf_item(item_id="jf-audio-1", mbid="mb-abc", title="Hit")
    playlist = {"Id": "pl-1", "Name": "My Playlist"}
    playlist_entry = {
        "Id": "jf-audio-1",
        "ProviderIds": {"MusicBrainzTrack": "mb-abc"},
    }

    def fake_list_audio_items():
        return [audio_item]

    def fake_list_playlists():
        return [playlist]

    def fake_list_playlist_items(pid):
        assert pid == "pl-1"
        return [playlist_entry]

    def fake_stream(path, dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(b"fakeaudio")

    with patch.object(client, "_list_audio_items", side_effect=fake_list_audio_items), \
         patch.object(client, "_list_playlists", side_effect=fake_list_playlists), \
         patch.object(client, "_list_playlist_items", side_effect=fake_list_playlist_items), \
         patch.object(client, "_stream_to_file", side_effect=fake_stream), \
         patch("src.jellyfin_client.write_mbid_to_file", return_value=True):
        summary = client.pull_all(mirror_playlists=True)

    assert summary["items_seen"] == 1
    assert summary["pulled"] == 1
    assert summary["failed"] == 0
    assert summary["playlists_mirrored"] == 1

    track = db.get_track_by_mbid("mb-abc")
    assert track is not None
    assert track.title == "Hit"
    assert track.local_path and os.path.exists(track.local_path)

    playlists = db.get_all_playlists()
    assert any(p.playlist_id == "jf:pl-1" for p in playlists)
    linked = db.get_playlist_tracks("jf:pl-1")
    assert [t.mbid for t in linked] == ["mb-abc"]

    db.close()


def test_pull_all_skips_items_without_mbid(tmp_path):
    db = DatabaseManager(":memory:")
    client = JellyfinClient(
        db=db,
        base_url="http://jf.local:8096",
        api_key="key",
        user_id="uid",
        music_library_path=str(tmp_path),
    )

    no_mbid = _jf_item(item_id="x", mbid=None)

    with patch.object(client, "_list_audio_items", return_value=[no_mbid]), \
         patch.object(client, "_list_playlists", return_value=[]), \
         patch.object(client, "_stream_to_file") as stream_mock:
        summary = client.pull_all(mirror_playlists=True)

    stream_mock.assert_not_called()
    assert summary["pulled"] == 0
    assert summary["skipped"] == 1

    db.close()
