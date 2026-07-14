use std::path::PathBuf;
use std::sync::Arc;

use tauri::{Emitter, Manager, State, WindowEvent};

use crate::backend::python::{
    ensure_venv, python_is_available, resolve_project_root, resolve_python,
};
use crate::backend::{bind_for_config_path, check_backend_port, BackendHandle};
use crate::seed_config;
use crate::window_chrome::handle_mini_player_chrome;

pub(crate) fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    attach_window_chrome(app);

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
    let config_path: Option<PathBuf> = home.as_deref().map(seed_config::platform_config_path);

    seed_satellite_config(
        home.as_deref(),
        config_path.as_deref(),
        maybe_resource_dir.as_deref(),
    );

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
        start_backend(backend, app_handle, root, venv_dir, config_path);
    });

    Ok(())
}

fn attach_window_chrome(app: &tauri::App) {
    if let Some(main_window) = app.get_webview_window("main") {
        let window_for_handler = main_window.clone();
        main_window.on_window_event(move |event| {
            if let WindowEvent::Resized(size) = event {
                handle_mini_player_chrome(&window_for_handler, size);
            }
        });
    }
}

fn seed_satellite_config(
    home_dir: Option<&std::path::Path>,
    config_path: Option<&std::path::Path>,
    resource_dir: Option<&std::path::Path>,
) {
    // Seed the satellite config synchronously (fast — just reads a small file
    // and writes JSON) before handing off to the backend thread.
    let (Some(home_dir), Some(config_path), Some(resource_dir)) =
        (home_dir, config_path, resource_dir)
    else {
        return;
    };

    match seed_config::seed_satellite_config(config_path, resource_dir, home_dir) {
        Ok(seed_config::SeedOutcome::Seeded {
            master_url,
            has_token,
        }) => {
            eprintln!(
                "DAPManager: seeded satellite config at {} (master={}, token={})",
                config_path.display(),
                master_url,
                if has_token { "yes" } else { "no" }
            );
        }
        Ok(_) => {}
        Err(error) => eprintln!(
            "DAPManager: seed_satellite_config failed at {}: {}",
            config_path.display(),
            error
        ),
    }
}

fn start_backend(
    backend: Arc<BackendHandle>,
    app_handle: tauri::AppHandle,
    root: PathBuf,
    venv_dir: PathBuf,
    config_path: Option<PathBuf>,
) {
    let initial_bind = bind_for_config_path(config_path.as_deref());
    if let Err(error) = check_backend_port(initial_bind, backend.port()) {
        report_startup_error(
            &backend,
            &app_handle,
            format!(
                "DAPManager cannot start because {}:{} is already in use ({error}).\n\n\
                 Quit the other DAPManager/server using that port, then relaunch this app.",
                initial_bind.host(),
                backend.port()
            ),
        );
        return;
    }

    let system_python = resolve_python();
    if !python_is_available(&system_python) {
        let message = format!(
            "Python 3 not found (tried: {system_python}).\n\n\
             Install Python 3 and relaunch:\n\
             • https://www.python.org/downloads/\n\
             • or run  xcode-select --install  in Terminal"
        );
        report_startup_error(&backend, &app_handle, message);
        return;
    }

    let python = match ensure_venv(&root, &venv_dir, &system_python) {
        Ok(python) => python,
        Err(message) => {
            report_startup_error(&backend, &app_handle, message);
            return;
        }
    };
    if let Err(error) = backend.spawn(root.clone(), python.clone(), config_path.as_deref()) {
        let message = format!(
            "Failed to start Python backend.\n\nPython: {python}\nRoot: {}\nError: {error}",
            root.display()
        );
        report_startup_error(&backend, &app_handle, message);
    }
}

fn report_startup_error(backend: &BackendHandle, app_handle: &tauri::AppHandle, message: String) {
    eprintln!("DAPManager: {message}");
    backend.set_startup_error(message.clone());
    let _ = app_handle.emit("backend-error", message);
}
