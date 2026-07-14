"""Device inventory and fleet lookup persistence."""

import sqlite3
from typing import Dict, List

from .base import SQLiteRepository


class InventoryRepository(SQLiteRepository):
    def replace(self, device_id: str, items: List[dict]) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                "DELETE FROM device_inventory WHERE device_id = ?",
                (device_id,),
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

    def get_device(self, device_id: str) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid, local_path, reported_at FROM device_inventory "
            "WHERE device_id = ? ORDER BY mbid",
            (device_id,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_fleet_summary(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT device_id, COUNT(*) AS track_count, "
            "MAX(reported_at) AS last_reported_at "
            "FROM device_inventory "
            "GROUP BY device_id "
            "ORDER BY device_id"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_devices_holding_mbid(
        self, mbid: str
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT device_id, local_path, reported_at FROM device_inventory "
            "WHERE mbid = ? ORDER BY device_id",
            (mbid,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def find_tracks(
        self, query: str, limit: int
    ) -> List[Dict[str, object]]:
        term = f"%{query}%"
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT t.mbid, t.artist, t.title, t.album, "
            "       (SELECT COUNT(*) FROM device_inventory d "
            "        WHERE d.mbid = t.mbid) AS device_count "
            "FROM tracks t "
            "WHERE t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ? "
            "ORDER BY device_count DESC, t.artist, t.album, t.title "
            "LIMIT ?",
            (term, term, term, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
