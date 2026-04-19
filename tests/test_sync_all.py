"""Tests for the Sync All orchestrator.

Patches the four sub-step entry points so we can verify orchestration
without exercising HTTP or the catalog sync clients themselves.
"""

from unittest.mock import patch

from src.sync_all import main_run_sync_all


def _cfg(**overrides):
    base = {
        "device_id": "dev-A",
        "device_role": "satellite",
        "master_url": "http://host:5001",
        "is_master": False,
        "report_inventory_to_host": True,
    }
    base.update(overrides)
    return base


def test_sync_all_runs_all_four_steps_when_configured(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.return_value = {"received": 0}
        mpp.return_value = {"received": 0}
        mpu.return_value = {"sent": 0}
        mir.return_value = {"items": 0}

        out = main_run_sync_all(db, _cfg())

    names = [s["name"] for s in out["steps"]]
    assert names == [
        "pull_catalog",
        "pull_playlists",
        "push_playlists",
        "report_inventory",
    ]
    assert all(s["status"] == "ok" for s in out["steps"])
    assert mcp.called and mpp.called and mpu.called and mir.called


def test_sync_all_skips_pulls_when_no_master_url(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mir.return_value = {"items": 0}

        out = main_run_sync_all(db, _cfg(master_url="", is_master=True))

    by_name = {s["name"]: s for s in out["steps"]}
    assert by_name["pull_catalog"]["status"] == "skipped"
    assert by_name["pull_playlists"]["status"] == "skipped"
    assert by_name["push_playlists"]["status"] == "skipped"
    assert by_name["report_inventory"]["status"] == "ok"
    assert not mcp.called and not mpp.called and not mpu.called
    assert mir.called


def test_sync_all_skips_inventory_when_disabled(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.return_value = {}
        mpp.return_value = {}
        mpu.return_value = {}

        out = main_run_sync_all(db, _cfg(report_inventory_to_host=False))

    inv = next(s for s in out["steps"] if s["name"] == "report_inventory")
    assert inv["status"] == "skipped"
    assert not mir.called


def test_sync_all_continues_on_sub_step_error(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.side_effect = RuntimeError("network down")
        mpp.return_value = {}
        mpu.return_value = {}
        mir.return_value = {}

        out = main_run_sync_all(db, _cfg())

    by_name = {s["name"]: s for s in out["steps"]}
    assert by_name["pull_catalog"]["status"] == "error"
    assert "network down" in by_name["pull_catalog"]["message"]
    # Later steps still ran.
    assert by_name["pull_playlists"]["status"] == "ok"
    assert by_name["push_playlists"]["status"] == "ok"
    assert by_name["report_inventory"]["status"] == "ok"


def test_sync_all_inventory_defaults_to_master_role(db):
    """When report_inventory_to_host is unset, master should still report."""
    with patch("src.sync_all.main_run_catalog_pull"), \
         patch("src.sync_all.main_run_playlist_pull"), \
         patch("src.sync_all.main_run_playlist_push"), \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mir.return_value = {}
        cfg = _cfg(master_url="", is_master=True)
        cfg.pop("report_inventory_to_host")
        out = main_run_sync_all(db, cfg)

    inv = next(s for s in out["steps"] if s["name"] == "report_inventory")
    assert inv["status"] == "ok"
    assert mir.called


def test_sync_all_progress_callback_receives_updates(db):
    messages = []
    with patch("src.sync_all.main_run_catalog_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_push", return_value={}), \
         patch("src.sync_all.main_run_inventory_report", return_value={}):
        main_run_sync_all(db, _cfg(), progress_callback=messages.append)
    assert any("Sync All" in m.get("message", "") for m in messages)
    assert any("finished" in m.get("message", "") for m in messages)
