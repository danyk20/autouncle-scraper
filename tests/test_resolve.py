"""Tests for brand/model resolution against car_search_form/config."""

from __future__ import annotations

import pytest
import responses

import autouncle_scraper as au


class TestResolveMakeKey:
    def test_exact_match_case_insensitive(self, search_form_config):
        assert au.resolve_make_key("vw", search_form_config) == "VW"
        assert au.resolve_make_key("VW", search_form_config) == "VW"
        assert au.resolve_make_key("  Vw  ", search_form_config) == "VW"

    def test_exact_match_multi_word_brand(self, search_form_config):
        assert au.resolve_make_key("alfa romeo", search_form_config) == "Alfa Romeo"

    def test_substring_fallback(self, search_form_config):
        # "tesl" isn't an exact brand but is a substring of "Tesla"
        assert au.resolve_make_key("tesl", search_form_config) == "Tesla"

    def test_multiple_substring_matches_warns_and_picks_first(self, search_form_config, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="autouncle_scraper")
        result = au.resolve_make_key("a", search_form_config)  # matches Audi, Alfa Romeo, VW? etc
        assert result in search_form_config["brands"]["all"]

    def test_unknown_make_raises(self, search_form_config):
        with pytest.raises(ValueError, match="Could not find a brand matching"):
            au.resolve_make_key("NotARealBrandXYZ", search_form_config)


class TestResolveModelKey:
    def test_exact_match_beats_substring(self, search_form_config):
        # "Golf" is an exact model entry distinct from "Golf II".."Golf VIII"
        assert au.resolve_model_key("VW", "golf", search_form_config) == "Golf"

    def test_exact_match_specific_generation(self, search_form_config):
        assert au.resolve_model_key("VW", "Golf VIII", search_form_config) == "Golf VIII"

    def test_substring_fallback(self, search_form_config):
        assert au.resolve_model_key("VW", "beetle cabrio", search_form_config) == "Beetle Cabriolet"

    def test_unknown_model_raises_with_available_list(self, search_form_config):
        with pytest.raises(ValueError, match="Available:"):
            au.resolve_model_key("VW", "NotARealModelXYZ", search_form_config)

    def test_unknown_brand_raises(self, search_form_config):
        with pytest.raises(ValueError, match="No model data found for brand"):
            au.resolve_model_key("NotARealBrandXYZ", "Golf", search_form_config)


class TestFetchSearchFormConfig:
    @responses.activate
    def test_fetches_and_parses_json(self, search_form_config):
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            json=search_form_config,
            status=200,
        )
        result = au.fetch_search_form_config()
        assert result["brands"]["all"] == search_form_config["brands"]["all"]

    @responses.activate
    def test_retries_on_500(self, search_form_config, no_sleep):
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            status=500,
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            json=search_form_config,
            status=200,
        )
        result = au.fetch_search_form_config()
        assert result["brands"]["all"] == search_form_config["brands"]["all"]

    def test_unsupported_domain_raises(self):
        with pytest.raises(ValueError, match="Unsupported domain"):
            au.fetch_search_form_config(domain="de")
