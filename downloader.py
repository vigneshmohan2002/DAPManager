# ============================================================================
# FILE: downloader.py
# ============================================================================
"""
Download queue processing for DAP Manager.
"""

import os
import subprocess
import shutil
import logging
from typing import List
from db_manager import DatabaseManager, DownloadItem, Track
from library_scanner import LibraryScanner
import mutagen

logger = logging.getLogger(__name__)


class Downloader:
    """
    Processes the download_queue, uses slsk-batchdl to download files,
    and hands them off to the LibraryScanner for tagging and DB update.
    """

    def __init__(
        self,
        db: DatabaseManager,
        scanner: LibraryScanner,
        slsk_cmd_base: List[str],
        downloads_dir: str,
        music_library_dir: str,
    ):
        """
        Initializes the Downloader.
        """
        self.db = db
        self.scanner = scanner
        self.slsk_cmd_base = slsk_cmd_base
        self.downloads_dir = downloads_dir
        self.music_library_dir = music_library_dir

        # Ensure directories exist
        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(self.music_library_dir, exist_ok=True)

        logger.info(f"Downloader initialized")
        logger.info(f"  Downloads dir: {self.downloads_dir}")
        logger.info(f"  Music library: {self.music_library_dir}")

    def run_queue(self):
        """
        Fetches all 'pending' and 'failed' downloads and attempts to process them.
        """
        logger.info("Starting download queue run...")

        pending_items = self.db.get_downloads(status="pending")
        failed_items = self.db.get_downloads(status="failed")

        queue = pending_items + failed_items

        if not queue:
            logger.info("Download queue is empty")
            return

        logger.info(
            f"Processing {len(queue)} items "
            f"({len(pending_items)} pending, {len(failed_items)} failed)"
        )

        success_count = 0
        fail_count = 0

        for item in queue:
            logger.info(f"Processing: {item.search_query}")

            try:
                self._attempt_download(item)
                self._process_success(item)
                success_count += 1

            except subprocess.CalledProcessError as e:
                logger.error(f"Download command failed: {e.stderr}")
                self._process_failure(item, str(e.stderr))
                fail_count += 1
            except subprocess.TimeoutExpired:
                logger.error(f"Download timed out: {item.search_query}")
                self._process_failure(item, "Timeout expired")
                fail_count += 1
            except FileNotFoundError:
                logger.error("FATAL: slsk-batchdl command not found")
                logger.error(f"Check your SLSK_CMD_BASE: {self.slsk_cmd_base}")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                self._process_failure(item, str(e))
                fail_count += 1

        logger.info(
            f"Download queue finished. Success: {success_count}, Failed: {fail_count}"
        )

    def _attempt_download(self, item: DownloadItem):
        """
        Calls slsk-batchdl to download a single track.
        Raises error on failure.
        """
        command = self.slsk_cmd_base + [
            "--user",
            "TheTediousDrunk",
            "--pass",
            "UPcqvAzKMMl6f6bi4v0VK",
            "download",
            item.search_query,
            "--format",
            "flac",
            "--output-dir",
            self.downloads_dir,
        ]

        logger.debug(f"Executing: {' '.join(command)}")

        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
            encoding="utf-8",
        )
        logger.debug("Download command successful")

    def _process_failure(self, item: DownloadItem, error_msg: str):
        """Updates the database for a failed download attempt."""
        self.db.update_download_status(item.id, "failed")
        logger.info(f"Marked as 'failed' in database")

    def _get_ipod_path_for_track(self, track: Track) -> str:
        """Generate clean iPod path: D:/Music/Artist/Album/Song.flac"""
        import re

        # Get album from tags if available, otherwise use playlist name
        try:
            file_tags = mutagen.File(track.local_path, easy=True)
            album = file_tags.get("album", ["Unknown Album"])[0]
        except:
            album = "Unknown Album"

        # Sanitize names
        safe_artist = (
            re.sub(r'[\\/*?:"<>|]', "_", track.artist)
            if track.artist
            else "Unknown Artist"
        )
        safe_album = re.sub(r'[\\/*?:"<>|]', "_", album)
        safe_title = (
            re.sub(r'[\\/*?:"<>|]', "_", track.title)
            if track.title
            else "Unknown Title"
        )

        # Build iPod path
        ipod_path = os.path.join(
            self.music_library_dir,  # This should be D:/Music
            safe_artist,
            safe_album,
            f"{safe_title}.flac",
        )

        return ipod_path.replace("\\", "/")

    def _process_success(self, item: DownloadItem):
        """Handles a successful download with clean iPod paths"""
        logger.debug("Processing successful download...")

        # Find the downloaded .flac file
        found_file_path = None
        for root, _, files in os.walk(self.downloads_dir):
            for file in files:
                if file.lower().endswith(".flac"):
                    found_file_path = os.path.join(root, file)
                    break
            if found_file_path:
                break

        if not found_file_path:
            raise Exception(
                f"Download succeeded but no .flac file found in '{self.downloads_dir}'"
            )

        logger.debug(f"Found file: {os.path.basename(found_file_path)}")

        # Create track object for path generation
        temp_track = Track(
            mbid=item.mbid_guess,
            title="Unknown Title",  # Will be updated by scanner
            artist="Unknown Artist",
            local_path=found_file_path,
        )

        # Generate clean iPod destination path
        dest_path = self._get_ipod_path_for_track(temp_track)
        dest_dir = os.path.dirname(dest_path)
        os.makedirs(dest_dir, exist_ok=True)

        # Move to music library with clean structure
        try:
            shutil.move(found_file_path, dest_path)
            logger.debug(f"Moved to: {dest_path}")
        except Exception as e:
            raise Exception(f"Failed to move file: {e}")

        # Process with LibraryScanner (this will update tags)
        logger.debug("Handing off to LibraryScanner...")
        try:
            self.scanner._process_file(dest_path)

            # Update the track with the correct iPod path after scanning
            updated_track = self.db.get_track_by_mbid(item.mbid_guess)
            if updated_track and updated_track.local_path == dest_path:
                # Generate final iPod path with correct metadata
                final_ipod_path = self._get_ipod_path_for_track(updated_track)
                updated_track.ipod_path = final_ipod_path
                self.db.add_or_update_track(updated_track)

        except Exception as e:
            raise Exception(f"LibraryScanner failed: {e}")

        # Remove from queue
        self.db.remove_from_queue(item.id)
        logger.info("Successfully processed and removed from queue")


def main_run_downloader(db: DatabaseManager, config: dict):
    """
    Main entry point for running the downloader from manager.py
    """
    slsk_cmd_base = config.get("slsk_cmd_base", [])
    downloads_path = config.get("downloads_path")
    music_library_path = config.get("music_library_path")
    picard_cmd_path = config.get("picard_cmd_path")

    # Validation
    if not slsk_cmd_base or not downloads_path or not music_library_path:
        logger.error("Downloader configuration incomplete in config.json")
        return

    # Initialize components
    scanner = LibraryScanner(db, picard_cmd_path)
    downloader = Downloader(
        db=db,
        scanner=scanner,
        slsk_cmd_base=slsk_cmd_base,
        downloads_dir=downloads_path,
        music_library_dir=music_library_path,
    )

    # Run the queue
    downloader.run_queue()


if __name__ == "__main__":
    from logger_setup import setup_logging
    from config_manager import get_config

    setup_logging()
    config = get_config()

    try:
        with DatabaseManager(config.db_path) as db:
            scanner = LibraryScanner(db, config.picard_path)

            downloader = Downloader(
                db=db,
                scanner=scanner,
                slsk_cmd_base=config.slsk_command,
                downloads_dir=config.downloads_dir,
                music_library_dir=config.music_library,
            )

            downloader.run_queue()

    except Exception as e:
        logger.error(f"Downloader error: {e}", exc_info=True)
