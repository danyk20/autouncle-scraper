## What & why

<!-- What does this change, and why? -->

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `mypy autouncle_scraper.py` passes
- [ ] `pytest` passes with coverage at or above the configured threshold
- [ ] New/changed behavior has a test (mocked via `responses`, not a real network call)
- [ ] If this touches request/response handling against the real site, `pytest -m e2e --no-cov` was run locally
- [ ] `docs/REFERENCE.md` updated if the public API, field schema, or a documented mechanism changed
- [ ] `CHANGELOG.md` has a new entry under `Unreleased`
