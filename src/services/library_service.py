"""Typed library query policy and public response shaping.

The HTTP adapter owns request parsing, database lifetime, and ``jsonify``.
This module owns which library query is used and how private catalog rows are
turned into the stable desktop wire shapes.
"""

from typing import (
    Any,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    TypeAlias,
)


TrackAvailability: TypeAlias = Literal[
    "local",
    "drive",
    "remote",
    "unavailable",
]


class LibraryQueryStore(Protocol):
    """Database facade required by the public library endpoints."""

    def list_albums(self) -> List[dict]: ...

    def list_all_tracks(self) -> List[dict]: ...

    def list_tracks_filtered(
        self,
        playlist_id: Optional[str] = None,
        local_only: bool = False,
        include_orphans: bool = False,
    ) -> List[dict]: ...

    def list_album_tracks(self, album_id: str) -> List[dict]: ...

    def build_artist_radio(
        self,
        artist_name: str,
        limit: int = 50,
    ) -> dict: ...


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


def list_public_albums(db: LibraryQueryStore) -> Dict[str, Any]:
    """Return the album-grid payload without leaking local cover paths."""
    albums = [
        {
            "id": album["id"],
            "title": album["title"],
            "artist": album["artist"],
            "track_count": album["track_count"],
        }
        for album in db.list_albums()
    ]
    return {"success": True, "albums": albums}


def query_public_tracks(
    db: LibraryQueryStore,
    *,
    playlist_id: Optional[str] = None,
    local_only: bool = False,
    include_orphans: bool = False,
    has_master: bool = False,
) -> Dict[str, Any]:
    """Query and shape the flat library while preserving orphan policy.

    The unfiltered path deliberately uses ``list_all_tracks`` for backward
    compatibility.  Filtered queries are delegated to the database so smart
    playlist ordering and local-only semantics remain authoritative there.
    """
    has_filters = bool(playlist_id) or local_only or include_orphans
    if has_filters:
        rows = db.list_tracks_filtered(
            playlist_id=playlist_id,
            local_only=local_only,
            include_orphans=include_orphans,
        )
    else:
        rows = db.list_all_tracks()

    tracks: List[Dict[str, Any]] = []
    for row in rows:
        availability = availability_for(row, has_master)
        is_orphan = bool(row.get("deleted_at"))
        if availability == "unavailable" and not (
            include_orphans and is_orphan
        ):
            continue

        public = public_track_row(row, has_master)
        if include_orphans:
            public["orphan"] = is_orphan
        tracks.append(public)

    return {"success": True, "tracks": tracks}


def list_local_album_tracks(
    db: LibraryQueryStore,
    album_id: str,
    *,
    has_master: bool,
) -> Dict[str, Any]:
    """Return ordered playable replica rows for an album fallback."""
    tracks = [
        public_track_row({**row, "album_id": album_id}, has_master)
        for row in db.list_album_tracks(album_id)
        if availability_for(row, has_master) != "unavailable"
    ]
    return {"success": True, "tracks": tracks}


def build_artist_radio_payload(
    db: LibraryQueryStore,
    artist_name: str,
    *,
    limit: int,
    has_master: bool,
) -> Dict[str, Any]:
    """Build the public Artist Radio response, dropping unplayable rows."""
    result = db.build_artist_radio(artist_name, limit=limit)
    tracks = [
        public_track_row(row, has_master)
        for row in result["tracks"]
        if availability_for(row, has_master) != "unavailable"
    ]
    return {
        "success": True,
        "tracks": tracks,
        "top_tag": result["top_tag"],
        "seed_count": result["seed_count"],
        "related_count": result["related_count"],
    }
