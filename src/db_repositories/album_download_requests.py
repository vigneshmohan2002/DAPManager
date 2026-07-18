"""Persistent progress records for MusicBrainz-backed album requests."""

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .base import SQLiteRepository


def _manifest_rows(
    request_id: int,
    recording_mbids: Sequence[str],
    track_manifest: Sequence[Mapping[str, Any]],
) -> List[Tuple[Any, ...]]:
    if not track_manifest:
        return [
            (
                request_id,
                position,
                recording_mbid,
                0,
                0,
                "",
                "",
                "",
                "",
                0,
                0,
                "",
            )
            for position, recording_mbid in enumerate(recording_mbids, start=1)
        ]
    rows: List[Tuple[Any, ...]] = []
    for fallback_position, track in enumerate(track_manifest, start=1):
        rows.append((
            request_id,
            int(track.get("position") or fallback_position),
            str(track.get("recording_mbid") or ""),
            int(track.get("medium_position") or 0),
            int(track.get("track_position") or 0),
            str(track.get("track_number") or ""),
            str(track.get("title") or ""),
            str(track.get("artist") or ""),
            str(track.get("date") or ""),
            int(track.get("track_total") or 0),
            int(track.get("disc_total") or 0),
            str(track.get("release_track_mbid") or ""),
        ))
    return rows


class AlbumDownloadRequestRepository(SQLiteRepository):
    _STAGES = frozenset({
        "queued",
        "downloading",
        "importing",
        "success",
        "failed",
    })

    def create(
        self,
        *,
        queue_item_id: Optional[int],
        release_mbid: str,
        artist: str,
        title: str,
        track_count: int,
        stage: str,
        detail: str,
        completed_tracks: int,
        recording_mbids: Sequence[str],
        track_manifest: Sequence[Mapping[str, Any]] = (),
    ) -> int:
        if stage not in self._STAGES:
            raise ValueError(f"invalid album request stage: {stage}")
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO album_download_requests "
                "(queue_item_id, release_mbid, artist, title, track_count, "
                " stage, detail, completed_tracks) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    queue_item_id,
                    release_mbid,
                    artist,
                    title,
                    max(0, int(track_count)),
                    stage,
                    detail,
                    max(0, int(completed_tracks)),
                ),
            )
            request_id = cursor.lastrowid or 0
            cursor.executemany(
                "INSERT INTO album_download_request_tracks "
                "(request_id, position, recording_mbid, medium_position, "
                " track_position, track_number, title, artist, date, "
                " track_total, disc_total, release_track_mbid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _manifest_rows(request_id, recording_mbids, track_manifest),
            )
            self.conn.commit()
            return request_id
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def claim_queue_and_create(
        self,
        *,
        queue_item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Sequence[str],
        track_manifest: Sequence[Mapping[str, Any]] = (),
    ) -> Optional[int]:
        """Claim compatible work and persist its tracker in one transaction."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "UPDATE download_queue SET search_query = ?, playlist_id = ?, "
                "status = 'pending' WHERE id = ? AND mbid_guess = ? "
                "COLLATE NOCASE AND status IN ('pending', 'failed')",
                (
                    search_query,
                    playlist_id,
                    int(queue_item_id),
                    release_mbid,
                ),
            )
            if cursor.rowcount != 1:
                self.conn.rollback()
                return None
            cursor.execute(
                "INSERT INTO album_download_requests "
                "(queue_item_id, release_mbid, artist, title, track_count, "
                " stage, detail, completed_tracks) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    int(queue_item_id),
                    release_mbid,
                    artist,
                    title,
                    max(0, int(track_count)),
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                ),
            )
            request_id = int(cursor.lastrowid or 0)
            cursor.executemany(
                "INSERT INTO album_download_request_tracks "
                "(request_id, position, recording_mbid, medium_position, "
                " track_position, track_number, title, artist, date, "
                " track_total, disc_total, release_track_mbid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _manifest_rows(request_id, recording_mbids, track_manifest),
            )
            self.conn.commit()
            return request_id
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def create_queue_and_request(
        self,
        *,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Sequence[str],
        track_manifest: Sequence[Mapping[str, Any]] = (),
    ) -> Tuple[int, int]:
        """Insert a queue row, tracker, and manifest as one visible unit."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO download_queue "
                "(search_query, playlist_id, status, mbid_guess) "
                "VALUES (?, ?, 'pending', ?)",
                (search_query, playlist_id, release_mbid),
            )
            queue_item_id = int(cursor.lastrowid or 0)
            cursor.execute(
                "INSERT INTO album_download_requests "
                "(queue_item_id, release_mbid, artist, title, track_count, "
                " stage, detail, completed_tracks) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
                (
                    queue_item_id,
                    release_mbid,
                    artist,
                    title,
                    max(0, int(track_count)),
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                ),
            )
            request_id = int(cursor.lastrowid or 0)
            cursor.executemany(
                "INSERT INTO album_download_request_tracks "
                "(request_id, position, recording_mbid, medium_position, "
                " track_position, track_number, title, artist, date, "
                " track_total, disc_total, release_track_mbid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _manifest_rows(request_id, recording_mbids, track_manifest),
            )
            self.conn.commit()
            return queue_item_id, request_id
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def create_queue_and_requeue(
        self,
        *,
        request_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        detail: str,
        completed_tracks: int,
    ) -> Optional[int]:
        """Replace failed work with a fresh canonical queue row atomically."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "SELECT queue_item_id FROM album_download_requests "
                "WHERE id = ? AND stage = 'failed'",
                (int(request_id),),
            )
            tracker = cursor.fetchone()
            if tracker is None:
                self.conn.rollback()
                return None
            old_queue_item_id = tracker["queue_item_id"]
            cursor.execute(
                "INSERT INTO download_queue "
                "(search_query, playlist_id, status, mbid_guess) "
                "VALUES (?, ?, 'pending', ?)",
                (search_query, playlist_id, release_mbid),
            )
            queue_item_id = int(cursor.lastrowid or 0)
            cursor.execute(
                "UPDATE album_download_requests SET queue_item_id = ?, "
                "stage = 'queued', detail = ?, completed_tracks = ?, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND stage = 'failed'",
                (
                    queue_item_id,
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                    int(request_id),
                ),
            )
            if cursor.rowcount != 1:
                self.conn.rollback()
                return None
            if old_queue_item_id is not None:
                cursor.execute(
                    "DELETE FROM download_queue WHERE id = ?",
                    (int(old_queue_item_id),),
                )
            self.conn.commit()
            return queue_item_id
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def _get(self, where: str, value: Any) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT r.*, q.status AS queue_status, "
                "q.last_attempt AS last_attempt "
                "FROM album_download_requests r "
                "LEFT JOIN download_queue q ON q.id = r.queue_item_id "
                f"WHERE {where} LIMIT 1",
                (value,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get(self, request_id: int) -> Optional[Dict[str, Any]]:
        return self._get("r.id = ?", int(request_id))

    def get_by_release(self, release_mbid: str) -> Optional[Dict[str, Any]]:
        return self._get("r.release_mbid = ?", release_mbid)

    def get_by_queue_item(self, queue_item_id: int) -> Optional[Dict[str, Any]]:
        return self._get("r.queue_item_id = ?", int(queue_item_id))

    def list_active(self, limit: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT r.*, q.status AS queue_status, "
                "q.last_attempt AS last_attempt "
                "FROM album_download_requests r "
                "LEFT JOIN download_queue q ON q.id = r.queue_item_id "
                "WHERE r.stage != 'success' "
                "ORDER BY r.updated_at DESC, r.id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_expected_recording_mbids(self, request_id: int) -> List[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT recording_mbid FROM album_download_request_tracks "
                "WHERE request_id = ? ORDER BY position",
                (int(request_id),),
            )
            return [str(row["recording_mbid"]) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_expected_track_manifest(
        self,
        request_id: int,
    ) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT position, recording_mbid, medium_position, "
                "track_position, track_number, title, artist, date, "
                "track_total, disc_total, release_track_mbid "
                "FROM album_download_request_tracks WHERE request_id = ? "
                "ORDER BY position",
                (int(request_id),),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_local_release_recordings(
        self,
        release_mbid: str,
    ) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT mbid, local_path, title, artist, album, "
                "track_number, disc_number FROM tracks "
                "WHERE release_mbid = ? COLLATE NOCASE AND deleted_at IS NULL",
                (release_mbid,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def update_by_queue_item(
        self,
        queue_item_id: int,
        stage: str,
        detail: str = "",
        completed_tracks: Optional[int] = None,
    ) -> bool:
        if stage not in self._STAGES:
            raise ValueError(f"invalid album request stage: {stage}")
        assignments = [
            "stage = ?",
            "detail = ?",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params = [stage, str(detail or "")]
        if completed_tracks is not None:
            assignments.append("completed_tracks = ?")
            params.append(max(0, int(completed_tracks)))
        params.append(int(queue_item_id))
        stage_guard = {
            "queued": "stage IN ('queued', 'failed')",
            "downloading": "stage IN ('queued', 'downloading', 'failed')",
            "importing": (
                "stage IN ('queued', 'downloading', 'importing', 'failed')"
            ),
            "failed": "stage != 'success'",
            "success": "stage != 'success'",
        }[stage]
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE album_download_requests SET "
                + ", ".join(assignments)
                + f" WHERE queue_item_id = ? AND {stage_guard}",
                params,
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def invalidate(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE album_download_requests SET stage = 'failed', "
                "detail = ?, completed_tracks = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                    int(request_id),
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def replace_identity(
        self,
        request_id: int,
        *,
        artist: str,
        title: str,
        track_count: int,
        recording_mbids: Sequence[str],
        detail: str,
        track_manifest: Sequence[Mapping[str, Any]] = (),
    ) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE album_download_requests SET artist = ?, title = ?, "
                "track_count = ?, stage = 'failed', detail = ?, "
                "completed_tracks = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND (queue_item_id IS NULL "
                "OR stage IN ('failed', 'success'))",
                (
                    artist,
                    title,
                    max(0, int(track_count)),
                    str(detail or ""),
                    int(request_id),
                ),
            )
            changed = cursor.rowcount > 0
            if not changed:
                self.conn.rollback()
                return False
            cursor.execute(
                "DELETE FROM album_download_request_tracks WHERE request_id = ?",
                (int(request_id),),
            )
            cursor.executemany(
                "INSERT INTO album_download_request_tracks "
                "(request_id, position, recording_mbid, medium_position, "
                " track_position, track_number, title, artist, date, "
                " track_total, disc_total, release_track_mbid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _manifest_rows(
                    int(request_id),
                    recording_mbids,
                    track_manifest,
                ),
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def complete_and_remove_queue_item(
        self,
        queue_item_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        """Atomically publish success and retire its downloader queue row."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE album_download_requests SET stage = 'success', "
                "detail = ?, completed_tracks = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE queue_item_id = ?",
                (
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                    int(queue_item_id),
                ),
            )
            changed = cursor.rowcount > 0
            if not changed:
                self.conn.rollback()
                return False
            cursor.execute(
                "DELETE FROM download_queue WHERE id = ?",
                (int(queue_item_id),),
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def complete_by_request_id(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        """Complete detached work and retire a linked queue row atomically."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT queue_item_id FROM album_download_requests WHERE id = ?",
                (int(request_id),),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                "UPDATE album_download_requests SET stage = 'success', "
                "detail = ?, completed_tracks = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                    int(request_id),
                ),
            )
            queue_item_id = row["queue_item_id"]
            if queue_item_id is not None:
                cursor.execute(
                    "DELETE FROM download_queue WHERE id = ?",
                    (int(queue_item_id),),
                )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def requeue(
        self,
        request_id: int,
        queue_item_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE album_download_requests SET queue_item_id = ?, "
                "stage = 'queued', detail = ?, completed_tracks = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (
                    int(queue_item_id),
                    str(detail or ""),
                    max(0, int(completed_tracks)),
                    int(request_id),
                ),
            )
            changed = cursor.rowcount > 0
            self.conn.commit()
            return changed
        finally:
            cursor.close()

    def count_local_release_tracks(self, release_mbid: str) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM tracks "
                "WHERE release_mbid = ? COLLATE NOCASE AND deleted_at IS NULL "
                "AND local_path IS NOT NULL AND local_path != ''",
                (release_mbid,),
            )
            return int(cursor.fetchone()[0])
        finally:
            cursor.close()
