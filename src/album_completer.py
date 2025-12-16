"""
Logic to identify missing tracks in albums and queue them for download.
"""

import logging
import time
import musicbrainzngs
from typing import Dict, Tuple
from .db_manager import DatabaseManager, DownloadItem
from .downloader import main_run_downloader

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
    # Get Album Info (for logging/search)
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

    # Get Local Tracks (Disc, Track)
    local_tracks = set()
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT disc_number, track_number FROM tracks WHERE release_mbid = ? AND local_path IS NOT NULL",
        (release_mbid,),
    )
    for r in cursor.fetchall():
        local_tracks.add((r[0], r[1]))
    cursor.close()

    # Get Official List
    official_tracks = fetch_album_tracklist(release_mbid)

    if not official_tracks:
        print("  > Failed to fetch official tracklist (or album has no tracks).")
        return

    # Find Missing & Queue
    missing_count = 0
    missing_items = []

    for (disc, track), title in official_tracks.items():
        if (disc, track) not in local_tracks:
            missing_count += 1
            missing_items.append((disc, track, title))
            
    if missing_count == 0:
        print("  > Album complete!")
    else:
        print(f"  > Missing {missing_count} tracks.")
        
        # --- NEW LOGIC: Queue Album if many missing ---
        # Threshold: If missing more than 60% OR more than 3 tracks
        total_tracks = len(official_tracks) or 1
        missing_pct = (missing_count / total_tracks) * 100
        
        if missing_count > 3 or missing_pct > 60:
            print(f"  > Missing {missing_count}/{total_tracks} ({missing_pct:.1f}%). Queuing ENTIRE ALBUM.")
            print(f"  > Missing {missing_count} tracks. Queuing ENTIRE ALBUM download.")
            query = f"::ALBUM:: {artist_name} - {album_title}"
            
            # Check if already queued
            q_cursor = db.conn.cursor()
            q_cursor.execute("SELECT id FROM downloads WHERE search_query = ?", (query,))
            if q_cursor.fetchone():
                print("    (Album already in queue)")
            else:
                db.add_to_queue(
                    search_query=query,
                    track_mbid="ALBUM_MODE", # Special marker
                    artist=artist_name,
                    title=f"[Full Album] {album_title}"
                )
                print("    [+] Added full album to download queue.")
        else:
            # Traditional: Queue individual tracks
            for disc, track, title in missing_items:
                print(f"    - Missing: {disc}-{track} {title}")
                
                # Construct search query
                query = f"{artist_name} - {title}"
                
                # Check if already in queue
                # (Simple check to avoid duplicates)
                q_cursor = db.conn.cursor()
                q_cursor.execute("SELECT id FROM downloads WHERE search_query = ?", (query,))
                if q_cursor.fetchone():
                    print("      (Already in queue)")
                    continue

                # Add to DB Queue
                # We don't have the specific Track MBID easily here unless we query MB again
                # But we can leave MBID empty or put a placeholder. 
                # Ideally we fetch it from official_tracks if we stored it.
                # For now, use placeholder or rely on lookup.
                db.add_to_queue(
                    search_query=query,
                    track_mbid="", # Scanner will resolve it
                    artist=artist_name,
                    title=title
                )
                print("      [+] Added to download queue.")


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

def audit_library(db: DatabaseManager):
    """Identify albums that are missing tracks based on total_tracks metadata."""
    try:
        incomplete_albums = db.get_incomplete_albums()
    except AttributeError:
        logger.error("Error: db_manager.py has not been updated with 'get_incomplete_albums'.")
        return
    except Exception as e:
        logger.error(f"Error querying database: {e}")
        return

    if not incomplete_albums:
        logger.info("All identified albums in your library appear to be complete!")
        return

    logger.info(f"ALBUM COMPLETENESS AUDIT: Found {len(incomplete_albums)} incomplete albums.")
    for item in incomplete_albums:
        status = f"{item['have']}/{item['total']} (Miss {item['missing']})"
        logger.info(f"MISSING: {item['artist']} - {item['album']} [{status}]")
