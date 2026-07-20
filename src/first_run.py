"""
First-run setup: pure config-build logic.

UI-free so the payload/validation can be unit tested in isolation. Both the
browser and Tauri first-run wizards submit to ``web_server.save_config``, which
routes their payloads through ``build_initial_config`` so every setup surface
writes the same role-aware config shape.
"""

import os
import socket
from typing import Optional, TypeAlias

from src.contracts import DeviceRole, InitialConfig


Role: TypeAlias = DeviceRole


def is_first_run(config_path: str = "config.json") -> bool:
    """True when no config.json has been written yet."""
    return not os.path.exists(config_path)


def suggest_device_name() -> str:
    """Hostname-based default so the wizard has something reasonable to
    show for `device_id` / display name without the user typing one."""
    try:
        return _device_name_from_hostname(socket.gethostname())
    except Exception:
        return "dap-satellite"


def _device_name_from_hostname(hostname: str) -> str:
    """Return the first hostname label or the historical fallback name."""
    return (hostname or "").split(".")[0] or "dap-satellite"


def _without_trailing_slash(value: str) -> str:
    """Normalize an optional base URL without changing other URL content."""
    return (value or "").rstrip("/")


def _base_config(
    role: Role,
    *,
    music_library_path: str,
    downloads_path: str,
    database_file: Optional[str],
    dap_mount_point: str,
    api_token: str,
    acoustid_api_key: str,
    contact_email: str,
    fast_search: bool,
    remove_ft: bool,
    desperate_mode: bool,
    strict_quality: bool,
) -> InitialConfig:
    """Build fields shared by every role without applying role policy."""
    return {
        # Direct callers retain the historical relative default.  The setup
        # server supplies an absolute path beside its resolved config file so
        # packaged desktop builds never create the DB under app Resources.
        "database_file": database_file or "dap_library.db",
        "music_library_path": music_library_path,
        "downloads_path": downloads_path,
        "ffmpeg_path": "ffmpeg",
        "slsk_cmd_base": ["slsk-batchdl"],
        "dap_mount_point": dap_mount_point or "",
        "dap_music_dir_name": "Music",
        "dap_playlist_dir_name": "Playlists",
        "conversion_sample_rate": 44100,
        "conversion_bit_depth": 16,
        "fast_search": bool(fast_search),
        "remove_ft": bool(remove_ft),
        "desperate_mode": bool(desperate_mode),
        "strict_quality": bool(strict_quality),
        "auto_tag_downloads": True,
        "is_master": role in ("master", "standalone"),
        "device_role": role,
        "acoustid_api_key": acoustid_api_key,
        "contact_email": contact_email,
        "artist_tag_max_age_days": 30,
        "library_maintenance_interval_seconds": 604800,
        "library_maintenance_on_startup": False,
        "api_token": api_token,
    }


def build_initial_config(
    role: Role,
    *,
    music_library_path: str,
    downloads_path: str,
    database_file: Optional[str] = None,
    dap_mount_point: str = "",
    master_url: str = "",
    public_master_url: str = "",
    api_token: str = "",
    device_name: str = "",
    slsk_username: str = "",
    slsk_password: str = "",
    jellyfin_url: str = "",
    jellyfin_api_key: str = "",
    jellyfin_user_id: str = "",
    jellyfin_music_library_path: str = "",
    lidarr_url: str = "",
    lidarr_api_key: str = "",
    lidarr_enabled: bool = False,
    lidarr_acquisition_handoff_enabled: bool = False,
    acoustid_api_key: str = "",
    contact_email: str = "",
    report_inventory_to_host: bool = False,
    contribute_to_host: bool = True,
    fast_search: bool = False,
    remove_ft: bool = False,
    desperate_mode: bool = False,
    strict_quality: bool = False,
) -> InitialConfig:
    """Shape a config.json dict for a first-run install.

    ``device_role`` drives the defaults.  The legacy ``is_master`` field is
    emitted only as a derived compatibility mirror.  Master accepts
    Jellyfin + Soulseek + Lidarr creds plus a ``public_master_url``
    that satellites use to reach back; satellite writes ``master_url``
    and an optional bearer token and leaves sldl config blank
    (downloads forward to the master); standalone is a local-only authority
    and keeps its own downloader, tag maintenance, and Daily Mix generation.
    """
    if role not in ("master", "satellite", "standalone"):
        raise ValueError(f"unknown role: {role}")
    if not music_library_path or not downloads_path:
        raise ValueError("music_library_path and downloads_path are required")

    cfg = _base_config(
        role,
        music_library_path=music_library_path,
        downloads_path=downloads_path,
        database_file=database_file,
        dap_mount_point=dap_mount_point,
        api_token=api_token,
        acoustid_api_key=acoustid_api_key,
        contact_email=contact_email,
        fast_search=fast_search,
        remove_ft=remove_ft,
        desperate_mode=desperate_mode,
        strict_quality=strict_quality,
    )

    if role == "master":
        cfg.update({
            "slsk_username": slsk_username,
            "slsk_password": slsk_password,
            "jellyfin_url": jellyfin_url,
            "jellyfin_api_key": jellyfin_api_key,
            "jellyfin_user_id": jellyfin_user_id,
            "jellyfin_music_library_path": jellyfin_music_library_path,
            "lidarr_enabled": bool(lidarr_enabled),
            "lidarr_acquisition_handoff_enabled": (
                lidarr_acquisition_handoff_enabled is True
            ),
            "lidarr_url": lidarr_url,
            "lidarr_api_key": lidarr_api_key,
            "master_url": "",
            "public_master_url": _without_trailing_slash(public_master_url),
            "report_inventory_to_host": True,
        })
        return cfg

    if role == "satellite":
        cfg.update({
            "slsk_username": "",
            "slsk_password": "",
            "master_url": _without_trailing_slash(master_url),
            "report_inventory_to_host": bool(report_inventory_to_host),
            "contribute_to_host": bool(contribute_to_host),
        })
        if device_name:
            cfg["device_name"] = device_name
        return cfg

    cfg.update({
        "slsk_username": slsk_username,
        "slsk_password": slsk_password,
        "jellyfin_url": jellyfin_url,
        "jellyfin_api_key": jellyfin_api_key,
        "jellyfin_user_id": jellyfin_user_id,
        "jellyfin_music_library_path": jellyfin_music_library_path,
        "master_url": "",
        "public_master_url": _without_trailing_slash(public_master_url),
        "report_inventory_to_host": False,
    })
    return cfg
