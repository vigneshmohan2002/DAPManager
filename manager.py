"""
Main command-line interface for DAP Manager with enhanced sync options
and Album Completeness Auditing.
"""

import logging
import os
from dataclasses import dataclass
from typing import Callable, Protocol

# Internal imports
from src.logger_setup import setup_logging
from src.config_manager import get_config
from src.db_manager import DatabaseManager
from src.library_scanner import main_scan_library, LibraryScanner
from src.spotify_client import SpotifyClient
from src.downloader import main_run_downloader, Downloader
from src.sync_dap import (
    SyncMode,
    SyncRequest,
    main_run_sync,
    run_sync_request,
)
from src.utils import EnvironmentManager
from src.album_completer import audit_library, complete_albums

# from src.clear_dupes import find_and_resolve_duplicates # Imported dynamically in main

# Setup logging first
setup_logging()
logger = logging.getLogger(__name__)


class ManagerConfig(Protocol):
    db_path: str
    music_library: str
    contact_email: str
    jellyfin_enabled: bool
    _config: dict


@dataclass(frozen=True)
class CliContext:
    db_path: str
    config: ManagerConfig


CommandHandler = Callable[[CliContext], bool]


def print_menu():
    """Displays the enhanced main menu."""
    print("\n" + "=" * 60)
    print("DAP (Digital Audio Player) Manager")
    print("=" * 60)
    print(" 1. [Setup] Scan local music library")
    print(" 2. [Add]   Add new Spotify playlist")
    print(" 3. [Fetch] Run download queue manually")
    print(" 4. [SYNC]  Sync PLAYLISTS to DAP")
    print(" 5. [SYNC]  Sync ENTIRE LIBRARY to DAP")
    print(" 6. [SYNC]  Selective sync (by artist)")
    print(" 7. [Utils] Clean DAP music directory (resets sync flags)")
    print(" 8. [Utils] RECONCILE DAP files to DB (Match existing files by MBID)")
    print(" 9. [Utils] CLEAR DUPLICATES (Resolve file conflicts)")
    print(" 10. [Batch] Run Batch Sync (Scan -> Queue -> Download -> Sync)")
    print(" 11. [Audit] Find Incomplete Albums")
    print(" 12. [Auto]  COMPLETE ALBUMS (find gaps -> queue -> download)")
    print(" 13. [PULL]  Pull from Jellyfin")
    print(" 14. Exit")
    print("=" * 60)
    print("NOTE: Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET in your environment.")


def get_conversion_format() -> str:
    """Prompt user for conversion format."""
    print("\nSelect conversion format:")
    print("  1. FLAC (lossless, large files)")
    print("  2. MP3 (universal, good quality)")
    print("  3. Opus (modern, best efficiency)")
    print("  4. AAC (Apple ecosystem)")

    choice = (
        input("\nEnter choice (1-4) or format name [default: flac]: ").strip().lower()
    )

    format_map = {
        "1": "flac",
        "2": "mp3",
        "3": "opus",
        "4": "aac",
        "flac": "flac",
        "mp3": "mp3",
        "opus": "opus",
        "aac": "aac",
        "": "flac",
    }

    return format_map.get(choice, "flac")


def confirm_large_sync(_track_count: int) -> bool:
    """CLI confirmation injected into the otherwise non-interactive sync core."""
    return input("This may take a while. Continue? (y/n): ").strip().lower() == "y"


def run_cli_sync(
    db: DatabaseManager,
    config: dict,
    mode: SyncMode,
    conversion_format: str,
    *,
    artist_filter: str | None = None,
    reconcile: bool = False,
) -> None:
    run_sync_request(
        db,
        config,
        SyncRequest(
            mode=mode,
            conversion_format=conversion_format,
            artist_filter=artist_filter,
            reconcile=reconcile,
        ),
        confirm_large_sync=confirm_large_sync,
    )


def show_sync_stats(db: DatabaseManager, config):
    """Display detailed sync statistics."""
    try:
        # We need to import here to avoid circular imports
        from src.sync_dap import EnhancedDapSyncer, ConversionOptions

        # Create a minimal syncer just for stats
        conversion_opts = ConversionOptions(
            sample_rate=config.get("conversion_sample_rate", 44100),
            bit_depth=config.get("conversion_bit_depth", 16),
            format="flac",
        )

        syncer = EnhancedDapSyncer(
            db=db,
            downloader=None,
            ffmpeg_path=config.get("ffmpeg_path", "ffmpeg"),
            dap_mount=config.get("dap_mount_point", ""),
            dap_music_dir=config.get("dap_music_dir_name", "Music"),
            dap_playlist_dir=config.get("dap_playlist_dir_name", "Playlists"),
            conversion_options=conversion_opts,
        )

        stats = syncer.get_sync_stats()

        print("\n" + "=" * 60)
        print("   Library Statistics ")
        print("=" * 60)
        print(f" Total tracks in library:  {stats['total_tracks']:,}")
        print(f" Already synced to DAP:   {stats['synced_tracks']:,}")
        print(f" Pending sync:             {stats['pending_tracks']:,}")
        print(f" Sync progress:            {stats['sync_percentage']:.1f}%")
        print(f" Total playlists:          {stats['total_playlists']}")
        print("=" * 60)

        if stats["pending_tracks"] > 0:
            print(f"\n {stats['pending_tracks']} tracks ready to sync!")

            # Calculate approximate sizes
            flac_size = stats["pending_tracks"] * 35  # ~35MB per track
            mp3_size = stats["pending_tracks"] * 10  # ~10MB per track
            opus_size = stats["pending_tracks"] * 5  # ~5MB per track

            print(f"\nEstimated space needed:")
            print(f"  FLAC:  ~{flac_size:,} MB ({flac_size/1024:.1f} GB)")
            print(f"  MP3:   ~{mp3_size:,} MB ({mp3_size/1024:.1f} GB)")
            print(f"  Opus:  ~{opus_size:,} MB ({opus_size/1024:.1f} GB)")
        else:
            print("\n All tracks are synced!")

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        print(f"\n Error getting stats: {e}")


def reconcile_dap(db: DatabaseManager, config: dict):
    """Reconcile tracks already on the DAP with the database."""
    # Ensure EnhancedDapSyncer is imported or available in the global scope
    from src.sync_dap import EnhancedDapSyncer

    # We must instantiate the Syncer using configuration
    downloader = Downloader(
        db=db,
        scanner=LibraryScanner(db, config.get("picard_cmd_path")),
        slsk_cmd_base=config.get("slsk_cmd_base", []),
        downloads_dir=config.get("downloads_path"),
        music_library_dir=config.get("music_library_path"),
        slsk_username=config.get("slsk_username"),
        slsk_password=config.get("slsk_password"),
    )

    syncer = EnhancedDapSyncer(
        db=db,
        downloader=downloader,
        ffmpeg_path=config.get("ffmpeg_path"),
        dap_mount=config.get("dap_mount_point"),
        dap_music_dir=config.get("dap_music_dir_name", "Music"),
        dap_playlist_dir=config.get("dap_playlist_dir_name", "Playlists"),
        conversion_options=None,  # Conversion options not needed for reconciliation
    )

    syncer.reconcile_dap_to_db()


def batch_sync():
    """
    Runs a full automation cycle:
    1. Scan local library
    2. Run downloader
    3. Sync to DAP
    """
    try:
        config = get_config()
    except SystemExit:
        return

    db_path = config.db_path

    try:
        # Step 1: Scan Library
        logger.info("Batch Sync Step 1/3: Scanning local library...")
        print("\n[Step 1/3] Scanning local library...")
        with DatabaseManager(db_path) as db:
            main_scan_library(db, config._config)

        # Step 2: Run Downloader
        logger.info("Batch Sync Step 2/3: Running downloader...")
        print("\n[Step 2/3] Running downloader...")
        with DatabaseManager(db_path) as db:
            main_run_downloader(db, config._config)

        # Step 3: Sync to DAP
        logger.info("Batch Sync Step 3/3: Syncing to DAP...")
        print("\n[Step 3/3] Syncing to DAP...")
        with DatabaseManager(db_path) as db:
            main_run_sync(db, config._config, sync_mode="playlists", conversion_format="flac")

        print("\nBatch sync completed successfully!")
        logger.info("Batch Sync completed successfully")

    except Exception as e:
        logger.error(f"Batch sync failed: {e}", exc_info=True)
        print(f"Batch sync error: {e}")
        raise


def run_queue_playlists(db_path, config, playlist_urls):
    """
    Queue multiple Spotify playlists for download.
    
    :param db_path: Path to the database
    :param config: ConfigManager instance
    :param playlist_urls: List of Spotify playlist URLs
    """
    try:
        with DatabaseManager(db_path) as db:
            spot_client = SpotifyClient(db)
            for url in playlist_urls:
                if url.strip():
                    logger.info(f"Processing playlist: {url}")
                    spot_client.process_playlist(url)

        logger.info(f"Queued {len(playlist_urls)} playlists successfully")
        print(f"Successfully queued {len(playlist_urls)} playlists")

    except Exception as e:
        logger.error(f"Failed to queue playlists: {e}", exc_info=True)
        print(f"Error queueing playlists: {e}")
        raise


def clean_dap_music(db, config):
    """Remove all music from DAP (useful for format changes)."""
    import shutil

    dap_mount = config.get("dap_mount_point")
    dap_music_dir = config.get("dap_music_dir_name", "Music")
    dap_music_path = os.path.join(dap_mount, dap_music_dir)

    if not os.path.exists(dap_music_path):
        print(f"\n DAP music path not found: {dap_music_path}")
        return

    # Count files
    file_count = sum(len(files) for _, _, files in os.walk(dap_music_path))

    print(f"\n WARNING: This will delete {file_count} files from:")
    print(f"    {dap_music_path}")
    print("\nThis action CANNOT be undone!")

    confirm = input("\nType 'DELETE' to confirm: ").strip()

    if confirm != "DELETE":
        print(" Operation cancelled.")
        return

    try:
        shutil.rmtree(dap_music_path)
        os.makedirs(dap_music_path)
        logger.info(f"Cleaned DAP music directory: {dap_music_path}")
        print(f"Deleted {file_count} files from DAP")
        print("Run a sync to repopulate with your desired format")
    except Exception as e:
        logger.error(f"Failed to clean DAP: {e}")
        print(f"Error: {e}")


def handle_scan_library(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Scanning Local Library")
    logger.info("=" * 60)
    print("\n Scanning music library...")
    print("This may take a while on first run (Picard tagging)...\n")
    with DatabaseManager(context.db_path) as db:
        main_scan_library(db, context.config._config)
    print("\n Scan complete!")
    logger.info("Scan Complete")
    return False


def handle_add_spotify_playlist(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Adding Spotify Playlist")
    logger.info("=" * 60)
    if not EnvironmentManager.validate_environment():
        return False

    print("\n Enter Spotify playlist URL")
    print("Example: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    playlist_url = input("\nURL: ").strip()
    if not playlist_url.startswith("http"):
        print(" Invalid URL. Must be a full Spotify playlist URL.")
        return False

    print("\nProcessing playlist...")
    with DatabaseManager(context.db_path) as db:
        SpotifyClient(db).process_playlist(playlist_url)
    print("\n Playlist processed!")
    logger.info("Playlist Processed")
    return False


def handle_download_queue(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Running Download Queue")
    logger.info("=" * 60)
    print("\nProcessing download queue...")
    print("This will download any tracks not found locally.\n")
    with DatabaseManager(context.db_path) as db:
        main_run_downloader(db, context.config._config)
    print("\n Download queue finished!")
    logger.info("Download Queue Finished")
    return False


def handle_playlist_sync(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Syncing Playlists to DAP")
    logger.info("=" * 60)
    print("\n Syncing playlist tracks to DAP...")
    conversion_format = get_conversion_format()
    print(f"\n Converting to {conversion_format.upper()}...")
    with DatabaseManager(context.db_path) as db:
        run_cli_sync(
            db,
            context.config._config,
            SyncMode.PLAYLISTS_ONLY,
            conversion_format,
        )
    print("\n Playlist sync complete!")
    logger.info("Playlist Sync Complete")
    return False


def handle_library_sync(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Syncing Full Library to DAP")
    logger.info("=" * 60)
    print("\n  FULL LIBRARY SYNC")
    print("This will sync ALL tracks in your library to the DAP.")
    with DatabaseManager(context.db_path) as db:
        show_sync_stats(db, context.config._config)

    print("\n" + "=" * 60)
    if input("Continue with full library sync? (y/n): ").strip().lower() != "y":
        print(" Sync cancelled.")
        return False

    conversion_format = get_conversion_format()
    print(f"\n Converting to {conversion_format.upper()}...")
    print(" This may take a LONG time for large libraries...\n")
    with DatabaseManager(context.db_path) as db:
        run_cli_sync(
            db,
            context.config._config,
            SyncMode.FULL_LIBRARY,
            conversion_format,
        )
    print("\n Full library sync complete!")
    logger.info("Full Library Sync Complete")
    return False


def handle_selective_sync(context: CliContext) -> bool:
    logger.info("=" * 60)
    logger.info("Selective Sync")
    logger.info("=" * 60)
    print("\nSelective Sync")
    print("Enter an artist name to sync only their tracks.")
    print("Leave blank to see all pending tracks.\n")
    conversion_format = get_conversion_format()
    artist_filter = input(
        "Enter artist name to filter (or press Enter for all): "
    ).strip() or None
    with DatabaseManager(context.db_path) as db:
        run_cli_sync(
            db,
            context.config._config,
            SyncMode.SELECTIVE,
            conversion_format,
            artist_filter=artist_filter,
        )
    print("\n Selective sync complete!")
    logger.info("Selective Sync Complete")
    return False


def handle_clean_dap(context: CliContext) -> bool:
    print("\n> CLEAN: Deleting all files from DAP Music folder...")
    with DatabaseManager(context.db_path) as db:
        clean_dap_music(db, context.config._config)
    logger.info("DAP clean process finished.")
    return False


def handle_reconcile_dap(context: CliContext) -> bool:
    print("\n> RECONCILE: Matching DAP files to local database...")
    with DatabaseManager(context.db_path) as db:
        reconcile_dap(db, context.config._config)
    logger.info("DAP reconciliation process finished.")
    return False


def handle_clear_duplicates(context: CliContext) -> bool:
    from src.clear_dupes import find_and_resolve_duplicates

    print("\n> DUPES: Analyzing library for duplicate tracks...")
    with DatabaseManager(context.db_path) as db:
        find_and_resolve_duplicates(db)
    logger.info("Duplicate resolution finished.")
    return False


def handle_batch_sync(_context: CliContext) -> bool:
    print("\n> BATCH: Starting full automation cycle...")
    logger.info("Starting Batch Sync")
    try:
        batch_sync()
    except Exception as error:
        logger.error(f"Batch sync failed: {error}")
        print(f"Batch sync failed: {error}")
    logger.info("Batch Sync finished.")
    return False


def handle_album_audit(context: CliContext) -> bool:
    logger.info("Starting Album Completeness Audit")
    print("\n> AUDIT: Finding incomplete albums...")
    with DatabaseManager(context.db_path) as db:
        incomplete = audit_library(db)

    if not incomplete:
        print("\nAll albums in your library are complete!")
        logger.info("Audit finished.")
        return False

    print(f"\nFound {len(incomplete)} incomplete albums:\n")
    for item in incomplete:
        status = f"{item['have']}/{item['total']} (missing {item['missing']})"
        print(f"  {item['artist']} - {item['album']}  [{status}]")
    print(f"\n{len(incomplete)} incomplete albums found.")
    proceed = input(
        "\nWould you like to queue missing tracks for download? (y/n): "
    ).strip().lower()
    if proceed != "y":
        logger.info("Audit finished.")
        return False

    print("\nQueueing missing tracks (this may take a while)...\n")
    with DatabaseManager(context.db_path) as db:
        summary = complete_albums(db)
    print(f"\nDone! Queued {summary['tracks_queued']} downloads.")
    print(f"  Albums discovered: {summary['albums_discovered']}")
    print(f"  Errors: {summary['errors']}")
    if summary["tracks_queued"] > 0:
        run_downloads = input("\nRun the downloader now? (y/n): ").strip().lower()
        if run_downloads == "y":
            with DatabaseManager(context.db_path) as db:
                main_run_downloader(db, context.config._config)
            print("\nDownload queue finished!")
    logger.info("Audit finished.")
    return False


def handle_complete_albums(context: CliContext) -> bool:
    logger.info("Starting Album Completion")
    print("\n" + "=" * 60)
    print("  ALBUM COMPLETION")
    print("=" * 60)
    print("This will:")
    print("  1. Discover album info for tracks missing it")
    print("  2. Identify all incomplete albums")
    print("  3. Queue missing tracks for download")
    print("  4. Run the downloader")
    print("=" * 60)
    if input("\nProceed? (y/n): ").strip().lower() != "y":
        print("Cancelled.")
        return False

    print("\n[Step 1-3] Analysing library and queueing missing tracks...\n")
    with DatabaseManager(context.db_path) as db:
        summary = complete_albums(db)

    print(f"\n{'=' * 60}")
    print(f"  Albums discovered:    {summary['albums_discovered']}")
    print(f"  Incomplete albums:    {summary['incomplete_albums']}")
    print(f"  Tracks queued:        {summary['tracks_queued']}")
    print(f"  Already queued:       {summary['albums_skipped_existing']}")
    print(f"  Errors:               {summary['errors']}")
    print(f"{'=' * 60}")

    if summary["tracks_queued"] <= 0:
        print("\nNo new tracks to download. Library looks good!")
        logger.info("Album Completion finished.")
        return False

    print("\n[Step 4] Running downloader...\n")
    with DatabaseManager(context.db_path) as db:
        main_run_downloader(db, context.config._config)
    print("\nDownload queue finished!")
    if input("\nRe-scan library to pick up new files? (y/n): ").strip().lower() == "y":
        with DatabaseManager(context.db_path) as db:
            main_scan_library(db, context.config._config)
        print("\nRe-scan complete!")
    logger.info("Album Completion finished.")
    return False


def handle_jellyfin_pull(context: CliContext) -> bool:
    logger.info("Starting Jellyfin Pull")
    if not context.config.jellyfin_enabled:
        print(
            "\nJellyfin not configured. Set jellyfin_url, "
            "jellyfin_api_key, and jellyfin_user_id in config.json."
        )
        return False

    from src.jellyfin_client import main_run_jellyfin_pull

    print("\n> JELLYFIN: Pulling audio + playlists from Jellyfin...")
    with DatabaseManager(context.db_path) as db:
        summary = main_run_jellyfin_pull(db, context.config._config)
    print(
        f"\nDone. Pulled {summary['pulled']}, skipped {summary['skipped']}, "
        f"failed {summary['failed']}, mirrored {summary['playlists_mirrored']} playlists."
    )
    logger.info("Jellyfin Pull finished.")
    return False


def handle_exit(_context: CliContext) -> bool:
    print("\n" + "=" * 60)
    print("  Thanks for using DAP Manager!")
    print("=" * 60)
    logger.info("Exiting DAP Manager")
    return True


COMMAND_HANDLERS: dict[str, CommandHandler] = {
    "1": handle_scan_library,
    "2": handle_add_spotify_playlist,
    "3": handle_download_queue,
    "4": handle_playlist_sync,
    "5": handle_library_sync,
    "6": handle_selective_sync,
    "7": handle_clean_dap,
    "8": handle_reconcile_dap,
    "9": handle_clear_duplicates,
    "10": handle_batch_sync,
    "11": handle_album_audit,
    "12": handle_complete_albums,
    "13": handle_jellyfin_pull,
    "14": handle_exit,
}


def main():
    """Main application loop driven by the explicit command registry."""
    try:
        config = get_config()
    except SystemExit:
        return

    from src import musicbrainz_client

    musicbrainz_client.configure(config.contact_email)
    context = CliContext(db_path=config.db_path, config=config)

    print("\n" + "=" * 60)
    print("  Welcome to DAP Manager!")
    print("=" * 60)
    print(f"  Database: {context.db_path}")
    print(f"  Library:  {config.music_library}")
    print("=" * 60)

    while True:
        print_menu()
        choice = input("\nEnter your choice (1-14): ").strip()
        handler = COMMAND_HANDLERS.get(choice)
        if handler is None:
            print(f"\n Invalid choice '{choice}'. Please enter 1-14.")
            continue

        try:
            if handler(context):
                break
        except KeyboardInterrupt:
            print("\n\nOperation cancelled by user (Ctrl+C).")
            logger.info("Operation cancelled by user")
            print("Returning to menu...\n")
        except Exception as error:
            logger.error("=" * 60)
            logger.error("AN ERROR OCCURRED")
            logger.error("=" * 60)
            logger.error(f"Error: {error}", exc_info=True)
            print("\n" + "=" * 60)
            print("  AN ERROR OCCURRED")
            print("=" * 60)
            print(f"Error: {error}")
            print("\nTroubleshooting:")
            print("  1. Check 'dap_manager.log' for detailed error info")
            print("  2. Verify all paths in 'config.json' are correct")
            print("  3. Ensure DAP is connected (for sync operations)")
            print("  4. Check environment variables (for Spotify features)")
            print("=" * 60)
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"\n Fatal error: {e}")
        print("Check 'dap_manager.log' for details.")
