"""
Wikipedia REST API client for artist infoscreens.

The summary endpoint (https://en.wikipedia.org/api/rest_v1/page/summary/<title>)
returns a JSON blob with `extract` (plain-text summary), `content_urls.desktop.page`,
and `thumbnail.source`. Etiquette requires a descriptive User-Agent — we reuse
contact_email from config so the same identity covers MusicBrainz and Wikipedia.

Rate limit is 200 req/s per IP (very generous), so no rate limiter here. A
process-local cache keeps repeat visits to the same artist from re-hitting the
network. Negative results are cached too — flaky artist names shouldn't retry
on every screen mount.
"""

import logging
import threading
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_TIMEOUT = 5.0
_FALLBACK_UA = "DAPManager/0.1.0 (https://github.com/vigneshmohan2002/DAPManager)"

_cache: dict[str, Optional[dict]] = {}
_cache_lock = threading.Lock()


def _user_agent() -> str:
    contact = ""
    try:
        from .config_manager import ConfigManager

        if ConfigManager._instance is not None:
            contact = (ConfigManager._instance.contact_email or "").strip()
    except Exception:
        pass
    return f"DAPManager/0.1.0 ({contact})" if contact else _FALLBACK_UA


def _fetch(title: str) -> Optional[dict]:
    url = _BASE + quote(title.replace(" ", "_"))
    try:
        r = requests.get(
            url, headers={"User-Agent": _user_agent()}, timeout=_TIMEOUT
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
    except Exception as e:
        logger.warning(f"Wikipedia lookup failed for {title!r}: {e}")
        return None
    # Disambiguation pages have type == "disambiguation"; the extract is just
    # the disambig blurb ("X may refer to:") which is worse than no summary.
    if body.get("type") == "disambiguation":
        return None
    return {
        "summary": body.get("extract") or "",
        "source_url": (body.get("content_urls") or {})
        .get("desktop", {})
        .get("page"),
        "image_url": (body.get("thumbnail") or {}).get("source"),
        "title": body.get("title") or title,
    }


def get_artist_summary(name: str) -> Optional[dict]:
    """Best-effort artist Wikipedia summary. Tries the bare name first, then
    "<name> (band)" as the standard disambiguation suffix. Cached per process.
    """
    name = (name or "").strip()
    if not name:
        return None
    with _cache_lock:
        if name in _cache:
            return _cache[name]
    result = _fetch(name)
    if result is None:
        result = _fetch(f"{name} (band)")
    with _cache_lock:
        _cache[name] = result
    return result


def reset_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
