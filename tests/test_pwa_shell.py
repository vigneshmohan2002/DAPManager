"""Focused checks for the installable web shells and safe static caching."""

import hashlib
import json
from pathlib import Path

import pytest

import web_server


def test_canonical_logo_is_the_unmodified_supplied_png():
    logo = Path(__file__).parents[1] / "assets" / "branding" / "dapmanager-logo.png"

    assert hashlib.sha256(logo.read_bytes()).hexdigest() == (
        "6f0062c2f2aef776c12dac2b70608a0743b2c94a35f88cbefd8bb7b6b05596dd"
    )


@pytest.mark.parametrize(
    ("page_path", "manifest_path"),
    [
        ("/", "/static/manifest.json?v=3"),
        ("/player", "/static/manifest-player.webmanifest?v=3"),
        ("/satellite", "/static/manifest-satellite.webmanifest?v=3"),
    ],
)
def test_installable_pages_use_their_own_manifest_and_png_icons(
    client,
    mock_config,
    monkeypatch,
    tmp_path,
    page_path,
    manifest_path,
):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(config_path))

    response = client.get(page_path)

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'<link rel="manifest" href="{manifest_path}">' in html
    assert (
        '<link rel="apple-touch-icon" sizes="180x180" '
        'href="/static/icons/apple-touch-icon-180.png?v=3">'
    ) in html
    assert (
        '<link rel="icon" type="image/png" sizes="32x32" '
        'href="/static/icons/favicon-32.png?v=3">'
    ) in html
    assert '<script src="/static/pwa-register.js?v=3" defer></script>' in html
    assert "Viggys" not in html


@pytest.mark.parametrize(
    ("filename", "manifest_id", "start_url"),
    [
        ("manifest.json", "/", "/"),
        ("manifest-player.webmanifest", "/player", "/player"),
        ("manifest-satellite.webmanifest", "/satellite", "/satellite"),
    ],
)
def test_pwa_manifests_have_distinct_install_identities(
    filename,
    manifest_id,
    start_url,
):
    path = Path(web_server.app.static_folder) / filename
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["id"] == manifest_id
    assert manifest["start_url"] == start_url
    assert manifest["scope"] == "/"
    assert manifest["name"].startswith("DAPManager")
    assert all(icon["type"] == "image/png" for icon in manifest["icons"])


def test_root_service_worker_is_public_and_never_browser_cached(
    client,
    mock_config,
    monkeypatch,
):
    mock_config._config = {"api_token": "secret-xyz"}
    monkeypatch.setattr(web_server, "config_exists", lambda: True)

    response = client.get("/service-worker.js", follow_redirects=False)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == (
        "no-cache, no-store, must-revalidate"
    )
    assert response.headers["Service-Worker-Allowed"] == "/"
    assert "isCacheableRequest" in response.get_data(as_text=True)


@pytest.mark.parametrize(
    ("page_path", "controller"),
    [
        ("/player", "/static/js/player.js"),
        ("/satellite", "/static/js/satellite.js"),
    ],
)
def test_standalone_players_load_shared_url_helper_before_controller(
    client,
    mock_config,
    monkeypatch,
    tmp_path,
    page_path,
    controller,
):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(config_path))

    html = client.get(page_path).get_data(as_text=True)

    assert html.index('/static/js/base.js') < html.index(controller)


def test_satellite_library_loader_does_not_touch_removed_status_node():
    source = (
        Path(web_server.app.static_folder) / "js" / "satellite.js"
    ).read_text(encoding="utf-8")

    render_call = source.index("renderLibrary();")
    stale_access = source.find('$("lib-msg")', render_call)
    next_section = source.index("/* ─── PLAYER", render_call)
    assert stale_access == -1 or stale_access >= next_section


def test_standalone_players_explicitly_reveal_css_hidden_artwork():
    static_root = Path(web_server.app.static_folder)
    player = (static_root / "js" / "player.js").read_text(encoding="utf-8")
    satellite = (
        static_root / "js" / "satellite.js"
    ).read_text(encoding="utf-8")

    assert 'img.style.display = "block"' in player
    assert 'img.style.display="block"; ph.style.display="none";' in satellite
    assert satellite.count('style.display="block"') >= 3


def test_satellite_artwork_falls_back_without_intersection_observer():
    source = (
        Path(web_server.app.static_folder) / "js" / "satellite.js"
    ).read_text(encoding="utf-8")

    assert 'typeof IntersectionObserver === "undefined"' in source
    assert 'img.loading = "lazy"' in source
    assert "revealArtwork();" in source


def test_service_worker_policy_excludes_dynamic_or_sensitive_responses():
    source = (
        Path(web_server.app.static_folder) / "service-worker.js"
    ).read_text(encoding="utf-8")

    assert 'request.headers.has("Range")' in source
    assert 'request.headers.has("Authorization")' in source
    assert 'request.mode === "navigate"' in source
    assert 'request.destination === "document"' in source
    assert 'url.pathname.startsWith("/api/")' in source
    assert 'url.pathname === "/auth"' in source
    assert 'contentType.includes("text/html")' in source
    assert 'url.search === `?v=${STATIC_VERSION}`' in source
    assert "CACHEABLE_PATHS.has(url.pathname)" in source
