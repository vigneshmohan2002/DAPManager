"""
Where config.json lives on disk.

Resolution order:
  1. ``DAPMANAGER_CONFIG`` env var — absolute path override (tests, Docker, dev).
  2. ``./config.json`` in the current working directory, if it already
     exists. Keeps the dev / run-from-repo / Docker workflows working.
  3. Platform user-config dir + ``DAPManager/config.json`` — the default
     when a packaged .app/.exe boots for the first time and there's no
     config.json next to it.

macOS: ``~/Library/Application Support/DAPManager/config.json``
Windows: ``%APPDATA%\\DAPManager\\config.json``
Linux/other: ``$XDG_CONFIG_HOME/dapmanager/config.json`` (or
``~/.config/dapmanager/config.json``)
"""

import os
import sys


def resolve_config_path() -> str:
    override = (os.environ.get("DAPMANAGER_CONFIG") or "").strip()
    if override:
        return os.path.abspath(override)

    legacy = os.path.abspath("config.json")
    if os.path.exists(legacy):
        return legacy

    return os.path.join(_platform_config_dir(), "config.json")


def _platform_config_dir() -> str:
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/DAPManager")
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DAPManager")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "dapmanager")


def ensure_parent_dir(path: str) -> None:
    """Create the directory containing ``path`` if it doesn't exist."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
