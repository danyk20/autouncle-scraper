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

    @responses.activate
    def test_merges_beautifulsoup_supplement(self):
        html = load_fixture("detail_6690428.html")
        responses.add(responses.GET, "https://www.autouncle.ch/de-ch/d/6690428", body=html, status=200)

        session = au.make_session()
        detail = au.fetch_detail("6690428", domain_cfg=DOMAIN_CFG, session=session)

        assert len(detail["imageUrls"]) == 4
        assert detail["equipment"]["Klimaanlage"] == "Ja"
        assert detail["sourcePlatform"] == "autoscout24-ch"
        assert detail["dealerName"] is None
        assert detail["vin"] is None


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


class TestExtractGalleryImages:
    def test_extracts_own_photos_only_by_matching_alt(self):
        html = load_fixture("detail_6690428.html")
        parsed = au.parse_detail_jsonld(html)
        images = au.extract_gallery_images(html, expected_alt=parsed["imageCaption"])

        # 4 unique uuids belong to this listing; a handful of "medium_"
        # thumbnails elsewhere on the page belong to OTHER listings (a
        # "similar cars" section) and must be excluded via the alt-text match.
        assert len(images) == 4
        assert all("car_images" in u for u in images)
        # Full-resolution (no size prefix) preferred over the small_ variant.
        assert all("/small_" not in u for u in images)

    def test_without_expected_alt_includes_everything_on_page(self):
        html = load_fixture("detail_6690428.html")
        images_scoped = au.extract_gallery_images(html, expected_alt="Gebraucht VW Golf 160 PS (117 kW) 2011 Cabrio")
        images_unscoped = au.extract_gallery_images(html)
        assert len(images_unscoped) > len(images_scoped)

    def test_no_images_on_page_returns_empty(self):
        assert au.extract_gallery_images("<html><body>no photos</body></html>") == []

    def test_ignores_non_string_src(self):
        # A malformed/missing src attribute shouldn't crash extraction.
        html = '<img alt="x"><img src="not-a-car-image-url">'
        assert au.extract_gallery_images(html) == []

    def test_ignores_car_images_src_with_unexpected_filename_shape(self):
        # Contains "car_images" but doesn't match the full URL pattern
        # (e.g. missing a file extension) - should be skipped, not crash.
        html = '<img src="https://images.autouncle.com/ch/car_images/no-extension-here">'
        assert au.extract_gallery_images(html) == []


class TestExtractEquipment:
    def test_extracts_real_fixture_equipment(self):
        html = load_fixture("detail_6690428.html")
        equipment = au.extract_equipment(html)

        assert equipment["Klimaanlage"] == "Ja"
        assert equipment["Isofix"] == "Ja"
        assert equipment["Türen"] == "2"
        assert equipment["Karosserie"] == "Cabrio"

    def test_ignores_lists_with_non_span_children(self):
        html = "<ul><li><div>not a span</div><span>value</span></li></ul>"
        assert au.extract_equipment(html) == {}

    def test_ignores_lists_with_wrong_child_count(self):
        html = "<ul><li><span>only-one-child</span></li></ul>"
        assert au.extract_equipment(html) == {}

    def test_ignores_empty_ul_with_no_li_children(self):
        html = "<ul></ul><ul><li><span>Klimaanlage</span><span>Ja</span></li></ul>"
        assert au.extract_equipment(html) == {"Klimaanlage": "Ja"}

    def test_ignores_li_with_empty_label(self):
        html = "<ul><li><span></span><span>value</span></li></ul>"
        assert au.extract_equipment(html) == {}

    def test_empty_page_returns_empty_dict(self):
        assert au.extract_equipment("<html><body></body></html>") == {}


class TestExtractSourceListing:
    def test_extracts_source_from_real_fixture(self):
        html = load_fixture("detail_6690428.html")
        source = au.extract_source_listing(html)
        assert source == {
            "sourcePlatform": "autoscout24-ch",
            "sourcePath": "/de-ch/das_wiedersehen/autoscout24-ch/6690428/8571289",
        }

    def test_returns_none_when_absent(self):
        assert au.extract_source_listing("<html><body>native listing</body></html>") is None


class TestPlaceholderExtractors:
    def test_extract_dealer_name_returns_none(self):
        assert au.extract_dealer_name("<html>anything</html>") is None

    def test_extract_description_returns_none(self):
        assert au.extract_description("<html>anything</html>") is None

    def test_extract_vin_returns_none(self):
        assert au.extract_vin("<html>anything</html>") is None
