// DAPManager Tauri shell.
//
// Responsibilities (Stage 1):
//   1. Launch the existing Python Flask backend as a child process.
//   2. Expose its base URL to the frontend via a Tauri command.
//   3. Kill the backend cleanly when the window closes.
//
// The webview talks to the backend over HTTP on localhost — same
// surface the browser already uses — so no serde contracts between
// Rust and Python are needed. Rust just babysits the process.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};

mod seed_config;

const DEFAULT_BACKEND_PORT: u16 = 5001;

struct BackendHandle {
    child: Mutex<Option<Child>>,
    port: u16,
}

impl BackendHandle {
    fn new(port: u16) -> Self {
        Self {
            child: Mutex::new(None),
            port,
        }
    }

    fn spawn(
        &self,
        project_root: PathBuf,
        python: String,
        config_path: Option<&Path>,
    ) -> std::io::Result<()> {
        let mut guard = self.child.lock().unwrap();
        if guard.is_some() {
            return Ok(());
        }
        let script = project_root.join("web_server.py");
        let mut cmd = Command::new(&python);
        cmd.arg(&script)
            .current_dir(&project_root)
            .env("DAPMANAGER_PORT", self.port.to_string())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
        if let Some(p) = config_path {
            cmd.env("DAPMANAGER_CONFIG", p);
        }
        let child = cmd.spawn()?;
        *guard = Some(child);
        Ok(())
    }

    fn kill(&self) {
        let mut guard = self.child.lock().unwrap();
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn resolve_project_root() -> PathBuf {
    // Dev/run-from-source: walk up from the src-tauri crate dir to the
    // repo root (which contains web_server.py). An explicit env var
    // overrides, so packaged builds can point anywhere.
    if let Ok(explicit) = std::env::var("DAPMANAGER_ROOT") {
        return PathBuf::from(explicit);
    }
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from)
        .unwrap_or(manifest_dir)
}

fn resolve_python() -> String {
    std::env::var("DAPMANAGER_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

#[tauri::command]
fn backend_url(state: State<BackendHandle>) -> String {
    format!("http://127.0.0.1:{}", state.port)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let handle = BackendHandle::new(DEFAULT_BACKEND_PORT);

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(handle)
        .invoke_handler(tauri::generate_handler![backend_url])
        .setup(|app| {
            let state: State<BackendHandle> = app.state();
            let root = resolve_project_root();
            let python = resolve_python();

            let home = app.path().home_dir().ok();
            let config_path = home
                .as_deref()
                .map(seed_config::platform_config_path);

            if let (Some(home_dir), Some(cfg_path)) = (home.as_deref(), config_path.as_deref()) {
                if let Ok(resource_dir) = app.path().resource_dir() {
                    match seed_config::seed_satellite_config(
                        cfg_path,
                        &resource_dir,
                        home_dir,
                    ) {
                        Ok(seed_config::SeedOutcome::Seeded { master_url, has_token }) => {
                            eprintln!(
                                "DAPManager: seeded satellite config at {} (master={}, token={})",
                                cfg_path.display(),
                                master_url,
                                if has_token { "yes" } else { "no" }
                            );
                        }
                        Ok(_) => {}
                        Err(e) => eprintln!(
                            "DAPManager: seed_satellite_config failed at {}: {}",
                            cfg_path.display(),
                            e
                        ),
                    }
                }
            }

            if let Err(e) = state.spawn(
                root.clone(),
                python.clone(),
                config_path.as_deref(),
            ) {
                eprintln!(
                    "DAPManager: failed to spawn Python backend ({} at {}): {}",
                    python,
                    root.display(),
                    e
                );
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                let state: State<BackendHandle> = app_handle.state();
                state.kill();
            }
        });
}
