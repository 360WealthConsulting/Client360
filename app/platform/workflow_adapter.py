"""Workflow execution adapter (F4.1 / Epic 4, ADR-016).

The thin **platform adapter layer** that reconciles the existing, validated
workflow execution engine (``app/services/workflow_automation.py``) with the
Epic 1 platform — **without modifying the engine** (ADR-016 Option B, bounded
hybrid). F4.1 delivers only the *registry binding*: associating a running
workflow instance with a platform F1.5 registry template identity
(``template_id`` @ ``version``).

Design (ADR-016):
- **Engine remains canonical.** ``launch_workflow`` / ``transition_workflow`` /
  ``complete_step`` are unchanged; this module *wraps*, it does not replace.
- **Registry lookup abstraction.** ``resolve_registry_template`` is the single
  seam onto the F1.5 registry; swap the resolver via ``set_registry_resolver``
  (extension point) for tests or future runtime-persistent registries.
- **Immutable binding.** A binding is write-once — enforced both here (clean
  error) and at the DB level (trigger ``workflow_instance_binding_immutable``).
- **Additive & compatibility-preserving.** ``launch_from_registry`` is a new
  platform entry point; existing callers of ``launch_workflow`` are untouched and
  produce unbound instances (binding columns NULL).

**Out of scope for F4.1** (later features, per ADR-016): event-driven advancement
(F4.3), automation (F4.4), approvals/SLA changes, UI, and any new execution
behavior. This module introduces **no** HTTP surface and **no** event emission.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from app.db import engine, workflow_instances
from app.platform.workflow_registry import (
    UnknownTemplateError,
    WorkflowTemplate,
    WorkflowTemplateRegistry,
    default_registry,
)

# --- registry lookup abstraction (extension point) ---------------------------

_registry_resolver: Callable[[], WorkflowTemplateRegistry] = default_registry


def set_registry_resolver(resolver: Callable[[], WorkflowTemplateRegistry]) -> None:
    """Override the registry source (extension point; e.g. tests or a future
    runtime-persistent registry). Pass ``default_registry`` to restore."""
    global _registry_resolver
    _registry_resolver = resolver


def resolve_registry_template(
    template_id: str, version: int | None = None, *, require_published: bool = False
) -> WorkflowTemplate:
    """Resolve a template from the platform F1.5 registry (the single seam).

    ``version=None`` resolves the latest (or latest *published* when
    ``require_published``). Raises ``UnknownTemplateError`` if absent, or
    ``ValueError`` if a published template is required but none is available.
    Note: the seeded 18 SOP templates are ``draft`` by design; execution-time
    "published only" gating is a later feature, so binding defaults to allowing
    draft association (``require_published=False``).
    """
    registry = _registry_resolver()
    if require_published:
        template = registry.latest_published(template_id) if version is None else registry.get(template_id, version)
        if template is None or template.status != "published":
            raise ValueError(f"No published registry template for {template_id!r}")
        return template
    return registry.get(template_id, version)


# --- binding model -----------------------------------------------------------

@dataclass(frozen=True)
class RegistryBinding:
    """A workflow instance's association to a platform registry template."""

    instance_id: int
    template_id: str
    version: int
    name: str | None = None
    status: str | None = None


def _read_binding(conn, instance_id: int):
    row = conn.execute(
        select(
            workflow_instances.c.id,
            workflow_instances.c.platform_template_ref,
            workflow_instances.c.platform_template_version,
        ).where(workflow_instances.c.id == instance_id)
    ).mappings().one_or_none()
    if row is None:
        raise ValueError("Workflow not found")
    return row


def _enrich(instance_id: int, template_id: str, version: int) -> RegistryBinding:
    try:
        t = _registry_resolver().get(template_id, version)
        return RegistryBinding(instance_id, template_id, version, t.name, t.status)
    except (UnknownTemplateError, KeyError):
        return RegistryBinding(instance_id, template_id, version)


# --- execution adapter -------------------------------------------------------

def bind_instance_template(
    instance_id: int, template_id: str, version: int | None = None,
    *, require_published: bool = False, conn=None,
) -> RegistryBinding:
    """Bind an instance to a registry template (write-once, idempotent).

    Resolves the registry template (validating it exists), then records
    ``platform_template_ref`` / ``platform_template_version`` on the instance.
    Re-binding to the **same** template is a no-op; re-binding to a **different**
    template raises ``ValueError`` (immutable), consistent with the DB trigger.
    """
    template = resolve_registry_template(template_id, version, require_published=require_published)
    resolved_version = template.version

    def _do(c) -> RegistryBinding:
        current = _read_binding(c, instance_id)
        existing_ref = current["platform_template_ref"]
        if existing_ref is not None:
            if existing_ref != template_id or current["platform_template_version"] != resolved_version:
                raise ValueError(
                    "Workflow platform template binding is immutable once set "
                    f"(bound to {existing_ref}@{current['platform_template_version']})"
                )
            return _enrich(instance_id, template_id, resolved_version)  # idempotent no-op
        c.execute(
            workflow_instances.update()
            .where(workflow_instances.c.id == instance_id)
            .values(platform_template_ref=template_id, platform_template_version=resolved_version)
        )
        return RegistryBinding(instance_id, template_id, resolved_version, template.name, template.status)

    if conn is not None:
        return _do(conn)
    with engine.begin() as connection:
        return _do(connection)


def get_binding(instance_id: int, *, conn=None) -> RegistryBinding | None:
    """Return the instance's registry binding, or ``None`` if unbound."""

    def _do(c) -> RegistryBinding | None:
        row = _read_binding(c, instance_id)
        if row["platform_template_ref"] is None:
            return None
        return _enrich(instance_id, row["platform_template_ref"], row["platform_template_version"])

    if conn is not None:
        return _do(conn)
    with engine.connect() as connection:
        return _do(connection)


def launch_from_registry(
    registry_template_id: str, db_template_code: str, *,
    version: int | None = None, require_published: bool = False, **launch_kwargs,
) -> int:
    """Platform entry point: launch a workflow and bind it to a registry template.

    The existing engine ``launch_workflow`` runs **unchanged** (using the DB
    template ``db_template_code``); the resulting instance is then associated with
    the platform registry template ``registry_template_id``. Additive: existing
    callers of ``launch_workflow`` are unaffected. Binding is a separate additive
    step — if it fails, the instance exists unbound and can be re-bound.
    """
    # Validate the registry template up front (fail fast before launching).
    resolve_registry_template(registry_template_id, version, require_published=require_published)
    from app.services.workflow_automation import launch_workflow

    instance_id = launch_workflow(db_template_code, **launch_kwargs)
    bind_instance_template(instance_id, registry_template_id, version, require_published=require_published)
    return instance_id
