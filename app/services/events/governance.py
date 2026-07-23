"""Event governance (Phase D.34) — validation of the domain-event model.

Read-only validation that the domain-event model is coherent: every published event type is registered
(no unregistered/orphan contracts), the registry matches the in-code contracts (no contract/version
drift), every subscription targets a live contract (no orphan subscriptions), every active contract has
a consumer (no producer-without-consumer), no contract's payload schema is malformed (no schema
violations), and no active contract references a deprecated/retired one. It reads the contract +
subscription registries and the in-code contracts read-only (the outbox remains the sole bus; the
runtime engine the sole evaluator) and returns a structured report. It never raises and never edits.
"""
from __future__ import annotations

from app.platform.events import SUPPORTED_VERSIONS

from . import contracts, registry
from .common import write_audit


def validate() -> dict:
    """Run every event-governance check → ``{ok, issue_count, findings, coverage}``. Never raises."""
    findings = []
    try:
        rows = registry.list_contracts()
        by_type = {r["event_type"]: r for r in rows}
        active = {r["event_type"] for r in rows if r["status"] == "active"}
        deprecated_or_retired = {r["event_type"] for r in rows if r["status"] in ("deprecated", "retired")}
        code = contracts.EVENT_CONTRACTS
        subs = registry.list_subscriptions()
        subbed = {s["event_type"] for s in subs if s["status"] == "active"}

        # 1. unregistered contracts (an in-code contract with no active registry row).
        for et in code:
            row = by_type.get(et)
            if row is None or row["status"] != "active":
                findings.append({"type": "unregistered_contract", "event_type": et})

        # 2. orphan contracts (an active registry row with no in-code contract).
        for r in rows:
            if r["status"] == "active" and r["event_type"] not in code:
                findings.append({"type": "orphan_contract", "event_type": r["event_type"]})

        # 3. contract/version drift + schema violations, per active contract.
        for r in rows:
            if r["status"] != "active":
                continue
            et = r["event_type"]
            if r["schema_version"] not in SUPPORTED_VERSIONS:
                findings.append({"type": "version_drift", "event_type": et,
                                 "schema_version": r["schema_version"]})
            cc = code.get(et)
            if cc is not None:
                if cc.schema_version != r["schema_version"]:
                    findings.append({"type": "version_drift", "event_type": et,
                                     "registry": r["schema_version"], "code": cc.schema_version})
                for problem in cc.schema_problems():
                    findings.append({"type": "schema_violation", "event_type": et, "detail": problem})
            # 7. invalid ownership (neither owner nor producer).
            if not r.get("owner") and not r.get("producer"):
                findings.append({"type": "invalid_ownership", "event_type": et})
            # 8. producer without a consumer.
            if et not in subbed:
                findings.append({"type": "producer_without_consumer", "event_type": et})
            # deprecated references in the dependency graph.
            for dep in (r.get("depends_on") or []):
                if dep in deprecated_or_retired:
                    findings.append({"type": "deprecated_reference", "event_type": et, "dependency": dep})

        # 4. orphan subscriptions (an active subscription with no active contract).
        for s in subs:
            if s["status"] != "active":
                continue
            if s["event_type"] not in active:
                findings.append({"type": "orphan_subscription", "event_type": s["event_type"],
                                 "consumer": s["consumer"]})
            elif s["event_type"] in deprecated_or_retired:
                findings.append({"type": "deprecated_reference", "event_type": s["event_type"],
                                 "consumer": s["consumer"]})

        cov = registry.coverage()
    except Exception as exc:   # never raise into a caller
        return {"ok": False, "issue_count": 1,
                "findings": [{"type": "governance_check_error", "detail": str(exc)}],
                "coverage": {"coverage_pct": 0.0}}
    return {"ok": len(findings) == 0, "issue_count": len(findings), "findings": findings, "coverage": cov}


def record_validation(*, actor_user_id=None) -> dict:
    """Run event governance validation and record a firm-level ``governance_validated`` audit event."""
    report = validate()
    write_audit("domain_event.governance_validated", entity_id=0, actor_user_id=actor_user_id,
                metadata={"issue_count": report["issue_count"]})
    return report
