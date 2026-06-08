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
  `bootstrap-master.ps1`, …).
- `docs/` — `onboarding.md` (master/satellite deployment), `cowork-setup.md`
  (browser/agent setup), `roadmap.md`, `desktop-rewrite.md`.
- `tests/` — pytest suite.
