import os

import pytest

from src.first_run import build_initial_config, is_first_run, suggest_device_name


def test_is_first_run_true_when_missing(tmp_path):
    assert is_first_run(str(tmp_path / "nope.json")) is True


def test_is_first_run_false_when_present(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{}")
    assert is_first_run(str(p)) is False


def test_suggest_device_name_returns_non_empty():
    name = suggest_device_name()
    assert name
    assert "." not in name  # hostname's domain part is stripped


def test_master_payload_flags_master_and_keeps_creds():
    cfg = build_initial_config(
        "master",
        music_library_path="/m",
        downloads_path="/d",
        slsk_username="u",
        slsk_password="p",
        jellyfin_url="http://j",
        jellyfin_api_key="k",
        jellyfin_user_id="uid",
    )
    assert cfg["is_master"] is True
    assert cfg["device_role"] == "master"
    assert cfg["slsk_username"] == "u"
    assert cfg["jellyfin_url"] == "http://j"
    assert cfg["master_url"] == ""
    assert cfg["report_inventory_to_host"] is True


def test_satellite_payload_strips_trailing_slash_and_omits_slsk():
    cfg = build_initial_config(
        "satellite",
        music_library_path="/m",
        downloads_path="/d",
        master_url="http://master.local:5001/",
        api_token="tok",
        device_name="livingroom",
        report_inventory_to_host=True,
    )
    assert cfg["is_master"] is False
    assert cfg["device_role"] == "satellite"
    assert cfg["master_url"] == "http://master.local:5001"
    assert cfg["api_token"] == "tok"
    assert cfg["device_name"] == "livingroom"
    assert cfg["report_inventory_to_host"] is True
    assert cfg["slsk_username"] == ""
    assert cfg["slsk_password"] == ""


def test_satellite_payload_omits_device_name_when_blank():
    cfg = build_initial_config(
        "satellite",
        music_library_path="/m",
        downloads_path="/d",
        master_url="http://m:5001",
    )
    assert "device_name" not in cfg


def test_standalone_payload_looks_like_local_only_satellite():
    cfg = build_initial_config(
        "standalone",
        music_library_path="/m",
        downloads_path="/d",
        slsk_username="u",
        slsk_password="p",
    )
    assert cfg["is_master"] is False
    assert cfg["device_role"] == "satellite"
    assert cfg["master_url"] == ""
    assert cfg["slsk_username"] == "u"
    assert cfg["report_inventory_to_host"] is False


def test_required_paths_raise():
    with pytest.raises(ValueError):
        build_initial_config("master", music_library_path="", downloads_path="/d")
    with pytest.raises(ValueError):
        build_initial_config("master", music_library_path="/m", downloads_path="")


def test_unknown_role_raises():
    with pytest.raises(ValueError):
        build_initial_config(
            "admin", music_library_path="/m", downloads_path="/d"
        )  # type: ignore[arg-type]


def test_master_strips_trailing_slash_on_public_master_url():
    cfg = build_initial_config(
        "master",
        music_library_path="/m",
        downloads_path="/d",
        public_master_url="http://master.tail.ts.net:5001/",
    )
    assert cfg["public_master_url"] == "http://master.tail.ts.net:5001"


def test_satellite_does_not_get_public_master_url():
    cfg = build_initial_config(
        "satellite",
        music_library_path="/m",
        downloads_path="/d",
        master_url="http://m:5001",
        public_master_url="http://leaked:5001",
    )
    assert "public_master_url" not in cfg


def test_master_carries_lidarr_and_acoustid_fields():
    cfg = build_initial_config(
        "master",
        music_library_path="/m",
        downloads_path="/d",
        lidarr_url="http://lidarr:8686",
        lidarr_api_key="lk",
        lidarr_enabled=True,
        acoustid_api_key="ak",
        contact_email="me@example.com",
    )
    assert cfg["lidarr_url"] == "http://lidarr:8686"
    assert cfg["lidarr_api_key"] == "lk"
    assert cfg["lidarr_enabled"] is True
    assert cfg["acoustid_api_key"] == "ak"
    assert cfg["contact_email"] == "me@example.com"


def test_advanced_sldl_flags_propagate():
    cfg = build_initial_config(
        "master",
        music_library_path="/m",
        downloads_path="/d",
        fast_search=True,
        remove_ft=True,
        desperate_mode=False,
        strict_quality=True,
    )
    assert cfg["fast_search"] is True
    assert cfg["remove_ft"] is True
    assert cfg["desperate_mode"] is False
    assert cfg["strict_quality"] is True


def test_all_roles_set_required_defaults():
    for role in ("master", "satellite", "standalone"):
        cfg = build_initial_config(role, music_library_path="/m", downloads_path="/d")
        # These must be present so ConfigManager won't fail validation
        for key in (
            "database_file",
            "music_library_path",
            "downloads_path",
            "ffmpeg_path",
            "dap_mount_point",
            "dap_music_dir_name",
            "dap_playlist_dir_name",
        ):
            assert key in cfg
