"""
First-run setup: pure config-build logic.

UI-free so the payload/validation can be unit tested in isolation.
Today's only consumer is the web ``/setup`` page (which rolls its own
form-to-dict mapping in ``web_server.save_config``); ``build_initial_config``
is kept here as the reusable, role-aware payload builder for a future
Tauri first-run wizard or a refactor of the web setup.
"""

import os
import socket
from typing import Literal


Role = Literal["master", "satellite", "standalone"]


def is_first_run(config_path: str = "config.json") -> bool:
    """True when no config.json has been written yet."""
    return not os.path.exists(config_path)


def suggest_device_name() -> str:
    """Hostname-based default so the wizard has something reasonable to
    show for `device_id` / display name without the user typing one."""
    try:
        return (socket.gethostname() or "").split(".")[0] or "dap-satellite"
    except Exception:
        return "dap-satellite"


def build_initial_config(
    role: Role,
    *,
    music_library_path: str,
    downloads_path: str,
    dap_mount_point: str = "",
    master_url: str = "",
    api_token: str = "",
    device_name: str = "",
    slsk_username: str = "",
    slsk_password: str = "",
    jellyfin_url: str = "",
    jellyfin_api_key: str = "",
    jellyfin_user_id: str = "",
    report_inventory_to_host: bool = False,
) -> dict:
    """Shape a config.json dict for a first-run install.

    Role drives the defaults: master flips ``is_master`` and accepts
    Jellyfin + Soulseek creds; satellite writes ``master_url`` and an
    optional bearer token and leaves sldl config blank (downloads
    forward to the master); standalone is satellite-without-master.
    """
    if role not in ("master", "satellite", "standalone"):
        raise ValueError(f"unknown role: {role}")
    if not music_library_path or not downloads_path:
        raise ValueError("music_library_path and downloads_path are required")

    cfg: dict = {
        "database_file": "dap_library.db",
        "music_library_path": music_library_path,
        "downloads_path": downloads_path,
        "ffmpeg_path": "ffmpeg",
        "slsk_cmd_base": ["slsk-batchdl"],
        "dap_mount_point": dap_mount_point or "",
        "dap_music_dir_name": "Music",
        "dap_playlist_dir_name": "Playlists",
        "conversion_sample_rate": 44100,
        "conversion_bit_depth": 16,
        "fast_search": False,
        "remove_ft": False,
        "desperate_mode": False,
        "strict_quality": False,
        "is_master": role == "master",
        "device_role": "master" if role == "master" else "satellite",
    }

    if role == "master":
        cfg.update({
            "slsk_username": slsk_username,
            "slsk_password": slsk_password,
            "jellyfin_url": jellyfin_url,
            "jellyfin_api_key": jellyfin_api_key,
            "jellyfin_user_id": jellyfin_user_id,
            "master_url": "",
            "report_inventory_to_host": True,
        })
    elif role == "satellite":
        cfg.update({
            "slsk_username": "",
            "slsk_password": "",
            "master_url": (master_url or "").rstrip("/"),
            "api_token": api_token,
            "report_inventory_to_host": bool(report_inventory_to_host),
        })
        if device_name:
            cfg["device_name"] = device_name
    else:  # standalone
        cfg.update({
            "slsk_username": slsk_username,
            "slsk_password": slsk_password,
            "master_url": "",
            "report_inventory_to_host": False,
        })

    return cfg
