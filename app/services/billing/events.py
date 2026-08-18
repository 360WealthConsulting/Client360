"""Billing event emission — domain-event hook + client notification, reusing existing infrastructure.

Two channels, both best-effort so a billing mutation is never rolled back by an eventing failure:

  * Domain event — ``publish_safe(f"billing.{event}")`` with a REFERENCES-ONLY payload (ids only, never
    amounts/PII, per the D.35 payload-safety rule). The billing event contracts are not yet registered
    in the governed D.34 catalog (a separate governed change), so this is a no-op hook today; the audit
    ledger + notifications below are the real MVP flow, and the hook activates once contracts land.
  * Client notification — ``notify(...)`` to each portal account associated with the bill-to subject, so
    the event appears in the existing Communication Hub relationship timeline (which reads
    ``portal_notifications``). No second timeline / history store is created.
"""
from __future__ import annotations

from sqlalchemy import or_, select

from app.db import engine, people, portal_access_grants, portal_accounts


def _subject_account_ids(bill_to_type: str, bill_to_id: int) -> list[int]:
    """Active portal accounts that should be notified for a bill-to subject (via portal grants)."""
    with engine.connect() as c:
        if bill_to_type == "organization":
            grants = select(portal_access_grants.c.portal_account_id).where(
                portal_access_grants.c.organization_id == bill_to_id)
        elif bill_to_type == "household":
            grants = select(portal_access_grants.c.portal_account_id).where(
                portal_access_grants.c.household_id == bill_to_id)
        else:  # person → the person's own accounts + their household's
            hid = c.scalar(select(people.c.household_id).where(people.c.id == bill_to_id))
            cond = [portal_access_grants.c.person_id == bill_to_id]
            if hid is not None:
                cond.append(portal_access_grants.c.household_id == hid)
            grants = select(portal_access_grants.c.portal_account_id).where(or_(*cond))
        account_ids = {r[0] for r in c.execute(grants).all() if r[0]}
        if not account_ids:
            return []
        active = c.execute(select(portal_accounts.c.id).where(
            portal_accounts.c.id.in_(tuple(account_ids)),
            portal_accounts.c.status == "active")).scalars().all()
    return list(active)


def emit_billing_event(event: str, *, bill_to_type: str, bill_to_id: int, invoice_id: int | None = None,
                       notification_type: str | None = None, title: str | None = None,
                       body: str | None = None) -> None:
    """Fire the domain-event hook (references only) and, when there is client-facing news, a portal
    notification. Fully best-effort — never raises into the billing transaction."""
    try:
        from app.services.events.publisher import publish_safe
        publish_safe(f"billing.{event}", {"bill_to_type": bill_to_type, "bill_to_id": bill_to_id,
                                          "invoice_id": invoice_id})
    except Exception:  # noqa: BLE001 — eventing must never break billing
        pass
    if title is None:
        return
    try:
        from app.portal.service import notify
        for account_id in _subject_account_ids(bill_to_type, bill_to_id):
            notify(account_id, notification_type or "billing", title, body=body,
                   entity_type="invoice", entity_id=invoice_id,
                   idempotency_key=f"billing:{event}:{invoice_id}:{account_id}")
    except Exception:  # noqa: BLE001 — notification delivery is best-effort
        pass
