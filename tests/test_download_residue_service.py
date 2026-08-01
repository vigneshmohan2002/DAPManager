from pathlib import Path

from src.services.download_residue_service import (
    enforce_download_residue_budget,
    remove_download_residue,
    scan_download_residue,
)


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_scan_groups_only_owned_directories_by_queue_item(tmp_path):
    _write(tmp_path / ".dap-queue-7-first" / "one.flac", 11)
    _write(tmp_path / ".dap-quarantine-7-second" / "two.flac", 13)
    _write(tmp_path / ".dap-queue-8-third" / "part.flac.incomplete", 17)
    _write(tmp_path / "ordinary-album" / "song.flac", 100)

    report = scan_download_residue(str(tmp_path))

    assert report.total_bytes == 41
    assert report.total_directories == 3
    assert report.total_files == 3
    assert [(item.item_id, item.bytes) for item in report.items] == [
        (7, 24),
        (8, 17),
    ]
    assert report.items[0].directory_count == 2
    assert report.items[0].kinds == ("attempt", "quarantine")
    assert report.errors == ()


def test_scan_ignores_symlinked_owned_directory(tmp_path):
    outside = tmp_path / "outside"
    _write(outside / "personal.flac", 50)
    link = tmp_path / ".dap-queue-9-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return

    assert scan_download_residue(str(tmp_path)).total_bytes == 0


def test_remove_deletes_only_matching_owned_directories(tmp_path):
    owned_attempt = tmp_path / ".dap-queue-7-first"
    owned_quarantine = tmp_path / ".dap-quarantine-7-second"
    other_item = tmp_path / ".dap-queue-8-third"
    ordinary = tmp_path / "ordinary-album"
    _write(owned_attempt / "one.flac", 11)
    _write(owned_quarantine / "two.flac", 13)
    _write(other_item / "three.flac", 17)
    _write(ordinary / "song.flac", 100)

    removed = remove_download_residue(str(tmp_path), 7)

    assert removed.removed_bytes == 24
    assert removed.removed_directories == 2
    assert removed.removed_files == 2
    assert not owned_attempt.exists()
    assert not owned_quarantine.exists()
    assert other_item.exists()
    assert ordinary.exists()


def test_cleanup_removes_expired_quarantine_but_not_active_staging(tmp_path):
    quarantine = tmp_path / ".dap-quarantine-7-old"
    active = tmp_path / ".dap-queue-8-active"
    _write(quarantine / "bad.flac", 11)
    _write(active / "downloading.flac", 13)
    old = 1_000.0
    quarantine.joinpath("bad.flac").touch()
    import os
    os.utime(quarantine / "bad.flac", (old, old))

    cleanup = enforce_download_residue_budget(
        str(tmp_path), max_bytes=100, max_age_seconds=10, now=2_000.0
    )

    assert cleanup.removed_bytes == 11
    assert not quarantine.exists()
    assert active.exists()


def test_cleanup_removes_oldest_quarantine_until_under_byte_cap(tmp_path):
    older = tmp_path / ".dap-quarantine-7-old"
    newer = tmp_path / ".dap-quarantine-8-new"
    _write(older / "old.flac", 11)
    _write(newer / "new.flac", 13)
    import os
    os.utime(older / "old.flac", (1_900, 1_900))
    os.utime(newer / "new.flac", (1_950, 1_950))

    cleanup = enforce_download_residue_budget(
        str(tmp_path), max_bytes=13, max_age_seconds=1_000, now=2_000
    )

    assert cleanup.removed_directories == 1
    assert not older.exists()
    assert newer.exists()
    assert cleanup.remaining_bytes == 13
