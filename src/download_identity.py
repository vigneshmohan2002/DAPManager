"""Stable identities for idempotent download-queue targets."""

from __future__ import annotations

import re


ALBUM_QUERY_PREFIX = "::ALBUM::"
_SPACE = re.compile(r"\s+")


def normalize_download_query(value: object) -> str:
    """Return a conservative, case-insensitive queue-query identity."""
    text = str(value or "").strip()
    if text.startswith(ALBUM_QUERY_PREFIX):
        text = text[len(ALBUM_QUERY_PREFIX):].strip()
    return _SPACE.sub(" ", text).casefold()


def download_target_key(
    search_query: object,
    playlist_id: object,
    mbid_guess: object,
) -> str:
    """Build the canonical active-work key without guessing release editions."""
    query = str(search_query or "").strip()
    playlist = str(playlist_id or "").strip()
    mbid = str(mbid_guess or "").strip().lower()
    if mbid:
        kind = (
            "release"
            if playlist == "SATELLITE_ALBUM"
            or query.startswith(ALBUM_QUERY_PREFIX)
            else "recording"
        )
        return f"{kind}:{mbid}"
    normalized = normalize_download_query(query)
    return f"query:{normalized}" if normalized else ""
