#!/usr/bin/env python3
"""
E2E harness: spin up an isolated master + satellite container pair, seed it with
N random real library files, run the full E2E suite against both, and report
coverage of the app code that ran inside the containers.

Everything runs on the host via Docker. Nothing here touches your live
`dapmanager` master — the test pair uses its own containers, network, config and
data dirs in a temp workspace.

    python scripts/run_e2e.py                 # build, run, report coverage
    python scripts/run_e2e.py --skip-build    # reuse existing dapmanager:latest
    python scripts/run_e2e.py --files 10      # number of library files to seed
    python scripts/run_e2e.py --keep          # leave containers up for debugging

Coverage:
  Both containers launch the app under `python -m coverage run`. On `docker stop`
  coverage flushes (sigterm=true in .coveragerc) to a shared /coverage volume.
  We then combine + report inside an ephemeral container (the image has the
  source at /app so line mapping is correct) and write HTML to ./htmlcov.
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = "dapmanager:latest"
NETWORK = "dap-e2e-net"
MASTER_NAME = "dap-e2e-master"
SAT_NAME = "dap-e2e-satellite"
MASTER_PORT = 5101
SAT_PORT = 5102
MASTER_URL = f"http://localhost:{MASTER_PORT}"
SAT_URL = f"http://localhost:{SAT_PORT}"


def run(cmd, **kw):
    """Run a command, raising on failure. Returns CompletedProcess."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)


def run_quiet(cmd):
    """Run, swallow output and errors (for best-effort cleanup)."""
    subprocess.run(cmd, capture_output=True)


def http(url, path, timeout=5):
    try:
        with urllib.request.urlopen(url + path, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except Exception as e:
        return None, str(e)


def wait_healthy(url, name, timeout=120):
    print(f"  waiting for {name} at {url} …")
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, data = http(url, "/api/healthz")
        if status == 200 and isinstance(data, dict) and data.get("ok"):
            print(f"  {name} healthy.")
            return True
        time.sleep(2)
    return False


def pick_files(library_dir, n):
    flacs = []
    for root, _dirs, files in os.walk(library_dir):
        for f in files:
            if f.lower().endswith((".flac", ".mp3", ".m4a", ".ogg")):
                flacs.append(os.path.join(root, f))
    if not flacs:
        sys.exit(f"No audio files found under {library_dir}")
    n = min(n, len(flacs))
    chosen = random.sample(flacs, n)
    print(f"  selected {n} random files from {len(flacs)} in the library")
    return chosen


def seed_master_music(files, library_dir, dest_music):
    """Copy chosen files into the test-master music dir under a flat, safe
    layout. The scanner groups by embedded tags, not paths, so structure is
    irrelevant — and flattening sidesteps Windows-hostile source dir names
    (e.g. album folders with trailing dots, which are invalid on Windows)."""
    os.makedirs(dest_music, exist_ok=True)
    for i, src in enumerate(files):
        base = os.path.basename(src).rstrip(". ")  # trim trailing dots/spaces
        dst = os.path.join(dest_music, f"{i:02d}_{base}")
        shutil.copy2(src, dst)


def base_config(is_master):
    cfg = {
        "database_file": "/data/dap_library.db",
        "music_library_path": "/data/music",
        "downloads_path": "/data/downloads",
        "ffmpeg_path": "ffmpeg",
        "slsk_cmd_base": ["sldl"],
        "dap_mount_point": "",
        "dap_music_dir_name": "Music",
        "dap_playlist_dir_name": "Playlists",
        "slsk_username": "", "slsk_password": "",
        "acoustid_api_key": "", "contact_email": "",
        "jellyfin_url": "", "jellyfin_api_key": "", "jellyfin_user_id": "",
        "lidarr_enabled": False, "lidarr_url": "", "lidarr_api_key": "",
        "api_token": "",
    }
    if is_master:
        cfg.update({
            "is_master": True, "device_role": "master",
            "public_master_url": f"http://{MASTER_NAME}:5001",
            "master_url": "",
        })
    else:
        cfg.update({
            "is_master": False, "device_role": "satellite",
            "master_url": f"http://{MASTER_NAME}:5001",
            "contribute_to_host": False, "report_inventory_to_host": False,
        })
    return cfg


def write_config(config_dir, is_master):
    os.makedirs(config_dir, exist_ok=True)
    with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump(base_config(is_master), f, indent=2)


def docker_run_app(name, port, config_dir, data_dir, cov_dir):
    run([
        "docker", "run", "-d", "--name", name, "--network", NETWORK,
        "-p", f"{port}:5001",
        "-v", f"{config_dir}:/config",
        "-v", f"{data_dir}:/data",
        "-v", f"{cov_dir}:/coverage",
        "-e", "DAPMANAGER_DEBUG=0",
        "-e", "COVERAGE_FILE=/coverage/.coverage",
        IMAGE,
        "python", "-m", "coverage", "run", "--parallel-mode",
        "--rcfile=/app/.coveragerc", "/app/web_server.py",
    ], capture_output=True)


def cleanup(keep=False):
    if keep:
        print("  --keep set: leaving containers/network up.")
        return
    for n in (MASTER_NAME, SAT_NAME):
        run_quiet(["docker", "stop", n])
        run_quiet(["docker", "rm", n])
    run_quiet(["docker", "network", "rm", NETWORK])


def main():
    ap = argparse.ArgumentParser(description="DAPManager E2E harness")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--files", type=int, default=10)
    ap.add_argument("--library", default=os.path.join(REPO, "data", "music"))
    ap.add_argument("--keep", action="store_true", help="leave the pair running")
    args = ap.parse_args()

    if not args.skip_build:
        print("== Building image ==")
        run(["docker", "build", "-t", IMAGE, REPO])

    # Clean any leftovers from a prior run.
    cleanup(keep=False)

    work = tempfile.mkdtemp(prefix="dap-e2e-")
    cov_dir = os.path.join(work, "coverage")
    master_cfg = os.path.join(work, "master", "config")
    master_data = os.path.join(work, "master", "data")
    sat_cfg = os.path.join(work, "satellite", "config")
    sat_data = os.path.join(work, "satellite", "data")
    for d in (cov_dir, os.path.join(master_data, "music"),
              os.path.join(master_data, "downloads"),
              os.path.join(sat_data, "music"),
              os.path.join(sat_data, "downloads")):
        os.makedirs(d, exist_ok=True)

    print(f"== Workspace: {work} ==")

    print("== Seeding master with random library files ==")
    files = pick_files(args.library, args.files)
    seed_master_music(files, args.library, os.path.join(master_data, "music"))
    write_config(master_cfg, is_master=True)
    write_config(sat_cfg, is_master=False)

    rc = 1
    try:
        print("== Starting containers ==")
        run_quiet(["docker", "network", "create", NETWORK])
        docker_run_app(MASTER_NAME, MASTER_PORT, master_cfg, master_data, cov_dir)
        docker_run_app(SAT_NAME, SAT_PORT, sat_cfg, sat_data, cov_dir)

        if not wait_healthy(MASTER_URL, "master") or not wait_healthy(SAT_URL, "satellite"):
            run_quiet(["docker", "logs", "--tail", "40", MASTER_NAME])
            raise SystemExit("containers did not become healthy")

        print("== Scanning master library ==")
        try:
            req = urllib.request.Request(MASTER_URL + "/api/scan", method="POST")
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            print(f"  scan trigger note: {e}")
        # wait for the scan to finish before driving the suite
        for _ in range(90):
            _, d = http(MASTER_URL, "/api/status")
            if isinstance(d, dict) and not d.get("running"):
                break
            time.sleep(2)
        print(f"  master status: {http(MASTER_URL, '/api/status')[1]}")

        print("== Running E2E suite ==")
        env = dict(os.environ, E2E_MASTER=MASTER_URL, E2E_SATELLITE=SAT_URL,
                   PYTHONPATH=REPO)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.e2e.test_e2e_suite", "-v"],
            cwd=REPO, env=env,
        )
        rc = proc.returncode

    finally:
        if args.keep:
            # Debugging mode: leave the pair RUNNING (so it can be poked at).
            # Coverage needs a stop to flush, so we skip the report here.
            print("== --keep: containers left RUNNING (no coverage report) ==")
            print(f"   master:    {MASTER_URL}")
            print(f"   satellite: {SAT_URL}")
            print(f"   workspace: {work}")
            print("   tear down: docker rm -f "
                  f"{MASTER_NAME} {SAT_NAME}; docker network rm {NETWORK}")
        else:
            print("== Stopping app containers (flush coverage) ==")
            # graceful stop so coverage's SIGTERM handler writes data
            run_quiet(["docker", "stop", "-t", "15", MASTER_NAME])
            run_quiet(["docker", "stop", "-t", "15", SAT_NAME])

            print("== Combining coverage ==")
            combine = subprocess.run([
                "docker", "run", "--rm", "--entrypoint", "",
                "-v", f"{cov_dir}:/coverage",
                "-e", "COVERAGE_FILE=/coverage/.coverage",
                IMAGE, "sh", "-c",
                "cd /app && python -m coverage combine /coverage && "
                "python -m coverage report && "
                "python -m coverage html -d /coverage/htmlcov && "
                "python -m coverage json -o /coverage/coverage.json",
            ], capture_output=True, text=True)
            print(combine.stdout)
            if combine.returncode != 0:
                print(combine.stderr)

            # Pull the HTML report into the repo for convenience.
            html_src = os.path.join(cov_dir, "htmlcov")
            html_dst = os.path.join(REPO, "htmlcov")
            if os.path.isdir(html_src):
                if os.path.isdir(html_dst):
                    shutil.rmtree(html_dst)
                shutil.copytree(html_src, html_dst)
                print(f"== Coverage HTML: {html_dst}\\index.html ==")

            cleanup(keep=False)
            shutil.rmtree(work, ignore_errors=True)

    print(f"== E2E suite exit code: {rc} ==")
    sys.exit(rc)


if __name__ == "__main__":
    main()
