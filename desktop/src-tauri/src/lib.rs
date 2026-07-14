// DAPManager Tauri shell.
//
// The native layer owns the Python backend process and exposes the same
// localhost HTTP surface to the React webview. Domain details live in focused
// modules; `run` only wires those modules into Tauri's lifecycle.

use std::sync::Arc;

use tauri::{Manager, RunEvent, State};

mod backend;
mod commands;
mod seed_config;
mod startup;
mod window_chrome;

use backend::BackendHandle;
use commands::{api_token, backend_startup_error, backend_url, restart_backend};

const DEFAULT_BACKEND_PORT: u16 = 5001;

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
        .setup(startup::setup)
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                let state: State<Arc<BackendHandle>> = app_handle.state();
                state.kill();
            }
        });
}
