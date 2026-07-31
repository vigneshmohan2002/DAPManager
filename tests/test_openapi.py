import re
from unittest.mock import MagicMock

import web_server
from web_server import API_AUTH_EXEMPT_PATHS, app, TaskManager
from src.openapi_spec import UNDOCUMENTED_PATHS, build_spec


# Public compatibility surface for the route-module refactor.  This is a
# behavioural snapshot of Flask's registered API rules rather than a source
# snapshot, so implementation details may move freely while paths, endpoint
# names, and accepted methods remain stable.
EXPECTED_API_ROUTES = {
    ("/api/albums/complete", "complete_albums", ("POST",)),
    ("/api/artist-tags", "get_artist_tags_delta", ("GET",)),
    ("/api/audit", "audit", ("POST",)),
    ("/api/audit/details", "audit_details", ("GET",)),
    ("/api/audit/queue", "audit_queue", ("POST",)),
    ("/api/audit/results", "audit_results", ("GET",)),
    ("/api/catalog", "get_catalog", ("GET",)),
    ("/api/catalog/link-local", "catalog_link_local", ("POST",)),
    ("/api/catalog/pull", "catalog_pull", ("POST",)),
    ("/api/catalog/queue-download", "catalog_queue_download", ("POST",)),
    ("/api/config", "get_config_json", ("GET",)),
    ("/api/config", "update_config", ("POST",)),
    ("/api/contribute", "contribute", ("POST",)),
    ("/api/contribute/track", "contribute_track", ("POST",)),
    ("/api/contributed", "list_contributed", ("GET",)),
    ("/api/contributions", "list_contributions", ("GET",)),
    ("/api/contributions", "post_contribution", ("POST",)),
    ("/api/contributions/<int:contribution_id>", "get_contribution_status", ("GET",)),
    ("/api/contributions/<int:contribution_id>/upload", "upload_contribution", ("POST",)),
    ("/api/download", "download", ("POST",)),
    ("/api/download/albums/request", "request_download_album", ("POST",)),
    ("/api/download/albums/requests", "list_download_album_requests", ("GET",)),
    ("/api/download/albums/requests/<int:request_id>", "get_download_album_request", ("GET",)),
    ("/api/download/albums/search", "search_download_albums", ("GET",)),
    ("/api/download/request", "request_download", ("POST",)),
    ("/api/downloads/<int:item_id>", "delete_download_item", ("DELETE",)),
    ("/api/downloads/<int:item_id>/retry", "retry_download_item", ("POST",)),
    ("/api/downloads/<int:item_id>/residue", "delete_download_residue", ("DELETE",)),
    ("/api/downloads/clear-completed", "clear_completed_downloads", ("POST",)),
    ("/api/downloads/list", "get_downloads_list", ("GET",)),
    ("/api/duplicates", "get_duplicates", ("GET",)),
    ("/api/duplicates/resolve", "resolve_dupes", ("POST",)),
    ("/api/fleet/summary", "fleet_summary", ("GET",)),
    ("/api/fleet/track", "fleet_track_lookup", ("GET",)),
    ("/api/healthz", "healthz", ("GET",)),
    ("/api/install_slsk", "install_slsk", ("POST",)),
    ("/api/inventory", "post_inventory", ("POST",)),
    ("/api/inventory/report", "inventory_report", ("POST",)),
    ("/api/jellyfin/pull", "jellyfin_pull", ("POST",)),
    ("/api/library/albums", "api_library_albums", ("GET",)),
    ("/api/library/albums/<path:album_id>/cover", "api_library_album_cover", ("GET",)),
    ("/api/library/albums/<path:album_id>/tracks", "api_library_album_tracks", ("GET",)),
    ("/api/library/artists", "api_library_artists", ("GET",)),
    ("/api/library/artists/<path:name>/info", "api_library_artist_info", ("GET",)),
    ("/api/library/artists/<path:name>/radio", "api_library_artist_radio", ("GET",)),
    ("/api/library/consolidate-editions", "api_consolidate_editions", ("POST",)),
    ("/api/library/daily-mixes/regenerate", "api_library_daily_mixes_regenerate", ("POST",)),
    ("/api/library/home", "api_library_home", ("GET",)),
    ("/api/library/play-stats", "api_library_play_stats", ("GET",)),
    ("/api/library/playlists", "api_library_playlists", ("GET",)),
    ("/api/library/playlists", "api_library_playlists_create", ("POST",)),
    ("/api/library/playlists/<playlist_id>", "api_library_playlist_delete", ("DELETE",)),
    ("/api/library/playlists/<playlist_id>", "api_library_playlist_update", ("PUT",)),
    ("/api/library/plays", "api_library_record_play", ("POST",)),
    ("/api/library/retag-files", "api_retag_files", ("POST",)),
    ("/api/library/scrub-dangling", "api_scrub_dangling", ("POST",)),
    ("/api/library/search", "library_search", ("GET",)),
    ("/api/library/split-albums", "api_split_albums", ("GET",)),
    ("/api/library/split-albums/dismiss", "api_split_albums_dismiss", ("POST",)),
    ("/api/library/split-albums/merge", "api_split_albums_merge", ("POST",)),
    ("/api/library/tags/backfill", "api_library_tags_backfill", ("POST",)),
    ("/api/library/tracks", "api_library_tracks", ("GET",)),
    ("/api/library/tracks/<mbid>/like", "api_library_track_like", ("DELETE", "POST")),
    ("/api/library/tracks/<mbid>/lyrics", "api_library_track_lyrics", ("GET", "POST")),
    ("/api/library/wrapped", "api_library_wrapped", ("GET",)),
    ("/api/lyrics", "get_lyrics_delta", ("GET",)),
    ("/api/openapi.json", "openapi_spec", ("GET",)),
    ("/api/orphans/playlists", "api_orphan_playlists", ("GET",)),
    ("/api/orphans/tracks", "api_orphan_tracks", ("GET",)),
    ("/api/playlists", "get_playlists_delta", ("GET",)),
    ("/api/playlists", "post_playlists", ("POST",)),
    ("/api/playlists/<playlist_id>", "soft_delete_playlist_route", ("DELETE",)),
    ("/api/playlists/<playlist_id>/restore", "restore_playlist_route", ("POST",)),
    ("/api/playlists/pull", "playlists_pull", ("POST",)),
    ("/api/playlists/push", "playlists_push", ("POST",)),
    ("/api/playlists/queue", "queue_playlists", ("POST",)),
    ("/api/releases/wanted", "releases_wanted", ("GET",)),
    ("/api/satellite-bundle-link", "satellite_bundle_link", ("GET",)),
    ("/api/save_config", "save_config", ("POST",)),
    ("/api/scan", "scan", ("POST",)),
    ("/api/setup/detect-public-url", "setup_detect_public_url", ("GET",)),
    ("/api/setup/status", "setup_status", ("GET",)),
    ("/api/setup/validate-path", "setup_validate_path", ("POST",)),
    ("/api/stats", "get_stats", ("GET",)),
    ("/api/status", "status", ("GET",)),
    ("/api/stream/<path:mbid>", "api_stream_track", ("GET",)),
    ("/api/suggestions", "post_suggestions", ("POST",)),
    ("/api/suggestions/forward", "forward_suggestions", ("POST",)),
    ("/api/sync", "sync", ("POST",)),
    ("/api/sync/all", "sync_all", ("POST",)),
    ("/api/sync/state", "sync_state", ("GET",)),
    ("/api/tag/apply/<mbid>", "tag_apply", ("POST",)),
    ("/api/tag/identify/<mbid>", "tag_identify", ("POST",)),
    ("/api/tracks/<mbid>", "soft_delete_track", ("DELETE",)),
    ("/api/tracks/<mbid>/file", "delete_track_file", ("DELETE",)),
    ("/api/tracks/<mbid>/restore", "restore_track_route", ("POST",)),
    ("/api/tracks/needs-review", "tracks_needs_review", ("GET",)),
}


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _norm(path: str) -> str:
    # Collapse both Flask <...> and OpenAPI {...} params to one placeholder.
    return re.sub(r"[<{][^>}]+[>}]", "{}", path)


def test_spec_is_wellformed():
    spec = build_spec()
    assert spec["openapi"].startswith("3.")
    assert spec["paths"]
    assert spec["info"]["title"] == "DAPManager API"


def test_api_route_method_and_endpoint_contract():
    actual = {
        (
            rule.rule,
            rule.endpoint,
            tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})),
        )
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/")
    }
    assert actual == EXPECTED_API_ROUTES


def test_api_auth_exemption_allowlist_is_exact():
    # Expanding this set creates an unauthenticated API surface, so require an
    # explicit contract update rather than allowing an accidental exemption.
    # Unscoped /api/status is handled separately because scoped status carries
    # application data and must pass through the normal token gate.
    assert API_AUTH_EXEMPT_PATHS == {
        "/api/healthz",
        "/api/openapi.json",
    }


def test_every_documented_path_exists_in_url_map():
    spec = build_spec()
    flask_rules = {_norm(r.rule) for r in app.url_map.iter_rules()}
    missing = [p for p in spec["paths"] if _norm(p) not in flask_rules]
    assert not missing, f"documented but not routed: {missing}"


def test_every_api_route_is_documented_or_explicitly_internal():
    documented = {_norm(path) for path in build_spec()["paths"]}
    routed = {
        _norm(rule.rule)
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api/")
    }
    unexpected = sorted(routed - documented - set(UNDOCUMENTED_PATHS))
    stale = sorted(set(UNDOCUMENTED_PATHS) - routed)
    assert not unexpected, f"new API routes need docs or allowlisting: {unexpected}"
    assert not stale, f"stale undocumented-route allowlist entries: {stale}"


def test_docs_and_spec_reachable_before_setup(monkeypatch):
    # No config on disk → setup gate must NOT redirect the docs.
    monkeypatch.setattr(web_server, "config_exists", lambda: False)
    c = _client()
    docs = c.get("/docs", follow_redirects=False)
    assert docs.status_code == 200
    html = docs.get_data(as_text=True)
    assert "unpkg" not in html
    assert "https://" not in html
    assert c.get("/static/api-docs.css").status_code == 200
    assert c.get("/static/api-docs.js").status_code == 200
    r = c.get("/api/openapi.json")
    assert r.status_code == 200
    assert r.get_json()["openapi"].startswith("3.")


def test_spec_exempt_from_bearer_token(monkeypatch):
    # Even with api_token set, the spec is readable without a bearer header.
    cfg = MagicMock()
    cfg._config = {"api_token": "secret"}
    monkeypatch.setattr(web_server, "config", cfg)
    monkeypatch.setattr(web_server, "task_manager", TaskManager())
    monkeypatch.setattr(web_server, "config_exists", lambda: True)
    c = _client()
    assert c.get("/api/openapi.json").status_code == 200


def test_contribution_paths_are_documented():
    spec = build_spec()
    for p in ("/api/save_config", "/api/contribute", "/api/contributions",
              "/api/contributions/{id}/upload"):
        assert p in spec["paths"], p


def test_download_recovery_contract_is_documented():
    spec = build_spec()
    assert "/api/downloads/list" in spec["paths"]
    assert "/api/downloads/{item_id}/residue" in spec["paths"]
    schema = spec["components"]["schemas"]["DownloadQueueItem"]
    assert {
        "attempt_count",
        "is_quarantined",
        "last_error",
        "retained_bytes",
        "retained_directories",
    } <= set(schema["properties"])
