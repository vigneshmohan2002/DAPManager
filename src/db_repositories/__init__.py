"""Internal SQLite repositories used behind ``DatabaseManager``."""

from .album_maintenance import AlbumMaintenanceRepository
from .album_download_requests import AlbumDownloadRequestRepository
from .contributions import ContributionRepository
from .downloads import DownloadRepository
from .inventory import InventoryRepository
from .library import LibraryRepository
from .listening import ListeningRepository
from .metadata import MetadataRepository
from .playlists import PlaylistRepository
from .sync import SyncRepository

__all__ = [
    "AlbumMaintenanceRepository",
    "AlbumDownloadRequestRepository",
    "ContributionRepository",
    "DownloadRepository",
    "InventoryRepository",
    "LibraryRepository",
    "ListeningRepository",
    "MetadataRepository",
    "PlaylistRepository",
    "SyncRepository",
]
