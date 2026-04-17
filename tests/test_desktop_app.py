"""Tests for pure helpers in desktop_app (no Qt event loop required)."""

from desktop_app import (
    compute_delete_paths,
    format_incomplete_album,
    parse_playlist_urls,
)


def test_parse_playlist_urls_splits_and_trims():
    text = "  https://a\nhttps://b \n"
    assert parse_playlist_urls(text) == ["https://a", "https://b"]


def test_parse_playlist_urls_ignores_blank_and_comment_lines():
    text = "https://a\n\n# a comment\n   \nhttps://b"
    assert parse_playlist_urls(text) == ["https://a", "https://b"]


def test_parse_playlist_urls_dedupes_preserving_order():
    text = "https://a\nhttps://b\nhttps://a\nhttps://c"
    assert parse_playlist_urls(text) == ["https://a", "https://b", "https://c"]


def test_parse_playlist_urls_handles_empty_input():
    assert parse_playlist_urls("") == []
    assert parse_playlist_urls("\n\n\n") == []


def test_format_incomplete_album_full_fields():
    row = format_incomplete_album(
        {"artist": "Radiohead", "album": "OK Computer", "have": 10, "total": 12, "missing": 2}
    )
    assert row == "Radiohead — OK Computer  (10/12, 2 missing)"


def test_format_incomplete_album_missing_defaults_from_totals():
    row = format_incomplete_album({"artist": "A", "album": "B", "have": 3, "total": 5})
    assert "(3/5, 2 missing)" in row


def test_format_incomplete_album_handles_missing_metadata():
    row = format_incomplete_album({})
    assert "Unknown Artist" in row
    assert "Unknown Album" in row


def test_compute_delete_paths_excludes_keep():
    paths = ["/a.flac", "/b.flac", "/c.flac"]
    assert compute_delete_paths("/b.flac", paths) == ["/a.flac", "/c.flac"]


def test_compute_delete_paths_skip_returns_empty():
    paths = ["/a.flac", "/b.flac"]
    assert compute_delete_paths("", paths) == []
    assert compute_delete_paths(None, paths) == []


def test_compute_delete_paths_keep_not_in_list_deletes_all():
    paths = ["/a.flac", "/b.flac"]
    assert compute_delete_paths("/other.flac", paths) == ["/a.flac", "/b.flac"]
