import logging
import os
import re
import stat
from dataclasses import dataclass
from typing import Dict, List, Sequence
from .db_manager import DatabaseManager
from .logger_setup import setup_logging
from .config_manager import get_config
from .utils import get_mbid_from_tags, get_release_mbid_from_tags

logger = logging.getLogger(__name__)

# --- Pattern Definitions ---
track_num_regex = re.compile(r"^\s*\d+\s*[-–—]\s*.+")
windows_copy_regex = re.compile(r".+\s\(\d+\)\.[^.]+$", re.IGNORECASE)
feat_regex = re.compile(r".+\s\(feat\..+\)\.[^.]+$", re.IGNORECASE)


def get_file_score(path: str) -> int:
    """
    Calculates a score for a file path. Higher is better.
    """
    # Normalize path just in case
    path = path.replace("\\", "/")
    base = os.path.basename(path).strip('"')
    ext = os.path.splitext(base)[1].lower()

    score = 100  # Base score

    # --- Filename Penalties (lower is worse) ---
    if windows_copy_regex.search(base):
        score -= 20  # e.g., "Song (1).flac"
    elif track_num_regex.match(base):
        score -= 10  # e.g., "01 - Song.flac"
    elif feat_regex.search(base):
        score -= 5  # e.g., "Song (feat. Artist).flac"

    # --- File Type Bonuses (higher is better) ---
    if ext == ".flac":
        score += 10
    elif ext == ".m4a":
        score += 5
    elif ext == ".mp3":
        score += 1

    return score


def get_duplicates_for_ui(db: DatabaseManager):
    """
    Returns a list of duplicate groups for the UI.
    Each group contains:
      - mbid
      - artist, title (if available)
      - candidates: List of {path, score, is_recommended}
    """
    duplicates_map = db.get_all_duplicates()
    if not duplicates_map:
        return []

    results = []
    
    for mbid, paths in duplicates_map.items():
        # Get basic metadata for display
        track = db.get_track_by_mbid(mbid)
        artist = track.artist if track else "Unknown Artist"
        title = track.title if track else "Unknown Title"

        candidates = []
        for path in paths:
            clean_path = _clean_path(path)
            score = get_file_score(clean_path)
            safe_file = _regular_non_symlink_file(clean_path)
            embedded_mbid = get_mbid_from_tags(clean_path) if safe_file else None
            release_mbid = (
                get_release_mbid_from_tags(clean_path) if safe_file else None
            )
            identity_status = (
                "match"
                if embedded_mbid and _same_mbid(embedded_mbid, mbid)
                else "mismatch"
                if embedded_mbid
                else "unknown"
            )
            candidates.append({
                "path": clean_path,
                "score": score,
                "exists": os.path.exists(clean_path),
                "is_safe_file": safe_file,
                "identity_status": identity_status,
                "release_mbid": str(release_mbid or "").strip(),
            })

        release_ids = {
            candidate["release_mbid"].casefold()
            for candidate in candidates
            if candidate["release_mbid"]
        }
        release_conflict = len(release_ids) > 1

        # Sort by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Mark recommendation
        recommendable = [
            candidate
            for candidate in candidates
            if candidate["is_safe_file"]
            and candidate["identity_status"] == "match"
            and not release_conflict
        ]
        recommended = recommendable[0] if recommendable else None
        for candidate in candidates:
            candidate["is_recommended"] = candidate is recommended

        results.append({
            "mbid": mbid,
            "artist": artist,
            "title": title,
            "release_conflict": release_conflict,
            "candidates": candidates
        })
    return results

def _clean_path(path: object) -> str:
    return os.path.abspath(os.path.normpath(str(path or "").strip().strip('"')))


def _path_key(path: str) -> str:
    return os.path.normcase(_clean_path(path))


def _same_mbid(left: object, right: object) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _regular_non_symlink_file(path: str) -> bool:
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


@dataclass(frozen=True)
class DuplicateResolutionPlan:
    mbid: str
    keep_path: str
    delete_paths: Sequence[str]
    untouched_paths: Sequence[str]
    missing_paths: Sequence[str]


def _validate_duplicate_identity(path: str, mbid: str) -> None:
    embedded_mbid = get_mbid_from_tags(path)
    if not embedded_mbid:
        raise ValueError(f"Cannot verify MusicBrainz identity: {path}")
    if not _same_mbid(embedded_mbid, mbid):
        raise ValueError(f"MusicBrainz identity does not match duplicate group: {path}")


def build_duplicate_resolution_plan(
    db: DatabaseManager,
    mbid: str,
    keep_path: str,
    delete_paths: Sequence[str],
) -> DuplicateResolutionPlan:
    """Validate authority, identity, aliases, and existence before mutation."""
    normalized_mbid = str(mbid or "").strip()
    if not normalized_mbid:
        raise ValueError("mbid is required")
    if db.get_track_sources(normalized_mbid) is None:
        raise ValueError("Duplicate group does not reference a live catalog track")

    groups = db.get_all_duplicates()
    group_paths = groups.get(normalized_mbid) or []
    if len(group_paths) < 2:
        raise ValueError("Duplicate group no longer exists or has fewer than two paths")
    authorized: Dict[str, str] = {}
    for raw_path in group_paths:
        clean_path = _clean_path(raw_path)
        authorized.setdefault(_path_key(clean_path), clean_path)

    embedded_releases = set()
    for candidate in authorized.values():
        if not _regular_non_symlink_file(candidate):
            continue
        embedded_mbid = get_mbid_from_tags(candidate)
        if embedded_mbid and not _same_mbid(embedded_mbid, normalized_mbid):
            raise ValueError(
                f"MusicBrainz identity does not match duplicate group: {candidate}"
            )
        release_mbid = str(
            get_release_mbid_from_tags(candidate) or ""
        ).strip()
        if release_mbid:
            embedded_releases.add(release_mbid.casefold())
    if len(embedded_releases) > 1:
        raise ValueError(
            "Duplicate candidates belong to different MusicBrainz releases; "
            "keep them as separate album-edition files"
        )

    clean_keep = _clean_path(keep_path)
    keep_key = _path_key(clean_keep)
    if keep_key not in authorized:
        raise ValueError("keep_path is not a member of this duplicate group")
    clean_keep = authorized[keep_key]
    if not _regular_non_symlink_file(clean_keep):
        raise ValueError("keep_path is missing, not a regular file, or is a symlink")
    _validate_duplicate_identity(clean_keep, normalized_mbid)

    requested_keys = set()
    validated_deletes: List[str] = []
    missing_paths: List[str] = []
    for raw_path in delete_paths or []:
        requested = _clean_path(raw_path)
        requested_key = _path_key(requested)
        if requested_key in requested_keys:
            continue
        requested_keys.add(requested_key)
        if requested_key not in authorized:
            raise ValueError(f"delete path is not a member of this duplicate group: {requested}")
        candidate = authorized[requested_key]
        if requested_key == keep_key:
            raise ValueError("delete_paths includes keep_path")
        if not os.path.lexists(candidate):
            missing_paths.append(candidate)
            continue
        if not _regular_non_symlink_file(candidate):
            raise ValueError(f"delete path is not a regular non-symlink file: {candidate}")
        try:
            if os.path.samefile(clean_keep, candidate):
                raise ValueError("delete path aliases the kept file")
        except OSError as exc:
            raise ValueError(f"Could not compare duplicate paths: {exc}") from exc
        _validate_duplicate_identity(candidate, normalized_mbid)
        validated_deletes.append(candidate)

    untouched = [
        path
        for key, path in authorized.items()
        if key != keep_key and key not in requested_keys
    ]
    return DuplicateResolutionPlan(
        mbid=normalized_mbid,
        keep_path=clean_keep,
        delete_paths=tuple(validated_deletes),
        untouched_paths=tuple(untouched),
        missing_paths=tuple(missing_paths),
    )


def resolve_duplicates(
    db: DatabaseManager,
    mbid: str,
    keep_path: str,
    delete_paths: Sequence[str],
):
    """Resolve only a fully validated plan and retain every unresolved copy."""
    plan = build_duplicate_resolution_plan(db, mbid, keep_path, delete_paths)

    # Point the catalog at the chosen keeper before deleting any old canonical
    # path. If a later unlink fails, playback still targets a valid file.
    db.update_track_local_path(plan.mbid, plan.keep_path)

    deleted: List[str] = []
    errors: List[str] = []
    failed_paths: List[str] = []
    for path in plan.delete_paths:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError as exc:
            failed_paths.append(path)
            errors.append(f"Error deleting {path}: {exc}")

    remaining = list(dict.fromkeys([
        plan.keep_path,
        *plan.untouched_paths,
        *failed_paths,
    ]))
    # A single keeper is not a duplicate group. Failed or deliberately
    # untouched copies stay visible for a later review.
    db.replace_duplicate_paths(
        plan.mbid,
        remaining if len(remaining) > 1 else [],
    )
    resolved = len(remaining) <= 1
    return {
        "deleted": deleted,
        "errors": errors,
        "missing": list(plan.missing_paths),
        "remaining": remaining if not resolved else [],
        "resolved": resolved,
    }

def find_and_resolve_duplicates(db: DatabaseManager):
    """
    Main Logic for CLI/Script usage.
    """
    logger.info("Use the Web UI for duplicate management.")



if __name__ == "__main__":
    setup_logging()

    try:
        config = get_config()
        db_path = config.db_path

        if not db_path:
            logger.error("db_path not found in config. Exiting.")
            exit(1)

        # Use the context manager to ensure DB is closed
        with DatabaseManager(db_path) as db:
            # Run the all-in-one script
            find_and_resolve_duplicates(db)

    except Exception as e:
        logger.error(f"Main process failed: {e}", exc_info=True)
