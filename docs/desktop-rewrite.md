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

Sidebar currently has **Albums / Artists / Songs** wired to real screens
and **Downloads / New Releases / Fleet / Sync / Settings** as
`<Placeholder>` stubs.

---

## Stage 5 — Library management

**Goal.** Close the gap where a satellite user sees their library via
the Tauri app but can't actually manage it — no playlists, no sync
buttons, no way to surface catalog-only or orphan rows. This is the
first stage that starts *retiring* PySide6 rather than duplicating it.

**Sub-pieces (land in this order):**

### 5a — Playlists in the sidebar + Songs scoping

- New `Playlists` section in `Sidebar.tsx`, populated from
  `GET /api/library/playlists` (live playlists with `track_count`).
- Clicking a playlist scopes the Songs screen via
  `?playlist_id=...` on `/api/library/tracks`.
- `SongsScreen` learns to accept a `playlistId` prop and re-fetch.

### 5b — Songs screen filters

- Two toggles in the Songs `TopBar`: **Show catalog-only** (adds
  `?local_only=0`; default OFF so Songs stays the "what I can play
  locally" view), **Show orphans** (adds `?include_orphans=1`).
- Availability badge column: `local` / `drive` / `catalog-only` /
  `orphan`, using the `availability` and `orphan` fields the web
  library page already consumes.
- The remote-stream path (`availability === "remote"`) already works
  end-to-end via the master proxy; the badge just makes it visible.

### 5c — Sync screen

Fills the `sync` sidebar placeholder. Scope mirrors the web dashboard's
Multi-Device Sync card:

- **Sync All** button, plus individual **Pull Catalog / Pull Playlists
  / Push Playlists / Report Inventory / Link Local Files** buttons.
- Last-sync cursor timestamps next to each button
  (`GET /api/sync/state`), formatted as relative time.
- A live status strip reading `/api/status` so the user sees which
  sub-step is currently running when a task is in flight.
- No scheduler config UI — that lives in Settings (Stage 7).

### 5d — Fleet screen

Fills the `fleet` placeholder. Reads `GET /api/fleet/summary` and
renders one row per device with `{device_id, track_count, last_report}`.
Clicking a row opens a detail panel listing the tracks that device
holds (reuse `GET /api/fleet/track?mbid=...` on click). Master-only —
satellites show an explanatory empty state.

**Stage 5 exit criteria.** A satellite user can open the Tauri app
and do *every* library-management action the PySide6 app supports
*except* the Audit / Complete-Albums / Resolve-Duplicates /
Identify-&-Tag / Settings flows (those are later stages).

**Estimated effort.** 5a ~1–2h · 5b ~1h · 5c ~2–3h · 5d ~1–2h.

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
