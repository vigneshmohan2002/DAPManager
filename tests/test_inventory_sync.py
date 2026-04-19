from unittest.mock import MagicMock, patch

import pytest

from src.db_manager import DatabaseManager, Track
from src.inventory_sync import main_run_inventory_report


@pytest.fixture
def db():
    mgr = DatabaseManager(":memory:")
    yield mgr
    mgr.close()


def _mock_response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def test_master_records_inventory_locally(db):
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path="/a"))
    db.add_or_update_track(Track(mbid="m2", title="T2", artist="B", local_path="/b"))

    config = {
        "device_id": "master-1",
        "device_role": "master",
    }
    summary = main_run_inventory_report(db, config)

    assert summary == {
        "mode": "local",
        "device_id": "master-1",
        "items": 2,
        "written": 2,
    }
    rows = db.get_device_inventory("master-1")
    assert {r["mbid"] for r in rows} == {"m1", "m2"}


def test_satellite_posts_to_master(db):
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path="/a"))
    config = {
        "device_id": "sat-1",
        "device_role": "satellite",
        "master_url": "http://host.local:5001/",
    }
    payload = {"success": True, "device_id": "sat-1", "received": 1, "written": 1}
    with patch("src.inventory_sync.requests.Session.post",
               return_value=_mock_response(payload)) as mock_post:
        summary = main_run_inventory_report(db, config)

    # Trailing slash normalized
    assert mock_post.call_args.args[0] == "http://host.local:5001/api/inventory"
    sent = mock_post.call_args.kwargs["json"]
    assert sent["device_id"] == "sat-1"
    assert sent["items"] == [{"mbid": "m1", "local_path": "/a"}]
    assert summary == {
        "mode": "remote",
        "device_id": "sat-1",
        "items": 1,
        "written": 1,
    }


def test_satellite_without_master_url_raises(db):
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path="/a"))
    config = {"device_id": "sat-1", "device_role": "satellite"}
    with pytest.raises(ValueError, match="master_url"):
        main_run_inventory_report(db, config)


def test_missing_device_id_raises(db):
    with pytest.raises(ValueError, match="device_id"):
        main_run_inventory_report(db, {"device_role": "satellite"})


def test_master_rejection_raises(db):
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path="/a"))
    config = {
        "device_id": "sat-1",
        "device_role": "satellite",
        "master_url": "http://host.local:5001",
    }
    payload = {"success": False, "message": "nope"}
    with patch("src.inventory_sync.requests.Session.post",
               return_value=_mock_response(payload)):
        with pytest.raises(RuntimeError, match="nope"):
            main_run_inventory_report(db, config)


def test_session_includes_bearer_header_when_token_given():
    from src.inventory_sync import _session
    s = _session(api_token="shared-secret")
    assert s.headers.get("Authorization") == "Bearer shared-secret"


def test_session_without_token_has_no_auth_header():
    from src.inventory_sync import _session
    s = _session(api_token=None)
    assert "Authorization" not in s.headers


def test_only_tracks_with_local_path_are_reported(db):
    # Track present locally
    db.add_or_update_track(Track(mbid="m1", title="T", artist="A", local_path="/a"))
    # Catalog-only track (no local_path) shouldn't be reported as present
    db.add_or_update_track(Track(mbid="m2", title="T2", artist="B"))

    config = {"device_id": "master-1", "device_role": "master"}
    summary = main_run_inventory_report(db, config)
    assert summary["items"] == 1
    rows = db.get_device_inventory("master-1")
    assert [r["mbid"] for r in rows] == ["m1"]
