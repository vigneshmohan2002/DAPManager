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
from typing import Callable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .db_manager import DatabaseManager

logger = logging.getLogger(__name__)

SYNC_STATE_KEY = "last_catalog_sync"


class CatalogClient:
    """Pulls catalog deltas from the master DAPManager."""

    def __init__(
        self,
        db: DatabaseManager,
        master_url: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        timeout: int = 30,
    ):
        if not master_url:
            raise ValueError("master_url is required to pull the catalog")

        self.db = db
        self.master_url = master_url.rstrip("/")
        self.progress_callback = progress_callback
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
        self.session.mount("http://", HTTPAdapter(max_retries=retries))
        self.session.mount("https://", HTTPAdapter(max_retries=retries))

    def _report(
        self,
        message: str,
        detail: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ):
        logger.info(message)
        if not self.progress_callback:
            return
        payload = {"message": message}
        if detail is not None:
            payload["detail"] = detail
        if current is not None and total is not None:
            payload["current"] = current
            payload["total"] = total
        self.progress_callback(payload)

    def pull(self) -> dict:
        """Pull the catalog delta and apply it.

        Returns a summary: {received, inserted, updated, skipped, as_of, since}.
        """
        since = self.db.get_sync_state(SYNC_STATE_KEY)
        self._report(
            "Fetching catalog delta" + (f" since {since}" if since else " (initial)")
        )

        params = {"since": since} if since else {}
        resp = self.session.get(
            f"{self.master_url}/api/catalog", params=params, timeout=self.timeout
        )
        resp.raise_for_status()
        data = resp.json() or {}
        if not data.get("success"):
            raise RuntimeError(
                f"Master responded with failure: {data.get('message', 'unknown error')}"
            )

        tracks = data.get("tracks") or []
        as_of = data.get("as_of")

        inserted = 0
        updated = 0
        skipped = 0
        total = len(tracks)
        for i, row in enumerate(tracks, 1):
            action = self.db.apply_catalog_row(row)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
            if total and (i == total or i % 100 == 0):
                self._report(
                    f"Applying catalog rows ({i}/{total})",
                    current=i,
                    total=total,
                )

        # Only advance the cursor if the master told us where it was at query
        # time. Without that we'd risk moving the cursor past rows written
        # during the pull itself.
        if as_of:
            self.db.set_sync_state(SYNC_STATE_KEY, as_of)

        summary = {
            "received": total,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "since": since,
            "as_of": as_of,
        }
        self._report(
            f"Catalog pull done: {inserted} new, {updated} updated, "
            f"{skipped} skipped"
        )
        return summary


def main_run_catalog_pull(
    db: DatabaseManager,
    config: dict,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Entry point used by the web server / CLI."""
    master_url = (config.get("master_url") or "").rstrip("/")
    client = CatalogClient(
        db=db,
        master_url=master_url,
        progress_callback=progress_callback,
    )
    return client.pull()
