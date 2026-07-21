"""Tests for detail-page JSON-LD + price-history parsing."""

from __future__ import annotations

import responses

import autouncle_scraper as au
from tests.conftest import load_fixture

DOMAIN_CFG = au.get_domain_config("ch")


def test_build_detail_url():
    assert au.build_detail_url(DOMAIN_CFG, "6690428") == "https://www.autouncle.ch/de-ch/d/6690428"


class TestParseDetailJsonld:
    def test_parses_real_fixture(self):
        html = load_fixture("detail_6690428.html")
        parsed = au.parse_detail_jsonld(html)

        assert parsed["id"] == "6690428"
        assert parsed["make"] == "VW"
        assert parsed["model"] == "Golf"
        assert parsed["year"] == 2011
        assert parsed["price"] == 5500
        assert parsed["priceCurrency"] == "CHF"
        assert parsed["mileageKm"] == 168000
        assert parsed["addressLocality"] == "Säriswil"
        assert parsed["addressRegion"] == "Bern"
        assert parsed["postalCode"] == "3049"
        assert parsed["enginePowerPs"] == 160
        assert parsed["enginePowerKw"] == 117

    def test_parses_price_rating_and_market_analysis_fields(self):
        html = load_fixture("detail_6690428.html")
        parsed = au.parse_detail_jsonld(html)

        assert parsed["priceRatingLabel"] == "Fairer Preis"
        assert parsed["savingsVsMarketChf"] == 700
        assert parsed["daysOnMarket"] == 139
        assert parsed["co2EmissionsLabel"] == 150

    def test_parses_price_history(self):
        html = load_fixture("detail_6690428.html")
        parsed = au.parse_detail_jsonld(html)

        history = parsed["priceHistory"]
        assert len(history) == 3
        assert history[0] == {
            "date": "2026-07-04T11:51:00+02:00",
            "price": 5500,
            "currency": "CHF",
            "description": "5’500 CHF erfasst am 2026-07-04T11:51:00+02:00",
        }
        # Chronologically descending in the source, as captured.
        assert [h["price"] for h in history] == [5500, 5900, 6500]

    def test_parses_dataset_license_metadata(self):
        html = load_fixture("detail_6690428.html")
        parsed = au.parse_detail_jsonld(html)

        assert parsed["datasetIsAccessibleForFree"] is True
        assert parsed["datasetLicense"] == "https://creativecommons.org/licenses/by/4.0/"

    def test_raises_when_no_vehicle_jsonld(self):
        import pytest

        with pytest.raises(ValueError, match="No Vehicle JSON-LD"):
            au.parse_detail_jsonld("<html><body>not a listing page</body></html>")

    def test_missing_dataset_yields_empty_price_history(self):
        html = (
            '<script type="application/ld+json">'
            '{"@graph":[{"@type":["Product","Vehicle"],"@id":"https://x/d/1-y#product"}]}'
            "</script>"
        )
        parsed = au.parse_detail_jsonld(html)
        assert parsed["priceHistory"] == []
        assert "datasetLicense" not in parsed


def test_price_history_from_dataset_skips_unparseable_entries(caplog):
    dataset = {
        "variableMeasured": [
            {"name": "Preis am 2026-07-04T11:51:00+02:00", "value": 100, "unitText": "CHF", "description": "d1"},
            {"name": "not a date at all", "value": 200},
        ]
    }
    history = au._price_history_from_dataset(dataset)
    assert len(history) == 1
    assert history[0]["price"] == 100


class TestFetchDetail:
    @responses.activate
    def test_fetches_and_parses(self):
        html = load_fixture("detail_6690428.html")
        responses.add(responses.GET, "https://www.autouncle.ch/de-ch/d/6690428", body=html, status=200)

        session = au.make_session()
        detail = au.fetch_detail("6690428", domain_cfg=DOMAIN_CFG, session=session)

        assert detail["id"] == "6690428"
        assert detail["url"] == "https://www.autouncle.ch/de-ch/d/6690428"
        assert detail["price"] == 5500


class TestVisitAllListings:
    @responses.activate
    def test_visits_each_id_and_returns_full_records(self, no_sleep):
        html = load_fixture("detail_6690428.html")
        responses.add(responses.GET, "https://www.autouncle.ch/de-ch/d/6690428", body=html, status=200)
        responses.add(responses.GET, "https://www.autouncle.ch/de-ch/d/6690428", body=html, status=200)

        session = au.make_session()
        visited = au.visit_all_listings(["6690428", "6690428"], domain_cfg=DOMAIN_CFG, session=session)

        assert len(visited) == 2
        assert all(v["id"] == "6690428" for v in visited)

    @responses.activate
    def test_empty_input_returns_empty(self, no_sleep):
        session = au.make_session()
        assert au.visit_all_listings([], domain_cfg=DOMAIN_CFG, session=session) == []
