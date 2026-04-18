import os
import sqlite3
import tempfile

import pytest
from src.db_manager import Track, DownloadItem, DatabaseManager

def test_add_and_get_track(db):
    t = Track(
        mbid="12345",
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        local_path="/music/Test Artist/Test Album/01 Test Song.flac"
    )
    db.add_or_update_track(t)
    
    fetched = db.get_track_by_mbid("12345")
    assert fetched is not None
    assert fetched.title == "Test Song"
    assert fetched.artist == "Test Artist"
    
def test_search_tracks(db):
    t1 = Track(mbid="1", title="Hit Song", artist="Pop Star", album="Greatest Hits")
    t2 = Track(mbid="2", title="Obscure Song", artist="Indie Band", album="Garage Demo")
    db.add_or_update_track(t1)
    db.add_or_update_track(t2)
    
    # Search by Title
    results = db.search_tracks("Hit")
    assert len(results) == 1
    assert results[0].mbid == "1"
    
    # Search by Artist
    results = db.search_tracks("Indie")
    assert len(results) == 1
    assert results[0].artist == "Indie Band"
    
    # Search should be case-insensitive (LIMITATION: standard SQLite LOOKUP is usually case-insensitive for ASCII, 
    # but strictly depends on collation. Using LIKE is usually case-insensitive.)
    results = db.search_tracks("pop star")
    assert len(results) > 0

def test_library_stats(db):
    db.add_or_update_track(Track(mbid="1", title="A", artist="Art1", album="Alb1", release_mbid="r1", track_number=1, local_path="/a"))
    db.add_or_update_track(Track(mbid="2", title="B", artist="Art1", album="Alb1", release_mbid="r1", track_number=2, local_path="/b"))
    
    # Set Metadata saying album has 10 tracks
    db.update_album_metadata("r1", "Alb1", 10)
    
    stats = db.get_library_stats()
    assert stats['tracks'] == 2
    assert stats['artists'] == 1
    assert stats['albums'] == 1
    assert stats['incomplete_albums'] == 1 # We have 2 tracks, but total is 10

def test_download_queue(db):
    item = DownloadItem(
        search_query="foo bar",
        playlist_id="plist1",
        mbid_guess="guess1",
        status="pending"
    )
    db.queue_download(item)
    
    queue = db.get_all_downloads()
    assert len(queue) == 1
    assert queue[0].search_query == "foo bar"
    assert queue[0].status == "pending"
    
    # Update status
    db.update_download_status(queue[0].id, "success")
    queue = db.get_all_downloads()
    assert queue[0].status == "success"


def test_schema_migration_renames_ipod_columns():
    """Opening an old-schema DB should rename ipod_path/synced_to_ipod → dap_*."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "legacy.db")

        # Hand-build the pre-rename tracks table.
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE tracks (
                mbid TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                album TEXT,
                isrc TEXT,
                local_path TEXT UNIQUE,
                ipod_path TEXT,
                synced_to_ipod INTEGER DEFAULT 0,
                release_mbid TEXT,
                track_number INTEGER,
                disc_number INTEGER
            )
        """)
        conn.execute(
            "INSERT INTO tracks (mbid, title, artist, ipod_path, synced_to_ipod) "
            "VALUES (?, ?, ?, ?, ?)",
            ("mb1", "Song", "Artist", "/old/ipod/song.flac", 1),
        )
        conn.commit()
        conn.close()

        # Opening via DatabaseManager should migrate.
        mgr = DatabaseManager(db_path)
        try:
            cols = {
                row[1]
                for row in mgr.conn.execute("PRAGMA table_info(tracks)").fetchall()
            }
            assert "ipod_path" not in cols
            assert "synced_to_ipod" not in cols
            assert "dap_path" in cols
            assert "synced_to_dap" in cols
            # Legacy DBs gain updated_at and get backfilled with current time
            assert "updated_at" in cols
            row = mgr.conn.execute(
                "SELECT updated_at FROM tracks WHERE mbid = ?", ("mb1",)
            ).fetchone()
            assert row["updated_at"] is not None

            # Existing row data survives the rename.
            track = mgr.get_track_by_mbid("mb1")
            assert track is not None
            assert track.dap_path == "/old/ipod/song.flac"
            assert track.synced_to_dap is True
        finally:
            mgr.close()


def _catalog_row(db, mbid):
    return db.conn.execute(
        "SELECT updated_at FROM tracks WHERE mbid = ?", (mbid,)
    ).fetchone()["updated_at"]


def test_add_or_update_track_sets_updated_at(db):
    db.add_or_update_track(Track(mbid="u1", title="S", artist="A"))
    assert _catalog_row(db, "u1") is not None


def test_add_or_update_track_bumps_updated_at_on_rewrite(db):
    import time

    db.add_or_update_track(Track(mbid="u1", title="S", artist="A"))
    first = _catalog_row(db, "u1")
    # SQLite CURRENT_TIMESTAMP resolution is 1s — wait enough to see a tick
    time.sleep(1.1)
    db.add_or_update_track(Track(mbid="u1", title="S2", artist="A"))
    second = _catalog_row(db, "u1")
    assert second > first


def test_get_catalog_since_returns_delta(db):
    import time

    db.add_or_update_track(Track(mbid="c1", title="Old", artist="A"))
    cutoff = db.conn.execute("SELECT CURRENT_TIMESTAMP AS t").fetchone()["t"]
    time.sleep(1.1)
    db.add_or_update_track(Track(mbid="c2", title="New", artist="B"))

    delta = db.get_catalog_since(cutoff)
    mbids = {row["mbid"] for row in delta}
    assert mbids == {"c2"}


def test_get_catalog_since_no_filter_returns_all(db):
    db.add_or_update_track(Track(mbid="c1", title="T", artist="A"))
    db.add_or_update_track(Track(mbid="c2", title="T", artist="B"))
    rows = db.get_catalog_since()
    assert {r["mbid"] for r in rows} == {"c1", "c2"}


def test_get_catalog_since_omits_local_only_fields(db):
    db.add_or_update_track(Track(
        mbid="c1", title="T", artist="A", local_path="/x.flac", dap_path="/y.flac",
    ))
    rows = db.get_catalog_since()
    assert "local_path" not in rows[0]
    assert "dap_path" not in rows[0]
    assert "synced_to_dap" not in rows[0]


def test_apply_catalog_row_inserts_new_track(db):
    action = db.apply_catalog_row({
        "mbid": "a1",
        "title": "Catalog Song",
        "artist": "Catalog Artist",
        "album": "Catalog Album",
        "isrc": "ISRC01",
        "release_mbid": "r1",
        "track_number": 3,
        "disc_number": 1,
        "updated_at": "2026-04-17 12:00:00",
    })
    assert action == "inserted"
    track = db.get_track_by_mbid("a1")
    assert track is not None
    assert track.title == "Catalog Song"
    assert track.local_path is None  # catalog row has no device presence


def test_apply_catalog_row_preserves_local_path(db):
    db.add_or_update_track(Track(
        mbid="a1",
        title="Stale Title",
        artist="Stale Artist",
        local_path="/music/a1.flac",
        dap_path="/dap/a1.flac",
        synced_to_dap=True,
    ))
    action = db.apply_catalog_row({
        "mbid": "a1",
        "title": "Fresh Title",
        "artist": "Fresh Artist",
        "updated_at": "2026-04-17 13:00:00",
    })
    assert action == "updated"
    track = db.get_track_by_mbid("a1")
    assert track.title == "Fresh Title"
    assert track.local_path == "/music/a1.flac"
    assert track.dap_path == "/dap/a1.flac"
    assert track.synced_to_dap is True


def test_apply_catalog_row_skips_when_mbid_missing(db):
    assert db.apply_catalog_row({"title": "No MBID"}) == "skipped"
    assert db.apply_catalog_row({}) == "skipped"


def test_sync_state_roundtrip(db):
    assert db.get_sync_state("last_catalog_sync") is None
    db.set_sync_state("last_catalog_sync", "2026-04-17 12:00:00")
    assert db.get_sync_state("last_catalog_sync") == "2026-04-17 12:00:00"
    # overwrite
    db.set_sync_state("last_catalog_sync", "2026-04-18 09:00:00")
    assert db.get_sync_state("last_catalog_sync") == "2026-04-18 09:00:00"
