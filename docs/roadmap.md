# DAPManager Delivered Roadmap: Post-Sync-Design Work

Status: **all items in this tranche shipped**. This is now a delivery record,
not an active backlog. The original gaps and design notes remain below to
preserve intent; each item records its delivered state.

The work built on the completed multi-device sync foundation: delta catalog +
playlist sync, inventory, fleet view, soft-delete/orphan recovery, and runtime
settings.

The parallel **Tauri desktop rewrite** is also complete: it replaced the
PySide6 client and subsequently closed contribution and suggestion parity.
Its staged delivery record lives in [`desktop-rewrite.md`](desktop-rewrite.md).

---

## High-value, low-effort

### 1. "Sync All" button + sync status widget

**Original gap.** Satellites required several manual operations to reconcile
with the master, and sync-cursor timestamps were not surfaced.

**Surface.**
- Web: a single "Sync All" button in the Multi-Device Sync card. Runs the
  applicable catalog, artist-tag, playlist, lyrics, inventory and contribution
  operations sequentially in a background thread.
- Web: a small widget next to each existing button showing
  "Last: <relative time>" read from `sync_state`.
- Desktop: one "Sync All" toolbar action; status bar shows the currently
  running sub-step (`Sync All: pulling catalog…`).

**API.**
- `POST /api/sync/all` — runs the full sequence as one `TaskManager` task.
  A failed sub-step is reported without preventing later steps from running.
- `GET /api/sync/state` — returns the catalog, artist-tag, playlist pull/push,
  lyrics, inventory and contribution cursors as ISO strings.

**Schema.** None. `sync_state` already has the keys; just expose them.

**Delivery decisions.**
- Sequencing: pull-catalog must finish before pull-playlists (playlist
  membership FKs expect the tracks to exist). The remaining applicable steps
  run serially because sync is not bandwidth-bound.
- One failed sub-step does not stop the rest; each step records its own
  success, skip or error result.

**Effort.** ~1–2 hours. No new machinery, just orchestration and a
`sync_state` read endpoint.

**Shipped (backend + web + desktop).** Sync All and background task progress
are available in both user interfaces; per-action cursor timestamps appear
where a manual control exists.

---

### 2. Scheduled background sync

**Original gap.** Even with "Sync All", users had to remember to click. For
the "set it and forget it" case the loop should run on its own.

**Surface.**
- Config keys:
  - `sync_interval_seconds`: integer, default `0` (disabled).
  - `sync_on_startup`: bool, default `false` — run one sync as soon as
    the server comes up.
- Optional indicator in the Multi-Device Sync card: "Auto-sync every 5 min"
  when configured.

**Implementation.**
- A simple daemon thread in `web_server.py` (or a new
  `src/sync_scheduler.py`) that wakes every `sync_interval_seconds`,
  checks that no manual task is currently running, and invokes the same
  "Sync All" entry point.
- Skips the tick if the previous run is still in flight.
- Logs each run at INFO; failures at WARNING with the sub-step that broke.

**Schema.** None.

**Delivery decisions.**
- The master may run the same scheduler; non-applicable remote pulls are
  skipped while its own inventory remains fresh.
- Repeated failures are logged without a separate backoff layer.

**Effort.** ~2–3 hours. Includes settings wiring and a basic integration
test that the thread starts, runs, and stops cleanly.

**Depends on** #1 ("Sync All") landing first so the scheduler has a
single callable.

**Shipped.** `SyncScheduler` supports interval and startup runs, skips
overlapping TaskManager work, and is configured through
`sync_interval_seconds` / `sync_on_startup`.

---

### 3. Orphan cleanup page

**Original gap.** Soft-delete stamped `deleted_at` but nothing cleared it.
Over time the tables accumulate tombstones. Users also need a way to
*undo* an accidental soft-delete and to hard-delete orphan files that
still sit on disk locally.

**Surface.**
- A new page: `/orphans`, linked from the dashboard under Multi-Device
  Sync (similar to how `/fleet` is linked).
- Two tabs: **Tracks** and **Playlists**.
- Track row columns: artist, title, album, deleted_at, local_path (if
  present), actions: **Restore**, **Purge row**, **Delete local file**.
- Playlist row columns: name, deleted_at, membership count, actions:
  **Restore**, **Purge**.
- Bulk actions: select-all + Purge selected / Restore selected.

**API.**
- `GET /api/orphans/tracks` → `[{mbid, artist, title, album, deleted_at,
  local_path}, ...]`
- `GET /api/orphans/playlists` → `[{playlist_id, name, deleted_at,
  track_count}, ...]`
- `POST /api/tracks/<mbid>/restore` — wraps `db.restore_track`.
- `POST /api/playlists/<id>/restore` — wraps `db.restore_playlist`.
- `DELETE /api/tracks/<mbid>?purge=true` — hard-delete the row (only
  allowed if `deleted_at IS NOT NULL`).
- `DELETE /api/playlists/<id>?purge=true` — same, playlist side.
- `DELETE /api/tracks/<mbid>/file` — delete the on-disk file that
  `local_path` points to, clear the column. Does NOT purge the row.

**DB methods.**
- Add `purge_track(mbid)` and `purge_playlist(id)` — raise unless
  `deleted_at IS NOT NULL` (prevents accidental hard-delete by API
  misuse).
- Add `get_orphan_tracks()` / `get_orphan_playlists()`.

**Open questions.**
- Should purge on the master also notify satellites? No — the satellite
  already has `deleted_at` set and treats the row as an orphan. Hard
  removal from the master just means the row stops being in the delta
  feed. The satellite's local tombstone is the satellite's problem.
- Should "Delete local file" require a separate toggle/confirmation? Yes
  — it's the only destructive filesystem action. Use a confirmation
  modal on both web and desktop.

**Effort.** ~3–4 hours including the two new DB methods and tests.

**Depends on** the soft-delete work already landed.

**Shipped (backend + web + desktop).** DB methods: `purge_track`, `purge_playlist`,
`get_orphan_tracks`, `get_orphan_playlists`. API routes: `GET
/api/orphans/{tracks,playlists}`, `POST /api/tracks/<mbid>/restore`,
`POST /api/playlists/<id>/restore`, `?purge=true` on the existing
DELETE endpoints, `DELETE /api/tracks/<mbid>/file` (409s on live
rows, idempotent on already-missing files). Web: `/orphans` page with
two tabs and confirm-gated destructive actions. The Tauri Orphans screen
provides the same restore, file-delete and purge
operations.

---

### 4. Auto-link local files to catalog rows

**Original gap.** After `Pull Catalog`, a satellite had rows it might already
have on disk from a previous pre-sync life (e.g. the user's existing
music library predates DAPManager). Those rows stay `local_path=NULL`
and get filtered out of the default view, so the user thinks they're
missing.

**Surface.**
- A toolbar / Multi-Device Sync card button: **Link Local Files**.
- Runs as a background task (same `TaskManager` pattern).
- Reports: scanned N files, linked M rows, ambiguous K (multiple files
  could match, skipped).

**Implementation.**
- New module: `src/catalog_linker.py` with `main_run_catalog_linker(db,
  config, progress_callback)`.
- Walk `config.music_library_path` (already configured), read tags with
  existing mutagen helpers, match by:
  1. ISRC if both sides have it.
  2. Exact `(artist, title)` case-insensitive.
  3. Exact `(artist, title, album)` for disambiguation.
- Only link rows where `local_path IS NULL`. Never overwrite.
- If the on-disk file's `path` is already in `tracks.local_path` for a
  *different* mbid, log and skip — means we already know about it under
  a different identity.

**API.** `POST /api/catalog/link-local` — starts the background task.

**Schema.** None.

**Open questions.**
- Match on MBID if the file has it embedded? Yes, highest priority —
  most reliable signal. Put it above ISRC.
- What about fuzzy matching? Skip for v1. Punt to a future iteration
  once we see how much the exact-match pass picks up in practice.

**Effort.** ~3–5 hours including walking the library, tag-reading, and
dry-run/apply modes for tests.

**Shipped (backend + web + desktop).** New module `src/catalog_linker.py` with
`main_run_catalog_linker`. API: `POST /api/catalog/link-local` runs
as a TaskManager task and returns a `{scanned, linked, ambiguous,
skipped, errors, linked_by_mbid/isrc/name}` summary via the progress
channel. Web: "Link Local Files" button in the Multi-Device Sync
card. Match order: embedded MBID → ISRC → (artist, title)
case-insensitive → album-disambiguated. The desktop Sync screen exposes the
same operation. Fuzzy matching remains deliberately unsupported because a
false link is worse than an explicit unresolved row.

---

### 10. Playback availability tiers + master proxy streaming

**Original gap.** Satellites that held a catalog row but no on-disk file
could not play that track at all — the Songs / album views filtered
those rows out, and `/api/stream` only knew how to serve a local path.
This defeats the point of catalog sync for anyone who wants the master
to act as the fallback jukebox.

**Surface.**
- Library endpoints (`/api/library/tracks`,
  `/api/library/album/<id>/tracks`) attach an `availability` tag per
  row: `"local"` (local file), `"drive"` (only DAP path set),
  `"remote"` (no on-disk path but `master_url` configured). Rows that
  resolve to none of the above are dropped.
- `/api/stream/<mbid>` resolves sources in priority order: local file
  → DAP drive → proxy to master's `/api/stream`. Range header and
  bearer token are forwarded upstream so `<audio>` seeks work across
  the proxy.

**API changes.** No new routes; existing library/stream endpoints get
richer behavior. The DB now exposes `get_track_sources(mbid)` returning
both `local_path` and `dap_path` so the stream handler can decide
without another query.

**Schema.** None. `dap_path` was already in `tracks`.

**Open questions.**
- Availability is decided at listing time from column values, not by
  stat'ing disk — a deliberate trade to avoid N stats per page load.
  Actual file existence is re-checked in the stream handler, and the
  proxy fallback catches any "row says local_path but the file vanished"
  case.
- The 502-on-master-unreachable path gives the UI a distinct signal
  from 404-missing-row; the desktop player could show a "master
  offline" hint on top of a "track missing" hint.

**Shipped.**

---

## Medium-effort, meaningful polish

### 5. Download-from-catalog action

**Original gap.** With `Show catalog-only` on, users saw rows they wanted
locally but had to use the download flow manually (search, queue,
etc.). The catalog view should *be* the wishlist.

**Surface.**
- Desktop: selecting catalog-only rows enables a **Download Selected**
  action in the toolbar. Queues them via the existing Soulseek pipeline.
- Web: a similar button in the Settings or Library card once the web
  track browser (#6) exists.

**API.** `POST /api/catalog/queue-download` with `{mbids: [...]}`. Looks
up each row, constructs an `"Artist - Title"` search query, enqueues via
`db.queue_download` (same helper used by `/api/suggestions`).

**DB.** No changes — `download_queue` is already there.

**Open questions.**
- Dedupe: if a search query is already queued, skip (same behavior as
  `/api/suggestions`).
- Retry budgets: respect existing download_queue status machine.
- Should we pre-populate `mbid_guess` so the downloader can use it for
  MusicBrainz verification? Yes — we already have the mbid, pass it
  through.

**Effort.** ~2 hours on the API + desktop side alone. Add web coverage
when #6 lands.

**Depends on** no other new features; independently shippable.

**Shipped (API + desktop).** `POST /api/catalog/queue-download` with body
`{"mbids": [...]}` returns `{queued, queued_mbids, skipped_linked,
skipped_queued, not_found}`. Dedupe via `db.is_download_queued`
(normalized search_query). `mbid_guess` populated so the downloader's
MusicBrainz verification uses the correct identity. It is wired into the
Tauri Songs context menu; the web library retains its existing download
actions.

---

### 6. Web track browser

**Original gap.** The web dashboard had sync buttons and a search box, but no
actual library table. Desktop had a full iTunes-style layout. To use the
web UI as a real remote control this gap has to close.

**Surface.**
- A new left-sidebar on the dashboard with playlists (mirroring
  `desktop_app.py`'s `QListWidget`).
- Right pane: a track table with columns Artist, Title, Album, mbid
  badge (catalog-only / orphan status).
- Toolbar-style buttons above the table mirroring desktop actions (Queue
  Download, Soft-Delete, Report to Master, etc.).
- "Show catalog-only" / "Show orphans" toggles mirroring desktop.

**API.** Mostly exists: `GET /api/library/search` returns rows; needs a
broader `GET /api/tracks?playlist_id=...&local_only=...&include_orphans=...`
to drive the full table. `GET /api/playlists` (list — not the delta one
already at that path) needs a non-delta variant or a separate route.

**Concern.** `/api/playlists` is already taken by the sync delta route.
Use `/api/library/playlists` for the UI list to avoid clashing.

**Schema.** None.

**Open questions.**
- Pagination: the current desktop UI loads everything at once, which is
  fine up to a few tens of thousands of rows. Web should do the same
  initially; add virtualization later if users complain.
- Inline edit (rename a playlist, etc.)? Defer to #7.

**Effort.** ~6–8 hours. Bulk of the work is the template + JS; API is
mostly additive.

**Shipped.** New `/library` page with a playlist sidebar (All Tracks +
live playlists from `list_playlists_with_counts`) and a track table with
availability/orphan badges. Toolbar wires Queue Download → `POST
/api/catalog/queue-download`, Soft-Delete → `DELETE /api/tracks/<mbid>`,
Report Inventory → `POST /api/inventory/report`. Toggles "Show
catalog-only" / "Show orphans" mirror the desktop semantics. API:
`GET /api/library/playlists` (non-delta; distinct from the sync
`/api/playlists` route), and `GET /api/library/tracks` now accepts
`playlist_id`, `local_only`, `include_orphans` without breaking its
default (no-param) behavior. Pagination and playlist rename/delete
context menus landed with #7.

---

### 7. Playlist editor (web + desktop)

**Original gap.** Playlists could only be created via Spotify import. There was
no UI for creating a playlist from scratch, adding/removing tracks, or
renaming.

**Surface.**
- "New Playlist" button creates an empty playlist (prompt for name).
- Right-click / context menu on track rows: **Add to Playlist → ...**
- Right-click on a playlist sidebar entry: **Rename**, **Delete** (which
  is now a soft-delete → orphan, already supported).
- Drag-and-drop on desktop, button-driven on web (drag-drop is nice but
  adds cost).

**API.**
- `POST /api/library/playlists` — `{name}` → creates empty playlist with
  a generated UUID for `playlist_id`.
- `PUT /api/library/playlists/<id>` — `{name?, track_mbids?}` — partial
  update.
- `DELETE /api/library/playlists/<id>` — already exists as soft-delete.

**DB.** Add `create_playlist(name)` returning the new id. Renaming is
already handled by `add_or_update_playlist` (which now uses
`ON CONFLICT DO UPDATE`, preserving membership — see the cascade-wipe
fix). Membership mutation is `link_track_to_playlist` +
`unlink_track_from_playlist` (the latter doesn't exist yet; add it).

**Open questions.**
- `updated_at` bumping on membership change is already handled
  correctly via `link_track_to_playlist`. New `unlink` method must do
  the same.
- Does a satellite creating a playlist get pushed to master? Yes — that
  was the point of the playlist push-back flow. Already wired.
- Conflicts when both a satellite and the master edit the same playlist
  at once? The existing last-writer-wins semantics handle it silently.
  Consider a visible conflict log in a follow-up.

**Effort.** ~5–7 hours including tests for the new endpoints and the
`unlink` method.

**Depends on** #6 for the web surface, but the desktop and API portions
are standalone.

**Shipped (API + web + desktop).** DB: `create_playlist`, `rename_playlist`,
`unlink_track_from_playlist`, `replace_playlist_membership`,
`get_playlist`. API: `POST /api/library/playlists` (201 with generated
uuid4 hex id), `PUT /api/library/playlists/<id>` (partial rename +/or
full membership replace; empty `track_mbids` list explicitly empties
the playlist, omitting the key leaves it untouched; unknown mbids
surfaced via `landed` vs `requested`), `DELETE
/api/library/playlists/<id>` delegating to the soft-delete handler
(forwards `?purge=true`). Web: New Playlist toolbar button, sidebar
context menu (Rename / Delete) on playlist entries, track-row context
menu with "Add to playlist" submenu that merges the existing
membership client-side and PUTs the full list. The Tauri sidebar and Songs
context menu cover creation, rename, delete, smart-rule editing and track
membership.

---

### 8. API auth tokens

**Original gap.** All `/api/*` routes were unauthenticated. On a
purely local LAN this may be fine; on Tailscale or any forwarded setup
it's a risk — anyone on the tailnet can soft-delete tracks, wipe
inventory, push arbitrary playlists, etc.

**Surface.**
- Config: `api_token` (optional string). If absent, the server runs in
  open mode (current behavior) with a warning log on startup.
- When present: `/api/*` routes require `Authorization: Bearer <token>` except
  `/api/status`, `/api/healthz`, and `/api/openapi.json`.
- Outbound satellite clients add the header when `api_token` is set. Two
  separate token names are avoided — master and satellites share the same
  token for simplicity.
- Settings card: field to set the token; masked like other secret fields.

**Implementation.**
- Flask `before_request` decorator on the `/api/*` URL prefix.
- Constant-time comparison when checking the token.
- Returns 401 with a clear message when missing/wrong.

**Schema.** None.

**Open questions.**
- Token rotation: for now, manual (edit config, restart). Fine.
- Rate limiting: out of scope here; do separately if needed.
- Multiple tokens / per-device tokens: over-engineered for a LAN tool.
  Use one shared token; revisit if the fleet grows beyond a handful of
  devices.

**Effort.** ~2–3 hours. Most of it is wiring the header into the two
sync clients and the settings UI.

**Delivered on** the existing Settings infrastructure; `api_token` is included
in the editable and secret-key sets.

**Shipped.** The Flask auth gate uses constant-time comparison, all satellite
sync/contribution clients forward the shared token, and Settings treats it as
a redacted secret. `/docs` and the OpenAPI spec remain available for setup and
diagnostics.

---

### 9. Picard-style identify & tag

**Original gap.** The existing `auto_tagger` ran post-download only, wrote FLAC
tags only, and never surfaced match confidence. Users could not select
library rows and say "identify these" the way Picard lets you.

**Surface.**
- Desktop: right-click a track row → "Identify & Tag". Fingerprints the
  local file via AcoustID, looks up MusicBrainz, shows a review dialog
  with before/after diff, colour-coded confidence (green ≥0.9, yellow
  0.5–0.9, red <0.5), Apply or Cancel.
- Web: `POST /api/tag/identify/<mbid>` returns candidate + current tags
  + tier; `POST /api/tag/apply/<mbid>` writes tags and updates the row.

**Implementation.**
- New module `src/tag_service.py` with `identify_file` (no writes) and
  `write_tags` (cross-format: FLAC, MP3, Ogg Vorbis, M4A). `auto_tagger`
  now delegates its `_apply_tags` to it so the downloader path gains
  cross-format support for free.
- On apply, if the returned MBID differs from the row's current MBID,
  the old row is soft-deleted before the new row is upserted (so the
  delta carries the corrected identity to satellites).

**Schema.** None.

**Effort delivered.** ~4 hours: tag_service + web routes + desktop
context menu + review dialog + 20 tests.

**Shipped.**

---

## Suggested sequencing

1. **#1 Sync All + status widget** — smallest, unlocks #2. _Done._
2. **#2 Scheduled background sync** — trivial on top of #1. _Done._
3. **#8 API auth tokens** — do before anyone runs DAPManager past the
   LAN. Cheap and blocks nothing. _Done._
4. **#9 Picard-style identify & tag** — shipped out-of-band (user
   request). _Done._
5. **#10 Playback availability tiers + master proxy streaming** —
   shipped out-of-band; makes catalog-only rows playable via the
   master. _Done._
6. **#3 Orphan cleanup page** — closes the soft-delete loop. _Done across
   backend, web and desktop._
7. **#4 Auto-link local files** — bridges pre-DAPManager libraries
   with pulled catalogs. _Done across backend, web and desktop._
8. **#5 Download-from-catalog** — wishlist queue via MBIDs. _Done in the
   API and desktop library._
9. **#6 Web track browser** — sidebar + track table on `/library`.
   _Done, with desktop browsing/filtering/context-menu parity._
10. **#7 Playlist editor** — create / rename / delete / add-to.
    _Done across API, web and desktop._

All items in this tranche are shipped. Further product ideas should go in a
new roadmap rather than being recorded as unfinished parity work here.
