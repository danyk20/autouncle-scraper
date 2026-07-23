# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.1] - 2026-07-23

### Fixed

- Level-1-only fields (`modelVariant` - e.g. the "85D"/"P90D (Free
  Supercharging)" trim spec - plus `priceChangePercent`,
  `estimatedMarketPriceChf`, `sourcePath`) were silently lost once a
  listing went through the detail phase, since AutoUncle's detail page
  never renders them and `scrape()` previously discarded the search-phase
  record entirely in favor of a fresh detail-only one. `scrape()` now
  merges the level-1 record's fields back into each detail record
  afterward (fill gaps only - a field the detail page's own data already
  set, e.g. `price` or a more precise `addressLocality`, always wins), so
  `detail=True` (the default) never loses anything `detail=False` would
  have shown.

## [0.4.0] - 2026-07-23

### Added

- Level-1 (search) scraping now captures nearly everything visible on
  AutoUncle's search-result page itself, without opening a single ad -
  closing a gap where fields like the model/trim line (e.g. "P90D (Free
  Supercharging)") were silently missing: `modelVariant`, `priceRatingLabel`,
  `savingsVsMarketChf`, `estimatedMarketPriceChf`, `priceChangePercent`,
  `daysOnMarket`, `sourcePlatform`/`sourcePath`, full `addressLocality`/
  `addressRegion`/`postalCode`, and the full `imageUrls` gallery. Sourced
  from the same RSC ("Flight") response AutoUncle's own frontend renders
  search-result cards from (`extract_search_card_supplements()`,
  `parse_search_card_object()`), which turns out to carry a much richer
  per-listing JSON object than previously used.
- Filtered searches (`price_to`/`year_from`/etc.) now return real summary
  fields even with `detail=False` - previously they returned bare
  `{"id": ...}` rows, since the RSC mechanism was thought to carry no
  per-listing data at all; it does, just not schema.org-shaped. Fuel type,
  transmission, engine power, and CO2/consumption figures still require a
  detail visit, since AutoUncle's search cards don't render those either
  way.
- Unfiltered searches make one extra request per page (fetching the same
  page's RSC response) to pick up the fields above; JSON-LD stays
  authoritative for everything it already provides - the RSC data only
  fills gaps, never overwrites.

### Fixed

- RSC/Flight responses (`Content-Type: text/x-component`, no charset) were
  being decoded as ISO-8859-1 instead of UTF-8 - `requests`' RFC-default
  fallback for undeclared charsets - silently mangling non-ASCII text (e.g.
  `"Graubünden"` -> `"GraubÃ¼nden"`). AutoUncle's responses are UTF-8
  regardless of what they declare; `request_with_retries()` now corrects
  the encoding whenever a response doesn't declare a charset.

## [0.3.0] - 2026-07-23

### Changed

- **`max_results`/`--max-results` now caps the search *before* opening any
  listing, not after.** Previously it visited the detail page of every
  matching listing (to sort by `firstSeenAt`) and only trimmed the
  returned set afterwards - correct, but defeated the purpose of asking
  for a small `max_results` on a search with many matches. Now only the
  first `max_results` ids from the search phase are ever opened (or
  returned, with `detail=False`) and the rest are never fetched at all,
  which is what actually makes a large search fast. The trade-off: this is
  "the first N listings AutoUncle's own search returns", not a guaranteed
  "the N most recently posted" - AutoUncle's default result order isn't a
  date sort. `max_results` no longer requires `detail=True`.

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
