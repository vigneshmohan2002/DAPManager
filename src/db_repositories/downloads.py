"""Download-queue persistence behind ``DatabaseManager``."""

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any, Callable, Collection, List, Mapping, Optional, Sequence, Set, Tuple

from .base import SQLiteRepository
from src.download_identity import download_target_key


def _utc_naive(value: Optional[datetime] = None) -> datetime:
    """Return a naive UTC datetime suitable for SQLite text comparison."""
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is not None:
        resolved = resolved.astimezone(timezone.utc).replace(tzinfo=None)
    return resolved


def _sql_timestamp(value: datetime) -> str:
    return value.isoformat(sep=" ", timespec="microseconds")


class DownloadRepository(SQLiteRepository):
    @staticmethod
    def _claim_eligibility(
        now_timestamp: str,
        include_item_ids: Optional[Collection[int]],
        exclude_item_ids: Collection[int],
    ) -> Tuple[Optional[str], List[object]]:
        filters = [
            "status IN ('pending', 'failed')",
            "is_paused = 0",
            "is_quarantined = 0",
            "attempt_count < max_attempts",
            "(next_attempt_at IS NULL OR next_attempt_at <= ?)",
            "(claim_owner IS NULL OR claim_expires_at IS NULL "
            "OR claim_expires_at <= ?)",
        ]
        parameters: List[object] = [now_timestamp, now_timestamp]

        if include_item_ids is not None:
            included = sorted({int(item_id) for item_id in include_item_ids})
            if not included:
                return None, []
            placeholders = ", ".join("?" for _ in included)
            filters.append(f"id IN ({placeholders})")
            parameters.extend(included)

        excluded = sorted({int(item_id) for item_id in exclude_item_ids})
        if excluded:
            placeholders = ", ".join("?" for _ in excluded)
            filters.append(f"id NOT IN ({placeholders})")
            parameters.extend(excluded)

        return " AND ".join(filters), parameters

    @staticmethod
    def _recover_stale_claims(
        cursor: sqlite3.Cursor,
        now_timestamp: str,
    ) -> List[int]:
        """Release expired owners and return their queue IDs to the caller."""
        cursor.execute(
            "SELECT id FROM download_queue WHERE claim_owner IS NOT NULL "
            "AND (claim_expires_at IS NULL OR claim_expires_at <= ?) "
            "ORDER BY id",
            (now_timestamp,),
        )
        recovered_ids = [int(row["id"]) for row in cursor.fetchall()]
        if not recovered_ids:
            return []
        cursor.execute(
            "UPDATE album_download_requests SET stage = 'failed', "
            "detail = 'Processing lease expired before completion', "
            "updated_at = CURRENT_TIMESTAMP WHERE stage != 'success' "
            "AND queue_item_id IN ("
            "SELECT id FROM download_queue WHERE claim_owner IS NOT NULL "
            "AND (claim_expires_at IS NULL OR claim_expires_at <= ?))",
            (now_timestamp,),
        )
        cursor.execute(
            "UPDATE download_queue SET "
            "status = CASE WHEN status = 'success' THEN status ELSE 'failed' END, "
            "next_attempt_at = CASE "
            "WHEN status = 'success' OR attempt_count >= max_attempts THEN NULL "
            "ELSE ? END, "
            "is_quarantined = CASE "
            "WHEN status != 'success' AND attempt_count >= max_attempts THEN 1 "
            "ELSE is_quarantined END, "
            "last_error = CASE WHEN status = 'success' THEN last_error "
            "ELSE 'Processing lease expired before completion' END, "
            "claim_owner = NULL, claim_expires_at = NULL, "
            "claim_heartbeat_at = NULL "
            "WHERE claim_owner IS NOT NULL "
            "AND (claim_expires_at IS NULL OR claim_expires_at <= ?)",
            (now_timestamp, now_timestamp),
        )
        return recovered_ids

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
        target_key = download_target_key(search_query, playlist_id, mbid_guess)
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO download_queue "
                "(search_query, playlist_id, mbid_guess, status, target_key) "
                "VALUES (?, ?, ?, ?, ?)",
                (search_query, playlist_id, mbid_guess, status, target_key),
            )
            item_id = int(cursor.lastrowid or 0)
            if not item_id and target_key:
                cursor.execute(
                    "SELECT id FROM download_queue WHERE target_key = ? "
                    "AND status IN ('pending', 'failed') ORDER BY id LIMIT 1",
                    (target_key,),
                )
                row = cursor.fetchone()
                item_id = int(row["id"]) if row else 0
            self.conn.commit()
            return item_id
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
            if status in {"failed", "success"}:
                cursor.execute(
                    "UPDATE download_queue SET status = ?, last_attempt = ?, "
                    "claim_owner = NULL, claim_expires_at = NULL, "
                    "claim_heartbeat_at = NULL WHERE id = ?",
                    (status, datetime.now(), item_id),
                )
            else:
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

    def fetch_one(self, item_id: int) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM download_queue WHERE id = ?",
                (item_id,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def retry(self, item_id: int) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET status = 'pending', "
                "attempt_count = 0, next_attempt_at = NULL, "
                "claim_owner = NULL, claim_expires_at = NULL, "
                "claim_heartbeat_at = NULL, is_paused = 0, "
                "is_quarantined = 0, last_error = NULL, phase = 'queued', "
                "failure_class = NULL, blocked_reason = NULL, strategy_index = 0 "
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
                "status = 'pending', attempt_count = 0, "
                "next_attempt_at = NULL, claim_owner = NULL, "
                "claim_expires_at = NULL, claim_heartbeat_at = NULL, "
                "is_paused = 0, is_quarantined = 0, last_error = NULL, "
                "phase = 'queued', failure_class = NULL, blocked_reason = NULL, "
                "strategy_index = 0 "
                "WHERE id = ? AND mbid_guess = ? "
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

    def claim_next(
        self,
        owner: str,
        lease_seconds: int = 900,
        now: Optional[datetime] = None,
        *,
        consume_attempt: bool = True,
        include_item_ids: Optional[Collection[int]] = None,
        exclude_item_ids: Collection[int] = (),
    ) -> Optional[sqlite3.Row]:
        """Atomically claim the next due row and consume one attempt.

        A write transaction serializes candidate selection across independent
        ``DatabaseManager`` instances. Expired claims are recovered inside the
        same transaction, so a replacement owner can claim them immediately.
        """
        normalized_owner = str(owner or "").strip()
        if not normalized_owner:
            raise ValueError("claim owner must not be blank")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")

        claimed_at = _utc_naive(now)
        claimed_timestamp = _sql_timestamp(claimed_at)
        expires_timestamp = _sql_timestamp(
            claimed_at + timedelta(seconds=lease_seconds)
        )
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            self._recover_stale_claims(cursor, claimed_timestamp)
            eligibility, parameters = self._claim_eligibility(
                claimed_timestamp,
                include_item_ids,
                exclude_item_ids,
            )
            if eligibility is None:
                self.conn.commit()
                return None

            cursor.execute(
                "SELECT id FROM download_queue WHERE " + eligibility + " "
                "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id "
                "LIMIT 1",
                parameters,
            )
            candidate = cursor.fetchone()
            if candidate is None:
                self.conn.commit()
                return None

            cursor.execute(
                "UPDATE download_queue SET status = 'pending', "
                "attempt_count = attempt_count + ?, "
                "last_attempt = CASE WHEN ? THEN ? ELSE last_attempt END, "
                "next_attempt_at = NULL, claim_owner = ?, phase = 'claimed', "
                "claim_expires_at = ?, claim_heartbeat_at = ? "
                "WHERE id = ?",
                (
                    int(consume_attempt),
                    int(consume_attempt),
                    claimed_timestamp,
                    normalized_owner,
                    expires_timestamp,
                    claimed_timestamp,
                    candidate["id"],
                ),
            )
            cursor.execute(
                "SELECT * FROM download_queue WHERE id = ?",
                (candidate["id"],),
            )
            row = cursor.fetchone()
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def start_network_attempt(
        self,
        item_id: int,
        owner: str,
        strategy_index: int,
        now: Optional[datetime] = None,
    ) -> Optional[sqlite3.Row]:
        """Consume retry budget only immediately before starting sldl."""
        attempted_at = _sql_timestamp(_utc_naive(now))
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "UPDATE download_queue SET attempt_count = attempt_count + 1, "
                "last_attempt = ?, strategy_index = ?, phase = 'acquiring', "
                "failure_class = NULL, blocked_reason = NULL "
                "WHERE id = ? AND claim_owner = ? AND claim_expires_at > ? "
                "AND attempt_count < max_attempts",
                (
                    attempted_at,
                    max(0, int(strategy_index)),
                    int(item_id),
                    str(owner),
                    attempted_at,
                ),
            )
            if cursor.rowcount == 0:
                self.conn.commit()
                return None
            cursor.execute("SELECT * FROM download_queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            self.conn.commit()
            return row
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def create_attempt(
        self,
        item_id: int,
        target_key: str,
        *,
        strategy: str = "",
        phase: str = "claimed",
        network_started: bool = False,
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO download_attempts (queue_item_id, target_key, "
                "strategy, phase, network_started) VALUES (?, ?, ?, ?, ?)",
                (item_id, target_key, strategy, phase, int(network_started)),
            )
            attempt_id = int(cursor.lastrowid)
            self.conn.commit()
            return attempt_id
        finally:
            cursor.close()

    def update_attempt(self, attempt_id: int, values: Mapping[str, Any]) -> bool:
        allowed = {
            "strategy", "phase", "outcome", "failure_class", "detail",
            "network_started", "bytes_staged", "files_staged",
            "files_validated", "files_imported", "finished_at", "retry_at",
        }
        updates = [(key, values[key]) for key in values if key in allowed]
        if not updates:
            return False
        assignments = ", ".join(f"{key} = ?" for key, _ in updates)
        parameters = [
            int(value) if key == "network_started" else value
            for key, value in updates
        ]
        parameters.append(int(attempt_id))
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"UPDATE download_attempts SET {assignments} WHERE id = ?",
                parameters,
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def list_attempts(self, item_id: int, limit: int = 50) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM download_attempts WHERE queue_item_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (int(item_id), max(1, min(int(limit), 200))),
            )
            return cursor.fetchall()
        finally:
            cursor.close()

    def record_attempt_files(
        self,
        attempt_id: int,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.executemany(
                "INSERT OR REPLACE INTO download_attempt_files "
                "(attempt_id, relative_name, manifest_position, recording_mbid, "
                "release_mbid, audio_sha256, acoustic_result, acoustic_score, "
                "decision, reason, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(
                    int(attempt_id),
                    str(row.get("relative_name") or "")[:1000],
                    row.get("manifest_position"),
                    row.get("recording_mbid"),
                    row.get("release_mbid"),
                    row.get("audio_sha256"),
                    row.get("acoustic_result"),
                    row.get("acoustic_score"),
                    str(row.get("decision") or "candidate")[:100],
                    str(row.get("reason") or "")[-2000:],
                    max(0, int(row.get("bytes") or 0)),
                ) for row in rows if row.get("relative_name")],
            )
            self.conn.commit()
        finally:
            cursor.close()

    def finalize_attempt_files(
        self,
        attempt_id: int,
        decision: str,
        reason: str = "",
    ) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_attempt_files SET decision = ?, reason = ? "
                "WHERE attempt_id = ?",
                (str(decision)[:100], str(reason or "")[-2000:], int(attempt_id)),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def list_attempt_files(self, attempt_id: int) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM download_attempt_files WHERE attempt_id = ? "
                "ORDER BY relative_name",
                (int(attempt_id),),
            )
            return cursor.fetchall()
        finally:
            cursor.close()

    def get_worker_state(self) -> sqlite3.Row:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO download_worker_state(singleton_id) VALUES (1)"
            )
            cursor.execute(
                "SELECT * FROM download_worker_state WHERE singleton_id = 1"
            )
            row = cursor.fetchone()
            self.conn.commit()
            return row
        finally:
            cursor.close()

    def set_worker_paused(self, paused: bool) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO download_worker_state(singleton_id) VALUES (1)"
            )
            cursor.execute(
                "UPDATE download_worker_state SET is_paused = ?, "
                "state = CASE WHEN ? THEN 'paused' ELSE state END, "
                "updated_at = CURRENT_TIMESTAMP WHERE singleton_id = 1",
                (int(paused), int(paused)),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def claim_worker(
        self,
        owner: str,
        lease_seconds: int,
        now: Optional[datetime] = None,
    ) -> bool:
        claimed_at = _utc_naive(now)
        claimed_timestamp = _sql_timestamp(claimed_at)
        expires = _sql_timestamp(claimed_at + timedelta(seconds=lease_seconds))
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT OR IGNORE INTO download_worker_state(singleton_id) VALUES (1)"
            )
            cursor.execute(
                "UPDATE download_worker_state SET lease_owner = ?, "
                "lease_expires_at = ?, heartbeat_at = ?, state = 'idle', "
                "updated_at = ? WHERE singleton_id = 1 AND is_paused = 0 "
                "AND (lease_owner IS NULL OR lease_expires_at IS NULL "
                "OR lease_expires_at <= ? OR lease_owner = ?)",
                (
                    owner, expires, claimed_timestamp, claimed_timestamp,
                    claimed_timestamp, owner,
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def heartbeat_worker(
        self,
        owner: str,
        lease_seconds: int,
        *,
        state: str,
        current_item_id: Optional[int] = None,
        detail: str = "",
        next_wake_at: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        heartbeat = _utc_naive(now)
        heartbeat_timestamp = _sql_timestamp(heartbeat)
        expires = _sql_timestamp(heartbeat + timedelta(seconds=lease_seconds))
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_worker_state SET lease_expires_at = ?, "
                "heartbeat_at = ?, state = ?, current_item_id = ?, detail = ?, "
                "next_wake_at = ?, updated_at = ? WHERE singleton_id = 1 "
                "AND lease_owner = ? AND is_paused = 0",
                (
                    expires, heartbeat_timestamp, state, current_item_id,
                    str(detail or "")[-2000:], next_wake_at,
                    heartbeat_timestamp, owner,
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def release_worker(self, owner: str, state: str = "stopped") -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_worker_state SET lease_owner = NULL, "
                "lease_expires_at = NULL, heartbeat_at = NULL, state = ?, "
                "current_item_id = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE singleton_id = 1 AND lease_owner = ?",
                (state, owner),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def count_claimable(
        self,
        now: Optional[datetime] = None,
        *,
        include_item_ids: Optional[Collection[int]] = None,
        exclude_item_ids: Collection[int] = (),
    ) -> int:
        now_timestamp = _sql_timestamp(_utc_naive(now))
        eligibility, parameters = self._claim_eligibility(
            now_timestamp,
            include_item_ids,
            exclude_item_ids,
        )
        if eligibility is None:
            return 0
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM download_queue WHERE " + eligibility,
                parameters,
            )
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()

    def list_claimable_ids(
        self,
        limit: int,
        now: Optional[datetime] = None,
        *,
        exclude_item_ids: Collection[int] = (),
    ) -> List[int]:
        now_timestamp = _sql_timestamp(_utc_naive(now))
        eligibility, parameters = self._claim_eligibility(
            now_timestamp,
            None,
            exclude_item_ids,
        )
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT id FROM download_queue WHERE " + str(eligibility) + " "
                "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id "
                "LIMIT ?",
                [*parameters, max(1, min(int(limit), 20))],
            )
            return [int(row["id"]) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def recover_stale_claims(self, now: Optional[datetime] = None) -> List[int]:
        now_timestamp = _sql_timestamp(_utc_naive(now))
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            recovered = self._recover_stale_claims(cursor, now_timestamp)
            self.conn.commit()
            return recovered
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def heartbeat_claim(
        self,
        item_id: int,
        owner: str,
        lease_seconds: int = 900,
        now: Optional[datetime] = None,
    ) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        heartbeat_at = _utc_naive(now)
        heartbeat_timestamp = _sql_timestamp(heartbeat_at)
        expires_timestamp = _sql_timestamp(
            heartbeat_at + timedelta(seconds=lease_seconds)
        )
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET claim_heartbeat_at = ?, "
                "claim_expires_at = ? WHERE id = ? AND claim_owner = ? "
                "AND claim_expires_at > ?",
                (
                    heartbeat_timestamp,
                    expires_timestamp,
                    item_id,
                    owner,
                    heartbeat_timestamp,
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def release_claim(self, item_id: int, owner: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET claim_owner = NULL, "
                "claim_expires_at = NULL, claim_heartbeat_at = NULL "
                "WHERE id = ? AND claim_owner = ?",
                (item_id, owner),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def fail_claim(
        self,
        item_id: int,
        owner: str,
        error_message: str = "",
        *,
        quarantine: bool = False,
        failure_class: str = "retryable",
        blocked_reason: str = "",
        advance_strategy: bool = False,
        base_delay_seconds: int = 300,
        max_delay_seconds: int = 86400,
        now: Optional[datetime] = None,
    ) -> bool:
        """Fence a failure by owner and schedule bounded exponential retry."""
        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must not be negative")
        if max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must not be negative")

        failed_at = _utc_naive(now)
        failed_timestamp = _sql_timestamp(failed_at)
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT attempt_count, max_attempts FROM download_queue "
                "WHERE id = ? AND claim_owner = ? AND claim_expires_at > ?",
                (item_id, owner, failed_timestamp),
            )
            row = cursor.fetchone()
            if row is None:
                self.conn.commit()
                return False

            attempt_count = max(0, int(row["attempt_count"] or 0))
            max_attempts = max(1, int(row["max_attempts"] or 1))
            is_capped = attempt_count >= max_attempts
            should_quarantine = bool(quarantine or is_capped)
            next_attempt_timestamp = None
            if not should_quarantine:
                exponent = min(max(attempt_count - 1, 0), 30)
                delay_seconds = min(
                    max_delay_seconds,
                    base_delay_seconds * (2 ** exponent),
                )
                next_attempt_timestamp = _sql_timestamp(
                    failed_at + timedelta(seconds=delay_seconds)
                )

            cursor.execute(
                "UPDATE download_queue SET status = 'failed', "
                "last_attempt = ?, next_attempt_at = ?, "
                "last_error = ?, phase = 'failed', failure_class = ?, "
                "blocked_reason = ?, strategy_index = strategy_index + ?, "
                "is_quarantined = CASE WHEN ? THEN 1 ELSE is_quarantined END, "
                "claim_owner = NULL, claim_expires_at = NULL, "
                "claim_heartbeat_at = NULL WHERE id = ? AND claim_owner = ?",
                (
                    failed_timestamp,
                    next_attempt_timestamp,
                    str(error_message or "")[-4000:],
                    str(failure_class or "retryable")[-100:],
                    str(blocked_reason or "")[-1000:] or None,
                    int(bool(advance_strategy)),
                    int(should_quarantine),
                    item_id,
                    owner,
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def complete_claim(
        self,
        item_id: int,
        owner: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """Delete completed work only while ``owner`` holds a live lease."""
        completed_timestamp = _sql_timestamp(_utc_naive(now))
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM download_queue WHERE id = ? AND claim_owner = ? "
                "AND claim_expires_at > ?",
                (item_id, owner, completed_timestamp),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def set_retry_limit(self, item_id: int, max_attempts: int) -> bool:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET max_attempts = ?, "
                "is_quarantined = CASE "
                "WHEN status = 'failed' AND attempt_count >= ? THEN 1 "
                "ELSE is_quarantined END WHERE id = ?",
                (max_attempts, max_attempts, item_id),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def set_paused(self, item_id: int, paused: bool) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET is_paused = ?, "
                "claim_owner = CASE WHEN ? THEN NULL ELSE claim_owner END, "
                "claim_expires_at = CASE WHEN ? THEN NULL ELSE claim_expires_at END, "
                "claim_heartbeat_at = CASE "
                "WHEN ? THEN NULL ELSE claim_heartbeat_at END WHERE id = ?",
                (int(paused), int(paused), int(paused), int(paused), item_id),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def set_quarantined(self, item_id: int, quarantined: bool) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE download_queue SET is_quarantined = ?, "
                "next_attempt_at = CASE WHEN ? THEN NULL ELSE next_attempt_at END, "
                "claim_owner = CASE WHEN ? THEN NULL ELSE claim_owner END, "
                "claim_expires_at = CASE WHEN ? THEN NULL ELSE claim_expires_at END, "
                "claim_heartbeat_at = CASE "
                "WHEN ? THEN NULL ELSE claim_heartbeat_at END WHERE id = ?",
                (
                    int(quarantined),
                    int(quarantined),
                    int(quarantined),
                    int(quarantined),
                    int(quarantined),
                    item_id,
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()
