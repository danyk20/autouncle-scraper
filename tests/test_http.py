"""Unit tests for make_session(), request_with_retries(), and domain config."""

from __future__ import annotations

import pytest
import requests
import responses

import autouncle_scraper as au


def test_make_session_sets_default_headers():
    session = au.make_session()
    assert "Mozilla" in session.headers["User-Agent"]
    assert str(session.headers["Accept-Language"]).startswith("de-CH")


def test_get_domain_config_ch():
    cfg = au.get_domain_config("ch")
    assert cfg.host == "www.autouncle.ch"
    assert cfg.locale == "de-ch"
    assert cfg.cars_path == "gebrauchtwagen"


def test_get_domain_config_default_is_ch():
    assert au.get_domain_config() == au.get_domain_config("ch")


def test_get_domain_config_unsupported_raises():
    with pytest.raises(ValueError, match="Unsupported domain"):
        au.get_domain_config("de")


@responses.activate
def test_request_with_retries_succeeds_first_try():
    responses.add(responses.GET, "https://example.test/ok", json={"ok": True}, status=200)
    session = au.make_session()

    resp = au.request_with_retries(session, "GET", "https://example.test/ok")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 1


@responses.activate
def test_request_with_retries_retries_on_429_then_succeeds(no_sleep):
    responses.add(responses.GET, "https://example.test/flaky", status=429)
    responses.add(responses.GET, "https://example.test/flaky", json={"ok": True}, status=200)
    session = au.make_session()

    resp = au.request_with_retries(session, "GET", "https://example.test/flaky")

    assert resp.status_code == 200
    assert len(responses.calls) == 2


@responses.activate
def test_request_with_retries_retries_on_500_then_succeeds(no_sleep):
    responses.add(responses.GET, "https://example.test/flaky500", status=503)
    responses.add(responses.GET, "https://example.test/flaky500", json={"ok": True}, status=200)
    session = au.make_session()

    resp = au.request_with_retries(session, "GET", "https://example.test/flaky500")

    assert resp.status_code == 200
    assert len(responses.calls) == 2


@responses.activate
def test_request_with_retries_raises_after_exhausting_retries_on_persistent_500(no_sleep):
    for _ in range(5):
        responses.add(responses.GET, "https://example.test/always500", status=500)
    session = au.make_session()

    with pytest.raises(requests.HTTPError) as excinfo:
        au.request_with_retries(session, "GET", "https://example.test/always500", max_retries=5)
    assert excinfo.value.response.status_code == 500
    assert len(responses.calls) == 5


@responses.activate
def test_request_with_retries_does_not_retry_on_client_error():
    responses.add(responses.GET, "https://example.test/notfound", status=404)
    session = au.make_session()

    with pytest.raises(requests.HTTPError) as excinfo:
        au.request_with_retries(session, "GET", "https://example.test/notfound")
    assert excinfo.value.response.status_code == 404
    assert len(responses.calls) == 1


@responses.activate
def test_request_with_retries_retries_on_connection_error_then_succeeds(no_sleep):
    responses.add(
        responses.GET,
        "https://example.test/conn-flaky",
        body=requests.ConnectionError("boom"),
    )
    responses.add(responses.GET, "https://example.test/conn-flaky", json={"ok": True}, status=200)
    session = au.make_session()

    resp = au.request_with_retries(session, "GET", "https://example.test/conn-flaky")

    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 2


@responses.activate
def test_request_with_retries_raises_after_exhausting_retries_on_connection_error(no_sleep):
    for _ in range(3):
        responses.add(
            responses.GET,
            "https://example.test/always-down",
            body=requests.ConnectionError("boom"),
        )
    session = au.make_session()

    with pytest.raises(requests.ConnectionError):
        au.request_with_retries(session, "GET", "https://example.test/always-down", max_retries=3)
    assert len(responses.calls) == 3
