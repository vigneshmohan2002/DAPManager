"""
Utility functions and decorators for DAP Manager.
"""

import time
import os
import logging
from functools import wraps
from typing import Callable, Type, Tuple, Any
import mutagen
from typing import Optional
from picard import webservice, acoustid

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter for API calls using token bucket algorithm."""

    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0

    def wait_if_needed(self):
        """Wait if necessary to respect the rate limit."""
        current_time = time.time()
        time_since_last_call = current_time - self.last_call

        if time_since_last_call < self.min_interval:
            sleep_time = self.min_interval - time_since_last_call
            logger.debug(f"Rate limiting: sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)

        self.last_call = time.time()

    def __call__(self, func: Callable) -> Callable:
        """Decorator to rate-limit a function."""

        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            self.wait_if_needed()
            return func(*args, **kwargs)

        return wrapper


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator that retries a function with exponential backoff."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {e}"
                        )
                        raise

                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

        return wrapper

    return decorator


class EnvironmentManager:
    """Handles environment variable loading with validation."""

    @staticmethod
    def get_spotify_credentials() -> tuple:
        """Retrieves Spotify credentials from environment variables."""
        client_id = os.environ.get("SPOTIPY_CLIENT_ID")
        client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")

        if not client_id or not client_secret:
            logger.error("Spotify credentials not found in environment variables")
            print("=" * 80)
            print("ERROR: Spotify credentials not found.")
            print("\nPlease set: SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET")
            print("\nGet credentials at: https://developer.spotify.com/dashboard")
            print("\nSet environment variables:")
            print("  Windows (PowerShell):  $env:SPOTIPY_CLIENT_ID='your_id'")
            print("  Windows (CMD):         set SPOTIPY_CLIENT_ID=your_id")
            print("  macOS/Linux:           export SPOTIPY_CLIENT_ID='your_id'")
            print("=" * 80)
            return None, None

        return client_id, client_secret

    @staticmethod
    def validate_environment() -> bool:
        """Validates that all required environment variables are set."""
        client_id, client_secret = EnvironmentManager.get_spotify_credentials()
        return client_id is not None and client_secret is not None


# MusicBrainz rate limiter (1 request per second per API terms)
musicbrainz_limiter = RateLimiter(calls_per_second=1.0)


def get_mbid_from_tags(file_path: str) -> Optional[str]:
    """
    Reads the MusicBrainz Track ID from a file's metadata.
    Handles both ID3 (MP3) and Vorbis (FLAC/Ogg) tags.
    """
    try:
        file_info = mutagen.File(file_path, easy=False)
        if not file_info:
            return None

        if file_info.tags and "musicbrainztrackid" in file_info.tags:
            return file_info.tags["musicbrainztrackid"][0].strip().lower()

        # Check for ID3 tags (MP3, M4A)
        if "TXXX:MusicBrainz Track Id" in file_info.tags:
            return str(file_info.tags["TXXX:MusicBrainz Track Id"]).strip().lower()

        # Fallback for 'easy' tags
        easy_tags = mutagen.File(file_path, easy=True)
        if easy_tags and "musicbrainz_trackid" in easy_tags:
            return easy_tags["musicbrainz_trackid"][0].strip().lower()

    except Exception as e:
        logger.debug(f"Error reading tags from {os.path.basename(file_path)}: {e}")

    return None


import os
import logging
from typing import Optional, List, Dict
import os
import subprocess
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"


def run_fpcalc(file_path: str) -> Optional[tuple[str, float]]:
    """Run fpcalc to generate a fingerprint and duration for an audio file."""
    try:
        result = subprocess.run(
            ["fpcalc", "-json", file_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
        )
        import json

        data = json.loads(result.stdout)
        fingerprint = data.get("fingerprint")
        duration = data.get("duration")
        if not fingerprint or not duration:
            logger.error("fpcalc output missing fingerprint or duration.")
            return None
        return fingerprint, duration
    except subprocess.CalledProcessError as e:
        logger.error(f"fpcalc failed: {e.stderr.strip()}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error running fpcalc: {e}")
        return None


def write_mbid_to_file(file_path: str, mbid: str) -> bool:
    """
    Writes the MusicBrainz Track ID to a file.
    :param file_path: Path to the audio file.
    :param mbid: The MusicBrainz Track ID to write.
    :return: True on success, False on failure.
    """
    if not os.path.exists(file_path):
        print(f"Error: File not found at {file_path}")
        return False

    try:
        # Load the file using mutagen
        audio = mutagen.File(file_path, easy=True)

        if audio is None:
            print(f"Error: Could not load file {file_path}. Unsupported format?")
            return False

        # Set the MusicBrainz Track ID tag
        # 'musicbrainz_trackid' is a common key in EasyID3/EasyMP4 etc.
        audio["musicbrainz_trackid"] = mbid

        # Save the changes
        audio.save()
        return True

    except mutagen.MutagenError as e:
        print(f"Error processing file {file_path}: {e}")
        return False
    except IOError as e:
        print(f"Error saving file {file_path}: {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred for {file_path}: {e}")
        return False


def find_mbid_by_fingerprint(
    file_path: str, acoustid_api_key="t1BbzA89Bw"
) -> Optional[str]:
    """Find the MusicBrainz MBID for an audio file using AcoustID lookup."""
    fp_result = run_fpcalc(file_path)
    if not fp_result:
        return None

    fingerprint, duration = fp_result

    if not acoustid_api_key:
        logger.error("Missing AcoustID API key.")
        return None

    data = {
        "client": acoustid_api_key.strip(),  # strip in case of newline/spaces
        "meta": "recordings",
        "duration": int(duration),
        "fingerprint": fingerprint,
    }

    try:
        # Use multipart/form-data instead of application/x-www-form-urlencoded
        resp = requests.get(ACOUSTID_API_URL, params=data, timeout=20)
        data = resp.json()
        print(data)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"AcoustID API request failed for {file_path}: {e}")
        return None
    except ValueError:
        logger.error(
            f"Failed to parse JSON response for {file_path}: {resp.text[:200]}"
        )
        return None

    if data.get("status") != "ok":
        logger.error(f"AcoustID lookup error for {file_path}: {data}")
        return None

    results = data.get("results", [])
    if not results:
        logger.warning(f"No AcoustID results for {file_path}")
        return None

    best = max(results, key=lambda r: r.get("score", 0))
    if best.get("score", 0) < 0.5:
        logger.warning(f"Low match score ({best.get('score', 0):.2f}) for {file_path}")
        return None

    recordings = best.get("recordings", [])
    if not recordings:
        logger.warning(f"No recordings for {file_path}")
        return None

    mbid = recordings[0].get("id")
    if mbid:
        logger.info(f"Found MBID for {os.path.basename(file_path)}: {mbid}")
        write_mbid_to_file(file_path=file_path, mbid=mbid.lower().strip())
        return mbid.lower()

    logger.warning(f"No MBID in AcoustID result for {file_path}")
    return None
