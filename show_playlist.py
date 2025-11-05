#!/usr/bin/env python
"""
Script to print all tracks for a given playlist, in order.
"""

import sys
import logging
from db_manager import DatabaseManager
from logger_setup import (
    setup_logging,
)  # Assuming this is in a file named logger_setup.py

DB_FILE = "dap_library.db"
logger = logging.getLogger(__name__)


def list_all_playlists(db: DatabaseManager):
    """Prints all available playlists and their IDs."""
    logger.info("No playlist ID provided. Listing all available playlists...")

    playlists = db.get_all_playlists()

    if not playlists:
        print("No playlists found in the database.")
        return

    print("Usage: python show_playlist.py <playlist_id>")
    print("\n--- Available Playlists ---")
    for p in playlists:
        print(f"  Name: {p.name}")
        print(f"  ID:   {p.playlist_id}\n")


def print_playlist_tracks(db: DatabaseManager, playlist_id: str):
    """Fetches and prints all tracks for a specific playlist ID."""
    logger.info(f"Fetching tracks for playlist: {playlist_id}")

    # We use the existing function which already sorts by track_order
    tracks = db.get_playlist_tracks(playlist_id)

    if not tracks:
        print(f"No tracks found for playlist ID '{playlist_id}'.")
        print("Please check the ID and try again.")
        return

    print(f"\n--- Tracks for Playlist ({playlist_id}) ---")

    # Loop and print in order. The __str__ method on Track is used.
    for i, track in enumerate(tracks):
        print(f"  {i+1:03d}: [MBID: {track.mbid}] {track}")

    print(f"\nTotal: {len(tracks)} tracks")


def main():
    """Main entry point for the script."""
    try:
        playlist_id = sys.argv[1]
    except IndexError:
        # No playlist ID was provided
        with DatabaseManager(DB_FILE) as db:
            list_all_playlists(db)
        sys.exit(0)

    # Playlist ID was provided
    try:
        with DatabaseManager(DB_FILE) as db:
            print_playlist_tracks(db, playlist_id)
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        print(f"An error occurred. Check the log for details.")


if __name__ == "__main__":
    setup_logging()  # Initialize your logging config
    main()
