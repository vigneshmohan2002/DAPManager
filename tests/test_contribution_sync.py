from unittest.mock import MagicMock, patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.contribution_sync import main_run_contribute


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


def test_master_role_is_noop(db):
    out = main_run_contribute(db, {"device_role": "master"})
    assert out["offered"] == 0 and out["uploaded"] == 0


def test_missing_master_url_raises(db):
    with pytest.raises(ValueError):
        main_run_contribute(db, {"device_role": "satellite"})


def test_offers_new_local_tracks(db, tmp_path):
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")
    db.add_or_update_track(Track(
        mbid="m1", title="T", artist="A", album="Alb", local_path=str(f),
    ))

    with patch("src.contribution_sync.requests.Session.post",
               return_value=_resp({"contribution_id": 7, "status": "attempting"})) as post, \
         patch("src.contribution_sync.requests.Session.get",
               return_value=_resp({"status": "attempting", "want_upload": False})):
        out = main_run_contribute(db, _sat_config())

    assert post.call_args.args[0] == "http://host.local:5001/api/contributions"
    sent = post.call_args.kwargs["json"]
    assert sent["mbid"] == "m1" and sent["artist"] == "A"
    assert "quality" in sent
    assert out["offered"] == 1
    # State recorded so we don't re-offer; now in-flight (polled next run).
    row = db.get_contributed("m1")
    assert row["contribution_id"] == 7 and row["status"] == "attempting"


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
