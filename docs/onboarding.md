# Onboarding — Master setup wizard + satellite client distribution

Status: **active stage** (post-Stage 8, pre-Stage 9). Stage 9
(Musicat-inspired polish) is parked until this lands.

The goal is a two-click satellite onboarding flow:

1. On a fresh Mac, visit `http://<master-tailscale>:5001/` in a
   browser. The download page hands you a `.app.zip` and a copy-link.
2. Drag `DAPManager.app` into `/Applications`, right-click → Open the
   first time (unsigned). The app launches, finds itself already
   pointed at the master, and lands on the album grid with no manual
   config.

Everything seeded into the satellite stays editable from the
Stage 7a Settings screen.

---

## Decisions (locked in this stage)

- **Binary source: GitHub Actions release.** CI builds the Tauri Mac
  bundle on `macos-latest` and attaches `DAPManager-mac.zip` to a
  tagged release. The master lazy-fetches on first request to
  `/download/mac` and caches by version.
- **URL injection: stream-rewrite the zip.** Master copies the cached
  base archive into a `BytesIO` and adds `Contents/Resources/master_url.txt`
  + `Contents/Resources/master_token.txt`. Tauri reads on first launch.
  Sign-and-modify is a non-issue because the bundle is unsigned.
- **Public URL: Tailscale MagicDNS, auto-detected, user-overridable.**
  Wizard runs `tailscale status --json` and pre-fills the suggestion.
  User can edit (multiple Tailnets, custom hostnames, mixed LAN+VPN,
  etc.) and the value is saved as `public_master_url` in config.
- **Auth: token-gate `/download/mac` and embed.** When master has
  `api_token` set, the download endpoint enforces it (so only people
  who already know the token can pull a pre-authed satellite); the
  zip carries the token alongside the URL. Open mode (no token)
  serves the artifact unauthenticated and embeds nothing.
- **Wizard: heavier rewrite, multi-step stepper.** The current 196-
  LOC single-form `setup.html` is replaced. The "Set up other
  devices" step at the end is the bridge that makes the download
  endpoint discoverable.
- **Unsigned `.app` is acceptable** for now. If notarization gets on
  the menu later, the fallback is the deep-link variant (Plan B in
  the original analysis) where the master serves an unmodified zip
  + a `dapmanager://configure/<base64>` button — modifying signed
  bundles invalidates the signature, so a deep-link handoff
  becomes the right primitive at that point.

---

## Architecture

### Piece 1 — `/download/mac` endpoint

```
satellite browser ──GET /download/mac──▶ master Flask
                                         │
                                         ├─ token check (if api_token set)
                                         │
                                         ├─ resolve cached base zip
                                         │   └─ if missing: GET github.com/.../releases/.../DAPManager-mac.zip → cache/
                                         │
                                         └─ stream-rewrite:
                                             open base zip
                                             copy entries 1:1 into BytesIO zip
                                             add Contents/Resources/master_url.txt
                                             add Contents/Resources/master_token.txt (if set)
                                             yield as application/zip
```

Implementation pieces:

- **`src/satellite_bundle.py`** (new) — owns the cache + the rewrite.
  - `ensure_cached_bundle(version) -> Path` — fetches from GH if absent.
  - `inject_master_config(base_path, master_url, token) -> bytes` —
    pure stream-rewrite, returns the modified zip bytes (or yields
    chunks for streaming responses).
- **`web_server.py`** — new `/download/mac` route.
  - Reads `public_master_url` from config (fallback: `request.host_url`).
  - Reads `api_token` from config; if set, enforces it on this route
    and embeds it in the artifact.
  - Returns a `application/zip` response with `Content-Disposition:
    attachment; filename=DAPManager-mac.zip`.

The cache key is the GH release tag pinned in a Python constant
(e.g. `DESKTOP_RELEASE_TAG = "desktop-v0.1.0"`). Bumping the constant
in a Python release triggers a re-fetch; deletion of the cache file
forces it.

### Piece 2 — Tauri first-run pickup

On Tauri launch, before the React app boots:

- Rust side (`src-tauri/src/main.rs` or a setup hook): if no
  `config.json` exists in the app-data dir, look for
  `Contents/Resources/master_url.txt` (resolved via
  `app.path().resource_dir()`). If present, read it + the optional
  `master_token.txt` and write a minimal config:
  ```json
  {
    "role": "satellite",
    "dap_manager_host_url": "<value>",
    "api_token": "<value-if-present>"
  }
  ```
- React side: existing Stage 7a Settings flow renders the seeded
  values; they're editable normally. If the user clears the field
  and saves, the seeded value is gone.

The choice to write to `config.json` (and not pass the values via a
Tauri command on every load) means the seeding is one-shot. After
the first launch the file is the source of truth; future launches
ignore the resource files. That keeps the "edit from Settings" loop
working without a "but the resource file overrides me" footgun.

### Piece 3 — Setup wizard rewrite

Replace `web/templates/setup.html` with a six-step stepper. State
lives client-side in the template JS; only the final submit hits
the backend. Each step has Back / Next; Next is disabled until the
step's required fields are valid.

| # | Step | Fields | Notes |
|---|---|---|---|
| 1 | Role | role: master / satellite / standalone | Defaults to master. Changes which subsequent steps render. |
| 2 | Library paths | music_library_path, downloads_path, dap_mount_point | Inline-validated via `POST /api/setup/validate-path`. |
| 3 | Public URL | public_master_url (auto-filled from `tailscale status --json` if available) | Master + standalone only; satellite uses `dap_manager_host_url` instead. |
| 4 | Integrations | jellyfin_url + key + user_id, lidarr_url + key, spotify_client_id + secret, acoustid_api_key | All optional. Collapsed by default; expand-per-service. |
| 5 | Auth | api_token (optional; "leave blank for open LAN mode") | Generates a default with a "regenerate" button if user wants one. |
| 6 | Done — share with devices | (no fields) | Shows `/download/mac` URL, "Copy link" button, QR code. Master + standalone only. |

Backend changes:

- **`web_server.save_config`** switches to
  `src.first_run.build_initial_config(role, **payload)` so the
  payload validates centrally and `src/first_run.py` becomes the
  single source of truth for first-run shape (closes the duplication
  noted when retiring `desktop_app.py`).
- **`/api/setup/validate-path`** — POST `{path: str, kind:
  "directory"|"file"}` → `{ok: bool, message?: str}`. Used by step 2.
- **`/api/setup/detect-tailscale`** — GET → `{detected: bool, url?:
  str, hostname?: str, error?: str}`. Subprocesses `tailscale status
  --json`, parses `Self.DNSName` (strip trailing dot), composes
  `http://<dnsname>:<listen_port>`. Used by step 3 to pre-fill.

QR code in step 6: a tiny client-side library (`qrcode-svg`,
`~5KB`) renders the download URL inline. Avoids a server round-trip
and keeps the wizard a single static template.

---

## Tailscale auto-detect

`tailscale status --json` returns:

```json
{
  "Self": {
    "DNSName": "mybox.tail47bdc0.ts.net.",
    "TailscaleIPs": ["100.99.187.41", "fd7a:..."]
  },
  ...
}
```

Detection logic:

1. Subprocess `tailscale status --json` with a 2s timeout.
2. If exit 0 and `Self.DNSName` is non-empty: strip trailing dot,
   compose `http://<dnsname>:<port>` where `port` = the master's
   listen port (from env `DAPMANAGER_PORT`, default 5001). Return.
3. Else if `Self.TailscaleIPs[0]` (IPv4) is set: fall back to that.
   MagicDNS may be disabled in the Tailnet — IP still works.
4. Else (binary missing, daemon down, exit non-zero): return
   `{detected: false}` and let the wizard show an empty field.

The wizard pre-fills but never blocks on the detection — if the
user knows their address better (custom DNS, reverse proxy, port
override), they overtype.

---

## Sub-stage breakdown (ranked by value/work)

Each is one feat commit + one docs commit, same cadence as Stage 8.

### 9a — CI Mac build *(no app changes; highest ratio)*

- New `.github/workflows/desktop-mac.yml`. Triggers on tags
  matching `desktop-v*`. Runs on `macos-latest`. Caches
  `~/.cargo`, `desktop/node_modules`, `desktop/src-tauri/target`.
- Steps: `cd desktop && npm ci && npx tauri build --bundles app`.
  Zip the `.app`. Attach as a release asset named
  `DAPManager-mac.zip`.
- Acceptance: pushing a `desktop-v0.0.1` tag produces a release with
  a downloadable zip. No DAPManager source changes.

### 9b — `/download/mac` endpoint + bundle injection

- Add `src/satellite_bundle.py` (cache + zip rewrite).
- Add `/download/mac` route in `web_server.py`. Token-gated when
  `api_token` is set. Streams the rewritten zip.
- Add `tests/test_satellite_bundle.py` covering: cache hit, cache
  miss, fetch failure, injection adds the right entries without
  breaking existing ones.
- Acceptance: `curl -L http://localhost:5001/download/mac > out.zip
  && unzip -p out.zip Contents/Resources/master_url.txt` echoes the
  configured URL.

### 9c — Tauri first-run pickup

- `src-tauri/src/main.rs` setup hook: read resource files, write
  `config.json` if absent. Tested by mocking
  `app.path().resource_dir()`.
- Existing api.ts wiring already routes `dap_manager_host_url` to
  Settings — no React changes needed unless the seeded path differs.
- Acceptance: launching the app from a `.app` whose
  `Resources/master_url.txt` is `http://x.ts.net:5001` lands on the
  album grid with that URL pre-filled in Settings, and the Sync
  screen's status strip shows it as the live `master_url`.

### 9d — Wizard steps 1–5 (skeleton + validation)

- Replace `setup.html` with a stepper template + JS state machine.
- Add `/api/setup/validate-path` and
  `/api/setup/detect-tailscale`.
- `web_server.save_config` switches to `build_initial_config`.
- Acceptance: walking through the wizard end-to-end on a fresh
  install produces an identical-shape `config.json` to the current
  flow (diff-tested in `tests/test_setup_flow.py`).

### 9e — Wizard step 6 (share with devices)

- Final step renders `/download/mac` URL, a Copy button, and an
  inline SVG QR code.
- Acceptance: completing the wizard lands on a screen with a
  working download link; clicking it on a *different* Mac on the
  Tailnet retrieves a configured `.app`.

Ordering: 9a → 9b → 9c form the end-to-end satellite story. 9d can
land in parallel with 9b/9c since it's independent. 9e gates on 9b
being live.

---

## Acceptance for "Onboarding stage done"

- Fresh master, fresh satellite, both on the same Tailnet.
- Master operator runs the master, walks the wizard, lands on step
  6, copies the download link.
- Satellite operator opens the link in a browser on the satellite
  Mac, downloads, drags to `/Applications`, right-click → Open.
- Satellite app launches and shows the master's album grid without
  the operator typing anything anywhere.
- Token mode (master has `api_token` set) and open mode both work
  with the same flow.

When all of that is true, this doc gets a *Shipped* marker per
sub-stage like `desktop-rewrite.md`, and Stage 9 (Musicat polish)
becomes the next focus.
