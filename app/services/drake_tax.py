"""Drake Tax → authoritative tax domain (PR 3B).

Populates the EXISTING tax tables (tax_engagements → returns → filing events → lifecycle events →
missing items → document links) from Drake return-status records, so the Client Workspace Tax tab
becomes the primary place to review return status. No separate Drake application, no new ADR, no new
tables — it writes the authoritative rows the Tax tab (PR 2D) already renders, and links the canonical
documents created in PR 3A.

Idempotent: engagement/return are upserted; append-only filing/lifecycle events are guarded by
idempotency keys / (return, status); missing items and document links are guarded by existence. A rerun
updates status/dates and adds nothing duplicate.

Input is a list of structured Drake return records (what a Drake export/report provides) — the parsing
of a specific Drake export format is environment-specific and lives in the Drake connector; this service
is the authoritative-write mechanism it feeds. Concepts the tax schema does not model (refund, balance
due, amendment/organizer status) are preserved as provenance in the filing-event metadata rather than
fabricated as first-class fields.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import and_, select

from app.db import (
    engine,
    filing_jurisdictions,
    tax_document_links,
    tax_engagement_returns,
    tax_engagements,
    tax_filing_events,
    tax_firms,
    tax_missing_items,
    tax_offices,
    tax_return_lifecycle_events,
    tax_return_types,
    tax_years,
)

SOURCE_SYSTEM = "Drake"


def _get_or_create(conn, table, where, values):
    row = conn.execute(select(table.c.id).where(where)).scalar()
    if row is not None:
        return row
    return conn.execute(table.insert().values(**values).returning(table.c.id)).scalar_one()


def _refs(conn, *, year, return_type, jurisdiction, entity_type):
    firm = _get_or_create(conn, tax_firms, tax_firms.c.code == "DRAKE",
                          {"code": "DRAKE", "name": "Drake Import"})
    office = _get_or_create(conn, tax_offices, tax_offices.c.code == "DRAKE-MAIN",
                            {"tax_firm_id": firm, "code": "DRAKE-MAIN", "name": "Drake"})
    yr = _get_or_create(conn, tax_years, tax_years.c.year == year,
                        {"year": year, "starts_on": date(year, 1, 1), "ends_on": date(year, 12, 31),
                         "status": "open"})
    rt = _get_or_create(conn, tax_return_types, tax_return_types.c.code == return_type,
                        {"code": return_type, "name": return_type, "entity_type": entity_type})
    jur = _get_or_create(conn, filing_jurisdictions, filing_jurisdictions.c.code == jurisdiction,
                         {"code": jurisdiction, "name": jurisdiction,
                          "level": "federal" if jurisdiction in ("US", "IRS") else "state"})
    return firm, office, yr, rt, jur


def _primary_member(household_id):
    from app.db import household_relationships
    with engine.connect() as conn:
        return conn.execute(
            select(household_relationships.c.person_id)
            .where(household_relationships.c.household_id == household_id)
            .order_by(household_relationships.c.is_primary.desc(),
                      household_relationships.c.person_id)).scalar()


def _as_dt(v):
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=UTC)
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def import_drake_tax_returns(records, *, actor_user_id=None, request_id=None, dry_run=False) -> dict:
    """Import/refresh Drake return status into the authoritative tax domain. ``records`` is a list of
    dicts (see module docstring / tests). Returns a summary."""
    summary = {"returns_imported": 0, "returns_updated": 0, "filing_events": 0,
               "lifecycle_events": 0, "missing_items": 0, "document_links": 0,
               "skipped": 0, "errors": [], "dry_run": dry_run}
    for rec in records:
        try:
            _import_one(rec, summary, actor_user_id, request_id, dry_run)
        except Exception as exc:      # noqa: BLE001 — record & continue
            summary["errors"].append(f"{rec.get('return_type', '?')} {rec.get('tax_year', '?')}: {exc}")
    return summary


def _import_one(rec, summary, actor_user_id, request_id, dry_run):
    person_id = rec.get("person_id")
    household_id = rec.get("household_id")
    if person_id is None and household_id is None and rec.get("client_folder"):
        with engine.connect() as conn:
            from app.importers.taxdome_drive import resolve_folder
            household_id, person_id = resolve_folder(conn, rec["client_folder"])
    # A tax engagement requires a person (or entity) subject; a joint/household return keys to the
    # household's primary member while still carrying household_id (so both spouses see it).
    if person_id is None and household_id is not None:
        person_id = _primary_member(household_id)
    if person_id is None and household_id is None:
        summary["skipped"] += 1
        return

    year = int(rec["tax_year"])
    return_type = rec.get("return_type") or "1040"
    jurisdiction = rec.get("jurisdiction") or "US"
    entity_type = rec.get("entity_type") or "individual"

    if dry_run:
        summary["returns_imported"] += 1
        return

    with engine.begin() as conn:
        firm, office, yr, rt, jur = _refs(conn, year=year, return_type=return_type,
                                          jurisdiction=jurisdiction, entity_type=entity_type)
        # Engagement (idempotent by owner + year).
        eng_where = [tax_engagements.c.tax_year_id == yr]
        if household_id is not None:
            eng_where.append(tax_engagements.c.household_id == household_id)
        else:
            eng_where.append(tax_engagements.c.person_id == person_id)
        eng = conn.execute(select(tax_engagements.c.id).where(and_(*eng_where))).scalar()
        if eng is None:
            eng = conn.execute(tax_engagements.insert().values(
                tax_firm_id=firm, tax_office_id=office, tax_year_id=yr, person_id=person_id,
                household_id=household_id, engagement_type=entity_type, status="active",
                created_by_user_id=actor_user_id).returning(tax_engagements.c.id)).scalar_one()
        # Return (idempotent by engagement + type + jurisdiction; update status/dates).
        ret_where = and_(tax_engagement_returns.c.tax_engagement_id == eng,
                         tax_engagement_returns.c.return_type_id == rt,
                         tax_engagement_returns.c.jurisdiction_id == jur)
        ret = conn.execute(select(tax_engagement_returns.c.id).where(ret_where)).scalar()
        values = {
            "status": rec.get("status") or "in_preparation",
            "filing_status": rec.get("federal_filing_status") or rec.get("filing_status") or "not_filed",
            "preparation_started_at": _as_dt(rec.get("prepared_started_at")),
            "preparation_completed_at": _as_dt(rec.get("prepared_at")),
            "filed_at": _as_dt(rec.get("filed_at")),
            "accepted_at": _as_dt(rec.get("federal_accepted_at") or rec.get("accepted_at")),
            "filing_provider_key": "drake",
            "filing_external_id": rec.get("filing_external_id"),
        }
        if ret is None:
            ret = conn.execute(tax_engagement_returns.insert().values(
                tax_engagement_id=eng, return_type_id=rt, jurisdiction_id=jur, **values)
                .returning(tax_engagement_returns.c.id)).scalar_one()
            summary["returns_imported"] += 1
        else:
            conn.execute(tax_engagement_returns.update().where(
                tax_engagement_returns.c.id == ret).values(**values))
            summary["returns_updated"] += 1

        summary["filing_events"] += _filing_events(conn, ret, rec, actor_user_id)
        summary["lifecycle_events"] += _lifecycle(conn, ret, rec, person_id, household_id, actor_user_id)
        summary["missing_items"] += _missing(conn, ret, rec)
        summary["document_links"] += _doc_links(conn, ret, rec)

        import uuid

        from app.security.audit import write_audit_event
        write_audit_event(action="tax.drake_imported", entity_type="tax_return", entity_id=ret,
                          actor_user_id=actor_user_id,
                          request_id=request_id or f"drake-tax-{uuid.uuid4()}",
                          metadata={"tax_year": year, "return_type": return_type,
                                    "refund_amount": rec.get("refund_amount"),
                                    "balance_due": rec.get("balance_due"),
                                    "amended": rec.get("amended"),
                                    "organizer_status": rec.get("organizer_status")})


def _filing_events(conn, ret, rec, actor_user_id):
    added = 0
    for jur_key, ack in (("US", rec.get("federal_ack")), (rec.get("state_code", "state"), rec.get("state_ack"))):
        if not ack:
            continue
        idem = f"drake:{ret}:{jur_key}:{ack.get('status')}:{ack.get('submission_id')}"
        exists = conn.execute(select(tax_filing_events.c.id).where(
            tax_filing_events.c.idempotency_key == idem)).scalar()
        if exists:
            continue
        conn.execute(tax_filing_events.insert().values(
            tax_engagement_return_id=ret, filing_status=ack.get("status") or "submitted",
            provider_key="drake", submission_id=ack.get("submission_id"),
            external_id=ack.get("external_id"), reason_code=ack.get("reason_code"),
            message=ack.get("message"), actor_user_id=actor_user_id, idempotency_key=idem,
            metadata={"jurisdiction": jur_key}))
        added += 1
    return added


def _lifecycle(conn, ret, rec, person_id, household_id, actor_user_id):
    from app.services.timeline import add_timeline_event
    added = 0
    transitions = [
        ("prepared_at", "prepared", "Return prepared"),
        ("reviewed_at", "reviewed", "Return reviewed"),
        ("filed_at", "filed", "Return filed"),
        ("federal_accepted_at", "federal_accepted", "Federal accepted"),
        ("state_accepted_at", "state_accepted", "State accepted"),
        ("extension_filed_at", "extension_filed", "Extension filed"),
        ("amended_at", "amended", "Amendment filed"),
    ]
    for key, to_status, title in transitions:
        when = _as_dt(rec.get(key))
        if when is None and not (key == "amended_at" and rec.get("amended")):
            continue
        exists = conn.execute(select(tax_return_lifecycle_events.c.id).where(and_(
            tax_return_lifecycle_events.c.tax_engagement_return_id == ret,
            tax_return_lifecycle_events.c.to_status == to_status)).limit(1)).scalar()
        if exists:
            continue
        conn.execute(tax_return_lifecycle_events.insert().values(
            tax_engagement_return_id=ret, to_status=to_status, reason="drake",
            actor_user_id=actor_user_id, created_at=when or datetime.now(UTC)))
        add_timeline_event(person_id=person_id, household_id=household_id, source="drake",
                           event_type=f"tax_{to_status}", title=title,
                           external_id=f"drake-tax-{ret}-{to_status}",
                           event_time=when, event_metadata={"return_id": ret})
        added += 1
    return added


def _missing(conn, ret, rec):
    added = 0
    items = list(rec.get("missing_k1", []) or [])
    if rec.get("organizer_status") in ("incomplete", "not_started", "pending"):
        items.append(("organizer", "Organizer incomplete"))
    for item in items:
        item_type, title = (("k1", f"Missing K-1 — {item}") if isinstance(item, str) else item)
        exists = conn.execute(select(tax_missing_items.c.id).where(and_(
            tax_missing_items.c.tax_engagement_return_id == ret,
            tax_missing_items.c.item_type == item_type,
            tax_missing_items.c.title == title)).limit(1)).scalar()
        if exists:
            continue
        conn.execute(tax_missing_items.insert().values(
            tax_engagement_return_id=ret, item_type=item_type, title=title, status="open"))
        added += 1
    return added


def _doc_links(conn, ret, rec):
    added = 0
    for doc_id in (rec.get("document_ids") or []):
        exists = conn.execute(select(tax_document_links.c.id).where(and_(
            tax_document_links.c.tax_engagement_return_id == ret,
            tax_document_links.c.document_id == doc_id)).limit(1)).scalar()
        if exists:
            continue
        # match_source/status are constrained enums; a Drake-provided return PDF is an established
        # (accepted) link, matched via the canonical SHA-256 model (ADR-072). Drake provenance lives
        # in metadata rather than an out-of-enum value.
        conn.execute(tax_document_links.insert().values(
            tax_engagement_return_id=ret, document_id=doc_id, match_source="hash",
            status="accepted", confidence=1, metadata={"source_system": SOURCE_SYSTEM}))
        added += 1
    return added
