"""Tests for build_arg_parser(), main(), and run_cli()."""

from __future__ import annotations

import json
import logging
import re

import pytest
import requests
import responses

import autouncle_scraper as au
from tests.conftest import load_fixture


def _single_page_search_fixture() -> str:
    """The page2 fixture (9 items) has numberOfItems=34 embedded, since it's
    really page 2 of a larger real set - patch that down to 9 so it reads
    as a complete, single-page result when served as page 1."""
    html = load_fixture("search_vw_golf_alltrack_page2.html")
    return html.replace('"numberOfItems":34', '"numberOfItems":9', 1)


@pytest.fixture
def unfiltered_scrape_mocks(search_form_config):
    """Mocks a full unfiltered scrape() run: config, one search page, one detail visit."""
    responses.add(
        responses.GET,
        "https://www.autouncle.ch/api/v4/car_search_form/config",
        json=search_form_config,
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
        body=_single_page_search_fixture(),  # 9 results, single page
        status=200,
    )
    # The fixture's 9 listings each have a distinct id; a regex matcher
    # covers all of them with the same (unrelated but structurally valid)
    # detail fixture, since these tests only care about the CLI plumbing.
    responses.add(
        responses.GET,
        re.compile(r"https://www\.autouncle\.ch/de-ch/d/\d+$"),
        body=load_fixture("detail_6690428.html"),
        status=200,
    )


class TestBuildArgParser:
    def test_requires_make_and_model(self):
        parser = au.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_parses_all_flags(self):
        parser = au.build_arg_parser()
        args = parser.parse_args(
            [
                "--make",
                "VW",
                "--model",
                "Golf",
                "--domain",
                "ch",
                "--category",
                "car",
                "--out",
                "myfile",
                "--no-detail",
                "--delay",
                "1.5",
                "--price-from",
                "1000",
                "--price-to",
                "5000",
                "--mileage-from",
                "0",
                "--mileage-to",
                "100000",
                "--year-from",
                "2015",
                "--year-to",
                "2020",
                "-v",
            ]
        )
        assert args.make == "VW"
        assert args.model == "Golf"
        assert args.no_detail is True
        assert args.delay == 1.5
        assert args.price_from == 1000
        assert args.price_to == 5000
        assert args.verbose is True

    def test_verbose_and_quiet_are_mutually_exclusive(self):
        parser = au.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--make", "VW", "--model", "Golf", "-v", "-q"])


class TestMain:
    @responses.activate
    def test_writes_default_named_csv_and_json(self, unfiltered_scrape_mocks, no_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        exit_code = au.main(["--make", "VW", "--model", "Golf"])
        assert exit_code == 0
        assert (tmp_path / "vw_golf.csv").exists()
        assert (tmp_path / "vw_golf.json").exists()

    @responses.activate
    def test_custom_out_basename(self, unfiltered_scrape_mocks, no_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        exit_code = au.main(["--make", "VW", "--model", "Golf", "--out", "custom"])
        assert exit_code == 0
        assert (tmp_path / "custom.csv").exists()
        assert (tmp_path / "custom.json").exists()

    @responses.activate
    def test_no_detail_skips_detail_visits(self, search_form_config, no_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            json=search_form_config,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/de-ch/gebrauchtwagen/VW/Golf",
            body=_single_page_search_fixture(),
            status=200,
        )
        exit_code = au.main(["--make", "VW", "--model", "Golf", "--no-detail"])
        assert exit_code == 0
        with open(tmp_path / "vw_golf.json", encoding="utf-8") as f:
            listings = json.load(f)
        assert len(listings) == 9
        # No detail visit happened, so no priceHistory field is present.
        assert "priceHistory" not in listings[0]

    @responses.activate
    def test_version_flag_exits_zero(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            au.main(["--version"])
        assert excinfo.value.code == 0
        assert au.__version__ in capsys.readouterr().out


class TestConfigureCliLogging:
    def test_verbose_sets_debug_level(self):
        au._configure_cli_logging(verbose=True, quiet=False)
        assert au.logger.level == logging.DEBUG
        au.logger.handlers.clear()

    def test_quiet_sets_warning_level(self):
        au._configure_cli_logging(verbose=False, quiet=True)
        assert au.logger.level == logging.WARNING
        au.logger.handlers.clear()

    def test_default_sets_info_level(self):
        au._configure_cli_logging(verbose=False, quiet=False)
        assert au.logger.level == logging.INFO
        au.logger.handlers.clear()


class TestRunCli:
    @responses.activate
    def test_success_returns_zero(self, unfiltered_scrape_mocks, no_sleep, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert au.run_cli(["--make", "VW", "--model", "Golf"]) == 0

    @responses.activate
    def test_value_error_returns_one(self, search_form_config):
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            json=search_form_config,
            status=200,
        )
        assert au.run_cli(["--make", "NotARealBrandXYZ", "--model", "Golf"]) == 1

    @responses.activate
    def test_value_error_from_bad_range_returns_one(self):
        assert au.run_cli(["--make", "VW", "--model", "Golf", "--price-from", "5000", "--price-to", "1000"]) == 1

    @responses.activate
    def test_network_error_returns_one(self, no_sleep):
        responses.add(
            responses.GET,
            "https://www.autouncle.ch/api/v4/car_search_form/config",
            body=requests.ConnectionError("boom"),
        )
        assert au.run_cli(["--make", "VW", "--model", "Golf", "--delay", "0"]) == 1


class TestMainEntryPoint:
    def test_dunder_main_guard_calls_run_cli(self):
        # __main__ guard (`if __name__ == "__main__": sys.exit(run_cli())`)
        # is exercised via subprocess in test_e2e.py, not here - this just
        # confirms run_cli is what it's wired to.
        import inspect

        source = inspect.getsource(au)
        assert "sys.exit(run_cli())" in source
