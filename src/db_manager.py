
"""
Database management for DAP Manager.
"""

import sqlite3
import os
import uuid
import logging
from dataclasses import dataclass
from typing import Any, Collection, List, Mapping, Optional, Set, Tuple, TypedDict
from datetime import datetime

from src.db_schema import create_tables, migrate_schema
from src.db_repositories import (
    AlbumDownloadRequestRepository,
    AlbumMaintenanceRepository,
    ContributionRepository,
    DownloadRepository,
    InventoryRepository,
    LibraryRepository,
    ListeningRepository,
    MetadataRepository,
    PlaylistRepository,
    SyncRepository,
)

logger = logging.getLogger(__name__)

# The default sqlite3 datetime adapter is deprecated in Python 3.12 and
# scheduled for removal. Register an explicit ISO-format adapter so writes
# from `datetime.now()` keep working on 3.13+.
sqlite3.register_adapter(datetime, lambda d: d.isoformat())


class LocalAlbumSnapshot(TypedDict):
    """Local identity and occupied disc/track positions for one release."""

    artist: str
    album: Optional[str]
    positions: Set[Tuple[Optional[int], Optional[int]]]


class AlbumGroupTrackRow(TypedDict):
    mbid: str
    title: str
    artist: str
    album: str
    track_number: Optional[int]
    disc_number: Optional[int]
    release_mbid: Optional[str]
    album_id: str


class SplitAlbumTrackRow(AlbumGroupTrackRow):
    local_path: Optional[str]


class AlbumTagRow(TypedDict):
    mbid: str
    album: Optional[str]
    artist: str
    release_mbid: Optional[str]
    local_path: str


class TrackPathRow(TypedDict):
    mbid: str
    local_path: Optional[str]


class AlbumGroupReassignment(TypedDict):
    matched: int
    moved: int
    tracks: List[TrackPathRow]


@dataclass
class Track:
    """Represents a single track in the master library."""

    mbid: str
    title: str
    artist: str
    album: Optional[str] = None
    isrc: Optional[str] = None
    local_path: Optional[str] = None
    dap_path: Optional[str] = None
    synced_to_dap: bool = False
    # New Fields for Completer
    release_mbid: Optional[str] = None
    track_number: int = 0
    disc_number: int = 1
    # Auto-tag confidence recorded at write time. tier ∈ {green, yellow, red}
    # or None for tracks that were never auto-tagged (legacy / manual import).
    tag_tier: Optional[str] = None
    tag_score: Optional[float] = None
    album_artist: Optional[str] = None

    @property
    def safe_artist(self):
        import re

        if not self.artist or self.artist == "Unknown Artist":
            return "Unknown Artist"
        return re.sub(r'[\\/*?:"<>|]', "_", self.artist)

    @property
    def safe_title(self):
        import re

        if not self.title or self.title == "Unknown Title":
            return "Unknown Title"
        return re.sub(r'[\\/*?:"<>|]', "_", self.title)


@dataclass
class Playlist:
    playlist_id: str
    name: str
    spotify_url: str
    smart_rules: Optional[str] = None


@dataclass
class DownloadItem:
    search_query: str
    playlist_id: str
    mbid_guess: str
    id: Optional[int] = None
    status: str = "pending"
    last_attempt: Optional[datetime] = None
    attempt_count: int = 0
    max_attempts: int = 3
    next_attempt_at: Optional[datetime] = None
    claim_owner: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    claim_heartbeat_at: Optional[datetime] = None
    is_paused: bool = False
    is_quarantined: bool = False
    last_error: Optional[str] = None


class DatabaseManager:
    """Handles all database operations for DAP Manager."""

    def __init__(self, db_path: str = "dap_library.db"):
        self.db_path = db_path
        self.conn = None
        self._connect()
        self._create_tables()
        self._migrate_schema()

    def _connect(self):
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON;")
            # Wait up to 5s for a write lock instead of failing immediately with
            # "database is locked". The synchronous maintenance endpoints
            # (consolidate/retag) can hold a write transaction while a
            # background scan/sync runs; without this they collide. WAL is
            # avoided deliberately — it misbehaves on the Windows Docker
            # bind-mount that backs /data.
            self.conn.execute("PRAGMA busy_timeout = 5000;")
            self._initialize_repositories()
            logger.info(f"Connected to database at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    def _initialize_repositories(self) -> None:
        """Bind internal repositories to the currently active connection."""
        self._library_repository = LibraryRepository(self.conn)
        self._playlist_repository = PlaylistRepository(self.conn)
        self._sync_repository = SyncRepository(self.conn)
        self._contribution_repository = ContributionRepository(self.conn)
        self._listening_repository = ListeningRepository(self.conn)
        self._metadata_repository = MetadataRepository(self.conn)
        self._download_repository = DownloadRepository(self.conn)
        self._inventory_repository = InventoryRepository(self.conn)
        self._album_maintenance_repository = AlbumMaintenanceRepository(
            self.conn
        )
        self._album_download_request_repository = (
            AlbumDownloadRequestRepository(self.conn)
        )

    def _create_tables(self):
        if not self.conn:
            self._connect()
        create_tables(self.conn, logger)

    def _migrate_schema(self):
        migrate_schema(self.conn, logger)

    # --- Track Methods ---
    def add_or_update_track(self, track: Track):
        # Normalize path to ensure consistency (force forward slashes)
        if track.local_path:
            track.local_path = os.path.normpath(track.local_path).replace("\\", "/")
        self._library_repository.add_or_update_track(track, logger)

    def set_track_tag_tier(
        self, mbid: str, tier: Optional[str], score: Optional[float]
    ) -> bool:
        """Record the auto-tag confidence tier/score for an existing track.

        Separate from ``add_or_update_track`` so the downloader can stamp
        the tier *after* the scanner has inserted the row from the file's
        on-disk tags. Returns True if a row was updated.
        """
        if not mbid:
            return False
        return self._library_repository.set_track_tag_tier(
            mbid,
            tier,
            score,
        )

    def set_track_album_artist(
        self,
        mbid: str,
        album_artist: str,
    ) -> bool:
        """Persist an embedded album-artist tag for an existing live track."""
        if not mbid or not album_artist.strip():
            return False
        return self._library_repository.set_track_album_artist(
            mbid,
            album_artist.strip(),
        )

    def get_tracks_needing_tag_review(self) -> List[Track]:
        """Tracks whose last auto-tag was yellow or red — user must review.

        Excludes soft-deleted rows and tracks with no local file.
        """
        return self._library_repository.get_tracks_needing_tag_review(
            lambda row: self._row_to_track(row)
        )

    def get_track_by_mbid(self, mbid: str) -> Optional[Track]:
        try:
            return self._row_to_track(
                self._library_repository.fetch_track_by_mbid(mbid)
            )
        except sqlite3.Error:
            return None

    def get_track_by_path(self, local_path: str) -> Optional[Track]:
        try:
            return self._row_to_track(
                self._library_repository.fetch_track_by_path(local_path)
            )
        except sqlite3.Error:
            return None

    def find_unlinked_tracks_by_isrc(self, isrc: str) -> List[str]:
        """MBIDs of non-deleted tracks with this ISRC and no local file yet.

        Multi-row results are left to the caller to treat as ambiguous —
        the linker skips those rather than guessing.
        """
        if not isrc:
            return []
        return self._library_repository.find_unlinked_tracks_by_isrc(isrc)

    def find_unlinked_tracks_by_artist_title(
        self, artist: str, title: str
    ) -> List[str]:
        """Case-insensitive exact match on (artist, title) among unlinked
        non-deleted rows. Used as a fallback when MBID / ISRC aren't
        available on the file's tags."""
        if not artist or not title:
            return []
        return self._library_repository.find_unlinked_tracks_by_artist_title(
            artist,
            title,
        )

    def find_unlinked_tracks_by_artist_title_album(
        self, artist: str, title: str, album: str
    ) -> List[str]:
        """Album-aware disambiguation step: only used when (artist, title)
        alone returned multiple candidates."""
        if not artist or not title or not album:
            return []
        return (
            self._library_repository
            .find_unlinked_tracks_by_artist_title_album(
                artist,
                title,
                album,
            )
        )

    def is_track_unlinked_and_live(self, mbid: str) -> bool:
        """Return whether a catalog row may safely claim a local file."""
        return self._library_repository.is_track_unlinked_and_live(mbid)

    # --- Album Methods ---
    def update_album_metadata(
        self, release_mbid: str, album_title: str, total_tracks: int
    ):
        self._library_repository.update_album_metadata(
            release_mbid,
            album_title,
            total_tracks,
            logger,
        )

    def get_incomplete_albums(self) -> List[dict]:
        return self._library_repository.get_incomplete_albums()

    def get_tracks_missing_album_info(self) -> List[Track]:
        """Find tracks that have a recording MBID but no release_mbid or no album entry."""
        return self._library_repository.get_tracks_missing_album_info(
            lambda row: self._row_to_track(row),
            logger,
        )

    def get_local_album_snapshot(
        self, release_mbid: str
    ) -> Optional[LocalAlbumSnapshot]:
        """Return album identity and occupied positions for local tracks.

        This deliberately mirrors the album completer's historic two-query
        read, including its treatment of soft-deleted rows.
        """
        return self._library_repository.get_local_album_snapshot(release_mbid)

    def update_track_release_mbid(self, mbid: str, release_mbid: str) -> int:
        """Assign one track to a release and preserve the legacy commit point."""
        repository = getattr(self, "_library_repository", None)
        if repository is None:
            repository = LibraryRepository(self.conn)
        return repository.update_track_release_mbid(mbid, release_mbid)

    def get_album_track_counts(self) -> List[dict]:
        """Get all albums with their local track count vs expected total."""
        return self._library_repository.get_album_track_counts(logger)

    @staticmethod
    def _normalize_query(s: str) -> str:
        """Lowercase + collapse whitespace so callers using slightly different
        formatting ('Artist - Title' vs 'artist  -  title') don't double-queue."""
        if not s:
            return ""
        return " ".join(s.lower().split())

    def list_albums(self) -> List[dict]:
        """Distinct albums implied by the tracks table.

        Groups by ``release_mbid`` when present, falling back to an
        ``album|artist`` synthetic key so tracks without MBIDs still
        group correctly. The ``cover_path`` is one of the album's track
        file paths — the web layer uses it to extract embedded art.
        """
        return self._library_repository.list_albums()

    def list_all_tracks(self) -> List[dict]:
        """Every non-deleted track in the library, flat.

        Returns ``local_path`` and ``dap_path`` so the caller can compute
        playback availability (local disk vs connected drive vs remote
        stream). The API layer does the filtering — the DB method stays
        purely about what's recorded.
        """
        return self._library_repository.list_all_tracks()

    def set_track_liked(self, mbid: str, liked: bool) -> Optional[bool]:
        """Flip ``tracks.is_liked`` for a single row.

        Returns the new state on success, or None if the track doesn't
        exist (or is soft-deleted — liking an orphan row would create a
        ghost entry in the Liked Songs smart playlist). Bumps
        ``updated_at`` so the change rides the catalog-sync delta to
        satellites.
        """
        if not mbid:
            return None
        return self._library_repository.set_track_liked(mbid, liked)

    def get_liked_tracks_summary(self, limit: int = 6) -> dict:
        """Return the live liked-track count and recent preview rows."""
        return self._library_repository.get_liked_tracks_summary(limit)

    # The Liked Songs smart playlist uses a reserved, deterministic id so
    # both master and satellite converge on the same row after a sync
    # tick instead of each minting a fresh UUID. Anywhere the rest of the
    # code refers to "the Liked Songs playlist", it uses this constant.
    LIKED_SONGS_PLAYLIST_ID = "liked_songs"
    LIKED_SONGS_PLAYLIST_NAME = "Liked Songs"

    def ensure_liked_songs_playlist(self) -> str:
        """Idempotently create the Liked Songs smart playlist and return
        its id. Auto-called on the first heart-toggle so users who never
        like a track don't get an empty playlist cluttering their sidebar.
        """
        return self._playlist_repository.ensure_liked_songs_playlist(
            self.LIKED_SONGS_PLAYLIST_ID,
            self.LIKED_SONGS_PLAYLIST_NAME,
        )

    def get_track_sources(self, mbid: str) -> Optional[dict]:
        """Return both candidate on-disk paths for a track, or None if
        the row doesn't exist / is soft-deleted.

        Stream resolution prefers ``local_path`` then ``dap_path``;
        anything missing falls through to the master proxy.
        """
        if not mbid:
            return None
        return self._library_repository.get_track_sources(mbid)

    def get_live_track_identity(self, mbid: str) -> Optional[dict]:
        """Return display metadata for a live track, or None if absent."""
        return self._library_repository.get_live_track_identity(mbid)

    def list_artists(self) -> List[dict]:
        """Distinct primary album artists with album and track counts.

        Uses embedded album-artist tags, so featured track credits do not
        become separate Artists-tab rows. Albums without one unambiguous
        album artist are not assigned to an invented owner.
        """
        return self._library_repository.list_artists()

    def get_album_cover_path(self, album_id: str) -> Optional[str]:
        """Return one track file path for an album id — used to extract
        embedded cover art. ``album_id`` is whatever ``list_albums``
        returned (a release_mbid or ``album|artist`` synthetic)."""
        if not album_id:
            return None
        return self._library_repository.get_album_cover_path(album_id)

    def list_album_tracks(self, album_id: str) -> List[dict]:
        """Ordered tracks belonging to the album identified by ``album_id``.

        ``album_id`` matches the id returned by ``list_albums`` — either a
        release_mbid or an ``album|artist`` synthetic. Ordered by disc
        then track number, with title as a final tiebreaker so tracks
        without numbers still sort predictably.
        """
        if not album_id:
            return []
        return self._library_repository.list_album_tracks(album_id)

    def get_track_local_path(self, mbid: str) -> Optional[str]:
        """Resolve an mbid to its local file path, or None if missing."""
        if not mbid:
            return None
        return self._library_repository.get_track_local_path(mbid)

    def find_local_tracks_by_identity(
        self,
        *,
        mbid: Optional[str] = None,
        isrc: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        album: Optional[str] = None,
    ) -> List[dict]:
        """Return safe local candidates for a possibly mismatched track id.

        Contribution offers originate on another device, whose tagger may
        resolve a different recording MBID from the master's downloader.  An
        MBID-only lookup therefore produces false misses and needless file
        uploads.  Match progressively by recording MBID, ISRC, exact
        artist/title/album, then artist/title, with both metadata fallbacks
        accepted *only when they are unambiguous*.

        Results are ordered by match confidence and de-duplicated by path.
        Fuzzy matching is deliberately excluded: asking for an upload is
        preferable to treating a different recording as the same track.
        """
        return self._contribution_repository.find_local_tracks_by_identity(
            mbid=mbid,
            isrc=isrc,
            artist=artist,
            title=title,
            album=album,
        )

    def has_queued_mbid(self, mbid: str) -> bool:
        """True if any row in download_queue already targets this MBID,
        regardless of status. Used by the release watcher to avoid
        re-queuing the same album on every tick."""
        if not mbid:
            return False
        return self._download_repository.has_queued_mbid(mbid)

    def get_active_download_id(
        self, mbid: Optional[str], search_query: str
    ) -> Optional[int]:
        """Return the id of a pending/failed queue row for this track, or
        ``None``. Matches on ``mbid_guess`` first, then a normalized
        ``search_query``. Lets a contribution attach to an in-flight download
        instead of double-queuing (and being mistaken for a failed attempt)."""
        return self._download_repository.get_active_download_id(
            mbid,
            search_query,
            self._normalize_query,
        )

    def is_download_queued(self, search_query: str) -> bool:
        """Return True if a normalized form of ``search_query`` is already
        pending or failed in the queue."""
        return self._download_repository.is_queued(
            search_query,
            self._normalize_query,
        )

    # --- Contributions (master side) ---
    def create_contribution(
        self,
        *,
        device_id: Optional[str],
        mbid: Optional[str],
        isrc: Optional[str],
        artist: Optional[str],
        title: Optional[str],
        album: Optional[str],
        target_quality: Optional[str],
        status: str = "attempting",
        download_id: Optional[int] = None,
        acquired_quality: Optional[str] = None,
    ) -> int:
        return self._contribution_repository.create(
            device_id=device_id,
            mbid=mbid,
            isrc=isrc,
            artist=artist,
            title=title,
            album=album,
            target_quality=target_quality,
            status=status,
            download_id=download_id,
            acquired_quality=acquired_quality,
        )

    def get_contribution(self, contribution_id: int) -> Optional[dict]:
        return self._contribution_repository.get(contribution_id)

    def list_contributions(self, limit: int = 200) -> List[dict]:
        """Recent contributions, newest first — backs the dashboard view."""
        return self._contribution_repository.list(limit)

    def update_contribution(self, contribution_id: int, **fields):
        """Patch a contribution row. Only known columns are written; always
        bumps ``updated_at``."""
        allowed = {
            "status", "acquired_quality", "download_id", "target_quality",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        self._contribution_repository.update(contribution_id, sets)

    # --- Contributed (satellite side) ---
    def upsert_contributed(
        self, mbid: str, contribution_id: Optional[int], status: Optional[str]
    ):
        self._contribution_repository.upsert_contributed(
            mbid, contribution_id, status
        )

    def get_contributed(self, mbid: str) -> Optional[dict]:
        return self._contribution_repository.get_contributed(mbid)

    def get_pending_contributed(self) -> List[dict]:
        """Rows in a non-terminal state — still need polling/upload."""
        return self._contribution_repository.get_pending_contributed()

    def list_contributed(self, limit: int = 200) -> List[dict]:
        """Recent outgoing offers with track labels for the satellite UI."""
        return self._contribution_repository.list_contributed(limit)

    def get_contributable_tracks(self, limit: int = 50) -> List[Track]:
        """Local tracks never offered to the master yet, capped at ``limit``.

        Tracks already in ``contributed`` with a master-side id are excluded —
        new offers come from here, while in-flight ones are driven by
        ``get_pending_contributed``.  A legacy/malformed row without a
        contribution id is intentionally offered again so it cannot strand a
        track forever.
        """
        return self._contribution_repository.get_contributable_tracks(
            limit,
            lambda row: self._row_to_track(row),
        )

    def merge_albums(self, source_mbid: str, target_mbid: str):
        if not source_mbid or not target_mbid:
            return False
        return self._library_repository.merge_albums(
            source_mbid,
            target_mbid,
            logger,
        )

    # --- Playlist / Download / Misc Methods (Abbreviated, assumes exist from prior code) ---
    def add_or_update_playlist(self, playlist: Playlist):
        # ON CONFLICT DO UPDATE (not INSERT OR REPLACE) so the row's identity
        # is preserved — REPLACE would delete + reinsert and cascade-wipe
        # playlist_tracks rows.
        self._playlist_repository.add_or_update(
            playlist.playlist_id,
            playlist.name,
            playlist.spotify_url,
        )

    def _bump_playlist_updated_at(self, playlist_id: str):
        self._playlist_repository.bump_updated_at(playlist_id)

    def create_playlist(self, name: str, smart_rules: Optional[str] = None) -> str:
        """Insert a new playlist with a generated UUID and return it.

        ``smart_rules`` is the JSON-encoded ruleset (already serialized by
        ``smart_playlist.serialize``) or None for a regular static playlist.
        Smart playlists are auto-populated from the ruleset on read; the
        caller doesn't insert membership rows for them.
        """
        if not name or not name.strip():
            raise ValueError("playlist name is required")
        pid = uuid.uuid4().hex
        self._playlist_repository.create(pid, name.strip(), smart_rules)
        return pid

    def update_playlist_smart_rules(
        self, playlist_id: str, smart_rules: Optional[str]
    ) -> bool:
        """Replace the smart_rules JSON for ``playlist_id``. Bumps updated_at
        so the change rides the playlist delta. Returns True iff a row was
        actually updated.
        """
        if not playlist_id:
            return False
        return self._playlist_repository.update_smart_rules(
            playlist_id,
            smart_rules,
        )

    def unlink_track_from_playlist(self, playlist_id: str, track_mbid: str) -> bool:
        """Remove a track from a playlist's membership.

        Returns True when a row was actually deleted. Bumps the playlist's
        ``updated_at`` in that case so the delta feed carries the change —
        matching the ``link_track_to_playlist`` contract.
        """
        if not playlist_id or not track_mbid:
            return False
        return self._playlist_repository.unlink_track(
            playlist_id,
            track_mbid,
            lambda value: self._bump_playlist_updated_at(value),
        )

    def replace_playlist_membership(
        self, playlist_id: str, track_mbids: List[str]
    ) -> int:
        """Rewrite a playlist's membership to exactly ``track_mbids``.

        Order in the input list becomes ``track_order`` (0-indexed).
        Missing/empty mbids are skipped. Unknown mbids are dropped the
        same way ``apply_playlist_row`` drops them — the caller gets a
        count of what actually landed so it can surface partial misses.

        The delete + re-insert run in one transaction and the playlist's
        ``updated_at`` is bumped regardless of whether the set changed,
        since PUT is an explicit edit action from the user.
        """
        if not playlist_id:
            return 0
        return self._playlist_repository.replace_membership(
            playlist_id,
            track_mbids or [],
        )

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """Fetch one playlist by id, excluding soft-deleted rows."""
        if not playlist_id:
            return None
        row = self._playlist_repository.fetch_playlist(playlist_id)
        return self._row_to_playlist(row) if row else None

    def rename_playlist(self, playlist_id: str, name: str) -> bool:
        """Update a playlist's name and bump updated_at. No-op on missing
        id; returns True only when a row actually changed.
        """
        if not playlist_id or not name or not name.strip():
            return False
        return self._playlist_repository.rename(playlist_id, name.strip())

    def queue_download(self, item: DownloadItem) -> int:
        return self._download_repository.queue(
            item.search_query,
            item.playlist_id,
            item.mbid_guess,
            item.status,
        )

    def get_downloads(self, status: str) -> List[DownloadItem]:
        return [
            self._row_to_download_item(row)
            for row in self._download_repository.fetch_by_status(status)
        ]

    def get_download_status(self, download_id: int) -> Optional[str]:
        """Return the queue row's status, or ``None`` if the row is gone
        (removed on success)."""
        return self._download_repository.get_status(download_id)

    def update_download_status(self, item_id: int, status: str):
        self._download_repository.update_status(item_id, status)

    def claim_next_download(
        self,
        owner: str,
        lease_seconds: int = 900,
        now: Optional[datetime] = None,
        *,
        include_item_ids: Optional[Collection[int]] = None,
        exclude_item_ids: Collection[int] = (),
    ) -> Optional[DownloadItem]:
        """Atomically lease one due queue row to ``owner``.

        ``include_item_ids`` supports a deliberately narrow recovery run;
        ``exclude_item_ids`` lets one runner process each row at most once.
        """
        row = self._download_repository.claim_next(
            owner,
            lease_seconds,
            now,
            include_item_ids=include_item_ids,
            exclude_item_ids=exclude_item_ids,
        )
        return self._row_to_download_item(row) if row else None

    def recover_stale_download_claims(
        self,
        now: Optional[datetime] = None,
    ) -> List[int]:
        """Release expired leases and return affected queue IDs."""
        return self._download_repository.recover_stale_claims(now)

    def count_claimable_downloads(
        self,
        now: Optional[datetime] = None,
        *,
        include_item_ids: Optional[Collection[int]] = None,
        exclude_item_ids: Collection[int] = (),
    ) -> int:
        return self._download_repository.count_claimable(
            now,
            include_item_ids=include_item_ids,
            exclude_item_ids=exclude_item_ids,
        )

    def heartbeat_download_claim(
        self,
        item_id: int,
        owner: str,
        lease_seconds: int = 900,
        now: Optional[datetime] = None,
    ) -> bool:
        return self._download_repository.heartbeat_claim(
            item_id,
            owner,
            lease_seconds,
            now,
        )

    def release_download_claim(self, item_id: int, owner: str) -> bool:
        return self._download_repository.release_claim(item_id, owner)

    def fail_download_claim(
        self,
        item_id: int,
        owner: str,
        error_message: str = "",
        *,
        quarantine: bool = False,
        base_delay_seconds: int = 300,
        max_delay_seconds: int = 86400,
        now: Optional[datetime] = None,
    ) -> bool:
        """Persist an owner-fenced failure, backoff, and retry-cap state."""
        return self._download_repository.fail_claim(
            item_id,
            owner,
            error_message,
            quarantine=quarantine,
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
            now=now,
        )

    def complete_download_claim(
        self,
        item_id: int,
        owner: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """Remove completed work only while ``owner`` holds a live lease."""
        return self._download_repository.complete_claim(
            item_id,
            owner,
            now,
        )

    def set_download_retry_limit(
        self,
        item_id: int,
        max_attempts: int,
    ) -> bool:
        return self._download_repository.set_retry_limit(item_id, max_attempts)

    def set_download_paused(self, item_id: int, paused: bool) -> bool:
        return self._download_repository.set_paused(item_id, paused)

    def set_download_quarantined(
        self,
        item_id: int,
        quarantined: bool,
    ) -> bool:
        return self._download_repository.set_quarantined(item_id, quarantined)

    def remove_from_queue(self, item_id: int):
        self._download_repository.remove(item_id)

    def create_album_download_request(
        self,
        *,
        queue_item_id: Optional[int],
        release_mbid: str,
        artist: str,
        title: str,
        track_count: int,
        stage: str = "queued",
        detail: str = "",
        completed_tracks: int = 0,
        recording_mbids: Tuple[str, ...] = (),
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> int:
        return self._album_download_request_repository.create(
            queue_item_id=queue_item_id,
            release_mbid=release_mbid,
            artist=artist,
            title=title,
            track_count=track_count,
            stage=stage,
            detail=detail,
            completed_tracks=completed_tracks,
            recording_mbids=recording_mbids,
            track_manifest=track_manifest,
        )

    def claim_download_and_create_album_request(
        self,
        *,
        queue_item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Tuple[str, ...],
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> Optional[int]:
        return self._album_download_request_repository.claim_queue_and_create(
            queue_item_id=queue_item_id,
            release_mbid=release_mbid,
            search_query=search_query,
            playlist_id=playlist_id,
            artist=artist,
            title=title,
            track_count=track_count,
            detail=detail,
            completed_tracks=completed_tracks,
            recording_mbids=recording_mbids,
            track_manifest=track_manifest,
        )

    def create_download_and_album_request(
        self,
        *,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        artist: str,
        title: str,
        track_count: int,
        detail: str,
        completed_tracks: int,
        recording_mbids: Tuple[str, ...],
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> Tuple[int, int]:
        return self._album_download_request_repository.create_queue_and_request(
            release_mbid=release_mbid,
            search_query=search_query,
            playlist_id=playlist_id,
            artist=artist,
            title=title,
            track_count=track_count,
            detail=detail,
            completed_tracks=completed_tracks,
            recording_mbids=recording_mbids,
            track_manifest=track_manifest,
        )

    def create_download_and_requeue_album_request(
        self,
        *,
        request_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
        detail: str,
        completed_tracks: int,
    ) -> Optional[int]:
        return self._album_download_request_repository.create_queue_and_requeue(
            request_id=request_id,
            release_mbid=release_mbid,
            search_query=search_query,
            playlist_id=playlist_id,
            detail=detail,
            completed_tracks=completed_tracks,
        )

    def get_album_download_request(self, request_id: int):
        return self._album_download_request_repository.get(request_id)

    def get_album_download_request_by_release(self, release_mbid: str):
        return self._album_download_request_repository.get_by_release(
            release_mbid
        )

    def get_album_download_request_by_queue_item(self, queue_item_id: int):
        return self._album_download_request_repository.get_by_queue_item(
            queue_item_id
        )

    def list_active_album_download_requests(self, limit: int = 30):
        return self._album_download_request_repository.list_active(limit)

    def get_album_download_request_recording_mbids(self, request_id: int):
        return (
            self._album_download_request_repository.get_expected_recording_mbids(
                request_id
            )
        )

    def get_album_download_request_track_manifest(self, request_id: int):
        return self._album_download_request_repository.get_expected_track_manifest(
            request_id
        )

    def get_local_release_recordings(self, release_mbid: str):
        return self._album_download_request_repository.get_local_release_recordings(
            release_mbid
        )

    def update_album_download_request_progress(
        self,
        queue_item_id: int,
        stage: str,
        detail: str = "",
        completed_tracks: Optional[int] = None,
    ) -> bool:
        return self._album_download_request_repository.update_by_queue_item(
            queue_item_id,
            stage,
            detail,
            completed_tracks,
        )

    def update_claimed_album_download_request_progress(
        self,
        queue_item_id: int,
        owner: str,
        stage: str,
        detail: str = "",
        completed_tracks: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        return (
            self._album_download_request_repository.update_claimed_by_queue_item(
                queue_item_id,
                owner,
                stage,
                detail,
                completed_tracks,
                now,
            )
        )

    def complete_album_download_request(
        self,
        queue_item_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        return (
            self._album_download_request_repository.complete_and_remove_queue_item(
                queue_item_id,
                detail,
                completed_tracks,
            )
        )

    def complete_claimed_album_download_request(
        self,
        queue_item_id: int,
        owner: str,
        detail: str,
        completed_tracks: int,
        now: Optional[datetime] = None,
    ) -> bool:
        """Atomically complete tracker and queue under one live lease."""
        return (
            self._album_download_request_repository
            .complete_claimed_and_remove_queue_item(
                queue_item_id,
                owner,
                detail,
                completed_tracks,
                now,
            )
        )

    def fail_claimed_album_download_request(
        self,
        queue_item_id: int,
        owner: str,
        error_message: str,
        completed_tracks: int = 0,
        *,
        quarantine: bool = False,
        base_delay_seconds: int = 300,
        max_delay_seconds: int = 86400,
        now: Optional[datetime] = None,
    ) -> bool:
        """Atomically fail an album tracker and its claimed queue row."""
        return self._album_download_request_repository.fail_claimed_queue_item(
            queue_item_id,
            owner,
            error_message,
            completed_tracks,
            quarantine=quarantine,
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
            now=now,
        )

    def complete_album_download_request_by_id(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        return self._album_download_request_repository.complete_by_request_id(
            request_id,
            detail,
            completed_tracks,
        )

    def invalidate_album_download_request(
        self,
        request_id: int,
        detail: str,
        completed_tracks: int,
    ) -> bool:
        return self._album_download_request_repository.invalidate(
            request_id,
            detail,
            completed_tracks,
        )

    def replace_album_download_request_identity(
        self,
        request_id: int,
        *,
        artist: str,
        title: str,
        track_count: int,
        recording_mbids: Tuple[str, ...],
        detail: str,
        track_manifest: Tuple[Mapping[str, Any], ...] = (),
    ) -> bool:
        return self._album_download_request_repository.replace_identity(
            request_id,
            artist=artist,
            title=title,
            track_count=track_count,
            recording_mbids=recording_mbids,
            detail=detail,
            track_manifest=track_manifest,
        )

    def requeue_album_download_request(
        self,
        request_id: int,
        queue_item_id: int,
        detail: str,
        completed_tracks: int = 0,
    ) -> bool:
        return self._album_download_request_repository.requeue(
            request_id,
            queue_item_id,
            detail,
            completed_tracks,
        )

    def count_local_release_tracks(self, release_mbid: str) -> int:
        return self._album_download_request_repository.count_local_release_tracks(
            release_mbid
        )

    def get_download_queue_count(self) -> int:
        return self._download_repository.active_count()

    def mark_track_synced(self, mbid: str, dap_path: str):
        self._sync_repository.mark_track_synced(mbid, dap_path)

    def get_all_tracks(self, local_only: bool = False, include_orphans: bool = False):
        return self._library_repository.get_all_tracks(
            local_only,
            include_orphans,
            lambda row: self._row_to_track(row),
        )

    def soft_delete_track(self, mbid: str) -> bool:
        """Mark a track as deleted without removing the row.

        Stamps ``deleted_at`` and bumps ``updated_at`` so the delta feed
        carries the signal to satellites on their next pull. Returns True
        if a row was stamped, False if no track matched.
        """
        if not mbid:
            return False
        return self._library_repository.soft_delete_track(mbid)

    def restore_track(self, mbid: str) -> bool:
        """Un-soft-delete a track: clears deleted_at and bumps updated_at."""
        if not mbid:
            return False
        return self._library_repository.restore_track(mbid)

    def soft_delete_playlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        return self._playlist_repository.soft_delete(playlist_id)

    def restore_playlist(self, playlist_id: str) -> bool:
        if not playlist_id:
            return False
        return self._playlist_repository.restore(playlist_id)

    def purge_track(self, mbid: str) -> bool:
        """Hard-delete a track row. Only permitted on already-soft-deleted
        rows so the API can't accidentally erase live data.

        Returns True if a row was deleted, False if no matching orphan
        existed. Membership rows in ``playlist_tracks`` cascade away via
        the FK declaration (PRAGMA foreign_keys is on).
        """
        if not mbid:
            return False
        return self._library_repository.purge_track(mbid)

    def purge_playlist(self, playlist_id: str) -> bool:
        """Hard-delete a playlist row (and its memberships via FK cascade).
        Only permitted on already-soft-deleted rows."""
        if not playlist_id:
            return False
        return self._playlist_repository.purge(playlist_id)

    def get_orphan_tracks(self) -> List[dict]:
        """Soft-deleted tracks, for the orphan cleanup UI.

        Shape matches what the /orphans page renders directly: identity
        fields, the deletion timestamp, and ``local_path`` so the UI can
        show a "still on disk" badge and offer the file-delete action.
        """
        return self._library_repository.get_orphan_tracks()

    def get_orphan_playlists(self) -> List[dict]:
        """Soft-deleted playlists with membership counts.

        track_count is the count of playlist_tracks rows still attached
        — useful because purging the playlist will cascade them away, so
        the UI can warn when the count is non-zero.
        """
        return self._playlist_repository.get_orphans()

    def get_catalog_since(self, since_iso: Optional[str] = None) -> List[dict]:
        """Return catalog-shape rows (device-agnostic fields + updated_at).

        If ``since_iso`` is provided, rows on or after that cursor are
        returned. The inclusive boundary prevents same-second SQLite writes
        from being lost; applying a boundary row again is idempotent.
        Local-only columns (local_path, dap_path, synced_to_dap)
        are deliberately omitted since they don't travel between devices.
        ``is_liked`` rides along because hearts are a user preference, not a
        device-local file fact — likes set on the master should appear on
        every satellite after the next sync tick.
        """
        return self._sync_repository.get_catalog_since(since_iso)

    def apply_catalog_row(self, row: dict) -> str:
        """Upsert a catalog row pulled from the master, preserving device-local
        columns (local_path, dap_path, synced_to_dap).

        Returns 'inserted' or 'updated' to indicate what happened. Rows missing
        an ``mbid`` are skipped and return 'skipped'.
        """
        mbid = (row or {}).get("mbid")
        if not mbid:
            return "skipped"
        return self._sync_repository.apply_catalog_row(row)

    def apply_playlist_row(self, row: dict) -> str:
        """Upsert a playlist (with its membership) pulled from the master.

        The incoming ``tracks`` list is treated as the authoritative current
        membership — existing rows for this playlist are cleared and
        replaced. ``updated_at`` is taken from the payload so the satellite's
        copy matches the master's timestamp.

        Returns 'inserted', 'updated', or 'skipped' (no playlist_id).
        """
        pid = (row or {}).get("playlist_id")
        if not pid:
            return "skipped"
        return self._playlist_repository.apply_row(row)

    def replace_device_inventory(self, device_id: str, items: List[dict]) -> int:
        """Replace the recorded inventory for ``device_id`` in one transaction.

        Each item is {mbid, local_path}. The whole snapshot is authoritative
        — rows not in the payload are dropped so removed tracks disappear.
        Returns the number of rows written.
        """
        if not device_id:
            raise ValueError("device_id is required")
        return self._inventory_repository.replace(device_id, items)

    def get_device_inventory(self, device_id: str) -> List[dict]:
        return self._inventory_repository.get_device(device_id)

    def get_fleet_summary(self) -> List[dict]:
        """Per-device inventory summary: device_id, track_count, last_reported_at."""
        return self._inventory_repository.get_fleet_summary()

    def get_devices_holding_mbid(self, mbid: str) -> List[dict]:
        """Which devices have reported holding a given track."""
        return self._inventory_repository.get_devices_holding_mbid(mbid)

    def find_tracks_for_fleet_search(self, query: str, limit: int = 50) -> List[dict]:
        """Find tracks matching artist/title/album for fleet lookup.

        Returns lightweight rows ({mbid, artist, title, album}) plus a
        per-row device_count so the UI can show matches ordered by
        how widely a track is held.
        """
        if not query:
            return []
        return self._inventory_repository.find_tracks(query, limit)

    # --- Play events (listening history) ---
    #
    # Appendable log: one row per "this track played long enough to count"
    # event. Client decides the threshold (e.g., 30s or 50% of duration);
    # the backend just records. Rows survive track soft-delete / purge so
    # historical stats remain meaningful — there's no FK constraint to
    # tracks.mbid for that reason.

    def record_play_event(
        self,
        track_mbid: str,
        source: Optional[str] = None,
        listened_ms: Optional[int] = None,
    ) -> int:
        """Append one play event. Returns the new event id.

        ``source`` is a free-form tag (e.g., "desktop", "web") so future
        stats can split per surface. None is fine and stays NULL.
        ``listened_ms`` is the wall-clock ms the user actually heard,
        not the track's full duration — the player owns that
        accounting. None on legacy + non-reporting clients; rows with
        NULL are excluded from listening-time aggregations rather
        than treated as zero.
        """
        if not track_mbid or not str(track_mbid).strip():
            raise ValueError("track_mbid is required")
        return self._listening_repository.record_event(
            track_mbid,
            source,
            listened_ms,
        )

    def plays_by_hour(self, since_iso: Optional[str] = None) -> List[dict]:
        """Per-hour-of-day play counts in the window.

        Returns one row per hour the user has actually played
        something — hours with zero plays are omitted, and the UI
        layer pads to a full 24-hour heatmap. Hour is parsed from
        ``strftime('%H', played_at)`` which treats played_at as UTC;
        the desktop renders against UTC labels (not local time) so
        the bins stay stable across DST shifts and traveling users.
        """
        return self._listening_repository.plays_by_hour(since_iso)

    def listening_time_since(self, since_iso: Optional[str] = None) -> int:
        """Sum of listened_ms in the window.

        Rows with NULL ``listened_ms`` are excluded (legacy scrobbles
        from before Stage 12a). Returns 0 when the window is empty or
        all rows are NULL — never None — so the API layer doesn't need
        to coalesce.
        """
        return self._listening_repository.listening_time_since(since_iso)

    def play_count_since(self, since_iso: Optional[str] = None) -> int:
        """Total play events recorded since ``since_iso`` (None = all time)."""
        return self._listening_repository.play_count_since(since_iso)

    def top_tracks_since(
        self, since_iso: Optional[str] = None, limit: int = 20
    ) -> List[dict]:
        """Top-played tracks in the window, joined to tracks for display.

        LEFT JOIN so events for tracks since deleted/purged still appear
        — the row carries the title/artist/album as currently known
        (NULLs if the track row is gone). The UI treats those as
        "(unknown)" rather than dropping them silently.
        """
        return self._listening_repository.top_tracks_since(since_iso, limit)

    def top_artists_since(
        self, since_iso: Optional[str] = None, limit: int = 20
    ) -> List[dict]:
        """Top artists by play count in the window.

        Groups on the *current* tracks.artist value, so a tag rewrite that
        renames an artist re-attributes their historical plays — that's
        the desired behaviour ("show me how much I've listened to X" should
        track X's current canonical name). Plays whose track row has been
        purged drop out of this view (the artist is unknown); they still
        count toward the global ``play_count_since`` total.
        """
        return self._listening_repository.top_artists_since(since_iso, limit)

    def wrapped_summary(self, year: int) -> dict:
        """Year-in-review aggregations powering the Wrapped screen.

        One method (not nine helpers) because every aggregation shares
        the same year window. Returns a dict with the same key shape
        the API exposes — the endpoint layer is a thin pass-through.

        Plays before Stage 12a have NULL listened_ms and are excluded
        from total_listening_time_ms; the count of those rows is
        returned as ``has_legacy_rows`` so the UI can disclaim the
        partial-history caveat under the headline number.
        """
        if not isinstance(year, int) or year < 1900 or year > 9999:
            raise ValueError(f"unreasonable year: {year!r}")
        since = f"{year}-01-01 00:00:00"
        until = f"{year}-12-31 23:59:59"
        return self._listening_repository.wrapped_summary(
            year,
            since,
            until,
        )

    def recent_plays(self, limit: int = 20) -> List[dict]:
        """Reverse-chronological feed of recent play events. Joined to
        tracks for display. Soft-deleted / purged tracks come back with
        NULL title/artist; the UI surfaces those as "(unknown track)".

        ``album_id`` mirrors the same COALESCE used by list_albums so the
        Home screen's "Jump back in" row can deep-link to the album
        detail without a second lookup.
        """
        return self._listening_repository.recent_plays(limit)

    # --- Lyrics (Stage 13) -------------------------------------------------

    def get_lyrics(self, track_mbid: str) -> Optional[dict]:
        """Return the cached lyrics row for a track, or None if no row
        exists at all.

        A row with ``lrc IS NULL`` is a *cached miss* — LRCLIB has been
        asked and didn't have lyrics. The caller still gets the row so
        it can render the empty state and respect the cache TTL instead
        of re-fetching on every open.
        """
        if not track_mbid:
            return None
        return self._metadata_repository.get_lyrics(track_mbid)

    def delete_lyrics(self, track_mbid: str) -> None:
        """Delete cached lyrics for a track and commit even when absent."""
        self._metadata_repository.delete_lyrics(track_mbid)

    def get_lyrics_since(self, since_iso: Optional[str] = None) -> List[dict]:
        """Lyrics rows whose ``fetched_at`` is newer than the cursor.

        Used by the catalog-sync pipeline to ship the master's cached
        LRCLIB results + manual overrides to satellites. ``fetched_at``
        doubles as a "last touched" timestamp because the upsert always
        bumps it on conflict.

        Rows with NULL lrc (negative-cache misses) are included so a
        satellite doesn't re-fetch LRCLIB for tracks the master already
        knows have no lyrics — saves outbound bandwidth across the fleet.
        """
        return self._metadata_repository.get_lyrics_since(since_iso)

    def apply_lyrics_row(self, row: dict) -> str:
        """Upsert a lyrics row pulled from the master.

        Returns 'inserted' / 'updated' / 'stale' / 'skipped'. Last-
        writer-wins on ``fetched_at`` — a satellite that just typed a
        manual override shouldn't be clobbered by the master's older
        cached LRCLIB row. Mirrors the resolution pattern in
        apply_pushed_playlist_row.
        """
        mbid = (row or {}).get("track_mbid")
        if not mbid:
            return "skipped"
        return self._metadata_repository.apply_lyrics_row(row)

    def upsert_lyrics(
        self,
        track_mbid: str,
        lrc: Optional[str],
        synced: bool,
        source: str,
    ) -> None:
        """Insert or replace the cached lyrics for a track.

        ``lrc=None`` is allowed and represents a cached miss (we asked
        LRCLIB and there's nothing). ``source`` must be 'lrclib' or
        'manual'; a manual override replaces any LRCLIB-cached row.
        """
        if not track_mbid:
            raise ValueError("track_mbid is required")
        if source not in ("lrclib", "manual"):
            raise ValueError(f"unknown lyrics source: {source!r}")
        self._metadata_repository.upsert_lyrics(
            track_mbid,
            lrc,
            synced,
            source,
        )

    # --- Artist tags (Stage 14a) ------------------------------------------

    # Genre-discovery noise on MusicBrainz — tags that appear on too
    # many unrelated artists to be useful for clustering. Filtered out
    # before persisting. Lowercased so the membership check ignores
    # casing variance in MB's user-submitted tag pool.
    _MB_NOISE_TAGS = frozenset({
        "seen live", "favourite", "favourites", "favorite", "favorites",
        "owned", "spotify", "soundtrack-no", "all", "best",
    })

    def get_distinct_artist_names(self) -> List[str]:
        """All live artist names in the library, case-insensitive sorted.

        Feeds the tag backfill — duplicate casings collapse to the
        canonical capitalization so we don't re-fetch the same MB
        artist three times. Soft-deleted tracks are excluded; the
        artists that *only* appear on deleted rows would otherwise
        keep showing up in every backfill pass.
        """
        return self._metadata_repository.get_distinct_artist_names()

    def get_artists_needing_tags(self, max_age_days: int = 30) -> List[str]:
        """Artists with no artist_tags row, or rows older than
        ``max_age_days``. Lets a resumed backfill skip the artists
        we've already covered.
        """
        return self._metadata_repository.get_artists_needing_tags(
            max_age_days
        )

    def record_artist_tags(
        self,
        artist_name: str,
        mbid: Optional[str],
        tags: List[dict],
        top_n: int = 10,
    ) -> int:
        """Replace any cached tags for an artist with a fresh top-N set.

        ``tags`` is a list of ``{"tag": str, "weight": int}`` dicts in
        MB's order (any order works — we sort here). Noise tags from
        ``_MB_NOISE_TAGS`` are dropped before truncation. Returns the
        number of rows persisted (could be < top_n if MB returned
        fewer tags or the filter ate most of them).

        Empty-tag results still wipe any previous row so a re-fetch
        that finds nothing isn't masked by yesterday's stale tags.
        """
        if not artist_name or not str(artist_name).strip():
            return 0
        return self._metadata_repository.record_artist_tags(
            artist_name,
            mbid,
            tags,
            top_n,
            self._MB_NOISE_TAGS,
        )

    def get_artist_tags_since(
        self, since_iso: Optional[str] = None,
    ) -> List[dict]:
        """Return authoritative per-artist tag snapshots for replica sync.

        A tag refresh replaces an artist's complete set, so shipping flat
        rows would leave removed tags behind on satellites.  Each returned
        item therefore nests the artist's current tags and carries the most
        recent ``fetched_at`` as its delta cursor.  The sentinel ``tag=''``
        row used for MusicBrainz misses becomes ``tags=[]`` in the wire
        representation, preserving both replacement and freshness semantics.

        The cursor boundary is intentionally inclusive. SQLite's
        ``CURRENT_TIMESTAMP`` has one-second resolution, so a refresh can
        commit after a pull's snapshot query yet still receive the exact same
        timestamp as that pull's ``as_of``. Replaying the boundary on the next
        request closes that race; ``apply_artist_tags_row`` makes the replay
        idempotent by comparing equal-timestamp snapshot content.
        """
        return self._metadata_repository.get_artist_tags_since(since_iso)

    def apply_artist_tags_row(self, row: dict) -> str:
        """Apply one authoritative artist-tag snapshot from the master.

        Returns ``inserted`` / ``updated`` / ``stale`` / ``skipped``.
        Snapshots replace, rather than merge, the current tag set so tags
        removed by a later MusicBrainz refresh disappear on satellites too.
        """
        artist_name = ((row or {}).get("artist_name") or "").strip()
        if not artist_name:
            return "skipped"
        return self._metadata_repository.apply_artist_tags_row(row)

    def get_top_tags_for_artist(
        self, artist_name: str, limit: int = 5,
    ) -> List[dict]:
        """Highest-weight tags for an artist. Returns ``[{tag, weight}]``.
        Empty list when nothing's cached — the caller decides whether
        that means "uncategorized" or "needs backfill". Sentinel rows
        (tag='') are filtered out — they exist to feed the freshness
        check, not the display."""
        if not artist_name:
            return []
        return self._metadata_repository.get_top_tags_for_artist(
            artist_name,
            limit,
        )

    def get_artists_by_tag(
        self, tag: str, limit: int = 50,
    ) -> List[dict]:
        """Artists tagged with ``tag``, ordered by their weight on it.
        Powers "more like this" / Daily Mix clustering lookups."""
        if not tag:
            return []
        return self._metadata_repository.get_artists_by_tag(tag, limit)

    # Columns used by the playable-row serializer (web_server's
    # _public_track_row). Centralized so the radio + daily-mix queries
    # don't drift away from the album/playlist queries.
    _PLAYABLE_TRACK_COLS = (
        "mbid, title, artist, album, track_number, disc_number, "
        "local_path, dap_path, is_liked, "
        "COALESCE(NULLIF(release_mbid, ''), album || '|' || artist) AS album_id"
    )

    def get_random_tracks_for_artists(
        self,
        artist_names: List[str],
        limit: int = 40,
    ) -> List[dict]:
        """Random sample of tracks across the given artist names.

        Powers Daily Mix generation: feed the cluster's artists, get
        back a shuffled pool of their tracks. Soft-deleted rows are
        excluded; the IN list is dropped to a no-op when empty so the
        caller doesn't have to gate the call.
        """
        if not artist_names:
            return []
        names = [n for n in artist_names if n]
        if not names:
            return []
        return self._metadata_repository.get_random_tracks_for_artists(
            names,
            limit,
            self._PLAYABLE_TRACK_COLS,
        )

    def ensure_system_playlist(
        self,
        playlist_id: str,
        name: str,
    ) -> str:
        """Idempotent UPSERT for a system playlist with a reserved id.

        Used for Liked Songs (Stage 11a) and Daily Mixes (Stage 14d).
        Both have id schemes the rest of the code branches on, so
        minting a UUID would defeat the lookup. Bumps name + updated_at
        on conflict so a re-generated Daily Mix can shift its display
        copy without churning a separate UPDATE.
        """
        return self._playlist_repository.ensure_system_playlist(
            playlist_id,
            name,
        )

    def build_artist_radio(
        self,
        artist_name: str,
        limit: int = 50,
    ) -> dict:
        """Generate an Artist Radio queue seeded on ``artist_name``.

        Pool composition:
          - ~30% tracks by the seed artist
          - ~70% tracks by other artists sharing the seed's top MB tag

        Falls back to seed-only when:
          - the seed has no artist_tags row (Stage 14a backfill hasn't
            covered them, or the backfill couldn't resolve them on MB)
          - the seed's top tag has no other artists in the library

        Both halves are SQL-RANDOM-sorted independently, then
        interleaved by a Python shuffle so the user doesn't hear all
        of the seed artist first then all related — RANDOM() per
        SELECT keeps the rows grouped by which SELECT they came from.

        Returns ``{"tracks": [...], "top_tag": str|None,
        "seed_count": int, "related_count": int}``. The breakdown
        feeds the UI's "Why am I hearing this?" tooltip.
        """
        if not artist_name:
            return {
                "tracks": [], "top_tag": None,
                "seed_count": 0, "related_count": 0,
            }
        return self._metadata_repository.build_artist_radio(
            artist_name,
            limit,
            self._PLAYABLE_TRACK_COLS,
            lambda value, limit=5: self.get_top_tags_for_artist(
                value,
                limit=limit,
            ),
        )

    def apply_pushed_playlist_row(self, row: dict) -> str:
        """Apply a playlist pushed from a satellite, using last-writer-wins
        on ``updated_at``.

        Returns:
          - 'inserted' / 'updated': incoming accepted and applied.
          - 'stale': local updated_at is equal to or newer than incoming —
                    incoming ignored so a round-tripped pull doesn't
                    overwrite a subsequent master-side edit.
          - 'skipped': no playlist_id in payload.

        Lexicographic ISO-string comparison matches SQLite's CURRENT_TIMESTAMP
        ordering, which is what both sides store.
        """
        pid = (row or {}).get("playlist_id")
        if not pid:
            return "skipped"
        return self._playlist_repository.apply_pushed_row(
            row,
            lambda value: self.apply_playlist_row(value),
        )

    def get_sync_state(self, key: str) -> Optional[str]:
        return self._sync_repository.get_state(key)

    def set_sync_state(self, key: str, value: str):
        self._sync_repository.set_state(key, value)

    def get_current_timestamp(self) -> Optional[str]:
        """Return SQLite's current timestamp from the active database."""
        return self._sync_repository.get_current_timestamp()

    def get_all_playlists(self, include_orphans: bool = False):
        return [
            self._row_to_playlist(row)
            for row in self._playlist_repository.fetch_all_playlists(
                include_orphans
            )
        ]

    def purge_playlists_by_prefix(self, prefix: str) -> None:
        self._playlist_repository.purge_by_prefix(prefix)

    def list_playlists_by_prefix(self, prefix: str) -> List[dict]:
        return self._playlist_repository.list_by_prefix(prefix)

    def list_playlists_with_counts(self) -> List[dict]:
        """Live playlists with membership counts, for the web library sidebar.

        Orphans are excluded — the /orphans page handles those. Sort is
        case-insensitive on name so the UI can render directly. Smart
        playlists return their stored rules JSON; ``track_count`` for them
        reflects only manual membership (always 0 today since add/remove
        ops are rejected for smart playlists), not the evaluated count —
        that would require running the rules query per playlist on every
        sidebar render.
        """
        return self._playlist_repository.list_with_counts(
            self.LIKED_SONGS_PLAYLIST_ID
        )

    def list_tracks_filtered(
        self,
        playlist_id: Optional[str] = None,
        local_only: bool = False,
        include_orphans: bool = False,
    ) -> List[dict]:
        """Tracks for the web library browser — flat dict rows with
        ``local_path``/``dap_path`` so the caller can resolve availability.

        When ``playlist_id`` is a static playlist, results are scoped to
        that playlist's membership (ordered by ``track_order``). When the
        playlist is smart (non-null ``smart_rules``), results come from
        evaluating the ruleset against the tracks table — no ``track_order``
        exists, so rows fall back to the standard library sort. Without a
        playlist_id, the full live library is returned.
        """
        return self._playlist_repository.list_tracks_filtered(
            playlist_id,
            local_only,
            include_orphans,
        )

    def get_playlists_since(self, since_iso: Optional[str] = None) -> List[dict]:
        """Return playlists changed since ``since_iso`` with their full
        current membership nested as ``tracks``.

        Each returned dict: {playlist_id, name, spotify_url, updated_at,
        tracks: [{track_mbid, track_order}, ...]}. Membership is always the
        complete current list — satellites replace, not diff, to handle
        track removals correctly.
        """
        return self._playlist_repository.get_since(since_iso)

    def get_playlist_tracks(
        self,
        playlist_id: str,
        local_only: bool = False,
        include_orphans: bool = False,
    ):
        return [
            self._row_to_track(row)
            for row in self._playlist_repository.fetch_playlist_tracks(
                playlist_id,
                local_only,
                include_orphans,
            )
        ]

    def get_tracks_for_playlist(
        self,
        playlist_id: str,
        local_only: bool = False,
        include_orphans: bool = False,
    ):
        return self.get_playlist_tracks(
            playlist_id, local_only=local_only, include_orphans=include_orphans
        )

    def link_track_to_playlist(self, playlist_id: str, track_mbid: str, order: int):
        self._playlist_repository.link_track(
            playlist_id,
            track_mbid,
            order,
            lambda value: self._bump_playlist_updated_at(value),
        )

    def get_mbid_to_track_path_map(self):
        return self._library_repository.get_mbid_to_track_path_map()

    def log_duplicate(self, mbid: str, file_path: str):
        if file_path:
            file_path = os.path.normpath(file_path).replace("\\", "/")
        self._album_maintenance_repository.log_duplicate(mbid, file_path)

    def get_all_duplicates(self):
        return self._album_maintenance_repository.get_all_duplicates()

    def clear_duplicate(self, mbid: str):
        self._album_maintenance_repository.clear_duplicate(mbid)

    # --- Album grouping maintenance ---
    def list_split_album_tracks(self) -> List[SplitAlbumTrackRow]:
        """Rows used by folder/name split-album detection."""
        return self._album_maintenance_repository.list_split_album_tracks()

    def list_album_group_tracks(self) -> List[AlbumGroupTrackRow]:
        """Rows used to plan edition consolidation without local paths."""
        return self._album_maintenance_repository.list_album_group_tracks()

    def reassign_album_group_tracks(
        self,
        source_album_id: str,
        target_album: str,
        target_artist: str,
        target_release_mbid: Optional[str],
        include_local_paths: bool = True,
    ) -> AlbumGroupReassignment:
        """Apply one planned album-group move and commit it once."""
        repository = getattr(self, "_album_maintenance_repository", None)
        if repository is None:
            repository = AlbumMaintenanceRepository(self.conn)
        return repository.reassign_album_group_tracks(
            source_album_id,
            target_album,
            target_artist,
            target_release_mbid,
            include_local_paths,
        )

    def list_local_album_tag_rows(self) -> List[AlbumTagRow]:
        """Album-level metadata for tracks backed by a local file."""
        return self._album_maintenance_repository.list_local_album_tag_rows()

    # --- Split-album dismissals ---
    def get_dismissed_split_albums(self) -> set:
        """Return the set of dismissed split-album incident keys."""
        return (
            self._album_maintenance_repository
            .get_dismissed_split_albums()
        )

    def dismiss_split_album(self, incident_key: str):
        self._album_maintenance_repository.dismiss_split_album(incident_key)

    def undismiss_split_album(self, incident_key: str):
        self._album_maintenance_repository.undismiss_split_album(incident_key)

    def clear_missing_local_paths(self, dry_run: bool = True) -> dict:
        """Find (and optionally clear) ``local_path`` for tracks whose file is gone.

        These dangling links accumulate when a file is renamed/removed outside
        DAPManager (e.g. after a duplicate cleanup). Only the broken file link is
        cleared (the catalog row is kept) so the track falls back to its DAP path
        / master stream / unavailable state instead of pointing at nothing.

        **Dry-run by default**: with ``dry_run=True`` nothing is written — it just
        reports what *would* be cleared. This is a deliberate safety guard: on a
        bind mount a transient I/O hiccup could make ``os.path.isfile`` return
        False for files that really exist, and clearing on that would be
        destructive. Always preview before applying.

        Must run where the files live (the container, for the bind-mounted
        library). Returns ``{dry_run, scanned, cleared, fraction, sample}``
        where ``cleared`` is the would-clear count in dry-run mode.
        """
        return self._library_repository.clear_missing_local_paths(
            dry_run,
            lambda path: os.path.isfile(path),
        )

    def update_track_local_path(self, mbid: str, path: str):
        if path:
            path = os.path.normpath(path).replace("\\", "/")
        self._library_repository.update_track_local_path(mbid, path)

    def _row_to_track(self, row):
        if not row:
            return None
        return Track(
            mbid=row["mbid"],
            title=row["title"],
            artist=row["artist"],
            album=row["album"],
            isrc=row["isrc"],
            local_path=row["local_path"],
            dap_path=row["dap_path"],
            synced_to_dap=bool(row["synced_to_dap"]),
            release_mbid=row["release_mbid"],
            track_number=row["track_number"],
            disc_number=row["disc_number"],
            tag_tier=row["tag_tier"] if "tag_tier" in row.keys() else None,
            tag_score=row["tag_score"] if "tag_score" in row.keys() else None,
            album_artist=(
                row["album_artist"]
                if "album_artist" in row.keys()
                else None
            ),
        )

    def _row_to_playlist(self, row):
        if not row:
            return None
        keys = row.keys() if hasattr(row, "keys") else ()
        return Playlist(
            playlist_id=row["playlist_id"],
            name=row["name"],
            spotify_url=row["spotify_url"],
            smart_rules=row["smart_rules"] if "smart_rules" in keys else None,
        )

    def get_library_stats(self) -> dict:
        return self._library_repository.get_library_stats(logger)

    def search_tracks(self, query: str) -> List[Track]:
        return self._library_repository.search_tracks(
            query,
            lambda row: self._row_to_track(row),
            logger,
        )

    def get_all_downloads(self) -> List[DownloadItem]:
        return [
            self._row_to_download_item(row)
            for row in self._download_repository.fetch_all()
        ]

    def get_download(self, item_id: int) -> Optional[DownloadItem]:
        row = self._download_repository.fetch_one(item_id)
        return self._row_to_download_item(row) if row else None

    def retry_download(self, item_id: int) -> bool:
        # Flip a failed row back to 'pending' so the downloader picks it up
        # on its next run. last_attempt is intentionally left alone — the
        # forensic "last failed at X" stays visible until the actual retry
        # bumps it via update_download_status.
        return self._download_repository.retry(item_id)

    def claim_download_for_album_request(
        self,
        item_id: int,
        release_mbid: str,
        search_query: str,
        playlist_id: str,
    ) -> bool:
        return self._download_repository.claim_for_album_request(
            item_id,
            release_mbid,
            search_query,
            playlist_id,
        )

    def get_queued_release_mbids(self) -> set:
        # Distinct mbid_guess values currently in the queue, regardless of
        # status — Lidarr's wanted list cares about "is this album already
        # being chased" not "did the last attempt succeed". The watcher's
        # has_queued_mbid uses the same regardless-of-status semantics
        # for the same reason.
        return self._download_repository.get_queued_release_mbids()

    def get_existing_release_mbids(self) -> set:
        # Live tracks only (deleted_at IS NULL) — soft-deleted rows
        # shouldn't make a wanted album look "downloaded".
        return self._download_repository.get_existing_release_mbids()

    def delete_succeeded_downloads(self) -> int:
        # 'success' is the schema's terminal-success state (see the
        # CHECK constraint on download_queue.status). The web UI labels
        # this column as "Completed" for end users; keep the DB-side
        # name aligned with the column value to avoid translation
        # drift.
        return self._download_repository.delete_succeeded()

    def _row_to_download_item(self, row):
        if not row:
            return None
        keys = row.keys() if hasattr(row, "keys") else ()

        def parse_timestamp(column: str) -> Optional[datetime]:
            if column not in keys or not row[column]:
                return None
            try:
                return datetime.fromisoformat(str(row[column]))
            except (ValueError, TypeError):
                return None

        return DownloadItem(
            id=row["id"],
            search_query=row["search_query"],
            playlist_id=row["playlist_id"],
            mbid_guess=row["mbid_guess"],
            status=row["status"],
            last_attempt=parse_timestamp("last_attempt"),
            attempt_count=(
                int(row["attempt_count"] or 0)
                if "attempt_count" in keys else 0
            ),
            max_attempts=(
                int(row["max_attempts"] or 3)
                if "max_attempts" in keys else 3
            ),
            next_attempt_at=parse_timestamp("next_attempt_at"),
            claim_owner=(
                row["claim_owner"] if "claim_owner" in keys else None
            ),
            claim_expires_at=parse_timestamp("claim_expires_at"),
            claim_heartbeat_at=parse_timestamp("claim_heartbeat_at"),
            is_paused=(
                bool(row["is_paused"]) if "is_paused" in keys else False
            ),
            is_quarantined=(
                bool(row["is_quarantined"])
                if "is_quarantined" in keys else False
            ),
            last_error=(
                row["last_error"] if "last_error" in keys else None
            ),
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
