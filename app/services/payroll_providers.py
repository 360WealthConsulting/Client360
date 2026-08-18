"""Payroll provider adapters — DISABLED / MANUAL (Payroll Hub foundation, 360Plus / Client360).

Provider-neutral adapter seam for the external payroll platforms the Payroll Hub will integrate with
(ADP, QuickBooks Payroll, and a generic "Other"/manual source). This foundation ships the interface and
*inert* adapters only — **no live integration, no network calls, no credentials, and no money movement.**
Every adapter reports an honest outcome: ADP/QuickBooks are ``disabled`` / ``not_connected``; the manual
adapter is ``manual`` (staff-entered data, no external system).

Adding a real ADP or QuickBooks integration later is a new class + a registry row (and a connector under
``app/connectors/<provider>/`` mirroring ``app/connectors/microsoft365/``) — no schema or interface
change here. Same registry idiom as ``benefits_providers`` / ``tax_filing_providers``.
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PayrollProviderResult:
    """Honest outcome of a payroll-provider operation. Inert adapters never fabricate a success or move
    money — ``disabled`` (no integration) or ``manual`` (staff-entered), status ``not_connected``."""
    outcome: str                       # 'disabled' | 'manual'
    status: str = "not_connected"
    detail: str = ""
    metadata: dict = field(default_factory=dict)


class PayrollProviderAdapter(Protocol):
    key: str                           # matches payroll_providers.code
    label: str
    enabled: bool                      # True only when a real integration exists (none yet)
    def connection_status(self, *, organization_id: int) -> PayrollProviderResult: ...
    def fetch_payroll_summary(self, *, organization_id: int) -> PayrollProviderResult: ...


class _InertAdapter:
    """Base for every inert adapter: no I/O, no money movement, honest outcomes."""
    key = "inert"
    label = "Inert"
    enabled = False
    _outcome = "disabled"

    def connection_status(self, *, organization_id: int) -> PayrollProviderResult:
        return PayrollProviderResult(
            outcome=self._outcome, status="not_connected",
            detail=f"{self.label} payroll integration is not implemented (foundation).",
            metadata={"organization_id": organization_id, "provider": self.key})

    def fetch_payroll_summary(self, *, organization_id: int) -> PayrollProviderResult:
        return PayrollProviderResult(
            outcome=self._outcome, status="not_connected",
            detail=f"{self.label} payroll summary fetch is not implemented (foundation).",
            metadata={"organization_id": organization_id, "provider": self.key})


class AdpPayrollAdapter(_InertAdapter):
    """ADP — inert until a real connector + provider port is built."""
    key = "adp"
    label = "ADP"


class QuickBooksPayrollAdapter(_InertAdapter):
    """QuickBooks Payroll — inert until a real connector + provider port is built."""
    key = "quickbooks_payroll"
    label = "QuickBooks Payroll"


class ManualPayrollAdapter(_InertAdapter):
    """Generic / Other — payroll data is entered and maintained manually by staff (no external system)."""
    key = "other"
    label = "Other (manual)"
    _outcome = "manual"


# --- registry (keyed by payroll_providers.code) -----------------------------
PAYROLL_ADAPTERS = {
    AdpPayrollAdapter.key: AdpPayrollAdapter(),
    QuickBooksPayrollAdapter.key: QuickBooksPayrollAdapter(),
    ManualPayrollAdapter.key: ManualPayrollAdapter(),
}


def get_adapter(provider_code: str) -> PayrollProviderAdapter:
    adapter = PAYROLL_ADAPTERS.get(provider_code)
    if adapter is None:
        raise ValueError(f"Unknown payroll provider: {provider_code}")
    return adapter


def connection_status(provider_code: str, *, organization_id: int) -> PayrollProviderResult:
    """Honest connection status for a payroll provider (never a live connection in this foundation)."""
    return get_adapter(provider_code).connection_status(organization_id=organization_id)
