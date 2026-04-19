
import os
import logging
from mediafile import MediaFile, UnreadableFileError
from typing import Optional, Tuple

from .db_manager import DatabaseManager, Track
from .config_manager import get_config
from .utils import find_mbid_by_fingerprint, write_mbid_to_file
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
                    self._process_file(os.path.join(root, file))

    def _fetch_release_info_from_api(
        self, recording_mbid: str, album_name_hint: str
    ) -> Tuple[Optional[str], int]:
        try:
            result = mb.get_recording_by_id(recording_mbid, includes=["releases"])
            if "recording" not in result or "release-list" not in result["recording"]:
                return None, 0

            releases = result["recording"]["release-list"]
            target = None
            clean_hint = album_name_hint.strip().lower()

            for rel in releases:
                if rel.get("title", "").strip().lower() == clean_hint:
                    target = rel
                    break
            if not target:
                for rel in releases:
                    if rel.get("status", "").lower() == "official":
                        target = rel
                        break
            if not target and releases:
                target = releases[0]
            if not target:
                return None, 0

            details = mb.get_release_by_id(target["id"], includes=["media"])
            total = 0
            if "release" in details and "medium-list" in details["release"]:
                for m in details["release"]["medium-list"]:
                    total += int(m.get("track-count", 0))
            return target["id"], total
        except Exception as e:
            logger.warning(f"MB lookup failed for {recording_mbid}: {e}")
            return None, 0

    def _process_file(self, file_path: str):
        if self.db.get_track_by_path(file_path):
            return "skipped"

        try:
            f = MediaFile(file_path)
        except (UnreadableFileError, OSError) as e:
            logger.debug(f"Skipping unreadable file {file_path}: {e}")
            return "skipped"

        mbid = f.mb_trackid
        if not mbid:
            if self._run_picard_tagger(file_path):
                try:
                    f = MediaFile(file_path)
                    mbid = f.mb_trackid
                except (UnreadableFileError, OSError) as e:
                    logger.debug(f"Skipping after picard re-read {file_path}: {e}")
                    return "skipped"

        if mbid:
            # API Fallback
            if (not f.mb_albumid) or (
                not f.tracktotal and f.mb_albumid not in self.resolved_albums
            ):
                rid, total = self._fetch_release_info_from_api(
                    mbid, f.album or "Unknown"
                )
                mod = False
                if rid:
                    f.mb_albumid = rid
                    mod = True
                if total:
                    f.tracktotal = total
                    mod = True
                if mod:
                    try:
                        f.save()
                    except (UnreadableFileError, OSError) as e:
                        logger.warning(f"Could not save tags to {file_path}: {e}")

            # Update DB Cache
            if f.mb_albumid and f.tracktotal:
                self.db.update_album_metadata(f.mb_albumid, f.album, f.tracktotal)
                self.resolved_albums.add(f.mb_albumid)

            # An embedded MBID means the file has already been identified by
            # MusicBrainz (Picard or a prior auto-tag run), so treat it as a
            # green match without re-fingerprinting. Score stays None to
            # distinguish "trust the tag" from "scored by AcoustID".
            track = Track(
                mbid=mbid,
                title=f.title or "Unknown",
                artist=f.artist or "Unknown",
                album=f.album or "Unknown",
                local_path=file_path,
                release_mbid=f.mb_albumid,
                track_number=f.track or 0,
                disc_number=f.disc or 1,
                tag_tier="green",
            )

            ext = self.db.get_track_by_mbid(mbid)
            if ext and ext.local_path:
                self.db.log_duplicate(mbid, ext.local_path)
                self.db.log_duplicate(mbid, file_path)
                return "skipped"

            self.db.add_or_update_track(track)
            return "processed"
        return "skipped"

    def _run_picard_tagger(self, file_path: str):
        return find_mbid_by_fingerprint(file_path)


def main_scan_library(db, config):
    LibraryScanner(db).scan_library(config.get("music_library_path"))
