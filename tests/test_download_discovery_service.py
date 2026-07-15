from types import SimpleNamespace
from unittest.mock import MagicMock, call

import requests

from src.services.download_discovery_service import (
    DownloadDiscoveryResult,
    PreparedDownloadRequest,
    SuggestionBatch,
    WantedReleaseRecords,
    build_suggestion_items,
    fetch_wanted_release_records,
    forward_suggestions,
    prepare_download_request,
    queue_catalog_downloads,
    queue_download_request,
    queue_suggestions,
    validate_catalog_queue_body,
    validate_download_authority,
    validate_forward_items,
    validate_wanted_releases_enabled,
    wanted_releases_result,
)


def _item_factory(**values):
    return SimpleNamespace(**values)


def test_download_request_authority_validation_precedes_payload_work():
    assert validate_download_authority(False) == DownloadDiscoveryResult(
        {"success": False, "message": "This instance is not a master"},
        400,
    )
    assert validate_download_authority(True) is None
    assert prepare_download_request({"search_query": "   "}) == (
        DownloadDiscoveryResult(
            {"success": False, "message": "search_query is required"},
            400,
        )
    )

    prepared = prepare_download_request({
        "search_query": "  Artist - Track  ",
        "mbid_guess": " mbid-1 ",
        "playlist_id": "   ",
    })
    assert prepared == PreparedDownloadRequest(
        search_query="Artist - Track",
        mbid_guess="mbid-1",
        playlist_id="SATELLITE",
    )


def test_download_request_dedupes_or_queues_with_exact_item_shape():
    db = MagicMock()
    db.is_download_queued.return_value = True
    prepared = PreparedDownloadRequest("A - B", "m1", "sat-1")

    duplicate = queue_download_request(
        db, prepared, item_factory=_item_factory
    )

    assert duplicate.payload == {
        "success": True,
        "queued": False,
        "message": "already queued",
    }
    db.queue_download.assert_not_called()

    db.reset_mock()
    db.is_download_queued.return_value = False
    db.queue_download.return_value = 42
    queued = queue_download_request(db, prepared, item_factory=_item_factory)

    assert queued.payload == {
        "success": True,
        "queued": True,
        "item_id": 42,
        "message": "queued",
    }
    item = db.queue_download.call_args.args[0]
    assert vars(item) == {
        "search_query": "A - B",
        "playlist_id": "sat-1",
        "mbid_guess": "m1",
    }


def test_suggestion_normalization_and_queue_order_are_stable():
    pairs = build_suggestion_items([
        {"search_query": "Foo - Bar", "mbid": "first"},
        {"search_query": "foo - bar", "mbid": "duplicate"},
        {"artist": "Portishead", "title": "Roads"},
        {"title": "Title only"},
        "invalid",
    ])
    assert pairs == [
        ("Foo - Bar", "first"),
        ("Portishead - Roads", ""),
        ("Title only", ""),
    ]

    db = MagicMock()
    db.is_download_queued.side_effect = [False, True, False]
    result = queue_suggestions(
        db,
        SuggestionBatch(received=5, pairs=pairs),
        item_factory=_item_factory,
    )

    assert result.payload == {
        "success": True,
        "received": 5,
        "queued": 2,
        "skipped": 1,
    }
    assert [
        queued.args[0].search_query
        for queued in db.queue_download.call_args_list
    ] == ["Foo - Bar", "Title only"]
    for queued in db.queue_download.call_args_list:
        assert queued.args[0].playlist_id == "SUGGESTED"
        assert queued.args[0].status == "pending"


def test_forward_suggestions_preserves_auth_upstream_status_and_non_json():
    response = MagicMock(status_code=202)
    response.json.return_value = {"success": True, "queued": 2}
    post = MagicMock(return_value=response)

    result = forward_suggestions(
        "http://master:5001",
        [{"title": "One"}],
        api_token=" secret ",
        http_post=post,
    )

    assert result == DownloadDiscoveryResult(
        {"success": True, "queued": 2}, 202
    )
    post.assert_called_once_with(
        "http://master:5001/api/suggestions",
        json={"items": [{"title": "One"}]},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer secret",
        },
        timeout=(5, 30),
    )

    response.status_code = 503
    response.json.side_effect = ValueError("html")
    non_json = forward_suggestions(
        "http://master:5001", [], http_post=post
    )
    assert non_json == DownloadDiscoveryResult(
        {
            "success": False,
            "message": "master returned non-JSON (503)",
        },
        503,
    )


def test_forward_suggestions_classifies_network_and_body_failures():
    assert validate_forward_items(None) == DownloadDiscoveryResult(
        {"success": False, "message": "body must be {'items': [...]}"},
        400,
    )
    result = forward_suggestions(
        "http://master:5001",
        [],
        http_post=MagicMock(
            side_effect=requests.ConnectionError("connection refused")
        ),
    )
    assert result == DownloadDiscoveryResult(
        {
            "success": False,
            "message": "master unreachable: connection refused",
        },
        502,
    )


def test_catalog_queue_classifies_each_input_without_reordering():
    assert validate_catalog_queue_body({"mbids": "bad"}) == (
        DownloadDiscoveryResult(
            {"success": False, "message": "body must be {'mbids': [...]}"},
            400,
        )
    )

    db = MagicMock()
    tracks = {
        "queue": SimpleNamespace(
            artist="Artist", title="Track", local_path=None
        ),
        "linked": SimpleNamespace(
            artist="Artist", title="Local", local_path="/music/local.flac"
        ),
        "duplicate": SimpleNamespace(
            artist="Artist", title="Queued", local_path=None
        ),
        "no-query": SimpleNamespace(artist="", title="", local_path=None),
    }
    db.get_track_by_mbid.side_effect = tracks.get
    db.is_download_queued.side_effect = lambda query: query == "Artist - Queued"

    result = queue_catalog_downloads(
        db,
        ["queue", "linked", "duplicate", "missing", "", "no-query"],
        item_factory=_item_factory,
    )

    assert result.payload == {
        "success": True,
        "received": 6,
        "queued": 1,
        "queued_mbids": ["queue"],
        "skipped_linked": 1,
        "skipped_queued": 1,
        "not_found": 3,
    }
    item = db.queue_download.call_args.args[0]
    assert vars(item) == {
        "search_query": "Artist - Track",
        "playlist_id": "CATALOG",
        "mbid_guess": "queue",
        "status": "pending",
    }


class _LidarrFailure(Exception):
    pass


def test_wanted_release_policy_distinguishes_disabled_unavailable_and_error():
    assert validate_wanted_releases_enabled({}) == DownloadDiscoveryResult(
        {"success": False, "reason": "lidarr_disabled"}
    )

    unavailable = fetch_wanted_release_records(
        {"lidarr_watch_enabled": True},
        client_factory=MagicMock(return_value=None),
        lidarr_error=_LidarrFailure,
    )
    assert unavailable == DownloadDiscoveryResult(
        {"success": False, "reason": "lidarr_unavailable"}
    )

    client = MagicMock()
    client.get_wanted_missing.side_effect = _LidarrFailure("offline")
    failed = fetch_wanted_release_records(
        {"lidarr_watch_enabled": True},
        client_factory=MagicMock(return_value=client),
        lidarr_error=_LidarrFailure,
    )
    assert failed == DownloadDiscoveryResult(
        {"success": False, "message": "offline"}, 502
    )
    client.get_wanted_missing.assert_called_once_with(page=1, page_size=100)


def test_wanted_release_enrichment_uses_lidarr_cover_then_caa_and_state():
    db = MagicMock()
    db.get_sync_state.return_value = "2026-07-15T12:00:00"
    db.get_queued_release_mbids.return_value = {"queued"}
    db.get_existing_release_mbids.return_value = {"downloaded"}
    records = [
        {
            "foreignAlbumId": "queued",
            "artist": {"artistName": "A"},
            "title": "Queued",
            "releaseDate": "2026-07-01",
            "images": [
                {"coverType": "banner", "remoteUrl": "ignore"},
                {"coverType": "cover", "remoteUrl": "https://lidarr/cover"},
            ],
        },
        {
            "foreignAlbumId": "downloaded",
            "artist": {"artistName": "B"},
            "title": "Downloaded",
            "images": [],
        },
    ]

    result = wanted_releases_result(db, records)

    assert result.payload["last_tick"] == "2026-07-15T12:00:00"
    assert result.payload["items"] == [
        {
            "mbid": "queued",
            "artist": "A",
            "title": "Queued",
            "release_date": "2026-07-01",
            "cover_url": "https://lidarr/cover",
            "queued": True,
            "downloaded": False,
        },
        {
            "mbid": "downloaded",
            "artist": "B",
            "title": "Downloaded",
            "release_date": None,
            "cover_url": (
                "https://coverartarchive.org/release-group/"
                "downloaded/front-250"
            ),
            "queued": False,
            "downloaded": True,
        },
    ]
    assert db.method_calls == [
        call.get_sync_state("last_release_watch_tick"),
        call.get_queued_release_mbids(),
        call.get_existing_release_mbids(),
    ]


def test_wanted_release_fetch_preserves_record_order_and_shape():
    client = MagicMock()
    records = [{"foreignAlbumId": "first"}, {"foreignAlbumId": "second"}]
    client.get_wanted_missing.return_value = records

    prepared = fetch_wanted_release_records(
        {"lidarr_watch_enabled": True},
        client_factory=MagicMock(return_value=client),
        lidarr_error=_LidarrFailure,
    )

    assert prepared == WantedReleaseRecords(records)
