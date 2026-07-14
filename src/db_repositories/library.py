"""Library read queries kept behind the public database façade."""

import logging
import sqlite3
from typing import Callable, Dict, List, Optional, Protocol, TypeVar

from .base import SQLiteRepository


T = TypeVar("T")


class TrackRecord(Protocol):
    mbid: str
    title: str
    artist: str
    album: Optional[str]
    isrc: Optional[str]
    local_path: Optional[str]
    dap_path: Optional[str]
    synced_to_dap: bool
    release_mbid: Optional[str]
    track_number: int
    disc_number: int
    tag_tier: Optional[str]
    tag_score: Optional[float]


class LibraryRepository(SQLiteRepository):
    def add_or_update_track(
        self,
        track: TrackRecord,
        logger: logging.Logger,
    ) -> None:
        sql = """
        INSERT INTO tracks
        (mbid, title, artist, album, isrc, local_path, dap_path, synced_to_dap,
         release_mbid, track_number, disc_number, tag_tier, tag_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(mbid) DO UPDATE SET
            title = excluded.title,
            artist = excluded.artist,
            album = excluded.album,
            isrc = excluded.isrc,
            local_path = excluded.local_path,
            dap_path = excluded.dap_path,
            synced_to_dap = excluded.synced_to_dap,
            release_mbid = excluded.release_mbid,
            track_number = excluded.track_number,
            disc_number = excluded.disc_number,
            tag_tier = COALESCE(excluded.tag_tier, tag_tier),
            tag_score = COALESCE(excluded.tag_score, tag_score),
            updated_at = CURRENT_TIMESTAMP
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                sql,
                (
                    track.mbid,
                    track.title,
                    track.artist,
                    track.album,
                    track.isrc,
                    track.local_path,
                    track.dap_path,
                    int(track.synced_to_dap),
                    track.release_mbid,
                    track.track_number,
                    track.disc_number,
                    track.tag_tier,
                    track.tag_score,
                ),
            )
            self.conn.commit()
        except sqlite3.Error as error:
            logger.error(f"Error adding track: {error}")
            self.conn.rollback()
        finally:
            if cursor:
                cursor.close()

    def set_track_tag_tier(
        self,
        mbid: str,
        tier: Optional[str],
        score: Optional[float],
    ) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET tag_tier = ?, tag_score = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (tier, score, mbid),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def get_tracks_needing_tag_review(
        self,
        row_to_track: Callable[[sqlite3.Row], T],
    ) -> List[T]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tracks "
            "WHERE tag_tier IN ('yellow', 'red') "
            "AND local_path IS NOT NULL "
            "AND deleted_at IS NULL "
            "ORDER BY artist, album, track_number"
        )
        tracks = [row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def find_unlinked_tracks_by_isrc(self, isrc: str) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid FROM tracks "
            "WHERE isrc = ? AND local_path IS NULL AND deleted_at IS NULL",
            (isrc,),
        )
        rows = [row["mbid"] for row in cursor.fetchall()]
        cursor.close()
        return rows

    def find_unlinked_tracks_by_artist_title(
        self,
        artist: str,
        title: str,
    ) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid FROM tracks "
            "WHERE artist = ? COLLATE NOCASE "
            "  AND title = ? COLLATE NOCASE "
            "  AND local_path IS NULL AND deleted_at IS NULL",
            (artist, title),
        )
        rows = [row["mbid"] for row in cursor.fetchall()]
        cursor.close()
        return rows

    def find_unlinked_tracks_by_artist_title_album(
        self,
        artist: str,
        title: str,
        album: str,
    ) -> List[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid FROM tracks "
            "WHERE artist = ? COLLATE NOCASE "
            "  AND title = ? COLLATE NOCASE "
            "  AND album = ? COLLATE NOCASE "
            "  AND local_path IS NULL AND deleted_at IS NULL",
            (artist, title, album),
        )
        rows = [row["mbid"] for row in cursor.fetchall()]
        cursor.close()
        return rows

    def is_track_unlinked_and_live(self, mbid: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM tracks "
            "WHERE mbid = ? AND local_path IS NULL AND deleted_at IS NULL",
            (mbid,),
        )
        eligible = cursor.fetchone() is not None
        cursor.close()
        return eligible

    def update_album_metadata(
        self,
        release_mbid: str,
        album_title: str,
        total_tracks: int,
        logger: logging.Logger,
    ) -> None:
        sql = """
        INSERT INTO albums (release_mbid, album_title, total_tracks)
        VALUES (?, ?, ?)
        ON CONFLICT(release_mbid) DO UPDATE SET
            total_tracks = MAX(total_tracks, excluded.total_tracks),
            album_title = excluded.album_title
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql, (release_mbid, album_title, total_tracks))
            self.conn.commit()
        except sqlite3.Error as error:
            logger.error(f"Error updating album metadata: {error}")
        finally:
            if cursor:
                cursor.close()

    def get_incomplete_albums(self) -> List[Dict[str, object]]:
        sql = """
        SELECT
            t.artist,
            a.album_title,
            a.release_mbid,
            COUNT(DISTINCT t.track_number) as local_count,
            a.total_tracks
        FROM tracks t
        JOIN albums a ON t.release_mbid = a.release_mbid
        WHERE t.local_path IS NOT NULL
        GROUP BY t.release_mbid
        HAVING local_count < a.total_tracks
        ORDER BY t.artist, a.album_title
        """
        results = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                results.append(
                    {
                        "artist": row["artist"],
                        "album": row["album_title"],
                        "mbid": row["release_mbid"],
                        "have": row["local_count"],
                        "total": row["total_tracks"],
                        "missing": row["total_tracks"] - row["local_count"],
                    }
                )
            return results
        except sqlite3.Error:
            return []
        finally:
            if cursor:
                cursor.close()

    def get_tracks_missing_album_info(
        self,
        row_to_track: Callable[[sqlite3.Row], T],
        logger: logging.Logger,
    ) -> List[T]:
        sql = """
            SELECT t.* FROM tracks t
            LEFT JOIN albums a ON t.release_mbid = a.release_mbid
            WHERE t.local_path IS NOT NULL
              AND (t.release_mbid IS NULL OR a.release_mbid IS NULL)
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            return [row_to_track(row) for row in cursor.fetchall()]
        except sqlite3.Error as error:
            logger.error(f"Error getting orphan tracks: {error}")
            return []
        finally:
            if cursor:
                cursor.close()

    def get_local_album_snapshot(
        self,
        release_mbid: str,
    ) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT artist, album FROM tracks "
            "WHERE release_mbid = ? AND local_path IS NOT NULL LIMIT 1",
            (release_mbid,),
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT disc_number, track_number FROM tracks "
            "WHERE release_mbid = ? AND local_path IS NOT NULL",
            (release_mbid,),
        )
        positions = {(item[0], item[1]) for item in cursor.fetchall()}
        cursor.close()
        return {
            "artist": row[0],
            "album": row[1],
            "positions": positions,
        }

    def update_track_release_mbid(
        self,
        mbid: str,
        release_mbid: str,
    ) -> int:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET release_mbid = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (release_mbid, mbid),
        )
        changed = cursor.rowcount
        self.conn.commit()
        cursor.close()
        return changed

    def get_album_track_counts(
        self,
        logger: logging.Logger,
    ) -> List[Dict[str, object]]:
        sql = """
            SELECT
                a.release_mbid,
                a.album_title,
                a.total_tracks,
                MIN(t.artist) as artist,
                COUNT(DISTINCT t.track_number) as local_count
            FROM albums a
            JOIN tracks t ON t.release_mbid = a.release_mbid
            WHERE t.local_path IS NOT NULL
            GROUP BY a.release_mbid
            ORDER BY t.artist, a.album_title
        """
        results = []
        try:
            cursor = self.conn.cursor()
            cursor.execute(sql)
            for row in cursor.fetchall():
                results.append({
                    "release_mbid": row["release_mbid"],
                    "album": row["album_title"],
                    "artist": row["artist"],
                    "total": row["total_tracks"],
                    "have": row["local_count"],
                    "missing": row["total_tracks"] - row["local_count"],
                })
            return results
        except sqlite3.Error as error:
            logger.error(f"Error getting album track counts: {error}")
            return []
        finally:
            if cursor:
                cursor.close()

    def merge_albums(
        self,
        source_mbid: str,
        target_mbid: str,
        logger: logging.Logger,
    ) -> bool:
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT album_title FROM albums WHERE release_mbid = ?",
                (target_mbid,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            target_title = row[0]

            cursor.execute(
                "UPDATE tracks SET release_mbid = ?, album = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE release_mbid = ?",
                (target_mbid, target_title, source_mbid),
            )
            cursor.execute(
                "DELETE FROM albums WHERE release_mbid = ?",
                (source_mbid,),
            )
            self.conn.commit()
            return True
        except sqlite3.Error as error:
            logger.error(f"Merge failed: {error}")
            self.conn.rollback()
            return False
        finally:
            if cursor:
                cursor.close()

    def fetch_track_by_mbid(self, mbid: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT * FROM tracks WHERE mbid = ?", (mbid,))
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetch_track_by_path(self, local_path: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM tracks WHERE local_path = ?", (local_path,)
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def list_albums(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS id,
                    COALESCE(album, '') AS title,
                    artist,
                    COUNT(*) AS track_count,
                    MIN(local_path) AS cover_path
                FROM tracks
                WHERE deleted_at IS NULL
                  AND album IS NOT NULL AND album != ''
                  AND artist IS NOT NULL AND artist != ''
                GROUP BY id
                ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE
                """
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def list_all_tracks(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                SELECT
                    mbid, title, artist, album, track_number, disc_number,
                    local_path, dap_path, is_liked,
                    COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
                FROM tracks
                WHERE deleted_at IS NULL
                ORDER BY artist COLLATE NOCASE,
                         album COLLATE NOCASE,
                         COALESCE(disc_number, 1),
                         COALESCE(track_number, 9999),
                         title COLLATE NOCASE
                """
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def set_track_liked(self, mbid: str, liked: bool) -> Optional[bool]:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET is_liked = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE mbid = ? AND deleted_at IS NULL",
            (1 if liked else 0, mbid),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return bool(liked) if changed else None

    def list_artists(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                artist AS name,
                COUNT(DISTINCT COALESCE(NULLIF(release_mbid, ''), album || '|' || artist)) AS album_count,
                COUNT(*) AS track_count
            FROM tracks
            WHERE deleted_at IS NULL
              AND artist IS NOT NULL AND artist != ''
              AND album IS NOT NULL AND album != ''
            GROUP BY artist
            ORDER BY artist COLLATE NOCASE
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_album_cover_path(self, album_id: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT local_path FROM tracks
            WHERE deleted_at IS NULL
              AND (release_mbid = ? OR (album || '|' || artist) = ?)
            LIMIT 1
            """,
            (album_id, album_id),
        )
        row = cursor.fetchone()
        cursor.close()
        return row["local_path"] if row else None

    def list_album_tracks(
        self,
        album_id: str,
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT mbid, title, artist, album, track_number, disc_number,
                   local_path, dap_path, is_liked
            FROM tracks
            WHERE deleted_at IS NULL
              AND (release_mbid = ? OR (album || '|' || artist) = ?)
            ORDER BY COALESCE(disc_number, 1),
                     COALESCE(track_number, 9999),
                     title COLLATE NOCASE
            """,
            (album_id, album_id),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_all_tracks(
        self,
        local_only: bool,
        include_orphans: bool,
        row_to_track: Callable[[sqlite3.Row], T],
    ) -> List[T]:
        sql = "SELECT * FROM tracks"
        clauses = []
        if local_only:
            clauses.append("local_path IS NOT NULL")
        if not include_orphans:
            clauses.append("deleted_at IS NULL")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        cursor = self.conn.cursor()
        cursor.execute(sql)
        tracks = [row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def soft_delete_track(self, mbid: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET deleted_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE mbid = ? AND deleted_at IS NULL",
            (mbid,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def restore_track(self, mbid: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tracks SET deleted_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE mbid = ? AND deleted_at IS NOT NULL",
            (mbid,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def purge_track(self, mbid: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM tracks WHERE mbid = ? AND deleted_at IS NOT NULL",
            (mbid,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def get_orphan_tracks(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid, artist, title, album, deleted_at, local_path "
            "FROM tracks WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_track_sources(self, mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT local_path, dap_path FROM tracks "
                "WHERE mbid = ? AND deleted_at IS NULL",
                (mbid,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_track_local_path(self, mbid: str) -> Optional[str]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT local_path FROM tracks "
                "WHERE mbid = ? AND deleted_at IS NULL",
                (mbid,),
            )
            row = cursor.fetchone()
            return row["local_path"] if row and row["local_path"] else None
        finally:
            cursor.close()

    def get_live_track_identity(
        self,
        mbid: str,
    ) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT title, artist, album FROM tracks "
            "WHERE mbid = ? AND deleted_at IS NULL",
            (mbid,),
        )
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def get_liked_tracks_summary(self, limit: int) -> Dict[str, object]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM tracks "
            "WHERE is_liked = 1 AND deleted_at IS NULL"
        )
        total = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT mbid, title, artist, album, "
            "COALESCE(NULLIF(release_mbid, ''), "
            "album || '|' || artist) AS album_id "
            "FROM tracks WHERE is_liked = 1 AND deleted_at IS NULL "
            "ORDER BY updated_at DESC LIMIT ?",
            (int(limit),),
        )
        preview = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return {"total": total, "preview": preview}

    def get_mbid_to_track_path_map(self) -> Dict[str, str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT mbid, local_path FROM tracks")
        result = {
            row[0]: row[1]
            for row in cursor.fetchall()
            if row[0] and row[1]
        }
        cursor.close()
        return result

    def clear_missing_local_paths(
        self,
        dry_run: bool,
        is_file: Callable[[str], bool],
    ) -> Dict[str, object]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT mbid, local_path FROM tracks "
            "WHERE deleted_at IS NULL AND local_path IS NOT NULL "
            "AND local_path != ''"
        )
        rows = cursor.fetchall()
        missing = []
        for row in rows:
            path = (dict(row).get("local_path") or "").strip()
            if path and not is_file(path):
                missing.append((dict(row)["mbid"], path))
        if not dry_run:
            for mbid, _path in missing:
                cursor.execute(
                    "UPDATE tracks SET local_path = NULL, "
                    "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
                    (mbid,),
                )
            self.conn.commit()
        cursor.close()
        scanned = len(rows)
        return {
            "dry_run": dry_run,
            "scanned": scanned,
            "cleared": len(missing),
            "fraction": round(len(missing) / scanned, 3) if scanned else 0.0,
            "sample": [path for _mbid, path in missing[:20]],
        }

    def update_track_local_path(self, mbid: str, path: str) -> None:
        cursor = self.conn.cursor()
        if path:
            cursor.execute(
                "UPDATE tracks SET local_path = NULL "
                "WHERE local_path = ? AND mbid != ?",
                (path, mbid),
            )
        cursor.execute(
            "UPDATE tracks SET local_path = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
            (path, mbid),
        )
        self.conn.commit()
        cursor.close()

    def get_library_stats(
        self,
        logger: logging.Logger,
    ) -> Dict[str, object]:
        cursor = self.conn.cursor()
        stats: Dict[str, object] = {}
        try:
            cursor.execute("SELECT COUNT(*) FROM tracks")
            stats["tracks"] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT artist) FROM tracks")
            stats["artists"] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM albums")
            stats["albums"] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM playlists")
            stats["playlists"] = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COUNT(*) FROM (
                    SELECT t.release_mbid
                    FROM tracks t JOIN albums a ON t.release_mbid = a.release_mbid
                    WHERE t.local_path IS NOT NULL
                    GROUP BY t.release_mbid
                    HAVING COUNT(DISTINCT t.track_number) < a.total_tracks
                )
            """)
            stats["incomplete_albums"] = cursor.fetchone()[0]
        except sqlite3.Error as error:
            logger.error(f"Error getting stats: {error}")
        finally:
            cursor.close()
        return stats

    def search_tracks(
        self,
        query: str,
        row_to_track: Callable[[sqlite3.Row], T],
        logger: logging.Logger,
    ) -> List[T]:
        cursor = self.conn.cursor()
        search_term = f"%{query}%"
        sql = """
            SELECT * FROM tracks
            WHERE title LIKE ? OR artist LIKE ? OR album LIKE ?
            ORDER BY artist, album, track_number
            LIMIT 100
        """
        try:
            cursor.execute(sql, (search_term, search_term, search_term))
            return [row_to_track(row) for row in cursor.fetchall()]
        except sqlite3.Error as error:
            logger.error(f"Search failed: {error}")
            return []
        finally:
            cursor.close()
