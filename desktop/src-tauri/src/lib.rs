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
use std::sync::{Arc, Mutex};

use tauri::{Manager, PhysicalSize, RunEvent, State, WebviewWindow, WindowEvent};

mod seed_config;

const DEFAULT_BACKEND_PORT: u16 = 5001;

// Mini-player threshold. Matches the size we set from the JS side
// (`enterMiniPlayer` in `lib/window.ts`); the small buffer absorbs
// rounding when the OS's logical size lands a pixel off after the
// scale-factor round-trip.
const MINI_PLAYER_SIZE: u32 = 220;

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
            // Prevent Python from writing __pycache__ into the read-only
            // Contents/Resources directory when running from a bundled .app.
            .env("PYTHONDONTWRITEBYTECODE", "1")
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

/// Resolve the directory that contains `web_server.py`.
///
/// Priority:
///   1. `DAPMANAGER_ROOT` env var (explicit override, useful for CI / dev)
///   2. Tauri's resource directory — populated at build time via
///      `bundle.resources` in `tauri.conf.json`; the right answer for any
///      installed `.app` on another machine.
///   3. Dev fallback: walk up from `CARGO_MANIFEST_DIR` to the repo root.
///      This is baked in at compile time and only works on the build machine,
///      which is fine for `cargo tauri dev`.
fn resolve_project_root(resource_dir: Option<PathBuf>) -> PathBuf {
    if let Ok(explicit) = std::env::var("DAPMANAGER_ROOT") {
        return PathBuf::from(explicit);
    }

    // Packaged .app: Python sources are bundled into Contents/Resources.
    if let Some(res) = resource_dir {
        if res.join("web_server.py").exists() {
            return res;
        }
    }

    // Dev fallback: repo root is two levels above the src-tauri crate.
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

/// Create (or reuse) a venv at `venv_dir` and ensure all requirements are
/// installed. Returns the path to the venv Python binary, or falls back to
/// `system_python` if venv creation fails (e.g. Python not found on PATH).
///
/// This is called from a background thread on first launch so the Tauri event
/// loop — and the webview's "booting…" spinner — keep running during what can
/// be a multi-minute `pip install` on a fresh machine.
fn ensure_venv(project_root: &Path, venv_dir: &Path, system_python: &str) -> String {
    let python_bin = if cfg!(target_os = "windows") {
        venv_dir.join("Scripts").join("python.exe")
    } else {
        venv_dir.join("bin").join("python3")
    };

    if !python_bin.exists() {
        eprintln!(
            "DAPManager: creating venv at {} (first launch only)",
            venv_dir.display()
        );
        let status = Command::new(system_python)
            .args(["-m", "venv", venv_dir.to_str().unwrap_or("")])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status();
        if let Err(e) = status {
            eprintln!("DAPManager: venv creation failed: {e}");
            return system_python.to_string();
        }
    }

    if python_bin.exists() {
        let req = project_root.join("requirements.txt");
        if req.exists() {
            eprintln!("DAPManager: installing Python requirements (this may take a minute on first launch)…");
            let _ = Command::new(&python_bin)
                .args([
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "--disable-pip-version-check",
                    "-r",
                    req.to_str().unwrap_or("requirements.txt"),
                ])
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit())
                .status();
        }
        python_bin.to_string_lossy().into_owned()
    } else {
        eprintln!(
            "DAPManager: venv creation produced no binary, falling back to {}",
            system_python
        );
        system_python.to_string()
    }
}

#[tauri::command]
fn backend_url(state: State<Arc<BackendHandle>>) -> String {
    format!("http://127.0.0.1:{}", state.port)
}

#[derive(Debug, PartialEq, Eq)]
enum ChromeAction {
    Shrink,
    Grow,
    None,
}

// Pure decision: should the chrome change, and which way? Split out
// from the side-effect wrapper below so the threshold + scale-factor
// rounding can be unit-tested without a real WebviewWindow.
fn decide_chrome_action(
    physical_size: (u32, u32),
    scale: f64,
    is_decorated: bool,
) -> ChromeAction {
    let scale = if scale > 0.0 { scale } else { 1.0 };
    let width = (physical_size.0 as f64 / scale).round() as u32;
    let height = (physical_size.1 as f64 / scale).round() as u32;
    let in_mini = width <= MINI_PLAYER_SIZE && height <= MINI_PLAYER_SIZE;
    match (in_mini, is_decorated) {
        (true, true) => ChromeAction::Shrink,
        (false, false) => ChromeAction::Grow,
        _ => ChromeAction::None,
    }
}

// When the main window shrinks to mini-player size, drop OS chrome
// and pin it always-on-top across spaces. Reverse on grow-back.
// Hooked to `WindowEvent::Resized` so a manual resize works too —
// not just the scripted one from `enterMiniPlayer`.
fn handle_mini_player_chrome(window: &WebviewWindow, size: &PhysicalSize<u32>) {
    let scale = window.scale_factor().unwrap_or(1.0);
    let is_decorated = window.is_decorated().unwrap_or(true);
    match decide_chrome_action((size.width, size.height), scale, is_decorated) {
        ChromeAction::Shrink => {
            let _ = window.set_decorations(false);
            let _ = window.set_always_on_top(true);
            let _ = window.set_visible_on_all_workspaces(true);
        }
        ChromeAction::Grow => {
            let _ = window.set_decorations(true);
            let _ = window.set_always_on_top(false);
            let _ = window.set_visible_on_all_workspaces(false);
        }
        ChromeAction::None => {}
    }
}

#[cfg(test)]
mod chrome_tests {
    use super::{decide_chrome_action, ChromeAction, MINI_PLAYER_SIZE};

    #[test]
    fn shrinks_at_threshold_when_decorated() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 1.0, true),
            ChromeAction::Shrink,
        );
    }

    #[test]
    fn no_op_at_threshold_when_already_chromeless() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 1.0, false),
            ChromeAction::None,
        );
    }

    #[test]
    fn grows_just_above_threshold_when_chromeless() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE + 1, MINI_PLAYER_SIZE + 1), 1.0, false),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn no_op_just_above_threshold_when_decorated() {
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE + 1, MINI_PLAYER_SIZE + 1), 1.0, true),
            ChromeAction::None,
        );
    }

    #[test]
    fn does_not_shrink_when_only_one_dim_is_small() {
        // User narrowed the width but kept height tall — still a normal window.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, 800), 1.0, true),
            ChromeAction::None,
        );
    }

    #[test]
    fn hidpi_2x_threshold_in_physical_pixels() {
        // On a 2x display, 440 physical px == 220 logical px.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE * 2, MINI_PLAYER_SIZE * 2), 2.0, true),
            ChromeAction::Shrink,
        );
        // 442 physical / 2.0 = 221 logical → above threshold.
        assert_eq!(
            decide_chrome_action(
                (MINI_PLAYER_SIZE * 2 + 2, MINI_PLAYER_SIZE * 2 + 2),
                2.0,
                false,
            ),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn fractional_scale_rounds_to_nearest_logical() {
        // 1.5x scale: 330 physical → 220 logical exactly.
        assert_eq!(
            decide_chrome_action((330, 330), 1.5, true),
            ChromeAction::Shrink,
        );
        // 332 / 1.5 ≈ 221.33 → rounds to 221 → above threshold.
        assert_eq!(
            decide_chrome_action((332, 332), 1.5, false),
            ChromeAction::Grow,
        );
    }

    #[test]
    fn zero_or_negative_scale_falls_back_to_one() {
        // Pathological scale factor — don't divide by zero, treat as 1x.
        assert_eq!(
            decide_chrome_action((MINI_PLAYER_SIZE, MINI_PLAYER_SIZE), 0.0, true),
            ChromeAction::Shrink,
        );
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Wrap in Arc so the handle can be shared with the background setup thread
    // without blocking the Tauri event loop during first-launch pip install.
    let handle = Arc::new(BackendHandle::new(DEFAULT_BACKEND_PORT));

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(handle)
        .invoke_handler(tauri::generate_handler![backend_url])
        .setup(|app| {
            if let Some(main_window) = app.get_webview_window("main") {
                let window_for_handler = main_window.clone();
                main_window.on_window_event(move |event| {
                    if let WindowEvent::Resized(size) = event {
                        handle_mini_player_chrome(&window_for_handler, size);
                    }
                });
            }

            // Resolve paths while we still hold a reference to `app`.
            let maybe_resource_dir = app.path().resource_dir().ok();
            let root = resolve_project_root(maybe_resource_dir.clone());

            // Venv lives in app-data so it persists across app updates and
            // never tries to write into the read-only Contents/Resources dir.
            let venv_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| root.clone())
                .join("venv");

            let home = app.path().home_dir().ok();
            let config_path: Option<PathBuf> =
                home.as_deref().map(seed_config::platform_config_path);

            // Seed the satellite config synchronously (fast — just reads a
            // small file and writes JSON) before handing off to the thread.
            if let (Some(home_dir), Some(cfg_path)) =
                (home.as_deref(), config_path.as_deref())
            {
                if let Some(ref resource_dir) = maybe_resource_dir {
                    match seed_config::seed_satellite_config(cfg_path, resource_dir, home_dir) {
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

            // Grab a cloned Arc so the thread owns its own reference.
            let state: State<Arc<BackendHandle>> = app.state();
            let backend = Arc::clone(&state);

            // Run venv setup + spawn on a background thread.
            // The webview's "booting…" spinner will keep animating while pip
            // does its thing; waitForBackend polls until the server is up.
            std::thread::spawn(move || {
                let python = ensure_venv(&root, &venv_dir, &resolve_python());
                if let Err(e) = backend.spawn(root.clone(), python.clone(), config_path.as_deref()) {
                    eprintln!(
                        "DAPManager: failed to spawn Python backend ({} at {}): {}",
                        python,
                        root.display(),
                        e
                    );
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                let state: State<Arc<BackendHandle>> = app_handle.state();
                state.kill();
            }
        });
}
