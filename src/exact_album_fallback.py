"""Fail-closed whole-album validation when AcoustID has no coverage.

This module deliberately plans only.  It never writes tags, moves media, or
updates the database.  The downloader may consume an accepted plan later,
after every staged FLAC has been matched bijectively to the persisted exact
MusicBrainz release manifest.
"""

from __future__ import annotations

import math
import os
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mutagen.flac import FLAC

from . import musicbrainz_client as mb
from . import tag_service
from .file_ingest import canonical_recording_mbid
from .services.album_download_request_service import (
    AlbumRequestResult,
    ResolvedAlbum,
    canonical_release_mbid,
    resolve_exact_release,
)


_NUMBER_TAG_RE = re.compile(r"^\s*(\d+)(?:\s*/\s*(\d+))?\s*$")
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")
_OMITTABLE_TITLE_SUFFIX_RE = re.compile(
    r"^(?P<title>.+?)\s+\((?:intro|interlude|outro|skit)\)$"
)
_OMITTABLE_ALBUM_TYPE_SUFFIX_RE = re.compile(
    r"^(?P<title>.+?)\s+\((?:album|ep|lp|single)\)$"
)
_AUDIO_SUFFIXES = (
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".wav",
    ".aiff",
    ".aif",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
    ".ape",
    ".wv",
)
_INCOMPLETE_AUDIO_SUFFIXES = tuple(
    suffix + ".incomplete" for suffix in _AUDIO_SUFFIXES
)
_EMPTY_PLAN: Mapping[str, str] = MappingProxyType({})
_EMPTY_SNAPSHOTS: Mapping[str, Tuple[int, int, int, int, int]] = (
    MappingProxyType({})
)


@dataclass(frozen=True)
class ExactAlbumFallbackPlan:
    """An immutable staged-path to exact-recording assignment."""

    recording_mbid_by_path: Mapping[str, str]
    reason: str = ""
    audio_payload_sha256_by_path: Mapping[str, str] = field(
        default_factory=lambda: _EMPTY_PLAN
    )
    file_snapshot_by_path: Mapping[
        str,
        Tuple[int, int, int, int, int],
    ] = field(default_factory=lambda: _EMPTY_SNAPSHOTS)

    @property
    def accepted(self) -> bool:
        paths = set(self.recording_mbid_by_path)
        return bool(
            paths
            and not self.reason
            and set(self.audio_payload_sha256_by_path) == paths
            and set(self.file_snapshot_by_path) == paths
            and all(
                re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))
                for digest in self.audio_payload_sha256_by_path.values()
            )
            and all(
                isinstance(snapshot, tuple)
                and len(snapshot) == 5
                and all(isinstance(value, int) for value in snapshot)
                for snapshot in self.file_snapshot_by_path.values()
            )
        )


@dataclass(frozen=True)
class _ExpectedTrack:
    position: int
    medium_position: int
    track_position: int
    track_number: str
    recording_mbid: str
    title: str
    artist: str
    date: str
    track_total: int
    disc_total: int
    release_track_mbid: str

    @property
    def medium_track(self) -> Tuple[int, int]:
        return self.medium_position, self.track_position

    @property
    def signature(self) -> Tuple[Any, ...]:
        return (
            self.position,
            self.recording_mbid,
            self.medium_position,
            self.track_position,
            self.track_number,
            self.title,
            self.artist,
            self.date,
            self.track_total,
            self.disc_total,
            self.release_track_mbid,
        )


@dataclass(frozen=True)
class _StagedFile:
    supplied_path: str
    real_path: str


@dataclass(frozen=True)
class _LiveTrackEvidence:
    length_ms: int
    isrcs: frozenset[str]


def _rejected(reason: str) -> ExactAlbumFallbackPlan:
    return ExactAlbumFallbackPlan(_EMPTY_PLAN, reason)


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _canonical_isrc(value: Any) -> str:
    normalized = re.sub(r"[-\s]", "", str(value or "")).upper()
    return normalized if _ISRC_RE.fullmatch(normalized) else ""


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def _title_matches(source: str, expected: str) -> bool:
    source_normalized = _normalize_text(source)
    expected_normalized = _normalize_text(expected)
    if not source_normalized or not expected_normalized:
        return False
    if source_normalized == expected_normalized:
        return True
    suffix_match = _OMITTABLE_TITLE_SUFFIX_RE.fullmatch(expected_normalized)
    return bool(
        suffix_match
        and source_normalized == suffix_match.group("title").strip()
    )


def _album_title_matches(source: str, expected: str) -> bool:
    source_normalized = _normalize_text(source)
    expected_normalized = _normalize_text(expected)
    if not source_normalized or not expected_normalized:
        return False
    if source_normalized == expected_normalized:
        return True
    suffix_match = _OMITTABLE_ALBUM_TYPE_SUFFIX_RE.fullmatch(
        source_normalized
    )
    return bool(
        suffix_match
        and suffix_match.group("title").strip() == expected_normalized
    )


def _single_value(tags: Mapping[str, Any], key: str) -> Optional[str]:
    raw = tags.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        values = (raw,)
    elif isinstance(raw, (list, tuple)):
        values = tuple(str(value) for value in raw)
    else:
        return None
    if len(values) != 1:
        return None
    value = str(values[0]).strip()
    return value or None


def _required_value(tags: Mapping[str, Any], key: str) -> Optional[str]:
    return _single_value(tags, key)


def _number_value(value: Optional[str]) -> Tuple[int, int]:
    if not value:
        return 0, 0
    match = _NUMBER_TAG_RE.fullmatch(value)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2) or 0)


def _total_value(
    tags: Mapping[str, Any],
    primary_key: str,
    alias_key: str,
) -> int:
    values = []
    for key in (primary_key, alias_key):
        raw = tags.get(key)
        if raw is None:
            continue
        value = _single_value(tags, key)
        parsed, composite_total = _number_value(value)
        if not value or not parsed or composite_total:
            return 0
        values.append(parsed)
    if not values or len(set(values)) != 1:
        return 0
    return values[0]


def _position_and_total(
    tags: Mapping[str, Any],
    number_key: str,
    total_key: str,
    total_alias: str,
) -> Tuple[int, int]:
    position, composite_total = _number_value(
        _required_value(tags, number_key)
    )
    separate_total = _total_value(tags, total_key, total_alias)
    if not position:
        return 0, 0
    if composite_total and separate_total and composite_total != separate_total:
        return 0, 0
    total = separate_total or composite_total
    if not total or position > total:
        return 0, 0
    return position, total


def _optional_id_values(
    tags: Mapping[str, Any],
    keys: Sequence[str],
    expected: str,
    canonicalize,
) -> bool:
    for key in keys:
        if tags.get(key) is None:
            continue
        raw = _single_value(tags, key)
        if not raw or canonicalize(raw) != expected:
            return False
    return True


def _parse_expected_tracks(
    album_manifest: Mapping[str, Any],
) -> Tuple[Tuple[_ExpectedTrack, ...], str]:
    raw_tracks = album_manifest.get("tracks")
    raw_recordings = album_manifest.get("recording_mbids")
    if not isinstance(raw_tracks, (list, tuple)) or not raw_tracks:
        return (), "The persisted exact-release track manifest is missing"
    if not isinstance(raw_recordings, (list, tuple)) or not raw_recordings:
        return (), "The persisted exact-release recording manifest is missing"
    if len(raw_tracks) != len(raw_recordings):
        return (), "The persisted release manifests have different sizes"

    tracks: List[_ExpectedTrack] = []
    for fallback_position, raw in enumerate(raw_tracks, start=1):
        if not isinstance(raw, Mapping):
            return (), "The persisted exact-release track manifest is malformed"
        recording_mbid = canonical_recording_mbid(raw.get("recording_mbid"))
        release_track_mbid = canonical_release_mbid(
            raw.get("release_track_mbid")
        )
        track = _ExpectedTrack(
            position=_positive_int(raw.get("position")),
            medium_position=_positive_int(raw.get("medium_position")),
            track_position=_positive_int(raw.get("track_position")),
            track_number=str(raw.get("track_number") or "").strip(),
            recording_mbid=recording_mbid or "",
            title=str(raw.get("title") or "").strip(),
            artist=str(raw.get("artist") or "").strip(),
            date=str(raw.get("date") or "").strip(),
            track_total=_positive_int(raw.get("track_total")),
            disc_total=_positive_int(raw.get("disc_total")),
            release_track_mbid=release_track_mbid or "",
        )
        if (
            track.position != fallback_position
            or not track.recording_mbid
            or not track.release_track_mbid
            or not track.title
            or not track.artist
            or not track.date
            or track.medium_position <= 0
            or track.track_position <= 0
            or track.track_total < track.track_position
            or track.disc_total < track.medium_position
            or not track.track_number
        ):
            return (), "The persisted exact-release track manifest is incomplete"
        tracks.append(track)

    expected_recordings = tuple(
        canonical_recording_mbid(value) or "" for value in raw_recordings
    )
    actual_recordings = tuple(track.recording_mbid for track in tracks)
    if expected_recordings != actual_recordings:
        return (), "The persisted recording order does not match its track manifest"
    if len(set(actual_recordings)) != len(actual_recordings):
        return (), (
            "The persisted release repeats a recording identity that the "
            "current library schema cannot represent safely"
        )
    release_tracks = tuple(track.release_track_mbid for track in tracks)
    if len(set(release_tracks)) != len(release_tracks):
        return (), "The persisted release repeats a release-track identity"
    medium_tracks = tuple(track.medium_track for track in tracks)
    if len(set(medium_tracks)) != len(medium_tracks):
        return (), "The persisted release repeats a disc/track position"

    disc_totals = {track.disc_total for track in tracks}
    dates = {track.date for track in tracks}
    if len(disc_totals) != 1 or len(dates) != 1:
        return (), "The persisted release has inconsistent shared metadata"
    disc_total = next(iter(disc_totals))
    media = {track.medium_position for track in tracks}
    if media != set(range(1, disc_total + 1)):
        return (), "The persisted release does not contain a complete disc set"
    for medium_position in sorted(media):
        medium_tracks_for_disc = [
            track for track in tracks
            if track.medium_position == medium_position
        ]
        totals = {track.track_total for track in medium_tracks_for_disc}
        if len(totals) != 1:
            return (), "The persisted release has inconsistent track totals"
        track_total = next(iter(totals))
        positions = {track.track_position for track in medium_tracks_for_disc}
        if (
            len(medium_tracks_for_disc) != track_total
            or positions != set(range(1, track_total + 1))
        ):
            return (), "The persisted release does not contain a complete track set"
    return tuple(tracks), ""


def match_exact_manifest_recording(
    album_manifest: Mapping[str, Any],
    source_tags: Mapping[str, Any],
) -> str:
    """Return one manifest recording proven by complete source tag context.

    This association is used only to disambiguate equal-top AcoustID
    recording identities. It deliberately requires the exact disc/track
    position, totals, title, artists, album, and date; incomplete or
    conflicting tags leave the recording unbound so acoustic ambiguity still
    fails closed.
    """
    if not isinstance(album_manifest, Mapping) or not isinstance(
        source_tags,
        Mapping,
    ):
        return ""
    tracks, error = _parse_expected_tracks(album_manifest)
    if error:
        return ""

    track_position, composite_track_total = _number_value(
        str(source_tags.get("track_number") or "")
    )
    track_total, _ = _number_value(
        str(source_tags.get("track_total") or "")
    )
    disc_position, composite_disc_total = _number_value(
        str(source_tags.get("disc_number") or "")
    )
    disc_total, _ = _number_value(
        str(source_tags.get("disc_total") or "")
    )
    if composite_track_total:
        if track_total and track_total != composite_track_total:
            return ""
        track_total = composite_track_total
    if composite_disc_total:
        if disc_total and disc_total != composite_disc_total:
            return ""
        disc_total = composite_disc_total
    if not all((track_position, track_total, disc_position, disc_total)):
        return ""

    matches = [
        track
        for track in tracks
        if track.medium_track == (disc_position, track_position)
    ]
    if len(matches) != 1:
        return ""
    track = matches[0]
    if (track.track_total, track.disc_total) != (track_total, disc_total):
        return ""
    if not _title_matches(str(source_tags.get("title") or ""), track.title):
        return ""
    if _normalize_text(source_tags.get("artist")) != _normalize_text(
        track.artist
    ):
        return ""
    if not _album_title_matches(
        str(source_tags.get("album") or ""),
        str(album_manifest.get("title") or ""),
    ):
        return ""
    if _normalize_text(source_tags.get("album_artist")) != _normalize_text(
        album_manifest.get("artist")
    ):
        return ""
    if _normalize_text(source_tags.get("date")) != _normalize_text(track.date):
        return ""
    source_recording = str(source_tags.get("mbid") or "").strip()
    if source_recording and (
        canonical_recording_mbid(source_recording) != track.recording_mbid
    ):
        return ""
    source_release = str(source_tags.get("release_mbid") or "").strip()
    if source_release and (
        canonical_release_mbid(source_release)
        != canonical_release_mbid(album_manifest.get("release_mbid"))
    ):
        return ""
    source_release_track = str(
        source_tags.get("release_track_mbid") or ""
    ).strip()
    if source_release_track and (
        canonical_release_mbid(source_release_track)
        != track.release_track_mbid
    ):
        return ""
    return track.recording_mbid


def _path_has_symlink_component(root: str, path: str) -> bool:
    relative = os.path.relpath(path, root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return True
    current = root
    try:
        if stat.S_ISLNK(os.lstat(current).st_mode):
            return True
        for component in relative.split(os.sep):
            current = os.path.join(current, component)
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return True
    except OSError:
        return True
    return False


def _validate_staged_paths(
    file_paths: Sequence[str],
    staging_root: str,
) -> Tuple[Tuple[_StagedFile, ...], str]:
    supplied_root = os.path.abspath(os.fspath(staging_root))
    real_root = os.path.realpath(supplied_root)
    try:
        root_stat = os.lstat(supplied_root)
    except OSError:
        return (), "The staging root is unavailable"
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        return (), "The staging root must be a real directory"

    staged: List[_StagedFile] = []
    seen_real_paths = set()
    for supplied in file_paths:
        try:
            supplied_path = os.fspath(supplied)
        except TypeError:
            return (), "A staged path is invalid"
        absolute_path = os.path.abspath(supplied_path)
        real_path = os.path.realpath(absolute_path)
        try:
            lexical_common = os.path.commonpath([supplied_root, absolute_path])
            real_common = os.path.commonpath([real_root, real_path])
        except ValueError:
            return (), "A staged file is outside the isolated staging root"
        if lexical_common != supplied_root or real_common != real_root:
            return (), "A staged file is outside the isolated staging root"
        if _path_has_symlink_component(supplied_root, absolute_path):
            return (), "Symlinks are not allowed in exact-album staging"
        try:
            file_stat = os.lstat(absolute_path)
        except OSError:
            return (), "A staged file is unavailable"
        if not stat.S_ISREG(file_stat.st_mode):
            return (), "Every staged album item must be a regular file"
        if not absolute_path.lower().endswith(".flac"):
            return (), "Every staged album item must be a FLAC file"
        if real_path in seen_real_paths:
            return (), "The staged album repeats the same file"
        seen_real_paths.add(real_path)
        staged.append(_StagedFile(str(supplied_path), real_path))

    supplied_audio = {item.real_path for item in staged}
    discovered_audio = set()

    def raise_walk_error(error: OSError) -> None:
        raise error

    try:
        for current_root, directories, filenames in os.walk(
            supplied_root,
            followlinks=False,
            onerror=raise_walk_error,
        ):
            for directory in directories:
                directory_path = os.path.join(current_root, directory)
                if os.path.islink(directory_path):
                    return (), "Symlinks are not allowed in exact-album staging"
            for filename in filenames:
                lower_name = filename.lower()
                if not lower_name.endswith(
                    _AUDIO_SUFFIXES + _INCOMPLETE_AUDIO_SUFFIXES
                ):
                    continue
                audio_path = os.path.join(current_root, filename)
                if os.path.islink(audio_path):
                    return (), "Symlinks are not allowed in exact-album staging"
                if lower_name.endswith(_INCOMPLETE_AUDIO_SUFFIXES):
                    return (), "Incomplete audio remains in exact-album staging"
                if not lower_name.endswith(".flac"):
                    return (), "Exact-album staging contains non-FLAC audio"
                discovered_audio.add(os.path.realpath(audio_path))
    except OSError:
        return (), "The isolated staging set could not be enumerated"
    if discovered_audio != supplied_audio:
        return (), "The supplied staged files are not the complete audio set"
    return tuple(staged), ""


def _file_snapshot(path: str) -> Tuple[int, int, int, int, int]:
    path_stat = os.lstat(path)
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ValueError("exact-album source is not a regular file")
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
    )


def _manifest_signature(
    tracks: Sequence[_ExpectedTrack],
) -> Tuple[Tuple[Any, ...], ...]:
    return tuple(track.signature for track in tracks)


def _resolved_signature(album: ResolvedAlbum) -> Tuple[Tuple[Any, ...], ...]:
    resolved: List[_ExpectedTrack] = []
    for track in album.tracks:
        resolved.append(_ExpectedTrack(
            position=track.position,
            medium_position=track.medium_position,
            track_position=track.track_position,
            track_number=track.track_number,
            recording_mbid=track.recording_mbid,
            title=track.title,
            artist=track.artist,
            date=track.date,
            track_total=track.track_total,
            disc_total=track.disc_total,
            release_track_mbid=track.release_track_mbid,
        ))
    return _manifest_signature(resolved)


def _release_track_evidence(
    response: Any,
    expected_release_mbid: str,
) -> Tuple[Dict[str, _LiveTrackEvidence], str]:
    if not isinstance(response, Mapping):
        return {}, "MusicBrainz returned an invalid exact-release response"
    release = response.get("release")
    if not isinstance(release, Mapping):
        return {}, "MusicBrainz returned an invalid exact-release response"
    if canonical_release_mbid(release.get("id")) != expected_release_mbid:
        return {}, "MusicBrainz returned a different release identity"
    media = release.get("medium-list") or release.get("media")
    if not isinstance(media, list):
        return {}, "MusicBrainz omitted the exact-release media"
    evidence: Dict[str, _LiveTrackEvidence] = {}
    for medium in media:
        if not isinstance(medium, Mapping):
            return {}, "MusicBrainz returned malformed release media"
        raw_tracks = medium.get("track-list") or medium.get("tracks")
        if not isinstance(raw_tracks, list):
            return {}, "MusicBrainz omitted an exact-release track list"
        for track in raw_tracks:
            if not isinstance(track, Mapping):
                return {}, "MusicBrainz returned a malformed release track"
            release_track_mbid = canonical_release_mbid(track.get("id"))
            length_ms = _positive_int(track.get("length"))
            recording = track.get("recording")
            raw_isrcs = (
                recording.get("isrc-list")
                if isinstance(recording, Mapping)
                else None
            )
            isrcs = frozenset(
                _canonical_isrc(value)
                for value in raw_isrcs
            ) if isinstance(raw_isrcs, list) else frozenset()
            if (
                not release_track_mbid
                or not length_ms
                or "" in isrcs
                or release_track_mbid in evidence
            ):
                return {}, (
                    "MusicBrainz omitted unambiguous track duration evidence"
                )
            evidence[release_track_mbid] = _LiveTrackEvidence(
                length_ms=length_ms,
                isrcs=isrcs,
            )
    return evidence, ""


def _duration_tolerance_ms(expected_ms: int) -> float:
    return max(1500.0, min(3000.0, expected_ms * 0.005))


def _validate_flac_against_track(
    path: str,
    track: _ExpectedTrack,
    *,
    album_title: str,
    album_artist: str,
    release_mbid: str,
    expected_evidence: _LiveTrackEvidence,
) -> str:
    try:
        audio = FLAC(path)
    except Exception:
        return "A staged file is not a readable FLAC stream"
    info = getattr(audio, "info", None)
    try:
        duration_ms = float(getattr(info, "length", 0.0)) * 1000.0
        sample_rate = int(getattr(info, "sample_rate", 0) or 0)
        channels = int(getattr(info, "channels", 0) or 0)
        bits_per_sample = int(getattr(info, "bits_per_sample", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return "A staged FLAC has invalid stream information"
    if (
        not math.isfinite(duration_ms)
        or duration_ms <= 0
        or sample_rate <= 0
        or channels <= 0
        or bits_per_sample <= 0
    ):
        return "A staged FLAC has incomplete stream information"

    title = _single_value(audio, "title")
    artist = _single_value(audio, "artist")
    album = _single_value(audio, "album")
    tagged_album_artist = _single_value(audio, "albumartist")
    date = _single_value(audio, "date")
    source_isrc = _canonical_isrc(_required_value(audio, "isrc"))
    if title and not _title_matches(title, track.title):
        return "A staged FLAC title does not match the exact release"
    if artist and _normalize_text(artist) != _normalize_text(track.artist):
        return "A staged FLAC artist does not match the exact release"
    if album and _normalize_text(album) != _normalize_text(album_title):
        return "A staged FLAC album does not match the exact release"
    if tagged_album_artist and _normalize_text(tagged_album_artist) != _normalize_text(album_artist):
        return "A staged FLAC album artist does not match the exact release"
    if date and _normalize_text(date) != _normalize_text(track.date):
        return "A staged FLAC date does not match the exact release"
    if (
        source_isrc
        and expected_evidence.isrcs
        and source_isrc not in expected_evidence.isrcs
    ):
        return "A staged FLAC ISRC does not match the exact recording"

    track_position, track_total = _position_and_total(
        audio,
        "tracknumber",
        "tracktotal",
        "totaltracks",
    )
    disc_position, disc_total = _position_and_total(
        audio,
        "discnumber",
        "disctotal",
        "totaldiscs",
    )
    if (track_position, track_total) != (
        track.track_position,
        track.track_total,
    ):
        return "A staged FLAC track position or total does not match"
    if (disc_position, disc_total) != (
        track.medium_position,
        track.disc_total,
    ):
        return "A staged FLAC disc position or total does not match"

    if not _optional_id_values(
        audio,
        ("musicbrainz_trackid", "musicbrainz_recordingid"),
        track.recording_mbid,
        canonical_recording_mbid,
    ):
        return "A staged FLAC has a conflicting recording identity"
    if not _optional_id_values(
        audio,
        ("musicbrainz_albumid",),
        release_mbid,
        canonical_release_mbid,
    ):
        return "A staged FLAC has a conflicting release identity"
    if not _optional_id_values(
        audio,
        ("musicbrainz_releasetrackid",),
        track.release_track_mbid,
        canonical_release_mbid,
    ):
        return "A staged FLAC has a conflicting release-track identity"

    if abs(duration_ms - expected_evidence.length_ms) > _duration_tolerance_ms(
        expected_evidence.length_ms
    ):
        return "A staged FLAC duration does not match the exact release"
    return ""


def build_exact_album_fallback_plan(
    file_paths: Sequence[str],
    staging_root: str,
    album_manifest: Mapping[str, Any],
    api_key: str,
    contact: str = "",
) -> ExactAlbumFallbackPlan:
    """Return a conservative whole-set exact-release validation plan.

    Every validation finishes before the returned plan can be consumed.  A
    single mismatch rejects the complete staging set and returns no path
    assignments.
    """
    if not isinstance(album_manifest, Mapping):
        return _rejected("The exact-release album manifest is invalid")
    release_mbid = canonical_release_mbid(album_manifest.get("release_mbid"))
    album_title = str(album_manifest.get("title") or "").strip()
    album_artist = str(album_manifest.get("artist") or "").strip()
    if not release_mbid or not album_title or not album_artist:
        return _rejected("The exact-release album identity is incomplete")

    tracks, manifest_error = _parse_expected_tracks(album_manifest)
    if manifest_error:
        return _rejected(manifest_error)
    if _positive_int(album_manifest.get("track_count")) != len(tracks):
        return _rejected(
            "The tracked album count does not match its persisted manifest"
        )
    if len(file_paths) != len(tracks):
        return _rejected(
            "The staged FLAC count does not match the exact release manifest"
        )
    staged_files, path_error = _validate_staged_paths(file_paths, staging_root)
    if path_error:
        return _rejected(path_error)
    if not staged_files:
        return _rejected("The exact release has no staged FLAC files")
    if not str(api_key or "").strip():
        return _rejected("An AcoustID API key is required for fallback validation")

    expected_by_position = {track.medium_track: track for track in tracks}
    for staged in staged_files:
        try:
            source_audio = FLAC(staged.real_path)
            track_position, _ = _position_and_total(
                source_audio, "tracknumber", "tracktotal", "totaltracks"
            )
            disc_position, _ = _position_and_total(
                source_audio, "discnumber", "disctotal", "totaldiscs"
            )
            expected_track = expected_by_position.get(
                (disc_position, track_position)
            )
            if expected_track is None:
                return _rejected(
                    "The staged FLAC positions do not form an exact release bijection"
                )
            coverage = tag_service.probe_acoustid_coverage(
                staged.real_path, api_key
            )
        except Exception:
            return _rejected("AcoustID evidence could not be verified")
        coverage_state = getattr(coverage, "state", None)
        if coverage_state == "no_results":
            continue
        if coverage_state != "has_results":
            return _rejected("AcoustID evidence is temporarily unavailable")
        try:
            evidence = tag_service.classify_acoustic_recording_evidence(
                staged.real_path, api_key, expected_track.recording_mbid
            )
        except Exception:
            return _rejected("AcoustID evidence could not be verified")
        state = getattr(evidence, "state", None)
        if state == "contradict":
            return _rejected(
                "AcoustID strongly contradicts the exact-release recording"
            )
        if state == "unavailable":
            return _rejected("AcoustID evidence is temporarily unavailable")

    mb.configure(contact)
    try:
        response = mb.get_release_by_id(
            release_mbid,
            includes=[
                "artists",
                "release-groups",
                "recordings",
                "isrcs",
            ],
        )
        resolved = resolve_exact_release(
            release_mbid,
            get_release_by_id=lambda *_args, **_kwargs: response,
        )
    except Exception:
        return _rejected("The exact MusicBrainz release could not be reverified")
    if isinstance(resolved, AlbumRequestResult) or not isinstance(
        resolved,
        ResolvedAlbum,
    ):
        return _rejected("The exact MusicBrainz release could not be reverified")
    if (
        resolved.release_mbid != release_mbid
        or resolved.title != album_title
        or resolved.artist != album_artist
        or _resolved_signature(resolved) != _manifest_signature(tracks)
    ):
        return _rejected(
            "The current MusicBrainz release no longer matches the persisted manifest"
        )

    live_evidence, evidence_error = _release_track_evidence(
        response,
        release_mbid,
    )
    if evidence_error:
        return _rejected(evidence_error)
    if set(live_evidence) != {
        track.release_track_mbid for track in tracks
    }:
        return _rejected(
            "MusicBrainz evidence does not cover the exact manifest"
        )

    assignments: Dict[str, str] = {}
    audio_digests: Dict[str, str] = {}
    file_snapshots: Dict[str, Tuple[int, int, int, int, int]] = {}
    assigned_release_tracks = set()
    for staged in staged_files:
        try:
            before_snapshot = _file_snapshot(staged.real_path)
            audio = FLAC(staged.real_path)
        except Exception:
            return _rejected("A staged file is not a readable FLAC stream")
        track_position, _ = _position_and_total(
            audio,
            "tracknumber",
            "tracktotal",
            "totaltracks",
        )
        disc_position, _ = _position_and_total(
            audio,
            "discnumber",
            "disctotal",
            "totaldiscs",
        )
        track = expected_by_position.get((disc_position, track_position))
        if track is None or track.release_track_mbid in assigned_release_tracks:
            return _rejected(
                "The staged FLAC positions do not form an exact release bijection"
            )
        validation_error = _validate_flac_against_track(
            staged.real_path,
            track,
            album_title=album_title,
            album_artist=album_artist,
            release_mbid=release_mbid,
            expected_evidence=live_evidence[track.release_track_mbid],
        )
        if validation_error:
            return _rejected(validation_error)
        try:
            audio_digest = tag_service.flac_audio_payload_digest(
                staged.real_path
            )
            after_snapshot = _file_snapshot(staged.real_path)
        except (OSError, ValueError):
            return _rejected(
                "A staged FLAC could not be captured as stable evidence"
            )
        if before_snapshot != after_snapshot:
            return _rejected(
                "A staged FLAC changed during exact-album validation"
            )
        assignments[staged.supplied_path] = track.recording_mbid
        audio_digests[staged.supplied_path] = audio_digest
        file_snapshots[staged.supplied_path] = after_snapshot
        assigned_release_tracks.add(track.release_track_mbid)

    expected_release_tracks = {track.release_track_mbid for track in tracks}
    if assigned_release_tracks != expected_release_tracks:
        return _rejected(
            "The staged FLAC files do not cover every exact-release track"
        )
    return ExactAlbumFallbackPlan(
        MappingProxyType(assignments),
        audio_payload_sha256_by_path=MappingProxyType(audio_digests),
        file_snapshot_by_path=MappingProxyType(file_snapshots),
    )
