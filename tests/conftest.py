"""Shared pytest fixtures for the autouncle_scraper test suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses as responses_lib

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> dict:
    return json.loads(load_fixture(name))


@pytest.fixture
def search_form_config() -> dict:
    return load_json_fixture("car_search_form_config.json")


@pytest.fixture
def mocked_responses():
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def no_sleep(monkeypatch):
    """Make time.sleep a no-op so tests exercising delay/retry loops run instantly."""
    import autouncle_scraper as au

    monkeypatch.setattr(au.time, "sleep", lambda *_args, **_kwargs: None)
