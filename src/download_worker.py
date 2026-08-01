"""Persistent, lease-fenced automatic processing for the master queue."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
import logging
import os
import shutil
import socket
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from .db_manager import DatabaseManager
from .downloader import DownloadRunSummary, main_run_downloader
from .services.download_residue_service import enforce_download_residue_budget


logger = logging.getLogger(__name__)

DEFAULT_MAX_ACQUISITIONS = 2
DEFAULT_POLL_SECONDS = 15
DEFAULT_WORKER_LEASE_SECONDS = 60
DEFAULT_BASE_FREE_GIB = 20
DEFAULT_PER_ACQUISITION_GIB = 5

DatabaseFactory = Callable[[str], DatabaseManager]
DownloadRunner = Callable[..., DownloadRunSummary]


class AutomaticDownloadWorker:
    """Run due queue items without allowing two masters to own the worker.

    Acquisitions use independent database connections and can overlap. The
    downloader itself serializes the validation/import commit, which keeps
    filesystem and catalog mutations deterministic while Soulseek waits in
    parallel.
    """

    def __init__(
        self,
        db_path: str,
        config_values: Mapping[str, Any],
        *,
        database_factory: DatabaseFactory = DatabaseManager,
        runner: DownloadRunner = main_run_downloader,
        poll_seconds: int = DEFAULT_POLL_SECONDS,
        lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS,
    ) -> None:
        self.db_path = str(db_path)
        self.config_values = dict(config_values)
        self.database_factory = database_factory
        self.runner = runner
        self.poll_seconds = max(1, int(poll_seconds))
        self.lease_seconds = max(10, int(lease_seconds))
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.is_alive:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="dap-download-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))

    def wake(self) -> None:
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        with self.database_factory(self.db_path) as db:
            db.set_download_worker_paused(paused)
        self.wake()

    def run_available_once(self) -> int:
        """Process one adaptive batch; useful for startup and deterministic tests."""
        with self.database_factory(self.db_path) as db:
            state = db.get_download_worker_state()
            if bool(state.get("is_paused")):
                return 0
            if not db.claim_download_worker(self.owner, self.lease_seconds):
                return 0
        try:
            return self._run_batch()
        finally:
            with self.database_factory(self.db_path) as db:
                db.release_download_worker(self.owner, "idle")

    def _configured_max_acquisitions(self) -> int:
        raw = self.config_values.get(
            "download_worker_max_acquisitions",
            DEFAULT_MAX_ACQUISITIONS,
        )
        try:
            return max(1, min(int(raw), DEFAULT_MAX_ACQUISITIONS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_ACQUISITIONS

    def _allowed_acquisitions(self) -> int:
        maximum = self._configured_max_acquisitions()
        path = str(
            self.config_values.get("downloads_path")
            or self.config_values.get("downloads_dir")
            or "."
        )
        try:
            free = int(shutil.disk_usage(path).free)
        except OSError:
            logger.warning("Cannot inspect download disk space at %s", path)
            return 0
        base = DEFAULT_BASE_FREE_GIB * 1024 ** 3
        per_job = DEFAULT_PER_ACQUISITION_GIB * 1024 ** 3
        allowed = max(0, (free - base) // per_job)
        return min(maximum, int(allowed))

    def _run_item(self, item_id: int) -> DownloadRunSummary:
        with self.database_factory(self.db_path) as db:
            return self.runner(
                db,
                dict(self.config_values),
                include_item_ids=[int(item_id)],
            )

    def _run_batch(self) -> int:
        downloads_path = str(
            self.config_values.get("downloads_path")
            or self.config_values.get("downloads_dir")
            or "."
        )
        cleanup = enforce_download_residue_budget(downloads_path)
        if cleanup.removed_directories:
            logger.info(
                "Removed %s expired/over-budget download quarantine directorie(s)",
                cleanup.removed_directories,
            )
        slots = self._allowed_acquisitions()
        if slots <= 0:
            self._heartbeat("blocked", (), "Waiting for download disk reserve")
            return 0
        with self.database_factory(self.db_path) as db:
            item_ids = db.list_claimable_download_ids(slots)
        if not item_ids:
            self._heartbeat("idle", (), "No due download items")
            return 0

        self._heartbeat("running", item_ids, f"Acquiring {len(item_ids)} item(s)")
        with ThreadPoolExecutor(
            max_workers=len(item_ids),
            thread_name_prefix="dap-download",
        ) as executor:
            futures: Dict[Future[DownloadRunSummary], int] = {
                executor.submit(self._run_item, item_id): item_id
                for item_id in item_ids
            }
            attempted = 0
            pending = set(futures)
            while pending:
                done, pending = wait(pending, timeout=5.0)
                for future in done:
                    item_id = futures[future]
                    try:
                        summary = future.result()
                        attempted += int(summary.attempted_count)
                        logger.info(
                            "Automatic download item %s: %s",
                            item_id,
                            summary.task_message,
                        )
                    except Exception:
                        logger.exception(
                            "Automatic download item %s crashed", item_id
                        )
                if pending:
                    self._heartbeat(
                        "running",
                        (futures[future] for future in pending),
                        f"Acquiring {len(pending)} item(s)",
                    )
        self._heartbeat("idle", (), "Batch complete")
        return attempted

    def _heartbeat(
        self,
        state: str,
        item_ids: Iterable[int],
        detail: str,
    ) -> bool:
        ids = tuple(int(item_id) for item_id in item_ids)
        current = ids[0] if len(ids) == 1 else None
        if len(ids) > 1:
            detail = f"{detail}: " + ", ".join(str(item_id) for item_id in ids)
        with self.database_factory(self.db_path) as db:
            return db.heartbeat_download_worker(
                self.owner,
                self.lease_seconds,
                state=state,
                current_item_id=current,
                detail=detail,
            )

    def _loop(self) -> None:
        owns_lease = False
        try:
            while not self._stop.is_set():
                with self.database_factory(self.db_path) as db:
                    state = db.get_download_worker_state()
                    paused = bool(state.get("is_paused"))
                    if not paused:
                        owns_lease = db.claim_download_worker(
                            self.owner,
                            self.lease_seconds,
                        )
                if paused or not owns_lease:
                    self._wait()
                    continue
                processed = self._run_batch()
                if processed == 0:
                    self._wait()
        except Exception:
            logger.exception("Automatic download worker stopped unexpectedly")
        finally:
            if owns_lease:
                try:
                    with self.database_factory(self.db_path) as db:
                        db.release_download_worker(self.owner, "stopped")
                except Exception:
                    logger.warning("Could not release download worker lease", exc_info=True)

    def _wait(self) -> None:
        self._wake.wait(self.poll_seconds)
        self._wake.clear()
