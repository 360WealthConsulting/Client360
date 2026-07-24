"""Recommendation adapters (Phase D.46) — read-only, fail-closed normalizers that turn the authoritative
signals/observations into the unified Recommendation contract. They never mutate, never re-derive domain
logic, and never invent recommendations; each is independently testable.
"""
from .composed import communication_followup
from .observations import observation_recommendations, workload_distribution
from .signals import recommendation_from_signal, signals_to_recommendations

__all__ = ["signals_to_recommendations", "recommendation_from_signal",
           "observation_recommendations", "workload_distribution", "communication_followup"]
