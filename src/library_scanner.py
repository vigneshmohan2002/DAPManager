
import logging
import os
from mediafile import MediaFile, UnreadableFileError
from typing import Any, Iterable, Literal, Mapping, Optional, Protocol, Tuple

from .db_manager import DatabaseManager, Track
from .config_manager import get_config
from .utils import find_mbid_by_fingerprint
from . import musicbrainz_client as mb

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = (
    ".flac",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".wav",
    ".alac",
    ".ape",
)

ScanResult = Literal["processed", "skipped"]


class MediaTags(Protocol):
    """Tag attributes consumed by the scanner.

    ``mediafile.MediaFile`` is deliberately wrapped by this small protocol so
    the scanner's decisions can be tested without coupling them to the
    library's dynamic attribute implementation.
    """

    mb_trackid: Optional[str]
    mb_albumid: Optional[str]
    title: Optional[str]
    artist: Optional[str]
    album: Optional[str]
    track: Optional[int]
    tracktotal: Optional[int]
    disc: Optional[int]

    def save(self) -> None:
        ...


def select_release(
    releases: Iterable[Mapping[str, Any]], album_name_hint: str
) -> Optional[Mapping[str, Any]]:
    """Choose the same MusicBrainz release the legacy scanner preferred."""
    release_list = list(releases)
    clean_hint = album_name_hint.strip().lower()

    for release in release_list:
        if str(release.get("title", "")).strip().lower() == clean_hint:
            return release
    for release in release_list:
        if str(release.get("status", "")).lower() == "official":
            return release
    return release_list[0] if release_list else None


def release_track_total(details: Mapping[str, Any]) -> int:
    """Return the total track count across all media in a release payload."""
    release = details.get("release")
    if not isinstance(release, Mapping):
        return 0
    media = release.get("medium-list")
    if not isinstance(media, list):
        return 0
    return sum(
        int(medium.get("track-count", 0))
        for medium in media
        if isinstance(medium, Mapping)
    )


def track_from_media(file_path: str, media: MediaTags, mbid: str) -> Track:
    """Build the database value object for an identified on-disk file."""
    return Track(
        mbid=mbid,
        title=media.title or "Unknown",
        artist=media.artist or "Unknown",
        album=media.album or "Unknown",
        local_path=file_path,
        release_mbid=media.mb_albumid,
        track_number=media.track or 0,
        disc_number=media.disc or 1,
        # An embedded MBID is trusted as an existing MusicBrainz match.  A
        # None score continues to distinguish it from an AcoustID score.
        tag_tier="green",
    )


class LibraryScanner:
    def __init__(self, db: DatabaseManager, picard_path: Optional[str] = None):
        self.db = db
        self.resolved_albums = set()
        if picard_path:
            self.picard_path = picard_path
        else:
            config = get_config()
            self.picard_path = config.picard_path

    def scan_library(self, library_path: str):
        logger.info(f"Starting scan: {library_path}")
        if not os.path.exists(library_path):
            return

        for root, _, files in os.walk(library_path):
            for file in files:
                if file.lower().endswith(SUPPORTED_EXTENSIONS):
                    self.process_file(os.path.join(root, file))

    def _fetch_release_info_from_api(
        self, recording_mbid: str, album_name_hint: str
    ) -> Tuple[Optional[str], int]:
        try:
            result = mb.get_recording_by_id(recording_mbid, includes=["releases"])
            if "recording" not in result or "release-list" not in result["recording"]:
                return None, 0

            releases = result["recording"]["release-list"]
            target = select_release(releases, album_name_hint)
            if not target:
                return None, 0

            details = mb.get_release_by_id(target["id"], includes=["media"])
            return str(target["id"]), release_track_total(details)
        except Exception as e:
            logger.warning(f"MB lookup failed for {recording_mbid}: {e}")
            return None, 0

    @staticmethod
    def _read_media_file(file_path: str) -> Optional[MediaTags]:
        try:
            return MediaFile(file_path)
        except (UnreadableFileError, OSError) as e:
            logger.debug(f"Skipping unreadable file {file_path}: {e}")
            return None

    def _read_identified_media(self, file_path: str) -> Optional[MediaTags]:
        media = self._read_media_file(file_path)
        if media is None or media.mb_trackid:
            return media
        if not self._run_picard_tagger(file_path):
            return media

        refreshed = self._read_media_file(file_path)
        if refreshed is None:
            logger.debug(f"Skipping after picard re-read {file_path}")
        return refreshed

    def _enrich_release_metadata(
        self, file_path: str, media: MediaTags, mbid: str
    ) -> None:
        release_mbid = media.mb_albumid
        needs_release = not release_mbid
        needs_total = not media.tracktotal and release_mbid not in self.resolved_albums
        if not needs_release and not needs_total:
            return

        release_id, total = self._fetch_release_info_from_api(
            mbid, media.album or "Unknown"
        )
        modified = False
        if release_id:
            media.mb_albumid = release_id
            modified = True
        if total:
            media.tracktotal = total
            modified = True
        if not modified:
            return

        try:
            media.save()
        except (UnreadableFileError, OSError) as e:
            logger.warning(f"Could not save tags to {file_path}: {e}")

    def _cache_album_metadata(self, media: MediaTags) -> None:
        if not media.mb_albumid or not media.tracktotal:
            return
        self.db.update_album_metadata(media.mb_albumid, media.album, media.tracktotal)
        self.resolved_albums.add(media.mb_albumid)

    def _is_duplicate(self, track: Track) -> bool:
        existing = self.db.get_track_by_mbid(track.mbid)
        if not existing or not existing.local_path:
            return False
        self.db.log_duplicate(track.mbid, existing.local_path)
        self.db.log_duplicate(track.mbid, track.local_path)
        return True

    def process_file(self, file_path: str) -> ScanResult:
        """Read, enrich, de-duplicate, and persist one library file."""
        if self.db.get_track_by_path(file_path):
            return "skipped"

        media = self._read_identified_media(file_path)
        if media is None or not media.mb_trackid:
            return "skipped"

        mbid = media.mb_trackid
        self._enrich_release_metadata(file_path, media, mbid)
        self._cache_album_metadata(media)

        track = track_from_media(file_path, media, mbid)
        if self._is_duplicate(track):
            return "skipped"

        self.db.add_or_update_track(track)
        return "processed"

    def _process_file(self, file_path: str) -> ScanResult:
        """Compatibility wrapper for callers that used the private method."""
        return self.process_file(file_path)

    def _run_picard_tagger(self, file_path: str):
        return find_mbid_by_fingerprint(file_path)


def main_scan_library(db, config):
    LibraryScanner(db).scan_library(config.get("music_library_path"))
