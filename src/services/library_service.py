"""Pure library response shaping used by the Flask adapter."""

from typing import Any, Dict, Literal, Mapping, TypeAlias


TrackAvailability: TypeAlias = Literal[
    "local",
    "drive",
    "remote",
    "unavailable",
]


def is_master_configured(config_data: object) -> bool:
    """Return whether a concrete configuration dictionary names a master."""
    if not isinstance(config_data, dict):
        return False
    return bool((config_data.get("master_url") or "").strip())


def availability_for(
    row: Mapping[str, Any],
    has_master: bool,
) -> TrackAvailability:
    """Resolve the existing local, drive, remote playback priority."""
    if (row.get("local_path") or "").strip():
        return "local"
    if (row.get("dap_path") or "").strip():
        return "drive"
    if has_master:
        return "remote"
    return "unavailable"


def public_track_row(
    row: Mapping[str, Any],
    has_master: bool,
) -> Dict[str, Any]:
    """Build the stable public track shape without exposing filesystem paths."""
    return {
        "mbid": row["mbid"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row.get("album"),
        "track_number": row.get("track_number"),
        "disc_number": row.get("disc_number"),
        "album_id": row.get("album_id"),
        "availability": availability_for(row, has_master),
        "is_liked": bool(row.get("is_liked")),
    }
