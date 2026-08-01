"""Release the desktop backend when its native parent disappears.

Tauri normally terminates the Python child during its exit lifecycle. A hard
kill or in-place app replacement can bypass that callback, leaving Flask bound
to the stable desktop port. POSIX reparents an orphan immediately, which gives
the child a dependency-free and unambiguous way to detect that condition.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping


PARENT_PID_ENV = "DAPMANAGER_PARENT_PID"
WATCH_INTERVAL_SECONDS = 1.0


def configured_parent_pid(environment: Mapping[str, str]) -> int | None:
    """Return a valid native-parent PID, or ``None`` outside desktop mode."""
    raw_pid = environment.get(PARENT_PID_ENV, "").strip()
    if not raw_pid:
        return None
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return None
    return parent_pid if parent_pid > 1 else None


def watch_parent(
    expected_parent_pid: int,
    *,
    get_parent_pid: Callable[[], int] = os.getppid,
    wait: Callable[[float], object] = threading.Event().wait,
    exit_process: Callable[[int], object] = os._exit,
) -> None:
    """Exit as soon as POSIX reparents this backend away from Tauri."""
    while get_parent_pid() == expected_parent_pid:
        wait(WATCH_INTERVAL_SECONDS)
    exit_process(0)


def start_parent_watchdog(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Start the POSIX desktop-parent watcher when its PID was supplied."""
    if os.name != "posix":
        return False
    parent_pid = configured_parent_pid(os.environ if environment is None else environment)
    if parent_pid is None:
        return False

    thread = threading.Thread(
        target=watch_parent,
        args=(parent_pid,),
        name="dapmanager-parent-watchdog",
        daemon=True,
    )
    thread.start()
    return True
