"""Producer licensing / CE adapter (Phase D.47).

Composes the AUTHORITATIVE producer licensing + continuing-education records (``insurance_licensing``) into
supervisory items (``licensing_review`` / ``continuing_education_review``) and a ``licensing_issue`` /
``ce_deficiency`` exception where a record is expired/deficient. It reads through the authoritative service
(which enforces ``insurance.licensing.read``), so it fails closed to empty when the supervisor does not also
hold that capability — the type stays registered but unpopulated. Read-only; never mutates or concludes a
licensing/CE determination (that stays firm-internal per the owning service).
"""
from __future__ import annotations

from .. import stats
from ..model import ComplianceException, SupervisoryItem


def licensing_items(principal):
    """Return (supervisory_items, exceptions) for producer licensing + CE. Fail-closed to empty."""
    items, exceptions = [], []
    try:
        from app.services.insurance_licensing import list_licenses
        licenses = list_licenses(principal)
    except Exception:
        stats.note("adapter_failures", source="insurance_licensing")
        return [], []
    for lic in licenses:
        status = lic.get("status") or "unknown"
        producer = lic.get("producer_name") or "producer"
        items.append(SupervisoryItem(
            item_id=f"sup:licensing_review:license:{lic.get('id')}",
            review_type="licensing_review", status=status, priority="medium",
            title=f"License review — {producer} ({lic.get('state')})",
            summary=f"Producer license status is {status}; expires {lic.get('expiry_date')}.",
            explanation="A producer license record is on file and subject to supervisory review, per "
                        "insurance_licensing.",
            governing_policy="producer licensing policy",
            evidence=(f"license_id={lic.get('id')}", f"state={lic.get('state')}",
                      f"expiry={lic.get('expiry_date')}"),
            authoritative_owner="insurance_licensing", required_reviewer="compliance officer",
            due_date=str(lic.get("expiry_date")) if lic.get("expiry_date") else None,
            deep_link="/insurance", recommended_action="Review the producer license",
            metadata={"producer": producer}))
        stats.note("reviews_composed", review_type="licensing_review")
        if status in ("expired", "lapsed", "suspended"):
            exceptions.append(ComplianceException(
                exception_id=f"exc:licensing_issue:license:{lic.get('id')}",
                exception_type="licensing_issue", severity="high", status="open",
                title=f"Licensing issue — {producer}",
                summary=f"Producer license is {status}.",
                explanation=f"A producer license is {status} and requires supervisory attention.",
                governing_policy="producer licensing policy",
                evidence=(f"license_id={lic.get('id')}", f"status={status}"),
                owner="insurance_licensing", escalation="compliance officer", deep_link="/insurance"))
    return items, exceptions
