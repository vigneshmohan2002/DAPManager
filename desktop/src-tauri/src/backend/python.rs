use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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
pub(crate) fn resolve_project_root(resource_dir: Option<PathBuf>) -> PathBuf {
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

pub(crate) fn resolve_python() -> String {
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
            "/opt/homebrew/bin/python3", // Apple Silicon Homebrew (M1/M2/M3)
            "/usr/local/bin/python3",    // Intel Homebrew / python.org pkg
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

/// Quick probe that the configured interpreter is usable. Absolute paths are
/// checked directly to avoid invoking macOS's Xcode command-line-tools stub.
pub(crate) fn python_is_available(system_python: &str) -> bool {
    if system_python.starts_with('/') {
        std::path::Path::new(system_python).exists()
    } else {
        Command::new(system_python)
            .arg("--version")
            .output()
            .map(|output| output.status.success())
            .unwrap_or(false)
    }
}

/// Create (or reuse) a venv at `venv_dir` and ensure all requirements are
/// installed. Returns the path to the venv Python binary; setup failures are
/// returned to the webview instead of launching a predictably broken backend.
///
/// This is called from a background thread on first launch so the Tauri event
/// loop — and the webview's "booting…" spinner — keep running during what can
/// be a multi-minute `pip install` on a fresh machine.
pub(crate) fn ensure_venv(
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

    let requirements = project_root.join("requirements.txt");
    if !requirements.exists() {
        return Err(format!(
            "The packaged Python requirements file is missing.\n\nExpected: {}",
            requirements.display()
        ));
    }

    eprintln!(
        "DAPManager: installing Python requirements (this may take a minute on first launch)…"
    );
    let status = Command::new(&python_bin)
        .args([
            "-m",
            "pip",
            "install",
            "-q",
            "--disable-pip-version-check",
            "-r",
        ])
        .arg(&requirements)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .status();
    match status {
        Ok(status) if status.success() => Ok(python_bin.to_string_lossy().into_owned()),
        Ok(status) => Err(format!(
            "DAPManager could not install its Python dependencies ({status}).\n\n\
             Check your internet connection, then relaunch DAPManager.\nRequirements: {}",
            requirements.display()
        )),
        Err(error) => Err(format!(
            "DAPManager could not run pip: {error}\n\nPython: {}",
            python_bin.display()
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::python_is_available;

    #[test]
    fn missing_absolute_python_is_unavailable_without_spawning() {
        assert!(!python_is_available(
            "/path/that/does/not/exist/dapmanager-python"
        ));
    }
}
