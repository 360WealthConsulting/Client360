"""Knowledge registries (Phase D.45) — the two declarative catalogs of the Enterprise Knowledge Graph:

  * ENTITY_REGISTRY — every entity TYPE the graph can expose as a node (owner service, source service,
    lifecycle, visibility, deep-link template, explainability note). This is the single place a new entity
    type is registered.
  * RELATIONSHIP_REGISTRY — every relationship TYPE the graph can expose as an edge (direction,
    authoritative owner, visibility, explanation template, traversal rule, lifecycle). Reuses the platform's
    existing `relationship_types` vocabulary plus the implicit domain edges (advises, insured_by, attends…).

The graph is a SEMANTIC LAYER over the authoritative models — governance verifies that every node/edge the
adapters produce is registered here, and that no duplicate ownership or hidden entity leaks.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import BOTH, INTERNAL

LIFECYCLES = ("active", "experimental", "deprecated", "retired")

# Traversal rules — how far an edge may be followed from a root.
TRAVERSE_ROOT = "root_only"        # only from the root entity (domain collections)
TRAVERSE_ONE_HOP = "one_hop"       # one relationship hop (people/households/businesses)
TRAVERSE_TERMINAL = "terminal"     # a leaf node, never traversed onward (advisor, carrier)


# --- entity registry ---------------------------------------------------------

@dataclass(frozen=True)
class EntityType:
    key: str
    owner: str                 # authoritative owning service
    source_service: str        # the read function's module
    lifecycle: str
    visibility: str
    deep_link: str             # deep-link template (…/{id})
    explainability: str        # how the platform knows this entity exists
    collection: bool = False   # a bounded per-domain summary node (count + link), not an individual record


def _e(key, owner, source, deep_link, explain, visibility=INTERNAL, collection=False, lifecycle="active"):
    return EntityType(key, owner, source, lifecycle, visibility, deep_link, explain, collection)


ENTITY_REGISTRY = (
    _e("person", "people", "client360.service", "/client/{id}",
       "A person record in the CRM.", visibility=BOTH),
    _e("household", "households", "client360.household", "/client/household/{id}",
       "A household grouping in the CRM.", visibility=BOTH),
    _e("business", "organization_service", "organization_service", "/organizations/{id}",
       "An organization/business entity (a relationship_entity of type business)."),
    _e("trust", "relationships", "relationships", "/client/{id}",
       "A trust entity (a named relationship_entity of type trust)."),
    _e("estate", "relationships", "relationships", "/client/{id}",
       "An estate entity (a named relationship_entity of type estate)."),
    _e("professional", "relationships", "relationships", "/client/{id}",
       "An outside professional (CPA/attorney/etc.) modeled as a relationship_entity."),
    _e("insurance_carrier", "relationships", "relationships", "/insurance",
       "An insurance carrier modeled as a relationship_entity."),
    _e("advisor", "identity", "identity", "/admin",
       "A staff user assigned to the client via record_assignments.", visibility=INTERNAL,
       lifecycle="active"),
    # Bounded per-domain collection nodes — a count + deep link, never individual hidden records.
    _e("accounts", "portfolio", "portfolio", "/portfolio",
       "The client's investment accounts/portfolio.", collection=True),
    _e("insurance", "insurance", "insurance", "/insurance",
       "The client's insurance policies.", collection=True),
    _e("tax", "tax_return_lifecycle", "tax_return_lifecycle", "/tax/returns",
       "The client's tax returns.", collection=True),
    _e("documents", "document_platform", "document_platform", "/document-library",
       "The client's documents.", collection=True),
    _e("meetings", "scheduling", "scheduling", "/scheduling",
       "The client's meetings/appointments.", collection=True),
    _e("communications", "communications.engagement", "communications.engagement", "/engagement",
       "The client's unified communications/interactions.", collection=True),
    _e("work", "work_queue", "work_queue", "/work",
       "The client's open work items.", collection=True),
    _e("opportunities", "opportunity", "opportunity", "/opportunities",
       "The client's sales opportunities.", collection=True),
    _e("benefits", "benefits_enrollment", "benefits_enrollment", "/benefits",
       "The client's benefit enrollments.", collection=True),
)

_ENTITY_BY_KEY = {e.key: e for e in ENTITY_REGISTRY}
COLLECTION_TYPES = tuple(e.key for e in ENTITY_REGISTRY if e.collection)
INTERNAL_ONLY_ENTITIES = tuple(e.key for e in ENTITY_REGISTRY if e.visibility == INTERNAL)


# --- relationship registry ---------------------------------------------------

@dataclass(frozen=True)
class RelationshipType:
    code: str
    direction: str             # directed | undirected
    authoritative_owner: str
    visibility: str
    explanation: str           # why this relationship exists (template)
    traversal_rule: str
    lifecycle: str
    category: str              # family | business | professional | estate | ownership | domain | assignment


def _r(code, direction, owner, explain, traversal, category, visibility=INTERNAL, lifecycle="active"):
    return RelationshipType(code, direction, owner, visibility, explain, traversal, lifecycle, category)


RELATIONSHIP_REGISTRY = (
    # Entity edges backed by the authoritative relationship engine (relationship_types vocabulary).
    _r("member_of", "directed", "households", "The person is a member of the household.",
       TRAVERSE_ONE_HOP, "family", visibility=BOTH),
    _r("spouse", "undirected", "relationships", "A spouse relationship recorded in the CRM.",
       TRAVERSE_ONE_HOP, "family"),
    _r("child", "directed", "relationships", "A parent/child relationship recorded in the CRM.",
       TRAVERSE_ONE_HOP, "family"),
    _r("owns", "directed", "organization_service", "The person/household owns the business (ownership edge).",
       TRAVERSE_ONE_HOP, "ownership"),
    _r("employer_of", "directed", "organization_service", "An employment relationship recorded in the CRM.",
       TRAVERSE_ONE_HOP, "business"),
    _r("trustee_of", "directed", "relationships", "The person is a trustee of the trust.",
       TRAVERSE_ONE_HOP, "estate"),
    _r("beneficiary_of", "directed", "relationships", "The person is a beneficiary of the entity.",
       TRAVERSE_ONE_HOP, "estate"),
    _r("advises", "directed", "relationships", "An outside professional advises the client.",
       TRAVERSE_ONE_HOP, "professional"),
    _r("related_to", "undirected", "relationships", "A general relationship recorded in the CRM.",
       TRAVERSE_ONE_HOP, "professional"),
    # Assignment edge backed by record_assignments.
    _r("advised_by", "directed", "identity", "A staff advisor is assigned to the client (record_assignments).",
       TRAVERSE_TERMINAL, "assignment"),
    # Domain edges to bounded collection nodes (authoritative domain records).
    _r("has_accounts", "directed", "portfolio", "The client holds investment accounts.",
       TRAVERSE_ROOT, "domain"),
    _r("insured_by", "directed", "insurance", "The client holds insurance policies.",
       TRAVERSE_ROOT, "domain"),
    _r("has_tax_returns", "directed", "tax_return_lifecycle", "The client has tax returns on file.",
       TRAVERSE_ROOT, "domain"),
    _r("uploaded", "directed", "document_platform", "The client has documents on file.",
       TRAVERSE_ROOT, "domain"),
    _r("attends", "directed", "scheduling", "The client has meetings/appointments.",
       TRAVERSE_ROOT, "domain"),
    _r("communicated", "directed", "communications.engagement", "The client has communications/interactions.",
       TRAVERSE_ROOT, "domain"),
    _r("has_work", "directed", "work_queue", "The client has open work items.",
       TRAVERSE_ROOT, "domain"),
    _r("has_opportunities", "directed", "opportunity", "The client has sales opportunities.",
       TRAVERSE_ROOT, "domain"),
    _r("enrolled_in", "directed", "benefits_enrollment", "The client has benefit enrollments.",
       TRAVERSE_ROOT, "domain"),
)

_REL_BY_CODE = {r.code: r for r in RELATIONSHIP_REGISTRY}
# Map the relationship engine's raw codes onto registered graph relationships.
_RAW_CODE_MAP = {
    "spouse": "spouse", "child": "child", "parent": "child", "sibling": "related_to",
    "owner": "owns", "owns": "owns", "business_partner": "related_to",
    "employer": "employer_of", "employee": "employer_of",
    "trustee": "trustee_of", "successor_trustee": "trustee_of", "beneficiary": "beneficiary_of",
    "executor": "trustee_of", "power_of_attorney": "advises",
    "cpa": "advises", "attorney": "advises", "insurance_agent": "advises", "banker": "advises",
    "financial_advisor": "advises", "household_member": "member_of",
    "parent_of": "owns", "affiliate_of": "related_to", "related_to": "related_to",
}


def entity_type(key) -> EntityType | None:
    return _ENTITY_BY_KEY.get(key)


def relationship_type(code) -> RelationshipType | None:
    return _REL_BY_CODE.get(code)


def map_raw_relationship(raw_code) -> str | None:
    """Map a raw relationship-engine code onto a registered graph relationship (or None if unknown)."""
    return _RAW_CODE_MAP.get(raw_code)


def deep_link_for(entity_key, entity_id) -> str | None:
    e = _ENTITY_BY_KEY.get(entity_key)
    if e is None:
        return None
    return e.deep_link.replace("{id}", str(entity_id)) if entity_id is not None else e.deep_link


def entity_registered(key) -> bool:
    return key in _ENTITY_BY_KEY


def relationship_registered(code) -> bool:
    return code in _REL_BY_CODE


def coverage() -> dict:
    return {
        "entity_types": len(ENTITY_REGISTRY),
        "relationship_types": len(RELATIONSHIP_REGISTRY),
        "collection_entities": len(COLLECTION_TYPES),
        "internal_only_entities": len(INTERNAL_ONLY_ENTITIES),
        "raw_code_mappings": len(_RAW_CODE_MAP),
        "domain_relationships": sum(1 for r in RELATIONSHIP_REGISTRY if r.category == "domain"),
    }
