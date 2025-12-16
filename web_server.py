
import os
import json
from flask import Flask, render_template, jsonify, request, redirect, url_for
import threading

# ... (Previous imports are fine, but imports that depend on config might fail if config is missing)
# We need to wrap imports or loading of config-dependent modules

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')

# Check if config exists
CONFIG_FILE = 'config.json'
MAC_CONFIG_FILE = 'config-mac.json'

def config_exists():
    return os.path.exists(CONFIG_FILE) or os.path.exists(MAC_CONFIG_FILE)

# Defer loading of modules that require config until we are sure it exists
task_manager = None
config = None

def init_app_logic():
    global task_manager, config, setup_logging, get_config, DatabaseManager, \
           main_scan_library, main_run_downloader, main_run_sync, \
           EnvironmentManager, audit_lib_logic
    
    # Import modules here to avoid crash on startup if config is missing/invalid
    from src.logger_setup import setup_logging
    from src.config_manager import get_config
    from src.db_manager import DatabaseManager
    from src.library_scanner import main_scan_library
    from src.downloader import main_run_downloader
    from src.sync_ipod import main_run_sync

    from src.album_completer import audit_library as audit_lib_logic

    setup_logging()
    config = get_config()
    
    # Re-initialize TaskManager if needed, or just define it here
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
                if 'progress_callback' in sig.parameters:
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
                    # Keep last message/detail for a bit? Or clear? 
                    # Keeping it is better for UI "Last Status"

    task_manager = TaskManager()


# Initialize if config exists
if config_exists():
    init_app_logic()


# Helper wrappers (need to be defined or redefined after init)
def run_scan(db_path, conf):
    with DatabaseManager(db_path) as db:
        main_scan_library(db, conf._config)

def run_download(db_path, conf, progress_callback=None):
    with DatabaseManager(db_path) as db:
        main_run_downloader(db, conf._config, progress_callback=progress_callback)

def run_sync(db_path, conf, mode, fmt, reconcile=False):
    with DatabaseManager(db_path) as db:
        main_run_sync(db, conf._config, sync_mode=mode, conversion_format=fmt, reconcile=reconcile)

def run_batch():
    batch_sync()

def run_audit(db_path):
    with DatabaseManager(db_path) as db:
        audit_lib_logic(db)


@app.before_request
def check_setup():
    # If config doesn't exist, force redirect to /setup
    # Allow static files and the save_config endpoint
    if not config_exists() and request.endpoint not in ('setup', 'save_config', 'static'):
        return redirect(url_for('setup'))
    
    # If config exists but app logic isn't loaded (e.g. just created), load it
    global task_manager
    if config_exists() and task_manager is None:
        init_app_logic()


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/setup')
def setup():
    if config_exists():
        return redirect(url_for('index'))
    return render_template('setup.html')

@app.route('/api/save_config', methods=['POST'])
def save_config():
    data = request.json
    try:
        # Construct the config dictionary based on config-mac.json structure
        new_config = {
            "music_library_path": data.get('music_library_path'),
            "downloads_path": data.get('downloads_path'),
            "slsk_command": ["slsk-batchdl"], # Default
            "picard_cmd_path": "picard",      # Default
            "ffmpeg_path": "ffmpeg",          # Default
            "ipod_mount_point": data.get('ipod_mount_point'),
            "ipod_music_dir_name": "Music",
            "ipod_playlist_dir_name": "Playlists",
            "database_file": "dap_library.db",
            "slsk_username": data.get('slsk_username'),
            "slsk_password": data.get('slsk_password'),
            "fast_search": data.get('fast_search', False),
            "remove_ft": data.get('remove_ft', False),
            "desperate_mode": data.get('desperate_mode', False),
            "strict_quality": data.get('strict_quality', False),
            "conversion_sample_rate": 44100,
            "conversion_bit_depth": 16
        }
        
        # Save as config.json
        with open(CONFIG_FILE, 'w') as f:
            json.dump(new_config, f, indent=4)
            
        # Also set env vars if provided (for immediately usage in this session if we reload env)
        # But better to just let the user restart or rely on config object
        # Note: Spotify credentials are usually ENV vars, but we can save them to a .env or just config
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/status')
def status():
    if not task_manager:
        return jsonify({'running': False, 'message': 'Not initialized'})
@app.route('/api/status')
def status():
    if not task_manager:
        return jsonify({'running': False, 'message': 'Not initialized'})
    with task_manager.lock:
        return jsonify({
            'running': task_manager.is_running,
            'task': task_manager.current_task,
            'message': task_manager.message,
            'detail': task_manager.progress_detail
        })

@app.route('/api/scan', methods=['POST'])
def scan():
    if not task_manager: return jsonify({'success': False, 'message': 'Not initialized'})
    success, msg = task_manager.start_task(run_scan, (config.db_path, config), "Library Scan")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/download', methods=['POST'])
def download():
    if not task_manager: return jsonify({'success': False, 'message': 'Not initialized'})
    success, msg = task_manager.start_task(run_download, (config.db_path, config), "Download Queue")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/sync', methods=['POST'])
def sync():
    if not task_manager: return jsonify({'success': False, 'message': 'Not initialized'})
    data = request.json or {}
    mode = data.get('mode', 'playlists')
    fmt = data.get('format', 'flac')
    success, msg = task_manager.start_task(run_sync, (config.db_path, config, mode, fmt), f"Sync ({mode})")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/playlists/queue', methods=['POST'])
def queue_playlists():
    if not task_manager: return jsonify({'success': False, 'message': 'Not initialized'})
    data = request.json or {}
    urls = data.get('urls', [])
    if isinstance(urls, str):
        urls = [u.strip() for u in urls.splitlines() if u.strip()]
    
    success, msg = task_manager.start_task(run_queue_playlists, (config.db_path, config, urls), f"Queueing {len(urls)} Playlists")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/audit', methods=['POST'])
def audit():
    # Legacy audit (runs in background thread, logs to file)
    if not task_manager: return jsonify({'success': False, 'message': 'Not initialized'})
    success, msg = task_manager.start_task(run_audit, (config.db_path,), "Audit Library")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/audit/results', methods=['GET'])
def audit_results():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            incomplete = db.get_incomplete_albums()
            
        # Add Cover Art URL
        for item in incomplete:
            item['cover_art'] = f"https://coverartarchive.org/release/{item['mbid']}/front-250"
            
        return jsonify({'success': True, 'results': incomplete})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/audit/details', methods=['GET'])
def audit_details():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    mbid = request.args.get('mbid')
    if not mbid: return jsonify({'success': False, 'message': 'MBID required'})
    
    try:
        from src.album_completer import get_missing_tracks_for_album
        with DatabaseManager(config.db_path) as db:
            result = get_missing_tracks_for_album(db, mbid)
            
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/audit/queue', methods=['POST'])
def audit_queue():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    data = request.json
    items = data.get('items', []) # List of {artist, title}
    
    # Or queue full album
    queue_album = data.get('queue_album', False)
    album_info = data.get('album_info', {}) # {artist, title}
    
    try:
        count = 0
        with DatabaseManager(config.db_path) as db:
            if queue_album:
                 # Queue full album
                 query = f"::ALBUM:: {album_info.get('artist')} - {album_info.get('album')}"
                 db.add_to_queue(
                    search_query=query,
                    track_mbid="ALBUM_MODE", 
                    artist=album_info.get('artist', 'Unknown'),
                    title=f"[Full Album] {album_info.get('album', 'Unknown')}"
                )
                 count = 1
            else:
                for item in items:
                    # Construct search query
                    query = f"{item['artist']} - {item['title']}"
                    db.add_to_queue(
                        search_query=query,
                        track_mbid="", 
                        artist=item['artist'],
                        title=item['title']
                    )
                    count += 1
                    
        return jsonify({'success': True, 'queued_count': count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    try:
        import shutil
        with DatabaseManager(config.db_path) as db:
            lib_stats = db.get_library_stats()
            
        # Disk Usage
        total, used, free = shutil.disk_usage(config.music_library)
        lib_stats['disk_total_gb'] = round(total / (1024**3), 2)
        lib_stats['disk_free_gb'] = round(free / (1024**3), 2)
        lib_stats['disk_used_percent'] = round((used / total) * 100, 1)
        
        return jsonify({'success': True, 'stats': lib_stats})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/downloads/list', methods=['GET'])
def get_downloads_list():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    try:
        with DatabaseManager(config.db_path) as db:
            items = db.get_all_downloads()
        
        # Convert to JSON serializable
        data = []
        for item in items:
            data.append({
                'id': item.id,
                'query': item.search_query,
                'status': item.status,
                'last_attempt': item.last_attempt.isoformat() if item.last_attempt else None
            })
            
        return jsonify({'success': True, 'items': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/library/search', methods=['GET'])
def library_search():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'success': False, 'message': 'Query required'})
        
    try:
        with DatabaseManager(config.db_path) as db:
            tracks = db.search_tracks(query)
            
        data = []
        for t in tracks:
            data.append({
                'artist': t.artist,
                'album': t.album,
                'title': t.title,
                'path': t.local_path,
                'mbid': t.mbid
            })
            
        return jsonify({'success': True, 'results': data})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/duplicates', methods=['GET'])
def get_duplicates():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    try:
        from src.clear_dupes import get_duplicates_for_ui
        with DatabaseManager(config.db_path) as db:
            dupes = get_duplicates_for_ui(db)
        return jsonify({'success': True, 'duplicates': dupes})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/duplicates/resolve', methods=['POST'])
def resolve_dupes():
    if not config: return jsonify({'error': 'Not initialized'}), 503
    data = request.json
    mbid = data.get('mbid')
    keep_path = data.get('keep_path')
    delete_paths = data.get('delete_paths', [])
    
    try:
        from src.clear_dupes import resolve_duplicates
        with DatabaseManager(config.db_path) as db:
            result = resolve_duplicates(db, mbid, keep_path, delete_paths)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/install_slsk', methods=['POST'])
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
            with open(CONFIG_FILE, 'r') as f:
                c = json.load(f)
            
            c["slsk_command"] = [final_path]
            
            with open(CONFIG_FILE, 'w') as f:
                json.dump(c, f, indent=4)
                
            # Force reload of app logic to pick up new command
            init_app_logic()
            
        return jsonify({'success': True, 'path': final_path})
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/bootstrap', methods=['POST'])
def bootstrap():
    """
    One-click setup: Config -> Scan -> Sync -> (Download)
    POST body: { "config": {...}, "run_downloads": bool }
    """
    global task_manager
    
    if not task_manager:
        # If task manager isn't init, it means config was missing. 
        # We need to initialize it temporarily or handle it.
        # But wait, init_app_logic checks config existence.
        # If we are bootstrapping, we are providing the config.
        # So we should save it first? 
        # bootstrap.py handles saving. But task_manager needs to exist to run it?
        # Chicken and egg.
        # Solution: If task_manager is None, we need to create it manually or 
        # allow bootstrap to run unwrapped (blocking) OR init a temporary task manager.
        # BETTER: Save the config here first if missing, THEN init app logic, THEN use task manager.
        pass

    data = request.json or {}
    config_data = data.get('config')
    run_downloads = data.get('run_downloads', False)
    
    if not config_data:
        return jsonify({'success': False, 'message': 'Missing config data'})

    # Special handling if this is the FIRST run (config doesn't exist yet)

    if not task_manager:
         # 1. Save config immediately to disk so we can init
         # (We duplicate the save logic slightly or just use bootstrap's logic but we can't call it without TM?)
         # Actually we can call it without TM if we want blocking, but user wants API response.
         # Let's stick to the plan: Bootstrap saves config.
         # So we need to enable TaskManager even if config missing?
         # Or just init TaskManager in a dummy state.
         
         # Let's manually init the Threading/Task logic if missing
         pass

    # Ensure TaskManager exists. If not, we can't return async status.
    # Refactor web_server to create TaskManager always?
    # For now, let's just create a temporary one if needed or init the global one.
    if config_exists() and not task_manager:
        init_app_logic()
    
    if not task_manager:
        # Still null? Means init_app_logic failed or config missing.
        # Lets Init just the TM part?
        # Hack: Just run `init_app_logic` even if config missing? No it imports things that crash.
        # Lets just save the config manually HERE first.
        try:
             with open(CONFIG_FILE, 'w') as f:
                json.dump(config_data, f, indent=4) # Rough dump
             init_app_logic() # Now it should work!
        except Exception as e:
            return jsonify({'success': False, 'message': f"Init failed: {e}"})

    if not task_manager:
         return jsonify({'success': False, 'message': 'System initialization failed'})

    try:
        from src.bootstrap import run_bootstrap_sequence
        success, msg = task_manager.start_task(
            run_bootstrap_sequence, 
            (config_data, run_downloads), 
            "Bootstrap Sequence"
        )
        return jsonify({'success': success, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

if __name__ == '__main__':
    print("Starting Web Server on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=True)
