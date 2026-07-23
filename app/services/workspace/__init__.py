"""Advisor Workspace (Phase D.38) — the personalized advisor home.

A composition + personalization layer on top of the existing record-scoped advisor dashboard
(``advisor_workspace``), the analytics metric sources, and the D.36/D.37 projection-backed reads.
It adds a personalizable WIDGET GRID (order / hide / pin / saved presets / remembered filters) and
clean AI-ready SUMMARY MODELS. It performs NO business mutation, reconstructs NO business logic,
never bypasses RBAC/record-scope, and consumes projections through the D.37 adoption helper (with
graceful authoritative fallback). The authoritative services remain the sole mutation layer and the
transactional outbox remains the sole event bus.
"""
from .service import get_workspace

__all__ = ["get_workspace"]
