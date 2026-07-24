"""Enterprise Risk Management, Internal Controls & Assurance Governance layer (Phase D.58).

A governed, READ-ONLY composition that provides a unified, governed view of enterprise risk posture — risk
domains, control coverage, control-health signals, open findings, exceptions, incidents, remediation
workload, assurance coverage, evidence availability, and executive risk posture — WITHOUT introducing a second
GRC platform, risk register, compliance engine, exception system, audit platform, incident-management system,
control-testing application, policy engine, or approval engine. It composes named risk dashboards from
declarative risk + control + assurance + panel registries over the platform's AUTHORITATIVE owners: Compliance
Intelligence + the Exception Engine, Security Operations + incidents, Data Governance, the Integration
Platform, Business Continuity, Vendor Management, Financial Operations, Document Intelligence, Automation
Orchestration, Insurance licensing, and the Runtime + Policy engines + audit logging. Control testing /
effectiveness, model/AI risk, and privacy risk have no authoritative owner in the platform today — those are
declared registry entries with a `not_configured` status, never a fabricated risk, control, or assurance
status. It defines no new metrics, owns no persistence, and never creates a risk, changes a rating, closes a
finding, approves a control, accepts an exception, acknowledges an incident, assigns remediation, alters
evidence, certifies compliance, or modifies policy; every panel is explainable, deep-links to its
authoritative owner, and carries counts / status / severity distributions / coverage summaries only — never
sensitive evidence, and never a fabricated composite risk score (the derived posture panel is labeled
derived).
"""
from .service import (
    client_risk_controls,
    compose_dashboard,
    get_panel,
    household_risk_controls,
    list_dashboards,
    risk_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "risk_summary",
    "client_risk_controls",
    "household_risk_controls",
]
