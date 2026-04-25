"""End-to-end checks for the first-run wizard endpoints.

The wizard talks to ``/api/save_config``, ``/api/setup/validate-path``,
and ``/api/setup/detect-public-url``. The spec is that the saved
config must match what ``build_initial_config`` would produce for the
same payload — so the wizard never accumulates its own private shape
that drifts from the canonical one.
"""

import json
import os

import pytest

import web_server
from src.first_run import build_initial_config


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(target))
    yield target


def test_save_config_master_matches_build_initial_config(client, fresh_config):
    payload = {
        "role": "master",
        "music_library_path": "/tmp/m",
        "downloads_path": "/tmp/d",
        "dap_mount_point": "/Volumes/DAP",
        "public_master_url": "http://master.tail.ts.net:5001/",
        "slsk_username": "user",
        "slsk_password": "pw",
        "jellyfin_url": "http://jelly:8096",
        "jellyfin_api_key": "jk",
        "jellyfin_user_id": "ju",
        "lidarr_enabled": True,
        "lidarr_url": "http://lidarr:8686",
        "lidarr_api_key": "lk",
        "acoustid_api_key": "ak",
        "contact_email": "me@example.com",
        "api_token": "tok",
        "fast_search": True,
    }
    res = client.post("/api/save_config", json=payload)
    assert res.status_code == 200
    assert res.get_json() == {"success": True}

    written = json.loads(fresh_config.read_text())
    expected = build_initial_config(
        "master",
        music_library_path=payload["music_library_path"],
        downloads_path=payload["downloads_path"],
        dap_mount_point=payload["dap_mount_point"],
        public_master_url=payload["public_master_url"],
        slsk_username=payload["slsk_username"],
        slsk_password=payload["slsk_password"],
        jellyfin_url=payload["jellyfin_url"],
        jellyfin_api_key=payload["jellyfin_api_key"],
        jellyfin_user_id=payload["jellyfin_user_id"],
        lidarr_enabled=payload["lidarr_enabled"],
        lidarr_url=payload["lidarr_url"],
        lidarr_api_key=payload["lidarr_api_key"],
        acoustid_api_key=payload["acoustid_api_key"],
        contact_email=payload["contact_email"],
        api_token=payload["api_token"],
        fast_search=payload["fast_search"],
    )
    assert written == expected


def test_save_config_satellite_writes_master_url(client, fresh_config):
    payload = {
        "role": "satellite",
        "music_library_path": "/tmp/m",
        "downloads_path": "/tmp/d",
        "master_url": "http://master:5001",
        "api_token": "tok",
    }
    res = client.post("/api/save_config", json=payload)
    assert res.status_code == 200
    written = json.loads(fresh_config.read_text())
    assert written["device_role"] == "satellite"
    assert written["master_url"] == "http://master:5001"
    assert written["api_token"] == "tok"
    assert "public_master_url" not in written


def test_save_config_legacy_payload_defaults_to_master(client, fresh_config):
    """Old single-form template doesn't send role; default keeps it working."""
    payload = {
        "music_library_path": "/tmp/m",
        "downloads_path": "/tmp/d",
        "dap_mount_point": "/Volumes/DAP",
    }
    res = client.post("/api/save_config", json=payload)
    assert res.status_code == 200
    written = json.loads(fresh_config.read_text())
    assert written["is_master"] is True
    assert written["device_role"] == "master"


def test_save_config_rejects_missing_required_paths(client, fresh_config):
    res = client.post("/api/save_config", json={"role": "master"})
    assert res.status_code == 400
    assert res.get_json()["success"] is False
    assert not fresh_config.exists()


def test_validate_path_directory_ok(client, fresh_config, tmp_path):
    res = client.post(
        "/api/setup/validate-path",
        json={"path": str(tmp_path), "kind": "directory"},
    )
    assert res.get_json() == {"ok": True}


def test_validate_path_directory_missing(client, fresh_config, tmp_path):
    res = client.post(
        "/api/setup/validate-path",
        json={"path": str(tmp_path / "nope"), "kind": "directory"},
    )
    body = res.get_json()
    assert body["ok"] is False
    assert "does not exist" in body["message"]


def test_validate_path_kind_mismatch(client, fresh_config, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    res = client.post(
        "/api/setup/validate-path",
        json={"path": str(f), "kind": "directory"},
    )
    body = res.get_json()
    assert body["ok"] is False
    assert body["message"] == "not a directory"


def test_validate_path_empty_input(client, fresh_config):
    res = client.post("/api/setup/validate-path", json={"path": ""})
    body = res.get_json()
    assert body["ok"] is False
    assert body["message"] == "path is empty"


def test_detect_public_url_prefers_env(client, fresh_config, monkeypatch):
    monkeypatch.setenv("MASTER_PUBLIC_URL", "http://from-env:5001")
    res = client.get("/api/setup/detect-public-url")
    assert res.get_json() == {
        "source": "env",
        "url": "http://from-env:5001",
    }


def test_detect_public_url_returns_none_when_nothing_available(
    client, fresh_config, monkeypatch
):
    monkeypatch.delenv("MASTER_PUBLIC_URL", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    res = client.get("/api/setup/detect-public-url")
    assert res.get_json() == {"source": "none"}
