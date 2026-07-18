from unittest.mock import MagicMock

import requests
from mutagen.flac import FLAC

from src.db_manager import DownloadItem, Track
from src.services.album_download_request_service import (
    ALBUM_PLAYLIST_ID,
    AlbumRequestResult,
    ResolvedAlbum,
    album_request_status,
    canonical_release_mbid,
    forward_master_json,
    inspect_release_inventory,
    queue_album_request,
    resolve_album_release,
    search_album_releases,
)


RELEASE_ID = "95FB59ED-1ECE-419B-B62F-AEF31E0EBF36"
CANONICAL_ID = RELEASE_ID.lower()
OTHER_ID = "461eac33-7edd-481a-a7d1-089ec6fc01af"
MANIFEST = tuple(
    f"00000000-0000-4000-8000-{number:012d}"
    for number in range(1, 11)
)


def _write_tagged_flac(path, recording_mbid, release_mbid=CANONICAL_ID):
    data = bytearray(b"fLaC")
    data += bytes([0x80, 0x00, 0x00, 0x22])
    data += (4096).to_bytes(2, "big") * 2
    data += (0).to_bytes(3, "big") * 2
    data += ((44100 << 44) | (15 << 36)).to_bytes(8, "big")
    data += b"\x00" * 16
    path.write_bytes(data)
    audio = FLAC(str(path))
    audio["musicbrainz_trackid"] = recording_mbid
    audio["musicbrainz_albumid"] = release_mbid
    audio.save()


def _release(
    release_id=RELEASE_ID,
    *,
    title="Album",
    artist="Artist",
    tracks=10,
    primary_type="Album",
):
    recording_mbids = MANIFEST[:tracks]
    return {
        "id": release_id,
        "title": title,
        "artist-credit": [
            {"artist": {"name": artist}, "joinphrase": " feat. "},
            {"artist": {"name": "Guest"}},
        ],
        "track-count": tracks,
        "medium-list": ([{
            "position": 1,
            "track-count": tracks,
            "track-list": [
                {
                    "id": f"10000000-0000-4000-8000-{position:012d}",
                    "position": position,
                    "number": str(position),
                    "title": f"Track {position}",
                    "recording": {
                        "id": recording_mbid,
                        "title": f"Track {position}",
                    },
                }
                for position, recording_mbid in enumerate(
                    recording_mbids,
                    start=1,
                )
            ],
        }] if tracks else []),
        "date": "2026-01-02",
        "country": "XW",
        "status": "Official",
        "release-group": {"primary-type": primary_type},
        "ext:score": "99",
    }


def test_release_mbid_canonicalization_rejects_invalid_and_nil():
    assert canonical_release_mbid(RELEASE_ID) == CANONICAL_ID
    assert canonical_release_mbid("not-an-mbid") is None
    assert canonical_release_mbid("00000000-0000-0000-0000-000000000000") is None


def test_search_is_fielded_for_artist_album_and_preserves_ambiguity():
    search = MagicMock(return_value={
        "release-list": [
            _release(title="Album", tracks=10),
            _release(OTHER_ID, title="Album (Deluxe)", tracks=14),
            _release(
                "2bcc3f49-80c5-4e7f-a07e-5fd935e1b15a",
                title="Single",
                tracks=1,
                primary_type="Single",
            ),
        ]
    })

    result = search_album_releases(
        " Artist feat. Guest - Album ",
        search_releases=search,
    )

    assert result.status_code == 200
    assert result.payload["ambiguous"] is True
    assert [row["track_count"] for row in result.payload["candidates"]] == [10, 14]
    assert result.payload["candidates"][0]["artist"] == "Artist feat. Guest"
    assert result.payload["candidates"][0]["release_mbid"] == CANONICAL_ID
    search.assert_called_once_with(
        limit=8,
        artist="Artist feat. Guest",
        release="Album",
        primarytype="album",
    )


def test_search_no_match_and_short_query_are_explicit_safe_results():
    search = MagicMock(return_value={"release-list": []})
    no_match = search_album_releases("zzzz", search_releases=search)
    assert no_match.payload == {
        "success": True,
        "query": "zzzz",
        "ambiguous": False,
        "candidates": [],
    }

    short = search_album_releases("x", search_releases=search)
    assert short == AlbumRequestResult(
        {"success": False, "message": "Enter at least 2 characters"},
        400,
    )


def test_resolve_rechecks_exact_release_and_rejects_non_album_or_no_tracks():
    get_release = MagicMock(return_value={"release": _release()})
    resolved = resolve_album_release(RELEASE_ID, get_release_by_id=get_release)
    assert isinstance(resolved, ResolvedAlbum)
    assert resolved.release_mbid == CANONICAL_ID
    assert resolved.track_count == 10
    get_release.assert_called_once_with(
        CANONICAL_ID,
        includes=["artists", "release-groups", "recordings"],
    )

    non_album = resolve_album_release(
        RELEASE_ID,
        get_release_by_id=lambda *a, **k: {
            "release": _release(primary_type="Single")
        },
    )
    assert isinstance(non_album, AlbumRequestResult)
    assert non_album.status_code == 400

    no_tracks = resolve_album_release(
        RELEASE_ID,
        get_release_by_id=lambda *a, **k: {"release": _release(tracks=0)},
    )
    assert isinstance(no_tracks, AlbumRequestResult)
    assert no_tracks.status_code == 409

    repeated_payload = _release(tracks=2)
    repeated_payload["medium-list"][0]["track-list"][1]["recording"]["id"] = (
        MANIFEST[0]
    )
    repeated = resolve_album_release(
        RELEASE_ID,
        get_release_by_id=lambda *a, **k: {"release": repeated_payload},
    )
    assert isinstance(repeated, AlbumRequestResult)
    assert repeated.status_code == 409
    assert "repeats" in repeated.payload["message"]


def test_queue_uses_only_resolved_identity_and_returns_persistent_tracker():
    db = MagicMock()
    db.get_album_download_request_by_release.return_value = None
    db.count_local_release_tracks.return_value = 0
    db.create_download_and_album_request.return_value = (42, 7)
    db.get_album_download_request.return_value = {
        "id": 7,
        "queue_item_id": 42,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "queued",
        "detail": "Waiting",
        "completed_tracks": 0,
        "queue_status": "pending",
    }
    album = ResolvedAlbum(
        CANONICAL_ID, "Album", "Artist", 10, recording_mbids=MANIFEST
    )

    result = queue_album_request(db, album, item_factory=DownloadItem)

    assert result.payload["queued"] is True
    assert result.payload["request"]["id"] == 7
    db.create_download_and_album_request.assert_called_once_with(
        release_mbid=CANONICAL_ID,
        search_query="::ALBUM:: Artist - Album",
        playlist_id=ALBUM_PLAYLIST_ID,
        artist="Artist",
        title="Album",
        track_count=10,
        detail="Waiting for the master download queue",
        completed_tracks=0,
        recording_mbids=MANIFEST,
        track_manifest=album.track_manifest(),
    )


def test_queue_does_not_mutate_untracked_pending_work_before_tracker_exists():
    db = MagicMock()
    db.get_album_download_request_by_release.return_value = None
    db.count_local_release_tracks.return_value = 0
    db.get_active_download_id.return_value = 31
    db.create_download_and_album_request.return_value = (32, 9)
    db.get_album_download_request.return_value = {
        "id": 9,
        "queue_item_id": 32,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "queued",
        "detail": "Waiting",
        "completed_tracks": 0,
        "queue_status": "pending",
    }

    album = ResolvedAlbum(
        CANONICAL_ID, "Album", "Artist", 10,
        recording_mbids=MANIFEST,
    )
    result = queue_album_request(
        db,
        album,
        item_factory=DownloadItem,
    )

    assert result.payload["request"]["id"] == 9
    db.create_download_and_album_request.assert_called_once_with(
        release_mbid=CANONICAL_ID,
        search_query="::ALBUM:: Artist - Album",
        playlist_id=ALBUM_PLAYLIST_ID,
        artist="Artist",
        title="Album",
        track_count=10,
        detail="Waiting for the master download queue",
        completed_tracks=0,
        recording_mbids=MANIFEST,
        track_manifest=album.track_manifest(),
    )
    db.claim_download_for_album_request.assert_not_called()


def test_concurrent_duplicate_tracker_winner_is_returned_and_extra_queue_removed():
    db = MagicMock()
    winner = {
        "id": 10,
        "queue_item_id": 40,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "queued",
        "detail": "Waiting",
        "completed_tracks": 0,
        "queue_status": "pending",
    }
    db.get_album_download_request_by_release.side_effect = [None, winner]
    db.count_local_release_tracks.return_value = 0
    db.create_download_and_album_request.side_effect = RuntimeError("unique release")

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID, "Album", "Artist", 10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.payload["queued"] is False
    assert result.payload["request"]["id"] == 10
    db.remove_from_queue.assert_not_called()


def test_queue_retries_failed_tracker_and_rolls_back_new_untracked_work():
    db = MagicMock()
    failed = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "failed",
        "detail": "No source",
        "completed_tracks": 0,
        "queue_status": "failed",
    }
    retried = dict(
        failed,
        stage="queued",
        detail="Retry waiting for the master download queue",
        queue_status="pending",
    )
    db.get_album_download_request_by_release.return_value = failed
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST)
    db.get_album_download_request.side_effect = [failed, failed, retried]
    db.count_local_release_tracks.return_value = 0
    db.create_download_and_requeue_album_request.return_value = 88
    album = ResolvedAlbum(
        CANONICAL_ID, "Album", "Artist", 10, recording_mbids=MANIFEST
    )
    retry = queue_album_request(db, album, item_factory=DownloadItem)
    assert retry.payload["queued"] is True
    assert retry.payload["message"] == "retry queued"
    assert retry.payload["request"]["stage"] == "queued"
    db.create_download_and_requeue_album_request.assert_called_once_with(
        request_id=3,
        release_mbid=CANONICAL_ID,
        search_query="::ALBUM:: Artist - Album",
        playlist_id=ALBUM_PLAYLIST_ID,
        detail="Retry waiting for the master download queue",
        completed_tracks=0,
    )
    db.queue_download.assert_not_called()

    db.reset_mock()
    db.get_album_download_request_by_release.return_value = None
    db.count_local_release_tracks.return_value = 0
    db.create_download_and_album_request.side_effect = RuntimeError("disk full")
    try:
        queue_album_request(db, album, item_factory=DownloadItem)
    except RuntimeError:
        pass
    else:
        raise AssertionError("tracker failure must propagate")
    db.remove_from_queue.assert_not_called()


def test_queue_dedupes_nonfailed_existing_request_without_new_work():
    db = MagicMock()
    existing = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "downloading",
        "detail": "50%",
        "completed_tracks": 0,
        "queue_status": "pending",
    }
    db.get_album_download_request_by_release.return_value = existing
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST)
    db.get_album_download_request.side_effect = [existing, existing]
    db.count_local_release_tracks.return_value = 0

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID, "Album", "Artist", 10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.payload["queued"] is False
    assert result.payload["request"]["stage"] == "downloading"
    db.queue_download.assert_not_called()


def test_musicbrainz_correction_rewrites_failed_queue_before_retry():
    db = MagicMock()
    old_manifest = tuple(reversed(MANIFEST))
    existing = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Old Artist",
        "title": "Old Title",
        "track_count": 10,
        "stage": "failed",
        "detail": "No source",
        "completed_tracks": 0,
        "queue_status": "failed",
    }
    corrected = dict(existing, artist="Artist", title="Album")
    retried = dict(
        corrected,
        stage="queued",
        queue_status="pending",
        detail="Retry waiting for the master download queue",
    )
    db.get_album_download_request_by_release.return_value = existing
    db.get_album_download_request_recording_mbids.side_effect = [
        list(old_manifest),
        list(MANIFEST),
    ]
    db.replace_album_download_request_identity.return_value = True
    db.get_album_download_request.side_effect = [
        corrected,
        corrected,
        corrected,
        retried,
    ]
    db.get_local_release_recordings.return_value = []
    db.create_download_and_requeue_album_request.return_value = 88

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID,
            "Album",
            "Artist",
            10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.payload["queued"] is True
    db.replace_album_download_request_identity.assert_called_once()
    db.create_download_and_requeue_album_request.assert_called_once_with(
        request_id=3,
        release_mbid=CANONICAL_ID,
        search_query="::ALBUM:: Artist - Album",
        playlist_id=ALBUM_PLAYLIST_ID,
        detail="Retry waiting for the master download queue",
        completed_tracks=0,
    )


def test_musicbrainz_correction_refuses_active_request_manifest_replacement():
    db = MagicMock()
    existing = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Old Artist",
        "title": "Old Title",
        "track_count": 10,
        "stage": "downloading",
        "detail": "50%",
        "completed_tracks": 0,
        "queue_status": "pending",
    }
    db.get_album_download_request_by_release.return_value = existing
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST)

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID,
            "Album",
            "Artist",
            10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.status_code == 409
    assert "active" in result.payload["message"]
    db.replace_album_download_request_identity.assert_not_called()
    db.create_download_and_requeue_album_request.assert_not_called()


def test_musicbrainz_correction_conflict_fails_without_requeue():
    db = MagicMock()
    existing = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Old Artist",
        "title": "Old Title",
        "track_count": 10,
        "stage": "failed",
        "queue_status": "failed",
    }
    db.get_album_download_request_by_release.return_value = existing
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST)
    db.replace_album_download_request_identity.return_value = False

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID,
            "Album",
            "Artist",
            10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.status_code == 409
    db.create_download_and_requeue_album_request.assert_not_called()
    db.queue_download.assert_not_called()


def test_queue_does_not_claim_retry_when_failed_row_cannot_be_flipped():
    db = MagicMock()
    failed = {
        "id": 3,
        "queue_item_id": 77,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 10,
        "stage": "failed",
        "detail": "No source",
        "completed_tracks": 0,
        "queue_status": "failed",
    }
    db.get_album_download_request_by_release.return_value = failed
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST)
    db.get_album_download_request.side_effect = [failed, failed]
    db.count_local_release_tracks.return_value = 0
    db.create_download_and_requeue_album_request.return_value = None

    result = queue_album_request(
        db,
        ResolvedAlbum(
            CANONICAL_ID, "Album", "Artist", 10,
            recording_mbids=MANIFEST,
        ),
        item_factory=DownloadItem,
    )

    assert result.status_code == 409
    assert result.payload["success"] is False
    db.requeue_album_download_request.assert_not_called()
    db.queue_download.assert_not_called()


def test_status_promotes_to_success_when_all_release_tracks_are_present(tmp_path):
    db = MagicMock()
    row = {
        "id": 8,
        "queue_item_id": 55,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 2,
        "stage": "importing",
        "detail": "One imported",
        "completed_tracks": 1,
        "queue_status": None,
    }
    promoted = dict(row, stage="success", detail="Complete", completed_tracks=2)
    db.get_album_download_request.side_effect = [row, promoted]
    paths = [tmp_path / "one.flac", tmp_path / "two.flac"]
    for index, path in enumerate(paths):
        _write_tagged_flac(path, MANIFEST[index])
    db.get_album_download_request_recording_mbids.return_value = list(MANIFEST[:2])
    db.get_local_release_recordings.return_value = [
        {"mbid": MANIFEST[index], "local_path": str(path)}
        for index, path in enumerate(paths)
    ]

    result = album_request_status(db, 8)

    db.complete_album_download_request_by_id.assert_called_once_with(
        8,
        "All MusicBrainz tracks are present in the master library",
        2,
    )
    assert result.payload["request"]["stage"] == "success"


def test_status_does_not_race_active_importer_into_success(tmp_path):
    db = MagicMock()
    row = {
        "id": 8,
        "queue_item_id": 55,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 1,
        "stage": "importing",
        "detail": "Still importing",
        "completed_tracks": 0,
        "queue_status": "pending",
    }
    path = tmp_path / "one.flac"
    _write_tagged_flac(path, MANIFEST[0])
    db.get_album_download_request.return_value = row
    db.get_album_download_request_recording_mbids.return_value = [MANIFEST[0]]
    db.get_local_release_recordings.return_value = [
        {"mbid": MANIFEST[0], "local_path": str(path)}
    ]

    result = album_request_status(db, 8)

    assert result.payload["request"]["stage"] == "importing"
    db.complete_album_download_request_by_id.assert_not_called()


def test_exact_inventory_verifies_flac_tags_paths_and_repeated_recordings(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    first = root / "one.flac"
    second = root / "two.flac"
    _write_tagged_flac(first, MANIFEST[0])
    _write_tagged_flac(second, MANIFEST[1])
    db = MagicMock()
    db.get_local_release_recordings.return_value = [
        {"mbid": MANIFEST[0], "local_path": str(first)},
        {"mbid": MANIFEST[1], "local_path": str(second)},
    ]

    exact = inspect_release_inventory(
        db,
        CANONICAL_ID,
        (MANIFEST[0], MANIFEST[0], MANIFEST[1]),
        str(root),
    )
    assert exact.exact is True
    assert exact.completed_tracks == 3

    _write_tagged_flac(second, MANIFEST[1], OTHER_ID)
    wrong_release = inspect_release_inventory(
        db,
        CANONICAL_ID,
        (MANIFEST[0], MANIFEST[1]),
        str(root),
    )
    assert wrong_release.exact is False
    assert wrong_release.inaccessible_recordings == (MANIFEST[1],)

    outside = tmp_path / "outside.flac"
    _write_tagged_flac(outside, MANIFEST[1])
    db.get_local_release_recordings.return_value[1]["local_path"] = str(outside)
    outside_library = inspect_release_inventory(
        db,
        CANONICAL_ID,
        (MANIFEST[0], MANIFEST[1]),
        str(root),
    )
    assert outside_library.exact is False


def test_status_fails_closed_for_failed_or_disappeared_active_queue_row():
    base = {
        "id": 8,
        "queue_item_id": 55,
        "release_mbid": CANONICAL_ID,
        "artist": "Artist",
        "title": "Album",
        "track_count": 2,
        "stage": "downloading",
        "detail": "50%",
        "completed_tracks": 0,
    }
    db = MagicMock()
    db.count_local_release_tracks.return_value = 0
    db.get_album_download_request.side_effect = [
        dict(base, queue_status="failed"),
        dict(base, stage="failed", detail="failed", queue_status="failed"),
    ]
    result = album_request_status(db, 8)
    assert result.payload["request"]["stage"] == "failed"
    db.update_album_download_request_progress.assert_called_once_with(
        55, "failed", "The master download attempt failed", 0
    )

    db.reset_mock()
    db.count_local_release_tracks.return_value = 0
    db.get_album_download_request.side_effect = [
        dict(base, queue_status=None),
        dict(base, stage="failed", detail="ended", queue_status=None),
    ]
    result = album_request_status(db, 8)
    assert result.payload["request"]["stage"] == "failed"
    db.update_album_download_request_progress.assert_called_once_with(
        55,
        "failed",
        "The master queue item ended before the album was imported",
        0,
    )


def test_db_tracker_persists_progress_and_counts_canonical_release(db):
    queue_id = db.queue_download(DownloadItem(
        "::ALBUM:: Artist - Album",
        ALBUM_PLAYLIST_ID,
        CANONICAL_ID,
    ))
    request_id = db.create_album_download_request(
        queue_item_id=queue_id,
        release_mbid=CANONICAL_ID,
        artist="Artist",
        title="Album",
        track_count=2,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=MANIFEST[:2],
    )
    assert db.update_album_download_request_progress(
        queue_id, "importing", "Imported one", 1
    ) is True
    row = db.get_album_download_request(request_id)
    assert row["stage"] == "importing"
    assert row["queue_status"] == "pending"
    assert row["completed_tracks"] == 1

    db.add_or_update_track(Track(
        mbid="recording-1",
        title="One",
        artist="Artist",
        album="Album",
        release_mbid=CANONICAL_ID.upper(),
        local_path="/music/one.flac",
    ))
    assert db.count_local_release_tracks(CANONICAL_ID) == 1

    assert db.complete_album_download_request(
        queue_id,
        "Verified complete",
        1,
    ) is True
    completed = db.get_album_download_request(request_id)
    assert completed["stage"] == "success"
    assert completed["completed_tracks"] == 1
    assert completed["queue_status"] is None
    assert db.get_download_status(queue_id) is None
    assert db.update_album_download_request_progress(
        queue_id,
        "failed",
        "late runner failure",
        0,
    ) is False
    assert db.get_album_download_request(request_id)["stage"] == "success"


def test_new_tracker_persists_picard_totals_and_release_track_identity(
    db,
    tmp_path,
):
    release_track_mbid = "10000000-0000-4000-8000-000000000001"
    manifest = ({
        "position": 1,
        "recording_mbid": MANIFEST[0],
        "medium_position": 2,
        "track_position": 3,
        "track_number": "3",
        "title": "Canonical Track",
        "artist": "Track Artist",
        "date": "2026-07-18",
        "track_total": 8,
        "disc_total": 2,
        "release_track_mbid": release_track_mbid,
    },)
    request_id = db.create_album_download_request(
        queue_item_id=None,
        release_mbid=CANONICAL_ID,
        artist="Album Artist",
        title="Canonical Album",
        track_count=1,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=MANIFEST[:1],
        track_manifest=manifest,
    )
    assert db.get_album_download_request_track_manifest(request_id) == [
        dict(manifest[0])
    ]

    root = tmp_path / "music"
    root.mkdir()
    path = root / "track.flac"
    _write_tagged_flac(path, MANIFEST[0])
    audio = FLAC(str(path))
    audio["title"] = "Canonical Track"
    audio["artist"] = "Track Artist"
    audio["album"] = "Canonical Album"
    audio["albumartist"] = "Album Artist"
    audio["date"] = "2026-07-18"
    audio["tracknumber"] = "3"
    audio["tracktotal"] = "8"
    audio["discnumber"] = "2"
    audio["disctotal"] = "2"
    audio["musicbrainz_releasetrackid"] = release_track_mbid
    audio.save()
    db.add_or_update_track(Track(
        mbid=MANIFEST[0],
        title="Canonical Track",
        artist="Track Artist",
        album="Canonical Album",
        release_mbid=CANONICAL_ID,
        track_number=3,
        disc_number=2,
        local_path=str(path),
    ))

    inventory = inspect_release_inventory(
        db,
        CANONICAL_ID,
        manifest,
        str(root),
    )
    assert inventory.exact is True
    assert inventory.completed_tracks == 1


def test_legacy_identity_only_tracker_uses_aligned_manifest_defaults(db):
    request_id = db.create_album_download_request(
        queue_item_id=None,
        release_mbid=CANONICAL_ID,
        artist="Artist",
        title="Album",
        track_count=1,
        stage="queued",
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=MANIFEST[:1],
    )

    assert db.get_album_download_request_track_manifest(request_id) == [{
        "position": 1,
        "recording_mbid": MANIFEST[0],
        "medium_position": 0,
        "track_position": 0,
        "track_number": "",
        "title": "",
        "artist": "",
        "date": "",
        "track_total": 0,
        "disc_total": 0,
        "release_track_mbid": "",
    }]


def test_db_claims_queue_and_creates_tracker_atomically(db):
    queue_id = db.queue_download(DownloadItem(
        "Old Artist - Old Album",
        "LEGACY",
        CANONICAL_ID.upper(),
        status="failed",
    ))
    request_id = db.claim_download_and_create_album_request(
        queue_item_id=queue_id,
        release_mbid=CANONICAL_ID,
        search_query="::ALBUM:: Artist - Album",
        playlist_id=ALBUM_PLAYLIST_ID,
        artist="Artist",
        title="Album",
        track_count=2,
        detail="Waiting",
        completed_tracks=0,
        recording_mbids=MANIFEST[:2],
    )

    assert request_id is not None
    tracker = db.get_album_download_request(request_id)
    assert tracker["queue_status"] == "pending"
    assert db.get_album_download_request_recording_mbids(request_id) == list(
        MANIFEST[:2]
    )
    queue = next(item for item in db.get_all_downloads() if item.id == queue_id)
    assert queue.search_query == "::ALBUM:: Artist - Album"
    assert queue.playlist_id == ALBUM_PLAYLIST_ID


def test_master_proxy_forwards_auth_and_classifies_network_or_non_json():
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True, "candidates": []}
    request = MagicMock(return_value=response)
    result = forward_master_json(
        "http://master:5001/",
        "GET",
        "/api/download/albums/search",
        api_token=" secret ",
        params={"q": "Album"},
        http_request=request,
    )
    assert result.payload["success"] is True
    request.assert_called_once_with(
        "GET",
        "http://master:5001/api/download/albums/search",
        params={"q": "Album"},
        json=None,
        headers={"Accept": "application/json", "Authorization": "Bearer secret"},
        timeout=(5, 45),
    )

    offline = forward_master_json(
        "http://master:5001",
        "GET",
        "/x",
        http_request=MagicMock(side_effect=requests.ConnectionError("offline")),
    )
    assert offline.status_code == 502
    assert "master unreachable" in offline.payload["message"]

    invalid_request = MagicMock()
    invalid_target = forward_master_json(
        "https://user:secret@master:5001/path?token=secret",
        "GET",
        "/x",
        http_request=invalid_request,
    )
    assert invalid_target.status_code == 409
    invalid_request.assert_not_called()

    response.status_code = 503
    response.json.side_effect = ValueError("html")
    invalid = forward_master_json(
        "http://master:5001",
        "GET",
        "/x",
        http_request=MagicMock(return_value=response),
    )
    assert invalid == AlbumRequestResult(
        {"success": False, "message": "master returned non-JSON (503)"},
        503,
    )
