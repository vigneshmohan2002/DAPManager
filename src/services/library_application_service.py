"""Library application policy independent of Flask request handling.

The HTTP adapter retains URL/input validation and response serialization.  This
module owns the like propagation policy and the Home read model while accessing
persistence only through the existing ``DatabaseManager`` facade.
"""

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

import requests


logger = logging.getLogger(__name__)


class LikeStore(Protocol):
    """Smallest persistence boundary needed by the like workflow."""

    def set_track_liked(self, mbid: str, liked: bool) -> Optional[bool]: ...

    def ensure_liked_songs_playlist(self) -> str: ...


class HomeStore(Protocol):
    """Read facade consumed by the desktop Home payload builder."""

    def recent_plays(self, limit: int = 20) -> List[Dict[str, Any]]: ...

    def top_artists_since(
        self,
        since_iso: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]: ...

    def get_liked_tracks_summary(self, limit: int = 6) -> Dict[str, Any]: ...


class UpstreamResponse(Protocol):
    status_code: int


LikeDatabaseFactory = Callable[[str], AbstractContextManager[LikeStore]]
RequestSender = Callable[..., UpstreamResponse]
DailyMixLoader = Callable[[HomeStore], List[Dict[str, Any]]]


@dataclass(frozen=True)
class LibraryApplicationResult:
    """JSON-shaped service outcome translated by the Flask adapter."""

    payload: Dict[str, Any]
    status_code: int = 200


def _config_mapping(config_data: object) -> Mapping[str, Any]:
    """Match the route's strict real-dictionary guard for mocked config."""
    return config_data if isinstance(config_data, dict) else {}


def apply_track_like(
    *,
    db_path: str,
    database_factory: LikeDatabaseFactory,
    config_data: object,
    mbid: str,
    method: str,
    request_sender: RequestSender,
    master_configured: Optional[bool] = None,
) -> LibraryApplicationResult:
    """Persist a like locally or authoritatively through the configured master.

    A successful satellite proxy is mirrored locally in the same order as the
    legacy route.  Mirror failures remain non-fatal because the master is the
    source of truth and the next catalog pull will reconcile the row.
    """
    liked = method == "POST"
    config_values = _config_mapping(config_data)
    configured_master_url = config_values.get("master_url") or ""
    has_master = (
        bool(configured_master_url.strip())
        if master_configured is None
        else master_configured
    )
    if has_master:
        master_url = configured_master_url.rstrip("/")
        headers: Dict[str, str] = {}
        token = (config_values.get("api_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = request_sender(
                method,
                f"{master_url}/api/library/tracks/{mbid}/like",
                headers=headers,
                timeout=(5, 10),
            )
        except requests.RequestException as exc:
            logger.warning("like proxy to master failed: %s", exc)
            return LibraryApplicationResult(
                {
                    "success": False,
                    "message": "couldn't reach master to save the like",
                },
                502,
            )

        if response.status_code == 404:
            return LibraryApplicationResult(
                {
                    "success": False,
                    "message": "track not found on master",
                },
                404,
            )
        if response.status_code >= 400:
            return LibraryApplicationResult(
                {
                    "success": False,
                    "message": f"master returned {response.status_code}",
                },
                response.status_code,
            )

        try:
            with database_factory(db_path) as db:
                db.set_track_liked(mbid, liked)
                if liked:
                    db.ensure_liked_songs_playlist()
        except Exception as exc:
            logger.warning("local mirror after like-proxy failed: %s", exc)
        return LibraryApplicationResult({"success": True, "liked": liked})

    try:
        with database_factory(db_path) as db:
            new_state = db.set_track_liked(mbid, liked)
            if new_state is None:
                return LibraryApplicationResult(
                    {
                        "success": False,
                        "message": "track not found or orphaned",
                    },
                    404,
                )
            if liked:
                db.ensure_liked_songs_playlist()
    except Exception as exc:
        logger.exception("api_library_track_like failed")
        return LibraryApplicationResult(
            {"success": False, "message": str(exc)},
            500,
        )
    return LibraryApplicationResult({"success": True, "liked": new_state})


def home_window_start(now: Optional[datetime] = None) -> str:
    """Return the established fixed 30-day UTC cutoff for Home."""
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")


def build_home_payload(
    db: HomeStore,
    daily_mix_loader: DailyMixLoader,
    *,
    since_30d: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build all Home cards in their established query and response order."""
    window_start = since_30d or home_window_start(now)
    recent = db.recent_plays(limit=12)
    top_artists = db.top_artists_since(window_start, limit=8)
    liked = db.get_liked_tracks_summary(limit=6)
    daily_mixes = daily_mix_loader(db)

    jump_back_in: List[Dict[str, Any]] = []
    seen_albums = set()
    for row in recent:
        album_id = row.get("album_id")
        album = row.get("album")
        if not album_id or not album or album_id in seen_albums:
            continue
        seen_albums.add(album_id)
        jump_back_in.append(
            {
                "album_id": album_id,
                "title": album,
                "artist": row.get("artist") or "",
            }
        )
        if len(jump_back_in) >= 6:
            break

    return {
        "success": True,
        "recent": recent,
        "top_artists": top_artists,
        "liked": liked,
        "jump_back_in": jump_back_in,
        "daily_mixes": daily_mixes,
    }
