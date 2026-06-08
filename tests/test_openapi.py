import re
from unittest.mock import MagicMock

import web_server
from web_server import app, TaskManager
from src.openapi_spec import build_spec


def _client():
    app.config["TESTING"] = True
    return app.test_client()


def _norm(path: str) -> str:
    # Collapse both Flask <...> and OpenAPI {...} params to one placeholder.
    return re.sub(r"[<{][^>}]+[>}]", "{}", path)


def test_spec_is_wellformed():
    spec = build_spec()
    assert spec["openapi"].startswith("3.")
    assert spec["paths"]
    assert spec["info"]["title"] == "DAPManager API"


def test_every_documented_path_exists_in_url_map():
    spec = build_spec()
    flask_rules = {_norm(r.rule) for r in app.url_map.iter_rules()}
    missing = [p for p in spec["paths"] if _norm(p) not in flask_rules]
    assert not missing, f"documented but not routed: {missing}"


def test_docs_and_spec_reachable_before_setup(monkeypatch):
    # No config on disk → setup gate must NOT redirect the docs.
    monkeypatch.setattr(web_server, "config_exists", lambda: False)
    c = _client()
    assert c.get("/docs", follow_redirects=False).status_code == 200
    r = c.get("/api/openapi.json")
    assert r.status_code == 200
    assert r.get_json()["openapi"].startswith("3.")


def test_spec_exempt_from_bearer_token(monkeypatch):
    # Even with api_token set, the spec is readable without a bearer header.
    cfg = MagicMock()
    cfg._config = {"api_token": "secret"}
    monkeypatch.setattr(web_server, "config", cfg)
    monkeypatch.setattr(web_server, "task_manager", TaskManager())
    monkeypatch.setattr(web_server, "config_exists", lambda: True)
    c = _client()
    assert c.get("/api/openapi.json").status_code == 200


def test_contribution_paths_are_documented():
    spec = build_spec()
    for p in ("/api/save_config", "/api/contribute", "/api/contributions",
              "/api/contributions/{id}/upload"):
        assert p in spec["paths"], p
