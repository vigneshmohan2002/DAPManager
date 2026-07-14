# Deferred correctness risks

This register records correctness concerns discovered during the
behavior-preserving full-stack refactor. They are intentionally not fixed in
that refactor: each item can change a public contract or synchronization
behavior and therefore needs its own characterization, decision, and focused
change.

## Smart-playlist field drift

The Python rules engine accepts the `genre` and `is_liked` fields in addition
to the fields offered by the desktop rule editor. The desktop wire type and
decoder retain those fields so existing web-created playlists remain visible,
but the editor does not offer field controls or specialized boolean handling
for them. Editing such a playlist is therefore not a fully supported
round-trip.

Relevant boundaries:

- `src/smart_playlist.py`: authoritative server whitelist and coercion.
- `desktop/src/lib/api/types.ts`: desktop `SmartField` wire type.
- `desktop/src/lib/smartRules.ts`: desktop editor field/operator model.
- `desktop/src/lib/api/playlists.ts`: runtime ruleset decoder.

Follow-up decision: either bring the editor to parity with the existing wire
contract, or explicitly version/reject rules that a client cannot edit.
Characterize existing stored `genre` and `is_liked` rules before changing the
editing behavior.

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

## Second-resolution delta cursors

SQLite `CURRENT_TIMESTAMP` and the synchronized `updated_at` values have
second-level precision. Catalog and playlist delta reads use a strict
`updated_at > since` boundary. A write that receives the same timestamp as the
server `as_of` cursor can sit exactly on the boundary and be absent from the
next pull. The snapshot-before-read ordering prevents the usual concurrent
write gap, but it does not create a total order among writes in the same
second.

Relevant boundaries:

- `/api/catalog` and `/api/playlists` cursor responses in `web_server.py`
- `src/db_repositories/sync.py::get_catalog_since`
- `src/db_repositories/playlists.py::get_since`
- cursor persistence in `src/catalog_sync.py`

Follow-up options include replaying an inclusive boundary with idempotent
upserts, using higher-resolution timestamps, or using a compound monotonic
cursor. Any option changes synchronization semantics and needs master/satellite
regression tests before adoption.
