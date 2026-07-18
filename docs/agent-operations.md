# Running DAPManager with an AI Agent

Short answer: **yes, an AI agent is a good fit for operating DAPManager** —
maintenance is exactly the kind of repetitive, checkable, well-bounded work
agents do well. This doc is the runbook: how an agent should drive the system,
what's safe to automate, and how to verify results.

## Why it works here

- **Every GUI action has a CLI/API equivalent** (`scripts/dap_admin.py`), so an
  agent never needs to click anything or screenshot a browser.
- **Operations are observable** — they return JSON/structured output the agent
  can parse and check.
- **Mutations have dry-runs or are idempotent** — the agent can preview, then
  apply, then re-run to confirm "nothing left to do."

## The agent's interface: `dap_admin.py`

The CLI is plain Python stdlib (no dependencies) and talks to the running
server over HTTP. Run it from the host:

```bash
python scripts/dap_admin.py [--server URL] [--token TOKEN] <command>
```

- `--server` defaults to `http://localhost:5001`. For a remote master over
  Tailscale: `--server http://viggys-pc:5001` (or the Tailscale IP).
- `--token` is required only if `api_token` is set in `config.json`.

Full command list: run `python scripts/dap_admin.py --help`, or see the table in
[library-maintenance.md](library-maintenance.md).

## Remote master configuration over SSH

Use `scripts/set-master-config.ps1` to update the Docker master on
`viggys-pc` without putting credentials in an SSH command, PowerShell history,
or the Git checkout. The script accepts exactly one JSON envelope on standard
input:

```json
{
  "version": 1,
  "set": {
    "slsk_username": "your-user",
    "slsk_password": "your-password",
    "api_token": "replace-with-32-or-more-random-characters"
  },
  "clear": []
}
```

Create the input file outside the repository and restrict it before adding
credentials:

```bash
mkdir -p "$HOME/.config/dapmanager"
chmod 700 "$HOME/.config/dapmanager"
touch "$HOME/.config/dapmanager/master-config.json"
chmod 600 "$HOME/.config/dapmanager/master-config.json"
```

Apply it from the Mac and reload the running service:

```bash
ssh -T viggys-pc \
  powershell.exe -NoLogo -NoProfile -NonInteractive \
  -ExecutionPolicy Bypass \
  -File C:/Users/Vignesh/Desktop/DAPManger/scripts/set-master-config.ps1 \
  -Restart \
  < "$HOME/.config/dapmanager/master-config.json"
```

Supported string keys in `set` are `slsk_username`, `slsk_password`,
`jellyfin_url`, `jellyfin_api_key`, `jellyfin_user_id`, `acoustid_api_key`,
`contact_email`, `api_token`, `lidarr_url`, `lidarr_api_key`, and
`jellyfin_music_library_path`. Supported Boolean keys are `lidarr_enabled` and
`auto_tag_downloads`. The dedicated Jellyfin mirror path is the only path this
helper edits; it deliberately does not edit the primary library/download
paths, device role or identity, public/master URLs, scheduler settings, or
Prowlarr/qBittorrent/Rutracker configuration. Unknown keys and wrong value
types are rejected before the live file is changed. A new `api_token` must
contain at least 32 characters.

To remove a configured secret, put its key in `clear`, omit it from `set`, and
explicitly opt in with `-AllowClear`:

```json
{
  "version": 1,
  "set": {},
  "clear": ["jellyfin_api_key"]
}
```

Use the same SSH command with `-AllowClear -Restart`. Without `-AllowClear`, a
non-empty `clear` list is rejected. `clear` accepts `slsk_username`,
`slsk_password`, `jellyfin_url`, `jellyfin_api_key`, `jellyfin_user_id`,
`jellyfin_music_library_path`, `acoustid_api_key`, `contact_email`,
`lidarr_url`, and `lidarr_api_key`; each is written as an empty string.
`api_token` cannot be cleared because that would silently reopen the API. It
can only be rotated to another non-empty value in `set`, after which every
satellite and API caller needs the replacement. Disable Lidarr with
`"set": {"lidarr_enabled": false}` rather than `clear`.

The helper reads raw standard-input bytes, rejects input over 128 KiB, and uses
a strict UTF-8 decoder before parsing JSON. It has no payload-file or secret
parameter fallback. Its target is also path-locked: it derives the repository
root from its own `scripts` directory and will only edit that checkout's
`config/config.json` and use its `docker-compose.yml`.

This is a direct edit of the host file bind-mounted into the container; the
helper does not call `/api/config`. It preserves existing container-only paths
and other unsupported fields, writes timestamped backups into the protected
`config/.backups` directory, then replaces the JSON atomically. The running
Flask process caches configuration, so omit `-Restart` only when a deliberate
later restart is already planned. With `-Restart`, the helper restarts
`dapmanager` and polls the local health endpoint. If health does not recover,
it restores the protected backup, saves the failed candidate under
`config/.backups`, and restarts the previous configuration. Its output reports
key names and status only, never values.

### Optional post-download Jellyfin mirror

No mirror is needed when DAPManager and Jellyfin already read the same host
music directory; keep `jellyfin_music_library_path` blank and the normal
post-download refresh is sufficient.

When Jellyfin reads a separate directory, mount that one host directory into
both containers. The DAPManager mount must be read-write so it can publish the
new import; Jellyfin can keep its view read-only. For example, add this bind to
the `dapmanager` service (using the real host path):

```yaml
volumes:
  - type: bind
    source: C:/Users/you/Music Library
    target: /jellyfin-music
```

The Jellyfin service should mount that same `source` at its configured media
path, for example `/media/music:ro`. Then set the DAPManager-side container
path over the existing SSH stdin flow:

```json
{
  "version": 1,
  "set": {
    "jellyfin_music_library_path": "/jellyfin-music"
  },
  "clear": []
}
```

Restart with `set-master-config.ps1 -Restart`. For each successful downloader
import, DAPManager preserves the relative `Artist/Album/Track` path and writes
through a synced temporary file followed by an atomic replace. A missing file
is copied; worse audio is replaced only when DAPManager's quality tuple
(lossless, bit depth, sample rate, bitrate) is strictly higher. Equal-quality
FLACs with matching frames can converge stale canonical tags, while a
better-quality destination keeps its audio and receives only the verified
canonical Picard fields through an atomic tag update. Artwork and user-owned
fields are preserved. Mirror errors
are logged but do not undo the canonical import, retain the queue item, or
suppress the one Jellyfin refresh at the end of the queue run. This is an
incremental post-download mirror, not a bulk merge of two existing libraries.

## Quick health report

Start here. `sweep` is a **read-only** preview that reports everything pending
(duplicates, split albums, edition merges, dangling links) without changing
anything, and prints the exact command to fix each:

```bash
python scripts/dap_admin.py sweep
```

Use it to decide what (if anything) the standard run below needs to do.

## Standard maintenance run (safe to schedule)

This is the routine an agent can run on a cadence (e.g. nightly, or after a
batch of downloads). Each step is read-then-act-then-verify.

```bash
# 1. Confirm the server is up and ready
python scripts/dap_admin.py healthz          # expect ok: True, initialized: True

# 2. Resolve duplicate files (keeps the highest-quality copy)
python scripts/dap_admin.py duplicates list          # review first
python scripts/dap_admin.py duplicates resolve-all   # then act

# 3. Consolidate split album editions (deluxe/standard)
python scripts/dap_admin.py library consolidate          # DRY RUN — review plan
python scripts/dap_admin.py library consolidate --apply  # apply if plan is sane

# 4. Review split-album incidents (merges need a human/agent judgement call)
python scripts/dap_admin.py split-albums list
#   → for clear cases: split-albums merge --primary … --secondary … --album … --artist …
#   → for false positives: split-albums dismiss <key>

# 5. Make sure on-disk tags match the DB so Jellyfin is correct
python scripts/dap_admin.py library retag    # tagged: N, skipped: M

# 6. (optional) pull in new files & refresh
python scripts/dap_admin.py scan
```

## Safety classification

| Risk | Operations | Agent guidance |
|------|-----------|----------------|
| **Safe / read-only** | `status`, `healthz`, all `list`, `library albums/tracks/artists`, any `--dry-run`/no-`--apply` | Run freely. |
| **Reversible-ish mutation** | `library consolidate --apply`, `split-albums merge`, `library retag` | Preview first; these change metadata (DB + file tags) but don't delete audio. Re-runnable. |
| **Destructive** | `duplicates resolve-all` / `resolve` | **Deletes the losing audio files from disk.** Have the agent `duplicates list` and confirm the kept candidate is the best before resolving. Not undoable. |

Rules of thumb for the agent:
- **Always dry-run `consolidate` and read the plan** before `--apply`. Two
  different albums sharing a base name would cluster together — catch it in the
  plan.
- **Never auto-`resolve-all` duplicates without first listing them** if the
  library has unusual filenames; the scorer is good but file deletion is final.
- **`retag` is the safe "make Jellyfin correct" button** — it only writes files
  whose tags drifted and never deletes anything.

## How to verify (don't trust, check)

- After `consolidate --apply`: re-run `library consolidate` — a correct run
  reports `0 editions, 0 tracks` (idempotent).
- After `retag`: re-run `library retag` — expect `tagged: 0` (all in sync).
- After any metadata change meant for Jellyfin: confirm the server logged
  `Triggered Jellyfin library refresh`, then check the album in Jellyfin after
  its scan completes. Remember: **Jellyfin reads file tags, not the DB** — if
  Jellyfin still looks wrong but DAPManager looks right, you changed the DB
  without retagging the files.

## Connectivity notes

- The master runs as a Docker container on the Windows host; **Tailscale runs on
  the host, not in the container**. Agents on other devices reach it at
  `http://viggys-pc:5001` (Tailscale MagicDNS) or the host's Tailscale IP.
- Health endpoint `GET /api/healthz` is unauthenticated and side-effect-free —
  use it as the agent's "is it alive?" probe.
- `GET /api/status` reports whether a background task (scan/download/sync) is
  currently running; poll it to avoid starting overlapping long jobs (the server
  rejects a second concurrent task anyway, but polling is cleaner).

## End-to-end testing without a human

`scripts/test_satellite_e2e.py` simulates a satellite: it requests a download on
the master, polls to completion, and confirms the track landed in the catalog.
A pytest version lives in `tests/e2e/` (skipped unless `DAPMANAGER_E2E_MASTER`
is set). An agent can run these to prove the full pipeline works after changes.

```bash
python scripts/test_satellite_e2e.py --master http://localhost:5001 --skip-download   # connectivity + catalog only
DAPMANAGER_E2E_MASTER=http://localhost:5001 pytest tests/e2e/    # full suite
```
