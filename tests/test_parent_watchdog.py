from src.parent_watchdog import configured_parent_pid, watch_parent


def test_configured_parent_pid_is_strict_and_desktop_only():
    assert configured_parent_pid({}) is None
    assert configured_parent_pid({"DAPMANAGER_PARENT_PID": ""}) is None
    assert configured_parent_pid({"DAPMANAGER_PARENT_PID": "not-a-pid"}) is None
    assert configured_parent_pid({"DAPMANAGER_PARENT_PID": "0"}) is None
    assert configured_parent_pid({"DAPMANAGER_PARENT_PID": "1"}) is None
    assert configured_parent_pid({"DAPMANAGER_PARENT_PID": " 4321 "}) == 4321


def test_watch_parent_exits_after_reparenting():
    parent_pids = iter((4321, 4321, 1))
    waits: list[float] = []
    exits: list[int] = []

    watch_parent(
        4321,
        get_parent_pid=lambda: next(parent_pids),
        wait=waits.append,
        exit_process=exits.append,
    )

    assert waits == [1.0, 1.0]
    assert exits == [0]
