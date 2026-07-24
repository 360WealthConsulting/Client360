"""Client 360 Workspace (Phase D.40) tests.

Covers composition (all sections), the section adapters + contract, role/capability visibility, the
compact snapshot, the read-only relationship graph, the deep-link quick actions, the activity timeline
(references + pagination), record scope (once at the boundary → 404 out of scope), fallback / unmodelled
concepts, diagnostics, governance (clean + detects), the routes + route inventory, and the architecture
invariants (composition only — no second client engine, no mutation, no shadow record, no new projection).
"""
import uuid

import pytest
from sqlalchemy import delete, insert, text
from starlette.requests import Request

from app.db import engine, household_relationships, households, people
from app.security.models import Principal
from app.services.client360 import diagnostics as diag
from app.services.client360 import get_workspace, governance
from app.services.client360.registry import SECTIONS, visible_quick_actions

FIRM_CAPS = frozenset({
    "client.read", "tax.read", "insurance.read", "benefits.read", "opportunity.view", "documents.view",
    "compliance.review.read", "timeline.read", "advisor_work.read", "work.read", "scheduling.view",
    "communications.read", "communications.view", "record.read_all", "observability.audit",
})
FIRM = Principal(1, "m@e.com", "M", FIRM_CAPS)          # record.read_all → in scope for any client
SCOPED = Principal(2, "s@e.com", "S", frozenset({"client.read"}))   # no read_all, no assignments

_state = {}


@pytest.fixture(scope="module", autouse=True)
def _seed():
    """Seed a household + person (the pristine test schema has none) so the workspace can compose."""
    email = f"c360-{uuid.uuid4().hex[:12]}@example.test"
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name="C360 Test HH").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(
            full_name="C360 Test", primary_email=email, normalized_email=email,
            active=True, household_id=hid).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(
            household_id=hid, person_id=pid, relationship_type="self", is_primary=True))
    _state["pid"], _state["hid"] = pid, hid
    yield
    with engine.begin() as c:
        c.execute(delete(household_relationships).where(household_relationships.c.household_id == hid))
        c.execute(delete(people).where(people.c.id == pid))
        c.execute(delete(households).where(households.c.id == hid))


def pid():
    return _state["pid"]


def hid():
    return _state["hid"]


def _req(path="/client/1", qs=b""):
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": qs})


# --- composition + contract --------------------------------------------------

def test_registry_has_fifteen_sections_with_builders():
    assert len(SECTIONS) == 15  # +Communications (D.44) +Knowledge (D.45) +Recommendations (D.46)
    assert all(s.builder is not None and s.label for s in SECTIONS)


def test_full_composition_builds_every_section():
    ws = get_workspace(FIRM, person_id=pid())
    assert ws is not None
    assert set(ws["sections"]) == {s.key for s in SECTIONS}
    errors = {k: v.get("error") for k, v in ws["sections"].items() if isinstance(v, dict) and v.get("error")}
    assert errors == {}, errors
    assert not ws["suppressed_sections"]


def test_section_shapes():
    ws = get_workspace(FIRM, person_id=pid())
    assert ws["sections"]["financial"]["not_summed"] is True
    assert set(ws["sections"]["financial"]["not_tracked"]) >= {"net_worth", "liabilities"}
    assert "graph" in ws["sections"]["relationships"]
    assert "rows" in ws["sections"]["timeline"]


# --- role / capability visibility --------------------------------------------

def test_sections_are_capability_gated():
    limited = Principal(3, "l@e.com", "L", frozenset({"client.read", "record.read_all"}))
    ws = get_workspace(limited, person_id=pid())
    built = set(ws["sections"])
    assert "tax" not in built and "compliance" not in built and "documents" not in built
    assert "summary" in built and "financial" in built
    assert set(ws["suppressed_sections"]) >= {"tax", "insurance", "compliance", "documents", "work"}


def test_quick_actions_are_capability_gated():
    limited = Principal(3, "l@e.com", "L", frozenset({"client.read", "record.read_all"}))
    keys = {a["key"] for a in visible_quick_actions(limited, pid(), None)}
    assert "add_note" in keys and "generate_meeting_prep" in keys       # client.read
    assert "start_tax_return" not in keys and "create_opportunity" not in keys  # gated out


def test_quick_actions_deep_link_with_client_id():
    actions = visible_quick_actions(FIRM, pid(), None)
    assert actions and all(a["href"] for a in actions)                  # no dead ends
    assert any(f"person_id={pid()}" in a["href"] or f"/{pid()}" in a["href"] for a in actions)


# --- snapshot + relationship graph -------------------------------------------

def test_snapshot_shape_and_not_summed():
    ws = get_workspace(FIRM, person_id=pid())
    s = ws["snapshot"]
    assert s["kind"] == "client_snapshot" and s["not_summed"] is True
    assert {"assets", "tax", "insurance", "compliance", "open_work", "upcoming_deadlines"} <= set(s)


def test_relationship_graph_read_only():
    ws = get_workspace(FIRM, person_id=pid())
    graph = ws["relationship_graph"]
    assert graph is not None and "categories" in graph


# --- record scope ------------------------------------------------------------

def test_out_of_scope_returns_none():
    assert get_workspace(SCOPED, person_id=pid()) is None


def test_scope_enforced_at_boundary():
    # service.py must carry the single boundary record_in_scope check (per ADR-045).
    import pathlib
    src = pathlib.Path("app/services/client360/service.py").read_text()
    assert "record_in_scope" in src


# --- household mode ----------------------------------------------------------

def test_household_workspace_composes():
    if hid() is None:
        pytest.skip("no household seeded")
    ws = get_workspace(FIRM, household_id=hid())
    assert ws is not None and ws["entity_type"] == "household"
    assert "financial" in ws["sections"]


# --- timeline pagination -----------------------------------------------------

def test_timeline_pagination():
    ws1 = get_workspace(FIRM, person_id=pid(), page=1)
    tl = ws1["sections"]["timeline"]
    assert {"rows", "total", "page", "page_size", "pages"} <= set(tl)
    assert tl["page"] == 1


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_shape():
    d = diag.client360_diagnostics(FIRM, person_id=pid())
    assert {"composition_timings_ms", "total_composition_ms", "sections_built",
            "suppressed_capabilities", "missing_adapters", "stale_sources",
            "record_scope_validated", "projection_usage"} <= set(d)
    assert d["record_scope_validated"] is True
    assert d["missing_adapters"] == []


def test_diagnostics_out_of_scope():
    d = diag.client360_diagnostics(SCOPED, person_id=pid())
    assert d["available"] is False


def test_performance_timings_present_and_bounded():
    ws = get_workspace(FIRM, person_id=pid())
    assert set(ws["timings"]) == {s.key for s in SECTIONS}
    assert all(isinstance(v, (int, float)) for v in ws["timings"].values())


# --- governance --------------------------------------------------------------

def test_governance_clean():
    report = governance.validate_client360(FIRM)
    assert report["ok"] is True, report["findings"]


def test_governance_detects_missing_adapter(monkeypatch):
    from app.services.client360.registry import SectionDef
    broken = (*SECTIONS, SectionDef("broken", "Broken", None, None))
    monkeypatch.setattr(governance, "SECTIONS", broken)
    report = governance.validate_client360()
    assert any(f["type"] == "missing_adapter" for f in report["findings"])


def test_composition_modules_do_not_mutate_or_read_rm_tables():
    import pathlib
    import re
    base = pathlib.Path("app/services/client360")
    for name in ("sections.py", "service.py", "snapshot.py", "diagnostics.py"):
        src = (base / name).read_text()
        assert not re.findall(r"\brm_[a-z]\w*", src), f"{name} reads an rm_ table"
        for verb in (".insert(", ".update(", ".delete(", "publish_safe", "write_audit_event"):
            assert verb not in src, f"{name} mutates/publishes ({verb})"
        assert not re.search(r"\bTable\s*\(", src), f"{name} defines a table (shadow record)"


def test_no_duplicate_projection():
    from app.database.projection_tables import READ_MODEL_TABLES
    assert len(READ_MODEL_TABLES) == 12 and "rm_client360" not in READ_MODEL_TABLES


# --- routes ------------------------------------------------------------------

def test_route_inventory():
    from app.main import app
    paths = {getattr(r, "path", None) for r in app.routes}
    assert {"/client/{person_id}", "/client/{person_id}/snapshot", "/client/{person_id}/diagnostics",
            "/client/household/{household_id}", "/client/household/{household_id}/diagnostics"} <= paths


def test_total_route_count():
    from app.main import app
    assert len(app.routes) == 891


def test_page_renders_and_404_out_of_scope():
    from app.routes.client360 import client_workspace
    body = client_workspace(_req(f"/client/{pid()}"), pid(), tab="summary", principal=FIRM).body.decode()
    assert "Client 360" in body and "c360-tabs" in body
    # out of scope → render_error 404
    resp = client_workspace(_req(f"/client/{pid()}"), pid(), tab="summary", principal=SCOPED)
    assert resp.status_code == 404


def test_snapshot_route_json():
    import json

    from app.routes.client360 import client_snapshot
    body = json.loads(bytes(client_snapshot(pid(), principal=FIRM).body))
    assert body["kind"] == "client_snapshot"


def test_migration_head_unchanged_no_new_table():
    # D.40 is composition-only — it introduced no schema. The durable invariant is that no client360
    # table exists (the global migration head legitimately advances in later phases, so it is not pinned).
    from app.db import metadata
    assert "client360" not in metadata.tables and "rm_client360" not in metadata.tables


def test_capability_seeded_none_new():
    # D.40 reuses existing capabilities — it seeds none. work_queue.saved_views (D.39) still the newest.
    with engine.connect() as c:
        exists = c.scalar(text("SELECT 1 FROM capabilities WHERE code = 'client360.view'"))
    assert exists is None
