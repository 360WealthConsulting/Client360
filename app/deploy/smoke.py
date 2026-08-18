"""Deployment smoke test — service-level route/auth/readiness checks + optional live HTTP checks.

The service-level checks run without a browser and are what the deployment tests assert:
  * route registration (the operational surface is wired up),
  * auth-gate membership (public paths public, gated paths gated), and
  * DB-backed **readiness** — an in-process ``/readiness`` probe that performs a real (smallest-safe)
    database round-trip. This is the difference between "the process started" and "the process can
    actually serve": a deployment whose database is unreachable now FAILS smoke instead of passing on
    a static ``{"status":"ok"}``.

When a ``--url`` is given, live HTTP checks confirm the running deployment answers the expected status
codes (200 for public liveness/login/static; ``/readiness`` must be 200 — a 503 is a readiness failure
and fails smoke; 401/302/303 for gated staff routes). It never logs in and never fabricates dashboard
data, and it never surfaces connection strings, credentials, SQL, or error detail.
"""
from __future__ import annotations

import urllib.error
import urllib.request

# Routes that MUST be registered (the working operational surface).
REQUIRED_ROUTES = (
    "/health", "/readiness", "/", "/home", "/api/home/summary",
    "/work", "/work/{source_domain}/{source_id}", "/client/{person_id}",
    "/api/vault/documents", "/api/vault/documents/{document_id}/download",
    "/portal/login", "/api/portal/login", "/api/portal/dashboard",
)
# Paths that must be publicly reachable (no staff/portal session).
EXPECTED_PUBLIC = ("/health", "/readiness", "/portal/login", "/api/portal/login")
# Staff paths that must NOT be public (require authentication).
EXPECTED_GATED = ("/home", "/api/home/summary", "/work")


def route_registration() -> dict:
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    missing = [r for r in REQUIRED_ROUTES if r not in paths]
    return {"ok": not missing, "missing": missing, "total_routes": len(app.routes)}


def auth_gating() -> dict:
    from app.security.middleware import PUBLIC_EXACT
    public_ok = [p for p in EXPECTED_PUBLIC if p not in PUBLIC_EXACT]
    gated_leaks = [p for p in EXPECTED_GATED if p in PUBLIC_EXACT]
    return {"ok": not public_ok and not gated_leaks,
            "public_missing": public_ok, "gated_leaking": gated_leaks}


def readiness_check() -> dict:
    """In-process DB-backed readiness probe: verify the app can actually use its database, not merely that
    routes are registered. Reuses the real ``/readiness`` contract (a single ``SELECT 1`` round-trip plus
    the migration/config/vault gates), so smoke and the live probe agree on what "ready" means. Returns
    ``ok=False`` (never raises) when the service is not ready, so an unreachable database fails the gate.
    Surfaces only the coarse ready/not-ready status and per-check labels — never config values,
    credentials, SQL, or error detail."""
    import json
    try:
        from app.routes.ops import readiness
        response = readiness()
        body = json.loads(response.body)
        return {"ok": response.status_code == 200, "status_code": response.status_code,
                "status": body.get("status"),
                "database": body.get("checks", {}).get("database")}
    except Exception:  # noqa: BLE001 — a probe must never raise into the smoke gate (and never leak detail)
        return {"ok": False, "status_code": None, "status": "error", "database": "error"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do NOT follow redirects — a gated staff route answers 302/303 (redirect to login), and the smoke
    check must observe that status, not the 200 of the login page it would redirect to."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None      # returning None makes urllib raise HTTPError with the real 3xx code


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_status(url, timeout=10):
    req = urllib.request.Request(url, method="GET")
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 — operator-supplied URL
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code      # includes 302/303 for gated routes (redirects are no longer followed)
    except Exception:  # noqa: BLE001
        return None


def http_smoke(base_url: str) -> dict:
    base = base_url.rstrip("/")
    checks = {
        "/health": (_http_status(f"{base}/health"), {200}),                     # liveness (DB-independent)
        "/readiness": (_http_status(f"{base}/readiness"), {200}),               # must be READY; 503 fails smoke
        "/home": (_http_status(f"{base}/home"), {401, 302, 303}),               # gated
        "/work": (_http_status(f"{base}/work"), {401, 302, 303}),               # gated
        "/portal/login": (_http_status(f"{base}/portal/login"), {200}),         # public
        "/static/css/main.css": (_http_status(f"{base}/static/css/main.css"), {200}),
    }
    results = {path: {"status": got, "ok": got in expected} for path, (got, expected) in checks.items()}
    return {"ok": all(r["ok"] for r in results.values()), "results": results}


def run(url: str | None = None) -> dict:
    routes = route_registration()
    gating = auth_gating()
    readiness = readiness_check()
    out = {"route_registration": routes, "auth_gating": gating, "readiness": readiness,
           "ok": routes["ok"] and gating["ok"] and readiness["ok"]}
    if url:
        http = http_smoke(url)
        out["http"] = http
        out["ok"] = out["ok"] and http["ok"]
    return out


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Client360 deployment smoke test.")
    parser.add_argument("--url", help="base URL of the running deployment (adds live HTTP checks)")
    args = parser.parse_args(argv)
    result = run(args.url)
    print("route_registration:", "OK" if result["route_registration"]["ok"] else
          f"MISSING {result['route_registration']['missing']}")
    print("auth_gating:", "OK" if result["auth_gating"]["ok"] else result["auth_gating"])
    rc = result["readiness"]
    print("readiness:", "OK" if rc["ok"] else
          f"NOT READY (status={rc['status']}, database={rc['database']})")
    if "http" in result:
        for path, r in result["http"]["results"].items():
            print(f"  {path:28} {r['status']}  {'OK' if r['ok'] else 'FAIL'}")
    print("RESULT:", "OK" if result["ok"] else "FAILED")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
