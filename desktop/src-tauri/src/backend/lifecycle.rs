use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;

use super::bind::{bind_for_config_path, check_backend_port, safe_restart_fallback, BackendBind};

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
pub(crate) struct BackendRestartResult {
    success: bool,
    message: String,
    bind_host: String,
    backend_running: bool,
}

pub(crate) struct BackendHandle {
    lifecycle: Mutex<BackendLifecycle>,
    startup_error: Mutex<Option<String>>,
    port: u16,
}

impl BackendHandle {
    pub(crate) fn new(port: u16) -> Self {
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

    pub(crate) fn port(&self) -> u16 {
        self.port
    }

    /// Keep the first startup failure so a frontend that mounts after the
    /// failure can still retrieve it. Tauri events are useful for low latency,
    /// but they are not queued for a webview that has not subscribed yet.
    pub(crate) fn set_startup_error(&self, message: String) {
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
    pub(crate) fn get_startup_error(&self) -> Option<String> {
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

    pub(crate) fn spawn(
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

    pub(crate) fn restart(&self) -> BackendRestartResult {
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

    pub(crate) fn kill(&self) {
        let mut lifecycle = self.lifecycle.lock().unwrap();
        let _ = Self::stop_locked(&mut lifecycle);
    }
}

#[cfg(test)]
mod tests {
    use super::{BackendHandle, BackendLaunch, BackendLifecycle, BackendRestartResult};
    use crate::backend::bind::BackendBind;
    use serde_json::json;
    use std::fs;

    #[test]
    fn restart_result_wire_shape_stays_stable() {
        let result = BackendRestartResult {
            success: false,
            message: "restart failed".to_string(),
            bind_host: "127.0.0.1".to_string(),
            backend_running: true,
        };

        assert_eq!(
            serde_json::to_value(result).unwrap(),
            json!({
                "success": false,
                "message": "restart failed",
                "bind_host": "127.0.0.1",
                "backend_running": true,
            })
        );
    }

    #[test]
    fn startup_error_is_persistent_and_first_failure_wins() {
        let backend = BackendHandle::new(5001);
        assert_eq!(backend.get_startup_error(), None);
        backend.set_startup_error("first failure".to_string());
        backend.set_startup_error("later failure".to_string());
        assert_eq!(
            backend.get_startup_error().as_deref(),
            Some("first failure")
        );

        backend.clear_startup_error();
        assert_eq!(backend.get_startup_error(), None);

        backend.set_startup_error("new failure".to_string());
        assert_eq!(backend.get_startup_error().as_deref(), Some("new failure"));
    }

    #[test]
    fn restart_without_launch_parameters_is_non_destructive() {
        let backend = BackendHandle::new(5123);
        backend.set_startup_error("startup failed".to_string());

        let result = backend.restart();

        assert!(!result.success);
        assert_eq!(
            result.message,
            "Backend restart is not ready yet; launch parameters are unavailable."
        );
        assert_eq!(result.bind_host, "127.0.0.1");
        assert!(!result.backend_running);
        assert_eq!(
            backend.get_startup_error().as_deref(),
            Some("startup failed")
        );
    }

    #[test]
    fn stopping_without_a_child_clears_stale_bind_state() {
        let mut lifecycle = BackendLifecycle {
            child: None,
            launch: None,
            active_bind: Some(BackendBind::Network),
        };

        BackendHandle::stop_locked(&mut lifecycle).unwrap();

        assert!(lifecycle.child.is_none());
        assert!(lifecycle.launch.is_none());
        assert_eq!(lifecycle.active_bind, None);
    }

    #[test]
    fn failed_master_restart_exhausts_fallback_without_claiming_a_child() {
        let dir = tempfile::tempdir().unwrap();
        let config = dir.path().join("config.json");
        fs::write(&config, r#"{"device_role":"master","api_token":"secret"}"#).unwrap();
        let missing_python = dir.path().join("missing-python");
        let backend = BackendHandle::new(0);
        {
            let mut lifecycle = backend.lifecycle.lock().unwrap();
            lifecycle.launch = Some(BackendLaunch {
                project_root: dir.path().to_path_buf(),
                python: missing_python.to_string_lossy().into_owned(),
                config_path: Some(config),
            });
        }

        let result = backend.restart();

        assert!(!result.success);
        assert_eq!(result.bind_host, "0.0.0.0");
        assert!(!result.backend_running);
        assert!(result
            .message
            .contains("Could not restart the Python backend on 0.0.0.0:0"));
        assert_eq!(
            backend.get_startup_error().as_deref(),
            Some(result.message.as_str())
        );
        let lifecycle = backend.lifecycle.lock().unwrap();
        assert!(lifecycle.child.is_none());
        assert_eq!(lifecycle.active_bind, None);
    }
}
