# DAPManager Roadmap: Post-Sync-Design Work

Context: the multi-device sync design (see `memory/project_sync_model.md`)
is now fully implemented — delta catalog + playlist sync, opt-in inventory,
fleet view, soft-delete with orphan surfacing, and a settings card. This
document scopes the next tranche of features that build on that foundation.

Items are ordered by bang-for-buck: each "high-value" item is cheap relative
to its payoff; "meaningful polish" items take more work but close obvious
gaps in the current experience.

---

## High-value, low-effort

### 1. "Sync All" button + sync status widget

**Problem.** Satellites need four manual clicks to fully reconcile with the
master (pull catalog, pull playlists, push playlists, report inventory),
and there's no visible "when did I last sync?" signal. The sync-cursor
timestamps live in `sync_state` but aren't surfaced.

**Surface.**
- Web: a single "Sync All" button in the Multi-Device Sync card. Runs the
  four operations sequentially in a background thread.
- Web: a small widget next to each existing button showing
  "Last: <relative time>" read from `sync_state`.
- Desktop: one "Sync All" toolbar action; status bar shows the currently
  running sub-step (`Sync All: pulling catalog…`).

**API.**
- `POST /api/sync/all` — runs the full sequence as one `TaskManager` task.
  Fails fast if any sub-step errors; reports which sub-step failed.
- `GET /api/sync/state` — returns `{last_catalog_sync, last_playlist_sync,
  last_playlist_push, last_inventory_report}` as ISO strings.

**Schema.** None. `sync_state` already has the keys; just expose them.

**Open questions.**
- Sequencing: pull-catalog must finish before pull-playlists (playlist
  membership FKs expect the tracks to exist). Push playlists and report
  inventory can run in parallel or serial — serial is simpler and sync
  isn't bandwidth-bound.
- If one sub-step fails, should we still attempt the rest? Default yes,
  with per-step success/error reported in the response.

**Effort.** ~1–2 hours. No new machinery, just orchestration and a
`sync_state` read endpoint.

---

### 2. Scheduled background sync

**Problem.** Even with "Sync All", users have to remember to click. For
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

**Open questions.**
- Should the master also auto-sync (inventory-only, to keep
  `device_inventory` fresh for itself)? Default yes — the fleet view
  should reflect the master's current state without manual prompting.
- Backoff on repeated failures — probably skip for v1; log is enough.
  Revisit only if users report spam.

**Effort.** ~2–3 hours. Includes settings wiring and a basic integration
test that the thread starts, runs, and stops cleanly.

**Depends on** #1 ("Sync All") landing first so the scheduler has a
single callable.

---

### 3. Orphan cleanup page

**Problem.** Soft-delete stamps `deleted_at` but nothing ever clears it.
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

---

### 4. Auto-link local files to catalog rows

**Problem.** After `Pull Catalog`, a satellite has rows it may already
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

---

## Medium-effort, meaningful polish

### 5. Download-from-catalog action

**Problem.** With `Show catalog-only` on, users see rows they know they
want locally but have to use the download flow manually (search, queue,
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

---

### 6. Web track browser

**Problem.** The web dashboard has sync buttons and a search box, but no
actual library table. Desktop has a full iTunes-style layout. To use the
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

---

### 7. Playlist editor (web + desktop)

**Problem.** Playlists can currently only be created via Spotify import.
No UI for creating a playlist from scratch, adding/removing tracks, or
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

---

### 8. API auth tokens

**Problem.** All `/api/*` routes are currently unauthenticated. On a
purely local LAN this may be fine; on Tailscale or any forwarded setup
it's a risk — anyone on the tailnet can soft-delete tracks, wipe
inventory, push arbitrary playlists, etc.

**Surface.**
- Config: `api_token` (optional string). If absent, the server runs in
  open mode (current behavior) with a warning log on startup.
- When present: all `/api/*` routes require `Authorization: Bearer <token>`
  except `/api/status` (useful for health checks).
- The satellite HTTP client in `src/catalog_sync.py` and
  `src/inventory_sync.py` adds the header when `api_token` is set in
  config. Two separate token names are avoided — master and satellites
  share the same token for simplicity.
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

**Depends on** Settings card (#done) — the UI for editing the token
already exists, just needs the new key added to
`CONFIG_EDITABLE_KEYS` / `CONFIG_SECRET_KEYS` in `web_server.py`.

---

## Suggested sequencing

1. **#1 Sync All + status widget** — smallest, unlocks #2.
2. **#2 Scheduled background sync** — trivial on top of #1.
3. **#8 API auth tokens** — do before anyone runs DAPManager past the
   LAN. Cheap and blocks nothing.
4. **#3 Orphan cleanup page** — closes the soft-delete loop.
5. **#4 Auto-link local files** — solves the "my pre-existing library
   doesn't show up" complaint.
6. **#5 Download-from-catalog** — converts catalog-only view into a
   useful wishlist.
7. **#6 Web track browser** — bigger project, do once the small stuff
   is out of the way.
8. **#7 Playlist editor** — needs #6 for the web half.
