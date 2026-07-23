# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-23

### Added

- Explicit two-level scraping model: level 1 (`search_listings()` /
  `search_listings_filtered()`) finds matching listings without opening a
  single ad; level 2 (`visit_all_listings()` / `fetch_detail()`) visits
  each one for the full record. Both levels are documented and usable on
  their own, not just through `scrape()`.
- `max_results` / `--max-results`: keep only the N most recently posted
  matching listings. Requires `detail=True` (recency is only knowable from
  each listing's own price-history data, not from level-1 summary fields).
- `firstSeenAt` / `lastUpdatedAt` fields, derived from the earliest/latest
  dates in each listing's price history.
- Full server-side filter expansion: `body_types`, `fuel_types`, `colors`,
  `doors`, `seller_kind`, `one_owner`, `equipment` (with matching CLI
  flags), plus an `extra_filters` dict for any other confirmed
  `CarSearchInput` field without its own flag. All choice-based filters are
  validated up front against known-good option lists, with a clean error
  listing valid values on a bad one.

### Changed

- `build_filtered_search_url()` / `search_listings_filtered()` now take a
  single `CarSearchInput`-shaped dict instead of individual price/mileage/
  year keyword arguments, to accommodate the much larger filter set.
- AutoUncle's "pretty URL" redirect for common filter combinations (e.g.
  max-price-only) is now followed generically by detecting the redirect
  marker embedded in the RSC response, replacing a smaller set of
  hand-coded slug rules.

### Fixed

- A single sold/removed listing (HTTP 404/410) between the search and
  detail phase no longer aborts the whole scrape - it's skipped with a
  warning, and every other error still propagates as before.

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
