"""Rule Catalog — the governance view over the Advisor Intelligence registry
(Phase D.6).

`RuleCatalog` consumes `advisor_intelligence.list_registered_signals()` and presents
each registered rule as an immutable `RuleDefinition` carrying governance metadata
(ownership, approval state, lifecycle, versioning, documentation references). It is
strictly a **reader**:

- it never executes a rule, generates a recommendation, or enforces a policy gate;
- it never modifies Advisor Intelligence (the dependency direction is one-way,
  compliance → advisor_intelligence, never the reverse);
- it holds no persistence — every value is derived from the registry or from a small
  static supplement of **real** documentation references. Nothing is fabricated:
  owner individuals and governance dates that have not been recorded are ``None``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.advisor_intelligence import RegisteredSignal, list_registered_signals

# --------------------------------------------------------------------------- #
# Approval states & lifecycle (display-only vocabularies).
# --------------------------------------------------------------------------- #

APPROVAL_STATES = (
    "draft",
    "pending_assignment",
    "pending_review",
    "approved",
    "deprecated",
    "retired",
)

# The Advisor Intelligence registry uses its own approval vocabulary; map it into the
# governance vocabulary above (a rule with no recorded approval has no assigned owner
# yet -> pending_assignment, per the D.6 ownership rule).
_APPROVAL_FROM_REGISTRY = {
    "approved": "approved",
    "pending_compliance_review": "pending_review",
}

#: Rules that omit a version in the registry are treated as their initial release.
_INITIAL_VERSION = "1.0.0"

#: Real documentation references (files that exist in this repository). No document is
#: parsed or evaluated — these are pointers only.
_ARCHITECTURE_DOC = {
    "type": "architecture",
    "title": "Advisor Workspace Architecture (§4, §7)",
    "ref": "docs/ADVISOR_WORKSPACE_ARCHITECTURE.md",
}
_GOVERNANCE_DOCS = (
    {"type": "regulatory", "title": "V1 Risk Register — GOV-2 (compliance-owner gate)",
     "ref": "docs/V1_RISK_REGISTER.md"},
    {"type": "sop", "title": "Product Decisions — PD-4 (regulated-intelligence policy)",
     "ref": "docs/PRODUCT_DECISIONS.md"},
)


# --------------------------------------------------------------------------- #
# Semantic version helpers — comparison only (no migrations).
# --------------------------------------------------------------------------- #


def is_valid_semver(version: str) -> bool:
    """True for a ``MAJOR.MINOR.PATCH`` version of non-negative integers."""
    parts = (version or "").split(".")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` into a comparable tuple. Raises on a bad version."""
    if not is_valid_semver(version):
        raise ValueError(f"not a semantic version: {version!r}")
    major, minor, patch = version.split(".")
    return (int(major), int(minor), int(patch))


def compare_versions(a: str, b: str) -> int:
    """Return -1/0/1 for ``a`` <, ==, > ``b`` (semantic comparison, not string)."""
    ta, tb = parse_version(a), parse_version(b)
    return (ta > tb) - (ta < tb)


# --------------------------------------------------------------------------- #
# The immutable governed-rule model.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RuleDefinition:
    """Immutable governance metadata for one registered Advisor Intelligence rule.

    Metadata only — no behavior, no persistence. ``owner_name`` and the governance
    dates are ``None`` until a real assignment/decision is recorded (never
    fabricated). ``version`` defaults to the initial release when the rule omits one.
    """

    rule_id: str
    title: str
    description: str
    category: str
    governing_rule: str | None
    version: str
    policy_gate: str
    owner_role: str | None
    owner_name: str | None
    approval_status: str
    approved_date: str | None
    effective_date: str | None
    expiration_date: str | None
    source_documents: tuple[dict, ...] = ()
    implementation_status: str = "implemented"
    superseded_by: str | None = None
    deprecated_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "governing_rule": self.governing_rule,
            "version": self.version,
            "policy_gate": self.policy_gate,
            "owner_role": self.owner_role,
            "owner_name": self.owner_name,
            "approval_status": self.approval_status,
            "approved_date": self.approved_date,
            "effective_date": self.effective_date,
            "expiration_date": self.expiration_date,
            "source_documents": [dict(d) for d in self.source_documents],
            "implementation_status": self.implementation_status,
            "superseded_by": self.superseded_by,
            "deprecated_reason": self.deprecated_reason,
        }


# --------------------------------------------------------------------------- #
# Building RuleDefinitions from the Advisor Intelligence registry.
# --------------------------------------------------------------------------- #


def _title_from_id(rule_id: str) -> str:
    """A human-readable title derived from the rule id (presentation only)."""
    return rule_id.replace("_", " ").title()


def _owner_role(compliance_owner: str | None) -> str | None:
    """The owning role from the registry's compliance-owner string. The registry may
    annotate an unassigned owner as ``"compliance_reviewer (unassigned — …)"``; keep
    only the role, and never invent an individual."""
    if not compliance_owner:
        return None
    return compliance_owner.split(" (", 1)[0] or None


def _approval_status(registry_status: str | None) -> str:
    """Map the registry approval vocabulary into the governance vocabulary. A rule
    with no recorded approval has no assigned owner yet -> pending_assignment."""
    if not registry_status:
        return "pending_assignment"
    return _APPROVAL_FROM_REGISTRY.get(registry_status, registry_status)


def _source_documents(category: str) -> tuple[dict, ...]:
    """Real documentation references for a rule. Governed recommendation rules also
    reference the governance-decision documents. References only — never parsed."""
    docs = [_ARCHITECTURE_DOC]
    if category == "recommendation":
        docs.extend(_GOVERNANCE_DOCS)
    return tuple(docs)


def rule_from_registered(rule: RegisteredSignal) -> RuleDefinition:
    """Project one registry entry into its governance metadata (read-only)."""
    return RuleDefinition(
        rule_id=rule.key,
        title=_title_from_id(rule.key),
        description=rule.description,
        category=rule.category,
        governing_rule=rule.governing_rule,
        version=rule.rule_version or _INITIAL_VERSION,
        policy_gate=rule.policy_gate.value if hasattr(rule.policy_gate, "value") else str(rule.policy_gate),
        owner_role=_owner_role(rule.compliance_owner),
        owner_name=None,  # no individual is assigned; never fabricated
        approval_status=_approval_status(rule.approval_status),
        approved_date=None,
        effective_date=None,
        expiration_date=None,
        source_documents=_source_documents(rule.category),
        implementation_status="implemented",  # the rule has a registered producer
        superseded_by=None,
        deprecated_reason=None,
    )


# --------------------------------------------------------------------------- #
# The catalog service.
# --------------------------------------------------------------------------- #

# Sortable columns exposed by the UI -> the RuleDefinition attribute to sort on.
_SORT_KEYS = {
    "rule_id": "rule_id",
    "title": "title",
    "category": "category",
    "version": "version",
    "policy_gate": "policy_gate",
    "owner": "owner_role",
    "approval_status": "approval_status",
}


@dataclass(frozen=True)
class RuleCatalog:
    """A read-only governance view over the Advisor Intelligence registry."""

    rules: tuple[RuleDefinition, ...] = field(default_factory=tuple)

    @classmethod
    def from_registry(cls) -> RuleCatalog:
        """Build the catalog from the current Advisor Intelligence registry."""
        rules = tuple(rule_from_registered(r) for r in list_registered_signals())
        return cls(rules=rules)

    def list_rules(self) -> tuple[RuleDefinition, ...]:
        return self.rules

    def get_rule(self, rule_id: str) -> RuleDefinition | None:
        return next((r for r in self.rules if r.rule_id == rule_id), None)

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({r.category for r in self.rules}))

    def policy_gates(self) -> tuple[str, ...]:
        return tuple(sorted({r.policy_gate for r in self.rules}))

    def approval_statuses(self) -> tuple[str, ...]:
        return tuple(sorted({r.approval_status for r in self.rules}))

    def validate_uniqueness(self) -> None:
        """Raise if two rules share a rule_id (registry keys are unique, so this is a
        defensive invariant check — it never mutates anything)."""
        seen: set[str] = set()
        for r in self.rules:
            if r.rule_id in seen:
                raise ValueError(f"duplicate rule_id in catalog: {r.rule_id!r}")
            seen.add(r.rule_id)

    def verify_versions(self) -> None:
        """Raise if any rule carries a non-semantic version."""
        for r in self.rules:
            if not is_valid_semver(r.version):
                raise ValueError(f"rule {r.rule_id!r} has an invalid version: {r.version!r}")

    def query(self, *, search: str | None = None, category: str | None = None,
              policy_gate: str | None = None, approval_status: str | None = None,
              sort: str = "rule_id", descending: bool = False) -> list[RuleDefinition]:
        """Filtered + sorted rules (all in Python — the template only renders).

        ``search`` matches rule_id/title/description/governing_rule case-insensitively.
        Filters are exact matches. ``sort`` is one of ``_SORT_KEYS`` (falls back to
        rule_id). ``version`` sorts semantically; every other key sorts as a string.
        """
        rows = list(self.rules)
        if search:
            needle = search.strip().lower()
            rows = [r for r in rows if needle in " ".join(filter(None, (
                r.rule_id, r.title, r.description, r.governing_rule))).lower()]
        if category:
            rows = [r for r in rows if r.category == category]
        if policy_gate:
            rows = [r for r in rows if r.policy_gate == policy_gate]
        if approval_status:
            rows = [r for r in rows if r.approval_status == approval_status]

        attr = _SORT_KEYS.get(sort, "rule_id")
        if attr == "version":
            def key(r):
                return (parse_version(r.version), r.rule_id)
        else:
            def key(r):
                return ((getattr(r, attr) or "").lower(), r.rule_id)
        rows.sort(key=key, reverse=descending)
        return rows
