"""Enterprise Knowledge Graph (Phase D.45) tests.

Covers the semantic composition layer that connects authoritative entities into one explainable graph
WITHOUT a graph database and WITHOUT a second relationship engine: the entity + relationship registries,
graph composition over the authoritative relationship engine, bounded cycle-safe traversal, record-scope
enforcement (out-of-scope people suppressed; out-of-scope root → None → 404), explanation generation
(why/owner/evidence/deep-link/inferred-vs-authoritative), semantic search, runtime gates, Client 360 /
Household 360 section integration, AI grounding, low-cardinality analytics, internal diagnostics, governance
invariants, and the architecture invariants (no graph DB dependency, no duplicate relationship tables, no
mutation, no policy/runtime bypass, no hidden-entity leakage, no unrestricted traversal). Deterministic —
seeds relationship edges via the authoritative engine and composes over them.
"""
import uuid

from sqlalchemy import insert

from app.db import engine, household_relationships, households, people, record_assignments, users
from app.security.models import Principal
from app.services.knowledge import (
    diagnostics,
    explain_relationship,
    gate,
    governance,
    knowledge_graph,
    metrics,
    registry,
    search_entities,
    stats,
    traverse,
)
from app.services.knowledge.adapters import relationship_nodes_edges
from app.services.knowledge.explain import explain_edge
from app.services.knowledge.model import AUTHORITATIVE, INFERRED, Edge
from app.services.relationships import create_relationship

_CAPS = frozenset({"client.read", "record.read_all", "observability.audit"})
FIRM = Principal(1, "a@e.com", "Advisor", _CAPS)
SCOPED = Principal(2, "s@e.com", "Scoped", frozenset({"client.read"}))   # no read_all / assignment


def _seed(label="KG"):
    suffix = uuid.uuid4().hex[:10]
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"{label} {suffix}").returning(households.c.id)).scalar_one()
        pid = c.execute(insert(people).values(household_id=hid, full_name=f"Client {suffix}",
                        active=True).returning(people.c.id)).scalar_one()
        c.execute(insert(household_relationships).values(household_id=hid, person_id=pid,
                  relationship_type="head", is_primary=True, is_primary_household=True))
    return hid, pid, suffix


def _seed_graph(pid, suffix):
    create_relationship(person_id=pid, relationship_code="owner", target_entity_type="business",
                        target_name=f"Biz {suffix}")
    create_relationship(person_id=pid, relationship_code="cpa", target_entity_type="professional",
                        target_name=f"CPA {suffix}")
    create_relationship(person_id=pid, relationship_code="trustee", target_entity_type="trust",
                        target_name=f"Trust {suffix}")


# --- registries --------------------------------------------------------------

def test_entity_and_relationship_registries_complete():
    assert len(registry.ENTITY_REGISTRY) >= 15
    for e in registry.ENTITY_REGISTRY:
        assert e.owner and e.source_service and e.deep_link
        assert e.visibility in ("internal", "external", "both")
        assert e.lifecycle in registry.LIFECYCLES
    for r in registry.RELATIONSHIP_REGISTRY:
        assert r.authoritative_owner and r.explanation and r.traversal_rule
        assert r.lifecycle in registry.LIFECYCLES
    # Every raw relationship-engine code maps onto a registered relationship.
    for raw, mapped in registry._RAW_CODE_MAP.items():
        assert registry.relationship_registered(mapped), raw


def test_registry_coverage_and_no_duplicate_ownership():
    cov = registry.coverage()
    assert cov["entity_types"] == len(registry.ENTITY_REGISTRY)
    assert len({e.key for e in registry.ENTITY_REGISTRY}) == len(registry.ENTITY_REGISTRY)
    assert len({r.code for r in registry.RELATIONSHIP_REGISTRY}) == len(registry.RELATIONSHIP_REGISTRY)


# --- composition -------------------------------------------------------------

def test_graph_composes_entities_and_advisor():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    with engine.begin() as c:
        uid = c.execute(insert(users).values(email=f"adv-{suffix}@e.com", normalized_email=f"adv-{suffix}@e.com",
                        display_name="Adv", auth_subject=f"adv-{suffix}", status="active").returning(users.c.id)).scalar_one()
        c.execute(insert(record_assignments).values(user_id=uid, entity_type="person", entity_id=pid,
                  assignment_type="primary_advisor"))
    g = knowledge_graph(FIRM, person_id=pid)
    assert g["enabled"] is True
    types = {n["entity_type"] for n in g["nodes"]}
    assert {"person", "business", "professional", "trust", "advisor", "household"} <= types
    rels = {e["relationship"] for e in g["edges"]}
    assert {"owns", "advises", "trustee_of", "advised_by", "member_of"} <= rels


def test_every_edge_is_authoritative_and_has_owner():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    g = knowledge_graph(FIRM, person_id=pid)
    for e in g["edges"]:
        assert e["provenance"] == AUTHORITATIVE and e["owner"]
        assert registry.relationship_registered(e["relationship"])


# --- traversal + cycle protection --------------------------------------------

def test_traversal_bounded_and_typed():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    res = traverse(FIRM, person_id=pid, target_type="business")
    assert res["enabled"] is True and res["depth_limit"] == 2
    assert all(p["depth"] == 1 for p in res["paths"])
    assert {p["edges"][0]["relationship"] for p in res["paths"]} == {"owns"}


def test_no_self_loops_and_dedup():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    g = knowledge_graph(FIRM, person_id=pid)
    node_ids = [n["node_id"] for n in g["nodes"]]
    assert len(node_ids) == len(set(node_ids))                 # node dedup
    edge_keys = [(e["source_id"], e["target_id"], e["relationship"]) for e in g["edges"]]
    assert len(edge_keys) == len(set(edge_keys))               # edge dedup
    assert all(e["source_id"] != e["target_id"] for e in g["edges"])   # no self-loops


# --- scope enforcement + hidden-entity suppression ---------------------------

def test_out_of_scope_root_returns_none():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    assert knowledge_graph(SCOPED, person_id=pid) is None
    assert traverse(SCOPED, person_id=pid) is None
    assert search_entities(SCOPED, person_id=pid) is None


def test_out_of_scope_person_counterpart_suppressed(monkeypatch):
    hid, pid, suffix = _seed()
    # A related person the principal cannot access must be suppressed, never leaked.
    create_relationship(person_id=pid, relationship_code="spouse", target_entity_type=None,
                        target_person_id=_other_person())
    # Restrict reachability to only the root person → the spouse is out of scope. The adapter imports
    # accessible_person_ids locally, so patch it at the authoritative source module.
    monkeypatch.setattr("app.security.authorization.accessible_person_ids",
                        lambda conn, principal: {pid})
    nodes, edges, suppressed = relationship_nodes_edges(FIRM, pid)
    person_targets = [e for e in edges if e.target_id.startswith("person:") and e.target_id != f"person:{pid}"]
    assert suppressed >= 1 and person_targets == []


def _other_person():
    with engine.begin() as c:
        hid = c.execute(insert(households).values(name=f"Other {uuid.uuid4().hex[:6]}").returning(households.c.id)).scalar_one()
        return c.execute(insert(people).values(household_id=hid, full_name="Outsider",
                         active=True).returning(people.c.id)).scalar_one()


# --- explanation -------------------------------------------------------------

def test_explanation_answers_the_six_questions():
    edge = Edge(source_id="person:1", target_id="business:9", relationship="owns", label="Owns",
                owner="organization_service", provenance=AUTHORITATIVE, confidence=100,
                deep_link="/organizations/9")
    x = explain_edge(edge).to_dict()
    assert x["why"] and x["authoritative_service"] == "organization_service" and x["evidence"]
    assert x["deep_link"] == "/organizations/9" and x["inferred"] is False


def test_inferred_never_presented_as_authoritative():
    edge = Edge(source_id="person:1", target_id="person:2", relationship="related_to", label="Related",
                owner="relationships", provenance=INFERRED)
    x = explain_edge(edge).to_dict()
    assert x["inferred"] is True and "Inferred" in x["evidence"]


def test_explain_relationship_endpoint_scope_and_match():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    res = explain_relationship(FIRM, person_id=pid, relationship="owns")
    assert res["enabled"] and res["explanation"]["relationship"] == "owns"
    assert explain_relationship(SCOPED, person_id=pid, relationship="owns") is None


# --- search ------------------------------------------------------------------

def test_search_filters_registered_scoped_nodes():
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    biz = search_entities(FIRM, person_id=pid, entity_type="business")
    assert biz["total"] == 1 and biz["results"][0]["entity_type"] == "business"
    by_rel = search_entities(FIRM, person_id=pid, relationship="trustee_of")
    assert {n["entity_type"] for n in by_rel["results"]} == {"trust"}


# --- runtime gates -----------------------------------------------------------

def test_master_gate_disables(monkeypatch):
    monkeypatch.setattr(gate, "gate", lambda name: False)
    g = knowledge_graph(FIRM, person_id=1)
    assert g["enabled"] is False and g["nodes"] == []


def test_search_and_explain_gates(monkeypatch):
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    monkeypatch.setattr(gate, "gate", lambda name: name not in ("knowledge.search.enabled", "explain.enabled"))
    assert search_entities(FIRM, person_id=pid)["enabled"] is False
    g = knowledge_graph(FIRM, person_id=pid)
    assert g["explanations"] == []   # explain gate off → no explanations emitted


def test_traversal_respects_policy_without_bypassing(monkeypatch):
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    monkeypatch.setattr(gate, "policy_ok", lambda area: False)
    res = traverse(FIRM, person_id=pid)
    assert res["paths"] == [] and res.get("denied") == "policy"


# --- Client 360 / Household 360 integration ----------------------------------

def test_client360_knowledge_section():
    from app.services.client360 import get_workspace
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    ws = get_workspace(FIRM, person_id=pid)
    section = ws["sections"]["knowledge"]
    assert section["source"] == "knowledge.graph" and section["not_a_graph_db"] is True
    assert section["summary"]["connected"] >= 3


def test_household360_knowledge_section():
    from app.services.client360.household import get_household_workspace
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    hws = get_household_workspace(FIRM, hid)
    assert hws["sections"]["knowledge"]["summary"]["connected"] >= 3


# --- AI grounding ------------------------------------------------------------

def test_ai_assist_grounds_connected_entity_count_only():
    from app.services.ai_assist.context import assemble
    hid, pid, suffix = _seed()
    _seed_graph(pid, suffix)
    bundle = assemble(FIRM, "client_brief", person_id=pid)
    kf = [f for f in bundle.facts if f.source_type == "knowledge"]
    assert kf and all(isinstance(f.fact_value, int) for f in kf)   # counts only, never relationship contents


# --- analytics + diagnostics + governance ------------------------------------

def test_low_cardinality_metrics_registered():
    from app.services.analytics.metrics import METRICS
    for k in ("knowledge_traversals", "knowledge_explanations", "knowledge_searches",
              "knowledge_adapter_failures"):
        assert k in METRICS
    import json
    assert "@e.test" not in json.dumps(metrics.knowledge_metrics(FIRM))


def test_diagnostics_internal_shape():
    d = diagnostics.knowledge_diagnostics()
    assert {"enabled", "gates", "registry_coverage", "adapter_availability", "governance"} <= set(d)
    assert d["governance"]["ok"] is True and d["adapter_availability"]["relationship"] is True


def test_governance_clean():
    report = governance.validate_knowledge()
    assert report["ok"], report["findings"]


# --- architecture invariants -------------------------------------------------

def test_no_graph_db_no_writes_no_shadow_store():
    import pathlib
    base = pathlib.Path("app/services/knowledge")
    for pyfile in base.rglob("*.py"):
        src = pyfile.read_text()
        if pyfile.name == "governance.py":
            continue  # holds detection literals
        for banned in ("import neo4j", "import rdflib", "sparql", "Table(", "add_timeline_event(",
                       "write_audit_event(", "publisher.publish", "publish_safe("):
            assert banned not in src, f"{banned} in {pyfile}"


def test_composes_authoritative_relationship_engine():
    # The relationship adapter must reuse build_relationship_graph (no second relationship engine).
    import pathlib
    src = pathlib.Path("app/services/knowledge/adapters/relationship.py").read_text()
    assert "build_relationship_graph" in src


def test_stats_reset_and_note():
    stats.reset_stats()
    stats.note("traversals")
    assert stats.knowledge_stats()["traversals"] == 1
