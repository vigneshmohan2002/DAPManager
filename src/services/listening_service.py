"""Listening-event validation and read-model construction for HTTP adapters."""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Protocol, Union


MAX_LISTENED_MS = 30 * 60 * 1000


class ListeningStore(Protocol):
    def record_play_event(
        self,
        mbid: str,
        source: Optional[str] = None,
        listened_ms: Optional[int] = None,
    ) -> int: ...

    def play_count_since(self, since_iso: Optional[str] = None) -> int: ...

    def listening_time_since(self, since_iso: Optional[str] = None) -> int: ...

    def top_tracks_since(
        self,
        since_iso: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]: ...

    def top_artists_since(
        self,
        since_iso: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]: ...

    def recent_plays(self, limit: int = 20) -> List[Dict[str, Any]]: ...

    def plays_by_hour(
        self,
        since_iso: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...


@dataclass(frozen=True)
class ListeningServiceResult:
    payload: Dict[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class PlayEventRequest:
    mbid: str
    source: Optional[str]
    listened_ms: Optional[int]


PreparedPlayEvent = Union[PlayEventRequest, ListeningServiceResult]


def prepare_play_event(data: Mapping[str, Any]) -> PreparedPlayEvent:
    """Validate a play event in the route's established field order."""
    mbid = (data.get("mbid") or "").strip()
    if not mbid:
        return ListeningServiceResult(
            {"success": False, "message": "mbid is required"},
            400,
        )

    source = data.get("source")
    if source is not None and not isinstance(source, str):
        return ListeningServiceResult(
            {
                "success": False,
                "message": "source must be a string when provided",
            },
            400,
        )

    listened_ms_raw = data.get("listened_ms")
    listened_ms: Optional[int] = None
    if listened_ms_raw is not None:
        if isinstance(listened_ms_raw, bool) or not isinstance(
            listened_ms_raw,
            (int, float),
        ):
            return ListeningServiceResult(
                {
                    "success": False,
                    "message": "listened_ms must be a number when provided",
                },
                400,
            )
        if listened_ms_raw < 0:
            return ListeningServiceResult(
                {
                    "success": False,
                    "message": "listened_ms must be non-negative",
                },
                400,
            )
        listened_ms = min(int(listened_ms_raw), MAX_LISTENED_MS)

    return PlayEventRequest(
        mbid=mbid,
        source=source,
        listened_ms=listened_ms,
    )


def record_play(db: ListeningStore, event: PlayEventRequest) -> int:
    """Persist one already-validated play event through the DB facade."""
    return db.record_play_event(
        event.mbid,
        source=event.source,
        listened_ms=event.listened_ms,
    )


def normalize_stats_limit(raw: str) -> int:
    """Parse and clamp the public play-stat limit to the established range."""
    return max(1, min(200, int(raw)))


def build_play_stats(
    db: ListeningStore,
    *,
    since: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    """Aggregate listening cards and pad the hour histogram to 24 entries."""
    total = db.play_count_since(since)
    listening_time_ms = db.listening_time_since(since)
    top_tracks = db.top_tracks_since(since, limit=limit)
    top_artists = db.top_artists_since(since, limit=limit)
    recent = db.recent_plays(limit=limit)
    hours = db.plays_by_hour(since)

    hour_counts = [0] * 24
    for row in hours:
        hour = row.get("hour")
        if isinstance(hour, int) and 0 <= hour < 24:
            hour_counts[hour] = int(row.get("plays") or 0)

    return {
        "success": True,
        "total": total,
        "listening_time_ms": listening_time_ms,
        "top_tracks": top_tracks,
        "top_artists": top_artists,
        "recent": recent,
        "hour_of_day": hour_counts,
    }
