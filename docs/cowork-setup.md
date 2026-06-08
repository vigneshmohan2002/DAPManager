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

## Contribution flow (satellite → master)

A satellite offers a track by identifier + quality. The master first tries to
download it itself; only if it can't match the quality does it ask the
satellite to upload the file (verified for size/quality before ingest). See the
**Contributions** tag in `/docs`.
