"""
Album completion: discovers album metadata for orphan tracks, identifies
incomplete albums, and queues missing songs for download.
"""

import logging
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Set,
    Tuple,
    TypedDict,
    Union,
    cast,
)

from . import musicbrainz_client as mb
from .db_manager import DatabaseManager, DownloadItem
from .download_request import queue_or_forward

logger = logging.getLogger(__name__)

TrackPosition = Tuple[int, int]
LocalTrackPosition = Tuple[Optional[int], Optional[int]]
AlbumTracklist = Dict[TrackPosition, str]
CompletionProgressCallback = Callable[[Dict[str, str]], None]


class MissingTrack(TypedDict):
    disc: int
    track: int
    title: str


class MissingAlbumDetails(TypedDict):
    artist: str
    album: Optional[str]
    mbid: str
    total_tracks: int
    have: int
    missing_count: int
    missing_tracks: List[MissingTrack]


class CompletionError(TypedDict):
    error: str


MissingAlbumResult = Union[MissingAlbumDetails, CompletionError]


@dataclass(frozen=True)
class AlbumDiscoveryPlan:
    """Metadata write selected from read-only MusicBrainz responses."""

    release_mbid: str
    album_title: str
    total_tracks: int


@dataclass(frozen=True)
class DownloadPlanItem:
    """One possible queue mutation and its existing progress messages."""

    search_query: str
    mbid_guess: str
    queued_message: str
    duplicate_message: Optional[str] = None


@dataclass(frozen=True)
class AlbumQueuePlan:
    """Side-effect-free queue plan for one incomplete album."""

    artist: str
    album: Optional[str]
    items: Tuple[DownloadPlanItem, ...]


@dataclass(frozen=True)
class TrackReleaseAssignment:
    """One discovered track-to-release write."""

    track_mbid: str
    release_mbid: str


def _parse_album_tracklist(result: Mapping[str, Any]) -> AlbumTracklist:
    """Build a track-position map from a MusicBrainz release response."""
    track_map: AlbumTracklist = {}
    if "release" in result and "medium-list" in result["release"]:
        for medium in result["release"]["medium-list"]:
            try:
                disc_num = int(medium["position"])
            except ValueError:
                disc_num = 1

            if "track-list" in medium:
                for track in medium["track-list"]:
                    try:
                        track_num = int(track["number"])
                        title = track["recording"]["title"]
                        track_map[(disc_num, track_num)] = title
                    except (ValueError, KeyError):
                        continue
    return track_map


def fetch_album_tracklist(release_mbid: str) -> AlbumTracklist:
    """
    Queries MusicBrainz for the full tracklist of a release.
    Returns: {(disc_num, track_num): "Track Title"}
    """
    try:
        result = mb.get_release_by_id(
            release_mbid, includes=["media", "recordings"]
        )

        return _parse_album_tracklist(result)

    except Exception as e:
        logger.error(f"Failed to fetch tracklist for {release_mbid}: {e}")
        return {}


def _select_release(
    releases: List[dict], album_hint: str = ""
) -> Optional[dict]:
    """Choose the same preferred release without performing any writes."""
    target = None
    clean_hint = (album_hint or "").strip().lower()
    if clean_hint:
        for release in releases:
            if release.get("title", "").strip().lower() == clean_hint:
                target = release
                break

    if not target:
        for release in releases:
            if release.get("status", "").lower() == "official":
                target = release
                break

    if not target and releases:
        target = releases[0]
    return target


def _build_album_discovery_plan(
    target: Mapping[str, Any],
    details: Mapping[str, Any],
    album_hint: str,
) -> AlbumDiscoveryPlan:
    """Create the album metadata mutation plan from API responses."""
    release_mbid = cast(str, target["id"])
    total_tracks = 0
    if "release" in details and "medium-list" in details["release"]:
        for medium in details["release"]["medium-list"]:
            total_tracks += int(medium.get("track-count", 0))

    album_title = cast(
        str, target.get("title", album_hint or "Unknown Album")
    )
    return AlbumDiscoveryPlan(
        release_mbid=release_mbid,
        album_title=album_title,
        total_tracks=total_tracks,
    )


def _execute_album_discovery_plan(
    db: DatabaseManager, plan: AlbumDiscoveryPlan
) -> None:
    """Persist a discovery plan at the legacy album-metadata commit point."""
    if plan.total_tracks <= 0:
        return
    db.update_album_metadata(
        plan.release_mbid, plan.album_title, plan.total_tracks
    )


def _execute_track_release_assignment(
    db: DatabaseManager, assignment: TrackReleaseAssignment
) -> None:
    """Persist a track assignment at the original per-track commit point."""
    db.update_track_release_mbid(
        assignment.track_mbid, assignment.release_mbid
    )


def discover_album_for_track(
    db: DatabaseManager, recording_mbid: str, album_hint: str = ""
) -> Optional[str]:
    """
    Look up a recording's album on MusicBrainz and populate the albums table.
    Returns the release_mbid if found, else None.
    """
    try:
        result = mb.get_recording_by_id(
            recording_mbid, includes=["releases"]
        )

        if "recording" not in result:
            return None
        releases = result["recording"].get("release-list", [])
        if not releases:
            return None

        target = _select_release(releases, album_hint)
        if target is None:
            return None

        details = mb.get_release_by_id(target["id"], includes=["media"])
        plan = _build_album_discovery_plan(target, details, album_hint)
        _execute_album_discovery_plan(db, plan)
        return plan.release_mbid

    except Exception as e:
        logger.error(f"Album discovery failed for recording {recording_mbid}: {e}")
        return None


def _build_missing_album_details(
    *,
    release_mbid: str,
    artist: str,
    album: Optional[str],
    local_tracks: Set[LocalTrackPosition],
    official_tracks: AlbumTracklist,
) -> MissingAlbumDetails:
    """Create a completion snapshot without database or network access."""
    missing_items: List[MissingTrack] = []
    for (disc, track), title in sorted(official_tracks.items()):
        if (disc, track) not in local_tracks:
            missing_items.append({"disc": disc, "track": track, "title": title})

    return {
        "artist": artist,
        "album": album,
        "mbid": release_mbid,
        "total_tracks": len(official_tracks),
        "have": len(local_tracks),
        "missing_count": len(missing_items),
        "missing_tracks": missing_items,
    }


def get_missing_tracks_for_album(
    db: DatabaseManager, release_mbid: str
) -> MissingAlbumResult:
    """
    Returns details about missing tracks for a specific album.
    """
    snapshot = db.get_local_album_snapshot(release_mbid)
    if snapshot is None:
        return {"error": f"No local tracks found for release {release_mbid}"}

    official_tracks = fetch_album_tracklist(release_mbid)
    if not official_tracks:
        return {"error": "Failed to fetch official tracklist from MusicBrainz"}
    return _build_missing_album_details(
        release_mbid=release_mbid,
        artist=snapshot["artist"],
        album=snapshot["album"],
        local_tracks=snapshot["positions"],
        official_tracks=official_tracks,
    )


def _build_album_queue_plan(
    data: MissingAlbumDetails, release_mbid: str
) -> AlbumQueuePlan:
    """Choose album-versus-track downloads without mutating the queue."""
    artist = data["artist"]
    album = data["album"]
    missing = data["missing_tracks"]
    total = data["total_tracks"]
    missing_count = data["missing_count"]
    if missing_count == 0:
        return AlbumQueuePlan(artist=artist, album=album, items=())

    missing_pct = (missing_count / total) * 100 if total else 0
    if missing_count > 3 or missing_pct > 60:
        query = f"::ALBUM:: {artist} - {album}"
        return AlbumQueuePlan(
            artist=artist,
            album=album,
            items=(
                DownloadPlanItem(
                    search_query=query,
                    mbid_guess=release_mbid,
                    queued_message=(
                        f"  Queued full album: {artist} - {album} "
                        f"({missing_count}/{total} missing)"
                    ),
                    duplicate_message=(
                        f"  Album already queued: {artist} - {album}"
                    ),
                ),
            ),
        )

    return AlbumQueuePlan(
        artist=artist,
        album=album,
        items=tuple(
            DownloadPlanItem(
                search_query=f"{artist} - {item['title']}",
                mbid_guess="",
                queued_message=f"  Queued: {artist} - {item['title']}",
            )
            for item in missing
        ),
    )


def _execute_album_queue_plan(
    db: DatabaseManager,
    plan: AlbumQueuePlan,
    report: Callable[[str], None],
) -> Tuple[int, int]:
    """Apply a queue plan sequentially, preserving duplicate checks."""
    queued = 0
    skipped = 0
    for item in plan.items:
        if db.is_download_queued(item.search_query):
            skipped += 1
            if item.duplicate_message:
                report(item.duplicate_message)
            continue
        queue_or_forward(
            db,
            DownloadItem(
                search_query=item.search_query,
                playlist_id="COMPLETER",
                mbid_guess=item.mbid_guess,
                status="pending",
            ),
        )
        report(item.queued_message)
        queued += 1
    return queued, skipped


def queue_missing_tracks_for_album(
    db: DatabaseManager,
    release_mbid: str,
    progress_callback: Optional[CompletionProgressCallback] = None,
) -> dict:
    """
    Identifies missing tracks for an album and queues them for download.
    Returns a summary dict: {album, artist, queued, skipped_existing}.
    """
    data = get_missing_tracks_for_album(db, release_mbid)

    if "error" in data:
        logger.warning(f"Skipping album {release_mbid}: {data['error']}")
        return {"error": data["error"]}

    plan = _build_album_queue_plan(data, release_mbid)
    if not plan.items:
        return {
            "album": plan.album,
            "artist": plan.artist,
            "queued": 0,
            "skipped_existing": 0,
        }

    def _report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback({"detail": msg})

    queued, skipped = _execute_album_queue_plan(db, plan, _report)

    return {
        "album": plan.album,
        "artist": plan.artist,
        "queued": queued,
        "skipped_existing": skipped,
    }


def complete_albums(
    db: DatabaseManager,
    progress_callback: Optional[CompletionProgressCallback] = None,
) -> dict:
    """
    Full album completion pipeline:
      1. Discover album metadata for tracks that are missing it
      2. Identify all incomplete albums
      3. Queue missing tracks for download

    Returns a summary of what was done.
    """
    def _report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback({"message": msg})

    def _detail(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback({"detail": msg})

    summary = {
        "albums_discovered": 0,
        "incomplete_albums": 0,
        "already_complete": 0,
        "tracks_queued": 0,
        "albums_skipped_existing": 0,
        "errors": 0,
        "details": [],
    }

    # ------------------------------------------------------------------
    # Phase 1: Discover album info for orphan tracks
    # ------------------------------------------------------------------
    _report("Phase 1: Discovering album info for unlinked tracks...")
    orphan_tracks = db.get_tracks_missing_album_info()

    if orphan_tracks:
        _report(f"Found {len(orphan_tracks)} tracks without album metadata")

        for i, track in enumerate(orphan_tracks, 1):
            _detail(f"[{i}/{len(orphan_tracks)}] Looking up: {track.artist} - {track.title}")

            release_mbid = discover_album_for_track(
                db, track.mbid, album_hint=track.album or ""
            )

            if release_mbid:
                assignment = TrackReleaseAssignment(
                    track_mbid=track.mbid,
                    release_mbid=release_mbid,
                )
                _execute_track_release_assignment(db, assignment)
                summary["albums_discovered"] += 1
                _detail(f"  Found album: {release_mbid}")
            else:
                _detail(f"  Could not find album for: {track.artist} - {track.title}")
    else:
        _report("No orphan tracks found - all tracks have album metadata")

    # ------------------------------------------------------------------
    # Phase 2: Find incomplete albums and queue missing tracks
    # ------------------------------------------------------------------
    _report("Phase 2: Finding incomplete albums...")
    incomplete = db.get_incomplete_albums()

    if not incomplete:
        _report("All albums in your library are complete!")
        return summary

    summary["incomplete_albums"] = len(incomplete)
    _report(f"Found {len(incomplete)} incomplete albums. Queueing missing tracks...")
    _report("(This may take time due to MusicBrainz API rate limits)")

    for idx, album_info in enumerate(incomplete, 1):
        label = f"{album_info['artist']} - {album_info['album']}"
        status_str = f"{album_info['have']}/{album_info['total']}"
        _report(f"[{idx}/{len(incomplete)}] {label} ({status_str})")

        result = queue_missing_tracks_for_album(
            db, album_info["mbid"], progress_callback=progress_callback
        )

        if "error" in result:
            summary["errors"] += 1
            summary["details"].append({"album": label, "status": "error", "reason": result["error"]})
        elif result["queued"] == 0 and result["skipped_existing"] == 0:
            summary["already_complete"] += 1
            summary["details"].append({"album": label, "status": "complete"})
        else:
            summary["tracks_queued"] += result["queued"]
            summary["albums_skipped_existing"] += (1 if result["skipped_existing"] > 0 and result["queued"] == 0 else 0)
            summary["details"].append({
                "album": label,
                "status": "queued",
                "queued": result["queued"],
                "skipped": result["skipped_existing"],
            })

    _report(
        f"Complete! Queued {summary['tracks_queued']} downloads across "
        f"{summary['incomplete_albums']} incomplete albums "
        f"({summary['errors']} errors)"
    )

    return summary


def audit_library(db: DatabaseManager):
    """Quick audit: log incomplete albums without taking action."""
    try:
        incomplete_albums = db.get_incomplete_albums()
    except Exception as e:
        logger.error(f"Error querying database: {e}")
        return []

    if not incomplete_albums:
        logger.info("All identified albums in your library appear to be complete!")
        return []

    logger.info(f"ALBUM COMPLETENESS AUDIT: Found {len(incomplete_albums)} incomplete albums.")
    for item in incomplete_albums:
        status = f"{item['have']}/{item['total']} (missing {item['missing']})"
        logger.info(f"INCOMPLETE: {item['artist']} - {item['album']}  [{status}]")

    return incomplete_albums
