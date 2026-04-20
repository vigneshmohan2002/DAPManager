"""
Album completion: discovers album metadata for orphan tracks, identifies
incomplete albums, and queues missing songs for download.
"""

import logging
from typing import Dict, List, Optional, Tuple

from . import musicbrainz_client as mb
from .db_manager import DatabaseManager, DownloadItem
from .download_request import queue_or_forward

logger = logging.getLogger(__name__)


def fetch_album_tracklist(release_mbid: str) -> Dict[Tuple[int, int], str]:
    """
    Queries MusicBrainz for the full tracklist of a release.
    Returns: {(disc_num, track_num): "Track Title"}
    """
    try:
        result = mb.get_release_by_id(
            release_mbid, includes=["media", "recordings"]
        )

        track_map = {}
        if "release" in result and "medium-list" in result["release"]:
            for medium in result["release"]["medium-list"]:
                try:
                    disc_num = int(medium["position"])
                except ValueError:
                    disc_num = 1

                if "track-list" in medium:
                    for track in medium["track-list"]:
                        try:
                            t_num = int(track["number"])
                            t_title = track["recording"]["title"]
                            track_map[(disc_num, t_num)] = t_title
                        except (ValueError, KeyError):
                            pass
        return track_map

    except Exception as e:
        logger.error(f"Failed to fetch tracklist for {release_mbid}: {e}")
        return {}


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

        target = None
        clean_hint = (album_hint or "").strip().lower()

        # Prefer matching album name
        if clean_hint:
            for rel in releases:
                if rel.get("title", "").strip().lower() == clean_hint:
                    target = rel
                    break

        # Fallback to official release
        if not target:
            for rel in releases:
                if rel.get("status", "").lower() == "official":
                    target = rel
                    break

        if not target:
            target = releases[0]

        release_mbid = target["id"]

        details = mb.get_release_by_id(release_mbid, includes=["media"])
        total = 0
        if "release" in details and "medium-list" in details["release"]:
            for m in details["release"]["medium-list"]:
                total += int(m.get("track-count", 0))

        album_title = target.get("title", album_hint or "Unknown Album")
        if total > 0:
            db.update_album_metadata(release_mbid, album_title, total)

        return release_mbid

    except Exception as e:
        logger.error(f"Album discovery failed for recording {recording_mbid}: {e}")
        return None


def get_missing_tracks_for_album(db: DatabaseManager, release_mbid: str) -> dict:
    """
    Returns details about missing tracks for a specific album.
    """
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT artist, album FROM tracks WHERE release_mbid = ? AND local_path IS NOT NULL LIMIT 1",
        (release_mbid,),
    )
    row = cursor.fetchone()
    cursor.close()

    if not row:
        return {"error": f"No local tracks found for release {release_mbid}"}

    artist_name, album_title = row[0], row[1]

    local_tracks = set()
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT disc_number, track_number FROM tracks WHERE release_mbid = ? AND local_path IS NOT NULL",
        (release_mbid,),
    )
    for r in cursor.fetchall():
        local_tracks.add((r[0], r[1]))
    cursor.close()

    official_tracks = fetch_album_tracklist(release_mbid)
    if not official_tracks:
        return {"error": "Failed to fetch official tracklist from MusicBrainz"}

    missing_items = []
    for (disc, track), title in sorted(official_tracks.items()):
        if (disc, track) not in local_tracks:
            missing_items.append({"disc": disc, "track": track, "title": title})

    return {
        "artist": artist_name,
        "album": album_title,
        "mbid": release_mbid,
        "total_tracks": len(official_tracks),
        "have": len(local_tracks),
        "missing_count": len(missing_items),
        "missing_tracks": missing_items,
    }


def queue_missing_tracks_for_album(
    db: DatabaseManager, release_mbid: str, progress_callback=None
) -> dict:
    """
    Identifies missing tracks for an album and queues them for download.
    Returns a summary dict: {album, artist, queued, skipped_existing}.
    """
    data = get_missing_tracks_for_album(db, release_mbid)

    if "error" in data:
        logger.warning(f"Skipping album {release_mbid}: {data['error']}")
        return {"error": data["error"]}

    artist = data["artist"]
    album = data["album"]
    missing = data["missing_tracks"]
    total = data["total_tracks"]
    missing_count = data["missing_count"]

    if missing_count == 0:
        return {"album": album, "artist": artist, "queued": 0, "skipped_existing": 0}

    def _report(msg):
        logger.info(msg)
        if progress_callback:
            progress_callback({"detail": msg})

    queued = 0
    skipped = 0

    # If missing a lot, queue the whole album as one download
    missing_pct = (missing_count / total) * 100 if total else 0
    if missing_count > 3 or missing_pct > 60:
        query = f"::ALBUM:: {artist} - {album}"
        if db.is_download_queued(query):
            _report(f"  Album already queued: {artist} - {album}")
            skipped = 1
        else:
            queue_or_forward(db, DownloadItem(
                search_query=query,
                playlist_id="COMPLETER",
                mbid_guess=release_mbid,
                status="pending",
            ))
            _report(f"  Queued full album: {artist} - {album} ({missing_count}/{total} missing)")
            queued = 1
    else:
        for item in missing:
            query = f"{artist} - {item['title']}"
            if db.is_download_queued(query):
                skipped += 1
                continue
            queue_or_forward(db, DownloadItem(
                search_query=query,
                playlist_id="COMPLETER",
                mbid_guess="",
                status="pending",
            ))
            _report(f"  Queued: {query}")
            queued += 1

    return {
        "album": album,
        "artist": artist,
        "queued": queued,
        "skipped_existing": skipped,
    }


def complete_albums(db: DatabaseManager, progress_callback=None) -> dict:
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
                # Update the track's release_mbid in the DB
                cursor = db.conn.cursor()
                cursor.execute(
                    "UPDATE tracks SET release_mbid = ?, updated_at = CURRENT_TIMESTAMP WHERE mbid = ?",
                    (release_mbid, track.mbid),
                )
                db.conn.commit()
                cursor.close()
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
