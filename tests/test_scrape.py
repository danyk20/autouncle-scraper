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
    def test_filtered_no_detail_logs_warning(self, search_form_config, no_sleep, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="autouncle_scraper")
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        au.scrape("VW", "Golf", price_to=5000, detail=False)
        assert "RSC (the filtered-search data source) carries no summary fields" in caplog.text


class TestScrapeMaxResults:
    def test_non_positive_max_results_raises(self):
        with pytest.raises(ValueError, match="max_results"):
            au.scrape("VW", "Golf", max_results=0)

    def test_max_results_with_detail_false_raises(self):
        with pytest.raises(ValueError, match="max_results requires detail=True"):
            au.scrape("VW", "Golf", detail=False, max_results=5)

    @responses.activate
    def test_sorts_newest_first_and_truncates(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),  # 9 real ids, see module-level helper
            status=200,
        )
        ids = ["6334442", "7001303", "6931690", "5160563", "6878461", "6767818", "6917430", "6836215", "6605992"]
        # Deliberately out-of-order firstSeenAt values, one per id.
        timestamps = [
            "2026-01-01T00:00:00+00:00",
            "2026-06-15T00:00:00+00:00",  # newest
            "2026-03-01T00:00:00+00:00",
            "2025-12-01T00:00:00+00:00",
            "2026-05-01T00:00:00+00:00",  # 2nd newest
            "2026-02-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
            "2025-11-01T00:00:00+00:00",
            "2026-01-15T00:00:00+00:00",
        ]
        for listing_id, ts in zip(ids, timestamps, strict=True):
            _mock_detail(listing_id, ts, price=10000)

        result = au.scrape("VW", "Golf", max_results=3)

        assert result.total_reported == 9  # the true total, unaffected by truncation
        assert len(result.listings) == 3
        assert len(result.rows) == 3
        assert [item["id"] for item in result.listings] == ["7001303", "6878461", "6917430"]
        assert [row["firstSeenAt"] for row in result.rows] == [
            "2026-06-15T00:00:00+00:00",
            "2026-05-01T00:00:00+00:00",
            "2026-04-01T00:00:00+00:00",
        ]

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
    def test_listings_with_unknown_first_seen_sort_last(self, search_form_config, no_sleep):
        _mock_config(search_form_config)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        ids = ["6334442", "7001303", "6931690", "5160563", "6878461", "6767818", "6917430", "6836215", "6605992"]
        for i, listing_id in enumerate(ids):
            if i == 0:
                # No Dataset object at all -> firstSeenAt is None.
                responses.add(
                    responses.GET,
                    f"https://www.autouncle.ch/de-ch/d/{listing_id}",
                    body=(
                        '<script type="application/ld+json">{"@graph":[{"@type":["Product","Vehicle"],'
                        f'"@id":"https://www.autouncle.ch/de-ch/d/{listing_id}-x#product"}}]}}'
                        "</script>"
                    ),
                    status=200,
                )
            else:
                _mock_detail(listing_id, "2026-01-01T00:00:00+00:00", price=10000)

        result = au.scrape("VW", "Golf", max_results=9)
        # The id with no firstSeenAt (index 0, "6334442") must sort last.
        assert result.rows[-1]["id"] == "6334442"
        assert result.rows[-1]["firstSeenAt"] == ""
