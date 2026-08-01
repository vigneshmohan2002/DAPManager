"""Duplicate and album-group maintenance persistence."""

from collections import defaultdict
from typing import Dict, List, Optional, Set

from .base import SQLiteRepository


class AlbumMaintenanceRepository(SQLiteRepository):
    def log_duplicate(self, mbid: str, file_path: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO duplicates (mbid, file_path) VALUES (?, ?)",
            (mbid, file_path),
        )
        self.conn.commit()
        cursor.close()

    def get_all_duplicates(self) -> Dict[str, List[str]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT mbid, file_path FROM duplicates")
        duplicates = defaultdict(list)
        for row in cursor.fetchall():
            duplicates[row["mbid"]].append(row["file_path"])
        cursor.close()
        return dict(duplicates)

    def clear_duplicate(self, mbid: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM duplicates WHERE mbid = ?", (mbid,))
        self.conn.commit()
        cursor.close()

    def replace_duplicate_paths(self, mbid: str, file_paths: List[str]) -> None:
        """Atomically replace one duplicate group after filesystem work."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("DELETE FROM duplicates WHERE mbid = ?", (mbid,))
            cursor.executemany(
                "INSERT INTO duplicates (mbid, file_path) VALUES (?, ?)",
                [(mbid, path) for path in file_paths],
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def list_split_album_tracks(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                mbid, title, artist, album, track_number, disc_number,
                local_path, release_mbid,
                COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
            FROM tracks
            WHERE deleted_at IS NULL
              AND album IS NOT NULL AND album != ''
              AND artist IS NOT NULL AND artist != ''
            ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE,
                     COALESCE(disc_number, 1), COALESCE(track_number, 9999)
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def list_album_group_tracks(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT
                mbid, title, artist, album, track_number, disc_number, release_mbid,
                COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id
            FROM tracks
            WHERE deleted_at IS NULL
              AND album IS NOT NULL AND album != ''
              AND artist IS NOT NULL AND artist != ''
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def reassign_album_group_tracks(
        self,
        source_album_id: str,
        target_album: str,
        target_artist: str,
        target_release_mbid: Optional[str],
        include_local_paths: bool,
    ) -> Dict[str, object]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT mbid FROM tracks
            WHERE deleted_at IS NULL
              AND COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) = ?
            """,
            (source_album_id,),
        )
        mbids = [row[0] for row in cursor.fetchall()]
        if not mbids:
            cursor.close()
            return {"matched": 0, "moved": 0, "tracks": []}

        placeholders = ",".join("?" * len(mbids))
        if target_release_mbid:
            cursor.execute(
                f"""
                UPDATE tracks SET album = ?, release_mbid = ?, updated_at = CURRENT_TIMESTAMP
                WHERE mbid IN ({placeholders})
                """,
                [target_album, target_release_mbid] + mbids,
            )
        else:
            cursor.execute(
                f"""
                UPDATE tracks SET album = ?, artist = ?, release_mbid = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE mbid IN ({placeholders})
                """,
                [target_album, target_artist] + mbids,
            )
        moved = cursor.rowcount
        self.conn.commit()

        tracks: List[Dict[str, object]] = []
        if include_local_paths:
            cursor.execute(
                f"SELECT mbid, local_path FROM tracks "
                f"WHERE mbid IN ({placeholders})",
                mbids,
            )
            tracks = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return {"matched": len(mbids), "moved": moved, "tracks": tracks}

    def list_local_album_tag_rows(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT mbid, album, artist, release_mbid, local_path FROM tracks
            WHERE deleted_at IS NULL AND local_path IS NOT NULL AND local_path != ''
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_dismissed_split_albums(self) -> Set[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT incident_key FROM split_album_dismissals")
        keys = {row[0] for row in cursor.fetchall()}
        cursor.close()
        return keys

    def dismiss_split_album(self, incident_key: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO split_album_dismissals (incident_key) "
            "VALUES (?)",
            (incident_key,),
        )
        self.conn.commit()
        cursor.close()

    def undismiss_split_album(self, incident_key: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM split_album_dismissals WHERE incident_key = ?",
            (incident_key,),
        )
        self.conn.commit()
        cursor.close()
