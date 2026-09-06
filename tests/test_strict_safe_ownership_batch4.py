"""Strict-safe ownership BATCH 4 — the conflict-cleanup rule, the guarded apply, and the rollback.

Batch 4 does not assign an owner. It REMOVES one: four documents carry both a person id and an
organization id, and the person record is the retired business-as-person shell that the canonical
type repair replaced. Clearing the wrong person id would silently strip a real owner, so almost
every test here pins a REFUSAL — a person who is still active, an organization without repair
provenance, a household that appeared, a filename naming somebody else.

The last one is the important one. Four sibling documents sit in the SAME business folder under the
SAME doubly-owned pair and must never be in this batch, because their filenames name the LLC's
principal — a separate client who already owns the same-filename copies. Folder evidence cannot tell
them apart. The filename rule can, and does.

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
from app.services import document_strict_safe_ownership_batch4 as b4
from scripts import apply_strict_safe_ownership_batch4 as ap
from scripts import build_strict_safe_ownership_batch4_manifest as builder
from scripts import rollback_strict_safe_ownership_batch4 as rb

_TAG = f"SSOFOUR{uuid.uuid4().hex[:6]}"
_ORG_CORE = f"Acme {_TAG}"
SP_ROOT = "https://example.sharepoint.com/sites/Data/Shared%20Documents"


@pytest.fixture(autouse=True)
def _clean():
    yield
    sources = metadata.tables["document_sources"]
    entities = metadata.tables["relationship_entities"]
    with engine.begin() as c:
        ids = [r[0] for r in c.execute(
            select(documents.c.id).where(documents.c.stored_name.like(f"sso4:{_TAG}%")))]
        if ids:
            c.execute(delete(sources).where(sources.c.document_id.in_(ids)))
            c.execute(delete(documents).where(documents.c.id.in_(ids)))
        c.execute(delete(people).where(people.c.last_name == _TAG))
        c.execute(delete(people).where(people.c.full_name.like(f"%{_TAG}%")))
        c.execute(delete(entities).where(entities.c.name.like(f"%{_TAG}%")))


# --- builders -----------------------------------------------------------------

def _former_person(name=None, *, active=False, first=None, last=None) -> int:
    """The retired business-as-person shell: inactive, no first/last name."""
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=name or f"{_ORG_CORE} LLC",
            active=active).returning(people.c.id)).scalar_one()


def _organization(former_person_id, *, name=None, active=True, entity_type="business",
                  origin=b4.CANONICAL_REPAIR_ORIGIN, repaired_from="use_person") -> int:
    entities = metadata.tables["relationship_entities"]
    details = {}
    if origin is not None:
        details["origin"] = origin
    if repaired_from is not None:
        details["repaired_from_person_id"] = (former_person_id if repaired_from == "use_person"
                                              else repaired_from)
    with engine.begin() as c:
        return c.execute(entities.insert().values(
            entity_type=entity_type, name=name or f"{_ORG_CORE} LLC", active=active,
            details=details).returning(entities.c.id)).scalar_one()


def _real_person(first, last) -> int:
    with engine.begin() as c:
        return c.execute(people.insert().values(
            first_name=first, last_name=last, full_name=f"{first} {last}", active=True)
            .returning(people.c.id)).scalar_one()


def _doc(*, person_id, organization_id, filename, folder=None, household_id=None,
         review_status="not_required", status="active", archived=False, available=True,
         with_source=True, source_system="SharePoint") -> int:
    folder = folder if folder is not None else f"{_ORG_CORE}"
    sources = metadata.tables["document_sources"]
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            original_name=filename, stored_name=f"sso4:{_TAG}{uuid.uuid4().hex}",
            storage_path="/x", storage_provider="Client360 Local", storage_uri=f"/x/{filename}",
            size_bytes=10, sha256=uuid.uuid4().hex * 2, status=status, archived=archived,
            review_status=review_status, current_version=1, person_id=person_id,
            household_id=household_id, organization_id=organization_id, tags={},
        ).returning(documents.c.id)).scalar_one()
        if with_source:
            parts = "/".join(quote(p) for p in ("Clients", "Tax Preparation", "Business", folder,
                                                "2023", filename))
            c.execute(sources.insert().values(
                document_id=did, source_system=source_system,
                source_uri=f"{SP_ROOT}/{parts}", source_external_id=f"E{did}",
                available=available, metadata={}))
    return did


def _plan_ids(*dids):
    return {r["document_id"] for r in b4.build_plan()} & set(dids)


def _row(did):
    with engine.connect() as c:
        return c.execute(select(documents).where(documents.c.id == did)).mappings().one()


@pytest.fixture
def batch(tmp_path, monkeypatch):
    """Two qualifying rows plus one that names a DIFFERENT client, and a matching manifest.

    The manifest is built from the WHOLE live plan, not from the fixture's own rows, because the
    apply deliberately refuses a manifest that is a subset of the plan — a partial batch is a batch
    nobody reviewed. This database is a production copy and already contains qualifying rows, so any
    row the fixture did not create is restored in teardown.
    """
    pre_existing = [(r["document_id"], r["former_person_id"]) for r in b4.build_plan()]

    former = _former_person()
    org = _organization(former)
    principal = _real_person("Randall", f"Jenkins{_TAG}")
    good1 = _doc(person_id=former, organization_id=org,
                 filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    good2 = _doc(person_id=former, organization_id=org,
                 filename=f"2022 Tax Return Documents ({_ORG_CORE} CO).pdf")
    principal_doc = _doc(person_id=former, organization_id=org,
                         filename=f"2023 Tax Return Documents (Randall Jenkins{_TAG}).pdf")
    mine = sorted([good1, good2])

    plan = b4.build_plan()
    ids = sorted(r["document_id"] for r in plan)
    assert set(mine) <= set(ids), "the fixture's rows must qualify"
    assert principal_doc not in ids, "a filename naming another client must never qualify"

    out_dir = tmp_path / "manifest"
    written = builder.write_manifest(plan, out_dir)
    monkeypatch.setattr(ap, "EXPECTED_ROWS", len(plan))

    yield {"ids": mine, "all_ids": ids, "plan": plan, "rows": len(plan), "former": former,
           "org": org, "principal": principal, "principal_doc": principal_doc,
           "digest": b4.plan_digest(plan), "csv": written["csv"], "json": written["json"],
           "csv_sha": written["csv_sha256"], "json_sha": written["json_sha256"],
           "snapshot_root": tmp_path / "snap"}

    # Put back any borrowed production-copy row this test may have cleared.
    with engine.begin() as c:
        for document_id, person_id in pre_existing:
            c.execute(text("update documents set person_id = :p "
                           "where id = :i and person_id is null"),
                      {"p": person_id, "i": document_id})


def _run(batch, **kw):
    kw.setdefault("expect_sha", batch["csv_sha"])
    kw.setdefault("expect_json_sha", batch["json_sha"])
    kw.setdefault("manifest_json", batch["json"])
    kw.setdefault("expect_plan_digest", batch["digest"])
    kw.setdefault("expect_rows", batch["rows"])
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return ap.run(batch["csv"], **kw)


def _apply(batch, **kw):
    return _run(batch, apply_changes=True, confirm=ap.confirm_phrase(batch["rows"]),
                actor_user_id=1, **kw)


def _audit(request_id):
    audit = metadata.tables["audit_events"]
    with engine.connect() as c:
        return c.execute(select(audit.c.action, audit.c.entity_id, audit.c.metadata)
                         .where(audit.c.request_id == request_id)).mappings().all()


def _request_id(batch):
    return f"strict-safe-ownership-batch4:{b4.BATCH_ID}:{batch['csv_sha'][:12]}"


def _snap_dir(batch):
    return next(Path(batch["snapshot_root"]).glob("strict-safe-ownership-batch4-apply-*"))


# --- the rule -----------------------------------------------------------------

def test_the_qualifying_shape_is_selected_with_full_evidence():
    former = _former_person()
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    row = next(r for r in b4.build_plan() if r["document_id"] == did)
    assert row["former_person_id"] == former and row["organization_id"] == org
    assert row["repaired_from_person_id"] == former
    assert row["filename_names_organization"] is True
    assert row["folder_segment"] == _ORG_CORE
    with engine.connect() as c:
        assert b4.verify_row(c, row) is None


def test_a_filename_naming_a_different_client_is_rejected():
    """The clause that keeps the principal's personal returns out of the LLC's batch."""
    former = _former_person()
    org = _organization(former)
    _real_person("Randall", f"Jenkins{_TAG}")
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents (Randall Jenkins{_TAG}).pdf")
    assert _plan_ids(did) == set()


def test_a_filename_that_does_not_name_the_organization_is_rejected():
    former = _former_person()
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org, filename="2023 Tax Return Documents.pdf")
    assert _plan_ids(did) == set()


def test_legal_suffixes_do_not_decide_identity():
    assert b4.core_name_tokens("Affordable Measures LLC") == \
        b4.core_name_tokens("AFFORDABLE MEASURES CO") == frozenset({"affordable", "measures"})
    # ...but a name that is only a suffix keeps its tokens rather than vanishing
    assert len(b4.core_name_tokens("Acme Co")) >= b4.MIN_CORE_TOKENS


def test_missing_repair_provenance_is_rejected():
    former = _former_person()
    for kwargs in ({"origin": None}, {"origin": "something_else"}, {"repaired_from": None},
                   {"repaired_from": 999999}):
        org = _organization(former, **kwargs)
        did = _doc(person_id=former, organization_id=org,
                   filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
        assert _plan_ids(did) == set(), kwargs


def test_an_active_former_person_record_is_rejected():
    """An active person may be a real client. Only a retired shell qualifies."""
    former = _former_person(active=True)
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()


def test_a_former_person_with_a_personal_name_is_rejected():
    former = _former_person(first="Randall", last=f"Jenkins{_TAG}")
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()


@pytest.mark.parametrize("kwargs", [
    {"active": False}, {"entity_type": "trust"},
], ids=["inactive-org", "not-a-business"])
def test_an_unusable_organization_is_rejected(kwargs):
    former = _former_person()
    org = _organization(former, **kwargs)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()


def test_an_organization_naming_a_different_person_is_rejected():
    """repaired_from_person_id must name THIS document's person, not some other."""
    former, other = _former_person(), _former_person()
    org = _organization(other)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()


def test_a_populated_household_is_rejected():
    households = metadata.tables["households"]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH {_TAG}")
                       .returning(households.c.id)).scalar_one()
    former = _former_person()
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org, household_id=hh,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()
    with engine.begin() as c:
        c.execute(delete(documents).where(documents.c.id == did))
        c.execute(delete(households).where(households.c.id == hh))


@pytest.mark.parametrize("kwargs", [
    {"status": "deleted"}, {"archived": True}, {"review_status": "pending"},
], ids=["deleted", "archived", "review-required"])
def test_a_non_live_document_is_rejected(kwargs):
    former = _former_person()
    org = _organization(former)
    did = _doc(person_id=former, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf", **kwargs)
    assert _plan_ids(did) == set()


def test_a_singly_owned_document_is_not_in_scope():
    """Batch 4 resolves CONFLICTS. A document with only an organization is already correct."""
    former = _former_person()
    org = _organization(former)
    did = _doc(person_id=None, organization_id=org,
               filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(did) == set()


def test_folder_evidence_is_required():
    former = _former_person()
    org = _organization(former)
    no_source = _doc(person_id=former, organization_id=org, with_source=False,
                     filename=f"2023 Tax Return Documents ({_ORG_CORE} CO).pdf")
    unavailable = _doc(person_id=former, organization_id=org, available=False,
                       filename=f"2022 Tax Return Documents ({_ORG_CORE} CO).pdf")
    wrong_folder = _doc(person_id=former, organization_id=org, folder="Somebody Else",
                        filename=f"2021 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert _plan_ids(no_source, unavailable, wrong_folder) == set()


def test_plan_digest_is_canonical_and_order_independent(batch):
    plan = batch["plan"]
    assert b4.plan_digest(plan) == b4.plan_digest(list(reversed(plan)))
    assert b4.plan_digest(plan) == b4.plan_digest([{**r, "extra": 1} for r in plan])
    moved = [{**r} for r in plan]
    moved[0]["organization_id"] += 1
    assert b4.plan_digest(moved) != b4.plan_digest(plan)


# --- apply gates ---------------------------------------------------------------

def test_apply_is_read_only_by_default(batch):
    report = _run(batch)
    assert report["committed"] is False and report["cleared"] == 0
    assert report["validated"] == batch["rows"]
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"]
    assert not batch["snapshot_root"].exists()
    assert _audit(_request_id(batch)) == []


def test_manifest_tampering_and_sha_mismatch_abort(batch):
    Path(batch["csv"]).write_text(Path(batch["csv"]).read_text(encoding="utf-8") + "\n",
                                  encoding="utf-8")
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch)
    with pytest.raises(SystemExit, match="SHA256"):
        _run(batch, expect_sha="0" * 64)


def test_json_manifest_sha_mismatch_aborts(batch):
    with pytest.raises(SystemExit, match="json manifest SHA256"):
        _run(batch, expect_json_sha="0" * 64)


def test_plan_digest_mismatch_aborts(batch):
    """A wrong expected digest is caught by the json cross-check, which runs first."""
    with pytest.raises(SystemExit, match="plan_digest|plan has moved"):
        _run(batch, expect_plan_digest="0" * 64)


def test_a_corpus_that_moves_trips_the_live_plan_digest_gate(batch):
    """The manifests still agree with each other; the DATABASE is what changed."""
    extra = _doc(person_id=batch["former"], organization_id=batch["org"],
                 filename=f"2021 Tax Return Documents ({_ORG_CORE} CO).pdf")
    assert extra in {r["document_id"] for r in b4.build_plan()}
    with pytest.raises(SystemExit, match="plan has moved"):
        _run(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"]


def test_wrong_row_count_aborts(batch):
    with pytest.raises(SystemExit, match="!= the approved"):
        _run(batch, expect_rows=batch["rows"] + 1)


def test_apply_without_confirm_or_actor_aborts(batch):
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=1)
    with pytest.raises(SystemExit, match="actor"):
        _run(batch, apply_changes=True, confirm=ap.confirm_phrase(batch["rows"]))


def test_an_earlier_batch_phrase_cannot_apply_batch4(batch):
    from scripts import apply_strict_safe_ownership_batch3 as batch3
    assert ap.confirm_phrase(batch["rows"]) != batch3.confirm_phrase(batch["rows"])
    with pytest.raises(SystemExit, match="--confirm"):
        _run(batch, apply_changes=True, actor_user_id=1,
             confirm=batch3.confirm_phrase(batch["rows"]))
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"]


def test_a_manifest_row_outside_the_live_plan_aborts(batch, tmp_path):
    """Adding the principal's document to the manifest must be refused."""
    rows = list(csv.DictReader(Path(batch["csv"]).open(encoding="utf-8")))
    rows.append({**rows[0], "document_id": str(batch["principal_doc"])})
    out = tmp_path / "extra"
    out.mkdir()
    path = out / "m.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(SystemExit, match="rows, approved|the live plan is|do not describe"):
        ap.run(path, expect_sha=ap.sha256_of(path), expect_json_sha=batch["json_sha"],
               manifest_json=batch["json"], expect_plan_digest=batch["digest"],
               expect_rows=batch["rows"], out=lambda *_a, **_k: None)
    assert _row(batch["principal_doc"])["person_id"] == batch["former"]


def test_ownership_drift_aborts_before_any_write(batch):
    other = _former_person()
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=other))
    with pytest.raises(SystemExit, match="plan has moved|no longer validate"):
        _apply(batch)
    assert _row(batch["ids"][1])["person_id"] == batch["former"]
    assert not batch["snapshot_root"].exists()


def test_a_household_appearing_aborts(batch):
    households = metadata.tables["households"]
    with engine.begin() as c:
        hh = c.execute(households.insert().values(name=f"HH2 {_TAG}")
                       .returning(households.c.id)).scalar_one()
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(household_id=hh))
    with pytest.raises(SystemExit, match="plan has moved|no longer validate"):
        _apply(batch)
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(household_id=None))
        c.execute(delete(households).where(households.c.id == hh))


def test_the_former_person_becoming_active_aborts(batch):
    with engine.begin() as c:
        c.execute(people.update().where(people.c.id == batch["former"]).values(active=True))
    with pytest.raises(SystemExit, match="plan has moved|no longer validate"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"]


def test_repair_provenance_disappearing_aborts(batch):
    entities = metadata.tables["relationship_entities"]
    with engine.begin() as c:
        c.execute(entities.update().where(entities.c.id == batch["org"]).values(details={}))
    with pytest.raises(SystemExit, match="plan has moved|no longer validate"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"]


# --- the successful apply ------------------------------------------------------

def test_successful_apply_clears_only_person_id(batch):
    before = {did: dict(_row(did)) for did in batch["ids"]}
    report = _apply(batch)
    assert report["committed"] is True
    assert report["cleared"] == report["audit_rows"] == batch["rows"]
    for did in batch["ids"]:
        after, prior = dict(_row(did)), before[did]
        assert after["person_id"] is None
        assert after["household_id"] is None
        assert after["organization_id"] == batch["org"] == prior["organization_id"]
        changed = {k for k in after if after[k] != prior[k]}
        assert changed <= {"person_id", "updated_at", "updated_by_user_id"}, changed
    # the sibling naming the principal is untouched
    assert _row(batch["principal_doc"])["person_id"] == batch["former"]

    events = _audit(_request_id(batch))
    assert len(events) == batch["rows"]
    assert {e["action"] for e in events} == {"document.ownership_conflict_resolved"}
    mine = [e for e in events if int(e["entity_id"]) in batch["ids"]]
    assert {e["metadata"]["removed_person_id"] for e in mine} == {batch["former"]}
    assert {e["metadata"]["retained_organization_id"] for e in mine} == {batch["org"]}
    assert {e["metadata"]["batch_id"] for e in events} == {"STRICT-SAFE-OWNERSHIP-4"}


def test_snapshot_is_written_before_any_write(batch):
    _apply(batch)
    snap_dir = _snap_dir(batch)
    rows = list(csv.DictReader((snap_dir / ap.SNAPSHOT_CSV).open(encoding="utf-8")))
    assert len(rows) == batch["rows"]
    rows = [r for r in rows if int(r["document_id"]) in batch["ids"]]
    for r in rows:
        assert int(r["prior_person_id"]) == batch["former"]      # captured BEFORE the clear
        assert r["prior_household_id"] == ""
        assert int(r["prior_organization_id"]) == batch["org"]
        assert json.loads(r["support_json"])["repaired_from_person_id"] == batch["former"]
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    assert meta["batch_id"] == "STRICT-SAFE-OWNERSHIP-4"
    assert meta["snapshot_sha256"] == ap.sha256_of(snap_dir / ap.SNAPSHOT_CSV)


def test_a_late_invariant_failure_rolls_back_everything(batch, monkeypatch):
    real = ap._fingerprints
    calls = {"n": 0}

    def drifting(conn, ids, people_ids, orgs):
        calls["n"] += 1
        result = real(conn, ids, people_ids, orgs)
        if calls["n"] > 1:
            result["non_target"] = "a document outside the batch moved"
        return result

    monkeypatch.setattr(ap, "_fingerprints", drifting)
    with pytest.raises(RuntimeError, match="non_target fingerprint changed"):
        _apply(batch)
    for did in batch["ids"]:
        assert _row(did)["person_id"] == batch["former"], "the clear must not survive"
    assert _audit(_request_id(batch)) == []


# --- rollback -------------------------------------------------------------------

def _rollback(batch, **kw):
    kw.setdefault("snapshot_root", batch["snapshot_root"])
    kw.setdefault("out", lambda *_a, **_k: None)
    return rb.run(_snap_dir(batch), **kw)


def test_rollback_phrase_is_batch4_specific():
    from scripts import rollback_strict_safe_ownership_batch3 as b3_rb
    assert rb.confirm_phrase(4) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-4-4"
    assert rb.confirm_phrase(4) != b3_rb.confirm_phrase(4)


def test_rollback_is_read_only_by_default(batch):
    _apply(batch)
    report = _rollback(batch)
    assert report["committed"] is False and report["restored"] == 0
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_rollback_restores_the_removed_person(batch):
    prior = {did: dict(_row(did)) for did in batch["ids"]}
    _apply(batch)
    report = _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(batch["rows"]),
                       actor_user_id=1)
    assert report["committed"] is True and report["restored"] == batch["rows"]
    for did in batch["ids"]:
        now = _row(did)
        assert now["person_id"] == prior[did]["person_id"] == batch["former"]
        assert now["organization_id"] == prior[did]["organization_id"]
        assert now["household_id"] is None
        assert now["tags"] == prior[did]["tags"]


def test_rollback_refuses_a_tampered_snapshot(batch):
    _apply(batch)
    snap = _snap_dir(batch) / ap.SNAPSHOT_CSV
    snap.write_text(snap.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="has been modified"):
        _rollback(batch)


def test_rollback_refuses_an_earlier_batch_snapshot(batch):
    _apply(batch)
    snap_dir = _snap_dir(batch)
    meta = json.loads((snap_dir / "manifest.json").read_text(encoding="utf-8"))
    meta["batch_id"] = "STRICT-SAFE-OWNERSHIP-3"
    (snap_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not 'STRICT-SAFE-OWNERSHIP-4'"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(batch["rows"]),
                  actor_user_id=1)
    for did in batch["ids"]:
        assert _row(did)["person_id"] is None


def test_rollback_refuses_a_snapshot_outside_the_batch4_root(batch):
    _apply(batch)
    with pytest.raises(SystemExit, match="not under the batch 4 snapshot root"):
        rb.run(_snap_dir(batch), out=lambda *_a, **_k: None)


def test_rollback_drift_blocks_the_entire_rollback(batch):
    _apply(batch)
    interloper = _real_person("New", f"Owner{_TAG}")
    with engine.begin() as c:
        c.execute(documents.update().where(documents.c.id == batch["ids"][0])
                  .values(person_id=interloper))
    with pytest.raises(SystemExit, match="drifted"):
        _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(batch["rows"]),
                  actor_user_id=1)
    assert _row(batch["ids"][1])["person_id"] is None, "no row may revert when the rollback aborts"


def test_rollback_scope_never_broadens_beyond_the_snapshot(batch):
    _apply(batch)
    before = dict(_row(batch["principal_doc"]))
    _rollback(batch, apply_changes=True, confirm=rb.confirm_phrase(batch["rows"]),
              actor_user_id=1)
    assert dict(_row(batch["principal_doc"])) == before


# --- the frozen production manifest ---------------------------------------------

FROZEN = Path(__file__).resolve().parents[1] / "reports"


def test_frozen_manifest_controls_are_the_reviewed_ones():
    directories = sorted(FROZEN.glob("strict-safe-ownership-batch4-*"))
    if not directories:
        pytest.skip("frozen batch 4 manifest not present in this worktree")
    out_dir = directories[-1]
    meta = json.loads((out_dir / builder.JSON_NAME).read_text(encoding="utf-8"))
    assert meta["batch_id"] == b4.BATCH_ID == "STRICT-SAFE-OWNERSHIP-4"
    assert meta["rows"] == 4
    assert meta["confirmation_phrase"] == ap.confirm_phrase(4) == "APPLY-STRICT-SAFE-OWNERSHIP-4-4"
    assert meta["rollback_phrase"] == rb.confirm_phrase(4) == "ROLLBACK-STRICT-SAFE-OWNERSHIP-4-4"
    assert [r["document_id"] for r in meta["plan"]] == [465, 467, 469, 471]
    assert {r["organization_id"] for r in meta["plan"]} == {129}
    assert {r["former_person_id"] for r in meta["plan"]} == {3796}
    assert b4.plan_digest(meta["plan"]) == meta["plan_digest"]
    for excluded in (466, 468, 470, 472):
        assert excluded not in [r["document_id"] for r in meta["plan"]]


def test_no_write_statement_outside_the_two_guarded_updates():
    """The service and the manifest builder never write; the apply/rollback write one column each."""
    service = Path(b4.__file__).read_text(encoding="utf-8")
    build = Path(builder.__file__).read_text(encoding="utf-8")
    for body, name in ((service, "service"), (build, "builder")):
        # Drop the module docstring before scanning: it DESCRIBES the apply's UPDATE, and prose
        # about a statement is not a statement.
        code = body.split('"""', 2)[-1]
        code = "\n".join(x for x in code.splitlines() if not x.strip().startswith("#"))
        for statement in ("insert into", "update documents", "delete from"):
            assert statement not in code.lower(), f"{name} contains {statement}"
    assert "SET TRANSACTION READ ONLY" in service and "SET TRANSACTION READ ONLY" in build
