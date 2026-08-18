"""360Plus Client Feature & Access Control — the feature catalog (code registry).

The catalog is CODE, not data: a new 360Plus capability is added by declaring a ``Feature`` here — no
schema migration per feature. Each feature belongs to a product tier and carries a conservative
firm-wide default state, so unfinished features stay closed until an administrator explicitly enables
them firm-wide.

Four distinct concepts (see ``docs``/service for precedence):
  * PRODUCTS       — 360Plus Core / Wealth / Business (Core is the baseline every client has).
  * FEATURES       — individually controllable client-facing capabilities, each tied to one product.
  * FIRM STATES    — firm-wide control per feature: enabled / disabled / beta / internal_only.
  * OVERRIDE STATES— per-client control per feature: inherit / enable / disable.
Client STATUS (active / inactive / needs_review → active|inactive|prospect|archive) is tracked
separately and, per spec, does NOT itself grant feature access.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- enumerations (kept as plain tuples so callers can validate against them) ---
PRODUCTS: tuple[str, ...] = ("core", "wealth", "business")

# Firm-wide feature states.
FIRM_ENABLED = "enabled"
FIRM_DISABLED = "disabled"
FIRM_BETA = "beta"
FIRM_INTERNAL_ONLY = "internal_only"
FIRM_STATES: tuple[str, ...] = (FIRM_ENABLED, FIRM_DISABLED, FIRM_BETA, FIRM_INTERNAL_ONLY)

# Per-client override states. INHERIT is represented by the ABSENCE of a row (fail-safe default).
OVERRIDE_INHERIT = "inherit"
OVERRIDE_ENABLE = "enable"
OVERRIDE_DISABLE = "disable"
OVERRIDE_STATES: tuple[str, ...] = (OVERRIDE_INHERIT, OVERRIDE_ENABLE, OVERRIDE_DISABLE)

# Client status + the dispositions a "needs_review" status may resolve to.
CLIENT_STATUSES: tuple[str, ...] = ("active", "inactive", "needs_review")
CLIENT_DISPOSITIONS: tuple[str, ...] = ("active", "inactive", "prospect", "archive")

# Subject kinds an entitlement/override/status may attach to.
SUBJECT_TYPES: tuple[str, ...] = ("household", "organization", "person")


@dataclass(frozen=True)
class Feature:
    key: str
    product: str            # one of PRODUCTS
    label: str
    default_firm_state: str  # one of FIRM_STATES — conservative: core enabled, wealth/business disabled


def _feat(key, product, label, default_firm_state):
    return Feature(key=key, product=product, label=label, default_firm_state=default_firm_state)


# Core features power the EXISTING working portal, so they default to firm ``enabled``. Wealth and
# Business features front unfinished integrations, so they default to firm ``disabled`` — the strongest
# backward-compat guarantee: no client reaches them until an administrator enables them firm-wide.
_CORE = FIRM_ENABLED
_OFF = FIRM_DISABLED

_FEATURE_LIST: tuple[Feature, ...] = (
    # CORE
    _feat("portal_access", "core", "Portal Access", _CORE),
    _feat("secure_messaging", "core", "Secure Messaging", _CORE),
    _feat("document_vault", "core", "Document Vault", _CORE),
    _feat("document_download", "core", "Document Download", _CORE),
    _feat("document_upload", "core", "Document Upload", _CORE),
    _feat("profile_editing", "core", "Profile Editing", _CORE),
    _feat("client_requests", "core", "Client Requests", _CORE),
    _feat("portal_notifications", "core", "Portal Notifications", _CORE),
    _feat("email_notifications", "core", "Email Notifications", _CORE),
    # Billing & Invoicing (MVP): the billing area + invoice viewing are Core (default enabled). Online
    # payments and autopay front unbuilt processor integrations, so they default firm-DISABLED and must
    # stay off until a payment processor is configured.
    _feat("billing", "core", "Billing", _CORE),
    _feat("invoice_view", "core", "Invoice View", _CORE),
    _feat("online_payments", "core", "Online Payments", _OFF),
    _feat("autopay", "core", "Autopay", _OFF),
    # WEALTH
    _feat("wealth_dashboard", "wealth", "Wealth Dashboard", _OFF),
    _feat("schwab_accounts", "wealth", "Schwab Accounts", _OFF),
    _feat("assetmark_accounts", "wealth", "AssetMark Accounts", _OFF),
    _feat("outside_accounts", "wealth", "Outside Accounts", _OFF),
    _feat("financial_planning", "wealth", "Financial Planning", _OFF),
    _feat("retirement_planning", "wealth", "Retirement Planning", _OFF),
    _feat("monte_carlo", "wealth", "Monte Carlo", _OFF),
    _feat("tax_planning", "wealth", "Tax Planning", _OFF),
    # BUSINESS
    _feat("business_dashboard", "business", "Business Dashboard", _OFF),
    _feat("quickbooks", "business", "QuickBooks", _OFF),
    _feat("payroll", "business", "Payroll", _OFF),
    _feat("adp", "business", "ADP", _OFF),
    _feat("sales_tax", "business", "Sales Tax", _OFF),
    _feat("business_documents", "business", "Business Documents", _OFF),
    _feat("business_analytics", "business", "Business Analytics", _OFF),
    _feat("business_forecasting", "business", "Business Forecasting", _OFF),
)

FEATURES: dict[str, Feature] = {f.key: f for f in _FEATURE_LIST}


def get_feature(key: str) -> Feature | None:
    """The registered feature, or None. Callers MUST treat None as fail-closed (deny)."""
    return FEATURES.get(key)


def is_registered(key: str) -> bool:
    return key in FEATURES


def features_for_product(product: str) -> list[Feature]:
    return [f for f in _FEATURE_LIST if f.product == product]


def default_firm_state(key: str) -> str:
    """Catalog default firm state for a feature; unknown features are treated as disabled (fail-closed)."""
    f = FEATURES.get(key)
    return f.default_firm_state if f else FIRM_DISABLED
