"""MusicBrainz-backed album requests and satellite-to-master forwarding.

The browser never supplies the Soulseek query for an album request.  It picks
one MusicBrainz *release*, and the master resolves that release again before it
constructs the canonical queue item.  Keeping this policy outside Flask makes
the ambiguity, validation, de-duplication, and proxy behaviour independently
testable.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import re
import stat
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import urlsplit
from uuid import UUID

import requests


logger = logging.getLogger(__name__)

ALBUM_PLAYLIST_ID = "SATELLITE_ALBUM"
ALBUM_QUERY_PREFIX = "::ALBUM::"
MAX_SEARCH_RESULTS = 8
MAX_TRACKED_REQUESTS = 30
LIDARR_IMPORT_STALE_SECONDS = 6 * 60 * 60
EXACT_RELEASE_PRIMARY_TYPES = frozenset({"album", "ep", "single"})
_SEARCH_SPLIT_RE = re.compile(r"\s+(?:-|\N{EN DASH}|\N{EM DASH})\s+", re.UNICODE)


@dataclass(frozen=True)
class AlbumRequestResult:
    payload: Dict[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class ResolvedTrack:
    position: int
    medium_position: int
    track_position: int
    track_number: str
    recording_mbid: str
    title: str
    artist: str
    date: str
    track_total: int = 0
    disc_total: int = 0
    release_track_mbid: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "position": self.position,
            "medium_position": self.medium_position,
            "track_position": self.track_position,
            "track_number": self.track_number,
            "recording_mbid": self.recording_mbid,
            "title": self.title,
            "artist": self.artist,
            "date": self.date,
            "track_total": self.track_total,
            "disc_total": self.disc_total,
            "release_track_mbid": self.release_track_mbid,
        }


@dataclass(frozen=True)
class ResolvedAlbum:
    release_mbid: str
    title: str
    artist: str
    track_count: int
    date: str = ""
    country: str = ""
    status: str = ""
    disambiguation: str = ""
    primary_type: str = ""
    recording_mbids: Tuple[str, ...] = ()
    format: str = ""
    label: str = ""
    catalog_number: str = ""
    barcode: str = ""
    tracks: Tuple[ResolvedTrack, ...] = ()

    def __post_init__(self) -> None:
        if self.tracks and not self.recording_mbids:
            object.__setattr__(
                self,
                "recording_mbids",
                tuple(track.recording_mbid for track in self.tracks),
            )

    def track_manifest(self) -> Tuple[Dict[str, Any], ...]:
        if self.tracks:
            return tuple(track.as_dict() for track in self.tracks)
        # Compatibility for older internal callers. MusicBrainz-resolved web
        # requests always carry the full manifest and are validated below.
        return tuple(
            ResolvedTrack(
                position=position,
                medium_position=1,
                track_position=position,
                track_number=str(position),
                recording_mbid=recording_mbid,
                title="",
                artist=self.artist,
                date=self.date,
            ).as_dict()
            for position, recording_mbid in enumerate(
                self.recording_mbids,
                start=1,
            )
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "release_mbid": self.release_mbid,
            "title": self.title,
            "artist": self.artist,
            "track_count": self.track_count,
            "date": self.date,
            "country": self.country,
            "status": self.status,
            "disambiguation": self.disambiguation,
            "primary_type": self.primary_type,
            "format": self.format,
            "label": self.label,
            "catalog_number": self.catalog_number,
            "barcode": self.barcode,
            "musicbrainz_url": (
                "https://musicbrainz.org/release/" f"{self.release_mbid}"
            ),
            "cover_url": (
                "https://coverartarchive.org/release/"
                f"{self.release_mbid}/front-250"
            ),
        }


class AlbumRequestStore(Protocol):
    def get_album_download_request_by_release(
        self, release_mbid: str
    ) -> Optional[Dict[str, Any]]: ...

    def get_active_download_id(
        self, mbid: Optional[str], search_query: str
    ) -> Optional[int]: ...

    def count_local_release_tracks(self, release_mbid: str) -> int: ...

    def get_album_download_request_recording_mbids(
        self, request_id: int
    ) -> List[str]: ...

    def get_album_download_request_track_manifest(
        self, request_id: int
    ) -> List[Dict[str, Any]]: ...

    def get_local_release_recordings(
        self, release_mbid: str
    ) -> List[Dict[str, Any]]: ...

    def queue_download(self, item: Any) -> int: ...

    def remove_from_queue(self, item_id: int) -> None: ...

    def create_album_download_request(
        self,
        *,
        queue_item_id: Optional[int],
        release_mbid: str,
        artist: str,
        title: str,
        track_count: int,
        stage: str,
        detail: str,
        completed_tracks: int,
        recording_mbids: Sequence[str],
        track_manifest: Sequence[Mapping[str, Any]] = (),
    ) -> int: ...

    def claim_download_and_create_album_request(
        self,
        *,
        queue_item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Tuple[str, ...],
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> Optional[int]: ...

    def create_download_and_album_request(
        self,
        *,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Tuple[str, ...],
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> Tuple[int, int]: ...

    def create_download_and_requeue_album_request(
        self,
        *,
        request_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        detail: str,
        completed_tracks: int,
    ) -> Optional[int]: ...

    def get_album_download_request(
        self, request_id: int
    ) -> Optional[Dict[str, Any]]: ...

    def list_active_album_download_requests(
        self, limit: int
    ) -> List[Dict[str, Any]]: ...

    def update_album_download_request_progress(
        self,
        queue_item_id: int,
        stage: str,
        detail: str = "",
        completed_tracks: Optional[int] = None,
    ) -> bool: ...

    def complete_album_download_request(
        self,
        queue_item_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool: ...

    def complete_album_download_request_by_id(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool: ...

    def retry_download(self, item_id: int) -> bool: ...

    def claim_download_for_album_request(
        self,
        item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
    ) -> bool: ...

    def requeue_album_download_request(
        self,
        request_id: int,
        queue_item_id: int,
        detail: str,
        completed_tracks: int = 0,
    ) -> bool: ...

    def invalidate_album_download_request(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool: ...

    def replace_album_download_request_identity(
        self,
        request_id: int,
        *,
        artist: str,
        title: str,
        track_count: int,
        recording_mbids: Tuple[str, ...],
        detail: str,
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> bool: ...


SearchReleases = Callable[..., Mapping[str, Any]]
GetRelease = Callable[..., Mapping[str, Any]]
ItemFactory = Callable[..., Any]
HttpRequest = Callable[..., Any]


def canonical_release_mbid(value: Any) -> Optional[str]:
    """Return a canonical non-nil UUID, or ``None`` for unsafe input."""
    text = str(value or "").strip()
    try:
        parsed = UUID(text)
    except (ValueError, TypeError, AttributeError):
        return None
    if parsed.int == 0:
        return None
    return str(parsed)


def _artist_credit(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: List[str] = []
    for credit in value:
        if isinstance(credit, str):
            parts.append(credit)
            continue
        if not isinstance(credit, Mapping):
            continue
        artist = credit.get("artist")
        name = credit.get("name") or credit.get("credit-name")
        if not name and isinstance(artist, Mapping):
            name = artist.get("name")
        if name:
            parts.append(str(name))
        join = credit.get("joinphrase")
        if join:
            parts.append(str(join))
    return "".join(parts).strip()


def _track_count(release: Mapping[str, Any]) -> int:
    raw_total = release.get("track-count")
    try:
        total = int(raw_total or 0)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        return total

    media = release.get("medium-list") or release.get("media") or []
    if not isinstance(media, list):
        return 0
    total = 0
    for medium in media:
        if not isinstance(medium, Mapping):
            continue
        raw_count = medium.get("track-count")
        if raw_count is None:
            tracks = medium.get("track-list") or medium.get("tracks") or []
            raw_count = len(tracks) if isinstance(tracks, list) else 0
        try:
            total += max(0, int(raw_count or 0))
        except (TypeError, ValueError):
            continue
    return total


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _track_manifest(
    release: Mapping[str, Any],
    album_artist: str,
    release_date: str,
) -> Tuple[ResolvedTrack, ...]:
    media = release.get("medium-list") or release.get("media") or []
    if not isinstance(media, list):
        return ()
    valid_media = [item for item in media if isinstance(item, Mapping)]
    disc_total = len(valid_media)
    manifest: List[ResolvedTrack] = []
    global_position = 0
    for medium_index, medium in enumerate(media, start=1):
        if not isinstance(medium, Mapping):
            continue
        medium_position = _positive_int(medium.get("position"), medium_index)
        tracks = medium.get("track-list") or medium.get("tracks") or []
        if not isinstance(tracks, list):
            continue
        track_total = len([track for track in tracks if isinstance(track, Mapping)])
        for track_index, track in enumerate(tracks, start=1):
            if not isinstance(track, Mapping):
                return ()
            recording = track.get("recording") or {}
            if not isinstance(recording, Mapping):
                return ()
            mbid = canonical_release_mbid(recording.get("id"))
            if not mbid:
                return ()
            release_track_mbid = (
                canonical_release_mbid(track.get("id")) or ""
            )
            global_position += 1
            track_position = _positive_int(track.get("position"), track_index)
            title = str(
                track.get("title") or recording.get("title") or ""
            ).strip()
            artist = (
                _artist_credit(track.get("artist-credit"))
                or _artist_credit(recording.get("artist-credit"))
                or album_artist
            )
            manifest.append(ResolvedTrack(
                position=global_position,
                medium_position=medium_position,
                track_position=track_position,
                track_number=str(
                    track.get("number") or track_position
                ).strip(),
                recording_mbid=mbid,
                title=title,
                artist=artist,
                date=release_date,
                track_total=track_total,
                disc_total=disc_total,
                release_track_mbid=release_track_mbid,
            ))
    return tuple(manifest)


def _primary_type(release: Mapping[str, Any]) -> str:
    group = release.get("release-group") or {}
    if not isinstance(group, Mapping):
        return ""
    return str(
        group.get("primary-type") or group.get("type") or ""
    ).strip()


def _release_format(release: Mapping[str, Any]) -> str:
    media = release.get("medium-list") or release.get("media") or []
    if not isinstance(media, list):
        return ""
    formats: List[str] = []
    for medium in media:
        if not isinstance(medium, Mapping):
            continue
        value = str(medium.get("format") or "").strip()
        if value and value not in formats:
            formats.append(value)
    return " + ".join(formats)


def _release_label(release: Mapping[str, Any]) -> Tuple[str, str]:
    label_info = release.get("label-info-list") or []
    if not isinstance(label_info, list):
        return "", ""
    for entry in label_info:
        if not isinstance(entry, Mapping):
            continue
        label = entry.get("label") or {}
        label_name = (
            str(label.get("name") or "").strip()
            if isinstance(label, Mapping)
            else ""
        )
        catalog_number = str(entry.get("catalog-number") or "").strip()
        if label_name or catalog_number:
            return label_name, catalog_number
    return "", ""


def _release_from_payload(release: Mapping[str, Any]) -> Optional[ResolvedAlbum]:
    mbid = canonical_release_mbid(release.get("id"))
    title = str(release.get("title") or "").strip()
    artist = _artist_credit(release.get("artist-credit"))
    if not mbid or not title or not artist:
        return None
    label, catalog_number = _release_label(release)
    release_date = str(release.get("date") or "").strip()
    tracks = _track_manifest(release, artist, release_date)
    return ResolvedAlbum(
        release_mbid=mbid,
        title=title,
        artist=artist,
        track_count=_track_count(release),
        date=release_date,
        country=str(release.get("country") or "").strip(),
        status=str(release.get("status") or "").strip(),
        disambiguation=str(release.get("disambiguation") or "").strip(),
        primary_type=_primary_type(release),
        recording_mbids=tuple(track.recording_mbid for track in tracks),
        format=_release_format(release),
        label=label,
        catalog_number=catalog_number,
        barcode=str(release.get("barcode") or "").strip(),
        tracks=tracks,
    )


def _search_fields(query: str) -> Dict[str, str]:
    """Turn the common ``Artist - Album`` form into fielded MB search."""
    split = _SEARCH_SPLIT_RE.split(query, maxsplit=1)
    if len(split) == 2 and all(part.strip() for part in split):
        return {
            "artist": split[0].strip(),
            "release": split[1].strip(),
            "primarytype": "album",
        }
    return {"primarytype": "album"}


def search_album_releases(
    raw_query: Any,
    *,
    search_releases: SearchReleases,
    limit: int = MAX_SEARCH_RESULTS,
) -> AlbumRequestResult:
    query = str(raw_query or "").strip()
    if len(query) < 2:
        return AlbumRequestResult(
            {"success": False, "message": "Enter at least 2 characters"},
            400,
        )

    bounded_limit = max(1, min(MAX_SEARCH_RESULTS, int(limit)))
    fields = _search_fields(query)
    if "release" in fields:
        response = search_releases(limit=bounded_limit, **fields)
    else:
        response = search_releases(query=query, limit=bounded_limit, **fields)

    raw_releases = response.get("release-list") or response.get("releases") or []
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for raw in raw_releases if isinstance(raw_releases, list) else []:
        if not isinstance(raw, Mapping):
            continue
        album = _release_from_payload(raw)
        if album is None or album.release_mbid in seen:
            continue
        # The fielded query normally enforces this, but filter defensively in
        # case MusicBrainz returns a loosely-related single or EP.
        if album.primary_type and album.primary_type.casefold() != "album":
            continue
        candidate = album.as_dict()
        try:
            candidate["score"] = int(raw.get("ext:score") or raw.get("score") or 0)
        except (TypeError, ValueError):
            candidate["score"] = 0
        candidates.append(candidate)
        seen.add(album.release_mbid)

    return AlbumRequestResult({
        "success": True,
        "query": query,
        "ambiguous": len(candidates) > 1,
        "candidates": candidates,
    })


def _allowed_primary_types(values: Sequence[str]) -> frozenset[str]:
    if isinstance(values, str):
        values = (values,)
    normalized = frozenset(
        str(value or "").strip().casefold()
        for value in values
        if str(value or "").strip()
    )
    if not normalized:
        raise ValueError("allowed_primary_types cannot be empty")
    unsupported = normalized - EXACT_RELEASE_PRIMARY_TYPES
    if unsupported:
        raise ValueError(
            "unsupported exact release primary type(s): "
            + ", ".join(sorted(unsupported))
        )
    return normalized


def resolve_exact_release(
    raw_release_mbid: Any,
    *,
    get_release_by_id: GetRelease,
    allowed_primary_types: Sequence[str] = ("Album", "EP", "Single"),
    require_official: bool = True,
) -> Union[ResolvedAlbum, AlbumRequestResult]:
    """Resolve one exact MusicBrainz release and validate its full manifest.

    Exact operational migrations can safely include official albums, EPs, and
    singles.  The normal browser request remains album-only through
    :func:`resolve_album_release` below.  Callers that deliberately handle a
    non-official release must opt out explicitly.
    """
    allowed_types = _allowed_primary_types(allowed_primary_types)
    mbid = canonical_release_mbid(raw_release_mbid)
    if not mbid:
        return AlbumRequestResult(
            {"success": False, "message": "A valid release_mbid is required"},
            400,
        )
    response = get_release_by_id(
        mbid,
        includes=["artists", "release-groups", "recordings"],
    )
    release = response.get("release") if isinstance(response, Mapping) else None
    album = _release_from_payload(release) if isinstance(release, Mapping) else None
    if album is None or album.release_mbid != mbid:
        return AlbumRequestResult(
            {"success": False, "message": "MusicBrainz release was incomplete"},
            502,
        )
    if album.primary_type.casefold() not in allowed_types:
        if allowed_types == {"album"}:
            message = "The selected release is not an album"
        else:
            message = "The selected release type is not allowed"
        return AlbumRequestResult(
            {"success": False, "message": message},
            400,
        )
    if require_official and album.status.casefold() != "official":
        return AlbumRequestResult(
            {"success": False, "message": "The selected release is not official"},
            400,
        )
    if album.track_count <= 0:
        return AlbumRequestResult(
            {"success": False, "message": "MusicBrainz has no track list for this release"},
            409,
        )
    if len(album.tracks) != album.track_count:
        return AlbumRequestResult(
            {
                "success": False,
                "message": (
                    "MusicBrainz did not return a complete recording manifest "
                    "for this release"
                ),
            },
            502,
        )
    if any(
        not track.title
        or not track.artist
        or track.position <= 0
        or track.medium_position <= 0
        or track.track_position <= 0
        or track.track_total <= 0
        or track.disc_total <= 0
        or track.track_position > track.track_total
        or track.medium_position > track.disc_total
        or not track.track_number
        or not track.release_track_mbid
        for track in album.tracks
    ):
        return AlbumRequestResult(
            {
                "success": False,
                "message": (
                    "MusicBrainz did not return complete title and ordering "
                    "metadata for this release"
                ),
            },
            502,
        )
    if tuple(track.position for track in album.tracks) != tuple(
        range(1, album.track_count + 1)
    ):
        return AlbumRequestResult(
            {
                "success": False,
                "message": "MusicBrainz returned an unsafe release track order",
            },
            502,
        )
    if len(set(album.recording_mbids)) != len(album.recording_mbids):
        return AlbumRequestResult(
            {
                "success": False,
                "message": (
                    "This release repeats a MusicBrainz recording and cannot "
                    "be represented safely by the current library catalog"
                ),
            },
            409,
        )
    return album


def resolve_album_release(
    raw_release_mbid: Any,
    *,
    get_release_by_id: GetRelease,
) -> Union[ResolvedAlbum, AlbumRequestResult]:
    """Resolve the album-only release type accepted by the public web flow."""
    return resolve_exact_release(
        raw_release_mbid,
        get_release_by_id=get_release_by_id,
        allowed_primary_types=("Album",),
        # Preserve existing web behavior: exact release requests historically
        # accepted MusicBrainz statuses other than Official.
        require_official=False,
    )


def _manifest_signature(
    tracks: Sequence[Mapping[str, Any]],
) -> Tuple[Tuple[Any, ...], ...]:
    signature: List[Tuple[Any, ...]] = []
    for fallback_position, track in enumerate(tracks, start=1):
        if not isinstance(track, Mapping):
            signature.append((
                fallback_position,
                canonical_release_mbid(track) or "",
                1,
                fallback_position,
                str(fallback_position),
                "",
                "",
                "",
                0,
                0,
                "",
            ))
            continue
        try:
            position = int(track.get("position") or 0)
            medium_position = int(track.get("medium_position") or 0)
            track_position = int(track.get("track_position") or 0)
            track_total = int(track.get("track_total") or 0)
            disc_total = int(track.get("disc_total") or 0)
        except (TypeError, ValueError):
            position = medium_position = track_position = 0
            track_total = disc_total = 0
        signature.append((
            position or fallback_position,
            canonical_release_mbid(track.get("recording_mbid")) or "",
            medium_position,
            track_position,
            str(track.get("track_number") or ""),
            str(track.get("title") or ""),
            str(track.get("artist") or ""),
            str(track.get("date") or ""),
            track_total,
            disc_total,
            canonical_release_mbid(track.get("release_track_mbid")) or "",
        ))
    return tuple(signature)


def _stored_track_manifest(
    db: AlbumRequestStore,
    request_id: int,
    row: Mapping[str, Any],
) -> Tuple[Dict[str, Any], ...]:
    getter = getattr(db, "get_album_download_request_track_manifest", None)
    if callable(getter):
        raw = getter(request_id)
        if isinstance(raw, (list, tuple)):
            tracks = tuple(
                dict(track) for track in raw if isinstance(track, Mapping)
            )
            if tracks:
                return tracks

    # Compatibility for small test doubles and pre-manifest store adapters.
    recording_mbids = db.get_album_download_request_recording_mbids(request_id)
    return ResolvedAlbum(
        release_mbid=str(row.get("release_mbid") or ""),
        title=str(row.get("title") or ""),
        artist=str(row.get("artist") or ""),
        track_count=len(recording_mbids),
        recording_mbids=tuple(recording_mbids),
    ).track_manifest()


@dataclass(frozen=True)
class ReleaseInventory:
    completed_tracks: int
    total_tracks: int
    exact: bool
    missing_recordings: Tuple[str, ...]
    unexpected_recordings: Tuple[str, ...]
    inaccessible_recordings: Tuple[str, ...]
    metadata_mismatches: Tuple[str, ...] = ()
    invalid_manifest: bool = False


@dataclass(frozen=True)
class FlacReleaseTrackIdentity:
    recording_mbid: Optional[str]
    release_mbid: Optional[str]
    title: str
    artist: str
    date: str
    track_position: int
    medium_position: int
    track_total: int = 0
    disc_total: int = 0
    release_track_mbid: Optional[str] = None


def _tag_position(value: Any) -> int:
    match = re.match(r"^\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def read_flac_release_track_identity(
    path: str,
) -> Optional[FlacReleaseTrackIdentity]:
    """Read exact release/track identity fields from a real FLAC file."""
    if not str(path or "").lower().endswith(".flac"):
        return None
    try:
        from mutagen import MutagenError
        from mutagen.flac import FLAC

        audio = FLAC(path)
    except (OSError, ValueError, TypeError, MutagenError):
        return None

    def first(*keys: str) -> str:
        for key in keys:
            values = audio.get(key)
            if values:
                return str(values[0]).strip()
        return ""

    return FlacReleaseTrackIdentity(
        recording_mbid=canonical_release_mbid(
            first("musicbrainz_trackid", "musicbrainz_recordingid")
        ),
        release_mbid=canonical_release_mbid(first("musicbrainz_albumid")),
        title=first("title"),
        artist=first("artist"),
        date=first("date"),
        track_position=_tag_position(first("tracknumber")),
        medium_position=_tag_position(first("discnumber")),
        track_total=_tag_position(first("tracktotal", "totaltracks")),
        disc_total=_tag_position(first("disctotal", "totaldiscs")),
        release_track_mbid=canonical_release_mbid(
            first("musicbrainz_releasetrackid")
        ),
    )


def read_flac_musicbrainz_identity(
    path: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Read and validate recording/release IDs from a real FLAC file."""
    identity = read_flac_release_track_identity(path)
    if identity is None:
        return None, None
    return identity.recording_mbid, identity.release_mbid


def _path_is_safe_library_flac(
    path: str,
    music_library_dir: Optional[str],
) -> bool:
    try:
        path_stat = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return False
    if not str(path).lower().endswith(".flac"):
        return False
    if music_library_dir:
        try:
            resolved_path = os.path.realpath(os.path.abspath(path))
            resolved_root = os.path.realpath(os.path.abspath(music_library_dir))
            if os.path.normcase(os.path.commonpath([resolved_path, resolved_root])) != (
                os.path.normcase(resolved_root)
            ):
                return False
        except (OSError, ValueError):
            return False
    return True


def inspect_release_inventory(
    db: AlbumRequestStore,
    release_mbid: str,
    expected_tracks: Sequence[Any],
    music_library_dir: Optional[str] = None,
) -> ReleaseInventory:
    """Verify recording membership plus canonical release-track metadata.

    A sequence of strings retains the legacy identity-only check for internal
    compatibility. Persistent album requests pass mapping rows and therefore
    require a complete, unique positional manifest and exact embedded/database
    title, artist, date, track, and disc metadata.
    """
    metadata_required = any(
        isinstance(track, ResolvedTrack)
        and bool(
            track.title
            or track.track_total
            or track.disc_total
            or track.release_track_mbid
        )
        or isinstance(track, Mapping)
        and bool(
            str(track.get("title") or "").strip()
            or _positive_int(track.get("track_total"), 0)
            or _positive_int(track.get("disc_total"), 0)
            or str(track.get("release_track_mbid") or "").strip()
        )
        for track in expected_tracks
    )
    manifest: List[Dict[str, Any]] = []
    invalid_manifest = False
    for fallback_position, raw_track in enumerate(expected_tracks, start=1):
        if isinstance(raw_track, ResolvedTrack):
            track = raw_track.as_dict()
        elif isinstance(raw_track, Mapping):
            track = dict(raw_track)
        else:
            track = {"recording_mbid": raw_track}
        recording_mbid = canonical_release_mbid(track.get("recording_mbid"))
        try:
            position = int(track.get("position") or fallback_position)
            medium_position = int(track.get("medium_position") or 0)
            track_position = int(track.get("track_position") or 0)
        except (TypeError, ValueError):
            position = medium_position = track_position = 0
        normalized = {
            "position": position,
            "recording_mbid": recording_mbid or "",
            "medium_position": medium_position,
            "track_position": track_position,
            "track_number": str(track.get("track_number") or ""),
            "title": str(track.get("title") or ""),
            "artist": str(track.get("artist") or ""),
            "date": str(track.get("date") or ""),
            "track_total": _positive_int(track.get("track_total"), 0),
            "disc_total": _positive_int(track.get("disc_total"), 0),
            "release_track_mbid": (
                canonical_release_mbid(track.get("release_track_mbid")) or ""
            ),
        }
        if metadata_required and (
            not recording_mbid
            or position != fallback_position
            or medium_position <= 0
            or track_position <= 0
            or not normalized["track_number"]
            or not normalized["title"]
            or not normalized["artist"]
            or normalized["track_total"] <= 0
            or normalized["disc_total"] <= 0
            or track_position > normalized["track_total"]
            or medium_position > normalized["disc_total"]
            or not normalized["release_track_mbid"]
        ):
            invalid_manifest = True
        manifest.append(normalized)

    expected = tuple(track["recording_mbid"] for track in manifest)
    expected_counts = Counter(expected)
    expected_set = {value for value in expected if value}
    invalid_expected = "" in expected_counts
    if metadata_required and any(count != 1 for count in expected_counts.values()):
        # The catalog has one row per recording MBID, so repeated recordings
        # cannot safely represent distinct release positions.
        invalid_manifest = True
    expected_by_recording = {
        track["recording_mbid"]: track
        for track in manifest
        if track["recording_mbid"]
    }

    actual_ids = set()
    accessible_ids = set()
    invalid_actual: List[str] = []
    metadata_mismatches = set()
    for row in db.get_local_release_recordings(release_mbid):
        actual = canonical_release_mbid(row.get("mbid"))
        if not actual:
            invalid_actual.append(str(row.get("mbid") or ""))
            continue
        actual_ids.add(actual)
        path = str(row.get("local_path") or "")
        if not path or not _path_is_safe_library_flac(
            path,
            music_library_dir,
        ):
            continue
        embedded = read_flac_release_track_identity(path)
        if (
            embedded is None
            or embedded.recording_mbid != actual
            or embedded.release_mbid != canonical_release_mbid(release_mbid)
        ):
            continue
        if metadata_required and actual in expected_by_recording:
            expected_track = expected_by_recording[actual]
            row_matches = (
                str(row.get("title") or "") == expected_track["title"]
                and str(row.get("artist") or "") == expected_track["artist"]
                and _positive_int(row.get("track_number"), 0)
                == expected_track["track_position"]
                and _positive_int(row.get("disc_number"), 0)
                == expected_track["medium_position"]
            )
            tags_match = (
                embedded.title == expected_track["title"]
                and embedded.artist == expected_track["artist"]
                and embedded.date == expected_track["date"]
                and embedded.track_position == expected_track["track_position"]
                and embedded.medium_position == expected_track["medium_position"]
                and embedded.track_total == expected_track["track_total"]
                and embedded.disc_total == expected_track["disc_total"]
                and (
                    not expected_track["release_track_mbid"]
                    or embedded.release_track_mbid
                    == expected_track["release_track_mbid"]
                )
            )
            if not row_matches or not tags_match:
                metadata_mismatches.add(actual)
                continue
        accessible_ids.add(actual)

    missing = tuple(sorted(expected_set - actual_ids))
    unexpected = tuple(sorted((actual_ids - expected_set) | set(invalid_actual)))
    inaccessible = tuple(
        sorted(expected_set - accessible_ids - metadata_mismatches)
    )
    completed = sum(
        count
        for recording_mbid, count in expected_counts.items()
        if recording_mbid and recording_mbid in accessible_ids
    )
    exact = bool(expected) and not (
        invalid_expected
        or invalid_manifest
        or missing
        or unexpected
        or inaccessible
        or metadata_mismatches
    )
    return ReleaseInventory(
        completed_tracks=completed,
        total_tracks=len(expected),
        exact=exact,
        missing_recordings=missing,
        unexpected_recordings=unexpected,
        inaccessible_recordings=inaccessible,
        metadata_mismatches=tuple(sorted(metadata_mismatches)),
        invalid_manifest=invalid_manifest,
    )


def _public_request(row: Mapping[str, Any]) -> Dict[str, Any]:
    request = {
        "id": int(row["id"]),
        "release_mbid": str(row.get("release_mbid") or ""),
        "artist": str(row.get("artist") or ""),
        "title": str(row.get("title") or ""),
        "track_count": int(row.get("track_count") or 0),
        "stage": str(row.get("stage") or "queued"),
        "detail": str(row.get("detail") or ""),
        "completed_tracks": int(row.get("completed_tracks") or 0),
        "queue_status": row.get("queue_status"),
        "last_attempt": row.get("last_attempt"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }
    request["cover_url"] = (
        "https://coverartarchive.org/release/"
        f"{request['release_mbid']}/front-250"
    )
    return request


def _inventory_failure_detail(inventory: ReleaseInventory) -> str:
    reasons = []
    if inventory.missing_recordings:
        reasons.append(f"{len(inventory.missing_recordings)} recording(s) missing")
    if inventory.unexpected_recordings:
        reasons.append(
            f"{len(inventory.unexpected_recordings)} unexpected recording(s)"
        )
    if inventory.inaccessible_recordings:
        reasons.append(
            f"{len(inventory.inaccessible_recordings)} file(s) missing or unsafe"
        )
    if inventory.metadata_mismatches:
        reasons.append(
            f"{len(inventory.metadata_mismatches)} track(s) have mismatched metadata"
        )
    if inventory.invalid_manifest:
        reasons.append("the stored track manifest is incomplete or unsafe")
    return (
        "Exact MusicBrainz release check failed: " + ", ".join(reasons)
        if reasons
        else "Exact MusicBrainz release check failed"
    )


def queue_album_request(
    db: AlbumRequestStore,
    album: ResolvedAlbum,
    *,
    item_factory: ItemFactory,
    music_library_dir: Optional[str] = None,
) -> AlbumRequestResult:
    album_manifest = album.track_manifest()
    existing = db.get_album_download_request_by_release(album.release_mbid)
    if existing is not None:
        stored_manifest = _stored_track_manifest(
            db,
            int(existing["id"]),
            existing,
        )
        identity_changed = (
            str(existing.get("artist") or "") != album.artist
            or str(existing.get("title") or "") != album.title
            or int(existing.get("track_count") or 0) != album.track_count
            or _manifest_signature(stored_manifest)
            != _manifest_signature(album_manifest)
        )
        if identity_changed:
            current_stage = str(existing.get("stage") or "").casefold()
            queue_item_id = existing.get("queue_item_id")
            if queue_item_id is not None and current_stage not in {
                "failed",
                "success",
            }:
                return AlbumRequestResult(
                    {
                        "success": False,
                        "message": (
                            "MusicBrainz changed this release while its "
                            "existing download request is active; wait for "
                            "that attempt to finish before replacing the "
                            "manifest"
                        ),
                    },
                    409,
                )
            replaced = db.replace_album_download_request_identity(
                int(existing["id"]),
                artist=album.artist,
                title=album.title,
                track_count=album.track_count,
                recording_mbids=album.recording_mbids,
                track_manifest=album_manifest,
                detail=(
                    "MusicBrainz corrected this release; the exact manifest "
                    "must be downloaded again"
                ),
            )
            if not replaced:
                return AlbumRequestResult(
                    {
                        "success": False,
                        "message": (
                            "The existing album request changed while its "
                            "MusicBrainz identity was being refreshed"
                        ),
                    },
                    409,
                )
            existing = db.get_album_download_request(int(existing["id"])) or existing
        current = album_request_status(
            db,
            int(existing["id"]),
            music_library_dir=music_library_dir,
        )
        current_row = db.get_album_download_request(int(existing["id"])) or existing
        if str(current_row.get("stage") or "") == "failed":
            query = f"{ALBUM_QUERY_PREFIX} {album.artist} - {album.title}"
            detail = "Retry waiting for the master download queue"
            completed_tracks = min(
                inspect_release_inventory(
                    db,
                    album.release_mbid,
                    album_manifest,
                    music_library_dir,
                ).completed_tracks,
                album.track_count,
            )
            queue_item_id = db.create_download_and_requeue_album_request(
                request_id=int(current_row["id"]),
                release_mbid=album.release_mbid,
                search_query=query,
                playlist_id=ALBUM_PLAYLIST_ID,
                detail=detail,
                completed_tracks=completed_tracks,
            )
            if queue_item_id is None:
                return AlbumRequestResult(
                    {
                        "success": False,
                        "message": (
                            "The failed album request changed before a fresh "
                            "canonical retry could be created"
                        ),
                    },
                    409,
                )
            retried = db.get_album_download_request(int(current_row["id"]))
            if retried is None:
                return AlbumRequestResult(
                    {"success": False, "message": "Retry tracker was not persisted"},
                    500,
                )
            return AlbumRequestResult({
                "success": True,
                "queued": True,
                "message": "retry queued",
                "request": _public_request(retried),
            })
        return AlbumRequestResult({
            "success": True,
            "queued": False,
            "message": "already requested",
            "request": current.payload.get("request", _public_request(current_row)),
        })

    query = f"{ALBUM_QUERY_PREFIX} {album.artist} - {album.title}"
    inventory = inspect_release_inventory(
        db,
        album.release_mbid,
        album_manifest,
        music_library_dir,
    )
    local_count = inventory.completed_tracks
    queue_item_id: Optional[int] = None
    request_id: Optional[int] = None
    stage = "success" if inventory.exact else "queued"
    detail = (
        "Album already present in the master library"
        if stage == "success"
        else "Waiting for the master download queue"
    )

    if stage != "success":
        try:
            queue_item_id, request_id = db.create_download_and_album_request(
                release_mbid=album.release_mbid,
                search_query=query,
                playlist_id=ALBUM_PLAYLIST_ID,
                artist=album.artist,
                title=album.title,
                track_count=album.track_count,
                detail=detail,
                completed_tracks=min(local_count, album.track_count),
                recording_mbids=album.recording_mbids,
                track_manifest=album_manifest,
            )
        except Exception:
            winner = db.get_album_download_request_by_release(album.release_mbid)
            if winner is not None:
                return AlbumRequestResult({
                    "success": True,
                    "queued": False,
                    "message": "already requested",
                    "request": _public_request(winner),
                })
            raise

    try:
        if request_id is None:
            request_id = db.create_album_download_request(
                queue_item_id=queue_item_id,
                release_mbid=album.release_mbid,
                artist=album.artist,
                title=album.title,
                track_count=album.track_count,
                stage=stage,
                detail=detail,
                completed_tracks=min(local_count, album.track_count),
                recording_mbids=album.recording_mbids,
                track_manifest=album_manifest,
            )
    except Exception:
        winner = db.get_album_download_request_by_release(album.release_mbid)
        if winner is not None:
            return AlbumRequestResult({
                "success": True,
                "queued": False,
                "message": "already requested",
                "request": _public_request(winner),
            })
        raise

    row = db.get_album_download_request(request_id)
    if row is None:
        return AlbumRequestResult(
            {"success": False, "message": "Request tracker was not persisted"},
            500,
        )
    return AlbumRequestResult({
        "success": True,
        "queued": stage != "success",
        "message": "queued" if stage != "success" else "already in library",
        "request": _public_request(row),
    })


def album_request_status(
    db: AlbumRequestStore,
    request_id: int,
    *,
    music_library_dir: Optional[str] = None,
) -> AlbumRequestResult:
    row = db.get_album_download_request(request_id)
    if row is None:
        return AlbumRequestResult(
            {"success": False, "message": "Album request not found"},
            404,
        )

    expected_track_manifest = _stored_track_manifest(
        db,
        request_id,
        row,
    )
    inventory = inspect_release_inventory(
        db,
        str(row.get("release_mbid") or ""),
        expected_track_manifest,
        music_library_dir,
    )
    expected = inventory.total_tracks
    local_count = inventory.completed_tracks
    stage = str(row.get("stage") or "queued")
    if (
        inventory.exact
        and stage != "success"
        and row.get("queue_status") is None
    ):
        # The downloader owns terminalization while its queue row exists. A
        # status GET must not race the importer after the expected tracks have
        # appeared but before it has rejected a later wrong/incomplete file.
        db.complete_album_download_request_by_id(
            request_id,
            "All MusicBrainz tracks are present in the master library",
            expected,
        )
        row = db.get_album_download_request(request_id) or row
    elif stage == "success" and not inventory.exact:
        db.invalidate_album_download_request(
            request_id,
            _inventory_failure_detail(inventory),
            local_count,
        )
        row = db.get_album_download_request(request_id) or row
    elif row.get("queue_status") == "failed" and stage != "failed":
        queue_item_id = row.get("queue_item_id")
        if queue_item_id is not None:
            db.update_album_download_request_progress(
                int(queue_item_id),
                "failed",
                "The master download attempt failed",
                min(local_count, expected) if expected > 0 else local_count,
            )
            row = db.get_album_download_request(request_id) or row
    elif (
        row.get("queue_item_id") is not None
        and row.get("queue_status") is None
        and stage in {"queued", "downloading"}
    ):
        db.update_album_download_request_progress(
            int(row["queue_item_id"]),
            "failed",
            "The master queue item ended before the album was imported",
            min(local_count, expected) if expected > 0 else local_count,
        )
        row = db.get_album_download_request(request_id) or row
    elif (
        stage == "importing"
        and row.get("queue_status") is None
        and _request_age_seconds(row) >= LIDARR_IMPORT_STALE_SECONDS
    ):
        db.invalidate_album_download_request(
            request_id,
            "Lidarr did not import the verified release before the handoff expired",
            min(local_count, expected) if expected > 0 else local_count,
        )
        row = db.get_album_download_request(request_id) or row

    return AlbumRequestResult({
        "success": True,
        "request": _public_request(row),
    })


def _request_age_seconds(row: Mapping[str, Any]) -> float:
    raw = str(row.get("updated_at") or "").strip()
    if not raw:
        return 0.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def list_album_requests(
    db: AlbumRequestStore,
    *,
    music_library_dir: Optional[str] = None,
    limit: int = MAX_TRACKED_REQUESTS,
) -> AlbumRequestResult:
    """Return server-persistent active work for browser reconciliation."""
    bounded_limit = max(1, min(MAX_TRACKED_REQUESTS, int(limit)))
    requests_payload: List[Dict[str, Any]] = []
    for row in db.list_active_album_download_requests(bounded_limit):
        result = album_request_status(
            db,
            int(row["id"]),
            music_library_dir=music_library_dir,
        )
        if result.status_code == 200 and result.payload.get("request"):
            current = result.payload["request"]
            if current.get("stage") in {
                "queued",
                "downloading",
                "importing",
                "failed",
            }:
                requests_payload.append(current)
    return AlbumRequestResult({"success": True, "requests": requests_payload})


def forward_master_json(
    master_url: Any,
    method: str,
    path: str,
    *,
    api_token: Any = "",
    params: Optional[Mapping[str, Any]] = None,
    json_body: Optional[Mapping[str, Any]] = None,
    http_request: HttpRequest = requests.request,
) -> AlbumRequestResult:
    base = str(master_url or "").strip().rstrip("/")
    if not base:
        return AlbumRequestResult(
            {"success": False, "message": "master_url not configured"},
            409,
        )
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return AlbumRequestResult(
            {"success": False, "message": "master_url must be a plain HTTP(S) origin"},
            409,
        )
    headers = {"Accept": "application/json"}
    token = str(api_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = http_request(
            method.upper(),
            f"{base}{path}",
            params=dict(params or {}),
            json=dict(json_body) if json_body is not None else None,
            headers=headers,
            timeout=(5, 45),
        )
    except requests.RequestException as exc:
        logger.warning(
            "Album request proxy to %s://%s failed (%s)",
            parsed.scheme,
            parsed.netloc,
            type(exc).__name__,
        )
        return AlbumRequestResult(
            {"success": False, "message": "master unreachable"},
            502,
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {
            "success": False,
            "message": f"master returned non-JSON ({response.status_code})",
        }
    if not isinstance(payload, dict):
        payload = {
            "success": False,
            "message": "master returned an invalid JSON payload",
        }
    return AlbumRequestResult(payload, int(response.status_code))
