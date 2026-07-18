"""SQLite schema creation and idempotent migrations for DAP Manager.

This module owns DDL only.  ``DatabaseManager`` retains the public lifecycle
and calls these functions in the same create-then-migrate order as before.
"""

import logging
import sqlite3
from typing import Dict, Set, Tuple


TABLE_DEFINITIONS: Dict[str, str] = {
    "tracks": """
        CREATE TABLE IF NOT EXISTS tracks (
            mbid TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            album TEXT,
            isrc TEXT,
            local_path TEXT UNIQUE,
            dap_path TEXT,
            synced_to_dap INTEGER DEFAULT 0,
            release_mbid TEXT,
            track_number INTEGER,
            disc_number INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at TIMESTAMP,
            tag_tier TEXT,
            tag_score REAL,
            is_liked INTEGER NOT NULL DEFAULT 0
        );
    """,
    "albums": """
        CREATE TABLE IF NOT EXISTS albums (
            release_mbid TEXT PRIMARY KEY,
            album_title TEXT,
            total_tracks INTEGER
        );
    """,
    "playlists": """
        CREATE TABLE IF NOT EXISTS playlists (
            playlist_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            spotify_url TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted_at TIMESTAMP
        );
    """,
    "playlist_tracks": """
        CREATE TABLE IF NOT EXISTS playlist_tracks (
            playlist_id TEXT,
            track_mbid TEXT,
            track_order INTEGER,
            PRIMARY KEY (playlist_id, track_mbid),
            FOREIGN KEY (playlist_id) REFERENCES playlists (playlist_id) ON DELETE CASCADE,
            FOREIGN KEY (track_mbid) REFERENCES tracks (mbid) ON DELETE CASCADE
        );
    """,
    "download_queue": """
        CREATE TABLE IF NOT EXISTS download_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_query TEXT NOT NULL,
            playlist_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'failed', 'success')),
            last_attempt TIMESTAMP,
            mbid_guess TEXT NOT NULL
        );
    """,
    "album_download_requests": """
        CREATE TABLE IF NOT EXISTS album_download_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_item_id INTEGER,
            release_mbid TEXT NOT NULL UNIQUE,
            artist TEXT NOT NULL,
            title TEXT NOT NULL,
            track_count INTEGER NOT NULL,
            stage TEXT NOT NULL DEFAULT 'queued'
                CHECK(stage IN ('queued', 'downloading', 'importing', 'success', 'failed')),
            detail TEXT NOT NULL DEFAULT '',
            completed_tracks INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "album_download_request_tracks": """
        CREATE TABLE IF NOT EXISTS album_download_request_tracks (
            request_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            recording_mbid TEXT NOT NULL,
            medium_position INTEGER NOT NULL,
            track_position INTEGER NOT NULL,
            track_number TEXT NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            date TEXT NOT NULL,
            track_total INTEGER NOT NULL,
            disc_total INTEGER NOT NULL,
            release_track_mbid TEXT NOT NULL,
            PRIMARY KEY (request_id, position),
            FOREIGN KEY (request_id) REFERENCES album_download_requests (id)
                ON DELETE CASCADE
        );
    """,
    "duplicates": """
        CREATE TABLE IF NOT EXISTS duplicates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mbid TEXT NOT NULL,
            file_path TEXT NOT NULL,
            UNIQUE(mbid, file_path)
        );
    """,
    "sync_state": """
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """,
    "device_inventory": """
        CREATE TABLE IF NOT EXISTS device_inventory (
            device_id TEXT NOT NULL,
            mbid TEXT NOT NULL,
            local_path TEXT,
            reported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (device_id, mbid)
        );
    """,
    "play_events": """
        CREATE TABLE IF NOT EXISTS play_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_mbid TEXT NOT NULL,
            played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            listened_ms INTEGER
        );
    """,
    "lyrics": """
        CREATE TABLE IF NOT EXISTS lyrics (
            track_mbid TEXT PRIMARY KEY,
            lrc TEXT,
            synced INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "artist_tags": """
        CREATE TABLE IF NOT EXISTS artist_tags (
            artist_name TEXT NOT NULL COLLATE NOCASE,
            mbid TEXT,
            tag TEXT NOT NULL,
            weight INTEGER NOT NULL DEFAULT 1,
            fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (artist_name, tag)
        );
    """,
    "contributions": """
        CREATE TABLE IF NOT EXISTS contributions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT,
            mbid TEXT,
            isrc TEXT,
            artist TEXT,
            title TEXT,
            album TEXT,
            target_quality TEXT,
            acquired_quality TEXT,
            status TEXT NOT NULL DEFAULT 'attempting',
            download_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "contributed": """
        CREATE TABLE IF NOT EXISTS contributed (
            mbid TEXT PRIMARY KEY,
            contribution_id INTEGER,
            status TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "split_album_dismissals": """
        CREATE TABLE IF NOT EXISTS split_album_dismissals (
            incident_key TEXT PRIMARY KEY,
            dismissed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """,
}


BASE_INDEX_DEFINITIONS: Tuple[str, ...] = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_album_download_requests_queue_item "
    "ON album_download_requests(queue_item_id) WHERE queue_item_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_album_download_requests_stage_updated "
    "ON album_download_requests(stage, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_album_download_request_tracks_recording "
    "ON album_download_request_tracks(recording_mbid)",
    "CREATE INDEX IF NOT EXISTS idx_play_events_played_at "
    "ON play_events(played_at)",
    "CREATE INDEX IF NOT EXISTS idx_play_events_track_mbid "
    "ON play_events(track_mbid)",
    "CREATE INDEX IF NOT EXISTS idx_artist_tags_tag ON artist_tags(tag)",
)


def create_tables(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """Create the baseline tables and indexes, preserving transaction policy."""
    cursor = None
    try:
        cursor = conn.cursor()
        for create_sql in TABLE_DEFINITIONS.values():
            cursor.execute(create_sql)
        for index_sql in BASE_INDEX_DEFINITIONS:
            cursor.execute(index_sql)
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Error creating tables: %s", error)
        conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()


def _columns(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    return {
        row[1]
        for row in cursor.execute("PRAGMA table_info(%s)" % table).fetchall()
    }


def migrate_schema(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """Apply every legacy schema migration idempotently in historical order."""
    cursor = conn.cursor()
    try:
        columns = _columns(cursor, "tracks")
        if "ipod_path" in columns and "dap_path" not in columns:
            cursor.execute("ALTER TABLE tracks RENAME COLUMN ipod_path TO dap_path")
            logger.info("Migrated column: ipod_path → dap_path")
        if "synced_to_ipod" in columns and "synced_to_dap" not in columns:
            cursor.execute(
                "ALTER TABLE tracks RENAME COLUMN synced_to_ipod TO synced_to_dap"
            )
            logger.info("Migrated column: synced_to_ipod → synced_to_dap")
        if "updated_at" not in columns:
            cursor.execute("ALTER TABLE tracks ADD COLUMN updated_at TIMESTAMP")
            cursor.execute("UPDATE tracks SET updated_at = CURRENT_TIMESTAMP")
            logger.info("Added column: tracks.updated_at (backfilled)")
        if "deleted_at" not in columns:
            cursor.execute("ALTER TABLE tracks ADD COLUMN deleted_at TIMESTAMP")
            logger.info("Added column: tracks.deleted_at")
        if "tag_tier" not in columns:
            cursor.execute("ALTER TABLE tracks ADD COLUMN tag_tier TEXT")
            logger.info("Added column: tracks.tag_tier")
        if "tag_score" not in columns:
            cursor.execute("ALTER TABLE tracks ADD COLUMN tag_score REAL")
            logger.info("Added column: tracks.tag_score")
        if "is_liked" not in columns:
            cursor.execute(
                "ALTER TABLE tracks ADD COLUMN is_liked INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Added column: tracks.is_liked")

        play_event_columns = _columns(cursor, "play_events")
        if "listened_ms" not in play_event_columns:
            cursor.execute(
                "ALTER TABLE play_events ADD COLUMN listened_ms INTEGER"
            )
            logger.info("Added column: play_events.listened_ms")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracks_is_liked "
            "ON tracks(is_liked) WHERE is_liked = 1"
        )

        playlist_columns = _columns(cursor, "playlists")
        if "updated_at" not in playlist_columns:
            cursor.execute(
                "ALTER TABLE playlists ADD COLUMN updated_at TIMESTAMP"
            )
            cursor.execute("UPDATE playlists SET updated_at = CURRENT_TIMESTAMP")
            logger.info("Added column: playlists.updated_at (backfilled)")
        if "deleted_at" not in playlist_columns:
            cursor.execute(
                "ALTER TABLE playlists ADD COLUMN deleted_at TIMESTAMP"
            )
            logger.info("Added column: playlists.deleted_at")
        if "smart_rules" not in playlist_columns:
            cursor.execute("ALTER TABLE playlists ADD COLUMN smart_rules TEXT")
            logger.info("Added column: playlists.smart_rules")

        album_track_columns = _columns(
            cursor,
            "album_download_request_tracks",
        )
        album_track_migrations = (
            ("medium_position", "INTEGER NOT NULL DEFAULT 0"),
            ("track_position", "INTEGER NOT NULL DEFAULT 0"),
            ("track_number", "TEXT NOT NULL DEFAULT ''"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("artist", "TEXT NOT NULL DEFAULT ''"),
            ("date", "TEXT NOT NULL DEFAULT ''"),
            ("track_total", "INTEGER NOT NULL DEFAULT 0"),
            ("disc_total", "INTEGER NOT NULL DEFAULT 0"),
            ("release_track_mbid", "TEXT NOT NULL DEFAULT ''"),
        )
        for column, definition in album_track_migrations:
            if column not in album_track_columns:
                cursor.execute(
                    "ALTER TABLE album_download_request_tracks "
                    f"ADD COLUMN {column} {definition}"
                )
                logger.info(
                    "Added column: album_download_request_tracks.%s",
                    column,
                )
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Schema migration failed: %s", error)
        conn.rollback()
        raise
    finally:
        cursor.close()
