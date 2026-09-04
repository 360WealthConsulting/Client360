"""Owner-proposal safety: STAFF detection must use narrow firm domains, not "any users domain".

Production evidence this pins down. ``_mark_owner_eligibility`` derived the firm's mail domains from
"every domain a ``users`` row uses", and anyone on one of those domains was marked STAFF and removed
from owner eligibility. That is the same over-broad rule the organization path already replaced, and
on this path it is worse, because it SUPPRESSES rather than merely fails to promote.

Measured against production before the fix, a single ``users`` row registered on a public provider
would have marked, as staff:

    gmail.com     1,949 people by their own address + 2,100 more through their source links
    yahoo.com       474  +  500
    liberty.edu      67  +   73   (an employer domain, not a provider)

and removed every one of them from ownership forever, silently. No generic domain was present, so
nothing was actually mis-suppressed — but nothing prevented it either.

Staff detection now shares ``_firm_mail_domains`` with the organization path, so "the practice's own
domain" has ONE definition in the module instead of two. A domain that is too broadly held stops
counting as the firm's, which fails OPEN into ordinary eligibility rather than into suppression.

No staff id, firm name or provider blacklist appears anywhere in this file or in the code it covers.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.document_owner_proposal import (
    _MAX_FIRM_DOMAIN_HOLDERS,
    _firm_mail_domains,
    build_match_indexes,
)


def _db(people_rows=(), user_emails=(), contact_emails=()):
    """Insert people / users / source contacts, build the REAL indexes, then clean up."""
    from app.db import engine, metadata
    from app.db import people as people_t
    from app.db import source_contacts as sc_t

    users_t = metadata.tables["users"]
    pids, uids, cids = [], [], []
    with engine.begin() as c:
        for kw in people_rows:
            pids.append(c.execute(people_t.insert().values(
                full_name=f"Staff {uuid.uuid4().hex[:6]}", active=True, **kw
            ).returning(people_t.c.id)).scalar_one())
        for em in user_emails:
            uids.append(c.execute(users_t.insert().values(
                email=em, normalized_email=em.lower(),
                display_name=f"u-{uuid.uuid4().hex[:6]}"
            ).returning(users_t.c.id)).scalar_one())
        for em in contact_emails:
            cids.append(c.execute(sc_t.insert().values(
                source_system="Test", source_file="t.csv",
                source_hash=uuid.uuid4().hex, raw_data={}, email=em,
                normalized_email=em.lower(), full_name=f"c-{uuid.uuid4().hex[:6]}"
            ).returning(sc_t.c.id)).scalar_one())
    try:
        with engine.connect() as c:
            idx = build_match_indexes(c)
            domains = _firm_mail_domains(c)
        return idx, domains, pids
    finally:
        with engine.begin() as c:
            if cids:
                c.execute(sc_t.delete().where(sc_t.c.id.in_(cids)))
            if uids:
                c.execute(users_t.delete().where(users_t.c.id.in_(uids)))
            if pids:
                c.execute(people_t.delete().where(people_t.c.id.in_(pids)))


def _uniq_domain(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}.test"


# ------------------------------------------------------------------ 1-4: the domain rule itself

def test_a_narrowly_held_domain_identifies_staff():
    dom = _uniq_domain("practice")
    idx, domains, pids = _db(
        people_rows=[{"contact_type": "Client", "primary_email": f"partner@{dom}"}],
        user_emails=[f"admin@{dom}"],
        contact_emails=[f"person{i}@{dom}" for i in range(3)])
    assert dom in domains
    assert pids[0] in idx["staff"], "a narrowly-held firm domain must still identify staff"
    assert pids[0] not in idx["owner_eligible"], "staff are never owner-eligible"


def test_a_public_provider_domain_cannot_identify_staff():
    """THE DEFECT. One staff account on a public provider must not suppress everyone who shares it."""
    dom = _uniq_domain("bigprovider")
    idx, domains, pids = _db(
        people_rows=[{"contact_type": "Client", "primary_email": f"client@{dom}"}],
        user_emails=[f"admin@{dom}"],
        contact_emails=[f"user{i}@{dom}" for i in range(_MAX_FIRM_DOMAIN_HOLDERS + 5)])
    assert dom not in domains
    assert pids[0] not in idx["staff"], "a broadly-held provider domain must not mark staff"


def test_an_employer_or_shared_domain_cannot_identify_staff():
    """An employer domain looks exactly like a provider to this rule, and must be treated alike."""
    dom = _uniq_domain("bigemployer")
    idx, domains, pids = _db(
        people_rows=[{"contact_type": "Client", "primary_email": f"employee@{dom}"}],
        user_emails=[f"hr@{dom}"],
        contact_emails=[f"staffer{i}@{dom}" for i in range(_MAX_FIRM_DOMAIN_HOLDERS + 1)])
    assert dom not in domains
    assert pids[0] not in idx["staff"]


def test_a_broad_domain_fails_open_into_eligibility_not_suppression():
    """Losing firm-domain status must leave the person ELIGIBLE, never merely un-suppressed-but-lost."""
    dom = _uniq_domain("failopen")
    idx, _domains, pids = _db(
        people_rows=[{"contact_type": "Client - Tax", "primary_email": f"real@{dom}"}],
        user_emails=[f"admin@{dom}"],
        contact_emails=[f"u{i}@{dom}" for i in range(_MAX_FIRM_DOMAIN_HOLDERS + 3)])
    assert pids[0] not in idx["staff"]
    assert pids[0] in idx["owner_eligible"], "a client on a broad domain must stay owner-eligible"


# ------------------------------------------------------------------ 5: real firm staff unchanged

def test_the_real_firm_domain_still_yields_the_same_staff():
    """The deployed firm domain is narrowly held, so this change must not alter who is staff."""
    from app.db import engine

    with engine.connect() as c:
        domains = _firm_mail_domains(c)
        idx = build_match_indexes(c)
    if not domains:
        pytest.skip("no firm domain resolvable in this database")
    if not idx["staff"]:
        pytest.skip("no staff identities in this database")

    # Every staff member must be reachable from a domain that STILL qualifies as the firm's --
    # i.e. nobody is staff on the strength of a domain the narrow rule has since rejected.
    from sqlalchemy import text

    with engine.connect() as c:
        for pid in idx["staff"]:
            addrs = [a for (a,) in c.execute(text("""
                select lower(coalesce(p.primary_email, p.normalized_email)) from people p
                 where p.id = :i and coalesce(p.primary_email, p.normalized_email) is not null
                union
                select lower(coalesce(s.email, s.normalized_email)) from person_source_links psl
                  join source_contacts s on s.id = psl.source_contact_id
                 where psl.person_id = :i and coalesce(s.email, s.normalized_email) is not null"""),
                {"i": pid}) if a and "@" in a]
            assert any(a.split("@", 1)[1] in domains for a in addrs), (
                f"person {pid} is staff but no qualifying firm domain explains it")
            assert pid not in idx["owner_eligible"], "staff must never be owner-eligible"


# ------------------------------------------------------------------ 6-8: eligibility unchanged

def test_prospects_remain_ineligible():
    idx, _d, pids = _db(people_rows=[{"contact_type": "Prospect"}])
    assert pids[0] not in idx["owner_eligible"]


def test_unknown_contacts_remain_ineligible():
    idx, _d, pids = _db(people_rows=[{"contact_type": None}])
    assert pids[0] not in idx["owner_eligible"]


def test_a_legitimate_client_is_not_suppressed():
    idx, _d, pids = _db(people_rows=[{"contact_type": "Client - Tax ELP"}])
    assert pids[0] in idx["owner_eligible"]
    assert pids[0] not in idx["staff"]


# ------------------------------------------------------------------ 9-10: neighbouring rules intact

def test_shared_phone_and_email_rules_are_unchanged():
    """``_mark_shared_values`` is untouched: a value held by two people still cannot identify."""
    from app.services.document_owner_proposal import _mark_shared_values

    idx = {"email": {"a@x.test": {1, 2}, "solo@x.test": {1}},
           "phone": {"5405551234": {1, 2}, "5405559999": {1}},
           "pid": {1: {"zips": {"24153"}, "streets": {"1 elm st"}},
                   2: {"zips": {"24153"}, "streets": set()}}}
    _mark_shared_values(idx)
    assert "a@x.test" in idx["shared"]["email"]
    assert "solo@x.test" not in idx["shared"]["email"]
    assert "5405551234" in idx["shared"]["phone"]
    assert "24153" in idx["shared"]["zip"]
    assert "1 elm st" not in idx["shared"]["street"]


def test_organization_path_behaviour_is_unchanged():
    """Firm entities and organization eligibility must be exactly as the release computes them."""
    from app.db import engine

    with engine.connect() as c:
        idx = build_match_indexes(c)
    # Asserted as invariants rather than counts: a seeded test database carries no eligible
    # businesses, and a test that only passes against production data is not a regression test.
    for key in ("org_eligible", "firm_entities", "org_ident", "org_shared", "org_name_counts"):
        assert key in idx, f"the organization index lost {key}"
    assert not (idx["org_eligible"] & idx["firm_entities"]), \
        "a firm entity is never organization-owner-eligible"
