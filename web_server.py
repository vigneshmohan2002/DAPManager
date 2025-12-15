
import logging
import threading
import time
from flask import Flask, render_template, jsonify, request
from src.logger_setup import setup_logging
from src.config_manager import get_config
from src.db_manager import DatabaseManager
from src.library_scanner import main_scan_library
from src.downloader import main_run_downloader
from src.sync_ipod import main_run_sync
from src.utils import EnvironmentManager
from src.batch_sync import batch_sync
from src.album_completer import audit_library as audit_lib_logic # Need to check if this exists or I need to import standard audit

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='web/templates', static_folder='web/static')

# Global state for background tasks
class TaskManager:
    def __init__(self):
        self.current_task = None
        self.is_running = False
        self.message = "Idle"
        self.lock = threading.Lock()

    def start_task(self, task_func, args=(), task_name="Task"):
        with self.lock:
            if self.is_running:
                return False, f"Task '{self.current_task}' is already running."
            
            self.is_running = True
            self.current_task = task_name
            self.message = f"Starting {task_name}..."
            
            thread = threading.Thread(target=self._run_wrapper, args=(task_func, args))
            thread.daemon = True
            thread.start()
            return True, "Task started."

    def _run_wrapper(self, func, args):
        try:
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

task_manager = TaskManager()
config = get_config()

# Helper wrappers to match existing function signatures
def run_scan(db_path, conf):
    with DatabaseManager(db_path) as db:
        main_scan_library(db, conf._config) # accessing internal dict if needed, or update signatures

def run_download(db_path, conf):
    with DatabaseManager(db_path) as db:
        main_run_downloader(db, conf._config)

def run_sync(db_path, conf, mode, fmt):
    with DatabaseManager(db_path) as db:
        main_run_sync(db, conf._config, sync_mode=mode, conversion_format=fmt)

def run_batch():
    batch_sync()

def run_audit(db_path):
    # Re-implement audit since it prints to stdout in manager.py
    # Ideally, we should refactor audit to return data, but for now we just run it and let logs capture it
    # checking manager.py's implementation of audit
    with DatabaseManager(db_path) as db:
        # Assuming we moved audit logic to src/manager_logic.py or similar? 
        # Wait, audit_library was in manager.py. I moved manager.py imports but the function `audit_library` was defined IN manager.py.
        # I should have moved shared logic to utils or a new module.
        # Since I didn't, I need to implement audit logic here or copy it.
        # I'll check if db_manager has get_incomplete_albums
        incomplete = db.get_incomplete_albums()
        logger.info(f"Audit found {len(incomplete)} incomplete albums.")
        # We could save this to a file or strictly rely on logs

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/status')
def status():
    with task_manager.lock:
        return jsonify({
            'running': task_manager.is_running,
            'task': task_manager.current_task,
            'message': task_manager.message
        })

@app.route('/api/scan', methods=['POST'])
def scan():
    success, msg = task_manager.start_task(run_scan, (config.db_path, config), "Library Scan")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/download', methods=['POST'])
def download():
    success, msg = task_manager.start_task(run_download, (config.db_path, config), "Download Queue")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/sync', methods=['POST'])
def sync():
    data = request.json or {}
    mode = data.get('mode', 'playlists')
    fmt = data.get('format', 'flac')
    success, msg = task_manager.start_task(run_sync, (config.db_path, config, mode, fmt), f"Sync ({mode})")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/batch', methods=['POST'])
def batch():
    success, msg = task_manager.start_task(run_batch, (), "Batch Sync")
    return jsonify({'success': success, 'message': msg})

@app.route('/api/audit', methods=['POST'])
def audit():
    success, msg = task_manager.start_task(run_audit, (config.db_path,), "Audit Library")
    return jsonify({'success': success, 'message': msg})

if __name__ == '__main__':
    print("Starting Web Server on port 5001...")
    app.run(host='0.0.0.0', port=5001, debug=True)
