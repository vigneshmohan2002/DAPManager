"""
DAP Manager desktop app (PySide6).

First-pass scaffold: iTunes-style layout with a playlist sidebar and a
track table backed by DatabaseManager. Toolbar actions are present but
not yet wired — long-running jobs will be moved onto QThread workers in
a follow-up commit.
"""

import inspect
import logging
import os
import sys
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QObject, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
)

from src.config_manager import get_config
from src.db_manager import DatabaseManager, Track
from src.logger_setup import setup_logging

logger = logging.getLogger(__name__)

ALL_LIBRARY_ID = "__library__"


def format_incomplete_album(item: dict) -> str:
    """Render an incomplete-album summary row for display in a list widget."""
    artist = item.get("artist") or "Unknown Artist"
    album = item.get("album") or "Unknown Album"
    have = item.get("have", 0)
    total = item.get("total", 0)
    missing = item.get("missing", max(total - have, 0))
    return f"{artist} — {album}  ({have}/{total}, {missing} missing)"


def parse_playlist_urls(text: str) -> List[str]:
    """Split a multi-line string into cleaned Spotify playlist URLs.

    Empty lines and lines starting with '#' are ignored. Whitespace is trimmed.
    Duplicates are removed while preserving first-occurrence order.
    """
    seen = set()
    urls: List[str] = []
    for line in (text or "").splitlines():
        url = line.strip()
        if not url or url.startswith("#"):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


class Worker(QObject):
    """Runs a blocking function on a QThread and emits progress/finished signals.

    If the wrapped callable accepts a `progress_callback` kwarg, the worker
    injects one that forwards dict payloads ({"message", "detail"}) or plain
    strings to the `progress` signal — matching the convention used by the
    Flask TaskManager and Jellyfin client.
    """

    progress = Signal(str)
    progress_ratio = Signal(int, int)  # (current, total)
    finished = Signal(bool, str)

    def __init__(self, task_name: str, fn: Callable, *args, **kwargs):
        super().__init__()
        self._task_name = task_name
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def run(self):
        try:
            if "progress_callback" in inspect.signature(self._fn).parameters:
                self._kwargs["progress_callback"] = self._forward_progress
            self._fn(*self._args, **self._kwargs)
            self.finished.emit(True, f"{self._task_name} complete")
        except Exception as e:
            logger.exception("Task '%s' failed", self._task_name)
            self.finished.emit(False, f"{self._task_name} failed: {e}")

    def _forward_progress(self, payload):
        if isinstance(payload, dict):
            msg = payload.get("message", "")
            detail = payload.get("detail")
            self.progress.emit(f"{msg} — {detail}" if detail else msg)
            current, total = payload.get("current"), payload.get("total")
            if isinstance(current, int) and isinstance(total, int) and total > 0:
                self.progress_ratio.emit(current, total)
        else:
            self.progress.emit(str(payload))


class TrackTableModel(QAbstractTableModel):
    HEADERS = ["Title", "Artist", "Album", "#", "On DAP", "Path"]

    def __init__(self, tracks: Optional[List[Track]] = None):
        super().__init__()
        self._tracks: List[Track] = tracks or []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        t = self._tracks[index.row()]
        col = index.column()
        if col == 0:
            return t.title or ""
        if col == 1:
            return t.artist or ""
        if col == 2:
            return t.album or ""
        if col == 3:
            return str(t.track_number) if t.track_number else ""
        if col == 4:
            return "✓" if t.synced_to_dap else ""
        if col == 5:
            return t.local_path or ""
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def set_tracks(self, tracks: List[Track]):
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()


class MainWindow(QMainWindow):
    def __init__(self, config, db_path: str):
        super().__init__()
        self.config = config
        self.db_path = db_path
        self._thread: Optional[QThread] = None
        self._worker: Optional[Worker] = None
        self.setWindowTitle("DAP Manager")
        self.resize(1200, 720)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._task_actions: List[QAction] = []
        for label, handler in (
            ("Scan Library", self._scan_library),
            ("Add Spotify Playlist", self._add_spotify_playlist),
            ("Download Queue", self._download_queue),
            ("Sync to DAP", self._sync_dap),
            ("Pull from Jellyfin", self._pull_jellyfin),
        ):
            action = QAction(label, self, triggered=handler)
            toolbar.addAction(action)
            self._task_actions.append(action)
        toolbar.addSeparator()
        toolbar.addAction(QAction("Audit Library", self, triggered=self._audit_library))
        complete_action = QAction("Complete Albums", self, triggered=self._complete_albums)
        toolbar.addAction(complete_action)
        self._task_actions.append(complete_action)
        toolbar.addSeparator()
        toolbar.addAction(QAction("Refresh", self, triggered=self._refresh))

        splitter = QSplitter(Qt.Horizontal)

        self.playlist_list = QListWidget()
        self.playlist_list.itemSelectionChanged.connect(self._on_playlist_selected)
        splitter.addWidget(self.playlist_list)

        self.track_model = TrackTableModel()
        self.track_view = QTableView()
        self.track_view.setModel(self.track_model)
        self.track_view.setSortingEnabled(True)
        self.track_view.setSelectionBehavior(QTableView.SelectRows)
        self.track_view.setAlternatingRowColors(True)
        self.track_view.verticalHeader().setVisible(False)
        header = self.track_view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        splitter.addWidget(self.track_view)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 960])
        self.setCentralWidget(splitter)

        status = QStatusBar()
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        status.addPermanentWidget(self.progress_bar)
        self.setStatusBar(status)
        self._refresh()

    def _refresh(self):
        try:
            with DatabaseManager(self.db_path) as db:
                playlists = db.get_all_playlists()
                tracks = db.get_all_tracks()
        except Exception as e:
            QMessageBox.critical(self, "Database error", str(e))
            return

        self.playlist_list.clear()
        library_item = QListWidgetItem("Library")
        library_item.setData(Qt.UserRole, ALL_LIBRARY_ID)
        self.playlist_list.addItem(library_item)
        for p in playlists:
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, p.playlist_id)
            self.playlist_list.addItem(item)
        self.playlist_list.setCurrentRow(0)

        self.track_model.set_tracks(tracks)
        self.statusBar().showMessage(
            f"{len(tracks)} tracks · {len(playlists)} playlists"
        )

    def _on_playlist_selected(self):
        item = self.playlist_list.currentItem()
        if not item:
            return
        pid = item.data(Qt.UserRole)
        try:
            with DatabaseManager(self.db_path) as db:
                if pid == ALL_LIBRARY_ID:
                    tracks = db.get_all_tracks()
                else:
                    tracks = db.get_tracks_for_playlist(pid)
        except Exception as e:
            QMessageBox.critical(self, "Database error", str(e))
            return
        self.track_model.set_tracks(tracks)
        self.statusBar().showMessage(f"{len(tracks)} tracks")

    def _placeholder(self):
        action = self.sender()
        name = action.text() if action else "Action"
        QMessageBox.information(self, name, f"{name} is not wired up yet.")

    # ---------- Task dispatch ----------

    def _run_worker(self, task_name: str, fn: Callable, *args, **kwargs):
        if self._thread is not None:
            QMessageBox.information(
                self, "Busy", "Another task is already running — wait for it to finish."
            )
            return
        thread = QThread(self)
        worker = Worker(task_name, fn, *args, **kwargs)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_task_progress)
        worker.progress_ratio.connect(self._on_task_progress_ratio)
        worker.finished.connect(self._on_task_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_thread_ref)
        self._thread = thread
        self._worker = worker
        self._set_actions_enabled(False)
        self.progress_bar.setRange(0, 0)  # indeterminate busy animation
        self.progress_bar.show()
        self.statusBar().showMessage(f"{task_name}: starting...")
        thread.start()

    def _on_task_progress(self, msg: str):
        self.statusBar().showMessage(msg)

    def _on_task_progress_ratio(self, current: int, total: int):
        if self.progress_bar.maximum() != total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setTextVisible(True)
        self.progress_bar.setValue(current)

    def _on_task_finished(self, success: bool, msg: str):
        self.progress_bar.hide()
        self.progress_bar.reset()
        self.progress_bar.setTextVisible(False)
        self.statusBar().showMessage(msg, 8000)
        if not success:
            QMessageBox.warning(self, "Task failed", msg)
        self._set_actions_enabled(True)
        self._refresh()

    def _clear_thread_ref(self):
        self._thread = None
        self._worker = None

    def _set_actions_enabled(self, enabled: bool):
        for action in self._task_actions:
            action.setEnabled(enabled)

    # ---------- Individual actions ----------

    def _scan_library(self):
        from src.library_scanner import main_scan_library

        db_path = self.db_path
        cfg = self.config._config

        def task():
            with DatabaseManager(db_path) as db:
                main_scan_library(db, cfg)

        self._run_worker("Scan Library", task)

    def _add_spotify_playlist(self):
        if not (os.environ.get("SPOTIPY_CLIENT_ID") and os.environ.get("SPOTIPY_CLIENT_SECRET")):
            QMessageBox.warning(
                self,
                "Spotify not configured",
                "Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in your environment.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Spotify Playlists")
        dialog.resize(520, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Paste one or more Spotify playlist URLs, one per line.\n"
            "Lines starting with '#' are ignored."
        ))
        editor = QPlainTextEdit()
        editor.setPlaceholderText("https://open.spotify.com/playlist/...")
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        urls = parse_playlist_urls(editor.toPlainText())
        if not urls:
            QMessageBox.information(self, "No URLs", "No playlist URLs were entered.")
            return

        from src.spotify_client import SpotifyClient

        db_path = self.db_path

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                client = SpotifyClient(db)
                total = len(urls)
                for idx, url in enumerate(urls, 1):
                    if progress_callback:
                        progress_callback({
                            "message": f"Processing playlist {idx}/{total}",
                            "detail": url,
                            "current": idx - 1,
                            "total": total,
                        })
                    client.process_playlist(url)
                if progress_callback:
                    progress_callback({"current": total, "total": total})

        label = "Add Spotify Playlist" if len(urls) == 1 else f"Queue {len(urls)} Spotify Playlists"
        self._run_worker(label, task)

    def _audit_library(self):
        try:
            with DatabaseManager(self.db_path) as db:
                incomplete = db.get_incomplete_albums()
        except Exception as e:
            QMessageBox.critical(self, "Database error", str(e))
            return

        if not incomplete:
            QMessageBox.information(
                self, "Audit Library", "All identified albums appear to be complete."
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Incomplete Albums ({len(incomplete)})")
        dialog.resize(640, 480)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            f"Found {len(incomplete)} incomplete albums. "
            "Use \u201cComplete Albums\u201d to queue missing tracks."
        ))
        list_widget = QListWidget()
        for item in incomplete:
            list_widget.addItem(format_incomplete_album(item))
        layout.addWidget(list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _complete_albums(self):
        choice = QMessageBox.question(
            self,
            "Complete Albums",
            "Discover missing tracks for incomplete albums and queue them.\n\n"
            "Also run the downloader and rescan when the queue is built?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.No,
        )
        if choice == QMessageBox.Cancel:
            return
        run_downloads = choice == QMessageBox.Yes

        from src.album_completer import complete_albums
        from src.downloader import main_run_downloader
        from src.library_scanner import main_scan_library

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                summary = complete_albums(db, progress_callback=progress_callback)

            if run_downloads and summary.get("tracks_queued", 0) > 0:
                if progress_callback:
                    progress_callback({"message": "Downloading queued tracks..."})
                with DatabaseManager(db_path) as db:
                    main_run_downloader(db, cfg, progress_callback=progress_callback)
                if progress_callback:
                    progress_callback({"message": "Re-scanning library..."})
                with DatabaseManager(db_path) as db:
                    main_scan_library(db, cfg)

        self._run_worker("Complete Albums", task)

    def _download_queue(self):
        from src.downloader import main_run_downloader

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_downloader(db, cfg, progress_callback=progress_callback)

        self._run_worker("Download Queue", task)

    def _sync_dap(self):
        if not self.config.get("dap_mount_point"):
            QMessageBox.warning(
                self, "DAP not configured", "Set dap_mount_point in config.json first."
            )
            return

        mode, ok = QInputDialog.getItem(
            self, "Sync to DAP", "Sync mode:", ["playlists", "library"], 0, False
        )
        if not ok:
            return
        fmt, ok = QInputDialog.getItem(
            self, "Sync to DAP", "Audio format:", ["flac", "mp3", "opus", "aac"], 0, False
        )
        if not ok:
            return

        from src.sync_dap import main_run_sync

        db_path = self.db_path
        cfg = self.config._config

        def task():
            with DatabaseManager(db_path) as db:
                main_run_sync(db, cfg, sync_mode=mode, conversion_format=fmt)

        self._run_worker(f"Sync to DAP ({mode}, {fmt})", task)

    def _pull_jellyfin(self):
        if not self.config.jellyfin_enabled:
            QMessageBox.warning(
                self,
                "Jellyfin not configured",
                "Set jellyfin_url, jellyfin_api_key, and jellyfin_user_id in config.json.",
            )
            return

        from src.jellyfin_client import main_run_jellyfin_pull

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_jellyfin_pull(db, cfg, progress_callback=progress_callback)

        self._run_worker("Pull from Jellyfin", task)


def main():
    setup_logging()
    config = get_config()
    app = QApplication(sys.argv)
    app.setApplicationName("DAP Manager")
    window = MainWindow(config, config.db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
