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

            # Existing row data survives the rename.
            track = mgr.get_track_by_mbid("mb1")
            assert track is not None
            assert track.dap_path == "/old/ipod/song.flac"
            assert track.synced_to_dap is True
        finally:
            mgr.close()
