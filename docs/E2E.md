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

## Coverage

**Unauthenticated (`e2e/test_smoke.py`)**
- `/health` serves; static stylesheet loads.
- A protected record (`/people/1`) and the home route redirect an unauthenticated
  visitor to login.

**Authenticated (`e2e/test_authenticated.py`)** — signs in through the
development-only provider (below): dashboard, people directory, households,
search (finds a seeded client), client profile, notes, tasks, and the
communication quick actions.

## Development-only authentication provider

The high-value flows need an authenticated browser session, and the app
authenticates through an external IdP with no test-login path. Rather than a test
IdP, `app/routes/dev_auth.py` provides a **development-only** sign-in at
`/dev-auth/login` that issues a real session through the same
`authenticate_claims` + `create_session` path (no RBAC bypass), for the
deterministic personas in `app/demo/credentials.py`.

It is **impossible to enable in production**, guarded twice: `app.main` mounts the
router only when `dev_auth_enabled()` is true, and that function returns false
whenever `CLIENT360_ENVIRONMENT=production` regardless of the toggle; every handler
also re-asserts the guard. Enable it with:

```
CLIENT360_ENVIRONMENT=development CLIENT360_DEV_AUTH=1
```

The `live_server` fixture sets these for the E2E server automatically.

## Promotion to a gate

Once the advisory workflow has run green in CI on a few PRs, add
`Client360 E2E (advisory)` to the branch-protection required checks to make it a
merge gate.
