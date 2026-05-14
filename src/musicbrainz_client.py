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


def get_recordings_by_isrc(isrc: str):
    _ensure_configured()
    _wait_for_slot()
    return musicbrainzngs.get_recordings_by_isrc(isrc)


def search_artists(query: str, limit: int = 1):
    """Search MB for an artist by free-text name. Returns the raw
    musicbrainzngs response — caller picks the right hit. Rate-limited
    via the same shared slot the rest of the module uses."""
    _ensure_configured()
    _wait_for_slot()
    return musicbrainzngs.search_artists(query=query, limit=int(limit))


def get_artist_tags(artist_mbid: str):
    """Fetch an MB artist's tags. Returns the dict's ``artist`` payload,
    which carries a ``tag-list`` of ``{name, count}`` entries; callers
    map these into the local artist_tags schema. Two-step (search then
    fetch) is unavoidable because MB's search endpoint doesn't include
    tags inline."""
    _ensure_configured()
    _wait_for_slot()
    return musicbrainzngs.get_artist_by_id(artist_mbid, includes=["tags"])


def reset_for_tests() -> None:
    """Reset module state. Tests only."""
    global _useragent_set, _last_call
    with _useragent_lock:
        _useragent_set = False
    with _rate_lock:
        _last_call = 0.0
