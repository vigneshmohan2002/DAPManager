from unittest.mock import MagicMock, patch

import pytest
import requests

from src.db_manager import DownloadItem
from src.download_request import queue_or_forward


def _item(query="Artist - Track", mbid="mb", playlist="p"):
    return DownloadItem(search_query=query, playlist_id=playlist, mbid_guess=mbid)


def _mock_cfg(*, is_master=False, master_url="", api_token=""):
    cfg = MagicMock()
    cfg.is_master = is_master
    cfg.master_url = master_url
    cfg.get.side_effect = lambda k, default=None: {"api_token": api_token}.get(k, default)
    return cfg


def _patch_config(cfg):
    return patch("src.download_request.get_config", return_value=cfg)


def test_master_queues_locally_without_network():
    db = MagicMock()
    db.queue_download.return_value = 11
    with _patch_config(_mock_cfg(is_master=True, master_url="http://master:5001")):
        with patch("src.download_request.requests.post") as post:
            assert queue_or_forward(db, _item()) == 11
            post.assert_not_called()
    db.queue_download.assert_called_once()


def test_satellite_without_master_url_queues_locally():
    db = MagicMock()
    db.queue_download.return_value = 22
    with _patch_config(_mock_cfg(is_master=False, master_url="")):
        with patch("src.download_request.requests.post") as post:
            assert queue_or_forward(db, _item()) == 22
            post.assert_not_called()


def test_satellite_forwards_to_master_with_bearer_token():
    db = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True, "queued": True, "item_id": 99}
    with _patch_config(_mock_cfg(master_url="http://master:5001/", api_token="tok-abc")):
        with patch("src.download_request.requests.post", return_value=response) as post:
            result = queue_or_forward(db, _item(query="X - Y", mbid="m1", playlist="pl"))

    assert result == 99
    db.queue_download.assert_not_called()
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "http://master:5001//api/download/request"
    assert kwargs["json"] == {"search_query": "X - Y", "mbid_guess": "m1", "playlist_id": "pl"}
    assert kwargs["headers"]["Authorization"] == "Bearer tok-abc"


def test_satellite_without_token_omits_auth_header():
    db = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True, "queued": True, "item_id": 1}
    with _patch_config(_mock_cfg(master_url="http://m:5001", api_token="")):
        with patch("src.download_request.requests.post", return_value=response) as post:
            queue_or_forward(db, _item())
    _, kwargs = post.call_args
    assert "Authorization" not in kwargs["headers"]


def test_master_dedupe_response_returns_zero_without_local_queue():
    db = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True, "queued": False, "message": "already queued"}
    with _patch_config(_mock_cfg(master_url="http://m:5001")):
        with patch("src.download_request.requests.post", return_value=response):
            assert queue_or_forward(db, _item()) == 0
    db.queue_download.assert_not_called()


def test_network_error_falls_back_to_local_queue():
    db = MagicMock()
    db.queue_download.return_value = 7
    with _patch_config(_mock_cfg(master_url="http://m:5001")):
        with patch("src.download_request.requests.post",
                   side_effect=requests.ConnectionError("boom")):
            assert queue_or_forward(db, _item()) == 7
    db.queue_download.assert_called_once()


def test_non_200_falls_back_to_local_queue():
    db = MagicMock()
    db.queue_download.return_value = 8
    response = MagicMock(status_code=500, text="internal err")
    with _patch_config(_mock_cfg(master_url="http://m:5001")):
        with patch("src.download_request.requests.post", return_value=response):
            assert queue_or_forward(db, _item()) == 8
    db.queue_download.assert_called_once()


def test_success_false_falls_back_to_local_queue():
    db = MagicMock()
    db.queue_download.return_value = 9
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": False, "message": "not a master"}
    with _patch_config(_mock_cfg(master_url="http://m:5001")):
        with patch("src.download_request.requests.post", return_value=response):
            assert queue_or_forward(db, _item()) == 9
    db.queue_download.assert_called_once()


def test_non_json_response_falls_back_to_local_queue():
    db = MagicMock()
    db.queue_download.return_value = 10
    response = MagicMock(status_code=200, text="<html>oops</html>")
    response.json.side_effect = ValueError("not json")
    with _patch_config(_mock_cfg(master_url="http://m:5001")):
        with patch("src.download_request.requests.post", return_value=response):
            assert queue_or_forward(db, _item()) == 10
    db.queue_download.assert_called_once()
