"""Tests for the public scrape() orchestration function."""

from __future__ import annotations

import json
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


def _detail_html(listing_id: str, first_seen_at: str, price: int) -> str:
    """Minimal, synthetic (not a real capture) detail-page JSON-LD, used
    only to control firstSeenAt/price per id for sort/truncate tests -
    parse_detail_jsonld()'s real-fixture coverage lives in test_detail.py.

    `first_seen_at` must be in the "Preis am <date>" price-history date
    format parse_detail_jsonld() actually derives firstSeenAt/lastUpdatedAt
    from (e.g. "2026-01-01T00:00:00+00:00") - it is embedded as this
    listing's one and only price-history entry, not as the Dataset's own
    datePublished/dateModified, which are confirmed live to be
    request-time noise rather than real listing metadata (see
    parse_detail_jsonld()'s docstring)."""
    graph = [
        {
            "@type": ["Product", "Vehicle"],
            "@id": f"https://www.autouncle.ch/de-ch/d/{listing_id}-x#product",
            "model": "Golf Alltrack",
            "brand": {"@type": "Brand", "name": "VW"},
            "offers": {"@type": "Offer", "price": price, "priceCurrency": "CHF"},
        },
        {
            "@type": "Dataset",
            "variableMeasured": [
                {
                    "@type": "PropertyValue",
                    "name": f"Preis am {first_seen_at}",
                    "value": price,
                    "unitText": "CHF",
                }
            ],
        },
    ]
    return f'<script type="application/ld+json">{json.dumps({"@graph": graph})}</script>'


def _mock_detail(listing_id: str, first_seen_at: str, price: int) -> None:
    responses.add(
        responses.GET,
        f"https://www.autouncle.ch/de-ch/d/{listing_id}",
        body=_detail_html(listing_id, first_seen_at, price),
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

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"body_types": ["NotARealBodyType"]},
            {"fuel_types": ["NotARealFuel"]},
            {"colors": ["Chartreuse"]},
            {"seller_kind": "Robot"},
            {"equipment": ["hasFlyingCarMode"]},
        ],
    )
    def test_invalid_vocab_filter_raises_before_any_network_call(self, kwargs):
        with pytest.raises(ValueError):
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
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
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
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
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
    def test_level1_only_fields_survive_into_detail_records(self, search_form_config, no_sleep):
        """The detail page never renders modelVariant/priceChangePercent/
        sourcePath at all (see fetch_detail()) - so without carrying the
        level-1 RSC card's fields forward, a full detail=True scrape would
        silently lose them (e.g. the "85D"-style trim spec vanishing once
        a listing goes through the detail phase). scrape() must merge them
        back in from the level-1 record, without clobbering a field the
        detail page's own (more authoritative) data already set."""
        _mock_config(search_form_config)
        card = {
            "carId": "6979282",
            "subtitle": "85D",
            "priceChange": -12,
            "outgoingPath": "/de-ch/das_wiedersehen/some-portal/6979282/1",
        }
        rsc_text = (
            '"resultsInfo":"Zeige 1 - 1 von 1 Resultate","pagination":{"currentPage":1,"lastPage":true}\n'
            '4a:["$","div",null,{"children":[["$","$1","6979282",{"children":[false,false,false,"$L56",false]}]]}]\n'
            f'69:["$","$Lce",null,{json.dumps(card, separators=(",", ":"))}]\n'
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
            body=rsc_text,
            status=200,
        )
        _mock_detail("6979282", "2026-01-01T00:00:00+00:00", price=4500)

        result = au.scrape("VW", "Golf", price_to=5000, detail=True)

        row = result.rows[0]
        assert row["modelVariant"] == "85D"
        assert row["priceChangePercent"] == -12
        assert row["sourcePath"] == "/de-ch/das_wiedersehen/some-portal/6979282/1"
        # The detail page's own price must win over anything level 1 had.
        assert row["price"] == 4500

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
    def test_new_filter_dimensions_logged_verbosely(self, search_form_config, no_sleep, caplog):
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
            body_types=["SUV"],
            fuel_types=["Diesel"],
            colors=["Black"],
            doors=5,
            seller_kind="Dealer",
            one_owner=True,
            equipment=["hasGps"],
            extra_filters={"euroEmissionClass": 6},
            detail=False,
            verbose=True,
        )
        assert "body types ['SUV']" in caplog.text
        assert "fuel types ['Diesel']" in caplog.text
        assert "colors ['Black']" in caplog.text
        assert "equipment ['hasGps']" in caplog.text
        assert "doors 5" in caplog.text
        assert "seller Dealer" in caplog.text
        assert "one owner True" in caplog.text
        assert "extra {'euroEmissionClass': 6}" in caplog.text

    @responses.activate
    def test_filtered_no_detail_logs_info_note(self, search_form_config, no_sleep, caplog):
        import logging

        caplog.set_level(logging.INFO, logger="autouncle_scraper")
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        au.scrape("VW", "Golf", price_to=5000, detail=False)
        assert "not fuel type, transmission, engine power" in caplog.text


class TestScrapeMaxResults:
    def test_non_positive_max_results_raises(self):
        with pytest.raises(ValueError, match="max_results"):
            au.scrape("VW", "Golf", max_results=0)

    @responses.activate
    def test_max_results_only_opens_the_first_n_and_skips_the_rest(self, search_form_config, no_sleep):
        """max_results must cap *before* the detail phase - only the first N
        ids from search get a detail request at all; requesting a detail
        page for any of the other ids would 500 (not mocked), proving
        they're genuinely never fetched, not just fetched-then-discarded."""
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),  # 9 real ids, in a fixed order, see module-level helper
            status=200,
        )
        first_three_ids = ["6334442", "7001303", "6931690"]
        for listing_id in first_three_ids:
            _mock_detail(listing_id, "2026-01-01T00:00:00+00:00", price=10000)

        result = au.scrape("VW", "Golf", max_results=3)

        assert result.total_reported == 9  # the true total, unaffected by truncation
        assert len(result.listings) == 3
        assert len(result.rows) == 3
        assert [item["id"] for item in result.listings] == first_three_ids

    @responses.activate
    def test_max_results_larger_than_available_keeps_everything(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        for listing_id in [
            "6334442",
            "7001303",
            "6931690",
            "5160563",
            "6878461",
            "6767818",
            "6917430",
            "6836215",
            "6605992",
        ]:
            _mock_detail(listing_id, "2026-01-01T00:00:00+00:00", price=10000)

        result = au.scrape("VW", "Golf", max_results=1000)
        assert len(result.rows) == 9

    @responses.activate
    def test_max_results_works_without_detail(self, search_form_config, no_sleep):
        """Unlike the old semantics, max_results no longer requires detail=True -
        capping at level 1 (before any detail page is opened) is the whole point."""
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        result = au.scrape("VW", "Golf", detail=False, max_results=3)
        assert len(result.rows) == 3
        assert "priceHistory" not in result.rows[0]
