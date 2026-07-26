import logging
import sqlite3
import threading

import src.db_schema as db_schema
from src.db_schema import create_tables, migrate_schema


def _columns(conn: sqlite3.Connection, table: str):
    return tuple(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))


def test_schema_functions_are_idempotent_and_preserve_existing_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    logger = logging.getLogger("test.db_schema")

    create_tables(conn, logger)
    migrate_schema(conn, logger)
    conn.execute(
        "INSERT INTO tracks (mbid, title, artist, local_path) "
        "VALUES ('track-1', 'Song', 'Artist', '/music/song.flac')"
    )
    conn.commit()

    create_tables(conn, logger)
    migrate_schema(conn, logger)

    row = conn.execute(
        "SELECT title, artist, local_path FROM tracks WHERE mbid = 'track-1'"
    ).fetchone()
    assert dict(row) == {
        "title": "Song",
        "artist": "Artist",
        "local_path": "/music/song.flac",
    }
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert indexes == {
        "idx_album_download_request_tracks_recording",
        "idx_album_download_requests_queue_item",
        "idx_album_download_requests_stage_updated",
        "idx_artist_tags_tag",
        "idx_download_queue_claimable",
        "idx_play_events_played_at",
        "idx_play_events_track_mbid",
        "idx_tracks_is_liked",
    }
    conn.close()


def test_schema_module_migrates_legacy_download_queue_idempotently():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE download_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_query TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending', 'failed', 'success')),
            last_attempt TIMESTAMP,
            mbid_guess TEXT NOT NULL
        );
        INSERT INTO download_queue (
            search_query, playlist_id, status, mbid_guess
        ) VALUES ('Artist - Album', 'SATELLITE_ALBUM', 'failed', 'release-1');
        INSERT INTO download_queue (
            search_query, playlist_id, status, mbid_guess
        ) VALUES ('New - Album', 'SATELLITE_ALBUM', 'pending', 'release-2');
        """
    )
    logger = logging.getLogger("test.db_schema.download_queue")

    create_tables(conn, logger)
    migrate_schema(conn, logger)
    migrate_schema(conn, logger)

    assert _columns(conn, "download_queue") == (
        "id",
        "search_query",
        "playlist_id",
        "status",
        "last_attempt",
        "mbid_guess",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "claim_owner",
        "claim_expires_at",
        "claim_heartbeat_at",
        "is_paused",
        "is_quarantined",
        "last_error",
    )
    row = conn.execute(
        "SELECT * FROM download_queue WHERE status = 'failed'"
    ).fetchone()
    assert row["status"] == "failed"
    assert row["attempt_count"] == 0
    assert row["max_attempts"] == 3
    assert row["is_paused"] == 0
    assert row["is_quarantined"] == 1
    assert row["claim_owner"] is None
    assert row["next_attempt_at"] is None
    assert row["last_error"] is None
    pending = conn.execute(
        "SELECT * FROM download_queue WHERE status = 'pending'"
    ).fetchone()
    assert pending["is_quarantined"] == 0
    assert pending["attempt_count"] == 0
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }
    assert "idx_download_queue_claimable" in indexes
    conn.close()


def test_concurrent_connections_serialize_legacy_download_queue_migration(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "concurrent-legacy.db"
    logger = logging.getLogger("test.db_schema.concurrent")

    # Build every current table/index first, then downgrade only download_queue
    # to the exact legacy shape involved in the production startup race.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    create_tables(conn, logger)
    migrate_schema(conn, logger)
    conn.execute("DROP INDEX idx_download_queue_claimable")
    conn.execute("DROP TABLE download_queue")
    conn.executescript(
        """
        CREATE TABLE download_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_query TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending', 'failed', 'success')),
            last_attempt TIMESTAMP,
            mbid_guess TEXT NOT NULL
        );
        INSERT INTO download_queue (
            search_query, playlist_id, status, mbid_guess
        ) VALUES ('Artist - Album', 'SATELLITE_ALBUM', 'failed', 'release-1');
        """
    )
    conn.close()

    first_rechecking_under_lock = threading.Event()
    second_started = threading.Event()
    second_detected_migration = threading.Event()
    second_rechecked_under_lock = threading.Event()
    release_first_connection = threading.Event()
    original_requires_migration = db_schema._schema_requires_migration
    migration_checks = {}
    checks_lock = threading.Lock()

    def gated_requires_migration(cursor):
        requires_migration = original_requires_migration(cursor)
        thread_name = threading.current_thread().name
        with checks_lock:
            check_number = migration_checks.get(thread_name, 0) + 1
            migration_checks[thread_name] = check_number
        if thread_name == "migration-first" and check_number == 2:
            first_rechecking_under_lock.set()
            assert release_first_connection.wait(timeout=5)
        elif thread_name == "migration-second" and check_number == 1:
            second_detected_migration.set()
        elif thread_name == "migration-second" and check_number == 2:
            second_rechecked_under_lock.set()
        return requires_migration

    monkeypatch.setattr(
        db_schema,
        "_schema_requires_migration",
        gated_requires_migration,
    )

    errors = []

    def run_migration():
        worker_conn = sqlite3.connect(db_path)
        worker_conn.row_factory = sqlite3.Row
        worker_conn.execute("PRAGMA busy_timeout = 5000")
        if threading.current_thread().name == "migration-second":
            second_started.set()
        try:
            migrate_schema(worker_conn, logger)
        except Exception as error:  # pragma: no cover - assertion reports details
            errors.append(error)
        finally:
            worker_conn.close()

    first = threading.Thread(target=run_migration, name="migration-first")
    second = threading.Thread(target=run_migration, name="migration-second")
    first.start()
    assert first_rechecking_under_lock.wait(timeout=5)
    second.start()

    # Both workers can perform the optimistic, read-only schema check. The
    # second must then wait on BEGIN IMMEDIATE and may not perform its locked
    # re-check until the first migration commits.
    assert second_started.wait(timeout=5)
    assert second_detected_migration.wait(timeout=5)
    assert not second_rechecked_under_lock.wait(timeout=0.25)
    release_first_connection.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []

    verify = sqlite3.connect(db_path)
    verify.row_factory = sqlite3.Row
    assert _columns(verify, "download_queue")[-9:] == (
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "claim_owner",
        "claim_expires_at",
        "claim_heartbeat_at",
        "is_paused",
        "is_quarantined",
        "last_error",
    )
    row = verify.execute(
        "SELECT status, attempt_count, is_quarantined "
        "FROM download_queue WHERE mbid_guess = 'release-1'"
    ).fetchone()
    assert dict(row) == {
        "status": "failed",
        "attempt_count": 0,
        "is_quarantined": 1,
    }
    verify.close()


def test_current_schema_migration_check_remains_read_only_under_writer_lock(
    tmp_path,
):
    db_path = tmp_path / "current-schema.db"
    logger = logging.getLogger("test.db_schema.current")
    setup = sqlite3.connect(db_path)
    setup.row_factory = sqlite3.Row
    create_tables(setup, logger)
    migrate_schema(setup, logger)
    setup.close()

    writer = sqlite3.connect(db_path)
    writer.execute("BEGIN IMMEDIATE")
    reader = sqlite3.connect(db_path)
    reader.row_factory = sqlite3.Row
    reader.execute("PRAGMA busy_timeout = 50")
    try:
        migrate_schema(reader, logger)
    finally:
        reader.close()
        writer.rollback()
        writer.close()


def test_schema_module_migrates_the_complete_legacy_contract_twice():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE tracks (
            mbid TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            ipod_path TEXT,
            synced_to_ipod INTEGER DEFAULT 0
        );
        CREATE TABLE play_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_mbid TEXT NOT NULL,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT
        );
        CREATE TABLE playlists (
            playlist_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            spotify_url TEXT
        );
        INSERT INTO tracks (
            mbid, title, artist, ipod_path, synced_to_ipod
        ) VALUES ('legacy', 'Song', 'Artist', '/old/device.flac', 1);
        """
    )
    logger = logging.getLogger("test.db_schema.legacy")

    create_tables(conn, logger)
    migrate_schema(conn, logger)
    migrate_schema(conn, logger)

    assert _columns(conn, "tracks") == (
        "mbid",
        "title",
        "artist",
        "dap_path",
        "synced_to_dap",
        "updated_at",
        "deleted_at",
        "tag_tier",
        "tag_score",
        "is_liked",
        "album_artist",
    )
    assert _columns(conn, "play_events")[-1] == "listened_ms"
    assert _columns(conn, "playlists")[-3:] == (
        "updated_at",
        "deleted_at",
        "smart_rules",
    )
    row = conn.execute(
        "SELECT dap_path, synced_to_dap, updated_at "
        "FROM tracks WHERE mbid = 'legacy'"
    ).fetchone()
    assert row["dap_path"] == "/old/device.flac"
    assert row["synced_to_dap"] == 1
    assert row["updated_at"] is not None
    conn.close()
