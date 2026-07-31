"""Inspect and remove downloader-owned staging residue safely.

Only top-level directories created by :class:`src.downloader.Downloader` are
eligible.  Callers never pass filesystem paths back to this module; queue IDs
are resolved against the configured download root so an API request cannot
escape that root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import re
import shutil
from typing import Dict, List, Literal, Optional


ResidueKind = Literal["attempt", "quarantine"]
_RESIDUE_NAME = re.compile(
    r"^\.dap-(?P<kind>queue|quarantine)-(?P<item_id>[0-9]+)-.+$"
)


@dataclass(frozen=True)
class DownloadResidue:
    """Aggregate retained files attributable to one download-queue row."""

    item_id: int
    bytes: int
    directory_count: int
    file_count: int
    kinds: tuple[ResidueKind, ...]
    newest_modified_at: Optional[str]


@dataclass(frozen=True)
class DownloadResidueReport:
    items: tuple[DownloadResidue, ...]
    total_bytes: int
    total_directories: int
    total_files: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class DownloadResidueRemoval:
    item_id: int
    removed_bytes: int
    removed_directories: int
    removed_files: int


@dataclass
class _MutableResidue:
    bytes: int = 0
    directory_count: int = 0
    file_count: int = 0
    kinds: set[ResidueKind] = field(default_factory=set)
    newest_mtime: Optional[float] = None


def _owned_directory(entry: os.DirEntry[str]) -> Optional[tuple[int, ResidueKind]]:
    """Return queue ownership for one safe top-level directory."""
    match = _RESIDUE_NAME.fullmatch(entry.name)
    if match is None or not entry.is_dir(follow_symlinks=False):
        return None
    kind: ResidueKind = (
        "attempt" if match.group("kind") == "queue" else "quarantine"
    )
    return int(match.group("item_id")), kind


def _directory_usage(path: str) -> tuple[int, int, Optional[float]]:
    total_bytes = 0
    file_count = 0
    newest_mtime: Optional[float] = None
    for root, directories, files in os.walk(path, followlinks=False):
        # Do not descend through a symlink placed inside retained evidence.
        directories[:] = [
            name for name in directories
            if not os.path.islink(os.path.join(root, name))
        ]
        for name in files:
            candidate = os.path.join(root, name)
            try:
                stat = os.stat(candidate, follow_symlinks=False)
            except OSError:
                continue
            if os.path.islink(candidate):
                continue
            total_bytes += int(stat.st_size)
            file_count += 1
            if newest_mtime is None or stat.st_mtime > newest_mtime:
                newest_mtime = stat.st_mtime
    return total_bytes, file_count, newest_mtime


def _iso_utc(timestamp: Optional[float]) -> Optional[str]:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def scan_download_residue(downloads_dir: str) -> DownloadResidueReport:
    """Return bounded metadata for downloader-owned top-level directories."""
    aggregates: Dict[int, _MutableResidue] = {}
    errors: List[str] = []
    try:
        entries = list(os.scandir(downloads_dir))
    except FileNotFoundError:
        return DownloadResidueReport((), 0, 0, 0, ())
    except OSError as exc:
        return DownloadResidueReport((), 0, 0, 0, (str(exc),))

    for entry in entries:
        try:
            owner = _owned_directory(entry)
            if owner is None:
                continue
            item_id, kind = owner
            byte_count, file_count, newest_mtime = _directory_usage(entry.path)
        except OSError as exc:
            errors.append(f"{entry.name}: {exc}")
            continue

        aggregate = aggregates.setdefault(item_id, _MutableResidue())
        aggregate.bytes += byte_count
        aggregate.directory_count += 1
        aggregate.file_count += file_count
        aggregate.kinds.add(kind)
        if (
            newest_mtime is not None
            and (
                aggregate.newest_mtime is None
                or newest_mtime > aggregate.newest_mtime
            )
        ):
            aggregate.newest_mtime = newest_mtime

    items = tuple(
        DownloadResidue(
            item_id=item_id,
            bytes=value.bytes,
            directory_count=value.directory_count,
            file_count=value.file_count,
            kinds=tuple(sorted(value.kinds)),
            newest_modified_at=_iso_utc(value.newest_mtime),
        )
        for item_id, value in sorted(aggregates.items())
    )
    return DownloadResidueReport(
        items=items,
        total_bytes=sum(item.bytes for item in items),
        total_directories=sum(item.directory_count for item in items),
        total_files=sum(item.file_count for item in items),
        errors=tuple(errors),
    )


def remove_download_residue(
    downloads_dir: str,
    item_id: int,
) -> DownloadResidueRemoval:
    """Delete only retained directories owned by ``item_id``.

    The caller is responsible for checking queue state and obtaining explicit
    operator confirmation. Symlink directory entries are ignored.
    """
    if item_id < 1:
        raise ValueError("item_id must be positive")

    report = scan_download_residue(downloads_dir)
    before = next((item for item in report.items if item.item_id == item_id), None)
    removed_directories = 0
    try:
        entries = list(os.scandir(downloads_dir))
    except FileNotFoundError:
        entries = []

    for entry in entries:
        owner = _owned_directory(entry)
        if owner is None or owner[0] != item_id:
            continue
        shutil.rmtree(entry.path)
        removed_directories += 1

    return DownloadResidueRemoval(
        item_id=item_id,
        removed_bytes=before.bytes if before else 0,
        removed_directories=removed_directories,
        removed_files=before.file_count if before else 0,
    )
