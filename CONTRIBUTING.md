# Contributing

Thanks for considering a contribution — human or AI agent, both welcome (see
the [License](README.md#license) section of the README).

## Dev setup

```bash
git clone https://github.com/danyk20/autouncle-scraper.git
cd autouncle-scraper
pipenv install --dev
```

## Before opening a PR

```bash
pipenv run ruff check .                 # lint
pipenv run ruff format .                # format
pipenv run mypy autouncle_scraper.py    # type-check
pipenv run pytest                       # unit tests, must stay at 100% coverage
```

If your change touches request/response handling against the real site, also
run the end-to-end suite (real network calls, a few seconds):

```bash
pipenv run pytest -m e2e --no-cov
```

## Expectations

- **Every behavior change needs a test.** The unit suite mocks all HTTP (via
  `responses`) and enforces 100% coverage — a change without a test will
  fail CI on that basis alone.
- **Keep `verbose`/logging output backward compatible** unless the PR is
  specifically about changing it — other code (and the e2e/CLI tests)
  depends on the current message wording.
- If AutoUncle changes its markup or API shape, prefer fixing the affected
  function directly over adding a workaround — the module docstring in
  `autouncle_scraper.py` documents the current mechanisms, and
  `docs/REFERENCE.md`'s "maintenance risk" section lists exactly which bits
  are most likely to need attention first.
- Keep the change minimal and focused; this is a small single-file utility
  by design, same as the AutoScout24 scraper it mirrors.

## If the site's markup changes

Several parts of this scraper (the RSC listing-id regex, the BeautifulSoup
equipment/gallery selectors, the exact `CarSearchInput` GraphQL field list)
were derived empirically by watching what AutoUncle's own frontend does,
not from any published API contract — so they can break if AutoUncle ships
a redesign. To re-derive them:

1. Open the relevant page in a real browser (search page with the filter
   form, or a detail page).
2. Monkey-patch `window.fetch` before interacting, so you can see exactly
   what the page's own JavaScript sends and receives:
   ```js
   const _f = window.fetch;
   window.fetch = function(...args) {
     console.log('FETCH', args[0], args[1] && args[1].body);
     return _f.apply(this, args);
   };
   ```
3. Toggle **one** filter control at a time (price, then km, then year, ...)
   and read the logged GraphQL request body to (re)learn field names.
4. For the RSC listing-id pattern: fetch a search URL with header
   `{"RSC": "1"}`, save the raw response text, and diff a few different
   real captures (different brand/model/filter combinations) before
   changing the regex — see `tests/fixtures/rsc_*.txt` for the shape these
   should take.
5. For BeautifulSoup selectors: prefer stable `data-*` attributes or
   structural shape (e.g. "an `<li>` with exactly two `<span>` children")
   over CSS class names, which are build-hashed and change on every deploy.
6. **For confirming/discovering `CarSearchInput` fields specifically**, this
   doesn't require a browser at all — GraphQL introspection is disabled in
   production, but a candidate field name/value can just be tried directly
   against the live endpoint:
   ```bash
   curl -s -X POST https://www.autouncle.ch/graphql \
     -H 'Content-Type: application/json' \
     -d '{"query":"query countCars($carSearch: CarSearchInput!) { countCars(carSearch: $carSearch) }",
          "variables":{"carSearch":{"brand":"VW","carModel":"Golf VIII","candidateField":123}}}'
   ```
   A wrong guess returns a clear `"Field is not defined on CarSearchInput"`
   error; a right one just returns a count. This is how every field in
   `docs/REFERENCE.md`'s `CarSearchInput` table was confirmed - much faster
   than driving the UI when you already have a guess to try (informed by
   `/api/v4/car_search_form/config`'s own key names, e.g. `bodyTypes` from
   its `bodyTypes` list, `sellerKind` from `sellerKinds`, each
   `equipmentOptions` string as its own top-level boolean field).
7. Whatever you confirm via `countCars()`, also check it works through
   `search_listings_filtered()`/`build_filtered_search_url()` (the RSC path
   that actually returns listings, not just a count) — a field CarSearchInput
   accepts isn't guaranteed to also be a working `s[...]` query param under
   the same snake_case name, though every field tried so far has been.

## Questions / bug reports

Open a GitHub issue using the bug report template — include the exact
command you ran and, if relevant, the raw HTML/JSON response you got back.

## Releasing (maintainer only)

Publishing to PyPI is automated via `.github/workflows/release.yml` using
PyPI Trusted Publishing (no API tokens stored anywhere) — pushing a tag is
the only manual step. **This requires a one-time setup that hasn't been
done for this project yet**: a Trusted Publisher registered on both
pypi.org and test.pypi.org for this repository, and matching GitHub
Environments named `pypi`/`testpypi`. Until that's done, `release.yml` will
build successfully but fail at the publish step.

1. Bump `__version__` in `autouncle_scraper.py`.
2. Add a new entry at the top of `CHANGELOG.md` (Keep a Changelog format).
3. Commit those two changes, then tag and push:
   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main
   git push origin vX.Y.Z
   ```
4. The release workflow verifies `__version__` matches the tag (fails fast
   if they disagree), builds, publishes to TestPyPI, then to real PyPI.
   Watch the Actions tab.
5. To dry-run the pipeline without a real release, push a pre-release tag
   instead (e.g. `vX.Y.Z-rc1`) — it publishes to TestPyPI only and never
   reaches real PyPI, since the version/tag check and the real-PyPI job
   both key off an exact `vX.Y.Z` tag.
