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

Sidebar currently has **Albums / Artists / Songs / Playlists / Sync /
Fleet** wired to real screens and **Downloads / New Releases /
Settings** as `<Placeholder>` stubs.

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

## Stage 6 — Track context menus & row actions

Right-click on a track row should mirror the web `/library` page:

- **Soft-Delete** → `DELETE /api/tracks/<mbid>`
- **Queue Download** (for catalog-only rows) → `POST
  /api/catalog/queue-download`
- **Add to Playlist → …** submenu → `PUT /api/library/playlists/<id>`
  (compose new membership client-side, same shape as the web page)
- **Identify & Tag** (needs the review dialog — see Stage 7)

A portal-based menu component (React's `createPortal` to `document.body`)
so the menu escapes table clipping the same way the web page's does.

Also in this stage: the **New Playlist** / rename / delete UX on the
playlist sidebar entries, matching the web right-click menu.

---

## Stage 7 — Settings dialog + Identify & Tag review

The PySide6 Settings dialog has focus-key behavior (opens scoped to a
specific missing field) and is the gate for auto-tag. Ship both
together because Identify & Tag needs Settings to surface the
`acoustid_api_key` warning path cleanly.

- Settings modal reading/writing `/api/config`. Secret keys masked
  with placeholder, empty submit == "don't change".
- Focus-key: when the app detects a missing key (e.g. user tries
  Identify & Tag without `acoustid_api_key`), opens Settings scoped
  to that field with a "you need this" banner.
- Identify & Tag review dialog: before/after diff, confidence tier
  colouring, Apply/Cancel. Hits `/api/tag/identify/<mbid>` and
  `/api/tag/apply/<mbid>`.

---

## Stage 8 — Remaining library actions (PySide6 parity closers)

The long tail, batched because none of them are big on their own and
none block anything else:

- **Audit Library** → `/api/audit` + `/api/audit/results` + cover art
  panels, queue missing tracks for download.
- **Complete Albums** → `/api/albums/complete` with optional
  `run_downloads` flag.
- **Resolve Duplicates** → `/api/duplicates` + `/api/duplicates/resolve`.
- **Orphans page** → reuse the web `/orphans` table shape.
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
