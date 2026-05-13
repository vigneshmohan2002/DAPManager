"""
Thin LRCLIB client for fetching synced (and plain) lyrics.

LRCLIB (https://lrclib.net) is a free, no-auth lyrics database with a
generous rate limit — built specifically to be used by music apps. We
hit `/api/get` with the canonical track + artist + optional album +
duration; on a hit it returns both the plain-text lyrics and a synced
LRC string. The 404 path is normal and the caller treats it as a
"no lyrics available" cached miss rather than an error.

This module owns the HTTP call only — caching to the `lyrics` table
and freshness policy live in ``db_manager`` so other consumers (a
satellite that already has a cached entry from the master sync) don't
need to re-fetch.
"""

import logging
import urllib.parse
import urllib.request
from typing import Optional


logger = logging.getLogger(__name__)

_BASE_URL = "https://lrclib.net/api/get"
# LRCLIB's docs ask requesters to identify themselves. The same value
# could come from config, but a static identifier is fine for an OSS
# app that doesn't need per-install tracking.
_USER_AGENT = "DAPManager/0.1.0 (https://github.com/vigneshmohan2002/DAPManager)"
_TIMEOUT_SEC = 8.0


def fetch_lyrics(
    track_name: str,
    artist_name: str,
    album_name: Optional[str] = None,
    duration_seconds: Optional[float] = None,
) -> Optional[dict]:
    """Return ``{"lrc": str, "synced": bool}`` on a hit, ``None`` on a miss.

    A miss means LRCLIB doesn't have lyrics for this exact identification.
    Callers cache the miss too (an empty-row marker in the lyrics table)
    so we don't hammer the same lookup on every reopen of the pane.

    Network errors raise — the caller decides whether to surface or
    swallow. Distinguishing "no lyrics" (None) from "couldn't ask LRCLIB"
    (exception) matters: the former is permanent and worth caching, the
    latter is transient and a retry on the next request is fine.
    """
    track = (track_name or "").strip()
    artist = (artist_name or "").strip()
    if not track or not artist:
        return None

    params = {"track_name": track, "artist_name": artist}
    if album_name and album_name.strip():
        params["album_name"] = album_name.strip()
    if duration_seconds and duration_seconds > 0:
        # LRCLIB matches on integer seconds.
        params["duration"] = str(int(round(duration_seconds)))

    url = f"{_BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                logger.warning(
                    "lrclib unexpected status %s for %r — %r",
                    resp.status, track, artist,
                )
                return None
            import json
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # Network-level — propagate so the caller can decide; don't
        # cache as a permanent miss.
        raise RuntimeError(f"lrclib request failed: {e}") from e

    synced = (payload.get("syncedLyrics") or "").strip()
    plain = (payload.get("plainLyrics") or "").strip()
    if synced:
        return {"lrc": synced, "synced": True}
    if plain:
        return {"lrc": plain, "synced": False}
    return None
