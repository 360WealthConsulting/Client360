# Browser End-to-End Testing (Playwright)

The unit suite (`tests/`) calls route handlers and services directly. It cannot
exercise the layer a user actually touches: rendered HTML, static assets,
redirects, and client-side behaviour. The `e2e/` suite closes that gap with a
real Chromium browser driven by Playwright.

## Layout

- `e2e/conftest.py` — boots the real app under `uvicorn` against the disposable
  test database and yields its base URL (`live_server` fixture). Skips the whole
  directory if Playwright is not installed.
- `e2e/test_smoke.py` — unauthenticated browser smoke tests.
- `.github/workflows/e2e.yml` — an **advisory** CI workflow (its own workflow, so
  it is not a required status check until promoted via branch protection).

`e2e/` sits outside `tests/` (pytest `testpaths=["tests"]`) so the browser suite
never runs during the unit run.

## Running locally

```bash
pip install -r requirements-e2e.txt
playwright install --with-deps chromium
scripts/test.sh reset            # migrated, disposable DB
python -m pytest e2e/ -q
```

## Current coverage (unauthenticated)

- `/health` serves.
- Static stylesheet loads.
- A protected record (`/people/1`) redirects an unauthenticated visitor to login.
- The home route redirects an unauthenticated visitor to login.

## Authenticated E2E — blocked, needs a product decision

The high-value flows (search → profile → notes → activity → communications →
tasks) require an authenticated browser session. The application authenticates
through an **external identity provider** and exposes **no test-login path**, so
a browser cannot obtain a session in CI without one of:

1. a **test-only authentication mechanism** (e.g. a seeded-session bootstrap
   gated to non-production environments), or
2. a **configured test IdP** in the E2E environment.

Both are product/security decisions and are intentionally **not** implemented
here. Until one is chosen, authenticated E2E coverage cannot be added. See the
Engineering Backlog item **"Authenticated E2E: test-authentication strategy."**

## Promotion to a gate

Once the advisory workflow has run green in CI on a few PRs, add
`Client360 E2E (advisory)` to the branch-protection required checks to make it a
merge gate.
