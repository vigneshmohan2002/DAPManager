"""Safely mirror imported audio into a separately mounted media library.

The mirror is intentionally one-way and file-at-a-time.  DAPManager keeps its
canonical library as the source of truth; a configured Jellyfin library gets
the same relative path only when it is missing or contains a lower-quality
copy. When the mirror has better audio, only verified canonical Picard tags
are synchronized and its encoded frames remain authoritative.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple

from . import tag_service
from .audio_quality import (
    equal_quality_destination_satisfies,
    quality_tuple,
    read_quality,
)


logger = logging.getLogger(__name__)
RESERVED_TOP_LEVEL_COMPONENTS = frozenset({".dap-reconcile-backups"})


@dataclass(frozen=True)
class _DestinationDecision:
    """Whether source media replaces the mirror or only tags may change."""

    replace: bool
    destination_better: bool = False
    destination_snapshot: Optional[Tuple[int, int, int, int, int]] = None


def _resolved(path: str) -> str:
    """Return an absolute, symlink-resolved filesystem path."""
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def _is_within(path: str, root: str) -> bool:
    """Return whether ``path`` is ``root`` or one of its descendants."""
    try:
        common = os.path.commonpath((os.path.normcase(path), os.path.normcase(root)))
    except ValueError:
        # Different Windows drives (or otherwise incomparable paths) cannot be
        # in the same directory tree.
        return False
    return common == os.path.normcase(root)


def _require_within(path: str, root: str, *, label: str) -> None:
    if not _is_within(path, root):
        raise ValueError(f"{label} resolves outside its configured library root")


def _reserved_component(name: str) -> bool:
    """Match controller namespaces including Windows case/dot aliases."""
    return name.rstrip(" .").casefold() in RESERVED_TOP_LEVEL_COMPONENTS


def _require_outside_reserved_control(path: str, mirror_root: str) -> None:
    """Reject an existing directory alias that resolves into controller state."""
    control = os.path.join(mirror_root, ".dap-reconcile-backups")
    if not os.path.isdir(control):
        return
    current = _resolved(path)
    while _is_within(current, mirror_root):
        try:
            if os.path.samefile(current, control):
                raise ValueError(
                    "mirror destination resolves into a reserved controller directory"
                )
        except FileNotFoundError:
            pass
        if os.path.normcase(current) == os.path.normcase(mirror_root):
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent


def _fsync_directory(path: str) -> None:
    """Best-effort directory sync after an atomic replacement.

    Opening directories is not supported by every platform/filesystem, while
    the file itself has already been flushed and synced.  Treat that final
    durability enhancement as best effort so a valid mirror is not reported as
    failed on Windows.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def _destination_decision(
    source_path: str,
    destination_path: str,
) -> _DestinationDecision:
    """Classify mirror media separately from canonical tag convergence."""
    if not os.path.lexists(destination_path):
        return _DestinationDecision(replace=True)
    if os.path.islink(destination_path):
        raise ValueError("mirror destination must not be a symbolic link")
    if not os.path.isfile(destination_path):
        raise ValueError("mirror destination exists but is not a regular file")
    for _attempt in range(3):
        try:
            before = os.lstat(destination_path)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ValueError("mirror destination is not a regular file")
            source_quality = quality_tuple(read_quality(source_path))
            destination_quality = quality_tuple(read_quality(destination_path))
            after = os.lstat(destination_path)
        except FileNotFoundError:
            return _DestinationDecision(replace=True)
        before_snapshot = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        destination_snapshot = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if before_snapshot == destination_snapshot:
            break
    else:
        raise tag_service.TagSynchronizationRace(
            "mirror destination did not stabilize during quality probe"
        )
    if source_quality > destination_quality:
        return _DestinationDecision(
            replace=True,
            destination_snapshot=destination_snapshot,
        )
    if source_quality < destination_quality:
        return _DestinationDecision(
            replace=False,
            destination_better=True,
            destination_snapshot=destination_snapshot,
        )
    return _DestinationDecision(
        replace=not equal_quality_destination_satisfies(
            source_path,
            destination_path,
        ),
        destination_snapshot=destination_snapshot,
    )


def _destination_can_be_replaced(source_path: str, destination_path: str) -> bool:
    """Compatibility boolean for callers interested only in media copying."""
    return _destination_decision(source_path, destination_path).replace


def _synchronize_better_destination_tags(
    source_path: str,
    destination_path: str,
    expected_destination_snapshot: Optional[
        Tuple[int, int, int, int, int]
    ],
) -> bool:
    """Converge canonical FLAC tags while preserving better mirror frames."""
    if (
        os.path.splitext(source_path)[1].lower() != ".flac"
        or os.path.splitext(destination_path)[1].lower() != ".flac"
    ):
        return False
    if not tag_service.has_complete_picard_tags(source_path):
        logger.warning(
            "Mirror retained better audio without tag sync because the "
            "source FLAC lacks complete Picard tags: %s",
            source_path,
        )
        return False
    return tag_service.copy_complete_picard_tags_atomic(
        source_path,
        destination_path,
        expected_destination_snapshot=expected_destination_snapshot,
    )


def mirror_imported_file(
    source_path: str,
    source_root: str,
    mirror_root: str,
) -> Optional[str]:
    """Atomically mirror one imported file while preserving its relative path.

    ``source_path`` must resolve inside ``source_root`` and the corresponding
    destination must resolve inside the already-mounted ``mirror_root``.
    Missing files are copied. Existing files are replaced when the source has a
    greater :func:`~src.audio_quality.quality_tuple`, or when equal-quality FLAC
    audio needs its canonical Picard identity synchronized. Better-quality
    destination audio is retained while complete canonical source tags are
    synchronized atomically when necessary.

    Returns the destination path when audio or tags changed and ``None`` when
    an existing destination was retained byte-for-byte. Validation and I/O
    errors are raised for the caller to log and isolate from the primary import.
    """
    resolved_source_root = _resolved(source_root)
    resolved_source = _resolved(source_path)
    resolved_mirror_root = _resolved(mirror_root)

    if not os.path.isdir(resolved_source_root):
        raise ValueError("source library root does not exist or is not a directory")
    _require_within(
        resolved_source,
        resolved_source_root,
        label="mirror source",
    )
    if resolved_source == resolved_source_root or not os.path.isfile(resolved_source):
        raise ValueError("mirror source does not exist or is not a regular file")
    if not os.path.isdir(resolved_mirror_root):
        raise ValueError("mirror library root does not exist or is not a directory")

    relative_path = os.path.relpath(resolved_source, resolved_source_root)
    first_component = relative_path.split(os.sep, 1)[0]
    if _reserved_component(first_component):
        raise ValueError(
            "mirror source uses a reserved top-level library component"
        )
    destination_path = os.path.join(resolved_mirror_root, relative_path)
    resolved_destination = _resolved(destination_path)
    _require_within(
        resolved_destination,
        resolved_mirror_root,
        label="mirror destination",
    )

    destination_dir = os.path.dirname(destination_path)
    os.makedirs(destination_dir, exist_ok=True)
    # Resolve again after creating parents so a pre-existing symlink component
    # cannot redirect the write outside the configured mirror.
    resolved_destination_dir = _resolved(destination_dir)
    _require_within(
        resolved_destination_dir,
        resolved_mirror_root,
        label="mirror destination directory",
    )
    _require_outside_reserved_control(
        resolved_destination_dir,
        resolved_mirror_root,
    )
    destination_path = os.path.join(
        resolved_destination_dir,
        os.path.basename(destination_path),
    )
    _require_within(
        _resolved(destination_path),
        resolved_mirror_root,
        label="mirror destination",
    )

    for attempt in range(3):
        decision = _destination_decision(resolved_source, destination_path)
        if decision.replace:
            break
        try:
            tags_changed = (
                _synchronize_better_destination_tags(
                    resolved_source,
                    destination_path,
                    decision.destination_snapshot,
                )
                if decision.destination_better
                else False
            )
        except tag_service.TagSynchronizationRace:
            if attempt == 2:
                raise
            continue
        if tags_changed:
            logger.info(
                "Mirror synchronized canonical tags onto better destination: %s",
                destination_path,
            )
            return destination_path
        logger.info("Mirror retained equal-or-better destination: %s", destination_path)
        return None

    temp_fd = -1
    temp_path: Optional[str] = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(destination_path)}.dapmirror-",
            suffix=".tmp",
            dir=resolved_destination_dir,
        )
        with open(resolved_source, "rb") as source_file, os.fdopen(
            temp_fd, "wb"
        ) as temp_file:
            temp_fd = -1
            shutil.copyfileobj(source_file, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        # Match the source's access mode so a Jellyfin process running as a
        # different container user can read the mirrored file.
        source_mode = stat.S_IMODE(os.stat(resolved_source).st_mode)
        os.chmod(temp_path, source_mode)
        try:
            shutil.copystat(resolved_source, temp_path)
        except OSError as exc:
            logger.debug("Could not copy mirror file timestamps: %s", exc)

        # Re-check immediately before replacement.  If another worker wrote a
        # better/equal destination while this file was being copied, retain it.
        for attempt in range(3):
            decision = _destination_decision(resolved_source, destination_path)
            if decision.replace:
                break
            try:
                tags_changed = (
                    _synchronize_better_destination_tags(
                        resolved_source,
                        destination_path,
                        decision.destination_snapshot,
                    )
                    if decision.destination_better
                    else False
                )
            except tag_service.TagSynchronizationRace:
                if attempt == 2:
                    raise
                continue
            if tags_changed:
                logger.info(
                    "Mirror synchronized canonical tags onto concurrently-written "
                    "better destination: %s",
                    destination_path,
                )
                return destination_path
            logger.info(
                "Mirror retained concurrently-written equal-or-better destination: %s",
                destination_path,
            )
            return None

        _require_within(
            _resolved(destination_path),
            resolved_mirror_root,
            label="mirror destination",
        )
        os.replace(temp_path, destination_path)
        temp_path = None
        _fsync_directory(resolved_destination_dir)
        logger.info("Mirrored imported file to: %s", destination_path)
        return destination_path
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
