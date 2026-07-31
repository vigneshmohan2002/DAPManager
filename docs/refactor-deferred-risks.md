# Deferred correctness risks

This register records correctness concerns discovered during the
behavior-preserving full-stack refactor. They are intentionally not fixed in
that refactor: each item can change a public contract or synchronization
behavior and therefore needs its own characterization, decision, and focused
change.

## Smart-playlist field drift — resolved

The desktop editor now offers the Python rules engine's `genre` and `is_liked`
fields. Liked rules use a boolean Yes/No control and equality-only operator;
genre rules retain the server-supported text operators. Existing rules now
round-trip without silently narrowing the server contract.

Relevant boundaries:

- `src/smart_playlist.py`: authoritative server whitelist and coercion.
- `desktop/src/lib/api/types.ts`: desktop `SmartField` wire type.
- `desktop/src/lib/smartRules.ts`: desktop editor field/operator model.
- `desktop/src/lib/api/playlists.ts`: runtime ruleset decoder.

Frontend tests characterize the field list, boolean coercion, operators, and
validation while the backend continues to revalidate every submitted ruleset.

## OpenAPI metadata drift

`src/openapi_spec.py` is deliberately hand-authored and only documents the
stable automation surface. Route-map tests protect path and method coverage,
but do not prove that every request field, response schema, status code, or
description still matches the Flask implementation. The explicit
`UNDOCUMENTED_PATHS` allowlist also leaves much of the desktop API outside the
schema.

Follow-up decision: add response/request contract fixtures for the documented
surface, then correct metadata in a dedicated API-documentation change. Do not
generate a schema or add a dependency without a separate design decision.

## Native-shell and Python config-path resolution

The Python resolver checks `DAPMANAGER_CONFIG`, then a pre-existing
`./config.json`, then the platform config directory. The native shell computes
the platform path and passes it to the sidecar through `DAPMANAGER_CONFIG`.
That is correct for the packaged application, but it differs from starting the
Python server directly from a directory containing a legacy config. The two
entry points can therefore select different files in development or migration
scenarios.

Relevant boundaries:

- `src/config_paths.py::resolve_config_path`
- `desktop/src-tauri/src/seed_config.rs::platform_config_path`
- `desktop/src-tauri/src/backend/lifecycle.rs` sidecar environment setup

Follow-up decision: define whether the native app must honor a working-directory
legacy config. Preserve packaged first-run seeding and never overwrite an
existing config.

## Second-resolution delta cursors — resolved

SQLite `CURRENT_TIMESTAMP` and the synchronized timestamp values have
second-level precision. Catalog, playlist, and lyrics delta reads now replay
the inclusive cursor boundary (`>=`). Their receivers already use idempotent
upserts or stale-row rejection, so the repeated boundary is harmless and a
same-second write cannot be skipped permanently. Artist-tag snapshots already
used an inclusive boundary.

Relevant boundaries:

- `/api/catalog` and `/api/playlists` cursor responses in `web_server.py`
- `src/db_repositories/sync.py::get_catalog_since`
- `src/db_repositories/playlists.py::get_since`
- cursor persistence in `src/catalog_sync.py`

Regression tests cover exact-boundary catalog, playlist, and lyrics rows. A
compound monotonic cursor remains a possible future scalability optimization,
but is no longer required for correctness.
