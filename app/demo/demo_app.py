"""Demo ASGI entrypoint (DEMO-ONLY).

Run with `uvicorn app.demo.demo_app:app`. This is the ONLY place the demo login
router is mounted; production runs `app.main:app` and never imports this module,
so no demo code or login path exists in production.

At import it fails fast unless it is pointed at a `*_demo` database in a
non-production environment, and it extends the auth public-path allowlist *in
this process only* so the demo login page is reachable.
"""
from app.demo.safety import assert_demo_database

# Fail fast — refuse to start against a non-demo DB or in production.
assert_demo_database()

# Process-local: make the demo login reachable without an existing session.
# Production never runs this module, so its allowlist is untouched.
from app.security import middleware as _middleware
_middleware.PUBLIC_EXACT = _middleware.PUBLIC_EXACT | frozenset({"/demo", "/demo/login"})

from app.main import app  # noqa: E402  (import after the guard + allowlist patch)
from app.demo.demo_auth import router as demo_router  # noqa: E402

app.include_router(demo_router)
