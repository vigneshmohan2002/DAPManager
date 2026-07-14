"""Contribution state persistence behind ``DatabaseManager``."""

import sqlite3
from typing import Callable, Dict, List, Optional, TypeVar

from .base import SQLiteRepository


T = TypeVar("T")


class ContributionRepository(SQLiteRepository):
    def find_local_tracks_by_identity(
        self,
        *,
        mbid: Optional[str],
        isrc: Optional[str],
        artist: Optional[str],
        title: Optional[str],
        album: Optional[str],
    ) -> List[Dict[str, object]]:
        candidates: List[Dict[str, object]] = []
        seen_paths = set()

        def add_rows(
            sql: str,
            params: tuple,
            match: str,
            *,
            unique: bool = False,
        ) -> None:
            cursor = self.conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
            cursor.close()
            if unique and len(rows) != 1:
                return
            for row in rows:
                path = row.get("local_path")
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                row["identity_match"] = match
                candidates.append(row)

        select = (
            "SELECT mbid, isrc, artist, title, album, local_path FROM tracks "
            "WHERE deleted_at IS NULL AND local_path IS NOT NULL "
            "AND local_path != '' AND "
        )
        if mbid:
            add_rows(select + "mbid = ?", (mbid,), "mbid")
        if isrc:
            add_rows(
                select + "isrc = ? COLLATE NOCASE",
                (isrc,),
                "isrc",
            )
        if artist and title and album:
            add_rows(
                select
                + "artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE "
                  "AND album = ? COLLATE NOCASE",
                (artist, title, album),
                "artist_title_album",
                unique=True,
            )
        if artist and title:
            add_rows(
                select
                + "artist = ? COLLATE NOCASE AND title = ? COLLATE NOCASE",
                (artist, title),
                "artist_title",
                unique=True,
            )
        return candidates

    def get_contributable_tracks(
        self,
        limit: int,
        row_to_track: Callable[[sqlite3.Row], T],
    ) -> List[T]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT t.* FROM tracks t "
            "LEFT JOIN contributed c ON c.mbid = t.mbid "
            "WHERE t.local_path IS NOT NULL AND t.deleted_at IS NULL "
            "AND (c.mbid IS NULL OR c.contribution_id IS NULL) "
            "LIMIT ?",
            (limit,),
        )
        tracks = [row_to_track(row) for row in cursor.fetchall()]
        cursor.close()
        return tracks

    def create(
        self,
        *,
        device_id: Optional[str],
        mbid: Optional[str],
        isrc: Optional[str],
        artist: Optional[str],
        title: Optional[str],
        album: Optional[str],
        target_quality: Optional[str],
        status: str,
        download_id: Optional[int],
        acquired_quality: Optional[str],
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO contributions "
                "(device_id, mbid, isrc, artist, title, album, target_quality, "
                " acquired_quality, status, download_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    mbid,
                    isrc,
                    artist,
                    title,
                    album,
                    target_quality,
                    acquired_quality,
                    status,
                    download_id,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid or 0
        finally:
            cursor.close()

    def get(self, contribution_id: int) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributions WHERE id = ?", (contribution_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def list(self, limit: int) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributions ORDER BY updated_at DESC, id DESC "
                "LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def update(self, contribution_id: int, fields: Dict[str, object]) -> None:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [contribution_id]
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"UPDATE contributions SET {assignments}, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )
            self.conn.commit()
        finally:
            cursor.close()

    def upsert_contributed(
        self,
        mbid: str,
        contribution_id: Optional[int],
        status: Optional[str],
    ) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO contributed "
                "(mbid, contribution_id, status, updated_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(mbid) DO UPDATE SET "
                "contribution_id = excluded.contribution_id, "
                "status = excluded.status, updated_at = CURRENT_TIMESTAMP",
                (mbid, contribution_id, status),
            )
            self.conn.commit()
        finally:
            cursor.close()

    def get_contributed(self, mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute("SELECT * FROM contributed WHERE mbid = ?", (mbid,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_pending_contributed(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT * FROM contributed WHERE status IS NULL OR status NOT IN "
                "('have_better', 'satisfied', 'ingested')"
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def list_contributed(self, limit: int) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT c.rowid AS local_id, c.mbid, c.contribution_id, "
                "c.status, c.updated_at, t.artist, t.title, t.album "
                "FROM contributed c LEFT JOIN tracks t ON t.mbid = c.mbid "
                "ORDER BY c.updated_at DESC, c.rowid DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
