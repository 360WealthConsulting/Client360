"""Timeline presentation contract (Phase D.10).

``TimelineEvent`` is a deterministic, testable **presentation** model — a projection of
an existing authoritative domain record. It never replaces a domain model, and it is
never persisted. ``sort_key`` (the stable event id) is the deterministic secondary sort
so ordering never depends on timestamps alone. ``redacted`` marks an event whose
confidential detail the current principal may not view (its summary reads "Additional
details are restricted."); the event's *existence* is still authorized.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TimelineEvent:
    event_id: str
    event_type: str
    occurred_at: datetime | None
    title: str
    summary: str
    person_id: int | None
    household_id: int | None
    source_domain: str
    source_record_type: str
    source_record_id: int
    actor_principal_id: int | None = None
    actor_display_name: str | None = None
    source_url: str | None = None
    severity: str | None = None
    status: str | None = None
    metadata: dict = field(default_factory=dict)
    redacted: bool = False

    @property
    def sort_key(self) -> str:
        """Stable deterministic secondary sort key (the event id)."""
        return self.event_id

    def with_actor(self, name: str | None) -> TimelineEvent:
        """Return a copy with the resolved actor display name (batch-resolved once)."""
        return TimelineEvent(**{**self.__dict__, "actor_display_name": name})

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "title": self.title,
            "summary": self.summary,
            "person_id": self.person_id,
            "household_id": self.household_id,
            "source_domain": self.source_domain,
            "source_record_type": self.source_record_type,
            "source_record_id": self.source_record_id,
            "actor_principal_id": self.actor_principal_id,
            "actor_display_name": self.actor_display_name,
            "source_url": self.source_url,
            "severity": self.severity,
            "status": self.status,
            "metadata": self.metadata,
            "redacted": self.redacted,
            "sort_key": self.sort_key,
        }
