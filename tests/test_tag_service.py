"""Tests for src.tag_service — identify (fingerprint+lookup) and write_tags."""

import os
from unittest.mock import MagicMock, patch

import pytest
from mutagen.flac import FLAC, StreamInfo

from src import tag_service


# ---------------------------------------------------------------------------
# _tier
# ---------------------------------------------------------------------------

def test_tier_green_at_threshold():
    assert tag_service._tier(0.90) == "green"
    assert tag_service._tier(0.99) == "green"


def test_tier_yellow_between():
    assert tag_service._tier(0.50) == "yellow"
    assert tag_service._tier(0.89) == "yellow"


def test_tier_red_below_half():
    assert tag_service._tier(0.49) == "red"
    assert tag_service._tier(0.0) == "red"


# ---------------------------------------------------------------------------
# identify_file
# ---------------------------------------------------------------------------

def test_identify_file_returns_none_without_api_key(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    assert tag_service.identify_file(str(f), api_key="") is None


def test_identify_file_returns_none_when_file_missing():
    assert tag_service.identify_file("/nonexistent", api_key="k") is None


def test_identify_file_returns_none_when_acoustid_empty(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(120.0, "FINGERPRINT")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value={"status": "ok", "results": []}):
            result = tag_service.identify_file(str(f), api_key="k")
    assert result is None


def test_identify_file_assembles_candidate_on_success(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")

    acoustid_result = {
        "status": "ok",
        "results": [
            {
                "score": 0.97,
                "recordings": [
                    {
                        "id": "rec-1",
                        "releases": [{"id": "rel-1"}],
                    }
                ],
            }
        ],
    }
    mb_release = {
        "release": {
            "title": "The Album",
            "date": "2020-01-01",
            "artist-credit": [{"artist": {"name": "The Artist"}}],
            "medium-list": [
                {
                    "position": 1,
                    "track-list": [
                        {
                            "number": "3",
                            "recording": {"id": "rec-1", "title": "The Song"},
                        }
                    ],
                }
            ],
        }
    }

    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(180.0, "FP")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value=acoustid_result):
            with patch("src.tag_service.mb.get_release_by_id",
                       return_value=mb_release):
                with patch("src.tag_service.mb.configure"):
                    with patch("src.tag_service.read_current_tags",
                               return_value={"title": "old title"}):
                        candidate = tag_service.identify_file(str(f), api_key="k")

    assert candidate is not None
    assert candidate["score"] == 0.97
    assert candidate["tier"] == "green"
    assert candidate["meta"]["artist"] == "The Artist"
    assert candidate["meta"]["title"] == "The Song"
    assert candidate["meta"]["album"] == "The Album"
    assert candidate["meta"]["mbid"] == "rec-1"
    assert candidate["meta"]["release_mbid"] == "rel-1"
    assert candidate["current"] == {"title": "old title"}


def test_identify_file_none_when_recording_has_no_releases(tmp_path):
    f = tmp_path / "x.flac"
    f.write_bytes(b"dummy")
    with patch("src.tag_service.acoustid.fingerprint_file",
               return_value=(120.0, "FP")):
        with patch("src.tag_service.acoustid.lookup",
                   return_value={"results": [
                       {"score": 0.9, "recordings": [{"id": "r1", "releases": []}]}
                   ]}):
            result = tag_service.identify_file(str(f), api_key="k")
    assert result is None


# ---------------------------------------------------------------------------
# write_tags — format coverage
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_flac(tmp_path):
    """Write a minimal, empty-tagged FLAC so we can exercise the writer."""
    # We can't synthesize a real FLAC without audio data, but we can take a
    # shortcut: use mutagen to write a FLAC header and rely on the writer
    # operating on tags only. A byte-identical playable file isn't needed.
    path = tmp_path / "t.flac"
    # Minimal FLAC magic + metadata block. Easiest: copy from a fixture if
    # one exists; otherwise, skip this test on CI. We'll use a small
    # bundled-on-the-fly FLAC via mutagen's low-level API.
    # For safety, mark as xfail if we can't build one.
    path.write_bytes(b"")  # placeholder; see test_write_tags_flac below
    return str(path)


def _minimal_flac(path):
    """Write just enough FLAC bytes that mutagen can open/tag it.

    STREAMINFO needs a non-zero sample rate or mutagen.flac rejects it.
    We pack 44100 Hz, 1 channel, 16 bps, 0 total samples — bit-packed
    per the FLAC spec's STREAMINFO layout.
    """
    data = bytearray()
    data += b"fLaC"
    # Last-block flag | type 0 (STREAMINFO), length 34
    data += bytes([0x80, 0x00, 0x00, 0x22])
    # min/max block size (2 bytes each), min/max frame size (3 bytes each)
    data += (4096).to_bytes(2, "big")
    data += (4096).to_bytes(2, "big")
    data += (0).to_bytes(3, "big")
    data += (0).to_bytes(3, "big")
    # Pack: sample_rate (20 bits) | channels-1 (3 bits) | bps-1 (5 bits) | total_samples (36 bits)
    sample_rate = 44100
    channels_minus_1 = 0  # 1 channel
    bps_minus_1 = 15  # 16 bps
    total_samples = 0
    packed = (sample_rate << 44) | (channels_minus_1 << 41) | (bps_minus_1 << 36) | total_samples
    data += packed.to_bytes(8, "big")
    # MD5 (16 bytes)
    data += b"\x00" * 16
    with open(path, "wb") as f:
        f.write(data)


def test_write_tags_flac(tmp_path):
    path = tmp_path / "t.flac"
    _minimal_flac(str(path))

    meta = {
        "title": "New Title",
        "artist": "New Artist",
        "album": "New Album",
        "album_artist": "New Artist",
        "date": "2024",
        "track_number": "5",
        "disc_number": "1",
        "mbid": "rec-abc",
        "release_mbid": "rel-xyz",
    }
    container = tag_service.write_tags(str(path), meta)
    assert container == "flac"

    audio = FLAC(str(path))
    assert audio["title"] == ["New Title"]
    assert audio["artist"] == ["New Artist"]
    assert audio["album"] == ["New Album"]
    assert audio["tracknumber"] == ["5"]
    assert audio["musicbrainz_trackid"] == ["rec-abc"]
    assert audio["musicbrainz_albumid"] == ["rel-xyz"]


def test_write_tags_unsupported_extension_raises(tmp_path):
    path = tmp_path / "t.wav"
    path.write_bytes(b"")
    with pytest.raises(ValueError, match="unsupported"):
        tag_service.write_tags(str(path), {"title": "x"})


# ---------------------------------------------------------------------------
# read_current_tags
# ---------------------------------------------------------------------------

def test_read_current_tags_missing_file_returns_empty():
    assert tag_service.read_current_tags("/nonexistent/file.flac") == {}


def test_read_current_tags_returns_values_after_write(tmp_path):
    path = tmp_path / "t.flac"
    _minimal_flac(str(path))
    tag_service.write_tags(str(path), {
        "title": "Song", "artist": "A", "album": "B",
        "date": "", "track_number": "", "disc_number": "",
        "mbid": "m1", "release_mbid": "",
    })
    tags = tag_service.read_current_tags(str(path))
    assert tags["title"] == "Song"
    assert tags["artist"] == "A"
    assert tags["album"] == "B"
    assert tags["mbid"] == "m1"
