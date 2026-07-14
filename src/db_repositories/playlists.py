"""Playlist reads kept behind the public database façade."""

import sqlite3
from typing import List, Optional

from .base import SQLiteRepository


class PlaylistRepository(SQLiteRepository):
    def fetch_playlist(self, playlist_id: str) -> Optional[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM playlists "
                "WHERE playlist_id = ? AND deleted_at IS NULL",
                (playlist_id,),
            )
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetch_all_playlists(self, include_orphans: bool) -> List[sqlite3.Row]:
        cursor = self.conn.cursor()
        try:
            if include_orphans:
                cursor.execute("SELECT * FROM playlists")
            else:
                cursor.execute(
                    "SELECT * FROM playlists WHERE deleted_at IS NULL"
                )
            return cursor.fetchall()
        finally:
            cursor.close()

    def fetch_playlist_tracks(
        self,
        playlist_id: str,
        local_only: bool,
        include_orphans: bool,
    ) -> List[sqlite3.Row]:
        sql = (
            "SELECT t.* FROM tracks t "
            "JOIN playlist_tracks pt ON t.mbid = pt.track_mbid "
            "WHERE pt.playlist_id = ?"
        )
        if local_only:
            sql += " AND t.local_path IS NOT NULL"
        if not include_orphans:
            sql += " AND t.deleted_at IS NULL"
        sql += " ORDER BY pt.track_order"

        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, (playlist_id,))
            return cursor.fetchall()
        finally:
            cursor.close()
