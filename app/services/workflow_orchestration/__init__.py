"""Workflow orchestration layer (Phase D.17).

A deterministic orchestration layer OVER the existing workflow engine
(``app/services/workflow_automation.py``). It reuses ``launch_workflow`` /
``transition_workflow`` / ``complete_step`` / ``process_event`` / ``workflow_detail`` and adds
domain-event triggers, an action registry that invokes existing domain services, per-step retry
and assignment, and the ``workflow.*`` capability surface. It owns no business entities and never
duplicates business logic; the existing engine, published templates, ``work.*`` capabilities,
``/workflows`` routes, and the tax launcher are all preserved.
"""
