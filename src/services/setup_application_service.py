"""First-run, satellite bundle, and bundled Soulseek installation policy.

Flask owns request parsing and response construction.  This module owns the
policy behind those adapters while keeping the filesystem, clock, HMAC,
process, bundle, installer, and runtime boundaries injectable for focused
tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Mapping,
    MutableMapping,
    Optional,
    Protocol,
    Sequence,
    Type,
)
from urllib.parse import quote


logger = logging.getLogger(__name__)

BUNDLE_LINK_TTL_SECONDS = 60 * 60
BUNDLE_DOWNLOAD_NAME = "DAPManager-mac.zip"
BUNDLE_MIMETYPE = "application/zip"

Clock = Callable[[], float]
BundleSigner = Callable[[str, bytes], str]
ConstantTimeCompare = Callable[[str, str], bool]
BinaryLookup = Callable[[str], Optional[str]]
ConfigPresenceCheck = Callable[[], bool]
ConfigReader = Callable[[str], MutableMapping[str, Any]]
ConfigWriter = Callable[[str, Mapping[str, Any]], None]
RuntimeInitializer = Callable[[], Any]


class ProcessResult(Protocol):
    """Subset of ``CompletedProcess`` consumed by URL detection."""

    returncode: int
    stdout: str


class ProcessRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> ProcessResult: ...


class BundleFetcher(Protocol):
    def __call__(self) -> os.PathLike[str] | str: ...


class BundleInjector(Protocol):
    def __call__(
        self,
        base_path: os.PathLike[str] | str,
        public_url: str,
        api_token: Optional[str] = None,
    ) -> bytes: ...


class BinaryInstaller(Protocol):
    def __call__(self, releases_dir: str, bin_dir: str) -> str: ...


@dataclass(frozen=True)
class SetupApplicationResult:
    """JSON payload and HTTP status translated by the Flask adapter."""

    payload: Mapping[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class BundleDownloadResult:
    """Either a binary bundle response or the established JSON failure."""

    status_code: int
    body: Optional[bytes] = None
    payload: Optional[Mapping[str, Any]] = None
    mimetype: str = BUNDLE_MIMETYPE
    headers: Mapping[str, str] = field(default_factory=dict)


def read_setup_config(path: str) -> Optional[Mapping[str, Any]]:
    """Re-read setup configuration while preserving the route's fallbacks."""
    try:
        with open(path, "r") as config_file:
            return json.load(config_file)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write_setup_config(
    path: str,
    values: Mapping[str, Any],
) -> None:
    with open(path, "w") as config_file:
        json.dump(values, config_file, indent=4)


def _read_install_config(path: str) -> MutableMapping[str, Any]:
    with open(path, "r") as config_file:
        return json.load(config_file)


def _sign_bundle_message(api_token: str, message: bytes) -> str:
    return hmac.new(
        api_token.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def bundle_download_token(
    api_token: str,
    expires_at: int,
    *,
    signer: BundleSigner = _sign_bundle_message,
) -> str:
    """Mint the existing bundle-only token without exposing the API secret."""
    normalized_expiry = int(expires_at)
    message = f"dapmanager-bundle:{normalized_expiry}".encode("utf-8")
    signature = signer(api_token, message)
    return f"{normalized_expiry}.{signature}"


def valid_bundle_download_token(
    value: str,
    api_token: str,
    *,
    clock: Optional[Clock] = None,
    signer: BundleSigner = _sign_bundle_message,
    compare_digest: ConstantTimeCompare = hmac.compare_digest,
) -> bool:
    """Validate token shape, expiry, and signature using constant-time compare."""
    try:
        expires_raw, _ = value.split(".", 1)
        expires_at = int(expires_raw)
    except (AttributeError, TypeError, ValueError):
        return False

    now = clock or time.time
    if expires_at < int(now()):
        return False
    expected = bundle_download_token(
        api_token,
        expires_at,
        signer=signer,
    )
    return compare_digest(value, expected)


def _public_master_url(config_values: Mapping[str, Any]) -> str:
    return (config_values.get("public_master_url") or "").strip().rstrip("/")


def _api_token(config_values: Mapping[str, Any]) -> str:
    return (config_values.get("api_token") or "").strip()


def build_satellite_bundle_link(
    config_values: Mapping[str, Any],
    *,
    clock: Optional[Clock] = None,
    ttl_seconds: int = BUNDLE_LINK_TTL_SECONDS,
    signer: BundleSigner = _sign_bundle_message,
) -> SetupApplicationResult:
    """Build the short-lived dashboard sharing link from persisted config."""
    public_url = _public_master_url(config_values)
    if not public_url:
        return SetupApplicationResult(
            {
                "success": False,
                "message": "public_master_url is not configured",
            },
            409,
        )

    url = f"{public_url}/download/mac"
    api_token = _api_token(config_values)
    expires_at: Optional[int] = None
    if api_token:
        now = clock or time.time
        expires_at = int(now()) + ttl_seconds
        scoped_token = bundle_download_token(
            api_token,
            expires_at,
            signer=signer,
        )
        url += f"?bundle_token={quote(scoped_token, safe='')}"

    return SetupApplicationResult(
        {
            "success": True,
            "url": url,
            "expires_at": expires_at,
        }
    )


def _provided_api_token(
    authorization_header: str,
    query_token: str,
    cookie_token: str,
) -> str:
    if authorization_header.startswith("Bearer "):
        provided = authorization_header[len("Bearer ") :].strip()
    else:
        provided = (query_token or "").strip()
    if not provided:
        provided = (cookie_token or "").strip()
    return provided


def _bundle_failure(
    message: str,
    status_code: int,
) -> BundleDownloadResult:
    return BundleDownloadResult(
        status_code=status_code,
        payload={"success": False, "message": message},
    )


def prepare_satellite_bundle_download(
    config_values: Mapping[str, Any],
    *,
    authorization_header: str = "",
    query_token: str = "",
    cookie_token: str = "",
    bundle_token: str = "",
    clock: Optional[Clock] = None,
    signer: BundleSigner = _sign_bundle_message,
    compare_digest: ConstantTimeCompare = hmac.compare_digest,
    fetch_bundle: Optional[BundleFetcher] = None,
    inject_bundle: Optional[BundleInjector] = None,
    bundle_fetch_error: Optional[Type[Exception]] = None,
    event_logger: logging.Logger = logger,
) -> BundleDownloadResult:
    """Authorize, fetch, and inject a satellite bundle for the HTTP adapter."""
    public_url = _public_master_url(config_values)
    if not public_url:
        return _bundle_failure(
            (
                "public_master_url is not set. Open Settings → Multi-Device "
                "Sync (or re-run /setup) and fill it in before sharing the "
                "download link."
            ),
            409,
        )

    api_token = _api_token(config_values)
    if api_token:
        provided = _provided_api_token(
            authorization_header,
            query_token,
            cookie_token,
        )
        direct_token_is_valid = compare_digest(provided, api_token)
        scoped_token_is_valid = direct_token_is_valid or (
            valid_bundle_download_token(
                (bundle_token or "").strip(),
                api_token,
                clock=clock,
                signer=signer,
                compare_digest=compare_digest,
            )
        )
        if not scoped_token_is_valid:
            return _bundle_failure("missing or invalid api token", 401)

    from src import satellite_bundle

    bundle_fetcher = fetch_bundle or satellite_bundle.ensure_cached_bundle
    bundle_injector = inject_bundle or satellite_bundle.inject_master_config
    fetch_error_type = bundle_fetch_error or satellite_bundle.BundleFetchError

    try:
        base_path = bundle_fetcher()
    except fetch_error_type as exc:
        event_logger.warning("download_mac: bundle fetch failed: %s", exc)
        return _bundle_failure(
            (
                "Could not fetch the satellite bundle from GitHub. "
                "Check the master's outbound connectivity."
            ),
            502,
        )

    try:
        body = bundle_injector(base_path, public_url, api_token or None)
    except Exception as exc:
        event_logger.exception("download_mac: injection failed")
        return _bundle_failure(str(exc), 500)

    return BundleDownloadResult(
        status_code=200,
        body=body,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{BUNDLE_DOWNLOAD_NAME}"'
            ),
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        },
    )


def detect_public_url(
    *,
    environment: Optional[Mapping[str, str]] = None,
    find_binary: Optional[BinaryLookup] = None,
    run_process: Optional[ProcessRunner] = None,
) -> SetupApplicationResult:
    """Suggest the master URL using env first, then local Tailscale state."""
    env = environment if environment is not None else os.environ
    env_url = (env.get("MASTER_PUBLIC_URL") or "").strip()
    if env_url:
        return SetupApplicationResult({"source": "env", "url": env_url})

    binary_lookup = find_binary or shutil.which
    tailscale_cli = binary_lookup("tailscale")
    if not tailscale_cli:
        return SetupApplicationResult({"source": "none"})

    port = (env.get("MASTER_PORT") or "5001").strip() or "5001"
    process_runner = run_process or subprocess.run
    try:
        process = process_runner(
            [tailscale_cli, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return SetupApplicationResult({"source": "none"})

    if process.returncode != 0 or not process.stdout:
        return SetupApplicationResult({"source": "none"})
    try:
        status = json.loads(process.stdout)
    except json.JSONDecodeError:
        return SetupApplicationResult({"source": "none"})

    self_block = status.get("Self") or {}
    dns_name = (self_block.get("DNSName") or "").rstrip(".")
    if dns_name:
        return SetupApplicationResult(
            {
                "source": "tailscale",
                "url": f"http://{dns_name}:{port}",
            }
        )

    for ip_address in self_block.get("TailscaleIPs") or []:
        if ip_address and ":" not in ip_address:
            return SetupApplicationResult(
                {
                    "source": "tailscale",
                    "url": f"http://{ip_address}:{port}",
                }
            )
    return SetupApplicationResult({"source": "none"})


def install_slsk(
    *,
    base_dir: str,
    config_path: str,
    config_is_present: ConfigPresenceCheck,
    reinitialize_runtime: RuntimeInitializer,
    install_binary: Optional[BinaryInstaller] = None,
    read_config: ConfigReader = _read_install_config,
    write_config: ConfigWriter = _write_setup_config,
) -> SetupApplicationResult:
    """Install bundled sldl, persist its command, then reload app state."""
    try:
        if install_binary is None:
            from src.binary_manager import install_from_local

            binary_installer = install_from_local
        else:
            binary_installer = install_binary

        releases_dir = os.path.join(base_dir, "sldl_releases")
        bin_dir = os.path.join(base_dir, "bin")
        final_path = binary_installer(releases_dir, bin_dir)

        if config_is_present():
            config_values = read_config(config_path)
            config_values["slsk_cmd_base"] = [final_path]
            write_config(config_path, config_values)
            reinitialize_runtime()

        return SetupApplicationResult(
            {"success": True, "path": final_path}
        )
    except Exception as exc:
        return SetupApplicationResult(
            {"success": False, "message": str(exc)}
        )
