
"""
Database management for DAP Manager.
"""

import sqlite3
import os
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# The default sqlite3 datetime adapter is deprecated in Python 3.12 and
# scheduled for removal. Register an explicit ISO-format adapter so writes
# from `datetime.now()` keep working on 3.13+.
sqlite3.register_adapter(datetime, lambda d: d.isoformat())


@dataclass
class Track:
    """Represents a single track in the master library."""

    mbid: str
    title: str
    artist: str
    album: Optional[str] = None
    isrc: Optional[str] = None
    local_path: Optional[str] = None
    dap_path: Optional[str] = None
    synced_to_dap: bool = False
    # New Fields for Completer
    release_mbid: Optional[str] = None
    track_number: int = 0
    disc_number: int = 1
    # Auto-tag confidence recorded at write time. tier ∈ {green, yellow, red}
    # or None for tracks that were never auto-tagged (legacy / manual import).
    tag_tier: Optional[str] = None
    tag_score: Optional[float] = None

    @property
    def safe_artist(self):
        import re

        if not self.artist or self.artist == "Unknown Artist":
            return "Unknown Artist"
        return re.sub(r'[\\/*?:"<>|]', "_", self.artist)

    @property
    def safe_title(self):
        import re

        if not self.title or self.title == "Unknown Title":
            return "Unknown Title"
        return re.sub(r'[\\/*?:"<>|]', "_", self.title)


@dataclass
class Playlist:
    playlist_id: str
    name: str
    spotify_url: str


@dataclass
class DownloadItem:
    search_query: str
    playlist_id: str
    mbid_guess: str
    id: Optional[int] = None
    status: str = "pending"
    last_attempt: Optional[datetime] = None


class DatabaseManager:
    """Handles all database operations for DAP Manager."""

    def __init__(self, db_path: str = "dap_library.db"):
        self.db_path = db_path
        self.conn = None
        self._connect()
        self._create_tables()
        self._migrate_schema()

    def _connect(self):
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON;")
            logger.info(f"Connected to database at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    def _create_tables(self):
        if not self.conn:
            self._connect()

        tables = {
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
                    tag_score REAL
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
        }

        try:
            cursor = self.conn.cursor()
            for table_name, create_sql in tables.items():
                cursor.execute(create_sql)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error creating tables: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def _migrate_schema(self):
        # Legacy DBs created before the iPod→DAP rename have columns named
        # `ipod_path` / `synced_to_ipod`. Rename them in-place (SQLite 3.25+).
        cursor = self.conn.cursor()
        try:
            cols = {row[1] for row in cursor.execute("PRAGMA table_info(tracks)").fetchall()}
            if "ipod_path" in cols and "dap_path" not in cols:
                cursor.execute("ALTER TABLE tracks RENAME COLUMN ipod_path TO dap_path")
                logger.info("Migrated column: ipod_path → dap_path")
            if "synced_to_ipod" in cols and "synced_to_dap" not in cols:
                cursor.execute("ALTER TABLE tracks RENAME COLUMN synced_to_ipod TO synced_to_dap")
                logger.info("Migrated column: synced_to_ipod → synced_to_dap")
            if "updated_at" not in cols:
                # ADD COLUMN rejects CURRENT_TIMESTAMP defaults, so add nullable
                # then backfill existing rows in a single pass.
                cursor.execute("ALTER TABLE tracks ADD COLUMN updated_at TIMESTAMP")
                cursor.execute("UPDATE tracks SET updated_at = CURRENT_TIMESTAMP")
                logger.info("Added column: tracks.updated_at (backfilled)")
            if "deleted_at" not in cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN deleted_at TIMESTAMP")
                logger.info("Added column: tracks.deleted_at")
            if "tag_tier" not in cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN tag_tier TEXT")
                logger.info("Added column: tracks.tag_tier")
            if "tag_score" not in cols:
                cursor.execute("ALTER TABLE tracks ADD COLUMN tag_score REAL")
                logger.info("Added column: tracks.tag_score")

            pl_cols = {
                row[1]
                for row in cursor.execute("PRAGMA table_info(playlists)").fetchall()
            }
            if "updated_at" not in pl_cols:
                cursor.execute("ALTER TABLE playlists ADD COLUMN updated_at TIMESTAMP")
                cursor.execute("UPDATE playlists SET updated_at = CURRENT_TIMESTAMP")
                logger.info("Added column: playlists.updated_at (backfilled)")
            if "deleted_at" not in pl_cols:
                cursor.execute("ALTER TABLE playlists ADD COLUMN deleted_at TIMESTAMP")
                logger.info("Added column: playlists.deleted_at")
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Schema migration failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    # --- Track Methods ---
    def add_or_update_track(self, track: Track):
        # Normalize path to ensure consistency (force forward slashes)
        if track.local_path:
            track.local_path = os.path.normpath(track.local_path).replace("\\", "/")

        # UPSERT (not REPLACE) so we can preserve tag_tier/tag_score when the
        # caller doesn't know them — e.g. a library re-scan shouldn't wipe
        # the tier recorded by the downloader.
        sql = """
        INSERT INTO tracks
        (mbid, title, artist, album, isrc, local_path, dap_path, synced_to_dap,
         release_mbid, track_number, disc_number, tag_tier, tag_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(mbid) DO UPDATE SET
            title = excluded.title,
            artist = excluded.artist,
            album = excluded.album,
            isrc = excluded.isrc,
            local_path = excluded.local_path,
            dap_path = excluded.dap_path,
            synced_to_dap = excluded.synced_to_dap,
            release_mbid = excluded.release_mbid,
            track_number = excluded.track_number,
            disc_number = excluded.disc_number,
            tag_tier = COALESCE(excluded.tag_tier, tag_tier),
            tag_score = COALESCE(excluded.tag_score, tag_score),
            updated_at = CURRENT_TIMESTAMP
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                sql,
                (
                    track.mbid,
                    track.title,
                    track.artist,
                    track.album,
                    track.isrc,
                    track.local_path,
                    track.dap_path,
                    int(track.synced_to_dap),
                    track.release_mbid,
                    track.track_number,
                    track.disc_number,
                    track.tag_tier,
                    track.tag_score,
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error adding track: {e}")
            self.conn.rollback()
        finally:
            if cursor:
                cursor.close()

    def set_track_tag_tier(
        self, mbid: str, tier: Optional[str], score: Optional[float]
    ) -> bool:
        """Record the auto-tag confidence tier/score for an existing track.

        Separate from ``add_or_update_track`` so the downloader can stamp
        the tier *after* the scanner has inserted the row from the file's
        on-disk tags. Returns True if a row was updated.
        """
        if not mbid:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET tag_tier = ?, tag_score = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (tier, score, mbid),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def get_tracks_needing_tag_review(self) -> List[Track]:
        """Tracks whose last auto-tag was yellow or red — user must review.

        Excludes soft-deleted rows and tracks with no local file.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tracks "
            "WHERE tag_tier IN ('yellow', 'red') "
            "AND local_path IS NOT NULL "
            "AND deleted_at IS NULL "
            "ORDER BY artist, album, track_number"
        )
        tracks = [self._row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def get_track_by_mbid(self, mbid: str) -> Optional[Track]:
        sql = "SELECT * FROM tracks WHERE mbid = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (mbid,))
            return self._row_to_track(cursor.fetchone())
        except sqlite3.Error:
            return None
        finally:
            if cursor:
                cursor.close()

    def get_track_by_path(self, local_path: str) -> Optional[Track]:
        sql = "SELECT * FROM tracks WHERE local_path = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (local_path,))
            return self._row_to_track(cursor.fetchone())
        except sqlite3.Error:
            return None
        finally:
            if cursor:
                cursor.close()

    # --- Album Methods ---
    def update_album_metadata(
        self, release_mbid: str, album_title: str, total_tracks: int
    ):
        sql = """
        INSERT INTO albums (release_mbid, album_title, total_tracks)
        VALUES (?, ?, ?)
        ON CONFLICT(release_mbid) DO UPDATE SET
            total_tracks = MAX(total_tracks, excluded.total_tracks),
            album_title = excluded.album_title
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (release_mbid, album_title, total_tracks))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating album metadata: {e}")
        finally:
            if cursor:
                cursor.close()

    def get_incomplete_albums(self) -> List[dict]:
        sql = """
        SELECT 
            t.artist,
            a.album_title,
            a.release_mbid,
            COUNT(DISTINCT t.track_number) as local_count,
            a.total_tracks
        FROM tracks t
        JOIN albums a ON t.release_mbid = a.release_mbid
        WHERE t.local_path IS NOT NULL
        GROUP BY t.release_mbid
        HAVING local_count < a.total_tracks
        ORDER BY t.artist, a.album_title
        """
        results = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                results.append(
                    {
                        "artist": row["artist"],
                        "album": row["album_title"],
                        "mbid": row["release_mbid"],
                        "have": row["local_count"],
                        "total": row["total_tracks"],
                        "missing": row["total_tracks"] - row["local_count"],
                    }
                )
            return results
        except sqlite3.Error:
            return []
        finally:
            if cursor:
                cursor.close()

    def get_tracks_missing_album_info(self) -> List[Track]:
        """Find tracks that have a recording MBID but no release_mbid or no album entry."""
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN albums a ON t.release_mbid = a.release_mbid
            WHERE t.local_path IS NOT NULL
              AND (t.release_mbid IS NULL OR a.release_mbid IS NULL)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            return [self._row_to_track(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting orphan tracks: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    def get_album_track_counts(self) -> List[dict]:
        """Get all albums with their local track count vs expected total."""
        sql = """
            SELECT
                a.release_mbid,
                a.album_title,
                a.total_tracks,
                MIN(t.artist) as artist,
                COUNT(DISTINCT t.track_number) as local_count
            FROM albums a
            JOIN tracks t ON t.release_mbid = a.release_mbid
            WHERE t.local_path IS NOT NULL
            GROUP BY a.release_mbid
            ORDER BY t.artist, a.album_title
        """
        results = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                results.append({
                    "release_mbid": row["release_mbid"],
                    "album": row["album_title"],
                    "artist": row["artist"],
                    "total": row["total_tracks"],
                    "have": row["local_count"],
                    "missing": row["total_tracks"] - row["local_count"],
                })
            return results
        except sqlite3.Error as e:
            logger.error(f"Error getting album track counts: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    @staticmethod
    def _normalize_query(s: str) -> str:
        """Lowercase + collapse whitespace so callers using slightly different
        formatting ('Artist - Title' vs 'artist  -  title') don't double-queue."""
        if not s:
            return ""
        return " ".join(s.lower().split())

    def list_albums(self) -> List[dict]:
        """Distinct albums implied by the tracks table.

        Groups by ``release_mbid`` when present, falling back to an
        ``album|artist`` synthetic key so tracks without MBIDs still
        group correctly. The ``cover_path`` is one of the album's track
        file paths — the web layer uses it to extract embedded art.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS id,
                COALESCE(album, '') AS title,
                artist,
                COUNT(*) AS track_count,
                MIN(local_path) AS cover_path
            FROM tracks
            WHERE deleted_at IS NULL
              AND album IS NOT NULL AND album != ''
              AND artist IS NOT NULL AND artist != ''
            GROUP BY id
            ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def list_all_tracks(self) -> List[dict]:
        """Every playable track in the library, flat. Feeds the Songs screen.

        Includes the derived ``album_id`` (release_mbid when present, else
        the ``album|artist`` synthetic) so the UI can look cover art up
        and play back without another round-trip. Soft-deleted rows and
        rows without a local file are excluded — Songs is a "what can I
        actually play right now" view, not a catalog view.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                mbid, title, artist, album, track_number, disc_number,
                COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
            FROM tracks
            WHERE deleted_at IS NULL
              AND local_path IS NOT NULL
            ORDER BY artist COLLATE NOCASE,
                     album COLLATE NOCASE,
                     COALESCE(disc_number, 1),
                     COALESCE(track_number, 9999),
                     title COLLATE NOCASE
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def list_artists(self) -> List[dict]:
        """Distinct artists with album and track counts.

        Mirrors ``list_albums`` in that it ignores soft-deleted rows and
        only counts tracks that have an album attached (we don't want
        loose singles inflating album_count). Sort is case-insensitive
        on the artist name so the UI can render it directly.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                artist AS name,
                COUNT(DISTINCT COALESCE(NULLIF(release_mbid, ''), album || '|' || artist)) AS album_count,
                COUNT(*) AS track_count
            FROM tracks
            WHERE deleted_at IS NULL
              AND artist IS NOT NULL AND artist != ''
              AND album IS NOT NULL AND album != ''
            GROUP BY artist
            ORDER BY artist COLLATE NOCASE
            """
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def get_album_cover_path(self, album_id: str) -> Optional[str]:
        """Return one track file path for an album id — used to extract
        embedded cover art. ``album_id`` is whatever ``list_albums``
        returned (a release_mbid or ``album|artist`` synthetic)."""
        if not album_id:
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT local_path FROM tracks
            WHERE deleted_at IS NULL
              AND (release_mbid = ? OR (album || '|' || artist) = ?)
            LIMIT 1
            """,
            (album_id, album_id),
        )
        row = cursor.fetchone()
        cursor.close()
        return row["local_path"] if row else None

    def list_album_tracks(self, album_id: str) -> List[dict]:
        """Ordered tracks belonging to the album identified by ``album_id``.

        ``album_id`` matches the id returned by ``list_albums`` — either a
        release_mbid or an ``album|artist`` synthetic. Ordered by disc
        then track number, with title as a final tiebreaker so tracks
        without numbers still sort predictably.
        """
        if not album_id:
            return []
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT mbid, title, artist, album, track_number, disc_number, local_path
            FROM tracks
            WHERE deleted_at IS NULL
              AND local_path IS NOT NULL
              AND (release_mbid = ? OR (album || '|' || artist) = ?)
            ORDER BY COALESCE(disc_number, 1),
                     COALESCE(track_number, 9999),
                     title COLLATE NOCASE
            """,
            (album_id, album_id),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def get_track_local_path(self, mbid: str) -> Optional[str]:
        """Resolve an mbid to its local file path, or None if missing."""
        if not mbid:
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT local_path FROM tracks WHERE mbid = ? AND deleted_at IS NULL",
            (mbid,),
        )
        row = cursor.fetchone()
        cursor.close()
        return row["local_path"] if row and row["local_path"] else None

    def has_queued_mbid(self, mbid: str) -> bool:
        """True if any row in download_queue already targets this MBID,
        regardless of status. Used by the release watcher to avoid
        re-queuing the same album on every tick."""
        if not mbid:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM download_queue WHERE mbid_guess = ? LIMIT 1",
            (mbid,),
        )
        row = cursor.fetchone()
        cursor.close()
        return row is not None

    def is_download_queued(self, search_query: str) -> bool:
        """Return True if a normalized form of ``search_query`` is already
        pending or failed in the queue."""
        target = self._normalize_query(search_query)
        if not target:
            return False
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT search_query FROM download_queue WHERE status IN ('pending', 'failed')"
            )
            for row in cursor.fetchall():
                if self._normalize_query(row["search_query"]) == target:
                    cursor.close()
                    return True
            cursor.close()
            return False
        except sqlite3.Error:
            return False

    def merge_albums(self, source_mbid: str, target_mbid: str):
        if not source_mbid or not target_mbid:
            return False
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT album_title FROM albums WHERE release_mbid = ?", (target_mbid,)
            )
            row = cursor.fetchone()
            if not row:
                return False
            target_title = row[0]

            cursor.execute(
                "UPDATE tracks SET release_mbid = ?, album = ?, updated_at = CURRENT_TIMESTAMP WHERE release_mbid = ?",
                (target_mbid, target_title, source_mbid),
            )
            cursor.execute("DELETE FROM albums WHERE release_mbid = ?", (source_mbid,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Merge failed: {e}")
            self.conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()

    # --- Playlist / Download / Misc Methods (Abbreviated, assumes exist from prior code) ---
    def add_or_update_playlist(self, playlist: Playlist):
        # ON CONFLICT DO UPDATE (not INSERT OR REPLACE) so the row's identity
        # is preserved — REPLACE would delete + reinsert and cascade-wipe
        # playlist_tracks rows.
        sql = """
        INSERT INTO playlists (playlist_id, name, spotify_url, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(playlist_id) DO UPDATE SET
            name = excluded.name,
            spotify_url = excluded.spotify_url,
            updated_at = CURRENT_TIMESTAMP
        """
        cursor = self.conn.cursor()
        cursor.execute(sql, (playlist.playlist_id, playlist.name, playlist.spotify_url))
        self.conn.commit()
        cursor.close()

    def _bump_playlist_updated_at(self, playlist_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP WHERE playlist_id = ?",
            (playlist_id,),
        )
        self.conn.commit()
        cursor.close()

    def queue_download(self, item: DownloadItem) -> int:
        sql = "INSERT OR IGNORE INTO download_queue (search_query, playlist_id, mbid_guess, status) VALUES (?, ?, ?, ?)"
        cursor = self.conn.cursor()
        cursor.execute(
            sql, (item.search_query, item.playlist_id, item.mbid_guess, item.status)
        )
        self.conn.commit()
        row_id = cursor.lastrowid
        cursor.close()
        return row_id or 0

    def get_downloads(self, status: str) -> List[DownloadItem]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM download_queue WHERE status = ?", (status,))
        items = [self._row_to_download_item(row) for row in cursor.fetchall()]
        cursor.close()
        return items

    def update_download_status(self, item_id: int, status: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE download_queue SET status = ?, last_attempt = ? WHERE id = ?",
            (status, datetime.now(), item_id),
        )
        self.conn.commit()
        cursor.close()

    def remove_from_queue(self, item_id: int):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM download_queue WHERE id = ?", (item_id,))
        self.conn.commit()
        cursor.close()

    def get_download_queue_count(self) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM download_queue WHERE status IN ('pending', 'failed')"
        )
        count = cursor.fetchone()[0]
        cursor.close()
        return count

    def mark_track_synced(self, mbid: str, dap_path: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET synced_to_dap = 1, dap_path = ?, updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (dap_path, mbid),
        )
        self.conn.commit()
        cursor.close()

    def get_all_tracks(self, local_only: bool = False, include_orphans: bool = False):
        sql = "SELECT * FROM tracks"
        clauses = []
        if local_only:
            clauses.append("local_path IS NOT NULL")
        if not include_orphans:
            clauses.append("deleted_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        cursor = self.conn.cursor()
        cursor.execute(sql)
        tracks = [self._row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def soft_delete_track(self, mbid: str) -> bool:
        """Mark a track as deleted without removing the row.

        Stamps ``deleted_at`` and bumps ``updated_at`` so the delta feed
        carries the signal to satellites on their next pull. Returns True
        if a row was stamped, False if no track matched.
        """
        if not mbid:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET deleted_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE mbid = ? AND deleted_at IS NULL",
            (mbid,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def restore_track(self, mbid: str) -> bool:
        """Un-soft-delete a track: clears deleted_at and bumps updated_at."""
        if not mbid:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET deleted_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE mbid = ? AND deleted_at IS NOT NULL",
            (mbid,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def soft_delete_playlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET deleted_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ? AND deleted_at IS NULL",
            (playlist_id,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def restore_playlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET deleted_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ? AND deleted_at IS NOT NULL",
            (playlist_id,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def get_catalog_since(self, since_iso: Optional[str] = None) -> List[dict]:
        """Return catalog-shape rows (device-agnostic fields + updated_at).

        If ``since_iso`` is provided, only rows with updated_at > since_iso
        are returned. Local-only columns (local_path, dap_path, synced_to_dap)
        are deliberately omitted since they don't travel between devices.
        """
        sql = (
            "SELECT mbid, title, artist, album, isrc, release_mbid, "
            "track_number, disc_number, updated_at, deleted_at FROM tracks"
        )
        params: tuple = ()
        if since_iso:
            sql += " WHERE updated_at > ?"
            params = (since_iso,)
        sql += " ORDER BY updated_at ASC"
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def apply_catalog_row(self, row: dict) -> str:
        """Upsert a catalog row pulled from the master, preserving device-local
        columns (local_path, dap_path, synced_to_dap).

        Returns 'inserted' or 'updated' to indicate what happened. Rows missing
        an ``mbid`` are skipped and return 'skipped'.
        """
        mbid = (row or {}).get("mbid")
        if not mbid:
            return "skipped"

        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM tracks WHERE mbid = ?", (mbid,))
        existed = cursor.fetchone() is not None

        sql = """
        INSERT INTO tracks
            (mbid, title, artist, album, isrc, release_mbid,
             track_number, disc_number, updated_at, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
        ON CONFLICT(mbid) DO UPDATE SET
            title = excluded.title,
            artist = excluded.artist,
            album = excluded.album,
            isrc = excluded.isrc,
            release_mbid = excluded.release_mbid,
            track_number = excluded.track_number,
            disc_number = excluded.disc_number,
            updated_at = excluded.updated_at,
            deleted_at = excluded.deleted_at
        """
        cursor.execute(
            sql,
            (
                mbid,
                row.get("title") or "Unknown Title",
                row.get("artist") or "Unknown Artist",
                row.get("album"),
                row.get("isrc"),
                row.get("release_mbid"),
                row.get("track_number") or 0,
                row.get("disc_number") or 1,
                row.get("updated_at"),
                row.get("deleted_at"),
            ),
        )
        self.conn.commit()
        cursor.close()
        return "updated" if existed else "inserted"

    def apply_playlist_row(self, row: dict) -> str:
        """Upsert a playlist (with its membership) pulled from the master.

        The incoming ``tracks`` list is treated as the authoritative current
        membership — existing rows for this playlist are cleared and
        replaced. ``updated_at`` is taken from the payload so the satellite's
        copy matches the master's timestamp.

        Returns 'inserted', 'updated', or 'skipped' (no playlist_id).
        """
        pid = (row or {}).get("playlist_id")
        if not pid:
            return "skipped"

        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM playlists WHERE playlist_id = ?", (pid,))
        existed = cursor.fetchone() is not None

        cursor.execute(
            """
            INSERT INTO playlists (playlist_id, name, spotify_url, updated_at, deleted_at)
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
            ON CONFLICT(playlist_id) DO UPDATE SET
                name = excluded.name,
                spotify_url = excluded.spotify_url,
                updated_at = excluded.updated_at,
                deleted_at = excluded.deleted_at
            """,
            (
                pid,
                row.get("name") or "Untitled Playlist",
                row.get("spotify_url") or "",
                row.get("updated_at"),
                row.get("deleted_at"),
            ),
        )

        # Replace membership atomically. Tracks the satellite hasn't synced
        # yet are silently dropped — WHERE EXISTS guards the FK so we don't
        # raise on rows referencing unknown MBIDs. They'll appear on a
        # subsequent playlist sync once the track delta has caught up.
        cursor.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pid,))
        for entry in row.get("tracks") or []:
            mbid = entry.get("track_mbid") if isinstance(entry, dict) else None
            if not mbid:
                continue
            order = entry.get("track_order", 0) if isinstance(entry, dict) else 0
            cursor.execute(
                "INSERT OR IGNORE INTO playlist_tracks "
                "(playlist_id, track_mbid, track_order) "
                "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM tracks WHERE mbid = ?)",
                (pid, mbid, order, mbid),
            )
        self.conn.commit()
        cursor.close()
        return "updated" if existed else "inserted"

    def replace_device_inventory(self, device_id: str, items: List[dict]) -> int:
        """Replace the recorded inventory for ``device_id`` in one transaction.

        Each item is {mbid, local_path}. The whole snapshot is authoritative
        — rows not in the payload are dropped so removed tracks disappear.
        Returns the number of rows written.
        """
        if not device_id:
            raise ValueError("device_id is required")

        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                "DELETE FROM device_inventory WHERE device_id = ?", (device_id,)
            )
            written = 0
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                mbid = item.get("mbid")
                if not mbid:
                    continue
                cursor.execute(
                    "INSERT INTO device_inventory "
                    "(device_id, mbid, local_path, reported_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                    (device_id, mbid, item.get("local_path")),
                )
                written += 1
            self.conn.commit()
            return written
        except sqlite3.Error:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def get_device_inventory(self, device_id: str) -> List[dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid, local_path, reported_at FROM device_inventory "
            "WHERE device_id = ? ORDER BY mbid",
            (device_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def get_fleet_summary(self) -> List[dict]:
        """Per-device inventory summary: device_id, track_count, last_reported_at."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT device_id, COUNT(*) AS track_count, MAX(reported_at) AS last_reported_at "
            "FROM device_inventory "
            "GROUP BY device_id "
            "ORDER BY device_id"
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def get_devices_holding_mbid(self, mbid: str) -> List[dict]:
        """Which devices have reported holding a given track."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT device_id, local_path, reported_at FROM device_inventory "
            "WHERE mbid = ? ORDER BY device_id",
            (mbid,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def find_tracks_for_fleet_search(self, query: str, limit: int = 50) -> List[dict]:
        """Find tracks matching artist/title/album for fleet lookup.

        Returns lightweight rows ({mbid, artist, title, album}) plus a
        per-row device_count so the UI can show matches ordered by
        how widely a track is held.
        """
        if not query:
            return []
        term = f"%{query}%"
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT t.mbid, t.artist, t.title, t.album, "
            "       (SELECT COUNT(*) FROM device_inventory d WHERE d.mbid = t.mbid) AS device_count "
            "FROM tracks t "
            "WHERE t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ? "
            "ORDER BY device_count DESC, t.artist, t.album, t.title "
            "LIMIT ?",
            (term, term, term, int(limit)),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return rows

    def apply_pushed_playlist_row(self, row: dict) -> str:
        """Apply a playlist pushed from a satellite, using last-writer-wins
        on ``updated_at``.

        Returns:
          - 'inserted' / 'updated': incoming accepted and applied.
          - 'stale': local updated_at is equal to or newer than incoming —
                    incoming ignored so a round-tripped pull doesn't
                    overwrite a subsequent master-side edit.
          - 'skipped': no playlist_id in payload.

        Lexicographic ISO-string comparison matches SQLite's CURRENT_TIMESTAMP
        ordering, which is what both sides store.
        """
        pid = (row or {}).get("playlist_id")
        if not pid:
            return "skipped"
        incoming_ts = row.get("updated_at")
        if incoming_ts:
            cur = self.conn.execute(
                "SELECT updated_at FROM playlists WHERE playlist_id = ?", (pid,)
            ).fetchone()
            if cur and cur["updated_at"] and cur["updated_at"] >= incoming_ts:
                return "stale"
        return self.apply_playlist_row(row)

    def get_sync_state(self, key: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
        row = cursor.fetchone()
        cursor.close()
        return row["value"] if row else None

    def set_sync_state(self, key: str, value: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()
        cursor.close()

    def get_all_playlists(self, include_orphans: bool = False):
        cursor = self.conn.cursor()
        if include_orphans:
            cursor.execute("SELECT * FROM playlists")
        else:
            cursor.execute("SELECT * FROM playlists WHERE deleted_at IS NULL")
        playlists = [self._row_to_playlist(row) for row in cursor.fetchall()]
        cursor.close()
        return playlists

    def get_playlists_since(self, since_iso: Optional[str] = None) -> List[dict]:
        """Return playlists changed since ``since_iso`` with their full
        current membership nested as ``tracks``.

        Each returned dict: {playlist_id, name, spotify_url, updated_at,
        tracks: [{track_mbid, track_order}, ...]}. Membership is always the
        complete current list — satellites replace, not diff, to handle
        track removals correctly.
        """
        cursor = self.conn.cursor()
        if since_iso:
            cursor.execute(
                "SELECT playlist_id, name, spotify_url, updated_at, deleted_at "
                "FROM playlists WHERE updated_at > ? ORDER BY updated_at ASC",
                (since_iso,),
            )
        else:
            cursor.execute(
                "SELECT playlist_id, name, spotify_url, updated_at, deleted_at "
                "FROM playlists ORDER BY updated_at ASC"
            )
        playlists = [dict(row) for row in cursor.fetchall()]

        for pl in playlists:
            cursor.execute(
                "SELECT track_mbid, track_order FROM playlist_tracks "
                "WHERE playlist_id = ? ORDER BY track_order ASC",
                (pl["playlist_id"],),
            )
            pl["tracks"] = [dict(r) for r in cursor.fetchall()]
        cursor.close()
        return playlists

    def get_playlist_tracks(
        self,
        playlist_id: str,
        local_only: bool = False,
        include_orphans: bool = False,
    ):
        sql = (
            "SELECT t.* FROM tracks t "
            "JOIN playlist_tracks pt ON t.mbid = pt.track_mbid "
            "WHERE pt.playlist_id = ?"
        )
        if local_only:
            sql += " AND t.local_path IS NOT NULL"
        if not include_orphans:
            sql += " AND t.deleted_at IS NULL"
        sql += " ORDER BY pt.track_order"
        cursor = self.conn.cursor()
        cursor.execute(sql, (playlist_id,))
        tracks = [self._row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def get_tracks_for_playlist(
        self,
        playlist_id: str,
        local_only: bool = False,
        include_orphans: bool = False,
    ):
        return self.get_playlist_tracks(
            playlist_id, local_only=local_only, include_orphans=include_orphans
        )

    def link_track_to_playlist(self, playlist_id: str, track_mbid: str, order: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_mbid, track_order) VALUES (?, ?, ?)",
            (playlist_id, track_mbid, order),
        )
        inserted = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        if inserted:
            # Membership changes are an edit to the playlist from a sync
            # standpoint; bump the parent's updated_at so the delta feed
            # picks it up.
            self._bump_playlist_updated_at(playlist_id)

    def get_mbid_to_track_path_map(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT mbid, local_path FROM tracks")
        res = {row[0]: row[1] for row in cursor.fetchall() if row[0] and row[1]}
        cursor.close()
        return res

    def log_duplicate(self, mbid: str, file_path: str):
        if file_path:
            file_path = os.path.normpath(file_path).replace("\\", "/")
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO duplicates (mbid, file_path) VALUES (?, ?)",
            (mbid, file_path),
        )
        self.conn.commit()
        cursor.close()

    def get_all_duplicates(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT mbid, file_path FROM duplicates")
        d = defaultdict(list)
        for row in cursor.fetchall():
            d[row["mbid"]].append(row["file_path"])
        cursor.close()
        return dict(d)

    def clear_duplicate(self, mbid: str):
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM duplicates WHERE mbid = ?", (mbid,))
        self.conn.commit()
        cursor.close()

    def update_track_local_path(self, mbid: str, path: str):
        if path:
            path = os.path.normpath(path).replace("\\", "/")
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET local_path = ?, updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (path, mbid),
        )
        self.conn.commit()
        cursor.close()

    def _row_to_track(self, row):
        if not row:
            return None
        return Track(
            mbid=row["mbid"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            local_path=row["local_path"],
            dap_path=row["dap_path"],
            synced_to_dap=bool(row["synced_to_dap"]),
            release_mbid=row["release_mbid"],
            track_number=row["track_number"],
            disc_number=row["disc_number"],
            tag_tier=row["tag_tier"] if "tag_tier" in row.keys() else None,
            tag_score=row["tag_score"] if "tag_score" in row.keys() else None,
        )

    def _row_to_playlist(self, row):
        if not row:
            return None
        return Playlist(
            playlist_id=row["playlist_id"],
            name=row["name"],
            spotify_url=row["spotify_url"],
        )

    def get_library_stats(self) -> dict:
        cursor = self.conn.cursor()
        stats = {}
        try:
            cursor.execute("SELECT COUNT(*) FROM tracks")
            stats['tracks'] = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(DISTINCT artist) FROM tracks")
            stats['artists'] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM albums")
            stats['albums'] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM playlists")
            stats['playlists'] = cursor.fetchone()[0]

            # Incomplete Albums count
            cursor.execute("""
                SELECT COUNT(*) FROM (
                    SELECT t.release_mbid
                    FROM tracks t JOIN albums a ON t.release_mbid = a.release_mbid
                    WHERE t.local_path IS NOT NULL
                    GROUP BY t.release_mbid
                    HAVING COUNT(DISTINCT t.track_number) < a.total_tracks
                )
            """)
            stats['incomplete_albums'] = cursor.fetchone()[0]
            
        except sqlite3.Error as e:
            logger.error(f"Error getting stats: {e}")
        finally:
            cursor.close()
        return stats

    def search_tracks(self, query: str) -> List[Track]:
        cursor = self.conn.cursor()
        search_term = f"%{query}%"
        sql = """
            SELECT * FROM tracks 
            WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
            ORDER BY artist, album, track_number
            LIMIT 100
        """
        try:
            cursor.execute(sql, (search_term, search_term, search_term))
            return [self._row_to_track(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Search failed: {e}")
            return []
        finally:
            cursor.close()

    def get_all_downloads(self) -> List[DownloadItem]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM download_queue ORDER BY id DESC")
        items = [self._row_to_download_item(row) for row in cursor.fetchall()]
        cursor.close()
        return items

    def _row_to_download_item(self, row):
        if not row:
            return None
        last_attempt = None
        if row["last_attempt"]:
            try:
                last_attempt = datetime.fromisoformat(str(row["last_attempt"]))
            except (ValueError, TypeError):
                pass
        return DownloadItem(
            id=row["id"],
            search_query=row["search_query"],
            playlist_id=row["playlist_id"],
            mbid_guess=row["mbid_guess"],
            status=row["status"],
            last_attempt=last_attempt,
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
