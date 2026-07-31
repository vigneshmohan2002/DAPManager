"""Lyrics and artist-tag persistence behind ``DatabaseManager``."""

import random
from typing import Dict, List, Optional, Protocol, Set, Tuple

from .base import SQLiteRepository


class TopTagsProvider(Protocol):
    def __call__(
        self,
        artist_name: str,
        limit: int = 5,
    ) -> List[Dict[str, object]]:
        ...


class MetadataRepository(SQLiteRepository):
    def delete_lyrics(self, track_mbid: str) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM lyrics WHERE track_mbid = ?",
            (track_mbid,),
        )
        self.conn.commit()
        cursor.close()

    def get_lyrics(self, track_mbid: str) -> Optional[Dict[str, object]]:
        cursor = self.conn.execute(
            "SELECT track_mbid, lrc, synced, source, fetched_at "
            "FROM lyrics WHERE track_mbid = ?",
            (track_mbid,),
        )
        try:
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            cursor.close()

    def get_lyrics_since(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        sql = "SELECT track_mbid, lrc, synced, source, fetched_at FROM lyrics"
        params: tuple = ()
        if since_iso:
            sql += " WHERE fetched_at >= ?"
            params = (since_iso,)
        sql += " ORDER BY fetched_at ASC"
        cursor = self.conn.execute(sql, params)
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def get_distinct_artist_names(self) -> List[str]:
        cursor = self.conn.execute(
            "SELECT DISTINCT artist FROM tracks "
            "WHERE deleted_at IS NULL "
            "  AND artist IS NOT NULL AND artist != '' "
            "ORDER BY artist COLLATE NOCASE"
        )
        try:
            return [row["artist"] for row in cursor.fetchall()]
        finally:
            cursor.close()

    def apply_lyrics_row(self, row: Dict[str, object]) -> str:
        mbid = row.get("track_mbid")
        incoming_ts = row.get("fetched_at")
        if incoming_ts:
            current = self.conn.execute(
                "SELECT fetched_at FROM lyrics WHERE track_mbid = ?",
                (mbid,),
            ).fetchone()
            if (
                current
                and current["fetched_at"]
                and current["fetched_at"] >= incoming_ts
            ):
                return "stale"

        existed = self.conn.execute(
            "SELECT 1 FROM lyrics WHERE track_mbid = ?", (mbid,)
        ).fetchone() is not None
        self.conn.execute(
            "INSERT INTO lyrics (track_mbid, lrc, synced, source, fetched_at) "
            "VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP)) "
            "ON CONFLICT(track_mbid) DO UPDATE SET "
            "  lrc = excluded.lrc, "
            "  synced = excluded.synced, "
            "  source = excluded.source, "
            "  fetched_at = excluded.fetched_at",
            (
                mbid,
                row.get("lrc"),
                1 if row.get("synced") else 0,
                row.get("source") or "lrclib",
                incoming_ts,
            ),
        )
        self.conn.commit()
        return "updated" if existed else "inserted"

    def upsert_lyrics(
        self,
        track_mbid: str,
        lrc: Optional[str],
        synced: bool,
        source: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO lyrics (track_mbid, lrc, synced, source, fetched_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(track_mbid) DO UPDATE SET "
            "  lrc = excluded.lrc, "
            "  synced = excluded.synced, "
            "  source = excluded.source, "
            "  fetched_at = CURRENT_TIMESTAMP",
            (track_mbid, lrc, 1 if synced else 0, source),
        )
        self.conn.commit()

    def get_artists_needing_tags(self, max_age_days: int) -> List[str]:
        cursor = self.conn.execute(
            """
            SELECT DISTINCT t.artist FROM tracks t
            WHERE t.deleted_at IS NULL
              AND t.artist IS NOT NULL AND t.artist != ''
              AND NOT EXISTS (
                  SELECT 1 FROM artist_tags at
                  WHERE at.artist_name = t.artist
                    AND at.fetched_at > datetime('now', ?)
              )
            ORDER BY t.artist COLLATE NOCASE
            """,
            (f"-{int(max_age_days)} days",),
        )
        rows = [row["artist"] for row in cursor.fetchall()]
        cursor.close()
        return rows

    def record_artist_tags(
        self,
        artist_name: str,
        mbid: Optional[str],
        tags: List[dict],
        top_n: int,
        noise_tags: Set[str],
    ) -> int:
        cleaned: List[Tuple[str, int]] = []
        seen_tags: Set[str] = set()
        for entry in sorted(
            tags or [],
            key=lambda tag: -int(tag.get("weight") or 0),
        ):
            tag = (entry.get("tag") or "").strip()
            if not tag:
                continue
            tag_lower = tag.lower()
            if tag_lower in noise_tags or tag_lower in seen_tags:
                continue
            seen_tags.add(tag_lower)
            cleaned.append((tag, int(entry.get("weight") or 1)))
            if len(cleaned) >= top_n:
                break

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM artist_tags WHERE artist_name = ?",
                (artist_name,),
            )
            for tag, weight in cleaned:
                cursor.execute(
                    "INSERT INTO artist_tags "
                    "(artist_name, mbid, tag, weight, fetched_at) "
                    "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                    (artist_name, mbid, tag, weight),
                )
            if not cleaned:
                cursor.execute(
                    "INSERT INTO artist_tags "
                    "(artist_name, mbid, tag, weight, fetched_at) "
                    "VALUES (?, ?, '', 0, CURRENT_TIMESTAMP)",
                    (artist_name, mbid),
                )
            self.conn.commit()
        finally:
            cursor.close()
        return len(cleaned)

    def get_artist_tags_since(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        sql = (
            "SELECT artist_name, mbid, tag, weight, fetched_at "
            "FROM artist_tags"
        )
        params: tuple = ()
        if since_iso:
            sql += (
                " WHERE artist_name IN ("
                "   SELECT artist_name FROM artist_tags "
                "   GROUP BY artist_name HAVING MAX(fetched_at) >= ?"
                " )"
            )
            params = (since_iso,)
        sql += (
            " ORDER BY artist_name COLLATE NOCASE, "
            "fetched_at DESC, weight DESC, tag COLLATE NOCASE"
        )

        cursor = self.conn.execute(sql, params)
        raw_rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()

        grouped: Dict[str, Dict[str, object]] = {}
        order: List[str] = []
        for row in raw_rows:
            key = (row.get("artist_name") or "").casefold()
            if not key:
                continue
            if key not in grouped:
                order.append(key)
                grouped[key] = {
                    "artist_name": row["artist_name"],
                    "mbid": row.get("mbid"),
                    "fetched_at": row.get("fetched_at"),
                    "tags": [],
                }
            snapshot = grouped[key]
            if not snapshot.get("mbid") and row.get("mbid"):
                snapshot["mbid"] = row["mbid"]
            fetched_at = row.get("fetched_at")
            current_fetched_at = snapshot.get("fetched_at")
            if fetched_at and (
                not current_fetched_at or fetched_at > current_fetched_at
            ):
                snapshot["fetched_at"] = fetched_at
            tag = (row.get("tag") or "").strip()
            if tag:
                snapshot_tags = snapshot["tags"]
                assert isinstance(snapshot_tags, list)
                snapshot_tags.append(
                    {"tag": tag, "weight": int(row.get("weight") or 0)}
                )
        return [grouped[key] for key in order]

    def apply_artist_tags_row(self, row: Dict[str, object]) -> str:
        artist_name = (row.get("artist_name") or "").strip()
        cleaned: List[Tuple[str, int]] = []
        seen_tags: Set[str] = set()
        raw_tags = row.get("tags") or []
        for entry in raw_tags:
            if not isinstance(entry, dict):
                continue
            tag = (entry.get("tag") or "").strip()
            key = tag.casefold()
            if not tag or key in seen_tags:
                continue
            seen_tags.add(key)
            try:
                weight = int(entry.get("weight") or 1)
            except (TypeError, ValueError):
                weight = 1
            cleaned.append((tag, weight))

        incoming_ts = row.get("fetched_at")
        incoming_rows = cleaned or [("", 0)]
        current_rows = self.conn.execute(
            "SELECT mbid, tag, weight, fetched_at FROM artist_tags "
            "WHERE artist_name = ? COLLATE NOCASE",
            (artist_name,),
        ).fetchall()
        existed = bool(current_rows)
        if incoming_ts and existed:
            current_ts = max(
                current["fetched_at"] or "" for current in current_rows
            )
            if current_ts > incoming_ts:
                return "stale"
            if current_ts == incoming_ts:
                current_content = sorted(
                    (current["tag"], int(current["weight"] or 0))
                    for current in current_rows
                )
                current_mbid = current_rows[0]["mbid"]
                if (
                    current_content == sorted(incoming_rows)
                    and current_mbid == row.get("mbid")
                ):
                    return "stale"

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM artist_tags WHERE artist_name = ? COLLATE NOCASE",
                (artist_name,),
            )
            for tag, weight in incoming_rows:
                cursor.execute(
                    "INSERT INTO artist_tags "
                    "(artist_name, mbid, tag, weight, fetched_at) "
                    "VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                    (artist_name, row.get("mbid"), tag, weight, incoming_ts),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()
        return "updated" if existed else "inserted"

    def get_top_tags_for_artist(
        self, artist_name: str, limit: int
    ) -> List[Dict[str, object]]:
        cursor = self.conn.execute(
            "SELECT tag, weight FROM artist_tags "
            "WHERE artist_name = ? COLLATE NOCASE AND tag != '' "
            "ORDER BY weight DESC, tag COLLATE NOCASE LIMIT ?",
            (artist_name, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_artists_by_tag(
        self, tag: str, limit: int
    ) -> List[Dict[str, object]]:
        cursor = self.conn.execute(
            "SELECT artist_name, weight FROM artist_tags "
            "WHERE tag = ? COLLATE NOCASE AND tag != '' "
            "ORDER BY weight DESC, artist_name COLLATE NOCASE LIMIT ?",
            (tag, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def get_random_tracks_for_artists(
        self,
        artist_names: List[str],
        limit: int,
        playable_track_columns: str,
    ) -> List[Dict[str, object]]:
        names = [name for name in artist_names if name]
        placeholders = ",".join("?" for _ in names)
        cursor = self.conn.execute(
            f"SELECT {playable_track_columns} FROM tracks "
            f"WHERE artist IN ({placeholders}) COLLATE NOCASE "
            "  AND deleted_at IS NULL "
            "ORDER BY RANDOM() LIMIT ?",
            (*names, int(limit)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def build_artist_radio(
        self,
        artist_name: str,
        limit: int,
        playable_track_columns: str,
        get_top_tags: TopTagsProvider,
    ) -> Dict[str, object]:
        limit = max(1, min(int(limit), 200))
        seed_slots = max(1, limit // 3)
        related_slots = limit - seed_slots
        top_tags = get_top_tags(artist_name, limit=1)
        top_tag = top_tags[0]["tag"] if top_tags else None

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"SELECT {playable_track_columns} FROM tracks "
                "WHERE artist = ? COLLATE NOCASE "
                "  AND deleted_at IS NULL "
                "ORDER BY RANDOM() LIMIT ?",
                (artist_name, seed_slots),
            )
            seed_rows = [dict(row) for row in cursor.fetchall()]

            related_rows: List[Dict[str, object]] = []
            if top_tag and related_slots > 0:
                cursor.execute(
                    f"SELECT {playable_track_columns} FROM tracks t "
                    "WHERE deleted_at IS NULL "
                    "  AND artist != ? COLLATE NOCASE "
                    "  AND artist IN ( "
                    "    SELECT artist_name FROM artist_tags "
                    "    WHERE tag = ? COLLATE NOCASE AND tag != '' "
                    "  ) "
                    "ORDER BY RANDOM() LIMIT ?",
                    (artist_name, top_tag, related_slots),
                )
                related_rows = [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

        combined = seed_rows + related_rows
        random.shuffle(combined)
        return {
            "tracks": combined,
            "top_tag": top_tag,
            "seed_count": len(seed_rows),
            "related_count": len(related_rows),
        }
