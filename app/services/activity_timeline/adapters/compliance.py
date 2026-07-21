"""Compliance adapter (Phase D.10).

Projects durable compliance occurrences into ``TimelineEvent``: review submission
(``compliance_reviews.submitted_at``) and each recorded decision (the append-only
``compliance_decisions`` ledger). Only durably-timestamped facts are projected —
assignment / status changes are not separately timestamped and are NOT fabricated;
approval-blocked records no decision row, so it produces no event.

The event's existence is shown to any timeline-authorized principal; confidential
decision **comments/exceptions** and the source link are redacted unless the principal
holds ``compliance.review.read`` (``redact['compliance']``).
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db import compliance_decisions, compliance_reviews
from app.services.activity_timeline.models import TimelineEvent

SOURCE_DOMAIN = "compliance"


def events(conn, *, person_ids: tuple[int, ...], household_id: int | None, limit: int, redact: dict):
    can = bool(redact.get("compliance"))
    out: list[TimelineEvent] = []

    def _scope(table):
        conds = []
        if person_ids:
            conds.append(table.c.person_id.in_(person_ids))
        if household_id is not None:
            conds.append(table.c.household_id == household_id)
        return conds

    rev_conds = _scope(compliance_reviews)
    if rev_conds:
        for r in conn.execute(
            select(compliance_reviews).where(or_(*rev_conds))
            .order_by(compliance_reviews.c.submitted_at.desc()).limit(limit)
        ).mappings():
            out.append(TimelineEvent(
                event_id=f"compliance:review:{r['id']}:submitted",
                event_type="compliance.review.submitted",
                occurred_at=r["submitted_at"],
                title="Compliance review submitted",
                summary=f"Governed recommendation review ({r['recommendation_type'].replace('_', ' ')}).",
                person_id=r["person_id"], household_id=r["household_id"],
                source_domain=SOURCE_DOMAIN, source_record_type="compliance_review", source_record_id=r["id"],
                actor_principal_id=r["submitted_by"], status=r["status"],
                source_url=(f"/compliance/reviews/{r['id']}" if can else None),
            ))

    # Decisions (join reviews for the person/household scope).
    j = compliance_decisions.join(
        compliance_reviews, compliance_reviews.c.id == compliance_decisions.c.compliance_review_id)
    dec_conds = _scope(compliance_reviews)
    if dec_conds:
        for d in conn.execute(
            select(compliance_decisions,
                   compliance_reviews.c.person_id.label("r_person"),
                   compliance_reviews.c.household_id.label("r_household"))
            .select_from(j).where(or_(*dec_conds))
            .order_by(compliance_decisions.c.decided_at.desc()).limit(limit)
        ).mappings():
            confidential = bool(d["comments"] or d["exceptions"])
            redacted = confidential and not can
            out.append(TimelineEvent(
                event_id=f"compliance:decision:{d['id']}",
                event_type="compliance.decision",
                occurred_at=d["decided_at"],
                title=f"Compliance decision recorded — {d['decision'].replace('_', ' ')}",
                summary=((d["comments"] or "") if can else
                         ("Additional details are restricted." if confidential else "")),
                person_id=d["r_person"], household_id=d["r_household"],
                source_domain=SOURCE_DOMAIN, source_record_type="compliance_decision", source_record_id=d["id"],
                actor_principal_id=d["reviewer_principal_id"], status=d["decision"],
                source_url=(f"/compliance/reviews/{d['compliance_review_id']}" if can else None),
                redacted=redacted,
            ))
    return out
