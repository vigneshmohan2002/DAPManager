import os
import sys
import time
import logging
from .config_manager import get_config
from .db_manager import DatabaseManager
from .spotify_client import SpotifyClient
from .downloader import main_run_downloader
from .sync_ipod import main_run_sync
from .library_scanner import main_scan_library

# Set logging for the batch script
logger = logging.getLogger(__name__)

# Maximum number of attempts to run the download queue
# Per your request, this is set to 1
MAX_DOWNLOAD_ATTEMPTS = 1


def batch_sync():
    """Full batch sync process with one download attempt:
    1. Scan local library
    2. Queue Spotify playlists
    3. Download Loop (1 attempt)
    4. Sync ENTIRE iPod library (with sorting & playlists)
    """

    # Set Spotify credentials (replace with your actual credentials)
    os.environ["SPOTIPY_CLIENT_ID"] = "523ee113d2f04f62a53abb375cbb1730"
    os.environ["SPOTIPY_CLIENT_SECRET"] = "e481eeb8ad2a48f0a351b25663b546ab"
    os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8080/callback"

    # Load configuration
    config = get_config()
    db_path = config.db_path
    config_dict = config._config

    # Playlists to sync
    playlist_urls = [
        "https://open.spotify.com/playlist/7naaRy7WIwE1kkUUdah5y3?si=f6d81f0a7b364e61",  # Gym
        "https://open.spotify.com/playlist/3EZ7FA7nqNyVZ6A3X7u6Sb?si=55675d6041ce43f5",  # white girl bangers
        "https://open.spotify.com/playlist/3fiYHjR4MfiF9zSvpPKNhb?si=3722ba7bdd3d41b6",  # Running
        "https://open.spotify.com/playlist/32wHbeFoVABa4x6kUy4A22?si=b30c98e74ad34cc1",  # Love
        "https://open.spotify.com/playlist/5UhBIiXlHliYcz7cb2Q8Gc?si=9e1823f63a43477d",  # Coding
    ]

    print("\n" + "#" * 80)
    print("Starting DAP Manager Full Batch Sync...")
    print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 80)

    try:
        with DatabaseManager(db_path) as db:

            # --- PHASE 1: SCAN LOCAL LIBRARY ---
            print("\n--- PHASE 1: SCANNING LOCAL LIBRARY ---")
            print("Scanning for existing local files...")
            try:
                main_scan_library(db, config_dict)
                print("--- PHASE 1: LOCAL SCAN FINISHED ---")
            except Exception as e:
                print(f"  > ERROR: Failed to scan library: {e}")
                # Continuing to playlist queuing...

            # --- PHASE 2: ADD PLAYLISTS / QUEUE DOWNLOADS ---
            print(f"\n--- PHASE 2: QUEUING {len(playlist_urls)} PLAYLISTS ---")
            for i, url in enumerate(playlist_urls, 1):
                print(f"Playlist {i}/{len(playlist_urls)}: {url}")
                try:
                    spotify_client = SpotifyClient(db)
                    spotify_client.process_playlist(url)
                    print(f"  > OK: Tracks added to download queue.")
                    time.sleep(1)
                except Exception as e:
                    print(f"  > ERROR: Failed to add playlist: {e}")

            # ------------------------------------------------------------------
            # --- PHASE 3: RUN DOWNLOADER (1 ATTEMPT) ---
            # ------------------------------------------------------------------
            print("\n--- PHASE 3: STARTING DOWNLOAD QUEUE (1 ATTEMPT) ---")

            for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
                try:
                    # Assuming a method like `get_download_queue_count()` exists in DatabaseManager
                    remaining_count = db.get_download_queue_count()
                except Exception as e:
                    print(
                        f"  [Attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS}] Error checking queue status: {e}. Assuming tracks remain."
                    )
                    remaining_count = 1  # Force another attempt if check fails

                if remaining_count == 0:
                    print(
                        f"  [Attempt {attempt}] Download queue is empty. Proceeding to sync."
                    )
                    break

                print(
                    f"\n  [Attempt {attempt}/{MAX_DOWNLOAD_ATTEMPTS}] Processing {remaining_count} remaining items..."
                )

                # Execute the downloader run
                main_run_downloader(db, config_dict)

                # Pause briefly to prevent hammering the download source
                time.sleep(1.1)
            else:
                # This runs if the loop completed all MAX_DOWNLOAD_ATTEMPTS without breaking
                print(
                    f"\n--- WARNING: Download queue stopped after {MAX_DOWNLOAD_ATTEMPTS} attempt. Tracks may remain pending. ---"
                )

            print("--- PHASE 3: DOWNLOADS FINISHED ---\n")

    except KeyboardInterrupt:
        print(f"\n\nSync stopped by user")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")

    finally:
        print(f"\n" + "#" * 80)
        print(f"Batch Sync Completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("#" * 80)


if __name__ == "__main__":
    # Configure basic logger for the main script
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    batch_sync()
