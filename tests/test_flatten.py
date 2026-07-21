"""Tests for flatten_listing(), _scalarize(), and order_fieldnames()."""

from __future__ import annotations

import autouncle_scraper as au


class TestScalarize:
    def test_none_becomes_empty_string(self):
        assert au._scalarize(None) == ""

    def test_scalars_pass_through(self):
        assert au._scalarize("VW") == "VW"
        assert au._scalarize(5000) == 5000
        assert au._scalarize(6.4) == 6.4
        assert au._scalarize(True) is True

    def test_dict_with_name_key_uses_name(self):
        assert au._scalarize({"name": "Fairer Preis", "value": 1}) == "Fairer Preis"

    def test_dict_without_known_key_falls_back_to_json(self):
        result = au._scalarize({"foo": "bar", "baz": 1})
        assert result == '{"baz": 1, "foo": "bar"}'

    def test_list_joins_with_semicolons(self):
        assert au._scalarize(["a", "b", "c"]) == "a; b; c"

    def test_nested_list_of_dicts(self):
        result = au._scalarize([{"name": "x"}, {"name": "y"}])
        assert result == "x; y"

    def test_unrecognized_type_falls_back_to_str(self):
        class Weird:
            def __str__(self):
                return "weird-value"

        assert au._scalarize(Weird()) == "weird-value"


class TestFlattenListing:
    def test_plain_scalars_pass_through(self):
        flat = au.flatten_listing({"id": "123", "price": 5000, "make": "VW"})
        assert flat == {"id": "123", "price": 5000, "make": "VW"}

    def test_price_history_flattens_to_date_equals_price(self):
        item = {
            "id": "1",
            "priceHistory": [
                {"date": "2026-07-04T11:51:00+02:00", "price": 5500, "currency": "CHF", "description": "d"},
                {"date": "2026-06-09T07:11:58+02:00", "price": 5900, "currency": "CHF", "description": "d2"},
            ],
        }
        flat = au.flatten_listing(item)
        assert flat["priceHistory"] == "2026-07-04T11:51:00+02:00=5500; 2026-06-09T07:11:58+02:00=5900"

    def test_empty_price_history_flattens_to_empty_string(self):
        flat = au.flatten_listing({"id": "1", "priceHistory": []})
        assert flat["priceHistory"] == ""

    def test_other_properties_flattens_to_name_equals_value(self):
        item = {
            "id": "1",
            "otherProperties": [
                {"name": "Some Label", "value": "whatever"},
                {"name": "Other Label", "value": 42},
            ],
        }
        flat = au.flatten_listing(item)
        assert flat["otherProperties"] == "Some Label=whatever; Other Label=42"

    def test_image_urls_flattens_to_semicolon_joined(self):
        item = {"id": "1", "imageUrls": ["https://x/a.jpg", "https://x/b.jpg"]}
        flat = au.flatten_listing(item)
        assert flat["imageUrls"] == "https://x/a.jpg; https://x/b.jpg"

    def test_empty_image_urls_flattens_to_empty_string(self):
        flat = au.flatten_listing({"id": "1", "imageUrls": []})
        assert flat["imageUrls"] == ""

    def test_equipment_dict_becomes_parent_child_columns(self):
        item = {"id": "1", "equipment": {"Klimaanlage": "Ja", "Türen": "2"}}
        flat = au.flatten_listing(item)
        assert flat["equipment_Klimaanlage"] == "Ja"
        assert flat["equipment_Türen"] == "2"
        assert "equipment" not in flat

    def test_generic_nested_dict_becomes_parent_child_columns(self):
        # Any other nested dict (not one of the special-cased keys above)
        # falls back to the same "parent_child" convention as the
        # reference project uses for e.g. "seller"/"make"/"model".
        item = {"id": "1", "vehicleEngine": {"power": 160, "unit": "PS"}}
        flat = au.flatten_listing(item)
        assert flat["vehicleEngine_power"] == 160
        assert flat["vehicleEngine_unit"] == "PS"

    def test_none_values_become_empty_string(self):
        flat = au.flatten_listing({"id": "1", "dealerName": None, "vin": None})
        assert flat["dealerName"] == ""
        assert flat["vin"] == ""

    def test_full_realistic_detail_record(self):
        item = {
            "id": "6690428",
            "make": "VW",
            "model": "Golf",
            "price": 5500,
            "priceHistory": [{"date": "2026-07-04T11:51:00+02:00", "price": 5500}],
            "imageUrls": ["https://images.autouncle.com/a.jpg"],
            "equipment": {"Klimaanlage": "Ja"},
            "otherProperties": [{"name": "X", "value": "Y"}],
            "dealerName": None,
        }
        flat = au.flatten_listing(item)
        assert flat["id"] == "6690428"
        assert flat["priceHistory"] == "2026-07-04T11:51:00+02:00=5500"
        assert flat["imageUrls"] == "https://images.autouncle.com/a.jpg"
        assert flat["equipment_Klimaanlage"] == "Ja"
        assert flat["otherProperties"] == "X=Y"
        assert flat["dealerName"] == ""


class TestOrderFieldnames:
    def test_priority_fields_come_first_then_alphabetical(self):
        keys = {"zebra", "id", "apple", "price", "make"}
        ordered = au.order_fieldnames(keys)
        assert ordered == ["id", "make", "price", "apple", "zebra"]

    def test_only_present_priority_fields_included(self):
        keys = {"id", "custom_field"}
        ordered = au.order_fieldnames(keys)
        assert ordered == ["id", "custom_field"]

    def test_no_priority_fields_present(self):
        keys = {"foo", "bar"}
        assert au.order_fieldnames(keys) == ["bar", "foo"]
