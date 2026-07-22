import os
import json
import logging
from typing import Optional
from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from werkzeug.middleware.proxy_fix import ProxyFix

from src.config_paths import resolve_config_path
from src.services import album_task_service
from src.services import album_download_request_service
from src.services import contribution_service
from src.services import download_discovery_service
from src.services import fleet_service
from src.services import library_application_service
from src.services import listening_service
from src.services import lyrics_service
from src.services import maintenance_application_service
from src.services import playlist_service
from src.services import setup_application_service
from src.services import tag_application_service
from src.services.library_service import (
    availability_for,
    build_artist_radio_payload,
    is_master_configured,
    list_local_album_tracks,
    list_public_albums,
    public_track_row,
    query_public_tracks,
)
from src.services.config_service import (
    build_first_run_config,
    build_public_config,
    merge_config_update,
    normalize_config_update,
    read_config_file,
    reload_runtime_config,
    write_config_file,
)
from src.services.media_proxy_service import (
    FileStreamResolution,
    LocalAlbumCoverResolution,
    MasterAlbumCoverResolution,
    MasterAlbumTracksResolution,
    MasterStreamResolution,
    guess_audio_mime,
    request_album_cover,
    request_album_tracks,
    request_stream,
    resolve_album_cover,
    resolve_album_tracks,
    resolve_stream_source,
)
from src.services.scheduler_service import (
    build_library_maintenance_scheduler,
    build_release_watcher_scheduler,
    build_sync_scheduler,
    stop_scheduler,
)
from src.services.task_service import TaskManager

logger = logging.getLogger(__name__)

# ... (Previous imports are fine, but imports that depend on config might fail if config is missing)
# We need to wrap imports or loading of config-dependent modules

TRUST_PROXY_ENV = "DAPMANAGER_TRUST_PROXY"


def _trust_one_proxy_hop(environment=None) -> bool:
    """Whether this deployment explicitly trusts one local reverse proxy.

    Proxy headers are a deployment trust boundary, not an end-user setting.
    Keep the opt-in deliberately strict: only the literal value ``1`` enables
    it.  The Docker topology that sets this flag publishes the backend on
    127.0.0.1 only, so an untrusted network client cannot send forged
    X-Forwarded-* headers directly to Flask.
    """
    values = os.environ if environment is None else environment
    return values.get(TRUST_PROXY_ENV) == "1"


def _proxy_aware_wsgi_app(wsgi_app):
    """Trust exactly one hop for the fields Tailscale Serve supplies."""
    return ProxyFix(
        wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_port=1,
    )


app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
if _trust_one_proxy_hop():
    app.wsgi_app = _proxy_aware_wsgi_app(app.wsgi_app)

# Check if config exists
CONFIG_FILE = resolve_config_path()


def config_exists():
    return os.path.exists(CONFIG_FILE)


# Defer loading of modules that require config until we are sure it exists
task_manager = None
config = None
sync_scheduler = None
release_watcher_scheduler = None
library_maintenance_scheduler = None


def _stop_scheduler(instance) -> None:
    stop_scheduler(instance, event_logger=logger)


def init_app_logic():
    global task_manager, config, setup_logging, get_config, DatabaseManager, DownloadItem, main_scan_library, main_run_downloader, main_run_sync, EnvironmentManager, audit_lib_logic, complete_albums_logic

    # Import modules here to avoid crash on startup if config is missing/invalid
    from src.logger_setup import setup_logging
    from src.config_manager import get_config
    from src.db_manager import DatabaseManager, DownloadItem
    from src.library_scanner import main_scan_library
    from src.downloader import main_run_downloader
    from src.sync_dap import main_run_sync
    from src.album_completer import audit_library as audit_lib_logic
    from src.album_completer import complete_albums as complete_albums_logic

    setup_logging()
    config = get_config()

    from src import musicbrainz_client
    musicbrainz_client.configure(config.contact_email)

    task_manager = TaskManager()

    if not (config._config.get("api_token") or "").strip():
        logger.warning(
            "API running in open mode: no api_token set in config.json. "
            "Any device on the network can hit /api/* endpoints. "
            "Set api_token in the Settings card to lock this down."
        )

    _start_sync_scheduler()
    _start_release_watcher()
    _start_library_maintenance_scheduler()


def _start_sync_scheduler(*, run_on_startup: Optional[bool] = None):
    """Kick off the periodic Sync All loop if configured."""
    global sync_scheduler
    from src.sync_scheduler import SyncScheduler

    _stop_scheduler(sync_scheduler)
    sync_scheduler = None
    sync_scheduler = build_sync_scheduler(
        config_values=config._config,
        db_path=config.db_path,
        config_context=config,
        task_manager=task_manager,
        task_target=run_sync_all,
        scheduler_factory=SyncScheduler,
        run_on_startup=run_on_startup,
        event_logger=logger,
    )
    sync_scheduler.start()


def _start_release_watcher():
    """Poll Lidarr's wanted/missing list and route new releases through sldl.

    Master-only and opt-in (``lidarr_watch_enabled``). Skips silently when
    Lidarr isn't configured — see ``downloader._build_lidarr_client`` for
    the full guard chain.
    """
    global release_watcher_scheduler
    from src.sync_scheduler import SyncScheduler
    from src.downloader import _build_lidarr_client
    from src.release_watcher import run_watch_tick

    _stop_scheduler(release_watcher_scheduler)
    release_watcher_scheduler = None
    release_watcher_scheduler = build_release_watcher_scheduler(
        config_values=config._config,
        is_master=config.is_master,
        db_path=config.db_path,
        database_factory=DatabaseManager,
        lidarr_client_factory=_build_lidarr_client,
        watch_tick=run_watch_tick,
        scheduler_factory=SyncScheduler,
        event_logger=logger,
    )
    if release_watcher_scheduler is None:
        return
    release_watcher_scheduler.start()


def _start_library_maintenance_scheduler(
    *, run_on_startup: Optional[bool] = None
):
    """Refresh MusicBrainz tags, then Daily Mixes, on a weekly cadence.

    Master-only. Setting ``library_maintenance_interval_seconds`` to zero
    disables the loop. The shared TaskManager rejects a tick while another
    background job is active, and the maintenance runner has its own lock as
    a second guard against overlapping MusicBrainz passes.
    """
    global library_maintenance_scheduler
    from src.library_maintenance import maintenance_interval_seconds
    from src.sync_scheduler import SyncScheduler

    _stop_scheduler(library_maintenance_scheduler)
    library_maintenance_scheduler = None
    library_maintenance_scheduler = build_library_maintenance_scheduler(
        config_values=config._config,
        is_master=config.is_master,
        db_path=config.db_path,
        config_context=config,
        task_manager=task_manager,
        task_target=run_library_maintenance_task,
        interval_resolver=maintenance_interval_seconds,
        scheduler_factory=SyncScheduler,
        run_on_startup=run_on_startup,
        event_logger=logger,
    )
    if library_maintenance_scheduler is None:
        return
    library_maintenance_scheduler.start()


# Helper wrappers (need to be defined or redefined after init)
def run_scan(db_path, conf):
    with DatabaseManager(db_path) as db:
        main_scan_library(db, conf._config)


def run_download(db_path, conf, progress_callback=None):
    with DatabaseManager(db_path) as db:
        return main_run_downloader(
            db,
            conf._config,
            progress_callback=progress_callback,
        )


def run_sync(db_path, conf, mode, fmt, reconcile=False):
    with DatabaseManager(db_path) as db:
        return main_run_sync(
            db, conf._config, sync_mode=mode, conversion_format=fmt, reconcile=reconcile
        )


def run_jellyfin_pull(db_path, conf, progress_callback=None):
    from src.jellyfin_client import main_run_jellyfin_pull
    with DatabaseManager(db_path) as db:
        main_run_jellyfin_pull(db, conf._config, progress_callback=progress_callback)


def run_catalog_pull(db_path, conf, progress_callback=None):
    from src.catalog_sync import main_run_catalog_pull
    with DatabaseManager(db_path) as db:
        main_run_catalog_pull(db, conf._config, progress_callback=progress_callback)


def run_playlist_pull(db_path, conf, progress_callback=None):
    from src.catalog_sync import main_run_playlist_pull
    with DatabaseManager(db_path) as db:
        main_run_playlist_pull(db, conf._config, progress_callback=progress_callback)


def run_playlist_push(db_path, conf, progress_callback=None):
    from src.catalog_sync import main_run_playlist_push
    with DatabaseManager(db_path) as db:
        main_run_playlist_push(db, conf._config, progress_callback=progress_callback)


def run_inventory_report(db_path, conf, progress_callback=None):
    from src.inventory_sync import main_run_inventory_report
    with DatabaseManager(db_path) as db:
        main_run_inventory_report(db, conf._config, progress_callback=progress_callback)


def run_contribute(db_path, conf, progress_callback=None):
    from src.contribution_sync import main_run_contribute
    with DatabaseManager(db_path) as db:
        main_run_contribute(db, conf._config, progress_callback=progress_callback)


def run_sync_all(db_path, conf, progress_callback=None):
    from src.sync_all import main_run_sync_all
    with DatabaseManager(db_path) as db:
        main_run_sync_all(db, conf._config, progress_callback=progress_callback)


def run_catalog_link_local(db_path, conf, progress_callback=None):
    from src.catalog_linker import main_run_catalog_linker
    with DatabaseManager(db_path) as db:
        main_run_catalog_linker(db, conf._config, progress_callback=progress_callback)


def run_batch():
    from manager import batch_sync
    batch_sync()


def run_queue_playlists(db_path, conf, urls):
    """Queue multiple Spotify playlists for download."""
    from src.spotify_client import SpotifyClient
    with DatabaseManager(db_path) as db:
        spot_client = SpotifyClient(db)
        for url in urls:
            if url.strip():
                spot_client.process_playlist(url)


def run_audit(db_path):
    with DatabaseManager(db_path) as db:
        audit_lib_logic(db)


def run_tag_backfill(db_path, incremental=True, progress_callback=None):
    """TaskManager target — kept out of the endpoint body so the
    request handler stays thin and the job can also be invoked from
    a future scheduler tick."""
    from src.genre_backfill import backfill_artist_tags
    with DatabaseManager(db_path) as db:
        return backfill_artist_tags(
            db,
            progress_callback=progress_callback,
            incremental=incremental,
        )


def run_library_maintenance_task(db_path, conf, progress_callback=None):
    """TaskManager target for the scheduled tag + Daily Mix refresh."""
    from src.library_maintenance import run_library_maintenance
    with DatabaseManager(db_path) as db:
        return run_library_maintenance(
            db,
            conf._config,
            progress_callback=progress_callback,
        )


def build_suggestion_items(raw_items):
    """Normalize a suggestions payload into (search_query, mbid_guess) pairs.

    Each raw item may provide any of:
      - search_query: used verbatim
      - mbid: kept as mbid_guess; if artist+title are also present they form the query
      - artist + title: combined into "artist - title"

    Returns a list of (query, mbid) tuples. Items without a usable query are dropped.
    Duplicates (same query, case-insensitive) are removed preserving first occurrence.
    """
    return download_discovery_service.build_suggestion_items(raw_items)


def run_complete_albums(db_path, conf, run_downloads=False, progress_callback=None):
    """Run the full album completion pipeline, optionally followed by downloads."""
    return album_task_service.run_album_completion_pipeline(
        db_path=db_path,
        config_values=conf._config,
        run_downloads=run_downloads,
        progress_callback=progress_callback,
        database_factory=DatabaseManager,
        complete_albums=complete_albums_logic,
        run_downloader=main_run_downloader,
        scan_library=main_scan_library,
    )


@app.before_request
def check_setup():
    # If config doesn't exist, force redirect to /setup
    # Allow static files and the save_config endpoint
    if not config_exists() and request.endpoint not in (
        "setup",
        "save_config",
        "setup_validate_path",
        "setup_detect_public_url",
        "setup_status",
        "download_mac",
        "healthz",
        "static",
        "api_docs",
        "openapi_spec",
        "service_worker",
    ):
        return redirect(url_for("setup"))

    # If config exists but app logic isn't loaded (e.g. just created), load it
    global task_manager
    if config_exists() and task_manager is None:
        init_app_logic()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/player")
def player_page():
    """Mobile-optimised PWA player for iOS Add-to-Home-Screen installs.

    Streams audio from /api/stream/<mbid> which already supports HTTP Range
    (206 Partial Content) for seeking, so the <audio> element works natively.
    Uses the Media Session API for lock-screen / AirPods controls.
    """
    return render_template("player.html")


@app.route("/satellite")
def satellite_page():
    """iOS satellite PWA — 4-tab interface for remote music management."""
    return render_template("satellite.html")


@app.route("/setup")
def setup():
    if config_exists():
        return redirect(url_for("index"))
    return render_template("setup.html")


from src.config_keys import (
    BOOL_KEYS as CONFIG_BOOL_KEYS,
    DEFAULT_VALUES as CONFIG_DEFAULT_VALUES,
    EDITABLE_KEYS as CONFIG_EDITABLE_KEYS,
    GROUPS as CONFIG_GROUPS,
    SECRET_KEYS as CONFIG_SECRET_KEYS,
)
from src.config_manager import normalize_device_role

API_AUTH_EXEMPT_PATHS = {"/api/healthz", "/api/openapi.json"}
AUTH_COOKIE_NAME = "dapmanager_auth"
TAURI_API_ORIGINS = frozenset({
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
})


def _configured_api_token() -> str:
    if config is None:
        return ""
    cfg_dict = getattr(config, "_config", None)
    if not isinstance(cfg_dict, dict):
        return ""
    return (cfg_dict.get("api_token") or "").strip()


def _valid_api_token(provided: str) -> bool:
    import hmac

    expected = _configured_api_token()
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def _web_origin_key(value: str):
    """Normalize an HTTP(S) origin to ``(scheme, host, effective_port)``."""
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value or "")
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


def _cookie_mutation_is_same_origin() -> bool:
    """Validate browser provenance for a cookie-authenticated mutation.

    ``Origin`` is authoritative when present. ``Referer`` is a fallback for
    user agents that omit Origin, and an absent/opaque provenance is rejected.
    Port participates in the comparison as required by the web origin model.
    """
    origin = request.headers.get("Origin")
    if origin is None:
        origin = request.headers.get("Referer")
    if not origin:
        return False
    return _web_origin_key(origin) == _web_origin_key(request.host_url)


def _safe_next_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


@app.before_request
def _handle_tauri_cors_preflight():
    """Answer only the known desktop-webview origins, never arbitrary sites."""
    if request.method != "OPTIONS" or not request.path.startswith("/api/"):
        return None
    if request.headers.get("Origin") not in TAURI_API_ORIGINS:
        return None
    return Response(status=204)


@app.after_request
def _add_tauri_cors_headers(response):
    origin = request.headers.get("Origin")
    if request.path.startswith("/api/") and origin in TAURI_API_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Range"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS"
        )
        response.headers["Access-Control-Expose-Headers"] = (
            "Accept-Ranges, Content-Length, Content-Range"
        )
    if request.path.startswith("/api/download/albums"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.before_request
def _protect_web_ui():
    """Require a signed-in cookie before rendering pages that control the API.

    Older builds embedded the raw API token into every HTML response so their
    JavaScript could add an Authorization header.  Because the pages
    themselves were public, that exposed the secret and nullified API auth.
    The browser UI now authenticates once and uses an HttpOnly, same-site
    cookie; Tauri continues to use its bearer token command.
    """
    if request.path.startswith("/api/"):
        return None
    if request.endpoint in {
        "static",
        "setup",
        "download_mac",
        "auth_login",
        "service_worker",
    }:
        return None
    token = _configured_api_token()
    if not token:
        return None
    if _valid_api_token(request.cookies.get(AUTH_COOKIE_NAME, "")):
        return None

    # A tokenised bookmark/download link may establish the cookie once. Strip
    # it from the address bar immediately so navigation and referrers do not
    # keep copying the credential around.
    query_token = (request.args.get("token") or "").strip()
    if _valid_api_token(query_token):
        from urllib.parse import urlencode

        args = request.args.copy()
        args.pop("token", None)
        target = request.path
        if args:
            target += "?" + urlencode(list(args.items(multi=True)))
        response = redirect(target)
        response.set_cookie(
            AUTH_COOKIE_NAME,
            token,
            httponly=True,
            secure=request.is_secure,
            samesite="Strict",
            max_age=30 * 24 * 60 * 60,
        )
        return response

    next_url = _safe_next_url(request.full_path.rstrip("?"))
    return redirect(url_for("auth_login", next=next_url))


@app.route("/auth", methods=["GET", "POST"])
def auth_login():
    """Authenticate the browser UI without placing the secret in page JS."""
    token = _configured_api_token()
    next_url = _safe_next_url(request.values.get("next", "/"))
    if not token:
        return redirect(next_url)
    error = None
    if request.method == "POST":
        if _valid_api_token((request.form.get("token") or "").strip()):
            response = redirect(next_url)
            response.set_cookie(
                AUTH_COOKIE_NAME,
                token,
                httponly=True,
                secure=request.is_secure,
                samesite="Strict",
                max_age=30 * 24 * 60 * 60,
            )
            return response
        error = "Invalid API token."
    return render_template("auth.html", next_url=next_url, error=error), (
        401 if error else 200
    )


@app.route("/service-worker.js")
def service_worker():
    """Serve the PWA worker at the origin root so it can control every UI.

    The worker is deliberately public like the other static assets, but the
    script itself must never be served from a browser cache: a stale worker
    could otherwise retain an obsolete cache policy after an application
    update.
    """
    response = app.send_static_file("service-worker.js")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.before_request
def _check_api_token():
    """Enforce Authorization: Bearer <token> on /api/* when api_token is set.

    Open mode (no token in config) keeps current behavior for LAN-only setups;
    a warning is logged at init time so the operator knows it's unauthenticated.
    Unscoped /api/status is exempt so health checks don't need the header.
    Scoped status surfaces are application data and use normal authentication.
    Authenticated GET/HEAD URLs may also carry ``?token=`` because browser
    media elements (album art and audio) cannot set an Authorization header.
    Mutating routes remain header-only so tokens do not get copied into action
    URLs.
    """
    if not request.path.startswith("/api/"):
        return None
    if request.path in API_AUTH_EXEMPT_PATHS:
        return None
    if request.path == "/api/status" and not request.args.get("scope", "").strip():
        return None
    if config is None:
        return None
    cfg_dict = getattr(config, "_config", None)
    if not isinstance(cfg_dict, dict):
        return None
    token = (cfg_dict.get("api_token") or "").strip()
    if not token:
        return None

    header = request.headers.get("Authorization", "")
    provided = ""
    auth_source = ""
    if header.startswith("Bearer "):
        provided = header[len("Bearer "):].strip()
        auth_source = "bearer"
    elif request.method in {"GET", "HEAD"}:
        provided = (request.args.get("token") or "").strip()
        if provided:
            auth_source = "query"
    if not provided:
        provided = (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
        if provided:
            auth_source = "cookie"
    if not provided:
        return jsonify({"success": False, "message": "missing bearer token"}), 401
    if not _valid_api_token(provided):
        return jsonify({"success": False, "message": "invalid api token"}), 401
    if (
        auth_source == "cookie"
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not _cookie_mutation_is_same_origin()
    ):
        return jsonify({
            "success": False,
            "message": (
                "cookie-authenticated mutations require a same-origin "
                "Origin or Referer"
            ),
        }), 403
    return None


@app.route("/api/config", methods=["GET"])
def get_config_json():
    """Return current config with secret fields redacted to ''.

    The UI shows a placeholder for secret fields; leaving them blank on
    save means "don't change". Unknown keys are passed through so the
    page can at least show them, but the edit form only surfaces the
    editable set.
    """
    try:
        raw = read_config_file(CONFIG_FILE)
    except FileNotFoundError:
        return jsonify({"success": False, "message": "config.json not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify(build_public_config(raw))


@app.route("/api/config", methods=["POST"])
def update_config():
    """Partial-merge update into config.json.

    Only keys in CONFIG_EDITABLE_KEYS are accepted; others are silently
    ignored so the UI can't wipe e.g. the db path. Empty strings for
    secret keys mean "don't change".
    """
    browser_cookie_was_valid = _valid_api_token(
        (request.cookies.get(AUTH_COOKIE_NAME) or "").strip()
    )
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "body must be an object"}), 400
    try:
        data = normalize_config_update(data)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400

    try:
        current = read_config_file(CONFIG_FILE)
    except FileNotFoundError:
        return jsonify({"success": False, "message": "config.json not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    current, changed = merge_config_update(current, data)

    try:
        write_config_file(CONFIG_FILE, current)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    try:
        reload_runtime_config(
            config,
            changed,
            start_sync_scheduler=_start_sync_scheduler,
            start_release_watcher=_start_release_watcher,
            start_library_maintenance_scheduler=(
                _start_library_maintenance_scheduler
            ),
        )
    except Exception as e:
        logger.warning(f"Config written but in-process reload failed: {e}")

    response = jsonify({"success": True, "changed": changed})
    if "api_token" in changed and browser_cookie_was_valid:
        refreshed = _configured_api_token()
        if refreshed:
            response.set_cookie(
                AUTH_COOKIE_NAME,
                refreshed,
                httponly=True,
                secure=request.is_secure,
                samesite="Strict",
                max_age=30 * 24 * 60 * 60,
            )
    return response


@app.route("/api/save_config", methods=["POST"])
def save_config():
    """Persist a fresh config.json from the setup wizard payload.

    Routes through ``src.first_run.build_initial_config`` so the wizard
    and any future programmatic setup share one shape definition.
    Defaults ``role`` to ``master`` to stay compatible with the legacy
    single-form template that didn't send a role.
    """
    from src.first_run import build_initial_config

    data = request.json or {}
    try:
        new_config = build_first_run_config(
            CONFIG_FILE,
            data,
            build_initial_config,
        )
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "message": str(e)}), 400

    try:
        write_config_file(CONFIG_FILE, new_config)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception("save_config failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/setup/status", methods=["GET"])
def setup_status():
    """Return whether first-run setup is still needed.

    Whitelisted in check_setup so the Tauri app can check on boot
    before config.json exists.
    """
    return jsonify({"needs_setup": not config_exists()})


@app.route("/api/setup/validate-path", methods=["POST"])
def setup_validate_path():
    """Light-weight existence check used by step 2 of the setup wizard.

    Body: {"path": str, "kind": "directory"|"file"} (kind defaults to
    "directory"). Returns {"ok": bool, "message"?: str}. Empty paths
    are treated as missing rather than valid — the wizard can render
    its own "required" copy when the field is blank.
    """
    data = request.json or {}
    path = (data.get("path") or "").strip()
    kind = (data.get("kind") or "directory").strip().lower()
    if not path:
        return jsonify({"ok": False, "message": "path is empty"})
    if not os.path.exists(path):
        return jsonify({"ok": False, "message": "path does not exist"})
    if kind == "directory" and not os.path.isdir(path):
        return jsonify({"ok": False, "message": "not a directory"})
    if kind == "file" and not os.path.isfile(path):
        return jsonify({"ok": False, "message": "not a file"})
    return jsonify({"ok": True})


def _read_master_config_for_download():
    """Re-read config.json from disk for /download/mac.

    Avoids depending on the in-process ``config`` global, which only
    becomes truthy after init_app_logic runs. The download endpoint
    needs to work right after the wizard finishes — before the next
    request triggers init.
    """
    return setup_application_service.read_setup_config(CONFIG_FILE)


BUNDLE_LINK_TTL_SECONDS = setup_application_service.BUNDLE_LINK_TTL_SECONDS


def _bundle_download_token(api_token: str, expires_at: int) -> str:
    """Mint a bundle-only token without exposing the general API secret."""
    return setup_application_service.bundle_download_token(api_token, expires_at)


def _valid_bundle_download_token(value: str, api_token: str) -> bool:
    return setup_application_service.valid_bundle_download_token(
        value,
        api_token,
    )


@app.route("/api/satellite-bundle-link", methods=["GET"])
def satellite_bundle_link():
    """Return a short-lived, bundle-scoped sharing URL for the dashboard."""
    cfg = _read_master_config_for_download() or {}
    result = setup_application_service.build_satellite_bundle_link(cfg)
    return jsonify(result.payload), result.status_code


@app.route("/download/mac", methods=["GET"])
def download_mac():
    """Serve the satellite Mac bundle with this master's URL embedded.

    Refuses with 409 when ``public_master_url`` isn't configured —
    serving a bundle pinned to a URL satellites can't reach is a
    worse outcome than failing loudly. When ``api_token`` is set, accepts the
    bearer token, authenticated browser cookie, legacy ``?token=``, or a
    short-lived bundle-only token minted by ``/api/satellite-bundle-link``.
    """
    cfg = _read_master_config_for_download() or {}
    result = setup_application_service.prepare_satellite_bundle_download(
        cfg,
        authorization_header=request.headers.get("Authorization", ""),
        query_token=request.args.get("token") or "",
        cookie_token=request.cookies.get(AUTH_COOKIE_NAME) or "",
        bundle_token=request.args.get("bundle_token") or "",
        event_logger=logger,
    )
    if result.body is None:
        return jsonify(result.payload), result.status_code
    return Response(
        result.body,
        status=result.status_code,
        mimetype=result.mimetype,
        headers=result.headers,
    )


@app.route("/api/setup/detect-public-url", methods=["GET"])
def setup_detect_public_url():
    """Suggest a public URL for the master at first-run.

    Detection priority, matching the host bootstrap script:
      1. ``MASTER_PUBLIC_URL`` env (populated by the bootstrap script).
      2. In-container ``tailscale status --json`` (only useful when
         Tailscale runs in the container or for non-Docker installs).
    Returns {"source": "env"|"tailscale"|"none", "url"?: str}.
    """
    result = setup_application_service.detect_public_url()
    return jsonify(result.payload), result.status_code


@app.route("/api/library/albums")
def api_library_albums():
    """List distinct albums from the scanned library.

    Feeds the desktop album grid. ``id`` is the release MBID when
    present, else an ``album|artist`` synthetic — stable enough to use
    as a React key and to look cover art back up by.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            payload = list_public_albums(db)
        return jsonify(payload)
    except Exception as e:
        logger.exception("api_library_albums failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/tracks")
def api_library_tracks():
    """Flat list of tracks for the library browser.

    Default (no params): every accessible track in the live library —
    backward-compatible with the Tauri Songs screen. Unavailable rows
    (no local file, no DAP drive, no master) are dropped.

    Query params (all optional):
      playlist_id:       scope to a playlist's membership (track_order).
      local_only=1:      drop rows without a local file.
      include_orphans=1: include soft-deleted rows; each row gets an
                         ``orphan`` flag the UI can badge. When this flag
                         is set, unavailable rows are *kept* so the user
                         can still see and act on (restore/purge) them.

    Each row carries an ``availability`` string: "local" when the file is
    on disk here, "drive" when only the DAP path is set, "remote" when
    neither is set but a master URL is configured (we'll proxy-stream).
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503

    playlist_id = (request.args.get("playlist_id") or "").strip() or None
    local_only = request.args.get("local_only", "").lower() in ("1", "true", "yes")
    include_orphans = request.args.get("include_orphans", "").lower() in (
        "1", "true", "yes",
    )

    try:
        with DatabaseManager(config.db_path) as db:
            payload = query_public_tracks(
                db,
                playlist_id=playlist_id,
                local_only=local_only,
                include_orphans=include_orphans,
                has_master=_master_url_configured(),
            )
        return jsonify(payload)
    except Exception as e:
        logger.exception("api_library_tracks failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/tracks/<mbid>/like", methods=["POST", "DELETE"])
def api_library_track_like(mbid: str):
    """Toggle the heart on a track.

    POST sets ``tracks.is_liked = 1`` and ensures the Liked Songs smart
    playlist exists (idempotent — only the *first* like in a fresh
    library creates it). DELETE flips back to 0 but does not remove the
    playlist; the user can keep the empty sidebar pin or remove it
    manually like any other playlist.

    On satellites the request is proxied to the master so the like
    survives the next catalog sync (otherwise the master's is_liked=0
    would clobber the satellite's local flip). The satellite also
    applies the change locally on success so the UI doesn't bounce
    between optimistic flip and next-sync resolution.

    404 when the mbid isn't in the library so the desktop can branch on
    status code without parsing the body — matches the convention used
    by Stage 10a's retry-download endpoint.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    mbid = (mbid or "").strip()
    if not mbid:
        return jsonify({"success": False, "message": "mbid is required"}), 400

    import requests

    result = library_application_service.apply_track_like(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        config_data=getattr(config, "_config", None),
        mbid=mbid,
        method=request.method,
        request_sender=requests.request,
        master_configured=_master_url_configured(),
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/library/playlists", methods=["GET"])
def api_library_playlists():
    """Live playlists with membership counts, for the library sidebar.

    Distinct from ``GET /api/playlists`` (which is the sync delta feed).
    Orphans are excluded — the /orphans page handles those. ``smart_rules``
    on each row is decoded into a dict (or null) so the client renders the
    smart-playlist badge and pre-fills the rule editor without parsing
    JSON-in-JSON.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            result = playlist_service.list_library_playlists(db)
        return jsonify(result.payload), result.status_code
    except Exception as e:
        logger.exception("api_library_playlists failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/playlists", methods=["POST"])
def api_library_playlists_create():
    """Create a playlist. Body: ``{"name": "...", "smart_rules": {...}?}``.

    ``smart_rules`` is optional; when provided it must be a ruleset object
    (``{match: "all"|"any", rules: [...]}``) which is validated and stored
    JSON-encoded. Smart playlists evaluate against the tracks table at
    read time — no manual membership rows are inserted. Returns the
    generated ``playlist_id``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    prepared = playlist_service.prepare_playlist_create(data)
    if isinstance(prepared, playlist_service.PlaylistServiceResult):
        return jsonify(prepared.payload), prepared.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            result = playlist_service.create_library_playlist(db, prepared)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.exception("api_library_playlists_create failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/library/playlists/<playlist_id>", methods=["PUT"])
def api_library_playlist_update(playlist_id: str):
    """Partial update: rename, replace full membership, and/or set rules.

    Body keys (all optional — at least one required):
      - ``name``: new display name (trimmed; rejected if empty).
      - ``track_mbids``: list of mbids in the desired order. An empty
        list explicitly empties the playlist; omit the key to leave
        membership untouched.
      - ``smart_rules``: ruleset object, or ``null`` to clear (turning
        a smart playlist back into a static one). Validated against
        the field/op whitelist before storage. Mutually exclusive with
        ``track_mbids`` in the same request — smart playlists derive
        membership from rules, so mixing the two has no coherent
        outcome.

    Unknown mbids in ``track_mbids`` are silently dropped and surface
    as ``landed`` vs ``requested`` in the response so the UI can flag
    a partial miss.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    prepared = playlist_service.prepare_playlist_update(data)
    if isinstance(prepared, playlist_service.PlaylistServiceResult):
        return jsonify(prepared.payload), prepared.status_code

    try:
        with DatabaseManager(config.db_path) as db:
            result = playlist_service.update_library_playlist(
                db,
                playlist_id,
                prepared,
            )
    except Exception as e:
        logger.exception("api_library_playlist_update failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/library/playlists/<playlist_id>", methods=["DELETE"])
def api_library_playlist_delete(playlist_id: str):
    """Soft-delete a playlist from the library UI. Delegates to the
    existing ``DELETE /api/playlists/<id>`` logic so the row shows up
    on the /orphans page and can be restored there. ``?purge=true``
    is forwarded for hard-delete parity.
    """
    return soft_delete_playlist_route(playlist_id)


def _master_url_configured() -> bool:
    """True when config carries a non-empty ``master_url``.

    Uses the same ``isinstance(_config, dict)`` guard as the auth hook
    so tests that stub ``config`` with a MagicMock don't trip a falsy
    ``MagicMock`` into a truthy "master is configured" signal.
    """
    return is_master_configured(getattr(config, "_config", None))


def _availability_for(row: dict, has_master: bool) -> str:
    """Resolve a track's playback tier from its columns + master config.

    Priority: a real local file > a DAP path that only resolves when the
    drive is mounted > the master's stream endpoint. Path columns are
    trusted at listing time; actual file existence is checked at stream
    time so the UI doesn't pay a stat-per-row tax.
    """
    return availability_for(row, has_master)


def _public_track_row(row: dict, has_master: bool) -> dict:
    """Shape a DB track row for the webview: strip on-disk paths, add availability."""
    return public_track_row(row, has_master)


@app.route("/api/library/artists")
def api_library_artists():
    """List distinct artists with album + track counts.

    Feeds the desktop Artists screen. Artist name is used as the
    identifier since we don't have a separate artists table — the
    frontend passes it back URL-encoded to fetch that artist's albums.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            artists = db.list_artists()
        return jsonify({"success": True, "artists": artists})
    except Exception as e:
        logger.exception("api_library_artists failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/artists/<path:name>/info")
def api_library_artist_info(name: str):
    """Wikipedia summary for an artist, used by the desktop infoscreen.

    Returns a success:false body (HTTP 200) on misses rather than 404 so the
    UI can render a quiet empty-state without console noise. Network/parse
    failures inside the client also fold to success:false — they're cached as
    misses so the screen doesn't retry on every mount.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        from src.wikipedia_client import get_artist_summary

        info = get_artist_summary(name)
        if info is None:
            return jsonify({"success": False, "message": "no summary"})
        return jsonify({"success": True, "info": info})
    except Exception as e:
        logger.exception("api_library_artist_info failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/albums/<path:album_id>/cover")
def api_library_album_cover(album_id: str):
    """Return album art from this device, falling back to the master.

    Satellite catalog rows deliberately do not copy a master's filesystem
    paths, so they cannot extract embedded artwork locally.  Their desktop UI
    still calls this local endpoint; proxying the miss keeps that contract the
    same for local and remote albums.
    """
    if not config:
        return ("", 503)
    try:
        from src.cover_art import extract_cover

        with DatabaseManager(config.db_path) as db:
            resolution = resolve_album_cover(
                db,
                album_id,
                config_values=getattr(config, "_config", None),
                extract_cover=extract_cover,
            )
        if isinstance(resolution, LocalAlbumCoverResolution):
            return Response(
                resolution.body,
                mimetype=resolution.content_type,
                headers={"Cache-Control": resolution.cache_control},
            )
        if isinstance(resolution, MasterAlbumCoverResolution):
            return _proxy_master_album_cover(resolution.master_url, album_id)
        return ("", resolution.status_code)
    except Exception:
        logger.exception("api_library_album_cover failed for %s", album_id)
        return ("", 500)


def _proxy_master_album_cover(master_url: str, album_id: str):
    """Stream a master's album-art response through the local satellite."""
    import requests
    from flask import Response, stream_with_context
    token = (config._config.get("api_token") or "").strip()
    try:
        result = request_album_cover(
            master_url,
            album_id,
            incoming_headers=request.headers,
            api_token=token,
        )
    except requests.RequestException:
        logger.exception("master album-cover proxy failed for %s", album_id)
        return ("", 502)

    if result.status_code >= 400:
        return ("", result.status_code)
    if result.status_code == 304:
        return Response(status=304, headers=result.headers)

    return Response(
        stream_with_context(result.chunks),
        status=result.status_code,
        headers=result.headers,
    )


@app.route("/api/library/albums/<path:album_id>/tracks")
def api_library_album_tracks(album_id: str):
    """Ordered playable tracks for an album.

    A satellite's replica intentionally contains catalog-only rows as well as
    tracks the master can actually stream.  Ask the authoritative master first
    so album playback does not queue stale/unavailable recording rows; retain
    the local query as an offline fallback.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        resolution = resolve_album_tracks(
            album_id,
            config_values=getattr(config, "_config", None),
            load_local_tracks=_load_local_album_tracks,
            request_tracks=request_album_tracks,
            event_logger=logger,
        )
        if isinstance(resolution, MasterAlbumTracksResolution):
            result = resolution.response
            return Response(
                result.body,
                status=result.status_code,
                content_type=result.content_type,
            )
        return jsonify(resolution.payload)
    except Exception as e:
        logger.exception("api_library_album_tracks failed for %s", album_id)
        return jsonify({"success": False, "message": str(e)}), 500


def _load_local_album_tracks(album_id: str, *, has_master: bool) -> dict:
    """Lazy database adapter used only when master rows are unavailable."""
    with DatabaseManager(config.db_path) as db:
        return list_local_album_tracks(
            db,
            album_id,
            has_master=has_master,
        )


@app.route("/api/library/plays", methods=["POST"])
def api_library_record_play():
    """Append one play event. Body: ``{"mbid": "...", "source": "desktop"?}``.

    The client decides what counts as a "play" (e.g., 30s elapsed or 50%
    of duration); this endpoint just records. Unknown / soft-deleted /
    purged mbids are still accepted — the stats path tolerates dangling
    events so historical counts survive a track being purged.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    prepared = listening_service.prepare_play_event(data)
    if isinstance(prepared, listening_service.ListeningServiceResult):
        return jsonify(prepared.payload), prepared.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            event_id = listening_service.record_play(db, prepared)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.exception("api_library_record_play failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "event_id": event_id}), 201


@app.route("/api/library/play-stats", methods=["GET"])
def api_library_play_stats():
    """Aggregated listening stats for the desktop's stats screen.

    Query params (all optional):
      since:      ISO-8601 cutoff (inclusive). Omit for all-time.
      limit:      cap on top_tracks / top_artists / recent items.
                  Defaults to 20; clamped to [1, 200].

    Returns ``{success, total, top_tracks, top_artists, recent}``.
    A single endpoint rather than four because the stats screen
    always wants all four for the same window — coalescing avoids
    four serial roundtrips on screen mount.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since = (request.args.get("since") or "").strip() or None
    try:
        limit = listening_service.normalize_stats_limit(
            request.args.get("limit", "20")
        )
    except ValueError:
        return jsonify({"success": False, "message": "limit must be an integer"}), 400
    try:
        with DatabaseManager(config.db_path) as db:
            payload = listening_service.build_play_stats(
                db,
                since=since,
                limit=limit,
            )
    except Exception as e:
        logger.exception("api_library_play_stats failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(payload)


# Compatibility names retained for tests/importers that patch the former route
# helpers. The cache policy itself now lives outside the Flask adapter.
_LYRICS_TTL_SEC = lyrics_service.LYRICS_TTL_SECONDS


def _is_lyrics_fresh(fetched_at: str) -> bool:
    return lyrics_service.is_lyrics_fresh(
        fetched_at,
        ttl_seconds=_LYRICS_TTL_SEC,
    )


def _fetch_lyrics_from_lrclib(**kwargs):
    """Late import keeps the provider optional until a cache miss needs it."""
    from src.lrclib_client import fetch_lyrics

    return fetch_lyrics(**kwargs)


@app.route("/api/library/tracks/<mbid>/lyrics", methods=["GET", "POST"])
def api_library_track_lyrics(mbid: str):
    """Get or set the cached lyrics for a track.

    GET tries the cache first. A fresh hit (including a cached miss)
    returns immediately. A stale-or-missing entry triggers a synchronous
    LRCLIB fetch against the track's name+artist, persists the result
    (or the miss), and returns. Manual-source rows are never auto-
    refreshed — the user's typed override beats LRCLIB.

    POST upserts a manual override. Body: ``{"lrc": "...", "synced": bool}``.
    A missing or empty lrc clears the override and falls back to the
    next GET's LRCLIB fetch.

    Returns ``{ok: bool, lrc, synced, source, fetched_at}``. ``lrc`` is
    null when there are no lyrics for this track — the desktop renders
    the empty-state copy on null rather than throwing.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    mbid = (mbid or "").strip()
    if not mbid:
        return jsonify({"success": False, "message": "mbid is required"}), 400

    if request.method == "POST":
        data = request.json or {}
        lrc = data.get("lrc")
        synced = bool(data.get("synced"))
        if lrc is not None and not isinstance(lrc, str):
            return jsonify({
                "success": False,
                "message": "lrc must be a string or null",
            }), 400
        try:
            with DatabaseManager(config.db_path) as db:
                result = lyrics_service.save_manual_lyrics(
                    db,
                    mbid=mbid,
                    lrc=lrc,
                    synced=synced,
                )
        except Exception as e:
            logger.exception("api_library_track_lyrics POST failed")
            return jsonify({"success": False, "message": str(e)}), 500
        return jsonify(result.payload), result.status_code

    try:
        with DatabaseManager(config.db_path) as db:
            result = lyrics_service.load_track_lyrics(
                db,
                mbid=mbid,
                fetch_lyrics=_fetch_lyrics_from_lrclib,
                freshness_check=_is_lyrics_fresh,
            )
    except Exception as e:
        logger.exception("api_library_track_lyrics GET failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/library/daily-mixes/regenerate", methods=["POST"])
def api_library_daily_mixes_regenerate():
    """Regenerate Daily Mixes from the current top-artist + tag state.

    Synchronous because the work is pure-SQL — no MB calls, no
    network. Caller waits ~100ms for the full clustering pass.
    Master-only: same reasoning as tag backfill — satellites get the
    finished playlist rows via catalog sync.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.is_master:
        return jsonify({
            "success": False,
            "message": (
                "Daily Mixes are generated on the master — they'll "
                "appear on this satellite after the next catalog sync."
            ),
        }), 400
    from src.daily_mixes import regenerate_daily_mixes
    try:
        with DatabaseManager(config.db_path) as db:
            summary = regenerate_daily_mixes(db)
        return jsonify({"success": True, **summary})
    except Exception as e:
        logger.exception("api_library_daily_mixes_regenerate failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/artists/<path:name>/radio", methods=["GET"])
def api_library_artist_radio(name: str):
    """Spotify-style Artist Radio: a shuffled queue seeded on the
    artist with ~30% seed tracks and ~70% from artists sharing the
    seed's top tag.

    Query params:
      limit: target queue length (default 50, capped at 200 in the DB
             layer). Returned counts may be smaller when the library
             doesn't have enough matching tracks.

    On a fresh install (no Stage 14a tag backfill yet) the related
    pool is empty and the queue is just the seed artist shuffled —
    still useful, just not very wide. The UI's "Why am I hearing
    this?" tooltip reads from ``top_tag`` to explain the choice.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    name = (name or "").strip()
    if not name:
        return jsonify({"success": False, "message": "name is required"}), 400
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({
            "success": False, "message": "limit must be an integer",
        }), 400

    try:
        with DatabaseManager(config.db_path) as db:
            payload = build_artist_radio_payload(
                db,
                name,
                limit=limit,
                has_master=_master_url_configured(),
            )
        return jsonify(payload)
    except Exception as e:
        logger.exception("api_library_artist_radio failed for %r", name)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/tags/backfill", methods=["POST"])
def api_library_tags_backfill():
    """Kick the MusicBrainz tag backfill in the background.

    Body (all optional): ``{"incremental": bool}`` — when True (default)
    skip artists with a fresh artist_tags row newer than 30 days. The
    job runs in the existing TaskManager so concurrent requests collide
    with the same "already running" message every other long-running
    task uses.

    Authority-only — master and standalone installs fetch MusicBrainz once;
    satellites receive the resulting snapshots through artist-tag delta sync
    so they do not independently hammer the upstream rate limit.
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.is_master:
        return jsonify({
            "success": False,
            "message": (
                "Tag backfill runs on the master only — your satellite "
                "will pick up tags from the next catalog sync."
            ),
        }), 400
    data = request.json or {}
    incremental = bool(data.get("incremental", True))
    success, msg = task_manager.start_task(
        run_tag_backfill,
        (config.db_path, incremental),
        "Genre tag backfill",
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/library/wrapped", methods=["GET"])
def api_library_wrapped():
    """Year-in-review summary for the Wrapped screen.

    Query params:
      year: integer (defaults to the current UTC year).

    Returns a single roll-up payload covering total plays + listening
    time, top track/artist/album, busiest day, top hour of day, first
    play, and longest consecutive-day streak.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    from datetime import datetime, timezone

    year_raw = request.args.get("year")
    if year_raw is None or year_raw == "":
        year = datetime.now(timezone.utc).year
    else:
        try:
            year = int(year_raw)
        except ValueError:
            return jsonify({
                "success": False,
                "message": "year must be an integer",
            }), 400

    try:
        with DatabaseManager(config.db_path) as db:
            summary = db.wrapped_summary(year)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.exception("api_library_wrapped failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, **summary})


def _load_daily_mixes(db):
    """Late-bound compatibility seam for Home's Daily Mix read model."""
    from src.daily_mixes import list_daily_mixes

    return list_daily_mixes(db)


@app.route("/api/library/home", methods=["GET"])
def api_library_home():
    """Roll-up payload for the desktop Home screen.

    Bundles the four cards (recent plays, top artists in the last 30
    days, liked-songs preview, jump-back-in albums) into a single round
    trip so the screen renders without four serial fetches on mount.

    The 30-day window for top artists isn't configurable here — the
    full Listening screen owns the time-range UI. Home is intentionally
    a fixed, opinionated view.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since_30d = library_application_service.home_window_start()

    try:
        with DatabaseManager(config.db_path) as db:
            payload = library_application_service.build_home_payload(
                db,
                _load_daily_mixes,
                since_30d=since_30d,
            )
    except Exception as e:
        logger.exception("api_library_home failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(payload)


@app.route("/api/stream/<path:mbid>")
def api_stream_track(mbid: str):
    """Stream a track's audio bytes, resolving source in priority order.

    1. Local file (``tracks.local_path``) — direct disk read, fastest.
    2. DAP drive (``tracks.dap_path``) — direct disk read from the
       mounted external drive.
    3. Proxy the master server's ``/api/stream/<mbid>`` — so a satellite
       with only a catalog entry can still play the track.

    send_file(conditional=True) handles Range/206 for the local/drive
    cases; the proxy helper forwards the Range header to the master and
    streams the response body through.
    """
    if not config:
        return ("", 503)
    try:
        from flask import send_file

        with DatabaseManager(config.db_path) as db:
            resolution = resolve_stream_source(
                db,
                mbid,
                config_values=getattr(config, "_config", None),
                file_exists=os.path.isfile,
            )
        if isinstance(resolution, FileStreamResolution):
            return send_file(
                resolution.path,
                mimetype=resolution.content_type,
                conditional=True,
            )
        if isinstance(resolution, MasterStreamResolution):
            return _proxy_master_stream(resolution.master_url, mbid)
        return ("", resolution.status_code)
    except Exception:
        logger.exception("api_stream_track failed for %s", mbid)
        return ("", 500)


def _proxy_master_stream(master_url: str, mbid: str):
    """Forward a stream request to the master server with Range preserved.

    Audio tags issue Range requests mid-playback for seeks; the master's
    own ``/api/stream`` endpoint already supports 206 via send_file, so
    we just relay the upstream status, range headers, and body bytes.
    """
    import requests
    from flask import Response, stream_with_context

    token = (config._config.get("api_token") or "").strip()
    try:
        result = request_stream(
            master_url,
            mbid,
            incoming_headers=request.headers,
            api_token=token,
        )
    except requests.RequestException:
        logger.exception("master stream proxy failed for %s", mbid)
        return ("", 502)

    if result.status_code >= 400:
        return ("", result.status_code)

    return Response(
        stream_with_context(result.chunks),
        status=result.status_code,
        headers=result.headers,
    )


def _guess_audio_mime(path: str) -> str:
    return guess_audio_mime(path)


@app.route("/api/healthz")
def healthz():
    """Liveness probe.

    Always 200 once Flask is serving; the body carries readiness so a
    caller can distinguish "alive but unconfigured" (fresh install,
    wizard not yet run) from "alive and ready". The Tauri desktop
    bootstrap waits on alive=200 then renders the appropriate screen
    based on `ok`. Unauthenticated, side-effect-free, exempt from the
    API-token gate and the pre-config setup-redirect gate.
    """
    return jsonify({
        "ok": config is not None,
        "initialized": task_manager is not None,
    }), 200


@app.route("/api/status")
def status():
    # Sync/audit work runs on a satellite itself, so the unscoped status must
    # remain local. Download queue controls, however, are master-owned. Give
    # download UIs an explicit scope that follows the same authority as the
    # queue/list/mutation endpoints below.
    if (
        config
        and not config.is_master
        and request.args.get("scope", "").strip().lower() == "downloads"
    ):
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "GET",
            "/api/status",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    if not task_manager:
        return jsonify({"running": False, "message": "Not initialized"})
    with task_manager.lock:
        return jsonify(
            {
                "running": task_manager.is_running,
                "task": task_manager.current_task,
                "message": task_manager.message,
                "detail": task_manager.progress_detail,
            }
        )


@app.route("/api/scan", methods=["POST"])
def scan():
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    success, msg = task_manager.start_task(
        run_scan, (config.db_path, config), "Library Scan"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/download", methods=["POST"])
def download():
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            "/api/download",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    success, msg = task_manager.start_task(
        run_download, (config.db_path, config), "Download Queue"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/download/albums/search", methods=["GET"])
def search_download_albums():
    """Search canonical MusicBrainz album releases on the master."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    query = request.args.get("q", "")
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "GET",
            "/api/download/albums/search",
            api_token=config.get("api_token") or "",
            params={"q": query},
        )
        return jsonify(result.payload), result.status_code

    from src import musicbrainz_client

    try:
        result = album_download_request_service.search_album_releases(
            query,
            search_releases=musicbrainz_client.search_releases,
        )
    except Exception as exc:
        logger.warning("MusicBrainz album search failed: %s", exc)
        return jsonify({
            "success": False,
            "message": "MusicBrainz album search is temporarily unavailable",
        }), 502
    return jsonify(result.payload), result.status_code


@app.route("/api/download/albums/request", methods=["POST"])
def request_download_album():
    """Resolve one exact release again, then queue its canonical album query."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "body must be an object"}), 400
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            "/api/download/albums/request",
            api_token=config.get("api_token") or "",
            json_body=data,
        )
        return jsonify(result.payload), result.status_code

    if config.get(
        "auto_tag_downloads",
        CONFIG_DEFAULT_VALUES["auto_tag_downloads"],
    ) is False:
        return jsonify({
            "success": False,
            "message": (
                "Verified album downloads require auto_tag_downloads to be "
                "enabled on the master."
            ),
        }), 409
    acoustid_api_key = config.get("acoustid_api_key", "")
    if not isinstance(acoustid_api_key, str) or not acoustid_api_key.strip():
        return jsonify({
            "success": False,
            "message": (
                "Verified album downloads require acoustid_api_key to be "
                "configured on the master."
            ),
        }), 409

    from src import musicbrainz_client

    try:
        resolved = album_download_request_service.resolve_album_release(
            data.get("release_mbid"),
            get_release_by_id=musicbrainz_client.get_release_by_id,
        )
    except Exception as exc:
        logger.warning("MusicBrainz album resolution failed: %s", exc)
        return jsonify({
            "success": False,
            "message": "MusicBrainz could not verify that release",
        }), 502
    if isinstance(
        resolved,
        album_download_request_service.AlbumRequestResult,
    ):
        return jsonify(resolved.payload), resolved.status_code

    try:
        with DatabaseManager(config.db_path) as db:
            result = album_download_request_service.queue_album_request(
                db,
                resolved,
                item_factory=DownloadItem,
                music_library_dir=config.music_library,
            )
    except Exception as exc:
        logger.error("Could not queue verified album request: %s", exc, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Could not persist the verified album request",
        }), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/download/albums/requests", methods=["GET"])
def list_download_album_requests():
    """Reconcile active server-persistent requests on any satellite browser."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "GET",
            "/api/download/albums/requests",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            result = album_download_request_service.list_album_requests(
                db,
                music_library_dir=config.music_library,
            )
    except Exception as exc:
        logger.error("Could not list album requests: %s", exc, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Could not list album requests",
        }), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/download/albums/requests/<int:request_id>", methods=["GET"])
def get_download_album_request(request_id):
    """Return persistent per-request progress, proxying from a satellite."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "GET",
            f"/api/download/albums/requests/{request_id}",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            result = album_download_request_service.album_request_status(
                db,
                request_id,
                music_library_dir=config.music_library,
            )
    except Exception as exc:
        logger.error("Could not read album request status: %s", exc, exc_info=True)
        return jsonify({
            "success": False,
            "message": "Could not read album request status",
        }), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/download/request", methods=["POST"])
def request_download():
    """Satellite → master: enqueue a download on the master's behalf.

    The master runs Lidarr + sldl and owns the canonical library, so
    satellites forward download requests here instead of queuing them
    locally. On success the item lands in the master's download_queue
    and the imported file flows back to satellites via catalog sync.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "body must be an object"}), 400
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            "/api/download/request",
            api_token=config.get("api_token") or "",
            json_body=data,
        )
        return jsonify(result.payload), result.status_code
    authority_error = download_discovery_service.validate_download_authority(
        config.is_master
    )
    if authority_error is not None:
        return jsonify(authority_error.payload), authority_error.status_code

    prepared = download_discovery_service.prepare_download_request(data)
    if isinstance(
        prepared,
        download_discovery_service.DownloadDiscoveryResult,
    ):
        return jsonify(prepared.payload), prepared.status_code

    try:
        with DatabaseManager(config.db_path) as db:
            result = download_discovery_service.queue_download_request(
                db,
                prepared,
                item_factory=DownloadItem,
            )
    except Exception as e:
        logger.error(f"Download request failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/sync", methods=["POST"])
def sync():
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    data = request.json or {}
    mode = data.get("mode", "playlists")
    fmt = data.get("format", "flac")
    success, msg = task_manager.start_task(
        run_sync, (config.db_path, config, mode, fmt), f"Sync ({mode})"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/jellyfin/pull", methods=["POST"])
def jellyfin_pull():
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.jellyfin_enabled:
        return jsonify({
            "success": False,
            "message": "Jellyfin not configured. Set jellyfin_url, jellyfin_api_key, and jellyfin_user_id in config.",
        })
    success, msg = task_manager.start_task(
        run_jellyfin_pull, (config.db_path, config), "Jellyfin Pull"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/catalog/pull", methods=["POST"])
def catalog_pull():
    """Trigger a delta pull of the master's catalog into this replica."""
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.master_url:
        return jsonify({
            "success": False,
            "message": "master_url not configured. Set master_url in config.json to the master DAPManager's base URL.",
        })
    success, msg = task_manager.start_task(
        run_catalog_pull, (config.db_path, config), "Catalog Pull"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/catalog/link-local", methods=["POST"])
def catalog_link_local():
    """Walk the local music library and bind unlinked catalog rows to
    on-disk files by MBID / ISRC / (artist, title[, album]).

    Intended for satellites that pulled a catalog with rows they
    already have on disk from a pre-DAPManager library. Runs as a
    background task; progress and the final scanned/linked/ambiguous
    summary flow through the /api/status channel.
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config or not getattr(config, "music_library", ""):
        return jsonify({
            "success": False,
            "message": "music_library_path not configured.",
        })
    success, msg = task_manager.start_task(
        run_catalog_link_local, (config.db_path, config), "Link Local Files"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/inventory/report", methods=["POST"])
def inventory_report():
    """Publish this device's inventory snapshot.

    Satellites POST to the master; a master (or any device acting as one)
    writes the snapshot into its own device_inventory. Gated by the
    ``report_inventory_to_host`` config flag so quiet-by-default
    satellites stay quiet.
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.report_inventory_to_host:
        return jsonify({
            "success": False,
            "message": "report_inventory_to_host is disabled in config; set it to true to opt in.",
        })
    success, msg = task_manager.start_task(
        run_inventory_report, (config.db_path, config), "Inventory Report"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/contribute", methods=["POST"])
def contribute():
    """Offer this device's local tracks to the master (identifier-first,
    upload fallback). Runs as a background task; progress flows through the
    /api/status channel."""
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.master_url:
        return jsonify({
            "success": False,
            "message": "master_url not configured. Set master_url in config.json to the master DAPManager's base URL.",
        })
    success, msg = task_manager.start_task(
        run_contribute, (config.db_path, config), "Contribute"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/contribute/track", methods=["POST"])
def contribute_track():
    """Offer a single local track to the master and report the resulting
    status. Synchronous so the library row gets immediate feedback — the offer
    is a quick POST; a large upload only happens on a later poll when the
    master's own download has failed.

    Body: ``{mbid}``. Response: ``{success, status?, message?}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    if not config.master_url:
        return jsonify({
            "success": False,
            "message": "master_url not configured. Set master_url in config.json to the master DAPManager's base URL.",
        }), 409
    mbid = ((request.json or {}).get("mbid") or "").strip()
    if not mbid:
        return jsonify({"success": False, "message": "mbid is required"}), 400

    from src.contribution_sync import main_run_contribute_one
    try:
        with DatabaseManager(config.db_path) as db:
            result = main_run_contribute_one(db, config._config, mbid)
    except Exception as e:
        logger.error(f"contribute_track failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result)


@app.route("/api/playlists/push", methods=["POST"])
def playlists_push():
    """Push locally-edited playlists to the master."""
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.master_url:
        return jsonify({
            "success": False,
            "message": "master_url not configured. Set master_url in config.json to the master DAPManager's base URL.",
        })
    success, msg = task_manager.start_task(
        run_playlist_push, (config.db_path, config), "Playlist Push"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/playlists/pull", methods=["POST"])
def playlists_pull():
    """Trigger a delta pull of the master's playlists into this replica.

    Membership references to track MBIDs the satellite hasn't seen yet
    are dropped, so a catalog pull should run first for best results.
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    if not config.master_url:
        return jsonify({
            "success": False,
            "message": "master_url not configured. Set master_url in config.json to the master DAPManager's base URL.",
        })
    success, msg = task_manager.start_task(
        run_playlist_pull, (config.db_path, config), "Playlist Pull"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/sync/all", methods=["POST"])
def sync_all():
    """Run pull catalog → pull playlists → push playlists → report inventory.

    Steps that aren't applicable (master_url missing, inventory disabled)
    are skipped rather than failing the run. Individual step errors are
    captured per-step and don't stop the rest from running.
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    success, msg = task_manager.start_task(
        run_sync_all, (config.db_path, config), "Sync All"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/sync/state", methods=["GET"])
def sync_state():
    """Return sync-cursor timestamps for the status widget."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    keys = {
        "last_catalog_sync": "catalog_pull",
        "last_artist_tags_sync": "artist_tags_pull",
        "last_playlist_sync": "playlist_pull",
        "last_playlist_push": "playlist_push",
        "last_lyrics_sync": "lyrics_pull",
        "last_inventory_report": "inventory_report",
        "last_contribute": "contribute",
    }
    try:
        with DatabaseManager(config.db_path) as db:
            state = {label: db.get_sync_state(k) for k, label in keys.items()}
    except Exception as e:
        logger.error(f"sync_state fetch failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "state": state})


@app.route("/api/playlists/queue", methods=["POST"])
def queue_playlists():
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    data = request.json or {}
    urls = data.get("urls", [])
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.splitlines() if u.strip()]

    success, msg = task_manager.start_task(
        run_queue_playlists,
        (config.db_path, config, urls),
        f"Queueing {len(urls)} Playlists",
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/catalog", methods=["GET"])
def get_catalog():
    """Return catalog rows for replica sync.

    Query params:
      since: ISO-ish timestamp (matching SQLite CURRENT_TIMESTAMP format,
             e.g. '2026-04-17 12:00:00'). Optional. If present, only rows
             with updated_at > since are returned.

    Response:
      { success, as_of, count, tracks: [...] }
      `as_of` is the server's CURRENT_TIMESTAMP at query time — callers
      should use it as the next ?since to avoid missing concurrent writes.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since = request.args.get("since") or None
    try:
        with DatabaseManager(config.db_path) as db:
            as_of = db.get_current_timestamp()
            rows = db.get_catalog_since(since)
    except Exception as e:
        logger.error(f"Catalog query failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "as_of": as_of,
        "count": len(rows),
        "tracks": rows,
    })


@app.route("/api/lyrics", methods=["GET"])
def get_lyrics_delta():
    """Return cached + manual lyrics rows for satellite sync.

    Same shape as /api/catalog and /api/playlists: optional ``since``
    cursor, response carries ``as_of`` for the next call. The cursor
    is the lyrics table's ``fetched_at`` column (bumped on every
    upsert), so manual overrides ride the same delta as cached
    LRCLIB results.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since = request.args.get("since") or None
    try:
        with DatabaseManager(config.db_path) as db:
            as_of = db.get_current_timestamp()
            rows = db.get_lyrics_since(since)
    except Exception as e:
        logger.error(f"Lyrics delta query failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({
        "success": True,
        "as_of": as_of,
        "count": len(rows),
        "lyrics": rows,
    })


@app.route("/api/artist-tags", methods=["GET"])
def get_artist_tags_delta():
    """Return authoritative per-artist tag snapshots for satellite sync.

    ``since`` filters on the latest MusicBrainz ``fetched_at`` for each
    artist.  Rows are grouped as complete snapshots so a satellite can
    replace an artist's tag set and remove tags that disappeared upstream.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since = request.args.get("since") or None
    try:
        with DatabaseManager(config.db_path) as db:
            as_of = db.get_current_timestamp()
            rows = db.get_artist_tags_since(since)
    except Exception as e:
        logger.error(f"Artist-tag delta query failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({
        "success": True,
        "as_of": as_of,
        "count": len(rows),
        "artist_tags": rows,
    })


@app.route("/api/playlists", methods=["GET"])
def get_playlists_delta():
    """Return playlists (with full track membership) for replica sync.

    Query params:
      since: optional ISO-ish timestamp. Only playlists with
             updated_at > since are returned.

    Response shape mirrors /api/catalog:
      { success, as_of, count, playlists: [...] }
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    since = request.args.get("since") or None
    try:
        with DatabaseManager(config.db_path) as db:
            as_of = db.get_current_timestamp()
            rows = db.get_playlists_since(since)
    except Exception as e:
        logger.error(f"Playlist delta query failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "as_of": as_of,
        "count": len(rows),
        "playlists": rows,
    })


@app.route("/api/playlists", methods=["POST"])
def post_playlists():
    """Accept playlists pushed from a satellite (write-anywhere semantics).

    Body: { "playlists": [ {playlist_id, name, spotify_url, updated_at,
                            tracks: [{track_mbid, track_order}, ...]}, ... ] }

    Merge strategy is last-writer-wins by updated_at. Rows with updated_at
    no newer than the local copy are rejected as 'stale' and the caller
    gets a per-row verdict so it can confirm what landed.

    Response: { success, received, accepted, stale, skipped, results }
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    items = data.get("playlists") or []
    if not isinstance(items, list):
        return jsonify({"success": False, "message": "playlists must be a list"}), 400

    try:
        with DatabaseManager(config.db_path) as db:
            result = playlist_service.apply_pushed_playlists(db, items)
    except Exception as e:
        logger.error(f"Playlist push apply failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify(result.payload), result.status_code


@app.route("/api/inventory", methods=["POST"])
def post_inventory():
    """Accept a device's inventory snapshot (MBID → local_path map).

    Body: { "device_id": "...", "items": [{"mbid": "...", "local_path": "..."}, ...] }
    The full snapshot is authoritative — the device's previous inventory is
    replaced in a single transaction.
    Response: { success, device_id, written }
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    device_id = (data.get("device_id") or "").strip()
    if not device_id:
        return jsonify({"success": False, "message": "device_id required"}), 400
    items = data.get("items") or []
    if not isinstance(items, list):
        return jsonify({"success": False, "message": "items must be a list"}), 400

    try:
        with DatabaseManager(config.db_path) as db:
            written = db.replace_device_inventory(device_id, items)
    except Exception as e:
        logger.error(f"Inventory write failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "device_id": device_id,
        "received": len(items),
        "written": written,
    })


@app.route("/api/tracks/needs-review", methods=["GET"])
def tracks_needs_review():
    """Tracks whose last auto-tag was yellow or red — the user's review queue.

    Response: { success, count, tracks: [{mbid, artist, album, title,
                                          path, tag_tier, tag_score}] }
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            tracks = db.get_tracks_needing_tag_review()
    except Exception as e:
        logger.error(f"needs-review query failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    data = [
        {
            "mbid": t.mbid,
            "artist": t.artist,
            "album": t.album,
            "title": t.title,
            "path": t.local_path,
            "tag_tier": t.tag_tier,
            "tag_score": t.tag_score,
        }
        for t in tracks
    ]
    return jsonify({"success": True, "count": len(data), "tracks": data})


@app.route("/api/tag/identify/<mbid>", methods=["POST"])
def tag_identify(mbid):
    """Picard-style identify: fingerprint the local file, return a candidate.

    Does not write anything. Response includes a colour tier
    (green/yellow/red) so the UI can mirror Picard's confidence cues.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    api_key = (config._config.get("acoustid_api_key") or "").strip()
    if not api_key:
        return jsonify({
            "success": False,
            "message": "acoustid_api_key not set in config.",
        }), 400

    from src import tag_service
    result = tag_application_service.identify_track(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        mbid=mbid,
        api_key=api_key,
        contact_provider=lambda: (
            config._config.get("contact_email") or ""
        ).strip(),
        identify_file=tag_service.identify_file,
        read_current_tags=tag_service.read_current_tags,
        event_logger=logger,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/tag/apply/<mbid>", methods=["POST"])
def tag_apply(mbid):
    """Write tags to the local file and update the tracks row.

    Request JSON: the ``meta`` dict from an earlier /identify call
    (caller may have edited fields). On success the tracks row's
    artist/title/album and mbid are updated so the catalog reflects
    the corrected identity.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503

    body = request.json or {}
    prepared = tag_application_service.prepare_tag_apply(body)
    if isinstance(prepared, tag_application_service.TagApplicationResult):
        return jsonify(prepared.payload), prepared.status_code

    from src import tag_service
    from src.db_manager import Track
    result = tag_application_service.apply_track_tags(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        mbid=mbid,
        prepared=prepared,
        write_tags=tag_service.write_tags,
        track_factory=Track,
        event_logger=logger,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/tracks/<mbid>", methods=["DELETE"])
def soft_delete_track(mbid):
    """Soft-delete a track by default; hard-delete with ``?purge=true``.

    Soft-delete stamps deleted_at + bumps updated_at so the next
    catalog delta carries the signal to satellites. Purge is only
    allowed on rows already marked deleted — a second step that
    the /orphans UI invokes after a human has reviewed the row.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    purge = request.args.get("purge", "").lower() in ("1", "true", "yes")
    try:
        with DatabaseManager(config.db_path) as db:
            if purge:
                changed = db.purge_track(mbid)
                return jsonify({
                    "success": True, "purged": changed, "mbid": mbid,
                })
            changed = db.soft_delete_track(mbid)
    except Exception as e:
        logger.error(f"soft_delete_track({mbid}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "deleted": changed, "mbid": mbid})


@app.route("/api/playlists/<playlist_id>", methods=["DELETE"])
def soft_delete_playlist_route(playlist_id):
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    purge = request.args.get("purge", "").lower() in ("1", "true", "yes")
    prepared = playlist_service.prepare_playlist_delete(
        playlist_id,
        purge=purge,
        liked_songs_playlist_id=DatabaseManager.LIKED_SONGS_PLAYLIST_ID,
    )
    if isinstance(prepared, playlist_service.PlaylistServiceResult):
        return jsonify(prepared.payload), prepared.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            result = playlist_service.delete_playlist(db, prepared)
    except Exception as e:
        logger.error(f"soft_delete_playlist({playlist_id}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/tracks/<mbid>/restore", methods=["POST"])
def restore_track_route(mbid):
    """Clear deleted_at so the row is live again. Bumps updated_at so
    the restoration propagates through the next catalog delta."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            changed = db.restore_track(mbid)
    except Exception as e:
        logger.error(f"restore_track({mbid}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "restored": changed, "mbid": mbid})


@app.route("/api/playlists/<playlist_id>/restore", methods=["POST"])
def restore_playlist_route(playlist_id):
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            changed = db.restore_playlist(playlist_id)
    except Exception as e:
        logger.error(f"restore_playlist({playlist_id}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({
        "success": True, "restored": changed, "playlist_id": playlist_id,
    })


@app.route("/api/tracks/<mbid>/file", methods=["DELETE"])
def delete_track_file(mbid):
    """Delete the on-disk file a soft-deleted track points at, and clear
    ``local_path``. Does NOT purge the row — that's a separate step.

    Refuses to act on live (non-orphan) tracks to prevent surprise
    filesystem mutations from the UI. Missing files are treated as
    success (idempotent) so a re-run after a manual delete still clears
    the column cleanly.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    result = library_application_service.delete_orphan_track_file(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        mbid=mbid,
        remove_file=os.remove,
        event_logger=logger,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/orphans/tracks")
def api_orphan_tracks():
    """Soft-deleted tracks in deletion-time order (newest first)."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            rows = db.get_orphan_tracks()
        return jsonify({"success": True, "tracks": rows})
    except Exception as e:
        logger.exception("api_orphan_tracks failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/orphans/playlists")
def api_orphan_playlists():
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            rows = db.get_orphan_playlists()
        return jsonify({"success": True, "playlists": rows})
    except Exception as e:
        logger.exception("api_orphan_playlists failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/fleet")
def fleet_page():
    """Master-side overview of which devices have what.

    Reads from the device_inventory table populated by /api/inventory.
    """
    return render_template("fleet.html")


@app.route("/contributions")
def contributions_page():
    """Master-side view of tracks satellites have offered, and their status."""
    return render_template("contributions.html")


@app.route("/docs")
def api_docs():
    """Offline interactive API explorer. Reachable before setup so an agent
    can learn the setup flow from a fresh install."""
    return render_template("docs.html")


@app.route("/api/openapi.json")
def openapi_spec():
    """The OpenAPI document backing /docs. Exempt from auth + setup gates."""
    from src.openapi_spec import build_spec
    return jsonify(build_spec())


@app.route("/orphans")
def orphans_page():
    """Soft-deleted tracks and playlists, for review / restore / purge."""
    return render_template("orphans.html")


@app.route("/library")
def library_page():
    """Web track browser: sidebar of playlists + filterable track table."""
    return render_template("library.html")


@app.route("/api/fleet/summary", methods=["GET"])
def fleet_summary():
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            summary = db.get_fleet_summary()
    except Exception as e:
        logger.error(f"Fleet summary failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "devices": summary})


@app.route("/api/fleet/track", methods=["GET"])
def fleet_track_lookup():
    """Which devices hold a given MBID, or search by artist/title/album."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    mbid = (request.args.get("mbid") or "").strip()
    query = (request.args.get("q") or "").strip()
    try:
        with DatabaseManager(config.db_path) as db:
            result = fleet_service.lookup_fleet_track(
                db,
                mbid=mbid,
                query=query,
            )
    except Exception as e:
        logger.error(f"Fleet track lookup failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/suggestions", methods=["POST"])
def post_suggestions():
    """Accept track suggestions from a satellite device and queue them for download.

    Body: { "items": [ {mbid?, artist?, title?, search_query?}, ... ] }
    Response: { success, queued, skipped, received }
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    raw_items = data.get("items", [])
    pairs = build_suggestion_items(raw_items)

    try:
        with DatabaseManager(config.db_path) as db:
            counts = download_discovery_service.queue_suggestion_pairs(
                db,
                pairs,
                item_factory=DownloadItem,
            )
    except Exception as e:
        logger.error(f"Failed to process suggestions: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    result = download_discovery_service.suggestion_queue_result(
        len(raw_items),
        counts,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/suggestions/forward", methods=["POST"])
def forward_suggestions():
    """Forward suggestions from this local UI to its configured master.

    The Tauri webview must not call ``master_url`` directly: it is a different
    origin, and adding the bearer header would require CORS preflight support
    on every master deployment.  Keeping the hop in Python also avoids
    exposing the configured host token to arbitrary browser origins.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    target = download_discovery_service.normalize_forward_target(
        config.get("master_url")
    )
    if isinstance(target, download_discovery_service.DownloadDiscoveryResult):
        return jsonify(target.payload), target.status_code

    data = request.json or {}
    raw_items = data.get("items")
    validation_error = download_discovery_service.validate_forward_items(
        raw_items
    )
    if validation_error is not None:
        return jsonify(validation_error.payload), validation_error.status_code

    import requests

    result = download_discovery_service.forward_suggestions(
        target,
        raw_items,
        api_token=config.get("api_token") or "",
        http_post=requests.post,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/catalog/queue-download", methods=["POST"])
def catalog_queue_download():
    """Queue download jobs for catalog-only rows (the "wishlist" flow).

    Body: ``{"mbids": ["...", "..."]}``. For each mbid:
      - 404-like: mbid not in the catalog → counted as ``not_found``.
      - Already has a local file → ``skipped_linked`` (nothing to do).
      - Already queued (same normalized search_query) → ``skipped_queued``.
      - Otherwise: enqueue with playlist_id=``"CATALOG"`` and mbid_guess
        set so the downloader's MusicBrainz verification uses the
        correct identity out of the gate.

    Returns per-bucket counts plus ``queued_mbids`` so a client can show
    which rows actually entered the queue.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    validated = download_discovery_service.validate_catalog_queue_body(data)
    if isinstance(
        validated,
        download_discovery_service.DownloadDiscoveryResult,
    ):
        return jsonify(validated.payload), validated.status_code
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            "/api/catalog/queue-download",
            api_token=config.get("api_token") or "",
            json_body=data,
        )
        return jsonify(result.payload), result.status_code

    try:
        with DatabaseManager(config.db_path) as db:
            result = download_discovery_service.queue_catalog_downloads(
                db,
                validated,
                item_factory=DownloadItem,
            )
    except Exception as e:
        logger.error(f"catalog_queue_download failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


def _attempt_timeout_seconds() -> int:
    """How long a contribution may sit in 'attempting' before we give up on
    the master acquiring it and ask the satellite to upload. Guards against a
    master whose download queue is never processed (the row would otherwise
    stay 'pending' forever and the upload fallback would never fire)."""
    values = getattr(config, "_config", {}) or {}
    return contribution_service.attempt_timeout_seconds(values)


def _contribution_age_seconds(contrib: dict) -> Optional[float]:
    """Seconds since the contribution was created. ``created_at`` is a SQLite
    CURRENT_TIMESTAMP string in UTC."""
    return contribution_service.contribution_age_seconds(contrib)


def _find_acceptable_local_copy(db, identity: dict, target: Optional[dict]):
    """Find a same-or-better local copy using stable recording identity.

    Different taggers can legitimately choose different recording MBIDs for
    the same audio.  Fall back to ISRC and exact metadata matches rather than
    making the satellite upload bytes the master already acquired.  The DB
    method intentionally refuses ambiguous metadata matches.  Metadata-only
    candidates must also have a duration within five seconds when both sides
    report one; this prevents a high-quality live/remix recording with the
    same tags from permanently satisfying the wrong offer.

    Returns ``(candidate_row, quality)`` or ``(None, None)``.
    """
    return contribution_service.find_acceptable_local_copy(db, identity, target)


def _evaluate_contribution(db, contrib: dict) -> dict:
    """Recompute a contribution's live status by comparing what the master
    now holds on disk against the satellite's target quality.

    Returns the (possibly updated) row as a dict. Lazy: this is where the
    download worker's outcome gets reflected, so no worker hooks are needed.
    """
    return contribution_service.evaluate_contribution(
        db,
        contrib,
        timeout_seconds=_attempt_timeout_seconds,
        find_local_copy=_find_acceptable_local_copy,
        age_seconds=_contribution_age_seconds,
    )


@app.route("/api/contributions", methods=["GET"])
def list_contributions():
    """List recent contributions for the dashboard. Quality JSON is parsed
    into objects so the client doesn't have to.

    Response: ``{success, contributions: [...]}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    try:
        with DatabaseManager(config.db_path) as db:
            result = contribution_service.list_master_contributions(
                db,
                limit,
                evaluate=lambda service_db, row, **_kwargs: (
                    _evaluate_contribution(service_db, row)
                ),
            )
    except Exception as e:
        logger.error(f"list_contributions failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/contributed", methods=["GET"])
def list_contributed():
    """List this device's outgoing contribution state for satellite UIs."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        limit = max(1, min(500, int(request.args.get("limit", 200))))
    except (TypeError, ValueError):
        limit = 200
    try:
        with DatabaseManager(config.db_path) as db:
            result = contribution_service.list_outgoing_contributions(db, limit)
    except Exception as e:
        logger.error("list_contributed failed: %s", e, exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/contributions", methods=["POST"])
def post_contribution():
    """A satellite offers a track it has locally. Master first tries to
    acquire it itself; only if it can't match the satellite's quality does it
    later ask for an upload.

    Body: ``{device_id?, mbid, isrc?, artist, title, album?, quality}`` where
    ``quality`` is an ``audio_quality.read_quality`` descriptor.
    Response: ``{success, contribution_id, status}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    artist = (data.get("artist") or "").strip()
    title = (data.get("title") or "").strip()
    if not (artist and title):
        return jsonify({
            "success": False,
            "message": "artist and title are required",
        }), 400

    try:
        with DatabaseManager(config.db_path) as db:
            result = contribution_service.offer_contribution(
                db,
                data,
                find_local_copy=_find_acceptable_local_copy,
                download_item_factory=lambda **values: DownloadItem(**values),
            )
    except Exception as e:
        logger.error(f"post_contribution failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


@app.route("/api/contributions/<int:contribution_id>", methods=["GET"])
def get_contribution_status(contribution_id: int):
    """Poll a contribution. Master recomputes status from what it now holds
    on disk and tells the satellite whether to upload the file.

    Response: ``{success, status, want_upload}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            result = contribution_service.poll_contribution(
                db,
                contribution_id,
                evaluate=lambda service_db, row, **_kwargs: (
                    _evaluate_contribution(service_db, row)
                ),
            )
    except Exception as e:
        logger.error(f"get_contribution_status failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


def _discard_staged_upload(path: Optional[str]) -> None:
    contribution_service.discard_staged_upload(path)


@app.route("/api/contributions/<int:contribution_id>/upload", methods=["POST"])
def upload_contribution(contribution_id: int):
    """Receive the actual file from a satellite and ingest it into the
    master's library. Multipart form field ``file``.

    Response: ``{success, status, local_path}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({
            "success": False, "message": "multipart 'file' field is required",
        }), 400

    try:
        with DatabaseManager(config.db_path) as db:
            result = contribution_service.process_contribution_upload(
                db,
                contribution_id,
                upload,
                downloads_dir=config.downloads_dir,
                music_library=config.music_library,
                picard_path=config.picard_path,
                verify=_verify_upload,
                find_local_copy=_find_acceptable_local_copy,
            )
    except Exception as e:
        logger.error(f"upload_contribution failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify(result.payload), result.status_code


def _verify_upload(path: str, target: Optional[dict]) -> Optional[str]:
    """Return a rejection reason if the staged upload is empty, grossly
    truncated, or worse than the satellite promised; ``None`` when it's good.
    No ``target`` means the satellite reported no quality, so only the
    empty-file check applies."""
    return contribution_service.verify_upload(path, target)


@app.route("/api/audit", methods=["POST"])
def audit():
    # Legacy audit (runs in background thread, logs to file)
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    success, msg = task_manager.start_task(
        run_audit, (config.db_path,), "Audit Library"
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/audit/results", methods=["GET"])
def audit_results():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            incomplete = db.get_incomplete_albums()

        # Add Cover Art URL
        for item in incomplete:
            item["cover_art"] = (
                f"https://coverartarchive.org/release/{item['mbid']}/front-250"
            )

        return jsonify({"success": True, "results": incomplete})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/audit/details", methods=["GET"])
def audit_details():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    mbid = request.args.get("mbid")
    if not mbid:
        return jsonify({"success": False, "message": "MBID required"})

    try:
        from src.album_completer import get_missing_tracks_for_album

        with DatabaseManager(config.db_path) as db:
            result = get_missing_tracks_for_album(db, mbid)

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/audit/queue", methods=["POST"])
def audit_queue():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    data = request.json
    items = data.get("items", [])  # List of {artist, title}

    # Or queue full album
    queue_album = data.get("queue_album", False)
    album_info = data.get("album_info", {})  # {artist, title}

    try:
        with DatabaseManager(config.db_path) as db:
            result = album_task_service.queue_audit_downloads(
                db,
                items=items,
                queue_album=queue_album,
                album_info=album_info,
                item_factory=DownloadItem,
            )
        return jsonify(result.payload)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/albums/complete", methods=["POST"])
def complete_albums():
    """
    Full album completion pipeline: discover albums, find gaps, queue missing tracks.
    Optional: run the downloader afterward.
    POST body: { "run_downloads": bool }
    """
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})

    data = request.json or {}
    run_downloads = data.get("run_downloads", False)

    success, msg = task_manager.start_task(
        run_complete_albums,
        (config.db_path, config, run_downloads),
        "Album Completion",
    )
    return jsonify({"success": success, "message": msg})


@app.route("/api/stats", methods=["GET"])
def get_stats():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    try:
        import shutil

        with DatabaseManager(config.db_path) as db:
            lib_stats = db.get_library_stats()

        # Disk Usage
        total, used, free = shutil.disk_usage(config.music_library)
        lib_stats["disk_total_gb"] = round(total / (1024**3), 2)
        lib_stats["disk_free_gb"] = round(free / (1024**3), 2)
        lib_stats["disk_used_percent"] = round((used / total) * 100, 1)

        return jsonify({"success": True, "stats": lib_stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/downloads/list", methods=["GET"])
def get_downloads_list():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "GET",
            "/api/downloads/list",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            items = db.get_all_downloads()

        # Convert to JSON serializable
        data = []
        for item in items:
            data.append(
                {
                    "id": item.id,
                    "query": item.search_query,
                    "status": item.status,
                    "last_attempt": (
                        item.last_attempt.isoformat() if item.last_attempt else None
                    ),
                    "attempt_count": item.attempt_count,
                    "max_attempts": item.max_attempts,
                    "next_attempt_at": (
                        item.next_attempt_at.isoformat()
                        if item.next_attempt_at
                        else None
                    ),
                    "is_paused": item.is_paused,
                    "is_quarantined": item.is_quarantined,
                    "last_error": item.last_error,
                }
            )

        return jsonify({"success": True, "items": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/downloads/<int:item_id>/retry", methods=["POST"])
def retry_download_item(item_id):
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            f"/api/downloads/{item_id}/retry",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            changed = db.retry_download(item_id)
        if not changed:
            return jsonify({
                "success": False,
                "message": "Row not found or not in 'failed' state",
            }), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/downloads/<int:item_id>", methods=["DELETE"])
def delete_download_item(item_id):
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "DELETE",
            f"/api/downloads/{item_id}",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            db.remove_from_queue(item_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/downloads/clear-completed", methods=["POST"])
def clear_completed_downloads():
    # Path keeps the user-facing "completed" wording the UI shows; the
    # underlying schema state is "success" (see db_manager).
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    if not config.is_master:
        result = album_download_request_service.forward_master_json(
            config.master_url,
            "POST",
            "/api/downloads/clear-completed",
            api_token=config.get("api_token") or "",
        )
        return jsonify(result.payload), result.status_code
    try:
        with DatabaseManager(config.db_path) as db:
            removed = db.delete_succeeded_downloads()
        return jsonify({"success": True, "removed": removed})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/releases/wanted", methods=["GET"])
def releases_wanted():
    """Lidarr's wanted/missing list, augmented with per-album queue and
    library state so the desktop New Releases screen can render the
    right pill on each card."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503

    disabled = download_discovery_service.validate_wanted_releases_enabled(
        config._config
    )
    if disabled is not None:
        return jsonify(disabled.payload), disabled.status_code

    from src.downloader import _build_lidarr_client
    from src.lidarr_client import LidarrError

    prepared = download_discovery_service.fetch_wanted_release_records(
        config._config,
        client_factory=_build_lidarr_client,
        lidarr_error=LidarrError,
    )
    if isinstance(
        prepared,
        download_discovery_service.DownloadDiscoveryResult,
    ):
        return jsonify(prepared.payload), prepared.status_code

    with DatabaseManager(config.db_path) as db:
        result = download_discovery_service.wanted_releases_result(
            db,
            prepared.records,
        )
    return jsonify(result.payload), result.status_code


@app.route("/api/library/search", methods=["GET"])
def library_search():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"success": False, "message": "Query required"})

    try:
        with DatabaseManager(config.db_path) as db:
            tracks = db.search_tracks(query)

        data = []
        for t in tracks:
            data.append(
                {
                    "artist": t.artist,
                    "album": t.album,
                    "title": t.title,
                    "path": t.local_path,
                    "mbid": t.mbid,
                }
            )

        return jsonify({"success": True, "results": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/duplicates", methods=["GET"])
def get_duplicates():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    try:
        from src.clear_dupes import get_duplicates_for_ui

        with DatabaseManager(config.db_path) as db:
            dupes = get_duplicates_for_ui(db)
        return jsonify({"success": True, "duplicates": dupes})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/duplicates/resolve", methods=["POST"])
def resolve_dupes():
    if not config:
        return jsonify({"error": "Not initialized"}), 503
    data = request.json
    mbid = data.get("mbid")
    keep_path = data.get("keep_path")
    delete_paths = data.get("delete_paths", [])

    try:
        from src.clear_dupes import resolve_duplicates

        with DatabaseManager(config.db_path) as db:
            result = resolve_duplicates(db, mbid, keep_path, delete_paths)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/library/split-albums", methods=["GET"])
def api_split_albums():
    """Detect albums that have been fragmented into multiple groups.

    Uses two strategies: same-folder tracks assigned to different album groups
    (metadata mismatch), and album-title similarity across groups from the same
    artist. Returns a list of incidents the user can review and optionally merge.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        from src.split_album_detector import detect_split_albums
        with DatabaseManager(config.db_path) as db:
            incidents = detect_split_albums(db)
        return jsonify({"success": True, "incidents": incidents, "count": len(incidents)})
    except Exception as e:
        logger.exception("api_split_albums failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/split-albums/merge", methods=["POST"])
def api_split_albums_merge():
    """Merge a secondary album group into the primary.

    Body:
      primary_album_id:    album_id of the group to keep
      secondary_album_id:  album_id whose tracks get reassigned
      target_album:        canonical album title to write
      target_artist:       canonical artist to write
      target_release_mbid: release MBID to write (or "" to clear)
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    primary = (data.get("primary_album_id") or "").strip()
    secondary = (data.get("secondary_album_id") or "").strip()
    target_album = (data.get("target_album") or "").strip()
    target_artist = (data.get("target_artist") or "").strip()
    target_release_mbid = (data.get("target_release_mbid") or "").strip() or None

    if not primary or not secondary or not target_album or not target_artist:
        return jsonify({
            "success": False,
            "message": "primary_album_id, secondary_album_id, target_album, and target_artist are required",
        }), 400
    if primary == secondary:
        return jsonify({"success": False, "message": "primary and secondary must differ"}), 400

    try:
        from src.split_album_detector import merge_album_groups
        with DatabaseManager(config.db_path) as db:
            result = merge_album_groups(
                db, primary, secondary, target_album, target_artist, target_release_mbid
            )
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.exception("api_split_albums_merge failed")
        return jsonify({"success": False, "message": str(e)}), 500


def _maintenance_jellyfin_client_factory(config_values):
    """Late-bound adapter so disabled maintenance never imports downloader."""
    from src.downloader import _build_jellyfin_client

    return _build_jellyfin_client(config_values)


def _trigger_jellyfin_scan(context: str) -> None:
    """Best-effort Jellyfin library refresh after a metadata mutation.

    Swallows all errors (logged) so a Jellyfin outage never fails the request
    that triggered it. ``context`` names the calling operation for the log line.
    """
    maintenance_application_service.trigger_jellyfin_scan(
        context=context,
        config_values=config._config,
        jellyfin_client_factory=_maintenance_jellyfin_client_factory,
        event_logger=logger,
    )


@app.route("/api/library/consolidate-editions", methods=["POST"])
def api_consolidate_editions():
    """Fold base/standard album editions into their superset (deluxe) edition.

    Body: ``{"dry_run": bool}`` — when true (default), returns the planned
    changes without writing. Send ``{"dry_run": false}`` to apply.
    Triggers a Jellyfin scan after a real run so the merged albums refresh.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    dry_run = bool(data.get("dry_run", True))
    from src.split_album_detector import consolidate_editions

    result = maintenance_application_service.consolidate_album_editions(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        dry_run=dry_run,
        consolidate_operation=consolidate_editions,
        config_values=config._config,
        jellyfin_client_factory=_maintenance_jellyfin_client_factory,
        event_logger=logger,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/library/scrub-dangling", methods=["POST"])
def api_scrub_dangling():
    """Find/clear local_path on tracks whose file is missing from disk.

    Body: ``{"dry_run": bool}`` — defaults to **true** (preview only). Send
    ``{"dry_run": false}`` to actually clear. The catalog row is kept; only the
    broken link is cleared. Returns ``{dry_run, scanned, cleared, fraction,
    sample}``.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    dry_run = bool(data.get("dry_run", True))
    try:
        with DatabaseManager(config.db_path) as db:
            result = db.clear_missing_local_paths(dry_run=dry_run)
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.exception("api_scrub_dangling failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/retag-files", methods=["POST"])
def api_retag_files():
    """Rewrite on-disk album tags to match the DB (repairs metadata-only edits).

    Body: ``{"only_mismatched": bool}`` (default true). Triggers a Jellyfin
    scan afterward so the corrected tags are picked up.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    only_mismatched = bool(data.get("only_mismatched", True))
    from src.split_album_detector import retag_files_from_db

    result = maintenance_application_service.retag_library_files(
        db_path=config.db_path,
        database_factory=DatabaseManager,
        only_mismatched=only_mismatched,
        retag_operation=retag_files_from_db,
        config_values=config._config,
        jellyfin_client_factory=_maintenance_jellyfin_client_factory,
        event_logger=logger,
    )
    return jsonify(result.payload), result.status_code


@app.route("/api/library/split-albums/dismiss", methods=["POST"])
def api_split_albums_dismiss():
    """Dismiss a split-album incident as a false positive.

    Body: ``{"key": "<incident key>"}`` — the stable key from the
    detect response. ``{"key": ..., "undismiss": true}`` reverses it.
    Dismissed incidents stay hidden across rescans.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    key = (data.get("key") or "").strip()
    if not key:
        return jsonify({"success": False, "message": "key is required"}), 400
    undismiss = bool(data.get("undismiss"))
    try:
        with DatabaseManager(config.db_path) as db:
            if undismiss:
                db.undismiss_split_album(key)
            else:
                db.dismiss_split_album(key)
        return jsonify({"success": True, "dismissed": not undismiss})
    except Exception as e:
        logger.exception("api_split_albums_dismiss failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/install_slsk", methods=["POST"])
def install_slsk():
    result = setup_application_service.install_slsk(
        base_dir=os.path.dirname(os.path.abspath(__file__)),
        config_path=CONFIG_FILE,
        config_is_present=config_exists,
        reinitialize_runtime=init_app_logic,
    )
    return jsonify(result.payload), result.status_code


if __name__ == "__main__":
    if config_exists():
        init_app_logic()
    debug_mode = os.environ.get("DAPMANAGER_DEBUG", "0").lower() in ("1", "true", "yes", "on")
    host = os.environ.get("DAPMANAGER_HOST", "0.0.0.0")
    port = int(os.environ.get("DAPMANAGER_PORT", "5001"))
    print(f"Starting Web Server on {host}:{port} (debug={debug_mode})...")
    app.run(host=host, port=port, debug=debug_mode)
