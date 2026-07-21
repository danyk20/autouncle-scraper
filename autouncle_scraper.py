#!/usr/bin/env python3
"""
AutoUncle.ch used-car listing scraper.

Unlike AutoScout24 (see the sibling `autoscout24-scraper` project this module
is a drop-in-compatible sibling of), AutoUncle has no single clean public
JSON search API. It is a Next.js (App Router / React Server Components)
site, and its data has to be gathered from three different places depending
on what you ask for:

  1. Brand/model reference data:
       GET https://www.autouncle.ch/api/v4/car_search_form/config
     A public, unauthenticated REST endpoint. Unlike AutoScout24's
     make/model API, there is no separate "key" vs. "display name" here -
     the brand/model string returned by this endpoint (e.g. "VW", "Golf")
     IS the URL path segment used everywhere else (e.g. /de-ch/gebrauchtwagen/VW/Golf).

  2. Unfiltered search results (no price/mileage/year filter):
       GET https://www.autouncle.ch/{locale}/{cars_path}/{Brand}/{Model}?page=N
     The server renders a schema.org JSON-LD block
     (<script type="application/ld+json">) containing an `ItemList` whose
     `itemListElement` is an almost-complete Product+Vehicle record per
     listing (price, mileage, year, engine, fuel, transmission, body type,
     one thumbnail). `numberOfItems` on the ItemList is the grand total
     across all pages. Confirmed: plain `?page=N` pagination preserves this
     JSON-LD block; nothing else does (see point 3).

  3. Filtered search results (any price/mileage/year filter):
     Confirmed by both raw HTTP fetch and full browser navigation: as soon
     as ANY filter is applied - whether as a query string or as the SEO
     slug-path form AutoUncle's own frontend uses (e.g.
     ".../VW/Golf/mp-unter-5000-chf" for "max price under 5000 CHF") - the
     server marks the page `<meta name="robots" content="noindex, follow">`
     and omits the JSON-LD block entirely. This is a deliberate
     anti-duplicate-content choice on AutoUncle's part, not a caching
     artifact. So filtered search has to go a different route:
       a. POST https://www.autouncle.ch/graphql with a `CarSearchInput`
          object to resolve the canonical filtered-search slug URL
          (`carSearchUrl` query) and/or a live match count (`countCars`
          query) - see `graphql_request()`, `resolve_filtered_search_url()`,
          `count_cars()`.
       b. Fetch that slug URL per page with the header `RSC: 1`, which
          returns Next.js's React Server Component "Flight" wire format
          instead of full HTML (much smaller, no JSON-LD either, but it is
          what the site's own frontend uses to hydrate filtered results
          client-side). Rather than writing a general Flight-protocol
          deserializer (a substantial, framework-internal-format,
          version-fragile undertaking), this module extracts only two
          things from that text via small targeted regexes: pagination
          metadata and the ordered list of listing ids on the page - see
          `fetch_rsc_page()`, `parse_rsc_pagination()`,
          `parse_rsc_listing_ids()`. Filtered search therefore only ever
          yields listing ids, not rich summary fields - the detail phase
          (point 4) is what fills those in, same as the unfiltered path.

  4. Listing detail pages:
       GET https://www.autouncle.ch/{locale}/d/{id}-{seo-slug}
     Carries a richer JSON-LD graph than the search-result summary: full
     seller address (city/zip/region), `additionalProperty` entries (fuel
     consumption, CO2 g/km, AutoUncle's own price-rating label e.g. "Fairer
     Preis", savings vs. market price, days on market), and a sibling
     `Dataset` object (id ending "#price-history") holding a full
     historical price time series - a field AutoScout24 does not expose at
     all. That `Dataset` is explicitly marked
     `"isAccessibleForFree": true` and licensed
     `"https://creativecommons.org/licenses/by/4.0/"` - a stronger, explicit
     legal footing than "we just call their own frontend's endpoint".
     The gallery (beyond one JSON-LD thumbnail) and the equipment/feature
     list are NOT in the JSON-LD at all; they only exist in the rendered
     HTML, so a small BeautifulSoup pass over the detail page supplements
     the JSON-LD for those two things (see `extract_gallery_images()`,
     `extract_equipment()`, `extract_dealer_name()`).

Domain: every function takes an optional `domain` (default "ch"), looked up
in `DOMAINS` for the target host/locale/cars-path. As of this writing, only
"ch" is implemented and tested; the lookup-table shape exists so adding
another AutoUncle country (e.g. "de", "dk") later is a one-entry addition,
not a rewrite - mirroring how the AutoScout24 scraper's own `domain`
parameter is designed.

This module can be used two ways:

1. As a standalone CLI script that writes a CSV + JSON file:

    python3 autouncle_scraper.py --make VW --model Golf
    python3 autouncle_scraper.py --make VW --model Golf --no-detail
    python3 autouncle_scraper.py --make VW --model Golf --price-to 5000 --year-from 2015

2. As a library, imported from another project, returning data directly
   instead of writing files:

    from autouncle_scraper import scrape

    result = scrape("VW", "Golf", price_to=5000)
    for row in result.rows:          # flattened dicts, one per listing
        print(row["price"], row["url"])
    result.listings                  # raw (unflattened) parsed records
    result.to_csv("vw_golf.csv")     # optional, if you want a file after all
    result.to_json("vw_golf.json")
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

__version__ = "0.1.0"


@dataclass(frozen=True)
class DomainConfig:
    """Everything that differs between AutoUncle's country sites."""

    host: str  # e.g. "www.autouncle.ch"
    locale: str  # e.g. "de-ch" - the URL locale prefix
    cars_path: str  # e.g. "gebrauchtwagen" - the localized "used cars" URL segment


DEFAULT_DOMAIN = "ch"

# Only "ch" is implemented/tested as of this writing (see module docstring).
# Adding another AutoUncle country site later is meant to be a one-entry
# addition here, not a rewrite of the functions that consume it.
DOMAINS: dict[str, DomainConfig] = {
    "ch": DomainConfig(host="www.autouncle.ch", locale="de-ch", cars_path="gebrauchtwagen"),
}


def get_domain_config(domain: str = DEFAULT_DOMAIN) -> DomainConfig:
    try:
        return DOMAINS[domain]
    except KeyError:
        raise ValueError(
            f"Unsupported domain {domain!r}. Only {sorted(DOMAINS)} are implemented as of this writing "
            "(see the module docstring for why AutoUncle's other country sites aren't wired up yet)."
        ) from None


# Library code only ever logs through this logger - it never calls
# basicConfig or attaches handlers of its own (that would be rude to a host
# application). The CLI (see _configure_cli_logging(), used by main()) is the
# only place that sets up real handlers, so plain library use is silent
# unless the caller configures logging themselves, e.g.:
#     import logging; logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("autouncle_scraper")
logger.addHandler(logging.NullHandler())


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def request_with_retries(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    backoff: float = 1.5,
    **kwargs: Any,
) -> requests.Response:
    kwargs.setdefault("timeout", 20)
    logger.debug("%s %s", method, url)
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            if attempt == max_retries:
                raise
            wait = backoff**attempt
            logger.warning("%s %s failed (%s); retry %d/%d in %.1fs", method, url, exc, attempt, max_retries, wait)
            time.sleep(wait)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == max_retries:
                resp.raise_for_status()
            wait = backoff**attempt
            logger.warning(
                "%s %s -> %d; retry %d/%d in %.1fs", method, url, resp.status_code, attempt, max_retries, wait
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    raise RuntimeError("unreachable")  # pragma: no cover


# ============================================================
# Brand / Model resolution
# ============================================================

CONFIG_ENDPOINT_PATH = "/api/v4/car_search_form/config"


def fetch_search_form_config(
    domain: str = DEFAULT_DOMAIN, *, session: requests.Session | None = None
) -> dict[str, Any]:
    """Fetch the public car_search_form/config endpoint: brands, models
    (with live listing counts), body types, and various filter-range
    metadata. This is the AutoUncle analog of AutoScout24's makes/models
    endpoints."""
    session = session or make_session()
    domain_cfg = get_domain_config(domain)
    url = f"https://{domain_cfg.host}{CONFIG_ENDPOINT_PATH}"
    resp = request_with_retries(session, "GET", url)
    result: dict[str, Any] = resp.json()
    return result


def _flatten_models(brand_entry: dict[str, Any]) -> list[str]:
    """Flatten a carModelsByBrandDetailed brand entry's nested
    allSeriesAndModels[].models[] into a flat list of model display strings."""
    models: list[str] = []
    for series in brand_entry.get("allSeriesAndModels", []):
        for m in series.get("models", []):
            models.append(m["model"])
    return models


def resolve_make_key(make_query: str, config: dict[str, Any]) -> str:
    """Resolve a make name (case-insensitive, exact or substring) to its
    canonical AutoUncle brand string - which is also the URL path segment,
    e.g. "VW". Unlike AutoScout24, there's no separate key/name split here,
    so this resolver is a simpler 2-tier cascade (exact, then substring)
    rather than the reference project's 4-tier one."""
    q = make_query.strip().lower()
    brands: list[str] = config["brands"]["all"]
    for b in brands:
        if b.lower() == q:
            return b
    matches = [b for b in brands if q in b.lower()]
    if matches:
        if len(matches) > 1:
            logger.warning("Multiple brands match %r: %s; using %r", make_query, matches, matches[0])
        return matches[0]
    raise ValueError(f"Could not find a brand matching {make_query!r}")


def resolve_model_key(make_key: str, model_query: str, config: dict[str, Any]) -> str:
    """Resolve a model name (case-insensitive, exact or substring) to its
    canonical AutoUncle model string for the given (already-resolved) brand -
    which is also the URL path segment, e.g. "Golf"."""
    q = model_query.strip().lower()
    brand_entries = config["carModelsByBrandDetailed"]["allBrands"]
    try:
        brand_entry = next(b for b in brand_entries if b["brand"] == make_key)
    except StopIteration:
        raise ValueError(f"No model data found for brand {make_key!r}") from None

    models = _flatten_models(brand_entry)
    for m in models:
        if m.lower() == q:
            return m
    matches = [m for m in models if q in m.lower()]
    if matches:
        if len(matches) > 1:
            logger.warning(
                "Multiple models match %r for brand %r: %s; using %r", model_query, make_key, matches, matches[0]
            )
        return matches[0]
    available = ", ".join(sorted(models))
    raise ValueError(f"Could not find a model matching {model_query!r} for brand {make_key!r}. Available: {available}")


# ============================================================
# JSON-LD extraction (shared by search-result and detail pages)
# ============================================================

_LDJSON_RE = re.compile(r'<script(?=[^>]*type="application/ld\+json")[^>]*>(.*?)</script>', re.S)
_LISTING_ID_RE = re.compile(r"/d/(\d+)-")


def _graph_items(html: str) -> list[dict[str, Any]]:
    """Parse every <script type="application/ld+json"> block on the page and
    return the merged @graph array. AutoUncle emits exactly one such block
    per page as of this writing, but merging is harmless if that changes."""
    items: list[dict[str, Any]] = []
    for match in _LDJSON_RE.finditer(html):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning("Skipping unparseable JSON-LD block")
            continue
        items.extend(data.get("@graph", []))
    return items


def _has_type(node: dict[str, Any], type_name: str) -> bool:
    t = node.get("@type")
    if isinstance(t, list):
        return type_name in t
    return bool(t == type_name)


def find_item_list(graph_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((g for g in graph_items if _has_type(g, "ItemList")), None)


def find_vehicle(graph_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((g for g in graph_items if _has_type(g, "Vehicle")), None)


def find_dataset(graph_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((g for g in graph_items if _has_type(g, "Dataset")), None)


def _listing_id_from_iri(iri: str) -> str | None:
    m = _LISTING_ID_RE.search(iri)
    return m.group(1) if m else None


# German-locale additionalProperty labels AutoUncle renders on its de-ch
# site, mapped to stable, English field names. Anything not in this table
# is kept verbatim under "otherProperties" rather than silently dropped -
# AutoUncle doesn't publish a fixed schema for these, so treat this list as
# "known so far", not exhaustive.
ADDITIONAL_PROPERTY_LABELS = {
    "Kraftstoffverbrauch": "fuelConsumptionLabel",
    "CO2-Emissionen": "co2EmissionsLabel",
    "Preisbewertung": "priceRatingLabel",
    "Ersparnis ggü. Marktpreis": "savingsVsMarketChf",
    "Tage auf dem Markt": "daysOnMarket",
}


def _parse_additional_properties(props: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    other: list[dict[str, Any]] = []
    for p in props:
        name = p.get("name")
        value = p.get("value")
        if isinstance(value, dict):
            value = value.get("value")
        key = ADDITIONAL_PROPERTY_LABELS.get(name) if isinstance(name, str) else None
        if key:
            result[key] = value
        else:
            other.append({"name": name, "value": value})
    if other:
        result["otherProperties"] = other
    return result


def parse_vehicle_jsonld(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a schema.org Product+Vehicle JSON-LD object (from either a
    search-result ItemList entry or a detail page) into a flat-ish dict of
    stable field names. Both shapes are the same schema, just with fewer
    fields populated in the search-result case (e.g. no full address, no
    additionalProperty) - so one parser covers both, and simply omits (as
    None/[]) whatever the source didn't provide."""
    engine = item.get("vehicleEngine", {}) or {}
    displacement = engine.get("engineDisplacement", {}) or {}
    power = engine.get("enginePower", {}) or {}
    power_kw_prop = power.get("additionalProperty", {}) or {}
    mileage = item.get("mileageFromOdometer", {}) or {}
    fuel_consumption = item.get("fuelConsumption", {}) or {}
    offers = item.get("offers", {}) or {}
    address = (offers.get("availableAtOrFrom", {}) or {}).get("address", {}) or {}
    brand = item.get("brand", {}) or {}
    image = item.get("image", {}) or {}

    listing_id = _listing_id_from_iri(item.get("@id", "")) or _listing_id_from_iri(
        (item.get("mainEntityOfPage", {}) or {}).get("@id", "")
    )

    parsed: dict[str, Any] = {
        "id": listing_id,
        "name": item.get("name"),
        "description": item.get("description"),
        "color": item.get("color") or None,
        "make": brand.get("name"),
        "model": item.get("model"),
        "year": _to_int(item.get("vehicleModelDate")),
        "transmission": item.get("vehicleTransmission"),
        "numberOfDoors": _to_int(item.get("numberOfDoors")),
        "bodyType": item.get("bodyType"),
        "fuelType": item.get("fuelType"),
        "engineDisplacementL": displacement.get("value"),
        "enginePowerPs": power.get("value"),
        "enginePowerKw": power_kw_prop.get("value") if power_kw_prop.get("name") == "kilowatt" else None,
        "mileageKm": _to_int(mileage.get("value")),
        "fuelConsumptionL100km": fuel_consumption.get("value"),
        "co2GKm": item.get("emissionsCO2"),
        "price": offers.get("price"),
        "priceCurrency": offers.get("priceCurrency"),
        "itemCondition": offers.get("itemCondition"),
        "availability": offers.get("availability"),
        "addressCountry": address.get("addressCountry"),
        "addressLocality": address.get("addressLocality"),
        "addressRegion": address.get("addressRegion"),
        "postalCode": address.get("postalCode"),
        "imageUrl": image.get("url"),
        # "url" is deliberately not set here - this parser is domain-agnostic
        # (it doesn't know the host/locale), so callers (search_listings(),
        # fetch_detail()) set the correct fully-qualified URL themselves.
    }
    parsed.update(_parse_additional_properties(item.get("additionalProperty", []) or []))
    return parsed


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ============================================================
# Unfiltered search (JSON-LD + pagination)
# ============================================================


def build_search_url(domain_cfg: DomainConfig, make_key: str, model_key: str, page: int = 1) -> str:
    path = "/".join(quote(seg, safe="") for seg in (domain_cfg.locale, domain_cfg.cars_path, make_key, model_key))
    url = f"https://{domain_cfg.host}/{path}"
    if page > 1:
        url += f"?page={page}"
    return url


def search_listings(
    make_key: str,
    model_key: str,
    domain_cfg: DomainConfig,
    *,
    session: requests.Session,
    delay: float = 0.4,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Fetch every listing for a make/model via the canonical (unfiltered)
    search-result pages' JSON-LD, paginating until numberOfItems is fully
    collected. Each returned dict is a parse_vehicle_jsonld() summary record
    (already fairly rich - price, mileage, year, engine, fuel, transmission,
    body type - though not as complete as a detail-page visit)."""
    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 1
    total_pages = 1
    total_elements: int | None = None
    page_size: int | None = None  # derived once, from page 1 - later pages are shorter (the remainder)

    while page <= total_pages:
        url = build_search_url(domain_cfg, make_key, model_key, page=page)
        resp = request_with_retries(session, "GET", url)
        graph = _graph_items(resp.text)
        item_list = find_item_list(graph)
        if item_list is None:
            if page == 1:
                logger.warning("No ItemList found on %s; assuming zero results", url)
            break

        total_elements = item_list.get("numberOfItems", total_elements)
        items = item_list.get("itemListElement", [])
        if page_size is None:
            page_size = len(items) or 25
        if total_elements is not None:
            total_pages = max(1, -(-total_elements // page_size))  # ceil division

        new_count = 0
        for element in items:
            vehicle = parse_vehicle_jsonld(element.get("item", {}))
            listing_id = vehicle.get("id")
            if listing_id and listing_id not in seen_ids:
                seen_ids.add(listing_id)
                vehicle["url"] = f"https://{domain_cfg.host}/{domain_cfg.locale}/d/{listing_id}"
                listings.append(vehicle)
                new_count += 1

        if verbose:
            logger.info(
                "  page %d/%d: %d listings (%d new, %d total so far)",
                page,
                total_pages,
                len(items),
                new_count,
                len(listings),
            )

        if new_count == 0 and page > 1:
            # A live inventory can shift between requests; stop rather than
            # loop forever if a later page unexpectedly yields nothing new.
            logger.warning("Page %d yielded no new listings; stopping early", page)
            break

        page += 1
        if page <= total_pages:
            time.sleep(delay)

    if verbose and total_elements is not None:
        logger.info("  site reports %d total matches; collected %d unique listings", total_elements, len(listings))

    return listings


# ============================================================
# Detail-page parsing (JSON-LD + price history)
# ============================================================

_PRICE_HISTORY_DATE_RE = re.compile(r"Preis am (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})")


def build_detail_url(domain_cfg: DomainConfig, listing_id: str) -> str:
    """AutoUncle detail URLs normally carry a marketing SEO slug after the
    id (e.g. "/d/6690428-gebraucht-2011-vw-golf-160-ps"), but the bare
    "/d/{id}" form (no slug) also resolves - AutoScout24's listing_url()
    doesn't need to know the slug either, so this mirrors that."""
    return f"https://{domain_cfg.host}/{domain_cfg.locale}/d/{listing_id}"


def _price_history_from_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """AutoUncle's price-history Dataset has no dedicated datetime field per
    entry - the ISO datetime only exists embedded in the human-readable
    "Preis am <ISO datetime>" name string, so it has to be regexed out.
    Entries that don't match are skipped (logged, not raised) rather than
    aborting the whole detail parse over one malformed entry."""
    history: list[dict[str, Any]] = []
    for entry in dataset.get("variableMeasured", []) or []:
        name = entry.get("name", "")
        m = _PRICE_HISTORY_DATE_RE.search(name)
        if not m:
            logger.debug("Could not parse a date out of price-history entry name %r; skipping", name)
            continue
        history.append(
            {
                "date": m.group(1),
                "price": entry.get("value"),
                "currency": entry.get("unitText"),
                "description": entry.get("description"),
            }
        )
    return history


def parse_detail_jsonld(html: str) -> dict[str, Any]:
    """Parse a listing detail page's JSON-LD into one merged dict: the
    normalized Product+Vehicle record (same parser used for search-result
    items, but with more fields populated here - full address,
    additionalProperty market-analysis fields) plus a "priceHistory" list
    from the sibling Dataset object, when present."""
    graph = _graph_items(html)
    vehicle = find_vehicle(graph)
    if vehicle is None:
        raise ValueError("No Vehicle JSON-LD object found on detail page")

    parsed = parse_vehicle_jsonld(vehicle)

    dataset = find_dataset(graph)
    if dataset is not None:
        parsed["priceHistory"] = _price_history_from_dataset(dataset)
        parsed["datasetLicense"] = dataset.get("license")
        parsed["datasetIsAccessibleForFree"] = dataset.get("isAccessibleForFree")
    else:
        parsed["priceHistory"] = []

    return parsed


def fetch_detail(listing_id: str, *, domain_cfg: DomainConfig, session: requests.Session) -> dict[str, Any]:
    """Fetch and parse one listing's detail page."""
    url = build_detail_url(domain_cfg, listing_id)
    resp = request_with_retries(session, "GET", url)
    detail = parse_detail_jsonld(resp.text)
    detail.setdefault("id", listing_id)
    detail["url"] = url
    return detail


def visit_all_listings(
    listing_ids: Iterable[str],
    *,
    domain_cfg: DomainConfig,
    session: requests.Session,
    delay: float = 0.4,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Visit each listing id's detail page one by one and return the full
    parsed record for each. This is where the vast majority of scraped
    fields come from - full address, price history, market-analysis
    labels - regardless of whether the search phase used JSON-LD or the
    filtered GraphQL+RSC path."""
    ids = list(listing_ids)
    total = len(ids)
    visited: list[dict[str, Any]] = []
    for i, listing_id in enumerate(ids, 1):
        detail = fetch_detail(listing_id, domain_cfg=domain_cfg, session=session)
        visited.append(detail)
        if verbose and (i % 10 == 0 or i == total):
            logger.info("  visited %d/%d listings (id=%s)", i, total, listing_id)
        if i < total:
            time.sleep(delay)
    return visited
