import json
from unittest.mock import MagicMock

import pytest

import web_server
from web_server import TaskManager
from src.db_manager import DatabaseManager, Track, DownloadItem


@pytest.fixture
def contrib_client(monkeypatch, tmp_path):
    """Flask client wired to a file-backed DB so POST/GET share state."""
    db_file = str(tmp_path / "master.db")
    # initialise schema
    DatabaseManager(db_file).close()

    cfg = MagicMock()
    cfg.db_path = db_file
    cfg.music_library = str(tmp_path / "music")
    cfg.downloads_dir = str(tmp_path / "downloads")
    cfg.picard_path = ""
    cfg.device_id = "master-dev"
    monkeypatch.setattr(web_server, "config", cfg)
    monkeypatch.setattr(web_server, "task_manager", TaskManager())
    # Bypass the first-run setup gate (before_request redirects to /setup
    # when no config file exists on disk).
    monkeypatch.setattr(web_server, "config_exists", lambda: True)
    # LibraryScanner falls back to get_config() when picard_path is empty.
    monkeypatch.setattr("src.library_scanner.get_config", lambda: cfg)

    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as client:
        client._db_file = db_file
        yield client


FLAC_Q = {
    "lossless": True, "bits_per_sample": 16, "sample_rate": 44100,
    "bitrate": 900000, "ext": "flac",
}


def _mark_needs_upload(client, contribution_id):
    with DatabaseManager(client._db_file) as db:
        db.update_contribution(contribution_id, status="needs_upload")


def test_post_contribution_queues_download_when_master_lacks_track(contrib_client):
    res = contrib_client.post("/api/contributions", json={
        "device_id": "sat-1", "mbid": "mb-1",
        "artist": "Boards of Canada", "title": "Roygbiv",
        "quality": FLAC_Q,
    })
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "attempting"
    cid = body["contribution_id"]

    # A CONTRIB download row was enqueued.
    with DatabaseManager(contrib_client._db_file) as db:
        contrib = db.get_contribution(cid)
        assert contrib["status"] == "attempting"
        assert contrib["download_id"] is not None
        assert db.get_download_status(contrib["download_id"]) == "pending"


def test_post_contribution_requires_artist_and_title(contrib_client):
    res = contrib_client.post("/api/contributions", json={"mbid": "x"})
    assert res.status_code == 400


def test_post_links_existing_inflight_download(contrib_client):
    # A download for this track is already queued (e.g. from a prior wishlist
    # action). The contribution must attach to it, not leave download_id None
    # (which the poll would read as "no attempt" → needs_upload).
    from src.db_manager import DownloadItem
    with DatabaseManager(contrib_client._db_file) as db:
        existing = db.queue_download(DownloadItem(
            search_query="A - B", playlist_id="CATALOG",
            mbid_guess="mb-dup", status="pending",
        ))

    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-dup", "artist": "A", "title": "B", "quality": FLAC_Q,
    })
    cid = res.get_json()["contribution_id"]
    with DatabaseManager(contrib_client._db_file) as db:
        assert db.get_contribution(cid)["download_id"] == existing
        # Keep this deterministic: the fixture's MagicMock config must use
        # the real one-hour fallback, not coerce a mock setting to one second.
        db.conn.execute(
            "UPDATE contributions SET created_at = "
            "datetime('now', '-5 seconds') WHERE id = ?",
            (cid,),
        )
        db.conn.commit()

    # Poll while that download is still pending → still attempting, no upload.
    body = contrib_client.get(f"/api/contributions/{cid}").get_json()
    assert body["status"] == "attempting"
    assert body["want_upload"] is False


def test_poll_reports_needs_upload_when_download_failed(contrib_client):
    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-2", "artist": "A", "title": "B", "quality": FLAC_Q,
    })
    cid = res.get_json()["contribution_id"]

    # Simulate the worker failing to find anything.
    with DatabaseManager(contrib_client._db_file) as db:
        dl_id = db.get_contribution(cid)["download_id"]
        db.update_download_status(dl_id, "failed")

    res = contrib_client.get(f"/api/contributions/{cid}")
    body = res.get_json()
    assert body["status"] == "needs_upload"
    assert body["want_upload"] is True


def test_post_short_circuits_have_better_when_master_already_good(contrib_client, tmp_path, monkeypatch):
    # Master already holds a same-or-better local copy → no download, no upload.
    f = tmp_path / "song.flac"
    f.write_bytes(b"fake")
    with DatabaseManager(contrib_client._db_file) as db:
        db.add_or_update_track(Track(
            mbid="mb-3", title="B", artist="A", album="C",
            local_path=str(f),
        ))
    monkeypatch.setattr("src.audio_quality.read_quality", lambda p: dict(FLAC_Q))

    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-3", "artist": "A", "title": "B", "quality": FLAC_Q,
    })
    body = res.get_json()
    assert body["status"] == "have_better"
    with DatabaseManager(contrib_client._db_file) as db:
        assert db.get_contribution(body["contribution_id"])["download_id"] is None


def test_post_matches_existing_copy_when_taggers_disagree_on_mbid(
    contrib_client, tmp_path, monkeypatch
):
    """Exact recording metadata prevents a redundant upload on MBID drift."""
    f = tmp_path / "master-copy.flac"
    f.write_bytes(b"fake")
    with DatabaseManager(contrib_client._db_file) as db:
        db.add_or_update_track(Track(
            mbid="master-mbid", title="Roygbiv", artist="Boards of Canada",
            album="Music Has the Right to Children", local_path=str(f),
        ))
    monkeypatch.setattr("src.audio_quality.read_quality", lambda p: dict(FLAC_Q))

    res = contrib_client.post("/api/contributions", json={
        "mbid": "satellite-mbid", "artist": "Boards of Canada",
        "title": "Roygbiv", "album": "Music Has the Right to Children",
        "quality": FLAC_Q,
    })

    assert res.status_code == 200
    assert res.get_json()["status"] == "have_better"


def test_poll_finds_download_scanned_under_a_different_mbid(
    contrib_client, tmp_path, monkeypatch
):
    """A completed download may be tagged to another valid recording MBID."""
    offer = contrib_client.post("/api/contributions", json={
        "mbid": "satellite-mbid", "isrc": "GBUM71029604",
        "artist": "James Blake", "title": "Limit to Your Love",
        "album": "James Blake", "quality": FLAC_Q,
    }).get_json()

    f = tmp_path / "downloaded.flac"
    f.write_bytes(b"fake")
    with DatabaseManager(contrib_client._db_file) as db:
        db.add_or_update_track(Track(
            mbid="master-mbid", isrc="gbum71029604",
            artist="James Blake", title="Limit to Your Love",
            album="James Blake", local_path=str(f),
        ))
    monkeypatch.setattr("src.audio_quality.read_quality", lambda p: dict(FLAC_Q))

    body = contrib_client.get(
        f"/api/contributions/{offer['contribution_id']}"
    ).get_json()
    assert body["status"] == "satisfied"
    assert body["want_upload"] is False


def test_identity_fallback_refuses_ambiguous_artist_title(
    contrib_client, tmp_path, monkeypatch
):
    """Two local editions without an album/ISRC remain upload-safe."""
    with DatabaseManager(contrib_client._db_file) as db:
        for index, album in enumerate(("Studio", "Live"), start=1):
            f = tmp_path / f"copy-{index}.flac"
            f.write_bytes(b"fake")
            db.add_or_update_track(Track(
                mbid=f"master-{index}", artist="A", title="Same Song",
                album=album, local_path=str(f),
            ))
    monkeypatch.setattr("src.audio_quality.read_quality", lambda p: dict(FLAC_Q))

    body = contrib_client.post("/api/contributions", json={
        "mbid": "satellite", "artist": "A", "title": "Same Song",
        "quality": FLAC_Q,
    }).get_json()
    assert body["status"] == "attempting"


def test_identity_fallback_refuses_ambiguous_same_album_metadata(
    contrib_client, tmp_path, monkeypatch
):
    """Duplicate tag rows on the same album must not pick the first file."""
    with DatabaseManager(contrib_client._db_file) as db:
        for index in (1, 2):
            f = tmp_path / f"same-album-{index}.flac"
            f.write_bytes(b"fake")
            db.add_or_update_track(Track(
                mbid=f"master-same-{index}", artist="A", title="Same Song",
                album="Same Album", local_path=str(f),
            ))
    monkeypatch.setattr("src.audio_quality.read_quality", lambda p: dict(FLAC_Q))

    body = contrib_client.post("/api/contributions", json={
        "mbid": "satellite", "artist": "A", "title": "Same Song",
        "album": "Same Album", "quality": FLAC_Q,
    }).get_json()
    assert body["status"] == "attempting"


def test_metadata_fallback_rejects_large_duration_mismatch(
    contrib_client, tmp_path, monkeypatch
):
    f = tmp_path / "different-recording.flac"
    f.write_bytes(b"fake")
    with DatabaseManager(contrib_client._db_file) as db:
        db.add_or_update_track(Track(
            mbid="master-other", artist="A", title="Song", album="Album",
            local_path=str(f),
        ))
    candidate_quality = {**FLAC_Q, "length_ms": 100_000}
    monkeypatch.setattr(
        "src.audio_quality.read_quality", lambda p: dict(candidate_quality)
    )

    body = contrib_client.post("/api/contributions", json={
        "mbid": "satellite", "artist": "A", "title": "Song",
        "album": "Album", "quality": {**FLAC_Q, "length_ms": 120_000},
    }).get_json()
    assert body["status"] == "attempting"


def test_poll_unknown_contribution_is_404(contrib_client):
    res = contrib_client.get("/api/contributions/9999")
    assert res.status_code == 404


def test_list_contributions_returns_parsed_quality(contrib_client):
    contrib_client.post("/api/contributions", json={
        "device_id": "sat-7", "mbid": "mb-list", "artist": "A", "title": "B",
        "quality": FLAC_Q,
    })
    res = contrib_client.get("/api/contributions")
    assert res.status_code == 200
    rows = res.get_json()["contributions"]
    assert len(rows) == 1
    assert rows[0]["device_id"] == "sat-7"
    # Quality JSON is parsed into an object for the client.
    assert rows[0]["target_quality"]["lossless"] is True


def test_list_contributions_refreshes_live_status(contrib_client):
    offer = contrib_client.post("/api/contributions", json={
        "mbid": "mb-refresh", "artist": "A", "title": "B",
        "quality": FLAC_Q,
    }).get_json()
    with DatabaseManager(contrib_client._db_file) as db:
        row = db.get_contribution(offer["contribution_id"])
        db.update_download_status(row["download_id"], "failed")

    rows = contrib_client.get("/api/contributions").get_json()["contributions"]
    assert rows[0]["status"] == "needs_upload"


def test_list_outgoing_contributions_includes_track_labels(contrib_client, tmp_path):
    local_file = tmp_path / "outgoing.flac"
    local_file.write_bytes(b"fake")
    with DatabaseManager(contrib_client._db_file) as db:
        db.add_or_update_track(Track(
            mbid="out-1", artist="Artist", title="Title", album="Album",
            local_path=str(local_file),
        ))
        db.upsert_contributed("out-1", 42, "attempting")

    rows = contrib_client.get("/api/contributed").get_json()["contributions"]
    assert rows[0]["contribution_id"] == 42
    assert rows[0]["artist"] == "Artist"
    assert rows[0]["title"] == "Title"


def test_contributions_page_renders(contrib_client):
    res = contrib_client.get("/contributions")
    assert res.status_code == 200
    assert b"Contributions" in res.data


def test_contribute_track_endpoint(contrib_client, monkeypatch):
    monkeypatch.setattr(
        "src.contribution_sync.main_run_contribute_one",
        lambda db, cfg, mbid: {"success": True, "mbid": mbid, "status": "attempting"},
    )
    res = contrib_client.post("/api/contribute/track", json={"mbid": "m1"})
    assert res.status_code == 200
    assert res.get_json()["status"] == "attempting"


def test_contribute_track_requires_mbid(contrib_client):
    res = contrib_client.post("/api/contribute/track", json={})
    assert res.status_code == 400


def test_upload_requires_file_field(contrib_client):
    res = contrib_client.post("/api/contributions/1/upload", json={})
    assert res.status_code == 400


def test_upload_ingests_file_and_marks_ingested(contrib_client):
    import io

    # No quality reported → verification only guards against an empty file.
    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-up", "artist": "Aphex Twin", "title": "Xtal",
        "album": "SAW 85-92",
    })
    cid = res.get_json()["contribution_id"]
    _mark_needs_upload(contrib_client, cid)

    res = contrib_client.post(
        f"/api/contributions/{cid}/upload",
        data={"file": (io.BytesIO(b"fake audio bytes"), "xtal.flac")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["status"] == "ingested"
    assert body["local_path"].endswith("Aphex Twin/SAW 85-92/Xtal.flac")

    with DatabaseManager(contrib_client._db_file) as db:
        assert db.get_contribution(cid)["status"] == "ingested"
        assert db.get_track_local_path("mb-up") == body["local_path"]
        # The fallback CONTRIB download was cleared.
        assert db.get_download_status(db.get_contribution(cid)["download_id"] or -1) is None


def test_poll_times_out_attempting_to_needs_upload(contrib_client, monkeypatch):
    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-to", "artist": "A", "title": "B", "quality": FLAC_Q,
    })
    cid = res.get_json()["contribution_id"]
    # Download is still pending, but the attempt window has elapsed → fall
    # back to upload rather than waiting on a stuck master queue forever.
    monkeypatch.setattr("web_server._attempt_timeout_seconds", lambda: 0)

    body = contrib_client.get(f"/api/contributions/{cid}").get_json()
    assert body["status"] == "needs_upload"
    assert body["want_upload"] is True


def test_upload_rejects_worse_quality_than_promised(contrib_client, monkeypatch):
    import io

    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-bad", "artist": "A", "title": "B", "quality": FLAC_Q,
    })
    cid = res.get_json()["contribution_id"]
    _mark_needs_upload(contrib_client, cid)

    # The staged file probes as a low-bitrate MP3 — worse than the FLAC promised.
    monkeypatch.setattr(
        "src.audio_quality.read_quality",
        lambda p: {"lossless": False, "bits_per_sample": 0,
                   "sample_rate": 44100, "bitrate": 128000},
    )
    res = contrib_client.post(
        f"/api/contributions/{cid}/upload",
        data={"file": (io.BytesIO(b"not as good"), "b.mp3")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 422
    assert res.get_json()["status"] == "rejected"
    with DatabaseManager(contrib_client._db_file) as db:
        # Not ingested; left recoverable for a retry.
        assert db.get_contribution(cid)["status"] == "needs_upload"
        assert db.get_track_local_path("mb-bad") is None


def test_upload_rejects_empty_file(contrib_client):
    import io

    res = contrib_client.post("/api/contributions", json={
        "mbid": "mb-empty", "artist": "A", "title": "B",
    })
    cid = res.get_json()["contribution_id"]
    _mark_needs_upload(contrib_client, cid)
    res = contrib_client.post(
        f"/api/contributions/{cid}/upload",
        data={"file": (io.BytesIO(b""), "empty.flac")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 422


def test_upload_refuses_terminal_contribution(contrib_client):
    import io
    from unittest.mock import patch

    offer = contrib_client.post("/api/contributions", json={
        "mbid": "mb-terminal", "artist": "A", "title": "B",
    }).get_json()
    cid = offer["contribution_id"]
    with DatabaseManager(contrib_client._db_file) as db:
        db.update_contribution(cid, status="satisfied")

    with patch("src.file_ingest.ingest_audio_file") as ingest:
        res = contrib_client.post(
            f"/api/contributions/{cid}/upload",
            data={"file": (io.BytesIO(b"stale upload"), "stale.flac")},
            content_type="multipart/form-data",
        )

    assert res.status_code == 409
    assert res.get_json()["status"] == "satisfied"
    ingest.assert_not_called()
    with DatabaseManager(contrib_client._db_file) as db:
        assert db.get_contribution(cid)["status"] == "satisfied"
        assert db.get_track_local_path("mb-terminal") is None


def test_upload_discards_staged_file_if_master_acquires_copy_during_upload(
    contrib_client, tmp_path, monkeypatch,
):
    import io
    from unittest.mock import patch

    offer = contrib_client.post("/api/contributions", json={
        "mbid": "mb-race", "artist": "A", "title": "B",
        "album": "Album", "quality": FLAC_Q,
    }).get_json()
    cid = offer["contribution_id"]
    _mark_needs_upload(contrib_client, cid)

    master_copy = tmp_path / "master-acquired.flac"
    master_copy.write_bytes(b"master's better copy")

    def acquire_while_upload_is_staged(_path, _target):
        with DatabaseManager(contrib_client._db_file) as db:
            db.add_or_update_track(Track(
                mbid="mb-race", artist="A", title="B", album="Album",
                local_path=str(master_copy),
            ))
        return None

    monkeypatch.setattr(web_server, "_verify_upload", acquire_while_upload_is_staged)
    monkeypatch.setattr("src.audio_quality.read_quality", lambda _p: dict(FLAC_Q))

    with patch("src.file_ingest.ingest_audio_file") as ingest:
        res = contrib_client.post(
            f"/api/contributions/{cid}/upload",
            data={"file": (io.BytesIO(b"satellite copy"), "satellite.flac")},
            content_type="multipart/form-data",
        )

    assert res.status_code == 200
    assert res.get_json()["status"] == "satisfied"
    ingest.assert_not_called()
    assert master_copy.read_bytes() == b"master's better copy"
    staging_dir = tmp_path / "downloads" / "_contrib"
    assert list(staging_dir.iterdir()) == []
    with DatabaseManager(contrib_client._db_file) as db:
        row = db.get_contribution(cid)
        assert row["status"] == "satisfied"
        assert db.get_track_local_path("mb-race") == str(master_copy)
