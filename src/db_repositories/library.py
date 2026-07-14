"""Library read queries kept behind the public database façade."""

import sqlite3
from typing import Dict, List, Optional

from .base import SQLiteRepository


class LibraryRepository(SQLiteRepository):
    def fetch_track_by_mbid(self, mbid: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT * FROM tracks WHERE mbid = ?", (mbid,))
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetch_track_by_path(self, local_path: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM tracks WHERE local_path = ?", (local_path,)
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def list_albums(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
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
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def list_all_tracks(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    mbid, title, artist, album, track_number, disc_number,
                    local_path, dap_path, is_liked,
                    COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
                FROM tracks
                WHERE deleted_at IS NULL
                ORDER BY artist COLLATE NOCASE,
                         album COLLATE NOCASE,
                         COALESCE(disc_number, 1),
                         COALESCE(track_number, 9999),
                         title COLLATE NOCASE
                """
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_track_sources(self, mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT local_path, dap_path FROM tracks "
                "WHERE mbid = ? AND deleted_at IS NULL",
                (mbid,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_track_local_path(self, mbid: str) -> Optional[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT local_path FROM tracks "
                "WHERE mbid = ? AND deleted_at IS NULL",
                (mbid,),
            )
            row = cursor.fetchone()
            return row["local_path"] if row and row["local_path"] else None
        finally:
            cursor.close()
