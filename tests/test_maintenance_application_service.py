from unittest.mock import MagicMock

from src.services.maintenance_application_service import (
    MaintenanceApplicationResult,
    consolidate_album_editions,
    retag_library_files,
    trigger_jellyfin_scan,
)


def _database_factory(db):
    factory = MagicMock()
    context = factory.return_value
    context.__enter__.return_value = db
    context.__exit__.return_value = False
    return factory, context


def test_consolidation_closes_database_before_conditional_scan():
    events = []
    db = object()
    database_factory, context = _database_factory(db)
    context.__exit__.side_effect = lambda *_args: events.append("close")
    operation = MagicMock(
        side_effect=lambda *_args, **_kwargs: (
            events.append("mutate") or {"tracks_reassigned": 3}
        )
    )
    client = MagicMock()
    client.trigger_library_scan.side_effect = lambda: events.append("scan")
    client_factory = MagicMock(return_value=client)
    config_values = {"jellyfin_url": "http://media"}

    result = consolidate_album_editions(
        db_path="library.db",
        database_factory=database_factory,
        dry_run=False,
        consolidate_operation=operation,
        config_values=config_values,
        jellyfin_client_factory=client_factory,
    )

    assert result == MaintenanceApplicationResult(
        {
            "success": True,
            "dry_run": False,
            "tracks_reassigned": 3,
        }
    )
    assert events == ["mutate", "close", "scan"]
    database_factory.assert_called_once_with("library.db")
    operation.assert_called_once_with(db, dry_run=False)
    client_factory.assert_called_once_with(config_values)


def test_consolidation_skips_scan_for_preview_and_no_op_apply():
    db = object()
    database_factory, _context = _database_factory(db)
    client_factory = MagicMock()

    preview = consolidate_album_editions(
        db_path="library.db",
        database_factory=database_factory,
        dry_run=True,
        consolidate_operation=MagicMock(
            return_value={"tracks_reassigned": 8}
        ),
        config_values={},
        jellyfin_client_factory=client_factory,
    )
    no_op = consolidate_album_editions(
        db_path="library.db",
        database_factory=database_factory,
        dry_run=False,
        consolidate_operation=MagicMock(
            return_value={"tracks_reassigned": 0}
        ),
        config_values={},
        jellyfin_client_factory=client_factory,
    )

    assert preview.status_code == 200
    assert no_op.status_code == 200
    client_factory.assert_not_called()


def test_retag_scans_only_when_at_least_one_file_was_tagged():
    db = object()
    database_factory, _context = _database_factory(db)
    operation = MagicMock(side_effect=[{"tagged": 2}, {"tagged": 0}])
    client = MagicMock()
    client_factory = MagicMock(return_value=client)

    changed = retag_library_files(
        db_path="library.db",
        database_factory=database_factory,
        only_mismatched=True,
        retag_operation=operation,
        config_values={"role": "master"},
        jellyfin_client_factory=client_factory,
    )
    unchanged = retag_library_files(
        db_path="library.db",
        database_factory=database_factory,
        only_mismatched=False,
        retag_operation=operation,
        config_values={"role": "master"},
        jellyfin_client_factory=client_factory,
    )

    assert changed.payload == {"success": True, "tagged": 2}
    assert unchanged.payload == {"success": True, "tagged": 0}
    assert operation.call_args_list[0].kwargs == {"only_mismatched": True}
    assert operation.call_args_list[1].kwargs == {"only_mismatched": False}
    client.trigger_library_scan.assert_called_once_with()


def test_jellyfin_factory_failure_is_logged_without_failing_mutation():
    db = object()
    database_factory, _context = _database_factory(db)
    event_logger = MagicMock()
    factory_error = MagicMock(side_effect=RuntimeError("offline"))

    result = consolidate_album_editions(
        db_path="library.db",
        database_factory=database_factory,
        dry_run=False,
        consolidate_operation=MagicMock(
            return_value={"tracks_reassigned": 1}
        ),
        config_values={},
        jellyfin_client_factory=factory_error,
        event_logger=event_logger,
    )

    assert result.status_code == 200
    assert result.payload["tracks_reassigned"] == 1
    event_logger.warning.assert_called_once_with(
        "Jellyfin scan after %s failed: %s",
        "consolidate",
        factory_error.side_effect,
    )


def test_jellyfin_client_failure_is_logged_and_non_fatal():
    event_logger = MagicMock()
    client = MagicMock()
    client.trigger_library_scan.side_effect = RuntimeError("busy")

    assert trigger_jellyfin_scan(
        context="consolidate",
        config_values={},
        jellyfin_client_factory=MagicMock(return_value=client),
        event_logger=event_logger,
    ) is False
    event_logger.warning.assert_called_once_with(
        "Jellyfin scan after %s failed: %s",
        "consolidate",
        client.trigger_library_scan.side_effect,
    )


def test_mutation_failure_keeps_existing_500_shape_and_skips_scan():
    db = object()
    database_factory, _context = _database_factory(db)
    operation = MagicMock(side_effect=ValueError("bad metadata"))
    client_factory = MagicMock()
    event_logger = MagicMock()

    result = consolidate_album_editions(
        db_path="library.db",
        database_factory=database_factory,
        dry_run=False,
        consolidate_operation=operation,
        config_values={},
        jellyfin_client_factory=client_factory,
        event_logger=event_logger,
    )

    assert result == MaintenanceApplicationResult(
        {"success": False, "message": "bad metadata"},
        500,
    )
    event_logger.exception.assert_called_once_with(
        "api_consolidate_editions failed"
    )
    client_factory.assert_not_called()
