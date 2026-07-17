# Release 0.12.0 — Phase P1.0 Knowledge Inventory (PROPOSED)

_Preparatory inventory before Operations Manual authoring. **Read-only** — no procedures authored, no
Confluence modified, no legacy pages reconciled, no documentation published. Produced 2026-07-17 on
branch `release/0.12.0`. `v0.11.0` immutable; D1–D10 unchanged; AD-5 unresolved._

## 0. Headline finding

**The operational knowledge for nearly every P1 priority area already exists — authored — in the
legacy 360OS/Atlas Confluence space** (the `CAP-xxx` trees, ~100+ pages). Release 0.12 P1 is therefore
**predominantly adaptation/normalization of existing content into the new framework with SME
verification**, not greenfield authoring. This tightly couples P1 (authoring) with P4 (legacy
reconciliation): for most areas the "replacement documentation" is largely the **same content
re-homed** into the framework and quality-reviewed.

> **Method & caveat.** This inventory is built from (a) the full Git `docs/` listing, (b) read-only
> Confluence descendant listings for the richest capability trees, and (c) the `360OS Operations
> Home` self-reported status. Completeness/quality figures are **structural estimates** (page
> inventory + titles + the home page's own "source-backed/verified" notes), **not** a full
> content-accuracy audit — that audit is P1/P3 work. No page content is assumed accurate until SME-
> verified (decision A2).

## 1. Knowledge sources catalogued

### 1a. Git repository (`docs/`)
- **Software/platform architecture (canonical for the *software* facet only):** `PRODUCTION_ARCHITECTURE.md`,
  `IDENTITY_AUTHORIZATION_AUDIT.md`, `SECURITY_HARDENING_0.9.7.md`, `SCHWAB_PORTFOLIO_ENGINE.md`,
  `EPIC_5_TAX_PRACTICE_PLATFORM.md` + `TAX_*`, `MICROSOFT_CALENDAR_SYNC.md` /
  `MICROSOFT_DOCUMENT_SYNC.md`, `RELATIONSHIP_ENGINE.md`, `WORK_MANAGEMENT_PLATFORM.md`,
  `WORKFLOW_PROCESS_AUTOMATION.md`, `CLIENT_PORTAL.md`, `UI_DESIGN_SYSTEM.md`, `ADR_EXCEPTION_ENGINE_SCOPE.md`.
- **Deployment/ops-adjacent (software):** `RELEASE_0.9.9_DEPLOYMENT_RUNBOOK.md`, `RELEASE_0.9.9_DEPLOYMENT_GATES.md`.
- **Release/RC history:** `RELEASE_0.9.*`–`0.10.0`, `RC*_VALIDATION.md`, `ROADMAP.md`.
- **These document the Client360 *application*, not firm IT/vendor operations** (the app is not
  SonicWall/AD/Windows Server). They are canonical for **software** doc types; they are **not** the
  source for operational SOPs.

### 1b. Confluence — legacy 360OS/Atlas (the primary operational-knowledge store)
Verified read-only. Key capability trees and page counts:

| Capability tree | ID | Pages (approx) | Covers P1 areas |
|---|---|---|---|
| Technology Operations (CAP-006) | `24051794` | **~24** | IT Ops, **M365** (SOP-036), **Windows Server + AD** (SOP-037), **SonicWall** (SOP-038), **Networking/DNS/DHCP** (SOP-087), **Backup** (SOP-039) + **DR** (SOP-091), **Incident Response** (SOP-040), workstation/printer/Tailscale/Quick-Assist; CHK-029–034/075–077; POL-018 (access control), POL-019 (password/MFA), POL-020 (backup/recovery) |
| Tax Operations (CAP-004) | `23494657` | **~22** | **TaxDome** (SOP-016 intake) + **Drake** (SOP-017 1040, SOP-018 business), review/delivery, e-file, IRS notice, planning, extensions, estimates; CHK-013–020; POL-007–010 |
| Schwab Operations (CAP-002) | `23330825` | **~10** | **Schwab** account opening/MoneyLink/ACAT/billing; CHK-004–007; POL-003/004 |
| AssetMark Operations (CAP-003) | `23265282` | **~14** (per home) | **AssetMark** TAMP workflows |
| Client Lifecycle (CAP-001) | `23330817` | **~15** | **Client onboarding/servicing**: SOP-000–005, CHK-001–003, POL-001/002, TMP-001–003, PLY-001, DEC-001, LL-001 |
| Compliance (CAP-005) | `23560193` | **~25** (per home) | RIA compliance (ADV, books/records, marketing review, privacy, cyber) |
| HR / People Operations (CAP-007) | `23560201` | **~18** (per home) | HR ops |
| Vendor Management (CAP-011) | `24510566` | (root + assets) | Vendor/contract ops |
| Client Experience (CAP-014), Risk (CAP-013), Business Continuity (CAP-012), Marketing (CAP-010), Finance (CAP-009), Exec (CAP-008), Knowledge Eng (CAP-015), Business Intelligence | various | varies | servicing, risk, continuity, etc. |

Aggregators: `360OS Operations Home` (`24117290`), `📐 360 Standards` (`23199768`), `🗄️ Atlas
Archive` (`25755689`), `📚 Knowledge Library` (`23199760`). The 23 legacy pages are itemized in
`LEGACY_ATLAS_CONFLUENCE_RECONCILIATION.md`.

### 1c. Release 0.11/0.12 artifacts
The **canonical Publication Register** (`docs/registers/pages.yml`, 554 rows) defines the **target**
structure (489 `planned` rows) — where each authored page will live. The framework
(`documentation-framework/`), templates, and the advisory DoD tooling are the authoring/validation
substrate. These are *targets/tools*, not source content.

### 1d. Excluded — NOT documentation (flagged, not catalogued for content)
- `01 Raw Imports/` (Schwab `AccountsList`, AssetMark `ClientList`, Dave Ramsey opportunity CSVs) and
  `documents/**/*.csv` are **raw client data / imports** — **client data, not documentation**. **Out
  of scope; must never be published; contents not catalogued here.**

## 2. Per-area inventory

Legend — Completeness/Quality are structural estimates (pending P1/P3 content audit). Canonical: which
system owns the page. Priority: authoring priority. Effort: adaptation effort (existing content lowers it).

| Area (P1) | Existing sources | Completeness | Quality (est.) | Duplication | Obsolete risk | Recommended canonical | Priority | Effort | Group |
|---|---|---|---|---|---|---|---|---|---|
| **IT Operations** | CAP-006 tree | High (existing) | Med–High (source-backed, unverified) | Overlaps M365/AD/Server/Net (all under CAP-006) | Configs may be stale | Confluence (SOP/CHK/POL) + Git links | High (risk floor) | M | Requires SME Review |
| **Microsoft 365** | CAP-006 SOP-036/CHK-031; Git `MICROSOFT_*_SYNC` (software) | High | Med–High | Software sync vs admin SOP (distinct facets) | Tenant specifics may drift | Confluence (admin SOP); Git (software sync) linked | High | S | Requires SME Review |
| **Active Directory** | CAP-006 SOP-037 | Med–High | Med (unverified topology) | With Windows Server (same SOP) | AD structure must be confirmed | Confluence | High | S–M | Requires SME Review |
| **Windows Server** | CAP-006 SOP-037 | Med | Med | With AD | Server inventory/patch specifics | Confluence | High | S–M | Requires SME Review |
| **SonicWall** | CAP-006 SOP-038/CHK-032 | Med–High | Med (device config unverified) | With Networking/VPN | Firmware/rules may drift | Confluence | High | S | Requires SME Review |
| **Networking** | CAP-006 SOP-087/CHK-075 | Med | Med | With SonicWall | Topology/IP specifics need SME | Confluence | High | M | Requires Additional Information |
| **Backup & DR** | CAP-006 SOP-039/SOP-091/CHK-033/077/POL-020; Git `restore_rehearsal.sh` | Med–High | Med–High | DR execution vs backup review | RTO/RPO must be defined/verified | Git `governance/dr` (git-canonical) + Confluence render | **Highest** (continuity) | M | Requires SME Review |
| **Security Operations** | CAP-006 SOP-040/POL-018/019; Git `SECURITY_HARDENING`, `IDENTITY_AUTHORIZATION_AUDIT` (app) | Med–High | Med–High | App security (Git) vs infra/IR (Confluence) | IR contacts/controls | Git (app sec) + Confluence (infra IR/policy) | High | M | Requires SME Review |
| **Schwab Operations** | CAP-002 (~10) | **High** | Med–High (procedural) | Git `SCHWAB_PORTFOLIO_ENGINE` = software (distinct) | Low (procedural) | Confluence (ops SOP); Git (software) linked | High (revenue ops) | S | **Ready to Author** |
| **AssetMark Operations** | CAP-003 (~14) | **High** | Med–High | — | Low | Confluence | High | S–M | **Ready to Author** |
| **TaxDome** | CAP-004 SOP-016/CHK-013 | **High** | Med–High | Git `EPIC_5_TAX`/`TAX_*` = software (distinct) | Low | Confluence (ops); Git (software) linked | High | S | **Ready to Author** |
| **Drake Tax** | CAP-004 SOP-017/018 (1040/business) | **High** | Med–High | Within Tax tree | Low | Confluence | High | S | **Ready to Author** |
| **Wealthbox** | No dedicated CAP; referenced across Client Lifecycle/CRM | **Low** | — | — | — (thin) | Confluence (new area page) | Med | M | **Requires Additional Information** |
| **Client onboarding** | CAP-001 (SOP-000–005, CHK, TMP, PLY) | **High** | Med–High | With servicing/Client Experience | Some early placeholders archived | Confluence | High | M | **Ready to Author** |
| **Client servicing** | CAP-001 + CAP-014 (Client Experience) | Med–High | Med–High | Onboarding vs servicing boundary | — | Confluence | High | M | Ready to Author |
| **Internal SOPs** | Distributed across all CAP trees + SOP Library aggregator | High (as a set) | Varies | Cross-area (index, not duplication) | Archived SOP-401–406 | Confluence (SOP Library index → area pages) | Med | M | Ready to Author (index) |

## 3. Grouping

### 3a. Ready to Author (rich existing content; procedural; light verification)
**Schwab Operations, AssetMark Operations, Tax Operations (TaxDome + Drake), Client onboarding,
Client servicing, Internal SOP index.** These have substantial, procedural 360OS content that adapts
cleanly into the framework with light SME confirmation (not full interviews).

### 3b. Requires SME Review (existing content, but facts/configs must be verified — A2)
**IT Operations, Microsoft 365, Active Directory, Windows Server, SonicWall, Backup & DR, Security
Operations.** Content exists (CAP-006) but describes **infrastructure whose current configuration must
be SME-verified** before it is treated as accurate (topology, firmware/rules, tenant settings, RTO/RPO,
IR contacts). Author from verified facts; scaffold-not-fabricate where unverified.

### 3c. Requires Additional Information (thin/absent existing content)
**Wealthbox** (no dedicated tree), **deep Networking topology / asset inventory specifics** (IPs,
device lists), and any **RTO/RPO targets** not yet defined. Needs firm-provided facts before authoring
beyond a scaffold.

### 3d. Blocked by AD-5
**None of the P1 priority areas are AD-5-gated.** AD-5 blocks only **regulated insurance rule sets**
(suitability, replacement/1035, licensing/CE validation), which are **not** in the P1 list and remain
out of scope. *Note:* RIA-compliance content in CAP-005 is regulatory-sensitive but is **not** an
insurance AD-5 item; it is not a P1 priority area and is handled later with care — flagged, not
authored here.

## 4. Duplication & obsolete material

- **Canonical-home split (not duplication):** several areas have a **software** facet in Git
  (`SCHWAB_PORTFOLIO_ENGINE`, `EPIC_5_TAX`, `MICROSOFT_*_SYNC`) and an **operational** facet in
  Confluence (CAP SOPs). These are *distinct* doc types under the one-home rule — the operational page
  links the software page; neither is re-authored.
- **Within CAP-006**, AD + Windows Server share SOP-037, and SonicWall + VPN + Networking overlap —
  the framework will split these into per-area pages (a **split**, not a merge).
- **Obsolete:** archived `SOP-401–406` and duplicate scaffold pages already sit under `🗄️ Atlas
  Archive`; the 360OS home flags "early placeholders archived." These are obsolete and should **not**
  be carried forward.
- **Three home/standard pages** (`360OS Operations Home`, `📐 360 Standards`, framework node `01`)
  overlap — a reconciliation item (P4), not authoring.

## 5. Authoring queue (prioritized)

Scored on **business value**, **implementation risk**, **completeness of existing info**, and
**likelihood of successful completion without additional SME interviews**.

| Rank | Batch | Areas | Business value | Existing completeness | SME-interview need | Success likelihood |
|---|---|---|---|---|---|---|
| **1** | **Client-Platform Operations** | Schwab, Tax (TaxDome/Drake), AssetMark | **High** (core revenue ops) | **High** (~46 pages) | **Low** (procedural) | **Highest** |
| 2 | Client & Internal Workflows | Client onboarding, servicing, Internal SOP index | High | High (~15+ pages) | Low–Med | High |
| 3 | Technology / Risk-Floor Ops | IT Ops, M365, AD, Windows Server, SonicWall, Networking, Backup & DR, Security | **Highest** (continuity/risk) | High (~24 pages) | **Med–High** (config verification) | Med (needs SME) |
| 4 | Thin/greenfield | Wealthbox, deep network/asset inventory, RTO/RPO definition | Med | Low | High | Lower |

## 6. Recommended first documentation batch

**Batch 1 — Client-Platform Operations (Schwab + Tax [TaxDome/Drake] + AssetMark).**

**Rationale:**
- **Highest likelihood of successful completion without new SME interviews** — the content is
  **procedural/workflow** (account opening, tax return lifecycle, TAMP workflows), which is stable and
  well-captured in the ~46 existing 360OS pages; adaptation + light verification suffices.
- **High business value** — these are the firm's **core client-service, revenue-generating**
  operations (custodial, tax, managed-portfolio).
- **Rich, high-completeness existing content** (Schwab 10, Tax 22, AssetMark ~14) → framework
  adaptation, not greenfield.
- **Low implementation risk** — procedural content is less config-volatile than infrastructure; a
  clean pilot to validate the P2 Authoring Standard against real content before the harder
  infrastructure tier (Batch 3), which carries the highest value **but** the highest SME-verification
  burden.

**Deferred within P1 (later batches):** Technology/Risk-Floor Ops (Batch 3) is the **highest-value**
tier but should follow once SME verification of infrastructure specifics is scheduled; Wealthbox and
deep network/asset facts (Batch 4) need firm-provided information first.

## 7. Constraints honored

Read-only inventory only: no procedures authored; no Confluence modified; no legacy page reconciled;
nothing published; no AD-5 content; `v0.11.0`/D1–D10 untouched; client-data imports excluded and never
exposed.

---

**Stopping after the Knowledge Inventory and prioritized authoring queue.** No authoring begun.
Awaiting explicit approval before beginning Phase P1 (and confirmation of the first batch + the SME
verification path for Batch 3 infrastructure content).
