"""Enterprise Regulatory Examination Readiness, Evidence Governance & Supervisory Certification layer
(Phase D.59).

A governed, READ-ONLY composition that provides a unified, governed view of the firm's readiness to respond to
regulatory examinations, audits, supervisory reviews, and evidence requests — obligation coverage, evidence
availability / completeness / freshness, supervisory-review coverage, certification & sign-off status, filing
& acknowledgement readiness, and remediation-evidence availability — WITHOUT introducing a second compliance
platform, examination-management system, audit platform, document repository, records-management system,
regulatory filing system, certification engine, evidence vault, supervisory approval engine, or
policy-management system. It composes named readiness dashboards from declarative obligation + evidence +
examination-request + certification registries over the platform's AUTHORITATIVE owners: Compliance
Intelligence + `compliance/reviews` + the rule catalog + the reviewer-authority owner, the Exception Engine,
Document Intelligence, Data Governance, Security Operations, Business Continuity, Vendor Management, Financial
Operations, Insurance licensing, audit logging, and the CI pipeline. Regulatory filing, examination-case
ownership, certification reviewers, evidence export, and several obligation domains have no authoritative owner
in the platform today — declared registry entries with a `not_configured` status, never fabricated. The
reviewer_authorities catalog is seeded empty, so every certification is `reviewer_not_confirmed` / blocked;
reviewer authority is never inferred and business approval is never regulatory certification. It defines no new
metrics, owns no persistence, and never creates an examination, uploads or modifies evidence, approves a rule
set, certifies compliance, signs an attestation, files a form, closes a finding, resolves an exception, or
changes retention; every panel is explainable, deep-links to its authoritative owner, and carries counts /
status / coverage / freshness / age bands only — never sensitive evidence. The derived readiness summary
describes OPERATIONAL READINESS, never regulatory certification, and never interprets an absence of findings as
compliance.
"""
from .service import (
    client_evidence_readiness,
    compose_dashboard,
    get_panel,
    household_evidence_readiness,
    list_dashboards,
    readiness_summary,
)

__all__ = [
    "compose_dashboard",
    "list_dashboards",
    "get_panel",
    "readiness_summary",
    "client_evidence_readiness",
    "household_evidence_readiness",
]
