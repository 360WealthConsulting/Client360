"""Data Governance platform tests (Phase D.23).

Covers data-domain/element CRUD, quality-rule CRUD + seeds, quality checks + findings (required
field, orphan) + finding status, duplicate candidates + safe merge (reusing
``person_merge.merge_source_contacts``) + golden-record + survivorship, lineage (read of
person_source_links + governance lineage for non-person entities), retention assignments (reusing
document_retention_policies + deterministic expiration) + due review, legal holds, deletion/archival
requests + approval (governance.review gate + legal-hold block + NO hard delete), remediation cases,
authorization + record scope, Workflow FK references, Automation dispatch integration, Analytics
consumption, Timeline lifecycle events, append-only audit ledger, and architecture invariants. The
matching/merge engine, document retention, and the D.5 golden are untouched.
"""
import uuid
from datetime import date

import pytest
from sqlalchemy import delete, insert, select, text, update

from app.db import (
    document_retention_policies,
    engine,
    governance_events,
    governance_legal_holds,
    governance_quality_findings,
    people,
    person_source_links,
    record_assignments,
    source_contacts,
    timeline_events,
    users,
)
from app.security.models import Principal
from app.services.governance import catalog, common, mdm, quality, retention
from app.services.governance import service as svc

CAPS = frozenset({"governance.view", "governance.manage", "governance.review", "governance.audit",
                  "governance.admin", "record.read_all", "record.write_all"})


def _sfx():
    return uuid.uuid4().hex[:8]


def _principal(uid, caps=CAPS):
    return Principal(uid, "a@e.test", "A", frozenset(caps))


def _setup():
    tag = _sfx()
    with engine.begin() as c:
        uid = c.execute(users.insert().values(
            email=f"gv-{tag}@e.test", normalized_email=f"gv-{tag}@e.test",
            display_name=f"U {tag}", status="active").returning(users.c.id)).scalar_one()
        stranger = c.execute(users.insert().values(
            email=f"str-{tag}@e.test", normalized_email=f"str-{tag}@e.test",
            display_name=f"S {tag}", status="active").returning(users.c.id)).scalar_one()
        pid = c.execute(people.insert().values(
            full_name=f"Client {tag}", primary_email=f"{tag}@e.test",
            normalized_email=f"{tag}@e.test", active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(
            user_id=uid, entity_type="person", entity_id=pid, assignment_type="owner",
            effective_date=date.today()))
    return {"uid": uid, "stranger": stranger, "pid": pid, "tag": tag}


def _source_contact(tag, i):
    with engine.begin() as c:
        return c.execute(source_contacts.insert().values(
            source_system=f"gov-{tag}", source_file=f"f-{tag}.csv", source_hash=(f"{tag}{i}" * 8)[:64],
            full_name=f"Dup {tag}", email=f"dup-{tag}@e.test", normalized_email=f"dup-{tag}@e.test",
            raw_data={"i": i}).returning(source_contacts.c.id)).scalar_one()


def _teardown(ids):
    tag, uid = ids["tag"], ids["uid"]
    with engine.begin() as c:
        c.execute(delete(person_source_links).where(person_source_links.c.source_contact_id.in_(
            select(source_contacts.c.id).where(source_contacts.c.source_system == f"gov-{tag}"))))
        c.execute(delete(source_contacts).where(source_contacts.c.source_system == f"gov-{tag}"))
        for t in ("governance_cases", "governance_deletion_requests", "governance_legal_holds",
                  "governance_merge_decisions", "governance_duplicate_candidates",
                  "governance_retention_assignments", "governance_survivorship_rules",
                  "governance_quality_findings", "governance_quality_rules", "governance_lineage",
                  "governance_data_elements", "governance_data_domains"):
            c.execute(text(f"DELETE FROM {t} WHERE created_by_user_id = :u"), {"u": uid})
        # quality_checks uses triggered_by_user_id (no created_by column)
        c.execute(text("DELETE FROM governance_quality_checks WHERE triggered_by_user_id = :u"), {"u": uid})
        c.execute(delete(document_retention_policies).where(document_retention_policies.c.code.like(f"rp-{tag}%")))
        c.execute(delete(timeline_events).where(timeline_events.c.source == "governance",
                                                timeline_events.c.person_id == ids["pid"]))
        c.execute(delete(record_assignments).where(record_assignments.c.entity_id == ids["pid"],
                                                   record_assignments.c.entity_type == "person"))
        c.execute(delete(people).where(people.c.full_name.like(f"%{tag}%")))
        # users that wrote governance actions to the append-only audit_events hash-chain cannot be
        # deleted (FK SET NULL would UPDATE an append-only row); leave them as leftovers.


# --- catalog -----------------------------------------------------------------

def test_domain_and_element_crud():
    ids = _setup()
    try:
        d = catalog.create_domain(code=f"dom-{ids['tag']}", name="Accounts",
                                  actor_user_id=ids["uid"])
        e = catalog.create_element(data_domain_id=d["id"], code=f"el-{ids['tag']}", name="Email",
                                   entity_type="person", field_name="primary_email",
                                   classification="pii", required=True, actor_user_id=ids["uid"])
        assert e["classification"] == "pii" and e["required"] is True
        assert any(x["id"] == d["id"] for x in catalog.list_domains())
        with pytest.raises(common.GovernanceError):
            catalog.create_domain(code=f"dom-{ids['tag']}", name="dup", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


def test_quality_rule_crud_and_seeds():
    ids = _setup()
    try:
        r = catalog.create_rule(code=f"r-{ids['tag']}", name="Req email", rule_type="required_field",
                                severity="high", actor_user_id=ids["uid"])
        assert r["rule_type"] == "required_field"
        assert catalog.get_rule(code="person_required_email") is not None   # seeded
        assert catalog.get_rule(code="account_orphan") is not None
        with pytest.raises(common.GovernanceError):
            catalog.create_rule(code=f"bad-{ids['tag']}", name="x", rule_type="not_a_type",
                                actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- quality checks + findings -----------------------------------------------

def test_required_field_check_creates_finding():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        # a person WITHOUT an email
        with engine.begin() as c:
            noemail = c.execute(people.insert().values(
                full_name=f"NoEmail {ids['tag']}", active=True).returning(people.c.id)).scalar_one()
        rule = catalog.create_rule(code=f"r-{ids['tag']}", name="Req email",
                                   rule_type="required_field", config={"field": "primary_email"},
                                   actor_user_id=ids["uid"])
        res = quality.run_check(p, rule["id"], actor_user_id=ids["uid"])
        assert res["status"] == "completed" and res["findings"] >= 1
        with engine.connect() as c:
            f = c.execute(select(governance_quality_findings).where(
                governance_quality_findings.c.rule_id == rule["id"],
                governance_quality_findings.c.entity_id == noemail)).mappings().first()
        assert f is not None and f["status"] == "open"
        # idempotent: re-run opens no duplicate
        res2 = quality.run_check(p, rule["id"], actor_user_id=ids["uid"])
        assert res2["findings"] == 0
        # resolve the finding
        resolved = quality.set_finding_status(p, f["id"], "resolved", actor_user_id=ids["uid"])
        assert resolved["status"] == "resolved" and resolved["resolved_at"] is not None
    finally:
        _teardown(ids)


def test_run_all_active_checks_and_stale_scan():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        catalog.create_rule(code=f"stale-{ids['tag']}", name="Stale", rule_type="stale",
                            config={"days": 3650}, actor_user_id=ids["uid"])
        out = quality.run_all_active_checks(p, actor_user_id=ids["uid"])
        assert out["checks_run"] >= 1
        stale = quality.run_stale_scan(p, actor_user_id=ids["uid"])
        assert "findings_opened" in stale
    finally:
        _teardown(ids)


# --- duplicates + safe merge + golden record ---------------------------------

def test_duplicate_candidate_and_safe_merge_reuses_person_merge():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        s1, s2 = _source_contact(ids["tag"], 1), _source_contact(ids["tag"], 2)
        cand = mdm.create_candidate(p, source_contact_ids=[s1, s2], match_method="manual",
                                    actor_user_id=ids["uid"])
        assert cand["status"] == "open"
        # applying a merge requires governance.review
        no_review = _principal(ids["uid"], {"governance.manage"})
        with pytest.raises(common.GovernanceError):
            mdm.record_merge_decision(no_review, cand["id"], decision="approved", apply=True,
                                      actor_user_id=ids["uid"])
        # with review: reuses person_merge.merge_source_contacts (the safe merge)
        d = mdm.record_merge_decision(p, cand["id"], decision="approved", apply=True,
                                      actor_user_id=ids["uid"])
        assert d["merged_person_id"] is not None
        assert d["golden_record_entity_type"] == "person"
        assert d["golden_record_entity_id"] == d["merged_person_id"]
        assert mdm.get_candidate(p, cand["id"])["status"] == "merged"
        # both source contacts now link to the one person (the safe merge)
        with engine.connect() as c:
            n = c.scalar(select(person_source_links.c.person_id).where(
                person_source_links.c.source_contact_id == s1))
        assert n == d["merged_person_id"]
    finally:
        _teardown(ids)


def test_survivorship_rule_crud():
    ids = _setup()
    try:
        r = catalog.create_survivorship_rule(code=f"sv-{ids['tag']}", name="Recent",
                                             strategy="most_recent", actor_user_id=ids["uid"])
        assert r["strategy"] == "most_recent"
        with pytest.raises(common.GovernanceError):
            catalog.create_survivorship_rule(code=f"sp-{ids['tag']}", name="Prio",
                                             strategy="source_priority", actor_user_id=ids["uid"])
    finally:
        _teardown(ids)


# --- lineage -----------------------------------------------------------------

def test_lineage_reads_person_source_links_and_records_non_person():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        sc = _source_contact(ids["tag"], 9)
        with engine.begin() as c:
            c.execute(person_source_links.insert().values(
                person_id=ids["pid"], source_contact_id=sc, match_method="manual_review",
                match_score=100, confirmed=True))
        lin = mdm.person_lineage(p, ids["pid"])
        assert lin and lin[0]["source_system"] == f"gov-{ids['tag']}"
        # governance lineage only for non-person entities; person is rejected
        with pytest.raises(common.GovernanceError):
            mdm.record_lineage(p, entity_type="person", entity_id=ids["pid"],
                               source_system="x", actor_user_id=ids["uid"])
        row = mdm.record_lineage(p, entity_type="organization", entity_id=1, source_system="wealthbox",
                                 actor_user_id=ids["uid"])
        assert row["entity_type"] == "organization"
    finally:
        _teardown(ids)


# --- retention (document retention integration) ------------------------------

def test_retention_assignment_derives_expiration_and_due_review():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        with engine.begin() as c:
            policy = c.execute(document_retention_policies.insert().values(
                code=f"rp-{ids['tag']}", name="7yr", retention_years=7, action_on_expiry="review")
                .returning(document_retention_policies.c.id)).scalar_one()
        a = retention.create_retention_assignment(p, entity_type="person", entity_id=ids["pid"],
                                                  retention_policy_id=policy, person_id=ids["pid"],
                                                  effective_date=date(2010, 1, 1), actor_user_id=ids["uid"])
        assert a["expiration_date"] == date(2017, 1, 1)   # deterministic: 2010 + 7 (already past)
        # a past expiration -> due review marks it expired + deletion-eligible
        review = retention.review_due_retention(p, actor_user_id=ids["uid"])
        assert review["reviewed"] >= 1
        assigns = retention.list_retention_assignments(status="expired")
        assert any(x["id"] == a["id"] and x["deletion_eligible"] for x in assigns)
    finally:
        _teardown(ids)


# --- legal holds + deletion review (no hard delete) --------------------------

def test_legal_hold_blocks_deletion_and_no_hard_delete():
    ids = _setup()
    try:
        reviewer = _principal(ids["uid"])
        # place a legal hold on the person
        hold = retention.place_legal_hold(reviewer, code=f"lh-{ids['tag']}", name="Litigation",
                                          entity_type="person", entity_id=ids["pid"],
                                          person_id=ids["pid"], actor_user_id=ids["uid"])
        assert hold["status"] == "active"
        assert retention.is_under_legal_hold("person", ids["pid"]) is True
        # a deletion request is blocked by the hold
        req = retention.create_deletion_request(reviewer, entity_type="person", entity_id=ids["pid"],
                                                person_id=ids["pid"], actor_user_id=ids["uid"])
        assert req["legal_hold_blocked"] is True
        with pytest.raises(common.GovernanceError):
            retention.review_deletion_request(reviewer, req["id"], decision="approved",
                                              actor_user_id=ids["uid"])   # blocked by legal hold
        # deletion approval requires governance.review
        manager = _principal(ids["uid"], {"governance.manage"})
        retention.release_legal_hold(reviewer, hold["id"], actor_user_id=ids["uid"])
        req2 = retention.create_deletion_request(reviewer, entity_type="person", entity_id=ids["pid"],
                                                 person_id=ids["pid"], actor_user_id=ids["uid"])
        with pytest.raises(common.GovernanceError):
            retention.review_deletion_request(manager, req2["id"], decision="approved",
                                              actor_user_id=ids["uid"])   # lacks governance.review
        approved = retention.review_deletion_request(reviewer, req2["id"], decision="approved",
                                                     actor_user_id=ids["uid"])
        assert approved["status"] == "approved" and approved["approved_at"] is not None
        executed = retention.execute_deletion(reviewer, req2["id"], actor_user_id=ids["uid"])
        assert executed["status"] == "executed"
        # NO hard delete — the canonical person still exists
        with engine.connect() as c:
            assert c.scalar(select(people.c.id).where(people.c.id == ids["pid"])) == ids["pid"]
    finally:
        _teardown(ids)


def test_archival_review():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        req = retention.create_deletion_request(p, entity_type="document", entity_id=1,
                                                request_type="archival", actor_user_id=ids["uid"])
        assert req["request_type"] == "archival"
        approved = retention.review_deletion_request(p, req["id"], decision="approved",
                                                    actor_user_id=ids["uid"])
        assert approved["status"] == "approved"
    finally:
        _teardown(ids)


# --- remediation cases -------------------------------------------------------

def test_remediation_case_lifecycle():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        case = retention.create_case(p, code=f"c-{ids['tag']}", title="Fix duplicate",
                                     case_type="remediation", person_id=ids["pid"],
                                     actor_user_id=ids["uid"])
        assert case["status"] == "open"
        case = retention.set_case_status(p, case["id"], "resolved", actor_user_id=ids["uid"])
        assert case["status"] == "resolved" and case["resolved_at"] is not None
    finally:
        _teardown(ids)


# --- authorization + record scope --------------------------------------------

def test_finding_scope_blocks_stranger():
    ids = _setup()
    try:
        owner = _principal(ids["uid"])
        f = quality.create_finding(owner, entity_type="person", entity_id=ids["pid"],
                                   finding_type="manual", person_id=ids["pid"], actor_user_id=ids["uid"])
        stranger = _principal(ids["stranger"], {"governance.view"})
        assert quality.get_finding(stranger, f["id"]) is None
    finally:
        _teardown(ids)


# --- integrations ------------------------------------------------------------

def test_automation_dispatch_has_governance_jobs():
    from app.services.automation import dispatch
    for jt in ("governance_quality_scan", "governance_stale_scan", "governance_retention_review"):
        assert jt in dispatch.DISPATCH_REGISTRY


def test_analytics_consumes_governance_metrics():
    ids = _setup()
    try:
        from app.services.analytics import sources
        from app.services.analytics.metrics import METRICS
        p = _principal(ids["uid"])
        before = sources.governance_open_finding_count(p)
        quality.create_finding(p, entity_type="person", entity_id=ids["pid"], finding_type="manual",
                               person_id=ids["pid"], actor_user_id=ids["uid"])
        assert sources.governance_open_finding_count(p) == before + 1
        assert "governance_open_findings" in METRICS and "governance_legal_holds" in METRICS
    finally:
        _teardown(ids)


def test_timeline_events_client_anchored():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        quality.create_finding(p, entity_type="person", entity_id=ids["pid"], finding_type="manual",
                               person_id=ids["pid"], actor_user_id=ids["uid"])
        retention.place_legal_hold(p, code=f"lh-{ids['tag']}", name="Hold", entity_type="person",
                                  entity_id=ids["pid"], person_id=ids["pid"], actor_user_id=ids["uid"])
        with engine.connect() as c:
            types = set(c.scalars(select(timeline_events.c.event_type).where(
                timeline_events.c.source == "governance",
                timeline_events.c.person_id == ids["pid"])))
        assert "governance_finding_opened" in types
        assert "governance_legal_hold_placed" in types
    finally:
        _teardown(ids)


def test_workflow_fk_targets_present():
    for t, col in ((governance_legal_holds, "workflow_instance_id"),):
        assert next(iter(t.c[col].foreign_keys)).column.table.name == "workflow_instances"


# --- append-only audit + architecture invariants -----------------------------

def test_audit_ledger_append_only():
    ids = _setup()
    try:
        p = _principal(ids["uid"])
        f = quality.create_finding(p, entity_type="person", entity_id=ids["pid"],
                                   finding_type="manual", person_id=ids["pid"], actor_user_id=ids["uid"])
        assert any(e["event_type"] == "finding_opened"
                   for e in common.audit_history(p, entity_type="finding", entity_id=f["id"]))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(update(governance_events).where(governance_events.c.entity_id == f["id"])
                          .values(event_type="tampered"))
        with pytest.raises(Exception):
            with engine.begin() as c:
                c.execute(delete(governance_events).where(governance_events.c.entity_id == f["id"]))
    finally:
        _teardown(ids)


def test_governance_does_not_import_composition_layers():
    import pathlib
    root = pathlib.Path(svc.__file__).parent
    for name in ("service.py", "quality.py", "mdm.py", "retention.py", "catalog.py", "common.py"):
        src = (root / name).read_text()
        for layer in ("annual_review", "business_owner", "app.services.reporting"):
            assert f"import {layer}" not in src and f"{layer} import" not in src, f"{name}:{layer}"


def test_route_prefix_matches_no_middleware_rule():
    from app.security.middleware import RULES
    assert not any(pattern.search("/governance") for pattern, _cap in RULES)
    assert not any(pattern.search("/governance/findings/5") for pattern, _cap in RULES)
