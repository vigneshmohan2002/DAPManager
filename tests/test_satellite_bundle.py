"""Tests for the master-side satellite bundle cache + URL injection."""

import io
import json
import os
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch

import pytest

import web_server
from src import satellite_bundle


def _make_fake_bundle(path):
    """Mimic an upstream Tauri Mac zip: a couple of .app entries."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("DAPManager.app/Contents/Info.plist", "<plist>...</plist>")
        z.writestr("DAPManager.app/Contents/MacOS/DAPManager", b"\x7fELF-stub")
        z.writestr("DAPManager.app/Contents/Resources/icon.icns", b"icns-stub")


@pytest.fixture
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield tmp_path


def test_inject_adds_master_url_and_token():
    src = io.BytesIO()
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("DAPManager.app/Contents/Info.plist", "<plist/>")
    src.seek(0)
    src_path = io.BytesIO(src.getvalue())

    # inject_master_config wants a Path-like. Write to a temp file.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        f.write(src.getvalue())
        f.flush()
        try:
            out = satellite_bundle.inject_master_config(
                f.name, "http://master.tail-x.ts.net:5001", "tok123",
            )
        finally:
            os.unlink(f.name)

    with zipfile.ZipFile(io.BytesIO(out)) as z:
        names = set(z.namelist())
        assert "DAPManager.app/Contents/Info.plist" in names
        assert "DAPManager.app/Contents/Resources/master_url.txt" in names
        assert "DAPManager.app/Contents/Resources/master_token.txt" in names
        assert z.read(
            "DAPManager.app/Contents/Resources/master_url.txt"
        ).decode() == "http://master.tail-x.ts.net:5001"
        assert z.read(
            "DAPManager.app/Contents/Resources/master_token.txt"
        ).decode() == "tok123"


def test_inject_omits_token_when_empty(tmp_path):
    base = tmp_path / "base.zip"
    _make_fake_bundle(base)
    out = satellite_bundle.inject_master_config(base, "http://m:5001", "")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        names = set(z.namelist())
        assert "DAPManager.app/Contents/Resources/master_url.txt" in names
        assert "DAPManager.app/Contents/Resources/master_token.txt" not in names


def test_inject_replaces_existing_resource_entries(tmp_path):
    base = tmp_path / "base.zip"
    with zipfile.ZipFile(base, "w") as z:
        z.writestr("DAPManager.app/Contents/Info.plist", "<plist/>")
        z.writestr("DAPManager.app/Contents/Resources/master_url.txt", "stale")
    out = satellite_bundle.inject_master_config(base, "http://fresh:5001")
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        assert z.read(
            "DAPManager.app/Contents/Resources/master_url.txt"
        ).decode() == "http://fresh:5001"
        # No duplicate entry left over from the source.
        names = z.namelist()
        assert names.count("DAPManager.app/Contents/Resources/master_url.txt") == 1


def test_inject_rejects_blank_url(tmp_path):
    base = tmp_path / "base.zip"
    _make_fake_bundle(base)
    with pytest.raises(ValueError):
        satellite_bundle.inject_master_config(base, "")


def test_ensure_cached_returns_existing_without_fetching(cache_in_tmp):
    target = satellite_bundle.cached_bundle_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    _make_fake_bundle(target)

    with patch("urllib.request.urlopen") as mock_open:
        path = satellite_bundle.ensure_cached_bundle()
    assert path == target
    mock_open.assert_not_called()


def test_ensure_cached_fetches_on_miss(cache_in_tmp):
    target = satellite_bundle.cached_bundle_path()
    assert not target.exists()

    fake_zip = io.BytesIO()
    with zipfile.ZipFile(fake_zip, "w") as z:
        z.writestr("DAPManager.app/Contents/Info.plist", "<plist/>")

    class _Resp:
        status = 200

        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=-1):
            return self._buf.read(n)

    with patch(
        "urllib.request.urlopen", return_value=_Resp(fake_zip.getvalue())
    ) as mock_open:
        path = satellite_bundle.ensure_cached_bundle()

    mock_open.assert_called_once()
    assert path.exists()
    assert path.stat().st_size == len(fake_zip.getvalue())


def test_ensure_cached_propagates_fetch_failure(cache_in_tmp):
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("offline"),
    ):
        with pytest.raises(satellite_bundle.BundleFetchError):
            satellite_bundle.ensure_cached_bundle()
    assert not satellite_bundle.cached_bundle_path().exists()


# --------- /download/mac route ---------

@pytest.fixture
def master_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "public_master_url": "http://master.tail-x.ts.net:5001",
    }))
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(cfg_path))
    return cfg_path


def test_download_mac_409_when_public_url_unset(client, tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}")
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(cfg_path))
    res = client.get("/download/mac")
    assert res.status_code == 409
    assert "public_master_url" in res.get_json()["message"]


def test_download_mac_streams_injected_zip(client, master_config, tmp_path, monkeypatch):
    base = tmp_path / "base.zip"
    _make_fake_bundle(base)
    monkeypatch.setattr(
        "src.satellite_bundle.ensure_cached_bundle", lambda *a, **kw: base
    )
    res = client.get("/download/mac")
    assert res.status_code == 200
    assert res.mimetype == "application/zip"
    assert "DAPManager-mac.zip" in res.headers["Content-Disposition"]

    with zipfile.ZipFile(io.BytesIO(res.data)) as z:
        url = z.read(
            "DAPManager.app/Contents/Resources/master_url.txt"
        ).decode()
        assert url == "http://master.tail-x.ts.net:5001"
        assert (
            "DAPManager.app/Contents/Resources/master_token.txt"
            not in z.namelist()
        )


def test_download_mac_embeds_token_when_set(client, tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "public_master_url": "http://m:5001",
        "api_token": "secret",
    }))
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(cfg_path))
    base = tmp_path / "base.zip"
    _make_fake_bundle(base)
    monkeypatch.setattr(
        "src.satellite_bundle.ensure_cached_bundle", lambda *a, **kw: base
    )

    # Without token → 401.
    assert client.get("/download/mac").status_code == 401

    # With Bearer → 200, token embedded.
    res = client.get(
        "/download/mac", headers={"Authorization": "Bearer secret"}
    )
    assert res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(res.data)) as z:
        assert z.read(
            "DAPManager.app/Contents/Resources/master_token.txt"
        ).decode() == "secret"

    # With ?token= query → 200 too (browser-friendly variant).
    res = client.get("/download/mac?token=secret")
    assert res.status_code == 200


def test_download_mac_502_when_fetch_fails(client, master_config, monkeypatch):
    def boom(*a, **kw):
        raise satellite_bundle.BundleFetchError("offline")
    monkeypatch.setattr("src.satellite_bundle.ensure_cached_bundle", boom)
    res = client.get("/download/mac")
    assert res.status_code == 502
    assert "GitHub" in res.get_json()["message"]
