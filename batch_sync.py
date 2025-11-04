import os
import sys
import time
from config_manager import get_config
from db_manager import DatabaseManager
from spotify_client import SpotifyClient
from downloader import main_run_downloader
from sync_ipod import main_run_sync


def batch_sync():
    """Direct batch sync using manager.py components"""

    # Set Spotify credentials (replace with your actual credentials)
    os.environ["SPOTIPY_CLIENT_ID"] = "your_actual_client_id_here"
    os.environ["SPOTIPY_CLIENT_SECRET"] = "your_actual_client_secret_here"

    # Load configuration
    config = get_config()
    db_path = config.db_path

    # Playlists to sync
    playlist_urls = [
        "https://open.spotify.com/playlist/3fiYHjR4MfiF9zSvpPKNhb",
        "https://open.spotify.com/playlist/32wHbeFoVABa4x6kUy4A22",
    ]

    sync_interval = 300  # 5 minutes

    print("Starting direct batch sync...")

    try:
        cycle_count = 0
        while True:
            cycle_count += 1
            print(f"\n" + "#" * 80)
            print(f"SYNC CYCLE #{cycle_count}")
            print(f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("#" * 80)

            with DatabaseManager(db_path) as db:
                # Add playlists
                print(f"\nADDING {len(playlist_urls)} PLAYLISTS")
                for i, url in enumerate(playlist_urls, 1):
                    print(f"\nPlaylist {i}/{len(playlist_urls)}: {url}")
                    try:
                        spotify_client = SpotifyClient(db)
                        spotify_client.process_playlist(url)
                        print(f"OK - Playlist added successfully")
                        time.sleep(2)
                    except Exception as e:
                        print(f"ERROR - Failed to add playlist: {e}")

                # Run downloads
                print(f"\nRUNNING DOWNLOAD QUEUE")
                try:
                    main_run_downloader(db, config._config)
                    print("OK - Downloads completed")
                    time.sleep(5)
                except Exception as e:
                    print(f"ERROR - Download queue failed: {e}")

                # Sync to iPod
                print(f"\nSYNCING TO IPOD")
                try:
                    main_run_sync(
                        db,
                        config._config,
                        sync_mode="playlists",
                        conversion_format="flac",
                    )
                    print("OK - iPod sync completed successfully")
                except Exception as e:
                    print(f"ERROR - iPod sync failed: {e}")

            print(f"\n" + "#" * 80)
            print(f"SYNC CYCLE #{cycle_count} COMPLETED")
            print(f"Next sync in {sync_interval//60} minutes...")
            print("#" * 80)

            # Wait for next sync cycle
            for remaining in range(sync_interval, 0, -10):
                mins, secs = divmod(remaining, 60)
                print(f"\rNext sync in: {mins:02d}:{secs:02d}", end="", flush=True)
                time.sleep(10)
            print("\r" + " " * 50 + "\r", end="", flush=True)

    except KeyboardInterrupt:
        print(f"\n\nSync stopped by user")
        print(f"Final cycle completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        print(f"Error occurred at: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    batch_sync()
