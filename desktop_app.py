"""
DAP Manager desktop app (PySide6).

First-pass scaffold: iTunes-style layout with a playlist sidebar and a
track table backed by DatabaseManager. Toolbar actions are present but
not yet wired — long-running jobs will be moved onto QThread workers in
a follow-up commit.
"""

import sys
from typing import List, Optional

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QTableView,
    QToolBar,
)

from src.config_manager import get_config
from src.db_manager import DatabaseManager, Track
from src.logger_setup import setup_logging


ALL_LIBRARY_ID = "__library__"


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
    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.setWindowTitle("DAP Manager")
        self.resize(1200, 720)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label in (
            "Scan Library",
            "Add Spotify Playlist",
            "Sync to DAP",
            "Pull from Jellyfin",
        ):
            toolbar.addAction(QAction(label, self, triggered=self._placeholder))
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

        self.setStatusBar(QStatusBar())
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


def main():
    setup_logging()
    config = get_config()
    app = QApplication(sys.argv)
    app.setApplicationName("DAP Manager")
    window = MainWindow(config.db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
