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
     JSON-LD block; nothing else does (see point 3). This module also
     fetches the same page's RSC response (see point 3.b) to fill in the
     search-card fields JSON-LD never carries at all - model/trim
     ("modelVariant"), price-rating label, savings vs. market, price-change
     percent, days listed, source marketplace, full address, and the image
     gallery (extract_search_card_supplements()) - one extra request per
     page, merged in without ever overwriting a field JSON-LD already set.

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
          deserializer, this module extracts pagination metadata and the
          ordered list of listing ids on the page via small targeted
          regexes (fetch_rsc_page(), parse_rsc_pagination(),
          parse_rsc_listing_ids()) - AND each search-result card's own
          embedded JSON object (extract_search_card_supplements(),
          parse_search_card_object()), found by locating a balanced JSON
          object literal around each "carId" key rather than a full Flight
          deserializer. That card object is surprisingly rich - price,
          mileage, year, doors, body type, model/trim, price rating,
          savings vs. market, price-change percent, days listed, source
          marketplace, full address, image gallery - everything visible on
          the search page itself. It does NOT carry fuel type,
          transmission, engine power, or CO2/consumption figures, since
          AutoUncle's search cards simply don't render those - the detail
          phase (point 4) is what fills those in, for both search paths.
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

import argparse
import csv
import json
import logging
import re
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from bs4 import BeautifulSoup

__version__ = "0.4.1"


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
        if "charset" not in resp.headers.get("Content-Type", "").lower():
            # requests defaults .encoding to ISO-8859-1 (per RFC 2616) for any
            # response that doesn't declare a charset - AutoUncle's RSC/Flight
            # responses (Content-Type: text/x-component) are real UTF-8 but
            # fall into exactly that gap, which silently mangles non-ASCII
            # text (e.g. "Graubünden" -> "GraubÃ¼nden"). Confirmed live that
            # every AutoUncle response is actually UTF-8 regardless of
            # whether it says so.
            resp.encoding = "utf-8"
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
    body type) topped up with the search-card fields JSON-LD never carries
    at all - model/trim ("modelVariant"), price-rating label, savings vs.
    market, price-change percent, days listed, source marketplace, full
    address, and the image gallery - by also fetching the same page's RSC
    payload and merging in whatever parse_vehicle_jsonld() left as None
    (see extract_search_card_supplements()). That's one extra request per
    page (not per listing), so this remains far cheaper than a detail-page
    visit while still surfacing everything visible on the search page
    itself without opening a single ad."""
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

        time.sleep(delay)
        card_supplements = extract_search_card_supplements(fetch_rsc_page(url, session=session))

        new_count = 0
        for element in items:
            vehicle = parse_vehicle_jsonld(element.get("item", {}))
            listing_id = vehicle.get("id")
            if listing_id and listing_id not in seen_ids:
                seen_ids.add(listing_id)
                vehicle["url"] = f"https://{domain_cfg.host}/{domain_cfg.locale}/d/{listing_id}"
                for key, value in card_supplements.get(listing_id, {}).items():
                    if value is not None and vehicle.get(key) is None:
                        vehicle[key] = value
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
    and "firstSeenAt"/"lastUpdatedAt" timestamps derived from it, when
    present.

    "firstSeenAt"/"lastUpdatedAt" are the earliest/latest dates in
    "priceHistory", NOT the Dataset object's own "datePublished"/
    "dateModified" fields - confirmed live that those two are regenerated
    on every request (they come back ~equal to "request time" regardless of
    the listing, verified by re-fetching the same listing seconds apart and
    seeing a stable value, then comparing several different listings and
    finding all of them within seconds of each other despite wildly
    different price-history depth - i.e. they describe when the page/JSON-LD
    was rendered, not anything about the listing itself). The earliest
    priceHistory entry is the best available proxy AutoUncle exposes for
    "when this listing started being tracked" - there is no dedicated field
    for it anywhere, and no timestamp of any kind in the summary/
    search-result shape (confirmed: neither the unfiltered JSON-LD ItemList
    items nor the filtered-search RSC payload carry one). That also means
    true "newest listing" ordering can only be computed after visiting a
    candidate's detail page - `scrape()`'s `max_results` deliberately does
    NOT do this (it would defeat the point of a fast, capped search); see
    its docstring for the speed/completeness trade-off it makes instead."""
    graph = _graph_items(html)
    vehicle = find_vehicle(graph)
    if vehicle is None:
        raise ValueError("No Vehicle JSON-LD object found on detail page")

    parsed = parse_vehicle_jsonld(vehicle)

    dataset = find_dataset(graph)
    if dataset is not None:
        history = _price_history_from_dataset(dataset)
        parsed["priceHistory"] = history
        parsed["datasetLicense"] = dataset.get("license")
        parsed["datasetIsAccessibleForFree"] = dataset.get("isAccessibleForFree")
        dates = [h["date"] for h in history if h.get("date")]
        parsed["firstSeenAt"] = min(dates) if dates else None
        parsed["lastUpdatedAt"] = max(dates) if dates else None
    else:
        parsed["priceHistory"] = []
        parsed["firstSeenAt"] = None
        parsed["lastUpdatedAt"] = None

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


_LISTING_GONE_STATUS_CODES = {404, 410}


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
    used JSON-LD or the filtered GraphQL+RSC path.

    A listing can legitimately disappear (sold, ad removed) between the
    search phase finding its id and this function visiting it - confirmed
    live, as a 410 on a listing that was in the search results moments
    earlier. That's normal for live inventory, not a scraper bug, so a
    404/410 for one listing is logged and skipped rather than aborting the
    whole batch; any other error (a persistent network failure, an
    unexpected 4xx that might indicate rate-limiting) still propagates,
    since silently swallowing those could mask a real problem. This means
    the returned list can be shorter than `listing_ids` - callers that need
    an exact count (e.g. scrape()'s `total_reported`) should capture it
    from the search phase, before calling this function."""
    ids = list(listing_ids)
    total = len(ids)
    visited: list[dict[str, Any]] = []
    for i, listing_id in enumerate(ids, 1):
        try:
            detail = fetch_detail(listing_id, domain_cfg=domain_cfg, session=session)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in _LISTING_GONE_STATUS_CODES:
                raise
            logger.warning("  listing %s is gone (HTTP %d); skipping", listing_id, status)
        else:
            visited.append(detail)
        if verbose and (i % 10 == 0 or i == total):
            logger.info("  visited %d/%d listings (id=%s)", i, total, listing_id)
        if i < total:
            time.sleep(delay)
    return visited


# ============================================================
# Filtered search (price/mileage/year/...) via RSC + GraphQL
#
# Server-rendered JSON-LD (see the "Unfiltered search" section above) is
# ONLY emitted for the plain, unfiltered brand/model URL - confirmed
# empirically: any filter, however it's expressed in the URL, gets
# `<meta name="robots" content="noindex, follow">` and no JSON-LD at all.
# So filtering needs a different mechanism entirely. Live reverse-engineering
# (driving the real filter form and probing the GraphQL endpoint directly,
# per CONTRIBUTING.md) turned up this actual, and fully working, mechanism -
# note it is NOT what an earlier pass of this investigation assumed (a
# `carSearchUrl` GraphQL query does not exist; that was a misread of an
# unrelated response captured while investigating overlapping requests):
#
#   - Every filter is a Rails-style nested query parameter on the plain
#     search URL: `?s[min_price]=X&s[body_types][]=SUV&s[has_gps]=true&...`
#     - the snake_case key is just the CarSearchInput GraphQL field name
#     (see build_car_search_input()) converted via _camel_to_snake();
#     array-valued filters (bodyTypes, fuelTypes, colors, ...) repeat the
#     same `s[key][]=` key once per value.
#   - AutoUncle canonicalizes *some* single-value filters into SEO-friendly
#     URL path segments instead of leaving them as query params - confirmed
#     for max price (`/mp-unter-{price}-chf`), a single fuel type
#     (`/f-{fuel}`), and a single body type (`/b-{bodytype}`) - and sorts
#     query keys alphabetically. Requesting the non-canonical form (plain
#     query params, unsorted, whatever) doesn't 404 - it 200s with a
#     Next.js "soft" redirect encoded *inside* the response body itself (a
#     `NEXT_REDIRECT;replace;<url>;<code>;` marker in the RSC stream, not a
#     real HTTP 3xx) pointing at the canonical URL. Rather than replicating
#     AutoUncle's canonicalization rules by hand (there could be more of
#     them for filters not yet tried), fetch_rsc_page() below just follows
#     that embedded redirect itself - so build_filtered_search_url() always
#     emits the plain query-param form for every filter, uniformly, and
#     lets the server decide the canonical URL.
#   - Fetching a search URL with the header `RSC: 1` returns Next.js's React
#     Server Component "Flight" wire format instead of full HTML - much
#     smaller, and (confirmed across many real captures spanning single-
#     and multi-filter combinations across price/km/year/body type/fuel
#     type/doors/colors/seller kind/equipment, 0 to 2000+ total results,
#     multiple pages) reliably contains a `"resultsInfo":"Zeige X - Y von Z
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
#
# CarSearchInput field names were confirmed empirically, one at a time, by
# calling countCars() directly with a candidate field name/value and reading
# whether the server accepted it or returned "Field is not defined on
# CarSearchInput" (GraphQL introspection is disabled in production, so this
# trial-and-error was the only way). Confirmed fields:
#
#   minPrice/maxPrice, minKm/maxKm, minYear/maxYear (CHF/km/year ranges)
#   bodyTypes: list[str]      - see BODY_TYPES
#   fuelTypes: list[str]      - see FUEL_TYPES
#   colors: list[str]         - see COLORS
#   doors: int                - exact match, not a range (no minDoors/maxDoors)
#   sellerKind: str           - see SELLER_KINDS (singular - one at a time, not a list)
#   euroEmissionClass: int    - 1-6; data coverage for CH listings seemed sparse
#                               when this was tested, so don't be surprised by
#                               a 0 count even for a common class like 6
#   isOneOwner: bool
#   notLeasing: bool          - config's own default for this is `true`
#   notDamaged: bool          - config's own default for this is `false`
#   minElectricDriveRange/maxElectricDriveRange (km)
#   minBatteryCapacity/maxBatteryCapacity (kWh)
#   minEnergyConsumption/maxEnergyConsumption (kWh/100km)
#   maxFuelEconomy (L/100km)  - no minFuelEconomy; confirmed one-directional
#   every string in EQUIPMENT_OPTIONS is its OWN top-level boolean field,
#     e.g. hasGps: true, has_4wd: true - not a single "equipment: [...]" list
#
# Tried and NOT found under any reasonable name (seats, transmission/gear,
# region, priceRating, emissionLabel, daysOnSale, horsepower) - these may
# not be supported by CarSearchInput at all, or use a name not yet guessed.
# ============================================================

BODY_TYPES = ["Hatchback", "Sedan", "Stationcar", "MPV", "SUV", "Cabriolet", "Coupe"]
FUEL_TYPES = ["El", "El_Hybrid", "Benzin", "Diesel", "CNG_Hybrid", "LPG_Hybrid", "Ethanol_Benzin", "Hydrogen"]
COLORS = [
    "White", "Black", "Silver", "Grey", "Blue", "Red", "Green", "Brown",
    "Orange", "Turquoise", "Gold", "Yellow", "Purple", "Beige", "Pink",
]  # fmt: skip
SELLER_KINDS = ["Dealer", "Private"]
EQUIPMENT_OPTIONS = [
    "hasGps", "hasAircondition", "hasTowBar", "has_4wd", "hasParking", "hasPilot", "hasAlloy",
    "hasElWindows", "hasElSeats", "hasHeadupDisplay", "hasIsofix", "hasClimateControl", "hasRainSensor",
    "hasSunroof", "hasSeatHeat", "hasFullLeather", "hasBluetooth", "hasAppleCarPlay", "hasAndroidAuto",
    "hasHeatedSteeringWheel", "hasParkingCamera", "hasBlindSpotDetection", "hasLaneKeepingAssist",
    "hasLedHeadlights", "has_360_camera", "hasEmergencyBreaking", "hasKeylessGo", "hasUsbPort",
    "hasDistanceControl", "hasHeatPump",
]  # fmt: skip

# CarSearchInput keys that identify the brand/model - already expressed in
# the URL path itself, so build_filtered_search_url() skips them rather
# than also emitting them as (redundant, and untested) query params.
_CAR_SEARCH_INPUT_PATH_KEYS = frozenset({"brand", "carModel", "brandsModels"})

_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    """CarSearchInput's GraphQL field names (camelCase, e.g. "sellerKind")
    to the s[...] query param convention (snake_case, e.g. "seller_kind") -
    confirmed to just be a straightforward conversion for every field
    tried. A few fields (e.g. "has_4wd") are already snake_case in
    CarSearchInput itself; this is idempotent for those (no uppercase
    letters to convert)."""
    return _CAMEL_CASE_BOUNDARY_RE.sub("_", name).lower()


_RSC_RESULTS_INFO_RE = re.compile(r'"resultsInfo":"([^"]*)"')
_RSC_PAGINATION_RE = re.compile(r'"pagination":\{"currentPage":(\d+),"lastPage":(true|false)')
_RSC_LISTING_ID_RE = re.compile(r',"(\d{6,7})",\{"children"')
_RSC_TOTAL_FROM_RESULTS_INFO_RE = re.compile(r"von ([\d’']+) ")
_RSC_REDIRECT_RE = re.compile(r'"digest":"NEXT_REDIRECT;replace;([^;]+);(\d+);"')

GRAPHQL_ENDPOINT_PATH = "/graphql"


def build_filtered_search_url(
    domain_cfg: DomainConfig,
    make_key: str,
    model_key: str,
    car_search_input: dict[str, Any],
    *,
    page: int = 1,
) -> str:
    """Build a filtered search URL from a CarSearchInput-shaped dict (see
    build_car_search_input()): every filter becomes a sorted s[...] query
    param, uniformly - even ones AutoUncle happens to canonicalize into an
    SEO slug (confirmed for max price, single fuel type, single body type).
    fetch_rsc_page() follows the server's own embedded redirect to whatever
    canonical form it wants, so this function doesn't try to replicate
    that logic - see the section docstring above."""
    path = "/".join(quote(seg, safe="") for seg in (domain_cfg.locale, domain_cfg.cars_path, make_key, model_key))

    query_params: list[tuple[str, str]] = []
    for key, value in car_search_input.items():
        if key in _CAR_SEARCH_INPUT_PATH_KEYS or value is None:
            continue
        snake_key = _camel_to_snake(key)
        if isinstance(value, (list, tuple, set)):
            query_params.extend((f"s[{snake_key}][]", str(v)) for v in value)
        elif isinstance(value, bool):
            query_params.append((f"s[{snake_key}]", "true" if value else "false"))
        else:
            query_params.append((f"s[{snake_key}]", str(value)))
    if page > 1:
        query_params.append(("page", str(page)))
    query_params.sort(key=lambda kv: kv[0])

    url = f"https://{domain_cfg.host}/{path}"
    if query_params:
        query_string = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in query_params)
        url += f"?{query_string}"
    return url


def fetch_rsc_page(url: str, *, session: requests.Session, _redirects_followed: int = 0) -> str:
    """Fetch a search-result URL's RSC ("Flight") payload. Follows an
    embedded "soft" redirect (see the section docstring above) to whatever
    canonical URL AutoUncle wants, bounded to avoid a loop - real
    canonicalization has never been observed to chain more than one hop,
    so hitting this bound at all likely means something unexpected is
    going on, not a legitimate long redirect chain."""
    resp = request_with_retries(session, "GET", url, headers={"RSC": "1"})
    text = resp.text
    redirect_m = _RSC_REDIRECT_RE.search(text)
    if redirect_m and _redirects_followed < 5:
        target = redirect_m.group(1)
        target_url = target if target.startswith("http") else f"https://{urlsplit(url).netloc}{target}"
        logger.debug("  RSC redirect -> %s", target_url)
        return fetch_rsc_page(target_url, session=session, _redirects_followed=_redirects_followed + 1)
    return text


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


_CARD_CAR_ID_KEY_RE = re.compile(r'"carId"\s*:\s*"(\d+)"')
_LOCATION_RE = re.compile(r"^(\d{4,5})\s+(.+?),\s*(.+)$")


def _find_matching_brace(text: str, start: int) -> int | None:
    """Return the index of the '}' that closes the '{' at `start`, treating
    text inside JSON string literals as opaque (so a brace character in,
    say, a listing title never throws off the depth count)."""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def _json_object_containing(text: str, key_index: int) -> dict[str, Any] | None:
    """Find and parse the smallest well-formed JSON object literal in `text`
    that has a "carId" key at or after `key_index` - used to pull one
    search-result card's data out of a much larger RSC/Flight response
    without writing a full Flight deserializer. Walks backward through
    candidate opening braces until one both closes past `key_index` and
    parses as a JSON object containing "carId" directly."""
    search_from = key_index
    while True:
        start = text.rfind("{", 0, search_from)
        if start == -1:
            return None
        end = _find_matching_brace(text, start)
        if end is not None and end >= key_index:
            try:
                obj = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict) and "carId" in obj:
                return obj
        search_from = start


def _parse_chf_amount(value: Any) -> int | None:
    """ "CHF\xa026’217" (or any other digit-grouping/whitespace
    AutoUncle's frontend renders) -> 26217."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"[^\d]", "", value)
    return int(digits) if digits else None


def _parse_location_string(value: str) -> dict[str, Any]:
    """ "7546 Scuol, Graubünden" -> postalCode/addressLocality/addressRegion.
    Returns {} (rather than guessing) if the string doesn't match the
    expected "<postal code> <locality>, <region>" shape."""
    m = _LOCATION_RE.match(value.strip())
    if not m:
        return {}
    return {"postalCode": m.group(1), "addressLocality": m.group(2), "addressRegion": m.group(3)}


def parse_search_card_object(obj: dict[str, Any]) -> dict[str, Any]:
    """Normalize one search-result card's RSC JSON object (see
    extract_search_card_supplements()) into the same field-name vocabulary
    parse_vehicle_jsonld() uses, so unfiltered JSON-LD items and filtered
    RSC cards are as directly comparable as AutoUncle's own data allows."""
    # "location"/"price"/"priceChange" are top-level fields on the card
    # object itself - modalPriceHistoryValues is a separate, parallel
    # sub-object for the price-history popup with its own (mostly
    # duplicate) "estimatedPrice"/"youSave" pair, and no "location" at all.
    modal = obj.get("modalPriceHistoryValues") or {}
    location = obj.get("location")
    address = _parse_location_string(location) if isinstance(location, str) else {}
    image_urls = obj.get("imageUrls") or []
    title = obj.get("title")
    price_rating_label = title.rsplit(" | ", 1)[1] if isinstance(title, str) and " | " in title else None

    return {
        "id": obj.get("carId"),
        "make": obj.get("brand"),
        "model": obj.get("carModel"),
        "modelVariant": obj.get("subtitle") or None,
        "year": _to_int(obj.get("year")),
        "mileageKm": _to_int(obj.get("km")),
        "numberOfDoors": _to_int(obj.get("doors")),
        "bodyType": obj.get("body"),
        "price": _parse_chf_amount(obj.get("price")),
        "priceCurrency": (obj.get("countryCurrencyCode") or "").upper() or None,
        "priceRatingLabel": price_rating_label,
        "savingsVsMarketChf": obj.get("youSaveDifference"),
        "priceChangePercent": obj.get("priceChange"),
        "estimatedMarketPriceChf": _parse_chf_amount(modal.get("estimatedPrice")),
        "daysOnMarket": obj.get("laytime"),
        "addressLocality": address.get("addressLocality"),
        "addressRegion": address.get("addressRegion"),
        "postalCode": address.get("postalCode"),
        "imageUrl": image_urls[0] if image_urls else None,
        "imageUrls": image_urls or None,
        "imageCaption": obj.get("imageAltText"),
        "sourcePlatform": obj.get("sourceName"),
        "sourcePath": obj.get("outgoingPath"),
    }


def extract_search_card_supplements(rsc_text: str) -> dict[str, dict[str, Any]]:
    """Extract every search-result card AutoUncle's frontend renders in an
    RSC/Flight response - present for both unfiltered and filtered search
    pages when fetched with header {"RSC": "1"} (see fetch_rsc_page()), but
    NOT in the JSON-LD ItemList, and NOT in the plain HTML an unfiltered
    request without that header gets. This is where fields like the
    model/trim line under the title ("P90D (Free Supercharging)"),
    AutoUncle's own price-rating label, savings vs. market, price-change
    percent, days listed, the aggregated source marketplace + outgoing
    link, full seller address, and the listing's own image gallery all
    live - all visible on the search page itself, without opening a single
    ad. Returns {listing_id: parse_search_card_object(...)}; a card this
    can't confidently parse is just absent from the result rather than
    raising, since this is a supplement to JSON-LD, not the primary
    source for the unfiltered path (it IS the primary source for the
    filtered path - see search_listings_filtered())."""
    supplements: dict[str, dict[str, Any]] = {}
    for m in _CARD_CAR_ID_KEY_RE.finditer(rsc_text):
        car_id = m.group(1)
        if car_id in supplements:
            continue
        obj = _json_object_containing(rsc_text, m.start())
        if obj is None:
            continue
        supplements[car_id] = parse_search_card_object(obj)
    return supplements


def _validate_choice(name: str, value: str, allowed: list[str]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} {value!r} is not one of {allowed}")


def _validate_choices(name: str, values: Iterable[str], allowed: list[str]) -> None:
    for value in values:
        if value not in allowed:
            raise ValueError(f"{name} {value!r} is not one of {allowed}")


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
    body_types: Iterable[str] | None = None,
    fuel_types: Iterable[str] | None = None,
    colors: Iterable[str] | None = None,
    doors: int | None = None,
    seller_kind: str | None = None,
    one_owner: bool | None = None,
    equipment: Iterable[str] | None = None,
    extra_filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the CarSearchInput GraphQL variable object used by both
    count_cars() and (via _camel_to_snake()) build_filtered_search_url().
    Field names confirmed live, one at a time, by calling countCars()
    directly with a candidate name/value and reading whether the server
    accepted it or said "Field is not defined on CarSearchInput" (GraphQL
    introspection is disabled in production) - see the "Filtered search"
    section docstring above for the confirmed field list, and
    docs/REFERENCE.md for the full table.

    body_types/fuel_types/colors/seller_kind/equipment are validated
    against AutoUncle's own known vocabulary (BODY_TYPES, FUEL_TYPES,
    COLORS, SELLER_KINDS, EQUIPMENT_OPTIONS) and raise ValueError listing
    the valid options otherwise, rather than silently sending a value the
    server would just ignore or 0-result on.

    extra_filters is a passthrough dict (merged in as-is, CarSearchInput
    field names) for any confirmed field without its own parameter here -
    euroEmissionClass, notLeasing, notDamaged, minElectricDriveRange/
    maxElectricDriveRange, minBatteryCapacity/maxBatteryCapacity,
    minEnergyConsumption/maxEnergyConsumption, maxFuelEconomy - or any
    field this module doesn't know about yet."""
    if body_types is not None:
        body_types = list(body_types)
        _validate_choices("body_types", body_types, BODY_TYPES)
    if fuel_types is not None:
        fuel_types = list(fuel_types)
        _validate_choices("fuel_types", fuel_types, FUEL_TYPES)
    if colors is not None:
        colors = list(colors)
        _validate_choices("colors", colors, COLORS)
    if seller_kind is not None:
        _validate_choice("seller_kind", seller_kind, SELLER_KINDS)
    if equipment is not None:
        equipment = list(equipment)
        _validate_choices("equipment", equipment, EQUIPMENT_OPTIONS)

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
    if body_types:
        car_search["bodyTypes"] = body_types
    if fuel_types:
        car_search["fuelTypes"] = fuel_types
    if colors:
        car_search["colors"] = colors
    if doors is not None:
        car_search["doors"] = doors
    if seller_kind is not None:
        car_search["sellerKind"] = seller_kind
    if one_owner is not None:
        car_search["isOneOwner"] = one_owner
    for flag in equipment or ():
        car_search[flag] = True
    if extra_filters:
        car_search.update(extra_filters)
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
    car_search_input: dict[str, Any],
    *,
    session: requests.Session,
    delay: float = 0.4,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Collect every listing matching a CarSearchInput-shaped filter dict
    (see build_car_search_input()), via the RSC mechanism described in the
    section docstring above. Unlike search_listings() (the unfiltered/
    JSON-LD path), there's no schema.org JSON-LD here at all - AutoUncle
    suppresses it on any filtered page - so each returned dict is built
    entirely from the RSC response's own per-card JSON objects (see
    parse_search_card_object()/extract_search_card_supplements()): id,
    make/model/modelVariant, year, mileage, doors, body type, price, price
    rating, savings vs. market, price-change percent, days listed, source
    marketplace, full address, and the image gallery. Notably absent
    compared to the unfiltered/JSON-LD path: fuel type, transmission,
    engine power, CO2/fuel-consumption figures - AutoUncle's search cards
    simply don't render those, so they stay None until a detail visit. A
    listing id this couldn't find/parse a card object for falls back to a
    bare `{"id": ...}`, same as the old behavior, rather than dropping it."""
    listings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    page = 1
    last_page = False

    while not last_page:
        url = build_filtered_search_url(domain_cfg, make_key, model_key, car_search_input, page=page)
        rsc_text = fetch_rsc_page(url, session=session)
        pagination = parse_rsc_pagination(rsc_text)
        ids = parse_rsc_listing_ids(rsc_text)
        card_supplements = extract_search_card_supplements(rsc_text)

        new_count = 0
        for listing_id in ids:
            if listing_id not in seen_ids:
                seen_ids.add(listing_id)
                listings.append(card_supplements.get(listing_id) or {"id": listing_id})
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
    "modelVariant",
    "year",
    "firstSeenAt",
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
    "estimatedMarketPriceChf",
    "priceChangePercent",
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


# ============================================================
# Public scrape() API
# ============================================================

SUPPORTED_CATEGORIES = ("car",)


def scrape(
    make: str,
    model: str,
    *,
    domain: str = DEFAULT_DOMAIN,
    category: str = "car",
    detail: bool = True,
    price_from: int | None = None,
    price_to: int | None = None,
    mileage_from: int | None = None,
    mileage_to: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    body_types: Iterable[str] | None = None,
    fuel_types: Iterable[str] | None = None,
    colors: Iterable[str] | None = None,
    doors: int | None = None,
    seller_kind: str | None = None,
    one_owner: bool | None = None,
    equipment: Iterable[str] | None = None,
    extra_filters: dict[str, Any] | None = None,
    max_results: int | None = None,
    delay: float = 0.4,
    verbose: bool = True,
    session: requests.Session | None = None,
) -> ScrapeResult:
    """Search autouncle.<domain> for a make/model and return the results in memory.

    Same call shape as the AutoScout24 scraper's scrape(), so switching
    `from autoscout24_scraper import scrape` to
    `from autouncle_scraper import scrape` needs no other code changes for
    a caller that only uses these parameters.

    Args:
        make: Brand name, e.g. "VW" (case-insensitive, substring matching supported).
        model: Model name, e.g. "Golf" (case-insensitive, substring matching supported).
        domain: Country domain to scrape, e.g. "ch" (default and, as of this
            writing, only implemented value - see the module docstring).
        category: "car" (default) - the only vehicle category AutoUncle
            surfaces in primary navigation as of this writing. Kept as a
            parameter (rather than dropped) for signature parity with the
            AutoScout24 scraper; any other value raises ValueError.
        detail: If True (default), visit every listing's detail page one by
            one for what only a detail visit can provide (fuel type,
            transmission, engine power, CO2/consumption figures, full price
            history, equipment - see the module docstring); every field the
            search phase already found (including RSC-search-card-only
            fields like "modelVariant") is merged back in afterward, so
            nothing level 1 found is ever lost by also fetching detail. If
            False, both unfiltered and filtered searches keep everything
            visible on the search page itself (see "modelVariant" etc.
            above) but skip the fields only a detail visit provides.
        price_from/price_to: Optional price range in CHF (inclusive).
        mileage_from/mileage_to: Optional mileage range in km (inclusive).
        year_from/year_to: Optional first-registration year range (inclusive).
        body_types: Optional list of body types, e.g. ["SUV", "Cabriolet"] - see BODY_TYPES.
        fuel_types: Optional list of fuel types, e.g. ["Diesel", "El"] - see FUEL_TYPES.
        colors: Optional list of colors, e.g. ["Black", "White"] - see COLORS.
        doors: Optional exact door count, e.g. 5 (not a range - AutoUncle doesn't
            expose min/max doors, only an exact match).
        seller_kind: Optional "Dealer" or "Private" - see SELLER_KINDS.
        one_owner: Optional bool - only-one-previous-owner listings if True.
        equipment: Optional list of equipment flags a listing must have, e.g.
            ["hasGps", "hasAppleCarPlay"] - see EQUIPMENT_OPTIONS for all ~30
            recognized flags. Combines with AND (a listing must have all of them).
        extra_filters: Optional passthrough dict of any other confirmed
            CarSearchInput field not given its own parameter above -
            euroEmissionClass, notLeasing, notDamaged, minElectricDriveRange/
            maxElectricDriveRange, minBatteryCapacity/maxBatteryCapacity,
            minEnergyConsumption/maxEnergyConsumption, maxFuelEconomy - see the
            "Filtered search" section docstring and docs/REFERENCE.md for the
            confirmed field list. Merged directly into the CarSearchInput sent
            to AutoUncle, so use the exact GraphQL field names (camelCase).
        max_results: If given, only the first N matching listings (in
            whatever order AutoUncle's own search returns them) are ever
            opened - this is what makes the parameter fast: the rest are
            never fetched at all, unlike a plain client-side truncation
            after the fact. Trade-off: AutoUncle's default search order is
            not confirmed to be newest-first (empirically it isn't a
            simple date sort), so this is "the first N AutoUncle's search
            gives back", not a guaranteed "the N most recently posted"
            - if you need the latter, call `search_listings()`/
            `search_listings_filtered()` yourself, sort by whatever
            criteria you need at level 2, and skip `max_results` entirely.
        delay: Seconds to wait between requests.
        verbose: If True, emit progress via the "autouncle_scraper" logger at INFO level.
        session: Optional requests.Session to reuse (e.g. across repeated
            calls). A new one is created if not given.

    Returns:
        A ScrapeResult with `.listings` (raw parsed records) and `.rows`
        (flattened dicts, one per listing), sorted by price ascending where
        known. If `max_results` was given, both are additionally capped to
        the first `max_results` matching listings before that sort runs -
        see `max_results` above for what "first" means here.
    """
    if category not in SUPPORTED_CATEGORIES:
        raise ValueError(f"Unsupported category {category!r}. Only {SUPPORTED_CATEGORIES} are implemented.")

    for lo_name, hi_name, lo, hi in (
        ("price_from", "price_to", price_from, price_to),
        ("mileage_from", "mileage_to", mileage_from, mileage_to),
        ("year_from", "year_to", year_from, year_to),
    ):
        if lo is not None and hi is not None and lo > hi:
            raise ValueError(f"{lo_name} ({lo}) cannot be greater than {hi_name} ({hi})")

    if max_results is not None and max_results <= 0:
        raise ValueError(f"max_results ({max_results}) must be a positive integer")

    # Validated here (before any network call), same as the range checks
    # above - build_car_search_input() (called later) validates these too,
    # but only after make/model have already been resolved over the network.
    if body_types is not None:
        _validate_choices("body_types", body_types, BODY_TYPES)
    if fuel_types is not None:
        _validate_choices("fuel_types", fuel_types, FUEL_TYPES)
    if colors is not None:
        _validate_choices("colors", colors, COLORS)
    if seller_kind is not None:
        _validate_choice("seller_kind", seller_kind, SELLER_KINDS)
    if equipment is not None:
        _validate_choices("equipment", equipment, EQUIPMENT_OPTIONS)

    domain_cfg = get_domain_config(domain)
    session = session or make_session()

    if verbose:
        logger.info("Resolving make %r ...", make)
    config = fetch_search_form_config(domain, session=session)
    make_key = resolve_make_key(make, config)
    if verbose:
        logger.info("  -> make %r", make_key)

    if verbose:
        logger.info("Resolving model %r for make %r ...", model, make_key)
    model_key = resolve_model_key(make_key, model, config)
    if verbose:
        logger.info("  -> model %r", model_key)

    car_search_input = build_car_search_input(
        make_key,
        model_key,
        price_from=price_from,
        price_to=price_to,
        mileage_from=mileage_from,
        mileage_to=mileage_to,
        year_from=year_from,
        year_to=year_to,
        body_types=body_types,
        fuel_types=fuel_types,
        colors=colors,
        doors=doors,
        seller_kind=seller_kind,
        one_owner=one_owner,
        equipment=equipment,
        extra_filters=extra_filters,
    )
    filtered = any(k not in _CAR_SEARCH_INPUT_PATH_KEYS for k in car_search_input)

    if verbose:
        active_filters = []
        if price_from is not None or price_to is not None:
            active_filters.append(f"price {price_from or 0}-{price_to or '∞'} CHF")
        if mileage_from is not None or mileage_to is not None:
            active_filters.append(f"mileage {mileage_from or 0}-{mileage_to or '∞'} km")
        if year_from is not None or year_to is not None:
            active_filters.append(f"year {year_from or '…'}-{year_to or '…'}")
        for label, value in (
            ("body types", body_types),
            ("fuel types", fuel_types),
            ("colors", colors),
            ("equipment", equipment),
        ):
            if value:
                active_filters.append(f"{label} {list(value)}")
        if doors is not None:
            active_filters.append(f"doors {doors}")
        if seller_kind is not None:
            active_filters.append(f"seller {seller_kind}")
        if one_owner is not None:
            active_filters.append(f"one owner {one_owner}")
        if extra_filters:
            active_filters.append(f"extra {extra_filters}")
        filter_note = f" [filters: {', '.join(active_filters)}]" if active_filters else ""
        logger.info("Fetching listings for %s %s (autouncle.%s)%s ...", make_key, model_key, domain, filter_note)

    if filtered:
        listings = search_listings_filtered(
            make_key,
            model_key,
            domain_cfg,
            car_search_input,
            session=session,
            delay=delay,
            verbose=verbose,
        )
    else:
        listings = search_listings(make_key, model_key, domain_cfg, session=session, delay=delay, verbose=verbose)
    total_reported = len(listings)

    if max_results is not None and len(listings) > max_results:
        if verbose:
            logger.info(
                "Opening only the first %d of %d matching listings (max_results=%d) - the rest are never "
                "fetched, so the search stays fast ...",
                max_results,
                len(listings),
                max_results,
            )
        listings = listings[:max_results]

    if detail:
        if verbose:
            logger.info("Visiting each of %d listings one by one to extract full details ...", len(listings))
        level1_by_id = {item["id"]: item for item in listings if item.get("id")}
        listings = visit_all_listings(
            list(level1_by_id), domain_cfg=domain_cfg, session=session, delay=delay, verbose=verbose
        )
        # The detail page doesn't carry every field the search-result card
        # does (e.g. "modelVariant"/"P90D (Free Supercharging)",
        # "priceChangePercent", "estimatedMarketPriceChf", "sourcePath" -
        # AutoUncle's detail page just never renders those) - fill those
        # gaps from the level-1 record, without ever overwriting a field
        # the detail page's own (more authoritative, e.g. more precise
        # address) data already set.
        for item in listings:
            for key, value in level1_by_id.get(item.get("id"), {}).items():
                if value is not None and item.get(key) is None:
                    item[key] = value
    elif filtered:
        logger.info(
            "detail=False on a filtered search still fills in the search-card fields (price, mileage, "
            "year, address, images, price rating, ...) but not fuel type, transmission, engine power, "
            "CO2/consumption figures, or price history - those need a detail visit."
        )

    rows = [flatten_listing(item) for item in listings]
    rows.sort(key=lambda r: (r.get("price") in (None, ""), r.get("price")))

    return ScrapeResult(
        make=make_key,
        model=model_key,
        domain=domain,
        filtered=filtered,
        total_reported=total_reported,
        listings=listings,
        rows=rows,
    )


# ============================================================
# CLI
# ============================================================


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape autouncle.ch listings for a given make/model.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--make", required=True, help="Brand name, e.g. 'VW'")
    parser.add_argument("--model", required=True, help="Model name, e.g. 'Golf'")
    parser.add_argument(
        "--domain",
        default=DEFAULT_DOMAIN,
        help=f"Country domain to scrape, matching autouncle.<domain> (default: {DEFAULT_DOMAIN!r}). "
        f"Only 'ch' is implemented as of this writing; see the module docstring.",
    )
    parser.add_argument(
        "--category", default="car", choices=list(SUPPORTED_CATEGORIES), help="Vehicle category (default: car)"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output file base name (without extension). Defaults to '<make>_<model>' in the current directory.",
    )
    parser.add_argument(
        "--no-detail",
        action="store_true",
        help="Skip visiting each listing's detail page; keep only the summary fields available from the search "
        "phase (unfiltered searches only - filtered searches always need detail for anything beyond an id).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Only open this many listings (the first N AutoUncle's search returns) and skip the rest - "
        "keeps large searches fast. Not guaranteed to be the N most recently posted, since AutoUncle's "
        "default search order isn't a date sort.",
    )
    parser.add_argument("--delay", type=float, default=0.4, help="Delay in seconds between requests.")
    parser.add_argument("--price-from", type=int, default=None, help="Minimum price in CHF (inclusive).")
    parser.add_argument("--price-to", type=int, default=None, help="Maximum price in CHF (inclusive).")
    parser.add_argument("--mileage-from", type=int, default=None, help="Minimum mileage in km (inclusive).")
    parser.add_argument("--mileage-to", type=int, default=None, help="Maximum mileage in km (inclusive).")
    parser.add_argument("--year-from", type=int, default=None, help="Earliest first-registration year (inclusive).")
    parser.add_argument("--year-to", type=int, default=None, help="Latest first-registration year (inclusive).")
    parser.add_argument(
        "--body-types",
        type=_csv_list,
        default=None,
        help=f"Comma-separated body types, e.g. 'SUV,Coupe'. One of: {', '.join(BODY_TYPES)}",
    )
    parser.add_argument(
        "--fuel-types",
        type=_csv_list,
        default=None,
        help=f"Comma-separated fuel types, e.g. 'Diesel,El'. One of: {', '.join(FUEL_TYPES)}",
    )
    parser.add_argument(
        "--colors",
        type=_csv_list,
        default=None,
        help=f"Comma-separated colors, e.g. 'Black,White'. One of: {', '.join(COLORS)}",
    )
    parser.add_argument("--doors", type=int, default=None, help="Exact door count, e.g. 5 (not a range).")
    parser.add_argument(
        "--seller-kind",
        default=None,
        choices=SELLER_KINDS,
        help="Restrict to dealer or private sellers.",
    )
    parser.add_argument(
        "--one-owner", action="store_true", help="Only listings with a single previous owner (isOneOwner)."
    )
    parser.add_argument(
        "--equipment",
        type=_csv_list,
        default=None,
        help="Comma-separated equipment flags a listing must all have, e.g. 'hasGps,hasAppleCarPlay'. "
        "See docs/REFERENCE.md for the full list of ~30 recognized flags.",
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug-level detail, including every HTTP request made."
    )
    verbosity.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress progress output; only warnings/errors are shown."
    )
    return parser


def _configure_cli_logging(*, verbose: bool, quiet: bool) -> None:
    """Set up console logging for CLI use: progress (INFO, or DEBUG with
    -v) goes to stdout, warnings/errors (-q still shows these) go to
    stderr. Only main() calls this - plain library use of scrape() never
    touches logging config, since that would be rude to whatever
    application imported it."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    plain = logging.Formatter("%(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.addFilter(lambda record: record.levelno < logging.WARNING)
    stdout_handler.setFormatter(plain)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(plain)

    logger.handlers.clear()
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.setLevel(level)
    logger.propagate = False


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Parses argv (defaults to sys.argv[1:]), scrapes, and
    writes CSV + JSON files. Returns 0 on success; lets exceptions propagate
    (see run_cli() for the error-handling / exit-code wrapper used by the
    __main__ guard below)."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _configure_cli_logging(verbose=args.verbose, quiet=args.quiet)

    result = scrape(
        args.make,
        args.model,
        domain=args.domain,
        category=args.category,
        detail=not args.no_detail,
        price_from=args.price_from,
        price_to=args.price_to,
        mileage_from=args.mileage_from,
        mileage_to=args.mileage_to,
        year_from=args.year_from,
        year_to=args.year_to,
        body_types=args.body_types,
        fuel_types=args.fuel_types,
        colors=args.colors,
        doors=args.doors,
        seller_kind=args.seller_kind,
        one_owner=args.one_owner or None,  # --one-owner is store_true (default False); False means "unset" here
        equipment=args.equipment,
        max_results=args.max_results,
        delay=args.delay,
        verbose=True,
    )

    out_base = args.out or f"{result.make}_{result.model}".lower().replace(" ", "-")
    csv_path = f"{out_base}.csv"
    json_path = f"{out_base}.json"
    result.to_csv(csv_path)
    result.to_json(json_path)

    logger.info("\nDone. %d unique listings found.", len(result.rows))
    logger.info("  CSV:  %s", csv_path)
    logger.info("  JSON: %s", json_path)
    return 0


def run_cli(argv: list[str] | None = None) -> int:
    """Run main() and translate exceptions into (message, exit code) the way
    the command line expects. Factored out from the __main__ guard so it can
    be unit-tested directly without spawning a subprocess."""
    try:
        return main(argv) or 0
    except ValueError as exc:
        logger.error("Error: %s", exc)
        return 1
    except requests.RequestException as exc:
        logger.error("Network error talking to autouncle.ch: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("\nInterrupted.")
        return 130


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in test_e2e.py
    sys.exit(run_cli())
