"""Tests for src.sync_scheduler.SyncScheduler."""

import threading
import time

from src.sync_scheduler import SyncScheduler


def _make(interval, on_startup=False):
    calls = []

    def trigger():
        calls.append(time.monotonic())

    return SyncScheduler(interval, trigger, run_on_startup=on_startup), calls


def test_disabled_when_interval_zero():
    sched, calls = _make(0)
    assert sched.enabled is False
    sched.start()
    time.sleep(0.05)
    sched.stop()
    assert calls == []


def test_disabled_when_interval_negative():
    sched, calls = _make(-10)
    assert sched.enabled is False


def test_run_on_startup_fires_once():
    sched, calls = _make(3600, on_startup=True)
    sched.start()
    time.sleep(1.3)  # startup delay is 1.0s
    sched.stop()
    assert len(calls) == 1


def test_interval_ticks_fire():
    # Interval of 1s; we expect >=2 calls in ~2.5s with on_startup=False.
    # Uses a short interval to keep the test fast; clamping is not the
    # concern here (no clamping in SyncScheduler).
    from src import sync_scheduler as ss
    original_delay = ss.STARTUP_DELAY_SECONDS
    ss.STARTUP_DELAY_SECONDS = 0.05
    try:
        sched, calls = _make(1, on_startup=True)
        sched.start()
        time.sleep(2.3)
        sched.stop()
    finally:
        ss.STARTUP_DELAY_SECONDS = original_delay
    assert len(calls) >= 2, f"expected ≥2 calls, got {len(calls)}"


def test_stop_is_prompt():
    from src import sync_scheduler as ss
    ss.STARTUP_DELAY_SECONDS = 0.01
    try:
        sched, calls = _make(60, on_startup=False)
        sched.start()
        time.sleep(0.1)
        start = time.monotonic()
        sched.stop()
        elapsed = time.monotonic() - start
    finally:
        ss.STARTUP_DELAY_SECONDS = 1.0
    assert elapsed < 1.0, f"stop should be near-instant, took {elapsed:.2f}s"


def test_trigger_exception_does_not_kill_loop():
    from src import sync_scheduler as ss
    ss.STARTUP_DELAY_SECONDS = 0.05
    calls = []

    def flaky_trigger():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    try:
        sched = SyncScheduler(1, flaky_trigger, run_on_startup=True)
        sched.start()
        time.sleep(1.5)
        sched.stop()
    finally:
        ss.STARTUP_DELAY_SECONDS = 1.0
    # startup call raised; interval tick still fires.
    assert len(calls) >= 2


def test_start_is_idempotent():
    sched, calls = _make(3600, on_startup=False)
    sched.start()
    first_thread = sched._thread
    sched.start()
    assert sched._thread is first_thread
    sched.stop()
