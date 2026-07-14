"""
Catalog delta pull: fetches GET /api/catalog from the master DAPManager and
applies incoming rows to the local replica, preserving per-device columns.

Design notes (see project sync model memory):
- Delta sync, not full snapshot. We persist the master's returned ``as_of``
  as the next ``since``, so concurrent writes during a pull aren't missed
  on the following pull.
- Never silently delete. Rows removed on the master surface later as
  orphans (tracks present locally that no longer appear upstream); this
  module only upserts.
- Writes are idempotent: rerunning a pull after a network hiccup replays
  the same rows harmlessly.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, cast

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .contracts import (
    CatalogApplyAction,
    CatalogApplyCallback,
    CatalogRow,
    ConfigMapping,
    DeltaSyncResult,
    PlaylistPushResult,
    ProgressCallback,
    ProgressEvent,
)
from .db_manager import DatabaseManager

logger = logging.getLogger(__name__)

SYNC_STATE_KEY = "last_catalog_sync"
PLAYLIST_SYNC_STATE_KEY = "last_playlist_sync"
PLAYLIST_PUSH_STATE_KEY = "last_playlist_push"
LYRICS_SYNC_STATE_KEY = "last_lyrics_sync"
ARTIST_TAGS_SYNC_STATE_KEY = "last_artist_tags_sync"


@dataclass(frozen=True)
class _DeltaPullSpec:
    """Stable protocol details for one master-owned delta feed."""

    state_key: str
    endpoint: str
    collection_key: str
    fetch_message: str
    apply_message: str
    completion_message: str
    batch_size: int
    include_stale: bool = False


_CATALOG_PULL = _DeltaPullSpec(
    state_key=SYNC_STATE_KEY,
    endpoint="/api/catalog",
    collection_key="tracks",
    fetch_message="Fetching catalog delta",
    apply_message="Applying catalog rows",
    completion_message="Catalog pull done",
    batch_size=100,
)
_PLAYLIST_PULL = _DeltaPullSpec(
    state_key=PLAYLIST_SYNC_STATE_KEY,
    endpoint="/api/playlists",
    collection_key="playlists",
    fetch_message="Fetching playlist delta",
    apply_message="Applying playlists",
    completion_message="Playlist pull done",
    batch_size=25,
)
_LYRICS_PULL = _DeltaPullSpec(
    state_key=LYRICS_SYNC_STATE_KEY,
    endpoint="/api/lyrics",
    collection_key="lyrics",
    fetch_message="Fetching lyrics delta",
    apply_message="Applying lyrics",
    completion_message="Lyrics pull done",
    batch_size=100,
    include_stale=True,
)
_ARTIST_TAGS_PULL = _DeltaPullSpec(
    state_key=ARTIST_TAGS_SYNC_STATE_KEY,
    endpoint="/api/artist-tags",
    collection_key="artist_tags",
    fetch_message="Fetching artist-tag delta",
    apply_message="Applying artist tags",
    completion_message="Artist-tag pull done",
    batch_size=100,
    include_stale=True,
)


class CatalogClient:
    """Pulls catalog deltas from the master DAPManager."""

    def __init__(
        self,
        db: DatabaseManager,
        master_url: str,
        progress_callback: Optional[ProgressCallback] = None,
        timeout: int = 30,
        api_token: Optional[str] = None,
    ) -> None:
        if not master_url:
            raise ValueError("master_url is required to pull the catalog")

        self.db = db
        self.master_url = master_url.rstrip("/")
        self.progress_callback: Optional[ProgressCallback] = progress_callback
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if api_token:
            self.session.headers["Authorization"] = f"Bearer {api_token}"
        # POST retries are safe: the push endpoint is idempotent under
        # last-writer-wins, so a retried duplicate is a no-op.
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST"]),
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _report(
        self,
        message: str,
        detail: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        logger.info(message)
        if not self.progress_callback:
            return
        payload: ProgressEvent = {"message": message}
        if detail is not None:
            payload["detail"] = detail
        if current is not None and total is not None:
            payload["current"] = current
            payload["total"] = total
        self.progress_callback(payload)

    def _pull_delta(
        self,
        spec: _DeltaPullSpec,
        apply_row: CatalogApplyCallback,
    ) -> DeltaSyncResult:
        """Fetch and atomically advance one delta feed's cursor.

        Cursor persistence deliberately remains after every row has applied.
        A request, validation, or row failure therefore leaves the previous
        cursor intact and makes the next pull safely replay the delta.
        """
        since = self.db.get_sync_state(spec.state_key)
        self._report(
            spec.fetch_message + (f" since {since}" if since else " (initial)")
        )

        params = {"since": since} if since else {}
        response = self.session.get(
            f"{self.master_url}{spec.endpoint}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = cast(Mapping[str, Any], response.json() or {})
        if not data.get("success"):
            raise RuntimeError(
                "Master responded with failure: "
                f"{data.get('message', 'unknown error')}"
            )

        rows = cast(Sequence[CatalogRow], data.get(spec.collection_key) or [])
        as_of = cast(Optional[str], data.get("as_of"))
        counts: Dict[str, int] = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
        }
        if spec.include_stale:
            counts["stale"] = 0

        total = len(rows)
        for current, row in enumerate(rows, 1):
            action: CatalogApplyAction = apply_row(row)
            if action in ("inserted", "updated"):
                counts[action] += 1
            elif action == "stale" and spec.include_stale:
                counts["stale"] += 1
            else:
                counts["skipped"] += 1

            if total and (current == total or current % spec.batch_size == 0):
                self._report(
                    f"{spec.apply_message} ({current}/{total})",
                    current=current,
                    total=total,
                )

        if as_of:
            self.db.set_sync_state(spec.state_key, as_of)

        summary: DeltaSyncResult = {
            "received": total,
            "inserted": counts["inserted"],
            "updated": counts["updated"],
            "skipped": counts["skipped"],
            "since": since,
            "as_of": as_of,
        }
        completion_parts = [
            f"{counts['inserted']} new",
            f"{counts['updated']} updated",
        ]
        if spec.include_stale:
            summary["stale"] = counts["stale"]
            completion_parts.append(f"{counts['stale']} stale")
        completion_parts.append(f"{counts['skipped']} skipped")
        self._report(
            f"{spec.completion_message}: {', '.join(completion_parts)}"
        )
        return summary

    def pull(self) -> DeltaSyncResult:
        """Pull the catalog delta and apply it.

        Returns a summary: {received, inserted, updated, skipped, as_of, since}.
        """
        return self._pull_delta(
            _CATALOG_PULL,
            cast(CatalogApplyCallback, self.db.apply_catalog_row),
        )

    def pull_playlists(self) -> DeltaSyncResult:
        """Pull the playlist delta and apply each one (full-membership replace).

        Tracks must be pulled first — unknown track MBIDs on the satellite
        get silently dropped from membership until a later pass picks them
        up. Returns {received, inserted, updated, skipped, since, as_of}.
        """
        return self._pull_delta(
            _PLAYLIST_PULL,
            cast(CatalogApplyCallback, self.db.apply_playlist_row),
        )

    def pull_lyrics(self) -> DeltaSyncResult:
        """Pull the lyrics delta and apply each row.

        Cheaper than running LRCLIB lookups on every satellite — the
        master's cached results (hits *and* negative-cache misses)
        propagate across the fleet, and a user's manual override on
        one device appears on every other. Tracks should be pulled
        first; lyrics for unknown mbids still apply (the row's keyed
        by mbid, not the foreign key).
        """
        return self._pull_delta(
            _LYRICS_PULL,
            cast(CatalogApplyCallback, self.db.apply_lyrics_row),
        )

    def pull_artist_tags(self) -> DeltaSyncResult:
        """Pull authoritative artist-tag snapshots from the master.

        Each payload item replaces one artist's full tag set, including an
        empty set for MusicBrainz misses.  This lets genre smart playlists,
        Artist Radio, and Daily Mix metadata behave consistently without
        every satellite independently consuming MusicBrainz's rate limit.
        """
        return self._pull_delta(
            _ARTIST_TAGS_PULL,
            cast(CatalogApplyCallback, self.db.apply_artist_tags_row),
        )

    def push_playlists(self) -> PlaylistPushResult:
        """Push locally-edited playlists to the master.

        Selects playlists with updated_at > last_playlist_push, POSTs them
        in one batch, and on success advances the cursor to the snapshot
        time taken at the start of the push. Edits that landed mid-push
        keep an updated_at > snapshot so they get picked up next round.

        The master applies last-writer-wins, so retrying a push after a
        network blip is safe — stale duplicates just come back as 'stale'.

        Returns {sent, accepted, stale, skipped, since, as_of}.
        """
        last_push = self.db.get_sync_state(PLAYLIST_PUSH_STATE_KEY)
        snapshot = self.db.conn.execute(
            "SELECT CURRENT_TIMESTAMP AS t"
        ).fetchone()["t"]
        rows = self.db.get_playlists_since(last_push)

        if not rows:
            # Still advance the cursor so we don't keep re-scanning the
            # same (empty) window.
            self.db.set_sync_state(PLAYLIST_PUSH_STATE_KEY, snapshot)
            self._report("No locally-edited playlists to push")
            return {
                "sent": 0,
                "accepted": 0,
                "stale": 0,
                "skipped": 0,
                "since": last_push,
                "as_of": snapshot,
            }

        self._report(f"Pushing {len(rows)} playlist(s) to master")
        resp = self.session.post(
            f"{self.master_url}/api/playlists",
            json={"playlists": rows},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        if not data.get("success"):
            raise RuntimeError(
                f"Master rejected playlist push: "
                f"{data.get('message', 'unknown error')}"
            )

        self.db.set_sync_state(PLAYLIST_PUSH_STATE_KEY, snapshot)

        summary: PlaylistPushResult = {
            "sent": len(rows),
            "accepted": int(data.get("accepted", 0)),
            "stale": int(data.get("stale", 0)),
            "skipped": int(data.get("skipped", 0)),
            "since": last_push,
            "as_of": snapshot,
        }
        self._report(
            f"Playlist push done: {summary['accepted']} accepted, "
            f"{summary['stale']} stale, {summary['skipped']} skipped"
        )
        return summary


def main_run_catalog_pull(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> DeltaSyncResult:
    """Entry point used by the web server / CLI."""
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
        api_token=(config.get("api_token") or "").strip() or None,
    )
    return client.pull()


def main_run_playlist_pull(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> DeltaSyncResult:
    """Pull only the playlist delta. Tracks should be pulled first."""
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
        api_token=(config.get("api_token") or "").strip() or None,
    )
    return client.pull_playlists()


def main_run_playlist_push(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> PlaylistPushResult:
    """Push locally-edited playlists to the master."""
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
        api_token=(config.get("api_token") or "").strip() or None,
    )
    return client.push_playlists()


def main_run_lyrics_pull(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> DeltaSyncResult:
    """Pull the lyrics delta from the master.

    Skipped at the sync_all level when no master_url is configured;
    callable here directly for tests or manual sync.
    """
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
        api_token=(config.get("api_token") or "").strip() or None,
    )
    return client.pull_lyrics()


def main_run_artist_tags_pull(
    db: DatabaseManager,
    config: ConfigMapping,
    progress_callback: Optional[ProgressCallback] = None,
) -> DeltaSyncResult:
    """Pull the artist-tag delta from the master."""
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
        api_token=(config.get("api_token") or "").strip() or None,
    )
    return client.pull_artist_tags()
