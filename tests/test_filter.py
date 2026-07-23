"""Tests for filtered search: URL construction, RSC parsing, GraphQL countCars."""

from __future__ import annotations

import json

import pytest
import responses

import autouncle_scraper as au
from tests.conftest import load_fixture

DOMAIN_CFG = au.get_domain_config("ch")


def _card_obj(car_id: str, **overrides):
    """A search-result card's RSC JSON object, shaped like a real capture
    (see parse_search_card_object()'s docstring) - not a full real capture
    itself, but every field name/nesting here was confirmed against one."""
    obj = {
        "title": "Gebraucht (2017) Tesla Model S 772 PS | Guter Preis",
        "subtitle": "P90D (Free Supercharging)",
        "carId": car_id,
        "sourceName": "Autoscout24",
        "outgoingPath": "/de-ch/das_wiedersehen/autoscout24-ch/6910126/9222511",
        "imageUrls": [
            "https://images.autouncle.com/ch/car_images/aaa_x.webp",
            "https://images.autouncle.com/ch/car_images/bbb_x.webp",
        ],
        "imageAltText": "Gebraucht Tesla Model S 567 kW (772 PS) 2017 Kleinwagen",
        "km": 125250,
        "brand": "Tesla",
        "carModel": "Model S",
        "laytime": 45,
        "year": 2017,
        "doors": 5,
        "body": "Hatchback",
        "countryCurrencyCode": "chf",
        "outgoingPathUnused": None,
        "youSaveDifference": 2200,
        "modalPriceHistoryValues": {
            "estimatedPrice": "CHF\xa026’217",
            "youSave": "CHF\xa02’200",
        },
        "location": "7546 Scuol, Graubünden",
        "rating": 4,
        "price": "CHF\xa024’000",
        "priceChange": -36,
    }
    obj.update(overrides)
    return obj


def _rsc_chunk(obj) -> str:
    """Wrap a card object the way it actually appears in an RSC/Flight
    response: a numbered chunk line, a React element reference, then the
    object literal itself - React's own serializer emits compact JSON (no
    spaces after ':'/','), which is what the extraction regex expects, so
    match that here rather than json.dumps()'s default spaced-out form."""
    compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return f'69:["$","$Lce",null,{compact}]\n'


class TestBuildFilteredSearchUrl:
    def test_max_price_becomes_plain_query_param(self):
        # No slug construction - AutoUncle's own canonicalization redirect
        # (followed by fetch_rsc_page()) handles turning this into
        # /mp-unter-5000-chf; build_filtered_search_url() always emits the
        # uniform s[...] query-param form. See TestFetchRscPageRedirect for
        # the redirect-following behavior itself.
        car_search = au.build_car_search_input("VW", "Golf", price_to=5000)
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert url == "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000"

    def test_other_filters_become_sorted_query_params(self):
        car_search = au.build_car_search_input(
            "VW", "Golf VIII", mileage_to=50000, mileage_from=1000, year_to=2024, price_from=15000
        )
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf VIII", car_search)
        assert url == (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?s%5Bmax_km%5D=50000&s%5Bmax_year%5D=2024&s%5Bmin_km%5D=1000&s%5Bmin_price%5D=15000"
        )

    def test_price_and_year_combined(self):
        car_search = au.build_car_search_input("VW", "Golf VIII", price_to=30000, year_from=2022)
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf VIII", car_search)
        assert url == (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII?s%5Bmax_price%5D=30000&s%5Bmin_year%5D=2022"
        )

    def test_page_param_included_when_greater_than_1(self):
        car_search = au.build_car_search_input("VW", "Golf VIII", price_to=30000, year_from=2022)
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf VIII", car_search, page=2)
        assert url == (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?page=2&s%5Bmax_price%5D=30000&s%5Bmin_year%5D=2022"
        )

    def test_page1_has_no_page_param(self):
        car_search = au.build_car_search_input("VW", "Golf", price_to=5000)
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search, page=1)
        assert "page=" not in url

    def test_no_filters_yields_plain_url(self):
        car_search = au.build_car_search_input("VW", "Golf")
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert url == "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf"

    def test_array_valued_filter_repeats_bracket_key(self):
        car_search = au.build_car_search_input("VW", "Golf", body_types=["SUV", "Coupe"])
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert url == (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf"
            "?s%5Bbody_types%5D%5B%5D=SUV&s%5Bbody_types%5D%5B%5D=Coupe"
        )

    def test_boolean_filter_becomes_true_false_string(self):
        car_search = au.build_car_search_input("VW", "Golf", one_owner=True, equipment=["hasGps"])
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert "s%5Bis_one_owner%5D=true" in url
        assert "s%5Bhas_gps%5D=true" in url

    def test_already_snake_case_field_is_idempotent(self):
        car_search = au.build_car_search_input("VW", "Golf", equipment=["has_4wd"])
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert "s%5Bhas_4wd%5D=true" in url

    def test_path_only_keys_are_not_emitted_as_query_params(self):
        car_search = au.build_car_search_input("VW", "Golf")
        url = au.build_filtered_search_url(DOMAIN_CFG, "VW", "Golf", car_search)
        assert "brand" not in url
        assert "carModel" not in url and "car_model" not in url
        assert "brandsModels" not in url


class TestParseRscPagination:
    def test_parses_real_single_page_fixture(self):
        rsc_text = load_fixture("rsc_vw_golf_mp5000_page1.txt")
        pagination = au.parse_rsc_pagination(rsc_text)
        assert pagination == {
            "currentPage": 1,
            "lastPage": True,
            "resultsInfo": "Zeige 1 - 5 von 5 Resultate",
            "total": 5,
        }

    def test_parses_real_multi_page_fixture_page1(self):
        rsc_text = load_fixture("rsc_vw_golf8_mp30000_minyear2022_page1.txt")
        pagination = au.parse_rsc_pagination(rsc_text)
        assert pagination["currentPage"] == 1
        assert pagination["lastPage"] is False
        assert pagination["total"] == 903

    def test_parses_real_multi_page_fixture_page2(self):
        rsc_text = load_fixture("rsc_vw_golf8_mp30000_minyear2022_page2.txt")
        pagination = au.parse_rsc_pagination(rsc_text)
        assert pagination["currentPage"] == 2
        assert pagination["resultsInfo"] == "Zeige 26 - 50 von 903 Resultate"

    def test_parses_query_only_filter_combo_fixture(self):
        rsc_text = load_fixture("rsc_vw_golf8_multi_query_only.txt")
        pagination = au.parse_rsc_pagination(rsc_text)
        assert pagination["total"] == 505

    def test_missing_data_yields_none_fields(self):
        pagination = au.parse_rsc_pagination("no pagination data here")
        assert pagination == {"currentPage": None, "lastPage": None, "resultsInfo": None, "total": None}


class TestParseRscListingIds:
    def test_extracts_all_ids_single_page_fixture(self):
        rsc_text = load_fixture("rsc_vw_golf_mp5000_page1.txt")
        ids = au.parse_rsc_listing_ids(rsc_text)
        assert len(ids) == 5
        assert set(ids) == {"6979282", "6963095", "6998522", "6990234", "6835111"}

    def test_extracts_25_ids_from_multi_page_fixtures_no_overlap(self):
        page1 = au.parse_rsc_listing_ids(load_fixture("rsc_vw_golf8_mp30000_minyear2022_page1.txt"))
        page2 = au.parse_rsc_listing_ids(load_fixture("rsc_vw_golf8_mp30000_minyear2022_page2.txt"))
        assert len(page1) == 25
        assert len(page2) == 25
        assert set(page1).isdisjoint(set(page2))

    def test_extracts_25_ids_from_query_only_combo_fixture(self):
        ids = au.parse_rsc_listing_ids(load_fixture("rsc_vw_golf8_multi_query_only.txt"))
        assert len(ids) == 25

    def test_legitimately_zero_results_returns_empty_without_raising(self):
        text = '"resultsInfo":"Zeige 0 - 0 von 0 Resultate"'
        assert au.parse_rsc_listing_ids(text) == []

    def test_raises_when_ids_missing_but_total_nonzero(self):
        import pytest

        text = '"resultsInfo":"Zeige 1 - 25 von 903 Resultate"'  # no id-shaped chunks present
        with pytest.raises(RuntimeError, match="RSC listing-id pattern"):
            au.parse_rsc_listing_ids(text)


class TestParseChfAmount:
    def test_parses_display_string_with_thousands_separator(self):
        assert au._parse_chf_amount("CHF\xa026’217") == 26217

    def test_non_string_returns_none(self):
        assert au._parse_chf_amount(None) is None
        assert au._parse_chf_amount(26217) is None

    def test_no_digits_returns_none(self):
        assert au._parse_chf_amount("n/a") is None


class TestParseLocationString:
    def test_parses_postal_locality_region(self):
        assert au._parse_location_string("7546 Scuol, Graubünden") == {
            "postalCode": "7546",
            "addressLocality": "Scuol",
            "addressRegion": "Graubünden",
        }

    def test_unrecognized_shape_returns_empty_dict(self):
        assert au._parse_location_string("somewhere, unparseable") == {}
        assert au._parse_location_string("") == {}


class TestParseSearchCardObject:
    def test_parses_all_fields_from_real_shaped_card(self):
        parsed = au.parse_search_card_object(_card_obj("6910126"))
        assert parsed == {
            "id": "6910126",
            "make": "Tesla",
            "model": "Model S",
            "modelVariant": "P90D (Free Supercharging)",
            "year": 2017,
            "mileageKm": 125250,
            "numberOfDoors": 5,
            "bodyType": "Hatchback",
            "price": 24000,
            "priceCurrency": "CHF",
            "priceRatingLabel": "Guter Preis",
            "savingsVsMarketChf": 2200,
            "priceChangePercent": -36,
            "estimatedMarketPriceChf": 26217,
            "daysOnMarket": 45,
            "addressLocality": "Scuol",
            "addressRegion": "Graubünden",
            "postalCode": "7546",
            "imageUrl": "https://images.autouncle.com/ch/car_images/aaa_x.webp",
            "imageUrls": [
                "https://images.autouncle.com/ch/car_images/aaa_x.webp",
                "https://images.autouncle.com/ch/car_images/bbb_x.webp",
            ],
            "imageCaption": "Gebraucht Tesla Model S 567 kW (772 PS) 2017 Kleinwagen",
            "sourcePlatform": "Autoscout24",
            "sourcePath": "/de-ch/das_wiedersehen/autoscout24-ch/6910126/9222511",
        }

    def test_missing_optional_fields_default_to_none(self):
        parsed = au.parse_search_card_object({"carId": "123"})
        assert parsed["id"] == "123"
        assert parsed["modelVariant"] is None
        assert parsed["priceRatingLabel"] is None
        assert parsed["addressLocality"] is None
        assert parsed["imageUrl"] is None
        assert parsed["imageUrls"] is None

    def test_title_without_separator_yields_no_rating_label(self):
        parsed = au.parse_search_card_object(_card_obj("1", title="Just a plain title, no rating"))
        assert parsed["priceRatingLabel"] is None

    def test_unparseable_location_yields_no_address_fields(self):
        parsed = au.parse_search_card_object(_card_obj("1", location="not a real location string"))
        assert parsed["addressLocality"] is None
        assert parsed["addressRegion"] is None
        assert parsed["postalCode"] is None


class TestExtractSearchCardSupplements:
    def test_extracts_single_card_keyed_by_id(self):
        rsc_text = _rsc_chunk(_card_obj("6910126"))
        supplements = au.extract_search_card_supplements(rsc_text)
        assert set(supplements) == {"6910126"}
        assert supplements["6910126"]["modelVariant"] == "P90D (Free Supercharging)"

    def test_extracts_multiple_cards_with_surrounding_noise(self):
        rsc_text = (
            '1:"$Sreact.fragment"\n'
            + _rsc_chunk(_card_obj("111", subtitle="Trim A"))
            + '2:["$","div",null,{"className":"_x","children":[false,true]}]\n'
            + _rsc_chunk(_card_obj("222", subtitle="Trim B"))
        )
        supplements = au.extract_search_card_supplements(rsc_text)
        assert set(supplements) == {"111", "222"}
        assert supplements["111"]["modelVariant"] == "Trim A"
        assert supplements["222"]["modelVariant"] == "Trim B"

    def test_carid_key_with_no_enclosing_object_is_skipped(self):
        # Not real RSC shape - just "carId" text with no JSON object around
        # it at all, exercising _json_object_containing()'s not-found path.
        assert au.extract_search_card_supplements('some text "carId":"999" more text') == {}

    def test_no_cards_present_returns_empty_dict(self):
        assert au.extract_search_card_supplements(load_fixture("rsc_vw_golf_mp5000_page1.txt")) == {}


class TestFindMatchingBrace:
    def test_finds_closing_brace_ignoring_braces_inside_strings(self):
        text = '{"a": "value with { and } inside"}'
        assert au._find_matching_brace(text, 0) == len(text) - 1

    def test_handles_escaped_quote_inside_string(self):
        text = r'{"title": "a \" escaped quote", "carId": "1"}'
        assert au._find_matching_brace(text, 0) == len(text) - 1

    def test_returns_none_when_never_closed(self):
        assert au._find_matching_brace('{"a": "no closing brace"', 0) is None


class TestJsonObjectContaining:
    def test_retries_past_a_nested_object_that_closes_before_carid(self):
        # The nearest '{' before the "carId" key belongs to the *nested*
        # "nested" object, which closes before "carId" appears - this must
        # be rejected and the search must fall back to the outer object.
        text = '{"nested":{"foo":"bar"},"carId":"123"}'
        key_index = text.index('"carId"')
        obj = au._json_object_containing(text, key_index)
        assert obj == {"nested": {"foo": "bar"}, "carId": "123"}

    def test_invalid_json_candidate_is_skipped(self):
        text = '{"carId":"1",}'  # trailing comma: braces balance, JSON doesn't parse
        key_index = text.index('"carId"')
        assert au._json_object_containing(text, key_index) is None


class TestBuildCarSearchInput:
    def test_minimal_input(self):
        result = au.build_car_search_input("VW", "Golf")
        assert result == {
            "brand": "VW",
            "carModel": "Golf",
            "brandsModels": [{"brand": "VW", "modelName": "Golf", "equipmentVariants": None}],
        }

    def test_all_filters_mapped_to_confirmed_field_names(self):
        result = au.build_car_search_input(
            "VW",
            "Golf",
            price_from=1000,
            price_to=5000,
            mileage_from=1000,
            mileage_to=150000,
            year_from=2015,
            year_to=2020,
        )
        assert result["minPrice"] == 1000
        assert result["maxPrice"] == 5000
        assert result["minKm"] == 1000
        assert result["maxKm"] == 150000
        assert result["minYear"] == 2015
        assert result["maxYear"] == 2020

    def test_new_named_filters_mapped_to_confirmed_field_names(self):
        result = au.build_car_search_input(
            "VW",
            "Golf",
            body_types=["SUV", "Coupe"],
            fuel_types=["Diesel"],
            colors=["Black", "White"],
            doors=5,
            seller_kind="Dealer",
            one_owner=True,
            equipment=["hasGps", "has_4wd"],
        )
        assert result["bodyTypes"] == ["SUV", "Coupe"]
        assert result["fuelTypes"] == ["Diesel"]
        assert result["colors"] == ["Black", "White"]
        assert result["doors"] == 5
        assert result["sellerKind"] == "Dealer"
        assert result["isOneOwner"] is True
        assert result["hasGps"] is True
        assert result["has_4wd"] is True

    def test_extra_filters_merged_in_as_is(self):
        result = au.build_car_search_input("VW", "Golf", extra_filters={"euroEmissionClass": 6, "notLeasing": False})
        assert result["euroEmissionClass"] == 6
        assert result["notLeasing"] is False

    def test_invalid_body_type_raises(self):
        with pytest.raises(ValueError, match="body_types"):
            au.build_car_search_input("VW", "Golf", body_types=["NotARealBodyType"])

    def test_invalid_fuel_type_raises(self):
        with pytest.raises(ValueError, match="fuel_types"):
            au.build_car_search_input("VW", "Golf", fuel_types=["NotARealFuel"])

    def test_invalid_color_raises(self):
        with pytest.raises(ValueError, match="colors"):
            au.build_car_search_input("VW", "Golf", colors=["Chartreuse"])

    def test_invalid_seller_kind_raises(self):
        with pytest.raises(ValueError, match="seller_kind"):
            au.build_car_search_input("VW", "Golf", seller_kind="Robot")

    def test_invalid_equipment_flag_raises(self):
        with pytest.raises(ValueError, match="equipment"):
            au.build_car_search_input("VW", "Golf", equipment=["hasFlyingCarMode"])


class TestCountCars:
    @responses.activate
    def test_returns_count_from_graphql_response(self):
        responses.add(
            responses.POST,
            "https://www.autouncle.ch/graphql",
            json={"data": {"numberOfCars": 903}},
            status=200,
        )
        car_search = au.build_car_search_input("VW", "Golf VIII", price_to=30000, year_from=2022)
        session = au.make_session()
        count = au.count_cars(car_search, domain_cfg=DOMAIN_CFG, session=session)
        assert count == 903

    @responses.activate
    def test_raises_on_graphql_errors(self):
        import pytest

        responses.add(
            responses.POST,
            "https://www.autouncle.ch/graphql",
            json={"errors": [{"message": "boom"}]},
            status=200,
        )
        session = au.make_session()
        with pytest.raises(ValueError, match="GraphQL request failed"):
            au.count_cars({"brand": "VW", "carModel": "Golf"}, domain_cfg=DOMAIN_CFG, session=session)


class TestSearchListingsFiltered:
    @responses.activate
    def test_single_page_result(self, no_sleep):
        rsc_text = load_fixture("rsc_vw_golf_mp5000_page1.txt")
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
            body=rsc_text,
            status=200,
        )
        session = au.make_session()
        car_search = au.build_car_search_input("VW", "Golf", price_to=5000)
        listings = au.search_listings_filtered("VW", "Golf", DOMAIN_CFG, car_search, session=session)
        assert len(listings) == 5
        assert all(set(item.keys()) == {"id"} for item in listings)

    @responses.activate
    def test_paginates_across_two_real_pages_no_duplicates(self, no_sleep):
        page1 = load_fixture("rsc_vw_golf8_mp30000_minyear2022_page1.txt")
        page2 = load_fixture("rsc_vw_golf8_mp30000_minyear2022_page2.txt")
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII?s%5Bmax_price%5D=30000&s%5Bmin_year%5D=2022",
            body=page1,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?page=2&s%5Bmax_price%5D=30000&s%5Bmin_year%5D=2022",
            body=page2,
            status=200,
        )
        # The real 903-result set has many more pages than this test cares
        # about; page 3 here simulates the natural end of results (both
        # fixtures' own pagination says lastPage=false, matching reality).
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?page=3&s%5Bmax_price%5D=30000&s%5Bmin_year%5D=2022",
            body='"resultsInfo":"Zeige 0 - 0 von 0 Resultate"',
            status=200,
        )
        session = au.make_session()
        car_search = au.build_car_search_input("VW", "Golf VIII", price_to=30000, year_from=2022)
        listings = au.search_listings_filtered("VW", "Golf VIII", DOMAIN_CFG, car_search, session=session)
        assert len(listings) == 50
        ids = [item["id"] for item in listings]
        assert len(ids) == len(set(ids))

    @responses.activate
    def test_query_only_combo(self, no_sleep):
        rsc_text = load_fixture("rsc_vw_golf8_multi_query_only.txt")
        page1_url = (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?s%5Bmax_km%5D=50000&s%5Bmax_year%5D=2024&s%5Bmin_km%5D=1000&s%5Bmin_price%5D=15000"
        )
        page2_url = (
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf%20VIII"
            "?page=2&s%5Bmax_km%5D=50000&s%5Bmax_year%5D=2024&s%5Bmin_km%5D=1000&s%5Bmin_price%5D=15000"
        )
        responses.add(responses.GET, page1_url, body=rsc_text, status=200)
        # The fixture's own pagination says lastPage=false (real site had 505
        # results across many pages); this test only cares about a single
        # page's worth, so page 2 here simulates the natural end of results.
        responses.add(responses.GET, page2_url, body='"resultsInfo":"Zeige 0 - 0 von 0 Resultate"', status=200)
        session = au.make_session()
        car_search = au.build_car_search_input(
            "VW", "Golf VIII", price_from=15000, mileage_from=1000, mileage_to=50000, year_to=2024
        )
        listings = au.search_listings_filtered("VW", "Golf VIII", DOMAIN_CFG, car_search, session=session)
        assert len(listings) == 25

    @responses.activate
    def test_follows_redirect_to_canonical_slug(self, no_sleep):
        """AutoUncle canonicalizes some single-value filters (confirmed for
        max price) into an SEO slug and redirects our plain query-param
        request there via an embedded NEXT_REDIRECT marker - confirmed this
        also works end-to-end against the live site (see the module
        docstring)."""
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf?s%5Bmax_price%5D=5000",
            body='6:E{"digest":"NEXT_REDIRECT;replace;/de-ch/gebrauchtwagen/VW/Golf/mp-unter-5000-chf;308;"}',
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf/mp-unter-5000-chf",
            body=load_fixture("rsc_vw_golf_mp5000_page1.txt"),
            status=200,
        )
        session = au.make_session()
        car_search = au.build_car_search_input("VW", "Golf", price_to=5000)
        listings = au.search_listings_filtered("VW", "Golf", DOMAIN_CFG, car_search, session=session)
        assert len(listings) == 5
