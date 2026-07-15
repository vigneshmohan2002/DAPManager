"""Typed fleet lookup policy independent of Flask presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, TypedDict


class FleetHolder(TypedDict, total=False):
    device_id: str
    local_path: Optional[str]
    reported_at: str


class FleetTrackMatch(TypedDict, total=False):
    mbid: str
    artist: str
    title: str
    album: str
    device_count: int


class FleetStore(Protocol):
    """Database façade consumed by a fleet track lookup."""

    def get_devices_holding_mbid(
        self,
        mbid: str,
    ) -> List[FleetHolder]: ...

    def find_tracks_for_fleet_search(
        self,
        query: str,
    ) -> List[FleetTrackMatch]: ...


@dataclass(frozen=True)
class FleetServiceResult:
    """JSON-shaped outcome translated by the HTTP adapter."""

    payload: Dict[str, Any]
    status_code: int = 200


def lookup_fleet_track(
    db: FleetStore,
    *,
    mbid: str,
    query: str,
) -> FleetServiceResult:
    """Resolve one MBID or enrich metadata matches with device holders.

    MBID deliberately takes precedence when both parameters are supplied,
    matching the existing route.  Persistence errors propagate to the HTTP
    adapter so it retains its established logging and 500 translation.
    """
    if mbid:
        holders = db.get_devices_holding_mbid(mbid)
        return FleetServiceResult(
            {"success": True, "mbid": mbid, "holders": holders}
        )

    if not query:
        return FleetServiceResult(
            {"success": False, "message": "provide mbid or q"},
            400,
        )

    matches = db.find_tracks_for_fleet_search(query)
    enriched: List[Dict[str, Any]] = []
    for row in matches:
        holders = db.get_devices_holding_mbid(row["mbid"])
        enriched.append({**row, "holders": holders})
    return FleetServiceResult(
        {"success": True, "query": query, "results": enriched}
    )
