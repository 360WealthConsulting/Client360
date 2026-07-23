"""Typed domain-event contracts (Phase D.34) — the executable contract catalog.

Builds the executable ``EventContract`` objects from the shared pure-data seed
(``app/database/event_seed.py``) — the same data the migration seeds into ``domain_event_contracts`` —
so the executable contracts and the registry rows cannot drift. A contract is a typed, versioned
description of a domain event: its type, category, producer, schema version, and a **references-only**
payload schema (field → type; ids/codes only, never PII or secrets). The publisher validates every
published event against its contract; governance reconciles the registry against these definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.database.event_seed import (
    D35_CONTRACTS_SEED,
    DOMAIN_EVENT_CONTRACTS_SEED,
    DOMAIN_EVENT_SUBSCRIPTIONS_SEED,
    EVENT_DOMAINS,
)

_ALLOWED_TYPES = {"int", "str", "float", "bool", "list", "dict"}

# The set of event types adopted by producers in Phase D.35 (governance treats these as requiring an
# actual publishing site).
D35_EVENT_TYPES = frozenset(c["event_type"] for c in D35_CONTRACTS_SEED)


@dataclass(frozen=True)
class EventContract:
    event_type: str
    category: str
    name: str
    producer: str
    schema_version: int
    owner: str | None = None
    payload_schema: dict = field(default_factory=dict)
    depends_on: tuple = ()
    description: str = ""

    def validate_payload(self, payload: dict) -> list[str]:
        """Return a list of contract violations for ``payload`` (empty if it conforms). Required fields
        must be present and of the declared primitive type; extra fields are allowed (forward-compat)."""
        problems = []
        if not isinstance(payload, dict):
            return ["payload must be a dict"]
        for fieldname, ftype in (self.payload_schema or {}).items():
            if fieldname not in payload:
                problems.append(f"missing required field {fieldname!r}")
                continue
            val = payload[fieldname]
            if val is not None and not _type_ok(val, ftype):
                problems.append(f"field {fieldname!r} expected {ftype}, got {type(val).__name__}")
        return problems

    def schema_problems(self) -> list[str]:
        """Static problems with the contract's own declared schema (a malformed contract)."""
        problems = []
        for fieldname, ftype in (self.payload_schema or {}).items():
            if ftype not in _ALLOWED_TYPES:
                problems.append(f"field {fieldname!r} declares unknown type {ftype!r}")
        return problems

    def sensitive_schema_fields(self) -> list[str]:
        """Declared schema fields that are prohibited (references-only violation, D.35)."""
        from .payload_safety import sensitive_fields
        return sensitive_fields((self.payload_schema or {}).keys())


def _type_ok(val, ftype: str) -> bool:
    mapping = {"int": int, "str": str, "float": (int, float), "bool": bool, "list": list, "dict": dict}
    py = mapping.get(ftype)
    if py is None:
        return True
    if ftype == "int" and isinstance(val, bool):   # bool is a subclass of int — keep them distinct
        return False
    return isinstance(val, py)


def _build(row) -> EventContract:
    """Build a D.34 contract from its seed tuple (owner defaults to the category)."""
    event_type, category, name, producer, version, payload_schema, depends_on, desc = row
    return EventContract(event_type=event_type, category=category, name=name, producer=producer,
                         schema_version=version, owner=category, payload_schema=dict(payload_schema),
                         depends_on=tuple(depends_on), description=desc)


def _build_d35(d: dict) -> EventContract:
    """Build a D.35 contract from its seed dict (carries an explicit owner + embedded subscribers)."""
    return EventContract(event_type=d["event_type"], category=d["category"], name=d["name"],
                         producer=d["producer"], schema_version=d["schema_version"], owner=d.get("owner"),
                         payload_schema=dict(d["payload_schema"]), depends_on=tuple(d.get("depends_on") or ()),
                         description=d.get("description", ""))


EVENT_CONTRACTS: dict[str, EventContract] = {r[0]: _build(r) for r in DOMAIN_EVENT_CONTRACTS_SEED}
EVENT_CONTRACTS.update({d["event_type"]: _build_d35(d) for d in D35_CONTRACTS_SEED})

# D.34 subscriptions (tuples) + D.35 subscriptions derived from each contract's embedded subscribers.
SEEDED_SUBSCRIPTIONS = tuple(DOMAIN_EVENT_SUBSCRIPTIONS_SEED) + tuple(
    (d["event_type"], consumer, d.get("owner"), f"Read-model projection of {d['event_type']}.")
    for d in D35_CONTRACTS_SEED for consumer in d.get("subscribers", ()))
DOMAINS = tuple(EVENT_DOMAINS)


def get_contract(event_type: str) -> EventContract | None:
    return EVENT_CONTRACTS.get(event_type)
