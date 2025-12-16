
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
    # New Fields for Completer
    release_mbid: Optional[str] = None
    track_number: int = 0
    disc_number: int = 1

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
                    ipod_path TEXT,
                    synced_to_ipod INTEGER DEFAULT 0,
                    release_mbid TEXT,
                    track_number INTEGER,
                    disc_number INTEGER
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

    # --- Track Methods ---
    def add_or_update_track(self, track: Track):
        # Normalize path to ensure consistency (force forward slashes)
        if track.local_path:
            track.local_path = os.path.normpath(track.local_path).replace("\\", "/")

        sql = """
        INSERT OR REPLACE INTO tracks 
        (mbid, title, artist, album, isrc, local_path, ipod_path, synced_to_ipod, release_mbid, track_number, disc_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    track.release_mbid,
                    track.track_number,
                    track.disc_number,
                ),
            )
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error adding track: {e}")
            self.conn.rollback()
        finally:
            if cursor:
                cursor.close()

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
                "UPDATE tracks SET release_mbid = ?, album = ? WHERE release_mbid = ?",
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
        sql = "INSERT OR REPLACE INTO playlists (playlist_id, name, spotify_url) VALUES (?, ?, ?)"
        cursor = self.conn.cursor()
        cursor.execute(sql, (playlist.playlist_id, playlist.name, playlist.spotify_url))
        self.conn.commit()
        cursor.close()

    def queue_download(self, item: DownloadItem):
        sql = "INSERT OR IGNORE INTO download_queue (search_query, playlist_id, mbid_guess, status) VALUES (?, ?, ?, ?)"
        cursor = self.conn.cursor()
        cursor.execute(
            sql, (item.search_query, item.playlist_id, item.mbid_guess, item.status)
        )
        self.conn.commit()
        cursor.close()

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

    def mark_track_synced(self, mbid: str, ipod_path: str):
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET synced_to_ipod = 1, ipod_path = ? WHERE mbid = ?",
            (ipod_path, mbid),
        )
        self.conn.commit()
        cursor.close()

    def get_all_tracks(self, local_only: bool = False):
        sql = "SELECT * FROM tracks"
        if local_only:
            sql += " WHERE local_path IS NOT NULL"
        cursor = self.conn.cursor()
        cursor.execute(sql)
        tracks = [self._row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def get_all_playlists(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM playlists")
        playlists = [self._row_to_playlist(row) for row in cursor.fetchall()]
        cursor.close()
        return playlists

    def get_playlist_tracks(self, playlist_id: str):
        sql = "SELECT t.* FROM tracks t JOIN playlist_tracks pt ON t.mbid = pt.track_mbid WHERE pt.playlist_id = ? ORDER BY pt.track_order"
        cursor = self.conn.cursor()
        cursor.execute(sql, (playlist_id,))
        tracks = [self._row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def get_tracks_for_playlist(self, playlist_id: str):
        return self.get_playlist_tracks(playlist_id)

    def link_track_to_playlist(self, playlist_id: str, track_mbid: str, order: int):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO playlist_tracks (playlist_id, track_mbid, track_order) VALUES (?, ?, ?)",
            (playlist_id, track_mbid, order),
        )
        self.conn.commit()
        cursor.close()

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
        cursor.execute("UPDATE tracks SET local_path = ? WHERE mbid = ?", (path, mbid))
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
            ipod_path=row["ipod_path"],
            synced_to_ipod=bool(row["synced_to_ipod"]),
            release_mbid=row["release_mbid"],
            track_number=row["track_number"],
            disc_number=row["disc_number"],
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

    def get_all_downloads(self) -> List[DownloadItem]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM download_queue ORDER BY id DESC")
        items = [self._row_to_download_item(row) for row in cursor.fetchall()]
        cursor.close()
        return items

    def close(self):
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
