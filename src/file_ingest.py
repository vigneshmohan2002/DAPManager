"""
Ingest a single audio file into the master's library — the tail end of both
a normal download and a satellite *upload* contribution.

Mirrors what ``downloader._process_success`` does for one file: make sure the
file carries an MBID, scan its tags into the catalog, then move it into the
clean ``Artist/Album/NN Title.flac`` layout and point the track row at its
new home.
"""

import logging
import os
import shutil
from typing import Optional

from .db_manager import DatabaseManager, Track
from .library_scanner import LibraryScanner
from .utils import write_mbid_to_file
from .audio_quality import library_path_for_track

logger = logging.getLogger(__name__)


def _file_has_embedded_mbid(path: str) -> bool:
    try:
        from mediafile import MediaFile, UnreadableFileError

        try:
            return bool(MediaFile(path).mb_trackid)
        except (UnreadableFileError, OSError):
            return False
    except Exception:
        return False


def ingest_audio_file(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    mbid_guess: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    album: Optional[str] = None,
    track_number: Optional[int] = None,
) -> str:
    """Move ``src_path`` into ``music_library_dir`` and link the track row.

    Returns the final library path. ``artist``/``title``/``album`` are used as
    a fallback identity when the file's tags don't yield a catalog row (e.g.
    the scanner couldn't fingerprint it).
    """
    # Make sure the file carries our MBID so the scanner keys it correctly.
    if mbid_guess and not _file_has_embedded_mbid(src_path):
        try:
            write_mbid_to_file(src_path, mbid_guess)
        except Exception as e:
            logger.warning("ingest: could not write MBID to %s: %s", src_path, e)

    # Scan tags → upserts a track row with local_path = src_path.
    try:
        scanner._process_file(src_path)
    except Exception as e:
        logger.warning("ingest: scan failed for %s: %s", src_path, e)

    norm_src = os.path.normpath(src_path).replace("\\", "/")
    track = db.get_track_by_path(norm_src)
    if track is None:
        # Scanner produced no row — fall back to the contribution's identity.
        track = Track(
            mbid=mbid_guess or "",
            title=title or "Unknown Title",
            artist=artist or "Unknown Artist",
            album=album or "Unknown Album",
            local_path=norm_src,
            track_number=track_number or 0,
        )
        if track.mbid:
            db.add_or_update_track(track)

    dest_path = library_path_for_track(music_library_dir, track)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.abspath(dest_path) != os.path.abspath(src_path):
        if os.path.exists(dest_path):
            logger.info("ingest: overwriting existing %s", dest_path)
            os.remove(dest_path)
        shutil.move(src_path, dest_path)

    mbid = track.mbid or mbid_guess
    if mbid:
        db.update_track_local_path(mbid, dest_path)
    return dest_path
