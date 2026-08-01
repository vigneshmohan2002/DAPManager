"""
Shared metadata for user-editable config keys.

Both web_server.py and the desktop Settings dialog read from this so
they never drift. When adding a new config key that end users should be
able to edit from either UI:

1. Add it to ``EDITABLE_KEYS``.
2. Drop it into the right group in ``GROUPS`` so it shows up in the form.
3. If it's a secret (token/password/api_key), add it to ``SECRET_KEYS``
   — the UI masks it and the server's blank-means-keep logic kicks in.
4. If it's a bool, add it to ``BOOL_KEYS`` so the UI renders a checkbox.
"""

from typing import Dict, Final, FrozenSet, List, Tuple, TypeAlias

from src.contracts import ConfigValue


ConfigGroup: TypeAlias = Tuple[str, List[str]]


SECRET_KEYS: Final[FrozenSet[str]] = frozenset({
    "slsk_password",
    "jellyfin_api_key",
    "api_token",
    "acoustid_api_key",
    "lidarr_api_key",
})

# Runtime/UI defaults for optional keys introduced after many installations
# already had a config.json. The config endpoint overlays these without
# rewriting the user's file; saving Settings persists any explicit changes.
DEFAULT_VALUES: Final[Dict[str, ConfigValue]] = {
    "auto_tag_downloads": True,
    "artist_tag_max_age_days": 30,
    "jellyfin_music_library_path": "",
    "lidarr_acquisition_handoff_enabled": False,
    "library_maintenance_interval_seconds": 604800,
    "library_maintenance_on_startup": False,
    "download_worker_max_acquisitions": 2,
}

EDITABLE_KEYS: Final[FrozenSet[str]] = frozenset({
    "music_library_path",
    "downloads_path",
    "dap_mount_point",
    "dap_music_dir_name",
    "dap_playlist_dir_name",
    "slsk_username",
    "slsk_password",
    "fast_search",
    "remove_ft",
    "desperate_mode",
    "strict_quality",
    "download_worker_max_acquisitions",
    "jellyfin_url",
    "jellyfin_api_key",
    "jellyfin_user_id",
    "jellyfin_music_library_path",
    "master_url",
    "public_master_url",
    "device_id",
    "device_role",
    "report_inventory_to_host",
    "contribute_to_host",
    "contribution_attempt_timeout_seconds",
    "sync_interval_seconds",
    "sync_on_startup",
    "api_token",
    "acoustid_api_key",
    "auto_tag_downloads",
    "contact_email",
    "library_maintenance_interval_seconds",
    "library_maintenance_on_startup",
    "artist_tag_max_age_days",
    "lidarr_enabled",
    "lidarr_acquisition_handoff_enabled",
    "lidarr_url",
    "lidarr_api_key",
    "lidarr_quality_profile_id",
    "lidarr_root_folder_path",
    "lidarr_watch_enabled",
    "lidarr_watch_interval_seconds",
})

BOOL_KEYS: Final[FrozenSet[str]] = frozenset({
    "fast_search",
    "remove_ft",
    "desperate_mode",
    "strict_quality",
    "auto_tag_downloads",
    "report_inventory_to_host",
    "contribute_to_host",
    "sync_on_startup",
    "library_maintenance_on_startup",
    "lidarr_enabled",
    "lidarr_acquisition_handoff_enabled",
    "lidarr_watch_enabled",
})

# Ordered groups for UI rendering. Each entry: (label, [keys...]).
# Keep the ordering stable — users build muscle memory.
GROUPS: Final[List[ConfigGroup]] = [
    ("Paths", [
        "music_library_path",
        "downloads_path",
    ]),
    ("DAP", [
        "dap_mount_point",
        "dap_music_dir_name",
        "dap_playlist_dir_name",
    ]),
    ("Downloader (Soulseek)", [
        "slsk_username",
        "slsk_password",
        "fast_search",
        "remove_ft",
        "desperate_mode",
        "strict_quality",
        "download_worker_max_acquisitions",
    ]),
    ("Jellyfin", [
        "jellyfin_url",
        "jellyfin_api_key",
        "jellyfin_user_id",
        "jellyfin_music_library_path",
    ]),
    ("Multi-Device Sync", [
        "device_id",
        "device_role",
        "master_url",
        "public_master_url",
        "report_inventory_to_host",
        "contribute_to_host",
        "contribution_attempt_timeout_seconds",
        "sync_interval_seconds",
        "sync_on_startup",
        "api_token",
    ]),
    ("Tagging (AcoustID / MusicBrainz)", [
        "auto_tag_downloads",
        "acoustid_api_key",
        "contact_email",
        "artist_tag_max_age_days",
        "library_maintenance_interval_seconds",
        "library_maintenance_on_startup",
    ]),
    ("Lidarr Sidecar (master only)", [
        "lidarr_enabled",
        "lidarr_acquisition_handoff_enabled",
        "lidarr_url",
        "lidarr_api_key",
        "lidarr_quality_profile_id",
        "lidarr_root_folder_path",
        "lidarr_watch_enabled",
        "lidarr_watch_interval_seconds",
    ]),
]
