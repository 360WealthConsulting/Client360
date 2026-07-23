"""Pure declarative seed data for the Phase D.33 orchestration definitions (no app dependencies).

Shared by the D.33 Alembic migration (which seeds ``orchestration_definitions``) and the service-layer
definition catalog (``app/services/orchestration/definitions.py``, which attaches executable
coordinators by code) so the registry metadata and the executable definitions cannot drift. Contains
only plain data — canonical stage/transition graphs modelled at the orchestration-lifecycle level using
the seven canonical instance states (pending/active/waiting/completed/cancelled/failed/compensated).

``active`` definitions are driven by the orchestration engine (their call sites are coordinated
through it). ``in_domain`` definitions are the mature domain lifecycles (the workflow-template engine,
compliance approval, operations/scheduling/advisor/tax/exception/campaign/document/communications state
machines, the frozen notification dispatcher) — registered + governed but authoritative in their own
domain, never re-implemented here (documented exceptions, mirroring D.32 in-domain policies).
"""

# canonical instance states a stage maps to
PENDING, ACTIVE, WAITING, COMPLETED, CANCELLED, FAILED, COMPENSATED = (
    "pending", "active", "waiting", "completed", "cancelled", "failed", "compensated")


def _stage(name, kind, *, entry=(), exit=(), terminal=False):
    return {"name": name, "kind": kind, "entry_actions": list(entry), "exit_actions": list(exit),
            "terminal": terminal}


def _t(frm, action, to, policy=None):
    d = {"from": frm, "action": action, "to": to}
    if policy:
        d["policy"] = policy
    return d


def _lifecycle(code, category, name, owner, *, status="in_domain", description="", policy_refs=(),
               runtime_refs=(), depends_on=(), with_waiting=True, with_compensation=False):
    """A standard canonical orchestration lifecycle (pending → active → completed/cancelled/failed),
    optionally with a waiting branch and a compensation path. Acyclic toward completion (well-formed)."""
    stages = [_stage("pending", PENDING), _stage("active", ACTIVE),
              _stage("completed", COMPLETED, terminal=True),
              _stage("cancelled", CANCELLED, terminal=True),
              _stage("failed", FAILED)]
    transitions = [_t("pending", "start", "active"), _t("active", "complete", "completed"),
                   _t("active", "cancel", "cancelled"), _t("active", "fail", "failed")]
    if with_waiting:
        stages.append(_stage("waiting", WAITING))
        transitions += [_t("active", "wait", "waiting"), _t("waiting", "resume", "active"),
                        _t("waiting", "cancel", "cancelled")]
    compensation = {}
    if with_compensation:
        stages.append(_stage("compensated", COMPENSATED, terminal=True))
        transitions.append(_t("failed", "compensate", "compensated"))
        compensation = {"failed": "compensate"}
    return {"code": code, "category": category, "name": name, "owner": owner, "version": 1,
            "status": status, "initial_stage": "pending", "stages": stages, "transitions": transitions,
            "completion_stages": ["completed"], "policy_refs": list(policy_refs),
            "runtime_refs": list(runtime_refs), "depends_on": list(depends_on),
            "timeout_seconds": None, "retry_policy": {}, "compensation": compensation,
            "description": description}


# --- active orchestrations (driven by the engine) ----------------------------

_AUTOMATION_DISPATCH = {
    "code": "automation.dispatch", "category": "automation", "name": "Automation job dispatch",
    "owner": "automation", "version": 1, "status": "active", "initial_stage": "pending",
    "stages": [_stage("pending", PENDING), _stage("dispatching", ACTIVE),
               _stage("running", ACTIVE), _stage("completed", COMPLETED, terminal=True),
               _stage("failed", FAILED), _stage("compensated", COMPENSATED, terminal=True)],
    "transitions": [_t("pending", "dispatch", "dispatching"), _t("dispatching", "execute", "running"),
                    _t("running", "complete", "completed"), _t("running", "fail", "failed"),
                    _t("failed", "compensate", "compensated")],
    "completion_stages": ["completed"], "policy_refs": ["automation.job_execution"],
    "runtime_refs": ["automation.job"], "depends_on": [], "timeout_seconds": None,
    "retry_policy": {"max_attempts": 1}, "compensation": {"failed": "compensate"},
    "description": "Orchestrates a single automation job dispatch through the engine, composing the "
                   "existing automation framework (never replacing it). Routing consumes the policy "
                   "automation.job_execution."}

_WORKFLOW_REVIEW = {
    "code": "workflow.review", "category": "workflow", "name": "Review workflow launch",
    "owner": "advisor_workspace", "version": 1, "status": "active", "initial_stage": "pending",
    "stages": [_stage("pending", PENDING), _stage("routing", ACTIVE), _stage("launching", ACTIVE),
               _stage("completed", COMPLETED, terminal=True), _stage("cancelled", CANCELLED, terminal=True)],
    "transitions": [_t("pending", "route", "routing", "workflow.review_routing"),
                    _t("routing", "launch", "launching"), _t("launching", "complete", "completed"),
                    _t("routing", "reject", "cancelled")],
    "completion_stages": ["completed"], "policy_refs": ["workflow.review_routing"],
    "runtime_refs": [], "depends_on": [], "timeout_seconds": None, "retry_policy": {},
    "compensation": {},
    "description": "Orchestrates launching a review workflow from a meeting outcome. The pending→routing "
                   "transition consumes the policy workflow.review_routing; launching composes the "
                   "workflow-template engine (launch_workflow)."}


# --- in-domain (registered + governed; the lifecycle stays authoritative in the owning domain) -----

ORCHESTRATION_DEFINITIONS_SEED = [
    _AUTOMATION_DISPATCH,
    _WORKFLOW_REVIEW,
    _lifecycle("workflow.template_instance", "workflow", "Workflow template instance", "workflow",
               with_compensation=False,
               description="The workflow-template execution engine (workflow_automation.py): "
                           "active/paused(→waiting)/completed/cancelled. Authoritative in-domain."),
    _lifecycle("compliance.review", "compliance", "Compliance review approval chain", "compliance",
               with_waiting=True,
               description="Submit→assign→decide approval chain with the regulatory double-gate. "
                           "Regulatory approval stays inside authorized Compliance (architecture invariant)."),
    _lifecycle("compliance.authority", "compliance", "Reviewer authority lifecycle", "compliance",
               description="Reviewer-authority record lifecycle (draft/active/suspended/revoked). In-domain."),
    _lifecycle("operations.project", "operations", "Operations project lifecycle", "operations",
               description="Deterministic project state machine (planned/active/blocked/on_hold/completed). In-domain."),
    _lifecycle("operations.task", "operations", "Operations task lifecycle", "operations",
               description="Deterministic task state machine with finish-to-start dependency gating. In-domain."),
    _lifecycle("advisor_work.item", "advisor", "Advisor work item lifecycle", "advisor_work",
               description="Advisor work item (new/assigned/in_progress/waiting/completed/cancelled/archived). In-domain."),
    _lifecycle("scheduling.meeting", "scheduling", "Meeting lifecycle", "scheduling",
               description="Deterministic meeting lifecycle (draft/scheduled/confirmed/checked_in/completed). In-domain."),
    _lifecycle("tax.return_lifecycle", "tax", "Tax return production lifecycle", "tax",
               description="Multi-stage tax-return production + filing sub-machine. In-domain."),
    _lifecycle("exception.lifecycle", "operations", "Exception lifecycle", "exceptions",
               description="Exception state machine (open/acknowledged/in_progress/resolved). In-domain."),
    _lifecycle("campaign.lifecycle", "reporting", "Campaign lifecycle", "campaign",
               description="Marketing campaign lifecycle (draft/active/paused/completed/archived). In-domain."),
    _lifecycle("document.lifecycle", "documents", "Document lifecycle", "document_platform",
               description="Document platform lifecycle (draft/active/review/approved/archived). In-domain."),
    _lifecycle("communications.delivery", "notifications", "Communication delivery lifecycle", "communications",
               description="Communication delivery lifecycle (queued/sending/sent/delivered/read). In-domain."),
    _lifecycle("notification.dispatch", "notifications", "Notification dispatch", "notifications",
               with_waiting=False,
               description="Notification channel dispatch — the certified frozen F5.5 module. In-domain (never modified)."),
]

# The orchestration domains D.33 centralizes (for coverage reporting) — the distinct categories.
ORCHESTRATION_DOMAINS = sorted({d["category"] for d in ORCHESTRATION_DEFINITIONS_SEED})
