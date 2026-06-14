# DAPManager

A multi-device music library manager. **Jellyfin / the "master" holds the
canonical catalog**; **satellite** devices (laptops, DAPs) keep a local subset,
sync from the master, and can **contribute** music they acquired independently
back up to it.

- Master deployment is a **Docker container on a Windows host**, with Tailscale
  on the host (not the container). See `docs/onboarding.md`.
- Backend: Flask (`web_server.py`) + SQLite (`src/db_manager.py`).
- Web UI: server-rendered templates in `web/templates`. There's also a Tauri
  desktop app under `desktop/` (separate track).

---

## Continue on a Windows instance

This is the quick path to get the project running on Windows and keep working.

### 0. Get the code

```powershell
git clone https://github.com/vigneshmohan2002/DAPManager.git
cd DAPManager
git checkout master   # latest is on master
```

(If you already have the repo: `git pull`.)

### Option A — Direct Python (simplest; good for dev / a satellite)

Requires **Python 3.10+** on PATH.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python web_server.py
```

Or use the bundled helper, which copies the Windows config template and starts
the server:

```powershell
.\bootstrap_windows.bat
```

The server listens on **http://localhost:5001** (override with the
`DAPMANAGER_PORT` env var). For downloads you also need **`sldl.exe`**
(slsk-batchdl) in the repo root — see the warning `bootstrap_windows.bat`
prints if it's missing.

### Option B — Docker (recommended for a full master)

Requires **Docker Desktop**. One command brings up DAPManager + Lidarr +
Prowlarr, wires them together, and pushes credentials:

```powershell
.\scripts\setup-master.ps1                       # auto-detects Tailscale URL
# or, if Tailscale isn't detected:
.\scripts\setup-master.ps1 -MasterPublicUrl http://yourhost:5001
```

See the header of `scripts/setup-master.ps1` for all parameters (Soulseek /
Jellyfin creds, ports, etc.) and `docker-compose.example.yml` for the compose
shape.

### 1. Configure in the browser (no terminal needed)

Open **http://localhost:5001/** — a fresh install redirects to **`/setup`**.
Pick a role (Master / Satellite / Standalone), fill the paths, and (satellite)
the Master URL. After saving you land on the Dashboard, where the **Settings**
card edits any config key.

> **Windows path note:** paths in the wizard are on the host running
> DAPManager. Use the `config.example.win.json` template style
> (`C:\\Users\\You\\Music\\...`). Config lives at
> `%APPDATA%\DAPManager\config.json` unless you set the `DAPMANAGER_CONFIG`
> env var to an explicit path.

### 2. API docs (for humans and for a Claude/Cowork instance)

- **Swagger UI:** http://localhost:5001/docs
- **Machine-readable spec:** http://localhost:5001/api/openapi.json (no external
  deps; an agent can parse this directly)
- **Runbook:** `docs/cowork-setup.md` — step-by-step browser setup for an agent.

Both docs endpoints work **before** the app is configured (they're exempt from
the setup + auth gates), so an automated browser agent can learn the setup flow
from a fresh install.

### 3. Run the tests

```powershell
python -m pytest -q
```

> Note: a block of `tests/test_web_routes.py` tests are environment/ordering
> sensitive (they depend on a config file existing) and can show as failures in
> isolation — this is **pre-existing**, not from the contribution feature. The
> contribution suites (`test_audio_quality`, `test_file_ingest`,
> `test_contributions`, `test_contribution_sync`, `test_openapi`) are
> self-contained and should all pass.

### End-to-end suite (real master + satellite containers, with coverage)

`scripts/run_e2e.py` spins up an **isolated** master + satellite container pair
on a private Docker network, seeds the master with N random files from your
local `data/music` library, runs the full HTTP-driven suite
(`tests/e2e/test_e2e_suite.py`) against both, and reports **code coverage of the
app running inside the containers**:

```powershell
python scripts/run_e2e.py            # build, run, report coverage -> ./htmlcov
python scripts/run_e2e.py --skip-build --files 10
python scripts/run_e2e.py --keep     # leave the pair running for debugging
```

It uses only the Python standard library on the host (no pytest needed) — the
app is launched under `coverage` inside the containers. Coverage of pure
external-service integrations (Soulseek, Lidarr, Spotify, MusicBrainz, LRCLIB,
Jellyfin, DAP sync) is necessarily low from E2E alone, since those need live
services/credentials/hardware.

### Git hooks (tests on commit & push)

Versioned hooks live in `.githooks/`. Enable them **once per clone**:

```powershell
git config core.hooksPath .githooks
```

- **pre-commit** — byte-compiles all Python (fast syntax gate).
- **pre-push** — runs the full E2E suite. Skips gracefully when Docker or
  `data/music` is absent. Bypass a single push with `SKIP_E2E=1 git push`.

---

## What's new: satellite → master contribution

A satellite offers a local track to the master **identifier-first**: it sends
the MBID + a quality descriptor, and the master tries to acquire the track
itself. **Only if the master can't match the quality** (or can't find it) does
the satellite upload the actual file, which the master verifies and ingests.

Quality is compared as a tier tuple — lossless → bit-depth → sample-rate →
bitrate — so "same or better" is a plain `>=`.

**Trigger it:** runs automatically in **Sync All** (gated by `contribute_to_host`,
default on when `master_url` is set), or manually via the **Contribute** button
on the Dashboard, the **Contribute to master** item in the Library right-click
menu, or `POST /api/contribute`. The **Contributions** page shows each offer's
status and promised-vs-acquired quality.

### Key files
| Area | File |
|------|------|
| Quality probe / comparison | `src/audio_quality.py` |
| Master intake + status (lazy poll) | `web_server.py` (`/api/contributions*`) |
| Upload ingest | `src/file_ingest.py` |
| Satellite offer/poll/upload | `src/contribution_sync.py` |
| Sync wiring | `src/sync_all.py` |
| Config keys | `src/config_keys.py` (`contribute_to_host`, `contribution_attempt_timeout_seconds`) |
| API spec | `src/openapi_spec.py` → `/api/openapi.json`, `/docs` |
| Web pages | `web/templates/contributions.html`, `docs.html` |

### Endpoints (see `/docs` for full detail)
- `POST /api/contributions` — satellite offers a track (master intake)
- `GET /api/contributions/<id>` — poll; master recomputes status
- `POST /api/contributions/<id>/upload` — upload fallback (verified before ingest)
- `GET /api/contributions` — list (dashboard)
- `POST /api/contribute` — contribute all local tracks (background)
- `POST /api/contribute/track` — contribute one track (synchronous)

---

## Library maintenance (duplicates, split albums, editions, retag)

Tools for keeping the library clean. All available in the Dashboard UI, over the
HTTP API, and via the `scripts/dap_admin.py` CLI.

- **Duplicates** — find/resolve multiple files mapped to one track; keeps the
  highest-quality copy. "Resolve All" does the whole list at once.
- **Split albums** — detect an album fragmented across entries (folder-based or
  name-similarity), then merge with file-tag rewrite. False positives can be
  dismissed permanently.
- **Consolidate editions** — fold standard/base editions into their superset
  (e.g. "Album" → "Album (Deluxe)") so every song lands on one album. Always
  dry-run/preview first; apply is idempotent.
- **Retag** — sync on-disk file tags to the database (only files that drifted).
- **Scrub dangling** — clear `local_path` for tracks whose file is missing.

> **Key model:** DAPManager's UI reads the **database**; Jellyfin reads the
> **embedded file tags**. Metadata operations that should reach Jellyfin
> rewrite the file tags and trigger a scan — see
> [docs/library-maintenance.md](docs/library-maintenance.md) for the full
> table and the DB-vs-file-tags explanation.

### CLI quick reference (`scripts/dap_admin.py`)

```bash
python scripts/dap_admin.py healthz
python scripts/dap_admin.py duplicates list | resolve-all
python scripts/dap_admin.py split-albums list | merge … | dismiss <key>
python scripts/dap_admin.py library consolidate            # dry-run preview
python scripts/dap_admin.py library consolidate --apply
python scripts/dap_admin.py library retag                  # sync tags to DB
```

### Running it with an AI agent

Every GUI action has a CLI/API equivalent with structured output, dry-runs, and
idempotent applies — which makes DAPManager well-suited to being operated by an
AI agent. See **[docs/agent-operations.md](docs/agent-operations.md)** for the
maintenance runbook, a safety classification (read-only vs. reversible vs.
destructive), and how to verify results.

---

## Known limitations / next steps

- **MBID mismatch:** if the master's auto-tagger resolves a *different* MBID than
  the satellite's guess, the "did we get it?" check can miss and ask for an
  upload unnecessarily. The attempt-timeout
  (`contribution_attempt_timeout_seconds`, default 1h) bounds the worst case to
  an eventual upload rather than a stall. Tightening this is a good follow-up.
- **Swagger UI loads from a CDN (unpkg).** `/api/openapi.json` has no
  dependencies, but the interactive `/docs` page needs internet. Vendor
  swagger-ui into `web/static` if you need fully offline docs.
- **Tauri desktop surface** for the contribution flow isn't built yet (the web
  UI is complete).

---

## Repo map (orientation)

- `web_server.py` — Flask app, all HTTP endpoints.
- `src/` — backend modules (db, downloader, sync, scanner, clients…).
- `web/templates/` — server-rendered pages (`index`, `library`, `fleet`,
  `contributions`, `orphans`, `setup`, `docs`).
- `scripts/` — Windows/POSIX bootstrap + setup (`setup-master.ps1`,
  `bootstrap-master.ps1`, …), plus `dap_admin.py` (admin CLI) and
  `test_satellite_e2e.py` (end-to-end smoke test).
- `src/split_album_detector.py` — split-album detection, edition consolidation,
  retag-from-DB.
- `src/tag_service.py` — file tag read/write, incl. `update_album_tags()`
  (targeted album-level writer).
- `docs/` — `onboarding.md` (master/satellite deployment), `cowork-setup.md`
  (browser/agent setup), `library-maintenance.md` (duplicates/splits/editions/
  retag), `agent-operations.md` (AI-agent runbook), `roadmap.md`,
  `desktop-rewrite.md`.
- `tests/` — pytest suite (`tests/e2e/` is the satellite end-to-end suite).
