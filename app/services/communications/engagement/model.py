"""Unified interaction read model (Phase D.44).

A normalized, read-only projection of a single client interaction. It REFERENCES its authoritative source
(source_system + source ids + deep_link) and NEVER copies source content — ``preview`` is a short, derived,
non-sensitive snippet only (never a full message body / email body / note body). This is the single shape
every engagement adapter returns and every engagement surface consumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Visibility classifications (mirrors the registry vocabulary).
INTERNAL = "internal"      # advisor/staff only — never surfaced to an external portal principal
EXTERNAL = "external"      # client-facing
BOTH = "both"              # visible internally and (client-appropriate view) externally

# Participant orientation.
INBOUND = "inbound"        # client → firm
OUTBOUND = "outbound"      # firm → client
INTERNAL_NOTE = "internal_note"
SYSTEM = "system"


def _preview(text, limit=140):
    """Short, non-sensitive snippet. Callers pass an already-safe title/summary — this only truncates."""
    s = " ".join(str(text or "").split())
    return (s[: limit - 1] + "…") if len(s) > limit else s


@dataclass(frozen=True)
class Interaction:
    interaction_id: str            # stable, source-qualified (e.g. "timeline:secure_message:1234")
    source_system: str             # authoritative owning service (from the registry)
    interaction_type: str          # registered interaction type key
    timestamp: datetime | None
    subject: str
    preview: str                   # derived snippet only — never full source content
    visibility: str                # internal | external | both
    direction: str                 # inbound | outbound | internal_note | system
    related_person_id: int | None = None
    related_household_id: int | None = None
    related_business_id: int | None = None
    participants: tuple[str, ...] = ()
    attachments_available: bool = False
    unread: bool = False
    action_required: bool = False
    deep_link: str | None = None
    lifecycle: str = "active"
    source_freshness: datetime | None = None
    retention_class: str | None = None

    @property
    def sort_key(self):
        # Deterministic secondary sort so equal timestamps order stably by id.
        return self.interaction_id

    def to_dict(self) -> dict:
        return {
            "interaction_id": self.interaction_id,
            "source_system": self.source_system,
            "interaction_type": self.interaction_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "subject": self.subject,
            "preview": self.preview,
            "visibility": self.visibility,
            "direction": self.direction,
            "related_person_id": self.related_person_id,
            "related_household_id": self.related_household_id,
            "related_business_id": self.related_business_id,
            "participants": list(self.participants),
            "attachments_available": self.attachments_available,
            "unread": self.unread,
            "action_required": self.action_required,
            "deep_link": self.deep_link,
            "lifecycle": self.lifecycle,
            "source_freshness": self.source_freshness.isoformat() if self.source_freshness else None,
            "retention_class": self.retention_class,
        }


def make_preview(text, limit=140):
    return _preview(text, limit)
