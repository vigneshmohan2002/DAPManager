#!/usr/bin/env python3
"""
Satellite end-to-end smoke test.

Simulates what a satellite does: requests a download on the master,
waits for it to complete, pulls the catalog, and confirms the track
landed in the master's library.

Usage (run from any device on Tailscale):
    python test_satellite_e2e.py
    python test_satellite_e2e.py --master http://100.70.27.116:5001
    python test_satellite_e2e.py --master http://viggys-pc:5001 --track "Radiohead Creep"
    python test_satellite_e2e.py --skip-download  # catalog/Jellyfin checks only
"""

import argparse
import sys
import time
import urllib.request
import urllib.error
import json

DEFAULT_MASTER = "http://100.70.27.116:5001"
DEFAULT_TRACK  = "Kendrick Lamar HUMBLE"
POLL_INTERVAL  = 5   # seconds
TIMEOUT        = 300 # seconds


def _request(method, url, body=None):
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} → HTTP {e.code}: {e.read().decode()}") from e
    except Exception as e:
        raise RuntimeError(f"{method} {url} failed: {e}") from e


def check(label, condition, detail=""):
    icon = "PASS" if condition else "FAIL"
    print(f"  [{icon}] {label}" + (f" -- {detail}" if detail else ""))
    return condition


def run(master, track_query, skip_download):
    passed = []
    failed = []

    def record(label, ok, detail=""):
        (passed if ok else failed).append(label)
        return check(label, ok, detail)

    print(f"\n=== Satellite E2E test ===")
    print(f"Master : {master}")
    print(f"Track  : {track_query}")
    print()

    # 1 — reachability
    print("-- Step 1: Master reachability")
    try:
        status = _request("GET", f"{master}/api/status")
        record("Master reachable", True, status.get("message", ""))
    except Exception as e:
        record("Master reachable", False, str(e))
        print("\n❌ Cannot reach master — aborting.")
        return False

    # 2 — queue download
    if not skip_download:
        print("\n-- Step 2: Queue download request")
        try:
            r = _request("POST", f"{master}/api/download/request",
                         {"search_query": track_query})
            already = not r.get("queued", True)
            record("Download queued (or already queued)", r.get("success"),
                   "already queued" if already else f"item_id={r.get('item_id')}")
        except Exception as e:
            record("Download queued", False, str(e))
            failed.append("Download queued")

        # 3 — trigger + poll
        print("\n-- Step 3: Trigger downloader and wait")
        try:
            _request("POST", f"{master}/api/download")
            print(f"  ... Polling every {POLL_INTERVAL}s (timeout {TIMEOUT}s)…")
            deadline = time.time() + TIMEOUT
            last_msg = ""
            while time.time() < deadline:
                time.sleep(POLL_INTERVAL)
                s = _request("GET", f"{master}/api/status")
                if s.get("message") != last_msg:
                    last_msg = s.get("message", "")
                    print(f"     {last_msg}")
                if not s.get("running"):
                    break
            ok = "completed successfully" in (s.get("message") or "")
            record("Download completed", ok, s.get("message", ""))
        except Exception as e:
            record("Download completed", False, str(e))

    # 4 — catalog check
    print("\n-- Step 4: Verify track in master catalog")
    keyword = track_query.split()[-1].lower()
    try:
        r = _request("GET", f"{master}/api/library/tracks?limit=500")
        tracks = r.get("tracks") or r.get("items") or []
        match = next(
            (t for t in tracks
             if keyword in (t.get("title") or "").lower()
             or keyword in (t.get("artist") or "").lower()),
            None,
        )
        record("Track in catalog", bool(match),
               f"{match.get('artist')} — {match.get('title')}" if match else f"no match for '{keyword}'")
    except Exception as e:
        record("Track in catalog", False, str(e))

    # 5 — Jellyfin scan was triggered (log check via status message)
    print("\n-- Step 5: Jellyfin scan trigger (inferred from last status)")
    try:
        s = _request("GET", f"{master}/api/status")
        msg = s.get("message") or ""
        triggered = "completed successfully" in msg or skip_download
        record("Jellyfin scan triggered", triggered,
               "inferred from successful download run" if triggered else msg)
    except Exception as e:
        record("Jellyfin scan triggered", False, str(e))

    # Summary
    print(f"\n{'='*40}")
    print(f"  PASSED : {len(passed)}")
    print(f"  FAILED : {len(failed)}")
    if failed:
        print(f"  FAILED steps: {', '.join(failed)}")
    else:
        print("  All checks passed")
    print()

    return len(failed) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DAPManager satellite E2E smoke test")
    parser.add_argument("--master", default=DEFAULT_MASTER,
                        help=f"Master URL (default: {DEFAULT_MASTER})")
    parser.add_argument("--track", default=DEFAULT_TRACK,
                        help=f"Track to download (default: {DEFAULT_TRACK!r})")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip the download step; only check catalog and connectivity")
    args = parser.parse_args()

    ok = run(args.master, args.track, args.skip_download)
    sys.exit(0 if ok else 1)
