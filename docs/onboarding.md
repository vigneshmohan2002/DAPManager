# Onboarding — Master setup wizard + satellite client distribution

Status: **shipped** (all six sub-stages, 9a–9f). Stage 9
(Musicat-inspired polish) is the next focus.

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
- **Public URL: env-var-first, populated by a host-side bootstrap
  script.** The reference master deployment is a Docker container on
  a Windows host, so `tailscale status --json` from inside the
  container can't see the host's Tailscale state. The host-side
  bootstrap script (see Architecture below) detects the Tailscale
  URL on the host and passes it through to the container as
  `MASTER_PUBLIC_URL`. Detection priority *inside the container*:
  1. `MASTER_PUBLIC_URL` env var — populated by the bootstrap
     script in the standard path; can also be set manually in
     `docker-compose.yml` or `docker run -e`.
  2. In-container `tailscale status --json` — fires only if
     Tailscale is running *inside* the container (sidecar pattern)
     or for non-Docker installs.
  3. Blank field; user types the URL in the wizard.

  Whatever the wizard ends up with is saved as `public_master_url`
  in `config.json` and is fully editable from Settings later.
  `request.host_url` is **not** used as a fallback — Docker Desktop
  serves it as `http://localhost:5001` from the operator's browser,
  which is not reachable from satellites.
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

### Piece 0 — Host-side bootstrap script

The Docker container can't see host Tailscale state, so the
detection has to happen on the host *before* `docker compose up`.
Two scripts ship side-by-side in `scripts/`:

- **`scripts/bootstrap-master.ps1`** (Windows hosts, primary
  deployment).
- **`scripts/bootstrap-master.sh`** (Linux/macOS hosts, or Windows
  hosts running WSL/Git Bash).

Each does the same three things:

1. Run the local Tailscale CLI (`tailscale status --json` on POSIX,
   `tailscale.exe status --json` on Windows). Parse `Self.DNSName`,
   strip the trailing dot, compose `http://<dnsname>:<port>` (port
   from a `MASTER_PORT` env var, default 5001). On failure, exit
   with a clear "Tailscale not detected — set MASTER_PUBLIC_URL
   manually and re-run" message rather than starting the container
   with a bad URL.
2. Export `MASTER_PUBLIC_URL` (and `MASTER_PORT` if overridden) into
   the env, then run `docker compose up -d`.
3. Print the resulting download link (`<MASTER_PUBLIC_URL>/download/mac`)
   so the operator can copy it to other devices without opening
   the wizard at all once the master is past first-run.

The script is the *recommended* startup path; running `docker
compose up` directly still works as long as the operator sets
`MASTER_PUBLIC_URL` themselves.

`docker-compose.yml` reads:

```yaml
services:
  dapmanager:
    environment:
      MASTER_PUBLIC_URL: ${MASTER_PUBLIC_URL:-}
```

so a missing env var passes through cleanly to the wizard's manual-
entry path.

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
  - Reads `public_master_url` from config. Refuses to serve (HTTP
    409 with a clear message) when it's unset, since serving a
    bundle pinned to `localhost` or a LAN IP that isn't reachable
    from satellites is a worse outcome than failing loudly.
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
| 3 | Public URL | public_master_url (auto-filled from `MASTER_PUBLIC_URL` env, then in-container `tailscale status --json`, else blank) | Master + standalone only; satellite uses `master_url` instead. Help text explains the env-var path for Docker users. |
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
- **`/api/setup/detect-public-url`** — GET → `{source: "env" |
  "tailscale" | "none", url?: str, hostname?: str, error?: str}`.
  Tries detection paths in order: `MASTER_PUBLIC_URL` env var, then
  `tailscale status --json` (subprocess with 2s timeout, parses
  `Self.DNSName`, strips trailing dot, composes
  `http://<dnsname>:<listen_port>`). Returns the first hit. Used by
  step 3 to pre-fill; the wizard surfaces `source` so the help text
  can explain *where* the suggestion came from ("from
  `MASTER_PUBLIC_URL`" vs "from local Tailscale").

QR code in step 6: a tiny client-side library (`qrcode-svg`,
`~5KB`) renders the download URL inline. Avoids a server round-trip
and keeps the wizard a single static template.

---

## Public URL detection

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

The same parsing logic appears in two places — once in the host
bootstrap script (POSIX + PowerShell variants), once in
`/api/setup/detect-public-url` for non-Docker installs and the
sidecar-Tailscale case:

1. Subprocess `tailscale status --json` with a 2s timeout.
2. If exit 0 and `Self.DNSName` is non-empty: strip trailing dot,
   compose `http://<dnsname>:<port>` where `port` = `MASTER_PORT`
   env (default 5001). Return.
3. Else if `Self.TailscaleIPs[0]` (IPv4) is set: fall back to that.
   MagicDNS may be disabled in the Tailnet — IP still works.
4. Else (binary missing, daemon down, exit non-zero):
   - Bootstrap script: exits with a clear error so the operator
     knows to set `MASTER_PUBLIC_URL` manually.
   - `/api/setup/detect-public-url`: returns `{source: "none"}` and
     lets the wizard show an empty field.

The wizard *prefers* `MASTER_PUBLIC_URL` from the env when set
(populated by the bootstrap script), only running its own detection
when the env is empty. Whichever path produces a URL, the user can
overtype it — multiple Tailnets, custom DNS, reverse proxies, port
overrides etc. all need the manual override.

---

## Sub-stage breakdown (ranked by value/work)

Each is one feat commit + one docs commit, same cadence as Stage 8.

### 9a — Host bootstrap scripts — _Shipped (`6fc110d`)_

Decisions worth preserving:

- **Two siblings, not one cross-shell script.** POSIX uses `tailscale
  status --json | python3` because python3 is universal on macOS/Linux
  and the JSON parse is two lines. PowerShell uses native
  `ConvertFrom-Json` and a `Resolve-TailscaleCli` helper that also
  checks `Program Files\Tailscale\tailscale.exe` since Windows
  installers don't always put the CLI on `PATH`. Trying to bridge both
  with WSL or pwsh-on-mac would have added a runtime dependency for no
  payoff — the scripts are short enough that duplicating the logic is
  cheaper than abstracting it.
- **Detection-failure exits 1, doesn't fall through.** If Tailscale
  isn't reachable we'd rather refuse to start than launch with an
  unset `MASTER_PUBLIC_URL` — the master would then serve a download
  bundle pinned to a URL satellites can't reach, which is a worse
  outcome than a loud failure. Operator override is documented in the
  exit message.
- **`MASTER_PUBLIC_URL: ${MASTER_PUBLIC_URL:-}`** in the compose file
  passes through *empty* when unset, so direct `docker compose up`
  without the bootstrap script still works (wizard prompts manually).
  An unset compose interpolation would error and block startup.
- **`--print-only` flag** lets operators verify the detected URL
  before booting the container — useful when debugging Tailnet
  routing or testing the script on a host that already has the master
  running.

### 9b — CI Mac build — _Shipped (`45b98f6`)_

Decisions worth preserving:

- **Universal binary, not single-arch.** `macos-latest` is arm64,
  but satellites can be on either Apple Silicon or Intel. Building
  with `--target universal-apple-darwin` (and adding both Rust
  targets via `dtolnay/rust-toolchain`) makes one zip work
  everywhere, at the cost of a slightly longer build. The bundle
  path moves from `target/release/...` to
  `target/universal-apple-darwin/release/...` — every step that
  references the artifact has to use the universal path.
- **`ditto`, not `zip`.** `ditto -c -k --sequesterRsrc --keepParent`
  is the macOS-native way to archive a `.app` while preserving
  resource forks and (later) any signatures. Plain `zip` strips
  metadata and can break Gatekeeper handling on the satellite side
  even before notarization is on the table.
- **`workflow_dispatch` alongside the tag trigger.** Lets the
  master operator (or anyone with write access) build a test bundle
  from a branch via the Actions tab without cutting a release tag.
  The "attach to release" step is gated on `startsWith(github.ref,
  'refs/tags/desktop-v')`, so dispatch builds upload the artifact
  but don't touch any release.
- **`Swatinem/rust-cache@v2` over hand-rolled cache.** It scopes
  the cache key to `Cargo.lock` automatically and handles target-
  dir invalidation correctly across runs. The `workspaces` input
  points it at `desktop/src-tauri` since that's where the Cargo
  workspace root lives.

### 9c — `/download/mac` endpoint + bundle injection — _Shipped (`4d69701`)_

Decisions worth preserving:

- **Inject per-request, not per-cache-write.** The cached zip on
  disk stays as the unmodified upstream artifact; injection runs at
  request time off `public_master_url` and `api_token` from
  config.json. Means an operator can rotate `public_master_url` or
  the token from Settings and the next download reflects it
  immediately without busting `cache/desktop-bundle/<tag>.zip`.
- **`?token=` query alongside `Authorization: Bearer`.** Browsers
  can't natively send Bearer headers from a clicked link, and the
  wizard's step-6 share screen needs a clickable URL. Query-string
  tokens are usually a leak risk, but here the deployment is
  Tailnet/LAN-only and the same token is embedded in the bundle the
  request returns — there's nothing in the URL the recipient
  doesn't already get. `hmac.compare_digest` covers the comparison.
- **`_read_master_config_for_download` re-reads config.json** rather
  than going through the `config` global. Right after the wizard
  completes, the in-process `init_app_logic` hasn't necessarily
  re-fired yet, so the global can be stale. Re-reading from disk
  costs one syscall per download and removes the timing dependency.
- **`/download/mac` is whitelisted in `check_setup`** so the route
  returns 409 with a useful message instead of 302-redirecting a
  satellite browser to `/setup`. A redirect to a setup wizard from
  a satellite POV is a confusing dead end.
- **`DATA_DIR` env var, not a new config field, anchors the cache.**
  Matches what `scripts/docker-entrypoint.sh` already exports;
  non-Docker installs fall through to `cwd/cache/desktop-bundle/`.
  Adding a `bundle_cache_dir` config key would have been one more
  thing for the Settings screen to surface — not worth it.
- **Fetch failure → 502, not 500.** The upstream is GitHub; a
  network blip there isn't a master bug. The 502 message points
  the operator at outbound connectivity rather than the master's
  logs.

### 9d — Tauri first-run pickup — _Shipped (`97c8b0e`)_

Decisions worth preserving:

- **Pure helper + thin Tauri wrapper.** `seed_satellite_config`
  takes `(config_path, resource_dir, home_dir)` as `&Path` so the
  whole thing is unit-testable with `tempdir`s — no `AppHandle`
  mocking required. The Tauri setup hook just resolves those three
  paths and calls in. 6/6 cargo tests cover the seed paths.
- **Seed writes a full satellite config**, not the 3-key shape the
  doc originally proposed. The Python backend's REQUIRED_KEYS gate
  rejects partial configs at boot, so a `{role, url, token}` seed
  would have made the satellite immediately broken. Defaults for
  paths land under `~/Music/DAPManager` and `~/Downloads/DAPManager`
  — operators can edit from Settings if they want elsewhere.
- **Dual-write `master_url` and `dap_manager_host_url`.** The two
  are parallel names for the same value: `master_url` is what the
  Python backend reads (sync, proxy-stream); `dap_manager_host_url`
  is what `SuggestScreen.tsx` reads. Until those are unified in a
  later cleanup, mirroring the URL into both keeps the satellite UI
  fully functional out of the box. Note for that future cleanup.
- **`DAPMANAGER_CONFIG` env var on spawn.** The Rust side computes
  the platform config path once and passes it explicitly to the
  Python child. This kills the timing window where the backend's
  `resolve_config_path()` cwd fallback would land on a different
  file than the one the Tauri seeder wrote to. One source of truth.
- **One-shot seed, never re-applied.** First check is "does the
  config file exist?" If yes, the resource files are ignored on
  every subsequent launch. Means edits made from Settings stick;
  re-running the same .app over and over doesn't clobber user
  changes. The "but the resource file overrides me" footgun the
  architecture doc warned about doesn't materialise.
- **Trim trailing slash on the seeded URL.** Matches what
  `build_initial_config` already does Python-side; otherwise a
  master URL that came from a copy-paste with `/` at the end
  produces `http://m:5001//api/...` calls.

### 9e — Wizard steps 1–5 — _Shipped (backend `b3820c1`, frontend `9018cd7`)_

Decisions worth preserving:

- **Two feat commits, not one.** Stage 8 used one feat per substage,
  but 9e was big enough that the backend (build_initial_config
  extension + endpoints + tests, ~350 LOC) and the frontend (template
  rewrite, ~600 LOC) were cleaner as separate commits. Pattern to use
  for any future substage that crosses ~500 LOC.
- **`save_config` defaults `role` to `master`** so the legacy single-
  form template wasn't a flag-day with the new stepper. The two
  commits could land minutes apart, but defaulting kept the system
  uninterrupted in either ordering — useful if anyone reverts only
  the frontend later.
- **Vanilla JS state machine, no framework.** A 100-LOC state object
  + `data-show-when` attribute + per-step `canAdvanceFrom` covers
  what React would have at 10× the bytes shipped to the browser.
  The wizard is one-shot; investing in a framework wouldn't pay back.
- **`data-show-when="role!=satellite"`** drives role-conditional
  visibility instead of branching the template into three flows.
  Steps 3 and 4 reshape themselves; the rest are role-agnostic.
  Keeps the markup linear and lets the same JS gating run regardless
  of role.
- **Path validation is on blur, not on every keystroke.** Filesystem
  checks per character would either spam the endpoint or need
  debouncing; blur is one round-trip per field and matches when the
  user actually expects feedback.
- **`public_master_url` is non-blocking at submit.** If detection
  fails and the user advances without typing one, the wizard saves
  the rest of the config; `/download/mac` (Stage 9c) is the gate
  that refuses to serve when the URL is unset, with a clear message
  pointing back at Settings. Blocking submit here would punish
  operators who *know* they'll fill it in later.
- **Token generation is client-side** via `crypto.getRandomValues`
  (24 hex bytes). No server round-trip means the secret never
  bounces through a request/response pair before the operator sees
  it. Same primitive will be reused on the Settings screen if a
  rotate-token button shows up there.
- **Collapsed integration sections** — Soulseek, Jellyfin, Lidarr,
  AcoustID. A fresh install with none of these still ends in three
  filled fields (paths) plus optional URL/token, instead of
  scrolling past 12 empty inputs. Lidarr is master-only, so the
  satellite/standalone flows don't see it at all.

### 9f — Wizard step 6 (share with devices) — _Shipped (`a015d78`)_

Decisions worth preserving:

- **Save between step 5 and step 6**, not at the end. Step 6's
  content (the share link) depends on the saved `public_master_url`
  — rendering it before save would mean trusting the user typed it
  identically twice. Saving first, then advancing, lets the share
  step render off the persisted value with no ambiguity.
- **Back disabled on step 6.** Once saved, going back to step 5 and
  hitting Next would re-submit. Locking the back button is cheaper
  than making the save endpoint defend against re-submits.
- **`?token=` in the share URL when one is set.** Browsers can't
  send Bearer headers from a clicked link, so the share link
  embeds the token via query string for the Mac-double-click flow.
  The wizard shows a "treat like a credential" warning so operators
  know what they're handing out. Open mode (no token) gets a
  different copy that says it's reachable to anyone on the Tailnet.
- **QR via CDN, fail-soft.** `qrcode@1.5.3` from jsdelivr is loaded
  with `defer`; `renderQr` checks for `window.QRCode` and silently
  hides the QR card if the lib didn't load (offline master, blocked
  CDN). The link + Copy button always work — QR is the nicety, not
  the contract. SVG output (`type: 'svg'`) keeps it crisp at any
  display size and stays inside the page DOM.
- **`navigator.clipboard.writeText` with a fallback message.** No
  hidden textarea + execCommand fallback; if clipboard is blocked
  the copy-status text tells the operator to select-and-copy
  manually. Modern browsers all support the async clipboard API on
  HTTPS / localhost / Tailnet hosts.
- **Satellite gets a "You're set" confirmation, not a hard
  redirect.** Symmetric flow — every role passes through six steps
  — and the confirmation page is a useful "hand-off, click
  Finish" beat instead of a jarring jump straight to the album
  grid.

Ordering: 9a unblocks the operator's Docker-on-Windows path. 9b →
9c → 9d form the end-to-end binary-distribution story. 9e (wizard
skeleton) can land in parallel with 9b/9c/9d since it's independent.
9f gates on 9c being live (download link works) and surfaces it in
the UI.

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

---

## Onboarding stage exit

All six sub-stages shipped. Real-world acceptance test still wants
a fresh master + fresh satellite on the same Tailnet to walk the
end-to-end flow once; until that happens the on-disk pieces work
in isolation but the loop is unverified. Carryover for the next
session:

- **`DESKTOP_RELEASE_TAG` bump after first CI release.** The
  constant in `src/satellite_bundle.py` is `desktop-v0.1.0` —
  needs to match whatever tag the 9b workflow attaches its first
  zip to before /download/mac will resolve.

Stage 9 (Musicat-inspired polish) is the next focus.
