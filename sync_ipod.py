# ============================================================================
# ENHANCED sync_ipod.py with Library Sync Options
# ============================================================================
"""
Enhanced iPod synchronization with full library sync support.
"""

import os
import subprocess
import re
import logging
from typing import List, Optional, Set
from enum import Enum
from db_manager import DatabaseManager, Track
from library_scanner import LibraryScanner
from downloader import Downloader
import mutagen
from utils import get_mbid_from_tags

logger = logging.getLogger(__name__)


class SyncMode(Enum):
    """Sync mode options."""

    PLAYLISTS_ONLY = "playlists"  # Only sync tracks in playlists
    FULL_LIBRARY = "library"  # Sync entire library
    SELECTIVE = "selective"  # Sync specific tracks/artists


class ConversionOptions:
    """Audio conversion options for iPod sync."""

    def __init__(
        self,
        sample_rate: int = 44100,
        bit_depth: int = 16,
        format: str = "flac",
        quality: Optional[int] = None,
    ):
        """
        :param sample_rate: Target sample rate (44100, 48000, 96000, etc.)
        :param bit_depth: Target bit depth (16, 24)
        :param format: Output format (flac, mp3, opus)
        :param quality: Quality setting (for lossy formats, e.g., 320 for MP3)
        """
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.format = format
        self.quality = quality

    def get_ffmpeg_args(self) -> List[str]:
        """Generate ffmpeg arguments based on settings."""
        args = []

        if self.format == "flac":
            args.extend(["-c:a", "flac"])
            args.extend(["-sample_fmt", f"s{self.bit_depth}"])
            args.extend(["-ar", str(self.sample_rate)])

        elif self.format == "mp3":
            args.extend(["-c:a", "libmp3lame"])
            if self.quality:
                args.extend(["-b:a", f"{self.quality}k"])
            else:
                args.extend(["-q:a", "0"])  # VBR highest quality
            args.extend(["-ar", str(self.sample_rate)])

        elif self.format == "opus":
            args.extend(["-c:a", "libopus"])
            if self.quality:
                args.extend(["-b:a", f"{self.quality}k"])
            else:
                args.extend(["-b:a", "128k"])  # Default 128kbps
            args.extend(["-ar", str(self.sample_rate)])

        elif self.format == "aac":
            args.extend(["-c:a", "aac"])
            if self.quality:
                args.extend(["-b:a", f"{self.quality}k"])
            else:
                args.extend(["-q:a", "2"])  # High quality
            args.extend(["-ar", str(self.sample_rate)])

        return args

    def get_extension(self) -> str:
        """Get file extension for the output format."""
        return self.format


class EnhancedIpodSyncer:
    """
    Enhanced iPod syncer with full library sync and conversion options.
    """

    def __init__(
        self,
        db: DatabaseManager,
        downloader: Downloader,
        ffmpeg_path: str,
        ipod_mount: str,
        ipod_music_dir: str,
        ipod_playlist_dir: str,
        conversion_options: Optional[ConversionOptions] = None,
    ):

        self.db = db
        self.downloader = downloader
        self.ffmpeg_path = ffmpeg_path
        self.ipod_mount_point = ipod_mount
        self.conversion_options = conversion_options or ConversionOptions()

        # Absolute paths
        self.ipod_music_path = os.path.join(self.ipod_mount_point, ipod_music_dir)
        self.ipod_playlist_path = os.path.join(self.ipod_mount_point, ipod_playlist_dir)

        # Validation
        from shutil import which

        if not os.path.exists(self.ffmpeg_path) and not which(self.ffmpeg_path):
            raise FileNotFoundError(f"ffmpeg not found at '{self.ffmpeg_path}'")

        logger.info("EnhancedIpodSyncer initialized")
        logger.info(
            f"  Conversion: {self.conversion_options.format} "
            f"@ {self.conversion_options.sample_rate}Hz/{self.conversion_options.bit_depth}bit"
        )

    def _detect_ipod(self) -> bool:
        """Checks if the iPod mount point is accessible."""
        if not os.path.isdir(self.ipod_mount_point):
            logger.error(f"iPod mount point not found: {self.ipod_mount_point}")
            print("=" * 80)
            print(f"ERROR: iPod not detected at: {self.ipod_mount_point}")
            print("Please connect your iPod (in Rockbox USB mode).")
            print("=" * 80)
            return False

        logger.info(f"iPod detected at: {self.ipod_mount_point}")
        os.makedirs(self.ipod_music_path, exist_ok=True)
        os.makedirs(self.ipod_playlist_path, exist_ok=True)
        return True

    def _sanitize_path_component(self, name: str) -> str:
        """Removes illegal characters from a file/folder name."""
        if not name:
            name = "Unknown"
        return re.sub(r'[\\/*?:"<>|]', "_", name)

    def _run_downloader(self):
        """Runs the downloader to retry failed/pending downloads."""
        logger.info("--- Step 1: Running Download Queue ---")
        try:
            self.downloader.run_queue()
        except Exception as e:
            logger.warning(f"Download run failed: {e}")
        logger.info("--- Download Queue Finished ---\n")

    def _get_tracks_to_sync(
        self, mode: SyncMode, artist_filter: Optional[str] = None
    ) -> List[Track]:
        """
        Get tracks to sync based on mode.

        :param mode: SyncMode enum value
        :param artist_filter: Optional artist name filter for SELECTIVE mode
        :return: List of Track objects
        """
        if mode == SyncMode.PLAYLISTS_ONLY:
            # Original behavior - only tracks in playlists
            playlists = self.db.get_all_playlists()
            tracks_dict = {}

            for p in playlists:
                tracks = self.db.get_tracks_for_playlist(p.playlist_id)
                for t in tracks:
                    if t.local_path and not t.synced_to_ipod:
                        tracks_dict[t.mbid] = t

            return list(tracks_dict.values())

        elif mode == SyncMode.FULL_LIBRARY:
            # Sync entire library
            all_tracks = self.db.get_all_tracks(local_only=True)
            return [t for t in all_tracks if not t.synced_to_ipod]

        elif mode == SyncMode.SELECTIVE:
            # Sync specific artist or filtered tracks
            all_tracks = self.db.get_all_tracks(local_only=True)
            tracks = [t for t in all_tracks if not t.synced_to_ipod]

            if artist_filter:
                tracks = [
                    t for t in tracks if artist_filter.lower() in t.artist.lower()
                ]

            return tracks

        return []

    def _get_ipod_music_path(self) -> str:
        """Helper to safely get the music path, used by reconciliation and sync."""
        return self.ipod_music_path

    def reconcile_ipod_to_db(self):
        """
        Scans the iPod's music directory, reads MBIDs, and reconciles the
        dap_library.db by marking matched tracks as synced.
        """
        logger.info("--- Starting iPod Reconciliation ---")
        ipod_music_path = self._get_ipod_music_path()

        # Check for supported extensions (assuming defined in library_scanner.py)
        try:
            from library_scanner import SUPPORTED_EXTENSIONS
        except ImportError:
            # Fallback if constant isn't available
            SUPPORTED_EXTENSIONS = (".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav")
            logger.warning("Could not import SUPPORTED_EXTENSIONS, using fallback.")

        if not os.path.isdir(ipod_music_path):
            logger.error(f"iPod music path not found: {ipod_music_path}")
            print(
                f"ERROR: iPod not mounted or music directory missing: {ipod_music_path}"
            )
            return

        # Get a map of MBID -> local_path from the DB for efficient lookups
        mbid_map = self.db.get_mbid_to_track_path_map()
        if not mbid_map:
            logger.warning("Database has no tracks. Scan local library first.")
            print("WARNING: Database is empty. Scan your local music library first.")
            return

        match_count = 0
        for root, _, files in os.walk(ipod_music_path):
            for file in files:
                # Only look at supported audio files
                if not file.lower().endswith(SUPPORTED_EXTENSIONS):
                    continue

                ipod_file_path = os.path.join(root, file).replace("\\", "/")

                # Check file size to avoid trying to read metadata from tiny/corrupted files
                if os.path.getsize(ipod_file_path) < 1024:
                    logger.debug(f"Skipping tiny file: {file}")
                    continue

                ipod_mbid = get_mbid_from_tags(ipod_file_path)

                if ipod_mbid and ipod_mbid in mbid_map:
                    # Match found! This means we have the local file and the iPod file
                    # is tagged correctly with a known MBID.
                    self.db.mark_track_synced(ipod_mbid, ipod_file_path)
                    match_count += 1
                    logger.info(f"Matched and marked synced: {ipod_file_path}")

        logger.info(
            f"--- Reconciliation Complete. {match_count} tracks matched on iPod. ---"
        )
        print(
            f"\nSUCCESS: Reconciled {match_count} existing tracks from iPod into the database."
        )

    def _sync_tracks(
        self,
        mode: SyncMode = SyncMode.PLAYLISTS_ONLY,
        artist_filter: Optional[str] = None,
    ):
        """
        Syncs tracks based on the specified mode.

        :param mode: SyncMode enum (PLAYLISTS_ONLY, FULL_LIBRARY, or SELECTIVE)
        :param artist_filter: For SELECTIVE mode, filter by artist name
        """
        logger.info("--- Step 2: Syncing Tracks to iPod ---")
        logger.info(f"Sync mode: {mode.value}")

        tracks_to_sync = self._get_tracks_to_sync(mode, artist_filter)

        if not tracks_to_sync:
            logger.info("No tracks to sync")
            return

        logger.info(f"Found {len(tracks_to_sync)} track(s) to sync")

        # Ask for confirmation if syncing large number
        if len(tracks_to_sync) > 50:
            print(f"\n⚠️  About to sync {len(tracks_to_sync)} tracks.")
            confirm = input("This may take a while. Continue? (y/n): ").strip().lower()
            if confirm != "y":
                logger.info("Sync cancelled by user")
                return

        success_count = 0
        fail_count = 0

        for i, track in enumerate(tracks_to_sync, 1):
            logger.info(f"[{i}/{len(tracks_to_sync)}] {track.artist} - {track.title}")
            try:
                self._convert_and_copy(track)
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to sync: {e}")
                fail_count += 1

        logger.info(f"Sync complete. Success: {success_count}, Failed: {fail_count}")

    def _convert_and_copy(self, track: Track):
        """Converts and copies track to iPod using clean path structure"""

        if not track.local_path or not os.path.exists(track.local_path):
            logger.error(f"Local file not found: {track.local_path}")
            return

        # Use the clean iPod path structure
        safe_artist = (
            self._sanitize_path_component(track.artist)
            if track.artist
            else "Unknown Artist"
        )
        safe_title = (
            self._sanitize_path_component(track.title)
            if track.title
            else "Unknown Title"
        )
        safe_album = (
            self._sanitize_path_component(track.album)
            if track.album
            else "Unknown Album"
        )

        # Build clean iPod path: D:/Music/Artist/Album/Song.flac
        output_filename = f"{safe_title}.{self.conversion_options.get_extension()}"
        output_extension = self.conversion_options.get_extension()
        output_filename = f"{safe_title}.{output_extension}"
        output_path = os.path.join(
            self.ipod_music_path, safe_artist, safe_album, output_filename
        ).replace("\\", "/")

        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)

        ipod_mbid = get_mbid_from_tags(output_path)
        db_mbid = track.mbid
        if ipod_mbid and db_mbid:
            if ipod_mbid.strip().lower() == db_mbid.strip().lower():
                logger.debug(f"Skipping (MBID match): {output_filename}")
                self.db.mark_track_synced(track.mbid, output_path)
                return
            else:
                logger.debug(f"Overwriting (MBID mismatch): {output_filename}")
        else:
            logger.debug(f"Overwriting (untagged): {output_filename}")

        # Build ffmpeg command
        command = [
            self.ffmpeg_path,
            "-i",
            track.local_path,
        ]
        ffmpeg_args = self.conversion_options.get_ffmpeg_args()

        # Add FLAC compression if output is FLAC
        if self.conversion_options.get_extension().lower() == "flac":
            ffmpeg_args.extend(["-compression_level", "5"])

        command.extend(ffmpeg_args)
        command.extend(["-map_metadata", "0"])
        command.extend(["-y", output_path])

        logger.debug(f"Converting: {safe_artist} - {safe_title}")
        logger.debug(f"Output: {output_path}")

        # Run conversion
        try:
            result = subprocess.run(
                command, check=True, capture_output=True, text=True, encoding="utf-8"
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg failed: {e.stderr}")
            raise Exception(f"Conversion error: {e.stderr[:200]}")

        logger.debug(f"Saved: {output_path}")

        # Update database with clean iPod path
        self.db.mark_track_synced(track.mbid, output_path)

    def _generate_playlists(self):
        """Generates .m3u playlist files."""
        logger.info("--- Step 3: Generating M3U Playlists ---")

        playlists = self.db.get_all_playlists()
        if not playlists:
            logger.info("No playlists in database")
            return

        for p in playlists:
            sane_name = self._sanitize_path_component(p.name)
            m3u_path = os.path.join(self.ipod_playlist_path, f"{sane_name}.m3u")

            logger.info(f"Generating: {sane_name}.m3u")
            tracks = self.db.get_tracks_for_playlist(p.playlist_id)

            if not tracks:
                continue

            try:
                with open(m3u_path, "w", encoding="utf-8") as f:
                    f.write("#EXTM3U\n")

                    for track in tracks:
                        print(track)
                        if track.synced_to_ipod and track.ipod_path:
                            rel_path = os.path.relpath(
                                track.ipod_path, self.ipod_mount_point
                            )
                            m3u_entry = rel_path.replace(os.sep, "/")
                            f.write(f"{m3u_entry}\n")

            except Exception as e:
                logger.error(f"Failed to write {m3u_path}: {e}")

        logger.info("Playlist generation complete")

    def run_sync(
        self,
        mode: SyncMode = SyncMode.PLAYLISTS_ONLY,
        artist_filter: Optional[str] = None,
        skip_downloads: bool = False,
    ):
        """
        Main sync entry point.

        :param mode: Sync mode (PLAYLISTS_ONLY, FULL_LIBRARY, or SELECTIVE)
        :param artist_filter: For SELECTIVE mode
        :param skip_downloads: Skip the download queue step
        """
        logger.info("=" * 50)
        logger.info("Starting iPod Sync")
        logger.info("=" * 50)

        if not self._detect_ipod():
            return

        if not skip_downloads:
            self._run_downloader()

        self._sync_tracks(mode, artist_filter)
        self._generate_playlists()

        logger.info("=" * 50)
        logger.info("Sync Complete!")
        logger.info("=" * 50)

    def get_sync_stats(self) -> dict:
        """Get statistics about what needs syncing."""
        stats = {
            "total_tracks": len(self.db.get_all_tracks(local_only=True)),
            "synced_tracks": len(
                [t for t in self.db.get_all_tracks(local_only=True) if t.synced_to_ipod]
            ),
            "pending_tracks": len(
                [
                    t
                    for t in self.db.get_all_tracks(local_only=True)
                    if not t.synced_to_ipod
                ]
            ),
            "total_playlists": len(self.db.get_all_playlists()),
        }
        stats["sync_percentage"] = (
            stats["synced_tracks"] / stats["total_tracks"] * 100
            if stats["total_tracks"] > 0
            else 0
        )
        return stats


def main_run_sync(
    db: DatabaseManager,
    config: dict,
    sync_mode: str = "playlists",
    conversion_format: str = "flac",
):
    """
    Enhanced main entry point with sync options.

    :param sync_mode: "playlists", "library", or "selective"
    :param conversion_format: "flac", "mp3", "opus", "aac"
    """
    # Extract configuration
    slsk_cmd_base = config.get("slsk_cmd_base", [])
    downloads_path = config.get("downloads_path")
    music_library_path = config.get("music_library_path")
    picard_cmd_path = config.get("picard_cmd_path")
    ffmpeg_path = config.get("ffmpeg_path")
    ipod_mount = config.get("ipod_mount_point")
    ipod_music_dir = config.get("ipod_music_dir_name", "Music")
    ipod_playlist_dir = config.get("ipod_playlist_dir_name", "Playlists")

    # Get conversion settings from config
    sample_rate = config.get("conversion_sample_rate", 44100)
    bit_depth = config.get("conversion_bit_depth", 16)
    quality = config.get("conversion_quality")  # For lossy formats

    # Create conversion options
    conversion_opts = ConversionOptions(
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        format=conversion_format,
        quality=quality,
    )

    # Initialize components
    from library_scanner import LibraryScanner

    scanner = LibraryScanner(db, picard_cmd_path)
    downloader = Downloader(
        db=db,
        scanner=scanner,
        slsk_cmd_base=slsk_cmd_base,
        downloads_dir=downloads_path,
        music_library_dir=music_library_path,
    )

    syncer = EnhancedIpodSyncer(
        db=db,
        downloader=downloader,
        ffmpeg_path=ffmpeg_path,
        ipod_mount=ipod_mount,
        ipod_music_dir=ipod_music_dir,
        ipod_playlist_dir=ipod_playlist_dir,
        conversion_options=conversion_opts,
    )

    # Show stats before sync
    stats = syncer.get_sync_stats()
    logger.info(
        f"Library stats: {stats['total_tracks']} tracks total, "
        f"{stats['pending_tracks']} pending sync "
        f"({stats['sync_percentage']:.1f}% synced)"
    )

    # Convert sync mode string to enum
    mode_map = {
        "playlists": SyncMode.PLAYLISTS_ONLY,
        "library": SyncMode.FULL_LIBRARY,
        "selective": SyncMode.SELECTIVE,
    }
    mode = mode_map.get(sync_mode, SyncMode.PLAYLISTS_ONLY)

    # Run sync
    artist_filter = None
    if mode == SyncMode.SELECTIVE:
        artist_filter = input(
            "Enter artist name to filter (or press Enter for all): "
        ).strip()
        if not artist_filter:
            artist_filter = None

    syncer.run_sync(mode=mode, artist_filter=artist_filter)
