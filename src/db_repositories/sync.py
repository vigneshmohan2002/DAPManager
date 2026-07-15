"""Sync cursor persistence kept behind the public database façade."""

from typing import Dict, List, Optional

from .base import SQLiteRepository


class SyncRepository(SQLiteRepository):
    def get_current_timestamp(self) -> Optional[str]:
        cursor = self.conn.cursor()
        try:
            row = cursor.execute(
                "SELECT CURRENT_TIMESTAMP AS ts"
            ).fetchone()
            return row["ts"] if row is not None else None
        finally:
            cursor.close()

    def get_state(self, key: str) -> Optional[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT value FROM sync_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None
        finally:
            cursor.close()

    def set_state(self, key: str, value: str) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def mark_track_synced(self, mbid: str, dap_path: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET synced_to_dap = 1, dap_path = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (dap_path, mbid),
        )
        self.conn.commit()
        cursor.close()

    def get_catalog_since(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        sql = (
            "SELECT mbid, title, artist, album, isrc, release_mbid, "
            "track_number, disc_number, is_liked, updated_at, deleted_at "
            "FROM tracks"
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

    def apply_catalog_row(self, row: Dict[str, object]) -> str:
        mbid = row.get("mbid")
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM tracks WHERE mbid = ?", (mbid,))
        existed = cursor.fetchone() is not None
        cursor.execute(
            """
            INSERT INTO tracks
                (mbid, title, artist, album, isrc, release_mbid,
                 track_number, disc_number, is_liked, updated_at, deleted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
            ON CONFLICT(mbid) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                album = excluded.album,
                isrc = excluded.isrc,
                release_mbid = excluded.release_mbid,
                track_number = excluded.track_number,
                disc_number = excluded.disc_number,
                is_liked = excluded.is_liked,
                updated_at = excluded.updated_at,
                deleted_at = excluded.deleted_at
            """,
            (
                mbid,
                row.get("title") or "Unknown Title",
                row.get("artist") or "Unknown Artist",
                row.get("album"),
                row.get("isrc"),
                row.get("release_mbid"),
                row.get("track_number") or 0,
                row.get("disc_number") or 1,
                1 if row.get("is_liked") else 0,
                row.get("updated_at"),
                row.get("deleted_at"),
            ),
        )
        self.conn.commit()
        cursor.close()
        return "updated" if existed else "inserted"
