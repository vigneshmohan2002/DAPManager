"""Contribution lifecycle operations independent of Flask presentation.

The HTTP adapter owns request parsing, authentication, database construction,
and ``jsonify``.  This module owns the state machine used by a master when a
satellite offers or uploads a track.  Dependencies that touch audio files or
the ingest pipeline are injectable so route-level compatibility wrappers can
remain monkeypatchable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
import tempfile
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
    cast,
)

from src.contracts import ContributionStatus


logger = logging.getLogger(__name__)

AudioQuality = Dict[str, Any]
ContributionRow = MutableMapping[str, Any]
LocalTrackRow = MutableMapping[str, Any]

TERMINAL_STATUSES: frozenset[ContributionStatus] = frozenset(
    {"have_better", "satisfied", "ingested"}
)
CONTRIBUTION_STATUSES: frozenset[ContributionStatus] = frozenset(
    {"attempting", "have_better", "satisfied", "needs_upload", "ingested"}
)


class ContributionStore(Protocol):
    """Database façade required by the master contribution workflow."""

    def find_local_tracks_by_identity(
        self,
        *,
        mbid: Optional[str] = None,
        isrc: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        album: Optional[str] = None,
    ) -> List[dict]: ...

    def create_contribution(self, **values: Any) -> int: ...

    def get_contribution(self, contribution_id: int) -> Optional[dict]: ...

    def list_contributions(self, limit: int = 200) -> List[dict]: ...

    def list_contributed(self, limit: int = 200) -> List[dict]: ...

    def update_contribution(
        self, contribution_id: int, **fields: Any
    ) -> None: ...

    def get_download_status(self, item_id: int) -> Optional[str]: ...

    def get_active_download_id(
        self, mbid: Optional[str], search_query: str
    ) -> Optional[int]: ...

    def queue_download(self, item: Any) -> int: ...

    def remove_from_queue(self, item_id: int) -> None: ...


class UploadedFile(Protocol):
    """Small boundary implemented by Werkzeug's uploaded-file object."""

    filename: Optional[str]

    def save(self, destination: str) -> None: ...


@dataclass(frozen=True)
class ContributionServiceResult:
    """JSON-shaped service outcome for translation by an HTTP adapter."""

    payload: Dict[str, Any]
    status_code: int = 200


ReadQuality = Callable[[str], Optional[dict]]
MeetsTarget = Callable[[Optional[dict], Optional[dict]], bool]
FindLocalCopy = Callable[
    [ContributionStore, Mapping[str, Any], Optional[dict]],
    Tuple[Optional[LocalTrackRow], Optional[dict]],
]
ContributionAge = Callable[[Mapping[str, Any]], Optional[float]]
AttemptTimeout = Union[int, Callable[[], int]]
VerifyUpload = Callable[[str, Optional[dict]], Optional[str]]
SecureFilename = Callable[[str], str]
ScannerFactory = Callable[[ContributionStore, str], Any]
IngestAudio = Callable[..., str]
DownloadItemFactory = Callable[..., Any]


def attempt_timeout_seconds(config_values: object) -> int:
    """Read the established one-hour attempt timeout from a real mapping."""
    if not isinstance(config_values, dict):
        return 3600
    try:
        return int(config_values.get("contribution_attempt_timeout_seconds", 3600))
    except (TypeError, ValueError):
        return 3600


def contribution_age_seconds(
    contribution: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Return age of a SQLite UTC timestamp, or ``None`` when malformed."""
    raw = contribution.get("created_at")
    if not raw:
        return None
    try:
        created = datetime.strptime(str(raw), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None
    current = now or datetime.now(timezone.utc)
    return (current - created).total_seconds()


def _quality_dependencies(
    read_quality: Optional[ReadQuality],
    meets_target: Optional[MeetsTarget],
) -> Tuple[ReadQuality, MeetsTarget]:
    if read_quality is None or meets_target is None:
        from src.audio_quality import meets_target as default_meets_target
        from src.audio_quality import read_quality as default_read_quality

        read_quality = read_quality or default_read_quality
        meets_target = meets_target or default_meets_target
    return read_quality, meets_target


def find_acceptable_local_copy(
    db: ContributionStore,
    identity: Mapping[str, Any],
    target: Optional[dict],
    *,
    path_exists: Callable[[str], bool] = os.path.exists,
    read_quality: Optional[ReadQuality] = None,
    meets_target: Optional[MeetsTarget] = None,
) -> Tuple[Optional[LocalTrackRow], Optional[dict]]:
    """Return a same-or-better local candidate using stable identity fields.

    MBID and ISRC matches are authoritative.  Metadata fallbacks additionally
    require durations within five seconds when both descriptors have one.
    Candidate ordering and ambiguity handling remain delegated to the database
    façade.
    """
    quality_reader, target_matcher = _quality_dependencies(
        read_quality, meets_target
    )
    candidates = db.find_local_tracks_by_identity(
        mbid=(identity.get("mbid") or "").strip() or None,
        isrc=(identity.get("isrc") or "").strip() or None,
        artist=(identity.get("artist") or "").strip() or None,
        title=(identity.get("title") or "").strip() or None,
        album=(identity.get("album") or "").strip() or None,
    )

    for raw_candidate in candidates:
        candidate = cast(LocalTrackRow, raw_candidate)
        path = candidate.get("local_path")
        if not path or not path_exists(path):
            continue
        quality = quality_reader(path)
        match_kind = candidate.get("identity_match")
        if match_kind not in {"mbid", "isrc"} and quality and target:
            try:
                candidate_length = int(quality.get("length_ms") or 0)
                target_length = int(target.get("length_ms") or 0)
            except (TypeError, ValueError):
                candidate_length = target_length = 0
            if (
                candidate_length
                and target_length
                and abs(candidate_length - target_length) > 5000
            ):
                continue
        if not target_matcher(quality, target):
            continue
        if match_kind != "mbid":
            logger.info(
                "Contribution identity fallback matched %s via %s (%s)",
                identity.get("mbid") or "<no mbid>",
                match_kind,
                candidate.get("mbid") or "<no mbid>",
            )
        return candidate, quality
    return None, None


def _stored_quality(raw: Any, *, require_mapping: bool = False) -> Optional[dict]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if require_mapping and not isinstance(parsed, dict):
        return None
    return cast(Optional[dict], parsed)


def evaluate_contribution(
    db: ContributionStore,
    contribution: ContributionRow,
    *,
    timeout_seconds: AttemptTimeout = 3600,
    now: Optional[datetime] = None,
    find_local_copy: FindLocalCopy = find_acceptable_local_copy,
    age_seconds: ContributionAge = contribution_age_seconds,
) -> ContributionRow:
    """Recompute one contribution while preserving its five-state machine."""
    status = cast(ContributionStatus, contribution["status"])
    if status in TERMINAL_STATUSES:
        return contribution

    target = _stored_quality(contribution.get("target_quality"))
    local_match, local_quality = find_local_copy(db, contribution, target)
    if local_match:
        db.update_contribution(
            cast(int, contribution["id"]),
            status="satisfied",
            acquired_quality=json.dumps(local_quality) if local_quality else None,
        )
        return cast(
            ContributionRow,
            db.get_contribution(cast(int, contribution["id"])),
        )

    download_id = contribution.get("download_id")
    download_status = db.get_download_status(download_id) if download_id else None
    if download_status == "pending":
        age = (
            contribution_age_seconds(contribution, now=now)
            if now is not None
            else age_seconds(contribution)
        )
        timeout = timeout_seconds() if callable(timeout_seconds) else timeout_seconds
        timed_out = age is not None and age >= timeout
        new_status: ContributionStatus = (
            "needs_upload" if timed_out else "attempting"
        )
    else:
        new_status = "needs_upload"

    if new_status == status:
        return contribution
    db.update_contribution(cast(int, contribution["id"]), status=new_status)
    return cast(
        ContributionRow,
        db.get_contribution(cast(int, contribution["id"])),
    )


def parse_quality_fields(rows: Sequence[ContributionRow]) -> List[ContributionRow]:
    """Parse quality JSON in-place as the dashboard wire contract expects."""
    parsed_rows = list(rows)
    for row in parsed_rows:
        for key in ("target_quality", "acquired_quality"):
            if not row.get(key):
                continue
            try:
                row[key] = json.loads(row[key])
            except (TypeError, ValueError):
                pass
    return parsed_rows


def list_master_contributions(
    db: ContributionStore,
    limit: int,
    *,
    timeout_seconds: int = 3600,
    evaluate: Callable[..., ContributionRow] = evaluate_contribution,
) -> ContributionServiceResult:
    rows = [
        evaluate(db, cast(ContributionRow, row), timeout_seconds=timeout_seconds)
        for row in db.list_contributions(limit=limit)
    ]
    return ContributionServiceResult({
        "success": True,
        "contributions": parse_quality_fields(rows),
    })


def list_outgoing_contributions(
    db: ContributionStore, limit: int
) -> ContributionServiceResult:
    return ContributionServiceResult({
        "success": True,
        "contributions": db.list_contributed(limit=limit),
    })


def _default_download_item_factory(**values: Any) -> Any:
    from src.db_manager import DownloadItem

    return DownloadItem(**values)


def offer_contribution(
    db: ContributionStore,
    data: Mapping[str, Any],
    *,
    find_local_copy: FindLocalCopy = find_acceptable_local_copy,
    download_item_factory: DownloadItemFactory = _default_download_item_factory,
) -> ContributionServiceResult:
    """Create a terminal offer or queue the master's own acquisition attempt."""
    mbid = (data.get("mbid") or "").strip()
    artist = (data.get("artist") or "").strip()
    title = (data.get("title") or "").strip()
    if not (artist and title):
        return ContributionServiceResult(
            {"success": False, "message": "artist and title are required"},
            400,
        )

    target_quality = (
        data.get("quality") if isinstance(data.get("quality"), dict) else None
    )
    target_json = json.dumps(target_quality) if target_quality else None
    local_match, local_quality = find_local_copy(db, data, target_quality)
    if local_match:
        contribution_id = db.create_contribution(
            device_id=data.get("device_id"),
            mbid=mbid,
            isrc=data.get("isrc"),
            artist=artist,
            title=title,
            album=data.get("album"),
            target_quality=target_json,
            acquired_quality=(
                json.dumps(local_quality) if local_quality else None
            ),
            status="have_better",
        )
        return ContributionServiceResult({
            "success": True,
            "contribution_id": contribution_id,
            "status": "have_better",
        })

    query = f"{artist} - {title}"
    download_id = db.get_active_download_id(mbid, query)
    if download_id is None:
        download_id = db.queue_download(download_item_factory(
            search_query=query,
            playlist_id="CONTRIB",
            mbid_guess=mbid,
            status="pending",
        ))
    contribution_id = db.create_contribution(
        device_id=data.get("device_id"),
        mbid=mbid,
        isrc=data.get("isrc"),
        artist=artist,
        title=title,
        album=data.get("album"),
        target_quality=target_json,
        status="attempting",
        download_id=download_id,
    )
    return ContributionServiceResult({
        "success": True,
        "contribution_id": contribution_id,
        "status": "attempting",
    })


def poll_contribution(
    db: ContributionStore,
    contribution_id: int,
    *,
    timeout_seconds: int = 3600,
    evaluate: Callable[..., ContributionRow] = evaluate_contribution,
) -> ContributionServiceResult:
    contribution = db.get_contribution(contribution_id)
    if contribution is None:
        return ContributionServiceResult(
            {"success": False, "message": "unknown contribution"}, 404
        )
    current = evaluate(
        db,
        cast(ContributionRow, contribution),
        timeout_seconds=timeout_seconds,
    )
    return ContributionServiceResult({
        "success": True,
        "status": current["status"],
        "want_upload": current["status"] == "needs_upload",
    })


def discard_staged_upload(path: Optional[str]) -> None:
    """Best-effort cleanup for unique contribution staging files."""
    if not path:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(
            "Could not remove staged contribution upload %s: %s", path, exc
        )


def verify_upload(
    path: str,
    target: Optional[dict],
    *,
    get_size: Callable[[str], int] = os.path.getsize,
    read_quality: Optional[ReadQuality] = None,
    meets_target: Optional[MeetsTarget] = None,
) -> Optional[str]:
    """Return the established rejection message for an invalid staged file."""
    quality_reader, target_matcher = _quality_dependencies(
        read_quality, meets_target
    )
    try:
        size = get_size(path)
    except OSError:
        return "uploaded file is unreadable"
    if size == 0:
        return "uploaded file is empty"
    if target:
        promised = int(target.get("size_bytes") or 0)
        if promised and size < promised * 0.5:
            return (
                f"uploaded file is truncated ({size} bytes vs promised "
                f"~{promised})"
            )
        if not target_matcher(quality_reader(path), target):
            return "uploaded file is lower quality than promised"
    return None


def _default_secure_filename(filename: str) -> str:
    from werkzeug.utils import secure_filename

    return secure_filename(filename)


def _default_scanner_factory(db: ContributionStore, picard_path: str) -> Any:
    from src.library_scanner import LibraryScanner

    return LibraryScanner(db, picard_path)


def _default_ingest_audio(*args: Any, **kwargs: Any) -> str:
    from src.file_ingest import ingest_audio_file

    return ingest_audio_file(*args, **kwargs)


def process_contribution_upload(
    db: ContributionStore,
    contribution_id: int,
    upload: UploadedFile,
    *,
    downloads_dir: str,
    music_library: str,
    picard_path: str,
    verify: VerifyUpload = verify_upload,
    find_local_copy: FindLocalCopy = find_acceptable_local_copy,
    secure_filename: SecureFilename = _default_secure_filename,
    scanner_factory: ScannerFactory = _default_scanner_factory,
    ingest_audio: IngestAudio = _default_ingest_audio,
    read_quality: Optional[ReadQuality] = None,
) -> ContributionServiceResult:
    """Stage, verify, race-check, and ingest one requested contribution."""
    contribution = db.get_contribution(contribution_id)
    if contribution is None:
        return ContributionServiceResult(
            {"success": False, "message": "unknown contribution"}, 404
        )
    if contribution.get("status") != "needs_upload":
        return ContributionServiceResult(
            {
                "success": False,
                "status": contribution.get("status"),
                "message": "this contribution is not requesting an upload",
            },
            409,
        )

    target = _stored_quality(
        contribution.get("target_quality"), require_mapping=True
    )
    staging_dir = os.path.join(downloads_dir, "_contrib")
    os.makedirs(staging_dir, exist_ok=True)
    safe_name = secure_filename(upload.filename or "") or "upload"
    suffix = os.path.splitext(safe_name)[1]
    file_descriptor, staged_path = tempfile.mkstemp(
        prefix=f"{contribution_id}_",
        suffix=suffix,
        dir=staging_dir,
    )
    os.close(file_descriptor)

    try:
        upload.save(staged_path)
        rejection = verify(staged_path, target)
        if rejection is not None:
            discard_staged_upload(staged_path)
            staged_path = ""
            db.update_contribution(contribution_id, status="needs_upload")
            return ContributionServiceResult(
                {"success": False, "status": "rejected", "message": rejection},
                422,
            )

        latest = db.get_contribution(contribution_id)
        if latest is None or latest.get("status") != "needs_upload":
            discard_staged_upload(staged_path)
            staged_path = ""
            return ContributionServiceResult(
                {
                    "success": False,
                    "status": latest.get("status") if latest else None,
                    "message": "the contribution stopped requesting an upload",
                },
                409,
            )

        local_match, local_quality = find_local_copy(db, latest, target)
        if local_match:
            discard_staged_upload(staged_path)
            staged_path = ""
            db.update_contribution(
                contribution_id,
                status="satisfied",
                acquired_quality=(
                    json.dumps(local_quality) if local_quality else None
                ),
            )
            if latest.get("download_id"):
                db.remove_from_queue(latest["download_id"])
            return ContributionServiceResult({
                "success": True,
                "status": "satisfied",
                "local_path": local_match.get("local_path"),
            })

        latest = db.get_contribution(contribution_id)
        if latest is None or latest.get("status") != "needs_upload":
            discard_staged_upload(staged_path)
            staged_path = ""
            return ContributionServiceResult(
                {
                    "success": False,
                    "status": latest.get("status") if latest else None,
                    "message": "the contribution stopped requesting an upload",
                },
                409,
            )

        destination = ingest_audio(
            db,
            scanner_factory(db, picard_path),
            music_library,
            staged_path,
            mbid_guess=latest.get("mbid"),
            artist=latest.get("artist"),
            title=latest.get("title"),
            album=latest.get("album"),
        )
        staged_path = ""

        quality_reader, _ = _quality_dependencies(read_quality, None)
        acquired_quality = None
        try:
            acquired_quality = json.dumps(quality_reader(destination))
        except Exception:
            pass
        db.update_contribution(
            contribution_id,
            status="ingested",
            acquired_quality=acquired_quality,
        )
        if latest.get("download_id"):
            db.remove_from_queue(latest["download_id"])
        return ContributionServiceResult({
            "success": True,
            "status": "ingested",
            "local_path": destination,
        })
    except Exception:
        discard_staged_upload(staged_path)
        raise
