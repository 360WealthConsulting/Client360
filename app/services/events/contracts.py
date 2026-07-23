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
    DOMAIN_EVENT_CONTRACTS_SEED,
    DOMAIN_EVENT_SUBSCRIPTIONS_SEED,
    EVENT_DOMAINS,
)

_ALLOWED_TYPES = {"int", "str", "float", "bool", "list", "dict"}


@dataclass(frozen=True)
class EventContract:
    event_type: str
    category: str
    name: str
    producer: str
    schema_version: int
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


def _type_ok(val, ftype: str) -> bool:
    mapping = {"int": int, "str": str, "float": (int, float), "bool": bool, "list": list, "dict": dict}
    py = mapping.get(ftype)
    if py is None:
        return True
    if ftype == "int" and isinstance(val, bool):   # bool is a subclass of int — keep them distinct
        return False
    return isinstance(val, py)


def _build(row) -> EventContract:
    event_type, category, name, producer, version, payload_schema, depends_on, desc = row
    return EventContract(event_type=event_type, category=category, name=name, producer=producer,
                         schema_version=version, payload_schema=dict(payload_schema),
                         depends_on=tuple(depends_on), description=desc)


EVENT_CONTRACTS: dict[str, EventContract] = {r[0]: _build(r) for r in DOMAIN_EVENT_CONTRACTS_SEED}
SEEDED_SUBSCRIPTIONS = tuple(DOMAIN_EVENT_SUBSCRIPTIONS_SEED)
DOMAINS = tuple(EVENT_DOMAINS)


def get_contract(event_type: str) -> EventContract | None:
    return EVENT_CONTRACTS.get(event_type)
