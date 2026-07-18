# Client360 — Workflow Template Registry (E1.8 / Backlog F1.5)

A stable, versioned, **domain-agnostic** catalog of workflow templates that a
future workflow execution engine will consume. It **versions and discovers** the
frozen SOP catalog — it does not execute workflows and does not change SOP
business behavior.

`app/platform/workflow_registry.py`

## Template
A `WorkflowTemplate` carries:

| Field | Meaning |
|---|---|
| `template_id` | Stable identifier (the SOP `page_id`, e.g. `TAXOPS-SOP-01`) |
| `name` | Human-readable name (the SOP title) |
| `version` | Integer ≥ 1; new versions are additive |
| `status` | `draft` → `published` → `retired` |
| `category` | Domain-agnostic grouping (e.g. `tax-operations`, `wealth-operations`) |
| `description` | Short description |
| `metadata` | Free-form (source page_id, area, repository_path, sop_status) |
| `required_event_types` | Event types this template needs (empty until producers exist) |
| `supported_schema_versions` | Envelope schema versions it supports (default `[SCHEMA_VERSION]`) |

Serialization: `to_dict`/`from_dict`, `to_json`/`from_json` round-trip losslessly;
`from_dict` ignores unknown fields.

## Registry API
```python
from app.platform import default_registry, WorkflowTemplate, WorkflowTemplateRegistry

reg = default_registry()          # seeded with the 18 frozen SOP templates
reg.get("TAXOPS-SOP-01")          # latest version
reg.get("TAXOPS-SOP-01", 1)       # specific version
reg.versions("TAXOPS-SOP-01")     # [1]
reg.list_templates()              # latest of every template
reg.snapshot()                    # serialized catalog (discovery)

reg.register(template)            # add / update (see immutability)
reg.publish("TAXOPS-SOP-01", 1)   # mark a version published (then immutable)
reg.validate_compatibility(t)     # raises if t doesn't support the envelope SCHEMA_VERSION
```

## Version lifecycle & immutability
- **Draft** versions are mutable — re-registering the same `(id, version)` replaces it.
- **Published** versions are **immutable** — re-registering a published `(id, version)`
  with different content raises `ImmutableTemplateError`.
- **Evolution is additive** — publish a new, higher `version` rather than editing a
  published one. `latest()` returns the highest version; `latest_published()` returns
  the highest published version.

## The 18-SOP mapping
`build_default_registry()` seeds the registry with the 18 git-canonical
operations-manual SOPs (`TAXOPS-SOP-01…08`, `WLTH-SOP-01…10`) recorded in
`docs/registers/pages.yml`. Each is registered at **version 1, status `draft`**
(the SOPs are `needs_review`, i.e. frozen but not compliance-published). The seed
is embedded in code for runtime purity (PyYAML is not a runtime dependency); a
test (`test_seed_matches_register`) cross-checks it against `pages.yml` so drift is
caught in CI. Publishing a template is a future step gated by SME/compliance
validation (see the RTM) — not performed here.

## Compatibility guarantees
- **Composes with F1.4:** a template declares `supported_schema_versions`, validated
  against the event envelope `SCHEMA_VERSION`; `required_event_types` links templates
  to the events they consume (empty until producers exist).
- **Composes with F1.3:** the registry is transport-agnostic; it does not touch the
  outbox, only describes what future execution will run.
- **Domain-agnostic & stable:** no domain logic is embedded, so the registry supports
  future tax, wealth, insurance, retirement, Microsoft, and operations workflows.
- **Published versions are immutable; new versions are additive.**

## Reconciliation (ADR-013)
This PLATFORM registry is **distinct from** the existing DOMAIN table
`workflow_templates` (practice-management work management). They do not overlap:
this catalogs SOP templates at the platform level (no DB schema change); that
stores work-management workflow definitions. No existing behavior is changed.

## Scope boundary
F1.5 delivers the registry + 18-SOP mapping only. **Workflow execution, business
logic, automation rules, integrations, and domain producers are out of scope**
(later epics).

## Known gaps / future (non-blocking)
- The registry is in-memory/code-seeded; runtime persistence (or generating the
  seed from `pages.yml` at build time) can be added when templates become
  runtime-editable.
- `required_event_types` are empty until concrete event producers/consumers exist.
