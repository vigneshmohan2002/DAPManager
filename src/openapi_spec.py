"""
OpenAPI 3.0 spec for the DAPManager HTTP API, served at ``/api/openapi.json``
and rendered by the offline interactive explorer at ``/docs``.

This is hand-authored (no decorators) so it stays dependency-free and can be
read *before* the app is configured — the whole point is to let a browser
agent (e.g. Claude Cowork) discover the setup flow from a fresh install.

It intentionally covers the stable setup, sync, contribution, sharing, and
metadata workflows rather than every desktop-UI implementation route.  A
two-way URL-map test keeps both this core set and the explicit internal-route
allowlist honest when endpoints are added or removed.
"""

from typing import Optional

# Endpoints intentionally omitted from the spec (internal, legacy, or
# media/streaming routes that don't help an agent set the system up). The
# coverage test allows these to be undocumented.
UNDOCUMENTED_PATHS = frozenset({
    "/api/albums/complete",
    "/api/audit",
    "/api/audit/details",
    "/api/audit/queue",
    "/api/audit/results",
    "/api/catalog",
    "/api/catalog/link-local",
    "/api/catalog/pull",
    "/api/catalog/queue-download",
    "/api/download",
    "/api/download/albums/request",
    "/api/download/albums/requests",
    "/api/download/albums/requests/{}",
    "/api/download/albums/search",
    "/api/download/request",
    "/api/duplicates",
    "/api/duplicates/resolve",
    "/api/fleet/summary",
    "/api/fleet/track",
    "/api/install_slsk",
    "/api/inventory",
    "/api/jellyfin/pull",
    "/api/library/albums",
    "/api/library/albums/{}/cover",
    "/api/library/albums/{}/tracks",
    "/api/library/artists",
    "/api/library/artists/{}/info",
    "/api/library/artists/{}/radio",
    "/api/library/consolidate-editions",
    "/api/library/home",
    "/api/library/play-stats",
    "/api/library/playlists",
    "/api/library/playlists/{}",
    "/api/library/plays",
    "/api/library/retag-files",
    "/api/library/scrub-dangling",
    "/api/library/search",
    "/api/library/split-albums",
    "/api/library/split-albums/dismiss",
    "/api/library/split-albums/merge",
    "/api/library/tracks/{}/like",
    "/api/library/tracks/{}/lyrics",
    "/api/library/wrapped",
    "/api/lyrics",
    "/api/openapi.json",
    "/api/orphans/playlists",
    "/api/orphans/tracks",
    "/api/playlists",
    "/api/playlists/pull",
    "/api/playlists/push",
    "/api/playlists/queue",
    "/api/playlists/{}",
    "/api/playlists/{}/restore",
    "/api/releases/wanted",
    "/api/scan",
    "/api/stats",
    "/api/status",
    "/api/stream/{}",
    "/api/sync",
    "/api/tag/apply/{}",
    "/api/tag/identify/{}",
    "/api/tracks/needs-review",
    "/api/tracks/{}",
    "/api/tracks/{}/file",
    "/api/tracks/{}/restore",
})


def build_spec(server_url: Optional[str] = None) -> dict:
    """Return the OpenAPI document. ``server_url`` defaults to a relative
    root so the interactive explorer uses whatever origin served the page."""
    ok = {
        "description": "Success",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SuccessEnvelope"}}},
    }

    def err(desc: str) -> dict:
        return {
            "description": desc,
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorEnvelope"}}},
        }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "DAPManager API",
            "version": "1.0.0",
            "description": _OVERVIEW,
        },
        "servers": [{"url": server_url or "/"}],
        "tags": [
            {"name": "Setup", "description": "First-run configuration. Start here."},
            {"name": "Config", "description": "Read/update config after setup."},
            {"name": "Sync", "description": "Catalog/playlist sync between satellite and master."},
            {"name": "Contributions", "description": "Push local tracks from a satellite up to the master."},
            {"name": "Suggestions", "description": "Queue track suggestions on a configured master."},
            {"name": "Metadata", "description": "MusicBrainz artist tags and generated mixes."},
            {"name": "Library", "description": "Browse the catalog."},
            {"name": "Downloads", "description": "Inspect and recover the master download queue."},
            {"name": "Health", "description": "Liveness / status."},
        ],
        "paths": {
            "/api/healthz": {
                "get": {
                    "tags": ["Health"],
                    "summary": "Liveness probe (no auth, no setup gate)",
                    "description": "Returns 200 even before configuration. Use this to tell 'alive but unconfigured' from 'backend down'.",
                    "responses": {"200": {
                        "description": "Alive",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}, "initialized": {"type": "boolean"}},
                        }}},
                    }},
                }
            },
            "/api/setup/status": {
                "get": {
                    "tags": ["Setup"],
                    "summary": "Is first-run setup still needed?",
                    "description": "Step 1. ``needs_setup: true`` means no config.json exists yet — POST /api/save_config next.",
                    "responses": {"200": {
                        "description": "Setup state",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"needs_setup": {"type": "boolean"}},
                        }}},
                    }},
                }
            },
            "/api/setup/validate-path": {
                "post": {
                    "tags": ["Setup"],
                    "summary": "Check that a path exists on the server host",
                    "description": "Optional helper for the wizard. The path is on the machine running DAPManager, not the browser.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["path"],
                        "properties": {
                            "path": {"type": "string"},
                            "kind": {"type": "string", "enum": ["directory", "file"], "default": "directory"},
                        },
                    }}}},
                    "responses": {"200": {
                        "description": "Validation result",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
                        }}},
                    }},
                }
            },
            "/api/setup/detect-public-url": {
                "get": {
                    "tags": ["Setup"],
                    "summary": "Best-guess public URL satellites use to reach this master",
                    "responses": {"200": ok},
                }
            },
            "/api/satellite-bundle-link": {
                "get": {
                    "tags": ["Setup"],
                    "summary": "Mint a short-lived Mac satellite download URL",
                    "description": "Returns a one-hour bundle-scoped URL without placing the general API token in the sharing link.",
                    "responses": {"200": ok, "409": err("public_master_url not configured")},
                }
            },
            "/api/save_config": {
                "post": {
                    "tags": ["Setup"],
                    "summary": "Write a fresh config.json (first-run wizard)",
                    "description": _SAVE_CONFIG_DESC,
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SaveConfigRequest"}}}},
                    "responses": {"200": ok, "400": err("Invalid role or missing required fields")},
                }
            },
            "/api/config": {
                "get": {
                    "tags": ["Config"],
                    "summary": "Current config + editable-field metadata",
                    "description": "Secrets are redacted to ''. ``groups``/``bool_keys``/``secret_keys`` describe the editable form so a client can render or fill it generically.",
                    "responses": {"200": {
                        "description": "Config + metadata",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ConfigEnvelope"}}},
                    }, "404": err("config.json not found — run setup first")},
                },
                "post": {
                    "tags": ["Config"],
                    "summary": "Partial-merge update of config.json",
                    "description": "Only keys in the editable set are accepted. Blank secret values mean 'keep current'.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "additionalProperties": True,
                        "example": {"contribute_to_host": True, "master_url": "http://master.tail-xxxx.ts.net:5001"},
                    }}}},
                    "responses": {"200": {
                        "description": "Updated",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"success": {"type": "boolean"}, "changed": {"type": "array", "items": {"type": "string"}}},
                        }}},
                    }, "404": err("config.json not found")},
                },
            },
            "/api/sync/all": {
                "post": {
                    "tags": ["Sync"],
                    "summary": "Run the full sync (pull catalog/playlists, push playlists, report inventory, contribute)",
                    "description": "Background task. Steps that don't apply (no master_url, flags off) are skipped, not failed.",
                    "responses": {"200": ok},
                }
            },
            "/api/sync/state": {
                "get": {
                    "tags": ["Sync"],
                    "summary": "Last-run timestamps for each sync step",
                    "responses": {"200": ok},
                }
            },
            "/api/inventory/report": {
                "post": {
                    "tags": ["Sync"],
                    "summary": "Publish this device's MBID→path inventory to the master",
                    "description": "Gated by ``report_inventory_to_host``. Background task.",
                    "responses": {"200": ok},
                }
            },
            "/api/artist-tags": {
                "get": {
                    "tags": ["Sync"],
                    "summary": "Artist-tag delta for satellite synchronization",
                    "description": "Returns complete per-artist tag snapshots updated after the optional cursor. Empty tag arrays preserve cached MusicBrainz misses.",
                    "parameters": [{
                        "name": "since", "in": "query",
                        "schema": {"type": "string"},
                        "description": "Previous response's as_of timestamp",
                    }],
                    "responses": {"200": {
                        "description": "Artist-tag delta",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "as_of": {"type": "string"},
                                "count": {"type": "integer"},
                                "artist_tags": {"type": "array", "items": {"$ref": "#/components/schemas/ArtistTagSnapshot"}},
                            },
                        }}},
                    }},
                }
            },
            "/api/contribute": {
                "post": {
                    "tags": ["Contributions"],
                    "summary": "Offer this satellite's local tracks to the master (background)",
                    "description": "Identifier-first: the master tries to download each track itself and only asks for an upload when it can't match the quality. Requires ``master_url``; gated by ``contribute_to_host``.",
                    "responses": {"200": ok},
                }
            },
            "/api/contribute/track": {
                "post": {
                    "tags": ["Contributions"],
                    "summary": "Offer a single local track (synchronous, returns status)",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["mbid"],
                        "properties": {"mbid": {"type": "string"}},
                    }}}},
                    "responses": {"200": {
                        "description": "Resulting status",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ContributeOneResult"}}},
                    }, "400": err("mbid required"), "409": err("master_url not configured")},
                }
            },
            "/api/contributions": {
                "get": {
                    "tags": ["Contributions"],
                    "summary": "List recent contributions (master view)",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 200}}],
                    "responses": {"200": {
                        "description": "Contributions",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "contributions": {"type": "array", "items": {"$ref": "#/components/schemas/Contribution"}},
                            },
                        }}},
                    }},
                },
                "post": {
                    "tags": ["Contributions"],
                    "summary": "Satellite offers a track (master intake)",
                    "description": "Normally called by the satellite's own sync, not by hand. The master either reports ``have_better`` or queues a download and returns ``attempting``.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ContributionOffer"}}}},
                    "responses": {"200": {
                        "description": "Accepted",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "contribution_id": {"type": "integer"},
                                "status": {"$ref": "#/components/schemas/ContributionStatus"},
                            },
                        }}},
                    }, "400": err("artist and title required")},
                },
            },
            "/api/contributed": {
                "get": {
                    "tags": ["Contributions"],
                    "summary": "List this satellite's outgoing offers",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 200, "maximum": 500}}],
                    "responses": {"200": ok},
                }
            },
            "/api/contributions/{id}": {
                "get": {
                    "tags": ["Contributions"],
                    "summary": "Poll a contribution; master recomputes status",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {
                        "description": "Status",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "success": {"type": "boolean"},
                                "status": {"$ref": "#/components/schemas/ContributionStatus"},
                                "want_upload": {"type": "boolean"},
                            },
                        }}},
                    }, "404": err("unknown contribution")},
                }
            },
            "/api/contributions/{id}/upload": {
                "post": {
                    "tags": ["Contributions"],
                    "summary": "Upload the file when the master asks for it",
                    "description": "Multipart ``file`` field. The master verifies the file isn't empty, truncated, or worse than promised before ingesting.",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }}}},
                    "responses": {
                        "200": {"description": "Ingested", "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"success": {"type": "boolean"}, "status": {"type": "string"}, "local_path": {"type": "string"}},
                        }}}},
                        "400": err("missing file field"),
                        "404": err("unknown contribution"),
                        "422": err("rejected: empty / truncated / lower quality than promised"),
                    },
                }
            },
            "/api/library/tracks": {
                "get": {
                    "tags": ["Library"],
                    "summary": "List catalog tracks",
                    "parameters": [
                        {"name": "local_only", "in": "query", "schema": {"type": "string", "enum": ["1"]}},
                        {"name": "playlist_id", "in": "query", "schema": {"type": "string"}},
                        {"name": "include_orphans", "in": "query", "schema": {"type": "string", "enum": ["1"]}},
                    ],
                    "responses": {"200": ok},
                }
            },
            "/api/library/tags/backfill": {
                "post": {
                    "tags": ["Metadata"],
                    "summary": "Refresh MusicBrainz artist tags (master only)",
                    "description": "Starts a background task. Incremental mode skips artists with a fresh cache row.",
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object",
                        "properties": {"incremental": {"type": "boolean", "default": True}},
                    }}}},
                    "responses": {"200": ok, "400": err("master only")},
                }
            },
            "/api/library/daily-mixes/regenerate": {
                "post": {
                    "tags": ["Metadata"],
                    "summary": "Regenerate Daily Mix playlists (master only)",
                    "description": "Rebuilds reserved Daily Mix playlists from listening history and cached artist tags.",
                    "responses": {"200": ok, "400": err("master only")},
                }
            },
            "/api/suggestions": {
                "post": {
                    "tags": ["Suggestions"],
                    "summary": "Accept suggestions and queue them on this device",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["items"],
                        "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Suggestion"}}},
                    }}}},
                    "responses": {"200": ok},
                }
            },
            "/api/suggestions/forward": {
                "post": {
                    "tags": ["Suggestions"],
                    "summary": "Forward suggestions to this device's configured master",
                    "description": "Local desktop proxy that avoids a cross-origin browser request to master_url.",
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["items"],
                        "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/Suggestion"}}},
                    }}}},
                    "responses": {"200": ok, "400": err("items array required"), "409": err("master_url not configured"), "502": err("master unreachable")},
                }
            },
            "/api/downloads/list": {
                "get": {
                    "tags": ["Downloads"],
                    "summary": "List queue rows and retained staging usage",
                    "responses": {
                        "200": {
                            "description": "Queue and residue report",
                            "content": {"application/json": {"schema": {
                                "$ref": "#/components/schemas/DownloadQueueEnvelope"
                            }}},
                        },
                        "503": err("not initialized"),
                    },
                }
            },
            "/api/downloads/{item_id}/retry": {
                "post": {
                    "tags": ["Downloads"],
                    "summary": "Reset one failed queue row for retry",
                    "parameters": [{
                        "name": "item_id", "in": "path", "required": True,
                        "schema": {"type": "integer", "minimum": 1},
                    }],
                    "responses": {"200": ok, "404": err("row not failed")},
                }
            },
            "/api/downloads/{item_id}/residue": {
                "delete": {
                    "tags": ["Downloads"],
                    "summary": "Permanently delete retained staging for a failed row",
                    "description": "Only DAPManager-owned top-level staging directories are eligible. Active work is refused.",
                    "parameters": [{
                        "name": "item_id", "in": "path", "required": True,
                        "schema": {"type": "integer", "minimum": 1},
                    }],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {
                        "type": "object", "required": ["confirm"],
                        "properties": {"confirm": {"type": "boolean", "enum": [True]}},
                    }}}},
                    "responses": {
                        "200": ok,
                        "400": err("confirmation required"),
                        "404": err("queue row not found"),
                        "409": err("work is active"),
                    },
                }
            },
            "/api/downloads/{item_id}": {
                "delete": {
                    "tags": ["Downloads"],
                    "summary": "Remove one queue row without deleting retained files",
                    "parameters": [{
                        "name": "item_id", "in": "path", "required": True,
                        "schema": {"type": "integer", "minimum": 1},
                    }],
                    "responses": {
                        "200": ok,
                        "404": err("queue row not found"),
                        "409": err("active work or retained files"),
                    },
                }
            },
            "/api/downloads/{item_id}/attempts": {
                "get": {
                    "tags": ["Downloads"],
                    "summary": "List durable processing attempts for one queue row",
                    "parameters": [{
                        "name": "item_id", "in": "path", "required": True,
                        "schema": {"type": "integer", "minimum": 1},
                    }],
                    "responses": {"200": ok, "404": err("queue row not found")},
                }
            },
            "/api/downloads/worker": {
                "get": {
                    "tags": ["Downloads"],
                    "summary": "Read the master automatic-worker state",
                    "responses": {"200": ok},
                }
            },
            "/api/downloads/worker/pause": {
                "post": {
                    "tags": ["Downloads"],
                    "summary": "Pause automatic acquisition after active work finishes",
                    "responses": {"200": ok},
                }
            },
            "/api/downloads/worker/resume": {
                "post": {
                    "tags": ["Downloads"],
                    "summary": "Resume and wake automatic acquisition",
                    "responses": {"200": ok},
                }
            },
            "/api/downloads/clear-completed": {
                "post": {
                    "tags": ["Downloads"],
                    "summary": "Remove completed queue rows",
                    "responses": {"200": ok},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer",
                               "description": "Required on /api/* only when ``api_token`` is set in config."}
            },
            "schemas": {
                "SuccessEnvelope": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean"}, "message": {"type": "string"}},
                },
                "ErrorEnvelope": {
                    "type": "object",
                    "properties": {"success": {"type": "boolean", "example": False}, "message": {"type": "string"}},
                },
                "SaveConfigRequest": {
                    "type": "object",
                    "required": ["role", "music_library_path", "downloads_path"],
                    "properties": {
                        "role": {"type": "string", "enum": ["master", "satellite", "standalone"]},
                        "music_library_path": {"type": "string"},
                        "downloads_path": {"type": "string"},
                        "master_url": {"type": "string", "description": "satellite only — base URL of the master"},
                        "public_master_url": {"type": "string", "description": "master/standalone — URL satellites use"},
                        "api_token": {"type": "string"},
                        "device_name": {"type": "string"},
                        "report_inventory_to_host": {"type": "boolean"},
                        "slsk_username": {"type": "string"},
                        "slsk_password": {"type": "string"},
                        "jellyfin_url": {"type": "string"},
                        "jellyfin_api_key": {"type": "string"},
                        "jellyfin_user_id": {"type": "string"},
                        "jellyfin_music_library_path": {"type": "string"},
                        "lidarr_url": {"type": "string"},
                        "lidarr_api_key": {"type": "string"},
                        "lidarr_enabled": {"type": "boolean"},
                        "lidarr_acquisition_handoff_enabled": {
                            "type": "boolean",
                            "description": (
                                "Explicitly allow non-satellite album queue "
                                "entries to be handed to Lidarr for acquisition"
                            ),
                        },
                        "acoustid_api_key": {"type": "string"},
                        "contact_email": {"type": "string"},
                    },
                    "example": {
                        "role": "satellite",
                        "music_library_path": "/Users/you/Music/Library",
                        "downloads_path": "/Users/you/Downloads/Soulseek",
                        "master_url": "http://master.tail-xxxx.ts.net:5001",
                    },
                },
                "ConfigEnvelope": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "config": {"type": "object", "additionalProperties": True},
                        "editable_keys": {"type": "array", "items": {"type": "string"}},
                        "secret_keys": {"type": "array", "items": {"type": "string"}},
                        "bool_keys": {"type": "array", "items": {"type": "string"}},
                        "groups": {"type": "array", "items": {
                            "type": "object",
                            "properties": {"label": {"type": "string"}, "keys": {"type": "array", "items": {"type": "string"}}},
                        }},
                    },
                },
                "Quality": {
                    "type": "object",
                    "description": "Audio quality descriptor (see src.audio_quality).",
                    "properties": {
                        "ext": {"type": "string"},
                        "lossless": {"type": "boolean"},
                        "bits_per_sample": {"type": "integer"},
                        "sample_rate": {"type": "integer"},
                        "bitrate": {"type": "integer"},
                        "channels": {"type": "integer"},
                        "length_ms": {"type": "integer"},
                        "size_bytes": {"type": "integer"},
                    },
                },
                "ContributionStatus": {
                    "type": "string",
                    "enum": ["attempting", "have_better", "satisfied", "needs_upload", "ingested"],
                },
                "ContributionOffer": {
                    "type": "object",
                    "required": ["artist", "title"],
                    "properties": {
                        "device_id": {"type": "string"},
                        "mbid": {"type": "string"},
                        "isrc": {"type": "string"},
                        "artist": {"type": "string"},
                        "title": {"type": "string"},
                        "album": {"type": "string"},
                        "quality": {"$ref": "#/components/schemas/Quality"},
                    },
                },
                "Contribution": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "device_id": {"type": "string"},
                        "mbid": {"type": "string"},
                        "artist": {"type": "string"},
                        "title": {"type": "string"},
                        "album": {"type": "string"},
                        "status": {"$ref": "#/components/schemas/ContributionStatus"},
                        "target_quality": {"$ref": "#/components/schemas/Quality"},
                        "acquired_quality": {"$ref": "#/components/schemas/Quality"},
                        "updated_at": {"type": "string"},
                    },
                },
                "ArtistTagSnapshot": {
                    "type": "object",
                    "required": ["artist_name", "tags", "fetched_at"],
                    "properties": {
                        "artist_name": {"type": "string"},
                        "mbid": {"type": "string", "nullable": True},
                        "fetched_at": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "tag": {"type": "string"},
                                    "weight": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
                "Suggestion": {
                    "type": "object",
                    "properties": {
                        "mbid": {"type": "string"},
                        "artist": {"type": "string"},
                        "title": {"type": "string"},
                        "search_query": {"type": "string"},
                    },
                },
                "ContributeOneResult": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "mbid": {"type": "string"},
                        "status": {"$ref": "#/components/schemas/ContributionStatus"},
                        "message": {"type": "string"},
                    },
                },
                "DownloadQueueItem": {
                    "type": "object",
                    "required": ["id", "query", "status"],
                    "properties": {
                        "id": {"type": "integer"},
                        "query": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "failed", "success"]},
                        "last_attempt": {"type": "string", "nullable": True},
                        "attempt_count": {"type": "integer"},
                        "max_attempts": {"type": "integer"},
                        "next_attempt_at": {"type": "string", "nullable": True},
                        "is_paused": {"type": "boolean"},
                        "is_quarantined": {"type": "boolean"},
                        "last_error": {"type": "string", "nullable": True},
                        "target_key": {"type": "string"},
                        "phase": {"type": "string"},
                        "failure_class": {"type": "string", "nullable": True},
                        "blocked_reason": {"type": "string", "nullable": True},
                        "strategy_index": {"type": "integer", "minimum": 0},
                        "retained_bytes": {"type": "integer", "minimum": 0},
                        "retained_directories": {"type": "integer", "minimum": 0},
                        "retained_files": {"type": "integer", "minimum": 0},
                        "retained_kinds": {"type": "array", "items": {
                            "type": "string", "enum": ["attempt", "quarantine"]
                        }},
                    },
                },
                "DownloadQueueEnvelope": {
                    "type": "object",
                    "required": ["success", "items", "worker", "residue"],
                    "properties": {
                        "success": {"type": "boolean"},
                        "items": {"type": "array", "items": {
                            "$ref": "#/components/schemas/DownloadQueueItem"
                        }},
                        "worker": {"type": "object"},
                        "residue": {"type": "object", "properties": {
                            "total_bytes": {"type": "integer"},
                            "total_directories": {"type": "integer"},
                            "total_files": {"type": "integer"},
                            "unmatched_item_ids": {"type": "array", "items": {"type": "integer"}},
                            "errors": {"type": "array", "items": {"type": "string"}},
                        }},
                    },
                },
            },
        },
        "security": [{"bearerAuth": []}],
    }


_OVERVIEW = """\
HTTP API for DAPManager, a multi-device music library where **Jellyfin/the
master holds the canonical catalog** and **satellites** (laptops, DAPs) keep a
local subset and can contribute music they acquired independently.

## Setting up from scratch (recommended order for an automation/agent)
1. **GET `/api/healthz`** — confirm the backend is up.
2. **GET `/api/setup/status`** — if `needs_setup` is true, continue; otherwise it's already configured.
3. **POST `/api/save_config`** — pick a `role` (`master`, `satellite`, or `standalone`) and supply paths. A satellite also needs `master_url`. This writes `config.json`; the app initialises on the next request.
4. **GET `/api/config`** — read back the config and the editable-field metadata; adjust anything via **POST `/api/config`**.
5. On a satellite: **POST `/api/sync/all`** to pull the catalog and (if `contribute_to_host` is on) contribute local tracks. Or **POST `/api/contribute`** to only contribute.

## Auth
When `api_token` is set in config, every `/api/*` call (except `/api/healthz`,
and this spec) needs `Authorization: Bearer <token>`. The web UI
uses `/auth` to establish an HttpOnly same-site cookie instead of embedding the
secret in HTML; GET/HEAD also accept `?token=` for browser media URLs. In open
mode (no token) the API is unauthenticated — LAN/Tailscale only.

## Contribution flow (satellite → master)
The satellite offers a track by identifier + quality. The master tries to
download it itself; only if it can't match the quality does it ask the
satellite to upload the actual file. See the **Contributions** tag.
"""

_SAVE_CONFIG_DESC = """\
Creates `config.json` from a role + fields. Only first-run fields are read;
unknown keys are ignored. `role` defaults to `master`.

- **master**: holds the catalog; accepts Jellyfin/Soulseek/Lidarr creds and a `public_master_url`.
- **satellite**: needs `master_url`; leaves the downloader blank (forwards to master). `contribute_to_host` defaults on.
- **standalone**: single-device master with its own downloader.
"""
