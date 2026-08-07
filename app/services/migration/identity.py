"""Identity (Canonical Matching) Service.

Resolving which canonical Client360 entity an artifact belongs to is its OWN service. The ingestion
engine ASKS the Identity Service — matching is never embedded in ingestion. Read-only: it reads the
canonical people/households directory and resolves a group hint (or an adapter-pre-resolved record) to a
matched / ambiguous / unmatched result. Exact matching only; ambiguity is never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.importers.taxdome_drive import _folder_person_keys, _name_key
from app.services.migration.artifact import CanonicalEntity, VersionedEnterpriseArtifact
from app.services.migration.config import MigrationConfig


@dataclass
class CanonicalMatch:
    status: str                     # "matched" | "ambiguous" | "unmatched"
    reason: str
    entity: CanonicalEntity | None = None
    candidates: list[str] = field(default_factory=list)


def _person_display(p: dict) -> str:
    last, first = (p.get("last_name") or "").strip(), (p.get("first_name") or "").strip()
    return f"{last}, {first}".strip(", ") or (p.get("full_name") or f"Person {p['id']}")


class IdentityService:
    """Loads the canonical directory once (read-only) and resolves group hints to canonical entities."""

    def __init__(self, config: MigrationConfig):
        self.config = config
        self._idx: dict[str, list[dict]] = {}
        self._hh: dict[int, str] = {}
        self.note: str | None = None
        self._loaded = False

    def load(self) -> None:
        try:
            from sqlalchemy import select

            from app.db import engine, households, people
            with engine.connect() as conn:
                for r in conn.execute(select(people.c.id, people.c.first_name, people.c.last_name,
                                             people.c.full_name, people.c.household_id)).mappings():
                    key = _name_key(r["full_name"])
                    if key:
                        self._idx.setdefault(key, []).append(dict(r))
                self._hh = {r["id"]: r["name"]
                            for r in conn.execute(select(households.c.id, households.c.name)).mappings()}
            self._loaded = True
        except Exception as exc:  # noqa: BLE001 — resolution must not fail the read-only pipeline
            self.note = f"canonical directory unavailable ({exc}); groups reported unmatched"

    def resolve(self, group_key: str, record: VersionedEnterpriseArtifact) -> CanonicalMatch:
        # 1) adapter already knows the entity (record-based sources like Wealthbox contacts)
        if record.entity_type and record.canonical_id:
            return CanonicalMatch("matched", "adapter-resolved",
                                  CanonicalEntity(record.entity_type, record.canonical_id,
                                                  record.display_name or str(record.canonical_id)))
        # 2) folder/name-based resolution against the canonical directory
        if not self._loaded:
            return CanonicalMatch("unmatched", self.note or "no canonical directory")
        keys = _folder_person_keys(group_key)
        if not keys:
            return CanonicalMatch("unmatched", "no parseable name")
        per_key = {k: self._idx.get(k, []) for k in keys}
        if any(len(v) > 1 for v in per_key.values()):
            cand = sorted({c["full_name"] for v in per_key.values() for c in v})
            return CanonicalMatch("ambiguous", "a name matches multiple people", candidates=cand[:8])
        matched = [v[0] for v in per_key.values() if len(v) == 1]
        if not matched:
            return CanonicalMatch("unmatched", "no matching canonical person")
        unique = {m["id"] for m in matched}
        households = {m["household_id"] for m in matched if m["household_id"] is not None}
        names = [m["full_name"] for m in matched]
        if len(keys) == 1 and len(unique) == 1:
            p = matched[0]
            return CanonicalMatch("matched", "unique person",
                                  CanonicalEntity("person", p["id"], _person_display(p)), [p["full_name"]])
        if len(households) == 1:
            hid = next(iter(households))
            return CanonicalMatch("matched", "joint -> shared household",
                                  CanonicalEntity("household", hid, self._hh.get(hid) or f"Household {hid}"), names)
        if len(unique) == 1:
            p = matched[0]
            return CanonicalMatch("matched", "single distinct person",
                                  CanonicalEntity("person", p["id"], _person_display(p)), [p["full_name"]])
        return CanonicalMatch("ambiguous", "matched people without one common household", candidates=names)
