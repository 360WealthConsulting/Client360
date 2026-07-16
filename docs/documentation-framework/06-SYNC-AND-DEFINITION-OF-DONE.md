# Deliverable 6 — Sync Model & Definition of Done (company-wide)

How the three surfaces — **GitHub** (technical + version-controlled governance), **Confluence**
(published operational manual), and **release/change documentation** — stay in lockstep across
software, infrastructure, and business operations, automatically and without duplication.
*Design only; implementation is a roadmap Phase E item — no application code is added.*

## 1. Source-of-truth layering (extended for non-code areas)

The principle is unchanged: **Git is the technical source of truth; Confluence is the operational
publishing platform.** For a company-wide manual there are now **three** canonical patterns —
still exactly one home per page:

| Content | Canonical home | Flow |
|---|---|---|
| Software technical (architecture, data model, rules, security, workflows, exceptions, integrations, reporting-defs, changelog, release notes) | **Git** (`docs/`, `app/`, migrations) | Git → Confluence (generated summary + link) |
| **Version-controlled governance** (policies, runbooks, DR/BCP plans, controls register, operating-calendar data, IR playbooks) | **Git** (`governance/`) | Git → Confluence — PR-reviewed for audit trail |
| Infrastructure config/inventory | **Git** (config / `governance/inventory`) or an authoritative system export | Generated → Confluence & the Asset Register |
| Staff-facing operational (User/Process Guides, SOPs, Checklists, Troubleshooting, FAQ, Training, Executive/Purpose, RACI, Vendor Register entries) | **Confluence** | Authored in Confluence; Git holds the register row + backlink |

Rule of thumb: **if a document needs a change history and review (a policy, a DR plan, a
control), it is git-canonical in `governance/`.** If it is narrative staff guidance, it is
Confluence-canonical. The register's `canonical_source` field is the contract; nothing is
authored in both.

## 2. Publication Register (machine-readable, all 26 areas)

Promote `docs/DOCUMENTATION_CROSSWALK.md` to `docs/registers/pages.yml` — one entry per page,
across every area and profile:

```yaml
- page_id: DR-BCP
  area: DR
  profile: infrastructure
  doc_type: BCDR
  canonical_source: git
  git_source: governance/dr/business-continuity-plan.md
  confluence_page_id: "TBD"
  owner: "IT lead"
  reviewer: "COO"
  status: draft
  last_reviewed: 2026-07-16
  review_cycle: semiannual
  next_review: 2027-01-16
```

The human-readable crosswalk table is generated from this file (single index for the whole
company). Ownership Directory, Review Calendar, Vendor Register, Asset Register, and Controls
Register are all views over it.

## 3. `scripts/docs_sync.py` — the sync tool (proposed)

One repo-tooling script (not application code), four subcommands, via the connected
**Atlassian/Confluence MCP** or REST API:

| Command | Runs where | Does |
|---|---|---|
| `verify` | CI on every PR | The **docs gate** — assert DoD obligations for the change type (§4). Non-zero exit fails the build. |
| `push` | CI on merge / release | Render changed **git-canonical** pages (software + `governance/`) and upsert to Confluence (idempotent, diff-driven). |
| `report` | CI on merge / nightly | For each **Confluence-canonical** page whose linked source changed, open a review task; mark register row `needs_review`. |
| `audit` | Nightly / weekly cron | Flag pages past `next_review`; surface expiring **vendor contracts** and **DR test** dates from the registers; notify owners. |

## 4. Definition of Done — now covering three change types

A change is "done" only when its documentation is current. The docs gate keys off **what the
change touches**:

**A. Software release/phase PR** (as before): Change Log (module-tagged) · Release Notes ·
every git-canonical doc type touched (path→type mapping: `migrations/**`→Data Model;
`*_detectors.py`/new exception types→Exception Handling; role/cap seeds→Security; workflow
templates→Workflows; `connectors|importers/**`→Integrations; `*_reporting.py`→Reporting;
`*_ARCHITECTURE.md`→Architecture) · register row · flagged Confluence follow-ups.

**B. Infrastructure change** (config, `governance/` runbook/DR, inventory): updated **Runbook**
and/or **Asset & Config Inventory** · **Change Record** · **DR/BCP** updated if a critical
service or RTO/RPO changed · register row.

**C. Business-process change** (policy, SOP, RACI, vendor, control): the affected **Policy /
SOP / RACI / Checklist / Controls / Vendor** page · effective-date bump · register row · owner
sign-off.

**PR / change template checklist:**
```
### Documentation (Definition of Done)
Change type: [ ] Software  [ ] Infrastructure  [ ] Business process
- [ ] Change Log / Change Record updated
- [ ] Affected git-canonical docs updated (arch/data/rules/security/workflows/exceptions/
      integrations/reporting  OR  runbook/DR/inventory/policy/controls)
- [ ] Publication register rows updated (status + last_reviewed + effective date)
- [ ] Confluence-canonical follow-ups filed (User/SOP/Training/Vendor/RACI as needed)
- [ ] Reviewer assigned (independent of author where possible)
```

The gate is **advisory** in roadmap Phase A and **blocking** in Phase E.

## 5. Release & change integration

- `scripts/release.sh` gains a **docs precondition** (`docs_sync verify --release`): refuses to
  tag a release with unmet documentation obligations (same pattern as its CHANGELOG/head checks).
- On tag, `docs_sync push` publishes git-canonical updates and generated per-area Release Notes.
- Infrastructure and business-process changes route through the same register + gate, so a
  firewall change or an updated HR policy is held to the same "documented = done" bar as a code
  release.

## 6. Why this prevents duplication and drift (company-wide)

- **One canonical home per page** (register-enforced) → the same words never live twice, whether
  a software rule or an HR policy.
- **Push is generated, not authored** → git-canonical Confluence pages (including policies/DR
  from `governance/`) can't drift.
- **Report + audit** → stale Confluence-canonical pages, expiring contracts, and overdue DR
  tests surface as tasks, not silent rot.
- **Docs gate across all three change types** → the gap can't reopen after a release, an infra
  change, or a process change — "documented" is part of "done" everywhere.

## 7. What implementation (post-approval, Phase E) will add
- `docs/registers/pages.yml` + the crosswalk generator; the `governance/` tree.
- `scripts/docs_sync.py` (verify/push/report/audit).
- CI workflow step + PR/change template + `release.sh` precondition.
All repo tooling, configuration, and documentation — **no application-code changes.**
