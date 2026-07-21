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

import logging
import time
from dataclasses import dataclass
from typing import Any

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
