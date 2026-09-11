"""The 3CX connector's authentication gate — default deny, and nothing but this secret.

Two layers, and each can only ever REMOVE access:

  1. The connector must be switched on, with a usable secret   (:func:`config.connector_enabled`)
  2. The request must present that exact secret as a Bearer    (:func:`authenticate`)

THERE IS NO THIRD LAYER, AND THAT IS THE DESIGN. A 3CX PBX is a machine with no Client360 user
behind it, so there is no principal to carry capabilities and no session to resolve. Authority is
therefore bounded by what the two connector endpoints can do at all — read one person's name and
profile URL for a number the PBX already has, and append one call record — rather than by a role.
Widening that authority means adding an endpoint, which is a visible code change, not a
configuration change.

WHY NOT A STAFF LOGIN. 3CX stores template parameters as readable configuration, so whatever is
put there is disclosed to every PBX administrator and replayed on every call. A normal Client360
credential there would hand the whole application to the phone system; this secret is dedicated,
grants only these two endpoints, and is revoked by changing one environment variable.
"""
from __future__ import annotations

import hmac

from app.integrations.threecx import config
from app.integrations.threecx.errors import ThreeCxUnauthenticated, ThreeCxUnavailable


def bearer_token(header_value: str | None) -> str | None:
    """The credential out of an ``Authorization: Bearer <secret>`` header, or ``None``.

    Case-insensitive on the scheme, as RFC 6750 requires. Anything that is not a well-formed Bearer
    header yields ``None``, which the caller turns into a 401 — never a partial credential.
    """
    if not header_value:
        return None
    parts = header_value.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def authenticate(header_value: str | None) -> None:
    """Admit one request, or raise. Returns ``None`` on success — there is no identity to return.

    Raises :class:`ThreeCxUnavailable` when the connector is off (the route 404s) and
    :class:`ThreeCxUnauthenticated` when the secret is absent or wrong (the route 401s). The
    comparison is constant-time, so a caller cannot learn the secret one byte at a time by timing
    repeated attempts.
    """
    expected = config.integration_secret()
    if not config.enabled() or expected is None:
        raise ThreeCxUnavailable("3CX connector is disabled")
    presented = bearer_token(header_value)
    if presented is None or not hmac.compare_digest(presented, expected):
        raise ThreeCxUnauthenticated("Authentication required")
