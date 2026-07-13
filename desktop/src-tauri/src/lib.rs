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
use std::{
    io,
    net::{Ipv4Addr, TcpListener},
};

use serde::Serialize;
use serde_json::Value;
use tauri::{Emitter, Manager, PhysicalSize, RunEvent, State, WebviewWindow, WindowEvent};

mod seed_config;

const DEFAULT_BACKEND_PORT: u16 = 5001;

// Mini-player threshold. Matches the size we set from the JS side
// (`enterMiniPlayer` in `lib/window.ts`); the small buffer absorbs
// rounding when the OS's logical size lands a pixel off after the
// scale-factor round-trip.
const MINI_PLAYER_SIZE: u32 = 220;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BackendBind {
    Loopback,
    Network,
}

impl BackendBind {
    fn host(self) -> &'static str {
        match self {
            Self::Loopback => "127.0.0.1",
            Self::Network => "0.0.0.0",
        }
    }

    fn address(self) -> Ipv4Addr {
        match self {
            Self::Loopback => Ipv4Addr::LOCALHOST,
            Self::Network => Ipv4Addr::UNSPECIFIED,
        }
    }
}

fn bind_for_config_value(value: &Value) -> BackendBind {
    let is_master = value
        .get("device_role")
        .and_then(Value::as_str)
        .map(str::trim)
        .is_some_and(|role| role.eq_ignore_ascii_case("master"));
    let has_token = value
        .get("api_token")
        .and_then(Value::as_str)
        .map(str::trim)
        .is_some_and(|token| !token.is_empty());
    if is_master && has_token {
        BackendBind::Network
    } else {
        BackendBind::Loopback
    }
}

fn bind_for_config_path(config_path: Option<&Path>) -> BackendBind {
    let Some(path) = config_path else {
        return BackendBind::Loopback;
    };
    let Ok(raw) = std::fs::read_to_string(path) else {
        return BackendBind::Loopback;
    };
    let Ok(value) = serde_json::from_str::<Value>(&raw) else {
        return BackendBind::Loopback;
    };
    bind_for_config_value(&value)
}

/// A failed attempt to expose an authenticated Master may safely fall back to
/// loopback. The reverse is never allowed: a satellite, standalone device, or
/// tokenless Master must not retain an old network-wide listener.
fn safe_restart_fallback(desired: BackendBind) -> Option<BackendBind> {
    (desired == BackendBind::Network).then_some(BackendBind::Loopback)
}

#[derive(Clone)]
struct BackendLaunch {
    project_root: PathBuf,
    python: String,
    config_path: Option<PathBuf>,
}

struct BackendLifecycle {
    child: Option<Child>,
    launch: Option<BackendLaunch>,
    active_bind: Option<BackendBind>,
}

#[derive(Serialize)]
struct BackendRestartResult {
    success: bool,
    message: String,
    bind_host: String,
    backend_running: bool,
}

struct BackendHandle {
    lifecycle: Mutex<BackendLifecycle>,
    startup_error: Mutex<Option<String>>,
    port: u16,
}

impl BackendHandle {
    fn new(port: u16) -> Self {
        Self {
            lifecycle: Mutex::new(BackendLifecycle {
                child: None,
                launch: None,
                active_bind: None,
            }),
            startup_error: Mutex::new(None),
            port,
        }
    }

    /// Keep the first startup failure so a frontend that mounts after the
    /// failure can still retrieve it. Tauri events are useful for low latency,
    /// but they are not queued for a webview that has not subscribed yet.
    fn set_startup_error(&self, message: String) {
        let mut guard = self.startup_error.lock().unwrap();
        if guard.is_none() {
            *guard = Some(message);
        }
    }

    fn clear_startup_error(&self) {
        *self.startup_error.lock().unwrap() = None;
    }

    /// Return a stored startup failure, also detecting a child that spawned
    /// successfully but exited before Flask became reachable.
    fn get_startup_error(&self) -> Option<String> {
        if let Some(message) = self.startup_error.lock().unwrap().clone() {
            return Some(message);
        }

        let exit_message = {
            let mut lifecycle = self.lifecycle.lock().unwrap();
            let child_status = match lifecycle.child.as_mut() {
                Some(child) => child.try_wait(),
                None => return None,
            };
            match child_status {
                Ok(Some(status)) => {
                    lifecycle.child.take();
                    lifecycle.active_bind = None;
                    Some(format!(
                        "The Python backend exited during startup ({status}).\n\n\
                         Its dependencies may be incomplete. Relaunch DAPManager; if the problem \
                         persists, reinstall Python 3 and check that pip can install requirements.txt."
                    ))
                }
                Ok(None) => None,
                Err(error) => Some(format!(
                    "DAPManager could not inspect the Python backend process: {error}"
                )),
            }
        };

        if let Some(message) = exit_message {
            self.set_startup_error(message);
        }
        self.startup_error.lock().unwrap().clone()
    }

    fn spawn(
        &self,
        project_root: PathBuf,
        python: String,
        config_path: Option<&Path>,
    ) -> std::io::Result<()> {
        let launch = BackendLaunch {
            project_root,
            python,
            config_path: config_path.map(PathBuf::from),
        };
        let desired_bind = bind_for_config_path(launch.config_path.as_deref());
        let mut lifecycle = self.lifecycle.lock().unwrap();
        lifecycle.launch = Some(launch.clone());
        if lifecycle.child.is_some() {
            return Ok(());
        }
        self.spawn_locked(&mut lifecycle, &launch, desired_bind)
    }

    fn spawn_locked(
        &self,
        lifecycle: &mut BackendLifecycle,
        launch: &BackendLaunch,
        bind: BackendBind,
    ) -> std::io::Result<()> {
        // Repeat the early preflight immediately before spawn so a second app
        // cannot claim the port while first-launch dependency installation is
        // still running.
        check_backend_port(bind, self.port)?;
        let script = launch.project_root.join("web_server.py");
        let mut cmd = Command::new(&launch.python);
        cmd.arg(&script)
            .current_dir(&launch.project_root)
            .env("DAPMANAGER_PORT", self.port.to_string())
            // Only an authenticated Master is reachable from LAN/Tailscale.
            // Every other role stays loopback-only; the React client always
            // uses 127.0.0.1 even when Flask also listens on other interfaces.
            .env("DAPMANAGER_HOST", bind.host())
            // Werkzeug's reloader forks a second process. That duplicates
            // scheduler threads and makes the child outlive the process Tauri
            // owns, so desktop runs are always production/no-reloader mode.
            .env("DAPMANAGER_DEBUG", "0")
            // Prevent Python from writing __pycache__ into the read-only
            // Contents/Resources directory when running from a bundled .app.
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
        if let Some(p) = launch.config_path.as_deref() {
            cmd.env("DAPMANAGER_CONFIG", p);
        }
        let child = cmd.spawn()?;
        lifecycle.child = Some(child);
        lifecycle.active_bind = Some(bind);
        Ok(())
    }

    fn stop_locked(lifecycle: &mut BackendLifecycle) -> std::io::Result<()> {
        let Some(mut child) = lifecycle.child.take() else {
            lifecycle.active_bind = None;
            return Ok(());
        };
        match child.try_wait() {
            Ok(Some(_)) => {}
            Ok(None) => {
                if let Err(error) = child.kill() {
                    lifecycle.child = Some(child);
                    return Err(error);
                }
                if let Err(error) = child.wait() {
                    lifecycle.child = Some(child);
                    return Err(error);
                }
            }
            Err(error) => {
                lifecycle.child = Some(child);
                return Err(error);
            }
        }
        lifecycle.active_bind = None;
        Ok(())
    }

    fn restart(&self) -> BackendRestartResult {
        let mut lifecycle = self.lifecycle.lock().unwrap();
        let Some(launch) = lifecycle.launch.clone() else {
            return BackendRestartResult {
                success: false,
                message: "Backend restart is not ready yet; launch parameters are unavailable."
                    .to_string(),
                bind_host: lifecycle
                    .active_bind
                    .map(BackendBind::host)
                    .unwrap_or(BackendBind::Loopback.host())
                    .to_string(),
                backend_running: lifecycle.child.is_some(),
            };
        };
        let desired_bind = bind_for_config_path(launch.config_path.as_deref());

        if let Err(error) = Self::stop_locked(&mut lifecycle) {
            let message = format!(
                "Could not stop the existing Python backend safely: {error}. Relaunch DAPManager."
            );
            return BackendRestartResult {
                success: false,
                message,
                bind_host: lifecycle
                    .active_bind
                    .map(BackendBind::host)
                    .unwrap_or(BackendBind::Loopback.host())
                    .to_string(),
                backend_running: lifecycle.child.is_some(),
            };
        }

        self.clear_startup_error();
        match self.spawn_locked(&mut lifecycle, &launch, desired_bind) {
            Ok(()) => BackendRestartResult {
                success: true,
                message: match desired_bind {
                    BackendBind::Network => format!(
                        "Backend restarted with authenticated Master access on all interfaces (0.0.0.0:{}).",
                        self.port
                    ),
                    BackendBind::Loopback => format!(
                        "Backend restarted in local-only mode (127.0.0.1:{}).",
                        self.port
                    ),
                },
                bind_host: desired_bind.host().to_string(),
                backend_running: true,
            },
            Err(primary_error) => {
                if let Some(fallback_bind) = safe_restart_fallback(desired_bind) {
                    if self
                        .spawn_locked(&mut lifecycle, &launch, fallback_bind)
                        .is_ok()
                    {
                        self.clear_startup_error();
                        return BackendRestartResult {
                            success: false,
                            message: format!(
                                "Could not enable authenticated Master network access ({primary_error}). The backend was restored safely on 127.0.0.1:{}; resolve the port conflict and save the role/token again to retry.",
                                self.port
                            ),
                            bind_host: fallback_bind.host().to_string(),
                            backend_running: true,
                        };
                    }
                }

                let message = format!(
                    "Could not restart the Python backend on {}:{}: {primary_error}. Relaunch DAPManager after resolving the port/process conflict.",
                    desired_bind.host(),
                    self.port
                );
                self.set_startup_error(message.clone());
                BackendRestartResult {
                    success: false,
                    message,
                    bind_host: desired_bind.host().to_string(),
                    backend_running: false,
                }
            }
        }
    }

    fn kill(&self) {
        let mut lifecycle = self.lifecycle.lock().unwrap();
        let _ = Self::stop_locked(&mut lifecycle);
    }
}

/// Fail before installing dependencies or spawning Flask when another process
/// already owns the stable desktop port. Without this preflight, `spawn()`
/// succeeds, Flask exits asynchronously, and the frontend can accidentally
/// accept an unrelated process's `/api/healthz` response.
fn check_backend_port(bind: BackendBind, port: u16) -> io::Result<()> {
    let listener = TcpListener::bind((bind.address(), port))?;
    drop(listener);
    Ok(())
}

fn report_startup_error(
    backend: &BackendHandle,
    app_handle: &tauri::AppHandle,
    message: String,
) {
    eprintln!("DAPManager: {message}");
    backend.set_startup_error(message.clone());
    let _ = app_handle.emit("backend-error", message);
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
    if let Ok(explicit) = std::env::var("DAPMANAGER_PYTHON") {
        return explicit;
    }

    // On macOS, /usr/bin/python3 is an Xcode CLT stub. Invoked from a GUI
    // app (non-interactively) it either hangs showing an install dialog or
    // exits non-zero. Check real, known-good Python locations first so we
    // don't block the background thread indefinitely.
    #[cfg(target_os = "macos")]
    {
        let candidates = [
            "/opt/homebrew/bin/python3",   // Apple Silicon Homebrew (M1/M2/M3)
            "/usr/local/bin/python3",      // Intel Homebrew / python.org pkg
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        ];
        for p in candidates {
            if std::path::Path::new(p).exists() {
                eprintln!("DAPManager: using Python at {p}");
                return p.to_string();
            }
        }
        // Fall through to PATH lookup; if /usr/bin/python3 (the Xcode stub)
        // is the only thing on PATH the venv check below will catch the error
        // quickly rather than hanging.
    }

    "python3".to_string()
}

/// Create (or reuse) a venv at `venv_dir` and ensure all requirements are
/// installed. Returns the path to the venv Python binary; setup failures are
/// returned to the webview instead of launching a predictably broken backend.
///
/// This is called from a background thread on first launch so the Tauri event
/// loop — and the webview's "booting…" spinner — keep running during what can
/// be a multi-minute `pip install` on a fresh machine.
fn ensure_venv(
    project_root: &Path,
    venv_dir: &Path,
    system_python: &str,
) -> Result<String, String> {
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
            .args(["-m", "venv"])
            .arg(venv_dir)
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .status();
        match status {
            Ok(status) if status.success() => {}
            Ok(status) => {
                return Err(format!(
                    "Python could not create DAPManager's virtual environment ({status}).\n\n\
                     Python: {system_python}\nLocation: {}",
                    venv_dir.display()
                ));
            }
            Err(error) => {
                return Err(format!(
                    "DAPManager could not run Python to create its virtual environment: {error}\n\n\
                     Python: {system_python}\nLocation: {}",
                    venv_dir.display()
                ));
            }
        }
    }

    if !python_bin.exists() {
        return Err(format!(
            "Python reported that the virtual environment was created, but its interpreter is missing.\n\n\
             Expected: {}",
            python_bin.display()
        ));
    }

    let req = project_root.join("requirements.txt");
    if !req.exists() {
        return Err(format!(
            "The packaged Python requirements file is missing.\n\nExpected: {}",
            req.display()
        ));
    }

    eprintln!("DAPManager: installing Python requirements (this may take a minute on first launch)…");
    let status = Command::new(&python_bin)
        .args([
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            "-r",
        ])
        .arg(&req)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status();
    match status {
        Ok(status) if status.success() => Ok(python_bin.to_string_lossy().into_owned()),
        Ok(status) => Err(format!(
            "DAPManager could not install its Python dependencies ({status}).\n\n\
             Check your internet connection, then relaunch DAPManager.\nRequirements: {}",
            req.display()
        )),
        Err(error) => Err(format!(
            "DAPManager could not run pip: {error}\n\nPython: {}",
            python_bin.display()
        )),
    }
}

#[tauri::command]
fn backend_url(state: State<Arc<BackendHandle>>) -> String {
    format!("http://127.0.0.1:{}", state.port)
}

/// Race-safe startup failure channel. Unlike a one-shot event, this remains
/// readable when Python fails before the React webview has mounted.
#[tauri::command]
fn backend_startup_error(state: State<Arc<BackendHandle>>) -> Option<String> {
    state.get_startup_error()
}

/// Restart the owned Python process after a role/token change. The lifecycle
/// mutex serializes this with startup, exit, and other restart requests; the
/// saved launch parameters keep the same sources, venv, config, and port.
#[tauri::command]
fn restart_backend(state: State<Arc<BackendHandle>>) -> BackendRestartResult {
    state.restart()
}

/// Return the configured API token to this app's own webview.
///
/// The Python API is intentionally the same authenticated surface used by
/// browsers and satellites.  A bundled satellite may receive its token via
/// `master_token.txt`, so the React client cannot rely on having seen the
/// setup form.  Reading it through a Tauri command lets every localhost fetch
/// carry the bearer token without exposing a new unauthenticated HTTP route.
fn read_api_token(config_path: &Path) -> String {
    let Ok(raw) = std::fs::read_to_string(config_path) else {
        return String::new();
    };
    let Ok(value) = serde_json::from_str::<Value>(&raw) else {
        return String::new();
    };
    value
        .get("api_token")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string()
}

#[tauri::command]
fn api_token(app: tauri::AppHandle) -> String {
    app.path()
        .home_dir()
        .ok()
        .map(|home| seed_config::platform_config_path(&home))
        .map(|path| read_api_token(&path))
        .unwrap_or_default()
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
    use super::{
        bind_for_config_value, check_backend_port, decide_chrome_action,
        read_api_token, safe_restart_fallback, BackendBind, BackendHandle,
        ChromeAction, MINI_PLAYER_SIZE,
    };
    use serde_json::json;
    use std::fs;
    use std::net::{Ipv4Addr, TcpListener};

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

    #[test]
    fn reads_trimmed_api_token_from_config() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.json");
        fs::write(&path, r#"{"api_token":"  seeded-secret  "}"#).unwrap();
        assert_eq!(read_api_token(&path), "seeded-secret");
    }

    #[test]
    fn missing_or_invalid_config_has_no_api_token() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("missing.json");
        assert_eq!(read_api_token(&missing), "");
        let invalid = dir.path().join("invalid.json");
        fs::write(&invalid, "not-json").unwrap();
        assert_eq!(read_api_token(&invalid), "");
    }

    #[test]
    fn startup_error_is_persistent_and_first_failure_wins() {
        let backend = BackendHandle::new(5001);
        backend.set_startup_error("first failure".to_string());
        backend.set_startup_error("later failure".to_string());
        assert_eq!(backend.get_startup_error().as_deref(), Some("first failure"));
    }

    #[test]
    fn backend_port_probe_rejects_an_occupied_loopback_port() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(check_backend_port(BackendBind::Loopback, port).is_err());
        drop(listener);
        assert!(check_backend_port(BackendBind::Loopback, port).is_ok());
    }

    #[test]
    fn only_master_with_nonempty_token_gets_network_bind() {
        assert_eq!(
            bind_for_config_value(&json!({
                "device_role": "master",
                "api_token": "secret"
            })),
            BackendBind::Network
        );
        for value in [
            json!({"device_role": "master", "api_token": "  "}),
            json!({"device_role": "satellite", "api_token": "secret"}),
            json!({"device_role": "standalone", "api_token": "secret"}),
            json!({"is_master": true, "api_token": "secret"}),
            json!({}),
        ] {
            assert_eq!(bind_for_config_value(&value), BackendBind::Loopback);
        }
    }

    #[test]
    fn restart_fallback_can_only_reduce_network_exposure() {
        assert_eq!(
            safe_restart_fallback(BackendBind::Network),
            Some(BackendBind::Loopback)
        );
        assert_eq!(safe_restart_fallback(BackendBind::Loopback), None);
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
        .invoke_handler(tauri::generate_handler![
            backend_url,
            backend_startup_error,
            restart_backend,
            api_token
        ])
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

            // Clone AppHandle so the background thread can emit events.
            let app_handle = app.handle().clone();

            // Run venv setup + spawn on a background thread so the Tauri
            // event loop (and the webview spinner) keep running during the
            // first-launch pip install.
            //
            // On failure the thread emits "backend-error" immediately so the
            // frontend can surface the message in seconds rather than waiting
            // the full 5-minute waitForBackend timeout.
            std::thread::spawn(move || {
                let initial_bind = bind_for_config_path(config_path.as_deref());
                if let Err(error) = check_backend_port(initial_bind, backend.port) {
                    report_startup_error(
                        &backend,
                        &app_handle,
                        format!(
                            "DAPManager cannot start because {}:{} is already in use ({error}).\n\n\
                             Quit the other DAPManager/server using that port, then relaunch this app.",
                            initial_bind.host(),
                            backend.port
                        ),
                    );
                    return;
                }

                let system_python = resolve_python();

                // Quick probe: does the Python binary actually work?
                // Avoids hanging on the macOS Xcode-CLT stub that shows a
                // GUI install dialog when invoked from a sandboxed process.
                let python_ok = if system_python.starts_with('/') {
                    std::path::Path::new(&system_python).exists()
                } else {
                    std::process::Command::new(&system_python)
                        .arg("--version")
                        .output()
                        .map(|o| o.status.success())
                        .unwrap_or(false)
                };

                if !python_ok {
                    let msg = format!(
                        "Python 3 not found (tried: {system_python}).\n\n\
                         Install Python 3 and relaunch:\n\
                         • https://www.python.org/downloads/\n\
                         • or run  xcode-select --install  in Terminal"
                    );
                    report_startup_error(&backend, &app_handle, msg);
                    return;
                }

                let python = match ensure_venv(&root, &venv_dir, &system_python) {
                    Ok(python) => python,
                    Err(message) => {
                        report_startup_error(&backend, &app_handle, message);
                        return;
                    }
                };
                if let Err(e) = backend.spawn(root.clone(), python.clone(), config_path.as_deref()) {
                    let msg = format!(
                        "Failed to start Python backend.\n\nPython: {python}\nRoot: {}\nError: {e}",
                        root.display()
                    );
                    report_startup_error(&backend, &app_handle, msg);
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
