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

import hashlib
import logging
import math
import os
import shutil
import stat
import tempfile
import uuid
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import acoustid
import musicbrainzngs
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from . import musicbrainz_client as mb
from .contracts import ConfidenceTier, TagCandidate, TagMetadata

logger = logging.getLogger(__name__)

# File extensions whose tags we can read/write. Used by callers (e.g. the
# retag pass) to skip unsupported files gracefully instead of treating each
# one as an error on every run.
TAGGABLE_EXTENSIONS = {".flac", ".mp3", ".ogg", ".m4a", ".mp4"}

CONFIDENCE_GREEN = 0.90
CONFIDENCE_YELLOW = 0.50
ACOUSTID_AMBIGUITY_MARGIN = 0.05

_CANONICAL_FLAC_TAGS = frozenset({
    "title",
    "artist",
    "album",
    "albumartist",
    "date",
    "tracknumber",
    "tracktotal",
    "totaltracks",
    "discnumber",
    "disctotal",
    "totaldiscs",
    "musicbrainz_trackid",
    "musicbrainz_recordingid",
    "musicbrainz_albumid",
    "musicbrainz_releasetrackid",
})

_PICARD_METADATA_FIELDS = (
    "title",
    "artist",
    "album",
    "album_artist",
    "date",
    "track_number",
    "track_total",
    "disc_number",
    "disc_total",
    "mbid",
    "release_mbid",
    "release_track_mbid",
)


class TagSynchronizationRace(OSError):
    """The destination changed after its quality decision was captured."""


def _classify_confidence(score: float) -> ConfidenceTier:
    """Return the Picard-style confidence tier for an AcoustID score."""
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        return "red"
    if score >= CONFIDENCE_GREEN:
        return "green"
    if score >= CONFIDENCE_YELLOW:
        return "yellow"
    return "red"


def _tier(score: float) -> ConfidenceTier:
    """Compatibility wrapper for callers of the original tier helper."""
    return _classify_confidence(score)


def _select_best_acoustid_result(
    results: Sequence[Mapping[str, Any]],
) -> Optional[Mapping[str, Any]]:
    """Select the highest-scoring result, retaining first-on-tie behavior."""
    if not results:
        return None
    return max(results, key=lambda result: result.get("score", 0))


def _select_recording_identity(
    result: Mapping[str, Any],
) -> Optional[Tuple[str, str]]:
    """Return the first recording/release identity in an AcoustID result."""
    recordings = result.get("recordings") or []
    if not recordings:
        return None

    recording = recordings[0]
    recording_id = recording.get("id")
    releases = recording.get("releases") or []
    if not releases:
        return None

    release_id = releases[0].get("id")
    if not recording_id or not release_id:
        return None
    return recording_id, release_id


def _select_musicbrainz_track(
    release_info: Mapping[str, Any], recording_id: str
) -> Optional[Tuple[Mapping[str, Any], Any]]:
    """Find the first medium track matching a MusicBrainz recording ID."""
    for medium in release_info.get("medium-list") or []:
        for track in medium.get("track-list") or []:
            if (track.get("recording") or {}).get("id") == recording_id:
                return track, medium.get("position")
    return None


def _select_musicbrainz_track_context(
    release_info: Mapping[str, Any], recording_id: str
) -> Optional[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Return the matching release track together with its full medium."""
    for medium in release_info.get("medium-list") or []:
        if not isinstance(medium, Mapping):
            continue
        for track in medium.get("track-list") or []:
            if not isinstance(track, Mapping):
                continue
            if (track.get("recording") or {}).get("id") == recording_id:
                return track, medium
    return None


def _artist_credit_text(value: Any) -> str:
    """Render MusicBrainz artist-credit while retaining join phrases."""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts = []
    for credit in value:
        if isinstance(credit, str):
            parts.append(credit)
            continue
        if not isinstance(credit, Mapping):
            continue
        artist = credit.get("artist") or {}
        name = credit.get("name") or credit.get("credit-name")
        if not name and isinstance(artist, Mapping):
            name = artist.get("name")
        if name:
            parts.append(str(name))
        if credit.get("joinphrase"):
            parts.append(str(credit["joinphrase"]))
    return "".join(parts).strip()


def _positive_int(value: Any, fallback: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _build_tag_metadata(
    release_info: Mapping[str, Any],
    track_info: Mapping[str, Any],
    recording_id: str,
    release_id: str,
    disc_number: Any,
    *,
    track_total: Any = "",
    disc_total: Any = "",
) -> TagMetadata:
    """Build the existing flat tag payload from release and track metadata."""
    recording = track_info.get("recording") or {}
    album_artist = _artist_credit_text(release_info.get("artist-credit"))
    artist = (
        _artist_credit_text(track_info.get("artist-credit"))
        or _artist_credit_text(
            recording.get("artist-credit")
            if isinstance(recording, Mapping)
            else None
        )
        or album_artist
    )
    title = str(
        track_info.get("title")
        or (recording.get("title") if isinstance(recording, Mapping) else "")
        or ""
    )

    return {
        "artist": artist,
        "album_artist": album_artist,
        "album": release_info.get("title", ""),
        "title": title,
        "date": release_info.get("date", ""),
        "track_number": (
            track_info.get("position") or track_info.get("number", "")
        ),
        "track_total": track_total,
        "disc_number": disc_number or "",
        "disc_total": disc_total,
        "mbid": recording_id,
        "release_mbid": release_id,
        "release_track_mbid": track_info.get("id", ""),
    }


def read_current_tags(filepath: str) -> TagMetadata:
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
        "track_total": _first("tracktotal") or _first("totaltracks"),
        "disc_number": _first("discnumber"),
        "disc_total": _first("disctotal") or _first("totaldiscs"),
        "mbid": _first("musicbrainz_trackid") or _first("musicbrainz_recordingid"),
        "release_mbid": _first("musicbrainz_albumid"),
        "release_track_mbid": _first("musicbrainz_releasetrackid"),
    }


def _canonical_uuid(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.int == 0:
        return None
    return str(parsed)


def _has_complete_picard_metadata(tags: Mapping[str, Any]) -> bool:
    """Validate one already-read canonical metadata snapshot."""
    if not tags:
        return False
    if not all(str(tags.get(key) or "").strip() for key in (
        "title", "artist", "album", "album_artist",
    )):
        return False
    if not all(_canonical_uuid(tags.get(key)) for key in (
        "mbid", "release_mbid", "release_track_mbid",
    )):
        return False
    track_number = _positive_int(tags.get("track_number"))
    track_total = _positive_int(tags.get("track_total"))
    disc_number = _positive_int(tags.get("disc_number"))
    disc_total = _positive_int(tags.get("disc_total"))
    return bool(
        track_number
        and track_total >= track_number
        and disc_number
        and disc_total >= disc_number
    )


def _has_verified_picard_flac_ids(
    filepath: str,
    tags: Mapping[str, Any],
) -> bool:
    """Require official, unambiguous physical MusicBrainz FLAC mappings."""
    if os.path.splitext(filepath)[1].lower() != ".flac":
        return True
    try:
        audio = FLAC(filepath)
    except Exception:
        return False

    expected = {
        "musicbrainz_trackid": _canonical_uuid(tags.get("mbid")),
        "musicbrainz_albumid": _canonical_uuid(tags.get("release_mbid")),
        "musicbrainz_releasetrackid": _canonical_uuid(
            tags.get("release_track_mbid")
        ),
    }
    for key, expected_value in expected.items():
        values = tuple(str(value).strip() for value in audio.get(key, []))
        if len(values) != 1 or values[0] != expected_value:
            return False

    # A complete/canonical automatic file uses TRACKID only. Migration-copy
    # inputs may tolerate an equal legacy alias in _source_flac_tags_are_verified.
    recording_aliases = tuple(
        str(value).strip()
        for value in audio.get("musicbrainz_recordingid", [])
    )
    return not recording_aliases


def has_complete_picard_tags(filepath: str) -> bool:
    """Return whether a new file already carries a complete canonical set.

    Merely having a recording MBID is not enough: that was the old shortcut
    which let partially or incorrectly tagged downloads bypass MusicBrainz.
    A complete set has both release identities, usable ordering/totals, and
    the core display fields. Date may legitimately be blank in MusicBrainz.
    """
    tags = read_current_tags(filepath)
    return _has_complete_picard_metadata(
        tags
    ) and _has_verified_picard_flac_ids(filepath, tags)


def _picard_metadata_signature(tags: Mapping[str, Any]) -> Tuple[str, ...]:
    """Return the exact canonical fields owned by automatic Picard tagging."""
    return tuple(str(tags.get(key) or "") for key in _PICARD_METADATA_FIELDS)


def is_safe_auto_candidate(
    candidate: Optional[Mapping[str, Any]],
    *,
    expected_release_mbid: str = "",
    expected_recording_mbid: str = "",
) -> bool:
    """Validate the hard auto-apply policy independently of API claims."""
    if not isinstance(candidate, Mapping):
        return False
    try:
        score = float(candidate.get("score", 0.0))
    except (TypeError, ValueError):
        return False
    if (
        not math.isfinite(score)
        or score < CONFIDENCE_GREEN
        or score > 1.0
        or candidate.get("tier") != "green"
    ):
        return False
    meta = candidate.get("meta")
    if not isinstance(meta, Mapping):
        return False
    if not all(str(meta.get(key) or "").strip() for key in (
        "title", "artist", "album", "album_artist",
    )):
        return False
    recording = _canonical_uuid(meta.get("mbid"))
    release = _canonical_uuid(meta.get("release_mbid"))
    release_track = _canonical_uuid(meta.get("release_track_mbid"))
    if not recording or not release or not release_track:
        return False
    expected_recording = _canonical_uuid(expected_recording_mbid)
    expected_release = _canonical_uuid(expected_release_mbid)
    if expected_recording and recording != expected_recording:
        return False
    if expected_release and release != expected_release:
        return False
    track_number = _positive_int(meta.get("track_number"))
    track_total = _positive_int(meta.get("track_total"))
    disc_number = _positive_int(meta.get("disc_number"))
    disc_total = _positive_int(meta.get("disc_total"))
    return bool(
        track_number
        and track_total >= track_number
        and disc_number
        and disc_total >= disc_number
    )


def identify_file(
    filepath: str, api_key: str, contact: str = ""
) -> Optional[TagCandidate]:
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

    valid_items = []
    for item in items:
        try:
            candidate_score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate_score) and 0.0 <= candidate_score <= 1.0:
            valid_items.append(item)
    best = _select_best_acoustid_result(valid_items)
    if best is None:
        return None
    score = float(best.get("score", 0.0))

    recording_identity = _select_recording_identity(best)
    if recording_identity is None:
        return None
    recording_id, release_id = recording_identity

    try:
        mb_data = mb.get_release_by_id(
            release_id, includes=["artists", "recordings", "release-groups"]
        )
    except musicbrainzngs.WebServiceError as e:
        logger.error(f"identify_file: MusicBrainz error: {e}")
        return None

    release_info = mb_data.get("release") or {}
    track_selection = _select_musicbrainz_track_context(
        release_info,
        recording_id,
    )
    if track_selection is None:
        return None
    track_info, medium = track_selection
    media = [
        item for item in (release_info.get("medium-list") or [])
        if isinstance(item, Mapping)
    ]
    tracks = [
        item for item in (medium.get("track-list") or [])
        if isinstance(item, Mapping)
    ]

    meta = _build_tag_metadata(
        release_info,
        track_info,
        recording_id,
        release_id,
        medium.get("position"),
        track_total=_positive_int(medium.get("track-count"), len(tracks)),
        disc_total=len(media),
    )

    candidate: TagCandidate = {
        "score": score,
        "tier": _tier(score),
        "meta": meta,
        "current": read_current_tags(filepath),
    }
    return candidate


def identify_file_for_release(
    filepath: str,
    api_key: str,
    release_mbid: str,
    recording_mbid: str = "",
    contact: str = "",
) -> Optional[TagCandidate]:
    """Identify audio while binding the result to one selected release.

    AcoustID commonly returns one recording on many releases. Selecting the
    first release is unsafe for a satellite request whose user chose a precise
    MusicBrainz edition. This variant considers every AcoustID result but only
    accepts the selected release (and, when supplied, recording), then builds
    metadata from that exact release payload.
    """
    expected_release = _canonical_uuid(release_mbid)
    expected_recording = _canonical_uuid(recording_mbid)
    if not api_key or not expected_release or not os.path.exists(filepath):
        return None

    mb.configure(contact)
    try:
        duration, fingerprint = acoustid.fingerprint_file(filepath)
    except Exception as exc:
        logger.warning(
            "identify_file_for_release: fingerprint failed on %s: %s",
            os.path.basename(filepath),
            exc,
        )
        return None
    try:
        response = acoustid.lookup(
            api_key,
            fingerprint,
            duration,
            meta=[
                "recordings",
                "releases",
                "tracks",
                "usermeta",
                "releasegroups",
            ],
        )
    except acoustid.WebServiceError as exc:
        logger.error("identify_file_for_release: AcoustID error: %s", exc)
        return None

    candidate_scores: Dict[str, float] = {}
    for result in (response or {}).get("results") or []:
        try:
            score = float(result.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(score) or score < 0.0 or score > 1.0:
            continue
        for recording in result.get("recordings") or []:
            candidate_recording = _canonical_uuid(recording.get("id"))
            if not candidate_recording:
                continue
            if expected_recording and candidate_recording != expected_recording:
                continue
            release_ids = {
                _canonical_uuid(release.get("id"))
                for release in (recording.get("releases") or [])
                if isinstance(release, Mapping)
            }
            if expected_release in release_ids:
                candidate_scores[candidate_recording] = max(
                    score,
                    candidate_scores.get(candidate_recording, -1.0),
                )
    if not candidate_scores:
        return None
    ranked_candidates = sorted(
        candidate_scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    if (
        not expected_recording
        and len(ranked_candidates) > 1
        and ranked_candidates[0][1] - ranked_candidates[1][1]
        < ACOUSTID_AMBIGUITY_MARGIN
    ):
        logger.warning(
            "identify_file_for_release: ambiguous AcoustID recordings for "
            "release %s (%.3f vs %.3f)",
            expected_release,
            ranked_candidates[0][1],
            ranked_candidates[1][1],
        )
        return None

    identified_recording, score = ranked_candidates[0]
    try:
        mb_data = mb.get_release_by_id(
            expected_release,
            includes=["artists", "recordings", "release-groups"],
        )
    except musicbrainzngs.WebServiceError as exc:
        logger.error("identify_file_for_release: MusicBrainz error: %s", exc)
        return None
    release_info = mb_data.get("release") or {}
    if _canonical_uuid(release_info.get("id")) != expected_release:
        return None
    selection = _select_musicbrainz_track_context(
        release_info,
        identified_recording,
    )
    if selection is None:
        return None
    track_info, medium = selection
    media = [
        item for item in (release_info.get("medium-list") or [])
        if isinstance(item, Mapping)
    ]
    tracks = [
        item for item in (medium.get("track-list") or [])
        if isinstance(item, Mapping)
    ]
    meta = _build_tag_metadata(
        release_info,
        track_info,
        identified_recording,
        expected_release,
        medium.get("position"),
        track_total=_positive_int(medium.get("track-count"), len(tracks)),
        disc_total=len(media),
    )
    return {
        "score": score,
        "tier": _tier(score),
        "meta": meta,
        "current": read_current_tags(filepath),
    }


def write_tags(filepath: str, meta: TagMetadata) -> str:
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


def _flac_audio_payload_digest(filepath: str) -> str:
    """Hash FLAC frames only, excluding mutable metadata blocks."""
    digest = hashlib.sha256()
    with open(filepath, "rb") as handle:
        if handle.read(4) != b"fLaC":
            raise ValueError("not a FLAC stream")
        while True:
            header = handle.read(4)
            if len(header) != 4:
                raise ValueError("truncated FLAC metadata")
            last_block = bool(header[0] & 0x80)
            block_size = int.from_bytes(header[1:4], "big")
            if len(handle.read(block_size)) != block_size:
                raise ValueError("truncated FLAC metadata block")
            if last_block:
                break
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_digest(filepath: str) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flac_protected_metadata(filepath: str) -> Tuple[Dict[str, Tuple[str, ...]], Tuple[bytes, ...]]:
    """Snapshot user-owned tags and artwork excluded from canonical writes."""
    audio = FLAC(filepath)
    preserved: Dict[str, Tuple[str, ...]] = {}
    for key in audio.keys():
        normalized_key = str(key).casefold()
        if normalized_key in _CANONICAL_FLAC_TAGS:
            continue
        preserved[normalized_key] = tuple(str(value) for value in audio.get(key, []))
    pictures = tuple(picture.write() for picture in audio.pictures)
    return preserved, pictures


def _expected_canonical_flac_tags(
    meta: Mapping[str, Any],
) -> Dict[str, Tuple[str, ...]]:
    """Build DAPManager's normalized Picard/Vorbis canonical tag set.

    Picard maps a recording to ``MUSICBRAINZ_TRACKID`` and a release track to
    ``MUSICBRAINZ_RELEASETRACKID``. ``MUSICBRAINZ_RECORDINGID`` is a legacy
    duplicate used by some taggers; it is owned for cleanup but is never
    emitted by automatic writes.
    """
    expected = {
        "title": (_s(meta.get("title")),),
        "artist": (_s(meta.get("artist")),),
        "album": (_s(meta.get("album")),),
        "albumartist": (_s(meta.get("album_artist") or meta.get("artist")),),
        "date": (_s(meta.get("date")),),
        "tracknumber": (_s(meta.get("track_number")),),
        "tracktotal": (_s(meta.get("track_total")),),
        "discnumber": (_s(meta.get("disc_number")),),
        "disctotal": (_s(meta.get("disc_total")),),
    }
    if meta.get("mbid"):
        expected["musicbrainz_trackid"] = (_s(meta.get("mbid")),)
    if meta.get("release_mbid"):
        expected["musicbrainz_albumid"] = (_s(meta.get("release_mbid")),)
    if meta.get("release_track_mbid"):
        expected["musicbrainz_releasetrackid"] = (
            _s(meta.get("release_track_mbid")),
        )
    return expected


def _flac_canonical_metadata(
    filepath: str,
) -> Dict[str, Tuple[str, ...]]:
    """Read every physical canonical FLAC field without collapsing aliases."""
    audio = FLAC(filepath)
    values: Dict[str, Tuple[str, ...]] = {}
    for key in audio.keys():
        normalized_key = str(key).casefold()
        if normalized_key in _CANONICAL_FLAC_TAGS:
            values[normalized_key] = tuple(
                str(value) for value in audio.get(key, [])
            )
    return values


def _source_flac_tags_are_verified(
    filepath: str,
    meta: Mapping[str, Any],
) -> bool:
    """Accept normalized Picard tags plus non-conflicting legacy aliases.

    Older DAPManager writes duplicated the recording ID into
    ``MUSICBRAINZ_RECORDINGID`` and external taggers may use TOTALTRACKS /
    TOTALDISCS aliases. They are safe migration inputs only when they agree
    exactly with the normalized canonical value. Destinations are always
    rewritten to the official normalized set.
    """
    actual = _flac_canonical_metadata(filepath)
    expected = _expected_canonical_flac_tags(meta)

    legacy_recording = actual.pop("musicbrainz_recordingid", None)
    if legacy_recording is not None and legacy_recording != expected.get(
        "musicbrainz_trackid"
    ):
        return False

    for alias, canonical in (
        ("totaltracks", "tracktotal"),
        ("totaldiscs", "disctotal"),
    ):
        alias_value = actual.pop(alias, None)
        if alias_value is None:
            continue
        canonical_value = actual.get(canonical)
        if canonical_value is None:
            actual[canonical] = alias_value
        elif alias_value != canonical_value:
            return False

    # MusicBrainz may legitimately omit a release date. Treat an absent DATE
    # as the same complete logical value as the normalized empty DATE emitted
    # by our writer.
    if expected.get("date") == ("",) and "date" not in actual:
        actual["date"] = ("",)
    return actual == expected


def _require_matching_destination_recording(
    filepath: str,
    source_recording_mbid: Any,
) -> None:
    """Prove tag-only repair cannot relabel a different recording's audio."""
    expected = _canonical_uuid(source_recording_mbid)
    if expected is None:
        raise ValueError("Picard tag source recording identity is invalid")

    audio = FLAC(filepath)
    track_ids = tuple(
        str(value).strip()
        for value in audio.get("musicbrainz_trackid", [])
    )
    recording_ids = tuple(
        str(value).strip()
        for value in audio.get("musicbrainz_recordingid", [])
    )

    if track_ids:
        if len(track_ids) != 1:
            raise ValueError(
                "Picard tag destination recording identity is ambiguous"
            )
        actual = _canonical_uuid(track_ids[0])
        if recording_ids and (
            len(recording_ids) != 1
            or _canonical_uuid(recording_ids[0]) != actual
        ):
            raise ValueError(
                "Picard tag destination recording identity conflicts"
            )
    else:
        # A legacy RECORDINGID is accepted only as the sole physical fallback
        # when TRACKID is absent. The successful rewrite removes this alias.
        if len(recording_ids) != 1:
            raise ValueError(
                "Picard tag destination recording identity is missing or ambiguous"
            )
        actual = _canonical_uuid(recording_ids[0])

    if actual is None or actual != expected:
        raise ValueError(
            "Picard tag destination recording identity does not match source"
        )


def _verify_flac_tags(filepath: str, meta: Mapping[str, Any]) -> None:
    expected = _expected_canonical_flac_tags(meta)
    actual = _flac_canonical_metadata(filepath)
    if actual != expected:
        mismatches = sorted(set(actual) | set(expected))
        raise OSError(
            "canonical FLAC tags did not verify: " + ", ".join(mismatches)
        )


def _fsync_directory(path: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_tags_atomic_impl(
    filepath: str,
    meta: TagMetadata,
    *,
    expected_snapshot: Optional[Tuple[int, int, int, int, int]] = None,
) -> str:
    """Atomically apply verified Picard-style tags to a staged FLAC.

    The original file is never edited in place. A same-directory copy is
    tagged, then its FLAC frame digest, non-canonical user tags, artwork, and
    requested fields are verified before one atomic replacement. Any lookup,
    write, or verification failure leaves the original byte-for-byte intact.

    Automatic downloads are hard-filtered to FLAC. Other formats retain the
    manual :func:`write_tags` API but fail closed here until an equally strong
    container-specific audio-payload verifier exists.
    """
    if os.path.splitext(filepath)[1].lower() != ".flac":
        raise ValueError("atomic automatic tagging currently requires FLAC")
    path_stat = os.lstat(filepath)
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("automatic tagging requires a regular non-symlink file")
    path_snapshot = (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
    )
    if expected_snapshot is not None and path_snapshot != expected_snapshot:
        raise TagSynchronizationRace(
            "FLAC destination changed after its quality decision"
        )

    original_file = _file_digest(filepath)
    original_audio = _flac_audio_payload_digest(filepath)
    protected = _flac_protected_metadata(filepath)
    directory = os.path.dirname(os.path.abspath(filepath))
    temp_fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(filepath)}.daptag-",
        suffix=".flac",
        dir=directory,
    )
    try:
        with open(filepath, "rb") as source, os.fdopen(temp_fd, "wb") as target:
            temp_fd = -1
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temp_path, stat.S_IMODE(path_stat.st_mode))
        try:
            shutil.copystat(filepath, temp_path)
        except OSError:
            pass

        write_tags(temp_path, meta)
        _verify_flac_tags(temp_path, meta)
        if _flac_audio_payload_digest(temp_path) != original_audio:
            raise OSError("FLAC audio payload changed during tag write")
        if _flac_protected_metadata(temp_path) != protected:
            raise OSError("user-owned FLAC tags or artwork changed during tag write")
        current_stat = os.lstat(filepath)
        if (
            stat.S_ISLNK(current_stat.st_mode)
            or not stat.S_ISREG(current_stat.st_mode)
            or current_stat.st_dev != path_stat.st_dev
            or current_stat.st_ino != path_stat.st_ino
            or _file_digest(filepath) != original_file
        ):
            error = "staged FLAC changed concurrently during tag write"
            if expected_snapshot is not None:
                raise TagSynchronizationRace(error)
            raise OSError(error)
        with open(temp_path, "rb") as tagged:
            os.fsync(tagged.fileno())
        os.replace(temp_path, filepath)
        temp_path = ""
        _fsync_directory(directory)
        return "flac"
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def write_tags_atomic(filepath: str, meta: TagMetadata) -> str:
    """Atomically apply and verify canonical tags without changing FLAC frames."""
    return _write_tags_atomic_impl(filepath, meta)


def copy_complete_picard_tags_atomic(
    source_path: str,
    destination_path: str,
    *,
    expected_destination_snapshot: Optional[
        Tuple[int, int, int, int, int]
    ] = None,
) -> bool:
    """Copy only canonical Picard tags onto an existing FLAC atomically.

    The source must be a stable, complete canonical FLAC. The destination's
    encoded frames, artwork, non-canonical user tags, and mode are preserved by
    :func:`write_tags_atomic`. ``True`` means the destination was atomically
    replaced; ``False`` is a byte-exact no-op for an already canonical
    destination.
    """
    if (
        os.path.splitext(source_path)[1].lower() != ".flac"
        or os.path.splitext(destination_path)[1].lower() != ".flac"
    ):
        raise ValueError(
            "Picard tag synchronization requires FLAC source and destination"
        )
    try:
        if os.path.samefile(source_path, destination_path):
            return False
    except OSError:
        pass

    source_stat = os.lstat(source_path)
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(
        source_stat.st_mode
    ):
        raise ValueError("Picard tag source must be a regular non-symlink file")
    destination_stat = os.lstat(destination_path)
    if stat.S_ISLNK(destination_stat.st_mode) or not stat.S_ISREG(
        destination_stat.st_mode
    ):
        raise ValueError("Picard tag destination must be a regular non-symlink file")
    destination_snapshot = (
        destination_stat.st_dev,
        destination_stat.st_ino,
        destination_stat.st_size,
        destination_stat.st_mtime_ns,
        destination_stat.st_ctime_ns,
    )
    if (
        expected_destination_snapshot is not None
        and destination_snapshot != expected_destination_snapshot
    ):
        raise TagSynchronizationRace(
            "FLAC destination changed after its quality decision"
        )

    source_digest = _file_digest(source_path)
    source_meta = read_current_tags(source_path)
    if not has_complete_picard_tags(source_path):
        raise ValueError("Picard tag source is incomplete")
    if not _has_complete_picard_metadata(source_meta):
        raise ValueError("Picard tag source changed while it was read")
    if not _source_flac_tags_are_verified(source_path, source_meta):
        raise ValueError("Picard tag source has conflicting canonical fields")
    source_after = os.lstat(source_path)
    if (
        stat.S_ISLNK(source_after.st_mode)
        or not stat.S_ISREG(source_after.st_mode)
        or source_after.st_dev != source_stat.st_dev
        or source_after.st_ino != source_stat.st_ino
        or _file_digest(source_path) != source_digest
    ):
        raise OSError("Picard tag source changed while it was verified")

    destination_digest = _file_digest(destination_path)
    _require_matching_destination_recording(
        destination_path,
        source_meta.get("mbid"),
    )
    destination_meta = read_current_tags(destination_path)
    if (
        has_complete_picard_tags(destination_path)
        and _picard_metadata_signature(destination_meta)
        == _picard_metadata_signature(source_meta)
        and _flac_canonical_metadata(destination_path)
        == _expected_canonical_flac_tags(source_meta)
    ):
        current_stat = os.lstat(destination_path)
        if (
            (
                current_stat.st_dev,
                current_stat.st_ino,
                current_stat.st_size,
                current_stat.st_mtime_ns,
                current_stat.st_ctime_ns,
            )
            != destination_snapshot
            or _file_digest(destination_path) != destination_digest
        ):
            raise TagSynchronizationRace(
                "FLAC destination changed during canonical no-op proof"
            )
        return False

    # The atomic writer verifies canonical fields and every protected
    # destination invariant on its same-directory temp before publication.
    # Do not introduce a fallible post-publication phase here.
    _write_tags_atomic_impl(
        destination_path,
        source_meta,
        expected_snapshot=destination_snapshot,
    )
    return True


def _s(v) -> str:
    return "" if v is None else str(v)


def _write_flac(filepath: str, meta: TagMetadata) -> None:
    audio = FLAC(filepath)
    for key in tuple(audio.keys()):
        if str(key).casefold() in _CANONICAL_FLAC_TAGS:
            del audio[key]
    for key, values in _expected_canonical_flac_tags(meta).items():
        audio[key] = list(values)
    audio.save()


def _write_mp3(filepath: str, meta: TagMetadata) -> None:
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
    track_number = _s(meta.get("track_number"))
    track_total = _s(meta.get("track_total"))
    audio["tracknumber"] = (
        f"{track_number}/{track_total}" if track_number and track_total
        else track_number
    )
    disc_number = _s(meta.get("disc_number"))
    disc_total = _s(meta.get("disc_total"))
    audio["discnumber"] = (
        f"{disc_number}/{disc_total}" if disc_number and disc_total
        else disc_number
    )
    # EasyID3 exposes these as extendable keys by default
    if meta.get("mbid"):
        audio["musicbrainz_trackid"] = _s(meta["mbid"])
    if meta.get("release_mbid"):
        audio["musicbrainz_albumid"] = _s(meta["release_mbid"])
    # EasyID3 does not expose this non-standard TXXX key consistently across
    # mutagen versions. Automatic downloads are FLAC; manual MP3 writes retain
    # the established safe subset rather than risking a tag-registration bug.
    audio.save()


def _write_ogg(filepath: str, meta: TagMetadata) -> None:
    audio = OggVorbis(filepath)
    audio["title"] = _s(meta.get("title"))
    audio["artist"] = _s(meta.get("artist"))
    audio["album"] = _s(meta.get("album"))
    audio["albumartist"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["date"] = _s(meta.get("date"))
    audio["tracknumber"] = _s(meta.get("track_number"))
    audio["tracktotal"] = _s(meta.get("track_total"))
    audio["discnumber"] = _s(meta.get("disc_number"))
    audio["disctotal"] = _s(meta.get("disc_total"))
    if meta.get("mbid"):
        audio["musicbrainz_trackid"] = _s(meta["mbid"])
    if meta.get("release_mbid"):
        audio["musicbrainz_albumid"] = _s(meta["release_mbid"])
    if meta.get("release_track_mbid"):
        audio["musicbrainz_releasetrackid"] = _s(meta["release_track_mbid"])
    audio.save()


def update_album_tags(
    filepath: str,
    album: str,
    album_artist: Optional[str] = None,
    release_mbid: Optional[str] = None,
) -> str:
    """Update only the album-level tags on a file, preserving everything else.

    Unlike :func:`write_tags` (which rewrites the whole tag set and would blank
    fields it isn't given, e.g. date), this touches just ``album``, optionally
    ``albumartist``, and optionally ``musicbrainz_albumid`` — leaving title,
    artist, track/disc numbers, date and all other tags intact. Used by edition
    consolidation where only the album assignment changes.

    Returns the container name on success; raises ValueError for unsupported
    formats.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".m4a", ".mp4"):
        audio = MP4(filepath)
        audio["\xa9alb"] = _s(album)
        if album_artist is not None:
            audio["aART"] = _s(album_artist)
        if release_mbid:
            audio["----:com.apple.iTunes:MusicBrainz Album Id"] = [
                _s(release_mbid).encode("utf-8")
            ]
        audio.save()
        return "m4a"

    if ext == ".flac":
        audio = FLAC(filepath)
    elif ext == ".mp3":
        try:
            audio = EasyID3(filepath)
        except mutagen.id3.ID3NoHeaderError:
            from mutagen.id3 import ID3
            ID3().save(filepath)
            audio = EasyID3(filepath)
    elif ext == ".ogg":
        audio = OggVorbis(filepath)
    else:
        raise ValueError(f"unsupported file format for tagging: {ext}")

    audio["album"] = _s(album)
    if album_artist is not None:
        audio["albumartist"] = _s(album_artist)
    if release_mbid:
        audio["musicbrainz_albumid"] = _s(release_mbid)
    audio.save()
    return ext.lstrip(".")


def _write_m4a(filepath: str, meta: TagMetadata) -> None:
    audio = MP4(filepath)
    # MP4 uses atom keys
    audio["\xa9nam"] = _s(meta.get("title"))
    audio["\xa9ART"] = _s(meta.get("artist"))
    audio["\xa9alb"] = _s(meta.get("album"))
    audio["aART"] = _s(meta.get("album_artist") or meta.get("artist"))
    audio["\xa9day"] = _s(meta.get("date"))
    tn = _s(meta.get("track_number"))
    if tn.isdigit():
        total = _s(meta.get("track_total"))
        audio["trkn"] = [(int(tn), int(total) if total.isdigit() else 0)]
    dn = _s(meta.get("disc_number"))
    if dn.isdigit():
        total = _s(meta.get("disc_total"))
        audio["disk"] = [(int(dn), int(total) if total.isdigit() else 0)]
    if meta.get("mbid"):
        audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [
            _s(meta["mbid"]).encode("utf-8")
        ]
    if meta.get("release_mbid"):
        audio["----:com.apple.iTunes:MusicBrainz Album Id"] = [
            _s(meta["release_mbid"]).encode("utf-8")
        ]
    if meta.get("release_track_mbid"):
        audio["----:com.apple.iTunes:MusicBrainz Release Track Id"] = [
            _s(meta["release_track_mbid"]).encode("utf-8")
        ]
    audio.save()
