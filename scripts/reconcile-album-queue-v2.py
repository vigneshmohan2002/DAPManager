#!/usr/bin/env python3
"""Fail-closed migration for the audited legacy album download queue.

The utility consumes a sealed, already-resolved JSON plan.  Dry-run is the
default.  Applying a plan requires its complete SHA-256 digest as an explicit
intent token, creates SQLite API backups, and records an atomic receipt beside
the plan.  It never calls the web API and never prints queue queries, file
paths, credentials, or MusicBrainz payloads.

This is deliberately a versioned, one-purpose operation.  It accepts the
twenty audited legacy rows (21, 22, 24, 27-41, 43, and 44), and retires legacy
row 25 only while the exact WASTELAND request on row 50 remains intact.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from uuid import UUID


SCHEMA_VERSION = 2
TOOL_VERSION = "2.0.0"
ALBUM_PLAYLIST_ID = "SATELLITE_ALBUM"
ALBUM_QUERY_PREFIX = "::ALBUM::"
EXPECTED_MIGRATION_QUEUE_IDS = frozenset(
    (21, 22, 24, *range(27, 42), 43, 44)
)
RETIRED_DUPLICATE_QUEUE_ID = 25
RETAINED_WASTELAND_QUEUE_ID = 50
WASTELAND_RELEASE_MBID = "95fb59ed-1ece-419b-b62f-aef31e0ebf36"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

QUEUE_COLUMNS = (
    "id",
    "search_query",
    "playlist_id",
    "status",
    "last_attempt",
    "mbid_guess",
)
MANIFEST_COLUMNS = (
    "position",
    "recording_mbid",
    "medium_position",
    "track_position",
    "track_number",
    "title",
    "artist",
    "date",
    "track_total",
    "disc_total",
    "release_track_mbid",
)
TARGET_TEXT_FIELDS = (
    "title",
    "artist",
    "date",
    "country",
    "status",
    "disambiguation",
    "primary_type",
    "format",
    "label",
    "catalog_number",
    "barcode",
)


class ReconcileError(RuntimeError):
    """A safety check prevented the reconciliation."""


ReleaseResolver = Callable[[str], Any]
InventoryReader = Callable[[sqlite3.Connection, Mapping[str, Any]], Mapping[str, Any]]


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ReconcileError(f"{label} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ReconcileError(f"{label} must be an integer") from error
    if parsed < minimum:
        raise ReconcileError(f"{label} must be at least {minimum}")
    return parsed


def _require_text(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ReconcileError(f"{label} must be a string")
    if not allow_empty and not value.strip():
        raise ReconcileError(f"{label} cannot be empty")
    return value


def _canonical_uuid(value: Any, label: str) -> str:
    try:
        return str(UUID(str(value).strip()))
    except (AttributeError, TypeError, ValueError) as error:
        raise ReconcileError(f"{label} must be a valid UUID") from error


def _parse_utc(value: Any, label: str) -> str:
    raw = _require_text(value, label)
    if not raw.endswith("Z"):
        raise ReconcileError(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as error:
        raise ReconcileError(f"{label} must be an RFC3339 UTC timestamp") from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ReconcileError(f"{label} must use UTC")
    return raw


def _normalize_queue_row(raw: Mapping[str, Any], label: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReconcileError(f"{label} must be an object")
    if set(raw) != set(QUEUE_COLUMNS):
        raise ReconcileError(f"{label} must contain exactly the six queue columns")
    status = _require_text(raw.get("status"), f"{label}.status")
    if status not in {"pending", "failed", "success"}:
        raise ReconcileError(f"{label}.status is invalid")
    last_attempt = raw.get("last_attempt")
    if last_attempt is not None and not isinstance(last_attempt, str):
        raise ReconcileError(f"{label}.last_attempt must be a string or null")
    return {
        "id": _require_int(raw.get("id"), f"{label}.id", minimum=1),
        "search_query": _require_text(raw.get("search_query"), f"{label}.search_query"),
        "playlist_id": _require_text(raw.get("playlist_id"), f"{label}.playlist_id"),
        "status": status,
        "last_attempt": last_attempt,
        "mbid_guess": _require_text(raw.get("mbid_guess"), f"{label}.mbid_guess"),
    }


def _normalize_manifest(raw: Any, label: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ReconcileError(f"{label} must be a non-empty array")
    normalized: List[Dict[str, Any]] = []
    recordings: set[str] = set()
    release_tracks: set[str] = set()
    for expected_position, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping) or set(item) != set(MANIFEST_COLUMNS):
            raise ReconcileError(
                f"{label}[{expected_position - 1}] must contain the complete manifest fields"
            )
        position = _require_int(
            item.get("position"), f"{label}[{expected_position - 1}].position", minimum=1
        )
        medium_position = _require_int(
            item.get("medium_position"),
            f"{label}[{expected_position - 1}].medium_position",
            minimum=1,
        )
        track_position = _require_int(
            item.get("track_position"),
            f"{label}[{expected_position - 1}].track_position",
            minimum=1,
        )
        track_total = _require_int(
            item.get("track_total"),
            f"{label}[{expected_position - 1}].track_total",
            minimum=1,
        )
        disc_total = _require_int(
            item.get("disc_total"),
            f"{label}[{expected_position - 1}].disc_total",
            minimum=1,
        )
        if position != expected_position:
            raise ReconcileError(f"{label} positions must be contiguous and one-based")
        if track_position > track_total or medium_position > disc_total:
            raise ReconcileError(f"{label}[{expected_position - 1}] has unsafe ordering")
        recording_mbid = _canonical_uuid(
            item.get("recording_mbid"),
            f"{label}[{expected_position - 1}].recording_mbid",
        )
        release_track_mbid = _canonical_uuid(
            item.get("release_track_mbid"),
            f"{label}[{expected_position - 1}].release_track_mbid",
        )
        if recording_mbid in recordings:
            raise ReconcileError(f"{label} repeats a recording MBID")
        if release_track_mbid in release_tracks:
            raise ReconcileError(f"{label} repeats a release-track MBID")
        recordings.add(recording_mbid)
        release_tracks.add(release_track_mbid)
        normalized.append(
            {
                "position": position,
                "recording_mbid": recording_mbid,
                "medium_position": medium_position,
                "track_position": track_position,
                "track_number": _require_text(
                    item.get("track_number"),
                    f"{label}[{expected_position - 1}].track_number",
                ),
                "title": _require_text(
                    item.get("title"), f"{label}[{expected_position - 1}].title"
                ),
                "artist": _require_text(
                    item.get("artist"), f"{label}[{expected_position - 1}].artist"
                ),
                "date": _require_text(
                    item.get("date"),
                    f"{label}[{expected_position - 1}].date",
                    allow_empty=True,
                ),
                "track_total": track_total,
                "disc_total": disc_total,
                "release_track_mbid": release_track_mbid,
            }
        )
    return normalized


def _normalize_inventory(raw: Any, label: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReconcileError(f"{label} must be an object")
    if set(raw) != {"completed_recording_mbids", "cross_release_ownership"}:
        raise ReconcileError(f"{label} has unexpected fields")
    completed_raw = raw.get("completed_recording_mbids")
    conflicts_raw = raw.get("cross_release_ownership")
    if not isinstance(completed_raw, list) or not isinstance(conflicts_raw, list):
        raise ReconcileError(f"{label} arrays are required")
    completed = sorted(
        {_canonical_uuid(value, f"{label}.completed_recording_mbids") for value in completed_raw}
    )
    conflicts: List[Dict[str, str]] = []
    seen = set()
    for index, conflict in enumerate(conflicts_raw):
        if not isinstance(conflict, Mapping) or set(conflict) != {
            "recording_mbid",
            "release_mbid",
        }:
            raise ReconcileError(f"{label}.cross_release_ownership[{index}] is invalid")
        recording = _canonical_uuid(
            conflict.get("recording_mbid"),
            f"{label}.cross_release_ownership[{index}].recording_mbid",
        )
        owner = _require_text(
            conflict.get("release_mbid"),
            f"{label}.cross_release_ownership[{index}].release_mbid",
            allow_empty=True,
        ).strip().casefold()
        key = (recording, owner)
        if key in seen:
            raise ReconcileError(f"{label} repeats an ownership conflict")
        seen.add(key)
        conflicts.append({"recording_mbid": recording, "release_mbid": owner})
    conflicts.sort(key=lambda row: (row["recording_mbid"], row["release_mbid"]))
    return {
        "completed_recording_mbids": completed,
        "cross_release_ownership": conflicts,
    }


def _normalize_target(raw: Any, label: str) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReconcileError(f"{label} must be an object")
    required = {
        "release_mbid",
        "track_count",
        "manifest",
        "manifest_sha256",
        "target_sha256",
        *TARGET_TEXT_FIELDS,
    }
    if set(raw) != required:
        raise ReconcileError(f"{label} does not contain the exact target fields")
    manifest = _normalize_manifest(raw.get("manifest"), f"{label}.manifest")
    target: Dict[str, Any] = {
        "release_mbid": _canonical_uuid(raw.get("release_mbid"), f"{label}.release_mbid"),
        "track_count": _require_int(raw.get("track_count"), f"{label}.track_count", minimum=1),
    }
    for field in TARGET_TEXT_FIELDS:
        target[field] = _require_text(
            raw.get(field),
            f"{label}.{field}",
            allow_empty=field not in {"title", "artist", "status", "primary_type"},
        )
    target["manifest"] = manifest
    if target["track_count"] != len(manifest):
        raise ReconcileError(f"{label}.track_count does not match its manifest")
    if target["status"].casefold() != "official":
        raise ReconcileError(f"{label} must resolve to an Official release")
    if target["primary_type"].casefold() not in {"album", "ep", "single"}:
        raise ReconcileError(f"{label} has a disallowed primary type")
    manifest_hash = _require_text(raw.get("manifest_sha256"), f"{label}.manifest_sha256")
    if manifest_hash != json_sha256(manifest):
        raise ReconcileError(f"{label}.manifest_sha256 does not match")
    target["manifest_sha256"] = manifest_hash
    target_payload = dict(target)
    target_payload.pop("manifest_sha256", None)
    target_hash = _require_text(raw.get("target_sha256"), f"{label}.target_sha256")
    if target_hash != json_sha256(target_payload):
        raise ReconcileError(f"{label}.target_sha256 does not match")
    target["target_sha256"] = target_hash
    return target


def target_from_resolved(album: Any) -> Dict[str, Any]:
    """Convert a validated ``ResolvedAlbum`` into the v2 target shape."""
    try:
        manifest = [dict(row) for row in album.track_manifest()]
        target: Dict[str, Any] = {
            "release_mbid": str(album.release_mbid),
            "track_count": int(album.track_count),
            "manifest": manifest,
        }
        for field in TARGET_TEXT_FIELDS:
            target[field] = str(getattr(album, field, "") or "")
    except (AttributeError, TypeError, ValueError) as error:
        raise ReconcileError("the release resolver returned an invalid object") from error
    normalized_manifest = _normalize_manifest(target["manifest"], "resolved.manifest")
    target["manifest"] = normalized_manifest
    target["manifest_sha256"] = json_sha256(normalized_manifest)
    normalized_base = _normalize_target_without_hashes(target, "resolved")
    target["target_sha256"] = json_sha256(normalized_base)
    return _normalize_target(target, "resolved")


def _normalize_target_without_hashes(raw: Mapping[str, Any], label: str) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "release_mbid": _canonical_uuid(raw.get("release_mbid"), f"{label}.release_mbid"),
        "track_count": _require_int(raw.get("track_count"), f"{label}.track_count", minimum=1),
        "manifest": _normalize_manifest(raw.get("manifest"), f"{label}.manifest"),
    }
    for field in TARGET_TEXT_FIELDS:
        base[field] = _require_text(
            raw.get(field),
            f"{label}.{field}",
            allow_empty=field not in {"title", "artist", "status", "primary_type"},
        )
    return base


def seal_plan(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a canonical deep-copied plan with a deterministic seal."""
    plan = copy.deepcopy(dict(payload))
    plan.pop("plan_sha256", None)
    plan["plan_sha256"] = json_sha256(plan)
    normalized = _normalize_plan(plan, require_canonical=False)
    normalized.pop("plan_sha256")
    normalized["plan_sha256"] = json_sha256(normalized)
    return normalized


def _normalize_plan(
    raw: Any,
    *,
    require_canonical: bool = True,
) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReconcileError("plan must be a JSON object")
    if set(raw) != {
        "schema_version",
        "run_id",
        "created_at",
        "tool_version",
        "queue_snapshot",
        "actions",
        "plan_sha256",
    }:
        raise ReconcileError("plan has unexpected or missing top-level fields")
    supplied_hash = _require_text(raw.get("plan_sha256"), "plan.plan_sha256")
    unsigned = copy.deepcopy(dict(raw))
    unsigned.pop("plan_sha256", None)
    if supplied_hash != json_sha256(unsigned):
        raise ReconcileError("plan SHA-256 seal does not match")
    if _require_int(raw.get("schema_version"), "plan.schema_version") != SCHEMA_VERSION:
        raise ReconcileError("unsupported plan schema version")
    if _require_text(raw.get("tool_version"), "plan.tool_version") != TOOL_VERSION:
        raise ReconcileError("plan was built for a different tool version")
    run_id = _require_text(raw.get("run_id"), "plan.run_id")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ReconcileError("plan.run_id is unsafe")
    created_at = _parse_utc(raw.get("created_at"), "plan.created_at")

    queue_raw = raw.get("queue_snapshot")
    if not isinstance(queue_raw, list):
        raise ReconcileError("plan.queue_snapshot must be an array")
    queue = [
        _normalize_queue_row(row, f"plan.queue_snapshot[{index}]")
        for index, row in enumerate(queue_raw)
    ]
    queue.sort(key=lambda row: row["id"])
    if len({row["id"] for row in queue}) != len(queue):
        raise ReconcileError("plan.queue_snapshot repeats a queue id")
    queue_by_id = {row["id"]: row for row in queue}

    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, list):
        raise ReconcileError("plan.actions must be an array")
    actions: List[Dict[str, Any]] = []
    action_ids = set()
    migration_ids = set()
    retire_count = 0
    target_recording_owner: Dict[str, str] = {}
    migration_releases = set()
    for index, raw_action in enumerate(actions_raw):
        label = f"plan.actions[{index}]"
        if not isinstance(raw_action, Mapping):
            raise ReconcileError(f"{label} must be an object")
        action_name = _require_text(raw_action.get("action"), f"{label}.action")
        if action_name not in {"migrate", "retire_duplicate"}:
            raise ReconcileError(f"{label}.action is invalid")
        expected_fields = {"action", "source", "target", "inventory"}
        if action_name == "migrate":
            expected_fields.add("ownership_conflict_policy")
        else:
            expected_fields.add("survivor")
        if set(raw_action) != expected_fields:
            raise ReconcileError(f"{label} has unexpected or missing fields")
        source = _normalize_queue_row(raw_action.get("source"), f"{label}.source")
        if source["id"] in action_ids:
            raise ReconcileError("a queue row appears in more than one action")
        action_ids.add(source["id"])
        if queue_by_id.get(source["id"]) != source:
            raise ReconcileError(f"{label}.source does not match the sealed queue snapshot")
        target = _normalize_target(raw_action.get("target"), f"{label}.target")
        inventory = _normalize_inventory(raw_action.get("inventory"), f"{label}.inventory")
        manifest_ids = {row["recording_mbid"] for row in target["manifest"]}
        if not set(inventory["completed_recording_mbids"]).issubset(manifest_ids):
            raise ReconcileError(f"{label}.inventory includes a non-target recording")

        action: Dict[str, Any] = {
            "action": action_name,
            "source": source,
            "target": target,
            "inventory": inventory,
        }
        for recording in manifest_ids:
            owner = target_recording_owner.get(recording)
            if owner is not None and owner != target["release_mbid"]:
                raise ReconcileError("a recording appears in more than one target release")
            target_recording_owner[recording] = target["release_mbid"]
        if action_name == "migrate":
            migration_ids.add(source["id"])
            migration_releases.add(target["release_mbid"])
            if source["playlist_id"] != "AUDIT":
                raise ReconcileError(f"{label}.source is not an AUDIT row")
            policy = _require_text(
                raw_action.get("ownership_conflict_policy"),
                f"{label}.ownership_conflict_policy",
            )
            if policy not in {"abort", "blocked_detach"}:
                raise ReconcileError(f"{label}.ownership_conflict_policy is invalid")
            conflicts = inventory["cross_release_ownership"]
            if policy == "blocked_detach" and not conflicts:
                raise ReconcileError(f"{label} requests blocked_detach without a conflict")
            action["ownership_conflict_policy"] = policy
        else:
            retire_count += 1
            survivor = _normalize_queue_row(raw_action.get("survivor"), f"{label}.survivor")
            if source["id"] != RETIRED_DUPLICATE_QUEUE_ID:
                raise ReconcileError("only audited row 25 may be retired")
            if survivor["id"] != RETAINED_WASTELAND_QUEUE_ID:
                raise ReconcileError("row 25 may only retire in favour of row 50")
            if queue_by_id.get(survivor["id"]) != survivor:
                raise ReconcileError(f"{label}.survivor does not match the queue snapshot")
            if target["release_mbid"] != WASTELAND_RELEASE_MBID:
                raise ReconcileError("row 25 retirement must use the exact WASTELAND release")
            canonical_query = _canonical_query(target)
            if (
                survivor["playlist_id"] != ALBUM_PLAYLIST_ID
                or survivor["mbid_guess"].casefold() != WASTELAND_RELEASE_MBID
                or survivor["search_query"] != canonical_query
            ):
                raise ReconcileError("retained row 50 is not the canonical WASTELAND queue row")
            action["survivor"] = survivor
        actions.append(action)

    if migration_ids != EXPECTED_MIGRATION_QUEUE_IDS:
        raise ReconcileError("plan does not contain the twenty audited migration rows")
    if len(migration_releases) != len(EXPECTED_MIGRATION_QUEUE_IDS):
        raise ReconcileError("migration target releases must be unique")
    if retire_count != 1 or action_ids != EXPECTED_MIGRATION_QUEUE_IDS | {
        RETIRED_DUPLICATE_QUEUE_ID
    }:
        raise ReconcileError("plan does not contain the single audited duplicate retirement")
    audit_ids = {row["id"] for row in queue if row["playlist_id"] == "AUDIT"}
    if audit_ids != action_ids:
        raise ReconcileError("the sealed queue has unplanned or missing AUDIT rows")

    actions.sort(key=lambda row: row["source"]["id"])
    normalized = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "tool_version": TOOL_VERSION,
        "queue_snapshot": queue,
        "actions": actions,
        "plan_sha256": supplied_hash,
    }
    normalized_unsigned = dict(normalized)
    normalized_unsigned.pop("plan_sha256")
    if require_canonical and unsigned != normalized_unsigned:
        raise ReconcileError(
            "plan values and arrays must already use their canonical representation"
        )
    return normalized


def load_plan(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ReconcileError("unable to read the sealed plan") from error
    return _normalize_plan(raw)


def _canonical_query(target: Mapping[str, Any]) -> str:
    return f"{ALBUM_QUERY_PREFIX} {target['artist']} - {target['title']}"


def _connect(database: Path, *, query_only: bool = False) -> sqlite3.Connection:
    if not database.is_file():
        raise ReconcileError("database file does not exist")
    conn = sqlite3.connect(str(database), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if query_only:
        conn.execute("PRAGMA query_only = ON")
    return conn


def _queue_snapshot(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, search_query, playlist_id, status, last_attempt, mbid_guess "
        "FROM download_queue ORDER BY id"
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "search_query": str(row["search_query"]),
            "playlist_id": str(row["playlist_id"]),
            "status": str(row["status"]),
            "last_attempt": row["last_attempt"],
            "mbid_guess": str(row["mbid_guess"]),
        }
        for row in rows
    ]


def read_inventory(
    conn: sqlite3.Connection,
    target: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return secret-free catalog ownership for a target manifest."""
    release_mbid = str(target["release_mbid"])
    manifest_ids = [str(row["recording_mbid"]) for row in target["manifest"]]
    if not manifest_ids:
        return {"completed_recording_mbids": [], "cross_release_ownership": []}
    placeholders = ",".join("?" for _ in manifest_ids)
    rows = conn.execute(
        "SELECT mbid, release_mbid, local_path FROM tracks "
        f"WHERE mbid IN ({placeholders}) AND deleted_at IS NULL "
        "AND local_path IS NOT NULL AND TRIM(local_path) != ''",
        tuple(manifest_ids),
    ).fetchall()
    completed = []
    conflicts = []
    for row in rows:
        if not os.path.isfile(str(row["local_path"] or "")):
            # A stale catalog path neither satisfies this release nor blocks
            # a different edition.  The script runs inside the master
            # container so bind-mounted /data paths are directly checkable.
            continue
        recording = str(row["mbid"]).strip().casefold()
        owner = str(row["release_mbid"] or "").strip().casefold()
        if owner == release_mbid.casefold():
            completed.append(recording)
        else:
            conflicts.append(
                {"recording_mbid": recording, "release_mbid": owner}
            )
    return _normalize_inventory(
        {
            "completed_recording_mbids": sorted(completed),
            "cross_release_ownership": conflicts,
        },
        "live inventory",
    )


def _manifest_for_request(conn: sqlite3.Connection, request_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT position, recording_mbid, medium_position, track_position, "
        "track_number, title, artist, date, track_total, disc_total, "
        "release_track_mbid FROM album_download_request_tracks "
        "WHERE request_id = ? ORDER BY position",
        (int(request_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _request_for_release(conn: sqlite3.Connection, release_mbid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM album_download_requests WHERE release_mbid = ? COLLATE NOCASE",
        (release_mbid,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["manifest"] = _manifest_for_request(conn, int(row["id"]))
    return result


def _request_for_queue(conn: sqlite3.Connection, queue_item_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM album_download_requests WHERE queue_item_id = ? ORDER BY id",
        (int(queue_item_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _request_identity_matches(
    request: Optional[Mapping[str, Any]],
    target: Mapping[str, Any],
    *,
    queue_item_id: Optional[int],
    stage: Optional[str] = None,
    detail: Optional[str] = None,
    completed_tracks: Optional[int] = None,
) -> bool:
    if request is None:
        return False
    if request.get("queue_item_id") != queue_item_id:
        return False
    if (
        str(request.get("release_mbid") or "").casefold()
        != str(target["release_mbid"]).casefold()
        or str(request.get("artist") or "") != str(target["artist"])
        or str(request.get("title") or "") != str(target["title"])
        or int(request.get("track_count") or 0) != int(target["track_count"])
    ):
        return False
    if stage is not None and request.get("stage") != stage:
        return False
    if detail is not None and request.get("detail") != detail:
        return False
    if completed_tracks is not None and int(request.get("completed_tracks") or 0) != completed_tracks:
        return False
    try:
        stored = _normalize_manifest(list(request.get("manifest") or []), "stored manifest")
    except ReconcileError:
        return False
    return stored == target["manifest"]


def _normal_detail(action: Mapping[str, Any]) -> str:
    completed = len(action["inventory"]["completed_recording_mbids"])
    return (
        "Reconciled from audited legacy queue; "
        f"{completed} of {action['target']['track_count']} exact release tracks already catalogued"
    )


def _blocked_detail(action: Mapping[str, Any]) -> str:
    conflicts = len(action["inventory"]["cross_release_ownership"])
    return (
        "Blocked during legacy queue reconciliation: "
        f"{conflicts} recording(s) are owned by another live release"
    )


def _is_blocked(action: Mapping[str, Any]) -> bool:
    return (
        action["action"] == "migrate"
        and action["ownership_conflict_policy"] == "blocked_detach"
    )


def _expected_post_queue(plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = {row["id"]: dict(row) for row in plan["queue_snapshot"]}
    for action in plan["actions"]:
        source_id = action["source"]["id"]
        if action["action"] == "retire_duplicate" or _is_blocked(action):
            rows.pop(source_id, None)
            continue
        target = action["target"]
        rows[source_id] = {
            "id": source_id,
            "search_query": _canonical_query(target),
            "playlist_id": ALBUM_PLAYLIST_ID,
            "status": "pending",
            "last_attempt": None,
            "mbid_guess": target["release_mbid"],
        }
    return [rows[key] for key in sorted(rows)]


def _pre_requests_absent(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> bool:
    for action in plan["actions"]:
        if action["action"] != "migrate":
            continue
        if _request_for_release(conn, action["target"]["release_mbid"]) is not None:
            return False
        if _request_for_queue(conn, action["source"]["id"]):
            return False
    return True


def _duplicate_guard(conn: sqlite3.Connection, action: Mapping[str, Any]) -> bool:
    source_id = action["source"]["id"]
    survivor = action["survivor"]
    current = {row["id"]: row for row in _queue_snapshot(conn)}
    if current.get(survivor["id"]) != survivor:
        return False
    if _request_for_queue(conn, source_id):
        return False
    contribution = conn.execute(
        "SELECT 1 FROM contributions WHERE download_id = ? LIMIT 1",
        (source_id,),
    ).fetchone()
    if contribution is not None:
        return False
    request = _request_for_release(conn, action["target"]["release_mbid"])
    return _request_identity_matches(
        request,
        action["target"],
        queue_item_id=RETAINED_WASTELAND_QUEUE_ID,
    )


def _post_requests_match(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> bool:
    for action in plan["actions"]:
        target = action["target"]
        if action["action"] == "retire_duplicate":
            if not _duplicate_guard(conn, action):
                return False
            continue
        completed = len(action["inventory"]["completed_recording_mbids"])
        if _is_blocked(action):
            matches = _request_identity_matches(
                _request_for_release(conn, target["release_mbid"]),
                target,
                queue_item_id=None,
                stage="failed",
                detail=_blocked_detail(action),
                completed_tracks=completed,
            )
        else:
            matches = _request_identity_matches(
                _request_for_release(conn, target["release_mbid"]),
                target,
                queue_item_id=action["source"]["id"],
                stage="queued",
                detail=_normal_detail(action),
                completed_tracks=completed,
            )
        if not matches:
            return False
    return True


def classify_state(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> str:
    current = _queue_snapshot(conn)
    pre_queue = current == plan["queue_snapshot"]
    post_queue = current == _expected_post_queue(plan)
    duplicate_action = next(
        action for action in plan["actions"] if action["action"] == "retire_duplicate"
    )
    if pre_queue and _pre_requests_absent(conn, plan) and _duplicate_guard(conn, duplicate_action):
        return "PRE"
    if post_queue and _post_requests_match(conn, plan):
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM download_queue WHERE playlist_id = 'AUDIT'"
        ).fetchone()[0]
        if int(audit_count) == 0:
            return "POST"
    return "MIXED"


def _database_checks(conn: sqlite3.Connection) -> Dict[str, Any]:
    integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    foreign_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    result = {
        "integrity_check": integrity,
        "foreign_key_violation_count": len(foreign_rows),
    }
    if integrity != ["ok"] or foreign_rows:
        raise ReconcileError("database integrity or foreign-key check failed")
    return result


def _verify_fresh_releases(plan: Mapping[str, Any], resolver: ReleaseResolver) -> None:
    targets: Dict[str, Mapping[str, Any]] = {}
    for action in plan["actions"]:
        targets[action["target"]["release_mbid"]] = action["target"]
    for release_mbid, sealed_target in sorted(targets.items()):
        try:
            resolved = resolver(release_mbid)
        except Exception as error:
            raise ReconcileError("fresh exact release resolution failed") from error
        fresh_target = target_from_resolved(resolved)
        if fresh_target != sealed_target:
            raise ReconcileError("a sealed target no longer matches fresh exact resolution")


def _verify_inventories(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    inventory_reader: InventoryReader,
) -> None:
    for action in plan["actions"]:
        current = _normalize_inventory(
            inventory_reader(conn, action["target"]),
            "current inventory",
        )
        if current != action["inventory"]:
            raise ReconcileError("sealed inventory no longer matches the live catalog")


def _verify_conflict_policies(plan: Mapping[str, Any]) -> None:
    for action in plan["actions"]:
        if (
            action["action"] == "migrate"
            and action["inventory"]["cross_release_ownership"]
            and action["ownership_conflict_policy"] != "blocked_detach"
        ):
            raise ReconcileError(
                "cross-release recording ownership requires explicit blocked_detach"
            )


def _verify_request_recording_ownership(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
) -> None:
    duplicate_release = WASTELAND_RELEASE_MBID
    for action in plan["actions"]:
        if action["action"] != "migrate":
            continue
        target = action["target"]
        recording_ids = [row["recording_mbid"] for row in target["manifest"]]
        placeholders = ",".join("?" for _ in recording_ids)
        rows = conn.execute(
            "SELECT DISTINCT r.release_mbid FROM album_download_request_tracks t "
            "JOIN album_download_requests r ON r.id = t.request_id "
            f"WHERE t.recording_mbid IN ({placeholders}) "
            "AND r.release_mbid != ? COLLATE NOCASE",
            (*recording_ids, target["release_mbid"]),
        ).fetchall()
        if rows:
            raise ReconcileError("an existing exact request owns a target recording")
        existing = _request_for_release(conn, target["release_mbid"])
        if existing is not None and target["release_mbid"] != duplicate_release:
            raise ReconcileError("an exact request already exists for a migration target")


def preflight(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    *,
    resolver: ReleaseResolver,
    inventory_reader: InventoryReader = read_inventory,
    require_pre: bool = True,
) -> Dict[str, Any]:
    checks = _database_checks(conn)
    state = classify_state(conn, plan)
    if require_pre and state != "PRE":
        raise ReconcileError(f"plan is not wholly PRE (state={state})")
    if state == "MIXED":
        raise ReconcileError("database is in a mixed reconciliation state")
    _verify_fresh_releases(plan, resolver)
    _verify_inventories(conn, plan, inventory_reader)
    _verify_conflict_policies(plan)
    if state == "PRE":
        _verify_request_recording_ownership(conn, plan)
    duplicate_action = next(
        action for action in plan["actions"] if action["action"] == "retire_duplicate"
    )
    if not _duplicate_guard(conn, duplicate_action):
        raise ReconcileError("row 25 duplicate-retirement guard failed")
    checks["state"] = state
    checks["fresh_releases"] = len(
        {action["target"]["release_mbid"] for action in plan["actions"]}
    )
    checks["inventories"] = len(plan["actions"])
    return checks


def _exact_queue_where(row: Mapping[str, Any]) -> Tuple[str, Tuple[Any, ...]]:
    where = (
        "id = ? AND search_query = ? AND playlist_id = ? AND status = ? "
        "AND last_attempt IS ? AND mbid_guess = ?"
    )
    values = tuple(row[column] for column in QUEUE_COLUMNS)
    return where, values


def _insert_request(
    conn: sqlite3.Connection,
    action: Mapping[str, Any],
    *,
    queue_item_id: Optional[int],
    stage: str,
    detail: str,
) -> int:
    target = action["target"]
    completed = len(action["inventory"]["completed_recording_mbids"])
    cursor = conn.execute(
        "INSERT INTO album_download_requests "
        "(queue_item_id, release_mbid, artist, title, track_count, stage, detail, completed_tracks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            queue_item_id,
            target["release_mbid"],
            target["artist"],
            target["title"],
            target["track_count"],
            stage,
            detail,
            completed,
        ),
    )
    request_id = int(cursor.lastrowid or 0)
    conn.executemany(
        "INSERT INTO album_download_request_tracks "
        "(request_id, position, recording_mbid, medium_position, track_position, "
        "track_number, title, artist, date, track_total, disc_total, release_track_mbid) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                request_id,
                track["position"],
                track["recording_mbid"],
                track["medium_position"],
                track["track_position"],
                track["track_number"],
                track["title"],
                track["artist"],
                track["date"],
                track["track_total"],
                track["disc_total"],
                track["release_track_mbid"],
            )
            for track in target["manifest"]
        ],
    )
    return request_id


def _apply_transaction(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    inventory_reader: InventoryReader,
) -> List[Dict[str, Any]]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if classify_state(conn, plan) != "PRE":
            raise ReconcileError("queue changed after preflight")
        _verify_inventories(conn, plan, inventory_reader)
        _verify_request_recording_ownership(conn, plan)
        duplicate_action = next(
            action for action in plan["actions"] if action["action"] == "retire_duplicate"
        )
        if not _duplicate_guard(conn, duplicate_action):
            raise ReconcileError("row 25 guard changed after preflight")

        results: List[Dict[str, Any]] = []
        for action in plan["actions"]:
            source = action["source"]
            where, values = _exact_queue_where(source)
            if action["action"] == "retire_duplicate":
                cursor = conn.execute(f"DELETE FROM download_queue WHERE {where}", values)
                if cursor.rowcount != 1:
                    raise ReconcileError("row 25 exact retirement predicate failed")
                results.append(
                    {
                        "action": "retire_duplicate",
                        "queue_item_id": source["id"],
                        "retained_queue_item_id": RETAINED_WASTELAND_QUEUE_ID,
                        "manifest_sha256": action["target"]["manifest_sha256"],
                    }
                )
                continue

            target = action["target"]
            if _is_blocked(action):
                cursor = conn.execute(f"DELETE FROM download_queue WHERE {where}", values)
                if cursor.rowcount != 1:
                    raise ReconcileError("blocked row exact retirement predicate failed")
                request_id = _insert_request(
                    conn,
                    action,
                    queue_item_id=None,
                    stage="failed",
                    detail=_blocked_detail(action),
                )
                outcome = "blocked_detached"
            else:
                cursor = conn.execute(
                    "UPDATE download_queue SET search_query = ?, playlist_id = ?, "
                    "status = 'pending', last_attempt = NULL, mbid_guess = ? "
                    f"WHERE {where}",
                    (
                        _canonical_query(target),
                        ALBUM_PLAYLIST_ID,
                        target["release_mbid"],
                        *values,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ReconcileError("legacy row exact migration predicate failed")
                request_id = _insert_request(
                    conn,
                    action,
                    queue_item_id=source["id"],
                    stage="queued",
                    detail=_normal_detail(action),
                )
                outcome = "migrated"
            results.append(
                {
                    "action": outcome,
                    "queue_item_id": source["id"],
                    "request_id": request_id,
                    "release_mbid": target["release_mbid"],
                    "manifest_sha256": target["manifest_sha256"],
                }
            )

        if classify_state(conn, plan) != "POST":
            raise ReconcileError("post-migration invariants failed")
        _database_checks(conn)
        conn.commit()
        return results
    except Exception:
        conn.rollback()
        raise


def _logical_snapshot(
    conn: sqlite3.Connection,
    plan: Mapping[str, Any],
    inventory_reader: InventoryReader,
) -> Dict[str, Any]:
    releases = sorted({action["target"]["release_mbid"] for action in plan["actions"]})
    requests = []
    inventories = []
    for release in releases:
        request = _request_for_release(conn, release)
        if request is not None:
            requests.append(request)
        target = next(
            action["target"] for action in plan["actions"] if action["target"]["release_mbid"] == release
        )
        inventories.append(
            {
                "release_mbid": release,
                "inventory": _normalize_inventory(
                    inventory_reader(conn, target), "snapshot inventory"
                ),
            }
        )
    return {
        "queue_rows": _queue_snapshot(conn),
        "requests": requests,
        "media_inventory": inventories,
    }


def _fsync_path(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_path(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _sqlite_backup(source: sqlite3.Connection, destination: Path) -> str:
    if destination.exists():
        return file_sha256(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    if temporary.exists():
        raise ReconcileError("a stale temporary backup blocks reconciliation")
    backup = sqlite3.connect(str(temporary))
    try:
        source.backup(backup)
        backup.row_factory = sqlite3.Row
        _database_checks(backup)
    finally:
        backup.close()
    os.chmod(temporary, 0o600)
    _fsync_path(temporary)
    os.replace(temporary, destination)
    _fsync_path(destination.parent)
    return file_sha256(destination)


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ReconcileError(f"unable to read {label}") from error
    if not isinstance(value, dict):
        raise ReconcileError(f"{label} is invalid")
    return value


def _backup_logical_snapshot(
    backup_path: Path,
    plan: Mapping[str, Any],
    inventory_reader: InventoryReader,
) -> Dict[str, Any]:
    conn = _connect(backup_path, query_only=True)
    try:
        return _logical_snapshot(conn, plan, inventory_reader)
    finally:
        conn.close()


def _receipt_payload(
    *,
    plan: Mapping[str, Any],
    before_sha256: str,
    after_sha256: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    checks_before: Mapping[str, Any],
    checks_after: Mapping[str, Any],
    reconstructed: bool,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "apply",
        "tool_version": TOOL_VERSION,
        "run_id": plan["run_id"],
        "plan_sha256": plan["plan_sha256"],
        "completed_at": utc_now(),
        "reconstructed": reconstructed,
        "database": {
            "before_backup": "database.before.sqlite3",
            "before_sha256": before_sha256,
            "after_backup": "database.after.sqlite3",
            "after_sha256": after_sha256,
        },
        "logical_before": before,
        "logical_after": after,
        "actions": list(actions),
        "retired_duplicate": {
            "queue_item_id": RETIRED_DUPLICATE_QUEUE_ID,
            "retained_queue_item_id": RETAINED_WASTELAND_QUEUE_ID,
            "release_mbid": WASTELAND_RELEASE_MBID,
        },
        "checks_before": dict(checks_before),
        "checks_after": dict(checks_after),
    }


def _results_from_post(conn: sqlite3.Connection, plan: Mapping[str, Any]) -> List[Dict[str, Any]]:
    results = []
    for action in plan["actions"]:
        if action["action"] == "retire_duplicate":
            results.append(
                {
                    "action": "retire_duplicate",
                    "queue_item_id": RETIRED_DUPLICATE_QUEUE_ID,
                    "retained_queue_item_id": RETAINED_WASTELAND_QUEUE_ID,
                    "manifest_sha256": action["target"]["manifest_sha256"],
                }
            )
            continue
        request = _request_for_release(conn, action["target"]["release_mbid"])
        if request is None:
            raise ReconcileError("cannot reconstruct a missing request receipt")
        results.append(
            {
                "action": "blocked_detached" if _is_blocked(action) else "migrated",
                "queue_item_id": action["source"]["id"],
                "request_id": int(request["id"]),
                "release_mbid": action["target"]["release_mbid"],
                "manifest_sha256": action["target"]["manifest_sha256"],
            }
        )
    return results


def apply_plan(
    database: Path,
    plan: Mapping[str, Any],
    run_dir: Path,
    *,
    intent: str,
    resolver: ReleaseResolver,
    inventory_reader: InventoryReader = read_inventory,
) -> Dict[str, Any]:
    normalized = _normalize_plan(plan)
    if intent != normalized["plan_sha256"]:
        raise ReconcileError("apply intent does not equal the complete plan SHA-256")
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = run_dir / "receipt.json"
    rollback_receipt = run_dir / "rollback-receipt.json"
    if rollback_receipt.exists():
        raise ReconcileError("this run was rolled back and cannot be reapplied")
    before_backup = run_dir / "database.before.sqlite3"
    after_backup = run_dir / "database.after.sqlite3"

    conn = _connect(database)
    try:
        state = classify_state(conn, normalized)
        if state == "MIXED":
            raise ReconcileError("database is in a mixed reconciliation state")
        if state == "POST":
            _verify_fresh_releases(normalized, resolver)
            _verify_inventories(conn, normalized, inventory_reader)
            _verify_conflict_policies(normalized)
            checks_after = _database_checks(conn)
            before_sha = file_sha256(before_backup) if before_backup.exists() else ""
            if not before_sha:
                raise ReconcileError("POST state has no trusted before backup")
            before = _backup_logical_snapshot(before_backup, normalized, inventory_reader)
            if before["queue_rows"] != normalized["queue_snapshot"]:
                raise ReconcileError("before backup does not contain the sealed PRE state")
            after_sha = _sqlite_backup(conn, after_backup)
            after = _logical_snapshot(conn, normalized, inventory_reader)
            actions = _results_from_post(conn, normalized)
            if receipt_path.exists():
                receipt = _load_json(receipt_path, "receipt")
                if receipt.get("plan_sha256") != normalized["plan_sha256"]:
                    raise ReconcileError("receipt belongs to a different plan")
                return receipt
            receipt = _receipt_payload(
                plan=normalized,
                before_sha256=before_sha,
                after_sha256=after_sha,
                before=before,
                after=after,
                actions=actions,
                checks_before={"state": "PRE", "reconstructed": True},
                checks_after={**checks_after, "state": "POST"},
                reconstructed=True,
            )
            _atomic_json(receipt_path, receipt)
            return receipt

        checks_before = preflight(
            conn,
            normalized,
            resolver=resolver,
            inventory_reader=inventory_reader,
        )
        before_sha = _sqlite_backup(conn, before_backup)
        before = _logical_snapshot(conn, normalized, inventory_reader)
        if before["queue_rows"] != normalized["queue_snapshot"]:
            raise ReconcileError("before snapshot changed before mutation")
        actions = _apply_transaction(conn, normalized, inventory_reader)
        checks_after = {**_database_checks(conn), "state": classify_state(conn, normalized)}
        if checks_after["state"] != "POST":
            raise ReconcileError("committed database is not wholly POST")
        after_sha = _sqlite_backup(conn, after_backup)
        after = _logical_snapshot(conn, normalized, inventory_reader)
        receipt = _receipt_payload(
            plan=normalized,
            before_sha256=before_sha,
            after_sha256=after_sha,
            before=before,
            after=after,
            actions=actions,
            checks_before=checks_before,
            checks_after=checks_after,
            reconstructed=False,
        )
        _atomic_json(receipt_path, receipt)
        return receipt
    finally:
        conn.close()


def _insert_queue_snapshot(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO download_queue "
        "(id, search_query, playlist_id, status, last_attempt, mbid_guess) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        tuple(row[column] for column in QUEUE_COLUMNS),
    )


def rollback_plan(
    database: Path,
    plan: Mapping[str, Any],
    run_dir: Path,
    *,
    intent: str,
    inventory_reader: InventoryReader = read_inventory,
) -> Dict[str, Any]:
    normalized = _normalize_plan(plan)
    if intent != normalized["plan_sha256"]:
        raise ReconcileError("rollback intent does not equal the complete plan SHA-256")
    receipt_path = run_dir / "receipt.json"
    receipt = _load_json(receipt_path, "receipt")
    if receipt.get("plan_sha256") != normalized["plan_sha256"]:
        raise ReconcileError("receipt belongs to a different plan")
    rollback_path = run_dir / "rollback-receipt.json"
    if rollback_path.exists():
        existing = _load_json(rollback_path, "rollback receipt")
        return existing
    before_backup = run_dir / "database.before.sqlite3"
    if not before_backup.exists() or file_sha256(before_backup) != receipt.get("database", {}).get(
        "before_sha256"
    ):
        raise ReconcileError("trusted before backup is missing or changed")

    conn = _connect(database)
    try:
        if classify_state(conn, normalized) != "POST":
            raise ReconcileError("rollback requires the exact POST logical state")
        current = _logical_snapshot(conn, normalized, inventory_reader)
        if current != receipt.get("logical_after"):
            raise ReconcileError("logical or media state changed after apply; rollback refused")
        checks_before = _database_checks(conn)
        pre_rollback_path = run_dir / "database.pre-rollback.sqlite3"
        pre_rollback_sha = _sqlite_backup(conn, pre_rollback_path)

        conn.execute("BEGIN IMMEDIATE")
        try:
            if _logical_snapshot(conn, normalized, inventory_reader) != current:
                raise ReconcileError("state changed before rollback lock was acquired")
            inserted = {
                int(row["queue_item_id"]): row
                for row in receipt.get("actions", [])
                if row.get("request_id") is not None
            }
            for action in normalized["actions"]:
                if action["action"] == "retire_duplicate":
                    _insert_queue_snapshot(conn, action["source"])
                    continue
                result = inserted.get(action["source"]["id"])
                if result is None:
                    raise ReconcileError("receipt is missing an inserted request id")
                request_id = int(result["request_id"])
                request = _request_for_release(conn, action["target"]["release_mbid"])
                if request is None or int(request["id"]) != request_id:
                    raise ReconcileError("inserted request identity changed")
                conn.execute("DELETE FROM album_download_requests WHERE id = ?", (request_id,))
                if _is_blocked(action):
                    _insert_queue_snapshot(conn, action["source"])
                else:
                    post = {
                        "id": action["source"]["id"],
                        "search_query": _canonical_query(action["target"]),
                        "playlist_id": ALBUM_PLAYLIST_ID,
                        "status": "pending",
                        "last_attempt": None,
                        "mbid_guess": action["target"]["release_mbid"],
                    }
                    where, values = _exact_queue_where(post)
                    source = action["source"]
                    cursor = conn.execute(
                        "UPDATE download_queue SET search_query = ?, playlist_id = ?, "
                        "status = ?, last_attempt = ?, mbid_guess = ? "
                        f"WHERE {where}",
                        (
                            source["search_query"],
                            source["playlist_id"],
                            source["status"],
                            source["last_attempt"],
                            source["mbid_guess"],
                            *values,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ReconcileError("rollback exact queue predicate failed")
            if _queue_snapshot(conn) != normalized["queue_snapshot"]:
                raise ReconcileError("rollback did not restore the sealed queue")
            if not _pre_requests_absent(conn, normalized):
                raise ReconcileError("rollback left a migration tracker behind")
            duplicate_action = next(
                action for action in normalized["actions"] if action["action"] == "retire_duplicate"
            )
            if not _duplicate_guard(conn, duplicate_action):
                raise ReconcileError("rollback damaged the retained WASTELAND request")
            _database_checks(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        restored = _logical_snapshot(conn, normalized, inventory_reader)
        if restored != receipt.get("logical_before"):
            raise ReconcileError("restored state differs from the recorded PRE state")
        post_rollback_path = run_dir / "database.post-rollback.sqlite3"
        post_rollback_sha = _sqlite_backup(conn, post_rollback_path)
        checks_after = _database_checks(conn)
        rollback_receipt = {
            "schema_version": SCHEMA_VERSION,
            "kind": "rollback",
            "tool_version": TOOL_VERSION,
            "run_id": normalized["run_id"],
            "plan_sha256": normalized["plan_sha256"],
            "completed_at": utc_now(),
            "database": {
                "pre_rollback_backup": pre_rollback_path.name,
                "pre_rollback_sha256": pre_rollback_sha,
                "post_rollback_backup": post_rollback_path.name,
                "post_rollback_sha256": post_rollback_sha,
            },
            "logical_before": current,
            "logical_after": restored,
            "checks_before": checks_before,
            "checks_after": checks_after,
        }
        _atomic_json(rollback_path, rollback_receipt)
        return rollback_receipt
    finally:
        conn.close()


def dry_run(
    database: Path,
    plan: Mapping[str, Any],
    *,
    resolver: ReleaseResolver,
    inventory_reader: InventoryReader = read_inventory,
) -> Dict[str, Any]:
    normalized = _normalize_plan(plan)
    conn = _connect(database, query_only=True)
    try:
        checks = preflight(
            conn,
            normalized,
            resolver=resolver,
            inventory_reader=inventory_reader,
            require_pre=False,
        )
        state = checks["state"]
        return {
            "success": True,
            "mode": "dry-run",
            "run_id": normalized["run_id"],
            "plan_sha256": normalized["plan_sha256"],
            "state": state,
            "migration_count": len(EXPECTED_MIGRATION_QUEUE_IDS),
            "blocked_detach_count": sum(
                1 for action in normalized["actions"] if _is_blocked(action)
            ),
            "duplicate_retirement_count": 1,
            "checks": checks,
        }
    finally:
        conn.close()


def _cli_resolver(release_mbid: str) -> Any:
    from src import musicbrainz_client
    from src.services.album_download_request_service import (
        AlbumRequestResult,
        resolve_exact_release,
    )

    result = resolve_exact_release(
        release_mbid,
        get_release_by_id=musicbrainz_client.get_release_by_id,
    )
    if isinstance(result, AlbumRequestResult):
        message = str(result.payload.get("message") or "exact release validation failed")
        raise ReconcileError(message)
    return result


def _validate_plan_location(plan_path: Path, plan: Mapping[str, Any]) -> Path:
    if plan_path.name != "plan.json" or plan_path.parent.name != plan["run_id"]:
        raise ReconcileError("plan must be stored at <control-dir>/<run_id>/plan.json")
    return plan_path.parent


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--intent",
        help="complete plan SHA-256; required for --apply and --rollback",
    )
    args = parser.parse_args(argv)

    try:
        plan = load_plan(args.plan)
        run_dir = _validate_plan_location(args.plan, plan)
        if args.apply or args.rollback:
            if not args.intent:
                raise ReconcileError("mutation requires --intent with the complete plan SHA-256")
        if args.rollback:
            receipt = rollback_plan(
                args.database,
                plan,
                run_dir,
                intent=args.intent,
            )
            output = {
                "success": True,
                "mode": "rollback",
                "run_id": plan["run_id"],
                "plan_sha256": plan["plan_sha256"],
                "state": "PRE",
                "receipt": "rollback-receipt.json",
                "completed_at": receipt["completed_at"],
            }
        elif args.apply:
            receipt = apply_plan(
                args.database,
                plan,
                run_dir,
                intent=args.intent,
                resolver=_cli_resolver,
            )
            output = {
                "success": True,
                "mode": "apply",
                "run_id": plan["run_id"],
                "plan_sha256": plan["plan_sha256"],
                "state": "POST",
                "receipt": "receipt.json",
                "completed_at": receipt["completed_at"],
                "reconstructed": bool(receipt.get("reconstructed")),
            }
        else:
            output = dry_run(args.database, plan, resolver=_cli_resolver)
        print(json.dumps(output, sort_keys=True))
        return 0
    except ReconcileError as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": error.__class__.__name__,
                    "message": str(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
