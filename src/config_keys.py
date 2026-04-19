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

SECRET_KEYS = frozenset({
    "slsk_password",
    "jellyfin_api_key",
    "api_token",
    "acoustid_api_key",
})

EDITABLE_KEYS = frozenset({
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
    "jellyfin_url",
    "jellyfin_api_key",
    "jellyfin_user_id",
    "dap_manager_host_url",
    "master_url",
    "device_id",
    "device_role",
    "report_inventory_to_host",
    "is_master",
    "sync_interval_seconds",
    "sync_on_startup",
    "api_token",
    "acoustid_api_key",
    "contact_email",
})

BOOL_KEYS = frozenset({
    "fast_search",
    "remove_ft",
    "desperate_mode",
    "strict_quality",
    "report_inventory_to_host",
    "is_master",
    "sync_on_startup",
})

# Ordered groups for UI rendering. Each entry: (label, [keys...]).
# Keep the ordering stable — users build muscle memory.
GROUPS = [
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
    ]),
    ("Jellyfin", [
        "jellyfin_url",
        "jellyfin_api_key",
        "jellyfin_user_id",
    ]),
    ("Multi-Device Sync", [
        "is_master",
        "device_id",
        "device_role",
        "master_url",
        "dap_manager_host_url",
        "report_inventory_to_host",
        "sync_interval_seconds",
        "sync_on_startup",
        "api_token",
    ]),
    ("Tagging (AcoustID / MusicBrainz)", [
        "acoustid_api_key",
        "contact_email",
    ]),
]
