# ============================================================================
# FILE: db_manager.py
# ============================================================================
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


@dataclass
class Track:
    """Represents a single track in the master library."""

    mbid: str
    title: str
    artist: str
    album: Optional[str] = None
    isrc: Optional[str] = None
    local_path: Optional[str] = None
    ipod_path: Optional[str] = None
    synced_to_ipod: bool = False

    @property
    def safe_artist(self):
        """Get filesystem-safe artist name"""
        import re

        if not self.artist or self.artist == "Unknown Artist":
            return "Unknown Artist"
        return re.sub(r'[\\/*?:"<>|]', "_", self.artist)

    @property
    def safe_title(self):
        """Get filesystem-safe title"""
        import re

        if not self.title or self.title == "Unknown Title":
            return "Unknown Title"
        return re.sub(r'[\\/*?:"<>|]', "_", self.title)

    @property
    def ipod_filename(self):
        """Generate iPod filename with extension"""
        return f"{self.safe_title}.flac"

    def __str__(self):
        return f"{self.artist} - {self.album} - {self.title}"


@dataclass
class Playlist:
    """Represents a single Spotify playlist."""

    playlist_id: str
    name: str
    spotify_url: str


@dataclass
class DownloadItem:
    """Represents a track in the download queue."""

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

    def _connect(self):
        """Establishes the database connection."""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON;")
            logger.info(f"Connected to database at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    def _create_tables(self):
        """Creates the database schema if it doesn't exist."""
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
                    ipod_path TEXT,
                    synced_to_ipod INTEGER DEFAULT 0
                );
            """,
            "playlists": """
                CREATE TABLE IF NOT EXISTS playlists (
                    playlist_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    spotify_url TEXT
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
                    FOREIGN KEY (playlist_id) REFERENCES playlists (playlist_id) ON DELETE CASCADE,
                    UNIQUE(mbid_guess, playlist_id)
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
        }

        try:
            cursor = self.conn.cursor()
            for table_name, create_sql in tables.items():
                cursor.execute(create_sql)
            self.conn.commit()
            logger.info("Database tables verified")
        except sqlite3.Error as e:
            logger.error(f"Error creating tables: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_mbid_to_track_path_map(self) -> dict:
        """
        Returns a dict mapping MBID -> local_path for all tracks.
        Used for efficient lookup during iPod reconciliation.
        """
        sql = "SELECT mbid, local_path FROM tracks"
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            # Create a dictionary {mbid: local_path}
            mbid_map = {
                row[0]: row[1] for row in cursor.fetchall() if row[0] and row[1]
            }
            return mbid_map
        except sqlite3.Error as e:
            logger.error(f"Error in get_mbid_to_track_path_map: {e}")
            raise
        finally:
            if cursor:
                cursor.close()

    def mark_track_synced(self, mbid: str, ipod_path: str):
        """
        Marks a track as synced and records its path on the iPod.
        """
        sql = "UPDATE tracks SET synced_to_ipod = 1, ipod_path = ? WHERE mbid = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (ipod_path, mbid))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error in mark_track_synced for {mbid}: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def close(self):
        """Closes the database connection."""
        if self.conn:
            self.conn.close()
            logger.info("Database connection closed")

    # --- Helper Row Mappers ---

    def _row_to_track(self, row: sqlite3.Row) -> Optional[Track]:
        if not row:
            return None
        return Track(
            mbid=row["mbid"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            local_path=row["local_path"],
            ipod_path=row["ipod_path"],
            synced_to_ipod=bool(row["synced_to_ipod"]),
        )

    def _row_to_playlist(self, row: sqlite3.Row) -> Optional[Playlist]:
        if not row:
            return None
        return Playlist(
            playlist_id=row["playlist_id"],
            name=row["name"],
            spotify_url=row["spotify_url"],
        )

    def _row_to_download_item(self, row: sqlite3.Row) -> Optional[DownloadItem]:
        if not row:
            return None
        return DownloadItem(
            id=row["id"],
            search_query=row["search_query"],
            playlist_id=row["playlist_id"],
            mbid_guess=row["mbid_guess"],
            status=row["status"],
            last_attempt=(
                datetime.fromisoformat(row["last_attempt"])
                if row["last_attempt"]
                else None
            ),
        )

    # --- Track Methods ---

    def add_or_update_track(self, track: Track):
        """Adds a new track or replaces an existing one."""
        sql = """
        INSERT OR REPLACE INTO tracks (mbid, title, artist, album, isrc, local_path, ipod_path, synced_to_ipod)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    track.ipod_path,
                    int(track.synced_to_ipod),
                ),
            )
            self.conn.commit()
            logger.debug(f"Added/updated track: {track.artist} - {track.title}")
        except sqlite3.Error as e:
            logger.error(f"Error in add_or_update_track: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_track_by_mbid(self, mbid: str) -> Optional[Track]:
        """Fetches a track by its MusicBrainz ID."""
        sql = "SELECT * FROM tracks WHERE mbid = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (mbid,))
            return self._row_to_track(cursor.fetchone())
        except sqlite3.Error as e:
            logger.error(f"Error in get_track_by_mbid: {e}")
            return None
        finally:
            if cursor:
                cursor.close()

    def get_track_by_path(self, local_path: str) -> Optional[Track]:
        """Fetches a track by its local file path."""
        sql = "SELECT * FROM tracks WHERE local_path = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (local_path,))
            return self._row_to_track(cursor.fetchone())
        except sqlite3.Error as e:
            logger.error(f"Error in get_track_by_path: {e}")
            return None
        finally:
            if cursor:
                cursor.close()

    def update_track_local_path(self, mbid: str, local_path: str):
        """Updates a track's local_path."""
        sql = "UPDATE tracks SET local_path = ? WHERE mbid = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (local_path, mbid))
            self.conn.commit()
            logger.debug(f"Updated local path for MBID {mbid}")
        except sqlite3.Error as e:
            logger.error(f"Error in update_track_local_path: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def set_track_synced(self, mbid: str, ipod_path: str):
        """Marks a track as synced to the iPod."""
        sql = "UPDATE tracks SET synced_to_ipod = 1, ipod_path = ? WHERE mbid = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (ipod_path, mbid))
            self.conn.commit()
            logger.debug(f"Marked track {mbid} as synced")
        except sqlite3.Error as e:
            logger.error(f"Error in set_track_synced: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def mark_track_synced(self, mbid: str, ipod_path: str):
        """Alias for set_track_synced."""
        self.set_track_synced(mbid, ipod_path)

    def get_all_tracks(self, local_only: bool = False) -> List[Track]:
        """Fetches all tracks from the database."""
        sql = "SELECT * FROM tracks"
        if local_only:
            sql += " WHERE local_path IS NOT NULL"
        sql += " ORDER BY artist, title ASC"

        tracks = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                tracks.append(self._row_to_track(row))
            return tracks
        except sqlite3.Error as e:
            logger.error(f"Error in get_all_tracks: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    # --- Playlist Methods ---

    def add_or_update_playlist(self, playlist: Playlist):
        """Adds a new playlist or replaces an existing one."""
        sql = "INSERT OR REPLACE INTO playlists (playlist_id, name, spotify_url) VALUES (?, ?, ?)"
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                sql, (playlist.playlist_id, playlist.name, playlist.spotify_url)
            )
            self.conn.commit()
            logger.debug(f"Added/updated playlist: {playlist.name}")
        except sqlite3.Error as e:
            logger.error(f"Error in add_or_update_playlist: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_all_playlists(self) -> List[Playlist]:
        """Fetches all playlists from the database."""
        sql = "SELECT * FROM playlists ORDER BY name ASC"
        playlists = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                playlists.append(self._row_to_playlist(row))
            return playlists
        except sqlite3.Error as e:
            logger.error(f"Error in get_all_playlists: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    def link_track_to_playlist(self, playlist_id: str, track_mbid: str, order: int):
        """Links a track to a playlist with specified order."""
        sql = "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_mbid, track_order) VALUES (?, ?, ?)"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (playlist_id, track_mbid, order))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error in link_track_to_playlist: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_playlist_tracks(
        self, playlist_id: str, local_only: bool = False, unsynced_only: bool = False
    ) -> List[Track]:
        """Gets all tracks for a playlist in order."""
        sql = """
        SELECT t.* FROM tracks t
        JOIN playlist_tracks pt ON t.mbid = pt.track_mbid
        WHERE pt.playlist_id = ?
        """

        if local_only:
            sql += " AND t.local_path IS NOT NULL"
        if unsynced_only:
            sql += " AND t.local_path IS NOT NULL AND t.synced_to_ipod = 0"

        sql += " ORDER BY pt.track_order ASC;"

        tracks = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (playlist_id,))
            for row in cursor.fetchall():
                tracks.append(self._row_to_track(row))
            return tracks
        except sqlite3.Error as e:
            logger.error(f"Error in get_playlist_tracks: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    def get_tracks_for_playlist(self, playlist_id: str) -> List[Track]:
        """Alias for get_playlist_tracks."""
        return self.get_playlist_tracks(playlist_id)

    # --- Download Queue Methods ---

    def queue_download(self, item: DownloadItem):
        """Adds a track to the download queue."""
        sql = """
        INSERT OR IGNORE INTO download_queue (search_query, playlist_id, mbid_guess, status, last_attempt)
        VALUES (?, ?, ?, ?, ?)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                sql,
                (
                    item.search_query,
                    item.playlist_id,
                    item.mbid_guess,
                    item.status,
                    item.last_attempt,
                ),
            )
            self.conn.commit()
            logger.debug(f"Queued download: {item.search_query}")
        except sqlite3.Error as e:
            logger.error(f"Error in queue_download: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_downloads(self, status: str) -> List[DownloadItem]:
        """Gets download queue items with a specific status."""
        if status not in ("pending", "failed"):
            raise ValueError("Status must be 'pending' or 'failed'")

        sql = "SELECT * FROM download_queue WHERE status = ?"
        items = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (status,))
            for row in cursor.fetchall():
                items.append(self._row_to_download_item(row))
            return items
        except sqlite3.Error as e:
            logger.error(f"Error in get_downloads: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    def update_download_status(self, item_id: int, status: str):
        """Updates the status of a download item."""
        if status not in ("pending", "failed", "success"):
            raise ValueError("Status must be 'pending', 'failed', or 'success'")

        sql = "UPDATE download_queue SET status = ?, last_attempt = ? WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (status, datetime.now(), item_id))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error in update_download_status: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def remove_from_queue(self, item_id: int):
        """Removes an item from the download queue."""
        sql = "DELETE FROM download_queue WHERE id = ?"
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (item_id,))
            self.conn.commit()
            logger.debug(f"Removed item {item_id} from queue")
        except sqlite3.Error as e:
            logger.error(f"Error in remove_from_queue: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_download_queue_count(self) -> int:
        """
        Returns the number of tracks currently in the queue with status 'pending' or 'failed'.
        """
        sql = (
            "SELECT COUNT(*) FROM download_queue WHERE status IN ('pending', 'failed')"
        )
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            # fetchone() returns a tuple, e.g., (10,)
            count = cursor.fetchone()[0]
            logger.debug(f"Remaining download queue items: {count}")
            return count
        except sqlite3.Error as e:
            logger.error(f"Error in get_download_queue_count: {e}")
            # Returning -1 or raising an exception is a good indicator of an error
            raise RuntimeError(f"Database error during queue count: {e}") from e
        finally:
            if cursor:
                cursor.close()

    def log_duplicate(self, mbid: str, file_path: str):
        """
        Logs a file path as a potential duplicate for a given MBID.
        Uses INSERT OR IGNORE to avoid errors on unique constraint violations.
        """
        sql = "INSERT OR IGNORE INTO duplicates (mbid, file_path) VALUES (?, ?)"
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (mbid, file_path))
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to log duplicate {mbid} for {file_path}: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    def get_all_duplicates(self) -> Dict[str, List[str]]:
        """
        Fetches all duplicate entries, grouped by MBID.
        :return: A dictionary mapping {mbid -> [list_of_conflicting_paths]}
        """
        sql = "SELECT mbid, file_path FROM duplicates ORDER BY mbid"
        duplicates_map = defaultdict(list)
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                duplicates_map[row["mbid"]].append(row["file_path"])
            return dict(duplicates_map)  # Convert back to standard dict for output
        except sqlite3.Error as e:
            logger.error(f"Error in get_all_duplicates: {e}")
            return {}
        finally:
            if cursor:
                cursor.close()

    def clear_duplicate(self, mbid: str, file_path: Optional[str] = None):
        """
        Removes duplicate entries.
        If file_path is provided, removes that specific entry.
        If file_path is None, removes all entries for that mbid.
        """
        sql = "DELETE FROM duplicates WHERE mbid = ?"
        params = [mbid]

        if file_path:
            sql += " AND file_path = ?"
            params.append(file_path)

        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, tuple(params))
            self.conn.commit()
            logger.debug(f"Cleared duplicate entries for {mbid}")
        except sqlite3.Error as e:
            logger.error(f"Error in clear_duplicate: {e}")
            self.conn.rollback()
            raise
        finally:
            if cursor:
                cursor.close()

    # --- Context Manager Support ---

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


if __name__ == "__main__":
    from logger_setup import setup_logging

    setup_logging()

    db_file = "dap_library.db"
    logger.info(f"Initializing database at '{db_file}'")

    try:
        with DatabaseManager(db_file) as db:
            logger.info("DatabaseManager initialized successfully")
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
