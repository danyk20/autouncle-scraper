# Reference

Full API surface, return types, and data schema for anyone integrating with
this project as a library — a human developer or an AI agent — without
reading the source. See [README.md](../README.md) for the pitch, install,
and CLI usage.

## Domains

Every function and the CLI accept a `domain` (default `"ch"`), used to look
up a `DomainConfig` (`host`, `locale`, `cars_path`) in the `DOMAINS` table.

**As of this writing, `ch` is the only domain implemented.** AutoUncle runs
country sites for at least Denmark, Sweden, Germany, Italy, Spain, Austria,
Portugal, Poland, Finland, Romania, the UK, the Netherlands, and France, each
with its own locale and localized "used cars" URL segment — none of those
are wired up here. `domain` exists as a parameter (rather than hardcoding
`.ch`) so that adding one is a one-entry addition to `DOMAINS`, e.g.:

```python
DOMAINS["de"] = DomainConfig(host="www.autouncle.de", locale="de", cars_path="gebrauchtwagen")
```

...**provided** the same JSON-LD / RSC / GraphQL mechanisms this scraper
relies on (see below) actually hold for that country site — that has not
been verified for any domain other than `ch`.

## How the data is gathered

Unlike AutoScout24 (this project's sibling, which calls one clean JSON REST
API), AutoUncle is a Next.js (App Router / React Server Components) site
with no single public search API. Its data comes from three different
places depending on what you ask for — the full derivation, including what
was tried and ruled out, is in the module docstring of
`autouncle_scraper.py`; this section is the short version.

| What | Mechanism | Functions |
|---|---|---|
| Brand/model reference data | `GET /api/v4/car_search_form/config` — a public, unauthenticated REST endpoint | `fetch_search_form_config()`, `resolve_make_key()`, `resolve_model_key()` |
| Unfiltered search (no price/mileage/year filter) | schema.org JSON-LD on the canonical, paginated brand/model page | `search_listings()`, `parse_vehicle_jsonld()` |
| Filtered search (any price/mileage/year filter) | Next.js RSC ("Flight") response, fetched from a URL built from the confirmed max-price-slug + sorted `s[...]` query-param rule | `build_filtered_search_url()`, `fetch_rsc_page()`, `parse_rsc_pagination()`, `parse_rsc_listing_ids()`, `search_listings_filtered()` |
| Listing detail | JSON-LD on the detail page, plus a BeautifulSoup pass over the same page for gallery/equipment/source-listing | `fetch_detail()`, `parse_detail_jsonld()`, `extract_gallery_images()`, `extract_equipment()`, `extract_source_listing()` |
| Fast match count (optional) | AutoUncle's own GraphQL `countCars` query | `count_cars()`, `build_car_search_input()` |

**Why two different search mechanisms exist**: confirmed empirically (both
via raw HTTP fetch and full browser navigation) that AutoUncle's server only
emits the JSON-LD block on the plain, unfiltered brand/model URL (optionally
paginated with `?page=N`) — any filter, in any URL form, gets `<meta
name="robots" content="noindex, follow">` and no JSON-LD at all. So a
filtered search has to use a different data source (RSC), which in turn
carries no rich per-listing fields — only ids. That's why a filtered
`scrape(..., detail=False)` call returns rows with only an `id` field; get
everything else via the detail phase (`detail=True`, the default).

### The filtered-search URL rule

Also confirmed empirically, by monkey-patching `window.fetch` and driving
AutoUncle's real filter form one control at a time (see
[CONTRIBUTING.md](../CONTRIBUTING.md) for the technique, if this needs
re-deriving):

- **Max price** canonicalizes into an SEO path segment:
  `/{brand}/{model}/mp-unter-{price}-chf` ("mp" = max price, German
  "unter" = "under").
- **Every other filter** (min price, min/max km, min/max year) is a
  Rails-style nested query parameter: `s[min_price]`, `s[min_km]`,
  `s[max_km]`, `s[min_year]`, `s[max_year]`.
- The canonical parameter order is those query keys **sorted
  alphabetically**. A non-canonical order (or an un-canonicalized
  max-price-as-query-param form) doesn't 404 — AutoUncle 200s with a
  Next.js redirect encoded *inside* the RSC response body itself
  (`NEXT_REDIRECT;replace;<canonical-url>;308;`), not a real HTTP 3xx.
  `build_filtered_search_url()` builds the canonical form directly, so this
  redirect is never actually triggered by this scraper.

This was validated against 4 independent real captures spanning single- and
multi-filter combinations (5 to 903 total results, pages 1 and 2, with and
without a max-price slug) with zero listing-id overlap across pages in
every case, and against one full live paginated run (903/903 listings
collected across 37 pages, exactly matching a live `count_cars()` call).

### `CarSearchInput` (GraphQL) — confirmed vs. presumed fields

Confirmed live, one field at a time, by watching the exact request body
AutoUncle's own filter form sends:

| Field | Confirmed |
|---|---|
| `brand`, `carModel` | ✅ |
| `brandsModels: [{brand, modelName, equipmentVariants}]` | ✅ |
| `maxPrice`, `minPrice` | ✅ |
| `minKm`, `maxKm` | ✅ |
| `minYear`, `maxYear` | ✅ |

GraphQL introspection (`__schema`) is disabled in production, so this list
is confirmed-by-observation, not from a schema dump — `CarSearchInput` may
have additional fields (fuel type, body type, equipment, ...) that were
never exercised. `build_car_search_input()` only ever sets the confirmed
fields above.

## `scrape()` signature

```python
def scrape(
    make: str,                       # e.g. "VW" — brand name, case-insensitive, substring matching supported
    model: str,                      # e.g. "Golf" — model name, case-insensitive, substring matching supported
    *,
    domain: str = "ch",              # autouncle.<domain>; only "ch" implemented today
    category: str = "car",           # only "car" is implemented; any other value raises ValueError
    detail: bool = True,             # visit every listing individually for the full record (slower)
    price_from: int | None = None,   # CHF, inclusive
    price_to: int | None = None,     # CHF, inclusive
    mileage_from: int | None = None, # km, inclusive
    mileage_to: int | None = None,   # km, inclusive
    year_from: int | None = None,    # first-registration year, inclusive
    year_to: int | None = None,      # first-registration year, inclusive
    delay: float = 0.4,              # seconds between HTTP requests
    verbose: bool = True,            # emit progress via the "autouncle_scraper" logger at INFO level
    session: requests.Session | None = None,  # reuse a session across calls if given
) -> ScrapeResult:
    ...
```

This is the same call shape as the
[AutoScout24 scraper](https://github.com/danyk20/autoscout24-scraper)'s
`scrape()` — switching `from autoscout24_scraper import scrape` to
`from autouncle_scraper import scrape` needs no other code changes for a
caller that only uses these parameters.

Raises `ValueError` immediately (before any network call) if `category` is
anything other than `"car"`, or if any `_from` is greater than its `_to`.
Raises `requests.RequestException` subclasses on unrecoverable network
errors, and `ValueError` if `make`/`model` can't be resolved (the message
lists valid models for an unknown-model error).

**Logging.** Library code never configures logging itself (no
`basicConfig`, no handlers) — it only emits through
`logging.getLogger("autouncle_scraper")`, same as any well-behaved library.
To see progress from a plain script:

```python
import logging
logging.basicConfig(level=logging.INFO)
```

The CLI is the one place that *does* configure real handlers automatically
(`--verbose`/`--quiet`).

## `ScrapeResult` — the return value

```python
@dataclass
class ScrapeResult:
    make: str               # resolved brand, e.g. "VW"
    model: str               # resolved model, e.g. "Golf"
    domain: str               # domain that was scraped, e.g. "ch"
    filtered: bool            # True if any price/mileage/year filter was applied
    total_reported: int | None  # total match count from the search phase
    listings: list[dict]     # raw parsed records — see "Data structure" below
    rows: list[dict]         # flattened dicts, one per listing, CSV-ready, sorted by price ascending (unknown prices last)

    def to_csv(self, path: str) -> None: ...   # writes self.rows
    def to_json(self, path: str) -> None: ...  # writes self.listings
```

`len(result.rows) == len(result.listings) == result.total_reported` always
holds for an **unfiltered** search (`detail` only adds fields, never drops
or adds listings) or a **filtered** search visited with `detail=True`. For
a filtered search with `detail=False`, `total_reported` is still the true
total, but each row is just `{"id": ...}`.

## Data structure

### JSON (`result.listings` / the `.json` file)

A JSON array of listing objects. AutoUncle publishes no fixed schema for
these — treat unknown/missing fields defensively (`.get(...)`, not `[...]`).

**Unfiltered search, `detail=False`** — from JSON-LD, already fairly rich:

| Field | Type | Description |
|---|---|---|
| `id` | `string` | AutoUncle's internal listing id |
| `url` | `string` | Full URL of the original ad |
| `make`, `model` | `string` | |
| `year` | `int \| None` | First-registration year |
| `price`, `priceCurrency` | `number \| None`, `string \| None` | |
| `mileageKm` | `int \| None` | |
| `fuelType`, `transmission`, `bodyType` | `string \| None` | Free-form German-locale strings (e.g. `"Benzin"`, `"Schaltgetriebe"`, `"Cabrio"`) |
| `enginePowerPs`, `enginePowerKw`, `engineDisplacementL` | `number \| None` | |
| `fuelConsumptionL100km`, `co2GKm` | `number \| None` | |
| `addressCountry` | `string \| None` | Only the country is present at search-result granularity; full address requires the detail phase |
| `imageUrl`, `imageCaption` | `string \| None` | One thumbnail; the full gallery is detail-only |
| `numberOfDoors` | `int \| None` | |
| `itemCondition`, `availability` | `string \| None` | schema.org URLs, e.g. `"https://schema.org/UsedCondition"` |

**Filtered search, any `detail=False`** — id only: `{"id": "<listing id>"}`.
RSC (the filtered-search data source) carries no summary fields at all.

**Any search with `detail=True`** (the default) — everything above, plus
whatever the detail page adds:

| Field | Type | Description |
|---|---|---|
| `addressLocality`, `addressRegion`, `postalCode` | `string \| None` | Full seller address (search-result JSON-LD only has `addressCountry`) |
| `priceRatingLabel` | `string \| None` | AutoUncle's own price-rating label, e.g. `"Fairer Preis"` |
| `savingsVsMarketChf` | `number \| None` | Savings vs. AutoUncle's estimated market price |
| `daysOnMarket` | `int \| None` | |
| `fuelConsumptionLabel`, `co2EmissionsLabel` | mirrors `fuelConsumptionL100km`/`co2GKm` from a second source (`additionalProperty`) |
| `otherProperties` | `list[{name, value}]` | Any `additionalProperty` entry not recognized by the fixed label table in `ADDITIONAL_PROPERTY_LABELS` — nothing is silently dropped |
| `priceHistory` | `list[{date, price, currency, description}]` | Full historical price time series. **Not exposed by AutoScout24 at all.** |
| `datasetLicense`, `datasetIsAccessibleForFree` | `string \| None`, `bool \| None` | From the JSON-LD `Dataset` object backing `priceHistory` — as of this writing, `"https://creativecommons.org/licenses/by/4.0/"` and `true` |
| `imageUrls` | `list[str]` | Full gallery, deduped by image uuid, full-resolution preferred over size-prefixed variants |
| `equipment` | `dict[str, str]` | Variable per listing — spec/equipment label → value pairs (e.g. `{"Klimaanlage": "Ja", "Türen": "2"}`) scraped from the rendered page, not JSON-LD |
| `sourcePlatform` | `string \| None` | If this listing is aggregated from another portal (e.g. `"autoscout24-ch"`) rather than hosted natively |
| `dealerName`, `description`, `vin` | always `None` as of this writing | Confirmed absent from the rendered DOM for every listing checked; kept as real fields (not omitted) for forward compatibility — see the "maintenance risk" section |

### CSV (`result.rows` / the `.csv` file)

Flattened via `flatten_listing()`. Most rules match the AutoScout24
scraper's convention exactly (nested dict → `parent_child` columns, e.g.
`equipment_Klimaanlage`; list → semicolon-joined cell); three rules are
specific to shapes this scraper has that the reference never needed:

- `priceHistory` → one cell of semicolon-joined `"<date>=<price>"` entries.
- `otherProperties` → one cell of semicolon-joined `"<name>=<value>"` entries.
- `imageUrls` → one cell of semicolon-joined URLs.

Columns are the union of every field seen across all rows (heterogeneous
listings don't crash the writer — missing values are an empty string), with
`id, make, model, year, price, priceCurrency, mileageKm, fuelType,
transmission, bodyType, enginePowerPs, enginePowerKw, priceRatingLabel,
savingsVsMarketChf, daysOnMarket, addressLocality, addressRegion,
postalCode, addressCountry, dealerName, sourcePlatform, url` pinned first
and everything else sorted alphabetically after them (`PRIORITY_FIELDS`,
`order_fieldnames()`).

A full-detail row is around 50-55 columns as of this writing; a filtered,
`detail=False` row is just `id`.

## Maintenance risk

These are the parts of this scraper most likely to need attention first if
AutoUncle changes something, roughly in order of fragility:

1. **BeautifulSoup selectors** (`extract_equipment()`,
   `extract_gallery_images()`'s alt-text scoping, `extract_source_listing()`)
   — found structurally rather than by CSS class name specifically because
   AutoUncle's classes are build-hashed, but the underlying DOM shape
   (e.g. "a `<ul>` of `<li>` with exactly two `<span>` children") could
   still change on a redesign.
2. **The RSC listing-id regex** (`parse_rsc_listing_ids()`) and the
   filtered-search URL canonicalization rule
   (`build_filtered_search_url()`) — both derived from Next.js's internal
   Flight wire format, which is framework-internal and not a stable public
   contract. `parse_rsc_listing_ids()` raises `RuntimeError` rather than
   silently returning nothing if this ever breaks (see its docstring).
3. **`CarSearchInput`'s presumed-complete field list** — only the fields
   actually exercised (see the table above) are confirmed; a redesign of
   AutoUncle's filter UI could add/rename fields this scraper doesn't know
   about.
4. **`dealerName`/`description`/`vin`** always returning `None` — this
   reflects every listing checked at the time of writing, not a guarantee
   that AutoUncle never renders these for any listing.
5. **The `/api/v4/car_search_form/config` endpoint and JSON-LD shape** are
   the most stable of the mechanisms here (a documented-in-spirit REST
   endpoint and a public schema.org vocabulary respectively), but neither
   is a versioned, published contract either.

## Test coverage by area

| Area | Unit tests | E2E tests |
|---|---|---|
| `request_with_retries` | retry-then-succeed and exhausted-retries paths for 429/5xx/connection errors, no retry on 4xx | — |
| `resolve_make_key`/`resolve_model_key` | exact match, case-insensitivity, substring fallback, multi-word brands, not-found errors, exact-vs-substring precedence (e.g. `"Golf"` vs. `"Golf II"`..`"Golf VIII"`) | real lookups against the live `/api/v4/car_search_form/config` |
| `search_listings` (JSON-LD) | pagination across real 2-page fixtures, de-dup, no-ItemList/empty-result handling, inventory-shift safety net | real result count matching the site's own stated total |
| `parse_detail_jsonld`/`_price_history_from_dataset` | real fixture parsing, missing-Dataset handling, unparseable price-history entries skipped | real detail fetch |
| `extract_gallery_images`/`extract_equipment`/`extract_source_listing` | real fixture extraction, alt-text scoping (excluding unrelated "similar cars" thumbnails), structural edge cases | implicitly, via real data |
| `build_filtered_search_url`/`parse_rsc_pagination`/`parse_rsc_listing_ids`/`search_listings_filtered` | 4 real RSC fixture captures (single- and multi-filter, pages 1-2, with/without max-price slug), zero-results vs. broken-pattern distinction | full live paginated run (903/903 listings across 37 pages) |
| `count_cars`/`build_car_search_input` | mocked GraphQL request/response, error handling | live count for a real filter |
| `flatten_listing`/`_scalarize`/`order_fieldnames` | every branch (nested dicts, lists, the 3 scraper-specific rules, missing/unrecognized types) | implicitly, via real data |
| `save_csv`/`save_json`/`ScrapeResult` | heterogeneous rows, unicode, empty input | round-trip against real files |
| `scrape()` | orchestration (both search paths), range/category validation, filter-note logging, filtered+no-detail warning, sorting | full real pipeline, with and without `detail` |
| `main()`/`run_cli()` | every CLI flag, default vs. custom output filenames, all exit-code paths including `KeyboardInterrupt` | real subprocess run, real error exit code |

The unit suite covers 100% of `autouncle_scraper.py` (the two lines
excluded via `# pragma: no cover` are a defensive "unreachable" guard at
the end of `request_with_retries()`'s loop, and the `if __name__ ==
"__main__":` guard itself, which is exercised for real by the e2e suite's
CLI subprocess tests instead).
