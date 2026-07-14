"""Listening-history persistence and simple aggregates."""

from typing import cast, Dict, List, Optional

from .base import SQLiteRepository


class ListeningRepository(SQLiteRepository):
    def record_event(
        self,
        track_mbid: str,
        source: Optional[str],
        listened_ms: Optional[int],
    ) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO play_events (track_mbid, source, listened_ms) "
                "VALUES (?, ?, ?)",
                (track_mbid, source, listened_ms),
            )
            event_id = cursor.lastrowid
            self.conn.commit()
            return cast(int, event_id)
        finally:
            cursor.close()

    def plays_by_hour(
        self, since_iso: Optional[str]
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT CAST(strftime('%H', played_at) AS INTEGER) AS hour, "
                    "       COUNT(*) AS plays "
                    "FROM play_events WHERE played_at >= ? "
                    "GROUP BY hour ORDER BY hour",
                    (since_iso,),
                )
            else:
                cursor.execute(
                    "SELECT CAST(strftime('%H', played_at) AS INTEGER) AS hour, "
                    "       COUNT(*) AS plays "
                    "FROM play_events "
                    "GROUP BY hour ORDER BY hour"
                )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def listening_time_since(self, since_iso: Optional[str]) -> int:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT COALESCE(SUM(listened_ms), 0) FROM play_events "
                    "WHERE listened_ms IS NOT NULL AND played_at >= ?",
                    (since_iso,),
                )
            else:
                cursor.execute(
                    "SELECT COALESCE(SUM(listened_ms), 0) FROM play_events "
                    "WHERE listened_ms IS NOT NULL"
                )
            return int(cursor.fetchone()[0] or 0)
        finally:
            cursor.close()

    def play_count_since(self, since_iso: Optional[str]) -> int:
        cursor = self.conn.cursor()
        try:
            if since_iso:
                cursor.execute(
                    "SELECT COUNT(*) FROM play_events WHERE played_at >= ?",
                    (since_iso,),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM play_events")
            return int(cursor.fetchone()[0] or 0)
        finally:
            cursor.close()

    def top_tracks_since(
        self, since_iso: Optional[str], limit: int
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        params: tuple
        if since_iso:
            sql = (
                "SELECT pe.track_mbid AS mbid, t.title, t.artist, t.album, "
                "       COUNT(*) AS plays "
                "FROM play_events pe "
                "LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
                "WHERE pe.played_at >= ? "
                "GROUP BY pe.track_mbid "
                "ORDER BY plays DESC, t.artist COLLATE NOCASE, "
                "t.title COLLATE NOCASE LIMIT ?"
            )
            params = (since_iso, int(limit))
        else:
            sql = (
                "SELECT pe.track_mbid AS mbid, t.title, t.artist, t.album, "
                "       COUNT(*) AS plays "
                "FROM play_events pe "
                "LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
                "GROUP BY pe.track_mbid "
                "ORDER BY plays DESC, t.artist COLLATE NOCASE, "
                "t.title COLLATE NOCASE LIMIT ?"
            )
            params = (int(limit),)
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def top_artists_since(
        self, since_iso: Optional[str], limit: int
    ) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        params: tuple
        if since_iso:
            sql = (
                "SELECT t.artist, COUNT(*) AS plays, "
                "       COUNT(DISTINCT pe.track_mbid) AS distinct_tracks "
                "FROM play_events pe "
                "JOIN tracks t ON t.mbid = pe.track_mbid "
                "WHERE pe.played_at >= ? "
                "GROUP BY t.artist "
                "ORDER BY plays DESC, t.artist COLLATE NOCASE LIMIT ?"
            )
            params = (since_iso, int(limit))
        else:
            sql = (
                "SELECT t.artist, COUNT(*) AS plays, "
                "       COUNT(DISTINCT pe.track_mbid) AS distinct_tracks "
                "FROM play_events pe "
                "JOIN tracks t ON t.mbid = pe.track_mbid "
                "GROUP BY t.artist "
                "ORDER BY plays DESC, t.artist COLLATE NOCASE LIMIT ?"
            )
            params = (int(limit),)
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def wrapped_summary(
        self, year: int, since: str, until: str
    ) -> Dict[str, object]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS total, "
            "       COALESCE(SUM(listened_ms), 0) AS total_ms, "
            "       SUM(CASE WHEN listened_ms IS NULL THEN 1 ELSE 0 END) "
            "         AS legacy_rows "
            "FROM play_events WHERE played_at BETWEEN ? AND ?",
            (since, until),
        )
        head = cursor.fetchone()
        total_plays = int(head["total"] or 0)
        total_ms = int(head["total_ms"] or 0)
        has_legacy_rows = bool(head["legacy_rows"] or 0)

        cursor.execute(
            "SELECT t.mbid, t.title, t.artist, t.album, COUNT(*) AS plays "
            "FROM play_events pe LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
            "WHERE pe.played_at BETWEEN ? AND ? "
            "GROUP BY pe.track_mbid ORDER BY plays DESC LIMIT 1",
            (since, until),
        )
        top_track_row = cursor.fetchone()
        top_track = dict(top_track_row) if top_track_row else None

        cursor.execute(
            "SELECT t.artist, COUNT(*) AS plays, "
            "       COUNT(DISTINCT pe.track_mbid) AS distinct_tracks "
            "FROM play_events pe LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
            "WHERE pe.played_at BETWEEN ? AND ? AND t.artist IS NOT NULL "
            "GROUP BY t.artist ORDER BY plays DESC LIMIT 1",
            (since, until),
        )
        top_artist_row = cursor.fetchone()
        top_artist = dict(top_artist_row) if top_artist_row else None

        cursor.execute(
            "SELECT "
            "  COALESCE(NULLIF(t.release_mbid, ''), "
            "           t.album || '|' || t.artist) AS album_id, "
            "  MAX(t.album) AS album, "
            "  MAX(t.artist) AS artist, "
            "  COUNT(*) AS plays "
            "FROM play_events pe JOIN tracks t ON t.mbid = pe.track_mbid "
            "WHERE pe.played_at BETWEEN ? AND ? AND t.album IS NOT NULL "
            "GROUP BY album_id ORDER BY plays DESC LIMIT 1",
            (since, until),
        )
        top_album_row = cursor.fetchone()
        top_album = dict(top_album_row) if top_album_row else None

        cursor.execute(
            "SELECT date(played_at) AS date, COUNT(*) AS plays "
            "FROM play_events WHERE played_at BETWEEN ? AND ? "
            "GROUP BY date ORDER BY plays DESC, date LIMIT 1",
            (since, until),
        )
        busiest_row = cursor.fetchone()
        busiest_day = dict(busiest_row) if busiest_row else None

        cursor.execute(
            "SELECT CAST(strftime('%H', played_at) AS INTEGER) AS hour, "
            "       COUNT(*) AS plays "
            "FROM play_events WHERE played_at BETWEEN ? AND ? "
            "GROUP BY hour ORDER BY plays DESC, hour LIMIT 1",
            (since, until),
        )
        hour_row = cursor.fetchone()
        top_hour = int(hour_row["hour"]) if hour_row else None

        cursor.execute(
            "SELECT pe.played_at, t.title, t.artist "
            "FROM play_events pe LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
            "WHERE pe.played_at BETWEEN ? AND ? "
            "ORDER BY pe.played_at ASC, pe.id ASC LIMIT 1",
            (since, until),
        )
        first_row = cursor.fetchone()
        first_play = dict(first_row) if first_row else None

        cursor.execute(
            "WITH days AS ( "
            "  SELECT DISTINCT date(played_at) AS d FROM play_events "
            "  WHERE played_at BETWEEN ? AND ? "
            "), runs AS ( "
            "  SELECT d, "
            "         julianday(d) - ROW_NUMBER() OVER (ORDER BY d) AS grp "
            "  FROM days "
            ") SELECT COUNT(*) AS streak FROM runs "
            "GROUP BY grp ORDER BY streak DESC LIMIT 1",
            (since, until),
        )
        streak_row = cursor.fetchone()
        longest_streak_days = int(streak_row["streak"]) if streak_row else 0

        cursor.close()
        return {
            "year": year,
            "total_plays": total_plays,
            "total_listening_time_ms": total_ms,
            "has_legacy_rows": has_legacy_rows,
            "top_track": top_track,
            "top_artist": top_artist,
            "top_album": top_album,
            "busiest_day": busiest_day,
            "top_hour": top_hour,
            "first_play": first_play,
            "longest_streak_days": longest_streak_days,
        }

    def recent_plays(self, limit: int) -> List[Dict[str, object]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT pe.id, pe.track_mbid AS mbid, pe.played_at, pe.source, "
            "       t.title, t.artist, t.album, "
            "       COALESCE(NULLIF(t.release_mbid, ''), "
            "                t.album || '|' || t.artist) AS album_id "
            "FROM play_events pe "
            "LEFT JOIN tracks t ON t.mbid = pe.track_mbid "
            "ORDER BY pe.played_at DESC, pe.id DESC LIMIT ?",
            (int(limit),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
