# Library Maintenance

Tools for keeping the music library clean: resolving duplicate files,
un-fragmenting albums that got split across multiple entries, consolidating
deluxe/standard editions, and keeping on-disk tags in sync with the database.

Every operation here is available three ways:
- **Web UI** — the Dashboard (`/`), under the Duplicates / Split Albums /
  Consolidate Album Editions / Library Audit panels.
- **HTTP API** — `POST`/`GET` under `/api/library/...` and `/api/duplicates/...`.
- **CLI** — `scripts/dap_admin.py` (see [agent-operations.md](agent-operations.md)).

---

## The one thing to understand first: DB vs. file tags

DAPManager keeps a **SQLite database** (`tracks` table) *and* the music files
carry their own **embedded tags** (ID3 for MP3, Vorbis comments for FLAC/OGG,
MP4 atoms for M4A).

- **DAPManager's own UI reads the database.**
- **Jellyfin (and most other players) read the embedded file tags**, *not*
  DAPManager's database.

So a metadata change that only touches the database will show up in DAPManager
but **will not** appear in Jellyfin until the file tags are rewritten and
Jellyfin rescans.

The maintenance operations below were built with this in mind:

| Operation | Updates DB | Rewrites file tags | Triggers Jellyfin scan |
|-----------|:----------:|:------------------:|:----------------------:|
| Resolve duplicates | ✅ | n/a (deletes losing files) | — |
| Split-album merge | ✅ | ✅ | ✅ |
| Consolidate editions | ✅ | ✅ | ✅ (on apply) |
| Retag (sync files to DB) | — | ✅ | ✅ |

`update_album_tags()` in `src/tag_service.py` is a **targeted** writer: it only
touches `album` / `albumartist` / `musicbrainz_albumid` and leaves title,
artist, track/disc numbers, **date/year**, genre and everything else intact.
This is deliberately *not* `write_tags()` (which rewrites the whole tag set and
would blank fields it isn't handed).

---

## Duplicates

Two or more files mapped to the same track (e.g. `Song.flac` and
`Song (1).flac` from a re-download).

- **Detect:** `GET /api/duplicates` → groups, each with candidate files scored
  by quality (FLAC > M4A > MP3) and filename cleanliness (penalises
  `(1)`, leading track numbers, `(feat. …)`).
- **Resolve one:** `POST /api/duplicates/resolve` with
  `{mbid, keep_path, delete_paths[]}`. Keeps one file, deletes the rest, points
  the DB row at the survivor.
- **Resolve all:** the UI "Resolve All" button processes every group using the
  recommended (highest-scored) candidate. CLI: `duplicates resolve-all`.

The DB fix behind this: `update_track_local_path()` first releases the chosen
path from any *other* track that owns it (the `tracks.local_path` column is
`UNIQUE`), preventing a `UNIQUE constraint failed` error when re-pointing.

Source: `src/clear_dupes.py`, `src/db_manager.py`.

---

## Split albums

One album fragmented into multiple entries — usually because a featured-artist
tag or a slightly different release MBID made some tracks group separately
(e.g. tracks 1–15 under "Album" and track 16 under "Album" with a `feat.`
artist).

**Detection** (`src/split_album_detector.py`, two strategies):

1. **Folder split** — tracks sharing a directory whose album groups also have
   **similar album names** (≥ 0.6 ratio). The name check is essential: a folder
   holding genuinely different songs by different artists is *not* a split and
   is **not** reported. (This was a real false-positive class — e.g. a Mac
   Miller track and an Alchemist track sharing a folder — now filtered out.)
2. **Name similarity** — same-artist album groups with very similar titles
   (≥ 0.82 ratio).

**Default selection** when you merge: the UI pre-selects the group that is a
**superset** of the others (its track set contains all of theirs, by disc+track
number or title), falling back to the group with the **most tracks** when no
clean superset exists.

**Actions:**
- `GET /api/library/split-albums` — list incidents (each has a stable `key`).
- `POST /api/library/split-albums/merge` —
  `{primary_album_id, secondary_album_id, target_album, target_artist, target_release_mbid?}`.
  Reassigns the secondary group's tracks onto the primary and rewrites file
  tags. **Per-track artist is preserved** when a release MBID is present
  (featured-artist credits survive); grouping is by MBID.
- `POST /api/library/split-albums/dismiss` — `{key, undismiss?}`. Marks an
  incident a false positive so it stays hidden across rescans (stored in
  `split_album_dismissals`).

CLI: `split-albums list | merge | dismiss`.

---

## Consolidate album editions

Library-wide pass that folds standard/base editions into their **superset
edition** — e.g. "The Melodic Blue" → "The Melodic Blue (Deluxe)" — so every
song lands on one album.

How it clusters (`find_edition_clusters`):
- Strips edition tags from album titles ("(Deluxe)", "(Expanded Edition)",
  "- Remastered", etc.) to get a **base name**.
- Strips `feat. …` from the album artist to get a **base artist**.
- Groups by `(base name, base artist)`; any cluster with 2+ album groups is a
  candidate.
- Picks the **canonical** (superset) group by edition rank
  (super deluxe > deluxe/expanded/… > bonus > none), then track count, then
  title length.

**Always preview first.** The endpoint defaults to a dry run:
- `POST /api/library/consolidate-editions` with `{dry_run: true}` (default) →
  returns the plan (`clusters`, `tracks_reassigned`) without writing.
- `{dry_run: false}` → applies: reassigns tracks, rewrites file tags, triggers
  a Jellyfin scan. Idempotent — re-running finds nothing.

UI: **Consolidate Album Editions** panel → *Preview Edition Merge* → *Apply*.
CLI: `library consolidate` (preview) / `library consolidate --apply`.

> ⚠️ Edge case: two genuinely different albums by one artist that share a base
> name would cluster together. That's why the preview/dry-run exists — review it
> before applying.

---

## Retag — sync file tags to the DB

Walks every track with a local file and rewrites the on-disk album tags to
match the database, but **only for files whose tags differ** (it reads each
file's current tags first). Use it to repair files after any DB-only change, or
just to verify everything is in sync.

- `POST /api/library/retag-files` with `{only_mismatched: true}` (default).
  `{only_mismatched: false}` rewrites every file unconditionally.
- Triggers a Jellyfin scan if anything changed.

UI: **Sync File Tags to DB** button. CLI: `library retag` (add `--all` to force).

This reads tags on the whole library, so it can take a while on large
libraries; the CLI uses a 30-minute timeout and prints a "this can take a
while" notice. A clean run reports e.g. `tagged: 0, skipped: 3354` — meaning
every file already matches the DB.

---

## Known data-hygiene gap

Soft-deleted / renamed files can leave **dangling `local_path` rows** — DB rows
pointing at files that no longer exist on disk. These are harmless to tagging
(retag skips them) but show up as `availability: unavailable` or as a `None`
album when you inspect the file. A "purge dangling local_paths" cleanup is a
sensible future addition; for now they're counted in retag output as
`skipped`/dangling rather than errored.
