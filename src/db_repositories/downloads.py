"""Download-queue persistence behind ``DatabaseManager``."""

from datetime import datetime
import sqlite3
from typing import List, Optional

from .base import SQLiteRepository


class DownloadRepository(SQLiteRepository):
    def queue(
        self,
        search_query: str,
        playlist_id: str,
        mbid_guess: str,
        status: str,
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO download_queue "
                "(search_query, playlist_id, mbid_guess, status) "
                "VALUES (?, ?, ?, ?)",
                (search_query, playlist_id, mbid_guess, status),
            )
            self.conn.commit()
            return cursor.lastrowid or 0
        finally:
            cursor.close()

    def fetch_by_status(self, status: str) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM download_queue WHERE status = ?", (status,)
            )
            return cursor.fetchall()
        finally:
            cursor.close()

    def get_status(self, download_id: int) -> Optional[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT status FROM download_queue WHERE id = ?", (download_id,)
            )
            row = cursor.fetchone()
            return row["status"] if row else None
        finally:
            cursor.close()

    def update_status(self, item_id: int, status: str) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET status = ?, last_attempt = ? "
                "WHERE id = ?",
                (status, datetime.now(), item_id),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def remove(self, item_id: int) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute("DELETE FROM download_queue WHERE id = ?", (item_id,))
            self.conn.commit()
        finally:
            cursor.close()

    def active_count(self) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM download_queue "
                "WHERE status IN ('pending', 'failed')"
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def fetch_all(self) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT * FROM download_queue ORDER BY id DESC")
            return cursor.fetchall()
        finally:
            cursor.close()

    def retry(self, item_id: int) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET status = 'pending' "
                "WHERE id = ? AND status = 'failed'",
                (item_id,),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def delete_succeeded(self) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute("DELETE FROM download_queue WHERE status = 'success'")
            removed = cursor.rowcount
            self.conn.commit()
            return removed
        finally:
            cursor.close()
