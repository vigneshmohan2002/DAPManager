"""Post-mutation policy for library maintenance operations.

The Flask adapter is responsible only for parsing request flags and injecting
the existing database, maintenance, and Jellyfin dependencies.  This module
keeps database lifetime, response construction, and best-effort Jellyfin scan
semantics together so every caller observes the same ordering.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
from typing import Any, Dict, Mapping, Optional, Protocol


logger = logging.getLogger(__name__)


class DatabaseFactory(Protocol):
    """Construct the existing database context manager for one operation."""

    def __call__(self, db_path: str) -> AbstractContextManager[Any]: ...


class ConsolidateEditionsOperation(Protocol):
    def __call__(
        self,
        db: Any,
        *,
        dry_run: bool,
    ) -> Mapping[str, Any]: ...


class RetagFilesOperation(Protocol):
    def __call__(
        self,
        db: Any,
        *,
        only_mismatched: bool,
    ) -> Mapping[str, Any]: ...


class JellyfinClient(Protocol):
    def trigger_library_scan(self) -> Any: ...


class JellyfinClientFactory(Protocol):
    def __call__(
        self,
        config_values: Mapping[str, Any],
    ) -> Optional[JellyfinClient]: ...


@dataclass(frozen=True)
class MaintenanceApplicationResult:
    """JSON-shaped outcome translated by the HTTP adapter."""

    payload: Dict[str, Any]
    status_code: int = 200


def trigger_jellyfin_scan(
    *,
    context: str,
    config_values: Mapping[str, Any],
    jellyfin_client_factory: JellyfinClientFactory,
    event_logger: logging.Logger = logger,
) -> bool:
    """Best-effort Jellyfin refresh preserving the existing failure policy.

    The factory can legitimately return ``None`` when Jellyfin is disabled.
    Factory and client failures are both swallowed and logged so a successful
    metadata mutation never becomes an HTTP failure because Jellyfin is down.
    """
    try:
        client = jellyfin_client_factory(config_values)
        if client is None:
            return False
        client.trigger_library_scan()
        return True
    except Exception as exc:
        event_logger.warning(
            "Jellyfin scan after %s failed: %s",
            context,
            exc,
        )
        return False


def consolidate_album_editions(
    *,
    db_path: str,
    database_factory: DatabaseFactory,
    dry_run: bool,
    consolidate_operation: ConsolidateEditionsOperation,
    config_values: Mapping[str, Any],
    jellyfin_client_factory: JellyfinClientFactory,
    event_logger: logging.Logger = logger,
) -> MaintenanceApplicationResult:
    """Run edition consolidation and refresh Jellyfin only after real work."""
    try:
        with database_factory(db_path) as db:
            summary = consolidate_operation(db, dry_run=dry_run)

        if not dry_run and summary.get("tracks_reassigned", 0) > 0:
            trigger_jellyfin_scan(
                context="consolidate",
                config_values=config_values,
                jellyfin_client_factory=jellyfin_client_factory,
                event_logger=event_logger,
            )

        payload: Dict[str, Any] = {
            "success": True,
            "dry_run": dry_run,
        }
        payload.update(summary)
        return MaintenanceApplicationResult(payload)
    except Exception as exc:
        event_logger.exception("api_consolidate_editions failed")
        return MaintenanceApplicationResult(
            {"success": False, "message": str(exc)},
            500,
        )


def retag_library_files(
    *,
    db_path: str,
    database_factory: DatabaseFactory,
    only_mismatched: bool,
    retag_operation: RetagFilesOperation,
    config_values: Mapping[str, Any],
    jellyfin_client_factory: JellyfinClientFactory,
    event_logger: logging.Logger = logger,
) -> MaintenanceApplicationResult:
    """Rewrite file tags and refresh Jellyfin only when files were tagged."""
    try:
        with database_factory(db_path) as db:
            summary = retag_operation(
                db,
                only_mismatched=only_mismatched,
            )

        if summary.get("tagged", 0) > 0:
            trigger_jellyfin_scan(
                context="retag",
                config_values=config_values,
                jellyfin_client_factory=jellyfin_client_factory,
                event_logger=event_logger,
            )

        payload: Dict[str, Any] = {"success": True}
        payload.update(summary)
        return MaintenanceApplicationResult(payload)
    except Exception as exc:
        event_logger.exception("api_retag_files failed")
        return MaintenanceApplicationResult(
            {"success": False, "message": str(exc)},
            500,
        )
