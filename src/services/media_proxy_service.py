"""Master media transport kept independent of Flask request/response objects."""

from dataclasses import dataclass
import os
from typing import Dict, Iterable, Mapping, Optional, Sequence
from urllib.parse import quote

import requests


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
