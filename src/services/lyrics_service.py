"""Lyrics cache, lookup, and override policy outside the Flask adapter."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, cast


logger = logging.getLogger(__name__)

LYRICS_TTL_SECONDS = 30 * 24 * 60 * 60


class LyricsStore(Protocol):
    def get_lyrics(self, track_mbid: str) -> Optional[Dict[str, Any]]: ...

    def upsert_lyrics(
        self,
        track_mbid: str,
        lrc: Optional[str],
        synced: bool,
        source: str,
    ) -> None: ...

    def delete_lyrics(self, track_mbid: str) -> None: ...

    def get_live_track_identity(self, mbid: str) -> Optional[Dict[str, Any]]: ...


LyricsFetcher = Callable[..., Optional[Mapping[str, Any]]]
FreshnessCheck = Callable[[str], bool]


@dataclass(frozen=True)
class LyricsServiceResult:
    payload: Dict[str, Any]
    status_code: int = 200


def is_lyrics_fresh(
    fetched_at: str,
    *,
    ttl_seconds: int = LYRICS_TTL_SECONDS,
    now: Optional[datetime] = None,
) -> bool:
    """Return cache freshness while tolerating malformed legacy timestamps."""
    try:
        timestamp = datetime.fromisoformat(fetched_at.replace(" ", "T"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return (current - timestamp).total_seconds() < ttl_seconds
    except Exception:
        return True


def _lyrics_payload(
    row: Mapping[str, Any],
    *,
    stale: bool = False,
) -> Dict[str, Any]:
    payload = {
        "success": True,
        "lrc": row["lrc"],
        "synced": bool(row["synced"]),
        "source": row["source"],
        "fetched_at": row["fetched_at"],
    }
    if stale:
        payload["stale"] = True
    return payload


def save_manual_lyrics(
    db: LyricsStore,
    *,
    mbid: str,
    lrc: Optional[str],
    synced: bool,
) -> LyricsServiceResult:
    """Clear a manual override or persist it and return the stored row."""
    if not (lrc or "").strip():
        db.delete_lyrics(mbid)
        return LyricsServiceResult({"success": True, "lrc": None})

    db.upsert_lyrics(mbid, lrc, synced, "manual")
    row = cast(Mapping[str, Any], db.get_lyrics(mbid))
    return LyricsServiceResult(_lyrics_payload(row))


def load_track_lyrics(
    db: LyricsStore,
    *,
    mbid: str,
    fetch_lyrics: LyricsFetcher,
    freshness_check: FreshnessCheck = is_lyrics_fresh,
) -> LyricsServiceResult:
    """Serve cache first, then fetch and persist an LRCLIB hit or miss."""
    row = db.get_lyrics(mbid)
    if row and (
        row["source"] == "manual"
        or freshness_check(cast(str, row["fetched_at"]))
    ):
        return LyricsServiceResult(_lyrics_payload(row))

    track = db.get_live_track_identity(mbid)
    if track is None:
        return LyricsServiceResult(
            {"success": False, "message": "track not found"},
            404,
        )

    try:
        result = fetch_lyrics(
            track_name=track["title"],
            artist_name=track["artist"],
            album_name=track["album"] or None,
        )
    except RuntimeError as exc:
        logger.warning("lrclib fetch failed: %s", exc)
        if row is not None:
            return LyricsServiceResult(_lyrics_payload(row, stale=True))
        return LyricsServiceResult(
            {"success": False, "message": "lyrics lookup failed"},
            502,
        )

    if result is None:
        db.upsert_lyrics(mbid, None, False, "lrclib")
        return LyricsServiceResult(
            {
                "success": True,
                "lrc": None,
                "synced": False,
                "source": "lrclib",
                "fetched_at": None,
            }
        )

    db.upsert_lyrics(
        mbid,
        cast(Optional[str], result["lrc"]),
        cast(bool, result["synced"]),
        "lrclib",
    )
    stored = cast(Mapping[str, Any], db.get_lyrics(mbid))
    return LyricsServiceResult(_lyrics_payload(stored))
