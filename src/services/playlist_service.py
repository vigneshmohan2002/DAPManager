"""Typed business operations for desktop-library playlist routes.

Flask remains responsible for configuration checks, opening the database,
logging unexpected failures, and converting service outcomes with ``jsonify``.
This module preserves the established request validation order and delegates
all persistence and transaction behavior to the existing database façade.
"""

from dataclasses import dataclass
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Union,
    cast,
)


class PlaylistStore(Protocol):
    """Small database façade consumed by the library playlist endpoints."""

    def list_playlists_with_counts(self) -> List[Dict[str, Any]]: ...

    def create_playlist(
        self, name: str, smart_rules: Optional[str] = None
    ) -> str: ...

    def get_playlist(self, playlist_id: str) -> Optional[object]: ...

    def rename_playlist(self, playlist_id: str, name: str) -> bool: ...

    def replace_playlist_membership(
        self, playlist_id: str, track_mbids: List[str]
    ) -> int: ...

    def update_playlist_smart_rules(
        self, playlist_id: str, smart_rules: Optional[str]
    ) -> bool: ...

    def apply_pushed_playlist_row(self, row: Any) -> str: ...

    def soft_delete_playlist(self, playlist_id: str) -> bool: ...

    def purge_playlist(self, playlist_id: str) -> bool: ...


@dataclass(frozen=True)
class PlaylistServiceResult:
    """JSON-shaped outcome translated into an HTTP response by Flask."""

    payload: Dict[str, Any]
    status_code: int = 200


@dataclass(frozen=True)
class CreatePlaylistRequest:
    name: str
    smart_rules: Optional[str]


@dataclass(frozen=True)
class UpdatePlaylistRequest:
    """Structurally validated partial update, before database lookup."""

    data: Mapping[str, Any]
    has_name: bool
    has_tracks: bool
    has_rules: bool
    smart_rules: Optional[str]


@dataclass(frozen=True)
class DeletePlaylistRequest:
    """Authorized playlist deletion and its selected mutation mode."""

    playlist_id: str
    purge: bool


PreparedCreate = Union[CreatePlaylistRequest, PlaylistServiceResult]
PreparedUpdate = Union[UpdatePlaylistRequest, PlaylistServiceResult]
PreparedDelete = Union[DeletePlaylistRequest, PlaylistServiceResult]


def list_library_playlists(db: PlaylistStore) -> PlaylistServiceResult:
    """Return live playlist rows with nested smart-rule JSON decoded."""
    from ..smart_playlist import parse_stored

    rows = db.list_playlists_with_counts()
    for row in rows:
        row["smart_rules"] = parse_stored(row.get("smart_rules"))
    return PlaylistServiceResult({"success": True, "playlists": rows})


def prepare_playlist_create(data: Mapping[str, Any]) -> PreparedCreate:
    """Validate and normalize a create request before opening the database."""
    from ..smart_playlist import serialize

    name = (data.get("name") or "").strip()
    if not name:
        return PlaylistServiceResult(
            {"success": False, "message": "name is required"}, 400
        )
    try:
        smart_rules = serialize(data.get("smart_rules"))
    except ValueError as exc:
        return PlaylistServiceResult(
            {"success": False, "message": str(exc)}, 400
        )
    return CreatePlaylistRequest(name=name, smart_rules=smart_rules)


def create_library_playlist(
    db: PlaylistStore,
    prepared: CreatePlaylistRequest,
) -> PlaylistServiceResult:
    """Persist a validated playlist using the existing façade call shape."""
    playlist_id = db.create_playlist(
        prepared.name,
        smart_rules=prepared.smart_rules,
    )
    return PlaylistServiceResult(
        {
            "success": True,
            "playlist_id": playlist_id,
            "name": prepared.name,
        },
        201,
    )


def prepare_playlist_update(data: object) -> PreparedUpdate:
    """Validate request structure and smart rules before database access.

    Name and membership values are deliberately validated later, after the
    playlist-existence lookup, matching the route's historical ordering.
    """
    if not isinstance(data, dict):
        return PlaylistServiceResult(
            {"success": False, "message": "body must be an object"}, 400
        )

    has_name = "name" in data
    has_tracks = "track_mbids" in data
    has_rules = "smart_rules" in data
    if not has_name and not has_tracks and not has_rules:
        return PlaylistServiceResult(
            {
                "success": False,
                "message": (
                    "at least one of 'name', 'track_mbids', or "
                    "'smart_rules' is required"
                ),
            },
            400,
        )
    if has_tracks and has_rules:
        return PlaylistServiceResult(
            {
                "success": False,
                "message": "track_mbids and smart_rules are mutually exclusive",
            },
            400,
        )

    smart_rules: Optional[str] = None
    if has_rules:
        from ..smart_playlist import serialize

        try:
            smart_rules = serialize(data.get("smart_rules"))
        except ValueError as exc:
            return PlaylistServiceResult(
                {"success": False, "message": str(exc)}, 400
            )

    return UpdatePlaylistRequest(
        data=data,
        has_name=has_name,
        has_tracks=has_tracks,
        has_rules=has_rules,
        smart_rules=smart_rules,
    )


def update_library_playlist(
    db: PlaylistStore,
    playlist_id: str,
    prepared: UpdatePlaylistRequest,
) -> PlaylistServiceResult:
    """Apply a validated partial update in the established mutation order."""
    if db.get_playlist(playlist_id) is None:
        return PlaylistServiceResult(
            {
                "success": False,
                "message": "playlist not found or deleted",
            },
            404,
        )

    renamed = False
    landed: Optional[int] = None
    requested: Optional[int] = None
    rules_changed = False

    if prepared.has_name:
        new_name = (prepared.data.get("name") or "").strip()
        if not new_name:
            return PlaylistServiceResult(
                {"success": False, "message": "name must not be empty"}, 400
            )
        renamed = db.rename_playlist(playlist_id, new_name)

    if prepared.has_tracks:
        track_mbids = prepared.data.get("track_mbids")
        if not isinstance(track_mbids, list):
            return PlaylistServiceResult(
                {
                    "success": False,
                    "message": "track_mbids must be a list",
                },
                400,
            )
        requested = len(track_mbids)
        landed = db.replace_playlist_membership(
            playlist_id,
            cast(List[str], track_mbids),
        )

    if prepared.has_rules:
        rules_changed = db.update_playlist_smart_rules(
            playlist_id,
            prepared.smart_rules,
        )

    payload: Dict[str, Any] = {
        "success": True,
        "playlist_id": playlist_id,
        "renamed": renamed,
    }
    if prepared.has_tracks:
        payload["landed"] = landed
        payload["requested"] = requested
    if prepared.has_rules:
        payload["rules_changed"] = rules_changed
    return PlaylistServiceResult(payload)


def apply_pushed_playlists(
    db: PlaylistStore,
    items: List[Any],
) -> PlaylistServiceResult:
    """Apply a validated playlist push and aggregate LWW outcomes in order.

    The database facade remains responsible for each row's transaction and
    last-writer-wins decision.  Unknown action strings intentionally count as
    skipped, matching the historical route's forward-compatible fallback.
    """
    accepted = 0
    stale = 0
    skipped = 0
    results: List[Dict[str, Any]] = []

    for row in items:
        action = db.apply_pushed_playlist_row(row)
        if action in ("inserted", "updated"):
            accepted += 1
        elif action == "stale":
            stale += 1
        else:
            skipped += 1
        results.append(
            {
                "playlist_id": (row or {}).get("playlist_id"),
                "result": action,
            }
        )

    return PlaylistServiceResult(
        {
            "success": True,
            "received": len(items),
            "accepted": accepted,
            "stale": stale,
            "skipped": skipped,
            "results": results,
        }
    )


def prepare_playlist_delete(
    playlist_id: str,
    *,
    purge: bool,
    liked_songs_playlist_id: str,
) -> PreparedDelete:
    """Protect the reserved Liked Songs row before opening the database."""
    if playlist_id == liked_songs_playlist_id:
        return PlaylistServiceResult(
            {
                "success": False,
                "message": (
                    "Liked Songs is a system playlist and can't be deleted. "
                    "Unlike tracks to empty it."
                ),
            },
            409,
        )
    return DeletePlaylistRequest(playlist_id=playlist_id, purge=purge)


def delete_playlist(
    db: PlaylistStore,
    prepared: DeletePlaylistRequest,
) -> PlaylistServiceResult:
    """Purge or soft-delete an authorized playlist via the DB façade."""
    if prepared.purge:
        changed = db.purge_playlist(prepared.playlist_id)
        return PlaylistServiceResult(
            {
                "success": True,
                "purged": changed,
                "playlist_id": prepared.playlist_id,
            }
        )

    changed = db.soft_delete_playlist(prepared.playlist_id)
    return PlaylistServiceResult(
        {
            "success": True,
            "deleted": changed,
            "playlist_id": prepared.playlist_id,
        }
    )
