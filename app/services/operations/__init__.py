"""Enterprise Operations platform (Phase D.20).

The authoritative domain for firm OPERATIONAL metadata — projects, phases, milestones, operational
tasks, dependencies, checklists, operational resources, capacity plans, workload/utilization,
issues/risks, comments, and an append-only audit ledger. It manages firm operations only and is
**never a source of truth for business records**. Client/business links are optional references:
it references people/households/organizations, Advisor Work, Workflow, Scheduling, Communications,
Documents, Compliance, Opportunities, and the Timeline — but owns none of them. Advisor Work
remains the authoritative client-work domain; Operations only references it. Approved lifecycle
events flow to the shared Activity Timeline (client-anchored items only); Analytics consumes
operational statistics (Operations never depends on Analytics).
"""
