"""Unified Communications & Client Engagement (Phase D.44) tests.

Covers the composition layer that provides one governed interaction history WITHOUT a second messaging /
timeline / notification / document / scheduling store: the declarative interaction registry, the read-only
adapters (isolation + fail-closed), timeline composition + ordering + classification (non-communication
activity dropped), unified search, interaction-attribute filtering, visibility classification, record-scope
enforcement (delegated to the authoritative timeline → 404 out of scope), runtime gates, the Client 360 /
Household 360 section integration, AI Assist grounding, low-cardinality analytics, internal diagnostics,
governance invariants, and the architecture invariants (no second store, no copied content, no DB write, no
outbox, no audit write). Deterministic — seeds timeline events and composes over them.
"""
import uuid

from sqlalchemy import insert

from app.db import engine, household_relationships, households, people
from app.security.models import Principal
from app.services.communications.engagement import (
    diagnostics,
    engagement_timeline,
    gate,
    governance,
    metrics,
    portal_engagement,
    registry,
    search_interactions,
    stats,
)
from app.services.communications.engagement.adapters import (
    render_timeline_event,
)
from app.services.communications.engagement.model import Interaction
from app.services.timeline import add_timeline_event

_CAPS = frozenset({"client.read", "record.read_all", "timeline.read", "communications.view",
                   "advisor_work.read", "compliance.review.read", "observability.audit"})
FIRM = Principal(1, "a@e.com", "Advisor", _CAPS)
SCOPED = Principal(2, "s@e.com", "Scoped", frozenset({"communications.view"}))   # no read_all/assignment


def _seed(label="UC"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {suffix}",
                        primary_email=f"{suffix}@e.test", normalized_email=f"{suffix}@e.test",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="self", is_primary=True, is_primary_household=True))
    return hid, pid, suffix


def _seed_interactions(pid):
    add_timeline_event(source="client_portal", event_type="secure_message", title="Portal question",
                       person_id=pid, summary="asked about tax")
    add_timeline_event(source="microsoft", event_type="email_received", title="Client email", person_id=pid)
    add_timeline_event(source="scheduling", event_type="calendar_event", title="Review meeting", person_id=pid)
    add_timeline_event(source="signature_provider", event_type="signature_requested",
                       title="Sign letter", person_id=pid)
    # A non-communication event that must be excluded from the engagement view.
    add_timeline_event(source="portfolio_import", event_type="portfolio_import_completed",
                       title="Import", person_id=pid)


# --- registry ----------------------------------------------------------------

def test_registry_complete_and_classifies():
    assert len(registry.REGISTRY) == 11
    for t in registry.REGISTRY:
        assert t.authoritative_owner and t.source_service and t.retention_class and t.deep_link
        assert t.visibility in ("internal", "external", "both")
        assert t.lifecycle in registry.LIFECYCLES
    assert registry.classify("client_portal", "secure_message") == "secure_message"
    assert registry.classify("microsoft", "email_received") == "email"
    # A non-communication event is not classified.
    assert registry.classify("portfolio_import", "portfolio_import_completed") is None


def test_registry_coverage_and_internal_only():
    cov = registry.coverage()
    assert cov["total_types"] == 11 and cov["timeline_backed"] >= 8
    # Internal reasoning types must be classified internal (never external).
    for itype in ("communication", "email", "note", "workflow_milestone"):
        assert registry.externally_visible(itype) is False


# --- adapter isolation + fail-closed -----------------------------------------

def test_render_timeline_event_pure_and_classifies():
    row = {"event_id": "domain:timeline_event:9", "event_type": "secure_message",
           "source_domain": "activity", "title": "Hi", "summary": "body", "person_id": 5}
    it = render_timeline_event(row)
    assert isinstance(it, Interaction) and it.interaction_type == "secure_message"
    assert it.related_person_id == 5 and it.preview == "body"
    # Non-communication event → None.
    assert render_timeline_event({"event_type": "portfolio_import_completed", "source_domain": "activity"}) is None


def test_timeline_adapter_fails_closed(monkeypatch):
    # If the authoritative timeline raises, the adapter yields None (never propagates).
    import app.services.communications.engagement.adapters.timeline as tl

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr("app.services.activity_timeline.service.client_timeline", boom)
    assert tl.timeline_interactions(FIRM, person_id=1) is None


# --- composition + ordering + classification ---------------------------------

def test_engagement_timeline_composes_and_excludes_non_communication():
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    res = engagement_timeline(FIRM, person_id=pid, page=1, page_size=25)
    assert res["enabled"] is True
    types = [r["interaction_type"] for r in res["rows"]]
    assert set(types) == {"secure_message", "email", "appointment", "signature_request"}
    assert "portfolio_import" not in types and len(types) == 4


def test_timeline_ordering_newest_first():
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    res = engagement_timeline(FIRM, person_id=pid)
    stamps = [r["timestamp"] for r in res["rows"] if r["timestamp"]]
    assert stamps == sorted(stamps, reverse=True)


def test_deduplication_by_interaction_id():
    hid, pid, _ = _seed()
    # Upsert the same external_id twice → one timeline row → one interaction (no duplicate).
    add_timeline_event(source="client_portal", event_type="secure_message", title="Dedup",
                       person_id=pid, external_id=f"dupe-{pid}")
    add_timeline_event(source="client_portal", event_type="secure_message", title="Dedup edited",
                       person_id=pid, external_id=f"dupe-{pid}")
    res = engagement_timeline(FIRM, person_id=pid)
    ids = [r["interaction_id"] for r in res["rows"]]
    assert len(ids) == len(set(ids))


# --- search + filtering ------------------------------------------------------

def test_search_delegates_and_filters():
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    res = search_interactions(FIRM, person_id=pid, query="tax")
    assert res["total"] == 1 and res["rows"][0]["interaction_type"] == "secure_message"


def test_interaction_type_and_action_required_filters():
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    only_email = engagement_timeline(FIRM, person_id=pid, interaction_type="email")
    assert only_email["total"] == 1
    action = engagement_timeline(FIRM, person_id=pid, action_required=True)
    assert all(r["action_required"] for r in action["rows"])
    assert "signature_request" in [r["interaction_type"] for r in action["rows"]]


# --- visibility --------------------------------------------------------------

def test_internal_interactions_never_marked_external():
    hid, pid, _ = _seed()
    add_timeline_event(source="microsoft", event_type="email_received", title="Internal email", person_id=pid)
    res = engagement_timeline(FIRM, person_id=pid)
    email = next(r for r in res["rows"] if r["interaction_type"] == "email")
    assert email["visibility"] == "internal"


# --- scope enforcement -------------------------------------------------------

def test_out_of_scope_returns_none():
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    # SCOPED lacks record.read_all and has no assignment → the authoritative timeline denies → None.
    assert engagement_timeline(SCOPED, person_id=pid) is None


# --- runtime gates -----------------------------------------------------------

def test_master_gate_disables_composition(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: False)
    res = engagement_timeline(FIRM, person_id=1)
    assert res["enabled"] is False and res["rows"] == []


def test_search_gate(monkeypatch):
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    monkeypatch.setattr(gate, "gate", lambda name: name != "engagement.search.enabled")
    assert search_interactions(FIRM, person_id=pid, query="tax")["enabled"] is False


def test_portal_timeline_gate_off_by_default():
    # portal.timeline.enabled defaults OFF — external engagement is opt-in.
    assert gate.GATES["portal.timeline.enabled"] is False


# --- portal integration ------------------------------------------------------

def test_portal_engagement_gate_and_shape(monkeypatch):
    class _PP:
        account_id = 999
    # Off by default.
    assert portal_engagement(_PP())["enabled"] is False
    # On → composes (empty for an account with no data) and never raises.
    monkeypatch.setattr(gate, "gate", lambda name: True)
    monkeypatch.setattr("app.portal.service.portal_scope",
                        lambda account_id, **k: {"person_ids": set(), "shared_household_ids": set()})
    out = portal_engagement(_PP())
    assert out["enabled"] is True and isinstance(out["rows"], list)


# --- advisor / Client 360 / Household 360 integration ------------------------

def test_client360_communications_section():
    from app.services.client360 import get_workspace
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    ws = get_workspace(FIRM, person_id=pid)
    section = ws["sections"]["communications"]
    assert section["source"] == "communications.engagement" and section["summary"]["total"] == 4
    assert section["not_a_second_store"] is True


def test_household360_communications_section():
    from app.services.client360.household import get_household_workspace
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    hws = get_household_workspace(FIRM, hid)
    assert hws["sections"]["communications"]["summary"]["total"] == 4


# --- AI grounding ------------------------------------------------------------

def test_ai_assist_grounds_communication_counts_only():
    from app.services.ai_assist.context import assemble
    hid, pid, _ = _seed()
    _seed_interactions(pid)
    bundle = assemble(FIRM, "client_brief", person_id=pid)
    comm = [f for f in bundle.facts if f.source_type == "communications"]
    assert comm and any(f.fact_key == "communications.recent_interactions" for f in comm)
    # Grounded counts only — never a message body/subject in the fact value.
    for f in comm:
        assert isinstance(f.fact_value, int)


# --- analytics + diagnostics + governance ------------------------------------

def test_low_cardinality_metrics_registered():
    from app.services.analytics.metrics import METRICS
    for k in ("engagement_interactions_composed", "engagement_searches", "engagement_adapter_failures"):
        assert k in METRICS
    m = metrics.engagement_metrics(FIRM)
    # No client identifiers in the metrics payload.
    import json
    blob = json.dumps(m)
    assert "@e.test" not in blob


def test_diagnostics_internal_shape():
    d = diagnostics.engagement_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "adapter_availability", "governance"} <= set(d)
    assert d["governance"]["ok"] is True
    assert d["adapter_availability"]["timeline"] is True


def test_governance_clean():
    report = governance.validate_engagement()
    assert report["ok"], report["findings"]


# --- architecture invariants -------------------------------------------------

def test_no_second_store_no_writes_in_engagement_modules():
    import pathlib
    base = pathlib.Path("app/services/communications/engagement")
    for pyfile in base.rglob("*.py"):
        src = pyfile.read_text()
        if pyfile.name == "governance.py":
            continue  # holds the detection string-literals
        assert "add_timeline_event(" not in src, pyfile
        assert "write_audit_event(" not in src, pyfile
        assert "publisher.publish" not in src and "publish_safe(" not in src, pyfile
        assert "Table(" not in src, pyfile


def test_stats_reset_and_note():
    stats.reset_stats()
    stats.note("searches")
    assert stats.engagement_stats()["searches"] == 1
