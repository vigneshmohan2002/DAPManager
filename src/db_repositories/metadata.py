"""Lyrics and artist-tag reads behind ``DatabaseManager``."""

from typing import Dict, List, Optional

from .base import SQLiteRepository


class MetadataRepository(SQLiteRepository):
    def get_lyrics(self, track_mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.execute(
            "SELECT track_mbid, lrc, synced, source, fetched_at "
            "FROM lyrics WHERE track_mbid = ?",
            (track_mbid,),
        )
        try:
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_lyrics_since(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        sql = "SELECT track_mbid, lrc, synced, source, fetched_at FROM lyrics"
        params: tuple = ()
        if since_iso:
            sql += " WHERE fetched_at > ?"
            params = (since_iso,)
        sql += " ORDER BY fetched_at ASC"
        cursor = self.conn.execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_distinct_artist_names(self) -> List[str]:
        cursor = self.conn.execute(
            "SELECT DISTINCT artist FROM tracks "
            "WHERE deleted_at IS NULL "
            "  AND artist IS NOT NULL AND artist != '' "
            "ORDER BY artist COLLATE NOCASE"
        )
        try:
            return [row["artist"] for row in cursor.fetchall()]
        finally:
            cursor.close()
