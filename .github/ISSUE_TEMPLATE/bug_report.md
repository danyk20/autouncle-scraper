---
name: Bug report
about: Something isn't working
title: ""
labels: bug
assignees: ""
---

**Command or code**
The exact CLI command or Python snippet that triggered the issue.

**Expected behavior**
What you expected to happen.

**Actual behavior**
What happened instead - include the full error/traceback if there is one.

**Environment**
- `autouncle-scraper` version (`autouncle-scraper --version` or `autouncle_scraper.__version__`):
- Python version:
- OS:

**Additional context**
If relevant, the raw response body you got back from AutoUncle (search page HTML,
detail page HTML, or GraphQL response) - especially useful for anything that looks
like a markup/API shape change on AutoUncle's end, since several parts of this
scraper (see docs/REFERENCE.md's "maintenance risk" section) are only as stable
as AutoUncle's own frontend markup.
