from pathlib import Path

from src.services.download_residue_service import (
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
