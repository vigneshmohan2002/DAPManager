"""Listening-history persistence and simple aggregates."""

from typing import cast, Dict, List, Optional

from .base import SQLiteRepository


class ListeningRepository(SQLiteRepository):
    def record_event(
        self,
        track_mbid: str,
        source: Optional[str],
        listened_ms: Optional[int],
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO play_events (track_mbid, source, listened_ms) "
                "VALUES (?, ?, ?)",
                (track_mbid, source, listened_ms),
            )
            event_id = cursor.lastrowid
            self.conn.commit()
            return cast(int, event_id)
        finally:
            cursor.close()

    def plays_by_hour(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT CAST(strftime('%H', played_at) AS INTEGER) AS hour, "
                    "       COUNT(*) AS plays "
                    "FROM play_events WHERE played_at >= ? "
                    "GROUP BY hour ORDER BY hour",
                    (since_iso,),
                )
            else:
                cursor.execute(
                    "SELECT CAST(strftime('%H', played_at) AS INTEGER) AS hour, "
                    "       COUNT(*) AS plays "
                    "FROM play_events "
                    "GROUP BY hour ORDER BY hour"
                )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def listening_time_since(self, since_iso: Optional[str]) -> int:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT COALESCE(SUM(listened_ms), 0) FROM play_events "
                    "WHERE listened_ms IS NOT NULL AND played_at >= ?",
                    (since_iso,),
                )
            else:
                cursor.execute(
                    "SELECT COALESCE(SUM(listened_ms), 0) FROM play_events "
                    "WHERE listened_ms IS NOT NULL"
                )
            return int(cursor.fetchone()[0] or 0)
        finally:
            cursor.close()

    def play_count_since(self, since_iso: Optional[str]) -> int:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT COUNT(*) FROM play_events WHERE played_at >= ?",
                    (since_iso,),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM play_events")
            return int(cursor.fetchone()[0] or 0)
        finally:
            cursor.close()
