"""Runtime Policy Engine, Declarative Rules, and Centralized Decision Services (Phase D.32).

D.32 centralizes application BUSINESS DECISIONS (eligibility / routing / gating / visibility) behind a
single Runtime Policy Engine that consumes the D.28 ``RuntimeContext``. The policy engine never
evaluates configuration itself and never bypasses the runtime engine — the runtime engine remains the
sole evaluator; D.29 coordination remains the sole synchronization mechanism; D.27 remains the sole
metadata owner; RBAC remains the sole access authority (policies never bypass capabilities/scope).

The only persistence D.32 adds is a durable **policy registry** (``runtime_policies``): the
discoverable catalog of the declarative decision policies — category, version, lifecycle status,
owner, the runtime definition each consumes, the capabilities referenced, and the dependency graph.
Seeds the 13 policies covering the ten declarative decision areas: nine are ``active`` (evaluated by
the engine — their call sites are rewired through it, behavior-preserving) and four are ``in_domain``
(registered + governed, but enforcement stays in the owning domain by documented constraint —
regulatory approval / the certified frozen F5.5 notification module / deterministic state machines).

Reuses the existing D.28 ``runtime.*`` capabilities (no new capabilities). Additive and reversible.
Single Alembic head (down ``z8a9b0c1d2e3``).
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "z9b0c1d2e3f4"
down_revision = "z8a9b0c1d2e3"
branch_labels = None
depends_on = None

_POLICY_STATUSES = ("active", "in_domain", "deprecated", "retired")


def _in(col, values):
    return f"{col} IN (" + ",".join(f"'{v}'" for v in values) + ")"


# (code, category, name, status, owner, consumes_feature, consumes_config, required_capabilities,
#  depends_on, per_instance, requires_definition, in_domain, default_decision, description)
_SEED = (
    # --- advisor workspace visibility (nested: tasks/exceptions live inside the work section) ------
    ("advisor_workspace.section.work", "advisor_workspace", "Advisor workspace: work section",
     "active", "advisor_workspace", "advisor_workspace.section.work", None, ["work.read"], [],
     False, True, False, True,
     "Whether the advisor daily-dashboard work section is shown (runtime feature + work.read)."),
    ("advisor_workspace.section.tasks", "advisor_workspace", "Advisor workspace: tasks section",
     "active", "advisor_workspace", "advisor_workspace.section.tasks", None, ["task.read"],
     ["advisor_workspace.section.work"], False, True, False, True,
     "Whether the tasks sub-section is shown (composes section.work; runtime feature + task.read)."),
    ("advisor_workspace.section.exceptions", "advisor_workspace", "Advisor workspace: exceptions section",
     "active", "advisor_workspace", "advisor_workspace.section.exceptions", None, ["exception.read"],
     ["advisor_workspace.section.work"], False, True, False, True,
     "Whether the exceptions sub-section is shown (composes section.work; runtime feature + exception.read)."),
    # --- workflow routing (the review-template whitelist) ------------------------------------------
    ("workflow.review_routing", "workflow", "Workflow review-template routing",
     "active", "advisor_workspace", None, None, ["client.write"], [], False, False, False,
     {"allowed": ["annual_review", "insurance_review"]},
     "Which review workflow templates may be launched from a meeting outcome (bounded whitelist)."),
    # --- automation execution (per-instance job-type key space) ------------------------------------
    ("automation.job_execution", "automation", "Automation job execution eligibility",
     "active", "automation", "automation.job", None, ["automation.execute"], [], True, False, False,
     True, "Whether an automation job type may execute (runtime automation.job.<type>; default enabled)."),
    # --- reporting eligibility (per-instance report-id key space) ----------------------------------
    ("reporting.module_eligibility", "reporting", "Reporting module eligibility",
     "active", "reporting", "reporting.module", None, ["reporting.view"], [], True, False, False,
     True, "Whether an optional report definition is included (runtime reporting.module.<id>; default included)."),
    # --- Microsoft integration behavior ------------------------------------------------------------
    ("microsoft365.sync_eligibility", "microsoft365", "Microsoft 365 sync eligibility",
     "active", "microsoft365", "microsoft365.sync", None, ["communication.read"], [], False, True,
     False, True, "Whether Microsoft 365 mail/calendar/document sync runs (runtime microsoft365.sync)."),
    ("microsoft365.sharepoint_scope", "microsoft365", "Microsoft 365 SharePoint site scope",
     "active", "microsoft365", None, "microsoft365.sharepoint_site_ids", ["communication.read"],
     ["microsoft365.sync_eligibility"], False, True, False, "",
     "The SharePoint site-id scope for document discovery (runtime config; composes sync eligibility)."),
    # --- operations visibility (the timeline-publish whitelist) ------------------------------------
    ("operations.timeline_publish", "operations", "Operations timeline-publish eligibility",
     "active", "operations", None, None, ["operations.view"], [], False, False, False, True,
     "Whether an operational lifecycle event kind publishes to the timeline (bounded event whitelist)."),
    # --- in-domain (registered + governed; enforcement stays in the owning domain) -----------------
    ("compliance.decision_routing", "compliance", "Compliance decision routing",
     "in_domain", "compliance", None, None, ["compliance.review.decide"], [], False, False, True,
     None, "Compliance review submit/assign/decide routing + the approval double-gate. Regulatory "
     "approval MUST stay inside authorized Compliance (architecture invariant) — registered for "
     "governance/inventory only; the generic engine never evaluates it."),
    ("notification.routing", "notifications", "Notification channel routing",
     "in_domain", "notifications", None, None, [], [], False, False, True,
     None, "Channel selection / dispatch eligibility. Data-driven via the F5.2 provider registry; the "
     "F5.5 notification_dispatch module is a certified frozen module — registered for governance only, "
     "never modified, never evaluated by the generic engine."),
    ("document.behavior", "documents", "Document platform behavior",
     "in_domain", "document_platform", None, None, ["document.read"], [], False, False, True,
     None, "Deterministic document CRUD / relationships / retention — no behavioral switch; registered "
     "for inventory only."),
    ("scheduling.behavior", "scheduling", "Scheduling behavior",
     "in_domain", "scheduling", None, None, ["scheduling.view"], [], False, False, True,
     None, "Deterministic meeting-lifecycle state machine + timeline-publish rules; registered for "
     "inventory only (enforced in the scheduling service)."),
)


def upgrade():
    bind = op.get_bind()
    op.create_table(
        "runtime_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.Text, nullable=False, unique=True),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("owner", sa.Text),
        sa.Column("consumes_feature", sa.Text),
        sa.Column("consumes_config", sa.Text),
        sa.Column("required_capabilities", sa.JSON),
        sa.Column("depends_on", sa.JSON),
        sa.Column("per_instance", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("requires_definition", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("in_domain", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("default_decision", sa.JSON),
        sa.Column("deprecated_at", sa.DateTime(timezone=True)),
        sa.Column("deprecated_reason", sa.Text),
        sa.Column("policy_metadata", sa.JSON),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(_in("status", _POLICY_STATUSES), name="ck_runtime_policy_status"),
    )

    for (code, category, name, status, owner, feat, cfg, caps, deps, per_inst, req_def, in_dom,
         default_decision, desc) in _SEED:
        if bind.execute(sa.text("SELECT id FROM runtime_policies WHERE code=:c"),
                        {"c": code}).scalar() is None:
            bind.execute(sa.text(
                "INSERT INTO runtime_policies "
                "(code, category, name, status, owner, consumes_feature, consumes_config, "
                " required_capabilities, depends_on, per_instance, requires_definition, in_domain, "
                " default_decision, description) "
                "VALUES (:c, :cat, :n, :s, :o, :feat, :cfg, CAST(:caps AS json), CAST(:deps AS json), "
                " :pi, :rd, :idm, CAST(:dd AS json), :desc)"),
                {"c": code, "cat": category, "n": name, "s": status, "o": owner, "feat": feat,
                 "cfg": cfg, "caps": json.dumps(caps), "deps": json.dumps(deps), "pi": per_inst,
                 "rd": req_def, "idm": in_dom, "dd": json.dumps(default_decision), "desc": desc})


def downgrade():
    op.drop_table("runtime_policies")
