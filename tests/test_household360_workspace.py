"""Household 360 Workspace (Phase D.41) tests.

Covers household composition, the member directory, primary-member resolution, reciprocal
person↔household navigation, household record scope, member visibility + suppression (fail closed),
capability suppression, the financial rollup (authoritative total reused, never re-summed; no fabricated
net worth), per-domain member attribution, unified-work reuse (D.39), timeline deduplication, the
relationship graph + cycle protection, the household snapshot, quick-action deep links, diagnostics,
governance (clean + detects), fail-closed adapters, route inventory, and the architecture invariants
(no second household engine, no shadow record, no direct mutation, no direct rm_* reads, no duplicate
portfolio aggregation, incompatible values never summed, record scope enforced, outbox unchanged).
"""
import uuid

import pytest
from sqlalchemy import delete, insert, text

from app.db import engine, household_relationships, households, people
from app.security.models import Principal
from app.services.client360 import diagnostics as diag
from app.services.client360 import governance
from app.services.client360.household import HOUSEHOLD_SECTIONS, get_household_workspace

FIRM_CAPS = frozenset({
    "client.read", "tax.read", "insurance.read", "benefits.read", "opportunity.view", "documents.view",
    "compliance.review.read", "timeline.read", "advisor_work.read", "work.read", "scheduling.view",
    "communications.read", "record.read_all", "observability.audit",
})
FIRM = Principal(1, "m@e.com", "M", FIRM_CAPS)                          # record.read_all → sees all
SCOPED = Principal(2, "s@e.com", "S", frozenset({"client.read"}))       # no read_all, no assignment

_state = {}


@pytest.fixture(scope="module", autouse=True)
def _seed():
    """Seed a household with two members (pristine schema has none)."""
    def _em():
        return f"h360-{uuid.uuid4().hex[:12]}@example.test"
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name="Smith Household").returning(households.c.id)).scalar_one()
        p1 = c.execute(insert(people).values(full_name="John Smith", primary_email=_em(),
                       normalized_email=_em(), active=True, household_id=hid).returning(people.c.id)).scalar_one()
        p2 = c.execute(insert(people).values(full_name="Jane Smith", primary_email=_em(),
                       normalized_email=_em(), active=True, household_id=hid).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=hid, person_id=p1, relationship_type="head", is_primary=True, is_primary_household=True))
        c.execute(insert(household_relationships).values(
            household_id=hid, person_id=p2, relationship_type="spouse", is_primary=False, is_primary_household=True))
    _state.update(hid=hid, p1=p1, p2=p2)
    yield
    with engine.begin() as c:
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == hid))
        c.execute(delete(people).where(people.c.id.in_([p1, p2])))
        c.execute(delete(households).where(households.c.id == hid))


def hid():
    return _state["hid"]


# --- composition + member directory ------------------------------------------

def test_household_composition_builds_every_section():
    ws = get_household_workspace(FIRM, hid())
    assert ws is not None and ws["entity_type"] == "household"
    assert {k for k, _ in HOUSEHOLD_SECTIONS} == set(ws["sections"])
    errors = {k: v.get("error") for k, v in ws["sections"].items() if isinstance(v, dict) and v.get("error")}
    assert errors == {}, errors


def test_member_directory_and_primary_resolution():
    ws = get_household_workspace(FIRM, hid())
    directory = ws["member_directory"]
    assert len(directory) == 2
    primary = [m for m in directory if m["is_primary"]]
    assert len(primary) == 1 and primary[0]["person_id"] == _state["p1"]
    assert ws["context"]["primary_member"]["id"] == _state["p1"]


def test_household_to_person_navigation():
    ws = get_household_workspace(FIRM, hid())
    for m in ws["member_directory"]:
        assert m["deep_link"] == f"/client/{m['person_id']}"


def test_person_to_household_navigation_link():
    # the person workspace exposes household_id so the template can link back.
    from app.services.client360 import get_workspace
    pw = get_workspace(FIRM, person_id=_state["p1"])
    assert pw["household_id"] == hid()


# --- record scope + member visibility ----------------------------------------

def test_out_of_scope_household_returns_none():
    assert get_household_workspace(SCOPED, hid()) is None


def test_member_visibility_suppresses_out_of_scope_members(monkeypatch):
    # gate members by accessible_person_ids — a member not in the set is suppressed (fail closed).
    from app.services.client360 import household as hh
    monkeypatch.setattr(hh, "accessible_person_ids", lambda c, p: {_state["p1"]})
    ws = get_household_workspace(FIRM, hid())
    assert ws["context"]["active_client_count"] == 1
    assert _state["p2"] in [m["id"] for m in ws["suppressed_members"]]
    fin_members = {m["person_id"] for m in ws["sections"]["financial"]["members"]}
    assert _state["p2"] not in fin_members   # suppressed member not rolled up


def test_capability_suppression():
    limited = Principal(3, "l@e.com", "L", frozenset({"client.read", "record.read_all"}))
    ws = get_household_workspace(limited, hid())
    assert "tax" not in ws["sections"] and "compliance" not in ws["sections"]
    assert {"tax", "insurance", "compliance", "work"} <= set(ws["suppressed_sections"])


# --- financial rollup --------------------------------------------------------

def test_financial_rollup_reuses_authoritative_total_no_fabricated_net_worth():
    ws = get_household_workspace(FIRM, hid())
    fin = ws["sections"]["financial"]
    assert fin["not_summed"] is True
    assert "net_worth" in fin["not_tracked"] and "liabilities" in fin["not_tracked"]
    assert len(fin["members"]) == 2 and all("contribution_pct" in m for m in fin["members"])


def test_incompatible_values_not_summed():
    ws = get_household_workspace(FIRM, hid())
    assert ws["sections"]["financial"]["not_summed"] is True
    assert ws["snapshot"]["not_summed"] is True
    assert ws["sections"]["insurance"]["is_asset"] is False


# --- attribution -------------------------------------------------------------

def test_tax_does_not_infer_relationships():
    ws = get_household_workspace(FIRM, hid())
    assert ws["sections"]["tax"]["inferred_relationships"] is False


def test_member_attribution_present():
    ws = get_household_workspace(FIRM, hid())
    assert ws["sections"]["opportunities"]["member_attributed"] is True
    assert "members" in ws["sections"]["insurance"] and "members" in ws["sections"]["benefits"]


# --- unified work reuse ------------------------------------------------------

def test_work_reuses_unified_queue():
    ws = get_household_workspace(FIRM, hid())
    assert ws["sections"]["work"]["source"] == "work_queue.compose_queue"


# --- timeline + graph --------------------------------------------------------

def test_timeline_deduplication_reported():
    ws = get_household_workspace(FIRM, hid())
    tl = ws["sections"]["timeline"]
    assert "dedup_count" in tl and {"rows", "total", "page"} <= set(tl)


def test_relationship_graph_and_cycle_protection():
    ws = get_household_workspace(FIRM, hid())
    graph = ws["sections"]["relationships"]["graph"]
    assert graph["cycle_protection"] is True and graph["depth_limit"] == 1
    # household + 2 members = at least 3 nodes; 2 membership edges; no self-loops.
    assert graph["node_count"] >= 3 and graph["edge_count"] >= 2
    assert all(e["from"] != e["to"] for e in graph["edges"])


# --- snapshot + quick actions ------------------------------------------------

def test_household_snapshot():
    ws = get_household_workspace(FIRM, hid())
    s = ws["snapshot"]
    assert s["kind"] == "household_snapshot" and s["not_summed"] is True
    assert s["member_count"] == 2 and {"portfolio_assets", "open_work", "connected_businesses"} <= set(s)


def test_quick_actions_deep_link_household():
    ws = get_household_workspace(FIRM, hid())
    actions = ws["quick_actions"]
    assert actions and all(a["href"] for a in actions)
    assert any(f"household_id={hid()}" in a["href"] for a in actions)
    assert any(f"/people/{_state['p1']}/notes" == a["href"] for a in actions)  # primary-member prefill


# --- diagnostics + governance ------------------------------------------------

def test_diagnostics_shape():
    d = diag.household_diagnostics(FIRM, household_id=hid())
    assert {"member_count", "scoped_member_count", "suppressed_members", "graph_node_count",
            "timeline_dedup_count", "record_scope_validated", "composition_timings_ms"} <= set(d)
    assert d["record_scope_validated"] is True


def test_governance_clean():
    report = governance.validate_household360(FIRM)
    assert report["ok"] is True, report["findings"]


def test_governance_detects_missing_adapter(monkeypatch):
    from app.services.client360 import household as hh
    monkeypatch.setattr(hh, "HOUSEHOLD_SECTIONS", (*hh.HOUSEHOLD_SECTIONS, ("broken", None)))
    report = governance.validate_household360()
    assert any(f["type"] == "missing_adapter" for f in report["findings"])


def test_fail_closed_adapter_isolation(monkeypatch):
    from app.services.client360 import household as hh
    monkeypatch.setattr(hh, "_financial",
                        lambda p, c: (_ for _ in ()).throw(RuntimeError("boom")))
    hh._SECTION_BUILDERS["financial"] = hh._financial
    try:
        ws = get_household_workspace(FIRM, hid())
        assert ws["sections"]["financial"].get("error")            # isolated
        assert ws["sections"]["summary"]  # the rest still built
    finally:
        import importlib
        importlib.reload(hh)


# --- architecture invariants -------------------------------------------------

def test_household_module_no_mutation_no_rm_no_shadow_table():
    import pathlib
    import re
    src = pathlib.Path("app/services/client360/household.py").read_text()
    assert not re.findall(r"\brm_[a-z]\w*", src), "reads an rm_ table"
    for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event"):
        assert verb not in src, f"mutates/publishes ({verb})"
    assert not re.search(r"\bTable\s*\(", src), "defines a table (shadow household/person record)"


def test_no_duplicate_portfolio_aggregation():
    import pathlib
    src = pathlib.Path("app/services/client360/household.py").read_text()
    assert "aggregate_portfolio" not in src              # never re-aggregates
    assert "get_household_portfolio" in src              # reuses the single authoritative aggregation


def test_record_scope_enforced_at_boundary():
    import pathlib
    src = pathlib.Path("app/services/client360/household.py").read_text()
    assert "record_in_scope" in src and "accessible_person_ids" in src


def test_no_duplicate_projection():
    from app.database.projection_tables import READ_MODEL_TABLES
    assert len(READ_MODEL_TABLES) == 12 and "rm_household360" not in READ_MODEL_TABLES


# --- routes ------------------------------------------------------------------

def test_route_inventory():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/client/household/{household_id}", "/client/household/{household_id}/snapshot",
            "/client/household/{household_id}/diagnostics"} <= paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 846


def test_household_page_renders_and_404():
    from starlette.requests import Request

    from app.routes.client360 import household_workspace

    def req(qs=b""):
        return Request({"type": "http", "method": "GET", "path": f"/client/household/{hid()}",
                        "headers": [], "query_string": qs})
    body = household_workspace(req(), hid(), tab="summary", principal=FIRM).body.decode()
    assert "Household 360" in body and "Primary" in body
    assert household_workspace(req(), hid(), tab="summary", principal=SCOPED).status_code == 404


def test_migration_head_unchanged_no_new_table():
    with engine.connect() as c:
        assert c.scalar(text("SELECT version_num FROM alembic_version")) == "l3q4v5w6x7y8"
    from app.db import metadata
    assert "household360" not in metadata.tables and "rm_household360" not in metadata.tables
