import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.services.contribution_service import (
    CONTRIBUTION_STATUSES,
    TERMINAL_STATUSES,
    attempt_timeout_seconds,
    contribution_age_seconds,
    evaluate_contribution,
    find_acceptable_local_copy,
    offer_contribution,
    parse_quality_fields,
    poll_contribution,
    process_contribution_upload,
    verify_upload,
)


class FakeContributionStore:
    def __init__(self):
        self.rows = {}
        self.created = []
        self.candidates = []
        self.download_statuses = {}
        self.active_download_id = None
        self.queued = []
        self.removed = []

    def find_local_tracks_by_identity(self, **_identity):
        return list(self.candidates)

    def create_contribution(self, **values):
        contribution_id = len(self.rows) + 1
        row = {"id": contribution_id, **values}
        self.rows[contribution_id] = row
        self.created.append(dict(values))
        return contribution_id

    def get_contribution(self, contribution_id):
        row = self.rows.get(contribution_id)
        return dict(row) if row else None

    def list_contributions(self, limit=200):
        return [dict(row) for row in list(self.rows.values())[:limit]]

    def list_contributed(self, limit=200):
        return []

    def update_contribution(self, contribution_id, **fields):
        self.rows[contribution_id].update(fields)

    def get_download_status(self, item_id):
        return self.download_statuses.get(item_id)

    def get_active_download_id(self, _mbid, _query):
        return self.active_download_id

    def queue_download(self, item):
        self.queued.append(item)
        item_id = len(self.queued) + 10
        self.download_statuses[item_id] = "pending"
        return item_id

    def remove_from_queue(self, item_id):
        self.removed.append(item_id)


class FakeUpload:
    def __init__(self, filename, content):
        self.filename = filename
        self.content = content

    def save(self, destination):
        Path(destination).write_bytes(self.content)


def test_status_contract_timeout_and_age_helpers_are_exact():
    assert CONTRIBUTION_STATUSES == {
        "attempting",
        "have_better",
        "satisfied",
        "needs_upload",
        "ingested",
    }
    assert TERMINAL_STATUSES == {"have_better", "satisfied", "ingested"}
    assert attempt_timeout_seconds(object()) == 3600
    assert attempt_timeout_seconds({"contribution_attempt_timeout_seconds": "15"}) == 15
    assert attempt_timeout_seconds({"contribution_attempt_timeout_seconds": "bad"}) == 3600
    assert contribution_age_seconds(
        {"created_at": "2026-07-15 09:00:00"},
        now=datetime(2026, 7, 15, 9, 0, 12, tzinfo=timezone.utc),
    ) == 12
    assert contribution_age_seconds({"created_at": "not-a-date"}) is None


def test_local_copy_metadata_fallback_enforces_duration_but_mbid_does_not():
    db = FakeContributionStore()
    db.candidates = [{
        "mbid": "local",
        "local_path": "/music/copy.flac",
        "identity_match": "artist_title_album",
    }]
    target = {"lossless": True, "length_ms": 120_000}
    quality = {"lossless": True, "length_ms": 100_000}
    common = {
        "path_exists": lambda _path: True,
        "read_quality": lambda _path: quality,
        "meets_target": lambda _candidate, _target: True,
    }

    assert find_acceptable_local_copy(db, {"artist": "A", "title": "B"}, target, **common) == (None, None)

    db.candidates[0]["identity_match"] = "mbid"
    assert find_acceptable_local_copy(db, {"mbid": "local"}, target, **common) == (db.candidates[0], quality)


def test_evaluate_contribution_transitions_pending_attempts_and_satisfaction():
    db = FakeContributionStore()
    db.rows[1] = {
        "id": 1,
        "status": "attempting",
        "download_id": 7,
        "created_at": "2026-07-15 09:00:00",
        "target_quality": json.dumps({"lossless": True}),
    }
    db.download_statuses[7] = "pending"

    current = evaluate_contribution(
        db,
        db.get_contribution(1),
        timeout_seconds=10,
        now=datetime(2026, 7, 15, 9, 0, 11, tzinfo=timezone.utc),
        find_local_copy=lambda _db, _row, _target: (None, None),
    )
    assert current["status"] == "needs_upload"

    db.rows[2] = {
        "id": 2,
        "status": "attempting",
        "download_id": 8,
        "target_quality": json.dumps({"lossless": True}),
    }
    local = {"local_path": "/music/good.flac"}
    current = evaluate_contribution(
        db,
        db.get_contribution(2),
        find_local_copy=lambda _db, _row, _target: (
            local,
            {"lossless": True},
        ),
    )
    assert current["status"] == "satisfied"
    assert json.loads(current["acquired_quality"]) == {"lossless": True}


def test_offer_reuses_active_download_and_preserves_wire_shape():
    db = FakeContributionStore()
    db.active_download_id = 44
    result = offer_contribution(
        db,
        {
            "device_id": "sat-1",
            "mbid": " recording-1 ",
            "artist": " Artist ",
            "title": " Track ",
            "album": "Album",
            "quality": {"lossless": True},
        },
        find_local_copy=lambda _db, _identity, _target: (None, None),
    )

    assert result.status_code == 200
    assert result.payload == {
        "success": True,
        "contribution_id": 1,
        "status": "attempting",
    }
    assert db.queued == []
    assert db.created[0]["download_id"] == 44
    assert db.created[0]["mbid"] == "recording-1"
    assert db.created[0]["artist"] == "Artist"


def test_offer_have_better_and_poll_unknown_shapes_are_exact():
    db = FakeContributionStore()
    result = offer_contribution(
        db,
        {"artist": "A", "title": "B", "mbid": "m"},
        find_local_copy=lambda _db, _identity, _target: (
            {"local_path": "/music/b.flac"},
            {"lossless": True},
        ),
    )
    assert result.payload == {
        "success": True,
        "contribution_id": 1,
        "status": "have_better",
    }
    assert poll_contribution(db, 999).payload == {
        "success": False,
        "message": "unknown contribution",
    }
    assert poll_contribution(db, 999).status_code == 404


def test_quality_fields_parse_valid_json_and_leave_invalid_values_unchanged():
    rows = [{
        "target_quality": '{"lossless": true}',
        "acquired_quality": "not-json",
    }]
    assert parse_quality_fields(rows) == [{
        "target_quality": {"lossless": True},
        "acquired_quality": "not-json",
    }]


def test_verify_upload_preserves_empty_truncated_and_quality_messages():
    assert verify_upload("x", None, get_size=lambda _path: 0) == "uploaded file is empty"
    assert verify_upload(
        "x", {"size_bytes": 100}, get_size=lambda _path: 49
    ) == "uploaded file is truncated (49 bytes vs promised ~100)"
    assert verify_upload(
        "x",
        {"size_bytes": 0},
        get_size=lambda _path: 50,
        read_quality=lambda _path: {"lossless": False},
        meets_target=lambda _candidate, _target: False,
    ) == "uploaded file is lower quality than promised"


def test_upload_rejection_cleans_unique_stage_and_remains_retryable(tmp_path):
    db = FakeContributionStore()
    db.rows[1] = {
        "id": 1,
        "status": "needs_upload",
        "target_quality": None,
    }
    result = process_contribution_upload(
        db,
        1,
        FakeUpload("song.flac", b"bad"),
        downloads_dir=str(tmp_path / "downloads"),
        music_library=str(tmp_path / "music"),
        picard_path="",
        verify=lambda _path, _target: "uploaded file is lower quality than promised",
    )

    assert result.status_code == 422
    assert result.payload == {
        "success": False,
        "status": "rejected",
        "message": "uploaded file is lower quality than promised",
    }
    assert db.rows[1]["status"] == "needs_upload"
    assert list((tmp_path / "downloads" / "_contrib").iterdir()) == []


def test_upload_ingests_and_clears_moot_download(tmp_path):
    db = FakeContributionStore()
    db.rows[1] = {
        "id": 1,
        "status": "needs_upload",
        "target_quality": None,
        "download_id": 17,
        "mbid": "m1",
        "artist": "Artist",
        "title": "Track",
        "album": "Album",
    }
    destination = tmp_path / "music" / "Artist" / "Album" / "Track.flac"

    def ingest(_db, _scanner, _library, source, **identity):
        assert identity == {
            "mbid_guess": "m1",
            "artist": "Artist",
            "title": "Track",
            "album": "Album",
        }
        destination.parent.mkdir(parents=True)
        os.replace(source, destination)
        return str(destination)

    result = process_contribution_upload(
        db,
        1,
        FakeUpload("track.flac", b"audio"),
        downloads_dir=str(tmp_path / "downloads"),
        music_library=str(tmp_path / "music"),
        picard_path="picard",
        verify=lambda _path, _target: None,
        find_local_copy=lambda _db, _identity, _target: (None, None),
        scanner_factory=lambda _db, path: SimpleNamespace(picard_path=path),
        ingest_audio=ingest,
        read_quality=lambda _path: {"lossless": True},
    )

    assert result.payload == {
        "success": True,
        "status": "ingested",
        "local_path": str(destination),
    }
    assert destination.read_bytes() == b"audio"
    assert db.rows[1]["status"] == "ingested"
    assert json.loads(db.rows[1]["acquired_quality"]) == {"lossless": True}
    assert db.removed == [17]
    assert list((tmp_path / "downloads" / "_contrib").iterdir()) == []
