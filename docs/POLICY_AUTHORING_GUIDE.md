# Policy Authoring Guide (Phase D.32)

How to add or change a centralized business-decision policy. A policy has two halves that governance
keeps in sync: an **in-code definition** (the decision function) and a **registry row** (the
discoverable metadata).

## 1. Decide whether it belongs in the policy engine

A policy centralizes a **business decision** — eligibility, routing, gating, visibility. It does **not**
belong here (leave it where it is) if it is:

- infrastructure configuration, authentication, cryptography, database connectivity, startup lifecycle;
- **authorization enforcement** — `require_capability` on routes and `record_in_scope` in services stay
  where they are (RBAC is the sole access authority; a policy never bypasses it);
- runtime evaluation itself (that is the D.28 engine — the sole evaluator);
- a decision that must stay in its owning domain by regulatory/frozen/deterministic constraint — those
  are registered `in_domain` for governance only (e.g. compliance approval, the frozen notification
  module).

## 2. Add the in-code definition (`app/services/policy/definitions.py`)

Add a `PolicyDefinition` to `_DEFS`. Prefer a data-driven decision function built from the factories:

```python
PolicyDefinition(
    "myarea.my_decision", "myarea",
    _feature_gate("myarea.my_feature", baked_default=True, shim=False),
    consumes_feature="myarea.my_feature", required_capabilities=("myarea.view",),
    requires_definition=True, owner="myarea",
    description="Whether … (one line).")
```

- **`_feature_gate(template, baked_default, shim)`** — a runtime feature flag. Use `{subject}` in the
  template for a per-instance key space (set `per_instance=True`). Set `shim=True` only for a
  compatibility shim (unbounded/retired) so a served legacy default is counted as a compatibility
  fallback.
- **`_config_scope(key, baked_default, shim)`** — a runtime configuration value.
- **`_whitelist_gate(allowed, feature_template)`** — a bounded whitelist, optionally overridable per
  subject by a runtime feature (default enabled) so it stays data-driven without changing behavior.
- **`_in_domain_decide(reason)`** — a registered in-domain policy (returns `None`; never evaluated).

**Behavior-preserving rule:** the `baked_default` (and `shim`) MUST equal what the call site used
before migration. With no runtime definition present, the decision must be identical to the legacy
behavior.

If the decision composes another, set `depends_on=("other.policy",)`. Boolean decisions AND their
dependencies; value decisions treat them as informational.

## 3. Add the registry row (a new Alembic migration)

Seed a matching row in `runtime_policies` (mirror the migration `z9b0c1d2e3f4` pattern): same `code`,
`category`, `status` (`active` or `in_domain`), `owner`, `consumes_feature`/`consumes_config`,
`required_capabilities`, `depends_on`, `per_instance`, `requires_definition`, `in_domain`,
`default_decision`. Keep a **single Alembic head**. If no persistence changes are needed (only a code
definition and it maps to an existing area) governance will flag the mismatch — always add the row.

## 4. Rewire the call site

Replace the embedded decision with the engine, **keeping the capability check**:

```python
from app.services.policy import evaluate as policy_evaluate
if principal.can("myarea.view") and policy_evaluate("myarea.my_decision", context=ctx, subject=x).decision:
    ...
```

Pass the request/loop `RuntimeContext` as `context=` so the decision reuses the snapshot (no duplicate
evaluation). Read `.decision`; surface `.explanation` for diagnostics if useful. Never re-implement the
decision at the call site.

## 5. Validate

Run `app/services/policy/governance.py::validate()` — it must report `ok: True` (no duplicate /
unreachable / orphan / circular / missing-definition / deprecated / invalid-capability findings). Add
tests: the decision default (behavior-preserving), a runtime override, the explanation, and the
registry/governance state. Bump the route-count guard only if you added routes.

## Deprecating a policy

`registry.deprecate(code, reason=…)` then, once no call site references it, `registry.retire(code)`.
Governance flags any active policy that still depends on a deprecated/retired policy.
