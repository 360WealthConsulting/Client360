# Client360 — Version 1.0 Release Plan

Authoritative definition of Version 1.0. Companion living docs:
[`RELEASE_READINESS.md`](RELEASE_READINESS.md) · [`PROJECT_STATUS.md`](PROJECT_STATUS.md) ·
[`PRODUCT_DECISIONS.md`](PRODUCT_DECISIONS.md).

**V1.0 thesis:** the smallest release that lets firm staff run their **daily client-management
work** in Client360 reliably, supportably, and maintainably — the Internal CRM. Everything beyond
that is deferred, excluded, or gated. Optimize for a reliable first release, not feature count.

_Baseline: `release/0.13.0` @ current tip. Frozen prior baseline: `v0.10.1-sprint1`._

---

## 1. Product Scope

### 1.1 Included in V1.0 (the CRM acceptance surface — built and verified)
- **Canonical client record** — people + households; editable contact/address fields (audited).
- **Search** — pg_trgm index-assisted, de-duplicated per canonical person.
- **Notes** — one permanent client note + typed append-only activity feed.
- **Communications** — one-click Log Call/Email/Meeting with inbound/outbound direction and optional
  follow-up tasks.
- **Tasks** — inline person tasks via the canonical assignment model, with submission idempotency.
- **Timeline & audit** — every user write emits a timeline event and an append-only audit event.
- **Identity pipeline** — import → conservative promotion → **Match Review** (human link/create) →
  on-demand backfill.
- **Household roll-up** — members, aggregate AUM, open tasks.
- **Security kernel** — capability + record scoping, CSRF same-origin, append-only audit, production
  session hardening (fail-fast on dev-auth; `SESSION_SECRET`/`SESSION_HTTPS_ONLY` enforced).
- **Operations** — `/health` + `/readiness` probes; reversible migrations; backup/restore mechanism.
- **Authentication** — external IdP (SSO) via OIDC.

### 1.2 Explicitly excluded from V1.0
- **Regulated insurance functionality** (suitability, replacement/1035, licensing validation) — behind
  the **AD-5 compliance gate**; non-regulated plumbing only. *Rationale:* requires a named compliance
  reviewer and sign-off (PD-4); shipping regulated logic without it is a compliance risk.
- **Automatic match-merge** — not built. *Rationale:* human review (built) covers the need; auto-merging
  client identities is a risk/compliance decision (PD-2), not required for a reliable V1.0.
- **Household auto-derivation activation** — engine built, default off. *Rationale:* the grouping rule is
  a business decision (PD-1); manual household management is sufficient for V1.0.

### 1.3 Deferred to V1.1+
- Communication metadata (participants/duration) — PD-3.
- `humandt` shared-templates refactor (timestamp formatting on all surfaces).
- Auth-endpoint rate limiting (low value — external IdP).
- Retire legacy free-text `tasks.assigned_to` after a data migration.
- Deepened tax/insurance/benefits domain hardening and their own V1.x certification.

### 1.4 Scope rationale
The tax, insurance (non-regulated), benefits, exceptions, portal, and M365-sync subsystems **exist in
the build** and are not removed, but they are **not part of the V1.0 acceptance criteria** — V1.0 is
certified on the CRM surface only. This keeps the first release small, testable, and supportable, and
avoids coupling V1.0 to the AD-5 compliance gate.

---

## 2. Release Criteria (measurable)

| # | Criterion | Measure | Status |
|---|-----------|---------|--------|
| E1 | Engineering: CRM surface complete | All §1.1 features merged to the release line | ✅ |
| E2 | Regression suite green | `scripts/test.sh run` = 0 failures (currently 1217 passed / 5 skipped) | ✅ |
| E3 | CI green on the release commit | `Client360 CI` success | ✅ (verify per-merge) |
| E4 | E2E covers the core flows | `Client360 E2E` green over login/dashboard/people/households/search/notes/tasks/comms | ✅ (advisory) |
| E5 | Migrations reversible, single head | `check_migrations_reversible.sh` + `check_migration_heads.sh` pass | ✅ |
| E6 | E2E promoted to a **required** status check | Branch protection lists `Client360 E2E` | ⛔ ops |
| O1 | Backup: scheduled, encrypted, RPO/RTO documented | Backup job configured on prod infra | ⛔ ops |
| O2 | Restore rehearsal passes on release schema | `restore_rehearsal.sh` clean (done for current schema) | ✅ mechanism |
| O3 | Staging deploy + rollback rehearsal | Recorded rehearsal on target infra | ⛔ ops |
| O4 | Monitoring/alerting wired to `/health`,`/readiness` | Probes registered in the monitoring system | ⛔ ops |
| O5 | SSO/IdP + env config in target environment | Successful login in the target env | ⛔ ops |
| D1 | Documentation | CHANGELOG, RELEASE_READINESS, PROJECT_STATUS, PRODUCT_DECISIONS, this plan current; a staff user guide exists | 🟡 (user guide pending) |
| T1 | Test coverage of V1.0 surface | Every V1.0 route/service has service- or route-level tests | ✅ |
| C1 | Compliance approvals | None required for the CRM scope; regulated insurance excluded (AD-5) | ✅ for scope |
| B1 | Business decisions blocking release | None: PD-1/PD-2/PD-3 have safe defaults and are not required for V1.0 | ✅ |
| I1 | Production infrastructure | App tier, managed Postgres, backups, TLS, secrets management provisioned | ⛔ ops |

**V1.0 is releasable when every row is ✅.** Engineering rows (E*, T1, D1-partial) are the team's;
O*/I1 are operational; C1/B1 are cleared for the CRM scope.

---

## 3. Remaining Engineering Work (categorized)

**Required for V1.0**
- Staff **user guide** (D1) — the one engineering-adjacent doc gap.
- Support **E6** by making the E2E workflow eligible as a required check (it is green and stable;
  promotion is a branch-protection action, but engineering confirms stability).

**Nice to have (must NOT delay V1.0)**
- `humandt` shared-templates refactor; communication metadata (PD-3); household-derivation UI/report.

**Technical debt (address when it improves reliability)**
- ~611 baselined ruff findings (issue #26); duplicate CI runs on feature→release PRs; legacy
  `tasks.assigned_to` fallback.

**Future enhancement (V1.1+)**
- Match auto-merge engine (PD-2); household auto-derivation activation (PD-1); rate limiting; deeper
  tax/insurance/benefits certification.

---

## 4. Version 1.0 Risk Register

**Engineering risks**
| ID | Description | Likelihood | Impact | Mitigation | Owner | Blocking |
|----|-------------|-----------|--------|-----------|-------|----------|
| ER-1 | UI regression not caught by service-level tests | Low | Med | E2E suite green + human browser smoke before GA | Eng | No (mitigated) |
| ER-2 | Baselined lint debt hides a real issue | Low | Low | Gate blocks *new* violations; burn-down tracked (#26) | Eng | No |
| ER-3 | Data-volume performance (search/profile) at scale | Med | Med | pg_trgm index in place; load test before GA (perf 🔴) | Eng | No for pilot; **yes for GA** |

**Operational risks**
| ID | Description | Likelihood | Impact | Mitigation | Owner | Blocking |
|----|-------------|-----------|--------|-----------|-------|----------|
| OR-1 | No scheduled prod backups → data loss | Med | **High** | Configure encrypted backups + RPO/RTO (restore verified) | Ops | **Yes (prod)** |
| OR-2 | Bad deploy with no rehearsed rollback | Med | High | Runbook + reversible migrations; staging rehearsal | Ops | **Yes (prod)** |
| OR-3 | Outage undetected (monitoring not wired) | Med | High | Wire `/readiness` to alerting | Ops | **Yes (prod)** |
| OR-4 | SSO/IdP misconfig blocks all access | Med | High | Verify login in target env before pilot | Ops | **Yes (pilot)** |

**Business risks**
| ID | Description | Likelihood | Impact | Mitigation | Owner | Blocking |
|----|-------------|-----------|--------|-----------|-------|----------|
| BR-1 | Wrong household grouping co-mingles clients | Low | High | Auto-derivation OFF by default; manual only (PD-1) | Business | No (safe default) |
| BR-2 | Wrong identity merge blends clients | Low | High | Auto-merge not built; human review only (PD-2) | Business | No (safe default) |
| BR-3 | Regulated insurance advice without review | Low | **High** | AD-5 gate; regulated logic excluded (PD-4) | Compliance | No for CRM scope |

---

## 5. Release Sequence (entry/exit criteria)

### Stage 1 — Internal engineering build *(current)*
- **Entry:** CRM features merged. **Exit:** E1–E5, T1 ✅; regression + CI + E2E green; backup/restore
  mechanism verified; docs current. → **All met.**

### Stage 2 — Internal business pilot
- **Entry:** Stage 1 exit + O4/O5 (monitoring wired, SSO configured) + a human browser smoke pass +
  a real (recoverable) backup taken. **Exit:** Lauren + a small staff group use it for real daily work
  for a defined period with no Sev-1 defects and no data-integrity issues.

### Stage 3 — Limited production
- **Entry:** Stage 2 exit + O1–O3 (scheduled backups, staging deploy+rollback rehearsal) + E6 (E2E a
  required check) + ER-3 perf validated at expected volume. **Exit:** a full production cohort operating
  with monitoring-confirmed stability over a defined window.

### Stage 4 — General availability
- **Entry:** Stage 3 exit + documented incident/support process + rollback proven in production once.
  **Exit:** GA declared; V1.1 planning begins.

---

## 6. Technical Lead responsibilities
`RELEASE_READINESS.md`, `PROJECT_STATUS.md`, and `PRODUCT_DECISIONS.md` remain living documents,
updated with each increment. This plan is the executive roadmap; it changes only for scope changes,
which must be recorded here with rationale.

## 7. Engineering philosophy
Every proposed change is evaluated against *"does this materially improve Version 1.0?"* If no, it is
recorded (debt/nice-to-have/future) and deferred. No feature creep; no gold plating; optimize for a
reliable first release.
