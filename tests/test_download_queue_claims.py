"""Durable ownership and bounded-retry tests for the download queue."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from src.db_manager import DatabaseManager, DownloadItem


T0 = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _queue(db: DatabaseManager, query: str = "Artist - Album") -> int:
    return db.queue_download(DownloadItem(
        search_query=query,
        playlist_id="SATELLITE_ALBUM",
        mbid_guess=f"release-{query}",
    ))


def _queue_album(db: DatabaseManager):
    return db.create_download_and_album_request(
        release_mbid="95fb59ed-1ece-419b-b62f-aef31e0ebf36",
        search_query="::ALBUM:: Artist - Album",
        playlist_id="SATELLITE_ALBUM",
        artist="Artist",
        title="Album",
        track_count=1,
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=("00000000-0000-4000-8000-000000000001",),
    )


def test_claim_next_supports_narrow_allowlist_and_per_run_exclusions(db):
    first_id = _queue(db, "First")
    second_id = _queue(db, "Second")

    assert db.count_claimable_downloads(now=T0) == 2
    assert db.count_claimable_downloads(
        now=T0,
        include_item_ids={second_id},
    ) == 1
    assert db.count_claimable_downloads(
        now=T0,
        exclude_item_ids={first_id, second_id},
    ) == 0

    claimed = db.claim_next_download(
        "runner-a",
        now=T0,
        include_item_ids={second_id},
    )

    assert claimed is not None
    assert claimed.id == second_id
    assert claimed.status == "pending"
    assert claimed.claim_owner == "runner-a"
    assert claimed.attempt_count == 1
    assert claimed.max_attempts == 3
    assert claimed.last_attempt == T0.replace(tzinfo=None)
    assert claimed.claim_heartbeat_at == T0.replace(tzinfo=None)
    assert db.claim_next_download(
        "runner-b",
        now=T0,
        include_item_ids={second_id},
    ) is None
    assert db.count_claimable_downloads(now=T0) == 1
    assert db.claim_next_download(
        "runner-b",
        now=T0,
        exclude_item_ids={first_id, second_id},
    ) is None


def test_idempotent_target_key_returns_existing_active_queue_row(db):
    first = db.queue_download(DownloadItem(
        "::ALBUM:: Artist - Album",
        "SATELLITE_ALBUM",
        "95FB59ED-1ECE-419B-B62F-AEF31E0EBF36",
    ))
    duplicate = db.queue_download(DownloadItem(
        "::ALBUM:: differently formatted display text",
        "SATELLITE_ALBUM",
        "95fb59ed-1ece-419b-b62f-aef31e0ebf36",
    ))

    assert duplicate == first
    rows = db.get_all_downloads()
    assert len(rows) == 1
    assert rows[0].target_key == (
        "release:95fb59ed-1ece-419b-b62f-aef31e0ebf36"
    )


def test_non_consuming_claim_defers_retry_budget_until_network_start(db):
    item_id = _queue(db)
    claimed = db.claim_next_download(
        "worker", now=T0, consume_attempt=False
    )
    assert claimed is not None
    assert claimed.attempt_count == 0
    assert claimed.last_attempt is None

    started = db.start_download_network_attempt(
        item_id, "worker", strategy_index=1, now=T0 + timedelta(seconds=1)
    )
    assert started is not None
    assert started.attempt_count == 1
    assert started.strategy_index == 1
    assert started.phase == "acquiring"


def test_worker_lease_is_singleton_and_pause_is_durable(db):
    assert db.get_download_worker_state()["is_paused"] == 1
    db.set_download_worker_paused(False)
    assert db.claim_download_worker("worker-a", 60, now=T0) is True
    assert db.claim_download_worker("worker-b", 60, now=T0) is False
    assert db.heartbeat_download_worker(
        "worker-a",
        60,
        state="running",
        current_item_id=42,
        detail="Acquiring exact release",
        now=T0 + timedelta(seconds=10),
    ) is True
    state = db.get_download_worker_state()
    assert state["state"] == "running"
    assert state["current_item_id"] == 42

    db.set_download_worker_paused(True)
    assert db.heartbeat_download_worker(
        "worker-a", 60, state="running", now=T0 + timedelta(seconds=11)
    ) is False
    assert db.release_download_worker("worker-a", "paused") is True
    assert db.get_download_worker_state()["is_paused"] == 1


def test_attempt_history_is_sanitized_callers_persisted_after_queue_success(db):
    item_id = _queue(db)
    attempt_id = db.create_download_attempt(
        item_id,
        "release:release-artist - album",
        strategy="musicbrainz-release",
    )
    assert db.update_download_attempt(attempt_id, {
        "phase": "finished",
        "outcome": "success",
        "detail": "Verified 10 tracks",
        "network_started": True,
        "files_validated": 10,
        "finished_at": "2026-07-22 12:10:00",
    }) is True
    attempts = db.list_download_attempts(item_id)
    assert attempts[0]["strategy"] == "musicbrainz-release"
    assert attempts[0]["files_validated"] == 10
    db.record_download_attempt_files(attempt_id, [{
        "relative_name": "Disc 1/01.flac",
        "recording_mbid": "00000000-0000-4000-8000-000000000001",
        "decision": "candidate",
        "bytes": 123,
    }])
    db.finalize_download_attempt_files(attempt_id, "accepted", "Exact match")
    files = db.list_download_attempt_files(attempt_id)
    assert files[0]["relative_name"] == "Disc 1/01.flac"
    assert files[0]["decision"] == "accepted"
    assert files[0]["reason"] == "Exact match"

    claimed = db.claim_next_download("worker", now=T0)
    assert claimed is not None
    assert db.complete_download_claim(
        item_id, "worker", now=T0 + timedelta(seconds=1)
    ) is True
    row = db.conn.execute(
        "SELECT queue_item_id, target_key, outcome FROM download_attempts "
        "WHERE id = ?",
        (attempt_id,),
    ).fetchone()
    assert row["queue_item_id"] is None
    assert row["target_key"] == "release:release-artist - album"
    assert row["outcome"] == "success"


def test_claim_heartbeat_release_pause_and_quarantine_are_owner_safe(db):
    item_id = _queue(db)
    claimed = db.claim_next_download("runner-a", lease_seconds=60, now=T0)
    assert claimed is not None

    assert db.heartbeat_download_claim(
        item_id,
        "wrong-owner",
        lease_seconds=60,
        now=T0 + timedelta(seconds=30),
    ) is False
    assert db.heartbeat_download_claim(
        item_id,
        "runner-a",
        lease_seconds=60,
        now=T0 + timedelta(seconds=30),
    ) is True
    refreshed = db.get_all_downloads()[0]
    assert refreshed.claim_expires_at == (
        T0 + timedelta(seconds=90)
    ).replace(tzinfo=None)

    assert db.release_download_claim(item_id, "wrong-owner") is False
    assert db.release_download_claim(item_id, "runner-a") is True
    assert db.set_download_paused(item_id, True) is True
    assert db.claim_next_download("runner-b", now=T0) is None
    assert db.set_download_paused(item_id, False) is True
    assert db.set_download_quarantined(item_id, True) is True
    assert db.claim_next_download("runner-b", now=T0) is None


def test_failure_uses_exponential_backoff_then_quarantines_at_cap(db):
    item_id = _queue(db)
    assert db.set_download_retry_limit(item_id, 3) is True

    first = db.claim_next_download("runner-1", now=T0)
    assert first is not None and first.attempt_count == 1
    assert db.fail_download_claim(
        item_id,
        "runner-1",
        "first failure",
        base_delay_seconds=60,
        max_delay_seconds=600,
        now=T0 + timedelta(seconds=10),
    ) is True
    failed = db.get_all_downloads()[0]
    assert failed.status == "failed"
    assert failed.last_error == "first failure"
    assert failed.next_attempt_at == (
        T0 + timedelta(seconds=70)
    ).replace(tzinfo=None)
    assert failed.is_quarantined is False
    assert failed.claim_owner is None
    assert db.claim_next_download(
        "too-early",
        now=T0 + timedelta(seconds=69),
    ) is None

    second_at = T0 + timedelta(seconds=70)
    second = db.claim_next_download("runner-2", now=second_at)
    assert second is not None and second.attempt_count == 2
    assert db.fail_download_claim(
        item_id,
        "runner-2",
        "second failure",
        base_delay_seconds=60,
        max_delay_seconds=600,
        now=second_at + timedelta(seconds=10),
    ) is True
    second_failure = db.get_all_downloads()[0]
    assert second_failure.next_attempt_at == (
        second_at + timedelta(seconds=130)
    ).replace(tzinfo=None)

    third_at = second_at + timedelta(seconds=130)
    third = db.claim_next_download("runner-3", now=third_at)
    assert third is not None and third.attempt_count == 3
    assert db.fail_download_claim(
        item_id,
        "runner-3",
        "retry budget exhausted",
        base_delay_seconds=60,
        max_delay_seconds=600,
        now=third_at + timedelta(seconds=10),
    ) is True
    exhausted = db.get_all_downloads()[0]
    assert exhausted.status == "failed"
    assert exhausted.attempt_count == 3
    assert exhausted.is_quarantined is True
    assert exhausted.next_attempt_at is None
    assert db.claim_next_download(
        "runner-4",
        now=third_at + timedelta(days=1),
    ) is None

    # Explicit user retry remains compatible and replenishes the budget.
    assert db.retry_download(item_id) is True
    reset = db.get_all_downloads()[0]
    assert reset.status == "pending"
    assert reset.attempt_count == 0
    assert reset.is_quarantined is False
    assert reset.next_attempt_at is None
    assert reset.last_error is None


def test_permanent_failure_can_be_quarantined_owner_atomically(db):
    item_id = _queue(db)
    assert db.claim_next_download("runner", now=T0) is not None

    assert db.fail_download_claim(
        item_id,
        "runner",
        "exact release manifest is invalid",
        quarantine=True,
        now=T0 + timedelta(seconds=1),
    ) is True
    failed = db.get_all_downloads()[0]
    assert failed.is_quarantined is True
    assert failed.attempt_count == 1
    assert failed.next_attempt_at is None


def test_stale_owner_cannot_fail_or_complete_work_reclaimed_by_another_db(
    tmp_path,
):
    db_path = str(tmp_path / "queue.sqlite")
    first_db = DatabaseManager(db_path)
    second_db = DatabaseManager(db_path)
    try:
        item_id = _queue(first_db)
        assert first_db.claim_next_download(
            "stale-runner",
            lease_seconds=60,
            now=T0,
        ) is not None
        assert second_db.claim_next_download(
            "other-runner",
            now=T0 + timedelta(seconds=30),
        ) is None

        reclaimed = second_db.claim_next_download(
            "replacement-runner",
            lease_seconds=60,
            now=T0 + timedelta(seconds=61),
        )
        assert reclaimed is not None
        assert reclaimed.id == item_id
        assert reclaimed.attempt_count == 2
        assert reclaimed.claim_owner == "replacement-runner"
        assert reclaimed.last_error == "Processing lease expired before completion"

        assert first_db.fail_download_claim(
            item_id,
            "stale-runner",
            "late failure",
            now=T0 + timedelta(seconds=62),
        ) is False
        assert first_db.complete_download_claim(
            item_id,
            "stale-runner",
            now=T0 + timedelta(seconds=62),
        ) is False
        assert second_db.complete_download_claim(
            item_id,
            "replacement-runner",
            now=T0 + timedelta(seconds=62),
        ) is True
        assert second_db.get_all_downloads() == []
    finally:
        first_db.close()
        second_db.close()


def test_two_database_connections_cannot_claim_the_same_item_concurrently(
    tmp_path,
):
    db_path = str(tmp_path / "concurrent-queue.sqlite")
    with DatabaseManager(db_path) as setup_db:
        item_id = _queue(setup_db)
    ready = Barrier(2)

    def claim(owner: str):
        with DatabaseManager(db_path) as worker_db:
            ready.wait(timeout=5)
            claimed = worker_db.claim_next_download(owner, now=T0)
            return claimed.id if claimed else None, owner

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("runner-a", "runner-b")))

    winners = [(claimed_id, owner) for claimed_id, owner in results if claimed_id]
    assert len(winners) == 1
    assert winners[0][0] == item_id
    with DatabaseManager(db_path) as verify_db:
        row = verify_db.get_all_downloads()[0]
        assert row.claim_owner == winners[0][1]
        assert row.attempt_count == 1


def test_album_progress_and_completion_are_fenced_by_same_live_claim(db):
    queue_item_id, request_id = _queue_album(db)
    assert db.claim_next_download(
        "stale-runner",
        lease_seconds=60,
        now=T0,
    ) is not None

    assert db.update_claimed_album_download_request_progress(
        queue_item_id,
        "wrong-owner",
        "downloading",
        "must not land",
        now=T0 + timedelta(seconds=1),
    ) is False
    assert db.update_claimed_album_download_request_progress(
        queue_item_id,
        "stale-runner",
        "downloading",
        "active",
        now=T0 + timedelta(seconds=1),
    ) is True
    assert db.complete_claimed_album_download_request(
        queue_item_id,
        "wrong-owner",
        "must not complete",
        1,
        now=T0 + timedelta(seconds=2),
    ) is False
    assert db.get_album_download_request(request_id)["stage"] == "downloading"

    replacement = db.claim_next_download(
        "replacement-runner",
        lease_seconds=60,
        now=T0 + timedelta(seconds=61),
    )
    assert replacement is not None
    assert replacement.id == queue_item_id
    recovered_tracker = db.get_album_download_request(request_id)
    assert recovered_tracker["stage"] == "failed"
    assert recovered_tracker["detail"] == (
        "Processing lease expired before completion"
    )
    assert db.update_claimed_album_download_request_progress(
        queue_item_id,
        "stale-runner",
        "failed",
        "late stale failure",
        now=T0 + timedelta(seconds=62),
    ) is False
    assert db.complete_claimed_album_download_request(
        queue_item_id,
        "stale-runner",
        "late stale success",
        1,
        now=T0 + timedelta(seconds=62),
    ) is False

    assert db.update_claimed_album_download_request_progress(
        queue_item_id,
        "replacement-runner",
        "importing",
        "verified import",
        1,
        now=T0 + timedelta(seconds=62),
    ) is True
    assert db.complete_claimed_album_download_request(
        queue_item_id,
        "replacement-runner",
        "complete",
        1,
        now=T0 + timedelta(seconds=63),
    ) is True
    completed = db.get_album_download_request(request_id)
    assert completed["stage"] == "success"
    assert completed["detail"] == "complete"
    assert completed["completed_tracks"] == 1
    assert db.get_download_status(queue_item_id) is None


def test_album_failure_and_backoff_commit_atomically_under_claim(db):
    queue_item_id, request_id = _queue_album(db)
    assert db.claim_next_download("runner", now=T0) is not None

    assert db.fail_claimed_album_download_request(
        queue_item_id,
        "wrong-owner",
        "must not land",
        now=T0 + timedelta(seconds=1),
    ) is False
    assert db.get_album_download_request(request_id)["stage"] == "queued"
    assert db.get_all_downloads()[0].status == "pending"

    assert db.fail_claimed_album_download_request(
        queue_item_id,
        "runner",
        "validation rejected every artifact",
        completed_tracks=0,
        base_delay_seconds=60,
        now=T0 + timedelta(seconds=10),
    ) is True
    tracker = db.get_album_download_request(request_id)
    queue = db.get_all_downloads()[0]
    assert tracker["stage"] == "failed"
    assert tracker["detail"] == "validation rejected every artifact"
    assert queue.status == "failed"
    assert queue.last_error == "validation rejected every artifact"
    assert queue.next_attempt_at == (
        T0 + timedelta(seconds=70)
    ).replace(tzinfo=None)
    assert queue.claim_owner is None


def test_explicit_stale_recovery_returns_ids_for_linked_tracker_updates(db):
    first_id = _queue(db, "First")
    second_id = _queue(db, "Second")
    assert db.claim_next_download("runner-1", lease_seconds=10, now=T0) is not None
    assert db.claim_next_download("runner-2", lease_seconds=20, now=T0) is not None

    assert db.recover_stale_download_claims(
        now=T0 + timedelta(seconds=11)
    ) == [first_id]
    rows = {item.id: item for item in db.get_all_downloads()}
    assert rows[first_id].status == "failed"
    assert rows[first_id].claim_owner is None
    assert rows[second_id].claim_owner == "runner-2"
