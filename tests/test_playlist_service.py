import json
from unittest.mock import MagicMock, call

from src.services.playlist_service import (
    CreatePlaylistRequest,
    PlaylistServiceResult,
    UpdatePlaylistRequest,
    create_library_playlist,
    list_library_playlists,
    prepare_playlist_create,
    prepare_playlist_update,
    update_library_playlist,
)


def test_list_library_playlists_decodes_rules_and_tolerates_legacy_garbage():
    db = MagicMock()
    db.list_playlists_with_counts.return_value = [
        {
            "playlist_id": "smart",
            "smart_rules": (
                '{"match":"all","rules":['
                '{"field":"artist","op":"contains","value":"beatles"}]}'
            ),
        },
        {"playlist_id": "legacy", "smart_rules": "not-json"},
    ]

    result = list_library_playlists(db)

    assert result == PlaylistServiceResult({
        "success": True,
        "playlists": [
            {
                "playlist_id": "smart",
                "smart_rules": {
                    "match": "all",
                    "rules": [
                        {
                            "field": "artist",
                            "op": "contains",
                            "value": "beatles",
                        }
                    ],
                },
            },
            {"playlist_id": "legacy", "smart_rules": None},
        ],
    })
    db.list_playlists_with_counts.assert_called_once_with()


def test_prepare_create_trims_name_and_serializes_valid_rules():
    prepared = prepare_playlist_create({
        "name": "  Beatles deep cuts  ",
        "smart_rules": {
            "match": "all",
            "rules": [
                {"field": "artist", "op": "contains", "value": "beatles"}
            ],
        },
    })

    assert isinstance(prepared, CreatePlaylistRequest)
    assert prepared.name == "Beatles deep cuts"
    assert json.loads(prepared.smart_rules) == {
        "match": "all",
        "rules": [
            {"field": "artist", "op": "contains", "value": "beatles"}
        ],
    }


def test_prepare_create_preserves_validation_messages():
    assert prepare_playlist_create({"name": "  "}) == PlaylistServiceResult(
        {"success": False, "message": "name is required"},
        400,
    )

    invalid = prepare_playlist_create({
        "name": "Bad",
        "smart_rules": {
            "rules": [
                {"field": "private_column", "op": "equals", "value": "x"}
            ]
        },
    })
    assert isinstance(invalid, PlaylistServiceResult)
    assert invalid.status_code == 400
    assert "private_column" in invalid.payload["message"]


def test_create_uses_existing_facade_call_and_response_shape():
    db = MagicMock()
    db.create_playlist.return_value = "playlist-1"
    prepared = CreatePlaylistRequest("Fresh", None)

    result = create_library_playlist(db, prepared)

    assert result == PlaylistServiceResult(
        {
            "success": True,
            "playlist_id": "playlist-1",
            "name": "Fresh",
        },
        201,
    )
    db.create_playlist.assert_called_once_with("Fresh", smart_rules=None)


def test_prepare_update_rejects_shape_empty_and_mixed_membership_rules():
    assert prepare_playlist_update(["not", "an", "object"]) == (
        PlaylistServiceResult(
            {"success": False, "message": "body must be an object"},
            400,
        )
    )
    assert prepare_playlist_update({}) == PlaylistServiceResult(
        {
            "success": False,
            "message": (
                "at least one of 'name', 'track_mbids', or "
                "'smart_rules' is required"
            ),
        },
        400,
    )
    mixed = prepare_playlist_update({"track_mbids": [], "smart_rules": None})
    assert isinstance(mixed, PlaylistServiceResult)
    assert mixed.status_code == 400
    assert mixed.payload["message"] == (
        "track_mbids and smart_rules are mutually exclusive"
    )


def test_prepare_update_validates_rules_before_database_work():
    invalid = prepare_playlist_update({
        "smart_rules": {
            "rules": [{"field": "artist", "op": "regex", "value": ".*"}]
        }
    })

    assert isinstance(invalid, PlaylistServiceResult)
    assert invalid.status_code == 400
    assert "regex" in invalid.payload["message"]


def test_update_checks_existence_before_validating_empty_name():
    db = MagicMock()
    db.get_playlist.return_value = None
    prepared = prepare_playlist_update({"name": ""})
    assert isinstance(prepared, UpdatePlaylistRequest)

    result = update_library_playlist(db, "missing", prepared)

    assert result == PlaylistServiceResult(
        {"success": False, "message": "playlist not found or deleted"},
        404,
    )
    db.get_playlist.assert_called_once_with("missing")
    db.rename_playlist.assert_not_called()


def test_update_renames_then_replaces_membership_and_reports_counts():
    db = MagicMock()
    db.get_playlist.return_value = object()
    db.rename_playlist.return_value = True
    db.replace_playlist_membership.return_value = 2
    prepared = prepare_playlist_update({
        "name": "  Renamed  ",
        "track_mbids": ["a", "b", "unknown"],
    })
    assert isinstance(prepared, UpdatePlaylistRequest)

    result = update_library_playlist(db, "playlist-1", prepared)

    assert result == PlaylistServiceResult({
        "success": True,
        "playlist_id": "playlist-1",
        "renamed": True,
        "landed": 2,
        "requested": 3,
    })
    assert db.method_calls == [
        call.get_playlist("playlist-1"),
        call.rename_playlist("playlist-1", "Renamed"),
        call.replace_playlist_membership(
            "playlist-1", ["a", "b", "unknown"]
        ),
    ]


def test_update_keeps_historical_rename_before_track_list_validation():
    db = MagicMock()
    db.get_playlist.return_value = object()
    db.rename_playlist.return_value = True
    prepared = prepare_playlist_update({
        "name": "Renamed",
        "track_mbids": "not-a-list",
    })
    assert isinstance(prepared, UpdatePlaylistRequest)

    result = update_library_playlist(db, "playlist-1", prepared)

    assert result == PlaylistServiceResult(
        {"success": False, "message": "track_mbids must be a list"},
        400,
    )
    db.rename_playlist.assert_called_once_with("playlist-1", "Renamed")
    db.replace_playlist_membership.assert_not_called()


def test_update_can_clear_smart_rules_without_touching_membership():
    db = MagicMock()
    db.get_playlist.return_value = object()
    db.update_playlist_smart_rules.return_value = True
    prepared = prepare_playlist_update({"smart_rules": None})
    assert isinstance(prepared, UpdatePlaylistRequest)

    result = update_library_playlist(db, "playlist-1", prepared)

    assert result == PlaylistServiceResult({
        "success": True,
        "playlist_id": "playlist-1",
        "renamed": False,
        "rules_changed": True,
    })
    db.update_playlist_smart_rules.assert_called_once_with("playlist-1", None)
    db.replace_playlist_membership.assert_not_called()
