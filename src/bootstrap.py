
import json
import logging
import os
from .config_manager import get_config
from .db_manager import DatabaseManager
from .library_scanner import main_scan_library
from .sync_ipod import main_run_sync
from .downloader import main_run_downloader
from .logger_setup import setup_logging

logger = logging.getLogger(__name__)

def run_bootstrap_sequence(config_data: dict, run_download: bool = False):
    """
    Executes a full bootstrap sequence:
    1. Save Config
    2. Scan Library
    3. Sync (Playlists mode)
    4. Download (Optional)
    """
    logger.info("Starting Bootstrap Sequence...")
    
    # 1. Save Config
    logger.info("Step 1/4: Saving Configuration...")
    config_path = "config.json" # Assumes running from root
    
    # Construct config dict from input (sanitized)
    # We expect config_data to match what the UI sends to save_config
    # We might need to ensure default values if missing
    
    final_config = {
        "music_library_path": config_data.get('music_library_path'),
        "downloads_path": config_data.get('downloads_path'),
        "slsk_command": config_data.get('slsk_command', ["slsk-batchdl"]),
        "picard_cmd_path": config_data.get('picard_cmd_path', "picard"),
        "ffmpeg_path": config_data.get('ffmpeg_path', "ffmpeg"),
        "ipod_mount_point": config_data.get('ipod_mount_point'),
        "ipod_music_dir_name": config_data.get('ipod_music_dir_name', "Music"),
        "ipod_playlist_dir_name": config_data.get('ipod_playlist_dir_name', "Playlists"),
        "database_file": config_data.get('database_file', "dap_library.db"),
        "slsk_username": config_data.get('slsk_username'),
        "slsk_password": config_data.get('slsk_password'),
        "fast_search": config_data.get('fast_search', False),
        "remove_ft": config_data.get('remove_ft', False),
        "desperate_mode": config_data.get('desperate_mode', False),
        "strict_quality": config_data.get('strict_quality', False),
        "conversion_sample_rate": config_data.get('conversion_sample_rate', 44100),
        "conversion_bit_depth": config_data.get('conversion_bit_depth', 16)
    }
    
    try:
        with open(config_path, 'w') as f:
            json.dump(final_config, f, indent=4)
        logger.info("Configuration saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        raise e

    # 2. Re-Initialize / Load Config
    # We need to ensure the global config object is updated. 
    # Since get_config singleton loads once, we might need a way to force reload 
    # or just create a new instance if possible, or trust that subsequent calls read the file?
    # ConfigManager is a singleton that loads on init. 
    # We can rely on the fact that we just wrote the file, so a fresh get_config() might NOT read it if instance exists.
    # Let's verify ConfigManager.
    # It has _instance. We might need to reset it.
    from .config_manager import ConfigManager
    ConfigManager._instance = None # Force reload
    config = get_config()
    db_path = config.db_path
    
    logger.info(f"Loaded config. DB Path: {db_path}")

    # 3. Scan Library
    logger.info("Step 2/4: Scanning Library...")
    with DatabaseManager(db_path) as db:
        main_scan_library(db, config._config)
        
    # 4. Sync iPod (if mount point exists)
    if config.get("ipod_mount_point"):
        logger.info("Step 3/4: Syncing iPod (Playlists Mode)...")
        try:
            with DatabaseManager(db_path) as db:
                main_run_sync(db, config._config, sync_mode="playlists", conversion_format="flac", reconcile=True)
        except Exception as e:
            logger.error(f"Sync failed (continuing): {e}")
    else:
        logger.info("Step 3/4: Skipped Sync (No Mount Point)")

    # 5. Download (Optional)
    if run_download:
        logger.info("Step 4/4: Running Downloader...")
        with DatabaseManager(db_path) as db:
            main_run_downloader(db, config._config)
    else:
        logger.info("Step 4/4: Skipped Downloader")
        
    logger.info("Bootstrap Sequence Complete!")
