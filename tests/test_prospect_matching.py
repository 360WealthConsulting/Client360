"""READ-ONLY existing-person matching for a detected prospect.

Two things are pinned here beyond the obvious: the tiers never merge (a weak name hit cannot dilute
a strong email hit), and the normalisation used to search is the SAME implementation that writes
``people.normalized_email`` / ``normalized_phone``. Searching with a different normaliser than the
writer is how an "exact match" silently stops being exact.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, insert, select

from app.db import engine, people
from app.services.prospect_matching import find_matches, match_for_candidate

_TAGS: list[str] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    for tag in _TAGS:
        with engine.begin() as c:
            c.execute(delete(people).where(people.c.last_name.like(f"%{tag}%")))
    _TAGS.clear()


def _tag():
    t = "PM" + uuid.uuid4().hex[:8]
    _TAGS.append(t)
    return t


def _person(tag, *, first, last, email=None, phone=None, contact_type="client",
            full_name=None):
    from app.security.identity_utils import normalize_email
    from app.services.people import _normalize_phone
    with engine.begin() as c:
        return c.execute(insert(people).values(
            first_name=first, last_name=f"{last}{tag}", full_name=full_name,
            primary_email=email, normalized_email=normalize_email(email) or None,
            primary_phone=phone, normalized_phone=_normalize_phone(phone),
            contact_type=contact_type, active=True).returning(people.c.id)).scalar_one()


# --------------------------------------------------------------- normalisation agreement
def test_search_normalisers_are_the_ones_that_write_the_columns():
    """If these ever diverge, 'exact match' stops being exact."""
    import app.services.prospect_matching as pm
    from app.security.identity_utils import normalize_email as writer_email
    from app.services.people import _normalize_phone as writer_phone
    assert pm.normalize_email is writer_email
    assert pm._normalize_phone is writer_phone


def test_the_gmail_folding_normaliser_is_deliberately_not_used():
    """app.matching.matcher.normalize_email folds gmail dots; people.normalized_email does not."""
    import app.services.prospect_matching as pm
    from app.matching.matcher import normalize_email as gmail_folding
    assert pm.normalize_email is not gmail_folding
    assert gmail_folding("j.doe@gmail.com") != pm.normalize_email("j.doe@gmail.com")
    # and the stored form is the unfolded one, which is what we search for
    assert pm.normalize_email("J.Doe@Gmail.com") == "j.doe@gmail.com"


# --------------------------------------------------------------- D. exact email match
def test_d_exact_email_match_returns_one_strong_match():
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    r = find_matches(email=f"jane.{tag}@example.com")
    assert r["outcome"] == "one" and r["strategy"] == "email"
    assert [m["person_id"] for m in r["matches"]] == [pid]
    m = r["matches"][0]
    assert m["display_name"] == f"Jane Prospect{tag}"
    assert m["workspace_url"] == f"/client/{pid}"
    assert m["contact_type"] == "client"


def test_email_match_is_case_insensitive():
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    r = find_matches(email=f"JANE.{tag.upper()}@EXAMPLE.COM".replace(tag.upper(), tag))
    assert [m["person_id"] for m in r["matches"]] == [pid]


def test_email_wins_over_a_weaker_name_hit():
    """Tiers never merge: an email hit must not be padded with same-surname people."""
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    _person(tag, first="John", last="Prospect")
    r = find_matches(email=f"jane.{tag}@example.com", name=f"Jane Prospect{tag}")
    assert r["strategy"] == "email"
    assert [m["person_id"] for m in r["matches"]] == [pid]


def test_phone_tier_is_used_only_when_email_finds_nothing():
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", phone="(415) 555-0134")
    r = find_matches(email=f"nobody.{tag}@example.com", phone="415-555-0134")
    assert r["strategy"] == "phone"
    assert [m["person_id"] for m in r["matches"]] == [pid]


def test_phone_match_ignores_formatting():
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", phone="415.555.0134")
    assert [m["person_id"] for m in find_matches(phone="4155550134")["matches"]] == [pid]


# --------------------------------------------------------------- E. multiple candidates
def test_e_multiple_name_candidates_require_selection():
    tag = _tag()
    a = _person(tag, first="Jane", last="Prospect")
    b = _person(tag, first="Janet", last="Prospect")
    r = find_matches(name=f"Jane Prospect{tag}")
    assert r["outcome"] == "multiple" and r["strategy"] == "name"
    assert {m["person_id"] for m in r["matches"]} == {a, b}


def test_full_name_exact_match_is_found():
    tag = _tag()
    pid = _person(tag, first=None, last="X", full_name=f"Jane Prospect{tag}")
    r = find_matches(name=f"  jane   prospect{tag} ")
    assert pid in {m["person_id"] for m in r["matches"]}


# --------------------------------------------------------------- F. no match
def test_f_no_match_returns_none_outcome():
    tag = _tag()
    _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    r = find_matches(email=f"stranger.{tag}@example.com", phone="212-555-0000",
                     name=f"Nobody Here{tag}")
    assert r == {"strategy": None, "matches": [], "outcome": "none"}


def test_empty_candidate_matches_nothing_rather_than_everything():
    tag = _tag()
    _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    assert find_matches()["outcome"] == "none"
    assert find_matches(email="", phone="", name="   ")["outcome"] == "none"


def test_match_for_candidate_reads_the_extractor_shape():
    tag = _tag()
    pid = _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    from app.services.forwarded_email import extract_candidate
    body = (f"<div>From: Jane Prospect &lt;jane.{tag}@example.com&gt;<br>Sent: Mon<br>"
            f"To: Lauren<br>Subject: Hi<br></div>")
    cand = extract_candidate(body=body, subject="FW: Hi", graph_from_name="Lauren",
                             graph_from_email="lauren@firm.test")
    r = match_for_candidate(cand)
    assert [m["person_id"] for m in r["matches"]] == [pid]


# --------------------------------------------------------------- read-only
def test_matching_writes_nothing():
    tag = _tag()
    _person(tag, first="Jane", last="Prospect", email=f"jane.{tag}@example.com")
    with engine.connect() as c:
        before = c.scalar(select(func.count()).select_from(people))
    find_matches(email=f"jane.{tag}@example.com")
    find_matches(name=f"Jane Prospect{tag}")
    find_matches(email=f"nobody.{tag}@example.com")
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(people)) == before
