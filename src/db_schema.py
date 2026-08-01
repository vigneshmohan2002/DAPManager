"""SQLite schema creation and idempotent migrations for DAP Manager.

This module owns DDL only.  ``DatabaseManager`` retains the public lifecycle
and calls these functions in the same create-then-migrate order as before.
"""

import logging
import sqlite3
from typing import Dict, Set, Tuple

from src.download_identity import download_target_key


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
            is_liked INTEGER NOT NULL DEFAULT 0,
            album_artist TEXT,
            master_streamable INTEGER
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
            mbid_guess TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            next_attempt_at TIMESTAMP,
            claim_owner TEXT,
            claim_expires_at TIMESTAMP,
            claim_heartbeat_at TIMESTAMP,
            is_paused INTEGER NOT NULL DEFAULT 0,
            is_quarantined INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            target_key TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL DEFAULT 'queued',
            failure_class TEXT,
            blocked_reason TEXT,
            strategy_index INTEGER NOT NULL DEFAULT 0
        );
    """,
    "download_worker_state": """
        CREATE TABLE IF NOT EXISTS download_worker_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            is_paused INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL DEFAULT 'stopped',
            lease_owner TEXT,
            lease_expires_at TIMESTAMP,
            heartbeat_at TIMESTAMP,
            current_item_id INTEGER,
            detail TEXT NOT NULL DEFAULT '',
            next_wake_at TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """,
    "download_attempts": """
        CREATE TABLE IF NOT EXISTS download_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_item_id INTEGER,
            target_key TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT '',
            phase TEXT NOT NULL DEFAULT 'claimed',
            outcome TEXT,
            failure_class TEXT,
            detail TEXT NOT NULL DEFAULT '',
            network_started INTEGER NOT NULL DEFAULT 0,
            bytes_staged INTEGER NOT NULL DEFAULT 0,
            files_staged INTEGER NOT NULL DEFAULT 0,
            files_validated INTEGER NOT NULL DEFAULT 0,
            files_imported INTEGER NOT NULL DEFAULT 0,
            started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            retry_at TIMESTAMP,
            FOREIGN KEY (queue_item_id) REFERENCES download_queue(id)
                ON DELETE SET NULL
        );
    """,
    "download_attempt_files": """
        CREATE TABLE IF NOT EXISTS download_attempt_files (
            attempt_id INTEGER NOT NULL,
            relative_name TEXT NOT NULL,
            manifest_position INTEGER,
            recording_mbid TEXT,
            release_mbid TEXT,
            audio_sha256 TEXT,
            acoustic_result TEXT,
            acoustic_score REAL,
            decision TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            bytes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (attempt_id, relative_name),
            FOREIGN KEY (attempt_id) REFERENCES download_attempts(id)
                ON DELETE CASCADE
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
    "CREATE INDEX IF NOT EXISTS idx_download_attempts_queue_started "
    "ON download_attempts(queue_item_id, started_at)",
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


ALBUM_TRACK_MIGRATIONS: Tuple[Tuple[str, str], ...] = (
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

DOWNLOAD_QUEUE_MIGRATIONS: Tuple[Tuple[str, str], ...] = (
    ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
    ("next_attempt_at", "TIMESTAMP"),
    ("claim_owner", "TEXT"),
    ("claim_expires_at", "TIMESTAMP"),
    ("claim_heartbeat_at", "TIMESTAMP"),
    ("is_paused", "INTEGER NOT NULL DEFAULT 0"),
    ("is_quarantined", "INTEGER NOT NULL DEFAULT 0"),
    ("last_error", "TEXT"),
    ("target_key", "TEXT NOT NULL DEFAULT ''"),
    ("phase", "TEXT NOT NULL DEFAULT 'queued'"),
    ("failure_class", "TEXT"),
    ("blocked_reason", "TEXT"),
    ("strategy_index", "INTEGER NOT NULL DEFAULT 0"),
)


REQUIRED_DOWNLOAD_INDEXES = {
    "idx_download_queue_claimable",
    "idx_download_queue_active_target",
}


def _schema_requires_migration(cursor: sqlite3.Cursor) -> bool:
    """Return whether any additive/rename migration or migration index is absent."""
    track_columns = _columns(cursor, "tracks")
    if (
        ("ipod_path" in track_columns and "dap_path" not in track_columns)
        or (
            "synced_to_ipod" in track_columns
            and "synced_to_dap" not in track_columns
        )
        or not {
            "updated_at",
            "deleted_at",
            "tag_tier",
            "tag_score",
            "is_liked",
            "album_artist",
            "master_streamable",
        }.issubset(track_columns)
    ):
        return True

    if "listened_ms" not in _columns(cursor, "play_events"):
        return True
    if not {"updated_at", "deleted_at", "smart_rules"}.issubset(
        _columns(cursor, "playlists")
    ):
        return True
    if not {column for column, _ in ALBUM_TRACK_MIGRATIONS}.issubset(
        _columns(cursor, "album_download_request_tracks")
    ):
        return True
    download_queue_columns = _columns(cursor, "download_queue")
    if not {column for column, _ in DOWNLOAD_QUEUE_MIGRATIONS}.issubset(
        download_queue_columns
    ):
        return True

    # Data backfills are part of the migration contract too. A previous build
    # could add the retry columns and quarantine legacy failures before the
    # explanatory ``last_error`` backfill existed. Treat those rows as pending
    # migration so the current-schema fast path does not skip the repair.
    if cursor.execute(
        "SELECT 1 FROM download_queue "
        "WHERE status = 'failed' AND is_quarantined = 1 "
        "AND attempt_count = 0 "
        "AND TRIM(COALESCE(last_error, '')) = '' LIMIT 1"
    ).fetchone():
        return True

    migration_indexes = {
        row[0]
        for row in cursor.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name IN (?, ?, ?)",
            (
                "idx_tracks_is_liked",
                "idx_download_queue_claimable",
                "idx_download_queue_active_target",
            ),
        ).fetchall()
    }
    return migration_indexes != {
        "idx_tracks_is_liked",
        "idx_download_queue_claimable",
        "idx_download_queue_active_target",
    }


def migrate_schema(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """Apply every legacy schema migration idempotently in historical order."""
    cursor = conn.cursor()
    try:
        requires_migration = _schema_requires_migration(cursor)
        if not requires_migration:
            # Keep the current-schema path read-only. DatabaseManager is opened
            # by high-fanout read routes (including each artwork request), so
            # taking a write reservation here would make readers contend with
            # unrelated writers even when there is no DDL to apply.
            conn.commit()
            return

        # Column migrations are necessarily check-then-ALTER operations. When
        # this function owns transaction startup, reserve the write lock and
        # re-read the schema under it: another process may have completed the
        # same migration between the optimistic read and this reservation.
        if not conn.in_transaction:
            cursor.execute("BEGIN IMMEDIATE")
            if not _schema_requires_migration(cursor):
                conn.commit()
                return

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
        if "album_artist" not in columns:
            cursor.execute("ALTER TABLE tracks ADD COLUMN album_artist TEXT")
            logger.info("Added column: tracks.album_artist")
        if "master_streamable" not in columns:
            cursor.execute(
                "ALTER TABLE tracks ADD COLUMN master_streamable INTEGER"
            )
            cursor.execute(
                "DELETE FROM sync_state WHERE key = 'last_catalog_sync'"
            )
            logger.info(
                "Added column: tracks.master_streamable and reset catalog cursor"
            )

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
        for column, definition in ALBUM_TRACK_MIGRATIONS:
            if column not in album_track_columns:
                cursor.execute(
                    "ALTER TABLE album_download_request_tracks "
                    f"ADD COLUMN {column} {definition}"
                )
                logger.info(
                    "Added column: album_download_request_tracks.%s",
                    column,
                )

        download_queue_columns = _columns(cursor, "download_queue")
        # Existing failed rows pre-date retry budgets and may represent large,
        # retained forensic downloads. Quarantine them exactly once rather
        # than turning deployment into an automatic retry storm.
        legacy_download_queue_retry_state = (
            "is_quarantined" not in download_queue_columns
        )
        for column, definition in DOWNLOAD_QUEUE_MIGRATIONS:
            if column not in download_queue_columns:
                cursor.execute(
                    "ALTER TABLE download_queue "
                    f"ADD COLUMN {column} {definition}"
                )
                logger.info("Added column: download_queue.%s", column)

        # Backfill stable target identities before installing the partial
        # uniqueness constraint. Resolve legacy collisions without reviving a
        # quarantined row or disturbing an active claim.
        queue_rows = cursor.execute(
            "SELECT id, search_query, playlist_id, mbid_guess, status, "
            "claim_owner, attempt_count, max_attempts, is_paused, "
            "is_quarantined, next_attempt_at, last_error FROM download_queue "
            "ORDER BY id"
        ).fetchall()
        rows_by_target = {}
        for row in queue_rows:
            target_key = download_target_key(
                row["search_query"], row["playlist_id"], row["mbid_guess"]
            )
            cursor.execute(
                "UPDATE download_queue SET target_key = ? WHERE id = ?",
                (target_key, row["id"]),
            )
            if target_key and row["status"] in ("pending", "failed"):
                rows_by_target.setdefault(target_key, []).append(row)

        for target_key, duplicate_rows in rows_by_target.items():
            if len(duplicate_rows) < 2:
                continue
            claimed = [row for row in duplicate_rows if row["claim_owner"]]
            keeper = claimed[0] if claimed else duplicate_rows[0]
            retired = [row for row in duplicate_rows if row["id"] != keeper["id"]]
            cursor.execute(
                "UPDATE download_queue SET attempt_count = ?, max_attempts = ?, "
                "is_paused = ?, is_quarantined = ?, next_attempt_at = ?, "
                "last_error = ? WHERE id = ?",
                (
                    max(int(row["attempt_count"] or 0) for row in duplicate_rows),
                    max(int(row["max_attempts"] or 3) for row in duplicate_rows),
                    int(any(row["is_paused"] for row in duplicate_rows)),
                    int(any(row["is_quarantined"] for row in duplicate_rows)),
                    max(
                        (row["next_attempt_at"] for row in duplicate_rows
                         if row["next_attempt_at"]),
                        default=None,
                    ),
                    next(
                        (str(row["last_error"]) for row in reversed(duplicate_rows)
                         if row["last_error"]),
                        None,
                    ),
                    keeper["id"],
                ),
            )
            retired_ids = [int(row["id"]) for row in retired]
            placeholders = ",".join("?" for _ in retired_ids)
            cursor.execute(
                "UPDATE album_download_requests SET queue_item_id = ? "
                f"WHERE queue_item_id IN ({placeholders})",
                (keeper["id"], *retired_ids),
            )
            cursor.execute(
                f"DELETE FROM download_queue WHERE id IN ({placeholders})",
                retired_ids,
            )
            logger.warning(
                "Merged %d duplicate active download target(s) for %s",
                len(retired_ids),
                target_key,
            )
        if legacy_download_queue_retry_state:
            cursor.execute(
                "UPDATE download_queue SET is_quarantined = 1, "
                "last_error = CASE "
                "WHEN TRIM(COALESCE(last_error, '')) = '' THEN "
                "'Legacy failed item quarantined during safety migration; "
                "review retained files and exact release before retrying' "
                "ELSE last_error END "
                "WHERE status = 'failed'"
            )
            if cursor.rowcount:
                logger.info(
                    "Quarantined %d legacy failed download(s)",
                    cursor.rowcount,
                )
        cursor.execute(
            "UPDATE download_queue SET last_error = "
            "'Legacy failed item quarantined during safety migration; "
            "review retained files and exact release before retrying' "
            "WHERE status = 'failed' AND is_quarantined = 1 "
            "AND attempt_count = 0 "
            "AND TRIM(COALESCE(last_error, '')) = ''"
        )
        # This index is created after the additive migrations so opening a
        # pre-lease database cannot fail in ``create_tables`` before the new
        # columns exist.
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_queue_claimable "
            "ON download_queue("
            "status, is_paused, is_quarantined, next_attempt_at, "
            "claim_expires_at, id)"
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_download_queue_active_target ON download_queue(target_key) "
            "WHERE target_key != '' AND status IN ('pending', 'failed')"
        )
        cursor.execute(
            "INSERT OR IGNORE INTO download_worker_state(singleton_id) VALUES (1)"
        )
        conn.commit()
    except sqlite3.Error as error:
        logger.error("Schema migration failed: %s", error)
        conn.rollback()
        raise
    finally:
        cursor.close()
