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
from typing import Optional, Protocol

from .db_manager import DatabaseManager, Track
from .library_scanner import LibraryScanner
from .utils import write_mbid_to_file
from .audio_quality import library_path_for_track

logger = logging.getLogger(__name__)


class Scanner(Protocol):
    """Public scanner boundary used by the ingest pipeline."""

    def process_file(self, file_path: str) -> str:
        ...


def file_has_embedded_mbid(path: str) -> bool:
    """Return whether ``path`` carries a MusicBrainz recording identifier."""
    try:
        from mediafile import MediaFile, UnreadableFileError

        try:
            return bool(MediaFile(path).mb_trackid)
        except (UnreadableFileError, OSError):
            return False
    except Exception:
        return False


def _file_has_embedded_mbid(path: str) -> bool:
    """Compatibility alias retained for existing private imports."""
    return file_has_embedded_mbid(path)


def _normalized_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")


def _scan_file(scanner: Scanner, path: str) -> str:
    """Use the public scanner API, with a legacy fallback for test doubles."""
    process_file = getattr(scanner, "process_file", None)
    if callable(process_file):
        return process_file(path)
    return scanner._process_file(path)  # type: ignore[attr-defined]


def _fallback_track(
    src_path: str,
    *,
    mbid_guess: Optional[str],
    artist: Optional[str],
    title: Optional[str],
    album: Optional[str],
    track_number: Optional[int],
) -> Track:
    """Build the same identity used when scanner tags produce no row."""
    return Track(
        mbid=mbid_guess or "",
        title=title or "Unknown Title",
        artist=artist or "Unknown Artist",
        album=album or "Unknown Album",
        local_path=src_path,
        track_number=track_number or 0,
    )


def _move_to_library(src_path: str, dest_path: str) -> None:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if os.path.abspath(dest_path) == os.path.abspath(src_path):
        return
    if os.path.exists(dest_path):
        logger.info("ingest: overwriting existing %s", dest_path)
        os.remove(dest_path)
    shutil.move(src_path, dest_path)


def _ingest_audio_file(
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
    prefer_scanned_identity: bool,
) -> str:
    # Make sure the file carries our MBID so the scanner keys it correctly.
    if mbid_guess and not file_has_embedded_mbid(src_path):
        try:
            write_mbid_to_file(src_path, mbid_guess)
        except Exception as e:
            logger.warning("ingest: could not write MBID to %s: %s", src_path, e)

    norm_src = _normalized_path(src_path)
    track = db.get_track_by_path(norm_src)
    if track is None:
        # Scan tags → upserts a track row with local_path = src_path.  A
        # downloader may already have scanned the temporary file; avoid doing
        # that work twice when its row is present.
        try:
            _scan_file(scanner, src_path)
        except Exception as e:
            logger.warning("ingest: scan failed for %s: %s", src_path, e)
        track = db.get_track_by_path(norm_src)

    supplied_identity = _fallback_track(
        norm_src,
        mbid_guess=mbid_guess,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
    )
    if track is None:
        # Scanner produced no row — fall back to the contribution's identity.
        track = supplied_identity
        if track.mbid:
            db.add_or_update_track(track)

    path_identity = track if prefer_scanned_identity else supplied_identity
    dest_path = library_path_for_track(music_library_dir, path_identity)
    _move_to_library(src_path, dest_path)

    mbid = track.mbid or mbid_guess
    if mbid:
        db.update_track_local_path(mbid, dest_path)
    return dest_path


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
    return _ingest_audio_file(
        db,
        scanner,
        music_library_dir,
        src_path,
        mbid_guess=mbid_guess,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        prefer_scanned_identity=True,
    )


def ingest_downloaded_audio_file(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    artist: str,
    title: str,
    album: str,
    track_number: int,
) -> str:
    """Ingest a downloader file while preserving its legacy sort identity.

    The downloader historically chose the destination from mutagen/auto-tag
    metadata even after the scanner had populated the database.  Keeping that
    choice here lets both download and contribution paths share one mutation
    pipeline without changing existing folder names.
    """
    return _ingest_audio_file(
        db,
        scanner,
        music_library_dir,
        src_path,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        prefer_scanned_identity=False,
    )
