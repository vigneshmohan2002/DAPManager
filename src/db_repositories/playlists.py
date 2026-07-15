"""Playlist reads kept behind the public database façade."""

import sqlite3
from typing import Callable, Dict, List, Optional

from .base import SQLiteRepository


class PlaylistRepository(SQLiteRepository):
    def purge_by_prefix(self, prefix: str) -> None:
        self.conn.execute(
            "DELETE FROM playlists WHERE playlist_id LIKE ?",
            (f"{prefix}%",),
        )
        self.conn.commit()

    def list_by_prefix(
        self,
        prefix: str,
    ) -> List[Dict[str, object]]:
        cursor = self.conn.execute(
            "SELECT p.playlist_id, p.name, "
            "       COUNT(pt.track_mbid) AS track_count "
            "FROM playlists p "
            "LEFT JOIN playlist_tracks pt "
            "ON pt.playlist_id = p.playlist_id "
            "WHERE p.playlist_id LIKE ? AND p.deleted_at IS NULL "
            "GROUP BY p.playlist_id "
            "ORDER BY p.playlist_id",
            (f"{prefix}%",),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def ensure_liked_songs_playlist(
        self,
        playlist_id: str,
        name: str,
    ) -> str:
        from ..smart_playlist import serialize

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT playlist_id FROM playlists WHERE playlist_id = ?",
            (playlist_id,),
        )
        row = cursor.fetchone()
        if row is not None:
            cursor.close()
            return playlist_id

        smart_rules = serialize({
            "match": "all",
            "rules": [{"field": "is_liked", "op": "equals", "value": True}],
        })
        cursor.execute(
            "INSERT INTO playlists "
            "(playlist_id, name, spotify_url, updated_at, smart_rules) "
            "VALUES (?, ?, '', CURRENT_TIMESTAMP, ?)",
            (playlist_id, name, smart_rules),
        )
        self.conn.commit()
        cursor.close()
        return playlist_id

    def add_or_update(
        self,
        playlist_id: str,
        name: str,
        spotify_url: str,
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO playlists (playlist_id, name, spotify_url, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(playlist_id) DO UPDATE SET
                name = excluded.name,
                spotify_url = excluded.spotify_url,
                updated_at = CURRENT_TIMESTAMP
            """,
            (playlist_id, name, spotify_url),
        )
        self.conn.commit()
        cursor.close()

    def bump_updated_at(self, playlist_id: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ?",
            (playlist_id,),
        )
        self.conn.commit()
        cursor.close()

    def create(
        self,
        playlist_id: str,
        name: str,
        smart_rules: Optional[str],
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO playlists "
            "(playlist_id, name, spotify_url, updated_at, smart_rules) "
            "VALUES (?, ?, '', CURRENT_TIMESTAMP, ?)",
            (playlist_id, name, smart_rules),
        )
        self.conn.commit()
        cursor.close()

    def update_smart_rules(
        self, playlist_id: str, smart_rules: Optional[str]
    ) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET smart_rules = ?, "
            "updated_at = CURRENT_TIMESTAMP WHERE playlist_id = ?",
            (smart_rules, playlist_id),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def unlink_track(
        self,
        playlist_id: str,
        track_mbid: str,
        bump_updated_at: Callable[[str], None],
    ) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM playlist_tracks "
            "WHERE playlist_id = ? AND track_mbid = ?",
            (playlist_id, track_mbid),
        )
        removed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        if removed:
            bump_updated_at(playlist_id)
        return removed

    def replace_membership(
        self, playlist_id: str, track_mbids: List[str]
    ) -> int:
        known = {mbid.strip() for mbid in track_mbids if mbid and mbid.strip()}
        cursor = self.conn.cursor()
        try:
            if known:
                cursor.execute(
                    "SELECT mbid FROM tracks WHERE mbid IN ({})".format(
                        ",".join("?" for _ in known)
                    ),
                    tuple(known),
                )
            else:
                cursor.execute("SELECT mbid FROM tracks WHERE 0")
            existing = {row["mbid"] for row in cursor.fetchall()}
            valid_ordered = [
                mbid.strip()
                for mbid in track_mbids
                if mbid and mbid.strip() and mbid.strip() in existing
            ]
            cursor.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id = ?",
                (playlist_id,),
            )
            for order, mbid in enumerate(valid_ordered):
                cursor.execute(
                    "INSERT INTO playlist_tracks "
                    "(playlist_id, track_mbid, track_order) VALUES (?, ?, ?)",
                    (playlist_id, mbid, order),
                )
            cursor.execute(
                "UPDATE playlists SET updated_at = CURRENT_TIMESTAMP "
                "WHERE playlist_id = ?",
                (playlist_id,),
            )
            self.conn.commit()
        finally:
            cursor.close()
        return len(valid_ordered)

    def rename(self, playlist_id: str, name: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET name = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ? AND deleted_at IS NULL",
            (name, playlist_id),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def soft_delete(self, playlist_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET deleted_at = CURRENT_TIMESTAMP, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ? AND deleted_at IS NULL",
            (playlist_id,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def restore(self, playlist_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE playlists SET deleted_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE playlist_id = ? AND deleted_at IS NOT NULL",
            (playlist_id,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def purge(self, playlist_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM playlists "
            "WHERE playlist_id = ? AND deleted_at IS NOT NULL",
            (playlist_id,),
        )
        changed = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        return changed

    def get_orphans(self) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT p.playlist_id, p.name, p.deleted_at, "
            "       COUNT(pt.track_mbid) AS track_count "
            "FROM playlists p "
            "LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id "
            "WHERE p.deleted_at IS NOT NULL "
            "GROUP BY p.playlist_id "
            "ORDER BY p.deleted_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def apply_row(self, row: Dict[str, object]) -> str:
        playlist_id = row.get("playlist_id")
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM playlists WHERE playlist_id = ?", (playlist_id,)
        )
        existed = cursor.fetchone() is not None
        cursor.execute(
            """
            INSERT INTO playlists
                (playlist_id, name, spotify_url, updated_at, deleted_at, smart_rules)
            VALUES (?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?)
            ON CONFLICT(playlist_id) DO UPDATE SET
                name = excluded.name,
                spotify_url = excluded.spotify_url,
                updated_at = excluded.updated_at,
                deleted_at = excluded.deleted_at,
                smart_rules = excluded.smart_rules
            """,
            (
                playlist_id,
                row.get("name") or "Untitled Playlist",
                row.get("spotify_url") or "",
                row.get("updated_at"),
                row.get("deleted_at"),
                row.get("smart_rules"),
            ),
        )
        cursor.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id = ?",
            (playlist_id,),
        )
        entries = row.get("tracks") or []
        for entry in entries:
            mbid = entry.get("track_mbid") if isinstance(entry, dict) else None
            if not mbid:
                continue
            order = entry.get("track_order", 0) if isinstance(entry, dict) else 0
            cursor.execute(
                "INSERT OR IGNORE INTO playlist_tracks "
                "(playlist_id, track_mbid, track_order) "
                "SELECT ?, ?, ? WHERE EXISTS "
                "(SELECT 1 FROM tracks WHERE mbid = ?)",
                (playlist_id, mbid, order, mbid),
            )
        self.conn.commit()
        cursor.close()
        return "updated" if existed else "inserted"

    def apply_pushed_row(
        self,
        row: Dict[str, object],
        apply_row: Callable[[Dict[str, object]], str],
    ) -> str:
        playlist_id = row.get("playlist_id")
        incoming_ts = row.get("updated_at")
        if incoming_ts:
            current = self.conn.execute(
                "SELECT updated_at FROM playlists WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            if (
                current
                and current["updated_at"]
                and current["updated_at"] >= incoming_ts
            ):
                return "stale"
        return apply_row(row)

    def ensure_system_playlist(self, playlist_id: str, name: str) -> str:
        self.conn.execute(
            "INSERT INTO playlists "
            "(playlist_id, name, spotify_url, updated_at, smart_rules) "
            "VALUES (?, ?, '', CURRENT_TIMESTAMP, NULL) "
            "ON CONFLICT(playlist_id) DO UPDATE SET "
            "  name = excluded.name, "
            "  updated_at = CURRENT_TIMESTAMP, "
            "  deleted_at = NULL",
            (playlist_id, name),
        )
        self.conn.commit()
        return playlist_id

    def list_with_counts(
        self, liked_songs_playlist_id: str
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.playlist_id, p.name, p.updated_at, p.smart_rules,
                   COUNT(pt.track_mbid) AS track_count
            FROM playlists p
            LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.playlist_id
            WHERE p.deleted_at IS NULL
            GROUP BY p.playlist_id
            ORDER BY p.name COLLATE NOCASE
            """
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        for row in rows:
            if row.get("playlist_id") != liked_songs_playlist_id:
                continue
            liked_cursor = self.conn.cursor()
            liked_cursor.execute(
                "SELECT COUNT(*) FROM tracks "
                "WHERE is_liked = 1 AND deleted_at IS NULL"
            )
            row["track_count"] = liked_cursor.fetchone()[0]
            liked_cursor.close()
        return rows

    def list_tracks_filtered(
        self,
        playlist_id: Optional[str],
        local_only: bool,
        include_orphans: bool,
    ) -> List[Dict[str, object]]:
        from ..smart_playlist import build_where, parse_stored

        params: tuple
        clauses = []
        if local_only:
            clauses.append("t.local_path IS NOT NULL")
        if not include_orphans:
            clauses.append("t.deleted_at IS NULL")

        base_cols = (
            "t.mbid, t.title, t.artist, t.album, t.track_number, t.disc_number, "
            "t.local_path, t.dap_path, t.deleted_at, t.is_liked, "
            "COALESCE(NULLIF(t.release_mbid, ''), "
            "t.album || '|' || t.artist) AS album_id"
        )
        library_order = (
            " ORDER BY t.artist COLLATE NOCASE, "
            "t.album COLLATE NOCASE, "
            "COALESCE(t.disc_number, 1), "
            "COALESCE(t.track_number, 9999), "
            "t.title COLLATE NOCASE"
        )

        cursor = self.conn.cursor()
        try:
            if playlist_id:
                cursor.execute(
                    "SELECT smart_rules FROM playlists WHERE playlist_id = ?",
                    (playlist_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return []
                smart = parse_stored(row["smart_rules"])
                if smart is not None:
                    where, smart_params = build_where(smart)
                    sql = f"SELECT {base_cols} FROM tracks t WHERE {where}"
                    if clauses:
                        sql += " AND " + " AND ".join(clauses)
                    sql += library_order
                    params = tuple(smart_params)
                else:
                    sql = (
                        f"SELECT {base_cols} FROM tracks t "
                        "JOIN playlist_tracks pt ON pt.track_mbid = t.mbid "
                        "WHERE pt.playlist_id = ?"
                    )
                    if clauses:
                        sql += " AND " + " AND ".join(clauses)
                    sql += " ORDER BY pt.track_order"
                    params = (playlist_id,)
            else:
                sql = f"SELECT {base_cols} FROM tracks t"
                if clauses:
                    sql += " WHERE " + " AND ".join(clauses)
                sql += library_order
                params = ()

            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_since(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        if since_iso:
            cursor.execute(
                "SELECT playlist_id, name, spotify_url, updated_at, "
                "deleted_at, smart_rules FROM playlists WHERE updated_at > ? "
                "ORDER BY updated_at ASC",
                (since_iso,),
            )
        else:
            cursor.execute(
                "SELECT playlist_id, name, spotify_url, updated_at, "
                "deleted_at, smart_rules FROM playlists "
                "ORDER BY updated_at ASC"
            )
        playlists = [dict(row) for row in cursor.fetchall()]
        for playlist in playlists:
            cursor.execute(
                "SELECT track_mbid, track_order FROM playlist_tracks "
                "WHERE playlist_id = ? ORDER BY track_order ASC",
                (playlist["playlist_id"],),
            )
            playlist["tracks"] = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return playlists

    def link_track(
        self,
        playlist_id: str,
        track_mbid: str,
        order: int,
        bump_updated_at: Callable[[str], None],
    ) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO playlist_tracks "
            "(playlist_id, track_mbid, track_order) VALUES (?, ?, ?)",
            (playlist_id, track_mbid, order),
        )
        inserted = cursor.rowcount > 0
        self.conn.commit()
        cursor.close()
        if inserted:
            bump_updated_at(playlist_id)

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
