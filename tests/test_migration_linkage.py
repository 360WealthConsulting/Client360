"""Canonical Linkage Remediation — READ-ONLY preview coverage.

Proves the strict, reuse-the-TaxDome-matchers resolution of UNLINKED documents' source folders to
EXISTING canonical entities: person -> household -> organization -> Review/Unresolved. Ambiguous and
no-match folders go to Review; junk artifacts are excluded; nothing is created, no rows change, no bytes
move. Temp rows only.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, households, metadata, people
from app.services.migration.base import Mode, ModeNotSupported
from app.services.migration.config import MigrationConfig
from app.services.migration.linkage import LinkageRemediationJob

relationship_entities = metadata.tables["relationship_entities"]
import_jobs = metadata.tables["import_jobs"]
_TAG = uuid.uuid4().hex[:8]
_CREATED = {"documents": [], "people": [], "households": [], "relationship_entities": []}


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        for tbl, key in ((documents, "documents"), (relationship_entities, "relationship_entities"),
                         (people, "people"), (households, "households")):
            if _CREATED[key]:
                c.execute(tbl.delete().where(tbl.c.id.in_(_CREATED[key])))
    for k in _CREATED:
        _CREATED[k].clear()


def _person(full_name, household_id=None):
    first, last = (full_name.split(" ", 1) + [""])[:2]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(full_name=full_name, first_name=first, last_name=last,
                                               active=True, household_id=household_id)
                        .returning(people.c.id)).scalar_one()
    _CREATED["people"].append(pid); return pid


def _household(name):
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
    _CREATED["households"].append(hid); return hid


def _org(name):
    with engine.begin() as c:
        oid = c.execute(relationship_entities.insert().values(entity_type="organization", name=name,
                                                              active=True).returning(relationship_entities.c.id)).scalar_one()
    _CREATED["relationship_entities"].append(oid); return oid


def _unlinked_doc(folder, original_name="doc.pdf"):
    with engine.begin() as c:
        did = c.execute(documents.insert().values(
            person_id=None, household_id=None, organization_id=None,
            original_name=original_name, stored_name=f"lnk-{_TAG}-{uuid.uuid4().hex}",
            storage_path="x", storage_uri="C:\\legacy\\" + original_name, size_bytes=10, sha256="0" * 64,
            status="active", tags={"source_system": "TaxDome Drive", "taxdome_folder": folder})
            .returning(documents.c.id)).scalar_one()
    _CREATED["documents"].append(did); return did


def _import_jobs_count():
    with engine.connect() as c:
        return int(c.execute(select(func.count()).select_from(import_jobs)).scalar_one())


def test_remediation_preview_resolves_all_entity_kinds_readonly():
    hid = _household(f"White HH {_TAG}")
    abigail = _person(f"Abigail Dargis{_TAG}")
    _person(f"Michael White{_TAG}", household_id=hid)
    _person(f"Debra White{_TAG}", household_id=hid)
    _person(f"Bravo Dup{_TAG}"); _person(f"Bravo Dup{_TAG}")           # duplicate -> ambiguous
    org = _org(f"Star City Heating{_TAG}")

    d_person1 = _unlinked_doc(f"Abigail Dargis{_TAG}", "1040.pdf")
    d_person2 = _unlinked_doc(f"Abigail Dargis{_TAG}", "w2.pdf")
    d_house   = _unlinked_doc(f"Michael and Debra White{_TAG}")
    d_biz     = _unlinked_doc(f"Star City Heating{_TAG}")
    d_amb     = _unlinked_doc(f"Bravo Dup{_TAG}")
    d_unm     = _unlinked_doc(f"Zzz Nobody {_TAG}")
    d_junk    = _unlinked_doc(f"Abigail Dargis{_TAG}", "Thumbs.db")     # excluded as junk

    before_jobs = _import_jobs_count()
    result = LinkageRemediationJob(MigrationConfig.from_env()).run(Mode.PREVIEW)
    by = {r["document_id"]: r for r in result.reconciliation}

    # person (both docs), household, business, ambiguous, unmatched
    assert by[d_person1]["resolution"] == "people" and str(by[d_person1]["proposed_entity_id"]) == str(abigail)
    assert by[d_person2]["resolution"] == "people"
    assert by[d_house]["resolution"] == "households" and str(by[d_house]["proposed_entity_id"]) == str(hid)
    assert by[d_biz]["resolution"] == "businesses" and by[d_biz]["proposed_entity_type"] == "organization" \
        and str(by[d_biz]["proposed_entity_id"]) == str(org)
    assert by[d_amb]["resolution"] == "ambiguous" and by[d_amb]["proposed_entity_id"] == ""
    assert by[d_unm]["resolution"] == "unmatched"

    # junk excluded from the analysis entirely
    assert d_junk not in by
    assert result.counts["junk_excluded"] >= 1

    # READ-ONLY: no import_jobs row; every seeded document remains unlinked
    assert _import_jobs_count() == before_jobs
    with engine.connect() as conn:
        linked = conn.execute(select(func.count()).select_from(documents).where(
            documents.c.id.in_([d_person1, d_house, d_biz]),
            documents.c.person_id.isnot(None))).scalar_one()
    assert linked == 0


def test_apply_refused_before_any_write():
    with pytest.raises(ModeNotSupported):
        LinkageRemediationJob(MigrationConfig.from_env()).run(Mode.APPLY)


def test_business_folder_needs_exact_org_match_else_review():
    _org(f"Acme Holdings{_TAG}")
    matched = _unlinked_doc(f"Acme Holdings{_TAG}")
    nomatch = _unlinked_doc(f"Unknown Vendor {_TAG}")
    result = LinkageRemediationJob(MigrationConfig.from_env()).run(Mode.PREVIEW)
    by = {r["document_id"]: r for r in result.reconciliation}
    assert by[matched]["resolution"] == "businesses"
    assert by[nomatch]["resolution"] == "unmatched"      # never auto-created, never guessed
