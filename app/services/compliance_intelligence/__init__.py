"""Enterprise Compliance Intelligence & Supervisory Operations layer (Phase D.47).

A governed, READ-ONLY composition over the platform's authoritative compliance, review, exception, audit,
approval, and licensing services. It gives supervisors ONE explainable supervisory operational view — open
reviews, pending approvals, compliance exceptions, advisor workload, aging reviews, documentation gaps,
licensing/CE — WITHOUT a second compliance rules engine, approval engine, audit log, or workflow, and
without any mutation. Every supervisory item is explainable (explanation + evidence + deep link into an
authoritative workflow). Supervisor-vs-advisor separation is enforced by the ``compliance.supervise``
capability: supervisory findings are never surfaced to advisors or clients; the advisor-visible compliance
TASKS are a separate, narrower projection.
"""
from .service import (
                      advisor_compliance_tasks,
                      client_compliance,
                      compliance_summary,
                      household_compliance,
                      supervisory_dashboard,
)

__all__ = ["supervisory_dashboard", "client_compliance", "household_compliance", "compliance_summary",
           "advisor_compliance_tasks"]
