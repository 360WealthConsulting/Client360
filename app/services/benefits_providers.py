"""Benefits provider ports — DISABLED (Release 0.9.11, Phase 2).

Provider-neutral interfaces for the external platforms benefits integrates with:
health **carriers**, retirement **recordkeepers** (Betterment at Work first), **payroll**,
and **HRIS**. Per ADR-18 this release ships the interfaces and *disabled* stubs only —
**no live integration, no network calls, no credentials.** Every stub reports an honest
``disabled`` action outcome and ``not_connected`` connection status, mirroring the honest
notification-outcome contract from the Exception Engine SLA sweep.

Adding a real provider later (Guideline / Human Interest / Vestwell / Empower / a carrier /
a payroll or HRIS system) is a new class + a registry row — no schema or interface change.
Same registry idiom as ``tax_filing_providers`` / ``portal.providers``.
"""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ProviderResult:
    """Honest outcome of a provider operation. Disabled providers always return
    ``outcome='disabled'`` / ``status='not_connected'`` — never a fabricated success."""
    outcome: str            # 'disabled' until a real integration exists
    status: str = "not_connected"
    detail: str = ""
    metadata: dict = field(default_factory=dict)


class BenefitsProvider(Protocol):
    key: str
    provider_type: str      # carrier | recordkeeper | payroll | hris
    enabled: bool
    def connection_status(self, *, organization_id: int) -> ProviderResult: ...


class _DisabledProvider:
    """Base for every disabled stub: no I/O, honest outcomes."""
    enabled = False

    def connection_status(self, *, organization_id: int) -> ProviderResult:
        return ProviderResult(
            outcome="disabled", status="not_connected",
            detail=f"{self.key} integration is not implemented in Release 0.9.11",
            metadata={"organization_id": organization_id, "provider_type": self.provider_type},
        )


# --- carriers (health) -------------------------------------------------------
class CarrierProvider(_DisabledProvider):
    provider_type = "carrier"
    def submit_enrollment(self, payload: dict) -> ProviderResult:
        return ProviderResult(outcome="disabled", detail="carrier enrollment (EDI 834) not implemented")


class DisabledCarrierProvider(CarrierProvider):
    key = "carrier_disabled"


# --- recordkeepers (retirement) — Betterment first ---------------------------
class RecordkeeperProvider(_DisabledProvider):
    provider_type = "recordkeeper"
    def sync_participants(self, payload: dict) -> ProviderResult:
        return ProviderResult(outcome="disabled", detail="recordkeeper participant sync not implemented")


class BettermentRecordkeeperProvider(RecordkeeperProvider):
    """First seeded retirement recordkeeper (Betterment at Work). Disabled stub."""
    key = "betterment"


# --- payroll -----------------------------------------------------------------
class PayrollProvider(_DisabledProvider):
    provider_type = "payroll"
    def export_deductions(self, payload: dict) -> ProviderResult:
        return ProviderResult(outcome="disabled", detail="payroll deduction export not implemented")


class DisabledPayrollProvider(PayrollProvider):
    key = "payroll_disabled"


# --- HRIS --------------------------------------------------------------------
class HrisProvider(_DisabledProvider):
    provider_type = "hris"
    def import_roster(self, payload: dict) -> ProviderResult:
        return ProviderResult(outcome="disabled", detail="HRIS roster import not implemented")


class DisabledHrisProvider(HrisProvider):
    key = "hris_disabled"


# --- registries (all disabled) ----------------------------------------------
CARRIER_PROVIDERS = {DisabledCarrierProvider.key: DisabledCarrierProvider()}
RECORDKEEPER_PROVIDERS = {BettermentRecordkeeperProvider.key: BettermentRecordkeeperProvider()}
PAYROLL_PROVIDERS = {DisabledPayrollProvider.key: DisabledPayrollProvider()}
HRIS_PROVIDERS = {DisabledHrisProvider.key: DisabledHrisProvider()}

ALL_PROVIDERS = {
    "carrier": CARRIER_PROVIDERS,
    "recordkeeper": RECORDKEEPER_PROVIDERS,
    "payroll": PAYROLL_PROVIDERS,
    "hris": HRIS_PROVIDERS,
}


def get_provider(provider_type: str, key: str):
    registry = ALL_PROVIDERS.get(provider_type)
    if registry is None or key not in registry:
        raise ValueError(f"Unknown provider {provider_type}:{key}")
    return registry[key]


def connection_status(provider_type: str, key: str, *, organization_id: int) -> ProviderResult:
    """Honest connection status for a provider (always ``not_connected`` this release)."""
    return get_provider(provider_type, key).connection_status(organization_id=organization_id)
