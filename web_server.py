import os
import json
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
import threading

from src.config_paths import ensure_parent_dir, resolve_config_path

logger = logging.getLogger(__name__)

# ... (Previous imports are fine, but imports that depend on config might fail if config is missing)
# We need to wrap imports or loading of config-dependent modules

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

# Check if config exists
CONFIG_FILE = resolve_config_path()


def config_exists():
    return os.path.exists(CONFIG_FILE)


# Defer loading of modules that require config until we are sure it exists
task_manager = None
config = None
sync_scheduler = None
release_watcher_scheduler = None


class TaskManager:
    def __init__(self):
        self.current_task = None
        self.is_running = False
        self.message = "Idle"
        self.progress_detail = ""  # New: detailed progress
        self.lock = threading.Lock()

    def start_task(self, task_func, args=(), task_name="Task"):
        with self.lock:
            if self.is_running:
                return False, f"Task '{self.current_task}' is already running."

            self.is_running = True
            self.current_task = task_name
            self.message = f"Starting {task_name}..."
            self.progress_detail = ""

            thread = threading.Thread(target=self._run_wrapper, args=(task_func, args))
            thread.daemon = True
            thread.start()
            return True, "Task started."

    def update_progress(self, data: dict):
        with self.lock:
            if "message" in data:
                self.message = data["message"]
            if "detail" in data:
                self.progress_detail = data["detail"]

    def _run_wrapper(self, func, args):
        try:
            # If the function accepts a 'progress_callback' kwarg, pass it
            import inspect

            sig = inspect.signature(func)
            if "progress_callback" in sig.parameters:
                func(*args, progress_callback=self.update_progress)
            else:
                func(*args)

            with self.lock:
                self.message = f"{self.current_task} completed successfully."
        except Exception as e:
            logger.error(f"Task failed: {e}", exc_info=True)
            with self.lock:
                self.message = f"Error in {self.current_task}: {str(e)}"
        finally:
            with self.lock:
                self.is_running = False
                self.current_task = None


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


def _start_sync_scheduler():
    """Kick off the periodic Sync All loop if configured."""
    global sync_scheduler
    from src.sync_scheduler import SyncScheduler

    interval = int(config._config.get("sync_interval_seconds") or 0)
    on_startup = bool(config._config.get("sync_on_startup") or False)

    def _trigger():
        task_manager.start_task(
            run_sync_all, (config.db_path, config), "Sync All (scheduled)"
        )

    sync_scheduler = SyncScheduler(interval, _trigger, run_on_startup=on_startup)
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

    if not bool(config._config.get("lidarr_watch_enabled") or False):
        logger.info("release_watcher disabled (lidarr_watch_enabled != true).")
        release_watcher_scheduler = None
        return

    interval = int(config._config.get("lidarr_watch_interval_seconds") or 3600)

    def _trigger():
        client = _build_lidarr_client(config._config)
        if client is None:
            logger.debug("release_watcher: Lidarr unavailable; skipping tick.")
            return
        with DatabaseManager(config.db_path) as db:
            run_watch_tick(db, client)

    release_watcher_scheduler = SyncScheduler(interval, _trigger, run_on_startup=False)
    release_watcher_scheduler.start()


# Helper wrappers (need to be defined or redefined after init)
def run_scan(db_path, conf):
    with DatabaseManager(db_path) as db:
        main_scan_library(db, conf._config)


def run_download(db_path, conf, progress_callback=None):
    with DatabaseManager(db_path) as db:
        main_run_downloader(db, conf._config, progress_callback=progress_callback)


def run_sync(db_path, conf, mode, fmt, reconcile=False):
    with DatabaseManager(db_path) as db:
        main_run_sync(
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


def build_suggestion_items(raw_items):
    """Normalize a suggestions payload into (search_query, mbid_guess) pairs.

    Each raw item may provide any of:
      - search_query: used verbatim
      - mbid: kept as mbid_guess; if artist+title are also present they form the query
      - artist + title: combined into "artist - title"

    Returns a list of (query, mbid) tuples. Items without a usable query are dropped.
    Duplicates (same query, case-insensitive) are removed preserving first occurrence.
    """
    seen = set()
    results = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        query = (item.get("search_query") or "").strip()
        mbid = (item.get("mbid") or "").strip()
        if not query:
            artist = (item.get("artist") or "").strip()
            title = (item.get("title") or "").strip()
            if artist and title:
                query = f"{artist} - {title}"
            elif title:
                query = title
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append((query, mbid))
    return results


def run_complete_albums(db_path, conf, run_downloads=False, progress_callback=None):
    """Run the full album completion pipeline, optionally followed by downloads."""
    with DatabaseManager(db_path) as db:
        summary = complete_albums_logic(db, progress_callback=progress_callback)

    if run_downloads and summary.get("tracks_queued", 0) > 0:
        if progress_callback:
            progress_callback({"message": "Downloading queued tracks..."})
        with DatabaseManager(db_path) as db:
            main_run_downloader(db, conf._config, progress_callback=progress_callback)

        if progress_callback:
            progress_callback({"message": "Re-scanning library..."})
        with DatabaseManager(db_path) as db:
            main_scan_library(db, conf._config)


@app.before_request
def check_setup():
    # If config doesn't exist, force redirect to /setup
    # Allow static files and the save_config endpoint
    if not config_exists() and request.endpoint not in (
        "setup",
        "save_config",
        "setup_validate_path",
        "setup_detect_public_url",
        "download_mac",
        "static",
    ):
        return redirect(url_for("setup"))

    # If config exists but app logic isn't loaded (e.g. just created), load it
    global task_manager
    if config_exists() and task_manager is None:
        init_app_logic()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/setup")
def setup():
    if config_exists():
        return redirect(url_for("index"))
    return render_template("setup.html")


from src.config_keys import (
    BOOL_KEYS as CONFIG_BOOL_KEYS,
    EDITABLE_KEYS as CONFIG_EDITABLE_KEYS,
    GROUPS as CONFIG_GROUPS,
    SECRET_KEYS as CONFIG_SECRET_KEYS,
)

API_AUTH_EXEMPT_PATHS = {"/api/status", "/api/healthz"}


@app.before_request
def _check_api_token():
    """Enforce Authorization: Bearer <token> on /api/* when api_token is set.

    Open mode (no token in config) keeps current behavior for LAN-only setups;
    a warning is logged at init time so the operator knows it's unauthenticated.
    /api/status is exempt so health checks don't need the header.
    """
    import hmac

    if not request.path.startswith("/api/"):
        return None
    if request.path in API_AUTH_EXEMPT_PATHS:
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
    if not header.startswith("Bearer "):
        return jsonify({"success": False, "message": "missing bearer token"}), 401
    provided = header[len("Bearer "):].strip()
    if not hmac.compare_digest(provided, token):
        return jsonify({"success": False, "message": "invalid api token"}), 401
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
        with open(CONFIG_FILE, "r") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return jsonify({"success": False, "message": "config.json not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    redacted = dict(raw)
    for key in CONFIG_SECRET_KEYS:
        if key in redacted and redacted[key]:
            redacted[key] = ""
    return jsonify({
        "success": True,
        "config": redacted,
        "editable_keys": sorted(CONFIG_EDITABLE_KEYS),
        "secret_keys": sorted(CONFIG_SECRET_KEYS),
        "bool_keys": sorted(CONFIG_BOOL_KEYS),
        "groups": [{"label": label, "keys": keys} for label, keys in CONFIG_GROUPS],
    })


@app.route("/api/config", methods=["POST"])
def update_config():
    """Partial-merge update into config.json.

    Only keys in CONFIG_EDITABLE_KEYS are accepted; others are silently
    ignored so the UI can't wipe e.g. the db path. Empty strings for
    secret keys mean "don't change".
    """
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "body must be an object"}), 400

    try:
        with open(CONFIG_FILE, "r") as f:
            current = json.load(f)
    except FileNotFoundError:
        return jsonify({"success": False, "message": "config.json not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    changed = []
    for key, value in data.items():
        if key not in CONFIG_EDITABLE_KEYS:
            continue
        if key in CONFIG_SECRET_KEYS and value == "":
            continue
        if current.get(key) != value:
            current[key] = value
            changed.append(key)

    try:
        ensure_parent_dir(CONFIG_FILE)
        with open(CONFIG_FILE, "w") as f:
            json.dump(current, f, indent=4)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    # Reload the in-process config so subsequent requests see the new values.
    try:
        from src.config_manager import ConfigManager
        ConfigManager._instance = None
        global config
        if config is not None:
            config._load_config()
    except Exception as e:
        logger.warning(f"Config written but in-process reload failed: {e}")

    return jsonify({"success": True, "changed": changed})


_FIRST_RUN_FIELDS = {
    "music_library_path", "downloads_path", "dap_mount_point",
    "master_url", "public_master_url", "api_token", "device_name",
    "slsk_username", "slsk_password",
    "jellyfin_url", "jellyfin_api_key", "jellyfin_user_id",
    "lidarr_url", "lidarr_api_key", "lidarr_enabled",
    "acoustid_api_key", "contact_email",
    "report_inventory_to_host",
    "fast_search", "remove_ft", "desperate_mode", "strict_quality",
}


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
    role = (data.get("role") or "master").strip().lower()
    payload = {k: v for k, v in data.items() if k in _FIRST_RUN_FIELDS}
    try:
        new_config = build_initial_config(role, **payload)
    except (TypeError, ValueError) as e:
        return jsonify({"success": False, "message": str(e)}), 400

    try:
        ensure_parent_dir(CONFIG_FILE)
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=4)
        return jsonify({"success": True})
    except Exception as e:
        logger.exception("save_config failed")
        return jsonify({"success": False, "message": str(e)}), 500


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
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@app.route("/download/mac", methods=["GET"])
def download_mac():
    """Serve the satellite Mac bundle with this master's URL embedded.

    Refuses with 409 when ``public_master_url`` isn't configured —
    serving a bundle pinned to a URL satellites can't reach is a
    worse outcome than failing loudly. When ``api_token`` is set,
    requires it via ``Authorization: Bearer`` or ``?token=`` query.
    """
    import hmac
    from src.satellite_bundle import (
        BundleFetchError,
        ensure_cached_bundle,
        inject_master_config,
    )

    cfg = _read_master_config_for_download() or {}
    public_url = (cfg.get("public_master_url") or "").strip().rstrip("/")
    if not public_url:
        return jsonify({
            "success": False,
            "message": (
                "public_master_url is not set. Open Settings → Multi-Device "
                "Sync (or re-run /setup) and fill it in before sharing the "
                "download link."
            ),
        }), 409

    token = (cfg.get("api_token") or "").strip()
    if token:
        provided = ""
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            provided = header[len("Bearer "):].strip()
        else:
            provided = (request.args.get("token") or "").strip()
        if not hmac.compare_digest(provided, token):
            return jsonify({
                "success": False,
                "message": "missing or invalid api token",
            }), 401

    try:
        base_path = ensure_cached_bundle()
    except BundleFetchError as e:
        logger.warning("download_mac: bundle fetch failed: %s", e)
        return jsonify({
            "success": False,
            "message": (
                "Could not fetch the satellite bundle from GitHub. "
                "Check the master's outbound connectivity."
            ),
        }), 502

    try:
        body = inject_master_config(base_path, public_url, token or None)
    except Exception as e:
        logger.exception("download_mac: injection failed")
        return jsonify({"success": False, "message": str(e)}), 500

    return Response(
        body,
        mimetype="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="DAPManager-mac.zip"',
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
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
    import shutil
    import subprocess

    env_url = (os.environ.get("MASTER_PUBLIC_URL") or "").strip()
    if env_url:
        return jsonify({"source": "env", "url": env_url})

    cli = shutil.which("tailscale")
    if not cli:
        return jsonify({"source": "none"})
    port = (os.environ.get("MASTER_PORT") or "5001").strip() or "5001"
    try:
        proc = subprocess.run(
            [cli, "status", "--json"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return jsonify({"source": "none"})
    if proc.returncode != 0 or not proc.stdout:
        return jsonify({"source": "none"})
    try:
        status = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return jsonify({"source": "none"})
    self_block = status.get("Self") or {}
    dns = (self_block.get("DNSName") or "").rstrip(".")
    if dns:
        return jsonify({"source": "tailscale", "url": f"http://{dns}:{port}"})
    for ip in (self_block.get("TailscaleIPs") or []):
        if ip and ":" not in ip:
            return jsonify({"source": "tailscale", "url": f"http://{ip}:{port}"})
    return jsonify({"source": "none"})


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
            albums = db.list_albums()
        # cover_path is a local FS path; strip before sending to the webview.
        public = [
            {
                "id": a["id"],
                "title": a["title"],
                "artist": a["artist"],
                "track_count": a["track_count"],
            }
            for a in albums
        ]
        return jsonify({"success": True, "albums": public})
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
    has_filters = bool(playlist_id) or local_only or include_orphans

    try:
        with DatabaseManager(config.db_path) as db:
            if has_filters:
                rows = db.list_tracks_filtered(
                    playlist_id=playlist_id,
                    local_only=local_only,
                    include_orphans=include_orphans,
                )
            else:
                rows = db.list_all_tracks()
        has_master = _master_url_configured()
        public = []
        for r in rows:
            availability = _availability_for(r, has_master)
            is_orphan = bool(r.get("deleted_at"))
            # When orphans are requested the UI wants to see them regardless
            # of whether they're playable — restoring is the point.
            if availability == "unavailable" and not (include_orphans and is_orphan):
                continue
            entry = _public_track_row(r, has_master)
            if include_orphans:
                entry["orphan"] = is_orphan
            public.append(entry)
        return jsonify({"success": True, "tracks": public})
    except Exception as e:
        logger.exception("api_library_tracks failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/playlists", methods=["GET"])
def api_library_playlists():
    """Live playlists with membership counts, for the library sidebar.

    Distinct from ``GET /api/playlists`` (which is the sync delta feed).
    Orphans are excluded — the /orphans page handles those.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            rows = db.list_playlists_with_counts()
        return jsonify({"success": True, "playlists": rows})
    except Exception as e:
        logger.exception("api_library_playlists failed")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/library/playlists", methods=["POST"])
def api_library_playlists_create():
    """Create an empty playlist. Body: ``{"name": "..."}``.

    Returns the generated ``playlist_id`` so the caller can immediately
    PUT tracks into it without re-listing. Membership pushes to master
    on the next scheduled push (same as any other playlist edit).
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"success": False, "message": "name is required"}), 400
    try:
        with DatabaseManager(config.db_path) as db:
            pid = db.create_playlist(name)
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        logger.exception("api_library_playlists_create failed")
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "playlist_id": pid, "name": name}), 201


@app.route("/api/library/playlists/<playlist_id>", methods=["PUT"])
def api_library_playlist_update(playlist_id: str):
    """Partial update: rename and/or replace full membership.

    Body keys (both optional — at least one required):
      - ``name``: new display name (trimmed; rejected if empty).
      - ``track_mbids``: list of mbids in the desired order. An empty
        list explicitly empties the playlist; omit the key to leave
        membership untouched.

    Unknown mbids in ``track_mbids`` are silently dropped and surface
    as ``landed`` vs ``requested`` in the response so the UI can flag
    a partial miss.
    """
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    data = request.json or {}
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "body must be an object"}), 400

    has_name = "name" in data
    has_tracks = "track_mbids" in data
    if not has_name and not has_tracks:
        return jsonify({
            "success": False,
            "message": "at least one of 'name' or 'track_mbids' is required",
        }), 400

    try:
        with DatabaseManager(config.db_path) as db:
            if db.get_playlist(playlist_id) is None:
                return jsonify({
                    "success": False,
                    "message": "playlist not found or deleted",
                }), 404
            renamed = False
            landed = None
            requested = None
            if has_name:
                new_name = (data.get("name") or "").strip()
                if not new_name:
                    return jsonify({
                        "success": False,
                        "message": "name must not be empty",
                    }), 400
                renamed = db.rename_playlist(playlist_id, new_name)
            if has_tracks:
                mbids = data.get("track_mbids")
                if not isinstance(mbids, list):
                    return jsonify({
                        "success": False,
                        "message": "track_mbids must be a list",
                    }), 400
                requested = len(mbids)
                landed = db.replace_playlist_membership(playlist_id, mbids)
    except Exception as e:
        logger.exception("api_library_playlist_update failed")
        return jsonify({"success": False, "message": str(e)}), 500

    resp = {"success": True, "playlist_id": playlist_id, "renamed": renamed}
    if has_tracks:
        resp["landed"] = landed
        resp["requested"] = requested
    return jsonify(resp)


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
    cfg = getattr(config, "_config", None)
    if not isinstance(cfg, dict):
        return False
    return bool((cfg.get("master_url") or "").strip())


def _availability_for(row: dict, has_master: bool) -> str:
    """Resolve a track's playback tier from its columns + master config.

    Priority: a real local file > a DAP path that only resolves when the
    drive is mounted > the master's stream endpoint. Path columns are
    trusted at listing time; actual file existence is checked at stream
    time so the UI doesn't pay a stat-per-row tax.
    """
    if (row.get("local_path") or "").strip():
        return "local"
    if (row.get("dap_path") or "").strip():
        return "drive"
    if has_master:
        return "remote"
    return "unavailable"


def _public_track_row(row: dict, has_master: bool) -> dict:
    """Shape a DB track row for the webview: strip on-disk paths, add availability."""
    return {
        "mbid": row["mbid"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row.get("album"),
        "track_number": row.get("track_number"),
        "disc_number": row.get("disc_number"),
        "album_id": row.get("album_id"),
        "availability": _availability_for(row, has_master),
    }


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


@app.route("/api/library/albums/<path:album_id>/cover")
def api_library_album_cover(album_id: str):
    """Return embedded cover art for an album, or 404 if none."""
    if not config:
        return ("", 503)
    try:
        from flask import Response
        from src.cover_art import extract_cover

        with DatabaseManager(config.db_path) as db:
            path = db.get_album_cover_path(album_id)
        if not path:
            return ("", 404)

        result = extract_cover(path)
        if result is None:
            return ("", 404)

        data, mime = result
        return Response(
            data,
            mimetype=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        logger.exception("api_library_album_cover failed for %s", album_id)
        return ("", 500)


@app.route("/api/library/albums/<path:album_id>/tracks")
def api_library_album_tracks(album_id: str):
    """Ordered track list for an album — feeds the detail/playback view."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            rows = db.list_album_tracks(album_id)
        has_master = _master_url_configured()
        public = [
            {
                **_public_track_row({**r, "album_id": album_id}, has_master),
            }
            for r in rows
            if _availability_for(r, has_master) != "unavailable"
        ]
        return jsonify({"success": True, "tracks": public})
    except Exception as e:
        logger.exception("api_library_album_tracks failed for %s", album_id)
        return jsonify({"success": False, "message": str(e)}), 500


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
            sources = db.get_track_sources(mbid)
        if sources is None:
            return ("", 404)

        local = (sources.get("local_path") or "").strip()
        if local and os.path.isfile(local):
            return send_file(local, mimetype=_guess_audio_mime(local), conditional=True)

        dap = (sources.get("dap_path") or "").strip()
        if dap and os.path.isfile(dap):
            return send_file(dap, mimetype=_guess_audio_mime(dap), conditional=True)

        cfg = getattr(config, "_config", None)
        if isinstance(cfg, dict):
            master_url = (cfg.get("master_url") or "").strip().rstrip("/")
            if master_url:
                return _proxy_master_stream(master_url, mbid)
        return ("", 404)
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

    headers = {}
    if "Range" in request.headers:
        headers["Range"] = request.headers["Range"]
    token = (config._config.get("api_token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    upstream_url = f"{master_url}/api/stream/{mbid}"
    try:
        upstream = requests.get(
            upstream_url, headers=headers, stream=True, timeout=(5, 30)
        )
    except requests.RequestException:
        logger.exception("master stream proxy failed for %s", mbid)
        return ("", 502)

    if upstream.status_code >= 400:
        upstream.close()
        return ("", upstream.status_code)

    forward = {}
    for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
        if h in upstream.headers:
            forward[h] = upstream.headers[h]

    def _iter():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return Response(
        stream_with_context(_iter()),
        status=upstream.status_code,
        headers=forward,
    )


def _guess_audio_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".wav": "audio/wav",
        ".aac": "audio/aac",
    }.get(ext, "application/octet-stream")


@app.route("/api/healthz")
def healthz():
    """Liveness probe for container orchestrators.

    Unauthenticated and side-effect-free; returns 200 once the Flask
    app is up and config has loaded. Exempt from the API-token gate.
    """
    ok = config is not None
    return (
        jsonify({"ok": ok, "initialized": task_manager is not None}),
        200 if ok else 503,
    )


@app.route("/api/status")
def status():
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
    if not task_manager:
        return jsonify({"success": False, "message": "Not initialized"})
    success, msg = task_manager.start_task(
        run_download, (config.db_path, config), "Download Queue"
    )
    return jsonify({"success": success, "message": msg})


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
    if not config.is_master:
        return jsonify({"success": False, "message": "This instance is not a master"}), 400

    data = request.json or {}
    query = (data.get("search_query") or "").strip()
    if not query:
        return jsonify({"success": False, "message": "search_query is required"}), 400

    mbid_guess = (data.get("mbid_guess") or "").strip()
    playlist_id = (data.get("playlist_id") or "SATELLITE").strip() or "SATELLITE"

    try:
        with DatabaseManager(config.db_path) as db:
            if db.is_download_queued(query):
                return jsonify({
                    "success": True,
                    "queued": False,
                    "message": "already queued",
                })
            item_id = db.queue_download(DownloadItem(
                search_query=query,
                playlist_id=playlist_id,
                mbid_guess=mbid_guess,
            ))
    except Exception as e:
        logger.error(f"Download request failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "queued": True,
        "item_id": item_id,
        "message": "queued",
    })


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
    """Return the four sync-cursor timestamps for the status widget."""
    if not config:
        return jsonify({"success": False, "message": "Not initialized"}), 503
    keys = {
        "last_catalog_sync": "catalog_pull",
        "last_playlist_sync": "playlist_pull",
        "last_playlist_push": "playlist_push",
        "last_inventory_report": "inventory_report",
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
            as_of = db.conn.execute("SELECT CURRENT_TIMESTAMP AS t").fetchone()["t"]
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
            as_of = db.conn.execute("SELECT CURRENT_TIMESTAMP AS t").fetchone()["t"]
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

    accepted = 0
    stale = 0
    skipped = 0
    results = []
    try:
        with DatabaseManager(config.db_path) as db:
            for row in items:
                action = db.apply_pushed_playlist_row(row)
                if action in ("inserted", "updated"):
                    accepted += 1
                elif action == "stale":
                    stale += 1
                else:
                    skipped += 1
                results.append({
                    "playlist_id": (row or {}).get("playlist_id"),
                    "result": action,
                })
    except Exception as e:
        logger.error(f"Playlist push apply failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "received": len(items),
        "accepted": accepted,
        "stale": stale,
        "skipped": skipped,
        "results": results,
    })


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
    with DatabaseManager(config.db_path) as db:
        track = db.get_track_by_mbid(mbid)
    if not track or not track.local_path:
        return jsonify({
            "success": False,
            "message": "Track has no local_path — cannot fingerprint.",
        }), 404

    contact = (config._config.get("contact_email") or "").strip()
    try:
        candidate = tag_service.identify_file(track.local_path, api_key, contact)
    except Exception as e:
        logger.error(f"tag_identify failed for {mbid}: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    if not candidate:
        return jsonify({
            "success": True,
            "candidate": None,
            "message": "no match",
            "current": tag_service.read_current_tags(track.local_path),
        })

    return jsonify({
        "success": True,
        "candidate": candidate,
        "mbid": mbid,
        "local_path": track.local_path,
    })


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
    meta = body.get("meta") or {}
    if not isinstance(meta, dict) or not meta.get("title"):
        return jsonify({"success": False, "message": "meta.title is required"}), 400

    from src import tag_service
    from src.db_manager import Track
    with DatabaseManager(config.db_path) as db:
        track = db.get_track_by_mbid(mbid)
        if not track or not track.local_path:
            return jsonify({
                "success": False,
                "message": "Track has no local_path — cannot tag.",
            }), 404

        try:
            container = tag_service.write_tags(track.local_path, meta)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400
        except Exception as e:
            logger.error(f"tag_apply write failed for {mbid}: {e}", exc_info=True)
            return jsonify({"success": False, "message": str(e)}), 500

        new_mbid = (meta.get("mbid") or "").strip() or track.mbid
        updated = Track(
            mbid=new_mbid,
            title=meta.get("title") or track.title,
            artist=meta.get("artist") or track.artist,
            album=meta.get("album") or track.album,
            local_path=track.local_path,
        )
        if new_mbid != track.mbid:
            db.soft_delete_track(track.mbid)
        db.add_or_update_track(updated)
        # Apply == user confirmation, so clear the review flag. Optional
        # `score` from the client (the original AcoustID score) is kept
        # for context; absence just leaves it NULL.
        score = body.get("score")
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        db.set_track_tag_tier(new_mbid, "green", score)

    return jsonify({
        "success": True,
        "container": container,
        "mbid": new_mbid,
        "previous_mbid": track.mbid,
    })


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
    try:
        with DatabaseManager(config.db_path) as db:
            if purge:
                changed = db.purge_playlist(playlist_id)
                return jsonify({
                    "success": True, "purged": changed, "playlist_id": playlist_id,
                })
            changed = db.soft_delete_playlist(playlist_id)
    except Exception as e:
        logger.error(f"soft_delete_playlist({playlist_id}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": True, "deleted": changed, "playlist_id": playlist_id})


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
    try:
        with DatabaseManager(config.db_path) as db:
            orphans = {r["mbid"]: r for r in db.get_orphan_tracks()}
            row = orphans.get(mbid)
            if row is None:
                return jsonify({
                    "success": False,
                    "message": "track is not an orphan; soft-delete it first",
                }), 409
            path = (row.get("local_path") or "").strip()
            removed = False
            if path:
                try:
                    os.remove(path)
                    removed = True
                except FileNotFoundError:
                    pass
                db.update_track_local_path(mbid, None)
        return jsonify({
            "success": True, "mbid": mbid, "path": path or None, "removed": removed,
        })
    except Exception as e:
        logger.error(f"delete_track_file({mbid}) failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500


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
            if mbid:
                holders = db.get_devices_holding_mbid(mbid)
                return jsonify({"success": True, "mbid": mbid, "holders": holders})
            if query:
                matches = db.find_tracks_for_fleet_search(query)
                enriched = []
                for row in matches:
                    holders = db.get_devices_holding_mbid(row["mbid"])
                    enriched.append({**row, "holders": holders})
                return jsonify({"success": True, "query": query, "results": enriched})
    except Exception as e:
        logger.error(f"Fleet track lookup failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False, "message": "provide mbid or q"}), 400


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

    queued = 0
    skipped = 0
    try:
        with DatabaseManager(config.db_path) as db:
            for query, mbid in pairs:
                if db.is_download_queued(query):
                    skipped += 1
                    continue
                db.queue_download(
                    DownloadItem(
                        search_query=query,
                        playlist_id="SUGGESTED",
                        mbid_guess=mbid,
                        status="pending",
                    )
                )
                queued += 1
    except Exception as e:
        logger.error(f"Failed to process suggestions: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "received": len(raw_items),
        "queued": queued,
        "skipped": skipped,
    })


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
    mbids = data.get("mbids")
    if not isinstance(mbids, list):
        return jsonify({
            "success": False,
            "message": "body must be {'mbids': [...]}",
        }), 400

    queued = 0
    queued_mbids = []
    skipped_linked = 0
    skipped_queued = 0
    not_found = 0
    try:
        with DatabaseManager(config.db_path) as db:
            for raw in mbids:
                mbid = (raw or "").strip()
                if not mbid:
                    not_found += 1
                    continue
                track = db.get_track_by_mbid(mbid)
                if track is None:
                    not_found += 1
                    continue
                if track.local_path:
                    skipped_linked += 1
                    continue
                query = f"{track.artist or ''} - {track.title or ''}".strip(" -")
                if not query:
                    not_found += 1
                    continue
                if db.is_download_queued(query):
                    skipped_queued += 1
                    continue
                db.queue_download(DownloadItem(
                    search_query=query,
                    playlist_id="CATALOG",
                    mbid_guess=mbid,
                    status="pending",
                ))
                queued += 1
                queued_mbids.append(mbid)
    except Exception as e:
        logger.error(f"catalog_queue_download failed: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

    return jsonify({
        "success": True,
        "received": len(mbids),
        "queued": queued,
        "queued_mbids": queued_mbids,
        "skipped_linked": skipped_linked,
        "skipped_queued": skipped_queued,
        "not_found": not_found,
    })


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
        count = 0
        with DatabaseManager(config.db_path) as db:
            if queue_album:
                # Queue full album
                query = (
                    f"::ALBUM:: {album_info.get('artist')} - {album_info.get('album')}"
                )
                db.queue_download(
                    DownloadItem(
                        search_query=query,
                        playlist_id="AUDIT",
                        mbid_guess=album_info.get(
                            "release_mbid", ""
                        ),  # Assuming FE sends this or we infer
                        status="pending",
                    )
                )
                count = 1
            else:
                for item in items:
                    # Construct search query
                    query = f"{item['artist']} - {item['title']}"
                    db.queue_download(
                        DownloadItem(
                            search_query=query,
                            playlist_id="AUDIT",
                            mbid_guess="",
                            status="pending",
                        )
                    )
                    count += 1

        return jsonify({"success": True, "queued_count": count})
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
                }
            )

        return jsonify({"success": True, "items": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


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


@app.route("/api/install_slsk", methods=["POST"])
def install_slsk():
    try:
        from src.binary_manager import install_from_local

        # Define dirs
        base_dir = os.path.dirname(os.path.abspath(__file__))
        releases_dir = os.path.join(base_dir, "sldl_releases")
        bin_dir = os.path.join(base_dir, "bin")

        # Install
        final_path = install_from_local(releases_dir, bin_dir)

        # Update Config
        if config_exists():
            # Reload explicit config file to edit it
            with open(CONFIG_FILE, "r") as f:
                c = json.load(f)

            c["slsk_cmd_base"] = [final_path]

            with open(CONFIG_FILE, "w") as f:
                json.dump(c, f, indent=4)

            # Force reload of app logic to pick up new command
            init_app_logic()

        return jsonify({"success": True, "path": final_path})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


if __name__ == "__main__":
    if config_exists():
        init_app_logic()
    debug_mode = os.environ.get("DAPMANAGER_DEBUG", "1").lower() in ("1", "true", "yes", "on")
    port = int(os.environ.get("DAPMANAGER_PORT", "5001"))
    print(f"Starting Web Server on port {port} (debug={debug_mode})...")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
