# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-21

Initial release.

### Added

- Scraper for autouncle.ch used-car listings, usable both as a CLI
  (`autouncle-scraper` / `python autouncle_scraper.py`) and as a library
  (`from autouncle_scraper import scrape`) with the same call shape as the
  [AutoScout24 scraper](https://github.com/danyk20/autoscout24-scraper) this
  project is a drop-in-compatible sibling of.
- Search by any brand/model (resolved dynamically against AutoUncle's own
  `/api/v4/car_search_form/config` endpoint, not a hardcoded list), with
  optional price/mileage/first-registration-year range filters.
- Two independent search mechanisms, chosen automatically: schema.org
  JSON-LD pagination for unfiltered searches (the richest, most stable
  source), and a reverse-engineered GraphQL + Next.js RSC mechanism for
  filtered searches (AutoUncle suppresses JSON-LD on any filtered page).
- Full-detail mode (default): visits every matching listing individually to
  extract every field its detail page exposes - full address, AutoUncle's
  own price-rating/market-analysis labels, a complete price-history time
  series (a field AutoScout24 doesn't expose at all), full image gallery,
  and an equipment/spec breakdown - generically flattened for CSV output.
  `--no-detail`/`detail=False` for a faster, summary-only pass on
  unfiltered searches.
- Every listing's raw JSON and flattened CSV row both carry a direct `url`
  back to the original ad.
- `domain` parameter (default `"ch"`) so the API host/locale/URL segment
  aren't hardcoded to Switzerland, in case another AutoUncle country site is
  wired up in the future.
- `ScrapeResult` dataclass return value (`.rows`, `.listings`, `.to_csv()`,
  `.to_json()`) for library use, with the CLI as a thin wrapper around the
  same `scrape()` function.
- Console script entry point (`autouncle-scraper`) and `pip install` support
  via `pyproject.toml` packaging metadata; `--version` flag.
- Logging-based output (`-v`/`--verbose`, `-q`/`--quiet`) instead of bare
  `print()`, so library consumers can configure/suppress it via the
  standard `logging` module.
- Full type hints throughout, checked with mypy; linted and formatted with
  Ruff.
- Unit test suite (100% coverage, all HTTP mocked, all fixtures real
  captures from the live site) plus a smaller end-to-end suite against the
  real live site, run on a separate weekly GitHub Actions schedule.
- CI (GitHub Actions) running lint, type-check, and the unit suite on every
  push/PR against Python 3.13.
- MIT license with an explicit statement welcoming AI agents/bots to use the
  project under the same terms as a human developer.
- Project governance docs: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`,
  `SECURITY.md`, issue/PR templates, `docs/REFERENCE.md`.
