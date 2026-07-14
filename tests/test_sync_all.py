"""Tests for the Sync All orchestrator.

Patches the four sub-step entry points so we can verify orchestration
without exercising HTTP or the catalog sync clients themselves.
"""

from unittest.mock import patch

import pytest

from src.sync_all import main_run_sync_all


@pytest.fixture(autouse=True)
def _stub_contribute():
    """Keep the new contribute step from doing real HTTP in orchestration
    tests that don't focus on it."""
    with patch("src.sync_all.main_run_contribute", return_value={"offered": 0}) as m:
        yield m


@pytest.fixture(autouse=True)
def _stub_artist_tags_pull():
    """Avoid HTTP while covering the new satellite metadata step."""
    with patch(
        "src.sync_all.main_run_artist_tags_pull",
        return_value={"received": 0},
    ) as mock:
        yield mock


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


def test_sync_all_runs_all_steps_when_configured(db, _stub_artist_tags_pull):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_lyrics_pull") as mlp, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.return_value = {"received": 0}
        mpp.return_value = {"received": 0}
        mpu.return_value = {"sent": 0}
        mlp.return_value = {"received": 0}
        mir.return_value = {"items": 0}

        out = main_run_sync_all(db, _cfg())

    names = [s["name"] for s in out["steps"]]
    assert names == [
        "pull_catalog",
        "pull_artist_tags",
        "pull_playlists",
        "push_playlists",
        "pull_lyrics",
        "report_inventory",
        "contribute",
    ]
    assert all(s["status"] == "ok" for s in out["steps"])
    assert mcp.called and mpp.called and mpu.called and mlp.called and mir.called
    assert _stub_artist_tags_pull.called


def test_sync_all_skips_pulls_when_no_master_url(db, _stub_artist_tags_pull):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_lyrics_pull") as mlp, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mir.return_value = {"items": 0}

        out = main_run_sync_all(
            db,
            _cfg(master_url="", device_role="master", is_master=False),
        )

    by_name = {s["name"]: s for s in out["steps"]}
    assert by_name["pull_catalog"]["status"] == "skipped"
    assert by_name["pull_artist_tags"]["status"] == "skipped"
    assert by_name["pull_playlists"]["status"] == "skipped"
    assert by_name["push_playlists"]["status"] == "skipped"
    assert by_name["pull_lyrics"]["status"] == "skipped"
    assert by_name["report_inventory"]["status"] == "ok"
    assert not mcp.called and not mpp.called and not mpu.called and not mlp.called
    assert not _stub_artist_tags_pull.called
    assert mir.called


def test_sync_all_skips_inventory_when_disabled(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_lyrics_pull") as mlp, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.return_value = {}
        mpp.return_value = {}
        mpu.return_value = {}
        mlp.return_value = {}

        out = main_run_sync_all(db, _cfg(report_inventory_to_host=False))

    inv = next(s for s in out["steps"] if s["name"] == "report_inventory")
    assert inv["status"] == "skipped"
    assert not mir.called


def test_sync_all_continues_on_sub_step_error(db):
    with patch("src.sync_all.main_run_catalog_pull") as mcp, \
         patch("src.sync_all.main_run_playlist_pull") as mpp, \
         patch("src.sync_all.main_run_playlist_push") as mpu, \
         patch("src.sync_all.main_run_lyrics_pull") as mlp, \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mcp.side_effect = RuntimeError("network down")
        mpp.return_value = {}
        mpu.return_value = {}
        mlp.return_value = {}
        mir.return_value = {}

        out = main_run_sync_all(db, _cfg())

    by_name = {s["name"]: s for s in out["steps"]}
    assert by_name["pull_catalog"]["status"] == "error"
    assert "network down" in by_name["pull_catalog"]["message"]
    # Later steps still ran.
    assert by_name["pull_playlists"]["status"] == "ok"
    assert by_name["push_playlists"]["status"] == "ok"
    assert by_name["pull_lyrics"]["status"] == "ok"
    assert by_name["report_inventory"]["status"] == "ok"


def test_sync_all_inventory_defaults_to_master_role(db):
    """When report_inventory_to_host is unset, master should still report."""
    with patch("src.sync_all.main_run_catalog_pull"), \
         patch("src.sync_all.main_run_playlist_pull"), \
         patch("src.sync_all.main_run_playlist_push"), \
         patch("src.sync_all.main_run_lyrics_pull"), \
         patch("src.sync_all.main_run_inventory_report") as mir:
        mir.return_value = {}
        cfg = _cfg(master_url="", device_role="master", is_master=False)
        cfg.pop("report_inventory_to_host")
        out = main_run_sync_all(db, cfg)

    inv = next(s for s in out["steps"] if s["name"] == "report_inventory")
    assert inv["status"] == "ok"
    assert mir.called


def test_contribute_runs_by_default_for_satellite(db, _stub_contribute):
    with patch("src.sync_all.main_run_catalog_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_push", return_value={}), \
         patch("src.sync_all.main_run_lyrics_pull", return_value={}), \
         patch("src.sync_all.main_run_inventory_report", return_value={}):
        cfg = _cfg()
        cfg.pop("report_inventory_to_host", None)  # contribute_to_host unset too
        out = main_run_sync_all(db, cfg)

    step = next(s for s in out["steps"] if s["name"] == "contribute")
    assert step["status"] == "ok"
    assert _stub_contribute.called


def test_contribute_skipped_when_disabled(db, _stub_contribute):
    with patch("src.sync_all.main_run_catalog_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_push", return_value={}), \
         patch("src.sync_all.main_run_lyrics_pull", return_value={}), \
         patch("src.sync_all.main_run_inventory_report", return_value={}):
        out = main_run_sync_all(db, _cfg(contribute_to_host=False))

    step = next(s for s in out["steps"] if s["name"] == "contribute")
    assert step["status"] == "skipped"
    assert not _stub_contribute.called


def test_contribute_skipped_without_master_url(db, _stub_contribute):
    with patch("src.sync_all.main_run_inventory_report", return_value={}):
        out = main_run_sync_all(
            db,
            _cfg(master_url="", device_role="master", is_master=False),
        )

    step = next(s for s in out["steps"] if s["name"] == "contribute")
    assert step["status"] == "skipped"
    assert not _stub_contribute.called


def test_sync_all_progress_callback_receives_updates(db):
    messages = []
    with patch("src.sync_all.main_run_catalog_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_push", return_value={}), \
         patch("src.sync_all.main_run_lyrics_pull", return_value={}), \
         patch("src.sync_all.main_run_inventory_report", return_value={}):
        main_run_sync_all(db, _cfg(), progress_callback=messages.append)
    assert any("Sync All" in m.get("message", "") for m in messages)
    assert any("finished" in m.get("message", "") for m in messages)


def test_authority_role_ignores_stale_master_url_and_legacy_false(
    db, _stub_contribute, _stub_artist_tags_pull
):
    """A role flip to master must stop all satellite-facing operations."""
    with patch("src.sync_all.main_run_catalog_pull") as catalog, \
         patch("src.sync_all.main_run_playlist_pull") as playlist_pull, \
         patch("src.sync_all.main_run_playlist_push") as playlist_push, \
         patch("src.sync_all.main_run_lyrics_pull") as lyrics, \
         patch("src.sync_all.main_run_inventory_report", return_value={}) as inventory:
        cfg = _cfg(device_role="master", is_master=False)
        cfg.pop("report_inventory_to_host")
        out = main_run_sync_all(db, cfg)

    by_name = {step["name"]: step for step in out["steps"]}
    for name in (
        "pull_catalog",
        "pull_artist_tags",
        "pull_playlists",
        "push_playlists",
        "pull_lyrics",
        "contribute",
    ):
        assert by_name[name]["status"] == "skipped"
    assert by_name["report_inventory"]["status"] == "ok"
    catalog.assert_not_called()
    _stub_artist_tags_pull.assert_not_called()
    playlist_pull.assert_not_called()
    playlist_push.assert_not_called()
    lyrics.assert_not_called()
    _stub_contribute.assert_not_called()
    inventory.assert_called_once_with(db, cfg)


def test_satellite_role_ignores_stale_legacy_true_for_inventory_default(db):
    with patch("src.sync_all.main_run_catalog_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_pull", return_value={}), \
         patch("src.sync_all.main_run_playlist_push", return_value={}), \
         patch("src.sync_all.main_run_lyrics_pull", return_value={}), \
         patch("src.sync_all.main_run_inventory_report") as inventory:
        cfg = _cfg(device_role="satellite", is_master=True)
        cfg.pop("report_inventory_to_host")
        out = main_run_sync_all(db, cfg)

    step = next(s for s in out["steps"] if s["name"] == "report_inventory")
    assert step["status"] == "skipped"
    inventory.assert_not_called()
