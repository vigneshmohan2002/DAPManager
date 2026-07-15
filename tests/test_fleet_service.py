from unittest.mock import MagicMock, call

from src.services.fleet_service import FleetServiceResult, lookup_fleet_track


def test_mbid_lookup_has_precedence_over_query():
    db = MagicMock()
    holders = [
        {
            "device_id": "dev-a",
            "local_path": "/music/a.flac",
            "reported_at": "2026-07-15 08:00:00",
        }
    ]
    db.get_devices_holding_mbid.return_value = holders

    result = lookup_fleet_track(db, mbid="track-1", query="ignored")

    assert result == FleetServiceResult(
        {
            "success": True,
            "mbid": "track-1",
            "holders": holders,
        }
    )
    db.get_devices_holding_mbid.assert_called_once_with("track-1")
    db.find_tracks_for_fleet_search.assert_not_called()


def test_query_lookup_enriches_each_match_in_database_order():
    db = MagicMock()
    db.find_tracks_for_fleet_search.return_value = [
        {
            "mbid": "track-1",
            "artist": "Artist",
            "title": "One",
            "holders": ["stale-value"],
        },
        {"mbid": "track-2", "artist": "Artist", "title": "Two"},
    ]
    first_holders = [{"device_id": "dev-a"}]
    second_holders = [{"device_id": "dev-b"}]
    db.get_devices_holding_mbid.side_effect = [
        first_holders,
        second_holders,
    ]

    result = lookup_fleet_track(db, mbid="", query="Artist")

    assert result == FleetServiceResult(
        {
            "success": True,
            "query": "Artist",
            "results": [
                {
                    "mbid": "track-1",
                    "artist": "Artist",
                    "title": "One",
                    "holders": first_holders,
                },
                {
                    "mbid": "track-2",
                    "artist": "Artist",
                    "title": "Two",
                    "holders": second_holders,
                },
            ],
        }
    )
    db.find_tracks_for_fleet_search.assert_called_once_with("Artist")
    assert db.get_devices_holding_mbid.call_args_list == [
        call("track-1"),
        call("track-2"),
    ]


def test_lookup_requires_mbid_or_query_without_touching_database():
    db = MagicMock()

    result = lookup_fleet_track(db, mbid="", query="")

    assert result == FleetServiceResult(
        {"success": False, "message": "provide mbid or q"},
        400,
    )
    db.get_devices_holding_mbid.assert_not_called()
    db.find_tracks_for_fleet_search.assert_not_called()
