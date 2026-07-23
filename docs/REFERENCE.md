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
| Unfiltered search (no price/mileage/year filter) | schema.org JSON-LD on the canonical, paginated brand/model page, topped up with the same page's RSC ("Flight") response for the search-card fields JSON-LD doesn't carry (see below) | `search_listings()`, `parse_vehicle_jsonld()`, `extract_search_card_supplements()` |
| Filtered search (price/mileage/year/body type/fuel type/colors/doors/seller kind/equipment/...) | Next.js RSC ("Flight") response, fetched from a URL of plain `s[...]` query params - the server's own embedded redirect handles turning some of those into an SEO slug | `build_filtered_search_url()`, `fetch_rsc_page()`, `parse_rsc_pagination()`, `parse_rsc_listing_ids()`, `extract_search_card_supplements()`, `search_listings_filtered()` |
| Listing detail | JSON-LD on the detail page, plus a BeautifulSoup pass over the same page for gallery/equipment/source-listing | `fetch_detail()`, `parse_detail_jsonld()`, `extract_gallery_images()`, `extract_equipment()`, `extract_source_listing()` |
| Fast match count (optional) | AutoUncle's own GraphQL `countCars` query | `count_cars()`, `build_car_search_input()` |

**Why two different search mechanisms exist**: confirmed empirically (both
via raw HTTP fetch and full browser navigation) that AutoUncle's server only
emits the JSON-LD block on the plain, unfiltered brand/model URL (optionally
paginated with `?page=N`) — any filter, in any URL form, gets `<meta
name="robots" content="noindex, follow">` and no JSON-LD at all. So a
filtered search has to use a different data source: the same RSC ("Flight")
response its own frontend renders search-result cards from. That response
turns out to carry a rich per-listing JSON object of its own (see
`extract_search_card_supplements()`/`parse_search_card_object()`) — price,
mileage, year, doors, body type, model/trim, price rating, savings vs.
market, price-change percent, days listed, source marketplace, full
address, and the image gallery — everything visible on the search page
itself, just not schema.org-shaped. It does NOT carry fuel type,
transmission, engine power, or CO2/consumption figures - AutoUncle's search
cards simply don't render those, so a filtered `scrape(..., detail=False)`
call's rows have everything except those few fields (and full price
history/equipment) until a detail visit (`detail=True`, the default).
Unfiltered searches fetch this same RSC data too (one extra request per
page), purely to fill in what JSON-LD doesn't have - JSON-LD stays
authoritative for every field it does carry.

### The filtered-search URL rule

Confirmed empirically two ways: by monkey-patching `window.fetch` and
driving AutoUncle's real filter form one control at a time, and by probing
the GraphQL `countCars` query directly with candidate field names (see
[CONTRIBUTING.md](../CONTRIBUTING.md) for both techniques, if this needs
re-deriving):

- **Every filter** is a Rails-style nested query parameter on the plain
  search URL: `s[min_price]`, `s[body_types][]`, `s[has_gps]`, etc. - the
  snake_case key is just the `CarSearchInput` field name (see below)
  converted by `_camel_to_snake()`; array-valued filters repeat the same
  `s[key][]=` key once per value.
- AutoUncle canonicalizes **some** single-value filters into an SEO path
  segment instead of leaving them as query params - confirmed for max price
  (`/mp-unter-{price}-chf`, "mp" = max price, German "unter" = "under"), a
  single fuel type (`/f-{fuel}`), and a single body type (`/b-{bodytype}`) -
  and sorts query keys alphabetically. Requesting the plain/unsorted form
  doesn't 404 - AutoUncle 200s with a redirect encoded *inside* the RSC
  response body itself (`NEXT_REDIRECT;replace;<canonical-url>;<code>;`),
  not a real HTTP 3xx.
- Rather than replicating AutoUncle's canonicalization rules by hand (there
  could be more not yet found), **`fetch_rsc_page()` follows that embedded
  redirect itself**, bounded to 5 hops. `build_filtered_search_url()`
  therefore always emits the plain, uniform query-param form for every
  filter and lets the server decide the canonical URL - this is simpler and
  more robust than trying to track every SEO-slug rule AutoUncle might have.

This was validated against many real captures spanning single- and
multi-filter combinations across price/km/year/body type/fuel type/doors/
colors/seller kind/equipment (0 to 2000+ total results, multiple pages)
with zero listing-id overlap across pages in every case, and against
several full live runs cross-checked against `count_cars()` (exact matches
every time, including a 903-listing run across 37 pages and a redirect
followed live end-to-end for a max-price filter).

### `CarSearchInput` (GraphQL) — confirmed vs. not-found fields

GraphQL introspection (`__schema`) is disabled in production, so every
field below was confirmed the hard way: calling `countCars()` directly with
a candidate field name/value and reading whether the server accepted it or
returned `"Field is not defined on CarSearchInput"`.

| Field | Type | Notes |
|---|---|---|
| `brand`, `carModel` | `string` | |
| `brandsModels` | `[{brand, modelName, equipmentVariants}]` | |
| `minPrice`/`maxPrice` | `int` (CHF) | |
| `minKm`/`maxKm` | `int` | |
| `minYear`/`maxYear` | `int` | |
| `bodyTypes` | `[string]` | see `BODY_TYPES` |
| `fuelTypes` | `[string]` | see `FUEL_TYPES` |
| `colors` | `[string]` | see `COLORS` |
| `doors` | `int` | **exact match**, not a range - no `minDoors`/`maxDoors` found |
| `sellerKind` | `string` | see `SELLER_KINDS` - **singular**, one value at a time, not a list |
| `euroEmissionClass` | `int` (1-6) | data coverage for CH listings seemed sparse when tested - a 0 count for a common class like 6 isn't necessarily a bug |
| `isOneOwner` | `bool` | |
| `notLeasing` | `bool` | config's own default for this is `true` |
| `notDamaged` | `bool` | config's own default for this is `false` |
| `minElectricDriveRange`/`maxElectricDriveRange` | `int` (km) | |
| `minBatteryCapacity`/`maxBatteryCapacity` | `int` (kWh) | |
| `minEnergyConsumption`/`maxEnergyConsumption` | `int` (kWh/100km) | |
| `maxFuelEconomy` | `float` (L/100km) | **no `minFuelEconomy`** found - confirmed one-directional |
| every string in `EQUIPMENT_OPTIONS` (~30 flags) | `bool` | each is its **own top-level field**, e.g. `hasGps: true` - not a single `equipment: [...]` list |

**Tried and not found** under any reasonable name: seats, transmission/gear,
region, price rating, emission label, days-on-sale, horsepower. These may
not be supported by `CarSearchInput` at all, or use a name not yet guessed
— see [CONTRIBUTING.md](../CONTRIBUTING.md) if you want to keep looking.

`build_car_search_input()` validates `body_types`/`fuel_types`/`colors`/
`seller_kind`/`equipment` against the known vocabulary above and raises
`ValueError` (listing the valid options) for anything else, rather than
silently sending AutoUncle a value it would just ignore or 0-result on.
Anything confirmed above without its own named parameter (`euroEmissionClass`,
`notLeasing`, `notDamaged`, the EV range fields, `maxFuelEconomy`) goes
through `extra_filters` - a plain passthrough dict merged in as-is (use the
exact GraphQL field names, camelCase).

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
    body_types: Iterable[str] | None = None,   # see BODY_TYPES
    fuel_types: Iterable[str] | None = None,   # see FUEL_TYPES
    colors: Iterable[str] | None = None,       # see COLORS
    doors: int | None = None,                  # exact match, not a range
    seller_kind: str | None = None,            # see SELLER_KINDS - singular
    one_owner: bool | None = None,
    equipment: Iterable[str] | None = None,    # see EQUIPMENT_OPTIONS - AND semantics
    extra_filters: dict | None = None,         # passthrough for any other confirmed CarSearchInput field
    max_results: int | None = None,  # only open this many listings (first N from search order), skip the rest
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

`len(result.rows) == len(result.listings) == result.total_reported` holds
for an **unfiltered** search (`detail` only adds fields, never drops or
adds listings) or a **filtered** search visited with `detail=True` -
**except** that a listing can legitimately disappear (sold, ad removed)
between the search phase finding it and the detail phase visiting it; that
one listing is skipped (logged as a warning) rather than aborting the
whole scrape, so `len(result.rows)`/`len(result.listings)` can occasionally
be one or a few short of `total_reported`. `total_reported` always reflects
what the search phase itself found - it is never adjusted for skips. For a
filtered search with `detail=False`, `total_reported` is still the true
total, but each row is just `{"id": ...}`. If `max_results` was given,
both `.rows` and `.listings` are capped at `max_results` **before** the
detail phase runs - only the first `max_results` ids from the search
phase are ever visited (or returned, with `detail=False`); the rest are
never fetched at all, which is the entire point of the parameter.
`total_reported` always reflects the search phase's true total and is
unaffected by that cap. Because the cap happens before ordering by
recency is possible, "first" here means "first in AutoUncle's own default
search-result order", which is not confirmed to be a date sort - so
`max_results` gives you *a* fast N, not guaranteed to be the *newest* N.

## Data structure

### JSON (`result.listings` / the `.json` file)

A JSON array of listing objects. AutoUncle publishes no fixed schema for
these — treat unknown/missing fields defensively (`.get(...)`, not `[...]`).

**Unfiltered search, `detail=False`** — from JSON-LD, topped up with the
RSC search-card fields below (see `extract_search_card_supplements()`):

| Field | Type | Description |
|---|---|---|
| `id` | `string` | AutoUncle's internal listing id |
| `url` | `string` | Full URL of the original ad |
| `make`, `model` | `string` | |
| `modelVariant` | `string \| None` | Trim/spec line under the title, e.g. `"P90D (Free Supercharging)"` - RSC-only, not in JSON-LD at all |
| `year` | `int \| None` | First-registration year |
| `price`, `priceCurrency` | `number \| None`, `string \| None` | |
| `mileageKm` | `int \| None` | |
| `fuelType`, `transmission`, `bodyType` | `string \| None` | Free-form German-locale strings (e.g. `"Benzin"`, `"Schaltgetriebe"`, `"Cabrio"`) - JSON-LD only, not in the RSC search card |
| `enginePowerPs`, `enginePowerKw`, `engineDisplacementL` | `number \| None` | JSON-LD only |
| `fuelConsumptionL100km`, `co2GKm` | `number \| None` | JSON-LD only |
| `addressLocality`, `addressRegion`, `postalCode`, `addressCountry` | `string \| None` | Full seller address - the locality/region/postal code come from the RSC search card, not JSON-LD (which only has the country at this level) |
| `imageUrl`, `imageCaption` | `string \| None` | One thumbnail (JSON-LD) |
| `imageUrls` | `list[string] \| None` | The listing's full image gallery - RSC-only, no detail visit needed |
| `priceRatingLabel` | `string \| None` | AutoUncle's own price-rating label, e.g. `"Guter Preis"` - RSC-only at this level |
| `savingsVsMarketChf` | `int \| None` | Savings vs. AutoUncle's estimated market price |
| `estimatedMarketPriceChf` | `int \| None` | AutoUncle's own estimated market price for this listing |
| `priceChangePercent` | `int \| None` | e.g. `-36` for a 36% price drop since first listed |
| `daysOnMarket` | `int \| None` | Days since first listed |
| `sourcePlatform`, `sourcePath` | `string \| None` | For listings aggregated from another portal (e.g. `"Autoscout24"`) - name and outgoing link path |
| `numberOfDoors` | `int \| None` | |
| `itemCondition`, `availability` | `string \| None` | schema.org URLs, e.g. `"https://schema.org/UsedCondition"` (JSON-LD only) |

**Filtered search, `detail=False`** — the same RSC search-card fields as
above (`id`, `make`, `model`, `modelVariant`, `year`, `mileageKm`,
`numberOfDoors`, `bodyType`, `price`, `priceCurrency`, `priceRatingLabel`,
`savingsVsMarketChf`, `estimatedMarketPriceChf`, `priceChangePercent`,
`daysOnMarket`, address fields, `imageUrl`/`imageUrls`/`imageCaption`,
`sourcePlatform`/`sourcePath`) - but **not** `fuelType`, `transmission`,
`enginePowerPs`/`enginePowerKw`/`engineDisplacementL`, or
`fuelConsumptionL100km`/`co2GKm`, since AutoUncle's search cards don't
carry those at all (JSON-LD does, but is suppressed on filtered pages -
see "How the data is gathered" above). A listing id this couldn't find/parse
a card object for falls back to `{"id": "<listing id>"}` alone.

**Any search with `detail=True`** (the default) — everything above (with
JSON-LD now filling in whatever the RSC search card didn't have, for a
filtered search too), plus whatever the detail page adds. Crucially,
`scrape()` explicitly merges the level-1 record's fields back into each
detail record afterward (fill gaps only, never overwrite - see its
docstring): the detail page itself never renders `modelVariant`,
`priceChangePercent`, `estimatedMarketPriceChf`, or `sourcePath` at all, so
without this merge those would silently vanish once a listing goes through
the detail phase, even though level 1 had them. A field the detail page
*does* set (e.g. `price`, or a more precise `addressLocality`) always wins
over the level-1 value.

| Field | Type | Description |
|---|---|---|
| `fuelConsumptionLabel`, `co2EmissionsLabel` | mirrors `fuelConsumptionL100km`/`co2GKm` from a second source (`additionalProperty`) |
| `otherProperties` | `list[{name, value}]` | Any `additionalProperty` entry not recognized by the fixed label table in `ADDITIONAL_PROPERTY_LABELS` — nothing is silently dropped |
| `priceHistory` | `list[{date, price, currency, description}]` | Full historical price time series. **Not exposed by AutoScout24 at all.** |
| `firstSeenAt`, `lastUpdatedAt` | `string \| None` (ISO 8601) | Earliest/latest date in `priceHistory` — **not** the JSON-LD `Dataset`'s own `datePublished`/`dateModified` fields, which are confirmed live to be request-time noise (they come back ~equal to "now" regardless of the listing) rather than real listing metadata. `None` when there's no price history to derive it from. Note `max_results` does **not** sort or filter by this field — see the `ScrapeResult` notes above. |
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
3. **The RSC search-card JSON object**
   (`extract_search_card_supplements()`/`parse_search_card_object()`) —
   relies on AutoUncle's frontend still serializing each search-result card
   as a `{"carId": ..., "subtitle": ..., ...}`-shaped object somewhere in
   the RSC response, and on specific field names within it (`subtitle`,
   `laytime`, `youSaveDifference`, `priceChange`, `location`,
   `modalPriceHistoryValues.estimatedPrice`, `sourceName`, `outgoingPath`,
   `imageUrls`, ...). A renamed field just goes missing (`None`) rather
   than breaking anything, since every read is a `.get(...)`, but a
   restructured card shape (e.g. these fields nested one level deeper)
   would silently stop matching. `_json_object_containing()` itself (the
   brace-matching JSON extractor) doesn't depend on any of AutoUncle's
   specific field names, only on the response staying valid, embedded JSON
   somewhere - the most stable part of this mechanism.
4. **`CarSearchInput`'s field list is confirmed-by-probing, not exhaustive**
   — the table above covers everything tried (including all ~30 equipment
   flags, which weren't individually tested but follow one confirmed,
   consistent pattern), but a redesign of AutoUncle's filter UI could
   add/rename fields this scraper doesn't know about, and a few plausible
   ones (seats, transmission, region) were tried and never found under any
   reasonable name.
5. **`dealerName`/`description`/`vin`** always returning `None` — this
   reflects every listing checked at the time of writing, not a guarantee
   that AutoUncle never renders these for any listing.
6. **`firstSeenAt`/`lastUpdatedAt` being derived from `priceHistory`
   extremes** rather than a dedicated field — because there isn't one (the
   JSON-LD `Dataset`'s own `datePublished`/`dateModified` were tried first
   and found to be request-time noise, not real listing metadata - see the
   data-structure table above). If AutoUncle ever exposes a real "date
   posted" field, or changes what `priceHistory` contains, this derivation
   needs revisiting. Note the RSC search card's own `lastObservedAt` field
   was deliberately NOT wired up here, to avoid two differently-derived
   fields with overlapping meaning; it's available in the raw RSC response
   if a future need justifies adding it.
7. **The `/api/v4/car_search_form/config` endpoint and JSON-LD shape** are
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
| `extract_search_card_supplements`/`parse_search_card_object`/`_json_object_containing`/`_find_matching_brace` | synthetic real-shaped card objects (full field mapping, missing fields, unparseable/malformed JSON, nested-object brace-matching edge cases), unfiltered-search merge (fills gaps, never overwrites JSON-LD) | real Tesla Model S listing cross-checked against the rendered search page |
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
