"""
Audio quality probing and comparison, shared between the master (deciding
whether a download it produced matches a satellite's copy) and satellites
(reporting the quality of a file they want to contribute).

Quality is compared as an ordered tuple — lossless beats lossy first, then
bit depth, then sample rate, then bitrate — so "same or better" is a plain
``>=`` on ``quality_tuple``.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Containers/codecs we treat as lossless. ``alac`` is the codec name mutagen
# may surface for lossless ``.m4a``; the extension alone can't distinguish
# ALAC from AAC, so callers that have only an ``.m4a`` extension get the
# lossy ranking unless mutagen reports otherwise (see ``read_quality``).
LOSSLESS_EXTS = frozenset({"flac", "wav", "aiff", "aif", "ape", "alac", "wv"})


def _ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower().lstrip(".")


def read_quality(path: str) -> Optional[dict]:
    """Probe an audio file and return a quality descriptor, or ``None`` if it
    can't be read.

    Keys: ``ext, bitrate, sample_rate, bits_per_sample, channels,
    length_ms, size_bytes, lossless``. Missing attributes default to 0 /
    False so the descriptor is always JSON-serialisable and comparable.
    """
    try:
        import mutagen

        audio = mutagen.File(path)
    except Exception as e:  # mutagen raises a variety of types on bad files
        logger.warning("audio_quality: mutagen failed on %s: %s", path, e)
        audio = None

    ext = _ext_of(path)
    try:
        size_bytes = os.path.getsize(path)
    except OSError:
        size_bytes = 0

    info = getattr(audio, "info", None)
    bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
    sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    bitrate = int(getattr(info, "bitrate", 0) or 0)
    channels = int(getattr(info, "channels", 0) or 0)
    length = float(getattr(info, "length", 0) or 0)

    # A non-zero bits_per_sample is mutagen's tell for a lossless stream
    # (FLAC/ALAC/WAV expose it; MP3/AAC/Vorbis don't), which lets us catch
    # lossless ``.m4a`` (ALAC) that the extension alone would misjudge.
    lossless = ext in LOSSLESS_EXTS or bits_per_sample > 0

    return {
        "ext": ext,
        "bitrate": bitrate,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "channels": channels,
        "length_ms": int(length * 1000),
        "size_bytes": size_bytes,
        "lossless": bool(lossless),
    }


def quality_tuple(q: Optional[dict]) -> tuple:
    """Return a comparable ``(lossless, bits_per_sample, sample_rate, bitrate)``
    tuple. A missing/empty descriptor sorts below everything.
    """
    if not q:
        return (0, 0, 0, 0)
    lossless = 1 if q.get("lossless") else 0
    return (
        lossless,
        int(q.get("bits_per_sample") or 0),
        int(q.get("sample_rate") or 0),
        int(q.get("bitrate") or 0),
    )


def meets_target(candidate: Optional[dict], target: Optional[dict]) -> bool:
    """True when ``candidate`` is the same or better quality than ``target``."""
    return quality_tuple(candidate) >= quality_tuple(target)


def library_path_for_track(music_library_dir: str, track) -> str:
    """Build a clean library path ``<lib>/Artist/Album/NN Title.flac`` for a
    ``Track``-like object (needs ``artist``, ``album``, ``title``,
    ``track_number``).

    Extracted from the downloader so the upload-ingest path foldering stays
    identical. Returns a forward-slash path.
    """
    import re

    album = getattr(track, "album", None) or "Unknown Album"
    safe_artist = (
        re.sub(r'[\\/*?:"<>|]', "_", track.artist)
        if getattr(track, "artist", None)
        else "Unknown Artist"
    )
    safe_album = re.sub(r'[\\/*?:"<>|]', "_", album)
    safe_title = (
        re.sub(r'[\\/*?:"<>|]', "_", track.title)
        if getattr(track, "title", None)
        else "Unknown Title"
    )

    prefix = ""
    if getattr(track, "track_number", None):
        try:
            prefix = f"{int(track.track_number):02d} "
        except (ValueError, TypeError):
            pass

    library_path = os.path.join(
        music_library_dir, safe_artist, safe_album, f"{prefix}{safe_title}.flac"
    )
    return library_path.replace("\\", "/")
