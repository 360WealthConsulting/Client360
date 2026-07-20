# Client360 — Version 1.0 Program Risk Register

Authoritative home for risks that could cause Version 1.0 to fail **despite successful
engineering** — operational, organizational, and governance risks that sit **outside** the
engineering team's control. Identified in the V1.0 Pre-Mortem.

This document does **not** cover: engineering/technical risks of the shipped software
([`V1_RELEASE_PLAN.md §4`](V1_RELEASE_PLAN.md)), release/operational *readiness criteria*
([`RELEASE_READINESS.md`](RELEASE_READINESS.md)), product-behavior *decisions*
([`PRODUCT_DECISIONS.md`](PRODUCT_DECISIONS.md)), or implementation rationale
([`ENGINEERING_DECISIONS.md`](ENGINEERING_DECISIONS.md)).

Each risk lists its repository evidence; none is engineering work to do — every remaining exposure
is an execution, ownership, adoption, or decision gap. Owners marked *(to appoint)* have no named
owner in the repository today, which is itself part of the risk.

_Last reviewed against `release/0.13.0`._

## Operational Risks

| ID | Description | Repository evidence | Likelihood | Impact | Existing mitigation | Remaining exposure | Recommended owner | Review cadence |
|----|-------------|---------------------|:--:|:--:|---------------------|--------------------|-------------------|----------------|
| OPS-1 | Deployment to staging/production is never executed | `RELEASE_READINESS` Deployment 🟡 "Outstanding (ops): staging/prod deploy"; `RC_READINESS` RC-4 | High | High | Verified `deploy.sh`/`smoke.sh`/`rollback.sh` + runbook + reversible migrations | Execution + no named owner | Operations lead *(to appoint)* | Weekly until executed |
| OPS-2 | No scheduled, encrypted production backups → data loss | `RELEASE_READINESS` Backup 🟡; `RC_READINESS` RC-8 owner "Ops" | Med-High | High | Restore **mechanism verified** (`restore_rehearsal.sh`) | Scheduling + RPO/RTO + owner | Operations lead *(to appoint)* | Weekly until in place, then quarterly |
| OPS-3 | Outage undetected — probes not wired to alerting | `RELEASE_READINESS` Monitoring 🟡 "Outstanding (ops): wire probes" | Med-High | High | `/readiness` verified robust (DB, migration-drift, scheduler, sync) | Alerting wiring + owner | Operations lead *(to appoint)* | Weekly until wired |
| OPS-4 | Disaster recovery never rehearsed on real infrastructure | Restore rehearsed **in-repo only**; `RELEASE_READINESS` §blockers | Med | High | Rehearsal script + runbook §5–6 | Production DR drill + owner | Operations lead *(to appoint)* | Quarterly |
| OPS-5 | Release gates not operationalized (SSO/env config; E2E not a required check) | `RELEASE_READINESS` E2E 🟡 advisory; SSO outstanding | Med | Med-High | Auth kernel built; E2E green; dev-auth impossible in prod | Branch-protection change + SSO config | Ops / repo admin *(to appoint)* | Once before pilot |

## Organizational Risks

| ID | Description | Repository evidence | Likelihood | Impact | Existing mitigation | Remaining exposure | Recommended owner | Review cadence |
|----|-------------|---------------------|:--:|:--:|---------------------|--------------------|-------------------|----------------|
| ORG-1 | Staff reject the tool / revert to Wealthbox | `V1_RELEASE_PLAN` thesis (staff switch *from Wealthbox*); deferred UX-friction items | High | High | `USER_GUIDE.md` | Change-management plan; friction reduction | Business / practice owner | Monthly during pilot |
| ORG-2 | Onboarding/training insufficient | `USER_GUIDE.md` present; **no training program** in repo | Med-High | Med | `USER_GUIDE.md` | Training program + rollout | Business / practice owner | Before pilot + ongoing |
| ORG-3 | Support process immature (no intake / SLA / triage) | `USER_GUIDE` "getting help" is request-id tracing only; no support runbook | Med | Med | Request-id tracing via audit log | Defined intake/triage/SLA | Support owner *(to appoint)* | Before pilot |

## Governance Risks

| ID | Description | Repository evidence | Likelihood | Impact | Existing mitigation | Remaining exposure | Recommended owner | Review cadence |
|----|-------------|---------------------|:--:|:--:|---------------------|--------------------|-------------------|----------------|
| GOV-1 | Executive sponsorship / ownership concentration | `PRODUCT_DECISIONS` names a single decision owner (Michael Shelton) for PD-1/2/3 | Med | High | Decisions recorded with safe defaults | Sponsor engagement + delegation of authority | Executive sponsor | Monthly |
| GOV-2 | Unresolved compliance ownership (AD-5 reviewer) | `PRODUCT_DECISIONS` PD-4 owner **UNFILLED**; regulated insurance gated | Med | High (regulated scope) | Regulated scope excluded/gated (safe) | Appoint an accountable compliance reviewer | Compliance *(to appoint)* | Quarterly / on regulated-scope demand |
| GOV-3 | Unclear ownership / no RACI for release + operations | `RELEASE_READINESS` / `RC_READINESS` owners are generic ("Ops") | Med | Med | Documented mechanisms + this register | Establish a RACI; name owners | Executive sponsor | Once (establish), then quarterly |
| GOV-4 | A future business-enabled policy is wrong (household grouping / identity merge) | Decisions in `PRODUCT_DECISIONS` PD-1/PD-2; safe defaults today | Low | High | Auto-derivation OFF; auto-merge not built; human review only | Sound policy + threshold when a decision is made | Business (via PD-1/PD-2) | On PD-1/PD-2 decision |

## Top three (from the Pre-Mortem)
If V1.0 fails despite the engineering, the most likely causes are **OPS-1/2/3 (operational
execution & ownership unassigned)**, **ORG-1 (adoption stall / no change management)**, and
**GOV-1/GOV-2 (decision & compliance ownership concentration)** — none of which engineering can
control.
