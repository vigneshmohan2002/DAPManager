"""
Picard-style tag identify/apply service.

Two operations, deliberately separated so callers can show a review step
between them (Picard's killer UX):

- ``identify_file``: fingerprint the file with AcoustID, look up the best
  recording on MusicBrainz, return a candidate dict (no writes).
- ``write_tags``: apply a metadata dict to the file across FLAC, MP3,
  Ogg Vorbis, and MP4 containers. Caller decides what to write.

Confidence tiers mirror Picard's colours:
- ``"green"``  ≥ 0.90 — safe to auto-apply.
- ``"yellow"`` 0.50–0.90 — require manual confirmation.
- ``"red"``    < 0.50 — do not apply without strong user override.
"""

import logging
import os
from typing import Optional

import acoustid
import musicbrainzngs
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from . import musicbrainz_client as mb

logger = logging.getLogger(__name__)

CONFIDENCE_GREEN = 0.90
CONFIDENCE_YELLOW = 0.50


def _tier(score: float) -> str:
    if score >= CONFIDENCE_GREEN:
        return "green"
    if score >= CONFIDENCE_YELLOW:
        return "yellow"
    return "red"


def read_current_tags(filepath: str) -> dict:
    """Return the file's existing tags as a flat dict (best-effort).

    Used to show a before/after diff in the review step. Missing or
    unreadable files return an empty dict instead of raising.
    """
    if not filepath or not os.path.exists(filepath):
        return {}
    try:
        audio = mutagen.File(filepath, easy=True)
    except Exception as e:
        logger.warning(f"read_current_tags: mutagen failed on {filepath}: {e}")
        return {}
    if audio is None:
        return {}

    def _first(key: str) -> str:
        val = audio.get(key)
        if not val:
            return ""
        return val[0] if isinstance(val, list) else str(val)

    return {
        "title": _first("title"),
        "artist": _first("artist"),
        "album": _first("album"),
        "album_artist": _first("albumartist"),
        "date": _first("date"),
        "track_number": _first("tracknumber"),
        "disc_number": _first("discnumber"),
        "mbid": _first("musicbrainz_trackid") or _first("musicbrainz_recordingid"),
        "release_mbid": _first("musicbrainz_albumid"),
    }


def identify_file(
    filepath: str, api_key: str, contact: str = ""
) -> Optional[dict]:
    """Fingerprint + MusicBrainz lookup; return a candidate without writing.

    Returns ``None`` when AcoustID returns no results, when MusicBrainz
    has no release context, or on any API error (callers treat missing
    candidate as "unknown"). Returns a dict shaped like::

        {
            "score": 0.97,
            "tier": "green",
            "meta": {artist, album, title, mbid, release_mbid, ...},
            "current": {...existing file tags...},
        }

    No API key => returns None with a warning logged so the UI can guide
    the user to the Settings card.
    """
    if not api_key:
        logger.warning("identify_file: no AcoustID API key; skipping lookup")
        return None
    if not os.path.exists(filepath):
        logger.warning(f"identify_file: file not found: {filepath}")
        return None

    mb.configure(contact)

    try:
        duration, fingerprint = acoustid.fingerprint_file(filepath)
    except Exception as e:
        logger.warning(f"identify_file: fingerprint failed on {filepath}: {e}")
        return None

    try:
        results = acoustid.lookup(
            api_key,
            fingerprint,
            duration,
            meta=["recordings", "releases", "tracks", "usermeta", "releasegroups"],
        )
    except acoustid.WebServiceError as e:
        logger.error(f"identify_file: AcoustID error: {e}")
        return None

    items = (results or {}).get("results") or []
    if not items:
        return None

    best = max(items, key=lambda x: x.get("score", 0))
    score = float(best.get("score", 0.0))

    recordings = best.get("recordings") or []
    if not recordings:
        return None
    recording_id = recordings[0].get("id")
    releases = recordings[0].get("releases") or []
    if not releases:
        return None
    release_id = releases[0].get("id")
    if not recording_id or not release_id:
        return None

    try:
        mb_data = mb.get_release_by_id(
            release_id, includes=["artists", "recordings", "release-groups"]
        )
    except musicbrainzngs.WebServiceError as e:
        logger.error(f"identify_file: MusicBrainz error: {e}")
        return None

    release_info = mb_data.get("release") or {}
    track_info = None
    disc_num = None
    media_tracks = []
    for media in release_info.get("medium-list") or []:
        for t in media.get("track-list") or []:
            if (t.get("recording") or {}).get("id") == recording_id:
                track_info = t
                disc_num = media.get("position")
                media_tracks = media.get("track-list") or []
                break
        if track_info:
            break
    if not track_info:
        return None

    artist_credit = release_info.get("artist-credit") or [{}]
    artist = (artist_credit[0].get("artist") or {}).get("name", "")
    album = release_info.get("title", "")
    title = (track_info.get("recording") or {}).get("title", "")

    meta = {
        "artist": artist,
        "album_artist": artist,
        "album": album,
        "title": title,
        "date": release_info.get("date", ""),
        "track_number": track_info.get("number", ""),
        "disc_number": disc_num or "",
        "mbid": recording_id,
        "release_mbid": release_id,
    }

    return {
        "score": score,
        "tier": _tier(score),
        "meta": meta,
        "current": read_current_tags(filepath),
    }


def write_tags(filepath: str, meta: dict) -> str:
    """Write ``meta`` to ``filepath`` using the right container handler.

    Returns the container name ("flac"|"mp3"|"ogg"|"m4a") on success.
    Raises ValueError for unsupported extensions so the caller can
    surface the "we can't tag this format" case instead of silently
    no-oping (the old AutoTagger behaviour for non-FLAC files).
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".flac":
        _write_flac(filepath, meta)
        return "flac"
    if ext == ".mp3":
        _write_mp3(filepath, meta)
        return "mp3"
    if ext == ".ogg":
        _write_ogg(filepath, meta)
        return "ogg"
    if ext in (".m4a", ".mp4"):
        _write_m4a(filepath, meta)
        return "m4a"

    raise ValueError(f"unsupported file format for tagging: {ext}")


def _s(v) -> str:
    return "" if v is None else str(v)


def _write_flac(filepath: str, meta: dict) -> None:
    audio = FLAC(filepath)
    audio["title"] = _s(meta.get("title"))
    audio["artist"] = _s(meta.get("artist"))
    audio["album"] = _s(meta.get("album"))
    audio["albumartist"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["date"] = _s(meta.get("date"))
    audio["tracknumber"] = _s(meta.get("track_number"))
    audio["discnumber"] = _s(meta.get("disc_number"))
    if meta.get("mbid"):
        audio["musicbrainz_trackid"] = _s(meta["mbid"])
        audio["musicbrainz_recordingid"] = _s(meta["mbid"])
    if meta.get("release_mbid"):
        audio["musicbrainz_albumid"] = _s(meta["release_mbid"])
    audio.save()


def _write_mp3(filepath: str, meta: dict) -> None:
    try:
        audio = EasyID3(filepath)
    except mutagen.id3.ID3NoHeaderError:
        audio = mutagen.File(filepath, easy=True)
        if audio is None or audio.tags is None:
            # Create an ID3 header on a tagless MP3
            from mutagen.id3 import ID3
            ID3().save(filepath)
            audio = EasyID3(filepath)

    audio["title"] = _s(meta.get("title"))
    audio["artist"] = _s(meta.get("artist"))
    audio["album"] = _s(meta.get("album"))
    audio["albumartist"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["date"] = _s(meta.get("date"))
    audio["tracknumber"] = _s(meta.get("track_number"))
    audio["discnumber"] = _s(meta.get("disc_number"))
    # EasyID3 exposes these as extendable keys by default
    if meta.get("mbid"):
        audio["musicbrainz_trackid"] = _s(meta["mbid"])
    if meta.get("release_mbid"):
        audio["musicbrainz_albumid"] = _s(meta["release_mbid"])
    audio.save()


def _write_ogg(filepath: str, meta: dict) -> None:
    audio = OggVorbis(filepath)
    audio["title"] = _s(meta.get("title"))
    audio["artist"] = _s(meta.get("artist"))
    audio["album"] = _s(meta.get("album"))
    audio["albumartist"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["date"] = _s(meta.get("date"))
    audio["tracknumber"] = _s(meta.get("track_number"))
    audio["discnumber"] = _s(meta.get("disc_number"))
    if meta.get("mbid"):
        audio["musicbrainz_trackid"] = _s(meta["mbid"])
    if meta.get("release_mbid"):
        audio["musicbrainz_albumid"] = _s(meta["release_mbid"])
    audio.save()


def _write_m4a(filepath: str, meta: dict) -> None:
    audio = MP4(filepath)
    # MP4 uses atom keys
    audio["\xa9nam"] = _s(meta.get("title"))
    audio["\xa9ART"] = _s(meta.get("artist"))
    audio["\xa9alb"] = _s(meta.get("album"))
    audio["aART"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["\xa9day"] = _s(meta.get("date"))
    tn = _s(meta.get("track_number"))
    if tn.isdigit():
        audio["trkn"] = [(int(tn), 0)]
    dn = _s(meta.get("disc_number"))
    if dn.isdigit():
        audio["disk"] = [(int(dn), 0)]
    if meta.get("mbid"):
        audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [
            _s(meta["mbid"]).encode("utf-8")
        ]
    if meta.get("release_mbid"):
        audio["----:com.apple.iTunes:MusicBrainz Album Id"] = [
            _s(meta["release_mbid"]).encode("utf-8")
        ]
    audio.save()
