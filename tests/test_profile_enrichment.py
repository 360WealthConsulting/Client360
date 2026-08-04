"""MDM-2 — canonical profile enrichment coverage.

Backfills null canonical people fields from linked source_contacts: fill null email/phone from a single
unambiguous source, never overwrite populated fields, skip on conflicting values, safe when the same
normalized value repeats, idempotent, person-scoped, preview makes no changes, and audit output.
"""
import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, person_source_links, source_contacts, users
from app.services.mdm import profile_enrichment as pe

_TAG = "ENRICH"


@pytest.fixture
def actor():
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        return c.execute(users.insert().values(
            email=f"en{t}@e.test", normalized_email=f"en{t}@e.test", display_name="EN",
            status="active").returning(users.c.id)).scalar_one()


def _person(**over):
    vals = {"first_name": "E", "last_name": _TAG, "full_name": f"E {_TAG} {uuid.uuid4().hex[:6]}",
            "active": True}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()


def _sc(**over):
    vals = {"source_system": "Wealthbox", "source_file": "wb.csv", "source_hash": uuid.uuid4().hex,
            "raw_data": {}}
    vals.update(over)
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(**vals).returning(source_contacts.c.id)).scalar_one()


def _link(pid, sc_id):
    with engine.begin() as c:
        c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sc_id, match_method="manual_review",
            match_score=100, confirmed=True))


def _canon(pid):
    with engine.connect() as c:
        return c.execute(text("SELECT primary_email, normalized_email, primary_phone, normalized_phone "
                              "FROM people WHERE id = :p"), {"p": pid}).mappings().first()


def _run(pid, *, apply=False, actor=None, report=None):
    return pe.enrich_people(apply=apply, person_id=pid, actor_user_id=actor, report_path=report)


# --- fill from a single source -----------------------------------------------

def test_fill_null_email_from_one_source(actor):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="Austin@Example.com"))               # normalized to lowercase
    s = _run(pid, apply=True, actor=actor)
    assert s["fields_filled"] >= 1
    assert _canon(pid)["primary_email"] == "austin@example.com"


def test_fill_null_phone_from_one_source(actor):
    pid = _person(primary_phone=None)
    _link(pid, _sc(raw_data={"home_phone": "(434) 592-8548"}))
    _run(pid, apply=True, actor=actor)
    assert _canon(pid)["primary_phone"] == "4345928548"       # normalized to digits


def test_austin_style_email_and_phone(actor):
    pid = _person(primary_email=None, primary_phone=None)
    _link(pid, _sc(raw_data={"home_email": "austinweaver4743@gmail.com", "home_phone": "4345928548"}))
    prev = _run(pid, apply=False)                             # preview proposals
    fills = {r["field"]: r["proposed_value"] for r in prev["rows"] if r["status"] == "would_fill"}
    assert fills.get("primary_email") == "austinweaver4743@gmail.com"
    assert fills.get("primary_phone") == "4345928548"


# --- never overwrite ---------------------------------------------------------

def test_does_not_overwrite_populated_field(actor):
    pid = _person(primary_email="keep@e.test")
    _link(pid, _sc(email="other@e.test"))
    _run(pid, apply=True, actor=actor)
    assert _canon(pid)["primary_email"] == "keep@e.test"      # untouched
    prev = _run(pid, apply=False)
    assert any(r["field"] == "primary_email" and r["status"] == "already_set" for r in prev["rows"])


# --- conflicts skip ----------------------------------------------------------

def test_conflicting_emails_skip(actor):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="a@x.com"))
    _link(pid, _sc(email="b@y.com"))                          # two distinct emails → conflict
    s = _run(pid, apply=True, actor=actor)
    assert _canon(pid)["primary_email"] is None
    assert any(r["field"] == "primary_email" and r["status"] == "conflict" for r in s["rows"])


def test_conflicting_phones_skip(actor):
    pid = _person(primary_phone=None)
    _link(pid, _sc(raw_data={"home_phone": "4345928548"}))
    _link(pid, _sc(raw_data={"home_phone": "2125551212"}))
    _run(pid, apply=True, actor=actor)
    assert _canon(pid)["primary_phone"] is None


def test_same_value_across_sources_is_safe(actor):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="dup@x.com"))
    _link(pid, _sc(raw_data={"work_email": "DUP@x.com"}))     # same normalized value
    _run(pid, apply=True, actor=actor)
    assert _canon(pid)["primary_email"] == "dup@x.com"


# --- idempotency + scope + preview + audit -----------------------------------

def test_idempotent_rerun(actor):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="once@x.com"))
    first = _run(pid, apply=True, actor=actor)
    assert first["fields_filled"] >= 1
    second = _run(pid, apply=True, actor=actor)
    assert second["fields_filled"] == 0                       # already set → nothing to do
    assert _canon(pid)["primary_email"] == "once@x.com"


def test_person_scoped_run_touches_only_target(actor):
    a = _person(primary_email=None)
    b = _person(primary_email=None)
    _link(a, _sc(email="a@x.com"))
    _link(b, _sc(email="b@x.com"))
    _run(a, apply=True, actor=actor)
    assert _canon(a)["primary_email"] == "a@x.com"
    assert _canon(b)["primary_email"] is None                 # unrelated person untouched


def test_preview_makes_no_changes(actor):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="preview@x.com"))
    s = _run(pid, apply=False)
    assert s["apply"] is False
    assert _canon(pid)["primary_email"] is None               # nothing written
    assert any(r["status"] == "would_fill" and r["proposed_value"] == "preview@x.com" for r in s["rows"])


def test_audit_records_source_provenance(actor):
    pid = _person(primary_email=None)
    sc = _sc(email="prov@x.com")
    _link(pid, sc)
    _run(pid, apply=True, actor=actor)
    with engine.connect() as c:
        row = c.execute(text("SELECT metadata FROM audit_events WHERE action='person.profile_enriched' "
                             "AND entity_id = :e ORDER BY id DESC LIMIT 1"),
                        {"e": str(pid)}).mappings().first()
    assert row is not None
    assert row["metadata"]["filled"]["primary_email"]["source_contact_id"] == sc


def test_report_csv_generated(actor, tmp_path):
    pid = _person(primary_email=None)
    _link(pid, _sc(email="csv@x.com"))
    report = str(tmp_path / "enrich.csv")
    _run(pid, apply=True, actor=actor, report=report)
    import csv
    with open(report) as fh:
        rows = list(csv.DictReader(fh))
    assert set(rows[0].keys()) == {"person_id", "field", "proposed_value", "source_contact_id",
                                   "status", "conflicting_values", "reason"}
