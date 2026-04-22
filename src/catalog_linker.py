"""Bind on-disk audio files to catalog rows that have local_path IS NULL.

After a satellite pulls the master catalog it may hold rows whose
files actually already exist on disk (e.g. the user had a pre-DAPManager
library). Those rows stay filtered out of the default "what can I play"
views until something connects the two. This module is that something.

Priority order for matching:
    1. Embedded MBID (musicbrainz_trackid / musicbrainz_recordingid).
    2. ISRC.
    3. Case-insensitive (artist, title); disambiguated by album when
       multiple candidates remain.

Policy:
    - Only link rows where local_path IS NULL. Never overwrite an
      existing binding.
    - If a file path is already in tracks.local_path for some mbid,
      leave it alone (existing binding is authoritative).
    - Ambiguous matches (>1 unlinked candidate) are skipped, not
      guessed. The user can resolve them via identify/tag or by
      disambiguating tags on disk.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

import mutagen
from mutagen.easyid3 import EasyID3

logger = logging.getLogger(__name__)

# ISRC lives in the TSRC frame for ID3; register it under easy-mode so
# mutagen.File(..., easy=True) surfaces it alongside FLAC/OGG/MP4.
try:
    EasyID3.RegisterTextKey("isrc", "TSRC")
except ValueError:
    pass

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav"}


def _iter_audio_files(root: str):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                yield os.path.join(dirpath, name)


def _read_link_tags(path: str) -> Optional[dict]:
    """Return the subset of tags the linker actually uses, or None on error."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception as e:
        logger.warning("catalog_linker: mutagen failed on %s: %s", path, e)
        return None
    if audio is None:
        return None

    def _first(key: str) -> str:
        val = audio.get(key)
        if not val:
            return ""
        return val[0] if isinstance(val, list) else str(val)

    return {
        "artist": _first("artist").strip(),
        "title": _first("title").strip(),
        "album": _first("album").strip(),
        "mbid": (_first("musicbrainz_trackid")
                 or _first("musicbrainz_recordingid")).strip(),
        "isrc": _first("isrc").strip(),
    }


def _match_one(db, tags: dict) -> tuple[Optional[str], str]:
    """Resolve tags to an mbid by walking the priority ladder.

    Returns (mbid_or_None, outcome) where outcome is one of:
      ``linked-mbid``, ``linked-isrc``, ``linked-name``,
      ``linked-name-album``, ``no-match``, ``ambiguous``.
    """
    # 1. Embedded MBID wins. Only claim it if the row is still unlinked
    #    and live; otherwise we'd stomp an existing binding.
    if tags["mbid"]:
        row = db.get_track_by_mbid(tags["mbid"])
        if row is not None and row.local_path is None:
            # deleted_at isn't on the Track dataclass; re-fetch via an
            # unlinked-filter lookup to be safe. A direct by_mbid that
            # hits a soft-deleted row is rare but possible after a
            # catalog-delta carrying a deletion.
            if _mbid_is_eligible(db, tags["mbid"]):
                return tags["mbid"], "linked-mbid"

    # 2. ISRC — only act on unambiguous matches.
    if tags["isrc"]:
        isrc_hits = db.find_unlinked_tracks_by_isrc(tags["isrc"])
        if len(isrc_hits) == 1:
            return isrc_hits[0], "linked-isrc"
        if len(isrc_hits) > 1:
            return None, "ambiguous"

    # 3. (artist, title), with album disambiguation when needed.
    if tags["artist"] and tags["title"]:
        name_hits = db.find_unlinked_tracks_by_artist_title(
            tags["artist"], tags["title"],
        )
        if len(name_hits) == 1:
            return name_hits[0], "linked-name"
        if len(name_hits) > 1 and tags["album"]:
            album_hits = db.find_unlinked_tracks_by_artist_title_album(
                tags["artist"], tags["title"], tags["album"],
            )
            if len(album_hits) == 1:
                return album_hits[0], "linked-name-album"
            return None, "ambiguous"
        if len(name_hits) > 1:
            return None, "ambiguous"

    return None, "no-match"


def _mbid_is_eligible(db, mbid: str) -> bool:
    """Confirm an MBID-matched row is both unlinked and not soft-deleted.

    The cheaper path would be to read deleted_at off the Track dataclass,
    but it isn't exposed there — the DB-side helper already has the
    correct filter, so use it."""
    cursor = db.conn.cursor()
    cursor.execute(
        "SELECT 1 FROM tracks "
        "WHERE mbid = ? AND local_path IS NULL AND deleted_at IS NULL",
        (mbid,),
    )
    hit = cursor.fetchone() is not None
    cursor.close()
    return hit


def main_run_catalog_linker(
    db,
    config,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """Walk ``config.music_library_path`` and link unlinked catalog rows.

    Returns a summary dict::

        {scanned, linked, ambiguous, skipped, errors,
         linked_by_mbid, linked_by_isrc, linked_by_name, message?}
    """
    root = getattr(config, "music_library", None) or getattr(
        config, "music_library_path", None
    ) or ""
    if not root or not os.path.isdir(root):
        msg = f"music_library_path not set or not a directory: {root!r}"
        logger.warning("catalog_linker: %s", msg)
        return {
            "scanned": 0, "linked": 0, "ambiguous": 0, "skipped": 0, "errors": 0,
            "linked_by_mbid": 0, "linked_by_isrc": 0, "linked_by_name": 0,
            "message": msg,
        }

    def report(msg: str):
        logger.info("catalog_linker: %s", msg)
        if progress_callback:
            progress_callback(msg)

    scanned = linked = ambiguous = skipped = errors = 0
    linked_by_mbid = linked_by_isrc = linked_by_name = 0

    for path in _iter_audio_files(root):
        scanned += 1
        if scanned % 100 == 0:
            report(
                f"Scanned {scanned} files, linked {linked} so far "
                f"(ambiguous {ambiguous}, skipped {skipped})."
            )

        # Path already in tracks.local_path? Someone else owns this file;
        # don't attempt to re-bind it to a different row.
        if db.get_track_by_path(path) is not None:
            skipped += 1
            continue

        tags = _read_link_tags(path)
        if tags is None:
            errors += 1
            continue

        mbid, outcome = _match_one(db, tags)
        if mbid is None:
            if outcome == "ambiguous":
                ambiguous += 1
            else:
                skipped += 1
            continue

        db.update_track_local_path(mbid, path)
        linked += 1
        if outcome == "linked-mbid":
            linked_by_mbid += 1
        elif outcome == "linked-isrc":
            linked_by_isrc += 1
        else:  # linked-name, linked-name-album
            linked_by_name += 1

    report(
        f"Done. Scanned {scanned}, linked {linked} "
        f"(mbid {linked_by_mbid}, isrc {linked_by_isrc}, name {linked_by_name}), "
        f"ambiguous {ambiguous}, skipped {skipped}, errors {errors}."
    )
    return {
        "scanned": scanned, "linked": linked, "ambiguous": ambiguous,
        "skipped": skipped, "errors": errors,
        "linked_by_mbid": linked_by_mbid,
        "linked_by_isrc": linked_by_isrc,
        "linked_by_name": linked_by_name,
    }
