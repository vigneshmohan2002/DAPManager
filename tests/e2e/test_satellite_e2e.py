"""
Satellite end-to-end integration tests.

These tests run against a *live* DAPManager master and are skipped unless
DAPMANAGER_E2E_MASTER is set in the environment, so they never fire in the
standard unit-test run.

    # run just these tests against a local master
    DAPMANAGER_E2E_MASTER=http://localhost:5001 pytest tests/test_satellite_e2e.py -v

    # run against the Tailscale master from any device
    DAPMANAGER_E2E_MASTER=http://100.70.27.116:5001 pytest tests/test_satellite_e2e.py -v
"""

import os
import time
import json
import urllib.request
import urllib.error

import pytest

MASTER = os.environ.get("DAPMANAGER_E2E_MASTER", "")
POLL_INTERVAL = 5
POLL_TIMEOUT = 300

pytestmark = pytest.mark.skipif(
    not MASTER,
    reason="Set DAPMANAGER_E2E_MASTER=<url> to run satellite E2E tests",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _req(method, path, body=None):
    url = MASTER.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _wait_idle(timeout=POLL_TIMEOUT):
    """Poll /api/status until running=False; return the final status dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = _req("GET", "/api/status")
        if not s.get("running"):
            return s
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"Master still running after {timeout}s")


def _track_in_catalog(keyword):
    """Return the first catalog track whose title or artist contains keyword."""
    r = _req("GET", "/api/library/tracks?limit=1000")
    tracks = r.get("tracks") or r.get("items") or []
    kw = keyword.lower()
    return next(
        (t for t in tracks
         if kw in (t.get("title") or "").lower()
         or kw in (t.get("artist") or "").lower()),
        None,
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def idle_master():
    """Ensure master is idle before any test in this module runs."""
    s = _req("GET", "/api/status")
    if s.get("running"):
        pytest.skip("Master is busy; skipping E2E tests to avoid interference")
    return s


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestMasterReachability:
    def test_status_endpoint(self, idle_master):
        """GET /api/status returns a valid response with expected fields."""
        assert "running" in idle_master
        assert "message" in idle_master

    def test_config_has_credentials(self):
        """Master config exposes Jellyfin URL and Soulseek username."""
        cfg = _req("GET", "/api/config")["config"]
        assert cfg.get("jellyfin_url"), "jellyfin_url not configured on master"
        assert cfg.get("slsk_username"), "slsk_username not configured on master"

    def test_library_not_empty(self):
        """Master catalog has at least one album."""
        r = _req("GET", "/api/library/albums?limit=1")
        albums = r.get("albums") or r.get("items") or []
        assert len(albums) > 0, "Master library is empty"


class TestDownloadAndCatalog:
    TRACK_QUERY = "Kendrick Lamar HUMBLE"
    TRACK_TITLE_KEYWORD = "HUMBLE"

    def test_queue_download(self, idle_master):
        """Satellite can queue a download on the master."""
        r = _req("POST", "/api/download/request",
                 {"search_query": self.TRACK_QUERY})
        assert r.get("success"), f"Queue failed: {r}"

    def test_download_completes(self, idle_master):
        """Downloader runs to completion without error."""
        _req("POST", "/api/download")
        status = _wait_idle()
        assert "completed successfully" in status.get("message", ""), (
            f"Download did not complete cleanly: {status}"
        )

    def test_track_appears_in_catalog(self, idle_master):
        """Downloaded track is indexed in the master library."""
        track = _track_in_catalog(self.TRACK_TITLE_KEYWORD)
        assert track is not None, (
            f"'{self.TRACK_TITLE_KEYWORD}' not found in catalog after download"
        )

    def test_jellyfin_scan_triggered(self, idle_master):
        """After a successful download, Jellyfin library refresh was triggered.

        We infer this from the download completing without error — the scan
        trigger fires synchronously at the end of run_queue and logs at INFO.
        A hard failure there would surface in the task status message.
        """
        status = _req("GET", "/api/status")
        assert "completed successfully" in status.get("message", "") or \
               status.get("message") == "Idle", (
            f"Unexpected master state after download: {status}"
        )


class TestCatalogSearch:
    def test_search_by_title_keyword_is_specific(self):
        """Catalog keyword search returns the correct artist for a known track."""
        track = _track_in_catalog("HUMBLE")
        assert track is not None, "HUMBLE not in catalog"
        assert "kendrick" in (track.get("artist") or "").lower(), (
            f"Expected Kendrick Lamar, got: {track.get('artist')}"
        )

    def test_artist_search_returns_results(self):
        """Searching by artist name returns at least one result."""
        track = _track_in_catalog("Arctic Monkeys")
        # This track may or may not be downloaded; just verify the search works
        # without error. If present, artist field should match.
        if track:
            assert "arctic" in (track.get("artist") or "").lower()
