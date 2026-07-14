"""Sync cursor persistence kept behind the public database façade."""

from typing import Optional

from .base import SQLiteRepository


class SyncRepository(SQLiteRepository):
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
