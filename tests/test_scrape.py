"""Tests for the public scrape() orchestration function."""

from __future__ import annotations

import re

import pytest
import responses

import autouncle_scraper as au
from tests.conftest import load_fixture

DOMAIN_CFG = au.get_domain_config("ch")


def _single_page_search_fixture() -> str:
    html = load_fixture("search_vw_golf_alltrack_page2.html")
    return html.replace('"numberOfItems":34', '"numberOfItems":9', 1)


def _mock_config(search_form_config):
    responses.add(
        responses.GET,
        "https://www.autouncle.ch/api/v4/car_search_form/config",
        json=search_form_config,
        status=200,
    )


class TestScrapeValidation:
    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="Unsupported category"):
            au.scrape("VW", "Golf", category="motorcycle")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"price_from": 5000, "price_to": 1000},
            {"mileage_from": 100000, "mileage_to": 1000},
            {"year_from": 2020, "year_to": 2015},
        ],
    )
    def test_invalid_range_raises_before_any_network_call(self, kwargs):
        with pytest.raises(ValueError, match="cannot be greater than"):
            au.scrape("VW", "Golf", **kwargs)


class TestScrapeUnfiltered:
    @responses.activate
    def test_returns_result_with_summary_rows(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        result = au.scrape("vw", "golf", detail=False, verbose=True)

        assert result.make == "VW"
        assert result.model == "Golf"
        assert result.domain == "ch"
        assert result.filtered is False
        assert result.total_reported == 9
        assert len(result.rows) == 9

    @responses.activate
    def test_visits_detail_pages_when_detail_true(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        responses.add(
            responses.GET,
            re.compile(r"https://www\.autouncle\.ch/de-ch/d/\d+$"),
            body=load_fixture("detail_6690428.html"),
            status=200,
        )
        result = au.scrape("VW", "Golf", detail=True)
        assert len(result.rows) == 9
        assert "priceHistory" in result.rows[0]

    @responses.activate
    def test_rows_sorted_by_price_ascending(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        result = au.scrape("VW", "Golf", detail=False)
        prices = [r["price"] for r in result.rows]
        assert prices == sorted(prices)


class TestScrapeFiltered:
    @responses.activate
    def test_filtered_scrape_returns_id_only_rows_when_no_detail(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf/mp-unter-5000-chf",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        result = au.scrape("VW", "Golf", price_to=5000, detail=False, verbose=True)
        assert result.filtered is True
        assert len(result.rows) == 5
        assert set(result.rows[0].keys()) == {"id"}

    @responses.activate
    def test_filtered_scrape_with_detail_fills_in_full_records(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf/mp-unter-5000-chf",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        responses.add(
            responses.GET,
            re.compile(r"https://www\.autouncle\.ch/de-ch/d/\d+$"),
            body=load_fixture("detail_6690428.html"),
            status=200,
        )
        result = au.scrape("VW", "Golf", price_to=5000, detail=True)
        assert len(result.rows) == 5
        assert "priceHistory" in result.rows[0]

    @responses.activate
    def test_all_three_filter_dimensions_logged_verbosely(self, search_form_config, no_sleep, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="autouncle_scraper")
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            re.compile(r"https://www\.autouncle\.ch/de-ch/gebrauchtwagen/VW/Golf.*"),
            body='"resultsInfo":"Zeige 0 - 0 von 0 Resultate","pagination":{"currentPage":1,"lastPage":true}',
            status=200,
        )
        au.scrape(
            "VW",
            "Golf",
            price_from=1000,
            price_to=5000,
            mileage_from=0,
            mileage_to=100000,
            year_from=2015,
            year_to=2020,
            detail=False,
            verbose=True,
        )
        assert "price 1000-5000 CHF" in caplog.text
        assert "mileage 0-100000 km" in caplog.text
        assert "year 2015-2020" in caplog.text

    @responses.activate
    def test_filtered_no_detail_logs_warning(self, search_form_config, no_sleep, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="autouncle_scraper")
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf/mp-unter-5000-chf",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        au.scrape("VW", "Golf", price_to=5000, detail=False)
        assert "RSC (the filtered-search data source) carries no summary fields" in caplog.text
