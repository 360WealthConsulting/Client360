"""MDM-2 — human-approved ambiguous-group merge coverage.

consolidator.approved_merge merges a manually-reviewed group (e.g. an all_empty_shells group the owner
confirmed is one person) using an EXPLICIT survivor + EXACT group, running the unchanged MDM-1 engine per
pair. It refuses on any blocker/warning/conflicting identifier/unexpected business evidence, preserves
source records + links, records history/audit/outbox, is idempotent, and never uses automatic survivor
selection. Automatic mode is asserted unchanged. Temp rows only; no production data.
"""
import uuid

import pytest
from sqlalchemy import text

from app.db import engine, people, person_source_links, source_contacts, users
from app.services.mdm.consolidator import MergeBlocked, approved_merge, consolidate

# Track everything created so the shared test DB is left clean (prevents cross-suite pollution).
_CREATED = {"people": set(), "sc": set(), "docs": set()}


@pytest.fixture(autouse=True)
def _cleanup():
    _CREATED["people"].clear(); _CREATED["sc"].clear(); _CREATED["docs"].clear()
    yield
    from sqlalchemy import bindparam
    pids = list(_CREATED["people"]) or [-1]
    scs = list(_CREATED["sc"]) or [-1]
    docs = list(_CREATED["docs"]) or [-1]
    with engine.begin() as c:
        for stmt, ids in (
            ("DELETE FROM person_merge_history WHERE merged_person_id IN :i OR survivor_person_id IN :i", pids),
            ("DELETE FROM documents WHERE id IN :i", docs),
            ("DELETE FROM relationship_entities WHERE person_id IN :i", pids),
            ("DELETE FROM person_source_links WHERE person_id IN :i OR source_contact_id IN :s", None),
            ("DELETE FROM source_contacts WHERE id IN :s", scs),
            ("DELETE FROM people WHERE id IN :i", pids),
        ):
            try:
                if "source_contact_id IN :s" in stmt:
                    c.execute(text(stmt).bindparams(bindparam("i", expanding=True),
                                                    bindparam("s", expanding=True)), {"i": pids, "s": scs})
                elif ":s" in stmt:
                    c.execute(text(stmt).bindparams(bindparam("s", expanding=True)), {"s": scs})
                else:
                    c.execute(text(stmt).bindparams(bindparam("i", expanding=True)), {"i": ids})
            except Exception:      # noqa: BLE001 — best-effort teardown
                pass


@pytest.fixture
def actor():
    # Explicit HIGH id: merges write append-only person.merged audit_events for this actor, and a user
    # with audit rows can never be deleted (SET NULL is blocked by the append-only trigger). Using a
    # high, dedicated id keeps these actors out of the low serial range that other suites (e.g. the vault
    # fixture) hardcode + delete, and an explicit id does not advance the users serial sequence.
    with engine.begin() as c:
        t = uuid.uuid4().hex[:8]
        uid = 900_000_000 + int(uuid.uuid4().hex[:7], 16) % 90_000_000
        return c.execute(users.insert().values(
            id=uid, email=f"ap{t}@e.test", normalized_email=f"ap{t}@e.test", display_name="AP",
            status="active").returning(users.c.id)).scalar_one()


@pytest.fixture
def gname():
    return f"Alyssa {uuid.uuid4().hex[:8]}"


def _person(full_name, **over):
    vals = {"first_name": "Alyssa", "last_name": "W", "full_name": full_name, "active": True}
    vals.update(over)
    with engine.begin() as c:
        pid = c.execute(people.insert().values(**vals).returning(people.c.id)).scalar_one()
    _CREATED["people"].add(pid)
    return pid


def _sc(**over):
    vals = {"source_system": "Dave Ramsey", "source_file": "dr.csv", "source_hash": uuid.uuid4().hex,
            "raw_data": {}}
    vals.update(over)
    with engine.begin() as c:
        sc = c.execute(source_contacts.insert().values(**vals).returning(source_contacts.c.id)).scalar_one()
    _CREATED["sc"].add(sc)
    return sc


def _doc(pid):
    from app.db import documents
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name="x.pdf", stored_name=f"x-{uuid.uuid4().hex[:8]}", storage_path="/x",
            storage_provider="Client360 Local", size_bytes=1, sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            person_id=pid, status="active", archived=False).returning(documents.c.id)).scalar_one()
    _CREATED["docs"].add(did)
    return did


def _link(pid, sc_id):
    with engine.begin() as c:
        c.execute(person_source_links.insert().values(
            person_id=pid, source_contact_id=sc_id, match_method="lead", match_score=100, confirmed=True))


def _relationship_entity(pid):
    with engine.begin() as c:
        c.execute(text("INSERT INTO relationship_entities (entity_type, name, person_id) "
                       "VALUES ('individual', 'e', :p)"), {"p": pid})


def _exists(pid):
    with engine.connect() as c:
        return c.execute(text("SELECT 1 FROM people WHERE id=:p"), {"p": pid}).first() is not None


def _sc_exists(scid):
    with engine.connect() as c:
        return c.execute(text("SELECT 1 FROM source_contacts WHERE id=:s"), {"s": scid}).first() is not None


def _shell_group(gname, n):
    """n distinct empty-shell people (same name), each with its own distinct Dave Ramsey source contact."""
    members, scs = [], []
    for _ in range(n):
        pid = _person(gname)
        sc = _sc(source_record_id=uuid.uuid4().hex)   # distinct lead per submission
        _link(pid, sc)
        members.append(pid); scs.append(sc)
    return members, scs


def _run(gname, survivor, *, apply=False, actor=None, ids=None, report=None):
    return approved_merge(group_name=gname, survivor_person_id=survivor, apply=apply,
                          actor_user_id=actor, restrict_ids=ids, report_path=report)


# --- preview + apply ---------------------------------------------------------

def test_approved_all_empty_shell_preview(gname, actor):
    members, _ = _shell_group(gname, 3)
    survivor = members[0]
    s = _run(gname, survivor, apply=False, actor=actor, ids=members)
    assert s["refused"] is False and s["merged"] == 0
    assert all(r["status"] == "would_merge" for r in s["rows"])
    assert all(_exists(m) for m in members)                    # preview: nothing changed


def test_approved_all_empty_shell_apply(gname, actor):
    members, scs = _shell_group(gname, 3)
    survivor = members[0]
    s = _run(gname, survivor, apply=True, actor=actor, ids=members)
    assert s["merged"] == 2 and s["refused"] is False
    assert _exists(survivor) and all(not _exists(m) for m in members[1:])
    assert all(_sc_exists(sc) for sc in scs)                   # every distinct lead source preserved


# --- input requirements ------------------------------------------------------

def test_explicit_survivor_required(gname, actor):
    members, _ = _shell_group(gname, 2)
    with pytest.raises(ValueError):
        approved_merge(group_name=gname, survivor_person_id=None, apply=True, restrict_ids=members)


def test_exact_group_required(gname, actor):
    members, _ = _shell_group(gname, 2)
    with pytest.raises(ValueError):
        approved_merge(group_name=None, survivor_person_id=members[0], apply=True, restrict_ids=members)


def test_survivor_must_belong_to_group(gname, actor):
    members, _ = _shell_group(gname, 2)
    outsider = _person(f"Someone {uuid.uuid4().hex[:6]}")
    with pytest.raises(ValueError, match="does not belong"):
        _run(gname, outsider, apply=True, actor=actor, ids=members + [outsider])


# --- refusals ----------------------------------------------------------------

def test_blocker_causes_refusal(gname, actor):
    members, _ = _shell_group(gname, 2)
    survivor, dup = members
    _relationship_entity(survivor); _relationship_entity(dup)   # engine blocks the pair
    with pytest.raises(MergeBlocked):
        _run(gname, survivor, apply=True, actor=actor, ids=members)
    assert _exists(survivor) and _exists(dup)                   # refused → nothing merged


def test_warning_causes_refusal(gname, actor):
    # A populated field that differs (preferred_name) makes preview_person_merge warn — not an identity
    # conflict — so approved mode must still refuse.
    members = [_person(gname, preferred_name="Al"), _person(gname, preferred_name="Ally")]
    for pid in members:
        _link(pid, _sc())
    with pytest.raises(MergeBlocked, match="warning"):
        _run(gname, members[0], apply=True, actor=actor, ids=members)
    assert all(_exists(m) for m in members)


def test_conflicting_identifiers_cause_refusal(gname, actor):
    a, b = _person(gname), _person(gname)
    _link(a, _sc(email="a@x.com")); _link(b, _sc(email="b@x.com"))   # distinct emails → conflict
    with pytest.raises(MergeBlocked, match="conflicting"):
        _run(gname, a, apply=True, actor=actor, ids=[a, b])
    assert _exists(a) and _exists(b)


def test_unexpected_business_evidence_refused(gname, actor):
    members, _ = _shell_group(gname, 2)
    survivor, dup = members
    _doc(dup)                                                  # duplicate unexpectedly owns a document
    with pytest.raises(MergeBlocked, match="unexpected business evidence"):
        _run(gname, survivor, apply=True, actor=actor, ids=members)
    assert _exists(dup)


# --- preservation + history/audit/outbox + idempotency -----------------------

def test_source_links_preserved_and_reassigned(gname, actor):
    members, scs = _shell_group(gname, 3)
    survivor = members[0]
    _run(gname, survivor, apply=True, actor=actor, ids=members)
    with engine.connect() as c:
        owners = list(c.scalars(text("SELECT DISTINCT person_id FROM person_source_links "
                                     "WHERE source_contact_id = ANY(:s)").bindparams(s=scs)))
    assert owners == [survivor]                                # all links now on the survivor
    assert all(_sc_exists(sc) for sc in scs)                   # source records intact


def test_history_audit_outbox_recorded(gname, actor):
    members, _ = _shell_group(gname, 2)
    survivor, dup = members
    _run(gname, survivor, apply=True, actor=actor, ids=members)
    with engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM person_merge_history WHERE merged_person_id=:d"),
                        {"d": dup}) == 1
        assert c.scalar(text("SELECT count(*) FROM audit_events WHERE action='person.merged' "
                             "AND entity_id=:e"), {"e": str(survivor)}) >= 1
        assert c.scalar(text("SELECT count(*) FROM outbox_events WHERE name='people.person_merged'")) >= 1


def test_idempotent_rerun(gname, actor):
    members, _ = _shell_group(gname, 2)
    survivor = members[0]
    first = _run(gname, survivor, apply=True, actor=actor, ids=members)
    assert first["merged"] == 1
    second = _run(gname, survivor, apply=True, actor=actor, ids=members)
    assert second["merged"] == 0                               # nothing to repeat


# --- automatic mode unchanged ------------------------------------------------

def test_automatic_mode_still_leaves_all_empty_ambiguous(gname, actor):
    members, _ = _shell_group(gname, 3)
    s = consolidate(apply=True, actor_user_id=actor, restrict_ids=members)
    assert s["ambiguous"] == 1 and s["merged"] == 0            # automatic never auto-merges empty shells
    assert all(_exists(m) for m in members)
