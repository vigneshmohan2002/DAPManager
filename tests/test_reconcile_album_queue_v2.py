import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
from uuid import UUID, uuid5

import pytest

from src.db_manager import DatabaseManager
from src.services.album_download_request_service import ResolvedAlbum, ResolvedTrack


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "reconcile-album-queue-v2.py"
SPEC = importlib.util.spec_from_file_location("reconcile_album_queue_v2", SCRIPT_PATH)
assert SPEC and SPEC.loader
reconcile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reconcile
SPEC.loader.exec_module(reconcile)


NAMESPACE = UUID("2d0564ff-ae05-42b2-b9ff-969433d5c02f")


def _uuid(label):
    return str(uuid5(NAMESPACE, label))


def _album(label, *, release_mbid=None, title=None):
    release = release_mbid or _uuid(f"release-{label}")
    recording = _uuid(f"recording-{label}")
    track = ResolvedTrack(
        position=1,
        medium_position=1,
        track_position=1,
        track_number="1",
        recording_mbid=recording,
        title=f"Track {label}",
        artist=f"Artist {label}",
        date="2026-07-20",
        track_total=1,
        disc_total=1,
        release_track_mbid=_uuid(f"release-track-{label}"),
    )
    return ResolvedAlbum(
        release_mbid=release,
        title=title or f"Album {label}",
        artist=f"Artist {label}",
        track_count=1,
        date="2026-07-20",
        country="GB",
        status="Official",
        disambiguation="",
        primary_type="Album",
        format="Digital Media",
        label=f"Label {label}",
        catalog_number=f"CAT-{label}",
        barcode="",
        tracks=(track,),
    )


def _insert_queue(conn, row):
    conn.execute(
        "INSERT INTO download_queue "
        "(id, search_query, playlist_id, status, last_attempt, mbid_guess) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        tuple(row[column] for column in reconcile.QUEUE_COLUMNS),
    )


def _insert_request(conn, queue_item_id, target, *, stage="failed"):
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
            "Existing exact request",
            0,
        ),
    )
    request_id = int(cursor.lastrowid)
    for track in target["manifest"]:
        conn.execute(
            "INSERT INTO album_download_request_tracks "
            "(request_id, position, recording_mbid, medium_position, track_position, "
            "track_number, title, artist, date, track_total, disc_total, release_track_mbid) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )
    return request_id


def _fixture(tmp_path, *, conflict_id=None, conflict_policy="abort"):
    database = tmp_path / "library.sqlite3"
    manager = DatabaseManager(str(database))
    conn = manager.conn
    albums = {}
    rows = []
    for queue_id in sorted(reconcile.EXPECTED_MIGRATION_QUEUE_IDS):
        row = {
            "id": queue_id,
            "search_query": f"Legacy Artist {queue_id} - Legacy Album {queue_id}",
            "playlist_id": "AUDIT",
            "status": "failed" if queue_id in {21, 22, 24} else "pending",
            "last_attempt": "2026-07-19 01:02:03" if queue_id == 21 else None,
            "mbid_guess": f"legacy-{queue_id}",
        }
        _insert_queue(conn, row)
        rows.append(row)
        albums[queue_id] = _album(str(queue_id))

    wasteland = _album(
        "wasteland",
        release_mbid=reconcile.WASTELAND_RELEASE_MBID,
        title="WASTELAND",
    )
    # The real release artist is part of the canonical queue identity.
    wasteland = ResolvedAlbum(
        **{
            **wasteland.__dict__,
            "artist": "Brent Faiyaz",
            "tracks": tuple(
                ResolvedTrack(**{**track.__dict__, "artist": "Brent Faiyaz"})
                for track in wasteland.tracks
            ),
        }
    )
    wasteland_target = reconcile.target_from_resolved(wasteland)
    row25 = {
        "id": 25,
        "search_query": "Brent Faiyaz - WASTELAND",
        "playlist_id": "AUDIT",
        "status": "failed",
        "last_attempt": None,
        "mbid_guess": reconcile.WASTELAND_RELEASE_MBID,
    }
    row50 = {
        "id": 50,
        "search_query": reconcile._canonical_query(wasteland_target),
        "playlist_id": reconcile.ALBUM_PLAYLIST_ID,
        "status": "failed",
        "last_attempt": "2026-07-20 09:30:00",
        "mbid_guess": reconcile.WASTELAND_RELEASE_MBID,
    }
    _insert_queue(conn, row25)
    _insert_queue(conn, row50)
    rows.extend((row25, row50))
    _insert_request(conn, 50, wasteland_target)

    if conflict_id is not None:
        target = reconcile.target_from_resolved(albums[conflict_id])
        track = target["manifest"][0]
        conflict_path = tmp_path / f"conflict-{conflict_id}.flac"
        conflict_path.write_bytes(b"fixture")
        conn.execute(
            "INSERT INTO tracks "
            "(mbid, title, artist, album, local_path, release_mbid, track_number, disc_number) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (
                track["recording_mbid"],
                track["title"],
                track["artist"],
                "Other Album",
                str(conflict_path),
                _uuid(f"other-owner-{conflict_id}"),
            ),
        )
    conn.commit()

    actions = []
    row_by_id = {row["id"]: row for row in rows}
    resolved = {}
    for queue_id in sorted(reconcile.EXPECTED_MIGRATION_QUEUE_IDS):
        album = albums[queue_id]
        target = reconcile.target_from_resolved(album)
        resolved[target["release_mbid"]] = album
        actions.append(
            {
                "action": "migrate",
                "source": copy.deepcopy(row_by_id[queue_id]),
                "target": target,
                "inventory": reconcile.read_inventory(conn, target),
                "ownership_conflict_policy": (
                    conflict_policy if queue_id == conflict_id else "abort"
                ),
            }
        )
    resolved[wasteland_target["release_mbid"]] = wasteland
    actions.append(
        {
            "action": "retire_duplicate",
            "source": copy.deepcopy(row25),
            "survivor": copy.deepcopy(row50),
            "target": wasteland_target,
            "inventory": reconcile.read_inventory(conn, wasteland_target),
        }
    )
    queue_snapshot = reconcile._queue_snapshot(conn)
    payload = {
        "schema_version": reconcile.SCHEMA_VERSION,
        "run_id": "audit-20260720",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tool_version": reconcile.TOOL_VERSION,
        "queue_snapshot": queue_snapshot,
        "actions": actions,
    }
    plan = reconcile.seal_plan(payload)

    def resolver(release_mbid):
        return resolved[release_mbid]

    manager.close()
    return database, plan, resolver, resolved


def test_seal_detects_any_plan_tampering(tmp_path):
    _, plan, _, _ = _fixture(tmp_path)
    reconcile._normalize_plan(plan)
    plan["actions"][0]["source"]["status"] = "success"
    with pytest.raises(reconcile.ReconcileError, match="seal"):
        reconcile._normalize_plan(plan)


def test_dry_run_apply_and_idempotent_receipt_reconstruction(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    dry = reconcile.dry_run(database, plan, resolver=resolver)
    assert dry["state"] == "PRE"
    assert dry["migration_count"] == 20

    run_dir = tmp_path / plan["run_id"]
    receipt = reconcile.apply_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
        resolver=resolver,
    )
    assert receipt["reconstructed"] is False
    assert (run_dir / "database.before.sqlite3").is_file()
    assert (run_dir / "database.after.sqlite3").is_file()
    assert (run_dir / "receipt.json").is_file()

    conn = reconcile._connect(database, query_only=True)
    try:
        assert reconcile.classify_state(conn, reconcile._normalize_plan(plan)) == "POST"
        assert not conn.execute(
            "SELECT 1 FROM download_queue WHERE id = 25"
        ).fetchone()
        migrated = conn.execute(
            "SELECT * FROM download_queue WHERE id = 21"
        ).fetchone()
        assert migrated["playlist_id"] == reconcile.ALBUM_PLAYLIST_ID
        assert migrated["status"] == "pending"
        assert migrated["last_attempt"] is None
        tracker_count = conn.execute(
            "SELECT COUNT(*) FROM album_download_requests WHERE queue_item_id != 50"
        ).fetchone()[0]
        assert tracker_count == 20
    finally:
        conn.close()

    (run_dir / "receipt.json").unlink()
    reconstructed = reconcile.apply_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
        resolver=resolver,
    )
    assert reconstructed["reconstructed"] is True


def test_exact_source_change_is_mixed_and_never_backed_up(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    conn = reconcile._connect(database)
    conn.execute("UPDATE download_queue SET status = 'success' WHERE id = 21")
    conn.commit()
    conn.close()

    run_dir = tmp_path / plan["run_id"]
    with pytest.raises(reconcile.ReconcileError, match="mixed"):
        reconcile.apply_plan(
            database,
            plan,
            run_dir,
            intent=plan["plan_sha256"],
            resolver=resolver,
        )
    assert not (run_dir / "database.before.sqlite3").exists()


def test_cross_release_ownership_defaults_to_abort(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path, conflict_id=21)
    with pytest.raises(reconcile.ReconcileError, match="blocked_detach"):
        reconcile.dry_run(database, plan, resolver=resolver)


def test_explicit_ownership_conflict_becomes_detached_failed_tracker(tmp_path):
    database, plan, resolver, _ = _fixture(
        tmp_path,
        conflict_id=21,
        conflict_policy="blocked_detach",
    )
    run_dir = tmp_path / plan["run_id"]
    reconcile.apply_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
        resolver=resolver,
    )
    target = next(
        action["target"] for action in plan["actions"] if action["source"]["id"] == 21
    )
    conn = reconcile._connect(database, query_only=True)
    try:
        assert conn.execute("SELECT 1 FROM download_queue WHERE id = 21").fetchone() is None
        tracker = conn.execute(
            "SELECT * FROM album_download_requests WHERE release_mbid = ?",
            (target["release_mbid"],),
        ).fetchone()
        assert tracker["queue_item_id"] is None
        assert tracker["stage"] == "failed"
        assert "owned by another live release" in tracker["detail"]
    finally:
        conn.close()


def test_cross_plan_recording_collision_is_rejected(tmp_path):
    _, plan, _, _ = _fixture(tmp_path)
    first = plan["actions"][0]["target"]
    second = plan["actions"][1]["target"]
    second["manifest"][0]["recording_mbid"] = first["manifest"][0]["recording_mbid"]
    second["manifest_sha256"] = reconcile.json_sha256(second["manifest"])
    target_without_hashes = dict(second)
    target_without_hashes.pop("manifest_sha256")
    target_without_hashes.pop("target_sha256")
    second["target_sha256"] = reconcile.json_sha256(target_without_hashes)
    with pytest.raises(reconcile.ReconcileError, match="more than one target release"):
        reconcile.seal_plan(plan)


def test_row25_contribution_guard_fails_closed(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    conn = reconcile._connect(database)
    conn.execute(
        "INSERT INTO contributions (download_id, status) VALUES (25, 'attempting')"
    )
    conn.commit()
    conn.close()
    with pytest.raises(reconcile.ReconcileError, match="mixed"):
        reconcile.dry_run(database, plan, resolver=resolver)


def test_fresh_musicbrainz_identity_mismatch_aborts_before_backup(tmp_path):
    database, plan, resolver, resolved = _fixture(tmp_path)
    changed_release = plan["actions"][0]["target"]["release_mbid"]
    changed = resolved[changed_release]
    changed = ResolvedAlbum(**{**changed.__dict__, "title": "Changed upstream"})

    def drifted(release_mbid):
        return changed if release_mbid == changed_release else resolver(release_mbid)

    run_dir = tmp_path / plan["run_id"]
    with pytest.raises(reconcile.ReconcileError, match="no longer matches"):
        reconcile.apply_plan(
            database,
            plan,
            run_dir,
            intent=plan["plan_sha256"],
            resolver=drifted,
        )
    assert not (run_dir / "database.before.sqlite3").exists()


def test_inventory_drift_aborts_before_backup(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    action = plan["actions"][0]
    track = action["target"]["manifest"][0]
    new_file = tmp_path / "new.flac"
    new_file.write_bytes(b"fixture")
    conn = reconcile._connect(database)
    conn.execute(
        "INSERT INTO tracks (mbid, title, artist, local_path, release_mbid) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            track["recording_mbid"],
            track["title"],
            track["artist"],
            str(new_file),
            action["target"]["release_mbid"],
        ),
    )
    conn.commit()
    conn.close()
    run_dir = tmp_path / plan["run_id"]
    with pytest.raises(reconcile.ReconcileError, match="inventory"):
        reconcile.apply_plan(
            database,
            plan,
            run_dir,
            intent=plan["plan_sha256"],
            resolver=resolver,
        )
    assert not (run_dir / "database.before.sqlite3").exists()


def test_inventory_only_counts_existing_local_files(tmp_path):
    database, plan, _, _ = _fixture(tmp_path)
    action = plan["actions"][0]
    target = action["target"]
    track = target["manifest"][0]
    missing_path = tmp_path / "not-created.flac"
    conn = reconcile._connect(database)
    conn.execute(
        "INSERT INTO tracks (mbid, title, artist, local_path, release_mbid) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            track["recording_mbid"],
            track["title"],
            track["artist"],
            str(missing_path),
            _uuid("different-release-owner"),
        ),
    )
    conn.commit()
    assert reconcile.read_inventory(conn, target) == {
        "completed_recording_mbids": [],
        "cross_release_ownership": [],
    }

    missing_path.write_bytes(b"fixture")
    inventory = reconcile.read_inventory(conn, target)
    assert inventory["completed_recording_mbids"] == []
    assert inventory["cross_release_ownership"] == [
        {
            "recording_mbid": track["recording_mbid"],
            "release_mbid": _uuid("different-release-owner"),
        }
    ]
    conn.close()


def test_rollback_restores_exact_pre_state(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    run_dir = tmp_path / plan["run_id"]
    apply_receipt = reconcile.apply_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
        resolver=resolver,
    )
    rollback = reconcile.rollback_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
    )
    assert rollback["logical_after"] == apply_receipt["logical_before"]
    conn = reconcile._connect(database, query_only=True)
    try:
        assert reconcile.classify_state(conn, reconcile._normalize_plan(plan)) == "PRE"
    finally:
        conn.close()


def test_rollback_refuses_when_post_state_has_advanced(tmp_path):
    database, plan, resolver, _ = _fixture(tmp_path)
    run_dir = tmp_path / plan["run_id"]
    reconcile.apply_plan(
        database,
        plan,
        run_dir,
        intent=plan["plan_sha256"],
        resolver=resolver,
    )
    conn = reconcile._connect(database)
    conn.execute(
        "UPDATE album_download_requests SET stage = 'downloading' WHERE queue_item_id = 21"
    )
    conn.commit()
    conn.close()
    with pytest.raises(reconcile.ReconcileError, match="exact POST"):
        reconcile.rollback_plan(
            database,
            plan,
            run_dir,
            intent=plan["plan_sha256"],
        )
    assert not (run_dir / "database.pre-rollback.sqlite3").exists()


def test_cli_requires_full_intent_and_only_prints_safe_summary(tmp_path, capsys):
    database, plan, _, _ = _fixture(tmp_path)
    run_dir = tmp_path / plan["run_id"]
    run_dir.mkdir()
    plan_path = run_dir / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result = reconcile.main(
        [
            "--database",
            str(database),
            "--plan",
            str(plan_path),
            "--apply",
        ]
    )
    assert result == 2
    error = json.loads(capsys.readouterr().err)
    assert error["success"] is False
    assert "--intent" in error["message"]
    assert "Legacy Artist" not in json.dumps(error)
