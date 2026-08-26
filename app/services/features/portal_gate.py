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

from app.portal.gate import gate as runtime_gate
from app.portal.gate import production_ready
from app.services.features.service import client_can, portal_access_state

# Reachable regardless of feature/portal state (login page, logout, invitation/reset auth endpoints).
_EXEMPT = (
    re.compile(r"^/portal/login$"),
    re.compile(r"^/portal/logout$"),
    re.compile(r"^/api/v1/portal/auth/"),
    re.compile(r"^/api/portal/login$"),
    re.compile(r"^/portal/auth/"),          # external IdP start/callback, pre-session
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
    (re.compile(r"^/api/(v1/)?portal/engagement"), None, "client_timeline"),
    (re.compile(r"^/portal/engagement"), None, "client_timeline"),
)


# FIRM-WIDE runtime gates (``app/portal/gate.py``, governed by the Runtime Engine) → the portal surfaces
# they govern. This layer is distinct from :data:`_RULES` above: ``_RULES`` asks "may THIS client use the
# feature?" (per-client entitlement via ``client_can``), whereas these ask "is the surface switched on for
# the firm at all?". Both must pass. The runtime gate is evaluated FIRST so a firm-wide kill switch cannot
# be bypassed by a per-client entitlement. Ordered most-specific first; ``methods=None`` means any method.
#
# The gate → surface mapping is the one documented in docs/CLIENT_PORTAL_OPERATIONS.md.
_RUNTIME_GATE_RULES: tuple[tuple[re.Pattern[str], frozenset[str] | None, str], ...] = (
    # documents — download must precede the generic vault paths
    (re.compile(r"^/api/(v1/)?portal/documents/\d+/download"), None, "portal.documents.download_enabled"),
    (re.compile(r"^/api/portal/documents$"), frozenset({"POST"}), "portal.documents.upload_enabled"),
    (re.compile(r"^/api/(v1/)?portal/requests/\d+/upload"), None, "portal.documents.upload_enabled"),
    (re.compile(r"^/portal/upload"), None, "portal.documents.upload_enabled"),
    # secure messaging — the WHOLE surface (list, read, send, reply, receipts). The codebase already
    # treats messaging as one surface: every messaging path maps to the single ``secure_messaging``
    # feature in _RULES, with no read/write split to mirror.
    (re.compile(r"^/api/(v1/)?portal/messages"), None, "portal.messaging_enabled"),
    (re.compile(r"^/portal/messages"), None, "portal.messaging_enabled"),
    # forms — client-facing structured submissions (tax intake organizer/questionnaire/letter, and the
    # tax-return decision). Ordinary HTML POSTs that merely happen to be forms (login, logout, profile)
    # are deliberately NOT here.
    (re.compile(r"^/api/v1/portal/tax/"), None, "portal.forms_enabled"),
    (re.compile(r"^/portal/tax-intake"), None, "portal.forms_enabled"),
    (re.compile(r"^/portal/tax-returns"), None, "portal.forms_enabled"),
)


# Phase 1F — portal MUTATIONS whose authorization is enforced in the SERVICE LAYER (principal-scoped /
# ownership check → PermissionError→403/404), not via a portal_gate Core feature. Each was verified to
# reject cross-client / cross-organization access. They are listed EXPLICITLY so a newly-added portal
# mutation that is neither feature-gated nor here is fail-closed denied (see :func:`mutation_is_covered`
# and the middleware). ``methods=None`` means any method. This is a coverage allow-list, NOT a per-client
# decision — the actual scope check still runs inside the delegated service.
_MUTATION_SELF_PROTECTED: tuple[tuple[re.Pattern[str], frozenset[str] | None], ...] = (
    (re.compile(r"^/api/v1/portal/benefits/census/upload$"), frozenset({"POST"})),        # employer_census_upload → org scope (404)
    (re.compile(r"^/api/v1/portal/consents$"), frozenset({"POST"})),                       # record_consent(principal.account_id)
    (re.compile(r"^/api/v1/portal/consents/withdraw$"), frozenset({"POST"})),              # withdraw_consent(principal.account_id)
    (re.compile(r"^/api/v1/portal/appointments/request$"), frozenset({"POST"})),           # request_appointment(principal) → 403
    (re.compile(r"^/api/v1/portal/tasks/\d+/complete$"), frozenset({"POST"})),             # complete_client_task(principal) → 403
    (re.compile(r"^/api/v1/portal/tax/intake/\d+/letter/accept$"), frozenset({"POST"})),   # accept_letter(portal_principal) → 403
    (re.compile(r"^/api/v1/portal/tax/intake/\d+/organizer$"), frozenset({"PUT"})),        # save_organizer(portal_principal) → 403
    (re.compile(r"^/api/v1/portal/tax/intake/\d+/questionnaire$"), frozenset({"PUT"})),    # save_questionnaire(portal_principal) → 403
    (re.compile(r"^/api/v1/portal/tax/intake/\d+/documents/sync$"), frozenset({"POST"})),  # portal_intakes(principal) scope → 403
    (re.compile(r"^/api/v1/portal/tax/returns/\d+/decision$"), frozenset({"POST"})),       # client_decision(portal_principal) → 403
)


def is_exempt(path: str) -> bool:
    return any(rx.match(path) for rx in _EXEMPT)


def feature_for_request(path: str, method: str) -> str | None:
    for rx, methods, feature in _RULES:
        if rx.match(path) and (methods is None or method in methods):
            return feature
    return None


def runtime_gate_for_request(path: str, method: str) -> str | None:
    """The firm-wide runtime gate governing ``(path, method)``, or None when the surface has no
    surface-specific gate (the master ``portal.enabled`` gate still applies to every non-exempt path)."""
    for rx, methods, gate_name in _RUNTIME_GATE_RULES:
        if rx.match(path) and (methods is None or method in methods):
            return gate_name
    return None


def _is_self_protected_mutation(path: str, method: str) -> bool:
    return any(rx.match(path) and (methods is None or method in methods)
               for rx, methods in _MUTATION_SELF_PROTECTED)


def mutation_is_covered(path: str, method: str) -> bool:
    """Fail-closed portal-mutation coverage (Phase 1F). True only when a portal mutation at
    ``(path, method)`` is protected by an APPROVED authorization mechanism:
      * an auth/bootstrap exemption (login / logout / invitation / password-reset), or
      * a mapped Core feature enforced via ``client_can`` (:data:`_RULES`), or
      * an explicitly-listed in-service scoped mutation (:data:`_MUTATION_SELF_PROTECTED`).
    Any other authenticated portal mutation is uncovered and the middleware DENIES it, so authentication
    with ``current_portal`` can never be sufficient on its own. This is a structural coverage check; the
    per-client feature/scope decision still happens in ``evaluate``/the delegated service."""
    return (is_exempt(path)
            or feature_for_request(path, method) is not None
            or _is_self_protected_mutation(path, method))


def evaluate(principal, path: str, method: str) -> tuple[bool, str, str | None]:
    """(allowed, reason, feature). Exempt auth/security paths always pass. Otherwise external access
    requires ``production_ready()`` (portal enabled AND compliance signed off), then the client's
    lifecycle/portal_access state, then the firm-wide surface gate, then the mapped Core feature. A
    generic client route with no specific mapping still requires the master gate (portal_access)."""
    if is_exempt(path):
        return (True, "exempt", None)
    # FIRM-WIDE master gate for EXTERNAL client access: the portal must be switched on AND compliance
    # must have signed off. ``production_ready()`` is the single definition of that condition
    # (``portal.enabled AND portal.production_signed_off``, app/portal/gate.py) — it is called here rather
    # than re-spelled, so the compliance gate cannot drift between the governance report and the request
    # path. Evaluated before anything client-specific, so neither a grant, an entitlement, nor an enabled
    # child gate can serve external client data while either condition is unmet. Fails closed: gate()
    # returns the production-safe default (False) whenever the runtime cannot be resolved.
    if not production_ready():
        return (False, "portal_not_production_ready", "portal_access")
    open_, reason = portal_access_state(principal)
    if not open_:
        return (False, reason, "portal_access")
    # FIRM-WIDE per-surface gate, before the per-client entitlement: a surface switched off for the firm
    # is unreachable even for a client whose grant and Core feature would otherwise allow it.
    gate_name = runtime_gate_for_request(path, method)
    if gate_name is not None and not runtime_gate(gate_name):
        return (False, f"{gate_name}_disabled", feature_for_request(path, method))
    feature = feature_for_request(path, method)
    if feature is None:
        return (True, "portal_access", None)          # master gate already passed
    if not client_can(principal, feature):
        return (False, f"{feature}_denied", feature)
    return (True, feature, None)
