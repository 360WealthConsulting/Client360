"""Task 1B — canonical-person promotion (identity-pipeline apply step) tests.

Verifies that unlinked source_contacts become canonical people: unique contacts create a
person, a later same-person contact links to the existing person, and genuine ambiguities
(shared email/phone, multiple candidate people) are left unpromoted for Match Review — never
falsely merged. Uses the current schema throughout.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db import engine, people, person_source_links, source_contacts
from app.matching.promote import PromotionReport, promote_unlinked


def _sc(system="Wealthbox", *, email=None, phone=None, first="A", last="B", full=None):
    tag = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(
            source_system=system, source_file="test-fixture.zip",
            source_record_id=f"{system}-{tag}", source_hash=f"h-{tag}",
            first_name=first, last_name=last, full_name=full or f"{first} {last}",
            email=email, normalized_email=(email.lower() if email else None),
            phone=phone, normalized_phone=(phone if phone else None), raw_data={},
        ).returning(source_contacts.c.id)).scalar_one()


def _phone():
    """A unique 10-digit phone so tests never collide (by phone) with people promoted in a
    prior run against the accumulated test database."""
    return "61" + f"{uuid.uuid4().int % 100000000:08d}"


def _person_for(source_contact_id):
    with engine.connect() as c:
        return c.execute(select(person_source_links.c.person_id).where(
            person_source_links.c.source_contact_id == source_contact_id)).scalar()


def test_unique_contact_is_promoted_to_a_canonical_person():
    e = f"unique-{uuid.uuid4().hex[:8]}@example.com"
    sid = _sc(email=e, phone=_phone())
    rep = promote_unlinked(source_system="Wealthbox")
    assert isinstance(rep, PromotionReport) and rep.created >= 1
    pid = _person_for(sid)
    assert pid is not None
    with engine.connect() as c:
        person = c.execute(select(people).where(people.c.id == pid)).mappings().one()
        link = c.execute(select(person_source_links).where(
            person_source_links.c.source_contact_id == sid)).mappings().one()
    assert person["primary_email"] == e and person["active"] is True   # current schema columns
    assert link["match_method"] == "auto_promote" and link["confirmed"] is True


def test_later_same_person_contact_links_to_existing_person():
    e = f"shared-{uuid.uuid4().hex[:8]}@example.com"
    ph = _phone()
    a = _sc(system="Wealthbox", email=e, phone=ph)
    promote_unlinked(source_system="Wealthbox")
    pid = _person_for(a)
    # a second source now brings the same person (matches by email/phone) -> link, no new person
    b = _sc(system="Schwab Profile", email=e, phone=ph)
    rep = promote_unlinked(source_system="Schwab Profile")
    assert rep.linked_existing >= 1
    assert _person_for(b) == pid  # linked to the SAME canonical person


def test_shared_contact_info_is_ambiguous_not_falsely_merged():
    e = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    x = _sc(email=e, phone=_phone(), first="Robert", last="Reed")
    y = _sc(email=e, phone=_phone(), first="Different", last="Reed")  # same email, different person
    rep = promote_unlinked(source_system="Wealthbox")
    assert rep.ambiguous >= 2
    assert _person_for(x) is None and _person_for(y) is None  # left for Match Review; no merge


def test_contact_without_email_or_phone_still_becomes_a_person():
    sid = _sc(email=None, phone=None, first="NoContact", last="Prospect")
    promote_unlinked(source_system="Wealthbox")
    assert _person_for(sid) is not None  # a named record with no dedup key still gets a profile


def test_promotion_is_idempotent():
    sid = _sc(email=f"idem-{uuid.uuid4().hex[:8]}@example.com", phone=_phone())
    promote_unlinked(source_system="Wealthbox")
    pid = _person_for(sid)
    with engine.connect() as c:
        before = c.execute(select(func.count()).select_from(person_source_links).where(
            person_source_links.c.source_contact_id == sid)).scalar_one()
    promote_unlinked(source_system="Wealthbox")  # re-run
    with engine.connect() as c:
        after = c.execute(select(func.count()).select_from(person_source_links).where(
            person_source_links.c.source_contact_id == sid)).scalar_one()
    assert before == after == 1 and _person_for(sid) == pid  # no duplicate person/link


def test_report_is_content_free():
    _sc(email=f"rep-{uuid.uuid4().hex[:8]}@example.com", phone=_phone())
    rep = promote_unlinked(source_system="Wealthbox")
    d = rep.to_dict()
    assert set(d) == {"inspected", "created", "linked_existing", "ambiguous"}
    assert all(isinstance(v, int) for v in d.values())
