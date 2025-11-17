# ============================================================================
# FILE: library_scanner.py
# ============================================================================
"""
Library scanning and tagging functionality for DAP Manager.
"""

import os
import subprocess
import logging
import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
import os
from typing import Optional
from db_manager import DatabaseManager, Track
from config_manager import get_config
from utils import get_mbid_from_tags, find_mbid_by_fingerprint, write_mbid_to_file
import time
import picard

logger = logging.getLogger(__name__)

# Supported file extensions
SUPPORTED_EXTENSIONS = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav")


class LibraryScanner:
    """
    Scans the local music library, uses Picard to tag untagged files,
    and populates the database with track information.
    """

    def __init__(self, db: DatabaseManager, picard_path: Optional[str] = None):
        """
        Initializes the scanner with a database manager instance.
        :param db: An initialized DatabaseManager object.
        :param picard_path: Optional override for Picard executable path.
        """
        self.db = db

        # Get Picard path from config or override
        if picard_path:
            self.picard_path = picard_path
        else:
            config = get_config()
            self.picard_path = config.picard_path

        logger.info(f"LibraryScanner initialized with Picard at: {self.picard_path}")

    def scan_library(self, library_path: str):
        """
        Recursively scans the given library path for music files.
        :param library_path: The absolute path to the music library.
        """
        logger.info(f"Starting library scan at: {library_path}")

        if not os.path.exists(library_path):
            logger.error(f"Library path does not exist: {library_path}")
            return

        file_count = 0
        processed_count = 0
        skipped_count = 0
        error_count = 0

        for root, _, files in os.walk(library_path):
            for file in files:
                if not file.lower().endswith(SUPPORTED_EXTENSIONS):
                    continue

                file_path = os.path.join(root, file)
                file_count += 1

                try:
                    result = self._process_file(file_path)
                    if result == "processed":
                        processed_count += 1
                    elif result == "skipped":
                        skipped_count += 1
                except Exception as e:
                    logger.error(f"Failed to process {file_path}: {e}")
                    error_count += 1

        logger.info(
            f"Scan complete. Found {file_count} files. "
            f"Processed: {processed_count}, Skipped: {skipped_count}, Errors: {error_count}"
        )

    def _process_file(self, file_path: str) -> str:
        """
        Processes a single music file.
        Returns: "processed", "skipped", or raises exception
        """

        # Check if file is already in the database
        if self.db.get_track_by_path(file_path):
            logger.debug(f"Skipping (already in DB): {os.path.basename(file_path)}")
            return "skipped"

        # Read existing MBID
        mbid = get_mbid_from_tags(file_path)

        # If no MBID, run Picard tagger
        if not mbid:
            logger.info(f"Tagging (no MBID): {os.path.basename(file_path)}")
            success = self._run_picard_tagger(file_path)

            if not success:
                logger.warning(f"Picard failed for: {os.path.basename(file_path)}")
                return "skipped"

            # Re-read the MBID after tagging
            mbid = get_mbid_from_tags(file_path)

        # If we have an MBID, add to database
        if mbid:
            file_tags = mutagen.File(file_path, easy=True)
            if not file_tags:
                logger.error(f"Could not read tags from: {os.path.basename(file_path)}")
                return "skipped"

            title = file_tags.get("title", ["Unknown Title"])[0]
            artist = file_tags.get("artist", ["Unknown Artist"])[0]
            album = file_tags.get("album", ["Unknown Album"])[0]

            track_data = Track(
                mbid=mbid, title=title, artist=artist, album=album, local_path=file_path
            )

            # Check for duplicates
            existing_track = self.db.get_track_by_mbid(mbid)
            if existing_track and existing_track.local_path:
                # --- DUPLICATE LOGIC ---
                # A file with this MBID already exists at a different path.
                # Log both files to the duplicates table for later review.
                logger.warning(
                    f"Duplicate MBID {mbid} found. Logging conflict:"
                    f"\n  Existing: {existing_track.local_path}"
                    f"\n  New:      {file_path}"
                )
                
                # Log both the file we just found AND the one already in the DB
                self.db.log_duplicate(mbid, existing_track.local_path)
                self.db.log_duplicate(mbid, file_path)
                
                return "skipped"
                # --- END DUPLICATE LOGIC ---

            # 6. No duplicate, add the new track
            self.db.add_or_update_track(track_data)
            logger.debug(f"Added: {artist} - {title} [{mbid}]")
            return "processed"
        else:
            logger.warning(f"No MBID found for: {os.path.basename(file_path)}")
            return "skipped"

    def _run_picard_tagger(self, file_path: str) -> bool:
        """
        Calls the Picard command-line tool to tag a single file.
        :return: True on success, False on failure.
        """
        return find_mbid_by_fingerprint(file_path=file_path)

    def write_mbid_to_file(self, file_path: str, mbid: str) -> bool:
        """
        Writes the MusicBrainz Track ID to a file.
        :param file_path: Path to the audio file.
        :param mbid: The MusicBrainz Track ID to write.
        :return: True on success, False on failure.
        """
        return write_mbid_to_file(file_path=file_path, mbid=mbid)


def main_scan_library(db: DatabaseManager, config: dict):
    """
    Main entry point for library scanning when called from manager.py
    """
    music_library_path = config.get("music_library_path")
    picard_path = config.get("picard_cmd_path")

    if not music_library_path or not os.path.exists(music_library_path):
        logger.error(f"Music library path not found: {music_library_path}")
        return

    scanner = LibraryScanner(db, picard_path)
    scanner.scan_library(music_library_path)


if __name__ == "__main__":
    from logger_setup import setup_logging

    setup_logging()

    config = get_config()

    try:
        with DatabaseManager(config.db_path) as db:
            scanner = LibraryScanner(db)
            scanner.scan_library(config.music_library)
    except Exception as e:
        logger.error(f"Scanner error: {e}", exc_info=True)
