"""
Master-side satellite bundle cache + URL injection.

Backs the ``/download/mac`` route. Lazy-fetches the Tauri Mac zip from
the GitHub release tagged in :data:`DESKTOP_RELEASE_TAG`, caches it
on disk, and rewrites the cached zip on-the-fly to embed
``Contents/Resources/master_url.txt`` (and optionally
``master_token.txt``) so a fresh ``DAPManager.app`` knows how to
reach this master without the user typing anything.

The bundle on disk is the unmodified upstream artifact. Each request
gets its own freshly-rewritten copy with the *current* master URL —
operators can change ``public_master_url`` without invalidating the
cache.
"""

import io
import logging
import os
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DESKTOP_RELEASE_TAG = "desktop-v0.1.0"
GITHUB_REPO = "vigneshmohan2002/DAPManager"
ASSET_NAME = "DAPManager-mac.zip"

_FETCH_TIMEOUT_S = 30
_RESOURCE_PREFIX = "DAPManager.app/Contents/Resources"


class BundleFetchError(RuntimeError):
    """Raised when the upstream release asset can't be retrieved."""


def cache_dir() -> Path:
    """Where cached release zips live.

    Honours ``DATA_DIR`` (set by the Docker entrypoint to ``/data``);
    otherwise drops a ``cache/`` dir under the current working directory.
    """
    base = (os.environ.get("DATA_DIR") or "").strip() or os.getcwd()
    return Path(base) / "cache" / "desktop-bundle"


def cached_bundle_path(tag: str = DESKTOP_RELEASE_TAG) -> Path:
    return cache_dir() / f"{tag}.zip"


def _release_url(tag: str) -> str:
    return (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"{tag}/{ASSET_NAME}"
    )


def _fetch_to(path: Path, tag: str) -> None:
    url = _release_url(tag)
    logger.info("Fetching satellite bundle %s from %s", tag, url)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT_S) as resp:
            if getattr(resp, "status", 200) >= 400:
                raise BundleFetchError(
                    f"GitHub returned HTTP {resp.status} for {url}"
                )
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        os.replace(tmp, path)
    except urllib.error.URLError as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise BundleFetchError(f"could not fetch {url}: {e}") from e
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def ensure_cached_bundle(tag: str = DESKTOP_RELEASE_TAG) -> Path:
    """Return the local path to the cached zip, fetching it if absent."""
    path = cached_bundle_path(tag)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    _fetch_to(path, tag)
    return path


def inject_master_config(
    base_zip: Path, master_url: str, token: Optional[str] = None
) -> bytes:
    """Stream-rewrite ``base_zip``, embedding the master URL + token.

    Reads the upstream archive, copies every entry verbatim into a new
    in-memory zip, and adds ``master_url.txt`` (always) plus
    ``master_token.txt`` (only when ``token`` is non-empty) under
    ``DAPManager.app/Contents/Resources``. Existing entries with the
    same names are dropped first so re-running the rewrite is safe.

    Returns the new zip's bytes — the caller streams them to the HTTP
    response. Sign-and-modify is a non-issue because the upstream
    bundle is unsigned.
    """
    if not master_url:
        raise ValueError("master_url is required for injection")

    inject_url_name = f"{_RESOURCE_PREFIX}/master_url.txt"
    inject_token_name = f"{_RESOURCE_PREFIX}/master_token.txt"
    skip = {inject_url_name, inject_token_name}

    out = io.BytesIO()
    with zipfile.ZipFile(base_zip, "r") as src, zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for item in src.infolist():
            if item.filename in skip:
                continue
            dst.writestr(item, src.read(item.filename))
        dst.writestr(inject_url_name, master_url)
        if token:
            dst.writestr(inject_token_name, token)
    return out.getvalue()
