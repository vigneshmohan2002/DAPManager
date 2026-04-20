"""Tests for src.cover_art.extract_cover."""

from unittest.mock import MagicMock, patch

from src.cover_art import extract_cover


def test_returns_none_for_empty_path():
    assert extract_cover("") is None
    assert extract_cover(None) is None  # type: ignore[arg-type]


def test_returns_none_when_mutagen_cant_open():
    with patch("mutagen.File", return_value=None):
        assert extract_cover("/nope.mp3") is None


def test_extracts_flac_picture():
    pic = MagicMock()
    pic.data = b"FLACBYTES"
    pic.mime = "image/jpeg"
    audio = MagicMock()
    audio.pictures = [pic]
    with patch("mutagen.File", return_value=audio):
        result = extract_cover("/a.flac")
    assert result == (b"FLACBYTES", "image/jpeg")


def test_extracts_mp3_apic():
    apic = MagicMock()
    apic.data = b"MP3BYTES"
    apic.mime = "image/png"
    tags = {"APIC:cover": apic}
    audio = MagicMock()
    audio.pictures = None
    audio.tags = tags
    with patch("mutagen.File", return_value=audio):
        result = extract_cover("/a.mp3")
    assert result == (b"MP3BYTES", "image/png")


def test_extracts_mp4_covr_jpeg():
    class FakeMP4Cover(bytes):
        imageformat = 13  # FORMAT_JPEG

    cover = FakeMP4Cover(b"MP4BYTES")
    tags = MagicMock()
    tags.keys.return_value = []
    tags.get.return_value = [cover]
    audio = MagicMock()
    audio.pictures = None
    audio.tags = tags
    with patch("mutagen.File", return_value=audio):
        result = extract_cover("/a.m4a")
    assert result == (b"MP4BYTES", "image/jpeg")


def test_returns_none_when_no_art_anywhere():
    tags = MagicMock()
    tags.keys.return_value = []
    tags.get.return_value = None
    audio = MagicMock()
    audio.pictures = None
    audio.tags = tags
    with patch("mutagen.File", return_value=audio):
        assert extract_cover("/a.flac") is None
