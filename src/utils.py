# ============================================================================
# FILE: utils.py
# ============================================================================
import time
import os
import logging
from functools import wraps
import acoustid  # Uses pyacoustid
from mediafile import MediaFile
from .config_manager import get_config

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, calls_per_second: float = 1.0):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0

    def wait_if_needed(self):
        now = time.time()
        if now - self.last_call < self.min_interval:
            time.sleep(self.min_interval - (now - self.last_call))
        self.last_call = time.time()

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self.wait_if_needed()
            return func(*args, **kwargs)

        return wrapper


musicbrainz_limiter = RateLimiter(1.0)


class EnvironmentManager:
    @staticmethod
    def validate_environment():
        return os.environ.get("SPOTIPY_CLIENT_ID") and os.environ.get(
            "SPOTIPY_CLIENT_SECRET"
        )


def get_mbid_from_tags(file_path: str):
    try:
        f = MediaFile(file_path)
        return f.mb_trackid
    except:
        return None


def write_mbid_to_file(file_path: str, mbid: str):
    try:
        f = MediaFile(file_path)
        f.mb_trackid = mbid
        f.save()
        return True
    except Exception as e:
        logger.error(f"Tag write failed: {e}")
        return False


def find_mbid_by_fingerprint(file_path: str):
    try:
        config = get_config()
        api_key = config.get("acoustid_api_key")
        if not api_key:
            logger.warning("AcoustID API key not found in config")
            return None

        for score, rid, title, artist in acoustid.match(api_key, file_path):
            if score > 0.4:
                logger.info(f"Match: {title} ({rid})")
                write_mbid_to_file(file_path, rid)
                return rid
    except Exception as e:
        logger.error(f"AcoustID error: {e}")
    return None
