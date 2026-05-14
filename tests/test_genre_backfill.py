from unittest.mock import patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.genre_backfill import (
    _best_artist_match,
    _extract_tags,
    backfill_artist_tags,
)


@pytest.fixture
def db():
    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


# --- _extract_tags ---------------------------------------------------------

def test_extract_tags_coerces_string_counts():
    """MB's response shape varies — count comes through as a string
    via the XML-shaped library path. Coerce so the DB sees ints."""
    payload = {
        "artist": {
            "tag-list": [
                {"name": "indie rock", "count": "10"},
                {"name": "rock", "count": "5"},
            ]
        }
    }
    tags = _extract_tags(payload)
    assert tags == [
        {"tag": "indie rock", "weight": 10},
        {"tag": "rock", "weight": 5},
    ]


def test_extract_tags_defaults_missing_count_to_one():
    payload = {"artist": {"tag-list": [{"name": "obscure"}]}}
    tags = _extract_tags(payload)
    assert tags == [{"tag": "obscure", "weight": 1}]


def test_extract_tags_drops_empty_names_and_handles_missing_list():
    payload = {"artist": {"tag-list": [{"name": "", "count": 5}]}}
    assert _extract_tags(payload) == []
    assert _extract_tags({"artist": {}}) == []
    assert _extract_tags({}) == []


# --- _best_artist_match ---------------------------------------------------

def test_best_match_accepts_exact_name_case_insensitive():
    res = {"artist-list": [{"id": "mb-1", "name": "The Beatles", "ext:score": "50"}]}
    pick = _best_artist_match(res, "the beatles")
    assert pick is not None
    assert pick["id"] == "mb-1"


def test_best_match_accepts_high_score_inexact():
    res = {"artist-list": [{"id": "mb-2", "name": "Beatles", "ext:score": "95"}]}
    pick = _best_artist_match(res, "the beatles")
    assert pick is not None
    assert pick["id"] == "mb-2"


def test_best_match_rejects_low_score_inexact():
    """Wrong-artist tags would poison Daily Mix clustering for the
    user's entire library — refuse rather than guess."""
    res = {"artist-list": [{"id": "mb-3", "name": "Bedles", "ext:score": "70"}]}
    assert _best_artist_match(res, "Beatles") is None


def test_best_match_empty_payload():
    assert _best_artist_match({}, "anything") is None
    assert _best_artist_match({"artist-list": []}, "anything") is None


# --- backfill_artist_tags --------------------------------------------------

def test_backfill_walks_distinct_artists_and_persists_top_tags(db):
    db.add_or_update_track(Track(mbid="m1", title="T1", artist="Beatles"))
    db.add_or_update_track(Track(mbid="m2", title="T2", artist="Stones"))

    def fake_search(name, limit=3):
        # Both names match exactly — MB returns the canonical hit.
        return {"artist-list": [{"id": f"mb-{name}", "name": name, "ext:score": "100"}]}

    def fake_tags(mbid):
        # Return distinct tag sets so the test can verify the right
        # mbid -> tags mapping was used.
        if "Beatles" in mbid:
            return {"artist": {"tag-list": [
                {"name": "indie pop", "count": "8"},
                {"name": "rock", "count": "10"},
            ]}}
        return {"artist": {"tag-list": [
            {"name": "blues rock", "count": "12"},
        ]}}

    with patch("src.genre_backfill.mb.search_artists", side_effect=fake_search), \
         patch("src.genre_backfill.mb.get_artist_tags", side_effect=fake_tags):
        summary = backfill_artist_tags(db)

    assert summary["processed"] == 2
    assert summary["matched"] == 2
    assert summary["tagged"] == 3  # 2 for Beatles + 1 for Stones

    # Spot-check the persisted shape — top tag first.
    beatles = db.get_top_tags_for_artist("Beatles")
    assert beatles[0]["tag"] == "rock"
    stones = db.get_top_tags_for_artist("Stones")
    assert [r["tag"] for r in stones] == ["blues rock"]


def test_backfill_records_empty_row_on_no_match_so_incremental_skips(db):
    """Without the empty-row marker, an un-matchable name (typo,
    one-off feature credit, dead band) would be re-queried on every
    pass — wasting MB's rate-limited budget forever."""
    db.add_or_update_track(Track(mbid="m1", title="T", artist="Wxq"))

    with patch(
        "src.genre_backfill.mb.search_artists",
        return_value={"artist-list": []},
    ), patch("src.genre_backfill.mb.get_artist_tags"):
        summary = backfill_artist_tags(db)

    assert summary["no_match"] == 1
    # The empty marker is in the DB so the next incremental pass skips.
    assert db.get_artists_needing_tags(max_age_days=30) == []


def test_backfill_continues_after_individual_artist_error(db):
    db.add_or_update_track(Track(mbid="m1", title="A", artist="Good"))
    db.add_or_update_track(Track(mbid="m2", title="B", artist="Broken"))

    def fake_search(name, limit=3):
        if name == "Broken":
            raise RuntimeError("MB 5xx")
        return {"artist-list": [{"id": "mb-good", "name": "Good", "ext:score": "100"}]}

    with patch("src.genre_backfill.mb.search_artists", side_effect=fake_search), \
         patch(
            "src.genre_backfill.mb.get_artist_tags",
            return_value={"artist": {"tag-list": [{"name": "x", "count": "1"}]}},
         ):
        summary = backfill_artist_tags(db)

    assert summary["matched"] == 1
    assert summary["errors"] == 1
    # The unbroken artist still got tagged.
    assert len(db.get_top_tags_for_artist("Good")) == 1


def test_backfill_emits_progress_per_artist(db):
    db.add_or_update_track(Track(mbid="m1", title="A", artist="X"))
    db.add_or_update_track(Track(mbid="m2", title="B", artist="Y"))

    messages = []

    def cb(payload):
        messages.append(payload)

    with patch(
        "src.genre_backfill.mb.search_artists",
        return_value={"artist-list": []},
    ), patch("src.genre_backfill.mb.get_artist_tags"):
        backfill_artist_tags(db, progress_callback=cb)

    # One opening message + one per-artist + one closing.
    assert any("Backfilling tags (1/2)" in m["message"] for m in messages)
    assert any("Backfilling tags (2/2)" in m["message"] for m in messages)
    assert any("done" in m["message"] for m in messages)


def test_backfill_no_artists_returns_zero_summary_without_mb_calls(db):
    """Library is empty; backfill must not even spin up the MB
    session — the 1-req/sec rate limit isn't free."""
    with patch("src.genre_backfill.mb.search_artists") as search:
        summary = backfill_artist_tags(db)
    search.assert_not_called()
    assert summary == {
        "processed": 0, "matched": 0, "no_match": 0,
        "errors": 0, "tagged": 0,
    }
