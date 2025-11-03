"""
Utility functions and decorators for DAP Manager.
"""

import time
import os
import logging
from functools import wraps
from typing import Callable, Type, Tuple, Any

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
