"""Typed tag identify/apply policy independent of Flask presentation.

The service preserves the existing two-phase workflow: identification closes
its database lookup before fingerprint/network work, while application keeps
the database context open across file tagging and catalog mutation.  Only the
historically translated identify/write exceptions are converted to results;
other persistence and data-shape failures continue to propagate.
"""

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Mapping,
    Optional,
    Protocol,
    Union,
    cast,
)

from src.contracts import TagCandidate, TagMetadata


logger = logging.getLogger(__name__)


class TrackRecord(Protocol):
    mbid: str
    title: str
    artist: str
    album: Optional[str]
    local_path: Optional[str]


class TagStore(Protocol):
    def get_track_by_mbid(self, mbid: str) -> Optional[TrackRecord]: ...

    def soft_delete_track(self, mbid: str) -> bool: ...

    def add_or_update_track(self, track: TrackRecord) -> None: ...

    def set_track_tag_tier(
        self,
        mbid: str,
        tier: Optional[str],
        score: Optional[float],
    ) -> bool: ...


TagDatabaseFactory = Callable[[str], AbstractContextManager[TagStore]]
TrackFactory = Callable[..., TrackRecord]
IdentifyFile = Callable[[str, str, str], Optional[TagCandidate]]
ReadCurrentTags = Callable[[str], TagMetadata]
WriteTags = Callable[[str, TagMetadata], str]
ContactProvider = Callable[[], str]


@dataclass(frozen=True)
class TagApplicationResult:
    """JSON-shaped service outcome translated by the Flask adapter."""

    payload: Dict[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class ApplyTagRequest:
    """Validated body fields kept in their original runtime shapes."""

    meta: Dict[str, Any]
    data: Mapping[str, Any]


PreparedTagApply = Union[ApplyTagRequest, TagApplicationResult]


def prepare_tag_apply(data: Mapping[str, Any]) -> PreparedTagApply:
    """Validate ``meta.title`` before importing tag or database machinery."""
    meta = data.get("meta") or {}
    if not isinstance(meta, dict) or not meta.get("title"):
        return TagApplicationResult(
            {"success": False, "message": "meta.title is required"},
            400,
        )
    return ApplyTagRequest(meta=meta, data=data)


def identify_track(
    *,
    db_path: str,
    database_factory: TagDatabaseFactory,
    mbid: str,
    api_key: str,
    contact_provider: ContactProvider,
    identify_file: IdentifyFile,
    read_current_tags: ReadCurrentTags,
    event_logger: logging.Logger = logger,
) -> TagApplicationResult:
    """Load a local path, close the DB, then run the read-only identifier."""
    with database_factory(db_path) as db:
        track = db.get_track_by_mbid(mbid)

    if not track or not track.local_path:
        return TagApplicationResult(
            {
                "success": False,
                "message": "Track has no local_path — cannot fingerprint.",
            },
            404,
        )

    local_path = track.local_path
    contact = contact_provider()
    try:
        candidate = identify_file(local_path, api_key, contact)
    except Exception as exc:
        event_logger.error(
            f"tag_identify failed for {mbid}: {exc}",
            exc_info=True,
        )
        return TagApplicationResult(
            {"success": False, "message": str(exc)},
            500,
        )

    if not candidate:
        return TagApplicationResult(
            {
                "success": True,
                "candidate": None,
                "message": "no match",
                "current": read_current_tags(local_path),
            }
        )

    return TagApplicationResult(
        {
            "success": True,
            "candidate": candidate,
            "mbid": mbid,
            "local_path": local_path,
        }
    )


def _normalized_score(raw_score: object) -> Optional[float]:
    try:
        return float(raw_score) if raw_score is not None else None
    except (TypeError, ValueError):
        return None


def apply_track_tags(
    *,
    db_path: str,
    database_factory: TagDatabaseFactory,
    mbid: str,
    prepared: ApplyTagRequest,
    write_tags: WriteTags,
    track_factory: TrackFactory,
    event_logger: logging.Logger = logger,
) -> TagApplicationResult:
    """Write file tags, then mutate catalog identity and review state in order."""
    meta = prepared.meta
    with database_factory(db_path) as db:
        track = db.get_track_by_mbid(mbid)
        if not track or not track.local_path:
            return TagApplicationResult(
                {
                    "success": False,
                    "message": "Track has no local_path — cannot tag.",
                },
                404,
            )

        try:
            container = write_tags(
                track.local_path,
                cast(TagMetadata, meta),
            )
        except ValueError as exc:
            return TagApplicationResult(
                {"success": False, "message": str(exc)},
                400,
            )
        except Exception as exc:
            event_logger.error(
                f"tag_apply write failed for {mbid}: {exc}",
                exc_info=True,
            )
            return TagApplicationResult(
                {"success": False, "message": str(exc)},
                500,
            )

        new_mbid = (meta.get("mbid") or "").strip() or track.mbid
        updated = track_factory(
            mbid=new_mbid,
            title=meta.get("title") or track.title,
            artist=meta.get("artist") or track.artist,
            album=meta.get("album") or track.album,
            local_path=track.local_path,
        )
        if new_mbid != track.mbid:
            db.soft_delete_track(track.mbid)
        db.add_or_update_track(updated)
        db.set_track_tag_tier(
            new_mbid,
            "green",
            _normalized_score(prepared.data.get("score")),
        )

    return TagApplicationResult(
        {
            "success": True,
            "container": container,
            "mbid": new_mbid,
            "previous_mbid": track.mbid,
        }
    )
