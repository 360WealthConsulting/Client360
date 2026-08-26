"""Canonical external origin for security-sensitive redirect URIs.

``request.url_for()`` builds its base from ``scope["scheme"]`` and the inbound **Host header**
(Starlette: ``f"{scheme}://{host_header}{path}"``). Neither is trustworthy behind a proxy:

* **Host** is never validated. The application installs no ``TrustedHostMiddleware`` and nothing reads
  the documented ``ALLOWED_HOSTS`` name, so a request carrying ``Host: attacker.example`` makes
  ``url_for()`` return ``https://attacker.example/portal/auth/callback``.
* **Scheme** is corrected from ``X-Forwarded-Proto`` only when the connection arrives from an address
  uvicorn trusts (``FORWARDED_ALLOW_IPS``, default ``127.0.0.1``). Same-host Caddy satisfies that; a
  proxy on any other address does not, and the scheme silently stays ``http``.

An OAuth ``redirect_uri`` is matched exactly by the identity provider and is repeated in the token
exchange, so either defect breaks sign-in (``AADSTS50011``) or, worse, sends a provider a redirect URI
an attacker chose. This module derives that URI from an explicitly configured origin instead.

The route is selected by **server code passing a route name**, never a caller-supplied URL, so this
introduces no open redirect: the path always comes from the application's own reverse routing.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

#: Canonical external origin of this deployment, e.g. ``https://app.example.com``. Scheme + host
#: (+ optional port) only — no path, query, fragment, or credentials.
PUBLIC_BASE_URL = "PUBLIC_BASE_URL"


class CanonicalOriginError(RuntimeError):
    """The canonical origin is missing or malformed. Always fail closed — never fall back to the
    Host header, which is exactly the untrusted input this exists to avoid."""


def _is_production() -> bool:
    # Read at call time (not import) so tests exercise both postures, matching app/config.py.
    return os.getenv("CLIENT360_ENVIRONMENT", "development").strip().lower() == "production"


def validate_origin(raw: str, *, production: bool | None = None) -> str:
    """Return the normalized ``scheme://host[:port]`` form, or raise :class:`CanonicalOriginError`.

    Rejects anything that is not a plain origin: non-HTTP schemes (``javascript:``, ``file:``),
    embedded credentials, a path, a query, or a fragment. A trailing ``/`` is accepted and normalized
    away so callers can concatenate a route path without doubling the separator."""
    production = _is_production() if production is None else production
    value = (raw or "").strip()
    if not value:
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} is empty")

    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        # Covers javascript:, file:, data:, and scheme-relative "//host" (scheme == "").
        raise CanonicalOriginError(
            f"{PUBLIC_BASE_URL} must use http or https, not {parts.scheme or '<none>'!r}")
    if production and parts.scheme != "https":
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} must use https in production")
    if not parts.hostname:
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} has no hostname")
    if parts.username or parts.password:
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} must not embed credentials")
    if parts.query:
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} must not contain a query string")
    if parts.fragment:
        raise CanonicalOriginError(f"{PUBLIC_BASE_URL} must not contain a fragment")
    if parts.path not in ("", "/"):
        # A path here would silently prefix every callback and break exact redirect-URI matching.
        raise CanonicalOriginError(
            f"{PUBLIC_BASE_URL} must be an origin only, with no path (got {parts.path!r})")
    return f"{parts.scheme}://{parts.netloc}"


def canonical_origin() -> str | None:
    """The validated canonical origin, or ``None`` when unconfigured.

    Raises :class:`CanonicalOriginError` when configured but malformed — a broken value must never
    degrade into "use the Host header"."""
    raw = os.getenv(PUBLIC_BASE_URL, "").strip()
    if not raw:
        return None
    return validate_origin(raw)


def canonical_origin_status() -> tuple[bool, str | None]:
    """``(configured_and_valid, error_message)`` for startup checks. Never raises, never echoes the
    configured value beyond the message the validator produced."""
    try:
        return (canonical_origin() is not None), None
    except CanonicalOriginError as exc:
        return False, str(exc)


def external_url(request, route_name: str) -> str:
    """The externally reachable URL of ``route_name``, built on the canonical origin.

    ``route_name`` is a route name chosen by server code — never anything a caller supplies — so the
    path comes from the application's own routing table and cannot be redirected elsewhere.

    Outside production an unconfigured origin falls back to ``request.url_for()``, keeping local and
    test runs on ``http://localhost``. In production that fallback would mean trusting the Host
    header for an OAuth redirect URI, so it fails closed instead."""
    origin = canonical_origin()                      # raises if configured-but-malformed
    routed = str(request.url_for(route_name))
    if origin is None:
        if _is_production():
            raise CanonicalOriginError(
                f"{PUBLIC_BASE_URL} is required in production to build {route_name!r}; refusing to "
                "derive an authentication redirect URI from the inbound Host header")
        return routed
    return f"{origin}{urlsplit(routed).path}"
