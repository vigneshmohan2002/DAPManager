"""Media source policy and master transport independent of Flask objects."""

from dataclasses import dataclass
import logging
import os
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    TypeAlias,
    Union,
)
from urllib.parse import quote

import requests

from src.artwork_cache import CachedArtwork


logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024

COVER_REQUEST_HEADERS = (
    "Range",
    "If-Range",
    "If-None-Match",
    "If-Modified-Since",
)
COVER_RESPONSE_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Cache-Control",
    "ETag",
    "Last-Modified",
)
STREAM_RESPONSE_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
)


@dataclass(frozen=True)
class StreamingProxyResult:
    status_code: int
    headers: Dict[str, str]
    chunks: Iterable[bytes]


@dataclass(frozen=True)
class BufferedProxyResult:
    status_code: int
    content_type: str
    body: bytes


class AlbumCoverStore(Protocol):
    """Database facade needed to locate an album's embedded artwork."""

    def get_album_cover_path(self, album_id: str) -> Optional[str]: ...


class TrackSourceStore(Protocol):
    """Database facade needed to resolve a playable track source."""

    def get_track_sources(self, mbid: str) -> Optional[dict]: ...


class AlbumTracksRequester(Protocol):
    """Injectable master album-track transport."""

    def __call__(
        self,
        master_url: str,
        album_id: str,
        *,
        api_token: str = "",
    ) -> BufferedProxyResult: ...


class LocalAlbumTracksLoader(Protocol):
    """Lazy replica loader; it must not run for a usable master response."""

    def __call__(
        self,
        album_id: str,
        *,
        has_master: bool,
    ) -> Dict[str, Any]: ...


CoverExtractor: TypeAlias = Callable[
    [str], Optional[Tuple[bytes, str]]
]
CachedCoverLoader: TypeAlias = Callable[[str], Optional[CachedArtwork]]
FileExists: TypeAlias = Callable[[str], bool]
FileSource: TypeAlias = Literal["local", "drive"]


@dataclass(frozen=True)
class LocalAlbumCoverResolution:
    body: bytes
    content_type: str
    cache_control: str = "public, max-age=86400"


@dataclass(frozen=True)
class MasterAlbumCoverResolution:
    master_url: str
    api_token: str


@dataclass(frozen=True)
class MissingMediaResolution:
    status_code: int = 404


AlbumCoverResolution: TypeAlias = Union[
    LocalAlbumCoverResolution,
    MasterAlbumCoverResolution,
    MissingMediaResolution,
]


@dataclass(frozen=True)
class MasterAlbumTracksResolution:
    response: BufferedProxyResult


@dataclass(frozen=True)
class LocalAlbumTracksResolution:
    payload: Dict[str, Any]


AlbumTracksResolution: TypeAlias = Union[
    MasterAlbumTracksResolution,
    LocalAlbumTracksResolution,
]


@dataclass(frozen=True)
class FileStreamResolution:
    path: str
    content_type: str
    source: FileSource


@dataclass(frozen=True)
class MasterStreamResolution:
    master_url: str
    api_token: str


StreamResolution: TypeAlias = Union[
    FileStreamResolution,
    MasterStreamResolution,
    MissingMediaResolution,
]


def normalized_master_url(config_values: object) -> Optional[str]:
    """Return a concrete, trailing-slash-free master URL from runtime config."""
    if not isinstance(config_values, dict):
        return None
    master_url = (config_values.get("master_url") or "").strip().rstrip("/")
    return master_url or None


def configured_api_token(config_values: object) -> str:
    """Return the stripped bearer token only from a concrete config mapping."""
    if not isinstance(config_values, dict):
        return ""
    return (config_values.get("api_token") or "").strip()


def resolve_album_cover(
    db: AlbumCoverStore,
    album_id: str,
    *,
    config_values: object,
    extract_cover: CoverExtractor,
    load_cached_cover: Optional[CachedCoverLoader] = None,
) -> AlbumCoverResolution:
    """Prefer embedded art, then a complete cache entry, then the master."""
    cover_path = db.get_album_cover_path(album_id)
    if cover_path:
        extracted = extract_cover(cover_path)
        if extracted is not None:
            body, content_type = extracted
            return LocalAlbumCoverResolution(body, content_type)

    if load_cached_cover is not None:
        cached = load_cached_cover(album_id)
        if cached is not None:
            return LocalAlbumCoverResolution(
                cached.body,
                cached.content_type,
            )

    master_url = normalized_master_url(config_values)
    if master_url is not None:
        return MasterAlbumCoverResolution(
            master_url=master_url,
            api_token=configured_api_token(config_values),
        )
    return MissingMediaResolution()


def resolve_album_tracks(
    album_id: str,
    *,
    config_values: object,
    load_local_tracks: LocalAlbumTracksLoader,
    request_tracks: AlbumTracksRequester,
    event_logger: logging.Logger = logger,
) -> AlbumTracksResolution:
    """Use authoritative master rows, falling back only when it is unavailable.

    Every response below 500 is authoritative, including client errors.  The
    lazy local loader is therefore untouched for all usable upstream replies,
    avoiding both a database open and stale replica reads in the common path.
    """
    master_url = normalized_master_url(config_values)
    if master_url is not None:
        try:
            response = request_tracks(
                master_url,
                album_id,
                api_token=configured_api_token(config_values),
            )
        except requests.RequestException:
            event_logger.warning(
                "master album-track lookup unavailable for %s; using replica",
                album_id,
                exc_info=True,
            )
        else:
            if response.status_code < 500:
                return MasterAlbumTracksResolution(response)
            event_logger.warning(
                "master album-track lookup returned %s for %s; using replica",
                response.status_code,
                album_id,
            )

    return LocalAlbumTracksResolution(
        load_local_tracks(
            album_id,
            has_master=master_url is not None,
        )
    )


def resolve_stream_source(
    db: TrackSourceStore,
    mbid: str,
    *,
    config_values: object,
    file_exists: FileExists = os.path.isfile,
) -> StreamResolution:
    """Resolve audio in local, DAP, normalized-master, then missing order."""
    sources = db.get_track_sources(mbid)
    if sources is None:
        return MissingMediaResolution()

    local_path = (sources.get("local_path") or "").strip()
    if local_path and file_exists(local_path):
        return FileStreamResolution(
            path=local_path,
            content_type=guess_audio_mime(local_path),
            source="local",
        )

    dap_path = (sources.get("dap_path") or "").strip()
    if dap_path and file_exists(dap_path):
        return FileStreamResolution(
            path=dap_path,
            content_type=guess_audio_mime(dap_path),
            source="drive",
        )

    master_url = normalized_master_url(config_values)
    if master_url is not None:
        return MasterStreamResolution(
            master_url=master_url,
            api_token=configured_api_token(config_values),
        )
    return MissingMediaResolution()


def build_upstream_headers(
    incoming: Mapping[str, str],
    *,
    forwarded: Sequence[str] = (),
    api_token: str = "",
    defaults: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Select approved request headers and attach the configured bearer token."""
    headers = dict(defaults or {})
    for name in forwarded:
        if name in incoming:
            headers[name] = incoming[name]
    token = api_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _forward_response_headers(
    incoming: Mapping[str, str],
    allowed: Sequence[str],
) -> Dict[str, str]:
    return {
        name: incoming[name]
        for name in allowed
        if name in incoming
    }


def _iter_upstream(upstream: requests.Response) -> Iterable[bytes]:
    try:
        for chunk in upstream.iter_content(chunk_size=CHUNK_SIZE):
            if chunk:
                yield chunk
    finally:
        upstream.close()


def request_album_cover(
    master_url: str,
    album_id: str,
    *,
    incoming_headers: Mapping[str, str],
    api_token: str = "",
) -> StreamingProxyResult:
    """Request and normalize a streaming album-cover response."""
    headers = build_upstream_headers(
        incoming_headers,
        forwarded=COVER_REQUEST_HEADERS,
        api_token=api_token,
    )
    encoded_album_id = quote(album_id, safe="")
    upstream = requests.get(
        f"{master_url}/api/library/albums/{encoded_album_id}/cover",
        headers=headers,
        stream=True,
        timeout=(5, 30),
    )
    status_code = upstream.status_code
    if status_code >= 400:
        upstream.close()
        return StreamingProxyResult(status_code, {}, ())

    response_headers = _forward_response_headers(
        upstream.headers,
        COVER_RESPONSE_HEADERS,
    )
    response_headers.setdefault("Cache-Control", "public, max-age=86400")
    if status_code == 304:
        upstream.close()
        return StreamingProxyResult(status_code, response_headers, ())
    return StreamingProxyResult(
        status_code,
        response_headers,
        _iter_upstream(upstream),
    )


def request_album_tracks(
    master_url: str,
    album_id: str,
    *,
    api_token: str = "",
) -> BufferedProxyResult:
    """Request the master's ordered playable album rows and close eagerly."""
    headers = build_upstream_headers(
        {},
        api_token=api_token,
        defaults={"Accept": "application/json"},
    )
    upstream = requests.get(
        f"{master_url}/api/library/albums/{quote(album_id, safe='')}/tracks",
        headers=headers,
        timeout=(5, 30),
    )
    if upstream.status_code >= 500:
        status_code = upstream.status_code
        content_type = upstream.headers.get("Content-Type", "application/json")
        upstream.close()
        return BufferedProxyResult(status_code, content_type, b"")
    try:
        return BufferedProxyResult(
            upstream.status_code,
            upstream.headers.get("Content-Type", "application/json"),
            upstream.content,
        )
    finally:
        upstream.close()


def request_stream(
    master_url: str,
    mbid: str,
    *,
    incoming_headers: Mapping[str, str],
    api_token: str = "",
) -> StreamingProxyResult:
    """Request audio while preserving Range semantics and streaming cleanup."""
    headers = build_upstream_headers(
        incoming_headers,
        forwarded=("Range",),
        api_token=api_token,
    )
    upstream = requests.get(
        f"{master_url}/api/stream/{mbid}",
        headers=headers,
        stream=True,
        timeout=(5, 30),
    )
    status_code = upstream.status_code
    if status_code >= 400:
        upstream.close()
        return StreamingProxyResult(status_code, {}, ())
    return StreamingProxyResult(
        status_code,
        _forward_response_headers(upstream.headers, STREAM_RESPONSE_HEADERS),
        _iter_upstream(upstream),
    )


def guess_audio_mime(path: str) -> str:
    """Map the established audio extensions to response MIME types."""
    extension = os.path.splitext(path)[1].lower()
    return {
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
    }.get(extension, "application/octet-stream")
