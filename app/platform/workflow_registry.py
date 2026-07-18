"""Workflow Template Registry (E1.8 / Backlog F1.5).

A stable, versioned, domain-agnostic catalog of workflow templates that a future
workflow execution engine will consume. It **references** the frozen SOP catalog
(the 18 git-canonical operations-manual SOPs recorded in
``docs/registers/pages.yml``) — it versions and discovers them; it does not change
SOP business behavior, and it does not execute workflows.

Reconciliation (ADR-013):
  * This is a PLATFORM registry, distinct from the existing DOMAIN table
    ``workflow_templates`` (practice-management work management). They do not
    overlap: this catalogs SOP templates for the platform; that stores
    work-management workflow definitions.
  * It composes with F1.3 (outbox) and F1.4 (event envelope): a template declares
    ``supported_schema_versions`` (validated against the envelope
    ``SCHEMA_VERSION``) and ``required_event_types`` (empty until producers exist).

Runtime purity: the SOP seed is embedded here (no YAML/file IO at runtime, since
PyYAML is not a runtime dependency). A test cross-checks the seed against
``pages.yml`` so drift is caught in CI.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from app.platform.events import SCHEMA_VERSION

# --- status lifecycle --------------------------------------------------------

DRAFT = "draft"
PUBLISHED = "published"
RETIRED = "retired"
VALID_STATUSES = frozenset({DRAFT, PUBLISHED, RETIRED})


class WorkflowRegistryError(Exception):
    """Base error for the workflow template registry."""


class TemplateValidationError(WorkflowRegistryError, ValueError):
    """A template is structurally invalid."""


class UnknownTemplateError(WorkflowRegistryError, KeyError):
    """No such template / version in the registry."""


class ImmutableTemplateError(WorkflowRegistryError):
    """Attempt to change a published (immutable) template version."""


class IncompatibleTemplateError(WorkflowRegistryError):
    """A template is not compatible with a required envelope schema version."""


# --- template ----------------------------------------------------------------

@dataclass
class WorkflowTemplate:
    template_id: str
    name: str
    category: str
    version: int = 1
    status: str = DRAFT
    description: str = ""
    metadata: dict = field(default_factory=dict)
    required_event_types: list[str] = field(default_factory=list)
    supported_schema_versions: list[int] = field(default_factory=lambda: [SCHEMA_VERSION])

    def validate(self) -> WorkflowTemplate:
        if not isinstance(self.template_id, str) or not self.template_id.strip():
            raise TemplateValidationError("template_id is required")
        if not isinstance(self.name, str) or not self.name.strip():
            raise TemplateValidationError("name is required")
        if not isinstance(self.category, str) or not self.category.strip():
            raise TemplateValidationError("category is required")
        if not isinstance(self.version, int) or self.version < 1:
            raise TemplateValidationError("version must be an integer >= 1")
        if self.status not in VALID_STATUSES:
            raise TemplateValidationError(f"invalid status: {self.status!r}")
        if not isinstance(self.metadata, dict):
            raise TemplateValidationError("metadata must be a dict")
        if not (isinstance(self.required_event_types, list)
                and all(isinstance(x, str) for x in self.required_event_types)):
            raise TemplateValidationError("required_event_types must be a list of strings")
        if not (isinstance(self.supported_schema_versions, list)
                and self.supported_schema_versions
                and all(isinstance(x, int) for x in self.supported_schema_versions)):
            raise TemplateValidationError("supported_schema_versions must be a non-empty list of ints")
        return self

    def supports_schema_version(self, schema_version: int) -> bool:
        return schema_version in self.supported_schema_versions

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict) -> WorkflowTemplate:
        if not isinstance(data, dict):
            raise TemplateValidationError("template data must be a dict")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known}).validate()

    @classmethod
    def from_json(cls, raw: str) -> WorkflowTemplate:
        return cls.from_dict(json.loads(raw))


# --- registry ----------------------------------------------------------------

class WorkflowTemplateRegistry:
    """In-memory, versioned catalog. Published versions are immutable; new
    versions are additive."""

    def __init__(self) -> None:
        # template_id -> {version -> WorkflowTemplate}
        self._store: dict[str, dict[int, WorkflowTemplate]] = {}

    def register(self, template: WorkflowTemplate) -> WorkflowTemplate:
        template.validate()
        versions = self._store.setdefault(template.template_id, {})
        existing = versions.get(template.version)
        if existing is not None:
            if existing.status == PUBLISHED and existing.to_dict() != template.to_dict():
                raise ImmutableTemplateError(
                    f"{template.template_id} v{template.version} is published and immutable; "
                    "register a new version instead"
                )
            # Draft/retired (or identical published) — idempotent replace is allowed.
        versions[template.version] = template
        return template

    def publish(self, template_id: str, version: int) -> WorkflowTemplate:
        template = self.get(template_id, version)
        published = WorkflowTemplate(
            **{**template.to_dict(), "status": PUBLISHED}
        ).validate()
        self._store[template_id][version] = published
        return published

    def get(self, template_id: str, version: int | None = None) -> WorkflowTemplate:
        versions = self._store.get(template_id)
        if not versions:
            raise UnknownTemplateError(template_id)
        if version is None:
            return versions[max(versions)]
        if version not in versions:
            raise UnknownTemplateError(f"{template_id} v{version}")
        return versions[version]

    def latest(self, template_id: str) -> WorkflowTemplate:
        return self.get(template_id, None)

    def latest_published(self, template_id: str) -> WorkflowTemplate | None:
        versions = self._store.get(template_id) or {}
        published = [v for v in versions.values() if v.status == PUBLISHED]
        return max(published, key=lambda t: t.version) if published else None

    def versions(self, template_id: str) -> list[int]:
        return sorted((self._store.get(template_id) or {}).keys())

    def list_templates(self) -> list[WorkflowTemplate]:
        """Latest version of every template, sorted by id."""
        return [self.get(tid) for tid in sorted(self._store)]

    def all_versions(self) -> list[WorkflowTemplate]:
        return [t for tid in sorted(self._store) for t in self.get_all(tid)]

    def get_all(self, template_id: str) -> list[WorkflowTemplate]:
        versions = self._store.get(template_id) or {}
        return [versions[v] for v in sorted(versions)]

    def validate_compatibility(self, template: WorkflowTemplate, schema_version: int = SCHEMA_VERSION) -> None:
        if not template.supports_schema_version(schema_version):
            raise IncompatibleTemplateError(
                f"{template.template_id} v{template.version} does not support envelope "
                f"schema_version {schema_version} (supports {template.supported_schema_versions})"
            )

    def __contains__(self, template_id: object) -> bool:
        return template_id in self._store

    def __len__(self) -> int:
        return len(self._store)

    def snapshot(self) -> list[dict]:
        """Serialize the whole catalog (latest per id) for discovery/inspection."""
        return [t.to_dict() for t in self.list_templates()]


# --- frozen SOP catalog seed (18 git-canonical operations-manual SOPs) --------
# Mirrors docs/registers/pages.yml (runtime-pure; drift-guarded by
# tests/test_e1_8_workflow_registry.py::test_seed_matches_register).

_CATEGORY_BY_AREA = {"TAXOPS": "tax-operations", "WLTH": "wealth-operations"}

# (page_id, title, area, repository_path)
_SOP_SEED: list[tuple[str, str, str, str]] = [
    ("TAXOPS-SOP-01", "Tax Operations — TaxDome Client Intake", "TAXOPS", "docs/operations-manual/tax/taxdome-intake.md"),
    ("TAXOPS-SOP-02", "Tax Operations — 1040 Individual Return Preparation (Drake)", "TAXOPS", "docs/operations-manual/tax/tax-1040-return-workflow.md"),
    ("TAXOPS-SOP-03", "Tax Operations — Business Return Preparation", "TAXOPS", "docs/operations-manual/tax/business-return-workflow.md"),
    ("TAXOPS-SOP-04", "Tax Operations — Tax Return Review & Delivery", "TAXOPS", "docs/operations-manual/tax/tax-review-and-delivery.md"),
    ("TAXOPS-SOP-05", "Tax Operations — E-file Authorization & Acknowledgements", "TAXOPS", "docs/operations-manual/tax/efile-authorization-and-acknowledgements.md"),
    ("TAXOPS-SOP-06", "Tax Operations — IRS & State Notice Handling", "TAXOPS", "docs/operations-manual/tax/irs-notice-handling.md"),
    ("TAXOPS-SOP-07", "Tax Operations — Tax Extensions", "TAXOPS", "docs/operations-manual/tax/tax-extensions.md"),
    ("TAXOPS-SOP-08", "Tax Operations — Quarterly Estimated Payments", "TAXOPS", "docs/operations-manual/tax/quarterly-estimated-payments.md"),
    ("WLTH-SOP-01", "Wealth Management — Schwab Account Opening", "WLTH", "docs/operations-manual/wealth/schwab-account-opening.md"),
    ("WLTH-SOP-02", "Wealth Management — Schwab Portfolio Connect Quarterly Billing & Fee Locking", "WLTH", "docs/operations-manual/wealth/schwab-portfolio-connect-billing.md"),
    ("WLTH-SOP-03", "Wealth Management — AssetMark Account Opening", "WLTH", "docs/operations-manual/wealth/assetmark-account-opening.md"),
    ("WLTH-SOP-04", "Wealth Management — AssetMark Proposal Generation", "WLTH", "docs/operations-manual/wealth/assetmark-proposal-generation.md"),
    ("WLTH-SOP-05", "Wealth Management — Schwab MoneyLink Setup", "WLTH", "docs/operations-manual/wealth/schwab-moneylink-setup.md"),
    ("WLTH-SOP-06", "Wealth Management — Schwab ACAT Transfer In", "WLTH", "docs/operations-manual/wealth/schwab-acat-transfer-in.md"),
    ("WLTH-SOP-07", "Wealth Management — AssetMark Household Setup", "WLTH", "docs/operations-manual/wealth/assetmark-household-setup.md"),
    ("WLTH-SOP-08", "Wealth Management — AssetMark Model Selection", "WLTH", "docs/operations-manual/wealth/assetmark-model-selection.md"),
    ("WLTH-SOP-09", "Wealth Management — AssetMark Funding & Transfers", "WLTH", "docs/operations-manual/wealth/assetmark-funding-transfers.md"),
    ("WLTH-SOP-10", "Wealth Management — AssetMark Billing Review", "WLTH", "docs/operations-manual/wealth/assetmark-billing-review.md"),
]


def _seed_template(page_id: str, title: str, area: str, repository_path: str) -> WorkflowTemplate:
    return WorkflowTemplate(
        template_id=page_id,
        name=title,
        category=_CATEGORY_BY_AREA.get(area, area.lower()),
        version=1,
        # The SOPs are `needs_review` (frozen but not compliance-published), so their
        # templates start as DRAFT — not published/immutable.
        status=DRAFT,
        description=f"Git-canonical operations-manual SOP: {title}",
        metadata={
            "source_page_id": page_id,
            "area": area,
            "repository_path": repository_path,
            "sop_status": "needs_review",
        },
        required_event_types=[],
        supported_schema_versions=[SCHEMA_VERSION],
    ).validate()


def build_default_registry() -> WorkflowTemplateRegistry:
    """Build a registry seeded with the 18 frozen SOP templates."""
    registry = WorkflowTemplateRegistry()
    for row in _SOP_SEED:
        registry.register(_seed_template(*row))
    return registry


_default_registry: WorkflowTemplateRegistry | None = None


def default_registry() -> WorkflowTemplateRegistry:
    """Lazily-built process-wide registry seeded from the frozen SOP catalog."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry
