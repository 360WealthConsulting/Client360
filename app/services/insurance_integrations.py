"""Insurance integration ports — DISABLED stubs (Release 0.10.0, Phase 9).

Vendor-neutral **extension points** for future insurance integrations. This release ships the
interfaces and **disabled** stubs only — **no live integration, no network calls, no file
transfers, no authentication, no vendor API calls, no credentials, no endpoints, no scheduled
jobs.** Every stub reports an honest ``disabled`` / ``not_connected`` outcome, mirroring the
benefits/tax disabled-provider contract. Same registry idiom as ``benefits_providers`` /
``tax_filing_providers`` / ``portal.providers`` — **no parallel integration framework.**

Enablement is **code-governed, disabled-by-default**: ``enabled`` is a hardcoded ``False`` and is
**never read from configuration or environment** — no port becomes active because a config value
exists. Activating a real integration later is a concrete adapter class + registry row (its own
release, with its own vendor contract, credentials in the platform secret store, and compliance
review); no schema or interface change here.

These are transport extension points only: no port performs or enables suitability,
replacement/1035, licensing validation, sale/issue blocking, compliance approval, or any
regulated determination — that stays behind the AD-5 gate.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.security.audit import write_audit_event

# port_type vocabulary (inbound feeds + one outbound hook), justified by the current domain.
INBOUND = "inbound"
OUTBOUND = "outbound"


@dataclass(frozen=True)
class PortResult:
    """Honest outcome of an integration-port operation. Disabled ports always return
    ``outcome='disabled'`` / ``status='not_connected'`` — never a fabricated success, never I/O."""
    outcome: str            # 'disabled' until a real integration exists
    status: str = "not_connected"
    detail: str = ""
    metadata: dict = field(default_factory=dict)


class InsuranceIntegrationPort(Protocol):
    key: str
    port_type: str
    direction: str          # inbound | outbound
    description: str
    enabled: bool
    def connection_status(self, *, organization_id: int | None = None) -> PortResult: ...
    def invoke(self, *, organization_id: int | None = None, payload: dict | None = None) -> PortResult: ...


class _DisabledPort:
    """Base for every insurance integration port: DISABLED, inert, no I/O, honest outcomes.

    A disabled port never performs a network call, file transfer, authentication, polling, or
    vendor API call. ``invoke`` **fails safe** — it returns a ``disabled`` outcome and never
    inspects, stores, or logs the ``payload`` (which never contains, and must never contain,
    secrets). Metadata carries the port identity + organization only — audit-safe.
    """
    enabled = False

    def _meta(self, organization_id):
        return {"port": self.key, "port_type": self.port_type, "direction": self.direction,
                "organization_id": organization_id, "enabled": False}

    def connection_status(self, *, organization_id=None) -> PortResult:
        return PortResult(
            outcome="disabled", status="not_connected",
            detail=f"{self.key} integration is not implemented in Release 0.10.0 (disabled stub)",
            metadata=self._meta(organization_id))

    def invoke(self, *, organization_id=None, payload=None) -> PortResult:
        # Fail safe: no external I/O; the payload is NEVER inspected, stored, or logged.
        return PortResult(
            outcome="disabled", status="not_connected",
            detail=f"{self.key} is a disabled stub; no I/O performed",
            metadata=self._meta(organization_id))


# --- inbound feeds -----------------------------------------------------------
class CarrierPolicyFeedPort(_DisabledPort):
    key = "carrier_policy_feed"
    port_type = "carrier_policy_feed"
    direction = INBOUND
    description = "Inbound carrier policy & in-force data feed"


class CaseStatusFeedPort(_DisabledPort):
    key = "case_status_feed"
    port_type = "case_status_feed"
    direction = INBOUND
    description = "Inbound application / case-status feed"


class CommissionStatementFeedPort(_DisabledPort):
    key = "commission_statement_feed"
    port_type = "commission_statement_feed"
    direction = INBOUND
    description = "Inbound automated carrier commission-statement import feed"


class LicensingAppointmentFeedPort(_DisabledPort):
    key = "licensing_appointment_feed"
    port_type = "licensing_appointment_feed"
    direction = INBOUND
    description = "Inbound producer licensing / appointment data feed"


class DocumentEvidenceIntakePort(_DisabledPort):
    key = "document_evidence_intake"
    port_type = "document_evidence_intake"
    direction = INBOUND
    description = "Inbound document / evidence intake"


# --- outbound hook -----------------------------------------------------------
class OperationalExportHookPort(_DisabledPort):
    key = "operational_export_hook"
    port_type = "operational_export_hook"
    direction = OUTBOUND
    description = "Outbound operational export hook"


# --- registry (all disabled) -------------------------------------------------
INSURANCE_PORTS = {p.key: p for p in (
    CarrierPolicyFeedPort(),
    CaseStatusFeedPort(),
    CommissionStatementFeedPort(),
    LicensingAppointmentFeedPort(),
    DocumentEvidenceIntakePort(),
    OperationalExportHookPort(),
)}


def get_port(key: str):
    port = INSURANCE_PORTS.get(key)
    if port is None:
        raise ValueError(f"Unknown insurance integration port: {key}")
    return port


def list_ports():
    """All insurance integration ports and their (disabled) state — audit-safe, no secrets."""
    return [{"key": p.key, "port_type": p.port_type, "direction": p.direction,
             "description": p.description, "enabled": p.enabled, "status": "not_connected"}
            for p in INSURANCE_PORTS.values()]


def port_status(key: str, *, organization_id=None) -> PortResult:
    """Honest connection status for a port (always ``not_connected`` this release)."""
    return get_port(key).connection_status(organization_id=organization_id)


def invoke_port(key: str, *, organization_id=None, payload=None, actor_user_id=None, request_id=None) -> PortResult:
    """Invoke a port. Disabled ports **fail safe** — ``outcome='disabled'`` with NO external I/O.
    Writes an audit-safe event (metadata only: port/outcome/status/organization — never the
    payload, credentials, or any sensitive content)."""
    result = get_port(key).invoke(organization_id=organization_id, payload=payload)
    write_audit_event(
        action="insurance.integration.port_invoked",
        entity_type="insurance_integration_port", entity_id=None,
        actor_user_id=actor_user_id, request_id=request_id or f"insurance-{uuid.uuid4()}",
        metadata={"port": key, "outcome": result.outcome, "status": result.status,
                  "organization_id": organization_id})
    return result
