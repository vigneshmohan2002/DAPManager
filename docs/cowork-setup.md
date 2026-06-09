# Setting up DAPManager from a browser (Claude Cowork runbook)

DAPManager can be configured end-to-end from a single Chrome tab — no terminal
needed. This page is the quick runbook; the **interactive API reference is at
`/docs`** (Swagger UI), backed by the machine-readable spec at
`/api/openapi.json`. Both are reachable *before* the app is configured.

## TL;DR for an agent

1. `GET /api/healthz` → expect `{"ok": true, ...}` (backend is up).
2. `GET /api/setup/status` → if `{"needs_setup": true}`, configure it; else skip to step 4.
3. `POST /api/save_config` with a role + paths (see below). Writes `config.json`.
4. `GET /api/config` → read back config + editable-field metadata.
5. Satellite only: `POST /api/sync/all` (pulls catalog, contributes local tracks)
   or `POST /api/contribute` (contribute only).

When `api_token` is set in config, send `Authorization: Bearer <token>` on every
`/api/*` call except `/api/healthz`, `/api/status`, and `/api/openapi.json`. In
open mode (no token) the API is unauthenticated — keep it on LAN/Tailscale only.

## In the browser (human or computer-use)

- Open `http://<host>:5001/` → a fresh install redirects to **`/setup`**.
- The wizard: pick a **role** (Master / Satellite / Standalone), fill paths, and
  (satellite) the **Master URL**. Save → you land on the Dashboard.
- **Settings** card (Dashboard) edits any config key — it renders from
  `/api/config`, so it always matches the backend (including
  `contribute_to_host` and `contribution_attempt_timeout_seconds`).
- **Contribute** button (Dashboard → Multi-Device Sync) and the **Contribute to
  master** item in the Library right-click menu push local tracks up.
- **Contributions** page shows what each satellite has offered and its status.

## Roles

| Role | Needs | Notes |
|------|-------|-------|
| `master` | `music_library_path`, `downloads_path`; optional `public_master_url`, Jellyfin/Soulseek/Lidarr creds | Holds the canonical catalog. |
| `satellite` | `music_library_path`, `downloads_path`, `master_url` | Pulls the catalog; `contribute_to_host` defaults on. |
| `standalone` | `music_library_path`, `downloads_path` | Single-device master with its own downloader. |

Paths in `save_config` / `validate-path` are on the **host running
DAPManager**, not the browser machine.

## This machine (Windows master)

Live services on this host:

| Service    | URL                          | How it runs                           |
|------------|------------------------------|---------------------------------------|
| DAPManager | http://localhost:5001        | Docker container `dapmanager`         |
| Lidarr     | http://localhost:8686        | Docker container `lidarr`             |
| Prowlarr   | http://localhost:9696        | **Native Windows process** — do NOT start a Prowlarr Docker container, port 9696 is taken |
| slskd      | http://localhost:5030        | Docker container `slskd`              |
| Soularr    | (internal)                   | Docker container `soularr`            |
| Jellyfin   | http://localhost:8096        | Docker container `jellyfin`           |

**Lidarr URL from inside the container:** use `http://host.docker.internal:8686`,
not `http://localhost:8686` — `localhost` inside the container refers to the
container itself.

**Windows shell scripts / CRLF:** `.sh` files checked out on Windows may have
CRLF line endings, which cause `exec: no such file or directory` inside the
container. If you re-clone or the entrypoint breaks, run:
```powershell
Get-ChildItem scripts\*.sh | ForEach-Object {
    $c = [IO.File]::ReadAllText($_.FullName) -replace "`r`n","`n"
    [IO.File]::WriteAllText($_.FullName, $c, [Text.UTF8Encoding]::new($false))
}
```
Then rebuild: `docker build -t dapmanager:latest . && docker restart dapmanager`.

## Self-hosted runner (Windows dispatch)

You can trigger operations on the Windows machine directly from a Claude Code
remote session — no port forwarding, Tailscale, or VPN required. The runner
makes an **outbound** HTTPS connection to GitHub, so it works behind any NAT.

### One-time runner setup (on the Windows machine)

1. Open **github.com → repo → Settings → Actions → Runners → New self-hosted runner**.
2. Choose **Windows** and follow the download/configure steps exactly as shown.
   - When `./config.cmd` asks for labels, add: `windows` (the workflow targets
     `[self-hosted, windows]`).
3. Install as a persistent Windows service so it survives reboots:
   ```powershell
   cd C:\actions-runner   # wherever you extracted the runner
   .\svc.sh install
   .\svc.sh start
   ```
   (Use `svc.cmd` if `svc.sh` isn't present — the installer generates the right one.)
4. Verify: the runner shows as **Idle** on the GitHub Runners page.

### Dispatching from Claude Code

The workflow is `.github/workflows/windows-dispatch.yml`. Claude Code can
trigger it via the GitHub MCP `actions_run_trigger` tool. Available operations:

| `operation`   | `args`                             | What it does                                        |
|---------------|------------------------------------|-----------------------------------------------------|
| `health-check`| —                                  | Hits each service's health endpoint; fails if any is down |
| `restart`     | —                                  | `docker restart` all running containers             |
| `update`      | —                                  | `git checkout master` + `docker build` + `docker restart dapmanager` |
| `run-script`  | script name without `.ps1`, e.g. `setup-master` | Runs `scripts/<name>.ps1` |
| `shell`       | raw PowerShell                     | Executes arbitrary PowerShell (private-repo-only escape hatch) |

Example: ask Claude Code to "restart the Windows containers" and it will call
`actions_run_trigger` with `operation=restart`.

## Contribution flow (satellite → master)

A satellite offers a track by identifier + quality. The master first tries to
download it itself; only if it can't match the quality does it ask the
satellite to upload the file (verified for size/quality before ingest). See the
**Contributions** tag in `/docs`.
