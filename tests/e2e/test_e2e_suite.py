"""
Full E2E suite — drives a live master + satellite container pair over HTTP.

This is orchestrated by ``scripts/run_e2e.py`` (which spins up the containers,
seeds 10 real library files, sets the env vars below, and measures coverage of
the app code running inside the containers). It can also be pointed at any
running pair manually:

    E2E_MASTER=http://localhost:5101 E2E_SATELLITE=http://localhost:5102 \
        python -m unittest tests.e2e.test_e2e_suite -v

Written with stdlib ``unittest`` (no pytest dependency) so it runs with a plain
``python`` on the host. The goal is breadth: hit as many endpoints/branches as
possible so the coverage run is meaningful, while still asserting real behaviour
on the core flows (scan → catalog → stream → satellite sync).
"""

import os
import json
import time
import unittest
import urllib.request
import urllib.error

MASTER = os.environ.get("E2E_MASTER", "")
SATELLITE = os.environ.get("E2E_SATELLITE", "")

POLL_INTERVAL = 2
POLL_TIMEOUT = 180


def _req(base, method, path, body=None, raw=False, range_header=None, timeout=30):
    """HTTP helper. Returns (status, data). data is parsed JSON, or raw bytes
    when raw=True. Never raises on HTTP error codes — returns them so tests can
    assert on status without try/except everywhere."""
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if range_header:
        headers["Range"] = range_header
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            if raw:
                return r.status, payload
            try:
                return r.status, json.loads(payload)
            except json.JSONDecodeError:
                return r.status, payload
    except urllib.error.HTTPError as e:
        payload = e.read()
        if raw:
            return e.code, payload
        try:
            return e.code, json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return e.code, {"raw": payload.decode(errors="replace")}
    except Exception as e:
        # Network/URL errors (timeouts, DNS, malformed URL) — return a sentinel
        # status of 0 so callers can decide rather than the whole test erroring.
        return (0, b"") if raw else (0, {"error": str(e)})


def _wait_idle(base, timeout=POLL_TIMEOUT):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _, last = _req(base, "GET", "/api/status")
        if not (isinstance(last, dict) and last.get("running")):
            return last
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{base} still running after {timeout}s: {last}")


@unittest.skipUnless(MASTER, "Set E2E_MASTER to run the E2E suite")
class TestMaster(unittest.TestCase):
    """Exercises the master's library/maintenance/playback surface."""

    sample_mbid = None
    sample_album_id = None
    sample_artist = None

    @classmethod
    def setUpClass(cls):
        # Master scan is done by the orchestrator; grab a streamable track.
        _wait_idle(MASTER)
        _, data = _req(MASTER, "GET", "/api/library/tracks?limit=500")
        tracks = (data or {}).get("tracks", []) if isinstance(data, dict) else []
        local = [t for t in tracks if t.get("availability") == "local"]
        chosen = local[0] if local else (tracks[0] if tracks else None)
        if chosen:
            cls.sample_mbid = chosen.get("mbid")
            cls.sample_album_id = chosen.get("album_id")
            cls.sample_artist = chosen.get("artist")

    # ---- liveness / config ------------------------------------------------
    def test_healthz(self):
        status, data = _req(MASTER, "GET", "/api/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_status(self):
        status, data = _req(MASTER, "GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertIn("running", data)

    def test_config_is_master(self):
        _, data = _req(MASTER, "GET", "/api/config")
        self.assertTrue(data["success"])
        self.assertTrue(data["config"].get("is_master"))

    # ---- library views ----------------------------------------------------
    def test_library_albums_nonempty(self):
        _, data = _req(MASTER, "GET", "/api/library/albums")
        self.assertTrue(data["success"])
        self.assertGreater(len(data["albums"]), 0, "scan produced no albums")

    def test_library_tracks_nonempty(self):
        _, data = _req(MASTER, "GET", "/api/library/tracks")
        self.assertTrue(data["success"])
        self.assertGreater(len(data["tracks"]), 0)

    def test_library_artists(self):
        _, data = _req(MASTER, "GET", "/api/library/artists")
        self.assertTrue(data["success"])
        self.assertGreater(len(data["artists"]), 0)

    def test_album_tracks_and_cover(self):
        self.assertIsNotNone(self.sample_album_id, "no sample album")
        from urllib.parse import quote
        aid = quote(self.sample_album_id, safe="")
        status, data = _req(MASTER, "GET", f"/api/library/albums/{aid}/tracks")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        # Cover may or may not be embedded — accept 200 (found) or 404 (none).
        cstatus, _ = _req(MASTER, "GET", f"/api/library/albums/{aid}/cover", raw=True)
        self.assertIn(cstatus, (200, 404))

    def test_library_home(self):
        status, data = _req(MASTER, "GET", "/api/library/home")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    # ---- playback / stream ------------------------------------------------
    def test_stream_full_and_range(self):
        self.assertIsNotNone(self.sample_mbid, "no sample track")
        status, body = _req(MASTER, "GET", f"/api/stream/{self.sample_mbid}", raw=True)
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0)
        # Range request should yield 206 Partial Content.
        rstatus, rbody = _req(
            MASTER, "GET", f"/api/stream/{self.sample_mbid}",
            raw=True, range_header="bytes=0-1023",
        )
        self.assertIn(rstatus, (200, 206))

    def test_record_play_and_stats(self):
        self.assertIsNotNone(self.sample_mbid)
        status, data = _req(MASTER, "POST", "/api/library/plays",
                            {"mbid": self.sample_mbid, "source": "e2e",
                             "listened_ms": 30000})
        self.assertEqual(status, 201)
        sstatus, sdata = _req(MASTER, "GET", "/api/library/play-stats")
        self.assertEqual(sstatus, 200)
        self.assertGreaterEqual(sdata["total"], 1)

    def test_wrapped(self):
        status, data = _req(MASTER, "GET", "/api/library/wrapped")
        self.assertEqual(status, 200)

    # ---- like toggle ------------------------------------------------------
    def test_like_toggle(self):
        self.assertIsNotNone(self.sample_mbid)
        status, data = _req(MASTER, "POST", f"/api/library/tracks/{self.sample_mbid}/like")
        self.assertEqual(status, 200)
        self.assertTrue(data["liked"])
        status, data = _req(MASTER, "DELETE", f"/api/library/tracks/{self.sample_mbid}/like")
        self.assertEqual(status, 200)
        self.assertFalse(data["liked"])

    # ---- playlists CRUD ---------------------------------------------------
    def test_playlist_lifecycle(self):
        _, created = _req(MASTER, "POST", "/api/library/playlists", {"name": "E2E Mix"})
        self.assertTrue(created["success"])
        pid = created["playlist_id"]
        # add the sample track
        if self.sample_mbid:
            _, upd = _req(MASTER, "PUT", f"/api/library/playlists/{pid}",
                          {"track_mbids": [self.sample_mbid]})
            self.assertTrue(upd["success"])
        # list + scoped tracks
        _, lst = _req(MASTER, "GET", "/api/library/playlists")
        self.assertTrue(any(p["playlist_id"] == pid for p in lst["playlists"]))
        _, scoped = _req(MASTER, "GET", f"/api/library/tracks?playlist_id={pid}")
        self.assertTrue(scoped["success"])
        # delete (soft)
        status, _ = _req(MASTER, "DELETE", f"/api/library/playlists/{pid}")
        self.assertEqual(status, 200)

    # ---- maintenance (the stuff this whole effort added) ------------------
    def test_duplicates_list(self):
        status, data = _req(MASTER, "GET", "/api/duplicates")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_split_albums_list(self):
        status, data = _req(MASTER, "GET", "/api/library/split-albums")
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertIn("incidents", data)

    def test_consolidate_dry_run(self):
        status, data = _req(MASTER, "POST", "/api/library/consolidate-editions",
                            {"dry_run": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertTrue(data["dry_run"])

    def test_retag_files(self):
        status, data = _req(MASTER, "POST", "/api/library/retag-files",
                            {"only_mismatched": True}, timeout=300)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_scrub_dangling(self):
        status, data = _req(MASTER, "POST", "/api/library/scrub-dangling", {}, timeout=120)
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertIn("scanned", data)
        self.assertIn("cleared", data)

    def test_audit_results(self):
        """Incomplete-album audit (DB-side) returns without error."""
        status, data = _req(MASTER, "GET", "/api/audit/results")
        self.assertEqual(status, 200)

    def test_filtered_track_queries(self):
        """Exercise the filtered list_tracks branches."""
        for q in ("?local_only=1", "?include_orphans=1"):
            status, data = _req(MASTER, "GET", "/api/library/tracks" + q)
            self.assertEqual(status, 200)
            self.assertTrue(data["success"])

    def test_pages_render(self):
        """Server-rendered pages return 200 (covers the route handlers)."""
        for path in ("/", "/player", "/satellite", "/library", "/fleet",
                     "/orphans", "/contributions", "/docs"):
            status, _ = _req(MASTER, "GET", path, raw=True)
            self.assertEqual(status, 200, f"{path} returned {status}")

    def test_openapi_and_setup_status(self):
        s1, _ = _req(MASTER, "GET", "/api/openapi.json", raw=True)
        self.assertEqual(s1, 200)
        s2, data = _req(MASTER, "GET", "/api/setup/status")
        self.assertEqual(s2, 200)
        self.assertIn("needs_setup", data)

    def test_config_roundtrip(self):
        """GET then POST a harmless config key — exercises update_config."""
        _, cfg = _req(MASTER, "GET", "/api/config")
        cur = bool(cfg["config"].get("fast_search"))
        status, data = _req(MASTER, "POST", "/api/config", {"fast_search": cur})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])

    def test_smart_playlist(self):
        """Create a smart playlist (rules), read it back, delete it."""
        rules = {"match": "all", "rules": [{"field": "is_liked", "op": "equals", "value": True}]}
        status, created = _req(MASTER, "POST", "/api/library/playlists",
                               {"name": "E2E Smart", "smart_rules": rules})
        self.assertEqual(status, 201)
        pid = created["playlist_id"]
        _, lst = _req(MASTER, "GET", "/api/library/playlists")
        row = next((p for p in lst["playlists"] if p["playlist_id"] == pid), None)
        self.assertIsNotNone(row)
        self.assertIsNotNone(row.get("smart_rules"))
        _req(MASTER, "DELETE", f"/api/library/playlists/{pid}")

    def test_stats_variants(self):
        from urllib.parse import quote
        since = quote("2020-01-01 00:00:00")
        s1, _ = _req(MASTER, "GET", f"/api/library/play-stats?limit=5&since={since}")
        self.assertEqual(s1, 200)
        s2, _ = _req(MASTER, "GET", "/api/library/wrapped?year=2026")
        self.assertEqual(s2, 200)

    # ---- broad exercise --------------------------------------------------
    def test_db_backed_endpoints_no_server_error(self):
        """Deterministic, DB-only endpoints must not return a 5xx."""
        from urllib.parse import quote
        calls = [
            ("GET", "/api/catalog"),
            ("GET", "/api/playlists"),
            ("GET", "/api/lyrics"),
            ("GET", "/api/orphans/tracks"),
            ("GET", "/api/orphans/playlists"),
            ("POST", "/api/library/daily-mixes/regenerate", {}),
        ]
        if self.sample_artist:
            a = quote(self.sample_artist, safe="")
            calls.append(("GET", f"/api/library/artists/{a}/radio?limit=10"))
        for call in calls:
            method, path = call[0], call[1]
            body = call[2] if len(call) > 2 else None
            status, _ = _req(MASTER, method, path, body, timeout=60)
            self.assertTrue(0 < status < 500, f"{method} {path} returned {status}")

    def test_network_backed_endpoints_exercised(self):
        """Network-backed features (Wikipedia/LRCLIB) — just exercise them for
        coverage; they may legitimately 502/timeout when upstream is flaky, so
        we only assert the call round-tripped (status is set)."""
        from urllib.parse import quote
        if self.sample_artist:
            a = quote(self.sample_artist, safe="")
            _req(MASTER, "GET", f"/api/library/artists/{a}/info", timeout=60)
        if self.sample_mbid:
            _req(MASTER, "GET", f"/api/library/tracks/{self.sample_mbid}/lyrics", timeout=60)
        # No status assertion — purpose is to run the handler code paths.


@unittest.skipUnless(SATELLITE and MASTER, "Set E2E_SATELLITE + E2E_MASTER for satellite tests")
class TestSatellite(unittest.TestCase):
    """Exercises the satellite → master flows: catalog sync, remote stream proxy."""

    @classmethod
    def setUpClass(cls):
        _wait_idle(SATELLITE)

    def test_healthz(self):
        status, data = _req(SATELLITE, "GET", "/api/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_config_is_satellite(self):
        _, data = _req(SATELLITE, "GET", "/api/config")
        self.assertTrue(data["success"])
        cfg = data["config"]
        self.assertFalse(cfg.get("is_master"))
        self.assertTrue(cfg.get("master_url"), "satellite has no master_url")

    def test_catalog_pull_populates_library(self):
        # Trigger the delta pull from the master, wait for it to finish.
        status, data = _req(SATELLITE, "POST", "/api/catalog/pull")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"), f"pull rejected: {data}")
        _wait_idle(SATELLITE)
        # Satellite should now list the master's tracks as 'remote'.
        _, lib = _req(SATELLITE, "GET", "/api/library/tracks")
        self.assertTrue(lib["success"])
        self.assertGreater(len(lib["tracks"]), 0, "catalog pull brought no tracks")
        self.assertTrue(
            any(t.get("availability") == "remote" for t in lib["tracks"]),
            "no remote-availability tracks after pull",
        )

    def test_remote_stream_proxy(self):
        # A track the satellite only has via catalog should still stream,
        # proxied through the master.
        _, lib = _req(SATELLITE, "GET", "/api/library/tracks")
        tracks = lib.get("tracks", []) if isinstance(lib, dict) else []
        remote = next((t for t in tracks if t.get("availability") == "remote"), None)
        if not remote:
            self.skipTest("no remote track to proxy-stream")
        status, body = _req(SATELLITE, "GET", f"/api/stream/{remote['mbid']}", raw=True)
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0, "proxied stream returned no bytes")

    def test_like_proxies_to_master(self):
        _, lib = _req(SATELLITE, "GET", "/api/library/tracks")
        tracks = lib.get("tracks", []) if isinstance(lib, dict) else []
        if not tracks:
            self.skipTest("no tracks on satellite")
        mbid = tracks[0]["mbid"]
        status, data = _req(SATELLITE, "POST", f"/api/library/tracks/{mbid}/like")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("success"))
        # confirm it landed on the master
        _, m = _req(MASTER, "GET", "/api/library/tracks")
        liked = next((t for t in m.get("tracks", []) if t["mbid"] == mbid), None)
        if liked is not None:
            self.assertTrue(liked.get("is_liked"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
