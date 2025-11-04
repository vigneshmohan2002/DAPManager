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
from typing import Optional
from db_manager import DatabaseManager, Track
from config_manager import get_config

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

        # Validate Picard path
        if not os.path.exists(self.picard_path):
            logger.error(f"Picard not found at: {self.picard_path}")
            raise FileNotFoundError(f"Picard not found at: {self.picard_path}")

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
        mbid = self._get_mbid_from_tags(file_path)

        # If no MBID, run Picard tagger
        if not mbid:
            logger.info(f"Tagging (no MBID): {os.path.basename(file_path)}")
            success = self._run_picard_tagger(file_path)

            if not success:
                logger.warning(f"Picard failed for: {os.path.basename(file_path)}")
                return "skipped"

            # Re-read the MBID after tagging
            mbid = self._get_mbid_from_tags(file_path)

        # If we have an MBID, add to database
        if mbid:
            file_tags = mutagen.File(file_path, easy=True)
            if not file_tags:
                logger.error(f"Could not read tags from: {os.path.basename(file_path)}")
                return "skipped"

            title = file_tags.get("title", ["Unknown Title"])[0]
            artist = file_tags.get("artist", ["Unknown Artist"])[0]

            track_data = Track(
                mbid=mbid, title=title, artist=artist, local_path=file_path
            )

            # Check if this MBID is already known
            existing_track = self.db.get_track_by_mbid(mbid)
            if existing_track and existing_track.local_path:
                logger.warning(
                    f"Duplicate MBID {mbid}: {existing_track.local_path} and {file_path}"
                )
                return "skipped"

            self.db.add_or_update_track(track_data)
            logger.info(f"Added: {artist} - {title} [{mbid}]")
            return "processed"
        else:
            logger.warning(f"No MBID found for: {os.path.basename(file_path)}")
            return "skipped"

    def _get_mbid_from_tags(self, file_path: str) -> Optional[str]:
        """
        Reads the MusicBrainz Track ID from a file's metadata.
        Handles both ID3 (MP3) and Vorbis (FLAC/Ogg) tags.
        """
        try:
            file_info = mutagen.File(file_path, easy=False)
            if not file_info:
                return None

            if file_info.tags and "musicbrainztrackid" in file_info.tags:
                return file_info.tags["musicbrainztrackid"][0]

            # Check for ID3 tags (MP3, M4A)
            if "TXXX:MusicBrainz Track Id" in file_info.tags:
                return str(file_info.tags["TXXX:MusicBrainz Track Id"])

            # Fallback for 'easy' tags
            easy_tags = mutagen.File(file_path, easy=True)
            if easy_tags and "musicbrainz_trackid" in easy_tags:
                return easy_tags["musicbrainz_trackid"][0]

        except Exception as e:
            logger.debug(f"Error reading tags from {os.path.basename(file_path)}: {e}")

        return None

    def _run_picard_tagger(self, file_path: str) -> bool:
        """
        Calls the Picard command-line tool to tag a single file.
        :return: True on success, False on failure.
        """
        cmd = [self.picard_path, "--save", file_path]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
                encoding="utf-8",
            )
            logger.debug(f"Picard output: {result.stdout.strip()}")
            return True

        except subprocess.CalledProcessError as e:
            logger.warning(f"Picard failed for {file_path}: {e.stderr}")
        except FileNotFoundError:
            logger.error(f"Picard executable not found at: {self.picard_path}")
            raise
        except subprocess.TimeoutExpired:
            logger.warning(f"Picard timed out for {file_path}")
        except Exception as e:
            logger.error(f"Unknown error running Picard: {e}")

        return False


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
