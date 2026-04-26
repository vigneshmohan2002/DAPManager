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
    let handle = BackendHandle::new(DEFAULT_BACKEND_PORT);

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
