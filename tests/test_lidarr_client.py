"""Tests for the Lidarr v1 REST client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.lidarr_client import LidarrClient, LidarrError


@pytest.fixture
def client():
    return LidarrClient(base_url="http://lidarr.local:8686", api_key="key")


def _resp(status=200, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body if json_body is not None else {}
    r.text = text
    r.content = text.encode() if text else b"{}"
    return r


def test_requires_base_url_and_api_key():
    with pytest.raises(ValueError):
        LidarrClient(base_url="", api_key="k")
    with pytest.raises(ValueError):
        LidarrClient(base_url="http://x", api_key="")


def test_sets_api_key_header(client):
    assert client.session.headers.get("X-Api-Key") == "key"


def test_url_prefixes_api_v1(client):
    assert client._url("/artist") == "http://lidarr.local:8686/api/v1/artist"
    assert client._url("artist") == "http://lidarr.local:8686/api/v1/artist"


def test_get_raises_on_http_error(client):
    with patch.object(client.session, "get", return_value=_resp(status=500, text="boom")):
        with pytest.raises(LidarrError):
            client._get("/system/status")


def test_get_raises_on_request_exception(client):
    with patch.object(
        client.session, "get", side_effect=requests.ConnectionError("nope")
    ):
        with pytest.raises(LidarrError):
            client._get("/system/status")


def test_ping_true_on_200(client):
    with patch.object(client.session, "get", return_value=_resp(200, {"version": "2"})):
        assert client.ping() is True


def test_ping_false_on_any_error(client):
    with patch.object(
        client.session, "get", side_effect=requests.ConnectionError("nope")
    ):
        assert client.ping() is False


def test_find_flac_profile_by_name(client):
    profiles = [
        {"id": 1, "name": "Any"},
        {"id": 2, "name": "FLAC Preferred"},
    ]
    with patch.object(client, "get_quality_profiles", return_value=profiles):
        assert client.find_flac_quality_profile_id() == 2


def test_find_flac_profile_falls_back_to_allowed_items(client):
    profiles = [
        {
            "id": 7,
            "name": "Lossless",
            "items": [
                {"allowed": True, "quality": {"name": "FLAC"}},
                {"allowed": False, "quality": {"name": "MP3-320"}},
            ],
        }
    ]
    with patch.object(client, "get_quality_profiles", return_value=profiles):
        assert client.find_flac_quality_profile_id() == 7


def test_find_flac_profile_none_when_no_match(client):
    profiles = [{"id": 1, "name": "Any", "items": []}]
    with patch.object(client, "get_quality_profiles", return_value=profiles):
        assert client.find_flac_quality_profile_id() is None


def test_get_queue_unwraps_paged_records(client):
    with patch.object(
        client.session,
        "get",
        return_value=_resp(200, {"records": [{"id": 1}, {"id": 2}], "page": 1}),
    ):
        assert client.get_queue() == [{"id": 1}, {"id": 2}]


def test_get_queue_accepts_bare_list(client):
    with patch.object(client.session, "get", return_value=_resp(200, [{"id": 9}])):
        assert client.get_queue() == [{"id": 9}]


def test_get_artist_by_mbid_returns_first(client):
    with patch.object(
        client.session,
        "get",
        return_value=_resp(200, [{"id": 1, "foreignArtistId": "mbid"}]),
    ):
        assert client.get_artist_by_mbid("mbid") == {"id": 1, "foreignArtistId": "mbid"}


def test_get_artist_by_mbid_none_when_empty(client):
    with patch.object(client.session, "get", return_value=_resp(200, [])):
        assert client.get_artist_by_mbid("mbid") is None


def test_add_artist_merges_payload(client):
    lookup = {"foreignArtistId": "mb", "artistName": "A", "images": []}
    post = MagicMock(return_value=_resp(201, {"id": 42}, text='{"id": 42}'))
    with patch.object(client.session, "post", post):
        result = client.add_artist(
            lookup,
            quality_profile_id=3,
            root_folder_path="/music",
            metadata_profile_id=1,
        )
    assert result == {"id": 42}
    _, kwargs = post.call_args
    payload = kwargs["json"]
    assert payload["foreignArtistId"] == "mb"
    assert payload["qualityProfileId"] == 3
    assert payload["rootFolderPath"] == "/music"
    assert payload["metadataProfileId"] == 1
    assert payload["monitored"] is True
    assert payload["addOptions"] == {
        "monitor": "all",
        "searchForMissingAlbums": True,
    }


def test_search_album_posts_command(client):
    post = MagicMock(return_value=_resp(201, {"id": 99}, text='{"id": 99}'))
    with patch.object(client.session, "post", post):
        client.search_album(5)
    _, kwargs = post.call_args
    assert kwargs["json"] == {"name": "AlbumSearch", "albumIds": [5]}


def test_ensure_album_monitored_adds_artist_and_searches(client):
    lookup_album = [
        {
            "foreignAlbumId": "rel-mb",
            "title": "X",
            "artist": {"foreignArtistId": "art-mb"},
        }
    ]
    lookup_artist = [{"foreignArtistId": "art-mb", "artistName": "A"}]
    stored_album = {"id": 11, "foreignAlbumId": "rel-mb"}

    with patch.object(client, "lookup_album", return_value=lookup_album), \
         patch.object(client, "get_artist_by_mbid", return_value=None), \
         patch.object(client, "lookup_artist", return_value=lookup_artist), \
         patch.object(client, "add_artist") as add_artist, \
         patch.object(client, "get_album_by_mbid", return_value=stored_album), \
         patch.object(client, "search_album") as search_album:
        result = client.ensure_album_monitored(
            "rel-mb", quality_profile_id=3, root_folder_path="/music"
        )

    assert result == stored_album
    add_artist.assert_called_once()
    search_album.assert_called_once_with(11)


def test_ensure_album_monitored_skips_add_if_artist_already_tracked(client):
    lookup_album = [
        {
            "foreignAlbumId": "rel-mb",
            "artist": {"foreignArtistId": "art-mb"},
        }
    ]
    with patch.object(client, "lookup_album", return_value=lookup_album), \
         patch.object(client, "get_artist_by_mbid", return_value={"id": 1}), \
         patch.object(client, "add_artist") as add_artist, \
         patch.object(client, "get_album_by_mbid", return_value={"id": 22}), \
         patch.object(client, "search_album") as search_album:
        client.ensure_album_monitored("rel-mb", 3, "/music")

    add_artist.assert_not_called()
    search_album.assert_called_once_with(22)


def test_ensure_album_monitored_returns_none_when_not_in_mb(client):
    with patch.object(client, "lookup_album", return_value=[]):
        assert client.ensure_album_monitored("missing", 3, "/music") is None
