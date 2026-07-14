"""Ordering and error contracts for the playlist service adapters."""

from unittest.mock import MagicMock, patch


def test_update_missing_playlist_precedes_empty_name_validation(
    client, mock_config
):
    with patch("web_server.DatabaseManager") as database:
        db = database.return_value.__enter__.return_value
        db.get_playlist.return_value = None

        response = client.put(
            "/api/library/playlists/missing",
            json={"name": ""},
        )

    assert response.status_code == 404
    assert response.get_json() == {
        "success": False,
        "message": "playlist not found or deleted",
    }
    db.rename_playlist.assert_not_called()


def test_update_rename_precedes_membership_type_rejection(client, mock_config):
    with patch("web_server.DatabaseManager") as database:
        db = database.return_value.__enter__.return_value
        db.get_playlist.return_value = MagicMock()
        db.rename_playlist.return_value = True

        response = client.put(
            "/api/library/playlists/playlist-1",
            json={"name": "Renamed", "track_mbids": "not-a-list"},
        )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "message": "track_mbids must be a list",
    }
    db.rename_playlist.assert_called_once_with("playlist-1", "Renamed")
    db.replace_playlist_membership.assert_not_called()


def test_create_database_value_error_remains_client_error(client, mock_config):
    with patch("web_server.DatabaseManager") as database:
        db = database.return_value.__enter__.return_value
        db.create_playlist.side_effect = ValueError("playlist name is invalid")

        response = client.post(
            "/api/library/playlists",
            json={"name": "Fresh"},
        )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "message": "playlist name is invalid",
    }
