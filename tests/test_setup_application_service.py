"""Focused policy tests for setup and satellite application services."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from src.services import setup_application_service as service


def test_bundle_token_uses_stable_message_and_injected_hmac():
    signed = []

    def signer(secret, message):
        signed.append((secret, message))
        return "signature"

    token = service.bundle_download_token("api-secret", 1234, signer=signer)

    assert token == "1234.signature"
    assert signed == [("api-secret", b"dapmanager-bundle:1234")]


def test_bundle_token_validation_preserves_expiry_boundary_and_compare():
    token = service.bundle_download_token("secret", 100)
    comparisons = []

    def compare(left, right):
        comparisons.append((left, right))
        return left == right

    assert service.valid_bundle_download_token(
        token,
        "secret",
        clock=lambda: 100.9,
        compare_digest=compare,
    )
    assert comparisons == [(token, token)]
    assert not service.valid_bundle_download_token(
        token,
        "secret",
        clock=lambda: 101,
        compare_digest=compare,
    )
    assert not service.valid_bundle_download_token(
        "not-a-token",
        "secret",
        clock=lambda: 1,
    )


def test_bundle_link_requires_public_url():
    result = service.build_satellite_bundle_link({})

    assert result.status_code == 409
    assert result.payload == {
        "success": False,
        "message": "public_master_url is not configured",
    }


def test_bundle_link_normalizes_url_and_mints_one_hour_token():
    result = service.build_satellite_bundle_link(
        {
            "public_master_url": "  http://master:5001///  ",
            "api_token": " secret ",
        },
        clock=lambda: 100.9,
    )

    expected_token = service.bundle_download_token("secret", 3700)
    assert result.status_code == 200
    assert result.payload == {
        "success": True,
        "url": (
            "http://master:5001/download/mac"
            f"?bundle_token={expected_token}"
        ),
        "expires_at": 3700,
    }
    assert "secret" not in result.payload["url"]


def test_bundle_link_without_api_token_has_no_expiry():
    result = service.build_satellite_bundle_link(
        {"public_master_url": "http://master:5001/"},
        clock=lambda: pytest.fail("clock should not be read in open mode"),
    )

    assert result.payload == {
        "success": True,
        "url": "http://master:5001/download/mac",
        "expires_at": None,
    }


def test_bundle_download_preserves_auth_precedence_and_binary_response(tmp_path):
    base_path = tmp_path / "base.zip"
    calls = []

    def fetch():
        calls.append(("fetch",))
        return base_path

    def inject(path, url, token):
        calls.append(("inject", path, url, token))
        return b"bundle-body"

    result = service.prepare_satellite_bundle_download(
        {
            "public_master_url": " http://master:5001/ ",
            "api_token": " secret ",
        },
        authorization_header="Bearer secret",
        query_token="wrong-query-token",
        cookie_token="wrong-cookie-token",
        clock=lambda: pytest.fail("scoped token should not be checked"),
        fetch_bundle=fetch,
        inject_bundle=inject,
    )

    assert result.status_code == 200
    assert result.body == b"bundle-body"
    assert result.payload is None
    assert result.mimetype == "application/zip"
    assert result.headers == {
        "Content-Disposition": (
            'attachment; filename="DAPManager-mac.zip"'
        ),
        "Content-Length": "11",
        "Cache-Control": "no-store",
    }
    assert calls == [
        ("fetch",),
        ("inject", base_path, "http://master:5001", "secret"),
    ]


@pytest.mark.parametrize(
    ("credentials", "expected_status"),
    [
        ({"query_token": "secret"}, 200),
        ({"cookie_token": "secret"}, 200),
        (
            {
                "authorization_header": "Basic ignored",
                "query_token": "secret",
            },
            200,
        ),
        (
            {
                "authorization_header": "Bearer wrong",
                "query_token": "secret",
            },
            401,
        ),
    ],
)
def test_bundle_download_preserves_direct_token_sources(
    credentials,
    expected_status,
):
    result = service.prepare_satellite_bundle_download(
        {"public_master_url": "http://master", "api_token": "secret"},
        fetch_bundle=lambda: "/bundle.zip",
        inject_bundle=lambda *_args: b"zip",
        **credentials,
    )

    assert result.status_code == expected_status


def test_bundle_download_accepts_scoped_token_without_api_secret_in_url():
    scoped = service.bundle_download_token("secret", 101)
    result = service.prepare_satellite_bundle_download(
        {"public_master_url": "http://master", "api_token": "secret"},
        bundle_token=scoped,
        clock=lambda: 100,
        fetch_bundle=lambda: "/bundle.zip",
        inject_bundle=lambda _path, _url, token: token.encode(),
    )

    assert result.status_code == 200
    assert result.body == b"secret"


def test_bundle_download_rejects_before_fetching_when_auth_is_invalid():
    result = service.prepare_satellite_bundle_download(
        {"public_master_url": "http://master", "api_token": "secret"},
        fetch_bundle=lambda: pytest.fail("bundle must not be fetched"),
    )

    assert result.status_code == 401
    assert result.payload == {
        "success": False,
        "message": "missing or invalid api token",
    }


def test_bundle_download_preserves_public_url_and_fetch_failure_policies():
    missing_url = service.prepare_satellite_bundle_download({})
    assert missing_url.status_code == 409
    assert "public_master_url is not set" in missing_url.payload["message"]

    class FetchError(RuntimeError):
        pass

    def failed_fetch():
        raise FetchError("offline")

    failed = service.prepare_satellite_bundle_download(
        {"public_master_url": "http://master"},
        fetch_bundle=failed_fetch,
        bundle_fetch_error=FetchError,
    )
    assert failed.status_code == 502
    assert failed.payload == {
        "success": False,
        "message": (
            "Could not fetch the satellite bundle from GitHub. "
            "Check the master's outbound connectivity."
        ),
    }


def test_bundle_download_turns_injection_failure_into_json_500():
    def failed_injection(*_args):
        raise ValueError("broken archive")

    result = service.prepare_satellite_bundle_download(
        {"public_master_url": "http://master"},
        fetch_bundle=lambda: "/bundle.zip",
        inject_bundle=failed_injection,
    )

    assert result.status_code == 500
    assert result.payload == {
        "success": False,
        "message": "broken archive",
    }


def test_detect_public_url_prefers_environment_without_running_tailscale():
    result = service.detect_public_url(
        environment={"MASTER_PUBLIC_URL": " http://from-env:5001 "},
        find_binary=lambda _name: pytest.fail("lookup must not run"),
        run_process=lambda *_args, **_kwargs: pytest.fail(
            "process must not run"
        ),
    )

    assert result.payload == {
        "source": "env",
        "url": "http://from-env:5001",
    }


def test_detect_public_url_uses_tailscale_dns_and_configured_port():
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "Self": {
                    "DNSName": "master.tailnet.ts.net.",
                    "TailscaleIPs": ["100.1.2.3"],
                }
            }),
        )

    result = service.detect_public_url(
        environment={"MASTER_PORT": " 6001 "},
        find_binary=lambda name: "/usr/bin/tailscale" if name == "tailscale" else None,
        run_process=run,
    )

    assert result.payload == {
        "source": "tailscale",
        "url": "http://master.tailnet.ts.net:6001",
    }
    assert calls == [
        (
            ["/usr/bin/tailscale", "status", "--json"],
            {"capture_output": True, "text": True, "timeout": 2},
        )
    ]


def test_detect_public_url_falls_back_to_first_ipv4_address():
    result = service.detect_public_url(
        environment={},
        find_binary=lambda _name: "tailscale",
        run_process=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "Self": {
                    "TailscaleIPs": ["fd7a:115c::1", "100.64.0.8"],
                }
            }),
        ),
    )

    assert result.payload == {
        "source": "tailscale",
        "url": "http://100.64.0.8:5001",
    }


@pytest.mark.parametrize(
    "find_binary,run_process",
    [
        (lambda _name: None, None),
        (
            lambda _name: "tailscale",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="{}",
            ),
        ),
        (
            lambda _name: "tailscale",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout="not-json",
            ),
        ),
    ],
)
def test_detect_public_url_preserves_none_fallbacks(
    find_binary,
    run_process,
):
    result = service.detect_public_url(
        environment={},
        find_binary=find_binary,
        run_process=run_process,
    )

    assert result.payload == {"source": "none"}


def test_detect_public_url_handles_tailscale_timeout():
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("tailscale", 2)

    result = service.detect_public_url(
        environment={},
        find_binary=lambda _name: "tailscale",
        run_process=timeout,
    )

    assert result.payload == {"source": "none"}


def test_install_slsk_persists_command_then_reinitializes(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"device_role": "master"}))
    events = []

    def install(releases_dir, bin_dir):
        events.append(("install", releases_dir, bin_dir))
        return "/installed/slsk-batchdl"

    def reinitialize():
        events.append(("reinitialize",))
        persisted = json.loads(config_path.read_text())
        assert persisted["slsk_cmd_base"] == ["/installed/slsk-batchdl"]

    result = service.install_slsk(
        base_dir=str(tmp_path),
        config_path=str(config_path),
        config_is_present=lambda: True,
        reinitialize_runtime=reinitialize,
        install_binary=install,
    )

    assert result.payload == {
        "success": True,
        "path": "/installed/slsk-batchdl",
    }
    assert json.loads(config_path.read_text()) == {
        "device_role": "master",
        "slsk_cmd_base": ["/installed/slsk-batchdl"],
    }
    assert events == [
        (
            "install",
            str(tmp_path / "sldl_releases"),
            str(tmp_path / "bin"),
        ),
        ("reinitialize",),
    ]


def test_install_slsk_skips_config_and_runtime_when_setup_is_absent(tmp_path):
    result = service.install_slsk(
        base_dir=str(tmp_path),
        config_path=str(tmp_path / "missing.json"),
        config_is_present=lambda: False,
        reinitialize_runtime=lambda: pytest.fail("must not reinitialize"),
        install_binary=lambda *_args: "/installed/slsk-batchdl",
        read_config=lambda _path: pytest.fail("must not read config"),
        write_config=lambda *_args: pytest.fail("must not write config"),
    )

    assert result.payload == {
        "success": True,
        "path": "/installed/slsk-batchdl",
    }


def test_install_slsk_preserves_success_status_for_reported_failures(tmp_path):
    result = service.install_slsk(
        base_dir=str(tmp_path),
        config_path=str(tmp_path / "config.json"),
        config_is_present=lambda: True,
        reinitialize_runtime=lambda: None,
        install_binary=lambda *_args: (_ for _ in ()).throw(
            FileNotFoundError("release missing")
        ),
    )

    assert result.status_code == 200
    assert result.payload == {
        "success": False,
        "message": "release missing",
    }
