from unittest.mock import MagicMock, patch

import pytest

from src import wikipedia_client


def _ok_response(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _missing_response():
    resp = MagicMock()
    resp.status_code = 404
    return resp


@pytest.fixture(autouse=True)
def reset_cache():
    wikipedia_client.reset_for_tests()
    yield
    wikipedia_client.reset_for_tests()


def test_returns_summary_on_direct_hit():
    payload = {
        "type": "standard",
        "title": "Radiohead",
        "extract": "Radiohead are an English rock band.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Radiohead"}},
        "thumbnail": {"source": "https://example/thumb.jpg"},
    }
    with patch.object(wikipedia_client.requests, "get", return_value=_ok_response(payload)) as g:
        info = wikipedia_client.get_artist_summary("Radiohead")

    assert info == {
        "summary": "Radiohead are an English rock band.",
        "source_url": "https://en.wikipedia.org/wiki/Radiohead",
        "image_url": "https://example/thumb.jpg",
        "title": "Radiohead",
    }
    # Direct hit should not trigger the "(band)" fallback.
    assert g.call_count == 1


def test_falls_back_to_band_disambiguator_on_404():
    payload = {
        "type": "standard",
        "title": "Yes (band)",
        "extract": "Yes are an English rock band.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Yes_(band)"}},
    }
    responses = [_missing_response(), _ok_response(payload)]
    with patch.object(wikipedia_client.requests, "get", side_effect=responses) as g:
        info = wikipedia_client.get_artist_summary("Yes")

    assert info is not None
    assert info["title"] == "Yes (band)"
    assert g.call_count == 2
    # Second call should hit the "(band)" suffixed URL.
    second_url = g.call_args_list[1].args[0]
    assert "Yes_%28band%29" in second_url


def test_disambiguation_pages_treated_as_miss():
    payload = {"type": "disambiguation", "extract": "Pink may refer to: ..."}
    with patch.object(wikipedia_client.requests, "get", return_value=_ok_response(payload)) as g:
        info = wikipedia_client.get_artist_summary("Pink")
    # First call returns disambiguation → treated as None → fallback "(band)" call → also disambig.
    assert info is None
    assert g.call_count == 2


def test_negative_results_are_cached():
    with patch.object(wikipedia_client.requests, "get", return_value=_missing_response()) as g:
        first = wikipedia_client.get_artist_summary("Nonexistent Artist")
        second = wikipedia_client.get_artist_summary("Nonexistent Artist")

    assert first is None and second is None
    # Two HTTP calls for the first lookup (bare + "(band)"), zero for the second.
    assert g.call_count == 2


def test_positive_results_are_cached():
    payload = {
        "type": "standard",
        "title": "Beck",
        "extract": "Beck is an American musician.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Beck"}},
    }
    with patch.object(wikipedia_client.requests, "get", return_value=_ok_response(payload)) as g:
        first = wikipedia_client.get_artist_summary("Beck")
        second = wikipedia_client.get_artist_summary("Beck")

    assert first is not None and second is not None
    assert first == second
    assert g.call_count == 1


def test_user_agent_header_is_sent():
    payload = {
        "type": "standard",
        "title": "Foo",
        "extract": "Foo.",
        "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Foo"}},
    }
    with patch.object(wikipedia_client.requests, "get", return_value=_ok_response(payload)) as g:
        wikipedia_client.get_artist_summary("Foo")

    headers = g.call_args.kwargs.get("headers") or {}
    assert headers.get("User-Agent", "").startswith("DAPManager/")


def test_blank_name_short_circuits():
    with patch.object(wikipedia_client.requests, "get") as g:
        assert wikipedia_client.get_artist_summary("") is None
        assert wikipedia_client.get_artist_summary("   ") is None
    g.assert_not_called()


def test_request_exceptions_treated_as_miss():
    import requests as _requests

    with patch.object(
        wikipedia_client.requests, "get", side_effect=_requests.Timeout("slow")
    ) as g:
        info = wikipedia_client.get_artist_summary("Anything")
    assert info is None
    # Both bare and "(band)" attempts should run and both should fold to miss.
    assert g.call_count == 2
