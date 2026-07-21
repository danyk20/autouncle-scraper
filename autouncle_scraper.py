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
     as ANY filter is applied, the server marks the page `<meta
     name="robots" content="noindex, follow">` and omits the JSON-LD block
     entirely - a deliberate anti-duplicate-content choice, not a caching
     artifact. Filtering instead works like this (see the "Filtered search"
     section below for the full derivation):
       a. Max price canonicalizes into an SEO path segment,
          "/mp-unter-{price}-chf"; every other filter (min price, min/max
          km, min/max year) is a Rails-style nested query param,
          "?s[min_price]=X&s[min_km]=Y&...", sorted alphabetically by key
          (build_filtered_search_url()).
       b. Fetch that URL per page with the header `RSC: 1`, which returns
          Next.js's React Server Component "Flight" wire format instead of
          full HTML. Rather than writing a general Flight-protocol
          deserializer, this module extracts only two things via small
          targeted regexes: pagination metadata and the ordered list of
          listing ids on the page (fetch_rsc_page(), parse_rsc_pagination(),
          parse_rsc_listing_ids()). Filtered search therefore only ever
          yields listing ids, not rich summary fields - the detail phase
          (point 4) is what fills those in, same as the unfiltered path.
       c. AutoUncle's GraphQL endpoint also exposes a `countCars` query
          with the same filter shape, confirmed live and used as a fast,
          optional total-count check (count_cars()) - it is a supplement,
          not the mechanism that yields listing data.

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

import csv
import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

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
        "imageCaption": image.get("caption"),
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


# ============================================================
# Supplemental HTML scraping (BeautifulSoup)
#
# None of this is in the JSON-LD - it only exists in the rendered DOM, so a
# detail page's full record is JSON-LD (parse_detail_jsonld) PLUS whatever
# this section adds. Every extractor here degrades to an empty list/None on
# a selector miss rather than raising, since presence varies per listing
# (e.g. equipment lists differ; a private-seller listing may show no dealer
# name at all).
# ============================================================

_IMAGE_URL_RE = re.compile(
    r"https://images\.autouncle\.com/[\w/]+/car_images/(?:(small|medium|large)_)?([\w-]+)_[\w.-]+\.(?:jpe?g|webp|png)"
)
_SOURCE_LISTING_RE = re.compile(r'href="(/[\w-]+/das_wiedersehen/([\w-]+)/\d+/\d+)"')


def extract_gallery_images(html: str, *, expected_alt: str | None = None) -> list[str]:
    """Extract this listing's own gallery photos, deduped by image uuid and
    preferring the full-resolution variant over small_/medium_/large_
    prefixed ones. The page can also embed OTHER listings' thumbnails
    (a "similar cars" section) using the same images.autouncle.com/car_images
    URL shape but a generic alt text (just brand+model, no full spec) - so
    when `expected_alt` is given (normally the JSON-LD Vehicle's
    `image.caption`), only <img> tags with a matching alt are considered,
    which reliably excludes those unrelated thumbnails."""
    soup = BeautifulSoup(html, "html.parser")
    by_uuid: dict[str, dict[str, str]] = {}
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not isinstance(src, str) or "car_images" not in src:
            continue
        if expected_alt is not None and img.get("alt") != expected_alt:
            continue
        m = _IMAGE_URL_RE.search(src)
        if not m:
            continue
        size_prefix, uuid = m.group(1) or "", m.group(2)
        by_uuid.setdefault(uuid, {})[size_prefix] = src

    result = []
    for variants in by_uuid.values():
        for pref in ("", "large", "medium", "small"):
            if pref in variants:
                result.append(variants[pref])
                break
    return result


def extract_equipment(html: str) -> dict[str, str]:
    """Extract label/value spec+equipment pairs (e.g. "Klimaanlage": "Ja",
    "Isofix": "Ja", "CO2": "150 g CO2/km komb.") from the page's simple
    <ul><li><span>label</span><span>value</span></li></ul> blocks. Found
    structurally (an <li> with exactly two direct <span> children and
    nothing else) rather than by class name, since AutoUncle's CSS classes
    are build-hashed and not a stable target across deploys."""
    soup = BeautifulSoup(html, "html.parser")
    merged: dict[str, str] = {}
    for ul in soup.find_all("ul"):
        lis = ul.find_all("li", recursive=False)
        if not lis:
            continue
        pairs: dict[str, str] = {}
        for li in lis:
            children = li.find_all(recursive=False)
            if len(children) != 2 or any(c.name != "span" for c in children):
                pairs = {}
                break
            label = children[0].get_text(strip=True)
            value = children[1].get_text(strip=True)
            if not label:
                pairs = {}
                break
            pairs[label] = value
        merged.update(pairs)
    return merged


def extract_source_listing(html: str) -> dict[str, str] | None:
    """Some AutoUncle listings are aggregated from another portal (e.g.
    AutoScout24) and link back to the original ad instead of showing an
    inline dealer name - extract that source platform + path when present."""
    m = _SOURCE_LISTING_RE.search(html)
    if not m:
        return None
    return {"sourcePlatform": m.group(2), "sourcePath": m.group(1)}


def extract_dealer_name(html: str) -> str | None:
    """Best-effort extraction of an inline dealer/seller display name.
    Empirically, listings aggregated from another portal (see
    extract_source_listing()) don't show one in the DOM at all - only a
    link back to the original ad - so this commonly returns None; that is
    expected, not a bug, and is documented in docs/REFERENCE.md."""
    return None


def extract_description(html: str) -> str | None:
    """Placeholder for a longer free-text description, if AutoUncle ever
    renders one beyond the JSON-LD "description" field. Empirically, no
    such text was found in the rendered DOM as of this writing - always
    returns None. Kept as a real function (not silently omitted) for
    scrape()/flatten_listing() signature stability if that changes."""
    return None


def extract_vin(html: str) -> str | None:
    """Placeholder for a VIN, if AutoUncle ever renders one. Empirically,
    no VIN was found in the rendered DOM as of this writing - always
    returns None. Kept as a real function for the same reason as
    extract_description()."""
    return None


def fetch_detail(listing_id: str, *, domain_cfg: DomainConfig, session: requests.Session) -> dict[str, Any]:
    """Fetch and parse one listing's detail page: the JSON-LD record plus
    every BeautifulSoup supplement above, merged into one dict."""
    url = build_detail_url(domain_cfg, listing_id)
    resp = request_with_retries(session, "GET", url)
    detail = parse_detail_jsonld(resp.text)
    detail.setdefault("id", listing_id)
    detail["url"] = url
    detail["imageUrls"] = extract_gallery_images(resp.text, expected_alt=detail.get("imageCaption"))
    detail["equipment"] = extract_equipment(resp.text)
    detail["dealerName"] = extract_dealer_name(resp.text)
    detail["vin"] = extract_vin(resp.text)
    source = extract_source_listing(resp.text)
    detail["sourcePlatform"] = source["sourcePlatform"] if source else None
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
    labels, gallery, equipment - regardless of whether the search phase
    used JSON-LD or the filtered GraphQL+RSC path."""
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


# ============================================================
# Filtered search (price/mileage/year) via RSC + GraphQL
#
# Server-rendered JSON-LD (see the "Unfiltered search" section above) is
# ONLY emitted for the plain, unfiltered brand/model URL - confirmed
# empirically: any filter, however it's expressed in the URL, gets
# `<meta name="robots" content="noindex, follow">` and no JSON-LD at all.
# So filtering needs a different mechanism entirely. Live reverse-engineering
# (driving the real filter form with `window.fetch` monkey-patched, per
# CONTRIBUTING.md) turned up this actual, and fully working, mechanism -
# note it is NOT what an earlier pass of this investigation assumed (a
# `carSearchUrl` GraphQL query does not exist; that was a misread of an
# unrelated response captured while investigating overlapping requests):
#
#   - Every filter *except* max price is a Rails-style nested query
#     parameter on the plain search URL: `?s[min_price]=X&s[min_km]=Y&
#     s[max_km]=Z&s[min_year]=A&s[max_year]=B`.
#   - Max price is the one exception: AutoUncle canonicalizes it into an
#     SEO-friendly URL path segment, `/mp-unter-{price}-chf` ("mp" = max
#     price, "unter" = German "under"), appended right after the model.
#   - The canonical param ORDER is the query keys sorted alphabetically.
#     Requesting a non-canonical order (or an un-canonicalized combination)
#     doesn't 404 - it 200s with a Next.js "soft" redirect encoded *inside*
#     the response body itself (a `NEXT_REDIRECT;replace;<url>;308;` marker
#     in the RSC stream, not a real HTTP 3xx), pointing at the canonical
#     URL. build_filtered_search_url() below builds the canonical form
#     directly, so that redirect is never actually hit.
#   - Fetching that URL with the header `RSC: 1` returns Next.js's React
#     Server Component "Flight" wire format instead of full HTML - much
#     smaller, and (confirmed across 4 independent real captures spanning
#     single- and multi-filter combinations, 5 to 903 total results, pages
#     1 and 2) reliably contains a `"resultsInfo":"Zeige X - Y von Z
#     Resultate"` string and a `"pagination":{"currentPage":N,
#     "lastPage":true|false,...}` object, plus the ids of every listing on
#     that page as `,"<6-7 digit id>",{"children"`. Rather than writing a
#     general Flight-protocol deserializer (a substantial,
#     framework-internal-format, version-fragile undertaking), this module
#     extracts only those two things via small targeted regexes - see
#     parse_rsc_pagination() / parse_rsc_listing_ids().
#   - Filtered search therefore only ever yields listing ids, not rich
#     summary fields (RSC carries no equivalent of the JSON-LD ItemList
#     item shape) - the detail phase (fetch_detail(), above) is what fills
#     those in, same as the unfiltered path.
#
# The site's own GraphQL endpoint also exposes a `countCars` query with the
# same filter shape (confirmed live) - genuinely useful as a fast, cheap way
# to get an authoritative total before paginating, so it's used for that,
# but it is a supplement, not the mechanism that yields listing data.
# ============================================================

_FILTER_QUERY_PARAM_NAMES = {
    "price_from": "min_price",
    "mileage_from": "min_km",
    "mileage_to": "max_km",
    "year_from": "min_year",
    "year_to": "max_year",
}

_RSC_RESULTS_INFO_RE = re.compile(r'"resultsInfo":"([^"]*)"')
_RSC_PAGINATION_RE = re.compile(r'"pagination":\{"currentPage":(\d+),"lastPage":(true|false)')
_RSC_LISTING_ID_RE = re.compile(r',"(\d{6,7})",\{"children"')
_RSC_TOTAL_FROM_RESULTS_INFO_RE = re.compile(r"von ([\d’']+) ")

GRAPHQL_ENDPOINT_PATH = "/graphql"


def build_filtered_search_url(
    domain_cfg: DomainConfig,
    make_key: str,
    model_key: str,
    *,
    price_from: int | None = None,
    price_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    page: int = 1,
) -> str:
    """Build the canonical (redirect-free) filtered search URL: max price as
    the /mp-unter-{price}-chf path segment, everything else as sorted
    s[...] query params - see the section docstring above for how this was
    derived."""
    path_parts = [domain_cfg.locale, domain_cfg.cars_path, make_key, model_key]
    if price_to is not None:
        path_parts.append(f"mp-unter-{price_to}-chf")
    path = "/".join(quote(seg, safe="") for seg in path_parts)

    filters = {
        "price_from": price_from,
        "mileage_from": mileage_from,
        "mileage_to": mileage_to,
        "year_from": year_from,
        "year_to": year_to,
    }
    query_params: list[tuple[str, str]] = []
    for name, value in filters.items():
        if value is not None:
            query_params.append((f"s[{_FILTER_QUERY_PARAM_NAMES[name]}]", str(value)))
    if page > 1:
        query_params.append(("page", str(page)))
    query_params.sort(key=lambda kv: kv[0])

    url = f"https://{domain_cfg.host}/{path}"
    if query_params:
        query_string = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query_params)
        url += f"?{query_string}"
    return url


def fetch_rsc_page(url: str, *, session: requests.Session) -> str:
    resp = request_with_retries(session, "GET", url, headers={"RSC": "1"})
    return resp.text


def parse_rsc_pagination(rsc_text: str) -> dict[str, Any]:
    """Extract pagination metadata from an RSC Flight response: current
    page, whether it's the last page, the raw "Zeige X - Y von Z" string,
    and the total result count parsed out of that string."""
    results_info_m = _RSC_RESULTS_INFO_RE.search(rsc_text)
    pagination_m = _RSC_PAGINATION_RE.search(rsc_text)
    results_info = results_info_m.group(1) if results_info_m else None

    total: int | None = None
    if results_info:
        total_m = _RSC_TOTAL_FROM_RESULTS_INFO_RE.search(results_info)
        if total_m:
            digits = total_m.group(1).replace("’", "").replace("'", "")
            total = _to_int(digits)

    return {
        "currentPage": _to_int(pagination_m.group(1)) if pagination_m else None,
        "lastPage": (pagination_m.group(2) == "true") if pagination_m else None,
        "resultsInfo": results_info,
        "total": total,
    }


def parse_rsc_listing_ids(rsc_text: str) -> list[str]:
    """Extract the ordered list of listing ids on an RSC Flight page. Raises
    RuntimeError (rather than silently returning an empty page) if the
    pagination metadata claims a non-zero total but no ids were found -
    that combination means the anchor pattern stopped matching (e.g. a
    frontend markup change), not that the page is legitimately empty."""
    ids = _RSC_LISTING_ID_RE.findall(rsc_text)
    if not ids:
        pagination = parse_rsc_pagination(rsc_text)
        if pagination["total"]:
            raise RuntimeError(
                "RSC listing-id pattern matched zero ids on a page reporting "
                f"{pagination['total']} total results; AutoUncle's markup may have changed."
            )
    return ids


def build_car_search_input(
    make_key: str,
    model_key: str,
    *,
    price_from: int | None = None,
    price_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> dict[str, Any]:
    """Build the CarSearchInput GraphQL variable object used by
    count_cars(). Field names confirmed live by monkey-patching
    window.fetch and driving the real filter form one control at a time."""
    car_search: dict[str, Any] = {
        "brand": make_key,
        "carModel": model_key,
        "brandsModels": [{"brand": make_key, "modelName": model_key, "equipmentVariants": None}],
    }
    if price_from is not None:
        car_search["minPrice"] = price_from
    if price_to is not None:
        car_search["maxPrice"] = price_to
    if mileage_from is not None:
        car_search["minKm"] = mileage_from
    if mileage_to is not None:
        car_search["maxKm"] = mileage_to
    if year_from is not None:
        car_search["minYear"] = year_from
    if year_to is not None:
        car_search["maxYear"] = year_to
    return car_search


_COUNT_CARS_QUERY = (
    "query countCars($carSearch: CarSearchInput!) {\n  numberOfCars: countCars(carSearch: $carSearch)\n}"
)


def graphql_request(
    query: str, variables: dict[str, Any], *, domain_cfg: DomainConfig, session: requests.Session
) -> dict[str, Any]:
    url = f"https://{domain_cfg.host}{GRAPHQL_ENDPOINT_PATH}"
    resp = request_with_retries(
        session,
        "POST",
        url,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"},
    )
    data: dict[str, Any] = resp.json()
    if "errors" in data:
        raise ValueError(f"GraphQL request failed: {data['errors']}")
    return data


def count_cars(car_search_input: dict[str, Any], *, domain_cfg: DomainConfig, session: requests.Session) -> int:
    """Fast, authoritative match count for a CarSearchInput, straight from
    AutoUncle's own GraphQL endpoint - the same query its filter UI calls
    on every keystroke. Useful for logging/progress before paginating."""
    data = graphql_request(_COUNT_CARS_QUERY, {"carSearch": car_search_input}, domain_cfg=domain_cfg, session=session)
    count: int = data["data"]["numberOfCars"]
    return count


def search_listings_filtered(
    make_key: str,
    model_key: str,
    domain_cfg: DomainConfig,
    *,
    session: requests.Session,
    price_from: int | None = None,
    price_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    delay: float = 0.4,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Collect every listing id matching a price/mileage/year filter, via
    the RSC mechanism described in the section docstring above. Unlike
    search_listings() (the unfiltered/JSON-LD path), each returned dict only
    has an "id" key - RSC carries no rich per-listing summary fields, so the
    detail phase (visit_all_listings()) is what fills in everything else."""
    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 1
    last_page = False

    while not last_page:
        url = build_filtered_search_url(
            domain_cfg,
            make_key,
            model_key,
            price_from=price_from,
            price_to=price_to,
            mileage_from=mileage_from,
            mileage_to=mileage_to,
            year_from=year_from,
            year_to=year_to,
            page=page,
        )
        rsc_text = fetch_rsc_page(url, session=session)
        pagination = parse_rsc_pagination(rsc_text)
        ids = parse_rsc_listing_ids(rsc_text)

        new_count = 0
        for listing_id in ids:
            if listing_id not in seen_ids:
                seen_ids.add(listing_id)
                listings.append({"id": listing_id})
                new_count += 1

        if verbose:
            logger.info(
                "  page %d: %s (%d new, %d total so far)",
                page,
                pagination["resultsInfo"] or f"{len(ids)} listings",
                new_count,
                len(listings),
            )

        last_page = bool(pagination["lastPage"]) or not ids or new_count == 0
        page += 1
        if not last_page:
            time.sleep(delay)

    return listings


# ============================================================
# Flatten / output
# ============================================================

# Fields worth pulling to the front of the CSV; everything else discovered
# on a listing (which has no fixed schema - AutoUncle can add/omit fields
# per listing) is appended afterwards, sorted alphabetically, so nothing is
# ever silently dropped.
PRIORITY_FIELDS = [
    "id",
    "make",
    "model",
    "year",
    "price",
    "priceCurrency",
    "mileageKm",
    "fuelType",
    "transmission",
    "bodyType",
    "enginePowerPs",
    "enginePowerKw",
    "priceRatingLabel",
    "savingsVsMarketChf",
    "daysOnMarket",
    "addressLocality",
    "addressRegion",
    "postalCode",
    "addressCountry",
    "dealerName",
    "sourcePlatform",
    "url",
]


def _scalarize(value: Any) -> Any:
    """Turn a nested dict/list value into something that fits one CSV cell."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        for key in ("name", "date", "value"):
            if key in value and not isinstance(value[key], (dict, list)):
                return value[key]
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return "; ".join(str(_scalarize(v)) for v in value)
    return str(value)


def flatten_listing(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a listing (any shape produced by search_listings(),
    search_listings_filtered(), or fetch_detail()) into one flat dict
    covering every field present, so nothing is lost. A handful of shapes
    specific to this scraper (not present in the AutoScout24 reference) get
    a dedicated, documented flattening rule instead of the generic one:

    - "priceHistory" (list of {date, price, ...}) -> one semicolon-joined
      cell of "<date>=<price>" entries, e.g. "2026-07-04T...=5500; ...".
    - "otherProperties" (list of {name, value}, from unrecognized
      additionalProperty labels) -> semicolon-joined "<name>=<value>".
    - "imageUrls" (list of gallery URLs) -> semicolon-joined.
    - "equipment" (dict, variable keys per listing) -> "parent_child"
      columns, e.g. "equipment_Klimaanlage", same convention as any other
      nested dict below.

    Everything else follows the reference project's convention: nested
    dicts become "parent_child" columns, lists are semicolon-joined,
    scalars pass through _scalarize()."""
    flat: dict[str, Any] = {}
    for key, value in item.items():
        if key == "priceHistory" and isinstance(value, list):
            flat[key] = "; ".join(f"{h.get('date')}={h.get('price')}" for h in value)
            continue
        if key == "otherProperties" and isinstance(value, list):
            flat[key] = "; ".join(f"{p.get('name')}={p.get('value')}" for p in value)
            continue
        if key == "imageUrls" and isinstance(value, list):
            flat[key] = "; ".join(str(v) for v in value)
            continue
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}_{sub_key}"] = _scalarize(sub_value)
            continue
        flat[key] = _scalarize(value)
    return flat


def order_fieldnames(all_keys: Iterable[str]) -> list[str]:
    ordered = [f for f in PRIORITY_FIELDS if f in all_keys]
    remaining = sorted(k for k in all_keys if k not in ordered)
    return ordered + remaining


def save_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        logger.warning("no rows to write")
        return
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())
    fieldnames = order_fieldnames(all_keys)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)


def save_json(rows: list[dict[str, Any]], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


@dataclass
class ScrapeResult:
    """Everything a scrape() call produced, ready to use in-memory or save to disk."""

    make: str  # resolved brand, e.g. "VW"
    model: str  # resolved model, e.g. "Golf"
    domain: str  # domain that was scraped, e.g. "ch"
    filtered: bool  # True if any price/mileage/year filter was applied
    total_reported: int | None  # total match count the site itself reported, if known
    listings: list[dict[str, Any]] = field(default_factory=list)  # raw parsed records
    rows: list[dict[str, Any]] = field(default_factory=list)  # flattened dicts, one per listing, CSV-ready

    def to_csv(self, path: str) -> None:
        save_csv(self.rows, path)

    def to_json(self, path: str) -> None:
        save_json(self.listings, path)
