"""Download-queue persistence behind ``DatabaseManager``."""

from datetime import datetime
import sqlite3
from typing import Callable, List, Optional, Set

from .base import SQLiteRepository


class DownloadRepository(SQLiteRepository):
    def has_queued_mbid(self, mbid: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM download_queue "
            "WHERE mbid_guess = ? COLLATE NOCASE LIMIT 1",
            (mbid,),
        )
        row = cursor.fetchone()
        cursor.close()
        return row is not None

    def get_active_download_id(
        self,
        mbid: Optional[str],
        search_query: str,
        normalize_query: Callable[[str], str],
    ) -> Optional[int]:
        cursor = self.conn.cursor()
        try:
            if mbid:
                cursor.execute(
                    "SELECT id FROM download_queue WHERE mbid_guess = ? "
                    "COLLATE NOCASE "
                    "AND status IN ('pending', 'failed') ORDER BY id LIMIT 1",
                    (mbid,),
                )
                row = cursor.fetchone()
                if row:
                    return row["id"]

            target = normalize_query(search_query)
            if not target:
                return None
            cursor.execute(
                "SELECT id, search_query FROM download_queue "
                "WHERE status IN ('pending', 'failed')"
            )
            for row in cursor.fetchall():
                if normalize_query(row["search_query"]) == target:
                    return row["id"]
            return None
        except sqlite3.Error:
            return None
        finally:
            cursor.close()

    def is_queued(
        self,
        search_query: str,
        normalize_query: Callable[[str], str],
    ) -> bool:
        target = normalize_query(search_query)
        if not target:
            return False
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT search_query FROM download_queue "
                "WHERE status IN ('pending', 'failed')"
            )
            for row in cursor.fetchall():
                if normalize_query(row["search_query"]) == target:
                    cursor.close()
                    return True
            cursor.close()
            return False
        except sqlite3.Error:
            return False

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

    def claim_for_album_request(
        self,
        item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
    ) -> bool:
        """Convert compatible pending/failed work into a tracked album row."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET search_query = ?, playlist_id = ?, "
                "status = 'pending' WHERE id = ? AND mbid_guess = ? "
                "COLLATE NOCASE "
                "AND status IN ('pending', 'failed')",
                (search_query, playlist_id, int(item_id), release_mbid),
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

    def get_queued_release_mbids(self) -> Set[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT mbid_guess FROM download_queue "
            "WHERE mbid_guess IS NOT NULL AND mbid_guess != ''"
        )
        result = {row["mbid_guess"] for row in cursor.fetchall()}
        cursor.close()
        return result

    def get_existing_release_mbids(self) -> Set[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT release_mbid FROM tracks "
            "WHERE release_mbid IS NOT NULL AND release_mbid != '' "
            "AND deleted_at IS NULL"
        )
        result = {row["release_mbid"] for row in cursor.fetchall()}
        cursor.close()
        return result
