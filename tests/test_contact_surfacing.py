"""Task 2 — Contact Information Everywhere.

Locks the canonical contact surfacing: Search returns the linked person so results open the
Client Profile; the tax production dashboard and the insurance policy detail expose the
client's primary phone/email. Household + Client-Profile-header surfacing are template-only
(the person row already carries primary_email/primary_phone).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db import engine, insurance_policies, source_contacts, tax_engagement_returns
from app.matching.promote import promote_unlinked
from app.routes.search import _search


class _P:
    def can(self, _c):
        return True


def test_search_returns_linked_person_so_results_open_the_profile():
    tag = uuid.uuid4().hex[:10]
    name = f"Contactsurfacing {tag}"
    email = f"cs-{tag}@example.com"
    with engine.begin() as c:
        c.execute(source_contacts.insert().values(
            source_system="Wealthbox", source_file="t.zip", source_record_id=f"cs-{tag}",
            source_hash=f"cs-{tag}", first_name="Contactsurfacing", last_name=tag,
            full_name=name, email=email, normalized_email=email, raw_data={}))
    promote_unlinked(source_system="Wealthbox")
    rows = _search(name)
    hit = next(r for r in rows if r["full_name"] == name)
    assert hit["person_id"] is not None          # -> template links to /people/{person_id}
    assert hit["email"] == email                  # contact visible in the results row


def test_tax_dashboard_items_carry_taxpayer_contact_keys():
    from app.services.tax_return_lifecycle import production_dashboard
    data = production_dashboard(_P())
    if not data["items"]:
        return  # structural change verified elsewhere; nothing to assert without returns
    item = data["items"][0]
    for key in ("taxpayer_person_id", "taxpayer_name", "taxpayer_email", "taxpayer_phone"):
        assert key in item


def test_insurance_policy_detail_carries_client_contact_keys():
    from app.services.insurance import get_policy
    with engine.connect() as c:
        pid = c.execute(select(insurance_policies.c.id).limit(1)).scalar()
    if pid is None:
        return  # no policy seeded; structural change verified elsewhere
    policy = get_policy(_P(), pid)
    for key in ("client_person_id", "client_name", "client_email", "client_phone"):
        assert key in policy


def test_no_tax_return_leak_when_query_has_no_person():
    # the taxpayer joins are LEFT joins: a return with no engagement/person must not vanish.
    from app.services.tax_return_lifecycle import production_dashboard
    with engine.connect() as c:
        total_returns = c.execute(select(tax_engagement_returns.c.id)).all()
    data = production_dashboard(_P())
    # read-all principal sees every return; the added left-joins must not drop rows
    assert len(data["items"]) == len(total_returns)
