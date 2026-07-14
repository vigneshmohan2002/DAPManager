use std::io;
use std::net::{Ipv4Addr, TcpListener};
use std::path::Path;

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum BackendBind {
    Loopback,
    Network,
}

impl BackendBind {
    pub(crate) fn host(self) -> &'static str {
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

pub(crate) fn bind_for_config_path(config_path: Option<&Path>) -> BackendBind {
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
pub(super) fn safe_restart_fallback(desired: BackendBind) -> Option<BackendBind> {
    (desired == BackendBind::Network).then_some(BackendBind::Loopback)
}

/// Fail before installing dependencies or spawning Flask when another process
/// already owns the stable desktop port. Without this preflight, `spawn()`
/// succeeds, Flask exits asynchronously, and the frontend can accidentally
/// accept an unrelated process's `/api/healthz` response.
pub(crate) fn check_backend_port(bind: BackendBind, port: u16) -> io::Result<()> {
    let listener = TcpListener::bind((bind.address(), port))?;
    drop(listener);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        bind_for_config_path, bind_for_config_value, check_backend_port, safe_restart_fallback,
        BackendBind,
    };
    use serde_json::json;
    use std::fs;
    use std::net::{Ipv4Addr, TcpListener};

    #[test]
    fn backend_port_probe_respects_ownership_and_releases_its_probe() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(check_backend_port(BackendBind::Loopback, port).is_err());

        drop(listener);
        assert!(check_backend_port(BackendBind::Loopback, port).is_ok());

        let new_owner = TcpListener::bind((Ipv4Addr::LOCALHOST, port)).unwrap();
        assert!(check_backend_port(BackendBind::Loopback, port).is_err());
        drop(new_owner);
    }

    #[test]
    fn only_master_with_nonempty_token_gets_network_bind() {
        assert_eq!(
            bind_for_config_value(&json!({
                "device_role": "  MASTER  ",
                "api_token": "  secret  "
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
    fn config_path_defaults_to_loopback_until_an_authenticated_master_is_valid() {
        let dir = tempfile::tempdir().unwrap();
        let missing = dir.path().join("missing.json");
        assert_eq!(bind_for_config_path(None), BackendBind::Loopback);
        assert_eq!(bind_for_config_path(Some(&missing)), BackendBind::Loopback);

        let config = dir.path().join("config.json");
        fs::write(&config, "not-json").unwrap();
        assert_eq!(bind_for_config_path(Some(&config)), BackendBind::Loopback);

        fs::write(&config, r#"{"device_role":"master","api_token":"secret"}"#).unwrap();
        assert_eq!(bind_for_config_path(Some(&config)), BackendBind::Network);

        fs::write(
            &config,
            r#"{"device_role":"satellite","api_token":"secret"}"#,
        )
        .unwrap();
        assert_eq!(bind_for_config_path(Some(&config)), BackendBind::Loopback);
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
