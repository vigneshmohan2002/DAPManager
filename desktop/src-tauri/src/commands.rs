use std::path::Path;
use std::sync::Arc;

use serde_json::Value;
use tauri::{Manager, State};

use crate::backend::{BackendHandle, BackendRestartResult};
use crate::seed_config;

#[tauri::command]
pub(crate) fn backend_url(state: State<Arc<BackendHandle>>) -> String {
    format!("http://127.0.0.1:{}", state.port())
}

/// Race-safe startup failure channel. Unlike a one-shot event, this remains
/// readable when Python fails before the React webview has mounted.
#[tauri::command]
pub(crate) fn backend_startup_error(state: State<Arc<BackendHandle>>) -> Option<String> {
    state.get_startup_error()
}

/// Restart the owned Python process after a role/token change. The lifecycle
/// mutex serializes this with startup, exit, and other restart requests; the
/// saved launch parameters keep the same sources, venv, config, and port.
#[tauri::command]
pub(crate) fn restart_backend(state: State<Arc<BackendHandle>>) -> BackendRestartResult {
    state.restart()
}

/// Return the configured API token to this app's own webview.
///
/// The Python API is intentionally the same authenticated surface used by
/// browsers and satellites. A bundled satellite may receive its token via
/// `master_token.txt`, so the React client cannot rely on having seen the
/// setup form. Reading it through a Tauri command lets every localhost fetch
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
pub(crate) fn api_token(app: tauri::AppHandle) -> String {
    app.path()
        .home_dir()
        .ok()
        .map(|home| seed_config::platform_config_path(&home))
        .map(|path| read_api_token(&path))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::read_api_token;
    use std::fs;

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

        for raw in [
            "not-json",
            r#"{}"#,
            r#"{"api_token":null}"#,
            r#"{"api_token":123}"#,
            r#"{"api_token":{"value":"nested-secret"}}"#,
        ] {
            fs::write(&missing, raw).unwrap();
            assert_eq!(read_api_token(&missing), "", "config was {raw}");
        }
    }
}
