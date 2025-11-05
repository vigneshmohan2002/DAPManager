# ============================================================================
# FILE: downloader.py (UPDATED VERSION)
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

# import mutagen # No longer needed, Picard lib handles tagging

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
                if not self._attempt_download(item):
                    fail_count += 1
                    continue
                self._process_success(item)
                success_count += 1

            except subprocess.CalledProcessError as e:
                # Log both stdout and stderr to capture the real error
                error_message = (
                    f"STDOUT: {e.stdout.strip()} | STDERR: {e.stderr.strip()}"
                )
                logger.error(f"Download command failed: {error_message}")
                self._process_failure(item, error_message)
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
            "vigmoh20022222",
            "--pass",
            "UPcqvAzKMMl6f6bi4v0VK",
            item.search_query,
            "--format",
            "flac",
            "-p",
            self.downloads_dir,
        ]

        logger.debug(f"Executing: {' '.join(command)}")

        output = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5-minute timeout
            encoding="utf-8",
        )
        if "not found" in str(output).lower():
            logger.debug("Did not find track")
            return False
        logger.debug("Download command successful")
        return True

    def _process_failure(self, item: DownloadItem, error_msg: str):
        """Updates the database for a failed download attempt."""
        self.db.update_download_status(item.id, "failed")
        logger.info(f"Marked as 'failed' in database")

    def _get_library_path_for_track(self, track: Track) -> str:
        """Generate clean library path: D:/Music/Artist/Album/Song.flac"""
        import re

        # Get album from the track object (populated by Spotify or Scanner)
        album = track.album or "Unknown Album"

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

        # Build library path
        library_path = os.path.join(
            self.music_library_dir,  # This should be D:/Music
            safe_artist,
            safe_album,
            f"{safe_title}.flac",
        )

        return library_path.replace("\\", "/")

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
            logger.warning(f"Did not find song")
            raise Exception("Downloaded .flac file not found in downloads directory")

        logger.debug(f"Found file: {os.path.basename(found_file_path)}")

        # === NEW TAGGING STEP ===
        # Write the MBID (from Spotify/DB) to the file *before* moving it.
        mbid_to_write = item.mbid_guess
        logger.debug(f"Writing MBID {mbid_to_write} to {found_file_path}...")

        tag_success = self.scanner.write_mbid_to_file(found_file_path, mbid_to_write)
        if not tag_success:
            # Don't fail the whole download, but log a critical warning
            logger.critical(f"CRITICAL: Failed to write tags to {found_file_path}")
            # Raise exception to stop processing this file
            raise Exception(f"Failed to write tags to file: {found_file_path}")

        # === END NEW TAGGING STEP ===

        # Process with LibraryScanner (this will READ tags and update database)
        logger.debug("Handing off to LibraryScanner to read tags and update DB...")
        try:
            # Scan the file IN-PLACE in the downloads folder.
            # This now reads the MBID we just wrote, plus all other tags.
            self.scanner._process_file(found_file_path)
        except Exception as e:
            # If scanner fails, we can't proceed
            raise Exception(f"LibraryScanner._process_file failed: {e}")

        # Get the full track data from the DB (now populated by scanner)
        updated_track = self.db.get_track_by_mbid(item.mbid_guess)
        if not updated_track:
            raise Exception(f"Scanner ran but track {item.mbid_guess} not found in DB")

        # Now, generate the FINAL library path using the correct metadata
        # (including the album name read by the scanner)
        dest_path = self._get_library_path_for_track(updated_track)
        dest_dir = os.path.dirname(dest_path)
        os.makedirs(dest_dir, exist_ok=True)

        # Move to final music library destination
        try:
            shutil.move(found_file_path, dest_path)
            logger.debug(f"Moved to: {dest_path}")
        except Exception as e:
            raise Exception(f"Failed to move file: {e}")

        # CRITICAL: Update the track's local_path in the DB to its new location
        updated_track.local_path = dest_path
        self.db.add_or_update_track(updated_track)

        # Remove from queue
        self.db.remove_from_queue(item.id)
        logger.info("Successfully processed, added to library, and removed from queue")


def main_run_downloader(db: DatabaseManager, config: dict):
    """
    Main entry point for running the downloader from manager.py
    """
    slsk_cmd_base = config.get("slsk_cmd_base", [])
    downloads_path = config.get("downloads_path")
    music_library_path = config.get("music_library_path")
    # picard_cmd_path = config.get("picard_cmd_path") # No longer needed

    # Validation
    if not slsk_cmd_base or not downloads_path or not music_library_path:
        logger.error("Downloader configuration incomplete in config.json")
        return

    # Initialize components
    scanner = LibraryScanner(db)  # No longer needs picard_path
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
            scanner = LibraryScanner(db)  # No longer needs picard_path

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
