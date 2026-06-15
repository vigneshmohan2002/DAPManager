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
