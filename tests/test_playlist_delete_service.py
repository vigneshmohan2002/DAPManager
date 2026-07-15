from unittest.mock import MagicMock

from src.services.playlist_service import (
    DeletePlaylistRequest,
    PlaylistServiceResult,
    delete_playlist,
    prepare_playlist_delete,
)


def test_reserved_liked_songs_playlist_is_rejected_before_database_work():
    prepared = prepare_playlist_delete(
        "liked_songs",
        purge=True,
        liked_songs_playlist_id="liked_songs",
    )

    assert prepared == PlaylistServiceResult(
        {
            "success": False,
            "message": (
                "Liked Songs is a system playlist and can't be deleted. "
                "Unlike tracks to empty it."
            ),
        },
        409,
    )


def test_soft_delete_uses_facade_and_preserves_response_shape():
    db = MagicMock()
    db.soft_delete_playlist.return_value = True
    prepared = prepare_playlist_delete(
        "playlist-1",
        purge=False,
        liked_songs_playlist_id="liked_songs",
    )
    assert prepared == DeletePlaylistRequest("playlist-1", False)

    result = delete_playlist(db, prepared)

    assert result == PlaylistServiceResult(
        {
            "success": True,
            "deleted": True,
            "playlist_id": "playlist-1",
        }
    )
    db.soft_delete_playlist.assert_called_once_with("playlist-1")
    db.purge_playlist.assert_not_called()


def test_purge_uses_facade_and_does_not_soft_delete():
    db = MagicMock()
    db.purge_playlist.return_value = False
    prepared = DeletePlaylistRequest("playlist-1", True)

    result = delete_playlist(db, prepared)

    assert result == PlaylistServiceResult(
        {
            "success": True,
            "purged": False,
            "playlist_id": "playlist-1",
        }
    )
    db.purge_playlist.assert_called_once_with("playlist-1")
    db.soft_delete_playlist.assert_not_called()
