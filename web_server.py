import os
import json
import logging
from flask import Flask, render_template, jsonify, request, redirect, url_for
import threading

logger = logging.getLogger(__name__)

# ... (Previous imports are fine, but imports that depend on config might fail if config is missing)
# We need to wrap imports or loading of config-dependent modules

app = Flask(__name__, template_folder="web/templates", static_folder="web/static")

# Check if config exists
CONFIG_FILE = "config.json"


def config_exists():
    return os.path.exists(CONFIG_FILE)


# Defer loading of modules that require config until we are sure it exists
task_manager = None
config = None


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


@app.route("/api/save_config", methods=["POST"])
def save_config():
    data = request.json
    try:
        # Construct the config dictionary based on config-mac.json structure
        new_config = {
            "music_library_path": data.get("music_library_path"),
            "downloads_path": data.get("downloads_path"),
            "slsk_cmd_base": ["slsk-batchdl"],  # Default
            "picard_cmd_path": "picard",  # Default
            "ffmpeg_path": "ffmpeg",  # Default
            "dap_mount_point": data.get("dap_mount_point"),
            "dap_music_dir_name": "Music",
            "dap_playlist_dir_name": "Playlists",
            "database_file": "dap_library.db",
            "slsk_username": data.get("slsk_username"),
            "slsk_password": data.get("slsk_password"),
            "fast_search": data.get("fast_search", False),
            "remove_ft": data.get("remove_ft", False),
            "desperate_mode": data.get("desperate_mode", False),
            "strict_quality": data.get("strict_quality", False),
            "conversion_sample_rate": 44100,
            "conversion_bit_depth": 16,
            "jellyfin_url": data.get("jellyfin_url", ""),
            "jellyfin_api_key": data.get("jellyfin_api_key", ""),
            "jellyfin_user_id": data.get("jellyfin_user_id", ""),
        }

        # Save as config.json
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=4)

        # Also set env vars if provided (for immediately usage in this session if we reload env)
        # But better to just let the user restart or rely on config object
        # Note: Spotify credentials are usually ENV vars, but we can save them to a .env or just config

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


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
    print("Starting Web Server on port 5001...")
    app.run(host="0.0.0.0", port=5001, debug=True)
