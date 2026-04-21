import os
import sqlite3
import tempfile

import pytest
from src.db_manager import Track, DownloadItem, DatabaseManager, Playlist

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


def test_list_albums_groups_by_release_mbid(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="Artist A", album="Album One",
        local_path="/m/A/One/01.flac", release_mbid="rmb-1",
    ))
    db.add_or_update_track(Track(
        mbid="t2", title="S2", artist="Artist A", album="Album One",
        local_path="/m/A/One/02.flac", release_mbid="rmb-1",
    ))
    albums = db.list_albums()
    assert len(albums) == 1
    assert albums[0]["id"] == "rmb-1"
    assert albums[0]["title"] == "Album One"
    assert albums[0]["artist"] == "Artist A"
    assert albums[0]["track_count"] == 2
    assert albums[0]["cover_path"] in ("/m/A/One/01.flac", "/m/A/One/02.flac")


def test_list_albums_synthesizes_id_when_no_mbid(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="X", album="Y",
        local_path="/p/1.flac",  # no release_mbid
    ))
    albums = db.list_albums()
    assert len(albums) == 1
    assert albums[0]["id"] == "Y|X"


def test_list_albums_skips_tracks_without_album(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="X", album="",
        local_path="/p/1.flac",
    ))
    assert db.list_albums() == []


def test_list_artists_groups_and_counts(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="Alpha", album="A1",
        local_path="/m/a/1.flac", release_mbid="rmb-a1",
    ))
    db.add_or_update_track(Track(
        mbid="t2", title="S2", artist="Alpha", album="A1",
        local_path="/m/a/2.flac", release_mbid="rmb-a1",
    ))
    db.add_or_update_track(Track(
        mbid="t3", title="S3", artist="Alpha", album="A2",
        local_path="/m/a/3.flac", release_mbid="rmb-a2",
    ))
    db.add_or_update_track(Track(
        mbid="t4", title="S4", artist="Bravo", album="B1",
        local_path="/m/b/1.flac", release_mbid="rmb-b1",
    ))
    artists = db.list_artists()
    by_name = {a["name"]: a for a in artists}
    assert by_name["Alpha"]["album_count"] == 2
    assert by_name["Alpha"]["track_count"] == 3
    assert by_name["Bravo"]["album_count"] == 1
    assert by_name["Bravo"]["track_count"] == 1
    # Sort is case-insensitive alphabetical.
    assert [a["name"] for a in artists] == ["Alpha", "Bravo"]


def test_list_artists_skips_tracks_without_album(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="Single", artist="Loose", album="",
        local_path="/m/loose.flac",
    ))
    assert db.list_artists() == []


def test_get_album_cover_path_by_mbid_and_synthetic(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="Artist A", album="Album One",
        local_path="/m/A/One/01.flac", release_mbid="rmb-1",
    ))
    db.add_or_update_track(Track(
        mbid="t2", title="S2", artist="Artist B", album="Album Two",
        local_path="/m/B/Two/01.flac",
    ))
    assert db.get_album_cover_path("rmb-1") == "/m/A/One/01.flac"
    assert db.get_album_cover_path("Album Two|Artist B") == "/m/B/Two/01.flac"
    assert db.get_album_cover_path("") is None
    assert db.get_album_cover_path("nope") is None


def test_list_album_tracks_orders_by_disc_then_track(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t-b", title="B side", artist="A", album="Alb",
        local_path="/p/b.flac", release_mbid="rmb", disc_number=1, track_number=2,
    ))
    db.add_or_update_track(Track(
        mbid="t-a", title="A side", artist="A", album="Alb",
        local_path="/p/a.flac", release_mbid="rmb", disc_number=1, track_number=1,
    ))
    db.add_or_update_track(Track(
        mbid="t-d", title="D2 first", artist="A", album="Alb",
        local_path="/p/d.flac", release_mbid="rmb", disc_number=2, track_number=1,
    ))
    rows = db.list_album_tracks("rmb")
    assert [r["mbid"] for r in rows] == ["t-a", "t-b", "t-d"]


def test_list_album_tracks_accepts_synthetic_id(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="Art", album="Alb",
        local_path="/p/1.flac",
    ))
    rows = db.list_album_tracks("Alb|Art")
    assert len(rows) == 1 and rows[0]["mbid"] == "t1"


def test_list_album_tracks_skips_missing_local_path(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S1", artist="Art", album="Alb",
        local_path="/p/1.flac", release_mbid="rmb",
    ))
    db.add_or_update_track(Track(
        mbid="t2", title="S2", artist="Art", album="Alb",
        local_path=None, release_mbid="rmb",
    ))
    rows = db.list_album_tracks("rmb")
    assert [r["mbid"] for r in rows] == ["t1"]


def test_list_album_tracks_returns_empty_for_blank_id(db):
    assert db.list_album_tracks("") == []


def test_get_track_local_path(db):
    from src.db_manager import Track
    db.add_or_update_track(Track(
        mbid="t1", title="S", artist="A", album="Al",
        local_path="/music/s.flac",
    ))
    assert db.get_track_local_path("t1") == "/music/s.flac"
    assert db.get_track_local_path("nope") is None
    assert db.get_track_local_path("") is None


def test_has_queued_mbid(db):
    assert db.has_queued_mbid("mbid-x") is False
    assert db.has_queued_mbid("") is False

    db.queue_download(DownloadItem(
        search_query="A - B", playlist_id="", mbid_guess="mbid-x", status="pending",
    ))
    assert db.has_queued_mbid("mbid-x") is True

    # Still True after status flips — release_watcher must not re-queue
    # an album that's already been attempted.
    row = db.get_all_downloads()[0]
    db.update_download_status(row.id, "failed")
    assert db.has_queued_mbid("mbid-x") is True
    db.update_download_status(row.id, "success")
    assert db.has_queued_mbid("mbid-x") is True


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


def _playlist_updated_at(db, pid):
    return db.conn.execute(
        "SELECT updated_at FROM playlists WHERE playlist_id = ?", (pid,)
    ).fetchone()["updated_at"]


def test_add_or_update_playlist_preserves_membership(db):
    """ON CONFLICT DO UPDATE must not cascade-wipe playlist_tracks."""
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="List", spotify_url=""))
    db.link_track_to_playlist("p1", "t1", 0)
    assert len(db.get_playlist_tracks("p1")) == 1

    # Re-upsert the same playlist (e.g. name change) — membership must survive.
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="Renamed", spotify_url=""))
    assert len(db.get_playlist_tracks("p1")) == 1


def test_get_playlist_tracks_local_only_filters_ghost_rows(db):
    """local_only=True must hide rows pulled from the master that this
    device doesn't actually have on disk (local_path IS NULL)."""
    db.add_or_update_track(
        Track(mbid="have", title="T1", artist="A", local_path="/music/a.flac")
    )
    db.add_or_update_track(Track(mbid="ghost", title="T2", artist="A"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="Mix", spotify_url=""))
    db.link_track_to_playlist("p1", "have", 0)
    db.link_track_to_playlist("p1", "ghost", 1)

    assert {t.mbid for t in db.get_playlist_tracks("p1")} == {"have", "ghost"}
    assert {t.mbid for t in db.get_playlist_tracks("p1", local_only=True)} == {"have"}


def test_add_or_update_playlist_sets_and_bumps_updated_at(db):
    import time

    db.add_or_update_playlist(Playlist(playlist_id="p1", name="A", spotify_url=""))
    first = _playlist_updated_at(db, "p1")
    assert first is not None
    time.sleep(1.1)
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="B", spotify_url=""))
    second = _playlist_updated_at(db, "p1")
    assert second > first


def test_link_track_bumps_playlist_updated_at_on_new_membership(db):
    import time

    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="L", spotify_url=""))
    before = _playlist_updated_at(db, "p1")
    time.sleep(1.1)
    db.link_track_to_playlist("p1", "t1", 0)
    after = _playlist_updated_at(db, "p1")
    assert after > before

    # Re-linking the same track (IGNORE) should NOT bump further.
    time.sleep(1.1)
    db.link_track_to_playlist("p1", "t1", 0)
    assert _playlist_updated_at(db, "p1") == after


def test_get_playlists_since_returns_delta_with_tracks(db):
    import time

    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_track(Track(mbid="t2", title="T2", artist="B"))
    db.add_or_update_playlist(Playlist(playlist_id="p_old", name="Old", spotify_url=""))
    db.link_track_to_playlist("p_old", "t1", 0)

    cutoff = db.conn.execute("SELECT CURRENT_TIMESTAMP AS t").fetchone()["t"]
    time.sleep(1.1)

    db.add_or_update_playlist(Playlist(playlist_id="p_new", name="New", spotify_url=""))
    db.link_track_to_playlist("p_new", "t2", 0)

    delta = db.get_playlists_since(cutoff)
    ids = {pl["playlist_id"] for pl in delta}
    assert ids == {"p_new"}

    new_pl = delta[0]
    assert new_pl["name"] == "New"
    assert new_pl["tracks"] == [{"track_mbid": "t2", "track_order": 0}]


def test_get_playlists_since_no_filter_returns_all(db):
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="A", spotify_url=""))
    db.add_or_update_playlist(Playlist(playlist_id="p2", name="B", spotify_url=""))
    rows = db.get_playlists_since()
    assert {r["playlist_id"] for r in rows} == {"p1", "p2"}
    for r in rows:
        assert "tracks" in r
        assert "updated_at" in r


def test_playlists_updated_at_migration_on_legacy_db():
    import tempfile
    import os
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "legacy.db")
        conn = sqlite3.connect(db_path)
        # Pre-updated_at playlists schema.
        conn.execute("""
            CREATE TABLE playlists (
                playlist_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                spotify_url TEXT
            )
        """)
        conn.execute(
            "INSERT INTO playlists (playlist_id, name, spotify_url) VALUES (?, ?, ?)",
            ("p1", "Legacy", ""),
        )
        conn.commit()
        conn.close()

        mgr = DatabaseManager(db_path)
        try:
            cols = {
                row[1]
                for row in mgr.conn.execute("PRAGMA table_info(playlists)").fetchall()
            }
            assert "updated_at" in cols
            row = mgr.conn.execute(
                "SELECT updated_at FROM playlists WHERE playlist_id = ?", ("p1",)
            ).fetchone()
            assert row["updated_at"] is not None
        finally:
            mgr.close()


def test_apply_playlist_row_inserts_with_membership(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_track(Track(mbid="t2", title="T2", artist="A"))
    action = db.apply_playlist_row({
        "playlist_id": "p1",
        "name": "Remote",
        "spotify_url": "http://x",
        "updated_at": "2026-04-18 12:00:00",
        "tracks": [
            {"track_mbid": "t1", "track_order": 0},
            {"track_mbid": "t2", "track_order": 1},
        ],
    })
    assert action == "inserted"
    ts = db.get_playlist_tracks("p1")
    assert [t.mbid for t in ts] == ["t1", "t2"]
    # Master's updated_at should land on the row verbatim.
    assert _playlist_updated_at(db, "p1") == "2026-04-18 12:00:00"


def test_apply_playlist_row_replaces_membership_on_update(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.add_or_update_track(Track(mbid="t2", title="T2", artist="A"))
    db.add_or_update_track(Track(mbid="t3", title="T3", artist="A"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="Local", spotify_url=""))
    db.link_track_to_playlist("p1", "t1", 0)
    db.link_track_to_playlist("p1", "t2", 1)

    # Master says the playlist now only has t3 in it.
    action = db.apply_playlist_row({
        "playlist_id": "p1",
        "name": "Local",
        "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00",
        "tracks": [{"track_mbid": "t3", "track_order": 0}],
    })
    assert action == "updated"
    assert [t.mbid for t in db.get_playlist_tracks("p1")] == ["t3"]


def test_apply_playlist_row_skips_unknown_track_mbids(db):
    # Known track present, unknown one referenced.
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    action = db.apply_playlist_row({
        "playlist_id": "p1",
        "name": "X",
        "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00",
        "tracks": [
            {"track_mbid": "t1", "track_order": 0},
            {"track_mbid": "nonexistent", "track_order": 1},
        ],
    })
    assert action == "inserted"
    # FK constraint drops the unknown one (INSERT OR IGNORE falls through).
    assert [t.mbid for t in db.get_playlist_tracks("p1")] == ["t1"]


def test_apply_playlist_row_skips_missing_playlist_id(db):
    assert db.apply_playlist_row({"name": "x"}) == "skipped"
    assert db.apply_playlist_row({}) == "skipped"


def test_soft_delete_track_marks_and_bumps(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    assert db.soft_delete_track("t1") is True
    rows = db.get_catalog_since()
    row = next(r for r in rows if r["mbid"] == "t1")
    assert row["deleted_at"] is not None
    # Repeat soft-delete is a no-op (already deleted).
    assert db.soft_delete_track("t1") is False


def test_soft_delete_track_hidden_by_default(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A", local_path="/a"))
    db.soft_delete_track("t1")
    assert db.get_all_tracks() == []
    assert db.get_all_tracks(local_only=True) == []
    assert [t.mbid for t in db.get_all_tracks(include_orphans=True)] == ["t1"]


def test_restore_track_clears_deleted_at(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    db.soft_delete_track("t1")
    assert db.restore_track("t1") is True
    assert [t.mbid for t in db.get_all_tracks()] == ["t1"]
    # Restore on a non-deleted track is a no-op.
    assert db.restore_track("t1") is False


def test_catalog_delta_carries_deletion(db):
    db.add_or_update_track(Track(mbid="t1", title="T", artist="A"))
    first_cursor = db.get_catalog_since()[-1]["updated_at"]
    import time; time.sleep(1.1)
    db.soft_delete_track("t1")
    delta = db.get_catalog_since(first_cursor)
    assert len(delta) == 1
    assert delta[0]["mbid"] == "t1"
    assert delta[0]["deleted_at"] is not None


def test_apply_catalog_row_propagates_deleted_at(db):
    db.apply_catalog_row({
        "mbid": "t1", "title": "T", "artist": "A",
        "updated_at": "2026-04-18 12:00:00",
    })
    assert [t.mbid for t in db.get_all_tracks()] == ["t1"]
    # Master later sends a soft-delete.
    db.apply_catalog_row({
        "mbid": "t1", "title": "T", "artist": "A",
        "updated_at": "2026-04-18 13:00:00",
        "deleted_at": "2026-04-18 13:00:00",
    })
    assert db.get_all_tracks() == []
    assert [t.mbid for t in db.get_all_tracks(include_orphans=True)] == ["t1"]


def test_soft_delete_playlist_hidden_by_default(db):
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="X", spotify_url=""))
    assert [p.playlist_id for p in db.get_all_playlists()] == ["p1"]
    assert db.soft_delete_playlist("p1") is True
    assert db.get_all_playlists() == []
    assert [p.playlist_id for p in db.get_all_playlists(include_orphans=True)] == ["p1"]


def test_get_playlist_tracks_hides_deleted_tracks_by_default(db):
    db.add_or_update_track(Track(mbid="t1", title="A", artist="X", local_path="/a"))
    db.add_or_update_track(Track(mbid="t2", title="B", artist="X", local_path="/b"))
    db.add_or_update_playlist(Playlist(playlist_id="p1", name="Mix", spotify_url=""))
    db.link_track_to_playlist("p1", "t1", 0)
    db.link_track_to_playlist("p1", "t2", 1)
    db.soft_delete_track("t2")
    assert [t.mbid for t in db.get_playlist_tracks("p1")] == ["t1"]
    assert {t.mbid for t in db.get_playlist_tracks("p1", include_orphans=True)} == {"t1", "t2"}


def test_apply_playlist_row_propagates_deleted_at(db):
    db.apply_playlist_row({
        "playlist_id": "p1", "name": "Mix", "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00", "tracks": [],
    })
    assert [p.playlist_id for p in db.get_all_playlists()] == ["p1"]
    db.apply_playlist_row({
        "playlist_id": "p1", "name": "Mix", "spotify_url": "",
        "updated_at": "2026-04-18 13:00:00",
        "deleted_at": "2026-04-18 13:00:00",
        "tracks": [],
    })
    assert db.get_all_playlists() == []


def test_apply_pushed_playlist_row_inserts_when_absent(db):
    action = db.apply_pushed_playlist_row({
        "playlist_id": "p1",
        "name": "From Sat",
        "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00",
        "tracks": [],
    })
    assert action == "inserted"


def test_apply_pushed_playlist_row_accepts_newer(db):
    db.apply_playlist_row({
        "playlist_id": "p1", "name": "old", "spotify_url": "",
        "updated_at": "2026-04-18 10:00:00", "tracks": [],
    })
    action = db.apply_pushed_playlist_row({
        "playlist_id": "p1", "name": "new", "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00", "tracks": [],
    })
    assert action == "updated"
    row = db.conn.execute(
        "SELECT name, updated_at FROM playlists WHERE playlist_id = 'p1'"
    ).fetchone()
    assert row["name"] == "new"
    assert row["updated_at"] == "2026-04-18 12:00:00"


def test_apply_pushed_playlist_row_rejects_older(db):
    db.apply_playlist_row({
        "playlist_id": "p1", "name": "current", "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00", "tracks": [],
    })
    action = db.apply_pushed_playlist_row({
        "playlist_id": "p1", "name": "stale", "spotify_url": "",
        "updated_at": "2026-04-18 10:00:00", "tracks": [],
    })
    assert action == "stale"
    row = db.conn.execute(
        "SELECT name FROM playlists WHERE playlist_id = 'p1'"
    ).fetchone()
    assert row["name"] == "current"


def test_apply_pushed_playlist_row_rejects_equal_timestamp(db):
    """Equal timestamps are treated as stale — the master keeps its copy.
    This is what protects round-tripped pulls from overwriting a master
    edit that happened to land at the same timestamp."""
    db.apply_playlist_row({
        "playlist_id": "p1", "name": "current", "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00", "tracks": [],
    })
    action = db.apply_pushed_playlist_row({
        "playlist_id": "p1", "name": "echo", "spotify_url": "",
        "updated_at": "2026-04-18 12:00:00", "tracks": [],
    })
    assert action == "stale"


def test_apply_pushed_playlist_row_without_playlist_id(db):
    assert db.apply_pushed_playlist_row({}) == "skipped"


def test_replace_device_inventory_writes_rows(db):
    written = db.replace_device_inventory("dev-A", [
        {"mbid": "m1", "local_path": "/music/m1.flac"},
        {"mbid": "m2", "local_path": "/music/m2.flac"},
    ])
    assert written == 2
    inv = db.get_device_inventory("dev-A")
    assert {r["mbid"] for r in inv} == {"m1", "m2"}


def test_replace_device_inventory_replaces_previous(db):
    db.replace_device_inventory("dev-A", [{"mbid": "m1", "local_path": "/old"}])
    db.replace_device_inventory("dev-A", [{"mbid": "m2", "local_path": "/new"}])
    inv = db.get_device_inventory("dev-A")
    assert len(inv) == 1
    assert inv[0]["mbid"] == "m2"


def test_replace_device_inventory_isolates_devices(db):
    db.replace_device_inventory("dev-A", [{"mbid": "m1", "local_path": "/a"}])
    db.replace_device_inventory("dev-B", [{"mbid": "m2", "local_path": "/b"}])
    # Replacing A must not touch B.
    db.replace_device_inventory("dev-A", [])
    assert db.get_device_inventory("dev-A") == []
    assert len(db.get_device_inventory("dev-B")) == 1


def test_replace_device_inventory_skips_bad_items(db):
    written = db.replace_device_inventory("dev-A", [
        {"mbid": "m1", "local_path": "/a"},
        {"local_path": "/no-mbid"},  # missing mbid
        "not a dict",
        {"mbid": ""},  # empty mbid
    ])
    assert written == 1


def test_replace_device_inventory_requires_device_id(db):
    with pytest.raises(ValueError):
        db.replace_device_inventory("", [{"mbid": "m1"}])


def test_get_fleet_summary_groups_by_device(db):
    db.replace_device_inventory("dev-A", [
        {"mbid": "m1", "local_path": "/a/1"},
        {"mbid": "m2", "local_path": "/a/2"},
    ])
    db.replace_device_inventory("dev-B", [{"mbid": "m1", "local_path": "/b/1"}])
    summary = db.get_fleet_summary()
    by_id = {row["device_id"]: row for row in summary}
    assert by_id["dev-A"]["track_count"] == 2
    assert by_id["dev-B"]["track_count"] == 1
    assert by_id["dev-A"]["last_reported_at"] is not None


def test_get_fleet_summary_empty(db):
    assert db.get_fleet_summary() == []


def test_get_devices_holding_mbid(db):
    db.replace_device_inventory("dev-A", [{"mbid": "m1", "local_path": "/a"}])
    db.replace_device_inventory("dev-B", [{"mbid": "m1", "local_path": "/b"}])
    db.replace_device_inventory("dev-C", [{"mbid": "m2", "local_path": "/c"}])
    holders = db.get_devices_holding_mbid("m1")
    assert {row["device_id"] for row in holders} == {"dev-A", "dev-B"}
    assert db.get_devices_holding_mbid("missing") == []


def test_find_tracks_for_fleet_search_ranks_by_device_count(db):
    db.add_or_update_track(Track(mbid="m_common", title="Common Song", artist="Artist"))
    db.add_or_update_track(Track(mbid="m_rare", title="Common Song Rare", artist="Artist"))
    db.replace_device_inventory("dev-A", [{"mbid": "m_common", "local_path": "/a"}])
    db.replace_device_inventory("dev-B", [{"mbid": "m_common", "local_path": "/b"}])
    db.replace_device_inventory("dev-C", [{"mbid": "m_rare", "local_path": "/c"}])
    hits = db.find_tracks_for_fleet_search("common")
    assert [h["mbid"] for h in hits[:2]] == ["m_common", "m_rare"]
    assert hits[0]["device_count"] == 2
    assert hits[1]["device_count"] == 1


def test_find_tracks_for_fleet_search_empty_query(db):
    assert db.find_tracks_for_fleet_search("") == []


def test_sync_state_roundtrip(db):
    assert db.get_sync_state("last_catalog_sync") is None
    db.set_sync_state("last_catalog_sync", "2026-04-17 12:00:00")
    assert db.get_sync_state("last_catalog_sync") == "2026-04-17 12:00:00"
    # overwrite
    db.set_sync_state("last_catalog_sync", "2026-04-18 09:00:00")
    assert db.get_sync_state("last_catalog_sync") == "2026-04-18 09:00:00"


def test_set_track_tag_tier_updates_row(db):
    db.add_or_update_track(Track(mbid="m1", title="t", artist="a", local_path="/p"))
    assert db.set_track_tag_tier("m1", "yellow", 0.72) is True
    got = db.get_track_by_mbid("m1")
    assert got.tag_tier == "yellow"
    assert got.tag_score == pytest.approx(0.72)


def test_set_track_tag_tier_missing_mbid_returns_false(db):
    assert db.set_track_tag_tier("nope", "green", 0.99) is False


def test_add_or_update_preserves_tag_tier_when_re_upserted(db):
    # Downloader stamps tier...
    db.add_or_update_track(Track(mbid="m1", title="t", artist="a", local_path="/p"))
    db.set_track_tag_tier("m1", "green", 0.95)
    # ...then a library rescan reinserts the same track without knowing the tier.
    db.add_or_update_track(Track(mbid="m1", title="t2", artist="a", local_path="/p"))
    got = db.get_track_by_mbid("m1")
    assert got.title == "t2"            # other fields do update
    assert got.tag_tier == "green"      # but the tier survives
    assert got.tag_score == pytest.approx(0.95)


def test_add_or_update_overwrites_tag_tier_when_provided(db):
    db.add_or_update_track(Track(mbid="m1", title="t", artist="a", local_path="/p"))
    db.set_track_tag_tier("m1", "yellow", 0.6)
    # If a caller *does* pass a new tier (e.g. manual review confirms green),
    # it should take effect.
    db.add_or_update_track(Track(
        mbid="m1", title="t", artist="a", local_path="/p",
        tag_tier="green", tag_score=0.97,
    ))
    got = db.get_track_by_mbid("m1")
    assert got.tag_tier == "green"
    assert got.tag_score == pytest.approx(0.97)


def test_get_tracks_needing_tag_review_filters_to_yellow_and_red(db):
    db.add_or_update_track(Track(mbid="g", title="g", artist="a", local_path="/g"))
    db.add_or_update_track(Track(mbid="y", title="y", artist="a", local_path="/y"))
    db.add_or_update_track(Track(mbid="r", title="r", artist="a", local_path="/r"))
    db.add_or_update_track(Track(mbid="n", title="n", artist="a", local_path="/n"))
    db.set_track_tag_tier("g", "green", 0.95)
    db.set_track_tag_tier("y", "yellow", 0.72)
    db.set_track_tag_tier("r", "red", 0.3)
    # 'n' left without a tier

    flagged = db.get_tracks_needing_tag_review()
    mbids = {t.mbid for t in flagged}
    assert mbids == {"y", "r"}


def test_get_tracks_needing_tag_review_excludes_soft_deleted(db):
    db.add_or_update_track(Track(mbid="y", title="y", artist="a", local_path="/y"))
    db.set_track_tag_tier("y", "yellow", 0.6)
    db.soft_delete_track("y")
    assert db.get_tracks_needing_tag_review() == []


def test_get_tracks_needing_tag_review_excludes_tracks_without_local_file(db):
    # Catalog-only rows (no local_path) aren't reviewable by the user.
    db.add_or_update_track(Track(mbid="y", title="y", artist="a"))
    db.set_track_tag_tier("y", "yellow", 0.6)
    assert db.get_tracks_needing_tag_review() == []
