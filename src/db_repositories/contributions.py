"""Contribution state persistence behind ``DatabaseManager``."""

from typing import Dict, List, Optional

from .base import SQLiteRepository


class ContributionRepository(SQLiteRepository):
    def create(
        self,
        *,
        device_id: Optional[str],
        mbid: Optional[str],
        isrc: Optional[str],
        artist: Optional[str],
        title: Optional[str],
        album: Optional[str],
        target_quality: Optional[str],
        status: str,
        download_id: Optional[int],
        acquired_quality: Optional[str],
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO contributions "
                "(device_id, mbid, isrc, artist, title, album, target_quality, "
                " acquired_quality, status, download_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    mbid,
                    isrc,
                    artist,
                    title,
                    album,
                    target_quality,
                    acquired_quality,
                    status,
                    download_id,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid or 0
        finally:
            cursor.close()

    def get(self, contribution_id: int) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributions WHERE id = ?", (contribution_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def list(self, limit: int) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributions ORDER BY updated_at DESC, id DESC "
                "LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def update(self, contribution_id: int, fields: Dict[str, object]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [contribution_id]
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"UPDATE contributions SET {assignments}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )
            self.conn.commit()
        finally:
            cursor.close()

    def upsert_contributed(
        self,
        mbid: str,
        contribution_id: Optional[int],
        status: Optional[str],
    ) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO contributed "
                "(mbid, contribution_id, status, updated_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(mbid) DO UPDATE SET "
                "contribution_id = excluded.contribution_id, "
                "status = excluded.status, updated_at = CURRENT_TIMESTAMP",
                (mbid, contribution_id, status),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def get_contributed(self, mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT * FROM contributed WHERE mbid = ?", (mbid,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_pending_contributed(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributed WHERE status IS NULL OR status NOT IN "
                "('have_better', 'satisfied', 'ingested')"
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def list_contributed(self, limit: int) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT c.rowid AS local_id, c.mbid, c.contribution_id, "
                "c.status, c.updated_at, t.artist, t.title, t.album "
                "FROM contributed c LEFT JOIN tracks t ON t.mbid = c.mbid "
                "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
