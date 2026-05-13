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
| 8d | `1c13ad8` | Suggest screen replacing the PySide6 "Suggest to Jellyfin" dialog. Reads `dap_manager_host_url` from `/api/config`; missing host routes through the focus-key Settings flow. Textarea parses `Artist - Title` lines client-side (mirrors `parse_manual_suggestions`) and POSTs `<host>/api/suggestions`. Selection-based suggest from SongsScreen is deferred — manual paste covers the workflow at a fraction of the integration cost. |

Sidebar currently has **Albums / Artists / Songs / Playlists / Audit /
Duplicates / Downloads / New Releases / Orphans / Sync / Fleet / Suggest
/ Settings** wired to real screens. No `<Placeholder>` stubs remain.

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

### 8d — Suggest screen — _Shipped (`1c13ad8`)_

Decisions worth preserving:

- **Manual-paste only, no SongsScreen integration.** The PySide6
  flow has both — paste *and* suggest-from-selection. Selection
  would mean adding a multi-select model to SongsScreen + a context-
  menu entry + a wiring path from there into the Suggest flow.
  Manual paste covers the actual workflow (user types a few lines)
  at maybe 10% of the cost. Selection-based can land in a later
  stage if the user actually misses it.
- **Fetch host from `/api/config`, not a prop.** The host can change
  via the Settings screen mid-session; reading config on mount keeps
  the screen self-contained. The Settings save flow already reloads
  the in-process config server-side, so a re-mount of Suggest picks
  up the new value.
- **Reuse Stage 7a's focus-key router.** Missing
  `dap_manager_host_url` calls `onOpenSettings("dap_manager_host_url")`
  which scrolls + flashes the field. Same plumbing as the
  acoustid_api_key path from Stage 7b.
- **Direct `fetch` to the host URL works** because Tauri's CSP is
  `null` and webview HTTP is unrestricted. If a future hardening
  turns CSP on, this will need to move to `@tauri-apps/plugin-http`
  with an allowlist — calling out as a known fragile point.
- **`parseManualSuggestions` lives in `lib/api.ts`** alongside the
  POST helper. It's a pure function but the symmetry with the Python
  side (`parse_manual_suggestions`) is the documentation: both ends
  use the same rules so test coverage on either side gives
  confidence.

---

**Stage 8 exit + retirement.** All four sub-stages shipped; the Tauri
app now covers every toolbar/menu action `desktop_app.py` exposed.
PySide6 was retired in a single follow-up commit that deleted
`desktop_app.py` (1,595 LOC), `tests/test_desktop_app.py`, and the
`PySide6>=6.6.0` line from `requirements.txt`. `src/first_run.py`
stayed — its pure `build_initial_config` helper is reusable for a
future Tauri first-run wizard, and its tests don't touch Qt.

---

## Stage 9 — Musicat-inspired polish

Onboarding shipped (see [`docs/onboarding.md`](onboarding.md)), so
Stage 9 is now active. Pool of ideas in
`memory/reference_design_musicat.md`; landing as small sub-stages,
ranked by value/work the same way Stage 8 was.

### 9 / mini-player — _Shipped (`0e579cb` + test `3f5e10a`)_

Compact always-on-top window variant. Threshold + scale-factor math
extracted to a pure `decide_chrome_action` returning a `ChromeAction`
enum so the boundary cases (220 px exact, hysteresis, HiDPI 2.0x,
fractional 1.5x scale, scale=0 fallback) are unit-testable on the
Rust side without touching `set_decorations`.

### 9 / waveform seeker — _Shipped (this commit)_

Canvas-based seeker in `PlayerBar`, replacing the plain
`<input type="range">` once peaks are decoded. Falls back to the
slider while peaks are loading or on decode failure.

Decisions worth preserving:

- **Pure `binPeaks` + thin React hook split.** `lib/waveform.ts`
  takes a `Float32Array` and returns the normalized per-bin peak
  array — no DOM, no `AudioContext`. The hook
  (`lib/useWaveformPeaks.ts`) does the `fetch` + `decodeAudioData` +
  cache plumbing. Keeps the math testable without jsdom and keeps
  the cache module-scoped so peaks survive PlayerBar re-mounts when
  the window flips into mini-player and back.
- **Module-scoped `Map<mbid, Float32Array>` cache.** Same `mbid`
  keys all fetches (catalog vs. local file paths don't matter — the
  stream URL is content-addressed by mbid). Re-skipping back to a
  played track within the session pulls peaks from the cache
  instantly; only browser-process restart clears it.
- **Range-input fallback, not a placeholder.** While peaks are null
  (loading/error) the original slider renders, so seek is always
  available. Replacing the slider unconditionally would have made
  the UI worse on decode failures than it is today.
- **Double-fetches the stream** (audio element + AudioContext).
  Acceptable on local-network streaming where bandwidth isn't the
  bottleneck. If remote streaming becomes common, revisit by
  routing both through a single `fetch` + `URL.createObjectURL`
  chain.
- **Pointer-capture drag, not click-only.** `onPointerDown` captures
  the pointer and `onPointerMove` (gated by `e.buttons === 1`)
  re-seeks during the drag. Matches the muscle memory of the
  original range input — clicking the canvas alone would have been
  a regression for users who scrub.
- **No RAF position loop.** Redraws on `position` prop change only;
  the audio element's `timeupdate` fires ~4×/sec which is choppy but
  fine for the bar's pixel velocity. Switching to RAF is cheap if
  smoother visuals matter later.

### 9 / artist infoscreen — _Shipped (this commit)_

Wikipedia-summary card on `ArtistDetailScreen`, above the album
grid. Skeleton on load, hidden on miss, links out to Wikipedia
on hit.

Decisions worth preserving:

- **Wikipedia REST API direct, not via MusicBrainz first.** The
  obvious "right" path is MB → artist MBID → URL relation →
  Wikipedia title, but that's three round-trips per artist for
  marginal disambiguation gain. Bare-name lookup with a
  "<name> (band)" fallback hits the right page in the common case
  with one or two HTTP calls. If a future user complains about
  wrong-page hits ("Pink" → the colour), revisit.
- **Disambiguation pages → treat as miss.** Wikipedia returns
  `type: "disambiguation"` with an extract like "X may refer to:".
  Showing that is worse than showing nothing, so the client folds
  it to None and falls back to "(band)".
- **Process-local cache, both positive and negative.** Repeat
  visits to the same artist within a session don't re-hit
  Wikipedia. Negative caching is important too — if a name
  doesn't resolve, retrying on every screen mount just wastes
  network and the user's attention.
- **HTTP 200 / `success: false` on miss, not 404.** Same shape as
  the rest of the API; the client renders a quiet empty-state
  rather than logging a console error on every artist without a
  Wikipedia page.
- **No discography timeline.** The original sketch was "Wikipedia
  summary + discography timeline", but the DB has no
  release-year column — adding one means schema migration plus MB
  release-group browse calls. Deferred until users ask; the album
  grid below already shows what we have.
- **`contact_email`-derived User-Agent.** Reuses the
  MusicBrainz-client identity rather than minting a new one;
  Wikipedia's etiquette is satisfied either way and config-key
  surface stays small.

### 9 / smart playlists — _Shipped (`5312d16` + `0111960` + `8747fb8`)_

Rule-based playlists, evaluated server-side at fetch time. Three
commits: DB schema + clause builder, web API, desktop UI.

Decisions worth preserving:

- **JSON column on `playlists`, not a separate `smart_playlist_rules`
  table.** A ruleset is small (≤ a handful of rows), always loaded
  with the playlist, and never queried independently — a side table
  would be all join with no payoff. The downside (no per-rule
  history) doesn't matter here because edits are rare and the whole
  ruleset is replaced atomically.
- **Field/op whitelist enforced in `src/smart_playlist.py`, never
  string-interpolated.** Unknown identifiers raise before reaching
  SQL; values are always parameters; LIKE values are escaped and the
  clause uses `ESCAPE '\'` so user-typed `%` / `_` behave as
  literals. The smart-playlists feature would be a SQL-injection
  surface if any of those guards were skipped, so the contract is
  enforced at the lowest layer (the clause builder) rather than at
  the endpoint, and `coerce_ruleset` is the single validation
  funnel both create and update flows go through.
- **Empty rules → `("0", [])`, not the full library.** Safer fallback
  if a future caller skips coercion. A misconfigured smart playlist
  resolves to zero tracks, never to "everything in the library".
- **`smart_rules` ships through the playlists delta feed.** Smart
  playlists are sync'd between master and satellites the same way
  static ones are; satellites pull the JSON and re-evaluate locally
  against their own tracks. No special-casing in the sync layer.
- **Mixing `track_mbids` and `smart_rules` in one PUT is a 400.**
  Manual membership and rule-driven membership in one request have
  no coherent outcome (the read path picks one or the other based
  on whether `smart_rules` is set), so reject up front rather than
  silently dropping one side.
- **Client whitelist mirrors server, but is for UX gating only.**
  `desktop/src/lib/smartRules.ts` exists so the dialog can disable
  Save and reconcile op when the user changes field. Don't expand
  the client list past what the server accepts — out-of-whitelist
  ops would surface as a 400 with no UI feedback.
- **Single dialog for static-and-smart create, toggle in the form.**
  The "+" button stays one button; the dialog's "Smart playlist"
  checkbox gates the rule editor inside the same modal. Splitting
  into a popover would have made static creation (the common case)
  worse for a feature most users won't reach for.
- **Smart playlists hide from "Add to playlist" submenu.**
  Manually adding a track to a smart playlist would store a row the
  read path ignores. `SongsScreen` filters smart playlists out of
  the submenu so the user can't "succeed" at a no-op.
- **No vitest yet.** `lib/smartRules.ts` is pure and small enough
  that the server-side test coverage (`tests/test_smart_playlist.py`,
  same whitelist) is doing double duty for now. Worth wiring vitest
  when a second pure-helper module appears in the desktop tree.

### 9 / listening stats — _Shipped (`58391dc` + `dae4671`)_

`play_events` table, `/api/library/plays` (POST) and
`/api/library/play-stats` (GET), Tauri-side scrobble in
`PlayerContext`, and a new "Listening" sidebar entry that shows top
tracks / top artists / recent plays for a chosen window
(30 days / 90 days / all time).

Decisions worth preserving:

- **No FK on `play_events.track_mbid`.** Stats are history.
  Soft-deleting or purging a track shouldn't erase its
  listening record. `LEFT JOIN tracks` on the read side keeps
  orphan plays visible (rendered as "(unknown track)") rather
  than silently dropping them from the count. Top-artists is
  the exception — it INNER JOINs because an event with no
  resolvable artist has nothing to attribute.
- **Top artists groups on the *current* `tracks.artist`.** A tag
  rewrite that renames an artist re-attributes their history.
  That matches user intuition ("how much have I listened to X"
  follows X's canonical name) and avoids the alternative —
  duplicating artist on `play_events` at write time — which
  would freeze a stale name into the stats forever.
- **Single coalesced `play-stats` endpoint.** Total +
  top tracks + top artists + recent are always wanted for the
  same window; four endpoints would trade simplicity for
  nothing. `limit` is clamped to [1, 200] so a stale dashboard
  polling can't pull unbounded rows.
- **Scrobble threshold = ≥30s OR ≥50% of duration.** Long-
  established Last.fm convention; user expectations transfer.
  The client owns the threshold; the backend just records.
  Swap-out is one line in `PlayerContext` if research suggests
  a better number later.
- **Once-per-load via a `useRef`, not state.** A re-queue of the
  same mbid is a fresh load → fresh recording, but a within-
  track pause/resume or scrub is not. Using a ref keeps the
  20× / sec `position` updates from re-rendering every
  consumer of the player context.
- **Fire-and-forget recording.** `recordPlay` catches its own
  failures with `console.warn` rather than toasting. A missed
  scrobble is invisible to the user, and a transient backend
  hiccup mid-song shouldn't surface a red banner. Caller is
  not responsible for error handling.
- **Window cutoff serialized as `YYYY-MM-DD HH:MM:SS` UTC, no
  `T`.** Matches sqlite's `CURRENT_TIMESTAMP` shape so the lex
  compare in `played_at >= ?` is correct. Mixing ISO-with-T
  and the space form in the same column's comparisons would
  technically work but is the kind of subtle thing that breaks
  later — emit the same shape sqlite is storing.
- **Tagged `source: "desktop"` on every recording.** Free-form
  string routed straight through to the DB. Per-source splits
  ("how much did I listen on the desktop vs. the web client")
  become a backend-only change when a second client lands.

### Remaining picks (not yet started)

- Discography timeline with release years (release-year column +
  MB release-group browse, gated on artist infoscreen having
  enough adoption to justify the schema work).

---

## Stage 10 — Discover sidebar (Downloads + New Releases)

The two `<Placeholder>` stubs in the sidebar's "Discover" section.
Both predate Stages 5–8 (they're the only screens left as
"Coming soon" copy) and surface backend state that's already
flowing — the app just hasn't grown a window onto it yet. Picking
order is value/work, not list order: Downloads first, then New
Releases.

### 10a — Downloads screen — _Shipped (`31a8da7` + backend `081cbc3`)_

**Problem.** Tracks queued via Soulseek (manual download, "Queue
Download" from a track context menu, "Link Local Files" misses,
release-watcher enqueues) all flow through `download_queue` but
the desktop app shows no progress, no failures, no completion
signal beyond the running-state strip on the Sync screen. Users
fire-and-forget queueing and have to grep the log to know what's
going on.

**Surface.**
- A new `DownloadsScreen` replacing the placeholder. Top-of-pane
  running-state strip when the downloader task is in flight (same
  edge-trigger pattern as `SyncScreen` / `AuditScreen`).
- A "Run Downloader" CTA next to the strip (`POST /api/download`)
  — disabled while running. Same shape as Audit's "Complete
  Albums".
- Single table grouped by status: Pending → Failed → Completed.
  Columns: query, last attempt (relative), status pill, row
  actions. Per-row: **Retry** (failed only — flips status back to
  pending) + **Remove** (DELETE the row from the queue, with
  confirm).
- Bulk: "Clear completed" wipes finished rows in one click since
  the table grows monotonically otherwise.
- 2s `/api/status` poll drives the strip; refetch the queue on
  the running→idle edge so the user sees the table update without
  a manual refresh button.

**API.**
- `GET /api/downloads/list` — already shipped; returns id, query,
  status, last_attempt. Reuse as-is.
- `POST /api/downloads/<id>/retry` — flip a failed row back to
  pending. New endpoint; thin wrapper over
  `db.update_download_status`.
- `DELETE /api/downloads/<id>` — remove a single row from the
  queue. New endpoint; thin wrapper over `db.delete_download_item`
  (already exists).
- `POST /api/downloads/clear-completed` — bulk-delete rows with
  status `success` (the schema's terminal-success state — UI
  labels it "Completed", which is where the path name comes
  from). New endpoint; one DB method
  (`delete_succeeded_downloads`).
- `POST /api/download` — already exists; kicks the downloader
  task. Reuse.

**DB.** One new method — `delete_completed_downloads()` returning
the number of rows removed. Everything else exists.

**Open questions.**
- Should "Retry" reset `last_attempt` or keep it as the previous
  failure timestamp for forensics? Keep it — the downloader will
  overwrite it on the next attempt anyway, and showing "last
  failed Xm ago, retried" is more informative than "last attempt
  just now" before the retry actually runs.
- Pagination — the queue can grow into the hundreds for heavy
  users. Match Songs/Library: render everything, virtualize later
  if it becomes a real problem. The `download_queue` is regularly
  trimmed by completion in normal operation.

**Effort.** ~3–4 hours: three new endpoints (each one method or
less), one DB helper, one screen with three sub-tables, status
poll wiring (copy from SyncScreen).

**Depends on** nothing. Independently shippable.

Decisions worth preserving:

- **DB-side `'success'` ↔ UI-side "Completed".** The schema's
  CHECK constraint allows `pending` / `failed` / `success`. Adding
  a fourth `completed` synonym would mean a migration for a label
  change. Kept the DB name; render "Completed" only in the UI
  layer (`DownloadsScreen` `BUCKETS` table). The `clear-completed`
  API path keeps the user-facing word — that's the URL the screen
  binds to and renaming it would break the API surface.
- **`retry_download` does not bump `last_attempt`.** UI shows
  "last failed Xm ago" until the next downloader run actually
  re-attempts and `update_download_status` overwrites the
  timestamp. Stamping it on retry would erase the only forensic
  signal of when the failure originally happened.
- **404 on retrying a non-failed row, not 200/success:false.**
  The desktop client branches on status code in
  `lib/api.ts:retryDownload`, returning a typed
  `{success: false, message}` without throwing — keeps the table
  mounted on a benign UI race (user clicks Retry on a row that
  the downloader just finished).
- **Single `_config_file_present` fixture in test_web_routes.py.**
  The wider suite assumes `DAPMANAGER_CONFIG` is set externally;
  the four new download endpoint tests instead set
  `web_server.CONFIG_FILE` to a real tmp path so
  `config_exists()` returns True without environment fiddling.
  Pre-existing tests that share the same setup-redirect issue
  weren't migrated — out of scope for this stage.

### 10b — New Releases screen — _Shipped (frontend `8d922e2` + backend `2a52a99`)_

**Problem.** The release-watcher polls Lidarr's wanted/missing
list and silently enqueues new albums via sldl. Operators have no
view into "which releases is Lidarr asking for" or "which of
those have I already grabbed" without going to Lidarr's own UI.
On non-Lidarr setups this whole feature is invisible.

**Surface.**
- A new `ReleasesScreen` replacing the placeholder.
- Lidarr-disabled state: an empty card explaining the watcher
  isn't running, with a "Configure Lidarr" button that routes to
  Settings via `onOpenSettings("lidarr_url")` — reuses Stage 7a's
  focus-key flow.
- Lidarr-enabled state: an album-cover grid (mirror Audit's
  `coverartarchive.org/release/<mbid>/front-250` pattern) with
  per-card title, artist, release date, and a **status pill**:
  "queued" (already in `download_queue`), "downloaded" (already a
  row in `tracks` with that release MBID), or "wanted" (neither).
- Per-card actions: "Queue Now" (POSTs `/api/catalog/queue-down\
  load` for the wanted ones — already exists from roadmap #5;
  reuses the same machinery as the track context-menu Queue
  Download). Disabled if already queued/downloaded.
- A header tick: "Last poll: Nm ago" reading the watcher's last
  run timestamp from `sync_state` (new key
  `last_release_watch_tick`).

**API.**
- `GET /api/releases/wanted` — proxy to Lidarr's
  `/api/v1/wanted/missing` filtered to the page-1 page size. Each
  item is augmented server-side with `queued: bool` (joins
  `download_queue.mbid_guess`) and `downloaded: bool` (joins
  `tracks.release_mbid`). Returns `success: false,
  reason: "lidarr_disabled"` when the integration isn't
  configured (client renders the empty state).
- `POST /api/catalog/queue-download` — already exists. Reuse for
  per-album manual queue.

**Schema.** None on `tracks` / `albums` / `download_queue`. One
new key on `sync_state`: `last_release_watch_tick`. The
release-watcher's `run_watch_tick` becomes responsible for
stamping it.

**Open questions.**
- Cover art: Lidarr returns its own `images` array which already
  includes a cover URL. Prefer that (it's what Lidarr's own UI
  uses) and fall back to coverartarchive.org by `foreignAlbumId`.
- Manually triggering the watcher: a "Run now" button POSTs
  `/api/releases/run-watch-tick` so users don't wait for the
  scheduler. Optional v1 — the existing scheduler interval is
  fine for most use, but cheap to add and removes a "why don't I
  see my new wanted" support question.
- Pagination: Lidarr's wanted/missing can be hundreds. Limit to
  the first 100 in v1 with a "View in Lidarr" external link for
  the rest. Adding cursor pagination is bigger and not on the
  critical path.

**Effort.** ~4–6 hours: one new backend endpoint with the
augmentation join, one new key on `sync_state`, one cover-art
grid screen, focus-key Settings router for the disabled state.

**Depends on** 10a landing first **only** for the running-state
strip pattern (otherwise duplicate work). Lidarr integration
itself is already in the codebase via `release_watcher` and
`lidarr_client`.

Decisions worth preserving:

- **Three states, not two, on the empty path.** `disabled`
  (Lidarr off in config) and `unavailable` (configured but
  unreachable) render different copy and route to different
  Settings keys. Folding them into a single "no Lidarr" empty
  state would have lost the distinction the user actually needs
  ("did I forget to enable it" vs "is the service down").
  `error` is a third bucket for genuine LidarrError responses
  (502 from the backend) — separate so a transient outage
  doesn't get the configure-it-first treatment.
- **`/api/download/request`, not `/api/catalog/queue-download`,
  for the "Queue Now" action.** `queue-download` only works on
  track MBIDs that already exist in the local `tracks` table —
  for wanted (not yet downloaded) Lidarr albums, those rows
  don't exist yet, so it would just return `not_found`.
  `download/request` mirrors what the watcher itself does:
  freeform `Artist - Title` query + release MBID as
  `mbid_guess`. Master-only, but the screen only renders when
  Lidarr is on, which is master-only, so the gate is implicit.
- **Three-way mutually-exclusive pill (`In library` >
  `Queued` > `Wanted`).** Stack-rank by usefulness: once a row
  exists in `tracks`, the queue history is just noise; until
  then, a queued row deserves a different visual than a fresh
  Wanted one. A user toggling between manual queue and the
  watcher should see the pill flip exactly once per state
  change.
- **Bulk join, not per-album lookups.** Two DB queries
  (`get_queued_release_mbids` / `get_existing_release_mbids`)
  feed Python set-membership checks for the augmentation. The
  obvious "loop and SELECT" approach would have been 200+
  queries for a 100-album page; this stays at 2 regardless of
  page size.
- **Stamp `last_release_watch_tick` on Lidarr success, not on
  enqueue success.** A tick that finds nothing new still proves
  polling is healthy; only stamping on enqueue would make a
  caught-up library look stale to the operator. LidarrError
  paths intentionally don't stamp, so a sustained Lidarr outage
  surfaces in the UI as a stale "Last poll" timestamp.

---

## Stage 11 — "For You" foundation

The basic listening verbs every modern music app takes for granted
but DAPManager didn't have: a "heart" / liked column, shuffle,
repeat, append-to-queue, and a launch surface that surfaces what
the user listened to recently and what they like. Ranked first
on the Stage 11+ Spotify-parity roadmap by value/work ratio —
mostly wiring, one DB column, one new screen, one new endpoint.

Ships in five small, independently revertable commits rather than
one big pile, matching the user's small-chunks preference.

### 11a — Liked Songs primitive — _Shipped (`ce65813`)_

**Problem.** No "liked" / favourite / heart primitive existed at
all. Users had no way to flag tracks they want easy access to and
no foundation for a downstream Daily-Mix-style recommender that
needs a positive-signal set to seed from.

**Surface.**
- New `tracks.is_liked` column (NOT NULL, default 0) + partial
  index `idx_tracks_is_liked WHERE is_liked = 1`.
- `POST /api/library/tracks/<mbid>/like` and `DELETE …/like`.
  404 (not 200/success:false) when the mbid isn't in the library
  so the desktop client can branch on status code without parsing
  message strings — same convention as Stage 10a's retry-download.
- An auto-created `Liked Songs` smart playlist with a reserved
  deterministic id `liked_songs` (not a UUID). Created on first
  like, idempotent on subsequent likes, never auto-deleted on
  unlike. The smart-playlist rule schema gains an `is_liked`
  boolean field + value coercion that accepts Python bool, 0/1,
  and "true"/"false" so JSON over the wire round-trips.

**DB.** One column, one partial index, one playlist row. The index
sits in `_migrate_schema` (not `_create_tables`'s indexes list)
because on legacy DBs the column doesn't exist when
`_create_tables` runs — partial index creation would otherwise
fail with "no such column".

**Reuse.** `is_liked` rides the existing catalog-sync delta (it's
a user preference, not a device-local file fact). `apply_catalog_row`'s
on-conflict overwrite matches the existing master-authoritative
model — v1 ships without satellite→master like proxying, so likes
set on a satellite are clobbered by the master's view on next
sync. Acceptable for v1; documented here for future revisit.

**Effort.** ~3 hours: column + migration, two endpoints, one
auto-playlist seed, rule-schema extension, web-route + db tests.

**Depends on** nothing.

Decisions worth preserving:

- **Reserved id `liked_songs`, not a UUID.** Both master and
  satellite converge on the same row after a sync tick instead
  of each minting their own UUID. The fixed string is also how
  the sidebar's "pin to top" logic identifies the row without a
  separate `is_system` flag.
- **404 not 200/success:false on unknown mbid.** Same convention
  as Stage 10a. Status-code branching keeps the client small and
  lets the row drop from the table without a try/catch.
- **Unlike doesn't auto-delete the playlist.** Otherwise a user
  who likes-then-unlikes immediately would see the sidebar pin
  flicker in and out. The user can delete the playlist manually
  if they really want.
- **`is_liked` is master-authoritative via catalog sync.** Picking
  per-row conflict resolution by `updated_at` instead would be
  the right long-term move (a satellite like would win until the
  master also touched the row), but it requires every catalog
  column to opt into the comparison and isn't gated on Stage 11.

### 11b — PlayerContext queue ergonomics — _Shipped (`2acdba6`)_

**Problem.** Shuffle, repeat, append-to-queue, and play-next —
the four primitives every music app exposes — were all missing.
Users had to replace the entire queue to add a track to it, and
playback always advanced sequentially with no wrap-around.

**Surface.**
- `shuffle: boolean` and `repeat: "off" | "all" | "one"` on the
  PlayerContext, both persisted to `localStorage` keys
  `dap.player.shuffle` / `dap.player.repeat` so they survive
  restarts (instead of silently resetting to off every launch).
- `playNext(tracks)` inserts after the current index;
  `addToQueue(tracks)` appends. Both start playback from the new
  tracks when the queue is empty so the very first context-menu
  click works without a separate "play" path.
- A single `pickNextIndex(reason)` helper routes auto-advance and
  user-driven `next()` through the same shuffle+repeat logic.
  Shuffle uses a without-replacement pool kept in a ref — every
  queue track plays once before repeats can occur.

**Effort.** ~2 hours: one context module change, no new endpoints
or types, no DB work. The biggest risk was the shuffle pool's
interaction with queue mutations; refilling the pool on identity-
changing events (queue + shuffle deps) keeps it sound.

**Depends on** nothing.

Decisions worth preserving:

- **`repeat=one` on track end seeks to 0 instead of changing
  index.** The src-load effect only fires on mbid change, so
  changing the index to itself wouldn't reload the audio. Seeking
  and re-playing is also gapless since the buffer isn't dropped.
- **Refill the shuffle pool excluding the current index when
  `repeat=all` runs out.** Otherwise the very-next-track after
  exhausting the pool can be the same track that just ended,
  which feels like the shuffle algorithm is broken.
- **Prev stays sequential even when shuffle is on.** Matches the
  visible-queue affordance — a played-history stack to pop from
  would feel better but is a future improvement, not v1.

### 11c — Heart icon, shuffle/repeat buttons, queue actions — _Shipped (`a237063`)_

**Problem.** The Stage 11a + 11b primitives needed user-visible
surfaces. Songs rows had no heart icon, the player bar had no
shuffle/repeat buttons, and the context menu had no Play-next /
Add-to-queue items.

**Surface.**
- Songs rows grow a heart column right after the playing-indicator
  column. Click toggles `is_liked` optimistically; on server error,
  the local flip rolls back and a toast surfaces the failure.
- The first like in a fresh library bumps `playlistsVersion` so
  the sidebar re-fetches and the auto-created Liked Songs pin
  appears without a manual reload.
- PlayerBar grows two new buttons: shuffle (⇄) and repeat (↻ with
  a small "1" superscript when in `one` mode). Both light up in
  accent color when active and have `aria-pressed` for screen
  readers.
- Songs row context menu grows Play next / Add to queue / Add
  to Liked Songs (or Remove from Liked Songs). All three are
  disabled when the row's availability is `unavailable` — adding
  a catalog-only row to the queue would just stall on next().

**Reuse.** `LibraryTrack` gets a new `is_liked: boolean` field,
flowing from the API response. `setTrackLiked` in `lib/api.ts`
returns a typed `{success, liked, message}` shape that matches
Stage 10a's retry-download pattern. No changes to PlayerContext.

**Effort.** ~3 hours. Mostly mechanical wiring.

**Depends on** Stages 11a + 11b shipped first.

Decisions worth preserving:

- **Heart is its own column, not inline in the title cell.**
  Inline would mean the click target overlaps the row's play-on-
  click handler, and `stopPropagation` doesn't always feel right
  to users who expect "click anywhere in the row to play". A
  dedicated w-9 cell keeps both intents legible.
- **Optimistic flip with server-side rollback on failure.** The
  alternative — wait for the server, then flip — costs a noticeable
  beat between click and visual feedback. The rollback path uses
  the captured pre-click `track.is_liked` value, not the current
  state, so a rapid double-click can't tear.
- **Album Detail + Queue panel hearts deferred.** Those screens
  use the bare `Track` type via `fetchAlbumTracks` rather than
  `LibraryTrack` from the library-tracks endpoint. Threading
  `is_liked` through requires either widening Track or extending
  the endpoint — out of scope for this commit; see Stage 11f
  for the follow-up.

### 11d — Sidebar pin + real Liked count — _Shipped (`feb35ea`)_

**Problem.** Two small Spotify-discordant defaults: the Liked
Songs playlist sorted alphabetically with user playlists (instead
of being pinned), and it showed "0 tracks" right after liking a
batch (because `list_playlists_with_counts` joins on
`playlist_tracks` and smart playlists have no manual membership).

**Surface.**
- Backend: a one-line specialization in `list_playlists_with_counts`
  runs an extra `COUNT(*) FROM tracks WHERE is_liked = 1` against
  the partial index for the Liked Songs row only. Other smart
  playlists still report 0 (the documented N-query trade-off).
- Frontend: the sidebar sorts `liked_songs` to the front of the
  Playlists section regardless of name, and prefixes its label
  with `♥ ` so it's identifiable even if a user names a regular
  playlist "Liked".

**Effort.** ~30 minutes. Two surgical edits.

**Depends on** Stage 11a shipped first.

### 11e — Home screen + landing card bundle — _Shipped (`dd307c7`)_

**Problem.** No top-level "landing" screen surfaced what the user
actually engaged with — recents, top artists, liked songs. Albums
was the default screen, which is the *catalog*, not the user's
*activity*. A first-launch user with no plays sees a wall of
covers; a returning user has to scroll the sidebar to find what
they just played.

**Surface.**
- New `/api/library/home` bundles four cards in one round trip:
  - `recent`: 12 reverse-chronological play events.
  - `top_artists`: top 8 artists in the last 30 days.
  - `liked`: `{ total, preview: first 6 most-recently-hearted }`.
  - `jump_back_in`: distinct albums derived client-side from the
    same `recent` feed, deep-linking to AlbumDetail.
- New `HomeScreen.tsx` with four sections rendered as Spotify-like
  rows / grids. Section actions ("See all") route into the existing
  Listening screen or the Liked Songs scoped Songs screen.
- Sidebar grows a heading-less "Home" anchor section at the very
  top. The default `screen` state moves from `"albums"` to
  `"home"` so fresh launches land here.
- `recent_plays` (existing helper) gains `album_id` (the same
  COALESCE shape `list_albums` uses) so the Home tile can deep-
  link without a second lookup.

**API.** `/api/library/home` is intentionally an opinionated fixed
view — the 30-day window for top artists isn't configurable here,
the Listening screen owns that UI. Coalescing the four cards into
one endpoint keeps screen mount fast and avoids four serial
fetches.

**DB.** No new tables or columns. `recent_plays` gets two extra
columns in its SELECT.

**Reuse.** Section / card layout doesn't introduce a new design
system — same `bg-[var(--color-surface)]` / muted-text patterns
the rest of the app uses.

**Effort.** ~4 hours: one endpoint, one screen, sidebar wiring,
default-screen change, web-route test.

**Depends on** Stages 11a + 11c for the Liked Songs surface, but
the empty-state copy ("Tap the ♡ on any track to add it here.")
makes Home render usefully even when nothing's liked.

Decisions worth preserving:

- **Jump-back-in derived client-side from `recent`, not a separate
  DB query.** The rare user who plays the same album six times in
  a row will see fewer than six tiles, which is the right behavior
  anyway ("you've been listening to one thing — keep going"). A
  dedicated query would be ~3× the data movement for the same
  perceptual result.
- **Heading-less Home section in the sidebar.** Wrapping it in
  a "Home" heading would visually weight it the same as Library
  and Manage, which it isn't — Home is the singular launch
  surface above everything else. `showHeading` skips the heading
  row when both `title` and `accessory` are empty.
- **Artist tiles use an initial in a circle, not an image.**
  DAPManager has no artist-image source today (Wikipedia bios
  don't carry portrait images consistently). An initial keeps
  the row visually rich without lying about a missing asset.

### 11f — Follow-ups — _All shipped_

- **AlbumDetail + QueuePanel hearts** — _Shipped (`16ba721`)_.
  `list_album_tracks` now selects `is_liked`; the existing
  `_public_track_row` serializer threads it through. `Track`
  grows an optional `is_liked?` so the bare type can carry the
  state when consumers care. PlayerContext exposes
  `setTrackLikedInQueue` so a toggle from *any* screen keeps the
  queue UI honest. AlbumDetail also bumps `playlistsVersion` on
  first like so the sidebar pin appears immediately.
- **Liked Songs sync delete-protect** — _Shipped (`657fa4b`)_.
  Server refuses DELETE on the reserved `liked_songs` id with a
  409 + "unlike tracks to empty it" message; the sidebar
  context menu disables Rename / Delete / Edit-rules for system
  playlists so the user doesn't reach for actions they'd get a
  toast on.
- **Liked Songs satellite → master proxy** — _Shipped (`31b667a`)_.
  When master_url is configured, the like endpoint forwards
  POST/DELETE to the master with the configured bearer token
  and mirrors the change locally on 200 so the UI doesn't
  snap-back between optimistic flip and next catalog
  reconciliation. The catalog-sync invariant ("master is the
  source of truth for is_liked") stays intact while satellite
  users get write-through semantics.

---

## Stage 12 — Listening intelligence

The Stats screen knew *that* you played track X but not how *long*
you played it, so total listening time was unavailable and
hour-of-day patterns were missing. A fresh install also had no
Wrapped-shaped recap of the year. Stage 12 lays the foundations
for both, in three independently revertable commits.

The scrobble bridges (Last.fm out / ListenBrainz import) are
scoped to Stage 12d-e but deferred — Stages 12a-c stand on their
own and value/work-wise the lyrics work (Stage 13) ranked higher.

### 12a — Per-event listened_ms + listening-time card — _Shipped (`b5c4b09`)_

**Problem.** ``play_events`` tracked *that* a track was played but
not how long the user actually listened. So "listening time" stats
were impossible and a Spotify-Wrapped-shaped recap had nothing to
build on.

**Surface.**
- `play_events.listened_ms` — nullable. Legacy rows stay NULL and
  are excluded from listening-time aggregations rather than
  counted as zero.
- `PlayerContext` accumulates wall-clock ms the audio was unpaused
  on the current load (a `useEffect` keyed on `isPlaying` captures
  the elapsed span on cleanup). Forward seeks don't inflate;
  pauses don't count.
- `recordPlay` now threads a third `listenedMs` arg through the
  POST body. Server-side cap at 30 minutes defends against
  pause-spam / clock-skew / hostile clients. A future tighter cap
  of `min(track_duration_ms * 1.5)` is gated on `tracks` growing
  a duration column.
- `api_library_play_stats` returns `listening_time_ms`.
  `StatsScreen` renders a three-card summary row — Plays,
  Listening time, Top artist — above the existing breakdown.

**DB.** One column, one migration. The `play_events` CREATE TABLE
gets the column too so fresh DBs match the migrated shape.

**Effort.** ~2 hours.

**Depends on** nothing.

Decisions worth preserving:

- **`useEffect` on `isPlaying` accumulates via cleanup, not on
  every `timeupdate`.** Two stable wall-clock reads per pause/play
  transition are simpler and more accurate than summing deltas at
  20Hz, and forward seeks transparently don't inflate (they don't
  flip `isPlaying`).
- **30-minute hard cap server-side.** Cheap defense against the
  trivial "claim a 6-hour listen for a 3-minute track" attack.
  Trading absolute tightness for ship-now simplicity — to be
  revisited once `tracks` knows duration.
- **Legacy NULL rows excluded, not zeroed.** Otherwise the Stats
  "Listening time" card would underreport real activity for users
  who already had hundreds of plays before this stage shipped. A
  separate `has_legacy_rows` count on the Wrapped payload drives
  a small caveat string in the UI.

### 12b — Hour-of-day heatmap — _Shipped (`650b5f7`)_

**Problem.** The Listening screen had no view of *when* the user
listens — morning, evening, late night. A heatmap is a one-glance
read users react to immediately.

**Surface.**
- `plays_by_hour(since_iso)` aggregator using
  `CAST(strftime('%H', played_at) AS INTEGER)`. Returns only hours
  with at least one play — the endpoint pads to a full 24-entry
  array so the heatmap renders without per-cell defaults.
- A sparkline-style bar strip on `StatsScreen`. Intensity-scaled
  opacity makes the top hour pop without a separate legend; a
  single-line caption ("Most active around 22:00 UTC") gives the
  headline read.

**Effort.** ~1 hour.

**Depends on** Stage 12a only insofar as the test data benefits
from real `listened_ms` values; the heatmap itself counts plays,
not duration.

Decisions worth preserving:

- **UTC bins, not local time.** Users who travel and users whose
  local clock shifts on DST would see bin assignments jitter
  otherwise. Future "local time toggle" can ship without rewriting
  the copy.
- **Padding lives in the endpoint, not the helper.** Wrapped (and
  future consumers) may want sparse rows; only the heatmap needs
  fixed-width zeroes.

### 12c — Wrapped year-in-review — _Shipped (`0759ed9`)_

**Problem.** No year-end recap. Spotify Wrapped is a flagship
moment that distills 12 months of listening into shareable cards;
nothing equivalent existed and the data was already there.

**Surface.**
- A new `wrapped_summary(year)` DB method bundles nine
  aggregations in one method (not nine helpers, since they all
  share the year window):
  - total plays, total listening_ms, `has_legacy_rows` flag
  - top track / artist / album (each one row, joined for display)
  - busiest day, top hour, first play, longest consecutive-day
    streak
- `GET /api/library/wrapped?year=YYYY` (defaults to current UTC
  year). ValueError on an unreasonable year flows through to a
  400 rather than a 500.
- `WrappedScreen.tsx` with a hero block, three top-X cards, four
  fact cards, and a year stepper. Linked from the Listening
  screen via a "✨ Wrapped 2026" button in the time-window strip.

**DB.** No new tables or columns. Existing `play_events`,
`tracks`, and a single SQL CTE for the streak.

**Reuse.** Hero + Card components are local — they don't earn
their own files yet. Date formatting helpers mirror the relative-
time helper used on the Stats screen.

**Effort.** ~3 hours: one DB method, one endpoint, one screen,
nine focused tests.

**Depends on** Stage 12a for `listening_time_ms` to be non-zero,
otherwise the hero block reads zero.

Decisions worth preserving:

- **Longest streak via gaps-and-islands SQL.** Distinct days minus
  their `ROW_NUMBER() OVER (ORDER BY date)` yields a constant
  per consecutive run; group by that constant and pick the max.
  SQLite 3.25+ window functions are required — they've shipped
  with Python's bundled `sqlite3` for years, so no compatibility
  gate.
- **Single-method aggregation, not nine helpers.** Wrapped is a
  fixed view; per-card endpoints would mean nine round trips for
  one screen and identical year-window plumbing everywhere. The
  cost is a fatter DB method, paid once.
- **`has_legacy_rows` flag plumbed through the API.** The same
  number quoted in the Stats screen caveat appears in the
  Wrapped screen footnote — both surfaces speak the same truth
  so users don't bounce between conflicting headlines.

### 12d/12e — Scrobble bridges (deferred)

The Last.fm outbound bridge and ListenBrainz history import were
scoped to Stage 12 but deferred behind Stage 13 lyrics on
value/work grounds: lyrics has universal user value and a single
no-auth API; the scrobble bridges require per-user credentials
and have value gated on whether the user already maintains an
external scrobble history. Picked up later in the roadmap.

---

## Stage 13 — Lyrics via LRCLIB

**Problem.** No lyrics support existed anywhere in the codebase.
For a music app, this is the single most missed Spotify-shaped
feature once playback works. LRCLIB is a no-auth, music-app-
focused lyrics API — well-suited to a one-evening integration.

**Surface.**
- New `lyrics` table keyed by `track_mbid`. Cached LRCLIB results
  *and* user manual paste overrides share the same row, with a
  `source` column disambiguating them. `lrc IS NULL` rows are
  cached misses — distinguishable from "never asked" — so the
  pane doesn't refetch on every reopen.
- `GET /api/library/tracks/<mbid>/lyrics` returns the cached row
  when fresh (30-day TTL for `lrclib` rows, infinite for manual),
  otherwise synchronously fetches from LRCLIB and persists.
  Transient network errors fall back to whatever stale cached
  row exists (with a `stale=true` flag) rather than failing hard.
- `POST` upserts a manual override. An empty body clears the row
  entirely so the next GET can re-try LRCLIB.
- `src/lrclib_client.py` is a 40-line `urllib` wrapper around
  `/api/get` — no auth, no rate limiter (LRCLIB explicitly
  invites music-app traffic), 8-second timeout so a failing
  fetch doesn't wedge the pane.
- `LyricsPane.tsx` slides in to the right of the queue panel.
  Synced LRC is parsed into ascending-time lines; the active
  line is picked by binary search on every `position` tick and
  auto-scrolled into view via `querySelector` against
  `data-lyric-index` (so the row JSX stays plain — no per-line
  refs).
- Clicking a synced line seeks playback to its time tag. Plain
  (unsynced) lyrics render as a paragraph; the seek affordance
  is hidden.

**API.** Two endpoints sharing one route via method dispatch
(`GET`/`POST`).

**DB.** One table — no other changes.

**Reuse.** `MusicBrainzClient` was the model for the
HTTP-cache-and-fetch shape, but LRCLIB has no rate limit so the
client is even simpler. PlayerContext's existing `currentTime`
stream drives line highlighting.

**Effort.** ~3 hours: one client module, one schema migration,
two DB helpers, two routes (one method-dispatched), one panel,
nine tests.

**Depends on** nothing structural. Sits alongside the existing
QueuePanel.

Decisions worth preserving:

- **Synchronous LRCLIB fetch in the GET handler.** Async/queued
  would let the response return immediately, but the UX is "open
  pane → see lyrics within 1s" — a synchronous fetch with an 8s
  timeout cap matches that expectation without needing a polling
  /retry loop on the client.
- **Negative cache (`lrc IS NULL`).** Without this, every reopen
  of an unmatched track refetches. Storing the miss costs ~50
  bytes per track without lyrics and pays back the first time
  the user reopens the pane.
- **Manual rows never auto-refresh.** A user who typed their own
  translation/correction should never have it silently
  overwritten by an LRCLIB row arriving later.
- **`scrollIntoView({block: "center"})` on active-index change,
  not on every `timeupdate`.** The 20Hz tick stream would
  thrash; reading the live DOM node on index *change* keeps
  rendering smooth and avoids a per-line ref map.
- **LRC parser drops untagged lines silently.** LRC files
  routinely have header rows like `[ar:Artist Name]` that aren't
  lyric content. Dropping them keeps the active-line binary
  search clean and avoids "blank line is active for 4 seconds"
  glitches.

### Stage 13 follow-ups

- **Lyrics sync across the fleet** — _Shipped (`e8ff14b`)_. A
  new `get_lyrics_since` / `apply_lyrics_row` pair lets the
  existing catalog-sync pipeline ship cached LRCLIB hits +
  manual overrides + negative-cache misses to satellites.
  `fetched_at` doubles as the cursor (the upsert bumps it on
  conflict). Last-writer-wins on `fetched_at` means a recent
  manual paste on a satellite isn't clobbered by the master's
  older cached LRCLIB row — same resolution pattern as
  `apply_pushed_playlist_row`. New `pull_lyrics` step runs last
  in `sync_all` so it never blocks the catalog/playlists pulls
  it's paired with.
- **Karaoke / translation overlays** — not yet started. LRCLIB
  supports translations but the v1 panel doesn't surface them.

---

## Keeping this doc current

Update the "Shipped stages" table as each stage lands (same cadence
as the main `roadmap.md`). When a stage's scope changes mid-flight,
amend the Stage section rather than mentioning it only in the commit
message — a future session should be able to open this file cold and
know exactly where the rewrite stands.
