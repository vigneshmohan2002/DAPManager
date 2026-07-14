from unittest.mock import MagicMock, patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.contribution_sync import (
    CONTRIBUTE_STATE_KEY,
    TERMINAL,
    VALID_STATUSES,
    main_run_contribute,
    main_run_contribute_one,
)


@pytest.fixture
def db():
    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _sat_config():
    return {
        "device_id": "sat-1",
        "device_role": "satellite",
        "master_url": "http://host.local:5001/",
    }


def test_contribution_status_contract_is_exact():
    assert VALID_STATUSES == {
        "attempting",
        "have_better",
        "satisfied",
        "needs_upload",
        "ingested",
    }
    assert TERMINAL == {"have_better", "satisfied", "ingested"}


def test_successful_empty_contribution_run_stamps_cursor(db):
    assert db.get_sync_state(CONTRIBUTE_STATE_KEY) is None

    out = main_run_contribute(db, _sat_config())

    assert out == {"offered": 0, "uploaded": 0, "satisfied": 0, "errors": 0}
    assert db.get_sync_state(CONTRIBUTE_STATE_KEY) is not None


def test_master_role_is_noop(db):
    out = main_run_contribute(db, {"device_role": "master"})
    assert out["offered"] == 0 and out["uploaded"] == 0


def test_standalone_role_is_noop(db):
    out = main_run_contribute(db, {
        "device_role": "standalone", "is_master": True,
    })
    assert out["offered"] == 0 and out["uploaded"] == 0


def test_explicit_satellite_role_wins_over_stale_legacy_master_flag(db):
    out = main_run_contribute(db, {
        "device_role": "satellite",
        "is_master": True,
        "master_url": "http://host.local:5001",
    })
    assert "skipped" not in out


def test_explicit_master_role_wins_over_stale_legacy_false_flag(db):
    out = main_run_contribute(db, {
        "device_role": "master", "is_master": False,
    })
    assert out["skipped"] == "device is master"


def test_missing_master_url_raises(db):
    with pytest.raises(ValueError):
        main_run_contribute(db, {"device_role": "satellite"})


def test_offers_new_local_tracks(db, tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m1", title="T", artist="A", album="Alb", local_path=str(f),
    ))

    quality = {"format": "FLAC", "sample_rate": 96000}
    with patch("src.contribution_sync.read_quality", return_value=quality), \
         patch("src.contribution_sync.requests.Session.post",
               return_value=_resp({"contribution_id": 7, "status": "attempting"})) as post, \
         patch("src.contribution_sync.requests.Session.get",
               return_value=_resp({"status": "attempting", "want_upload": False})):
        out = main_run_contribute(db, _sat_config())

    assert post.call_args.args[0] == "http://host.local:5001/api/contributions"
    assert post.call_args.kwargs["timeout"] == 60
    assert post.call_args.kwargs["json"] == {
        "device_id": "sat-1",
        "mbid": "m1",
        "isrc": None,
        "artist": "A",
        "title": "T",
        "album": "Alb",
        "quality": quality,
    }
    assert out["offered"] == 1
    # State recorded so we don't re-offer; now in-flight (polled next run).
    row = db.get_contributed("m1")
    assert row["contribution_id"] == 7 and row["status"] == "attempting"


def test_malformed_offer_status_is_not_persisted(db, tmp_path):
    f = tmp_path / "malformed.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m-bad", title="T", artist="A", local_path=str(f),
    ))

    with patch(
        "src.contribution_sync.requests.Session.post",
        return_value=_resp({"contribution_id": 7}),
    ):
        out = main_run_contribute(db, _sat_config())

    assert out["errors"] == 1
    assert db.get_contributed("m-bad") is None


def test_legacy_null_status_is_still_polled(db, tmp_path):
    f = tmp_path / "legacy.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m-legacy", title="T", artist="A", local_path=str(f),
    ))
    db.upsert_contributed("m-legacy", 42, None)

    with patch(
        "src.contribution_sync.requests.Session.get",
        return_value=_resp({"status": "attempting", "want_upload": False}),
    ) as get:
        out = main_run_contribute(db, _sat_config())

    get.assert_called_once()
    assert out["errors"] == 0
    assert db.get_contributed("m-legacy")["status"] == "attempting"


def test_legacy_row_without_contribution_id_is_reoffered(db, tmp_path):
    f = tmp_path / "orphaned-state.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m-orphan", title="T", artist="A", local_path=str(f),
    ))
    db.upsert_contributed("m-orphan", None, None)

    with patch(
        "src.contribution_sync.requests.Session.post",
        return_value=_resp({"contribution_id": 9, "status": "attempting"}),
    ), patch(
        "src.contribution_sync.requests.Session.get",
        return_value=_resp({"status": "attempting", "want_upload": False}),
    ):
        out = main_run_contribute(db, _sat_config())

    assert out["offered"] == 1
    row = db.get_contributed("m-orphan")
    assert row["contribution_id"] == 9
    assert row["status"] == "attempting"


def test_unknown_poll_status_does_not_replace_retryable_state(db, tmp_path):
    f = tmp_path / "future-status.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m-future", title="T", artist="A", local_path=str(f),
    ))
    db.upsert_contributed("m-future", 77, "attempting")

    with patch(
        "src.contribution_sync.requests.Session.get",
        return_value=_resp({"status": "teleported", "want_upload": False}),
    ):
        out = main_run_contribute(db, _sat_config())

    assert out["errors"] == 1
    assert db.get_contributed("m-future")["status"] == "attempting"


def test_needs_upload_without_upload_request_keeps_retryable_state(db, tmp_path):
    f = tmp_path / "missing-upload-request.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m-missing-upload", title="T", artist="A", local_path=str(f),
    ))
    db.upsert_contributed("m-missing-upload", 78, "attempting")

    with patch(
        "src.contribution_sync.requests.Session.get",
        return_value=_resp({"status": "needs_upload", "want_upload": False}),
    ), patch("src.contribution_sync.requests.Session.post") as post:
        out = main_run_contribute(db, _sat_config())

    post.assert_not_called()
    assert out["errors"] == 1
    assert db.get_contributed("m-missing-upload")["status"] == "attempting"


def test_have_better_is_terminal_and_not_re_offered(db, tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path=str(f)))

    with patch("src.contribution_sync.requests.Session.post",
               return_value=_resp({"contribution_id": 1, "status": "have_better"})):
        out = main_run_contribute(db, _sat_config())
    assert out["satisfied"] == 1

    # Second run: terminal row excluded from candidates and from polling.
    with patch("src.contribution_sync.requests.Session.post") as post, \
         patch("src.contribution_sync.requests.Session.get") as get:
        out2 = main_run_contribute(db, _sat_config())
    post.assert_not_called()
    get.assert_not_called()
    assert out2["offered"] == 0


def test_contribute_one_offers_and_polls(db, tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path=str(f)))

    with patch("src.contribution_sync.requests.Session.post",
               return_value=_resp({"contribution_id": 5, "status": "attempting"})), \
         patch("src.contribution_sync.requests.Session.get",
               return_value=_resp({"status": "attempting", "want_upload": False})):
        out = main_run_contribute_one(db, _sat_config(), "m1")

    assert out["success"] and out["status"] == "attempting"
    assert db.get_contributed("m1")["contribution_id"] == 5


def test_contribute_one_rejects_track_without_local_file(db):
    out = main_run_contribute_one(db, _sat_config(), "nope")
    assert out["success"] is False
    assert "local file" in out["message"]


def test_contribute_one_is_noop_for_master(db):
    out = main_run_contribute_one(db, {"device_role": "master"}, "x")
    assert out["success"] is False


def test_contribute_one_is_noop_for_standalone(db):
    out = main_run_contribute_one(
        db, {"device_role": "standalone", "is_master": True}, "x"
    )
    assert out["success"] is False


def test_poll_triggers_upload_when_master_wants_file(db, tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"audio")
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path=str(f)))
    # Pre-seed an in-flight contribution (already offered last run).
    db.upsert_contributed("m1", 42, "attempting")

    with patch("src.contribution_sync.requests.Session.get",
               return_value=_resp({"status": "needs_upload", "want_upload": True})), \
         patch("src.contribution_sync.requests.Session.post",
               return_value=_resp({"status": "ingested"})) as post:
        out = main_run_contribute(db, _sat_config())

    assert post.call_args.args[0] == "http://host.local:5001/api/contributions/42/upload"
    assert "files" in post.call_args.kwargs
    assert out["uploaded"] == 1
    assert db.get_contributed("m1")["status"] == "ingested"


def test_offer_and_poll_results_are_aggregated_without_changing_status_rules(
    db, tmp_path
):
    for mbid in ("m-new", "m-satisfied", "m-upload"):
        path = tmp_path / f"{mbid}.flac"
        path.write_bytes(b"audio")
        db.add_or_update_track(
            Track(mbid=mbid, title=mbid, artist="A", local_path=str(path))
        )
    db.upsert_contributed("m-satisfied", 2, "attempting")
    db.upsert_contributed("m-upload", 3, "attempting")
    events = []

    with patch(
        "src.contribution_sync.requests.Session.post",
        side_effect=[
            _resp({"contribution_id": 1, "status": "have_better"}),
            _resp({"status": "ingested"}),
        ],
    ) as post, patch(
        "src.contribution_sync.requests.Session.get",
        side_effect=[
            _resp({"status": "satisfied", "want_upload": False}),
            _resp({"status": "needs_upload", "want_upload": True}),
        ],
    ) as get:
        out = main_run_contribute(
            db, _sat_config(), progress_callback=events.append
        )

    assert out == {"offered": 1, "uploaded": 1, "satisfied": 2, "errors": 0}
    assert [call.kwargs["timeout"] for call in post.call_args_list] == [60, 600]
    assert [call.kwargs["timeout"] for call in get.call_args_list] == [30, 30]
    assert db.get_contributed("m-new")["status"] == "have_better"
    assert db.get_contributed("m-satisfied")["status"] == "satisfied"
    assert db.get_contributed("m-upload")["status"] == "ingested"
    assert events == [
        {"message": "Offering 1 track(s) to http://host.local:5001"},
        {
            "message": "Contribute finished: "
            "{'offered': 1, 'uploaded': 1, 'satisfied': 2, 'errors': 0}"
        },
    ]
    upload_file = post.call_args_list[-1].kwargs["files"]["file"]
    assert upload_file[0] == "m-upload.flac"
    assert upload_file[1].closed


def test_contribute_one_terminal_state_does_not_make_http_calls(db, tmp_path):
    path = tmp_path / "terminal.flac"
    path.write_bytes(b"audio")
    db.add_or_update_track(
        Track(mbid="m-terminal", title="T", artist="A", local_path=str(path))
    )
    db.upsert_contributed("m-terminal", 42, "satisfied")

    with patch("src.contribution_sync.requests.Session.post") as post, patch(
        "src.contribution_sync.requests.Session.get"
    ) as get:
        out = main_run_contribute_one(db, _sat_config(), "m-terminal")

    assert out == {"success": True, "mbid": "m-terminal", "status": "satisfied"}
    post.assert_not_called()
    get.assert_not_called()


def test_session_preserves_auth_headers_and_retry_policy():
    from src.contribution_sync import _session

    session = _session("shared-secret")
    retries = session.get_adapter("https://").max_retries

    assert session.headers["Accept"] == "application/json"
    assert session.headers["Authorization"] == "Bearer shared-secret"
    assert "Content-Type" not in session.headers
    assert retries.total == 3
    assert retries.backoff_factor == 1.0
    assert retries.status_forcelist == [500, 502, 503, 504]
    assert retries.allowed_methods == frozenset({"GET", "POST"})
