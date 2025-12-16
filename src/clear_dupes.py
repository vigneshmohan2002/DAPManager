import csv
import logging
import os
import re
from collections import defaultdict
from .db_manager import DatabaseManager
from .logger_setup import setup_logging
from .config_manager import get_config
from .utils import get_mbid_from_tags

logger = logging.getLogger(__name__)

# --- Pattern Definitions ---
track_num_regex = re.compile(r"^\s*\d+\s*[-–—]\s*.+")
windows_copy_regex = re.compile(r".+\s\(\d+\)\.[^.]+$", re.IGNORECASE)
feat_regex = re.compile(r".+\s\(feat\..+\)\.[^.]+$", re.IGNORECASE)


def get_file_score(path: str) -> int:
    """
    Calculates a score for a file path. Higher is better.
    """
    base = path.split("\\")[-1].strip('"')
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
            clean_path = path.strip('"')
            score = get_file_score(clean_path)
            candidates.append({"path": clean_path, "score": score})

        # Sort by score descending
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Mark recommendation
        if candidates:
            candidates[0]["is_recommended"] = True
            for c in candidates[1:]:
                c["is_recommended"] = False

        results.append({
            "mbid": mbid,
            "artist": artist,
            "title": title,
            "candidates": candidates
        })
    return results

def resolve_duplicates(db: DatabaseManager, mbid: str, keep_path: str, delete_paths: list):
    """
    Resolves a duplicate group by:
    1. Updating the DB to point to 'keep_path'
    2. Deleting files in 'delete_paths'
    3. Clearing the duplicate entry
    """
    # Update DB
    if keep_path:
        db.update_track_local_path(mbid, keep_path)
    
    # Delete files
    deleted = []
    errors = []
    for path in delete_paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                deleted.append(path)
            else:
                errors.append(f"File not found: {path}")
        except Exception as e:
            errors.append(f"Error deleting {path}: {e}")

    # Clear duplicate entry
    db.clear_duplicate(mbid)
    
    return {"deleted": deleted, "errors": errors}

def find_and_resolve_duplicates(db: DatabaseManager):
    """
    Main Logic for CLI/Script usage.
    """
    print("Use the Web UI for duplicate management.")



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
