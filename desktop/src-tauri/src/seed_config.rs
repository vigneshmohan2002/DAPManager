//! First-launch satellite config seeding.
//!
//! When the master serves /download/mac, it injects two text files
//! into `DAPManager.app/Contents/Resources/`:
//!   - `master_url.txt` — the master's public URL
//!   - `master_token.txt` — bearer token (only when the master has one)
//!
//! On Tauri startup (before the Python backend is spawned), this
//! module checks whether config.json already exists at the platform
//! config path. If not, and the resource files are present, it writes
//! a fully-shaped satellite config so the Python side boots cleanly
//! and the React Settings screen lands with master_url pre-filled.
//!
//! The seed is one-shot: once config.json exists, the resource files
//! are ignored on every subsequent launch. Edits made from the
//! Settings screen are the source of truth from then on.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

const RES_URL: &str = "master_url.txt";
const RES_TOKEN: &str = "master_token.txt";

/// Result of a seed attempt — useful so the caller can log it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SeedOutcome {
    /// `config.json` already existed; we left it alone.
    AlreadyConfigured,
    /// No `master_url.txt` in the resource dir; nothing to seed.
    NoBundleConfig,
    /// Wrote a fresh satellite config from the bundle resources.
    Seeded { master_url: String, has_token: bool },
}

/// Look in `resource_dir` for `master_url.txt` (+ optional
/// `master_token.txt`) and, if present, write a satellite-shaped
/// config to `config_path`. Existing configs are never overwritten.
pub fn seed_satellite_config(
    config_path: &Path,
    resource_dir: &Path,
    home_dir: &Path,
) -> io::Result<SeedOutcome> {
    if config_path.exists() {
        return Ok(SeedOutcome::AlreadyConfigured);
    }

    let url_path = resource_dir.join(RES_URL);
    if !url_path.exists() {
        return Ok(SeedOutcome::NoBundleConfig);
    }

    let master_url = fs::read_to_string(&url_path)?
        .trim()
        .trim_end_matches('/')
        .to_string();
    if master_url.is_empty() {
        return Ok(SeedOutcome::NoBundleConfig);
    }

    let token_path = resource_dir.join(RES_TOKEN);
    let api_token = if token_path.exists() {
        fs::read_to_string(&token_path)?.trim().to_string()
    } else {
        String::new()
    };

    let cfg = build_satellite_config(&master_url, &api_token, home_dir);

    if let Some(parent) = config_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(
        config_path,
        serde_json::to_string_pretty(&cfg).expect("config serialises"),
    )?;

    Ok(SeedOutcome::Seeded {
        master_url,
        has_token: !api_token.is_empty(),
    })
}

fn build_satellite_config(master_url: &str, api_token: &str, home_dir: &Path) -> Value {
    let music = home_dir.join("Music").join("DAPManager");
    let downloads = home_dir.join("Downloads").join("DAPManager");
    let db = home_dir
        .join("Library")
        .join("Application Support")
        .join("DAPManager")
        .join("dap_library.db");

    let mut cfg = json!({
        "database_file": db.to_string_lossy(),
        "music_library_path": music.to_string_lossy(),
        "downloads_path": downloads.to_string_lossy(),
        "ffmpeg_path": "ffmpeg",
        "slsk_cmd_base": ["slsk-batchdl"],
        "dap_mount_point": "",
        "dap_music_dir_name": "Music",
        "dap_playlist_dir_name": "Playlists",
        "conversion_sample_rate": 44100,
        "conversion_bit_depth": 16,
        "is_master": false,
        "device_role": "satellite",
        "slsk_username": "",
        "slsk_password": "",
        "master_url": master_url,
        // SuggestScreen reads dap_manager_host_url; until that field
        // is unified with master_url in a later cleanup, mirror the
        // value so the satellite UI works without Settings edits.
        "dap_manager_host_url": master_url,
        "report_inventory_to_host": false,
    });
    if !api_token.is_empty() {
        cfg["api_token"] = Value::String(api_token.to_string());
    }
    cfg
}

/// Platform config path mirroring `src/config_paths.py`.
/// macOS: `~/Library/Application Support/DAPManager/config.json`
/// Windows: `%APPDATA%\DAPManager\config.json`
/// Linux/other: `$XDG_CONFIG_HOME/dapmanager/config.json` or
/// `~/.config/dapmanager/config.json`.
pub fn platform_config_path(home_dir: &Path) -> PathBuf {
    if cfg!(target_os = "macos") {
        home_dir
            .join("Library")
            .join("Application Support")
            .join("DAPManager")
            .join("config.json")
    } else if cfg!(target_os = "windows") {
        let base = std::env::var_os("APPDATA")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir.to_path_buf());
        base.join("DAPManager").join("config.json")
    } else {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir.join(".config"));
        base.join("dapmanager").join("config.json")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn write(p: &Path, contents: &str) {
        if let Some(parent) = p.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(p, contents).unwrap();
    }

    #[test]
    fn skips_when_config_already_exists() {
        let dir = tempdir().unwrap();
        let cfg = dir.path().join("config.json");
        write(&cfg, "{\"existing\": true}");
        let res = dir.path().join("res");
        fs::create_dir_all(&res).unwrap();
        write(&res.join("master_url.txt"), "http://m:5001");

        let outcome =
            seed_satellite_config(&cfg, &res, dir.path()).unwrap();
        assert_eq!(outcome, SeedOutcome::AlreadyConfigured);
        // Existing file untouched.
        assert_eq!(fs::read_to_string(&cfg).unwrap(), "{\"existing\": true}");
    }

    #[test]
    fn no_op_when_resource_file_absent() {
        let dir = tempdir().unwrap();
        let cfg = dir.path().join("DAPManager").join("config.json");
        let res = dir.path().join("res");
        fs::create_dir_all(&res).unwrap();

        let outcome =
            seed_satellite_config(&cfg, &res, dir.path()).unwrap();
        assert_eq!(outcome, SeedOutcome::NoBundleConfig);
        assert!(!cfg.exists());
    }

    #[test]
    fn seeds_with_url_only() {
        let dir = tempdir().unwrap();
        let cfg = dir.path().join("DAPManager").join("config.json");
        let res = dir.path().join("res");
        fs::create_dir_all(&res).unwrap();
        write(&res.join("master_url.txt"), "http://master.tail.ts.net:5001\n");

        let outcome =
            seed_satellite_config(&cfg, &res, dir.path()).unwrap();
        assert_eq!(
            outcome,
            SeedOutcome::Seeded {
                master_url: "http://master.tail.ts.net:5001".to_string(),
                has_token: false,
            }
        );

        let written: Value =
            serde_json::from_str(&fs::read_to_string(&cfg).unwrap()).unwrap();
        assert_eq!(written["master_url"], "http://master.tail.ts.net:5001");
        assert_eq!(
            written["dap_manager_host_url"],
            "http://master.tail.ts.net:5001"
        );
        assert_eq!(written["device_role"], "satellite");
        assert_eq!(written["is_master"], false);
        assert!(written.get("api_token").is_none());
    }

    #[test]
    fn seeds_with_url_and_token() {
        let dir = tempdir().unwrap();
        let cfg = dir.path().join("DAPManager").join("config.json");
        let res = dir.path().join("res");
        fs::create_dir_all(&res).unwrap();
        write(&res.join("master_url.txt"), "http://m:5001/");
        write(&res.join("master_token.txt"), "abc123\n");

        let outcome =
            seed_satellite_config(&cfg, &res, dir.path()).unwrap();
        assert_eq!(
            outcome,
            SeedOutcome::Seeded {
                master_url: "http://m:5001".to_string(),
                has_token: true,
            }
        );
        let written: Value =
            serde_json::from_str(&fs::read_to_string(&cfg).unwrap()).unwrap();
        assert_eq!(written["api_token"], "abc123");
        assert_eq!(written["master_url"], "http://m:5001");
    }

    #[test]
    fn empty_url_file_is_treated_as_absent() {
        let dir = tempdir().unwrap();
        let cfg = dir.path().join("DAPManager").join("config.json");
        let res = dir.path().join("res");
        fs::create_dir_all(&res).unwrap();
        write(&res.join("master_url.txt"), "   \n");

        let outcome =
            seed_satellite_config(&cfg, &res, dir.path()).unwrap();
        assert_eq!(outcome, SeedOutcome::NoBundleConfig);
        assert!(!cfg.exists());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn platform_config_path_macos_layout() {
        let home = Path::new("/Users/test");
        let p = platform_config_path(home);
        assert_eq!(
            p,
            Path::new("/Users/test/Library/Application Support/DAPManager/config.json")
        );
    }
}
