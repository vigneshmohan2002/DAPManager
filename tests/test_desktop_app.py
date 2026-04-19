"""Tests for pure helpers in desktop_app (no Qt event loop required)."""

from PySide6.QtCore import QModelIndex, Qt

from desktop_app import (
    TAG_TIER_COLORS,
    TrackTableModel,
    compute_delete_paths,
    format_incomplete_album,
    parse_manual_suggestions,
    parse_playlist_urls,
    tracks_to_suggestions,
)
from src.db_manager import Track


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


def _track(mbid="", artist="", title=""):
    return Track(mbid=mbid, artist=artist, title=title, album="", local_path=None)


def test_tracks_to_suggestions_prefers_mbid():
    items = tracks_to_suggestions([_track(mbid="m1", artist="A", title="T")])
    assert items == [{"mbid": "m1", "artist": "A", "title": "T"}]


def test_tracks_to_suggestions_falls_back_to_artist_title():
    items = tracks_to_suggestions([_track(artist="A", title="T")])
    assert items == [{"artist": "A", "title": "T"}]


def test_tracks_to_suggestions_skips_unsalvageable_rows():
    items = tracks_to_suggestions([
        _track(),                   # nothing usable
        _track(artist="A"),         # title missing
        _track(mbid="m1"),          # mbid alone is fine
    ])
    assert items == [{"mbid": "m1"}]


def test_tracks_to_suggestions_dedupes_by_mbid():
    items = tracks_to_suggestions([
        _track(mbid="m1", artist="A", title="T"),
        _track(mbid="m1", artist="A", title="T"),
    ])
    assert len(items) == 1


def test_parse_manual_suggestions_splits_artist_title():
    items = parse_manual_suggestions("Radiohead - Idioteque\nPortishead - Roads")
    assert items == [
        {"artist": "Radiohead", "title": "Idioteque"},
        {"artist": "Portishead", "title": "Roads"},
    ]


def test_parse_manual_suggestions_free_form_falls_through_to_query():
    items = parse_manual_suggestions("just a song name")
    assert items == [{"search_query": "just a song name"}]


def test_parse_manual_suggestions_ignores_blank_and_comments():
    items = parse_manual_suggestions("\n# comment\nA - B\n   ")
    assert items == [{"artist": "A", "title": "B"}]


def _tier_track(tier=None, score=None):
    return Track(mbid="m", title="t", artist="a", tag_tier=tier, tag_score=score)


def test_track_model_tag_column_blank_when_no_tier():
    model = TrackTableModel([_tier_track()])
    idx = model.index(0, TrackTableModel.COL_TAG)
    assert model.data(idx, Qt.DisplayRole) == ""


def test_track_model_tag_column_dot_with_tier_colour():
    model = TrackTableModel([
        _tier_track(tier="green", score=0.97),
        _tier_track(tier="yellow", score=0.7),
        _tier_track(tier="red", score=0.3),
    ])
    for row, tier in enumerate(("green", "yellow", "red")):
        idx = model.index(row, TrackTableModel.COL_TAG)
        assert model.data(idx, Qt.DisplayRole) == "●"
        colour = model.data(idx, Qt.ForegroundRole)
        # QColor.name() gives "#rrggbb"; compare case-insensitively.
        assert colour is not None
        assert colour.name().lower() == TAG_TIER_COLORS[tier].lower()


def test_track_model_tag_column_tooltip_flags_non_green():
    model = TrackTableModel([_tier_track(tier="yellow", score=0.72)])
    idx = model.index(0, TrackTableModel.COL_TAG)
    tooltip = model.data(idx, Qt.ToolTipRole)
    assert "yellow" in tooltip.lower()
    assert "0.72" in tooltip
