"""Download queue and discovery policy independent of Flask presentation.

The route layer retains configuration readiness checks, database context
ownership, logging, and ``jsonify``.  This module owns validation,
classification, de-duplication, forwarding, queue item construction, and
Lidarr wanted-release enrichment while accepting all stateful collaborators as
typed dependencies.
"""

from dataclasses import dataclass
import logging
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

import requests


logger = logging.getLogger(__name__)

DownloadItemFactory = Callable[..., Any]
SuggestionPair = Tuple[str, str]


class DownloadTrack(Protocol):
    artist: str
    title: str
    local_path: Optional[str]


class DownloadDiscoveryStore(Protocol):
    """Database façade used across queue and release-discovery operations."""

    def is_download_queued(self, search_query: str) -> bool: ...

    def queue_download(self, item: Any) -> int: ...

    def get_track_by_mbid(self, mbid: str) -> Optional[DownloadTrack]: ...

    def get_sync_state(self, key: str) -> Optional[str]: ...

    def get_queued_release_mbids(self) -> Set[str]: ...

    def get_existing_release_mbids(self) -> Set[str]: ...


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> Any: ...


class WantedReleaseClient(Protocol):
    def get_wanted_missing(
        self, page: int = 1, page_size: int = 50
    ) -> List[dict]: ...


HttpPost = Callable[..., HttpResponse]
WantedClientFactory = Callable[[Mapping[str, Any]], Optional[WantedReleaseClient]]


@dataclass(frozen=True)
class DownloadDiscoveryResult:
    """Payload and status translated into an HTTP response by Flask."""

    payload: Any
    status_code: int = 200


@dataclass(frozen=True)
class PreparedDownloadRequest:
    search_query: str
    mbid_guess: str
    playlist_id: str


@dataclass(frozen=True)
class SuggestionBatch:
    received: int
    pairs: List[SuggestionPair]


@dataclass(frozen=True)
class SuggestionQueueCounts:
    queued: int
    skipped: int


@dataclass(frozen=True)
class WantedReleaseRecords:
    records: Sequence[Mapping[str, Any]]


PreparedDownload = Union[PreparedDownloadRequest, DownloadDiscoveryResult]
PreparedWanted = Union[WantedReleaseRecords, DownloadDiscoveryResult]


def validate_download_authority(is_master: bool) -> Optional[DownloadDiscoveryResult]:
    """Reject non-authority download requests before request-body parsing."""
    if is_master:
        return None
    return DownloadDiscoveryResult(
        {"success": False, "message": "This instance is not a master"},
        400,
    )


def prepare_download_request(data: Mapping[str, Any]) -> PreparedDownload:
    """Normalize the master-facing download request without touching the DB."""
    search_query = (data.get("search_query") or "").strip()
    if not search_query:
        return DownloadDiscoveryResult(
            {"success": False, "message": "search_query is required"},
            400,
        )
    mbid_guess = (data.get("mbid_guess") or "").strip()
    playlist_id = (
        (data.get("playlist_id") or "SATELLITE").strip() or "SATELLITE"
    )
    return PreparedDownloadRequest(
        search_query=search_query,
        mbid_guess=mbid_guess,
        playlist_id=playlist_id,
    )


def queue_download_request(
    db: DownloadDiscoveryStore,
    prepared: PreparedDownloadRequest,
    *,
    item_factory: DownloadItemFactory,
) -> DownloadDiscoveryResult:
    """Queue a new master request or return the established idempotent result."""
    if db.is_download_queued(prepared.search_query):
        return DownloadDiscoveryResult({
            "success": True,
            "queued": False,
            "message": "already queued",
        })

    item_id = db.queue_download(item_factory(
        search_query=prepared.search_query,
        playlist_id=prepared.playlist_id,
        mbid_guess=prepared.mbid_guess,
    ))
    return DownloadDiscoveryResult({
        "success": True,
        "queued": True,
        "item_id": item_id,
        "message": "queued",
    })


def build_suggestion_items(raw_items: Any) -> List[SuggestionPair]:
    """Normalize and case-insensitively de-duplicate suggestion queries."""
    seen: Set[str] = set()
    results: List[SuggestionPair] = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        query = (item.get("search_query") or "").strip()
        mbid = (item.get("mbid") or "").strip()
        if not query:
            artist = (item.get("artist") or "").strip()
            title = (item.get("title") or "").strip()
            if artist and title:
                query = f"{artist} - {title}"
            elif title:
                query = title
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append((query, mbid))
    return results


def prepare_suggestion_batch(raw_items: Any) -> SuggestionBatch:
    """Capture received count before queue mutation, matching the wire shape."""
    pairs = build_suggestion_items(raw_items)
    return SuggestionBatch(
        received=len(raw_items),
        pairs=pairs,
    )


def queue_suggestions(
    db: DownloadDiscoveryStore,
    batch: SuggestionBatch,
    *,
    item_factory: DownloadItemFactory,
) -> DownloadDiscoveryResult:
    """Queue normalized suggestions in input order, skipping existing work."""
    counts = queue_suggestion_pairs(
        db,
        batch.pairs,
        item_factory=item_factory,
    )
    return suggestion_queue_result(batch.received, counts)


def queue_suggestion_pairs(
    db: DownloadDiscoveryStore,
    pairs: Sequence[SuggestionPair],
    *,
    item_factory: DownloadItemFactory,
) -> SuggestionQueueCounts:
    """Apply only the database portion of suggestion queueing."""
    queued = 0
    skipped = 0
    for query, mbid in pairs:
        if db.is_download_queued(query):
            skipped += 1
            continue
        db.queue_download(item_factory(
            search_query=query,
            playlist_id="SUGGESTED",
            mbid_guess=mbid,
            status="pending",
        ))
        queued += 1
    return SuggestionQueueCounts(queued=queued, skipped=skipped)


def suggestion_queue_result(
    received: int,
    counts: SuggestionQueueCounts,
) -> DownloadDiscoveryResult:
    """Attach the raw received count after queue mutation, as before."""
    return DownloadDiscoveryResult({
        "success": True,
        "received": received,
        "queued": counts.queued,
        "skipped": counts.skipped,
    })


def normalize_forward_target(value: Any) -> Union[str, DownloadDiscoveryResult]:
    """Normalize a configured master URL or produce the existing 409 result."""
    master_url = (value or "").strip().rstrip("/")
    if master_url:
        return master_url
    return DownloadDiscoveryResult(
        {"success": False, "message": "master_url not configured"},
        409,
    )


def validate_forward_items(raw_items: Any) -> Optional[DownloadDiscoveryResult]:
    if isinstance(raw_items, list):
        return None
    return DownloadDiscoveryResult(
        {"success": False, "message": "body must be {'items': [...]}"},
        400,
    )


def forward_suggestions(
    master_url: str,
    raw_items: List[Any],
    *,
    api_token: str = "",
    http_post: HttpPost = requests.post,
) -> DownloadDiscoveryResult:
    """Forward suggestions with bearer auth and preserve the upstream result."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    token = api_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        upstream = http_post(
            f"{master_url}/api/suggestions",
            json={"items": raw_items},
            headers=headers,
            timeout=(5, 30),
        )
    except requests.RequestException as exc:
        logger.warning("Suggestion forward to master failed: %s", exc)
        return DownloadDiscoveryResult(
            {
                "success": False,
                "message": f"master unreachable: {exc}",
            },
            502,
        )

    try:
        payload = upstream.json()
    except ValueError:
        payload = {
            "success": False,
            "message": f"master returned non-JSON ({upstream.status_code})",
        }
    return DownloadDiscoveryResult(payload, upstream.status_code)


def validate_catalog_queue_body(
    data: Mapping[str, Any],
) -> Union[List[Any], DownloadDiscoveryResult]:
    mbids = data.get("mbids")
    if isinstance(mbids, list):
        return mbids
    return DownloadDiscoveryResult(
        {"success": False, "message": "body must be {'mbids': [...]}"},
        400,
    )


def queue_catalog_downloads(
    db: DownloadDiscoveryStore,
    mbids: List[Any],
    *,
    item_factory: DownloadItemFactory,
) -> DownloadDiscoveryResult:
    """Classify catalog rows and queue only playable, unlinked new work."""
    queued = 0
    queued_mbids: List[str] = []
    skipped_linked = 0
    skipped_queued = 0
    not_found = 0

    for raw in mbids:
        mbid = (raw or "").strip()
        if not mbid:
            not_found += 1
            continue
        track = db.get_track_by_mbid(mbid)
        if track is None:
            not_found += 1
            continue
        if track.local_path:
            skipped_linked += 1
            continue
        query = f"{track.artist or ''} - {track.title or ''}".strip(" -")
        if not query:
            not_found += 1
            continue
        if db.is_download_queued(query):
            skipped_queued += 1
            continue
        db.queue_download(item_factory(
            search_query=query,
            playlist_id="CATALOG",
            mbid_guess=mbid,
            status="pending",
        ))
        queued += 1
        queued_mbids.append(mbid)

    return DownloadDiscoveryResult({
        "success": True,
        "received": len(mbids),
        "queued": queued,
        "queued_mbids": queued_mbids,
        "skipped_linked": skipped_linked,
        "skipped_queued": skipped_queued,
        "not_found": not_found,
    })


def load_wanted_release_records(
    config_values: Mapping[str, Any],
    *,
    client_factory: WantedClientFactory,
    lidarr_error: Type[Exception],
) -> PreparedWanted:
    """Apply Lidarr enablement/availability/upstream-error policy."""
    disabled = validate_wanted_releases_enabled(config_values)
    if disabled is not None:
        return disabled

    return fetch_wanted_release_records(
        config_values,
        client_factory=client_factory,
        lidarr_error=lidarr_error,
    )


def validate_wanted_releases_enabled(
    config_values: Mapping[str, Any],
) -> Optional[DownloadDiscoveryResult]:
    if bool(config_values.get("lidarr_watch_enabled") or False):
        return None
    return DownloadDiscoveryResult(
        {"success": False, "reason": "lidarr_disabled"}
    )


def fetch_wanted_release_records(
    config_values: Mapping[str, Any],
    *,
    client_factory: WantedClientFactory,
    lidarr_error: Type[Exception],
) -> PreparedWanted:
    """Construct the configured client and classify its upstream outcome."""

    client = client_factory(config_values)
    if client is None:
        return DownloadDiscoveryResult(
            {"success": False, "reason": "lidarr_unavailable"}
        )
    try:
        records = client.get_wanted_missing(page=1, page_size=100)
    except lidarr_error as exc:
        return DownloadDiscoveryResult(
            {"success": False, "message": str(exc)},
            502,
        )
    return WantedReleaseRecords(
        cast(Sequence[Mapping[str, Any]], records)
    )


def wanted_releases_result(
    db: DownloadDiscoveryStore,
    records: Sequence[Mapping[str, Any]],
) -> DownloadDiscoveryResult:
    """Join wanted records to local queue/library state without reordering."""
    last_tick = db.get_sync_state("last_release_watch_tick")
    queued_mbids = db.get_queued_release_mbids()
    existing_mbids = db.get_existing_release_mbids()

    items: List[Dict[str, Any]] = []
    for record in records:
        mbid = record.get("foreignAlbumId") or ""
        artist = (record.get("artist") or {}).get("artistName") or ""
        title = record.get("title") or ""
        cover_url = ""
        for image in record.get("images") or []:
            if image.get("coverType") == "cover" and image.get("remoteUrl"):
                cover_url = image.get("remoteUrl")
                break
        if not cover_url and mbid:
            cover_url = (
                "https://coverartarchive.org/release-group/"
                f"{mbid}/front-250"
            )
        items.append({
            "mbid": mbid,
            "artist": artist,
            "title": title,
            "release_date": record.get("releaseDate") or None,
            "cover_url": cover_url,
            "queued": mbid in queued_mbids,
            "downloaded": mbid in existing_mbids,
        })

    return DownloadDiscoveryResult({
        "success": True,
        "last_tick": last_tick,
        "items": items,
    })
