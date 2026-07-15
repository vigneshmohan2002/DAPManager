"""Album-completion and audit-queue policy outside Flask presentation.

The HTTP/background adapters retain readiness checks, request parsing,
``jsonify``, and exception translation.  This module owns the two stateful
album workflows while accepting database construction and domain operations
as typed dependencies.  Keeping those dependencies injectable lets the web
wrapper preserve its established monkeypatch points.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    TypedDict,
)

from src.contracts import ProgressCallback


class AlbumCompletionSummary(TypedDict, total=False):
    """Fields consumed by the post-completion download policy."""

    albums_discovered: int
    incomplete_albums: int
    already_complete: int
    tracks_queued: int
    albums_skipped_existing: int
    errors: int
    details: List[Dict[str, Any]]


class AuditQueuePayload(TypedDict):
    success: bool
    queued_count: int


class DatabaseContextFactory(Protocol):
    """Create one independently owned database context."""

    def __call__(self, db_path: str) -> AbstractContextManager[Any]: ...


class CompleteAlbumsOperation(Protocol):
    def __call__(
        self,
        db: Any,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> AlbumCompletionSummary: ...


class DownloadOperation(Protocol):
    def __call__(
        self,
        db: Any,
        config_values: Mapping[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Any: ...


class ScanOperation(Protocol):
    def __call__(
        self,
        db: Any,
        config_values: Mapping[str, Any],
    ) -> Any: ...


class AuditQueueStore(Protocol):
    def queue_download(self, item: Any) -> int: ...


class DownloadItemFactory(Protocol):
    def __call__(
        self,
        *,
        search_query: str,
        playlist_id: str,
        mbid_guess: Any,
        status: str,
    ) -> Any: ...


@dataclass(frozen=True)
class AlbumCompletionResult:
    """Internal outcome; background wrappers may intentionally discard it."""

    summary: AlbumCompletionSummary
    downloads_run: bool
    rescan_run: bool


@dataclass(frozen=True)
class AuditQueueResult:
    """JSON-shaped outcome translated by the Flask adapter."""

    payload: AuditQueuePayload


def run_album_completion_pipeline(
    *,
    db_path: str,
    config_values: Mapping[str, Any],
    run_downloads: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
    database_factory: DatabaseContextFactory,
    complete_albums: CompleteAlbumsOperation,
    run_downloader: DownloadOperation,
    scan_library: ScanOperation,
) -> AlbumCompletionResult:
    """Complete albums, then conditionally download and rescan.

    Each phase deliberately owns a fresh database context.  Progress events
    occur between contexts exactly as they did in the web wrapper, and domain
    exceptions are allowed to propagate to ``TaskManager`` unchanged.
    """
    with database_factory(db_path) as db:
        summary = complete_albums(
            db,
            progress_callback=progress_callback,
        )

    should_download = (
        run_downloads and summary.get("tracks_queued", 0) > 0
    )
    if not should_download:
        return AlbumCompletionResult(
            summary=summary,
            downloads_run=False,
            rescan_run=False,
        )

    if progress_callback:
        progress_callback({"message": "Downloading queued tracks..."})
    with database_factory(db_path) as db:
        run_downloader(
            db,
            config_values,
            progress_callback=progress_callback,
        )

    if progress_callback:
        progress_callback({"message": "Re-scanning library..."})
    with database_factory(db_path) as db:
        scan_library(db, config_values)

    return AlbumCompletionResult(
        summary=summary,
        downloads_run=True,
        rescan_run=True,
    )


def queue_audit_downloads(
    db: AuditQueueStore,
    *,
    items: Sequence[Mapping[str, Any]],
    queue_album: Any,
    album_info: Mapping[str, Any],
    item_factory: DownloadItemFactory,
) -> AuditQueueResult:
    """Queue either one full-album request or the supplied track requests.

    ``queue_album`` intentionally retains truthiness-based selection.  Album
    mode ignores ``items`` and keeps the ``::ALBUM::`` marker used by the
    downloader.  Track mode uses required mapping keys so malformed entries
    continue to fail at the same mutation boundary.
    """
    count = 0
    if queue_album:
        query = (
            f"::ALBUM:: {album_info.get('artist')} - "
            f"{album_info.get('album')}"
        )
        db.queue_download(
            item_factory(
                search_query=query,
                playlist_id="AUDIT",
                mbid_guess=album_info.get("release_mbid", ""),
                status="pending",
            )
        )
        count = 1
    else:
        for item in items:
            query = f"{item['artist']} - {item['title']}"
            db.queue_download(
                item_factory(
                    search_query=query,
                    playlist_id="AUDIT",
                    mbid_guess="",
                    status="pending",
                )
            )
            count += 1

    return AuditQueueResult(
        payload={"success": True, "queued_count": count}
    )
