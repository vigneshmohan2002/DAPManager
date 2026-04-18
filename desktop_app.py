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

import requests

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QObject, QThread, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.config_manager import get_config
from src.db_manager import DatabaseManager, Track
from src.logger_setup import setup_logging

logger = logging.getLogger(__name__)

ALL_LIBRARY_ID = "__library__"


def tracks_to_suggestions(tracks: List[Track]) -> List[dict]:
    """Build /api/suggestions payload items from local Track rows.

    Prefers MBID (most reliable for the host's MusicBrainz-driven downloader);
    falls back to artist+title. Tracks with no MBID and no artist+title are
    skipped. Duplicates by MBID are collapsed.
    """
    items = []
    seen_mbids = set()
    for t in tracks:
        mbid = (t.mbid or "").strip()
        artist = (t.artist or "").strip()
        title = (t.title or "").strip()
        if mbid:
            if mbid in seen_mbids:
                continue
            seen_mbids.add(mbid)
            item = {"mbid": mbid}
            if artist:
                item["artist"] = artist
            if title:
                item["title"] = title
            items.append(item)
        elif artist and title:
            items.append({"artist": artist, "title": title})
    return items


def parse_manual_suggestions(text: str) -> List[dict]:
    """Parse a multiline block where each line is 'artist - title' (or just a free-form query).

    Blank lines and lines starting with '#' are skipped. Lines without ' - '
    fall through as a plain search_query.
    """
    items = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if " - " in line:
            artist, _, title = line.partition(" - ")
            artist, title = artist.strip(), title.strip()
            if artist and title:
                items.append({"artist": artist, "title": title})
                continue
        items.append({"search_query": line})
    return items


def compute_delete_paths(keep_path: Optional[str], all_paths: List[str]) -> List[str]:
    """Given a duplicate group's keep_path and all candidate paths, return the paths to delete.

    If keep_path is None or empty (user chose to skip), nothing is deleted.
    """
    if not keep_path:
        return []
    return [p for p in all_paths if p != keep_path]


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
        toolbar.addAction(QAction("Resolve Duplicates", self, triggered=self._resolve_duplicates))
        toolbar.addSeparator()
        toolbar.addAction(QAction("Suggest to Jellyfin", self, triggered=self._suggest_to_jellyfin))
        toolbar.addSeparator()
        for label, handler in (
            ("Pull Catalog", self._pull_catalog),
            ("Pull Playlists", self._pull_playlists),
            ("Push Playlists", self._push_playlists),
            ("Report Inventory", self._report_inventory),
        ):
            action = QAction(label, self, triggered=handler)
            toolbar.addAction(action)
            self._task_actions.append(action)
        toolbar.addSeparator()
        toolbar.addAction(QAction("Refresh", self, triggered=self._refresh))

        self.catalog_only_checkbox = QCheckBox("Show catalog-only")
        self.catalog_only_checkbox.setToolTip(
            "Include tracks the master catalog has but this device doesn't."
        )
        self.catalog_only_checkbox.toggled.connect(lambda _: self._reload_tracks())
        toolbar.addWidget(self.catalog_only_checkbox)

        splitter = QSplitter(Qt.Horizontal)

        self.playlist_list = QListWidget()
        self.playlist_list.itemSelectionChanged.connect(self._on_playlist_selected)
        splitter.addWidget(self.playlist_list)

        self.track_model = TrackTableModel()
        self.track_view = QTableView()
        self.track_view.setModel(self.track_model)
        self.track_view.setSortingEnabled(True)
        self.track_view.setSelectionBehavior(QTableView.SelectRows)
        self.track_view.setSelectionMode(QTableView.ExtendedSelection)
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

    def _catalog_only_enabled(self) -> bool:
        cb = getattr(self, "catalog_only_checkbox", None)
        return bool(cb and cb.isChecked())

    def _refresh(self):
        try:
            with DatabaseManager(self.db_path) as db:
                playlists = db.get_all_playlists()
                tracks = db.get_all_tracks(local_only=not self._catalog_only_enabled())
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

    def _reload_tracks(self):
        """Re-query the currently selected playlist/library view."""
        item = self.playlist_list.currentItem()
        if item is None:
            self._refresh()
            return
        self._on_playlist_selected()

    def _on_playlist_selected(self):
        item = self.playlist_list.currentItem()
        if not item:
            return
        pid = item.data(Qt.UserRole)
        local_only = not self._catalog_only_enabled()
        try:
            with DatabaseManager(self.db_path) as db:
                if pid == ALL_LIBRARY_ID:
                    tracks = db.get_all_tracks(local_only=local_only)
                else:
                    tracks = db.get_tracks_for_playlist(pid, local_only=local_only)
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

    def _suggest_to_jellyfin(self):
        host = (self.config.get("dap_manager_host_url") or "").strip()
        if not host:
            QMessageBox.warning(
                self,
                "Host not configured",
                "Set 'dap_manager_host_url' in config.json (e.g. http://jellyfin.local:5001) "
                "to point this device at the Jellyfin host's DAPManager.",
            )
            return

        selected_tracks = self._selected_tracks()
        if selected_tracks:
            items = tracks_to_suggestions(selected_tracks)
            source_desc = f"{len(items)} selected track(s)"
        else:
            dialog = QDialog(self)
            dialog.setWindowTitle("Suggest to Jellyfin")
            dialog.resize(520, 320)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel(
                "Select tracks in the library, or paste 'Artist - Title' one per line.\n"
                "Each suggestion will be queued on the Jellyfin host's downloader."
            ))
            editor = QPlainTextEdit()
            editor.setPlaceholderText("Radiohead - Idioteque\nPortishead - Roads")
            layout.addWidget(editor)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.Accepted:
                return
            items = parse_manual_suggestions(editor.toPlainText())
            source_desc = f"{len(items)} manual entry/entries"

        if not items:
            QMessageBox.information(self, "Nothing to suggest", "No usable items were found.")
            return

        url = host.rstrip("/") + "/api/suggestions"
        try:
            response = requests.post(url, json={"items": items}, timeout=20)
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as e:
            QMessageBox.critical(self, "Suggestion failed", f"Could not reach host:\n{e}")
            return
        except ValueError:
            QMessageBox.critical(
                self, "Suggestion failed", "Host returned a non-JSON response."
            )
            return

        if not result.get("success"):
            QMessageBox.warning(
                self, "Host rejected suggestions", result.get("message", "Unknown error")
            )
            return

        QMessageBox.information(
            self,
            "Suggestions sent",
            f"Sent {source_desc} to {host}.\n\n"
            f"Queued: {result.get('queued', 0)}\n"
            f"Skipped (already queued): {result.get('skipped', 0)}",
        )

    def _selected_tracks(self) -> List[Track]:
        indexes = self.track_view.selectionModel().selectedRows()
        tracks = []
        for idx in indexes:
            row = idx.row()
            if 0 <= row < len(self.track_model._tracks):
                tracks.append(self.track_model._tracks[row])
        return tracks

    def _resolve_duplicates(self):
        from src.clear_dupes import get_duplicates_for_ui, resolve_duplicates

        try:
            with DatabaseManager(self.db_path) as db:
                groups = get_duplicates_for_ui(db)
        except Exception as e:
            QMessageBox.critical(self, "Database error", str(e))
            return

        if not groups:
            QMessageBox.information(
                self, "Resolve Duplicates", "No duplicate tracks found."
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Resolve Duplicates ({len(groups)})")
        dialog.resize(780, 560)
        outer = QVBoxLayout(dialog)
        outer.addWidget(QLabel(
            "Pick which copy to keep in each group. \u201cSkip\u201d leaves the group untouched."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        inner = QVBoxLayout(container)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        # Each entry: (mbid, all_paths, button_group, skip_button)
        entries = []
        for group in groups:
            box = QGroupBox(f"{group.get('artist', '?')} — {group.get('title', '?')}")
            box_layout = QVBoxLayout(box)
            btn_group = QButtonGroup(box)
            btn_group.setExclusive(True)
            all_paths = []
            for candidate in group["candidates"]:
                path = candidate["path"]
                all_paths.append(path)
                tag = " (recommended)" if candidate.get("is_recommended") else ""
                rb = QRadioButton(f"Keep: {path}  [score {candidate['score']}]{tag}")
                if candidate.get("is_recommended"):
                    rb.setChecked(True)
                rb.setProperty("keep_path", path)
                btn_group.addButton(rb)
                box_layout.addWidget(rb)
            skip_rb = QRadioButton("Skip this group")
            skip_rb.setProperty("keep_path", "")
            btn_group.addButton(skip_rb)
            box_layout.addWidget(skip_rb)
            inner.addWidget(box)
            entries.append((group["mbid"], all_paths, btn_group))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        plans = []
        for mbid, all_paths, btn_group in entries:
            checked = btn_group.checkedButton()
            keep_path = checked.property("keep_path") if checked else ""
            delete_paths = compute_delete_paths(keep_path, all_paths)
            if keep_path and delete_paths:
                plans.append((mbid, keep_path, delete_paths))

        if not plans:
            QMessageBox.information(self, "Resolve Duplicates", "Nothing to resolve.")
            return

        errors = []
        resolved = 0
        deleted_total = 0
        try:
            with DatabaseManager(self.db_path) as db:
                for mbid, keep_path, delete_paths in plans:
                    result = resolve_duplicates(db, mbid, keep_path, delete_paths)
                    errors.extend(result.get("errors", []))
                    deleted_total += len(result.get("deleted", []))
                    resolved += 1
        except Exception as e:
            QMessageBox.critical(self, "Resolve failed", str(e))
            return

        msg = f"Resolved {resolved} group(s); deleted {deleted_total} file(s)."
        if errors:
            msg += "\n\nErrors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... and {len(errors) - 10} more"
            QMessageBox.warning(self, "Resolve Duplicates", msg)
        else:
            QMessageBox.information(self, "Resolve Duplicates", msg)
        self._refresh()

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

    def _pull_catalog(self):
        if not self.config.master_url:
            QMessageBox.warning(
                self,
                "master_url not configured",
                "Set 'master_url' in config.json to the master DAPManager's base URL "
                "(e.g. http://jellyfin.local:5001).",
            )
            return

        from src.catalog_sync import main_run_catalog_pull

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_catalog_pull(db, cfg, progress_callback=progress_callback)

        self._run_worker("Pull Catalog", task)

    def _pull_playlists(self):
        if not self.config.master_url:
            QMessageBox.warning(
                self,
                "master_url not configured",
                "Set 'master_url' in config.json to the master DAPManager's base URL.",
            )
            return

        from src.catalog_sync import main_run_playlist_pull

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_playlist_pull(db, cfg, progress_callback=progress_callback)

        self._run_worker("Pull Playlists", task)

    def _push_playlists(self):
        if not self.config.master_url:
            QMessageBox.warning(
                self,
                "master_url not configured",
                "Set 'master_url' in config.json to the master DAPManager's base URL.",
            )
            return

        from src.catalog_sync import main_run_playlist_push

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_playlist_push(db, cfg, progress_callback=progress_callback)

        self._run_worker("Push Playlists", task)

    def _report_inventory(self):
        if not self.config.report_inventory_to_host and not self.config.is_master:
            QMessageBox.warning(
                self,
                "Inventory reporting disabled",
                "Set 'report_inventory_to_host' to true in config.json to report "
                "this device's inventory to the master.",
            )
            return
        if not self.config.is_master and not self.config.master_url:
            QMessageBox.warning(
                self,
                "master_url not configured",
                "Set 'master_url' in config.json so this satellite can report inventory.",
            )
            return

        from src.inventory_sync import main_run_inventory_report

        db_path = self.db_path
        cfg = self.config._config

        def task(progress_callback=None):
            with DatabaseManager(db_path) as db:
                main_run_inventory_report(db, cfg, progress_callback=progress_callback)

        self._run_worker("Report Inventory", task)


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
