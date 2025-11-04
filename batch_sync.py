import os
import sys
import time
from config_manager import get_config
from db_manager import DatabaseManager
from spotify_client import SpotifyClient
from downloader import main_run_downloader
from sync_ipod import main_run_sync


def batch_queue_for_download():
    """Direct batch sync using manager.py components"""

    # Set Spotify credentials (replace with your actual credentials)
    os.environ["SPOTIPY_CLIENT_ID"] = "523ee113d2f04f62a53abb375cbb1730"
    os.environ["SPOTIPY_CLIENT_SECRET"] = "e481eeb8ad2a48f0a351b25663b546ab"
    os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8080/callback"

    # Load configuration
    config = get_config()
    db_path = config.db_path

    # Playlists to sync
    playlist_urls = [
        "https://open.spotify.com/playlist/7naaRy7WIwE1kkUUdah5y3",  # Gym
    ]

    print("Starting direct batch sync...")

    try:
        print(f"\n" + "#" * 80)
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

    except KeyboardInterrupt:
        print(f"\n\nSync stopped by user")
        print(f"Final cycle completed at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        print(f"Error occurred at: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    batch_queue_for_download()
