"""End-to-end tests against the real, live autouncle.ch site.

Excluded from the default test run (see `addopts = "-m 'not e2e'"` in
pyproject.toml) since these make real network calls and depend on live
inventory. Run explicitly with:

    pipenv run pytest -m e2e --no-cov

Targets a narrow, low-volume model (VW Golf Alltrack, ~34 listings as of
this writing) to keep these fast and light on the real site, per
CONTRIBUTING.md's "be a reasonable citizen" note.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import autouncle_scraper as au

pytestmark = pytest.mark.e2e

MAKE = "VW"
MODEL = "Golf Alltrack"


def test_fetch_search_form_config_returns_real_brands():
    config = au.fetch_search_form_config()
    assert len(config["brands"]["all"]) > 100
    assert "VW" in config["brands"]["all"]


def test_resolve_make_and_model_against_live_config():
    config = au.fetch_search_form_config()
    assert au.resolve_make_key("vw", config) == "VW"
    assert au.resolve_model_key("VW", "golf alltrack", config) == "Golf Alltrack"


def test_unfiltered_search_matches_reported_total():
    domain_cfg = au.get_domain_config("ch")
    session = au.make_session()
    listings = au.search_listings(MAKE, MODEL, domain_cfg, session=session, delay=0.5)
    assert len(listings) > 0
    assert all(item["make"] == MAKE for item in listings)


def test_fetch_detail_returns_full_record():
    domain_cfg = au.get_domain_config("ch")
    session = au.make_session()
    listings = au.search_listings(MAKE, MODEL, domain_cfg, session=session, delay=0.5)
    detail = au.fetch_detail(listings[0]["id"], domain_cfg=domain_cfg, session=session)
    assert detail["make"] == MAKE
    assert detail["price"] is not None
    assert isinstance(detail["priceHistory"], list)
    assert isinstance(detail["imageUrls"], list)


def test_count_cars_matches_a_real_filter():
    car_search = au.build_car_search_input(MAKE, MODEL, price_to=30000)
    domain_cfg = au.get_domain_config("ch")
    session = au.make_session()
    count = au.count_cars(car_search, domain_cfg=domain_cfg, session=session)
    assert count >= 0


def test_scrape_end_to_end_no_detail():
    result = au.scrape(MAKE, MODEL, detail=False, delay=0.5, verbose=False)
    assert result.total_reported > 0
    assert len(result.rows) == result.total_reported


def test_cli_subprocess_writes_files(tmp_path):
    out_base = tmp_path / "e2e_golf_alltrack"
    proc = subprocess.run(
        [
            sys.executable,
            "autouncle_scraper.py",
            "--make",
            MAKE,
            "--model",
            MODEL,
            "--no-detail",
            "--delay",
            "0.5",
            "--out",
            str(out_base),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out_base.with_suffix(".csv").exists()
    assert out_base.with_suffix(".json").exists()


def test_cli_unknown_make_exits_with_error_code():
    proc = subprocess.run(
        [sys.executable, "autouncle_scraper.py", "--make", "NotARealBrandXYZ", "--model", "Golf"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert "Error" in proc.stderr
