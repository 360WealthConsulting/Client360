"""Runtime configuration for the 3CX connector. Every switch defaults to the SAFE value.

A deployment that sets nothing exposes nothing: the endpoints 404, exactly as the MCP interface
does (:mod:`app.mcp.config`). Turning the connector on is an explicit, two-part act — set the
enable flag AND provision a secret — and :func:`connector_enabled` refuses to report "on" unless
both are true, so a half-finished rollout cannot leave an unauthenticated surface live.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "yes", "on"}

#: The two endpoint paths, defined HERE rather than in the route module.
#:
#: Three things need to agree on them: the route that serves them, the XML template that tells
#: 3CX where to call, and ``app.security.middleware.PUBLIC_EXACT``. Putting them in the route
#: would mean the template importing a FastAPI module to learn its own URLs — the integration
#: package depending on the web layer, which is backwards. They are plain strings with no
#: dependencies, so everything can import them from here.
LOOKUP_PATH = "/api/integrations/3cx/lookup"
JOURNAL_PATH = "/api/integrations/3cx/calls"

#: The template Version this server's contract corresponds to. 3CX uses ``<Crm Version>`` to decide
#: whether an uploaded template supersedes the installed one, so bump this whenever the request or
#: response shape changes in a way an installed template would get wrong.
TEMPLATE_VERSION = 1

#: Shown in the 3CX console's CRM list.
TEMPLATE_NAME = "Client360"

#: Minimum length of the integration secret. 3CX stores it as template parameter text and replays
#: it on every call, so it is a long-lived bearer credential and is held to a random-token length
#: rather than a password length.
MIN_SECRET_LENGTH = 32

#: URI schemes the click-to-call link may use. 3CX's desktop app registers ``tel:`` on install and
#: ``callto:`` on some builds; anything else would produce a link no handler answers.
DIAL_SCHEMES = ("tel", "callto")
DEFAULT_DIAL_SCHEME = "tel"


def enabled() -> bool:
    """The master switch alone. Prefer :func:`connector_enabled`, which also requires a secret."""
    return os.getenv("CLIENT360_3CX_ENABLED", "false").strip().lower() in _TRUE


def integration_secret() -> str | None:
    """The DEDICATED 3CX bearer secret, or ``None`` when unusable.

    This is deliberately not a Client360 login, an MCP token, or any staff credential: 3CX stores
    it in template configuration where a PBX administrator can read it, so it must grant nothing
    beyond the two connector endpoints and must be revocable without touching a person's account.

    A secret shorter than :data:`MIN_SECRET_LENGTH` is treated as absent rather than accepted, so a
    placeholder left in an env file switches the connector OFF instead of guarding it weakly.
    """
    value = (os.getenv("CLIENT360_3CX_INTEGRATION_SECRET") or "").strip()
    if len(value) < MIN_SECRET_LENGTH:
        return None
    return value


def connector_enabled() -> bool:
    """True only when the connector is switched on AND a usable secret is configured."""
    return enabled() and integration_secret() is not None


def instance_slug() -> str:
    """Identifies WHICH 3CX system a record came from, for provenance.

    It is part of the ``source_system`` namespace, so two PBXs (a migration, a second office) can
    never collide on a derived call identity, and replacing a PBX does not retroactively
    reinterpret journal rows written by the old one.
    """
    raw = (os.getenv("CLIENT360_3CX_INSTANCE") or "default").strip().lower()
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return cleaned or "default"


def dial_scheme() -> str:
    """URI scheme for the client-profile click-to-call link. Unrecognised values fall back to
    ``tel``, which every 3CX install registers — a typo must not produce a dead link."""
    value = (os.getenv("CLIENT360_3CX_DIAL_SCHEME") or DEFAULT_DIAL_SCHEME).strip().lower()
    return value if value in DIAL_SCHEMES else DEFAULT_DIAL_SCHEME
