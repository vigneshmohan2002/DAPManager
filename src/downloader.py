
"""
Download queue processing for DAP Manager.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

from .db_manager import DatabaseManager, DownloadItem, Track
from .library_scanner import LibraryScanner
from .utils import write_mbid_to_file
from .audio_quality import library_path_for_track
from .file_ingest import file_has_embedded_mbid, ingest_downloaded_audio_file
from . import tag_service
from .lidarr_client import LidarrClient, LidarrError
from .jellyfin_client import JellyfinClient
from .config_manager import is_authority_config

logger = logging.getLogger(__name__)

DOWNLOADED_AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".wav", ".ogg")


@dataclass(frozen=True)
class DownloadedMetadata:
    artist: str
    album: str
    title: str
    track_number: int


def parse_download_query(search_query: str) -> Tuple[str, bool]:
    """Return the sldl query and whether its album-mode marker was present."""
    if not search_query.startswith("::ALBUM::"):
        return search_query, False
    return search_query.replace("::ALBUM::", "").strip(), True


def build_download_command(
    command_base: List[str],
    username: str,
    password: str,
    search_query: str,
    downloads_dir: str,
    music_library_dir: str,
    config: Mapping[str, Any],
) -> Tuple[List[str], bool]:
    """Build the exact sldl command previously assembled in the controller."""
    query, is_album_mode = parse_download_query(search_query)
    command = list(command_base) + [
        "--user",
        username,
        "--pass",
        password,
        "--input",
        query,
        "-p",
        downloads_dir,
    ]

    if is_album_mode:
        command.append("--album")
        command.extend(["--skip-music-dir", music_library_dir])
    else:
        command.extend(["--format", "flac"])

    if config.get("fast_search"):
        command.append("--fast-search")
    if config.get("remove_ft"):
        command.append("--remove-ft")
    if config.get("desperate_mode"):
        command.append("--desperate")
    if config.get("strict_quality"):
        command.append("--strict-conditions")
        if "--pref-format" not in command:
            command.extend(["--pref-format", "flac,wav"])

    return command, is_album_mode


def discover_downloaded_audio(downloads_dir: str) -> List[str]:
    """Return supported audio files in the same ``os.walk`` order as before."""
    return [
        os.path.join(root, filename)
        for root, _, files in os.walk(downloads_dir)
        for filename in files
        if filename.lower().endswith(DOWNLOADED_AUDIO_EXTENSIONS)
    ]


def cleanup_empty_download_directories(downloads_dir: str) -> None:
    """Best-effort removal of empty subdirectories after an item finishes."""
    try:
        for root, dirs, _ in os.walk(downloads_dir, topdown=False):
            for name in dirs:
                try:
                    os.rmdir(os.path.join(root, name))
                except OSError:
                    pass
    except OSError as e:
        logger.debug(f"Empty-dir cleanup failed: {e}")


def read_downloaded_metadata(
    file_path: str, tagged_meta: Optional[Mapping[str, Any]]
) -> Optional[DownloadedMetadata]:
    """Read the sort-path identity while retaining legacy tag precedence."""
    import mutagen

    audio = mutagen.File(file_path)
    if not audio:
        return None

    if tagged_meta:
        artist = str(tagged_meta["artist"])
        album = str(tagged_meta["album"])
        title = str(tagged_meta["title"])
        track_number = tagged_meta.get("track_number", 0)
    else:
        artist = str(audio.get("artist", ["Unknown Artist"])[0])
        album = str(audio.get("album", ["Unknown Album"])[0])
        title = str(audio.get("title", ["Unknown Title"])[0])
        track_number = audio["tracknumber"][0] if "tracknumber" in audio else 0

    return DownloadedMetadata(
        artist=artist,
        album=album,
        title=title,
        track_number=int(track_number) if track_number else 0,
    )


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
        lidarr_client: Optional["LidarrClient"] = None,
        lidarr_quality_profile_id: Optional[int] = None,
        lidarr_root_folder_path: Optional[str] = None,
        jellyfin_client: Optional["JellyfinClient"] = None,
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

        self.acoustid_api_key = self.slsk_config.get("acoustid_api_key", "") or ""
        self.contact_email = self.slsk_config.get("contact_email", "") or ""

        self.lidarr = lidarr_client
        self.lidarr_quality_profile_id = lidarr_quality_profile_id
        self.lidarr_root_folder_path = lidarr_root_folder_path
        self.jellyfin_client = jellyfin_client

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

                if self._try_lidarr_album(item, report):
                    # Lidarr owns this one now; library scanner will pick
                    # up the imported file on its next pass.
                    self.db.remove_from_queue(item.id)
                    success_count += 1
                    continue

                if not self._attempt_download(item, item_callback):
                    fail_count += 1
                    continue
                self._process_success(item)
                success_count += 1

            except subprocess.CalledProcessError as e:
                error_message = f"STDOUT: {(e.stdout or '').strip()} | STDERR: {(e.stderr or '').strip()}"
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

        if success_count > 0 and self.jellyfin_client:
            report("Triggering Jellyfin library scan...")
            self.jellyfin_client.trigger_library_scan()

    def _attempt_download(self, item: DownloadItem, item_callback=None):
        """
        Calls slsk-batchdl to download a single track or album.
        Streams output to item_callback if provided.
        """
        command, is_album_mode = build_download_command(
            self.slsk_cmd_base,
            self.slsk_username,
            self.slsk_password,
            item.search_query,
            self.downloads_dir,
            self.music_library_dir,
            self.slsk_config,
        )
        if is_album_mode:
            query, _ = parse_download_query(item.search_query)
            logger.info(f"Detected Album Mode for: {query}")

        logger.debug(f"Executing: {' '.join(command)}")

        # Use Popen to stream output
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,  # Line buffered
                universal_newlines=True,
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
                raise subprocess.CalledProcessError(
                    process.returncode, command, output="See logs"
                )

            return True

        except subprocess.TimeoutExpired:
            process.kill()
            raise

    def _try_lidarr_album(self, item: DownloadItem, report) -> bool:
        """Hand album-mode downloads off to Lidarr when it's configured.

        Only fires on the master — satellites never get a Lidarr client
        in the first place. Returns True if Lidarr accepted the release
        and is now monitoring + searching for it; the caller should then
        clear the queue entry and move on. Returns False for anything we
        don't want Lidarr to try (single-track items, items with no
        release MBID, Lidarr not configured), so sldl stays the fallback.
        """
        if not self.lidarr:
            return False
        if not self.lidarr_quality_profile_id or not self.lidarr_root_folder_path:
            return False
        if not item.search_query.startswith("::ALBUM::"):
            return False
        release_mbid = (item.mbid_guess or "").strip()
        if not release_mbid:
            return False

        try:
            result = self.lidarr.ensure_album_monitored(
                release_mbid,
                quality_profile_id=self.lidarr_quality_profile_id,
                root_folder_path=self.lidarr_root_folder_path,
            )
        except LidarrError as e:
            logger.warning("Lidarr handoff failed for %s: %s", release_mbid, e)
            return False

        if not result:
            return False

        report(
            f"Handed off to Lidarr: {release_mbid} "
            f"(album id={result.get('id')}) — upgrade monitor will chase FLAC"
        )
        return True

    def _auto_tag_file(self, file_path: str):
        """Picard-style auto-tag for a freshly-downloaded file.

        Returns ``(meta, tier, score)`` where ``meta`` is the candidate
        metadata dict (used downstream for the library sort path) or
        None, ``tier`` is "green"/"yellow"/"red"/None, and ``score`` is
        the AcoustID match score or None.

        Policy:
        * If the file already carries an MBID in its tags, trust it and
          return tier="green" with no metadata dict — caller should fall
          back to the file's on-disk tags for the sort path. No AcoustID
          call.
        * Otherwise fingerprint + lookup via ``tag_service``.
          - green: apply tags, return tier="green".
          - yellow/red: do NOT write tags; return tier so caller can flag
            the track for manual review. Yellow is "probably right but
            not confident" and still requires user confirmation.
        * Any failure (no API key, API error, unreadable file) returns
          ``(None, None, None)`` — the track proceeds with whatever tags
          the downloader source provided.
        """
        if self._file_has_embedded_mbid(file_path):
            logger.info(
                "Embedded MBID found, treating as green: %s",
                os.path.basename(file_path),
            )
            return None, "green", None

        if not self.acoustid_api_key:
            logger.debug("No AcoustID key configured; skipping auto-tag.")
            return None, None, None

        try:
            candidate = tag_service.identify_file(
                file_path, self.acoustid_api_key, self.contact_email
            )
        except Exception as e:
            logger.error(f"Auto-tag identify failed: {e}")
            return None, None, None

        if not candidate:
            logger.warning(
                "No AcoustID match for %s — flagging red.",
                os.path.basename(file_path),
            )
            return None, "red", None

        tier = candidate.get("tier")
        score = candidate.get("score")
        meta = candidate.get("meta") or {}

        if tier == "green":
            try:
                tag_service.write_tags(file_path, meta)
                logger.info(
                    "Auto-tagged (green, %.2f): %s — %s",
                    score or 0.0, meta.get("artist", "?"), meta.get("title", "?"),
                )
                return meta, "green", score
            except ValueError as e:
                logger.info(f"Skipping tag write ({e})")
                return None, "green", score
            except Exception as e:
                logger.error(f"Tag write failed: {e}")
                return None, "green", score

        logger.warning(
            "Low-confidence match (%s, score=%.2f) for %s — not writing tags, "
            "flagging for review.",
            tier, score or 0.0, os.path.basename(file_path),
        )
        return None, tier, score

    def _file_has_embedded_mbid(self, file_path: str) -> bool:
        """Return True iff the audio file already has a MusicBrainz track ID."""
        return file_has_embedded_mbid(file_path)

    def _process_failure(self, item: DownloadItem, error_msg: str):
        """Updates the database for a failed download attempt."""
        self.db.update_download_status(item.id, "failed")
        logger.info(f"Marked as 'failed' in database")

    def _get_library_path_for_track(self, track: Track) -> str:
        """Generate clean library path: D:/Music/Artist/Album/Song.flac"""
        return library_path_for_track(self.music_library_dir, track)

    def _scan_downloaded_file(self, file_path: str) -> None:
        """Use the scanner's public entry point with legacy-double fallback."""
        process_file = getattr(self.scanner, "process_file", None)
        if callable(process_file):
            process_file(file_path)
            return
        self.scanner._process_file(file_path)

    def _process_downloaded_file(
        self, file_path: str, item: DownloadItem, is_album_mode: bool
    ) -> None:
        tagged_meta, tag_tier, tag_score = self._auto_tag_file(file_path)

        # A single-track queue item can supply its recording MBID when the
        # auto-tagger did not produce metadata.  A release MBID must never be
        # stamped onto every file in album mode.
        if not tagged_meta and not is_album_mode and item.mbid_guess:
            logger.debug(
                f"Writing MBID {item.mbid_guess} to {os.path.basename(file_path)}"
            )
            write_mbid_to_file(file_path, item.mbid_guess)

        try:
            self._scan_downloaded_file(file_path)
        except Exception as e:
            logger.error(f"Scanner failed for {file_path}: {e}")
            return

        metadata = read_downloaded_metadata(file_path, tagged_meta)
        if metadata is None:
            logger.warning(f"Could not read tags for moving: {file_path}")
            return

        dest_path = ingest_downloaded_audio_file(
            self.db,
            self.scanner,
            self.music_library_dir,
            file_path,
            artist=metadata.artist,
            title=metadata.title,
            album=metadata.album,
            track_number=metadata.track_number,
        )
        logger.debug(f"Moved to: {dest_path}")

        if not tag_tier:
            return
        scanned = self.db.get_track_by_path(dest_path)
        if not scanned or not scanned.mbid:
            return
        self.db.set_track_tag_tier(scanned.mbid, tag_tier, tag_score)
        if tag_tier != "green":
            logger.warning(
                "Flagged for tag review (%s, score=%s): %s",
                tag_tier,
                tag_score,
                dest_path,
            )

    def _process_success(self, item: DownloadItem):
        """Handles a successful download (Single Track or Album)"""
        logger.debug("Processing successful download...")

        found_files = discover_downloaded_audio(self.downloads_dir)
        if not found_files:
            logger.warning(
                f"Download reported success but no audio files found in {self.downloads_dir}"
            )
            # If skip-music-dir worked, maybe no files were downloaded?
            # In that case, we can assume success and clear queue.
            # But we should verify if files exist in library?
            # For now, if no files found, we assume they were skipped or failed silently.
            # We'll trust the 'check=True' on subprocess but log warning.
            logger.info("Assuming files were skipped (existing in library). Marking done.")
            self.db.remove_from_queue(item.id)
            return

        is_album_mode = item.search_query.startswith("::ALBUM::")
        logger.info(f"Found {len(found_files)} files. Processing...")

        for file_path in found_files:
            try:
                self._process_downloaded_file(file_path, item, is_album_mode)
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")

        cleanup_empty_download_directories(self.downloads_dir)

        # Done
        self.db.remove_from_queue(item.id)
        logger.info(f"Item {item.id} processing complete.")


def _build_jellyfin_client(config: dict) -> Optional[JellyfinClient]:
    url = (config.get("jellyfin_url") or "").strip()
    api_key = (config.get("jellyfin_api_key") or "").strip()
    user_id = (config.get("jellyfin_user_id") or "").strip()
    if not url or not api_key or not user_id:
        return None
    try:
        return JellyfinClient(base_url=url, api_key=api_key, user_id=user_id)
    except Exception as e:
        logger.warning("Could not build Jellyfin client for scan trigger: %s", e)
        return None


def _build_lidarr_client(config: dict) -> Optional[LidarrClient]:
    """Return a Lidarr client only for an authority role when enabled.

    Satellites never get one: they queue locally and rely on the next
    catalog sync to pull down whatever the master has imported.
    """
    if not is_authority_config(config):
        return None
    if not config.get("lidarr_enabled"):
        return None
    url = (config.get("lidarr_url") or "").strip()
    api_key = (config.get("lidarr_api_key") or "").strip()
    if not url or not api_key:
        logger.info("Lidarr enabled but url/api_key missing; skipping sidecar.")
        return None
    try:
        client = LidarrClient(base_url=url, api_key=api_key)
    except ValueError as e:
        logger.warning("Could not build Lidarr client: %s", e)
        return None
    if not client.ping():
        logger.warning("Lidarr unreachable at %s; falling back to sldl.", url)
        return None
    return client


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

    lidarr_client = _build_lidarr_client(config)
    jellyfin_client = _build_jellyfin_client(config)

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
        slsk_config=config,  # Pass entire config dict
        lidarr_client=lidarr_client,
        lidarr_quality_profile_id=config.get("lidarr_quality_profile_id"),
        lidarr_root_folder_path=config.get("lidarr_root_folder_path"),
        jellyfin_client=jellyfin_client,
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
