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


def test_poll_unknown_contribution_is_404(contrib_client):
    res = contrib_client.get("/api/contributions/9999")
    assert res.status_code == 404


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
    res = contrib_client.post(
        f"/api/contributions/{cid}/upload",
        data={"file": (io.BytesIO(b""), "empty.flac")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 422
