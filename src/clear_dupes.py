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


def find_and_resolve_duplicates(db: DatabaseManager):
    """
    Finds all duplicates from the DB, uses a scoring system
    to find the "best" file, and applies fixes.
    """
    logger.info("Starting duplicate analysis and fix process...")

    # --- 1. Fetch all duplicates directly from the database ---
    try:
        # get_all_duplicates() returns a Dict[str, List[str]]
        # e.g., {'mbid-123': ['/path/a.flac', '/path/b.mp3']}
        duplicates_map = db.get_all_duplicates()

        if not duplicates_map:
            logger.info("No duplicates found in the database. All done!")
            return

        logger.info(f"Found {len(duplicates_map)} duplicate MBID groups to analyze.")

    except Exception as e:
        logger.error(f"FATAL: Error fetching duplicates from database: {e}")
        return

    # --- 2. Analyze and Fix each group ---
    paths_to_delete = []
    unresolved_patterns = []

    updated_count = 0
    cleared_count = 0
    sanity_failures = 0
    error_count = 0

    for mbid, paths in duplicates_map.items():

        # --- 2a. Score all candidate files ---
        candidates = []
        for path in paths:
            # Handle potential quotes in file paths
            clean_path = path.strip('"')
            score = get_file_score(clean_path)
            candidates.append((score, clean_path))

        # Sort candidates by score, descending. Highest score is best.
        candidates.sort(key=lambda x: x[0], reverse=True)

        # --- 2b. Check for a clear winner ---
        if len(candidates) < 2:
            continue  # Should not happen, but good to check

        best_score = candidates[0][0]
        good_path_to_keep = candidates[0][1]
        second_best_score = candidates[1][0]

        if best_score == second_best_score:
            # --- UNRESOLVABLE TIE ---
            unresolved_patterns.append(
                {
                    "mbid": mbid,
                    "reason": f"Tie for best file (Score: {best_score}). Requires manual review.",
                    "paths": [c[1] for c in candidates],
                }
            )
            continue

        # --- 2c. We have a clear winner. Sanity Check and Fix ---
        try:
            mbid_from_file = get_mbid_from_tags(good_path_to_keep)

            if mbid_from_file != mbid:
                logger.error(f"SANITY CHECK FAILED for {good_path_to_keep}")
                logger.error(f"  Reported MBID: {mbid}")
                logger.error(f"  File contains MBID: {mbid_from_file}")
                unresolved_patterns.append(
                    {
                        "mbid": mbid,
                        "reason": "Sanity check failed (File MBID did not match DB MBID)",
                        "paths": paths,
                    }
                )
                sanity_failures += 1
                continue

            # --- All checks passed, let's fix it! ---

            # Update the 'tracks' table
            track = db.get_track_by_mbid(mbid)
            if track:
                db.update_track_local_path(mbid, good_path_to_keep)
                logger.debug(f"Updated local_path for {mbid} to {good_path_to_keep}")
                updated_count += 1
            else:
                logger.warning(f"Track with MBID {mbid} not found. Cannot update path.")
                error_count += 1
                continue

            # Clear the duplicate log entry
            db.clear_duplicate(mbid)
            logger.debug(f"Cleared duplicate log entries for {mbid}")
            cleared_count += 1

            # Add all other files to the deletion list
            paths_to_delete.extend([c[1] for c in candidates[1:]])

        except Exception as e:
            logger.error(f"Failed to process {mbid}: {e}", exc_info=True)
            error_count += 1

    # --- 3. Write output files ---
    logger.info("--- Analysis and Fix Complete ---")

    if paths_to_delete:
        with open("files_to_delete.txt", "w", encoding="utf-8") as f:
            for path in paths_to_delete:
                f.write(f"{path}\n")
        logger.info(
            f"Generated 'files_to_delete.txt' with {len(paths_to_delete)} paths."
        )

    if unresolved_patterns:
        with open("unresolved_duplicates.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["mbid", "reason", "paths"])
            writer.writeheader()
            writer.writerows(unresolved_patterns)
        logger.info(
            f"Generated 'unresolved_duplicates.csv' with {len(unresolved_patterns)} groups."
        )

    logger.info("--- Final Summary ---")
    logger.info(f"Tracks updated: {updated_count}")
    logger.info(f"Duplicate log groups cleared: {cleared_count}")
    logger.info(f"Sanity check failures: {sanity_failures}")
    logger.info(
        f"Unresolved groups (Ties): {len(unresolved_patterns) - sanity_failures}"
    )
    logger.info(f"Other errors: {error_count}")


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
