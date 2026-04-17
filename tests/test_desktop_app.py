"""Tests for pure helpers in desktop_app (no Qt event loop required)."""

from desktop_app import parse_playlist_urls


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
