import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.db_manager import Track
from src.sync_dap import (
    EnhancedDapSyncer,
    SyncMode,
    SyncRequest,
    main_run_sync,
    run_sync_request,
)


def _pending_tracks(count: int) -> list[Track]:
    return [
        Track(
            mbid=f"track-{index}",
            title=f"Track {index}",
            artist="Artist",
            local_path=f"/music/{index}.flac",
            synced_to_dap=False,
        )
        for index in range(count)
    ]


def _syncer_with_tracks(count: int) -> EnhancedDapSyncer:
    syncer = EnhancedDapSyncer.__new__(EnhancedDapSyncer)
    syncer.db = MagicMock()
    syncer.db.get_all_tracks.return_value = _pending_tracks(count)
    syncer._convert_and_copy = MagicMock()
    return syncer


def test_large_sync_core_never_reads_stdin_without_an_injected_policy():
    syncer = _syncer_with_tracks(51)

    with patch("builtins.input", side_effect=AssertionError("stdin used")):
        syncer._sync_tracks(SyncMode.FULL_LIBRARY)

    assert syncer._convert_and_copy.call_count == 51


def test_large_sync_honours_an_injected_cli_confirmation():
    syncer = _syncer_with_tracks(51)
    confirm = MagicMock(return_value=False)

    syncer._sync_tracks(SyncMode.FULL_LIBRARY, confirm_large_sync=confirm)

    confirm.assert_called_once_with(51)
    syncer._convert_and_copy.assert_not_called()


def test_typed_sync_request_preserves_artist_and_confirmation_policy():
    syncer = MagicMock()
    syncer.get_sync_stats.return_value = {
        "total_tracks": 3,
        "pending_tracks": 2,
        "sync_percentage": 33.3,
    }
    confirm = MagicMock(return_value=True)
    request = SyncRequest(
        mode=SyncMode.SELECTIVE,
        conversion_format="opus",
        artist_filter="Massive Attack",
        reconcile=True,
    )

    with patch("src.sync_dap._build_syncer", return_value=syncer) as build:
        run_sync_request(
            MagicMock(),
            {"ffmpeg_path": "ffmpeg"},
            request,
            confirm_large_sync=confirm,
        )

    build.assert_called_once()
    assert build.call_args.args[2] == "opus"
    syncer.run_sync.assert_called_once_with(
        mode=SyncMode.SELECTIVE,
        artist_filter="Massive Attack",
        reconcile=True,
        confirm_large_sync=confirm,
    )


def test_compatibility_entry_point_signature_remains_exact():
    signature = inspect.signature(main_run_sync)
    assert list(signature.parameters) == [
        "db",
        "config",
        "sync_mode",
        "conversion_format",
        "reconcile",
    ]
    assert signature.parameters["sync_mode"].default == "playlists"
    assert signature.parameters["conversion_format"].default == "flac"
    assert signature.parameters["reconcile"].default is False


def test_manager_registry_covers_the_stable_menu_and_selective_prompt():
    import manager

    assert list(manager.COMMAND_HANDLERS) == [str(value) for value in range(1, 15)]
    config = SimpleNamespace(
        _config={"music_library_path": "/music"},
        jellyfin_enabled=False,
    )
    context = manager.CliContext(db_path=":memory:", config=config)
    database = MagicMock()
    database.__enter__.return_value = database

    with (
        patch.object(manager, "get_conversion_format", return_value="flac"),
        patch.object(manager, "DatabaseManager", return_value=database),
        patch.object(manager, "run_cli_sync") as run,
        patch("builtins.input", return_value="  Massive Attack  "),
    ):
        assert manager.handle_selective_sync(context) is False

    run.assert_called_once_with(
        database,
        config._config,
        SyncMode.SELECTIVE,
        "flac",
        artist_filter="Massive Attack",
    )
