import os
import sys
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import musicbrainzngs
from db_manager import DatabaseManager, Track, Playlist, DownloadItem
from typing import Optional, List, Tuple
import time

class SpotifyClient:
    """
    Handles fetching Spotify playlist data, matching tracks via
    MusicBrainz, and updating the central database.
    """

    def __init__(self, db: DatabaseManager):
        """
        Initializes the client and authenticates with Spotify and MusicBrainz.
        :param db: An initialized DatabaseManager object.
        """
        self.db = db
        self.sp = None
        self._setup_clients()

    def _setup_clients(self):
        """Initializes and authenticates the API clients."""

        # 1. Setup Spotify (Spotipy)
        # Relies on SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET env variables
        try:
            auth_manager = SpotifyClientCredentials()
            self.sp = spotipy.Spotify(auth_manager=auth_manager)
        except spotipy.oauth2.SpotifyOauthError as e:
            print(
                "Spotify authentication failed. Did you set the environment variables?"
            )
            print("SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET")
            print(f"Error: {e}")
            raise

        # 2. Setup MusicBrainz (musicbrainzngs)
        # This is required by their API terms of service.
        try:
            musicbrainzngs.set_useragent(
                app="DAPManager",
                version="0.1.0",
                contact="plaeseigood2002@gmail.com",  # Using your repo email as a placeholder
            )
        except Exception as e:
            print(f"Error setting MusicBrainz user agent: {e}")
            raise

        print("Spotify and MusicBrainz clients initialized.")

    def process_playlist(self, playlist_url: str):
        """
        Main function to process a Spotify playlist.
        Fetches tracks, finds MBIDs, and updates the database.
        :param playlist_url: The full URL of the Spotify playlist.
        """
        try:
            playlist_id = playlist_url.split("/")[-1].split("?")[0]
        except Exception:
            print(f"E: Invalid Spotify playlist URL: {playlist_url}")
            return

        print(f"Processing playlist ID: {playlist_id}")

        # 1. Fetch playlist details and all tracks (handles pagination)
        playlist_name, spotify_tracks = self._fetch_playlist_details(playlist_id)

        if not playlist_name or not spotify_tracks:
            print("E: Could not fetch playlist details. Exiting.")
            return

        # 2. Add/Update the playlist in our database
        playlist_data = Playlist(
            playlist_id=playlist_id, name=playlist_name, spotify_url=playlist_url
        )
        self.db.add_or_update_playlist(playlist_data)
        print(f"\nPlaylist '{playlist_name}' saved to database.")

        # 3. Process each track
        print(f"Found {len(spotify_tracks)} tracks. Processing each...")
        for i, item in enumerate(spotify_tracks):
            if not item or not item.get("track"):
                print(f"W: Skipping empty track item at position {i}")
                continue

            self._process_track(item["track"], playlist_id, i)
            time.sleep(5)

        print("\nPlaylist processing complete.")

    def _fetch_playlist_details(
        self, playlist_id: str
    ) -> Tuple[Optional[str], Optional[List[dict]]]:
        """
        Fetches the playlist name and a *full* list of all its tracks,
        handling Spotify's API pagination automatically.
        """
        try:
            print("Fetching playlist info...")
            playlist_info = self.sp.playlist(playlist_id, fields="name")
            name = playlist_info["name"]

            print("Fetching tracks (page 1)...")
            results = self.sp.playlist_tracks(playlist_id)
            tracks = results["items"]

            # Handle pagination
            page = 2
            while results["next"]:
                print(f"Fetching tracks (page {page})...")
                results = self.sp.next(results)
                tracks.extend(results["items"])
                page += 1

            return name, tracks

        except spotipy.exceptions.SpotifyException as e:
            print(f"E: Spotify API error fetching playlist. Is the URL correct? {e}")
            return None, None
        except Exception as e:
            print(f"E: An unknown error occurred fetching playlist details: {e}")
            return None, None

    def _get_mbid_from_isrc(self, isrc: str) -> Optional[str]:
        """
        Queries the MusicBrainz API to find a recording MBID from an ISRC.
        :param isrc: The ISRC string.
        :return: A MusicBrainz Recording ID (MBID) or None.
        """
        try:
            # Search for the ISRC
            result = musicbrainzngs.get_recordings_by_isrc(isrc)

            # Extract the first recording ID
            if result.get("isrc") and result["isrc"].get("recording-list"):
                return result["isrc"]["recording-list"][0]["id"]

        except musicbrainzngs.WebServiceError as e:
            print(f"    W: MusicBrainz API error for ISRC {isrc}: {e}")
        except (KeyError, IndexError, TypeError):
            # This just means no match was found, which is common.
            pass
        except Exception as e:
            print(f"    E: Unexpected error querying MusicBrainz: {e}")

        return None

    def _process_track(self, spotify_track: dict, playlist_id: str, track_order: int):
        """
        Processes a single track from the playlist.
        Finds MBID, checks DB, and adds to queue if needed.
        """
        try:
            isrc = spotify_track["external_ids"].get("isrc")
            title = spotify_track["name"]
            # Join multiple artists
            artist = ", ".join([a["name"] for a in spotify_track["artists"]])
            print(f"\nI: Processing: {artist} - {title}")

            if not isrc:
                print(
                    f"    W: No ISRC found for '{title}'. Cannot match to MBID. Skipping."
                )
                return

        except KeyError:
            print(f"    E: Track data is malformed. Skipping.")
            return

        # 1. Get MBID from ISRC
        mbid = self._get_mbid_from_isrc(isrc)

        if not mbid:
            print(f"    W: No MBID found for ISRC {isrc} ('{title}'). Skipping.")
            return

        print(f"    I: Matched: ISRC {isrc} -> MBID {mbid}")

        # 2. Check our database
        existing_track = self.db.get_track_by_mbid(mbid)

        # 3. Create/Update the master track data
        track_data = Track(mbid=mbid, title=title, artist=artist, isrc=isrc)

        # We use add_or_update to ensure metadata (title, artist) is
        # kept fresh from Spotify, even if the track is already known.
        self.db.add_or_update_track(track_data)

        # 4. Link this track to the playlist
        self.db.link_track_to_playlist(playlist_id, mbid, track_order)

        # 5. Decide if download is needed
        if existing_track and existing_track.local_path:
            # We have this track in our local library already.
            print(
                f"    S: Found locally: {os.path.basename(existing_track.local_path)}"
            )
        else:
            # We don't have this file locally. Add to download queue.
            print(f"    Q: Queuing for download.")
            download_item = DownloadItem(
                search_query=f"{artist} - {title}",
                playlist_id=playlist_id,
                mbid_guess=mbid,
            )
            self.db.queue_download(download_item)


# --- Main execution block ---
if __name__ == "__main__":

    # 1. Check for environment variables
    if (
        "SPOTIPY_CLIENT_ID" not in os.environ
        or "SPOTIPY_CLIENT_SECRET" not in os.environ
    ):
        print("=" * 80)
        print("ERROR: Environment variables not set.")
        print("Please set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET before running.")
        print("Example (macOS/Linux): export SPOTIPY_CLIENT_ID='your-id'")
        print("Example (Windows):   set SPOTIPY_CLIENT_ID=your-id")
        print("=" * 80)
        sys.exit(1)

    # 2. Check for command-line argument
    if len(sys.argv) < 2:
        print("=" * 80)
        print("Usage: python spotify_client.py <full_spotify_playlist_url>")
        print("=" * 80)
        sys.exit(1)

    playlist_url = sys.argv[1]
    DB_FILE = "dap_library.db"

    try:
        # Use a 'with' block for clean DB connection handling
        with DatabaseManager(DB_FILE) as db:
            client = SpotifyClient(db)
            client.process_playlist(playlist_url)
    except Exception as e:
        print(f"\nAn overall error occurred: {e}")
