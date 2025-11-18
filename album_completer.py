# ============================================================================
# FILE: album_completer.py
# ============================================================================
"""
Logic to identify missing tracks in albums and queue them for download.
"""

import logging
import time
import musicbrainzngs
from typing import Dict, Tuple
from db_manager import DatabaseManager, DownloadItem
from downloader import main_run_downloader

logger = logging.getLogger(__name__)


def fetch_album_tracklist(release_mbid: str) -> Dict[Tuple[int, int], str]:
    """
    Queries MusicBrainz for the full tracklist of a release.
    Returns a dictionary: {(disc_num, track_num): "Track Title"}
    """
    try:
        # Set User Agent (Required by MusicBrainz)
        try:
            musicbrainzngs.set_useragent("DAPManager", "0.1.0", "contact@example.com")
        except:
            pass

        # Respect rate limit (1 req/sec)
        time.sleep(1.1)

        # Fetch release with media (discs) and recordings (tracks)
        result = musicbrainzngs.get_release_by_id(
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


def queue_missing_tracks_for_album(db: DatabaseManager, release_mbid: str):
    """
    Compares local DB tracks vs Official MusicBrainz tracklist
    and adds missing songs to the download queue.
    """
    # 1. Get Album Info (for logging/search)
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT artist, title FROM tracks WHERE release_mbid = ? LIMIT 1",
        (release_mbid,),
    )
    row = cursor.fetchone()
    cursor.close()

    if not row:
        print(f"Error: Could not find local artist/album info for MBID {release_mbid}")
        return

    artist_name, album_title = row[0], row[1]
    print(f"\nChecking: {artist_name} - {album_title} ...")

    # 2. Get Local Tracks (Disc, Track)
    local_tracks = set()
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT disc_number, track_number FROM tracks WHERE release_mbid = ? AND local_path IS NOT NULL",
        (release_mbid,),
    )
    for r in cursor.fetchall():
        local_tracks.add((r[0], r[1]))
    cursor.close()

    # 3. Get Official List
    official_tracks = fetch_album_tracklist(release_mbid)

    if not official_tracks:
        print("  > Failed to fetch official tracklist (or album has no tracks).")
        return

    # 4. Find Missing & Queue
    missing_count = 0

    for (disc, track), title in official_tracks.items():
        if (disc, track) not in local_tracks:
            # Construct Search Query: "Artist - Title"
            search_query = f"{artist_name} - {title}"

            print(f"  > Missing: Disc {disc} Track {track}: {title}")

            # Create Download Item
            item = DownloadItem(
                search_query=search_query,
                playlist_id=f"COMPLETER_{release_mbid}",
                mbid_guess=release_mbid,
                status="pending",
            )

            # Add to DB
            db.queue_download(item)
            missing_count += 1

    if missing_count > 0:
        print(f"  > [OK] Queued {missing_count} missing tracks.")
    else:
        print("  > [OK] Album is already complete.")


def batch_complete_all(db: DatabaseManager):
    """
    Automated function to fill ALL gaps in the library.
    """
    incomplete = db.get_incomplete_albums()

    if not incomplete:
        print("No incomplete albums found!")
        return

    print(
        f"\nFound {len(incomplete)} incomplete albums. Starting batch queue process..."
    )
    print("NOTE: This may take time due to API rate limits (1s per album).\n")

    for idx, album in enumerate(incomplete, 1):
        print(f"[{idx}/{len(incomplete)}] Processing {album['album']}...")
        queue_missing_tracks_for_album(db, album["mbid"])

    print("\nBatch queueing complete!")
