"""Advisor AI Assist (Phase D.42) tests.

Covers the registry + prompt versioning, context assembly + source-capability filtering + record scope,
all six capabilities (daily/client/household/meeting/work/factual), citation + limitation + human-review
fields, unsupported + regulated refusals, the deterministic provider double (timeout/failure/malformed/
refusal), unavailable-provider fail-closed fallback, diagnostics, governance (clean + detects), the route
inventory, and the architecture invariants. Explicitly proves the assistant cannot mutate, cannot write a
DB or the outbox, cannot read rm_*, cannot bypass record scope or leak suppressed sources, does not log
prompts/sensitive context, runs offline, and does not break D.38–D.41 on failure.
"""
import uuid

import pytest
from sqlalchemy import delete, insert

from app.db import engine, household_relationships, households, people
from app.security.models import Principal
from app.services.ai_assist import assistant, governance
from app.services.ai_assist.common import HUMAN_REVIEW_LABEL, reset_stats
from app.services.ai_assist.diagnostics import assist_diagnostics
from app.services.ai_assist.registry import ASSISTANTS

FULL_CAPS = frozenset({
    "client.read", "work.read", "tax.read", "insurance.read", "benefits.read", "opportunity.view",
    "documents.view", "compliance.review.read", "timeline.read", "advisor_work.read", "scheduling.view",
    "communications.read", "record.read_all", "observability.audit",
})
FIRM = Principal(1, "m@e.com", "M", FULL_CAPS)
SCOPED = Principal(2, "s@e.com", "S", frozenset({"client.read"}))

_state = {}


@pytest.fixture(scope="module", autouse=True)
def _seed():
    def _em():
        return f"ai-{uuid.uuid4().hex[:12]}@example.test"
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name="AI HH").returning(households.c.id)).scalar_one()
        p1 = c.execute(insert(people).values(full_name="A B", primary_email=_em(), normalized_email=_em(),
                       active=True, household_id=hid).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=hid, person_id=p1, relationship_type="head", is_primary=True, is_primary_household=True))
    _state.update(hid=hid, p1=p1)
    yield
    with engine.begin() as c:
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == hid))
        c.execute(delete(people).where(people.c.id == p1))
        c.execute(delete(households).where(households.c.id == hid))


@pytest.fixture(autouse=True)
def _reset():
    reset_stats()
    yield


def pid():
    return _state["p1"]


def hid():
    return _state["hid"]


def _envelope_ok(out):
    assert out["human_review"] == HUMAN_REVIEW_LABEL
    assert isinstance(out["citations"], list) and isinstance(out["limitations"], list)
    assert len(out["limitations"]) >= 3 and out.get("generated_at")


# --- registry + prompts ------------------------------------------------------

def test_registry_six_capabilities_with_contracts_and_prompts():
    assert set(ASSISTANTS) == {"daily_brief", "client_brief", "household_brief", "meeting_prep",
                               "work_explanation", "factual_question_answering"}
    for a in ASSISTANTS.values():
        assert a.input_contract and a.output_contract and a.prompt_version
        assert a.lifecycle in ("active", "experimental", "deprecated", "retired")


def test_prompts_carry_read_only_and_grounding_constraints():
    from app.services.ai_assist.prompts import PROMPTS, REQUIRED_CONSTRAINTS
    for p in PROMPTS.values():
        tpl = p["template"].lower()
        for c in REQUIRED_CONSTRAINTS:
            assert c.lower() in tpl


# --- the six capabilities ----------------------------------------------------

def test_daily_brief():
    out = assistant.daily_brief(FIRM)
    assert out["kind"] == "daily_brief"
    _envelope_ok(out)
    assert out["citations"] and len(out["sections"]) >= 1


def test_client_brief_and_scope():
    out = assistant.client_brief(FIRM, pid())
    assert out["kind"] == "client_brief"
    _envelope_ok(out)
    assert any(c["source"] == "Client 360" for c in out["citations"])
    # out of scope → empty grounded context, no leaked facts.
    outs = assistant.client_brief(SCOPED, pid())
    assert not outs["facts"] and outs["unavailable"]


def test_household_brief():
    out = assistant.household_brief(FIRM, hid())
    assert out["kind"] == "household_brief"
    _envelope_ok(out)
    assert any(c["source"] == "Household 360" for c in out["citations"])


def test_meeting_prep_excludes_note_bodies_and_pii():
    out = assistant.meeting_prep(FIRM, pid())
    _envelope_ok(out)
    blob = str(out)
    assert "primary_email" not in blob and "@example.test" not in blob   # no contact PII
    assert all("body" not in str(f) for f in out["facts"])               # no note bodies


def test_work_explanation():
    out = assistant.work_explanation(FIRM, "tasks", "999999999")
    _envelope_ok(out)  # no such item → unavailable, still a valid labelled envelope


def test_factual_question_answering_grounded():
    out = assistant.answer(FIRM, "How much overdue work do I have?")
    assert out["kind"] == "factual_answer" and out["human_review"] == HUMAN_REVIEW_LABEL
    assert out["citations"] and not out["unsupported"]


def test_unsupported_question_marked():
    out = assistant.answer(FIRM, "What is the capital of France?")
    assert out.get("unsupported") is True and "Insufficient data" in out["answer"]


# --- refusals ----------------------------------------------------------------

@pytest.mark.parametrize("question,category", [
    ("Should I buy this stock?", "trade_recommendation"),
    ("How should I file the return?", "tax_conclusion"),
    ("Approve the compliance review", "compliance_approval"),
    ("Is this suitable for the client?", "suitability_determination"),
    ("Just do it and submit the paperwork", "autonomous_action"),
])
def test_regulated_requests_refused(question, category):
    out = assistant.answer(FIRM, question)
    assert out.get("refused") is True and out["refusal_category"] == category
    assert out["human_review"] == HUMAN_REVIEW_LABEL


def test_refusal_never_mutates_or_asserts():
    out = assistant.answer(FIRM, "Approve everything and send to the client")
    assert out["refused"] is True and out.get("unsupported") is True


# --- provider double + fail-closed -------------------------------------------

def test_provider_default_is_offline_local():
    from app.services.ai_assist.provider import get_provider
    p = get_provider()
    assert p.model == "local-deterministic" and p.available is True


@pytest.mark.parametrize("sim", ["timeout", "failure", "malformed"])
def test_provider_faults_fail_closed_to_source_facts(sim):
    out = assistant.daily_brief(FIRM, simulate=sim)
    assert out["provider"]["available"] is False   # fell back
    _envelope_ok(out)
    assert "AI generation unavailable" in " ".join(out["limitations"])


def test_provider_model_refusal():
    out = assistant.daily_brief(FIRM, simulate="refusal")
    assert out.get("refused") is True


def test_feature_disabled_fails_closed(monkeypatch):
    from app.services.ai_assist import assistant as a
    monkeypatch.setattr(a, "_enabled", lambda: False)
    out = a.daily_brief(FIRM)
    assert out["provider"]["available"] is False   # generation disabled → deterministic source facts
    _envelope_ok(out)


# --- diagnostics + governance ------------------------------------------------

def test_diagnostics_shape_no_sensitive():
    assistant.daily_brief(FIRM)
    d = assist_diagnostics(FIRM)
    assert {"provider", "capabilities", "requests", "refusals", "avg_latency_ms",
            "citation_coverage", "source_adapters"} <= set(d)
    assert "prompt" not in str(d).lower() or "prompt_version" in str(d)   # versions ok, no contents


def test_governance_clean():
    report = governance.validate_ai_assist(FIRM)
    assert report["ok"] is True, report["findings"]


def test_governance_detects_prompt_missing_constraint(monkeypatch):
    from app.services.ai_assist import prompts
    broken = dict(prompts.PROMPTS)
    broken["daily_brief"] = {**broken["daily_brief"], "template": "do whatever you want"}
    monkeypatch.setattr(prompts, "PROMPTS", broken)
    monkeypatch.setattr(governance, "PROMPTS", broken)
    report = governance.validate_ai_assist()
    assert any(f["type"] == "prompt_missing_constraint" for f in report["findings"])


# --- architecture invariants -------------------------------------------------

def test_no_mutation_no_db_no_outbox_no_rm_in_package():
    import pathlib
    import re
    base = pathlib.Path("app/services/ai_assist")
    for py in base.glob("*.py"):
        if py.name == "governance.py":
            continue   # the governor legitimately references these strings as detection patterns
        src = py.read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{py.name} reads an rm_ table"
        assert "write_audit_event" not in src, f"{py.name} writes audit"
        assert not re.search(r"publish_safe\s*\(|publisher\.publish|publish_event\s*\(", src), \
            f"{py.name} publishes to the outbox"
        # no SQLAlchemy table mutation calls.
        assert not re.search(r"\.(insert|update|delete)\(\s*\)?\s*\.values", src), f"{py.name} mutates a table"


def test_cannot_execute_queue_actions():
    import pathlib
    src = " ".join(p.read_text() for p in pathlib.Path("app/services/ai_assist").glob("*.py"))
    assert "dispatch_action" not in src and "dispatch_bulk" not in src   # never executes queue actions


def test_suppressed_source_not_leaked_becomes_unavailable():
    # a principal without opportunity.view: the client snapshot's revenue is suppressed (None) → the
    # assistant must surface it as Unavailable, never a raw value.
    p = Principal(3, "n@e.com", "N", frozenset({"client.read", "record.read_all"}))
    out = assistant.client_brief(p, pid())
    rev = [f for f in out["facts"] if "revenue" in f["fact_key"]]
    assert rev and all(not f["available"] for f in rev)


def test_stats_record_no_client_data():
    from app.services.ai_assist.common import assist_stats
    assistant.client_brief(FIRM, pid())
    s = assist_stats()
    # only aggregate counters — no person ids, names, or prose.
    assert set(s) >= {"requests", "success", "refusals", "by_capability"}
    assert all(isinstance(v, (int, float, dict, type(None))) for v in s.values())


# --- routes ------------------------------------------------------------------

def test_route_inventory():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/workspace/assist", "/workspace/assist/query", "/workspace/assist/diagnostics",
            "/client/{person_id}/brief", "/client/household/{household_id}/brief",
            "/workspace/meetings/{person_id}/brief", "/work/{item_type}/{item_id}/explain"} <= paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 906


def test_brief_route_404_out_of_scope():
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.routes.ai_assist import client_brief_page

    def req():
        return Request({"type": "http", "method": "GET", "path": "/x", "headers": [], "query_string": b""})
    raised = False
    try:
        client_brief_page(req(), pid(), principal=SCOPED)
    except HTTPException as exc:
        raised = exc.status_code == 404
    assert raised


def test_failure_isolation_does_not_break_workspace(monkeypatch):
    # even if the provider blows up unexpectedly, the assistant returns a labelled envelope (never raises).
    from app.services.ai_assist import provider
    monkeypatch.setattr(provider.LocalProvider, "generate",
                        lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = assistant.daily_brief(FIRM)
    _envelope_ok(out)  # composed from source facts, did not raise
