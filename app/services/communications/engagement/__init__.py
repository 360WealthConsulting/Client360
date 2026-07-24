"""Unified Communications & Client Engagement layer (Phase D.44).

A governed COMPOSITION over the platform's authoritative communication subsystems — it provides a single
engagement/interaction view without creating a second messaging, notification, timeline, document,
scheduling, audit, or event system. Advisor/staff surfaces compose over the authoritative activity timeline
(``activity_timeline``); the external client surface composes over the D.43 portal scoped reads. Every
interaction stays owned by its authoritative subsystem; this layer only reads, classifies (via the
declarative interaction registry), filters, searches, and summarizes.
"""
from .service import engagement_summary, engagement_timeline, portal_engagement, search_interactions

__all__ = ["engagement_timeline", "search_interactions", "engagement_summary", "portal_engagement"]
