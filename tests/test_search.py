"""Tests for unfiltered JSON-LD search + pagination."""

from __future__ import annotations

import json
import re

import responses

import autouncle_scraper as au
from tests.conftest import load_fixture

DOMAIN_CFG = au.get_domain_config("ch")


def test_build_search_url_page1_has_no_query_string():
    url = au.build_search_url(DOMAIN_CFG, "VW", "Golf", page=1)
    assert url == "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf"


def test_build_search_url_encodes_spaces():
    url = au.build_search_url(DOMAIN_CFG, "VW", "Golf Alltrack", page=1)
    assert url == "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack"


def test_build_search_url_page2_has_query_string():
    url = au.build_search_url(DOMAIN_CFG, "VW", "Golf Alltrack", page=2)
    assert url == "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack?page=2"


class TestGraphExtraction:
    def test_graph_items_parses_real_fixture(self):
        html = load_fixture("search_vw_golf_alltrack_page1.html")
        graph = au._graph_items(html)
        # The real @graph has several entities (Organization, WebSite,
        # BreadcrumbList, ...) alongside the ItemList we actually care about.
        assert len(graph) > 1
        assert any(au._has_type(g, "ItemList") for g in graph)

    def test_graph_items_empty_on_no_ldjson(self):
        assert au._graph_items("<html><body>no data here</body></html>") == []

    def test_graph_items_skips_unparseable_block(self, caplog):
        html = '<script type="application/ld+json">{not valid json</script>'
        assert au._graph_items(html) == []

    def test_find_item_list(self):
        html = load_fixture("search_vw_golf_alltrack_page1.html")
        item_list = au.find_item_list(au._graph_items(html))
        assert item_list is not None
        assert item_list["numberOfItems"] == 34
        assert len(item_list["itemListElement"]) == 25

    def test_find_item_list_returns_none_when_absent(self):
        assert au.find_item_list([]) is None


class TestSearchListingsRealFixtures:
    @responses.activate
    def test_paginates_across_two_real_pages_no_duplicates(self, no_sleep):
        page1 = load_fixture("search_vw_golf_alltrack_page1.html")
        page2 = load_fixture("search_vw_golf_alltrack_page2.html")
        responses.add(
            responses.GET, "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack", body=page1, status=200
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack?page=2",
            body=page2,
            status=200,
        )

        session = au.make_session()
        listings = au.search_listings("VW", "Golf Alltrack", DOMAIN_CFG, session=session)

        assert len(listings) == 34
        ids = [item["id"] for item in listings]
        assert len(ids) == len(set(ids))  # no duplicates
        assert all(item["url"].startswith("https://www.autouncle.ch/de-ch/d/") for item in listings)
        assert all(item["make"] == "VW" for item in listings)

    @responses.activate
    def test_single_page_result_stops_after_page1(self, no_sleep):
        page1 = load_fixture("search_vw_golf_alltrack_page1.html")
        # Reuse page1's fixture but pretend numberOfItems matches its own
        # item count, simulating a single-page (<=25 results) search.
        import re

        html = page1
        m = re.search(r'"numberOfItems":(\d+)', html)
        html = html.replace(f'"numberOfItems":{m.group(1)}', '"numberOfItems":25', 1)
        responses.add(
            responses.GET, "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack", body=html, status=200
        )

        session = au.make_session()
        listings = au.search_listings("VW", "Golf Alltrack", DOMAIN_CFG, session=session)

        assert len(listings) == 25
        # 2 requests: the JSON-LD page itself, plus the same page's RSC
        # fetch for search-card supplements (see search_listings()).
        assert len(responses.calls) == 2

    @responses.activate
    def test_no_item_list_returns_empty(self, no_sleep):
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/NoSuchModel",
            body="<html><body>nothing</body></html>",
            status=200,
        )
        session = au.make_session()
        listings = au.search_listings("VW", "NoSuchModel", DOMAIN_CFG, session=session)
        assert listings == []


class TestSearchListingsCardSupplements:
    @responses.activate
    def test_merges_rsc_supplement_without_overwriting_jsonld_fields(self, no_sleep):
        """search_listings() fetches the same page twice: once for JSON-LD
        (registered first), once with the RSC header for search-card
        supplements (registered second) - `responses` serves same-URL mocks
        in registration order, so this exercises the real two-request path
        end to end, including the "fill gaps, don't clobber JSON-LD" merge
        rule in search_listings()."""
        html = load_fixture("search_vw_golf_alltrack_page1.html")
        m = re.search(r'"numberOfItems":(\d+)', html)
        html = html.replace(f'"numberOfItems":{m.group(1)}', '"numberOfItems":25', 1)
        first_id = re.search(r'"@id":"https://www\.autouncle\.ch/de-ch/d/(\d+)-', html).group(1)

        card = {"carId": first_id, "subtitle": "Special Trim", "laytime": 12, "price": "CHF\xa010’000"}
        rsc_text = f'69:["$","$Lce",null,{json.dumps(card, ensure_ascii=False, separators=(",", ":"))}]\n'

        responses.add(
            responses.GET, "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack", body=html, status=200
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack",
            body=rsc_text,
            status=200,
        )

        session = au.make_session()
        listings = au.search_listings("VW", "Golf Alltrack", DOMAIN_CFG, session=session)
        item = next(i for i in listings if i["id"] == first_id)

        assert item["modelVariant"] == "Special Trim"
        assert item["daysOnMarket"] == 12
        # JSON-LD's own price is authoritative - the supplement must not
        # overwrite a field JSON-LD already populated.
        assert item["price"] != 10000


class TestParseVehicleJsonld:
    def test_parses_search_result_item_shape(self):
        html = load_fixture("search_vw_golf_alltrack_page1.html")
        item_list = au.find_item_list(au._graph_items(html))
        first_item = item_list["itemListElement"][0]["item"]
        parsed = au.parse_vehicle_jsonld(first_item)

        assert parsed["id"] is not None
        assert parsed["make"] == "VW"
        assert parsed["model"] == "Golf Alltrack"
        assert isinstance(parsed["year"], int)
        assert parsed["price"] is not None
        assert parsed["priceCurrency"] == "CHF"
        assert parsed["mileageKm"] is not None
        assert parsed["fuelType"] is not None

    def test_missing_optional_fields_default_to_none(self):
        parsed = au.parse_vehicle_jsonld({"@id": "https://example/d/123-foo#product"})
        assert parsed["id"] == "123"
        assert parsed["price"] is None
        assert parsed["mileageKm"] is None
        assert parsed["enginePowerKw"] is None

    def test_additional_properties_mapped_and_unmapped_kept(self):
        item = {
            "@id": "https://example/d/123-foo#product",
            "additionalProperty": [
                {"name": "Preisbewertung", "value": "Fairer Preis"},
                {"name": "Tage auf dem Markt", "value": 139},
                {"name": "Some Unknown Label", "value": "whatever"},
            ],
        }
        parsed = au.parse_vehicle_jsonld(item)
        assert parsed["priceRatingLabel"] == "Fairer Preis"
        assert parsed["daysOnMarket"] == 139
        assert parsed["otherProperties"] == [{"name": "Some Unknown Label", "value": "whatever"}]


def test_listing_id_from_iri():
    assert au._listing_id_from_iri("https://www.autouncle.ch/de-ch/d/6690428-gebraucht-2011-vw-golf-160-ps") == (
        "6690428"
    )
    assert au._listing_id_from_iri("https://example.com/no-id-here") is None


class TestToInt:
    def test_none_returns_none(self):
        assert au._to_int(None) is None

    def test_valid_values_convert(self):
        assert au._to_int("2011") == 2011
        assert au._to_int(2011) == 2011

    def test_unconvertible_value_returns_none(self):
        assert au._to_int("not-a-year") is None
        assert au._to_int(object()) is None


class TestSearchListingsSafetyNet:
    @responses.activate
    def test_stops_early_when_a_later_page_yields_no_new_listings(self, no_sleep):
        """A live inventory can shift between requests; if page 2 reports
        more pages remain but yields zero new ids, stop rather than loop
        forever re-fetching the same (or an emptied) page."""
        page1 = load_fixture("search_vw_golf_alltrack_page1.html")
        responses.add(
            responses.GET, "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack", body=page1, status=200
        )
        # Page 2 duplicates page 1's own items instead of the real remainder,
        # simulating an inventory shift: no *new* ids on this "page".
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20Alltrack?page=2",
            body=page1,
            status=200,
        )
        session = au.make_session()
        listings = au.search_listings("VW", "Golf Alltrack", DOMAIN_CFG, session=session)
        assert len(listings) == 25
        # 2 pages visited, each fetched twice (JSON-LD + RSC supplements).
        assert len(responses.calls) == 4
