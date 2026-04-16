"""
Jellyfin client: pull audio items and playlists from a Jellyfin server into
the local music library and register them in the DAP database.
"""

import os
import logging
from typing import Optional, List, Dict, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from mediafile import MediaFile, UnreadableFileError

from .db_manager import DatabaseManager, Track, Playlist
from .utils import sanitize_path_component, write_mbid_to_file

logger = logging.getLogger(__name__)

LOSSLESS_CODECS = {"flac", "alac", "wav", "ape"}


class JellyfinClient:
    """
    Pulls audio files and playlists from a Jellyfin server.
    """

    def __init__(
        self,
        db: DatabaseManager,
        base_url: str,
        api_key: str,
        user_id: str,
        music_library_path: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ):
        if not base_url or not api_key or not user_id:
            raise ValueError("jellyfin_url, jellyfin_api_key, and jellyfin_user_id are required")

        self.db = db
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.music_library_path = music_library_path
        self.progress_callback = progress_callback

        self.session = requests.Session()
        self.session.headers.update({"X-Emby-Token": api_key, "Accept": "application/json"})
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

        os.makedirs(self.music_library_path, exist_ok=True)

    def _report(self, message: str, detail: Optional[str] = None):
        logger.info(message)
        if self.progress_callback:
            payload = {"message": message}
            if detail is not None:
                payload["detail"] = detail
            self.progress_callback(payload)

    # ---------- HTTP helpers ----------

    def _get_json(self, path: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _stream_to_file(self, path: str, dest_path: str):
        url = f"{self.base_url}{path}"
        with self.session.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            tmp_path = dest_path + ".part"
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, dest_path)

    # ---------- Jellyfin API ----------

    def _list_audio_items(self) -> List[dict]:
        data = self._get_json(
            f"/Users/{self.user_id}/Items",
            params={
                "IncludeItemTypes": "Audio",
                "Recursive": "true",
                "Fields": "MediaStreams,MediaSources,ProviderIds,Path,AlbumArtist",
            },
        )
        return data.get("Items", [])

    def _list_playlists(self) -> List[dict]:
        data = self._get_json(
            f"/Users/{self.user_id}/Items",
            params={"IncludeItemTypes": "Playlist", "Recursive": "true"},
        )
        return data.get("Items", [])

    def _list_playlist_items(self, playlist_id: str) -> List[dict]:
        data = self._get_json(
            f"/Playlists/{playlist_id}/Items",
            params={
                "UserId": self.user_id,
                "Fields": "ProviderIds",
            },
        )
        return data.get("Items", [])

    # ---------- Quality comparison ----------

    @staticmethod
    def _jellyfin_stream(item: dict) -> dict:
        sources = item.get("MediaSources") or []
        if not sources:
            return {}
        streams = sources[0].get("MediaStreams") or []
        for s in streams:
            if s.get("Type") == "Audio":
                return s
        return streams[0] if streams else {}

    @staticmethod
    def _local_stream(file_path: str) -> dict:
        try:
            mf = MediaFile(file_path)
        except (UnreadableFileError, OSError):
            return {}
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        return {
            "Codec": ext or None,
            "BitRate": getattr(mf, "bitrate", None),
            "SampleRate": getattr(mf, "samplerate", None),
            "BitDepth": getattr(mf, "bitdepth", None),
        }

    @classmethod
    def _quality_score(cls, stream: dict) -> tuple:
        codec = (stream.get("Codec") or "").lower()
        lossless = 1 if codec in LOSSLESS_CODECS else 0
        bitrate = stream.get("BitRate") or 0
        sample_rate = stream.get("SampleRate") or 0
        bit_depth = stream.get("BitDepth") or 0
        return (lossless, bit_depth, sample_rate, bitrate)

    def _should_pull(self, jf_item: dict, local_track: Optional[Track]) -> bool:
        if not local_track:
            return True
        if not local_track.local_path or not os.path.exists(local_track.local_path):
            return True
        jf_score = self._quality_score(self._jellyfin_stream(jf_item))
        local_score = self._quality_score(self._local_stream(local_track.local_path))
        return jf_score > local_score

    # ---------- Track registration ----------

    @staticmethod
    def _extract_mbid(jf_item: dict) -> Optional[str]:
        provider_ids = jf_item.get("ProviderIds") or {}
        # Jellyfin capitalizes these; accept both forms.
        for key in ("MusicBrainzTrack", "MusicBrainzRecording", "musicbrainztrack"):
            if provider_ids.get(key):
                return provider_ids[key]
        return None

    @staticmethod
    def _extract_release_mbid(jf_item: dict) -> Optional[str]:
        provider_ids = jf_item.get("ProviderIds") or {}
        for key in ("MusicBrainzAlbum", "MusicBrainzReleaseGroup"):
            if provider_ids.get(key):
                return provider_ids[key]
        return None

    def _destination_path(self, jf_item: dict) -> str:
        artist = sanitize_path_component(
            jf_item.get("AlbumArtist") or jf_item.get("Artists", [""])[0] or "Unknown Artist"
        )
        album = sanitize_path_component(jf_item.get("Album") or "Unknown Album")
        title = sanitize_path_component(jf_item.get("Name") or "Unknown Title")

        container = "flac"
        sources = jf_item.get("MediaSources") or []
        if sources:
            container = (sources[0].get("Container") or "flac").split(",")[0].strip() or "flac"
        filename = f"{title}.{container}"
        return os.path.join(self.music_library_path, artist, album, filename)

    def _pull_item(self, jf_item: dict) -> Optional[Track]:
        mbid = self._extract_mbid(jf_item)
        if not mbid:
            logger.debug(f"Skipping (no MBID): {jf_item.get('Name')}")
            return None

        existing = self.db.get_track_by_mbid(mbid)
        if not self._should_pull(jf_item, existing):
            logger.debug(f"Skipping (local quality >= Jellyfin): {jf_item.get('Name')}")
            return existing

        dest_path = self._destination_path(jf_item)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        self._report(
            f"Pulling: {jf_item.get('Name')}",
            detail=f"{jf_item.get('AlbumArtist') or ''} — {jf_item.get('Album') or ''}",
        )
        self._stream_to_file(f"/Items/{jf_item['Id']}/Download", dest_path)

        # Best-effort MBID tag write in case Jellyfin stripped it on export.
        write_mbid_to_file(dest_path, mbid)

        track = Track(
            mbid=mbid,
            title=jf_item.get("Name") or "Unknown Title",
            artist=jf_item.get("AlbumArtist")
            or (jf_item.get("Artists", [""])[0] if jf_item.get("Artists") else "Unknown Artist"),
            album=jf_item.get("Album"),
            local_path=dest_path,
            release_mbid=self._extract_release_mbid(jf_item),
            track_number=jf_item.get("IndexNumber") or 0,
            disc_number=jf_item.get("ParentIndexNumber") or 1,
        )
        self.db.add_or_update_track(track)
        return track

    # ---------- Public surface ----------

    def pull_all(self, mirror_playlists: bool = True) -> dict:
        """Pull every audio item and (optionally) mirror all playlists.
        Returns a summary dict."""
        self._report("Listing Jellyfin audio items...")
        items = self._list_audio_items()
        self._report(f"Found {len(items)} audio items on Jellyfin")

        pulled = 0
        skipped = 0
        failed = 0
        mbid_by_jellyfin_id: Dict[str, str] = {}

        for i, item in enumerate(items, 1):
            self._report(
                f"[{i}/{len(items)}] {item.get('Name')}",
                detail=item.get("AlbumArtist") or "",
            )
            try:
                track = self._pull_item(item)
                if track is None:
                    skipped += 1
                else:
                    pulled += 1
                    mbid_by_jellyfin_id[item["Id"]] = track.mbid
            except requests.HTTPError as e:
                logger.error(f"Jellyfin HTTP error for {item.get('Name')}: {e}")
                failed += 1
            except OSError as e:
                logger.error(f"Filesystem error for {item.get('Name')}: {e}")
                failed += 1

        playlists_mirrored = 0
        if mirror_playlists:
            playlists_mirrored = self._mirror_playlists(mbid_by_jellyfin_id)

        summary = {
            "items_seen": len(items),
            "pulled": pulled,
            "skipped": skipped,
            "failed": failed,
            "playlists_mirrored": playlists_mirrored,
        }
        self._report(
            f"Jellyfin pull complete: {pulled} pulled, {skipped} skipped, "
            f"{failed} failed, {playlists_mirrored} playlists"
        )
        return summary

    def _mirror_playlists(self, mbid_by_jellyfin_id: Dict[str, str]) -> int:
        playlists = self._list_playlists()
        self._report(f"Mirroring {len(playlists)} Jellyfin playlists")
        count = 0
        for pl in playlists:
            pl_id = pl.get("Id")
            if not pl_id:
                continue
            name = pl.get("Name") or "Jellyfin Playlist"
            db_playlist_id = f"jf:{pl_id}"
            self.db.add_or_update_playlist(
                Playlist(playlist_id=db_playlist_id, name=name, spotify_url="")
            )
            try:
                items = self._list_playlist_items(pl_id)
            except requests.HTTPError as e:
                logger.warning(f"Could not fetch items for playlist {name}: {e}")
                continue
            for order, entry in enumerate(items):
                mbid = self._extract_mbid(entry) or mbid_by_jellyfin_id.get(entry.get("Id"))
                if not mbid:
                    continue
                self.db.link_track_to_playlist(db_playlist_id, mbid, order)
            count += 1
        return count


def main_run_jellyfin_pull(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """CLI/web entry point. Returns the pull summary dict."""
    client = JellyfinClient(
        db=db,
        base_url=config["jellyfin_url"],
        api_key=config["jellyfin_api_key"],
        user_id=config["jellyfin_user_id"],
        music_library_path=config["music_library_path"],
        progress_callback=progress_callback,
    )
    return client.pull_all()
