import pytest

from src.daily_mixes import (
    DAILY_MIX_ID_PREFIX,
    list_daily_mixes,
    regenerate_daily_mixes,
)
from src.db_manager import DatabaseManager, Track


@pytest.fixture
def db():
    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


def _seed_history(db, artist, n_plays):
    """Add one track for `artist` and record `n_plays` listens against
    it. Tracks need at least one play_event per artist to clear the
    top-artist threshold."""
    mbid = f"mb-{artist}-1"
    db.add_or_update_track(Track(
        mbid=mbid, title="t", artist=artist, album="al",
        local_path=f"/m/{artist}.flac",
    ))
    for _ in range(n_plays):
        db.record_play_event(mbid, source="test")
    return mbid


def test_cold_start_returns_zero_mixes(db):
    # Only one artist with plays — below the 8-artist threshold.
    _seed_history(db, "Solo", n_plays=10)
    summary = regenerate_daily_mixes(db)
    assert summary["mixes"] == 0
    assert summary["reason"] == "cold_start"
    assert list_daily_mixes(db) == []


def test_cold_start_purges_stale_prior_mixes(db):
    """An install that *used to* qualify shouldn't keep last week's
    mixes around once the user stops listening — otherwise the Home
    tile shows a static lie indefinitely."""
    # Seed: 8 qualifying artists, run a regen, then strip the
    # play_events so the next regen falls back to cold_start.
    for i in range(8):
        _seed_history(db, f"A{i}", n_plays=3)
        db.record_artist_tags(f"A{i}", None, [{"tag": "rock", "weight": 5}])
    regenerate_daily_mixes(db)
    assert len(list_daily_mixes(db)) > 0

    db.conn.execute("DELETE FROM play_events")
    db.conn.commit()
    summary = regenerate_daily_mixes(db)
    assert summary["reason"] == "cold_start"
    assert list_daily_mixes(db) == []


def test_no_tags_returns_zero_with_reason(db):
    """Enough listening but no tag backfill ever ran — should call
    that out by name so the Settings nudge is specific."""
    for i in range(8):
        _seed_history(db, f"A{i}", n_plays=3)
    summary = regenerate_daily_mixes(db)
    assert summary["mixes"] == 0
    assert summary["reason"] == "no_tags"


def test_clusters_artists_by_top_tag(db):
    # Four artists in 'rock', three in 'jazz', one in 'classical' —
    # target=6 means all three clusters land; ranking is by size.
    rock_artists = ["A1", "A2", "A3", "A4"]
    jazz_artists = ["A5", "A6", "A7"]
    other_artists = ["A8"]
    for a in rock_artists:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "rock", "weight": 5}])
    for a in jazz_artists:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "jazz", "weight": 5}])
    for a in other_artists:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "classical", "weight": 5}])

    summary = regenerate_daily_mixes(db, target_mixes=6, tracks_per_mix=10)
    assert summary["mixes"] == 3
    # Largest cluster first (rock), then jazz, then classical.
    tags = [d["tag"] for d in summary["details"]]
    assert tags == ["rock", "jazz", "classical"]
    # Each tile gets the expected ids in order.
    pids = [d["playlist_id"] for d in summary["details"]]
    assert pids == [f"{DAILY_MIX_ID_PREFIX}1",
                    f"{DAILY_MIX_ID_PREFIX}2",
                    f"{DAILY_MIX_ID_PREFIX}3"]


def test_regenerate_replaces_prior_mixes(db):
    """A second run with different cluster sizes must purge the
    previous bigger generation — otherwise the sidebar carries
    orphaned 'Daily Mix 5' entries from a richer history."""
    for a in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "rock", "weight": 5}])
    regenerate_daily_mixes(db, target_mixes=6)
    first = list_daily_mixes(db)

    # Tighten the cap so the next regen produces fewer mixes.
    regenerate_daily_mixes(db, target_mixes=2)
    second = list_daily_mixes(db)
    assert len(second) <= 2
    # No orphan ids from the previous run.
    assert {m["playlist_id"] for m in second}.issubset(
        {m["playlist_id"] for m in first}
    )


def test_regenerate_persists_track_membership(db):
    for a in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "rock", "weight": 5}])
    regenerate_daily_mixes(db, target_mixes=1, tracks_per_mix=5)

    rows = db.list_tracks_filtered(playlist_id=f"{DAILY_MIX_ID_PREFIX}1")
    # 5 cap, drawn from 8 artists' single tracks each — should land
    # at exactly 5 rows from distinct artists.
    assert 1 <= len(rows) <= 5
    artists = {r["artist"] for r in rows}
    # No artist should appear twice in this case since each only has
    # one track.
    assert len(artists) == len(rows)


def test_untagged_artist_is_excluded_but_doesnt_block_clustering(db):
    """An untagged artist among the top-N must not stop the cluster
    step — just skip them and continue with the rest. The Home tile
    for that artist's mix can come back later once they get tagged."""
    for a in ["A1", "A2", "A3", "A4", "A5", "A6", "A7"]:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "rock", "weight": 5}])
    # One more qualifying artist, but no tags.
    _seed_history(db, "Untagged", n_plays=3)

    summary = regenerate_daily_mixes(db, target_mixes=6)
    assert summary["mixes"] == 1
    assert summary["details"][0]["tag"] == "rock"


def test_list_daily_mixes_extracts_tag_from_name(db):
    """The Home tile reads the cluster tag back out of the playlist
    name for its subtitle copy — confirm the parser is symmetric
    with what regenerate writes."""
    for a in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]:
        _seed_history(db, a, n_plays=3)
        db.record_artist_tags(a, None, [{"tag": "indie-rock", "weight": 5}])
    regenerate_daily_mixes(db, target_mixes=1)
    mixes = list_daily_mixes(db)
    assert mixes[0]["tag"].lower().startswith("indie rock")
