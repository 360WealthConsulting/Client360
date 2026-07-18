"""Tamper-evident audit hash-chain (F3.2 / Epic 3, ADR-015 Option A).

Cryptographic integrity for the append-only ``audit_events`` log (F3.1). Each new
record links to the previous one in its chain via a SHA-256 hash, so any change to
a committed record's content — or any reordering/removal detectable via the
chain — is evident on verification.

Scope: canonical serialization (defined **once** here), deterministic hashing, and
an internal ``IntegrityVerifier`` service. No Evidence store, auditor export,
external anchoring, SIEM, ledger, or async services (deferred/out of scope).

Serialization contract (v1): the hashed content is a JSON object of the audit
record's application fields with **sorted keys** and compact separators; the entry
hash binds the version, chain id, previous hash, and that content:

    content_json = json.dumps(
        {actor_user_id, action, entity_type, entity_id, ip_address,
         metadata, outcome, request_id, user_agent},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    entry_hash = sha256("v{hash_version}|{chain_id}|{prev_hash}|" + content_json).hexdigest()

``occurred_at`` and ``id`` are DB-generated and are intentionally **excluded** from
the hash (so the existing write behavior is unchanged and the hash is computable
before insert). The first record in a chain uses GENESIS_PREV_HASH.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from sqlalchemy import select

HASH_VERSION = 1
DEFAULT_CHAIN = "default"
GENESIS_PREV_HASH = "0" * 64

# The exact application fields bound into the hash (order is irrelevant — sorted).
_CONTENT_FIELDS = (
    "actor_user_id", "action", "entity_type", "entity_id",
    "ip_address", "metadata", "outcome", "request_id", "user_agent",
)


def content_from_fields(
    *, actor_user_id, action, entity_type, entity_id, outcome,
    request_id, ip_address, user_agent, metadata,
) -> dict:
    """The canonical content dict (identical at write time and verify time)."""
    return {
        "actor_user_id": actor_user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "outcome": outcome,
        "request_id": request_id,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "metadata": metadata if isinstance(metadata, dict) else (metadata or {}),
    }


def canonical_serialization(content: dict) -> str:
    """Deterministic JSON serialization of the content (the single implementation)."""
    payload = {key: content.get(key) for key in _CONTENT_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def compute_entry_hash(content: dict, *, prev_hash: str, chain_id: str, hash_version: int = HASH_VERSION) -> str:
    """Deterministic entry hash binding version, chain, previous hash, and content."""
    material = f"v{hash_version}|{chain_id}|{prev_hash}|{canonical_serialization(content)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def chain_lock_key(chain_id: str) -> int:
    """A stable signed 64-bit key for a Postgres transaction advisory lock, so
    writers on the same chain serialize (preventing chain forks)."""
    return int.from_bytes(hashlib.sha256(chain_id.encode("utf-8")).digest()[:8], "big", signed=True)


@dataclass(frozen=True)
class VerifyResult:
    """Result of verifying a chain (deterministic)."""

    ok: bool
    chain_id: str
    checked: int
    first_failure_id: int | None = None
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class IntegrityVerifier:
    """Validates an audit hash chain. Reads committed rows; makes no changes."""

    def __init__(self, engine=None) -> None:
        self._engine = engine

    def _engine_or_default(self):
        if self._engine is not None:
            return self._engine
        from app.db import engine  # lazy

        return engine

    def verify_chain(self, chain_id: str = DEFAULT_CHAIN, *, from_id: int | None = None, conn=None) -> VerifyResult:
        """Verify a chain from genesis (default) or from an arbitrary checkpoint
        (``from_id`` — its stored hash is trusted as the anchor). Returns the first
        integrity failure, if any."""
        from app.db import audit_events

        query = (
            select(audit_events)
            .where(audit_events.c.chain_id == chain_id, audit_events.c.entry_hash.isnot(None))
            .order_by(audit_events.c.id)
        )
        if from_id is not None:
            query = query.where(audit_events.c.id >= from_id)

        def _run(connection) -> VerifyResult:
            rows = connection.execute(query).mappings().all()
            checked = 0
            expected_prev = None  # None until the first row establishes the anchor
            for index, row in enumerate(rows):
                content = content_from_fields(
                    actor_user_id=row["actor_user_id"], action=row["action"],
                    entity_type=row["entity_type"], entity_id=row["entity_id"],
                    outcome=row["outcome"], request_id=row["request_id"],
                    ip_address=row["ip_address"], user_agent=row["user_agent"],
                    metadata=row["metadata"],
                )
                recomputed = compute_entry_hash(
                    content, prev_hash=row["prev_hash"], chain_id=chain_id,
                    hash_version=row["hash_version"] or HASH_VERSION,
                )
                if recomputed != row["entry_hash"]:
                    return VerifyResult(False, chain_id, checked, row["id"], "entry hash mismatch (content tampered)")
                if index == 0:
                    # Anchor: genesis (full-chain) requires GENESIS_PREV_HASH; a
                    # checkpoint trusts the stored prev_hash.
                    if from_id is None and row["prev_hash"] != GENESIS_PREV_HASH:
                        return VerifyResult(False, chain_id, checked, row["id"], "genesis prev_hash invalid")
                elif row["prev_hash"] != expected_prev:
                    return VerifyResult(False, chain_id, checked, row["id"], "broken chain linkage")
                expected_prev = row["entry_hash"]
                checked += 1
            return VerifyResult(True, chain_id, checked, None, "chain intact")

        if conn is not None:
            return _run(conn)
        with self._engine_or_default().connect() as connection:
            return _run(connection)


def verify_chain(chain_id: str = DEFAULT_CHAIN, *, from_id: int | None = None, conn=None, engine=None) -> VerifyResult:
    """Module-level convenience wrapper over IntegrityVerifier."""
    return IntegrityVerifier(engine).verify_chain(chain_id, from_id=from_id, conn=conn)
