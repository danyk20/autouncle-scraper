"""Tests for save_csv(), save_json(), and ScrapeResult."""

from __future__ import annotations

import csv
import json

import autouncle_scraper as au


class TestSaveCsv:
    def test_writes_header_and_rows(self, tmp_path):
        rows = [{"id": "1", "make": "VW", "price": 5000}, {"id": "2", "make": "Audi", "price": 8000}]
        path = tmp_path / "out.csv"
        au.save_csv(rows, str(path))

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            written = list(reader)

        assert fieldnames == ["id", "make", "price"]
        assert written[0]["make"] == "VW"
        assert written[1]["make"] == "Audi"

    def test_heterogeneous_rows_missing_values_become_empty_string(self, tmp_path):
        rows = [{"id": "1", "make": "VW"}, {"id": "2", "model": "Golf"}]
        path = tmp_path / "out.csv"
        au.save_csv(rows, str(path))

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            written = list(reader)

        assert written[0]["model"] == ""
        assert written[1]["make"] == ""

    def test_empty_rows_writes_nothing_and_warns(self, tmp_path, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="autouncle_scraper")
        path = tmp_path / "out.csv"
        au.save_csv([], str(path))
        assert not path.exists()
        assert "no rows to write" in caplog.text

    def test_unicode_roundtrips(self, tmp_path):
        rows = [{"id": "1", "addressLocality": "Säriswil"}]
        path = tmp_path / "out.csv"
        au.save_csv(rows, str(path))

        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            written = list(reader)
        assert written[0]["addressLocality"] == "Säriswil"


class TestSaveJson:
    def test_writes_pretty_printed_array(self, tmp_path):
        rows = [{"id": "1", "make": "VW"}]
        path = tmp_path / "out.json"
        au.save_json(rows, str(path))

        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded == rows

    def test_unicode_not_escaped(self, tmp_path):
        rows = [{"addressLocality": "Säriswil"}]
        path = tmp_path / "out.json"
        au.save_json(rows, str(path))

        with open(path, encoding="utf-8") as f:
            raw = f.read()
        assert "Säriswil" in raw

    def test_empty_list_writes_empty_array(self, tmp_path):
        path = tmp_path / "out.json"
        au.save_json([], str(path))
        with open(path, encoding="utf-8") as f:
            assert json.load(f) == []


class TestScrapeResult:
    def test_to_csv_writes_rows(self, tmp_path):
        result = au.ScrapeResult(
            make="VW",
            model="Golf",
            domain="ch",
            filtered=False,
            total_reported=17,
            listings=[{"id": "1", "price": 5000}],
            rows=[{"id": "1", "price": 5000}],
        )
        path = tmp_path / "out.csv"
        result.to_csv(str(path))
        with open(path, encoding="utf-8") as f:
            assert "price" in f.read()

    def test_to_json_writes_listings_not_rows(self, tmp_path):
        result = au.ScrapeResult(
            make="VW",
            model="Golf",
            domain="ch",
            filtered=False,
            total_reported=17,
            listings=[{"id": "1", "priceHistory": [{"date": "x", "price": 1}]}],
            rows=[{"id": "1", "priceHistory": "x=1"}],
        )
        path = tmp_path / "out.json"
        result.to_json(str(path))
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        # .to_json() writes the raw (unflattened) listings, not the flat rows.
        assert loaded[0]["priceHistory"] == [{"date": "x", "price": 1}]

    def test_defaults_are_empty_lists(self):
        result = au.ScrapeResult(make="VW", model="Golf", domain="ch", filtered=False, total_reported=None)
        assert result.listings == []
        assert result.rows == []
