"""
Lidarr sidecar client (master-only).

DAPManager talks to a user-hosted Lidarr instance over its v1 REST API.
Lidarr owns indexers, download clients, quality profiles, and the
upgrade-monitor loop; we just enqueue artists/albums and poll for
completion. This keeps the "download from everywhere" surface out of
our codebase — users configure indexers in Lidarr's UI, and we reuse
whatever they've set up.

Deployment: Lidarr runs alongside the *master* only. Satellite devices
do not download music themselves — they ask the master to fetch and tag
it, and then pull the finished file from the master's library. Callers
on satellite devices should never instantiate this client; use the
master-facing "request download" endpoint instead.

Quality policy: we always add new artists/albums under a FLAC-preferring
profile. If no FLAC is available at the time, Lidarr will grab the best
it can, and — as long as the profile has "Upgrade Allowed" on — will
replace the file automatically when a FLAC rip appears later.
"""

from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class LidarrError(Exception):
    """Raised when Lidarr rejects a request or is unreachable."""


class LidarrClient:
    """
    Thin wrapper over the Lidarr v1 REST API.

    All endpoints live under ``{base_url}/api/v1/`` and auth is a single
    ``X-Api-Key`` header. We deliberately don't try to model every
    Lidarr concept — just what DAPManager needs to queue and observe
    downloads.
    """

    API_PREFIX = "/api/v1"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
    ):
        if not base_url or not api_key:
            raise ValueError("Lidarr base_url and api_key are required")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {"X-Api-Key": api_key, "Accept": "application/json"}
        )
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    # ---------- HTTP helpers ----------

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{self.API_PREFIX}{path}"

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            r = self.session.get(self._url(path), params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise LidarrError(f"Lidarr GET {path} failed: {e}") from e
        if r.status_code >= 400:
            raise LidarrError(
                f"Lidarr GET {path} returned {r.status_code}: {r.text[:200]}"
            )
        return r.json()

    def _post(self, path: str, payload: dict) -> Any:
        try:
            r = self.session.post(self._url(path), json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise LidarrError(f"Lidarr POST {path} failed: {e}") from e
        if r.status_code >= 400:
            raise LidarrError(
                f"Lidarr POST {path} returned {r.status_code}: {r.text[:200]}"
            )
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # ---------- System / config ----------

    def ping(self) -> bool:
        """Return True if Lidarr answers and our API key works."""
        try:
            self._get("/system/status")
            return True
        except LidarrError:
            return False

    def get_quality_profiles(self) -> List[dict]:
        return self._get("/qualityprofile")

    def get_root_folders(self) -> List[dict]:
        return self._get("/rootfolder")

    def find_flac_quality_profile_id(self) -> Optional[int]:
        """Best-effort: pick the quality profile most likely to prefer FLAC.

        Matches a profile name containing 'flac' (case-insensitive) first,
        then falls back to the first profile that lists FLAC as an allowed
        quality. Returns ``None`` if nothing obvious fits — callers should
        ask the user to configure ``lidarr_quality_profile_id`` explicitly.
        """
        profiles = self.get_quality_profiles()
        for p in profiles:
            if "flac" in (p.get("name") or "").lower():
                return p.get("id")
        for p in profiles:
            for item in p.get("items") or []:
                quality = item.get("quality") or {}
                if "flac" in (quality.get("name") or "").lower() and item.get("allowed"):
                    return p.get("id")
        return None

    # ---------- Lookup ----------

    def lookup_artist(self, term: str) -> List[dict]:
        """Search MusicBrainz via Lidarr. Accepts plain text or ``lidarr:mbid``."""
        return self._get("/artist/lookup", params={"term": term})

    def lookup_album(self, term: str) -> List[dict]:
        return self._get("/album/lookup", params={"term": term})

    # ---------- State queries ----------

    def get_artist_by_mbid(self, mbid: str) -> Optional[dict]:
        results = self._get("/artist", params={"mbId": mbid})
        if isinstance(results, list):
            return results[0] if results else None
        return results

    def get_album_by_mbid(self, mbid: str) -> Optional[dict]:
        """Lidarr uses ``foreignAlbumId`` for release-group MBIDs."""
        results = self._get("/album", params={"foreignAlbumId": mbid})
        if isinstance(results, list):
            return results[0] if results else None
        return results

    def get_queue(self) -> List[dict]:
        data = self._get("/queue")
        if isinstance(data, dict) and "records" in data:
            return data["records"]
        return data or []

    def get_history_for_album(self, album_id: int, page_size: int = 50) -> List[dict]:
        data = self._get(
            "/history",
            params={"albumId": album_id, "pageSize": page_size},
        )
        if isinstance(data, dict) and "records" in data:
            return data["records"]
        return data or []

    def get_wanted_missing(
        self, page: int = 1, page_size: int = 50
    ) -> List[dict]:
        """Albums Lidarr is tracking but hasn't grabbed yet.

        The release watcher polls this and routes each record through
        sldl. Returns the ``records`` list from Lidarr's paginated
        response; sorted newest release first.
        """
        data = self._get(
            "/wanted/missing",
            params={
                "page": page,
                "pageSize": page_size,
                "sortKey": "releaseDate",
                "sortDirection": "descending",
                "includeArtist": True,
            },
        )
        if isinstance(data, dict) and "records" in data:
            return data["records"]
        return data or []

    # ---------- Monitoring / enqueue ----------

    def add_artist(
        self,
        artist_lookup: dict,
        quality_profile_id: int,
        root_folder_path: str,
        metadata_profile_id: Optional[int] = None,
        monitor: str = "all",
        search_for_missing: bool = True,
    ) -> dict:
        """Add an artist from a lookup result and (optionally) kick off a search.

        ``artist_lookup`` is an item out of :meth:`lookup_artist` — we
        forward its Lidarr-flavoured fields verbatim and layer our
        profile/root-folder/monitoring choices on top.
        """
        payload = dict(artist_lookup)
        payload.update(
            {
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder_path,
                "monitored": True,
                "addOptions": {
                    "monitor": monitor,
                    "searchForMissingAlbums": bool(search_for_missing),
                },
            }
        )
        if metadata_profile_id is not None:
            payload["metadataProfileId"] = metadata_profile_id
        return self._post("/artist", payload)

    def search_album(self, album_id: int) -> dict:
        """Trigger an indexer/search sweep for a single album."""
        return self._post(
            "/command",
            {"name": "AlbumSearch", "albumIds": [album_id]},
        )

    def search_artist(self, artist_id: int) -> dict:
        return self._post(
            "/command",
            {"name": "ArtistSearch", "artistId": artist_id},
        )

    # ---------- High-level helpers ----------

    def ensure_album_monitored(
        self,
        release_mbid: str,
        quality_profile_id: int,
        root_folder_path: str,
        metadata_profile_id: Optional[int] = None,
    ) -> Optional[dict]:
        """Make sure Lidarr is tracking this release and searching for it.

        Lidarr organises everything under artists, so we look up the
        album, find its parent artist, add the artist (with the album
        monitored) if it's not already present, then trigger an
        ``AlbumSearch`` for the specific release.

        Returns the Lidarr album dict, or ``None`` if the release can't
        be resolved via MusicBrainz.
        """
        candidates = self.lookup_album(f"lidarr:{release_mbid}")
        if not candidates:
            candidates = self.lookup_album(release_mbid)
        if not candidates:
            logger.info("Lidarr lookup found no album for %s", release_mbid)
            return None

        album = candidates[0]
        artist_stub = album.get("artist") or {}
        artist_mbid = artist_stub.get("foreignArtistId")

        existing_artist = None
        if artist_mbid:
            existing_artist = self.get_artist_by_mbid(artist_mbid)

        if not existing_artist and artist_mbid:
            artist_hits = self.lookup_artist(f"lidarr:{artist_mbid}")
            if artist_hits:
                self.add_artist(
                    artist_hits[0],
                    quality_profile_id=quality_profile_id,
                    root_folder_path=root_folder_path,
                    metadata_profile_id=metadata_profile_id,
                    monitor="all",
                    search_for_missing=False,
                )

        stored = self.get_album_by_mbid(release_mbid)
        if stored and stored.get("id"):
            self.search_album(stored["id"])
            return stored
        return album
