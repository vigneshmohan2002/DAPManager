"""
Ingest a single audio file into the master's library — the tail end of both
a normal download and a satellite *upload* contribution.

Mirrors what ``downloader._process_success`` does for one file: make sure the
file carries an MBID, scan its tags into the catalog, then move it into the
clean ``Artist/Album/NN Title.flac`` layout and point the track row at its
new home.
"""

import logging
import os
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol, Tuple

from . import tag_service
from .db_manager import DatabaseManager, Track
from .library_scanner import LibraryScanner
from .utils import write_mbid_to_file
from .audio_quality import (
    equal_quality_destination_satisfies,
    library_path_for_track,
    quality_tuple,
    read_quality,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestResult:
    """Final canonical path and whether the primary library changed."""

    path: str
    changed: bool


@dataclass(frozen=True)
class _DestinationDecision:
    """Whether canonical media wins and whether only its tags may change."""

    retain: bool
    destination_better: bool = False
    destination_snapshot: Optional[Tuple[int, int, int, int, int]] = None


class Scanner(Protocol):
    """Public scanner boundary used by the ingest pipeline."""

    def process_file(self, file_path: str) -> str:
        ...


def _read_raw_embedded_recording_mbid(path: str) -> Optional[str]:
    """Return the non-empty recording-ID tag exactly as the tagger exposed it."""
    try:
        from mediafile import MediaFile, UnreadableFileError

        try:
            value = MediaFile(path).mb_trackid
        except (UnreadableFileError, OSError):
            return None
    except Exception:
        return None

    normalized = str(value).strip() if value else ""
    return normalized or None


def canonical_recording_mbid(value: Optional[str]) -> Optional[str]:
    """Validate and canonicalize one MusicBrainz UUID.

    MusicBrainz entity IDs are UUIDs and are conventionally stored in lower-
    case hyphenated form.  Canonicalizing at the filesystem boundary prevents
    an upper-case source tag from bypassing a lower-case database key and
    creating a second identity for the same recording.
    """
    normalized = str(value).strip() if value else ""
    if not normalized:
        return None
    try:
        parsed = uuid.UUID(normalized)
    except (AttributeError, ValueError):
        return None
    if parsed.int == 0:
        # The nil UUID is a sentinel, never a MusicBrainz entity identity.
        return None
    return str(parsed)


def read_embedded_recording_mbid(path: str) -> Optional[str]:
    """Return a validated, canonical MusicBrainz recording ID, when present."""
    return canonical_recording_mbid(_read_raw_embedded_recording_mbid(path))


def normalize_embedded_recording_mbid(path: str) -> Optional[str]:
    """Validate and, when necessary, canonicalize the file's recording tag.

    Invalid non-empty identifiers fail closed so the scanner cannot persist a
    malformed identity.  A valid upper-case or non-hyphenated UUID is written
    back in canonical form before scanning, which keeps scanner and database
    lookups on the same key.
    """
    raw_mbid = _read_raw_embedded_recording_mbid(path)
    if raw_mbid is None:
        return None
    canonical_mbid = canonical_recording_mbid(raw_mbid)
    if canonical_mbid is None:
        raise ValueError("embedded MusicBrainz recording ID is not a valid UUID")
    if raw_mbid != canonical_mbid:
        if not write_mbid_to_file(path, canonical_mbid):
            raise OSError("could not canonicalize embedded recording MBID")
        if read_embedded_recording_mbid(path) != canonical_mbid:
            raise OSError("embedded recording MBID did not persist canonically")
    return canonical_mbid


def file_has_embedded_mbid(path: str) -> bool:
    """Return whether ``path`` carries a MusicBrainz recording identifier."""
    return read_embedded_recording_mbid(path) is not None


def _file_has_embedded_mbid(path: str) -> bool:
    """Compatibility alias retained for existing private imports."""
    return file_has_embedded_mbid(path)


def _normalized_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")


def _resolved(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.normcase(os.path.commonpath([path, root])) == os.path.normcase(
            root
        )
    except ValueError:
        # Different Windows drives (or otherwise incomparable roots).
        return False


def _validated_library_path(
    path: str,
    music_library_dir: str,
    *,
    require_existing: bool,
    label: str,
) -> Optional[str]:
    """Validate a file path before treating it as a canonical destination.

    Database paths are persisted state and must not be allowed to redirect an
    ingest outside the configured library.  A live canonical file must also
    be a real regular file: final-component symlinks and special files are
    refused rather than followed or replaced.
    """
    absolute_path = os.path.abspath(path)
    resolved_root = _resolved(music_library_dir)
    resolved_path = _resolved(absolute_path)
    if not _is_within(resolved_path, resolved_root):
        raise ValueError(f"{label} resolves outside the music library")

    if not os.path.lexists(absolute_path):
        return None if require_existing else _normalized_path(absolute_path)

    path_stat = os.lstat(absolute_path)
    if stat.S_ISLNK(path_stat.st_mode):
        raise ValueError(f"{label} must not be a symbolic link")
    if not stat.S_ISREG(path_stat.st_mode):
        raise ValueError(f"{label} exists but is not a regular file")
    return _normalized_path(absolute_path)


def _same_path(first: str, second: str) -> bool:
    try:
        return os.path.samefile(first, second)
    except (FileNotFoundError, OSError):
        return os.path.abspath(first) == os.path.abspath(second)


def _scan_file(scanner: Scanner, path: str) -> str:
    """Use the public scanner API, with a legacy fallback for test doubles."""
    process_file = getattr(scanner, "process_file", None)
    if callable(process_file):
        return process_file(path)
    return scanner._process_file(path)  # type: ignore[attr-defined]


def _fallback_track(
    src_path: str,
    *,
    mbid_guess: Optional[str],
    artist: Optional[str],
    title: Optional[str],
    album: Optional[str],
    track_number: Optional[int],
    disc_number: Optional[int],
) -> Track:
    """Build the same identity used when scanner tags produce no row."""
    return Track(
        mbid=mbid_guess or "",
        title=title or "Unknown Title",
        artist=artist or "Unknown Artist",
        album=album or "Unknown Album",
        local_path=src_path,
        track_number=track_number or 0,
        disc_number=disc_number or 1,
    )


def _fsync_directory(path: str) -> None:
    """Best-effort directory sync after an atomic file replacement."""
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


def _remove_superseded_source(src_path: str) -> None:
    """Remove a staging candidate after a canonical file satisfied it."""
    try:
        os.remove(src_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Per-item staging prevents a leftover candidate from contaminating a
        # later queue item, so cleanup failure is safe to leave for inspection.
        logger.warning(
            "ingest: could not remove superseded source %s: %s",
            src_path,
            exc,
        )


def _destination_decision(
    src_path: str,
    dest_path: str,
    music_library_dir: str,
) -> _DestinationDecision:
    """Classify an existing destination without conflating media and tags."""
    validated = _validated_library_path(
        dest_path,
        music_library_dir,
        require_existing=True,
        label="ingest destination",
    )
    if validated is None:
        return _DestinationDecision(retain=False)
    for _attempt in range(3):
        try:
            before = os.lstat(validated)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ValueError("ingest destination is not a regular file")
            source_quality = quality_tuple(read_quality(src_path))
            destination_quality = quality_tuple(read_quality(validated))
            after = os.lstat(validated)
        except FileNotFoundError:
            return _DestinationDecision(retain=False)
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
            "ingest destination did not stabilize during quality probe"
        )
    if destination_quality > source_quality:
        return _DestinationDecision(
            retain=True,
            destination_better=True,
            destination_snapshot=destination_snapshot,
        )
    if destination_quality < source_quality:
        return _DestinationDecision(
            retain=False,
            destination_snapshot=destination_snapshot,
        )
    return _DestinationDecision(
        retain=equal_quality_destination_satisfies(src_path, validated),
        destination_snapshot=destination_snapshot,
    )


def _existing_destination_satisfies(
    src_path: str,
    dest_path: str,
    music_library_dir: str,
) -> bool:
    """Compatibility boolean for callers interested only in media retention."""
    return _destination_decision(
        src_path,
        dest_path,
        music_library_dir,
    ).retain


def _synchronize_better_destination_tags(
    src_path: str,
    dest_path: str,
    expected_destination_snapshot: Optional[
        Tuple[int, int, int, int, int]
    ],
) -> bool:
    """Converge canonical FLAC tags without replacing better audio frames."""
    if (
        os.path.splitext(src_path)[1].lower() != ".flac"
        or os.path.splitext(dest_path)[1].lower() != ".flac"
    ):
        return False
    if not tag_service.has_complete_picard_tags(src_path):
        logger.warning(
            "ingest: retained better audio without tag sync because the "
            "staged FLAC lacks complete Picard tags: %s",
            src_path,
        )
        return False
    return tag_service.copy_complete_picard_tags_atomic(
        src_path,
        dest_path,
        expected_destination_snapshot=expected_destination_snapshot,
    )


def _atomic_copy_into_place(
    src_path: str,
    dest_path: str,
    music_library_dir: str,
) -> bool:
    """Atomically publish a copy unless a concurrent better copy appeared."""
    destination_dir = os.path.dirname(dest_path)
    temp_fd = -1
    temp_path: Optional[str] = None
    try:
        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(dest_path)}.dapingest-",
            suffix=".tmp",
            dir=destination_dir,
        )
        with open(src_path, "rb") as source_file, os.fdopen(
            temp_fd, "wb"
        ) as temp_file:
            temp_fd = -1
            shutil.copyfileobj(source_file, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        source_mode = stat.S_IMODE(os.stat(src_path).st_mode)
        os.chmod(temp_path, source_mode)
        try:
            shutil.copystat(src_path, temp_path)
        except OSError as exc:
            logger.debug("ingest: could not copy file timestamps: %s", exc)

        # A different worker may have published a better file while the
        # candidate was being copied. Re-check immediately before replacement.
        for attempt in range(3):
            decision = _destination_decision(
                src_path,
                dest_path,
                music_library_dir,
            )
            if not decision.retain:
                break
            try:
                tags_changed = (
                    _synchronize_better_destination_tags(
                        src_path,
                        dest_path,
                        decision.destination_snapshot,
                    )
                    if decision.destination_better
                    else False
                )
            except tag_service.TagSynchronizationRace:
                if attempt == 2:
                    raise
                continue
            logger.info(
                "ingest: retaining concurrently-written equal-or-better file %s",
                dest_path,
            )
            return tags_changed

        # Re-resolve the destination immediately before replacement.  This
        # catches a swapped symlink or directory that appeared while copying.
        _validated_library_path(
            dest_path,
            music_library_dir,
            require_existing=False,
            label="ingest destination",
        )
        os.replace(temp_path, dest_path)
        temp_path = None
        _fsync_directory(destination_dir)
        return True
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _move_to_library(
    src_path: str,
    dest_path: str,
    music_library_dir: str,
) -> IngestResult:
    """Publish a candidate without ever downgrading an existing destination."""
    validated = _validated_library_path(
        dest_path,
        music_library_dir,
        require_existing=False,
        label="ingest destination",
    )
    assert validated is not None
    dest_path = validated
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    # Re-check after directory creation in case a pre-existing parent resolved
    # through a symlink or the final component appeared concurrently.
    _validated_library_path(
        dest_path,
        music_library_dir,
        require_existing=False,
        label="ingest destination",
    )
    same_file = _same_path(src_path, dest_path)
    if same_file:
        return IngestResult(path=dest_path, changed=False)

    if os.path.lexists(dest_path):
        for attempt in range(3):
            decision = _destination_decision(
                src_path,
                dest_path,
                music_library_dir,
            )
            if not decision.retain:
                break
            try:
                tags_changed = (
                    _synchronize_better_destination_tags(
                        src_path,
                        dest_path,
                        decision.destination_snapshot,
                    )
                    if decision.destination_better
                    else False
                )
            except tag_service.TagSynchronizationRace:
                if attempt == 2:
                    raise
                continue
            logger.info(
                "ingest: retaining equal-or-better existing file %s",
                dest_path,
            )
            _remove_superseded_source(src_path)
            return IngestResult(path=dest_path, changed=tags_changed)
        logger.info("ingest: atomically upgrading existing %s", dest_path)

    changed = _atomic_copy_into_place(
        src_path,
        dest_path,
        music_library_dir,
    )
    _remove_superseded_source(src_path)
    return IngestResult(path=dest_path, changed=changed)


def _ingest_audio_file(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    mbid_guess: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    album: Optional[str] = None,
    track_number: Optional[int] = None,
    disc_number: Optional[int] = None,
    recording_mbid: Optional[str] = None,
    prefer_scanned_identity: bool,
) -> IngestResult:
    # Normalize before the scanner sees the file.  Otherwise an upper-case
    # tag can be inserted as a distinct primary key beside the canonical
    # lower-case MusicBrainz UUID already present in the catalog.
    embedded_mbid = normalize_embedded_recording_mbid(src_path)
    canonical_guess = canonical_recording_mbid(mbid_guess)
    if canonical_guess is not None:
        # Contribution callers pass the requested recording as ``mbid_guess``.
        # Preserve legacy non-UUID guesses, but ensure a real MusicBrainz UUID
        # reaches the scanner in canonical form.
        mbid_guess = canonical_guess
    if recording_mbid:
        supplied_recording_mbid = recording_mbid.strip() or None
        recording_mbid = canonical_recording_mbid(supplied_recording_mbid)
        if supplied_recording_mbid and recording_mbid is None:
            raise ValueError("explicit recording MBID is not a valid UUID")
    if recording_mbid and embedded_mbid and recording_mbid != embedded_mbid:
        raise ValueError(
            "explicit recording MBID does not match the staged file tags"
        )

    # Make sure the file carries our MBID so the scanner keys it correctly.
    if mbid_guess and not embedded_mbid and not recording_mbid:
        try:
            write_mbid_to_file(src_path, mbid_guess)
            embedded_mbid = read_embedded_recording_mbid(src_path)
        except Exception as e:
            logger.warning("ingest: could not write MBID to %s: %s", src_path, e)

    norm_src = _normalized_path(src_path)
    track = db.get_track_by_path(norm_src)
    if track is None:
        # Scan tags → upserts a track row with local_path = src_path.  A
        # downloader may already have scanned the temporary file; avoid doing
        # that work twice when its row is present.
        try:
            _scan_file(scanner, src_path)
        except Exception as e:
            logger.warning("ingest: scan failed for %s: %s", src_path, e)
        track = db.get_track_by_path(norm_src)

    # The scanner may have tagged the file (for example through Picard), so
    # read the recording identity once more before resolving a duplicate.
    scanned_embedded_mbid = read_embedded_recording_mbid(src_path)
    if (
        recording_mbid
        and scanned_embedded_mbid
        and recording_mbid != scanned_embedded_mbid
    ):
        raise ValueError(
            "explicit recording MBID does not match the scanned file tags"
        )
    exact_recording_mbid = (
        scanned_embedded_mbid or embedded_mbid or recording_mbid
    )
    if (
        track is not None
        and exact_recording_mbid
        and track.mbid
        and track.mbid != exact_recording_mbid
    ):
        raise ValueError(
            "scanner recording MBID does not match the staged file tags"
        )

    identity_mbid = (
        (track.mbid if track is not None else None)
        or exact_recording_mbid
        or mbid_guess
    )
    existing_track = (
        db.get_track_by_mbid(identity_mbid) if identity_mbid else None
    )
    canonical_path = None
    if existing_track is not None and existing_track.local_path:
        existing_norm = _normalized_path(existing_track.local_path)
        # A freshly scanned staging row points at ``src_path`` outside the
        # library and is not canonical yet.  The same path inside the library
        # is already canonical and should remain stable.
        # Only an exact stored staging path is treated as the scanner's
        # transient row.  A distinct symlink/hardlink that happens to resolve
        # to the source is still persisted canonical state and must pass the
        # strict validation below.
        source_is_existing_row = (
            os.path.abspath(existing_norm) == os.path.abspath(norm_src)
        )
        source_inside_library = _is_within(
            _resolved(norm_src),
            _resolved(music_library_dir),
        )
        if not source_is_existing_row or source_inside_library:
            canonical_path = _validated_library_path(
                existing_norm,
                music_library_dir,
                require_existing=True,
                label="database canonical path",
            )

    supplied_identity = _fallback_track(
        norm_src,
        mbid_guess=identity_mbid,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        disc_number=disc_number,
    )
    if track is None:
        if existing_track is not None:
            # Duplicate scans deliberately do not create a staged-path row.
            # Preserve the catalog's identity and route to its canonical path.
            track = existing_track
        else:
            # Scanner produced no row — fall back to the supplied identity.
            track = supplied_identity
            if track.mbid:
                db.add_or_update_track(track)

    path_identity = track if prefer_scanned_identity else supplied_identity
    dest_path = canonical_path or library_path_for_track(
        music_library_dir,
        path_identity,
    )
    ingest_result = _move_to_library(
        src_path,
        dest_path,
        music_library_dir,
    )

    mbid = track.mbid or identity_mbid
    if mbid:
        db.update_track_local_path(mbid, dest_path)
    return ingest_result


def ingest_audio_file(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    mbid_guess: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    album: Optional[str] = None,
    track_number: Optional[int] = None,
    disc_number: Optional[int] = None,
    recording_mbid: Optional[str] = None,
) -> str:
    """Move ``src_path`` into ``music_library_dir`` and link the track row.

    Returns the final library path. ``artist``/``title``/``album`` are used as
    a fallback identity when the file's tags don't yield a catalog row (e.g.
    the scanner couldn't fingerprint it).
    """
    return _ingest_audio_file(
        db,
        scanner,
        music_library_dir,
        src_path,
        mbid_guess=mbid_guess,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        disc_number=disc_number,
        recording_mbid=recording_mbid,
        prefer_scanned_identity=True,
    ).path


def ingest_downloaded_audio_file(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    artist: str,
    title: str,
    album: str,
    track_number: int,
    disc_number: int = 1,
    recording_mbid: Optional[str] = None,
) -> str:
    """Ingest a downloader file while preserving its legacy sort identity.

    The downloader historically chose the destination from mutagen/auto-tag
    metadata even after the scanner had populated the database.  Keeping that
    choice here lets both download and contribution paths share one mutation
    pipeline without changing existing folder names.
    """
    return ingest_downloaded_audio_file_with_result(
        db,
        scanner,
        music_library_dir,
        src_path,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        disc_number=disc_number,
        recording_mbid=recording_mbid,
    ).path


def ingest_downloaded_audio_file_with_result(
    db: DatabaseManager,
    scanner: LibraryScanner,
    music_library_dir: str,
    src_path: str,
    *,
    artist: str,
    title: str,
    album: str,
    track_number: int,
    disc_number: int = 1,
    recording_mbid: Optional[str] = None,
) -> IngestResult:
    """Downloaded-file ingest including whether canonical audio changed."""
    return _ingest_audio_file(
        db,
        scanner,
        music_library_dir,
        src_path,
        artist=artist,
        title=title,
        album=album,
        track_number=track_number,
        disc_number=disc_number,
        recording_mbid=recording_mbid,
        prefer_scanned_identity=False,
    )
