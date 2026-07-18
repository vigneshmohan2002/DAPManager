
"""
Download queue processing for DAP Manager.
"""

import logging
import os
import queue as thread_queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .db_manager import DatabaseManager, DownloadItem, Track
from .library_scanner import LibraryScanner
from .audio_quality import library_path_for_track
from .file_ingest import (
    canonical_recording_mbid,
    file_has_embedded_mbid,
    ingest_downloaded_audio_file_with_result,
    normalize_embedded_recording_mbid,
    read_embedded_recording_mbid,
)
from . import tag_service
from .lidarr_client import LidarrClient, LidarrError
from .jellyfin_client import JellyfinClient
from .library_mirror import mirror_imported_file
from .config_manager import is_authority_config
from .services.album_download_request_service import (
    canonical_release_mbid,
    inspect_release_inventory,
    read_flac_release_track_identity,
)

logger = logging.getLogger(__name__)

DOWNLOADED_AUDIO_EXTENSIONS = (".flac", ".mp3", ".m4a", ".wav", ".ogg")
INCOMPLETE_AUDIO_EXTENSIONS = tuple(
    f"{extension}.incomplete" for extension in DOWNLOADED_AUDIO_EXTENSIONS
)
ALBUM_QUERY_PREFIX = "::ALBUM::"
FEATURE_CREDIT_SEPARATOR = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DownloadedMetadata:
    artist: str
    album: str
    title: str
    track_number: int
    disc_number: int = 1


@dataclass(frozen=True)
class ProcessedDownload:
    """A satisfied staged file and whether canonical audio changed."""

    path: str
    library_changed: bool


@dataclass(frozen=True)
class ProcessedQueueItem:
    """Outcome of consuming one isolated queue-item staging directory."""

    changed_file_count: int
    completed: bool


def parse_download_query(search_query: str) -> Tuple[str, bool]:
    """Return the sldl query and whether its album-mode marker was present."""
    if not search_query.startswith(ALBUM_QUERY_PREFIX):
        return search_query, False
    return search_query[len(ALBUM_QUERY_PREFIX):].strip(), True


def _contains_command_unsafe_control_characters(value: str) -> bool:
    """Reject control characters before deriving a broader search query."""
    return any(
        ord(character) < 32 or ord(character) == 127
        for character in value
    )


def primary_artist_album_fallback_query(search_query: str) -> Optional[str]:
    """Return one conservative album-search fallback for feature credits.

    Queue items retain their complete credited-artist identity.  This helper
    derives a second, command-local query only when the album artist contains
    an explicit ``feat.``, ``ft.``, or ``featuring`` separator.  Delimiters
    such as ``&`` and commas are deliberately not interpreted because they can
    be part of a group's canonical name.
    """
    query, is_album_mode = parse_download_query(search_query)
    if not is_album_mode:
        return None
    if (
        not query
        or ALBUM_QUERY_PREFIX in query
        or _contains_command_unsafe_control_characters(query)
    ):
        return None

    credited_artist, separator, album = query.partition(" - ")
    if not separator or not credited_artist.strip() or not album.strip():
        return None

    credit_match = FEATURE_CREDIT_SEPARATOR.search(credited_artist)
    if not credit_match:
        return None

    primary_artist = credited_artist[:credit_match.start()].strip()
    featured_artist = credited_artist[credit_match.end():].strip()
    normalized_album = album.strip()
    if (
        not primary_artist
        or not featured_artist
        or primary_artist.startswith("-")
        or normalized_album.startswith("-")
    ):
        return None

    return f"::ALBUM:: {primary_artist} - {normalized_album}"


def build_download_command(
    command_base: List[str],
    username: str,
    password: str,
    search_query: str,
    downloads_dir: str,
    music_library_dir: str,
    config: Mapping[str, Any],
    album_track_count: Optional[int] = None,
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

        if album_track_count is not None:
            try:
                exact_track_count = int(album_track_count)
            except (TypeError, ValueError) as exc:
                raise ValueError("album track count must be a positive integer") from exc
            if exact_track_count <= 0:
                raise ValueError("album track count must be a positive integer")

            separate_flags = [
                index
                for index, value in enumerate(command)
                if value == "--album-track-count"
            ]
            inline_values = [
                value.split("=", 1)[1]
                for value in command
                if value.startswith("--album-track-count=")
            ]
            if len(separate_flags) + len(inline_values) > 1:
                raise ValueError("duplicate --album-track-count options are unsafe")
            if separate_flags:
                flag_index = separate_flags[0]
                try:
                    configured_track_count = int(command[flag_index + 1])
                except (IndexError, TypeError, ValueError) as exc:
                    raise ValueError(
                        "--album-track-count requires a positive integer"
                    ) from exc
            elif inline_values:
                try:
                    configured_track_count = int(inline_values[0])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "--album-track-count requires a positive integer"
                    ) from exc
            else:
                configured_track_count = exact_track_count
                command.extend([
                    "--album-track-count",
                    str(exact_track_count),
                ])
            if configured_track_count != exact_track_count:
                raise ValueError(
                    "configured --album-track-count conflicts with the exact "
                    "MusicBrainz release manifest"
                )
    elif album_track_count is not None:
        raise ValueError("album track count can only be used in album mode")

    # ``--pref-format`` only changes Soulseek result ranking; it does not
    # exclude lossy files.  Apply sldl's hard format filter to both tracks and
    # albums so every newly acquired library file is FLAC.
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


def _discover_incomplete_audio(downloads_dir: str) -> List[str]:
    """Return sldl temporary files that prove an audio attempt was partial."""
    return [
        os.path.join(root, filename)
        for root, _, files in os.walk(downloads_dir)
        for filename in files
        if filename.lower().endswith(INCOMPLETE_AUDIO_EXTENSIONS)
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
        disc_number = tagged_meta.get("disc_number", 1)
    else:
        artist = str(audio.get("artist", ["Unknown Artist"])[0])
        album = str(audio.get("album", ["Unknown Album"])[0])
        title = str(audio.get("title", ["Unknown Title"])[0])
        track_number = audio["tracknumber"][0] if "tracknumber" in audio else 0
        disc_number = audio["discnumber"][0] if "discnumber" in audio else 1

    track_match = re.match(r"^\s*(\d+)", str(track_number or ""))
    parsed_track_number = int(track_match.group(1)) if track_match else 0
    disc_match = re.match(r"^\s*(\d+)", str(disc_number or ""))
    parsed_disc_number = int(disc_match.group(1)) if disc_match else 1
    return DownloadedMetadata(
        artist=artist,
        album=album,
        title=title,
        track_number=parsed_track_number,
        disc_number=parsed_disc_number,
    )


def exact_manifest_tag_metadata(
    album_manifest: Mapping[str, Any],
    recording_mbid: str,
) -> Optional[Dict[str, Any]]:
    """Build canonical tags from a persisted exact-release manifest."""
    release_mbid = canonical_release_mbid(album_manifest.get("release_mbid"))
    recording_mbid = canonical_recording_mbid(recording_mbid)
    raw_tracks = album_manifest.get("tracks") or ()
    tracks: Sequence[Mapping[str, Any]] = tuple(
        track for track in raw_tracks if isinstance(track, Mapping)
    )
    if not release_mbid or not recording_mbid or not tracks:
        return None
    matches = [
        track for track in tracks
        if canonical_recording_mbid(track.get("recording_mbid"))
        == recording_mbid
    ]
    if len(matches) != 1:
        return None
    track = matches[0]
    try:
        track_position = int(track.get("track_position") or 0)
        disc_position = int(track.get("medium_position") or 0)
        track_total = int(track.get("track_total") or 0)
        disc_total = int(track.get("disc_total") or 0)
    except (TypeError, ValueError):
        return None
    if not track_total:
        try:
            track_total = sum(
                1
                for candidate in tracks
                if int(candidate.get("medium_position") or 0) == disc_position
            )
        except (TypeError, ValueError):
            return None
    if not disc_total:
        try:
            disc_total = len({
                int(candidate.get("medium_position") or 0)
                for candidate in tracks
                if int(candidate.get("medium_position") or 0) > 0
            })
        except (TypeError, ValueError):
            return None
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    album = str(album_manifest.get("title") or "").strip()
    album_artist = str(album_manifest.get("artist") or "").strip()
    if (
        not title
        or not artist
        or not album
        or not album_artist
        or track_position <= 0
        or track_total < track_position
        or disc_position <= 0
        or disc_total < disc_position
    ):
        return None
    release_track_mbid = canonical_release_mbid(
        track.get("release_track_mbid")
    )
    if not release_track_mbid:
        return None
    return {
        "artist": artist,
        "album_artist": album_artist,
        "album": album,
        "title": title,
        "date": str(track.get("date") or "").strip(),
        "track_number": track_position,
        "track_total": track_total,
        "disc_number": disc_position,
        "disc_total": disc_total,
        "mbid": recording_mbid,
        "release_mbid": release_mbid,
        "release_track_mbid": release_track_mbid,
    }


def usable_exact_track_manifest(rows: Any) -> Tuple[Mapping[str, Any], ...]:
    """Return only a complete newer-request manifest; legacy rows stay legacy."""
    if not isinstance(rows, (list, tuple)):
        return ()
    tracks = tuple(row for row in rows if isinstance(row, Mapping))
    if len(tracks) != len(rows) or not tracks:
        return ()
    for track in tracks:
        try:
            track_position = int(track.get("track_position") or 0)
            disc_position = int(track.get("medium_position") or 0)
            track_total = int(track.get("track_total") or 0)
            disc_total = int(track.get("disc_total") or 0)
            complete = (
                canonical_recording_mbid(track.get("recording_mbid"))
                and str(track.get("title") or "").strip()
                and str(track.get("artist") or "").strip()
                and 0 < track_position <= track_total
                and 0 < disc_position <= disc_total
                and canonical_release_mbid(track.get("release_track_mbid"))
            )
        except (TypeError, ValueError):
            return ()
        if not complete:
            return ()
    return tracks


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
        jellyfin_music_library_dir: Optional[str] = None,
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
        self.auto_tag_downloads = (
            self.slsk_config.get("auto_tag_downloads", True) is not False
        )

        self.lidarr = lidarr_client
        self.lidarr_quality_profile_id = lidarr_quality_profile_id
        self.lidarr_root_folder_path = lidarr_root_folder_path
        self.jellyfin_client = jellyfin_client
        self.jellyfin_music_library_dir = (
            (jellyfin_music_library_dir or "").strip() or None
        )

        # Ensure directories exist
        os.makedirs(self.downloads_dir, exist_ok=True)
        os.makedirs(self.music_library_dir, exist_ok=True)

        logger.info(f"Downloader initialized")
        logger.info(f"  Downloads dir: {self.downloads_dir}")
        logger.info(f"  Music library: {self.music_library_dir}")
        if self.jellyfin_music_library_dir:
            logger.info(
                "  Jellyfin mirror library: %s",
                self.jellyfin_music_library_dir,
            )

    def _create_item_staging_dir(self, item: DownloadItem) -> str:
        """Create a fresh directory attributable to exactly one queue attempt."""
        item_id = item.id if item.id is not None else "new"
        return tempfile.mkdtemp(
            prefix=f".dap-queue-{item_id}-",
            dir=self.downloads_dir,
        )

    def _finish_item_staging_dir(self, staging_dir: str) -> None:
        """Remove an empty attempt directory or retain isolated residue safely."""
        cleanup_empty_download_directories(staging_dir)
        try:
            os.rmdir(staging_dir)
        except FileNotFoundError:
            return
        except OSError:
            logger.warning(
                "Retaining isolated staging residue for inspection: %s",
                staging_dir,
            )

    def _update_album_request_progress(
        self,
        item: DownloadItem,
        stage: str,
        detail: str = "",
        completed_tracks: Optional[int] = None,
    ) -> None:
        """Best-effort progress for satellite-originated album requests."""
        if item.playlist_id != "SATELLITE_ALBUM" or item.id is None:
            return
        update = getattr(self.db, "update_album_download_request_progress", None)
        if not callable(update):
            return
        try:
            update(
                item.id,
                stage,
                str(detail or "")[-2000:],
                completed_tracks,
            )
        except Exception:
            # Progress visibility must never turn a valid media import into a
            # failed download attempt.
            logger.warning(
                "Could not persist album-request progress for queue item %s",
                item.id,
                exc_info=True,
            )

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
        changed_file_count = 0

        for i, item in enumerate(queue, 1):
            msg = f"[{i}/{len(queue)}] Processing: {item.search_query}"
            report(msg)
            if (
                item.playlist_id == "SATELLITE_ALBUM"
                and item.id is not None
                and item.status == "failed"
            ):
                # This queue runner deliberately retries failed rows. Publish
                # the active attempt as pending before satellite polling sees
                # the new downloading stage; otherwise the persisted failed
                # queue state would immediately overwrite live progress.
                self.db.update_download_status(item.id, "pending")
            self._update_album_request_progress(
                item,
                "downloading",
                "Starting the master download attempt",
            )
            item_staging_dir = None

            try:
                # Pass a specialized callback for the item
                def item_callback(line):
                    detail = line.strip()
                    self._update_album_request_progress(
                        item,
                        "downloading",
                        detail,
                    )
                    if progress_callback:
                        progress_callback({
                            "message": msg,
                            "detail": detail
                        })

                if self._try_lidarr_album(item, report):
                    # Lidarr owns this one now; library scanner will pick
                    # up the imported file on its next pass.
                    self._update_album_request_progress(
                        item,
                        "importing",
                        "Lidarr accepted the release; waiting for its library import",
                    )
                    self.db.remove_from_queue(item.id)
                    success_count += 1
                    continue

                item_staging_dir = self._create_item_staging_dir(item)
                if not self._attempt_download(
                    item,
                    item_callback,
                    staging_dir=item_staging_dir,
                ):
                    self._process_failure(
                        item,
                        "Downloader completed without staging any audio files",
                    )
                    fail_count += 1
                    continue
                processed_item = self._process_success(
                    item,
                    staging_dir=item_staging_dir,
                )
                if isinstance(processed_item, ProcessedQueueItem):
                    changed_file_count += processed_item.changed_file_count
                    if processed_item.completed:
                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    # Compatibility for private-method test doubles that still
                    # return the pre-result-object integer change count.
                    changed_file_count += int(processed_item)
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
                self._process_failure(item, "slsk-batchdl command not found")
                fail_count += 1
                report("FATAL: slsk-batchdl command not found")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}", exc_info=True)
                self._process_failure(item, str(e))
                fail_count += 1
            finally:
                if item_staging_dir:
                    self._finish_item_staging_dir(item_staging_dir)

        report(f"Download queue finished. Success: {success_count}, Failed: {fail_count}")

        if changed_file_count > 0 and self.jellyfin_client:
            report("Triggering Jellyfin library scan...")
            self.jellyfin_client.trigger_library_scan()

    def _attempt_download(
        self,
        item: DownloadItem,
        item_callback=None,
        staging_dir: Optional[str] = None,
    ):
        """
        Calls slsk-batchdl to download a single track or album.
        Streams output to item_callback if provided.
        """
        album_track_count = None
        if item.playlist_id == "SATELLITE_ALBUM":
            if item.id is None:
                raise ValueError(
                    "verified album queue item is missing its persistent ID"
                )
            get_request = getattr(
                self.db,
                "get_album_download_request_by_queue_item",
                None,
            )
            tracker = get_request(item.id) if callable(get_request) else None
            if not tracker:
                raise ValueError(
                    "verified album tracker is missing before download launch"
                )
            try:
                album_track_count = int(tracker.get("track_count") or 0)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(
                    "verified album tracker has an invalid track count"
                ) from exc
            if album_track_count <= 0:
                raise ValueError(
                    "verified album tracker has an invalid track count"
                )

        command, is_album_mode = build_download_command(
            self.slsk_cmd_base,
            self.slsk_username,
            self.slsk_password,
            item.search_query,
            staging_dir or self.downloads_dir,
            self.music_library_dir,
            self.slsk_config,
            album_track_count,
        )
        if is_album_mode:
            query, _ = parse_download_query(item.search_query)
            logger.info("Detected Album Mode for: %r", query)

        total_timeout = self._download_timeout_seconds(is_album_mode)
        idle_timeout = self._download_idle_timeout_seconds(is_album_mode)
        self._run_sldl_command(
            command,
            item_callback,
            timeout_seconds=total_timeout,
            idle_timeout_seconds=idle_timeout,
        )

        # Album attempts run in a fresh item-specific staging directory.  A
        # completed first search with no staged audio is therefore an exact
        # zero-file result for this queue attempt.  Retry only that case with
        # a deterministic primary-artist query; never retry a partial album or
        # change the queue item's credited identity.
        fallback_query = primary_artist_album_fallback_query(item.search_query)
        fallback_enabled = (
            self.slsk_config.get(
                "slsk_album_primary_artist_fallback",
                True,
            )
            is True
        )
        if (
            is_album_mode
            and staging_dir
            and fallback_enabled
            and fallback_query
            and not discover_downloaded_audio(staging_dir)
            and not _discover_incomplete_audio(staging_dir)
        ):
            fallback_command, _ = build_download_command(
                self.slsk_cmd_base,
                self.slsk_username,
                self.slsk_password,
                fallback_query,
                staging_dir,
                self.music_library_dir,
                self.slsk_config,
                album_track_count,
            )
            fallback_input, _ = parse_download_query(fallback_query)
            logger.info(
                "Album search returned no audio; retrying Soulseek with "
                "primary artist only: %s",
                fallback_input,
            )
            if item_callback:
                item_callback(
                    "No album audio found; retrying with primary artist: "
                    f"{fallback_input}"
                )
            self._run_sldl_command(
                fallback_command,
                item_callback,
                timeout_seconds=total_timeout,
                idle_timeout_seconds=idle_timeout,
            )

        if staging_dir and not discover_downloaded_audio(staging_dir):
            logger.warning(
                "Downloader completed without staging audio for queue item %s",
                item.id,
            )
            return False

        return True

    def _download_timeout_seconds(self, is_album_mode: bool) -> int:
        key = (
            "slsk_album_download_timeout_seconds"
            if is_album_mode
            else "slsk_download_timeout_seconds"
        )
        default = 6 * 60 * 60 if is_album_mode else 60 * 60
        try:
            return max(60, int(self.slsk_config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _download_idle_timeout_seconds(self, is_album_mode: bool) -> int:
        key = (
            "slsk_album_download_idle_timeout_seconds"
            if is_album_mode
            else "slsk_download_idle_timeout_seconds"
        )
        default = 30 * 60 if is_album_mode else 10 * 60
        try:
            return max(60, int(self.slsk_config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _run_sldl_command(
        self,
        command,
        item_callback=None,
        *,
        timeout_seconds: Optional[int] = None,
        idle_timeout_seconds: Optional[int] = None,
    ) -> None:
        """Run one sldl command and stream its output."""
        # Keep argv structured (``shell=False`` is Popen's default) and never
        # expose the Soulseek password in debug logs. ``repr`` also escapes
        # newlines in malformed input rather than allowing log-line injection.
        logged_command = list(command)
        secrets_to_redact = set()
        for index, argument in enumerate(logged_command):
            if argument == "--pass" and index + 1 < len(logged_command):
                if logged_command[index + 1]:
                    secrets_to_redact.add(str(logged_command[index + 1]))
                logged_command[index + 1] = "<redacted>"
            elif argument.startswith("--pass="):
                inline_secret = argument.partition("=")[2]
                if inline_secret:
                    secrets_to_redact.add(inline_secret)
                logged_command[index] = "--pass=<redacted>"
        logger.debug("Executing argv: %r", logged_command)

        # Use Popen to stream output
        process = None
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,  # Line buffered
                universal_newlines=True,
            )

            output_queue = thread_queue.Queue()
            output_finished = object()

            def read_output() -> None:
                try:
                    for output_line in process.stdout:
                        output_queue.put(output_line)
                finally:
                    output_queue.put(output_finished)

            reader = threading.Thread(
                target=read_output,
                name="sldl-output-reader",
                daemon=True,
            )
            reader.start()
            total_timeout = max(60, int(timeout_seconds or 60 * 60))
            idle_timeout = max(60, int(idle_timeout_seconds or 10 * 60))
            deadline = time.monotonic() + total_timeout
            idle_deadline = time.monotonic() + idle_timeout

            # Reading a pipe directly can block forever before ``wait`` gets
            # its timeout. A daemon reader plus a bounded queue wait enforces
            # one real wall-clock deadline while preserving streamed progress.
            while True:
                remaining = min(deadline, idle_deadline) - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(logged_command, total_timeout)
                try:
                    line = output_queue.get(timeout=min(0.25, remaining))
                except thread_queue.Empty:
                    continue
                if line is output_finished:
                    break
                line = line.strip()
                if line:
                    idle_deadline = time.monotonic() + idle_timeout
                    safe_line = line
                    for secret in secrets_to_redact:
                        safe_line = safe_line.replace(secret, "<redacted>")
                    logger.debug("SLSK: %s", safe_line)
                    if item_callback:
                        item_callback(safe_line)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(logged_command, total_timeout)
            process.wait(timeout=remaining)

            if process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode, logged_command, output="See logs"
                )

        except subprocess.TimeoutExpired as exc:
            if process is not None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            # Never attach the raw argv (which contains --pass) to the
            # propagated exception or downstream logs.
            raise subprocess.TimeoutExpired(
                logged_command,
                exc.timeout,
                output="See logs",
            ) from None
        except Exception:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    process.kill()
                    try:
                        process.wait(timeout=5)
                    except (subprocess.TimeoutExpired, OSError):
                        pass
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
        if item.playlist_id == "SATELLITE_ALBUM":
            # Satellite requests select one concrete MusicBrainz release and
            # require hard-FLAC, per-file manifest validation. Lidarr operates
            # on release groups and an accepted search is not proof that the
            # selected pressing or a FLAC result was imported, so these jobs
            # must stay on the verified Soulseek pipeline.
            return False
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

    def _auto_tag_file(
        self,
        file_path: str,
        *,
        expected_release_mbid: str = "",
        expected_recording_mbid: str = "",
    ):
        """Picard-style auto-tag for a freshly-downloaded file.

        Returns ``(meta, tier, score)`` where ``meta`` is the candidate
        metadata dict (used downstream for the library sort path) or
        None, ``tier`` is "green"/"yellow"/"red"/None, and ``score`` is
        the AcoustID match score or None.

        Policy:
        * Complete embedded Picard identity is still acoustically verified;
          source tags alone never earn a green tier.
        * Fingerprint + lookup via ``tag_service``.
          - green: apply tags, return tier="green".
          - yellow/red: do NOT write tags; return tier so caller can flag
            the track for manual review. Yellow is "probably right but
            not confident" and still requires user confirmation.
        * Any failure (no API key, API error, unreadable file) returns
          ``(None, None, None)`` — the track proceeds with whatever tags
          the downloader source provided.
        """
        current = tag_service.read_current_tags(file_path)
        current_recording = canonical_recording_mbid(current.get("mbid"))
        current_release = canonical_release_mbid(current.get("release_mbid"))
        expected_release = canonical_release_mbid(expected_release_mbid)
        expected_recording = canonical_recording_mbid(expected_recording_mbid)

        if tag_service.has_complete_picard_tags(file_path):
            if (
                (expected_release and current_release != expected_release)
                or (expected_recording and current_recording != expected_recording)
            ):
                logger.warning(
                    "Complete embedded MusicBrainz identity conflicts with "
                    "the selected manifest: %s",
                    os.path.basename(file_path),
                )
                return None, "red", None
            logger.info(
                "Verifying complete embedded Picard tags acoustically%s: %s",
                (
                    f" against selected release {expected_release}"
                    if expected_release
                    else ""
                ),
                os.path.basename(file_path),
            )

        if not self.auto_tag_downloads:
            logger.info(
                "Automatic Picard tagging disabled; leaving staged tags unchanged: %s",
                os.path.basename(file_path),
            )
            return None, None, None

        if not self.acoustid_api_key:
            logger.debug("No AcoustID key configured; skipping auto-tag.")
            return None, None, None

        try:
            bound_release = expected_release or current_release
            bound_recording = expected_recording or current_recording
            if bound_release:
                candidate = tag_service.identify_file_for_release(
                    file_path,
                    self.acoustid_api_key,
                    bound_release,
                    bound_recording or "",
                    self.contact_email,
                )
            else:
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

        if tier == "green" and tag_service.is_safe_auto_candidate(
            candidate,
            expected_release_mbid=expected_release or "",
            expected_recording_mbid=(
                expected_recording
                or (current_recording if expected_release else "")
                or ""
            ),
        ):
            if expected_release:
                # A selected-release request already carries a persisted,
                # immutable track manifest.  AcoustID is the fail-closed
                # identity check here; the manifest must be the sole metadata
                # write so a later failure cannot leave a second, freshly
                # fetched MusicBrainz interpretation on the staged file.
                logger.info(
                    "Acoustically verified (green, %.2f) against release %s: %s",
                    score or 0.0,
                    expected_release,
                    os.path.basename(file_path),
                )
                return meta, "green", score
            try:
                tag_service.write_tags_atomic(file_path, meta)
                logger.info(
                    "Auto-tagged (green, %.2f): %s — %s",
                    score or 0.0, meta.get("artist", "?"), meta.get("title", "?"),
                )
                return meta, "green", score
            except ValueError as e:
                logger.info(f"Skipping tag write ({e})")
                return None, "red", score
            except Exception as e:
                logger.error(f"Tag write failed: {e}")
                return None, "red", score

        if tier == "green":
            logger.warning(
                "High-score match lacked complete canonical MusicBrainz "
                "identity for %s — not writing tags.",
                os.path.basename(file_path),
            )
            return None, "red", score

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
        self._update_album_request_progress(item, "failed", error_msg)
        logger.info(f"Marked as 'failed' in database")

    def _mirror_to_jellyfin(self, imported_path: str) -> bool:
        """Best-effort mirror; return whether Jellyfin-visible media changed."""
        if not self.jellyfin_music_library_dir:
            return False
        try:
            return bool(
                mirror_imported_file(
                    imported_path,
                    self.music_library_dir,
                    self.jellyfin_music_library_dir,
                )
            )
        except Exception as exc:
            # The canonical DAPManager import already succeeded.  A missing or
            # unhealthy mirror mount must not retain the queue item or prevent
            # the existing Jellyfin refresh from running.
            logger.error(
                "Could not mirror imported file %s to Jellyfin library: %s",
                imported_path,
                exc,
                exc_info=True,
            )
            return False

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
        self,
        file_path: str,
        item: DownloadItem,
        is_album_mode: bool,
        album_manifest: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ProcessedDownload]:
        # Defence in depth for album jobs: even if a downloader regression or
        # stale staging file bypasses the command filter, never import lossy
        # audio into the music library.
        if is_album_mode and not file_path.lower().endswith(".flac"):
            logger.warning("Skipping non-FLAC album file: %s", file_path)
            return None

        # Validate and canonicalize source recording tags before either the
        # auto-tagger or scanner trusts them.  In particular, upper-case UUIDs
        # must resolve to the same lower-case database identity, while an
        # arbitrary non-UUID tag must not become a catalog primary key.
        try:
            embedded_before_lookup = normalize_embedded_recording_mbid(file_path)
        except (OSError, ValueError) as exc:
            logger.warning("Rejecting invalid recording MBID in %s: %s", file_path, exc)
            return None

        expected_release = ""
        expected_recordings = set()
        expected_queue_recording = None
        if not is_album_mode and item.mbid_guess:
            expected_queue_recording = canonical_recording_mbid(item.mbid_guess)
            if expected_queue_recording is None:
                logger.warning(
                    "Rejecting invalid queue recording MBID for item %s",
                    item.id,
                )
                return None
            if (
                embedded_before_lookup
                and embedded_before_lookup != expected_queue_recording
            ):
                logger.warning(
                    "Rejecting staged single-track file whose embedded "
                    "recording %s conflicts with queue identity %s",
                    embedded_before_lookup,
                    expected_queue_recording,
                )
                return None
        if album_manifest is not None:
            expected_release = canonical_release_mbid(
                album_manifest.get("release_mbid")
            ) or ""
            expected_recordings = {
                canonical_recording_mbid(value)
                for value in album_manifest.get("recording_mbids", ())
            }
            expected_recordings.discard(None)
            if (
                embedded_before_lookup
                and embedded_before_lookup not in expected_recordings
            ):
                logger.warning(
                    "Rejecting staged album file whose embedded recording is "
                    "outside the selected manifest: %s",
                    file_path,
                )
                return None

        self._update_album_request_progress(
            item,
            "importing",
            f"Checking Picard-style tags for {os.path.basename(file_path)}",
        )
        tagged_meta, tag_tier, tag_score = self._auto_tag_file(
            file_path,
            expected_release_mbid=expected_release,
            expected_recording_mbid=expected_queue_recording or "",
        )
        if album_manifest is not None and tag_tier != "green":
            logger.warning(
                "Rejecting selected-release file without a green release-bound "
                "acoustic identification: %s",
                file_path,
            )
            return None

        if expected_queue_recording and tag_tier != "green":
            logger.warning(
                "Rejecting identity-bound single-track file without a green "
                "AcoustID match for recording %s: %s",
                expected_queue_recording,
                file_path,
            )
            return None

        # The auto-tagger may have added a recording ID.
        # Canonicalize once more before the scanner persists that key.
        try:
            normalize_embedded_recording_mbid(file_path)
        except (OSError, ValueError) as exc:
            logger.warning("Rejecting invalid recording MBID in %s: %s", file_path, exc)
            return None

        if expected_queue_recording:
            verified_queue_recording = read_embedded_recording_mbid(file_path)
            if verified_queue_recording != expected_queue_recording:
                logger.warning(
                    "Rejecting tagged single-track file whose persisted "
                    "recording identity did not verify: %s",
                    file_path,
                )
                return None

        if album_manifest is not None:
            recording_mbid = (
                read_embedded_recording_mbid(file_path)
                or canonical_recording_mbid((tagged_meta or {}).get("mbid"))
            )
            if not recording_mbid or recording_mbid not in expected_recordings:
                logger.warning(
                    "Rejecting staged album file outside selected MusicBrainz "
                    "manifest: %s (%s)",
                    file_path,
                    recording_mbid or "missing recording MBID",
                )
                return None

            if not expected_release:
                logger.warning("Rejecting album import with invalid tracker release MBID")
                return None
            existing_track = self.db.get_track_by_mbid(recording_mbid)
            existing_release = canonical_release_mbid(
                getattr(existing_track, "release_mbid", None)
            )
            if (
                existing_track is not None
                and existing_release
                and existing_release != expected_release
                and getattr(existing_track, "local_path", None)
                and os.path.isfile(existing_track.local_path)
            ):
                logger.warning(
                    "Rejecting selected release because recording %s already "
                    "belongs to another live edition %s",
                    recording_mbid,
                    existing_release,
                )
                return None
            try:
                # The persisted exact-release manifest wins over source tags
                # and any ambiguous release attached to the AcoustID result.
                exact_meta = exact_manifest_tag_metadata(
                    album_manifest,
                    recording_mbid,
                )
                if exact_meta is None:
                    raise ValueError("selected release track metadata is incomplete")
                tag_service.write_tags_atomic(file_path, exact_meta)
                tagged_meta = exact_meta
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Could not bind staged file to selected release %s: %s",
                    expected_release,
                    exc,
                )
                return None

            verified = read_flac_release_track_identity(file_path)
            if (
                verified is None
                or verified.recording_mbid != recording_mbid
                or verified.release_mbid != expected_release
                or verified.title != str(exact_meta["title"])
                or verified.artist != str(exact_meta["artist"])
                or verified.date != str(exact_meta["date"])
                or verified.track_position != int(exact_meta["track_number"])
                or verified.track_total != int(exact_meta["track_total"])
                or verified.medium_position != int(exact_meta["disc_number"])
                or verified.disc_total != int(exact_meta["disc_total"])
                or (
                    exact_meta.get("release_track_mbid")
                    and verified.release_track_mbid
                    != exact_meta["release_track_mbid"]
                )
            ):
                logger.warning(
                    "Rejecting staged file whose MusicBrainz tags could not be "
                    "verified: %s",
                    file_path,
                )
                return None

        try:
            self._scan_downloaded_file(file_path)
        except Exception as e:
            logger.error(f"Scanner failed for {file_path}: {e}")
            return None

        # Album queue identity is a *release* MBID.  Route each staged file by
        # its own exact embedded recording MBID and never pass the release ID
        # as a recording fallback.
        recording_mbid = read_embedded_recording_mbid(file_path)
        metadata = read_downloaded_metadata(file_path, tagged_meta)
        if metadata is None:
            logger.warning(f"Could not read tags for moving: {file_path}")
            return None

        ingest_result = ingest_downloaded_audio_file_with_result(
            self.db,
            self.scanner,
            self.music_library_dir,
            file_path,
            artist=metadata.artist,
            title=metadata.title,
            album=metadata.album,
            track_number=metadata.track_number,
            disc_number=metadata.disc_number,
            recording_mbid=recording_mbid,
        )
        dest_path = ingest_result.path
        logger.debug(f"Moved to: {dest_path}")

        if tag_tier:
            scanned = self.db.get_track_by_path(dest_path)
            if scanned and scanned.mbid:
                self.db.set_track_tag_tier(scanned.mbid, tag_tier, tag_score)
                if tag_tier != "green":
                    logger.warning(
                        "Flagged for tag review (%s, score=%s): %s",
                        tag_tier,
                        tag_score,
                        dest_path,
                    )

        return ProcessedDownload(
            path=dest_path,
            library_changed=ingest_result.changed,
        )

    def _process_success(
        self,
        item: DownloadItem,
        staging_dir: Optional[str] = None,
    ) -> ProcessedQueueItem:
        """Handles a successful download (Single Track or Album)"""
        logger.debug("Processing successful download...")

        active_staging_dir = staging_dir or self.downloads_dir
        found_files = discover_downloaded_audio(active_staging_dir)
        incomplete_files = _discover_incomplete_audio(active_staging_dir)
        if not found_files:
            logger.warning(
                "Download reported success but no audio files found in %s",
                active_staging_dir,
            )
            self._process_failure(
                item,
                "Download completed without any staged audio files",
            )
            return ProcessedQueueItem(changed_file_count=0, completed=False)

        is_album_mode = item.search_query.startswith(ALBUM_QUERY_PREFIX)
        logger.info(f"Found {len(found_files)} files. Processing...")
        album_manifest: Optional[Mapping[str, Any]] = None
        album_inventory = None
        if item.playlist_id == "SATELLITE_ALBUM" and item.id is not None:
            get_request = getattr(
                self.db,
                "get_album_download_request_by_queue_item",
                None,
            )
            tracker = get_request(item.id) if callable(get_request) else None
            if not tracker:
                self._process_failure(
                    item,
                    "Verified album tracker is missing; staged files were not imported",
                )
                return ProcessedQueueItem(changed_file_count=0, completed=False)
            recording_mbids = tuple(
                self.db.get_album_download_request_recording_mbids(
                    int(tracker["id"])
                )
            )
            get_track_manifest = getattr(
                self.db,
                "get_album_download_request_track_manifest",
                None,
            )
            track_manifest = usable_exact_track_manifest(tuple(
                get_track_manifest(int(tracker["id"]))
                if callable(get_track_manifest)
                else ()
            ))
            if not recording_mbids:
                self._process_failure(
                    item,
                    "Verified MusicBrainz recording manifest is missing",
                )
                return ProcessedQueueItem(changed_file_count=0, completed=False)
            album_manifest = {
                "release_mbid": tracker.get("release_mbid"),
                "artist": tracker.get("artist"),
                "title": tracker.get("title"),
                "recording_mbids": recording_mbids,
                "tracks": track_manifest,
            }
            album_inventory = inspect_release_inventory(
                self.db,
                str(tracker.get("release_mbid") or ""),
                track_manifest or recording_mbids,
                self.music_library_dir,
            )
        self._update_album_request_progress(
            item,
            "importing",
            f"Importing {len(found_files)} downloaded FLAC file(s)",
            album_inventory.completed_tracks if album_inventory else 0,
        )

        satisfied_count = 0
        changed_count = 0
        # Any incomplete audio artifact proves the album attempt was partial.
        # Completed files may still safely satisfy/upgrade existing tracks,
        # but the queue identity must remain failed for a later missing-track
        # retry and the residue must stay isolated for inspection.
        rejected_count = len(incomplete_files)
        for file_path in found_files:
            try:
                if album_manifest is None:
                    processed = self._process_downloaded_file(
                        file_path,
                        item,
                        is_album_mode,
                    )
                else:
                    processed = self._process_downloaded_file(
                        file_path,
                        item,
                        is_album_mode,
                        album_manifest,
                    )
                if processed:
                    # Compatibility for older test doubles and callers of this
                    # private method that returned only the final path.
                    if isinstance(processed, ProcessedDownload):
                        imported_path = processed.path
                        library_changed = processed.library_changed
                    else:
                        imported_path = str(processed)
                        library_changed = True
                    satisfied_count += 1
                    progress_count = satisfied_count
                    if album_manifest is not None:
                        album_inventory = inspect_release_inventory(
                            self.db,
                            str(album_manifest.get("release_mbid") or ""),
                            tuple(
                                album_manifest.get("tracks")
                                or album_manifest.get("recording_mbids")
                                or ()
                            ),
                            self.music_library_dir,
                        )
                        progress_count = album_inventory.completed_tracks
                    self._update_album_request_progress(
                        item,
                        "importing",
                        f"Imported {satisfied_count} of {len(found_files)} file(s)",
                        progress_count,
                    )
                    mirror_changed = self._mirror_to_jellyfin(imported_path)
                    if library_changed or mirror_changed:
                        changed_count += 1
                else:
                    rejected_count += 1
            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                rejected_count += 1

        cleanup_empty_download_directories(active_staging_dir)

        if rejected_count:
            self._process_failure(
                item,
                "Rejected or incomplete audio artifacts: "
                f"{rejected_count} of {len(found_files) + len(incomplete_files)}",
            )
            logger.warning(
                "Item %s remains failed after importing %s file(s); %s staged "
                "audio artifact(s) were rejected or incomplete.",
                item.id,
                satisfied_count,
                rejected_count,
            )
            return ProcessedQueueItem(
                changed_file_count=changed_count,
                completed=False,
            )

        verified_present = satisfied_count
        if item.playlist_id == "SATELLITE_ALBUM" and item.id is not None:
            get_request = getattr(
                self.db,
                "get_album_download_request_by_queue_item",
                None,
            )
            if callable(get_request):
                tracker = get_request(item.id)
                if tracker:
                    release_mbid = str(tracker.get("release_mbid") or "")
                    recording_mbids = (
                        self.db.get_album_download_request_recording_mbids(
                            int(tracker["id"])
                        )
                    )
                    get_track_manifest = getattr(
                        self.db,
                        "get_album_download_request_track_manifest",
                        None,
                    )
                    track_manifest = usable_exact_track_manifest((
                        get_track_manifest(int(tracker["id"]))
                        if callable(get_track_manifest)
                        else ()
                    ))
                    inventory = inspect_release_inventory(
                        self.db,
                        release_mbid,
                        track_manifest or recording_mbids,
                        self.music_library_dir,
                    )
                    verified_present = inventory.completed_tracks
                    if not inventory.exact:
                        self._process_failure(
                            item,
                            "MusicBrainz completion check failed: "
                            f"{inventory.completed_tracks} of "
                            f"{inventory.total_tracks} exact release tracks "
                            "are present",
                        )
                        return ProcessedQueueItem(
                            changed_file_count=changed_count,
                            completed=False,
                        )

        completion_detail = (
            f"Imported {satisfied_count} FLAC file(s) into the master library"
        )
        complete_request = getattr(
            self.db,
            "complete_album_download_request",
            None,
        )
        if (
            item.playlist_id == "SATELLITE_ALBUM"
            and item.id is not None
            and callable(complete_request)
            and complete_request(item.id, completion_detail, verified_present)
        ):
            pass
        else:
            self.db.remove_from_queue(item.id)
            self._update_album_request_progress(
                item,
                "success",
                completion_detail,
                verified_present,
            )
        logger.info(
            "Item %s processing complete (%s file(s) imported).",
            item.id,
            satisfied_count,
        )
        return ProcessedQueueItem(
            changed_file_count=changed_count,
            completed=True,
        )


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
        jellyfin_music_library_dir=config.get("jellyfin_music_library_path"),
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
                jellyfin_music_library_dir=config.get(
                    "jellyfin_music_library_path"
                ),
            )

            downloader.run_queue()

    except Exception as e:
        logger.error(f"Downloader error: {e}", exc_info=True)
