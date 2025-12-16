
"""
Download queue processing for DAP Manager.
"""

import os
import subprocess
import shutil
import logging
from typing import List
from .db_manager import DatabaseManager, DownloadItem, Track
from .library_scanner import LibraryScanner
from .utils import write_mbid_to_file

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
        slsk_username: str,
        slsk_password: str,
        slsk_config: dict = None,
    ):
        """
        Initializes the Downloader.
        """
        self.db = db
        self.scanner = scanner
        self.slsk_cmd_base = slsk_cmd_base
        self.downloads_dir = downloads_dir
        self.music_library_dir = music_library_dir
        self.slsk_username = slsk_username
        self.slsk_password = slsk_password
        self.slsk_config = slsk_config or {}

        # Ensure directories exist
        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(self.music_library_dir, exist_ok=True)

        logger.info(f"Downloader initialized")
        logger.info(f"  Downloads dir: {self.downloads_dir}")
        logger.info(f"  Music library: {self.music_library_dir}")

    def run_queue(self, progress_callback=None):
        """
        Fetches all 'pending' and 'failed' downloads and attempts to process them.
        :param progress_callback: func(str) -> None, called with status updates
        """
        def report(msg):
            logger.info(msg)
            if progress_callback:
                progress_callback({"message": msg})

        report("Starting download queue run...")

        pending_items = self.db.get_downloads(status="pending")
        failed_items = self.db.get_downloads(status="failed")

        queue = pending_items + failed_items

        if not queue:
            report("Download queue is empty")
            return

        report(
            f"Processing {len(queue)} items "
            f"({len(pending_items)} pending, {len(failed_items)} failed)"
        )

        success_count = 0
        fail_count = 0

        for i, item in enumerate(queue, 1):
            msg = f"[{i}/{len(queue)}] Processing: {item.search_query}"
            report(msg)

            try:
                # Pass a specialized callback for the item
                def item_callback(line):
                    if progress_callback:
                        progress_callback({
                            "message": msg,
                            "detail": line.strip()
                        })

                if not self._attempt_download(item, item_callback):
                    fail_count += 1
                    continue
                self._process_success(item)
                success_count += 1

            except subprocess.CalledProcessError as e:
                error_message = f"STDOUT: {e.stdout.strip()} | STDERR: {e.stderr.strip()}"
                logger.error(f"Download command failed: {error_message}")
                self._process_failure(item, error_message)
                fail_count += 1
            except subprocess.TimeoutExpired:
                logger.error(f"Download timed out: {item.search_query}")
                self._process_failure(item, "Timeout expired")
                fail_count += 1
            except FileNotFoundError:
                logger.error("FATAL: slsk-batchdl command not found")
                report("FATAL: slsk-batchdl command not found")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                self._process_failure(item, str(e))
                fail_count += 1

        report(f"Download queue finished. Success: {success_count}, Failed: {fail_count}")

    def _attempt_download(self, item: DownloadItem, item_callback=None):
        """
        Calls slsk-batchdl to download a single track or album.
        Streams output to item_callback if provided.
        """
        query = item.search_query
        is_album_mode = False

        if query.startswith("::ALBUM::"):
            is_album_mode = True
            query = query.replace("::ALBUM::", "").strip()
            logger.info(f"Detected Album Mode for: {query}")

        command = self.slsk_cmd_base + [
            "--user", self.slsk_username,
            "--pass", self.slsk_password,
            "--input", query,
            "-p", self.downloads_dir,
        ]

        if is_album_mode:
            command.append("--album")
            command.extend(["--skip-music-dir", self.music_library_dir])
        else:
            command.extend(["--format", "flac"])

        if self.slsk_config.get("fast_search"):
            command.append("--fast-search")
        if self.slsk_config.get("remove_ft"):
            command.append("--remove-ft")
        if self.slsk_config.get("desperate_mode"):
            command.append("--desperate")
        if self.slsk_config.get("strict_quality"):
            command.append("--strict-conditions")
            if "--pref-format" not in command: 
                 command.extend(["--pref-format", "flac,wav"])

        logger.debug(f"Executing: {' '.join(command)}")

        # Use Popen to stream output
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1, # Line buffered
                universal_newlines=True
            )
            
            # Read streaming output
            for line in process.stdout:
                line = line.strip()
                if line:
                    logger.debug(f"SLSK: {line}")
                    if item_callback:
                        item_callback(line)
            
            process.wait(timeout=300)
            
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, command, output="See logs")
                
            return True
            
        except subprocess.TimeoutExpired:
            process.kill()
            raise

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
        """Handles a successful download (Single Track or Album)"""
        logger.debug("Processing successful download...")
        
        # Check if Album Mode (based on query, though better to pass state)
        # We can check the query again or pass it. 
        # Simpler: Scan the download directory for potential candidates.
        # Since we use a fresh download dir or rely on timestamp, catching new files is tricky.
        # BUT: slsk-batchdl usually creates a folder for albums.
        
        # Strategy: Find ALL supported audio files in downloads_dir
        found_files = []
        for root, _, files in os.walk(self.downloads_dir):
            for file in files:
                if file.lower().endswith((".flac", ".mp3", ".m4a", ".wav", ".ogg")):
                    found_files.append(os.path.join(root, file))
        
        if not found_files:
            logger.warning(f"Download reported success but no audio files found in {self.downloads_dir}")
            # If skip-music-dir worked, maybe no files were downloaded?
            # In that case, we can assume success and clear queue.
            # But we should verify if files exist in library? 
            # For now, if no files found, we assume they were skipped or failed silently.
            # We'll trust the 'check=True' on subprocess but log warning.
            logger.info("Assuming files were skipped (existing in library). Marking done.")
            self.db.remove_from_queue(item.id)
            return

        is_album_mode = item.search_query.startswith("::ALBUM::")
        mbid_to_write = item.mbid_guess

        logger.info(f"Found {len(found_files)} files. Processing...")

        for file_path in found_files:
            try:
                # Write MBID (ONLY if single track mode)
                # In Album Mode, we can't apply one Track MBID to all files.
                if not is_album_mode and mbid_to_write:
                    logger.debug(f"Writing MBID {mbid_to_write} to {os.path.basename(file_path)}")
                    write_mbid_to_file(file_path, mbid_to_write)

                # Scanning (Read Tags)
                # This updates the DB with whatever tags exist
                try:
                    self.scanner._process_file(file_path)
                except Exception as e:
                    logger.error(f"Scanner failed for {file_path}: {e}")
                    continue

                # Move to Library
                # Get updated track info from DB (we need to find it by scanning result)
                # This is tricky because `_process_file` doesn't return the Track object explicitly,
                # but adds it to DB. We can query DB by path? No, path is temp.
                # We can query by fingerprint/MBID.
                # BETTER APPROACH: _process_file in scanner logic adds to DB.
                # We need to retrieve that track to generate the Sort Path.
                
                # We'll rely on the file's metadata we just scanned.
                import mutagen
                audio = mutagen.File(file_path)
                if not audio:
                    logger.warning(f"Could not read tags for moving: {file_path}")
                    continue
                
                # Extract basic info for path generation directly or query DB
                # Querying DB is safer if scanner did its job
                # We can't easily query DB without knowing what ID it got.
                # Let's use `_get_library_path_for_track` but construct a temporary Track object
                # from tags.
                
                # Hack: create a temporary Track object from tags
                s_artist = str(audio.get('artist', ['Unknown Artist'])[0])
                s_album = str(audio.get('album', ['Unknown Album'])[0])
                s_title = str(audio.get('title', ['Unknown Title'])[0])
                
                temp_track = Track(
                    id=0, mbid="", artist=s_artist, album=s_album, title=s_title,
                    filepath=file_path, duration=0, file_hash="", local_path=""
                )
                
                # Generate Sync Path
                dest_path = self._get_library_path_for_track(temp_track)
                dest_dir = os.path.dirname(dest_path)
                os.makedirs(dest_dir, exist_ok=True)
                
                # Move
                if os.path.exists(dest_path):
                     logger.info(f"File exists, overwriting: {dest_path}")
                     os.remove(dest_path)
                     
                shutil.move(file_path, dest_path)
                logger.debug(f"Moved to: {dest_path}")
                
                # Update DB with final path
                # We need to update the entry matched by scanner (which used hash/mbid)
                # This is hard to pinpoint perfectly without reloading scanner logic.
                # Simplified: Just run scanner on the DESTINATION file again?
                # Yes, rescanning the single final file is safest and robust.
                self.scanner._process_file(dest_path)

            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")

        # Cleanup: Remove any empty folders in download dir
        try:
            for root, dirs, files in os.walk(self.downloads_dir, topdown=False):
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except: pass
        except: pass

        # Done
        self.db.remove_from_queue(item.id)
        logger.info(f"Item {item.id} processing complete.")


def main_run_downloader(db: DatabaseManager, config: dict, progress_callback=None):
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
        slsk_username=config.get("slsk_username"),
        slsk_password=config.get("slsk_password"),
    )

    # Run the queue
    downloader.run_queue(progress_callback=progress_callback)


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
                slsk_username=config.get("slsk_username"),
                slsk_password=config.get("slsk_password"),
                slsk_config=config,  # Pass entire config dict
            )

            downloader.run_queue()

    except Exception as e:
        logger.error(f"Downloader error: {e}", exc_info=True)
