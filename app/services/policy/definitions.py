"""Declarative policy definitions (Phase D.32) — the centralized business-decision catalog.

Each definition binds a policy code to a deterministic decision function. The decision functions are
**data-driven wherever practical**: they consume the D.28 ``RuntimeContext`` through the standardized
consumption API (``app/services/runtime/consumption.py``) — the runtime engine remains the sole
evaluator — and fall back to a behavior-preserving legacy default so every migration is identical to
the pre-D.32 behavior. Capability/scope enforcement is NOT here: it stays at the call site (RBAC is the
sole access authority). A definition only centralizes the *business* decision and reports which runtime
features/config it consulted.

The registry rows in ``runtime_policies`` (seeded by the D.32 migration) mirror these definitions;
governance cross-checks the two so an orphaned row (no definition) or an unreachable definition (no
row) is caught.
"""
from __future__ import annotations

from collections import namedtuple
from collections.abc import Callable
from dataclasses import dataclass, field

from app.services.runtime import consumption

# A raw decision returned by a definition's decide(); the engine wraps it into a PolicyResult.
RawDecision = namedtuple("RawDecision", ["decision", "explanation", "evaluated_features"])

_UNSET = object()


@dataclass(frozen=True)
class PolicyDefinition:
    code: str
    category: str
    decide: Callable                       # (ctx, subject, default) -> RawDecision
    consumes_feature: str | None = None
    consumes_config: str | None = None
    required_capabilities: tuple = ()
    depends_on: tuple = ()
    per_instance: bool = False
    requires_definition: bool = False
    in_domain: bool = False
    owner: str | None = None
    description: str = ""
    default_decision: object = None
    metadata: dict = field(default_factory=dict)


# --- decision-function factories ---------------------------------------------

def _feature_gate(feature_template: str, *, baked_default: bool, shim: bool):
    """A feature-flag gate. ``feature_template`` may contain ``{subject}`` for a per-instance key
    space. Consumes the runtime feature; returns the behavior-preserving default when undefined."""
    def decide(ctx, subject=None, default=_UNSET) -> RawDecision:
        key = feature_template.format(subject=subject) if "{subject}" in feature_template else feature_template
        dflt = bool(baked_default) if default is _UNSET else bool(default)
        enabled = consumption.feature_enabled(key, context=ctx, default=dflt, shim=shim)
        why = (f"runtime feature {key!r} evaluated {'enabled' if enabled else 'disabled'}"
               if ctx is not None and ctx.feature_defined(key)
               else f"no runtime feature {key!r} defined — legacy default {dflt}")
        return RawDecision(enabled, why, ((key, enabled),))
    return decide


def _config_scope(config_key: str, *, baked_default, shim: bool):
    """A configuration-value decision (e.g. a scope string). Consumes the runtime config item; returns
    the caller/legacy default when unset."""
    def decide(ctx, subject=None, default=_UNSET) -> RawDecision:
        dflt = baked_default if default is _UNSET else default
        val = consumption.config_value(config_key, context=ctx, default=dflt, shim=shim)
        defined = ctx is not None and ctx.config(config_key, None) is not None
        why = (f"runtime config {config_key!r} = {val!r}" if defined
               else f"no runtime config {config_key!r} — legacy default {dflt!r}")
        return RawDecision(val, why, ((config_key, val),))
    return decide


def _whitelist_gate(allowed: tuple, feature_template: str | None):
    """A bounded-whitelist decision (subject must be in ``allowed``), optionally overridable per subject
    by a runtime feature (default enabled) so it is data-driven without changing behavior."""
    allowed_set = frozenset(allowed)

    def decide(ctx, subject=None, default=_UNSET) -> RawDecision:
        if subject not in allowed_set:
            return RawDecision(False, f"{subject!r} not in the approved set {sorted(allowed_set)}", ())
        if feature_template is not None:
            key = feature_template.format(subject=subject)
            enabled = consumption.feature_enabled(key, context=ctx, default=True)
            why = (f"{subject!r} approved; runtime feature {key!r} "
                   f"{'enabled' if enabled else 'disabled'}")
            return RawDecision(enabled, why, ((key, enabled),))
        return RawDecision(True, f"{subject!r} is in the approved set", ())
    return decide


def _in_domain_decide(reason: str):
    """A registered in-domain policy: enforcement stays in the owning domain; the generic engine never
    decides it. Returns None with an explanation (callers never route through this)."""
    def decide(ctx, subject=None, default=_UNSET) -> RawDecision:
        return RawDecision(None, f"enforced in the owning domain — {reason}", ())
    return decide


# --- the declarative catalog --------------------------------------------------

_REVIEW_TEMPLATES = ("annual_review", "insurance_review")
_OPERATIONS_TIMELINE_KINDS = ("project_created", "project_completed", "task_completed",
                              "milestone_reached")

_DEFS = (
    # --- advisor workspace visibility (authoritative runtime features; NOT shims) ------------------
    PolicyDefinition(
        "advisor_workspace.section.work", "advisor_workspace",
        _feature_gate("advisor_workspace.section.work", baked_default=True, shim=False),
        consumes_feature="advisor_workspace.section.work", required_capabilities=("work.read",),
        requires_definition=True, owner="advisor_workspace",
        description="Whether the advisor daily-dashboard work section is shown."),
    PolicyDefinition(
        "advisor_workspace.section.tasks", "advisor_workspace",
        _feature_gate("advisor_workspace.section.tasks", baked_default=True, shim=False),
        consumes_feature="advisor_workspace.section.tasks", required_capabilities=("task.read",),
        depends_on=("advisor_workspace.section.work",), requires_definition=True,
        owner="advisor_workspace",
        description="Whether the tasks sub-section is shown (composes the work section)."),
    PolicyDefinition(
        "advisor_workspace.section.exceptions", "advisor_workspace",
        _feature_gate("advisor_workspace.section.exceptions", baked_default=True, shim=False),
        consumes_feature="advisor_workspace.section.exceptions",
        required_capabilities=("exception.read",),
        depends_on=("advisor_workspace.section.work",), requires_definition=True,
        owner="advisor_workspace",
        description="Whether the exceptions sub-section is shown (composes the work section)."),
    # --- workflow routing (the review-template whitelist) ------------------------------------------
    PolicyDefinition(
        "workflow.review_routing", "workflow",
        _whitelist_gate(_REVIEW_TEMPLATES, "workflow.review_template.{subject}"),
        required_capabilities=("client.write",), owner="advisor_workspace",
        default_decision={"allowed": list(_REVIEW_TEMPLATES)},
        description="Which review workflow templates may be launched from a meeting outcome."),
    # --- automation execution (per-instance job-type key space; compatibility shim) ----------------
    PolicyDefinition(
        "automation.job_execution", "automation",
        _feature_gate("automation.job.{subject}", baked_default=True, shim=True),
        consumes_feature="automation.job", required_capabilities=("automation.execute",),
        per_instance=True, owner="automation",
        description="Whether an automation job type may execute (runtime automation.job.<type>)."),
    # --- reporting eligibility (per-instance report-id key space; compatibility shim) --------------
    PolicyDefinition(
        "reporting.module_eligibility", "reporting",
        _feature_gate("reporting.module.{subject}", baked_default=True, shim=True),
        consumes_feature="reporting.module", required_capabilities=("reporting.view",),
        per_instance=True, owner="reporting",
        description="Whether an optional report definition is included (runtime reporting.module.<id>)."),
    # --- Microsoft integration behavior (compatibility shims) --------------------------------------
    PolicyDefinition(
        "microsoft365.sync_eligibility", "microsoft365",
        _feature_gate("microsoft365.sync", baked_default=True, shim=True),
        consumes_feature="microsoft365.sync", required_capabilities=("communication.read",),
        requires_definition=True, owner="microsoft365",
        description="Whether Microsoft 365 mail/calendar/document sync runs."),
    PolicyDefinition(
        "microsoft365.sharepoint_scope", "microsoft365",
        _config_scope("microsoft365.sharepoint_site_ids", baked_default="", shim=True),
        consumes_config="microsoft365.sharepoint_site_ids",
        required_capabilities=("communication.read",),
        depends_on=("microsoft365.sync_eligibility",), requires_definition=True, owner="microsoft365",
        description="The SharePoint site-id scope for document discovery."),
    # --- operations visibility (the timeline-publish whitelist) ------------------------------------
    PolicyDefinition(
        "operations.timeline_publish", "operations",
        _whitelist_gate(_OPERATIONS_TIMELINE_KINDS, "operations.timeline_publish.{subject}"),
        required_capabilities=("operations.view",), owner="operations",
        default_decision={"allowed": list(_OPERATIONS_TIMELINE_KINDS)},
        description="Whether an operational lifecycle event kind publishes to the timeline."),
    # --- in-domain (registered + governed; enforcement stays in the owning domain) -----------------
    PolicyDefinition(
        "compliance.decision_routing", "compliance",
        _in_domain_decide("regulatory approval must stay inside authorized Compliance"),
        required_capabilities=("compliance.review.decide",), in_domain=True, owner="compliance",
        description="Compliance review submit/assign/decide routing + the approval double-gate."),
    PolicyDefinition(
        "notification.routing", "notifications",
        _in_domain_decide("data-driven via the F5.2 provider registry; F5.5 dispatch is a frozen module"),
        in_domain=True, owner="notifications",
        description="Channel selection / dispatch eligibility (frozen F5.5 module)."),
    PolicyDefinition(
        "document.behavior", "documents",
        _in_domain_decide("deterministic document CRUD / relationships / retention — no switch"),
        required_capabilities=("document.read",), in_domain=True, owner="document_platform",
        description="Deterministic document platform behavior."),
    PolicyDefinition(
        "scheduling.behavior", "scheduling",
        _in_domain_decide("deterministic meeting-lifecycle state machine"),
        required_capabilities=("scheduling.view",), in_domain=True, owner="scheduling",
        description="Deterministic scheduling behavior (enforced in the scheduling service)."),
)

POLICY_DEFINITIONS: dict[str, PolicyDefinition] = {d.code: d for d in _DEFS}

# The ten declarative decision areas D.32 centralizes (for coverage reporting).
DECISION_AREAS = ("workflow", "advisor_workspace", "operations", "reporting", "automation",
                  "microsoft365", "notifications", "compliance", "documents", "scheduling")
