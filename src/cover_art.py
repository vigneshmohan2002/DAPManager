"""
Extract embedded cover art from audio files via mutagen.

Returns ``(bytes, mime_type)`` when a picture is present, else ``None``.
Handles MP3 (APIC), FLAC/Vorbis (pictures + metadata_block_picture),
and MP4/M4A (covr). Any decoding error yields ``None`` — callers treat
that as "no cover, serve a placeholder".
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def extract_cover(path: str) -> Optional[Tuple[bytes, str]]:
    if not path:
        return None
    try:
        from mutagen import File as MutagenFile
    except ImportError:  # pragma: no cover
        logger.warning("mutagen not installed; can't extract cover art")
        return None

    try:
        audio = MutagenFile(path)
    except Exception as e:
        logger.debug("cover_art: mutagen failed to open %s: %s", path, e)
        return None

    if audio is None:
        return None

    # FLAC: audio.pictures is a list of Picture objects.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        pic = pictures[0]
        return bytes(pic.data), pic.mime or "image/jpeg"

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    # MP3 (ID3): APIC:<desc> frame.
    try:
        for key in list(tags.keys()):
            if str(key).startswith("APIC"):
                apic = tags[key]
                data = getattr(apic, "data", None)
                mime = getattr(apic, "mime", None) or "image/jpeg"
                if data:
                    return bytes(data), mime
    except Exception:
        pass

    # MP4 / M4A: "covr" atom, list of MP4Cover.
    try:
        covr = tags.get("covr") if hasattr(tags, "get") else None
        if covr:
            pic = covr[0]
            # MP4Cover.imageformat: FORMAT_JPEG=13, FORMAT_PNG=14.
            fmt = getattr(pic, "imageformat", None)
            mime = "image/png" if fmt == 14 else "image/jpeg"
            return bytes(pic), mime
    except Exception:
        pass

    return None
