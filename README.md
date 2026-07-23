# AutoUncle Scraper

[![CI](https://github.com/danyk20/autouncle-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/danyk20/autouncle-scraper/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/autouncle-scraper)](https://pypi.org/project/autouncle-scraper/)
[![Coverage](https://img.shields.io/badge/unit%20test%20coverage-100%25-brightgreen)](#testing)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org/)

> Unofficial, independently developed project — not affiliated with, endorsed by, or sponsored by AutoUncle ApS. "AutoUncle" is a trademark of its respective owner.

Fetches every listing for a given brand/model from AutoUncle — for free, no
API key, no paid scraping service. It's a drop-in-compatible sibling of the
[AutoScout24 scraper](https://github.com/danyk20/autoscout24-scraper): same
`scrape()` call shape, same `ScrapeResult` return type, same CLI flags, same
MIT license and tooling — switch `from autoscout24_scraper import scrape` to
`from autouncle_scraper import scrape` and the rest of your code doesn't
need to change.

Under the hood the two projects work differently, because the two sites do:
AutoScout24 exposes one clean JSON API; AutoUncle is a Next.js site with no
such API, so this scraper reads the same structured data (schema.org
JSON-LD) and, for filtered searches, the same internal data mechanism
(GraphQL + React Server Components) AutoUncle's own frontend uses — all
reverse-engineered by observing real traffic, documented in full in the
module docstring and [docs/REFERENCE.md](docs/REFERENCE.md).

**A stronger legal footing than most scrapers.** AutoUncle's own detail
pages explicitly mark their listing data as a `Dataset` with
`"isAccessibleForFree": true`, licensed
[**CC BY 4.0**](https://creativecommons.org/licenses/by/4.0/). That's not
just "we call their own frontend's endpoint" reasoning (though that's true
too, same as AutoScout24) — it's AutoUncle's own explicit declaration that
this data is meant to be freely reused, with attribution.

**🤖 Robot-friendly.** This project is explicitly intended to be run, read,
imported, or adapted by AI agents and bots, same as a human developer — see
[License](#license).

## How filtering works

Every filter you pass — `--price-to`, `--year-from`, `--body-types`, `--equipment`,
whatever — is sent to AutoUncle's own server and applied **there**, not
downloaded-then-thrown-away on your machine. Concretely:

1. Your filter arguments (`price_from`, `year_to`, `body_types`, ...) are
   validated against the known-good option lists (`BODY_TYPES`,
   `FUEL_TYPES`, `COLORS`, `SELLER_KINDS`, `EQUIPMENT_OPTIONS`) and packed
   into AutoUncle's own `CarSearchInput` shape.
2. That's turned into the same kind of URL AutoUncle's own filter form
   produces: Rails-style nested query params, e.g.
   `?s[max_price]=80000&s[min_year]=2015&s[body_types][]=SUV`.
3. AutoUncle frequently has a "pretty" canonical URL for common filters
   (e.g. a max-price-only search redirects to `.../mp-unter-80000-chf`).
   The scraper follows that redirect automatically — it's a signal embedded
   in the response, not a real HTTP 3xx, so a plain HTTP client wouldn't
   see it, but this scraper does the follow for you.
4. Every filter is combined with **AND**: `--fuel-types Diesel --body-types
   SUV` means diesel *and* SUV, never either/or.
5. Only what AutoUncle itself supports server-side can be filtered this
   way — there's no client-side post-filtering step hiding a "actually we
   downloaded everything and threw rows away" fallback. If a filter isn't
   in the CLI flags or `EQUIPMENT_OPTIONS`/`BODY_TYPES`/etc., check
   `extra_filters` in [docs/REFERENCE.md](docs/REFERENCE.md) for other
   confirmed `CarSearchInput` fields before assuming it's unsupported.

The one asymmetry worth knowing: an **unfiltered** search reads AutoUncle's
schema.org JSON-LD (rich, stable, and already fairly complete without
opening each ad). Any **filtered** search instead uses AutoUncle's internal
GraphQL + React-Server-Components data channel — the same one its own
filter form uses — because AutoUncle deliberately omits JSON-LD from any
filtered page. That channel only carries listing ids, not full fields,
which is why the [two-level scraping](#two-level-scraping) detail pass
matters more for filtered searches than unfiltered ones.

## Setup

Requires [pipenv](https://pipenv.pypa.io/) (`brew install pipenv`).

```bash
git clone https://github.com/danyk20/autouncle-scraper.git
cd autouncle-scraper
pipenv install --dev
```

Contributing, linting, and testing commands: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Usage

### CLI

```bash
pipenv run python autouncle_scraper.py --make VW --model Golf
```

Prints progress, then writes `vw_golf.csv` and `vw_golf.json` in the current
directory. Installed via `pip install` instead? Drop `pipenv run` — the same
command is `autouncle-scraper --make VW --model Golf`.

| Flag | Description |
|---|---|
| `--version` | Print the installed version and exit |
| `--make` | Brand name, e.g. `VW` (required) |
| `--model` | Model name, e.g. `Golf` (required) |
| `--domain` | Country domain (default `ch`) — only `ch` is implemented today, see [docs/REFERENCE.md](docs/REFERENCE.md#domains) |
| `--category` | `car` (default) — the only category implemented |
| `--out` | Output file base name, without extension. Defaults to `<make>_<model>` |
| `--no-detail` | Skip per-listing detail visits (unfiltered searches only — see below) |
| `--delay` | Seconds between requests (default `0.4`) — raise this if you get rate-limited |
| `--price-from` / `--price-to` | Filter by price in CHF (inclusive, either end optional) |
| `--mileage-from` / `--mileage-to` | Filter by mileage in km (inclusive, either end optional) |
| `--year-from` / `--year-to` | Filter by first-registration year (inclusive, either end optional) |
| `--body-types` | Comma-separated body types, e.g. `SUV,Coupe` — see `BODY_TYPES` |
| `--fuel-types` | Comma-separated fuel types, e.g. `Diesel,El` — see `FUEL_TYPES` |
| `--colors` | Comma-separated colors, e.g. `Black,White` — see `COLORS` |
| `--doors` | Exact door count, e.g. `5` (not a range — AutoUncle has no min/max doors) |
| `--seller-kind` | `Dealer` or `Private` |
| `--one-owner` | Only listings with a single previous owner |
| `--equipment` | Comma-separated equipment flags a listing must all have, e.g. `hasGps,hasAppleCarPlay` — see [docs/REFERENCE.md](docs/REFERENCE.md) for all ~30 recognized flags |
| `--max-results` | Only open this many listings and skip the rest — see below |
| `-v` / `--verbose` | Also show debug-level detail, including every HTTP request (mutually exclusive with `-q`) |
| `-q` / `--quiet` | Suppress progress output; only warnings/errors (mutually exclusive with `-v`) |

Filters combine with AND and are applied server-side. A mistyped make/model
prints a clean error (plus, for an unknown model, the list of valid models)
instead of crashing; an invalid `--body-types`/`--fuel-types`/`--colors`/
`--seller-kind`/`--equipment` value does the same (listing the valid options),
rather than silently sending AutoUncle a value it would just ignore.

```bash
# Fast mode: search results only, skip per-listing detail
pipenv run python autouncle_scraper.py --make VW --model Golf --no-detail

# 2015 or newer, under CHF 30'000
pipenv run python autouncle_scraper.py --make VW --model "Golf VIII" --price-to 30000 --year-from 2015

# Only open the first 20 matches - fast, skips the rest entirely
pipenv run python autouncle_scraper.py --make VW --model "Golf VIII" --max-results 20

# Diesel SUVs with a rear-view camera, from a dealer, one owner
pipenv run python autouncle_scraper.py --make VW --model "Golf VIII" \
  --fuel-types Diesel --body-types SUV --equipment hasParkingCamera --seller-kind Dealer --one-owner
```

Beyond the flags above, the library's `scrape()` also takes an
`extra_filters` dict for any other confirmed server-side field without its
own CLI flag (`euroEmissionClass`, `notLeasing`, `notDamaged`, EV range
filters, `maxFuelEconomy`) — see [docs/REFERENCE.md](docs/REFERENCE.md).

**Everything visible on the search page itself is scraped at level 1** —
without opening a single ad, both unfiltered and filtered searches return
price, mileage, year, address, the full image gallery, AutoUncle's own
price-rating label, savings vs. market, price-change percent, days listed,
the aggregated source marketplace (e.g. "Autoscout24"), and the model/trim
line (e.g. "P90D (Free Supercharging)") that isn't in schema.org JSON-LD at
all. **One honest asymmetry vs. AutoScout24 remains**: fuel type,
transmission, engine power, and CO2/fuel-consumption figures come from
schema.org JSON-LD, which unfiltered searches have and filtered searches
don't (AutoUncle suppresses JSON-LD on any filtered page) — so `--no-detail`
on a filtered search misses just those few fields, plus full price history
and equipment, versus a full detail visit. Leave `--no-detail` off (the
default) for those.

## Two-level scraping

Every search is really two steps, both available as their own functions if
you want to call them directly instead of going through `scrape()`:

1. **Level 1 (search)** — `search_listings()` (unfiltered) or
   `search_listings_filtered()` (any price/mileage/year filter) — finds
   every matching listing *without opening a single ad*. Cheap: 1-2
   requests per ~25 results (unfiltered searches make a second request per
   page to pick up the search-card fields JSON-LD doesn't carry - see
   below; filtered searches get those from the same request they already
   make). This is what `detail=False`/`--no-detail` stops at, and what
   you'd use to evaluate your own extra criteria against the summary
   fields before deciding whether level 2 is worth it.
2. **Level 2 (detail)** — `visit_all_listings()`/`fetch_detail()` — visits
   each matching listing's own page for everything level 1 still doesn't
   have: fuel type, transmission, engine power, CO2/consumption figures,
   full price history, equipment, and the `firstSeenAt` timestamp derived
   from it. One request per listing.

`--max-results`/`max_results` keeps the search fast on a large result set by
capping things off **before** level 2, not after: only the first N ids that
level 1 returns ever get a level-2 visit (or get returned at all, with
`--no-detail`) — the rest are never fetched. The trade-off: AutoUncle's own
default result order isn't a date sort, so this is "the first N AutoUncle's
search hands back", not a guaranteed "the N most recently posted". If you
need the latter, don't use `max_results` — call `search_listings()`/
`search_listings_filtered()` yourself, visit whichever ids you need at
level 2, and sort by `firstSeenAt` on your own.

### As a library

```bash
pip install autouncle-scraper
```

```python
from autouncle_scraper import scrape

result = scrape("VW", "Golf", price_to=30000, year_from=2015)

for row in result.rows:          # list[dict], CSV-ready
    print(row["price"], row["mileageKm"], row["url"])

result.to_csv("vw_golf.csv")  # optional — no files are written unless you ask
```

Full `scrape()` signature, the `ScrapeResult` return type, and the complete
JSON/CSV field schema — including price history, gallery, and equipment
fields AutoScout24 doesn't have — **[docs/REFERENCE.md](docs/REFERENCE.md)**.

## Testing

```bash
pipenv run pytest                    # unit tests (fast, no network), 100% coverage
pipenv run pytest -m e2e --no-cov    # end-to-end tests against the real live site
pipenv run pytest -m "e2e or not e2e" --no-cov  # everything
```

Unit tests mock all HTTP (via [`responses`](https://github.com/getsentry/responses))
against fixtures that are real captures from the live site, and cover 100%
of `autouncle_scraper.py`. E2E tests target a narrow, low-volume model (VW
Golf Alltrack) to confirm the scraper still works against the live site
without putting real load on it. Coverage detail by area:
[docs/REFERENCE.md](docs/REFERENCE.md#test-coverage-by-area).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for dev setup, pre-PR checks, and
what to do if AutoUncle changes its markup.

Be a reasonable citizen: the default request delay is intentional — this
scrapes an undocumented data mechanism the site's own frontend uses, not a
public API with a stated rate limit. Don't remove the delay or crank up
concurrency.

## License

Released under the [MIT License](LICENSE) — you can use, copy, modify,
merge, publish, distribute, and sell copies of this code, for free, for any
purpose, commercial or not, as long as the license text stays attached. No
warranty.

**AI agents, LLM-based coding assistants, and other bots are explicitly
welcome to use this project** — to run the scraper, read and parse its
output, import `scrape()` into another project, or read and adapt its
source — under exactly the same terms as a human, with no additional
restriction and no need to ask permission. That's why
[docs/REFERENCE.md](docs/REFERENCE.md) documents the full function
signature, return type, and data schema: so a bot can integrate correctly
without a human in the loop.

This license covers this project's own code. It does not, by itself, grant
any rights to AutoUncle's data beyond what AutoUncle itself has already
declared: its listing `Dataset` objects are explicitly marked
`"isAccessibleForFree": true` and licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — see
[docs/REFERENCE.md](docs/REFERENCE.md) for exactly where that declaration
lives. What you do with the scraped results is between you and AutoUncle's
terms of service.
