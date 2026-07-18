"""Canonical event envelope & schema versioning (E1.7 / Backlog F1.4).

A domain-agnostic envelope that wraps every event with a stable, versioned
contract for future producers and consumers. It layers on top of the F1.3
transactional outbox (the canonical transport) and does not change any outbox
guarantee: the envelope is serialized into the existing ``outbox_events.payload``
JSON column, so **no schema change** is introduced.

Envelope fields:
  schema_version  monotonic integer; enables backward-compatible evolution
  event_id        stable idempotency key (uuid)
  event_type      routing/subscription key (domain-agnostic string)
  occurred_at     ISO-8601 UTC timestamp
  correlation_id  ties related events across a flow (optional)
  causation_id    the event that caused this one (optional)
  subject_ref     what the event is about, e.g. "account:123" (optional; a
                  REFERENCE only — never PII)
  producer        who emitted it, e.g. "wealth.account_opening" (optional)
  payload         the business data (references only, never secrets/PII)
  metadata        transport/diagnostic context (optional)

Compatibility: ``from_dict`` runs ``upgrade_envelope`` so older stored envelopes
deserialize into the current shape; an envelope whose ``schema_version`` is newer
than this code supports is rejected (fail-closed).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime

# Current envelope schema version. Bump when the envelope shape changes; add an
# upgrade step in upgrade_envelope() so older events still deserialize.
SCHEMA_VERSION = 1
SUPPORTED_VERSIONS = frozenset({1})


class EnvelopeError(ValueError):
    """Raised when an event envelope is invalid or an unsupported version."""


def _new_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Envelope:
    event_type: str
    payload: dict = field(default_factory=dict)
    event_id: str = field(default_factory=_new_id)
    schema_version: int = SCHEMA_VERSION
    occurred_at: str = field(default_factory=_now_iso)
    correlation_id: str | None = None
    causation_id: str | None = None
    subject_ref: str | None = None
    producer: str | None = None
    metadata: dict = field(default_factory=dict)

    def validate(self) -> Envelope:
        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise EnvelopeError("event_type is required and must be a non-empty string")
        if self.schema_version not in SUPPORTED_VERSIONS:
            raise EnvelopeError(f"unsupported schema_version: {self.schema_version!r}")
        if not isinstance(self.payload, dict):
            raise EnvelopeError("payload must be a dict")
        if not isinstance(self.metadata, dict):
            raise EnvelopeError("metadata must be a dict")
        if not isinstance(self.event_id, str) or not self.event_id:
            raise EnvelopeError("event_id must be a non-empty string")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict) -> Envelope:
        if not isinstance(data, dict):
            raise EnvelopeError("envelope data must be a dict")
        upgraded = upgrade_envelope(dict(data))
        known = {f.name for f in fields(cls)}
        unknown = set(upgraded) - known
        # Forward-compat: unknown fields (from a newer minor shape) are preserved
        # into metadata rather than dropped, so nothing is silently lost.
        extra = {k: upgraded.pop(k) for k in list(unknown)}
        if extra:
            upgraded.setdefault("metadata", {}).setdefault("_unknown", {}).update(extra)
        return cls(**upgraded).validate()

    @classmethod
    def from_json(cls, raw: str) -> Envelope:
        return cls.from_dict(json.loads(raw))


def upgrade_envelope(data: dict) -> dict:
    """Backward-compatible version handling: normalize an envelope dict to current.

    - A dict without ``schema_version`` is treated as v1 (the first version).
    - A ``schema_version`` newer than this code supports is rejected (fail-closed).
    - Future upgrades chain here, e.g. ``if version < 2: data = _v1_to_v2(data)``.
    """
    version = data.get("schema_version", 1)
    if not isinstance(version, int):
        raise EnvelopeError(f"schema_version must be an int, got {version!r}")
    if version > SCHEMA_VERSION:
        raise EnvelopeError(
            f"envelope schema_version {version} is newer than supported {SCHEMA_VERSION}"
        )
    # (No historical upgrades yet — v1 is the first and current version.)
    data["schema_version"] = SCHEMA_VERSION
    return data


def new_event(event_type: str, payload: dict | None = None, **kwargs) -> Envelope:
    """Construct and validate an Envelope (the canonical producer entry point)."""
    return Envelope(event_type=event_type, payload=payload or {}, **kwargs).validate()


def is_envelope(value: object) -> bool:
    """True if ``value`` looks like a serialized envelope (vs a bare F1.3 payload)."""
    return (
        isinstance(value, dict)
        and "schema_version" in value
        and "event_type" in value
        and "event_id" in value
    )
