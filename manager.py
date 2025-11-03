# ============================================================================
# FILE: manager.py (COMPLETE ENHANCED VERSION)
# ============================================================================
"""
Main command-line interface for DAP Manager with enhanced sync options.
"""

import logging
from logger_setup import setup_logging
from config_manager import get_config
from db_manager import DatabaseManager
from library_scanner import main_scan_library
from spotify_client import SpotifyClient
from downloader import main_run_downloader
from sync_ipod import main_run_sync
from utils import EnvironmentManager

# Setup logging first
setup_logging()
logger = logging.getLogger(__name__)


def print_menu():
    """Displays the enhanced main menu."""
    print("\n" + "=" * 60)
    print("  🎵  DAP (Digital Audio Player) Manager  🎵")
    print("=" * 60)
    print(" 1. [Setup] Scan local music library")
    print(" 2. [Add]   Add new Spotify playlist")
    print(" 3. [Fetch] Run download queue manually")
    print(" 4. [SYNC]  Sync PLAYLISTS to iPod")
    print(" 5. [SYNC]  Sync ENTIRE LIBRARY to iPod")
    print(" 6. [SYNC]  Selective sync (by artist)")
    print(" 7. [Stats] Show sync statistics")
    print(" 8. [Clean] Remove synced tracks from iPod")
    print(" 9. Exit")
    print("=" * 60)
    print("NOTE: Set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET")
    print("      as environment variables for Spotify features.")
    print("=" * 60)


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


def show_sync_stats(db: DatabaseManager, config):
    """Display detailed sync statistics."""
    try:
        # We need to import here to avoid circular imports
        from sync_ipod import EnhancedIpodSyncer, ConversionOptions

        # Create a minimal syncer just for stats
        conversion_opts = ConversionOptions(
            sample_rate=config.get("conversion_sample_rate", 44100),
            bit_depth=config.get("conversion_bit_depth", 16),
            format="flac",
        )

        syncer = EnhancedIpodSyncer(
            db=db,
            downloader=None,
            ffmpeg_path=config.get("ffmpeg_path", "ffmpeg"),
            ipod_mount=config.get("ipod_mount_point", ""),
            ipod_music_dir=config.get("ipod_music_dir_name", "Music"),
            ipod_playlist_dir=config.get("ipod_playlist_dir_name", "Playlists"),
            conversion_options=conversion_opts,
        )

        stats = syncer.get_sync_stats()

        print("\n" + "=" * 60)
        print("  📊  Library Statistics  📊")
        print("=" * 60)
        print(f" Total tracks in library:  {stats['total_tracks']:,}")
        print(f" Already synced to iPod:   {stats['synced_tracks']:,}")
        print(f" Pending sync:             {stats['pending_tracks']:,}")
        print(f" Sync progress:            {stats['sync_percentage']:.1f}%")
        print(f" Total playlists:          {stats['total_playlists']}")
        print("=" * 60)

        if stats["pending_tracks"] > 0:
            print(f"\n💡 {stats['pending_tracks']} tracks ready to sync!")

            # Calculate approximate sizes
            flac_size = stats["pending_tracks"] * 35  # ~35MB per track
            mp3_size = stats["pending_tracks"] * 10  # ~10MB per track
            opus_size = stats["pending_tracks"] * 5  # ~5MB per track

            print(f"\nEstimated space needed:")
            print(f"  FLAC:  ~{flac_size:,} MB ({flac_size/1024:.1f} GB)")
            print(f"  MP3:   ~{mp3_size:,} MB ({mp3_size/1024:.1f} GB)")
            print(f"  Opus:  ~{opus_size:,} MB ({opus_size/1024:.1f} GB)")
        else:
            print("\n✅ All tracks are synced!")

    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        print(f"\n❌ Error getting stats: {e}")


def clean_ipod_music(config):
    """Remove all music from iPod (useful for format changes)."""
    import os
    import shutil

    ipod_mount = config.get("ipod_mount_point")
    ipod_music_dir = config.get("ipod_music_dir_name", "Music")
    ipod_music_path = os.path.join(ipod_mount, ipod_music_dir)

    if not os.path.exists(ipod_music_path):
        print(f"\n❌ iPod music path not found: {ipod_music_path}")
        return

    # Count files
    file_count = sum(len(files) for _, _, files in os.walk(ipod_music_path))

    print(f"\n⚠️  WARNING: This will delete {file_count} files from:")
    print(f"    {ipod_music_path}")
    print("\nThis action CANNOT be undone!")

    confirm = input("\nType 'DELETE' to confirm: ").strip()

    if confirm != "DELETE":
        print("❌ Operation cancelled.")
        return

    try:
        shutil.rmtree(ipod_music_path)
        os.makedirs(ipod_music_path)
        logger.info(f"Cleaned iPod music directory: {ipod_music_path}")
        print(f"✅ Deleted {file_count} files from iPod")
        print("💡 Run a sync to repopulate with your desired format")
    except Exception as e:
        logger.error(f"Failed to clean iPod: {e}")
        print(f"❌ Error: {e}")


def main():
    """Main application loop."""

    # Load configuration
    try:
        config = get_config()
    except SystemExit:
        return

    db_path = config.db_path

    print("\n" + "=" * 60)
    print("  Welcome to DAP Manager!")
    print("=" * 60)
    print(f"  Database: {db_path}")
    print(f"  Library:  {config.music_library}")
    print("=" * 60)

    while True:
        print_menu()
        choice = input("\nEnter your choice (1-9): ").strip()

        try:
            if choice == "1":
                # ============================================
                # Scan Local Library
                # ============================================
                logger.info("=" * 60)
                logger.info("Scanning Local Library")
                logger.info("=" * 60)

                print("\n🔍 Scanning music library...")
                print("This may take a while on first run (Picard tagging)...\n")

                with DatabaseManager(db_path) as db:
                    main_scan_library(db, config._config)

                print("\n✅ Scan complete!")
                logger.info("Scan Complete")

            elif choice == "2":
                # ============================================
                # Add Spotify Playlist
                # ============================================
                logger.info("=" * 60)
                logger.info("Adding Spotify Playlist")
                logger.info("=" * 60)

                # Check environment variables
                if not EnvironmentManager.validate_environment():
                    continue

                print("\n📝 Enter Spotify playlist URL")
                print(
                    "Example: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
                )

                playlist_url = input("\nURL: ").strip()

                if not playlist_url.startswith("https://open.spotify.com/playlist/"):
                    print("❌ Invalid URL. Must be a full Spotify playlist URL.")
                    continue

                print(f"\n🎵 Processing playlist...")

                with DatabaseManager(db_path) as db:
                    SpotifyClient.process_playlist(db, playlist_url)

                print("\n✅ Playlist processed!")
                logger.info("Playlist Processed")

            elif choice == "3":
                # ============================================
                # Run Download Queue
                # ============================================
                logger.info("=" * 60)
                logger.info("Running Download Queue")
                logger.info("=" * 60)

                print("\n⬇️  Processing download queue...")
                print("This will download any tracks not found locally.\n")

                with DatabaseManager(db_path) as db:
                    main_run_downloader(db, config._config)

                print("\n✅ Download queue finished!")
                logger.info("Download Queue Finished")

            elif choice == "4":
                # ============================================
                # Sync Playlists to iPod
                # ============================================
                logger.info("=" * 60)
                logger.info("Syncing Playlists to iPod")
                logger.info("=" * 60)

                print("\n🎧 Syncing playlist tracks to iPod...")

                fmt = get_conversion_format()
                print(f"\n🔄 Converting to {fmt.upper()}...")

                with DatabaseManager(db_path) as db:
                    main_run_sync(
                        db, config._config, sync_mode="playlists", conversion_format=fmt
                    )

                print("\n✅ Playlist sync complete!")
                logger.info("Playlist Sync Complete")

            elif choice == "5":
                # ============================================
                # Sync FULL Library to iPod
                # ============================================
                logger.info("=" * 60)
                logger.info("Syncing Full Library to iPod")
                logger.info("=" * 60)

                print("\n⚠️  FULL LIBRARY SYNC")
                print("This will sync ALL tracks in your library to the iPod.")

                # Show what will be synced
                with DatabaseManager(db_path) as db:
                    show_sync_stats(db, config._config)

                print("\n" + "=" * 60)
                confirm = (
                    input("Continue with full library sync? (y/n): ").strip().lower()
                )

                if confirm != "y":
                    print("❌ Sync cancelled.")
                    continue

                fmt = get_conversion_format()
                print(f"\n🔄 Converting to {fmt.upper()}...")
                print("⏳ This may take a LONG time for large libraries...\n")

                with DatabaseManager(db_path) as db:
                    main_run_sync(
                        db, config._config, sync_mode="library", conversion_format=fmt
                    )

                print("\n✅ Full library sync complete!")
                logger.info("Full Library Sync Complete")

            elif choice == "6":
                # ============================================
                # Selective Sync (by Artist)
                # ============================================
                logger.info("=" * 60)
                logger.info("Selective Sync")
                logger.info("=" * 60)

                print("\n🎯 Selective Sync")
                print("Enter an artist name to sync only their tracks.")
                print("Leave blank to see all pending tracks.\n")

                fmt = get_conversion_format()

                with DatabaseManager(db_path) as db:
                    main_run_sync(
                        db, config._config, sync_mode="selective", conversion_format=fmt
                    )

                print("\n✅ Selective sync complete!")
                logger.info("Selective Sync Complete")

            elif choice == "7":
                # ============================================
                # Show Sync Statistics
                # ============================================
                with DatabaseManager(db_path) as db:
                    show_sync_stats(db, config._config)

            elif choice == "8":
                # ============================================
                # Clean iPod Music
                # ============================================
                logger.info("=" * 60)
                logger.info("Cleaning iPod Music Directory")
                logger.info("=" * 60)

                print("\n🧹 Clean iPod Music")
                print("This will remove ALL music from your iPod.")
                print("Useful when changing formats (FLAC → MP3, etc.)")

                clean_ipod_music(config._config)

                logger.info("Clean Complete")

            elif choice == "9":
                # ============================================
                # Exit
                # ============================================
                print("\n" + "=" * 60)
                print("  Thanks for using DAP Manager!")
                print("=" * 60)
                logger.info("Exiting DAP Manager")
                break

            else:
                print(f"\n❌ Invalid choice '{choice}'. Please enter 1-9.")

        except KeyboardInterrupt:
            print("\n\n⚠️  Operation cancelled by user (Ctrl+C).")
            logger.info("Operation cancelled by user")
            print("Returning to menu...\n")
        except Exception as e:
            logger.error("=" * 60)
            logger.error("AN ERROR OCCURRED")
            logger.error("=" * 60)
            logger.error(f"Error: {e}", exc_info=True)
            print("\n" + "=" * 60)
            print("  ❌ AN ERROR OCCURRED")
            print("=" * 60)
            print(f"Error: {e}")
            print("\n💡 Troubleshooting:")
            print("  1. Check 'dap_manager.log' for detailed error info")
            print("  2. Verify all paths in 'config.json' are correct")
            print("  3. Ensure iPod is connected (for sync operations)")
            print("  4. Check environment variables (for Spotify features)")
            print("=" * 60)
            input("\nPress Enter to continue...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"\n❌ Fatal error: {e}")
        print("Check 'dap_manager.log' for details.")
