# Client360 — Workflow Execution Foundation (F4.1 / Epic 4)

The **platform adapter layer** that reconciles the existing, validated workflow
execution engine with the Epic 1 platform, per **ADR-016** (Option B, bounded
hybrid). F4.1 delivers only the first slice: **registry binding** — associating a
running workflow instance with a platform **F1.5** registry template identity.

`app/platform/workflow_adapter.py`

## Reconciliation (ADR-013 / ADR-016)
- **The engine remains canonical and unchanged.** `app/services/workflow_automation.py`
  (`launch_workflow`, `transition_workflow`, `complete_step`, approvals, SLA) is not
  modified. This adapter **wraps**; it does not replace.
- **Additive only.** Two nullable columns on `workflow_instances`
  (`platform_template_ref`, `platform_template_version`) plus one lookup index and
  one immutability trigger (migration `f41b2n3d4c5e`). No existing behavior changes.
- **Reflection preserved.** The new columns are added by migration and reflected at
  runtime (`app/db.py`); they are intentionally **not** declared in
  `work_tables.py`/`schema.py` (ADR-016; avoids the declared-metadata trap — see
  `docs/DATABASE.md`).

## What F4.1 does (and does not)
- **Is:** a registry-lookup abstraction, a write-once instance↔template binding, and
  a compatibility-preserving launch wrapper.
- **Is not:** event-driven advancement (F4.3), automation (F4.4), approvals/SLA/UI
  changes, or any new execution behavior. This module introduces **no HTTP surface**
  and emits **no events** yet.

## Services (internal; no public API)
```python
from app.platform import (
    resolve_registry_template, bind_instance_template, get_binding,
    launch_from_registry, set_registry_resolver, RegistryBinding,
)

# Registry lookup abstraction (the single seam onto F1.5):
resolve_registry_template("TAXOPS-SOP-01")                 # latest; require_published optional

# Launch via the engine (unchanged) and associate with a registry template:
instance_id = launch_from_registry("TAXOPS-SOP-01", "client_onboarding",
                                   actor_user_id=uid, person_id=pid)

# Or bind an already-launched instance (write-once):
bind_instance_template(instance_id, "TAXOPS-SOP-01")
get_binding(instance_id)   # -> RegistryBinding | None
```

## Registry binding contract
- **Association, not execution.** The DB `workflow_templates` snapshot still drives
  execution; `platform_template_ref@version` records which **F1.5 registry** template
  the instance corresponds to (ADR-016 §12).
- **Write-once (immutable).** Once set, a binding cannot be changed or cleared —
  enforced both in the adapter (clean `ValueError`) and at the database level by the
  `workflow_instance_binding_immutable` trigger, which rejects **only** binding-column
  changes and leaves all other instance updates (status, timestamps, …) unaffected.
- **Idempotent.** Re-binding to the same template is a no-op; re-binding to a
  different template raises.
- **Backward compatible.** Existing `launch_workflow` callers are untouched and
  produce **unbound** instances (`platform_template_ref` NULL); legacy instances
  remain valid and unbound.
- **Published gating deferred.** The 18 seeded SOP templates are `draft` by design;
  `require_published` defaults to `False` for binding. Execution-time "published only"
  gating is a later feature.

## Extension points (for later Epic 4 features)
- `set_registry_resolver(...)` swaps the registry source (tests / future
  runtime-persistent registry).
- The adapter is where **F4.3** (F1.4 event emission over the F1.3 outbox) and
  **F4.4** (automation consumers) will attach — without changing the engine.

## Compatibility (per the ADR-016 Compatibility Contract)
- Existing public routes, service signatures, execution semantics, and DB/automation
  guarantees are **unchanged**.
- Additive, reversible migration; single Alembic head `f41b2n3d4c5e`; no role widened;
  no event emission; no new HTTP routes.

## References
ADR-013, ADR-015, ADR-016; `docs/WORKFLOW_TEMPLATES.md` (F1.5),
`docs/OUTBOX.md` (F1.3), `docs/EVENTS.md` (F1.4), `docs/DATABASE.md` (migration
standard); `app/services/workflow_automation.py` (the canonical engine).
