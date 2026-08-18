"""Centralized server-side feature enforcement for the client portal.

Mirrors the existing staff ``RULES`` (regex → capability) convention in ``app.security.middleware``: a
single, ordered map of portal request patterns → the client feature required, enforced in ONE place
(the auth middleware) rather than scattered across ~35 route signatures. Every client-facing /portal
request passes ``evaluate`` before its route runs, so a disabled feature cannot be bypassed by a direct
URL / API call.

Two layers:
  * MASTER portal gate (kill switch) — lifecycle status + the ``portal_access`` feature (per-client
    override and firm-wide DISABLED). If closed, ALL normal client functionality is denied.
  * Per-feature gate — the specific Core feature a route maps to (messaging/documents/upload/etc.).

Auth / security / logout flows are exempt so a user can always be safely informed, sign out, or reset.
The employer/benefits/insurance subsystem routes are intentionally NOT mapped here — they are the
existing organization-scoped surface with their own permission model, not one of the catalog Core
client features (documented in the enforcement inventory).
"""
from __future__ import annotations

import re

from app.services.features.service import client_can, portal_access_state

# Reachable regardless of feature/portal state (login page, logout, invitation/reset auth endpoints).
_EXEMPT = (
    re.compile(r"^/portal/login$"),
    re.compile(r"^/portal/logout$"),
    re.compile(r"^/api/v1/portal/auth/"),
    re.compile(r"^/api/portal/login$"),
)

# (pattern, methods_or_None, feature). First match wins; ordered most-specific first. ``methods=None``
# means any method. Only individual-client Core features are mapped (business is per-org, handled at the
# route/service level; employer/benefits/insurance are out of scope — see module docstring).
_RULES: tuple[tuple[re.Pattern[str], frozenset[str] | None, str], ...] = (
    (re.compile(r"^/api/(v1/)?portal/documents/\d+/download"), None, "document_download"),
    (re.compile(r"^/api/portal/documents$"), frozenset({"POST"}), "document_upload"),
    (re.compile(r"^/api/(v1/)?portal/requests/\d+/upload"), None, "document_upload"),
    (re.compile(r"^/portal/upload"), None, "document_upload"),
    (re.compile(r"^/api/(v1/)?portal/documents$"), None, "document_vault"),
    (re.compile(r"^/portal/documents$"), None, "document_vault"),
    (re.compile(r"^/api/(v1/)?portal/messages"), None, "secure_messaging"),
    (re.compile(r"^/portal/messages"), None, "secure_messaging"),
    (re.compile(r"^/api/(v1/)?portal/requests$"), None, "client_requests"),
    (re.compile(r"^/portal/requests$"), None, "client_requests"),
    (re.compile(r"^/api/portal/profile$"), frozenset({"PATCH", "POST", "PUT"}), "profile_editing"),
    (re.compile(r"^/portal/profile$"), frozenset({"POST"}), "profile_editing"),
    (re.compile(r"^/api/(v1/)?portal/notifications"), None, "portal_notifications"),
    (re.compile(r"^/portal/notifications"), None, "portal_notifications"),
    (re.compile(r"^/portal/billing/invoices/\d+"), None, "invoice_view"),
    (re.compile(r"^/portal/billing"), None, "billing"),
)


def is_exempt(path: str) -> bool:
    return any(rx.match(path) for rx in _EXEMPT)


def feature_for_request(path: str, method: str) -> str | None:
    for rx, methods, feature in _RULES:
        if rx.match(path) and (methods is None or method in methods):
            return feature
    return None


def evaluate(principal, path: str, method: str) -> tuple[bool, str, str | None]:
    """(allowed, reason, feature). Exempt auth/security paths always pass. Otherwise the master portal
    gate is enforced for every client route, then the mapped Core feature (if any). A generic client
    route with no specific mapping still requires the master gate (portal_access)."""
    if is_exempt(path):
        return (True, "exempt", None)
    open_, reason = portal_access_state(principal)
    if not open_:
        return (False, reason, "portal_access")
    feature = feature_for_request(path, method)
    if feature is None:
        return (True, "portal_access", None)          # master gate already passed
    if not client_can(principal, feature):
        return (False, f"{feature}_denied", feature)
    return (True, feature, None)
