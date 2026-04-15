"""
Shared MusicBrainz client.

MusicBrainz's anonymous API is rate-limited to 1 request per second per IP,
and requires a real user-agent (with a contact URL or email) — bogus contacts
get blocked. This module owns both concerns globally so concurrent callers
can't accidentally exceed the limit and so the user-agent is set exactly once
from config.
"""

import logging
import threading
import time

import musicbrainzngs

logger = logging.getLogger(__name__)

_APP_NAME = "DAPManager"
_APP_VERSION = "0.1.0"
_FALLBACK_CONTACT = "https://github.com/vigneshmohan2002/DAPManager"
_MIN_INTERVAL_SEC = 1.1

_useragent_lock = threading.Lock()
_useragent_set = False

_rate_lock = threading.Lock()
_last_call = 0.0


def configure(contact: str = "") -> None:
    """Set the MusicBrainz user-agent. Idempotent — only the first call wins."""
    global _useragent_set
    with _useragent_lock:
        if _useragent_set:
            return
        contact = contact.strip() if contact else _FALLBACK_CONTACT
        try:
            musicbrainzngs.set_useragent(_APP_NAME, _APP_VERSION, contact)
            _useragent_set = True
        except Exception as e:
            logger.warning(f"Failed to set MusicBrainz user-agent: {e}")


def _ensure_configured() -> None:
    if _useragent_set:
        return
    contact = ""
    try:
        from .config_manager import ConfigManager
        if ConfigManager._instance is not None:
            contact = ConfigManager._instance.contact_email
    except Exception:
        pass
    configure(contact)


def _wait_for_slot() -> None:
    global _last_call
    with _rate_lock:
        now = time.monotonic()
        delta = now - _last_call
        if delta < _MIN_INTERVAL_SEC:
            time.sleep(_MIN_INTERVAL_SEC - delta)
        _last_call = time.monotonic()


def get_release_by_id(release_mbid: str, includes=None):
    _ensure_configured()
    _wait_for_slot()
    return musicbrainzngs.get_release_by_id(release_mbid, includes=includes or [])


def get_recording_by_id(recording_mbid: str, includes=None):
    _ensure_configured()
    _wait_for_slot()
    return musicbrainzngs.get_recording_by_id(recording_mbid, includes=includes or [])


def reset_for_tests() -> None:
    """Reset module state. Tests only."""
    global _useragent_set, _last_call
    with _useragent_lock:
        _useragent_set = False
    with _rate_lock:
        _last_call = 0.0
