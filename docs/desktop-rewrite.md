# Desktop Rewrite Plan — PySide6 → Tauri

Context: the PySide6 `desktop_app.py` is 1,595 LOC and covers the full
library + sync + audit surface. It's being replaced stage by stage by a
Tauri + React + TypeScript app in `desktop/`. The Python Flask backend
(`web_server.py`) is kept unchanged and runs as a sidecar process; the
frontend talks to it over HTTP.

This document is the stage plan — what's shipped, what's next, and why
that ordering. It complements the commit-series view (each stage is a
`feat(desktop): ... (Stage N)` commit) with the *scope* and *rationale*
that don't fit in a commit message.

---

## Shipped stages

| Stage | Commit | Scope |
|---|---|---|
| 1 | `6625125` | Tauri scaffold. Rust `main.rs` boots `web_server.py` as a child process via the Tauri sidecar pattern; React/Vite frontend polls `/api/healthz` until ready. `backend_url` invoke returns the live port to the frontend. |
| 2 | `ecfdcc9` | Album grid layout with Tailwind. Doppler-style 4-column card grid, embedded cover art served via `/api/library/albums/<id>/cover`. |
| 3 | `4f1eaeb` | Audio playback. `PlayerContext` owns a queue + the `<audio>` element; `PlayerBar` has transport + scrubber. `AlbumDetailScreen` starts a queue from a selected track. |
| 4a | `bc78df2` | Artists screen + artist detail (list artists, drill into albums). |
| 4b | `8a0dad6` | Songs screen — a sortable flat track table served by `/api/library/tracks` (no filters yet). |
| 4c | `533d334` | Up Next queue panel (jump / remove / clear, mirrors the PySide6 queue dialog). |
| 4d | `f9b289b` | Global search overlay (⌘K). Searches albums + artists + tracks via existing endpoints; keyboard navigation. |
| 5a | `626fe1d` | Playlists in sidebar (live from `/api/library/playlists`) + Songs scoping via `?playlist_id=...`. Header reflects the scoped playlist's name. |
| 5b | `4496daa` | Songs-screen filters: "Show catalog-only" (`local_only=0`) and "Show orphans" (`include_orphans=1`) toggles, plus a Status column with availability badges (`local`/`drive`/`catalog-only`/`missing`) and an orphan chip. Unavailable rows are dimmed and stripped from the play queue with a remapped start index. |
| 5c | `7f57839` | Sync screen replacing the placeholder. Sync All + per-step buttons (Pull Catalog / Pull Playlists / Push Playlists / Report Inventory / Link Local Files), each with a "Last: Nm ago" cursor from `/api/sync/state`. 2s `/api/status` poll drives a running-state strip and refreshes cursors on running→idle. |
| 5d | `f26f687` | Fleet screen replacing the placeholder. Devices table from `/api/fleet/summary` + a track-across-fleet search (`/api/fleet/track?q=...`) with per-result holder pills tooltipped to `local_path`. Role-unaware. Also extracts `lib/time.ts` for `relativeTime`. |
| 6 | `032116e` | Track + playlist context menus. Portal-based `ContextMenu` escapes table clipping; track rows get Add-to-Playlist submenu + Queue Download + Soft-Delete; playlist sidebar entries get Rename / Delete. New Playlist via a "+" accessory in the Playlists header. App-wide `Toast` context for mutation feedback. App owns a `playlistsVersion` counter that bumps on any mutation so Sidebar + SongsScreen re-fetch without prop-drilling. |
| 7a | `d562aed` + `c7d3b85` | Settings screen replacing the placeholder, driven by `/api/config` fieldsets. Backend change: `/api/config` now returns `groups` + `bool_keys` from `src/config_keys.py` so the desktop reads the single Python source of truth rather than drifting like the web dashboard's hardcoded copy did. Numeric values are coerced on save so re-typing a number doesn't trip the backend's `!=` diff. Scrolls + flashes a `focusKey` row when another screen routes here with a missing-key complaint. |
| 7b | `0340282` | Identify & Tag review dialog. Right-click a local row → `/api/tag/identify` → a modal with field-by-field before/after diff, tier-colored confidence pill, Apply/Cancel. Missing `acoustid_api_key` narrows via a discriminated union on the client and routes to Settings with `focusKey` set (reuses Stage 7a's scroll + flash). Disabled on non-local rows because the backend can't fingerprint without a local file. Cmd/Ctrl+Enter applies, Escape cancels. |
| 8a | `24cd34f` | Audit screen replacing the placeholder, paired with Complete-Albums trigger. Auto-loads `/api/audit/results` on mount; renders an album-cover grid with a per-card "N missing" badge + have/total counts. "Complete Albums" button POSTs `/api/albums/complete` with an optional "Also run downloader & rescan" checkbox; running-state strip + results refresh reuse SyncScreen's `/api/status` edge-trigger pattern. |
| 8b | `4223c36` | Orphans screen replacing the web `/orphans` page. Tabbed Tracks / Playlists tables from `/api/orphans/{tracks,playlists}`; row actions are Restore / Delete file (tracks, when `local_path` is set) / Purge with confirms before destructive ones. Playlist mutations bump `playlistsVersion` so the sidebar's live-list refetch covers both restore (re-appears) and purge (no-op visually but cheap to bump). |
| 8c | `35de8fd` | Resolve Duplicates screen replacing the PySide6 modal. Lists groups from `/api/duplicates` with per-group radios + "Skip this group"; recommended candidate (highest score) seeds the default. Single "Resolve N (M files)" CTA loops `/api/duplicates/resolve` per group and surfaces a deleted + errors summary toast. Groups with one candidate or skipped state are filtered from the plan. |

Sidebar currently has **Albums / Artists / Songs / Playlists / Audit /
Duplicates / Orphans / Sync / Fleet / Settings** wired to real screens
and **Downloads / New Releases** as `<Placeholder>` stubs.

---

## Stage 5 — Library management

**Goal.** Close the gap where a satellite user sees their library via
the Tauri app but can't actually manage it — no playlists, no sync
buttons, no way to surface catalog-only or orphan rows. This is the
first stage that starts *retiring* PySide6 rather than duplicating it.

**Sub-pieces (land in this order):**

### 5a — Playlists in the sidebar + Songs scoping — _Shipped (`626fe1d`)_

### 5b — Songs screen filters + availability badges — _Shipped (`4496daa`)_

Decisions worth preserving (not obvious from the diff):

- The filter strip lives below the `TopBar`, not inside it — the
  `TopBar` is shared across screens and owns the draggable titlebar
  region, so adding Songs-specific controls inline would have forced
  a prop explosion.
- `catalog-only OFF` maps to `local_only=1` (not the absence of the
  param). That keeps Songs a "what I can play locally" view by
  default and matches the web `/library` page's initial semantics.
- Rows with `availability === "unavailable"` are kept in the table
  (greyed out with a "missing" badge) so the user can *see* the
  soft-deleted row when `Show orphans` is on, but are removed from
  the play queue. `playFrom` remaps `startIndex` by walking `visible`
  in order — a filter-then-clamp approach would mis-target when an
  unavailable row precedes the clicked one.

### 5c — Sync screen — _Shipped (`7f57839`)_

Decisions worth preserving:

- The `TopBar`'s `search` prop is now optional rather than required,
  so the Sync screen can share the same draggable-titlebar header
  without a phantom search box. Other non-list screens (Fleet,
  Settings when they land) will benefit too.
- `postAction` is a single generic helper over every trigger
  endpoint instead of per-endpoint wrappers. The five buttons all
  return `{success, message}` in the same shape; the screen surfaces
  `message` verbatim on `success: false` so config-gated failures
  ("`master_url` not configured", "`report_inventory_to_host`
  disabled") land in the UI without the client needing to know which
  config keys each step requires.
- Cursor refresh uses a `wasRunning` ref + the existing 2s `/api/status`
  poll rather than a dedicated interval. Running→idle edge triggers
  `fetchSyncState()` once, and the immediately-succeeded case is
  covered by a `setTimeout(loadState, 2000)` after every POST. No
  polling while idle beyond the status tick itself.
- No scheduler config UI here — that lives in Settings (Stage 7).

### 5d — Fleet screen — _Shipped (`f26f687`)_

Decisions worth preserving (scope diverged from the original sketch):

- The original plan had "click a device row to see tracks it holds"
  but there's no backend endpoint for device→tracks — the fleet API
  only exposes device→mbid lookups. Matched the web `/fleet` page
  instead: device summary table + a separate fleet-wide track
  search. Adding device→tracks would need a new backend query and
  isn't obviously worth a whole endpoint just for the desktop side.
- Role-unaware. The web page doesn't gate by `is_master` either —
  satellites that never see reports just render the empty state.
  Gating would require a fresh `/api/config` call and only changes
  copy, not behavior.
- Search submits on Enter (or button click), not as-you-type. The
  backend does a triple `LIKE` across tracks; debounced live-search
  would be fine but the web UI is submit-on-Enter and matching it
  keeps muscle memory consistent.
- `relativeTime` moved to `lib/time.ts`. Two callers is the right
  inflection for extraction; deferring any longer would invite drift
  when a third caller lands.

---

**Stage 5 exit.** All four sub-stages shipped. A satellite user can
open the Tauri app and do every library-management action the
PySide6 app supports *except* the Audit / Complete-Albums /
Resolve-Duplicates / Identify-&-Tag / Settings flows (those are
Stages 6–8).

---

## Stage 6 — Track context menus & row actions — _Shipped (`032116e`)_

Decisions worth preserving:

- Single-track right-click, not the web's multi-select checkbox
  model. The web `/library` page right-click operates on whatever
  checkboxes are ticked ("N tracks selected"); the desktop uses
  right-click-the-row-under-the-cursor because that's the native
  desktop idiom. Shift-click ranges + Ctrl-click multi-add are a
  larger feature on their own and aren't gated on this stage.
- "Identify & Tag" is not in the menu yet — it needs the review
  dialog, which is Stage 7. Deferred so we don't ship a half-working
  entry that just hits the API without letting the user diff.
- `ContextMenu` dismisses on `pointerdown` (not `click`) so the
  outside-tap can still bubble through to whatever the user clicked
  — matches the web page's `document.addEventListener("click",
  closeContextMenu)` pattern, where clicking another row to open a
  different menu works in a single click rather than requiring a
  dismissing click + a fresh right-click.
- `addTrackToPlaylist` fetches the current membership and PUTs the
  merged list. The PUT endpoint is replace-not-append (that's the
  design so last-writer-wins across fleet conflicts), so the client
  has to compose. Same shape as the web page.
- App-level `playlistsVersion` counter (bumped on every mutation)
  instead of lifting the whole playlists list to App state. Sidebar
  and SongsScreen each own their own fetch; the counter is just an
  invalidation signal. Cheaper than a shared cache and avoids the
  "who owns the source of truth" question for one client-side list.
- `useToast` replaces the per-screen toast state I used in Sync/
  Fleet. Migrating those two screens over is a cleanup for a later
  commit — not worth bundling into Stage 6.

---

## Stage 7 — Settings + Identify & Tag — _Shipped (7a: `c7d3b85`, 7b: `0340282`)_

Decisions worth preserving:

- Settings is a **screen** not a modal. The sidebar already had a
  "Settings" entry and every other stage-5+ target is a full screen;
  a modal would have been the outlier. If a modal variant is ever
  needed (e.g. scoped edit overlays from elsewhere in the app), the
  component renders groups from `/api/config` and is easy to wrap.
- **Backend change first.** `/api/config` now returns `groups` +
  `bool_keys` from `src/config_keys.py`. The web dashboard already
  drifted from the Python source (no Lidarr group); the desktop
  reads the API directly so the same drift can't happen twice. The
  web dashboard cleanup is a separate job.
- **Numeric coercion on save.** If the original value for a key was
  a number, the Save patch coerces the string input back to Number
  before POSTing. The web dashboard sends everything as a string,
  which means editing `sync_interval_seconds` from `5` to `5` still
  trips the backend's `!=` diff and gets written as `"5"`. Desktop
  avoids that.
- **Focus-key via scroll + flash**, not a persistent banner. The
  toast surfaces the reason ("acoustid_api_key not set"); Settings
  scrolls the target row into view and flashes an accent ring. If
  the field is off-screen the scroll anchors it mid-viewport. A
  persistent banner could be added later if this doesn't prove
  explanatory enough.
- **Identify & Tag is gated on `availability === "local"`.** The
  backend requires `local_path`; surfacing a 404 from the apply path
  is worse UX than a grayed menu item. "drive" rows technically have
  a file on a mounted drive but `local_path` is empty (they live at
  `dap_path` only), so they're disabled too.
- **Discriminated-union return from `identifyTrack`.** The client
  distinguishes `match` / `no_match` / `needs_config` / `error` at
  the API boundary so SongsScreen can route each case to the right
  UI (dialog / toast / Settings / toast) instead of string-matching
  on the error message in the component.

---

## Stage 8 — Remaining library actions (PySide6 parity closers)

The long tail, batched because none of them are big on their own and
none block anything else.

### 8a — Audit screen + Complete-Albums trigger — _Shipped (`24cd34f`)_

Decisions worth preserving:

- **Audit + Complete are one screen, not two.** PySide6 splits them
  across two toolbar buttons (Audit opens a list dialog, Complete is
  a separate Yes/No prompt) but the conceptual flow is "see what's
  missing, then fix it." Combining them gets two of the five Stage 8
  sub-tasks in a single screen with a shared running-state strip and
  no extra sidebar entry to invent. Each Complete run also re-fires
  the audit query on the running→idle edge, so the user sees the
  list shrink without touching anything.
- **No `/api/audit` POST.** That endpoint kicks the legacy file-
  logged audit task; the desktop reads `/api/audit/results` directly
  (which is just a DB query for incomplete albums + cover art URL
  enrichment). Same shape as the web dashboard's audit pane.
- **Cover art straight from coverartarchive.org**, not proxied
  through the backend. The audit results endpoint already attaches
  `https://coverartarchive.org/release/<mbid>/front-250` URLs; the
  card uses `loading="lazy"` and an `onError` fallback so missing
  releases don't break the grid.
- **Confirm prompt on Complete.** The downloader/rescan path
  rewrites tags and downloads files; a one-click trigger felt too
  loose for a destructive-ish operation. Mirrors the PySide6
  `QMessageBox.question`. The downloader checkbox lives next to the
  CTA so the user can flip it without a separate dialog.

### 8b — Orphans screen — _Shipped (`4223c36`)_

Decisions worth preserving:

- **Picked next on value/work ratio.** Stage 6 added the soft-delete
  path from the desktop, so orphans now accumulate from desktop use
  itself — making the cleanup view recurring rather than occasional.
  Work was small (two read endpoints + restore/purge/delete-file
  endpoints already existed) so this is the leanest path to closing
  a loop the user opens from the desktop.
- **Tabs, not stacked sections.** The web `/orphans` page uses tabs
  for the same two tables; matched it because the table shapes are
  unrelated (track rows vs playlist rows) and stacking burns
  vertical space when one side is empty.
- **Delete file is hidden when `local_path` is empty.** Idempotent
  for missing files at the backend, but offering an action that does
  nothing visible is worse UX than just hiding the button.
- **Bump `playlistsVersion` after both restore and purge.** Restore
  re-adds the playlist to the sidebar's live list; purge is a no-op
  for the sidebar (the row was already hidden) but bumping is cheap
  and avoids a "did I update both directions?" footgun.

### 8c — Resolve Duplicates screen — _Shipped (`35de8fd`)_

Decisions worth preserving:

- **Single batched "Resolve N" CTA, not per-group buttons.** PySide6
  uses one OK button that loops; matched it because the typical case
  is "accept the recommendations across the board" and a per-group
  button forces the user into N×click for the same outcome. Per-
  group state is still a radio so individual edits cost nothing.
- **Sequential POSTs, not a single bulk endpoint.** The backend's
  `/api/duplicates/resolve` takes one mbid at a time. Adding a bulk
  variant would only help if the round-trip latency stacked up —
  duplicate counts are typically <50, so the chatty version stays
  simpler and surfaces per-group errors naturally.
- **Skip = empty keep_path on the client side.** The PySide6 dialog
  uses a sentinel "Skip this group" radio with `keep_path = ""`;
  matched it. Plans where `keep_path === ""` or where no paths would
  be deleted are filtered before resolve, so the count on the CTA
  reflects what the loop will actually touch.
- **Refresh after resolve.** Resolved groups disappear from the
  result of `/api/duplicates` (the backend clears the duplicate
  entry), so a re-fetch is the simplest way to show progress without
  tracking partial state in the screen.

### Remaining sub-tasks

- **Suggest to Jellyfin** — Jellyfin-pull flow.

Once Stage 8 lands, `desktop_app.py` can be retired — the Tauri app
will cover every toolbar/menu action it exposes. The retirement is a
separate commit that removes the PySide6 file, the `PySide6` dep from
`requirements.txt`, and the `test_desktop_app.py` test module.

---

## Stage 9 — Musicat-inspired polish

After parity, move *beyond* PySide6. See
`memory/reference_design_musicat.md` for the pool of ideas. Likely
picks:

- Waveform-based seeker in `PlayerBar` (canvas rendering an offline
  `AudioContext.decodeAudioData` pass on `play()`).
- Smart playlists (rule-based, evaluated server-side). Needs new
  schema: `smart_playlist_rules` table or a JSON column on
  `playlists`.
- Artist infoscreen: Wikipedia summary + discography timeline.
  Read-only; Wikipedia lookup via existing `contact_email` UA.
- Mini-player window mode (Tauri supports multi-window; a compact
  always-on-top variant when the main window is hidden).
- Listening stats tab (needs a `play_events` table — schema change,
  not trivial).

Stage 9 is explicitly post-parity and exploratory; don't schedule
until Stage 8 lands and the PySide6 app is gone.

---

## Keeping this doc current

Update the "Shipped stages" table as each stage lands (same cadence
as the main `roadmap.md`). When a stage's scope changes mid-flight,
amend the Stage section rather than mentioning it only in the commit
message — a future session should be able to open this file cold and
know exactly where the rewrite stands.
