"""Strict-safe ownership BATCH 3 — the two support rules, the guarded apply, and the rollback.

Batch 3 assigns real client documents on a filename that names the proposed person plus ONE contact
corroborator, where the SharePoint folder names somebody ELSE. A folder naming a different person is
normally evidence AGAINST a proposal, so each row must carry one of two supports that is proved
against live database rows rather than against the folder string:

    HOUSEHOLD_PATH_MEMBER      the folder names a CURRENT member of the proposed person's household
    SAME_FILENAME_SAME_OWNER   a live document with the same normalized filename is ALREADY owned
                               by the proposed person

Every test below pins one way that could go wrong: a household membership that has been dissolved, a
member who was never in the household, a folder renamed, a source gone, a duplicate deleted /
archived / unowned / re-owned / renamed, a proposal that moved, a manifest that is not the reviewed
one, a partial write, or a rollback that reverses somebody else's batch.

The fixture builds a small batch and patches the approved counts to match, so the real code path runs
without the 5-row production manifest. The frozen batch is exercised against its real values in
test_frozen_batch3_plan_is_reproduced_from_live_data.

Temp rows only, all tagged, all cleaned up. Nothing here writes to a production database.
"""
from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import delete, select, text

from app.db import documents, engine, metadata, people
from app.services import document_strict_safe_ownership_batch3 as b3
from scripts import apply_strict_safe_ownership_batch3 as ap
from scripts import rollback_strict_safe_ownership_batch3 as rb

_TAG = f"SSOTHREE{uuid.uuid4().hex[:6]}"

MANIFEST_COLUMNS = ["document_id", "proposed_person_id", "proposed_person_name", "original_name",
                    "corroborator", "rule", "support_json"]

FROZEN_DIR = Path(r"C:\Client360\reports\strict-safe-ownership-batch3-6df558979ed8")
FROZEN_CSV = FROZEN_DIR / "strict_safe_ownership_batch3_manifest.csv"
FROZEN_JSON = FROZEN_DIR / "strict_safe_ownership_batch3_manifest.json"
FROZEN_CSV_SHA = "881ad49e7ec35760b1786b18ffa14a8c56badc43a3dc7b9960efaa22db650599"
FROZEN_JSON_SHA = "970f5ae621c80dcee44e5a4e86b0a685071ed7a0be722b28ec1eaa5c23eecc51"
FROZEN_DIGEST = "6df558979ed8666b09de17ae8754316268387ba1d4550f670499939aedf1ac33"


@pytest.fixture(autouse=True)
def _clean():
    yield
    facts = metadata.tables["document_facts"]
    sources = metadata.tables["document_sources"]
    households = metadata.tables["households"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"sso3:{_TAG}%")))]
        if ids:
            c.execute(delete(sources).where(sources.c.document_id.in_(ids)))
            c.execute(delete(facts).where(facts.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))
        c.execute(delete(households).where(households.c.name.like(f"HH {_TAG}%")))


# --- builders -----------------------------------------------------------------

def _household() -> int:
    households = metadata.tables["households"]
    with engine.begin() as c:
        return c.execute(households.insert().values(name=f"HH {_TAG} {uuid.uuid4().hex[:6]}")
                         .returning(households.c.id)).scalar_one()


def _person(first: str, household_id=None) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=_TAG, full_name=f"{first} {_TAG}",
            household_id=household_id, active=True).returning(people.c.id)).scalar_one()


def _evidence(person_name, *, exact_name=True, email=False, phone=False, address=True):
    """Proposal evidence in the engine's own vocabulary. Default: name + ONE ADDRESS corroborator."""
    ev = []
    if exact_name:
        ev.append(f"✓ exact name '{person_name}'")
    if email:
        ev.append("✓ email someone@example.com matched")
    if phone:
        ev.append("✓ phone ending 0123 matched")
    if address:
        ev.append("✓ address/ZIP matched")
    ev.append("context only (not an owner): irs")
    return ev


def _uri(folder: str, filename: str, *, ancestry=("Clients", "Individual")) -> str:
    parts = "/".join(quote(p) for p in (*ancestry, folder, "2021", filename))
    return f"https://example.sharepoint.com/sites/Data/Shared%20Documents/{parts}"


def _doc(person_id, first, *, route="HIGH", entity_type="person", evidence=None,
         review_status="not_required", archived=False, status="active", owner=None,
         filename=None, folder=None, source_system="SharePoint", available=True,
         with_source=True, uri=None, rival_proposal_person_id=None, entity_name=None) -> int:
    """One candidate document: a current owner_proposal plus (optionally) a SharePoint source."""
    person_name = entity_name if entity_name is not None else f"{first} {_TAG}"
    filename = filename if filename is not None else f"{first} {_TAG} 2021.pdf"
    sources = metadata.tables["document_sources"]
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=filename, stored_name=f"sso3:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status=review_status, current_version=1, person_id=owner,
            tags={"source_system": "SharePoint"},
        ).returning(documents.c.id)).scalar_one()
        proposals = [(person_id, person_name)]
        if rival_proposal_person_id is not None:
            proposals.append((rival_proposal_person_id, f"Rival {_TAG}"))
        for version, (entity_id, name) in enumerate(proposals, start=1):
            c.execute(facts.insert().values(
                document_id=did, fact_type="owner_proposal",
                fact_value=json.dumps({
                    "route": route, "confidence": "HIGH", "entity_type": entity_type,
                    "entity_id": entity_id, "entity_name": name,
                    "evidence": evidence if evidence is not None else _evidence(name),
                }),
                confidence=0.0, extraction_engine="owner_proposal", extractor_version="test",
                version=version, is_current=True))
        if with_source and folder is not None:
            c.execute(sources.insert().values(
                document_id=did, source_system=source_system,
                source_uri=uri if uri is not None else _uri(folder, filename),
                source_external_id=f"EXT{did}", source_hash="f" * 64,
                available=available, metadata={}))
    return did


def _household_doc(proposed_person_id, first, member_first, **kw) -> int:
    """A document whose folder names ``member_first``, never the proposed person."""
    kw.setdefault("folder", f"{_TAG}, {member_first}")
    return _doc(proposed_person_id, first, **kw)


def _duplicate_pair(person_id, first, *, dup_status="active", dup_archived=False,
                    dup_owner="same", dup_filename=None, **kw) -> tuple[int, int]:
    """An unowned target plus an already-owned document with the same normalized filename."""
    filename = f"{first} {_TAG} originals.pdf"
    target = _doc(person_id, first, filename=filename, with_source=False, **kw)
    owner = person_id if dup_owner == "same" else dup_owner
    dup = _doc(person_id, first, filename=dup_filename or filename, with_source=False,
               owner=owner, status=dup_status, archived=dup_archived)
    return target, dup


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents).where(documents.c.id == did)).mappings().one()


def _plan():
    return b3.build_plan()


def _plan_by_id(*dids):
    return {r["document_id"]: r for r in _plan() if r["document_id"] in set(dids)}


def _plan_ids(*dids):
    return set(_plan_by_id(*dids))


def _write_manifest(tmp_path, plan_rows, *, mutate=None, name="manifest.csv") -> tuple[Path, str]:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(plan_rows, key=lambda x: x["document_id"]):
            row = {
                "document_id": r["document_id"],
                "proposed_person_id": r["proposed_person_id"],
                "proposed_person_name": r["proposed_person_name"],
                "original_name": r["original_name"],
                "corroborator": r["corroborator"],
                "rule": r["rule"],
                "support_json": json.dumps(r["support"], sort_keys=True, ensure_ascii=False),
            }
            if mutate is not None:
                row = mutate(row)
            w.writerow(row)
    return path, ap.sha256_of(path)


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Two HOUSEHOLD_PATH_MEMBER rows and one SAME_FILENAME_SAME_OWNER row, plus a matching manifest."""
    hh = _household()
    member = _person("Wayne", household_id=hh)
    p1 = _person("Kristena", household_id=hh)
    p2 = _person("Dolores", household_id=hh)
    p3 = _person("Desiderio")                      # no household: duplicate rule only
    d1 = _household_doc(p1, "Kristena", "Wayne")
    d2 = _household_doc(p2, "Dolores", "Wayne")
    d3, dup = _duplicate_pair(p3, "Desiderio")
    ids = sorted([d1, d2, d3])
    plan = [r for r in _plan() if r["document_id"] in set(ids)]
    assert len(plan) == 3, f"fixture must be batch-3 eligible; got {len(plan)}"
    assert {r["rule"] for r in plan} == {b3.RULE_HOUSEHOLD_PATH_MEMBER,
                                         b3.RULE_SAME_FILENAME_SAME_OWNER}
    path, sha = _write_manifest(tmp_path, plan)
    monkeypatch.setattr(ap, "EXPECTED_ROWS", 3)
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 3)
    monkeypatch.setattr(ap, "EXPECTED_RULES", {"HOUSEHOLD_PATH_MEMBER": 2,
                                               "SAME_FILENAME_SAME_OWNER": 1})
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 3})
    return {"ids": ids, "plan": plan, "path": path, "sha": sha, "tmp_path": tmp_path,
            "household_id": hh, "member": member, "p1": p1, "p2": p2, "p3": p3,
            "d1": d1, "d2": d2, "d3": d3, "dup": dup,
            "digest": b3.plan_digest(_plan()),
            "snapshot_root": tmp_path / "snap"}


def _run(batch, **kw):
    kw.setdefault("expect_sha", batch["sha"])
    kw.setdefault("expect_plan_digest", batch["digest"])
    kw.setdefault("expect_rows", len(batch["ids"]))
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["path"], **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=1, **kw)


def _request_id(sha):
    return f"strict-safe-ownership-batch3:{b3.BATCH_ID}:{sha[:12]}"


def _audit_rows(request_id):
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        return c.execute(select(audit.c.action, audit.c.entity_id, audit.c.metadata)
                         .where(audit.c.request_id == request_id)).mappings().all()


def _snap_dir(batch):
    return next(Path(batch["snapshot_root"]).glob("strict-safe-ownership-batch3-apply-*"))


# --- the frozen batch ----------------------------------------------------------

def _frozen():
    if not FROZEN_JSON.is_file():
        pytest.skip("frozen batch 3 manifest not present on this machine")
    return json.loads(FROZEN_JSON.read_text(encoding="utf-8"))


def test_frozen_manifest_shas_and_constants_are_the_reviewed_ones():
    frozen = _frozen()
    assert ap.sha256_of(FROZEN_CSV) == FROZEN_CSV_SHA
    assert ap.sha256_of(FROZEN_JSON) == FROZEN_JSON_SHA
    assert frozen["plan_digest"] == FROZEN_DIGEST
    assert frozen["batch_id"] == b3.BATCH_ID == "STRICT-SAFE-OWNERSHIP-3"
    assert frozen["confirmation_phrase"] == ap.confirm_phrase(5) == "APPLY-STRICT-SAFE-OWNERSHIP-3-5"
    assert rb.confirm_phrase(5) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-3-5"
    assert frozen["rows"] == ap.EXPECTED_ROWS == 5
    assert frozen["distinct_people"] == ap.EXPECTED_DISTINCT_PEOPLE == 5
    rules: dict[str, int] = {}
    corro: dict[str, int] = {}
    for r in frozen["plan"]:
        rules[r["rule"]] = rules.get(r["rule"], 0) + 1
        corro[r["corroborator"]] = corro.get(r["corroborator"], 0) + 1
    assert rules == ap.EXPECTED_RULES == {"HOUSEHOLD_PATH_MEMBER": 4, "SAME_FILENAME_SAME_OWNER": 1}
    assert corro == ap.EXPECTED_COMPOSITION == {"ADDRESS": 5}


def test_frozen_batch3_plan_is_reproduced_from_live_data():
    """The rule must rebuild the reviewed five rows, field for field, from this database."""
    frozen = _frozen()
    expected = sorted(frozen["plan"], key=lambda r: r["document_id"])
    wanted = {r["document_id"] for r in expected}
    live = {r["document_id"]: r for r in _plan() if r["document_id"] in wanted}
    missing = sorted(wanted - set(live))
    if missing:
        pytest.skip(f"this database does not hold the frozen batch 3 documents: {missing}")
    got = [{k: live[r["document_id"]][k] for k in b3.PLAN_FIELDS} for r in expected]
    exp = [{k: r[k] for k in b3.PLAN_FIELDS} for r in expected]
    assert got == exp
    assert b3.plan_digest(list(live.values())) == FROZEN_DIGEST
    assert b3.plan_census(list(live.values())) == {
        "rows": 5, "distinct_people": 5,
        "by_rule": {"HOUSEHOLD_PATH_MEMBER": 4, "SAME_FILENAME_SAME_OWNER": 1},
        "by_corroborator": {"ADDRESS": 5}}


def test_every_frozen_support_still_proves_against_live_rows():
    """Each reviewed row's recorded support — household or duplicate — re-proves under lock."""
    frozen = _frozen()
    with engine.connect() as c:
        for row in frozen["plan"]:
            exists = c.execute(select(documents.c.id)
                               .where(documents.c.id == row["document_id"])).first()
            if not exists:
                pytest.skip(f"document {row['document_id']} is not in this database")
            assert b3.verify_support(c, row) is None, row["document_id"]


def test_batch1_and_batch2_semantics_are_untouched():
    from app.services import document_strict_safe_ownership as b1
    from app.services import document_strict_safe_ownership_batch2 as b2
    assert b1.MIN_CORROBORATORS == 2
    assert b1.BATCH_ID == "STRICT-SAFE-OWNERSHIP-1"
    assert b2.BATCH_ID == "STRICT-SAFE-OWNERSHIP-2"
    assert b2.PERMANENT_EXCLUDED_DOCUMENT_IDS == frozenset({40100, 44247})
    assert b3.BATCH_ID == "STRICT-SAFE-OWNERSHIP-3"
    # Batch 3 keeps Batch 2's one-corroborator bar; two corroborators is still a Batch 1 row.
    two = _evidence("Ada Lovelace", email=True, address=True)
    assert b3.sole_corroborator(two) is None
    assert b1.is_strict_safe({"route": "HIGH", "entity_type": "person", "entity_id": 1}, two)


# --- selection: the household support -----------------------------------------

def test_household_support_selects_and_records_the_member():
    hh = _household()
    member = _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    row = _plan_by_id(did)[did]
    assert row["rule"] == b3.RULE_HOUSEHOLD_PATH_MEMBER
    assert row["proposed_person_id"] == proposed
    assert row["support"]["household_id"] == hh
    assert row["support"]["member_person_id"] == member
    assert row["support"]["member_name"] == f"Wayne {_TAG}"
    assert row["support"]["matching_folder"] == f"{_TAG}, Wayne"
    with engine.connect() as c:
        assert b3.verify_support(c, row) is None


def test_a_folder_person_outside_the_household_is_not_support():
    """The household link is the evidence. A stranger in the folder name is evidence AGAINST."""
    hh, other_hh = _household(), _household()
    _person("Wayne", household_id=other_hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    assert _plan_ids(did) == set()


def test_removed_household_membership_drops_the_row():
    hh = _household()
    member = _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    row = _plan_by_id(did)[did]
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == member).values(household_id=None))
    assert _plan_ids(did) == set()
    with engine.connect() as c:
        assert "no longer in household" in b3.verify_support(c, row)


def test_proposed_person_leaving_the_household_drops_the_row():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    row = _plan_by_id(did)[did]
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == proposed).values(household_id=None))
    assert _plan_ids(did) == set()
    with engine.connect() as c:
        assert "no longer in any household" in b3.verify_support(c, row)


def test_a_renamed_folder_drops_the_row():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    row = _plan_by_id(did)[did]
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.update().where(sources.c.document_id == did)
                  .values(source_uri=_uri("Somebody Else", "renamed.pdf")))
    assert _plan_ids(did) == set()
    with engine.connect() as c:
        assert "no longer a parent segment" in b3.verify_support(c, row)


def test_an_unavailable_source_is_ignored():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne", available=False)
    assert _plan_ids(did) == set()


def test_a_non_sharepoint_source_cannot_satisfy_the_household_rule():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    for system in ("TaxDome Drive", "Drake"):
        did = _household_doc(proposed, "Kristena", "Wayne", source_system=system)
        assert _plan_ids(did) == set(), system


def test_a_folder_naming_the_proposed_person_is_a_batch2_row_not_a_batch3_row():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Kristena")     # folder names the PROPOSED person
    assert _plan_ids(did) == set()


def test_the_lowest_id_matching_source_is_canonical():
    hh = _household()
    _person("Wayne", household_id=hh)
    proposed = _person("Kristena", household_id=hh)
    did = _household_doc(proposed, "Kristena", "Wayne")
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.insert().values(
            document_id=did, source_system="SharePoint",
            source_uri=_uri(f"{_TAG}, Wayne", "second.pdf", ancestry=("Archive",)),
            source_external_id=f"EXT{did}B", available=True, metadata={}))
        lowest = c.execute(text("select min(id) from document_sources where document_id=:d"),
                           {"d": did}).scalar()
    assert _plan_by_id(did)[did]["support"]["source_id"] == lowest


# --- selection: the duplicate support -----------------------------------------

def test_duplicate_support_selects_and_records_the_duplicate():
    pid = _person("Desiderio")
    target, dup = _duplicate_pair(pid, "Desiderio")
    row = _plan_by_id(target)[target]
    assert row["rule"] == b3.RULE_SAME_FILENAME_SAME_OWNER
    assert row["support"] == {"duplicate_document_id": dup, "duplicate_person_id": pid}
    with engine.connect() as c:
        assert b3.verify_support(c, row) is None


@pytest.mark.parametrize("field,value,reason", [
    ("status", "deleted", "is deleted"),
    ("archived", True, "is archived"),
    ("person_id", None, "no longer owned"),
], ids=["deleted", "archived", "unowned"])
def test_duplicate_lifecycle_drift_drops_the_row(field, value, reason):
    pid = _person("Desiderio")
    target, dup = _duplicate_pair(pid, "Desiderio")
    row = _plan_by_id(target)[target]
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == dup).values(**{field: value}))
    assert _plan_ids(target) == set()
    with engine.connect() as c:
        assert reason in b3.verify_support(c, row)


def test_a_duplicate_owned_by_somebody_else_is_not_support():
    pid, other = _person("Desiderio"), _person("Stranger")
    target, dup = _duplicate_pair(pid, "Desiderio", dup_owner=None)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == dup).values(person_id=other))
    assert _plan_ids(target) == set()
    with engine.connect() as c:
        row = {"document_id": target, "proposed_person_id": pid,
               "rule": b3.RULE_SAME_FILENAME_SAME_OWNER,
               "support": {"duplicate_document_id": dup, "duplicate_person_id": pid}}
        assert "is owned by" in b3.verify_support(c, row)


def test_duplicate_filename_drift_drops_the_row():
    pid = _person("Desiderio")
    target, dup = _duplicate_pair(pid, "Desiderio")
    row = _plan_by_id(target)[target]
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == dup)
                  .values(original_name=f"Desiderio {_TAG} something else.pdf"))
    assert _plan_ids(target) == set()
    with engine.connect() as c:
        assert "filenames no longer match" in b3.verify_support(c, row)


def test_normalized_filenames_match_across_case_and_punctuation():
    pid = _person("Desiderio")
    target, dup = _duplicate_pair(pid, "Desiderio",
                                  dup_filename=f"DESIDERIO_{_TAG}__ORIGINALS.pdf")
    assert b3.normalized_filename(f"Desiderio {_TAG} originals.pdf") == \
        b3.normalized_filename(f"DESIDERIO_{_TAG}__ORIGINALS.pdf")
    assert _plan_by_id(target)[target]["support"]["duplicate_document_id"] == dup


def test_a_document_is_never_its_own_duplicate():
    pid = _person("Lonely")
    did = _doc(pid, "Lonely", with_source=False)
    assert _plan_ids(did) == set()


# --- selection: the shared proposal bar ----------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"status": "deleted"},
    {"archived": True},
    {"review_status": "pending"},
], ids=["deleted", "archived", "review_required"])
def test_deleted_archived_and_review_targets_are_rejected(kwargs):
    hh = _household()
    _person("Wayne", household_id=hh)
    pid = _person("Kristena", household_id=hh)
    did = _household_doc(pid, "Kristena", "Wayne", **kwargs)
    assert _plan_ids(did) == set()


def test_a_pre_owned_target_is_rejected():
    hh = _household()
    _person("Wayne", household_id=hh)
    pid, other = _person("Kristena", household_id=hh), _person("Owner")
    did = _household_doc(pid, "Kristena", "Wayne", owner=other)
    assert _plan_ids(did) == set()


def test_missing_exact_name_evidence_is_rejected():
    hh = _household()
    _person("Wayne", household_id=hh)
    pid = _person("Kristena", household_id=hh)
    did = _household_doc(pid, "Kristena", "Wayne",
                         evidence=_evidence(f"Kristena {_TAG}", exact_name=False))
    assert _plan_ids(did) == set()


# --- selection: ADDRESS-only is part of the RULE, not just the manifest -------

#: (address, email, phone) -> does a Batch 3 candidate survive on this contact evidence?
CORROBORATOR_MATRIX = [
    pytest.param({"address": True}, True, id="address-only"),
    pytest.param({"email": True, "address": False}, False, id="email-only"),
    pytest.param({"phone": True, "address": False}, False, id="phone-only"),
    pytest.param({"address": True, "email": True}, False, id="address+email"),
    pytest.param({"address": True, "phone": True}, False, id="address+phone"),
    pytest.param({"address": False}, False, id="none"),
    pytest.param({"address": True, "email": True, "phone": True}, False, id="all-three"),
]


@pytest.mark.parametrize("flags,eligible", CORROBORATOR_MATRIX)
def test_batch3_corroborator_accepts_address_and_nothing_else(flags, eligible):
    evidence = _evidence("Someone Named", **flags)
    got = b3.batch3_corroborator(evidence)
    assert (got == "ADDRESS") is eligible, got
    assert b3.REQUIRED_CORROBORATOR == "ADDRESS"
    assert b3.is_batch3_candidate(
        {"route": "HIGH", "entity_type": "person", "entity_id": 1}, evidence) is eligible


@pytest.mark.parametrize("flags,eligible", CORROBORATOR_MATRIX)
def test_only_address_corroboration_survives_the_household_rule(flags, eligible):
    """Every other Batch 3 condition passes; the corroborator alone decides."""
    hh = _household()
    _person("Wayne", household_id=hh)
    pid = _person("Kristena", household_id=hh)
    did = _household_doc(pid, "Kristena", "Wayne",
                         evidence=_evidence(f"Kristena {_TAG}", **flags))
    selected = _plan_by_id(did)
    assert (did in selected) is eligible
    if eligible:
        assert selected[did]["corroborator"] == "ADDRESS"
        assert selected[did]["rule"] == b3.RULE_HOUSEHOLD_PATH_MEMBER


@pytest.mark.parametrize("flags,eligible", CORROBORATOR_MATRIX)
def test_only_address_corroboration_survives_the_duplicate_rule(flags, eligible):
    """Duplicate support that otherwise passes cannot rescue a non-ADDRESS corroborator."""
    pid = _person("Desiderio")
    target, dup = _duplicate_pair(pid, "Desiderio",
                                  evidence=_evidence(f"Desiderio {_TAG}", **flags))
    selected = _plan_by_id(target)
    assert (target in selected) is eligible
    if eligible:
        assert selected[target]["corroborator"] == "ADDRESS"
        assert selected[target]["support"]["duplicate_document_id"] == dup


def test_every_frozen_row_rests_on_address_only():
    frozen = _frozen()
    for row in frozen["plan"]:
        assert row["corroborator"] == b3.REQUIRED_CORROBORATOR == "ADDRESS"
    assert ap.EXPECTED_COMPOSITION == {"ADDRESS": 5}


@pytest.mark.parametrize("route,entity_type", [("MEDIUM", "person"), ("HIGH", "organization")])
def test_non_high_and_non_person_proposals_are_rejected(route, entity_type):
    hh = _household()
    _person("Wayne", household_id=hh)
    pid = _person("Kristena", household_id=hh)
    did = _household_doc(pid, "Kristena", "Wayne", route=route, entity_type=entity_type)
    assert _plan_ids(did) == set()


def test_more_than_one_current_proposal_is_rejected():
    hh = _household()
    _person("Wayne", household_id=hh)
    pid, rival = _person("Kristena", household_id=hh), _person("Rival")
    did = _household_doc(pid, "Kristena", "Wayne", rival_proposal_person_id=rival)
    assert _plan_ids(did) == set()


def test_a_filename_that_does_not_name_the_proposed_person_is_rejected():
    hh = _household()
    _person("Wayne", household_id=hh)
    pid = _person("Kristena", household_id=hh)
    did = _household_doc(pid, "Kristena", "Wayne", filename="2021 Form 1099-INT.pdf")
    assert _plan_ids(did) == set()
    surname_only = _household_doc(pid, "Kristena", "Wayne", filename=f"{_TAG} 2021.pdf")
    assert _plan_ids(surname_only) == set()


# --- the digest and the census -------------------------------------------------

def test_plan_digest_is_canonical_and_support_sensitive(batch):
    plan = batch["plan"]
    assert b3.plan_digest(plan) == b3.plan_digest(list(reversed(plan)))
    assert b3.plan_digest(plan) == b3.plan_digest([{**r, "extra": "ignored"} for r in plan])
    moved = [{**r, "support": dict(r["support"])} for r in plan]
    key = next(iter(moved[0]["support"]))
    moved[0]["support"][key] = 999_999
    assert b3.plan_digest(moved) != b3.plan_digest(plan)


def test_census_reports_rules_and_corroborators(batch):
    assert b3.plan_census(batch["plan"]) == {
        "rows": 3, "distinct_people": 3,
        "by_rule": {"HOUSEHOLD_PATH_MEMBER": 2, "SAME_FILENAME_SAME_OWNER": 1},
        "by_corroborator": {"ADDRESS": 3}}


# --- apply gates ---------------------------------------------------------------

def test_apply_is_read_only_by_default(batch):
    report = _run(batch)
    assert report["committed"] is False and report["applied"] == 0
    assert report["validated"] == 3
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert not batch["snapshot_root"].exists()
    assert _audit_rows(_request_id(batch["sha"])) == []


def test_manifest_tampering_and_sha_mismatch_abort(batch):
    batch["path"].write_text(batch["path"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch)
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_plan_digest_mismatch_aborts(batch):
    with pytest.raises(SystemExit, match="plan has moved"):
        _run(batch, expect_plan_digest="0" * 64)


def test_wrong_row_count_aborts(batch):
    with pytest.raises(SystemExit, match="!= the approved"):
        _run(batch, expect_rows=5)


def test_composition_rule_split_and_people_mismatches_abort(batch, monkeypatch):
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 99})
    with pytest.raises(SystemExit, match="composition"):
        _run(batch)
    monkeypatch.setattr(ap, "EXPECTED_COMPOSITION", {"ADDRESS": 3})
    monkeypatch.setattr(ap, "EXPECTED_RULES", {"HOUSEHOLD_PATH_MEMBER": 3})
    with pytest.raises(SystemExit, match="rule split"):
        _run(batch)
    monkeypatch.setattr(ap, "EXPECTED_RULES", {"HOUSEHOLD_PATH_MEMBER": 2,
                                               "SAME_FILENAME_SAME_OWNER": 1})
    monkeypatch.setattr(ap, "EXPECTED_DISTINCT_PEOPLE", 99)
    with pytest.raises(SystemExit, match="distinct people"):
        _run(batch)


@pytest.mark.parametrize("column,value,match", [
    ("rule", "FOLDER_SAYS_SO", "not one of"),
    ("corroborator", "FOLDER", "not one of"),
    ("support_json", "{}", "support keys"),
], ids=["unknown-rule", "unknown-corroborator", "wrong-support-keys"])
def test_structurally_invalid_manifest_rows_abort(batch, tmp_path, column, value, match):
    out = tmp_path / f"bad-{column}"
    out.mkdir()
    path, sha = _write_manifest(out, batch["plan"], mutate=lambda r: {**r, column: value})
    with pytest.raises(SystemExit, match=match):
        ap.run(path, expect_sha=sha, expect_plan_digest=batch["digest"], expect_rows=3,
               out=lambda *_a, **_k: None)


def test_apply_without_confirm_or_actor_aborts(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=1)
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(3))


def test_an_earlier_batch_confirmation_phrase_cannot_apply_batch3(batch):
    from scripts import apply_strict_safe_ownership as batch1
    from scripts import apply_strict_safe_ownership_batch2 as batch2
    assert ap.confirm_phrase(3) not in (batch1.confirm_phrase(3), batch2.confirm_phrase(3))
    for other in (batch1.confirm_phrase(3), batch2.confirm_phrase(3)):
        with pytest.raises(SystemExit, match="--confirm"):
            _run(batch, apply_changes=True, actor_user_id=1, confirm=other)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


# --- the json manifest ---------------------------------------------------------

def _write_json(tmp_path, batch, **overrides):
    payload = {
        "batch_id": b3.BATCH_ID,
        "confirmation_phrase": ap.confirm_phrase(3),
        "rows": 3,
        "distinct_people": 3,
        "plan_digest": batch["digest"],
        "plan": [{k: r[k] for k in b3.PLAN_FIELDS} for r in batch["plan"]],
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, ap.sha256_of(path)


def test_json_manifest_is_pinned_and_cross_checked(batch, tmp_path):
    out = tmp_path / "json-ok"
    out.mkdir()
    jpath, jsha = _write_json(out, batch)
    report = _run(batch, manifest_json=jpath, expect_json_sha=jsha)
    assert report["json_sha256"] == jsha and report["validated"] == 3


@pytest.mark.parametrize("overrides,match", [
    ({"batch_id": "STRICT-SAFE-OWNERSHIP-2"}, "not 'STRICT-SAFE-OWNERSHIP-3'"),
    ({"plan_digest": "0" * 64}, "plan_digest"),
    ({"confirmation_phrase": "APPLY-STRICT-SAFE-OWNERSHIP-2-3"}, "confirmation_phrase"),
    ({"rows": 9}, "rows"),
    ({"plan": []}, "same rows"),
], ids=["batch", "digest", "phrase", "rows", "plan"])
def test_json_manifest_disagreement_aborts(batch, tmp_path, overrides, match):
    out = tmp_path / f"json-{next(iter(overrides))}"
    out.mkdir()
    jpath, jsha = _write_json(out, batch, **overrides)
    with pytest.raises(SystemExit, match=match):
        _run(batch, manifest_json=jpath, expect_json_sha=jsha)


def test_json_manifest_sha_mismatch_aborts(batch, tmp_path):
    out = tmp_path / "json-sha"
    out.mkdir()
    jpath, _ = _write_json(out, batch)
    with pytest.raises(SystemExit, match="json manifest SHA256"):
        _run(batch, manifest_json=jpath, expect_json_sha="0" * 64)


def test_json_manifest_needs_its_sha(batch, tmp_path):
    out = tmp_path / "json-pair"
    out.mkdir()
    jpath, _ = _write_json(out, batch)
    with pytest.raises(SystemExit, match="must be given together"):
        _run(batch, manifest_json=jpath)


# --- drift aborts under the lock ------------------------------------------------

def _expect_drift(batch, match="no longer validate|plan has moved"):
    with pytest.raises(SystemExit, match=match):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None, "no row may be written when the batch aborts"
    assert not batch["snapshot_root"].exists()


def test_household_membership_drift_aborts(batch):
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == batch["member"]).values(household_id=None))
    _expect_drift(batch, match="plan has moved")


def test_source_drift_aborts(batch):
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        c.execute(sources.update().where(sources.c.document_id == batch["d1"])
                  .values(available=False))
    _expect_drift(batch, match="plan has moved")


def test_duplicate_drift_aborts(batch):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["dup"])
                  .values(person_id=None))
    _expect_drift(batch, match="plan has moved")


def test_proposal_drift_aborts(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["d1"]).values(
            fact_value=json.dumps({
                "route": "HIGH", "confidence": "HIGH", "entity_type": "person",
                "entity_id": batch["p2"], "entity_name": f"Dolores {_TAG}",
                "evidence": _evidence(f"Dolores {_TAG}")})))
    _expect_drift(batch, match="plan has moved")


def test_exact_name_loss_aborts(batch):
    facts = metadata.tables["document_facts"]
    with engine.begin() as c:
        c.execute(facts.update().where(facts.c.document_id == batch["d1"]).values(
            fact_value=json.dumps({
                "route": "HIGH", "confidence": "HIGH", "entity_type": "person",
                "entity_id": batch["p1"], "entity_name": f"Kristena {_TAG}",
                "evidence": _evidence(f"Kristena {_TAG}", exact_name=False)})))
    _expect_drift(batch, match="plan has moved")


def test_ownership_drift_aborts(batch):
    other = _person("Interloper")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d1"]).values(person_id=other))
    with pytest.raises(SystemExit, match="no longer validate|plan has moved"):
        _apply(batch)
    for did in batch["ids"][1:]:
        assert _row(did)["person_id"] is None


@pytest.mark.parametrize("field,value", [
    ("archived", True), ("status", "deleted"), ("review_status", "pending"),
])
def test_lifecycle_and_review_status_drift_aborts(batch, field, value):
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["d1"])
                  .values(**{field: value}))
    _expect_drift(batch)


@pytest.mark.parametrize("key,match", [
    ("source_id", "support drifted"),
    ("member_person_id", "support drifted"),
    ("household_id", "support drifted"),
], ids=["source", "member", "household"])
def test_a_manifest_whose_support_disagrees_with_live_state_aborts_under_the_lock(
        batch, tmp_path, key, match):
    out = tmp_path / f"support-{key}"
    out.mkdir()

    def bend(row):
        support = json.loads(row["support_json"])
        if key in support:
            support[key] = int(support[key]) + 10_000_000
            row = {**row, "support_json": json.dumps(support, sort_keys=True, ensure_ascii=False)}
        return row

    path, sha = _write_manifest(out, batch["plan"], mutate=bend)
    with pytest.raises(SystemExit, match=match):
        ap.run(path, expect_sha=sha, expect_plan_digest=batch["digest"], expect_rows=3,
               apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=1,
               snapshot_root=batch["snapshot_root"], out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_a_manifest_naming_the_wrong_person_aborts_under_the_lock(batch, tmp_path):
    out = tmp_path / "person"
    out.mkdir()
    swap = {batch["p1"]: batch["p2"], batch["p2"]: batch["p1"]}
    path, sha = _write_manifest(out, batch["plan"], mutate=lambda r: {
        **r, "proposed_person_id": swap.get(int(r["proposed_person_id"]),
                                            int(r["proposed_person_id"]))})
    with pytest.raises(SystemExit, match="proposed person drifted"):
        ap.run(path, expect_sha=sha, expect_plan_digest=batch["digest"], expect_rows=3,
               apply_changes=True, confirm=ap.confirm_phrase(3), actor_user_id=1,
               snapshot_root=batch["snapshot_root"], out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


# --- the successful apply ------------------------------------------------------

def test_successful_apply_changes_only_person_id_and_audits_once_per_row(batch):
    before = {did: dict(_row(did)) for did in batch["ids"]}
    bystander = _household_doc(batch["p1"], "Kristena", "Wayne", route="MEDIUM")
    bystander_before = dict(_row(bystander))

    report = _apply(batch)
    assert report["committed"] is True and report["applied"] == 3 and report["audit_rows"] == 3

    want = {r["document_id"]: r["proposed_person_id"] for r in batch["plan"]}
    for did in batch["ids"]:
        after, prior = dict(_row(did)), before[did]
        assert after["person_id"] == want[did]
        assert after["household_id"] is None and after["organization_id"] is None
        assert after["review_status"] == "not_required"
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"person_id", "updated_at", "updated_by_user_id"}, changed
    assert dict(_row(bystander)) == bystander_before

    events = _audit_rows(_request_id(batch["sha"]))
    assert len(events) == 3
    assert {e["action"] for e in events} == {"document.ownership_resolved"}
    assert {int(e["entity_id"]) for e in events} == set(batch["ids"])


def test_apply_leaves_sources_and_ocr_untouched(batch):
    ocr = metadata.tables["document_ocr"]
    with engine.begin() as c:
        for did in batch["ids"]:
            c.execute(ocr.insert().values(document_id=did, status="completed", char_count=99))

    def fp():
        with engine.connect() as c:
            return (
                c.execute(text("select md5(string_agg(document_id::text||coalesce(source_uri,'')"
                               "||available::text,',' order by id)) from document_sources "
                               "where document_id=any(:i)"), {"i": batch["ids"]}).scalar(),
                c.execute(text("select md5(string_agg(document_id::text||coalesce(status,''),"
                               "',' order by id)) from document_ocr where document_id=any(:i)"),
                          {"i": batch["ids"]}).scalar())

    before = fp()
    _apply(batch)
    assert fp() == before
    with engine.begin() as c:
        c.execute(delete(ocr).where(ocr.c.document_id.in_(batch["ids"])))


def test_snapshot_is_written_before_any_write_and_records_the_rule(batch):
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    assert len(rows) == 3
    for r in rows:
        assert r["prior_person_id"] == ""              # captured BEFORE the assignment
        assert r["prior_review_status"] == "not_required"
        assert json.loads(r["prior_tags_json"])["source_system"] == "SharePoint"
        assert r["rule"] in b3.RULES
        assert r["corroborator"] == "ADDRESS"
        assert r["independent_document_corroborator_count"] == "1"
        assert json.loads(r["support_json"])
        assert int(r["destination_person_id"]) in (batch["p1"], batch["p2"], batch["p3"])
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["batch_id"] == "STRICT-SAFE-OWNERSHIP-3"
    assert meta["snapshot_sha256"] == ap.sha256_of(snap_dir / ap.SNAPSHOT_CSV)


# --- transactional rollback on failure -----------------------------------------

def test_a_refused_assignment_rolls_back_every_earlier_row(batch, monkeypatch):
    from app.services.households import resolve_document_ownership as real_fn
    calls = {"n": 0}

    def flaky(document_id, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            return {"document_id": document_id, "assigned": False, "reason": "no_longer_eligible"}
        return real_fn(document_id, **kw)

    monkeypatch.setattr("app.services.households.resolve_document_ownership", flaky)
    with pytest.raises(RuntimeError, match="was not assigned"):
        _apply(batch)
    assert calls["n"] == 3
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert _audit_rows(_request_id(batch["sha"])) == []


def test_non_target_mutation_is_detected_and_rolls_everything_back(batch, monkeypatch):
    real = ap._fingerprints
    calls = {"n": 0}

    def drifting(conn, ids):
        calls["n"] += 1
        result = real(conn, ids)
        if calls["n"] > 1:
            result["non_target"] = "a document outside the batch moved"
        return result

    monkeypatch.setattr(ap, "_fingerprints", drifting)
    with pytest.raises(RuntimeError, match="non_target fingerprint changed"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None
    assert _audit_rows(_request_id(batch["sha"])) == []
    assert not (_snap_dir(batch) / "apply_receipt.json").exists()


def test_a_missing_audit_row_aborts_the_whole_batch(batch, monkeypatch):
    """Ownership without its audit event is not an outcome this batch is allowed to commit."""
    from sqlalchemy import text as sa_text

    def silent(document_id, *, person_id, actor_user_id=None, request_id=None, conn=None, **kw):
        conn.execute(sa_text("update documents set person_id = :p where id = :i"),
                     {"p": person_id, "i": document_id})
        return {"document_id": document_id, "assigned": True}

    monkeypatch.setattr("app.services.households.resolve_document_ownership", silent)
    with pytest.raises(RuntimeError, match="audit rows for 3 assignments"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None, "ownership must not survive a missing audit row"


# --- the batch 3 rollback -------------------------------------------------------

def _rollback(batch, **kw):
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return rb.run(_snap_dir(batch), **kw)


def _rollback_request_id(batch):
    sha = ap.sha256_of(_snap_dir(batch) / ap.SNAPSHOT_CSV)
    return f"strict-safe-ownership-batch3-rollback:{sha[:12]}"


def _rewrite_snapshot(snap_dir, *, rows=None, batch_id="STRICT-SAFE-OWNERSHIP-3", columns=None):
    path = snap_dir / ap.SNAPSHOT_CSV
    existing = list(csv.DictReader(path.open(encoding="utf-8")))
    rows = existing if rows is None else rows
    columns = columns if columns is not None else list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    meta["batch_id"] = batch_id
    meta["snapshot_sha256"] = ap.sha256_of(path)
    (snap_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def test_rollback_confirmation_phrase_is_batch3_specific():
    from scripts import rollback_strict_safe_ownership as b1_rb
    from scripts import rollback_strict_safe_ownership_batch2 as b2_rb
    assert rb.confirm_phrase(5) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-3-5"
    assert rb.confirm_phrase(5) not in (b1_rb.confirm_phrase(5), b2_rb.confirm_phrase(5))
    assert rb.EXPECTED_BATCH_ID == b3.BATCH_ID


def test_rollback_is_read_only_by_default(batch):
    _apply(batch)
    report = _rollback(batch)
    assert report["committed"] is False and report["restored"] == 0
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None
    assert _audit_rows(_rollback_request_id(batch)) == []
    assert not (_snap_dir(batch) / "rollback_receipt.json").exists()


def test_rollback_requires_the_batch3_phrase_and_an_actor(batch):
    _apply(batch)
    from scripts import rollback_strict_safe_ownership_batch2 as b2_rb
    with pytest.raises(SystemExit, match="ROLLBACK-STRICT-SAFE-OWNERSHIP-3-3"):
        _rollback(batch, apply_changes=True, actor_user_id=1, confirm=b2_rb.confirm_phrase(3))
    with pytest.raises(SystemExit, match="--confirm"):
        _rollback(batch, apply_changes=True, actor_user_id=1)
    with pytest.raises(SystemExit, match="actor"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3))
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_restores_exact_prior_state_and_audits_as_batch3(batch):
    prior = {did: dict(_row(did)) for did in batch["ids"]}
    _apply(batch)
    request_id = _rollback_request_id(batch)
    report = _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    assert report["committed"] is True and report["restored"] == 3
    for did in batch["ids"]:
        now, was = _row(did), prior[did]
        assert now["person_id"] == was["person_id"] is None
        assert now["household_id"] == was["household_id"]
        assert now["organization_id"] == was["organization_id"]
        assert now["review_status"] == was["review_status"]
        assert now["tags"] == was["tags"]
    events = _audit_rows(request_id)
    assert len(events) == 3
    assert {e["action"] for e in events} == {"document.ownership_rollback"}
    assert {e["metadata"]["batch_id"] for e in events} == {"STRICT-SAFE-OWNERSHIP-3"}
    assert {e["metadata"]["rule"] for e in events} == {b3.RULE_HOUSEHOLD_PATH_MEMBER,
                                                       b3.RULE_SAME_FILENAME_SAME_OWNER}
    assert (_snap_dir(batch) / "rollback_receipt.json").is_file()


def test_rollback_refuses_a_tampered_snapshot(batch):
    _apply(batch)
    snap = _snap_dir(batch) / ap.SNAPSHOT_CSV
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has been modified"):
        _rollback(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


@pytest.mark.parametrize("batch_id", ["STRICT-SAFE-OWNERSHIP-1", "STRICT-SAFE-OWNERSHIP-2"])
def test_rollback_refuses_an_earlier_batch_snapshot_by_its_manifest(batch, batch_id):
    _apply(batch)
    _rewrite_snapshot(_snap_dir(batch), batch_id=batch_id)
    with pytest.raises(SystemExit, match="not 'STRICT-SAFE-OWNERSHIP-3'"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


@pytest.mark.parametrize("foreign", ["corroborator_count", "matching_folder"])
def test_rollback_refuses_an_earlier_batch_shaped_snapshot_by_its_columns(batch, foreign):
    """Even a manifest that CLAIMS batch 3: batch 1 and batch 2 snapshots carry other columns."""
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    shaped = [{**r, foreign: "1"} for r in rows]
    _rewrite_snapshot(snap_dir, rows=shaped, columns=list(shaped[0]))
    with pytest.raises(SystemExit, match="earlier-batch columns"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_refuses_a_snapshot_missing_the_batch3_marker_columns(batch):
    """A batch 2 snapshot has no rule/support_json, so it cannot pass as batch 3."""
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    batch2_shaped = [{k: v for k, v in r.items() if k not in ("rule", "support_json")} for r in rows]
    _rewrite_snapshot(snap_dir, rows=batch2_shaped, columns=list(batch2_shaped[0]))
    with pytest.raises(SystemExit, match="missing batch 3 columns"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_refuses_a_snapshot_outside_the_batch3_root(batch):
    _apply(batch)
    with pytest.raises(SystemExit, match="not under the batch 3 snapshot root"):
        rb.run(_snap_dir(batch), out=lambda *_a, **_k: None)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is not None


def test_rollback_ownership_drift_blocks_the_entire_rollback(batch):
    _apply(batch)
    other = _person("Reassigned")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="drifted"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    assert _row(batch["ids"][0])["person_id"] == other
    for did in batch["ids"][1:]:
        assert _row(did)["person_id"] is not None
    assert _audit_rows(_rollback_request_id(batch)) == []


def test_rollback_scope_never_broadens_beyond_the_snapshot_ids(batch):
    bystander = _household_doc(batch["p1"], "Kristena", "Wayne", route="MEDIUM")
    _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == bystander)
                  .values(person_id=batch["p1"]))
    before = dict(_row(bystander))
    _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(3), actor_user_id=1)
    assert dict(_row(bystander)) == before
