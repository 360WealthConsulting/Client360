# Legacy 360OS / Atlas ↔ Operations Manual — Reconciliation Inventory

_Informational **inventory and recommendation only**. Prepared during Release 0.11.0 · P2 from the
read-only page data discovered in P1 (see `P1_CONFLUENCE_SKELETON_REPORT.md` §8). **No Confluence
page was moved, renamed, edited, archived, merged, relabeled, re-parented, or deleted; the new
Operations Manual nodes and Area Shell templates were not altered.** Every recommendation is a
proposal for a **separate, future reconciliation decision** — nothing here is executed. Date
2026-07-17._

> Status values below distinguish **Confluence page status** (`current` = live in the space) from the
> page's own **document-status metadata** (e.g. body says "Draft"/"Published"). Search indexes only
> `current` pages, so all listed pages are live; several carry an internal "Draft" doc-status.
> Current parent for all listed top-level pages is the space homepage `21266602` unless noted.

## 1. Master inventory

| # | Title | Page ID | Doc-status (meta) | Apparent subject / capability | Likely framework destination | Overlap | Canonical-source concern | Recommendation | Migration risk |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 360OS Operations Home | `24117290` | Published (HOME-001) | Firm front door / start-here | node `00` (GOV) | **`00 · Company Home`** | Two "home" pages | **Requires manual review → eventually merge** (designate one canonical home) | Med |
| 2 | 📐 360 Standards | `23199768` | current | Documentation standard | node `01` | **`01 · How This Manual Works`** | Competing doc standard vs framework README | **Requires manual review → merge/link** | Med |
| 3 | 🗄️ Atlas Archive | `25755689` | current | Legacy/duplicate store | GOV (utility) | — (destination for superseded pages) | None | **Retain in place** (use as archive target) | Low |
| 4 | 🧾 Tax Operations | `23494657` | Draft (CAP-004) | Tax process | `TAXOPS` (node 10) | node 10 area | Software arch is Git-canonical; page = process (Confluence-canonical) | **Eventually move + link** under node 10; manual review | Low–Med |
| 5 | ⚖️ Compliance | `23560193` | Draft (CAP-005) | Compliance process | `CMP` (node 30) | node 30 area | Controls are Git-canonical (`governance/controls`) | **Eventually move**; manual review; **AD-5** | Med |
| 6 | 👥 HR / People Operations | `23560201` | Draft (CAP-007) | HR process | `HR` (node 30) | node 30 area | Policies Git-canonical (`governance/policies`) | **Eventually move + link** | Low |
| 7 | 💰 Finance Operations | `24510485` | Draft (CAP-009) | Finance/accounting | `ACCT` (node 30) | node 30 area | Revenue defs partly software (link) | **Eventually move** | Low |
| 8 | 📣 Marketing Operations | `25034803` | Draft (CAP-010) | Marketing | `MKT` (node 30) | node 30 area | None | **Eventually move** | Low |
| 9 | 🤝 Vendor Management | `24510566` | Published (CAP-011) | Vendor/contracts | `VEND` (node 30) | node 30 area | Vendor Register lives in node 90 | **Eventually move + link** | Low–Med |
| 10 | 🛡️ Business Continuity | `24510526` | Draft (CAP-012) | Continuity/DR | `DR` (node 20) | node 20 area | **DR is Git-canonical (`governance/dr`)** → page must become a link | **Eventually move → link, not canonical**; manual review | Med |
| 11 | 💻 Technology Operations | `24051794` | Published (CAP-006) | IT/infra (M365/AD/network/etc.) | node 20 (M365/AD/NET/SRV/SEC) | node 20 areas | Arch/security Git-canonical | **Requires manual review → split/move** (1→many) | Med |
| 12 | ⚠️ Risk Management | `25886741` | Draft (CAP-013) | Enterprise risk | `CMP`/`GOV` (no 1:1 area) | node 30 / GOV | No dedicated framework area | **Requires manual review** | Med |
| 13 | 🏛️ Executive Management | `25493545` | Draft (CAP-008) | Exec management | `GOV` / node 00 | node 00 | Structural, not a capability area | **Requires manual review** | Low |
| 14 | 💎 Client Experience | `25657365` | Draft (CAP-014) | Client experience | `DOC`/`CLM360` (node 10) | node 10 area | Portal docs Git-canonical (`CLIENT_PORTAL.md`) | **Eventually move + link** | Low |
| 15 | 👤 Client Lifecycle | `23330817` | Draft (CAP-001) | Client onboarding lifecycle (**many child SOP/CHK/POL/TMP pages**) | `CRM`/`CLM360` (node 10) | node 10 area | Process = Confluence-canonical | **Eventually move**; manual review; **high child dependency** | Med–High |
| 16 | 📈 Schwab Operations | `23330825` | Draft (CAP-002) | Custodian ops (has children) | `WLTH` (node 10) | node 10 area | Portfolio arch Git-canonical | **Eventually move + link** | Low–Med |
| 17 | 📊 AssetMark Operations | `23265282` | Draft (CAP-003) | Managed-portfolio ops | `WLTH` (node 10) | node 10 area | As above | **Eventually move + link** | Low |
| 18 | 💼 Office Operations | `23625729` | current | Office/facilities | `OFFICE` (node 30) | node 30 area | None | **Eventually move** | Low |
| 19 | 📚 Knowledge Library | `23199760` | current | Cross-area library | node `80` | node 80 | Aggregator/index | **Link/merge**; manual review | Low |
| 20 | 📊 Business Intelligence | `23035917` | current | KPIs/dashboards | `RPT` (node 10) / KPIs | node 10 | Reporting defs partly software | **Eventually move + link**; manual review | Low |
| 21 | 📘 Atlas v0.2 Repository | `23822337` | current | Atlas build/meta tracking | GOV (meta) | — | Meta/experimental | **Eventually archive** or manual review | Low |
| 22 | Builder Pilot | `23920682` | current | Tooling/experiment | — (unclear) | — | Unclear purpose | **Requires manual review** | Low |
| 23 | 🏠 Home | `23166977` | current | Duplicate/legacy home | node 00 | overlaps `00` / #1 | Third "home" page | **Requires manual review** | Low |

## 2. Specific analysis (required pages)

### `360OS Operations Home` (`24117290`) — overlaps `00 · Company Home` (`28966913`)
A **Published** front-door page (HOME-001) that functionally is what the framework calls Company Home.
Two home pages should not both be canonical. **Recommendation:** manual review → **merge** into one
canonical Company Home, preserving the surviving page's ID and redirecting/linking the other.
**Dependency:** it is the space's navigational entry point; reconciliation should update inbound
navigation. **Risk:** Med (front-door visibility). **No action taken.**

### `📐 360 Standards` (`23199768`) — overlaps `01 · How This Manual Works` (`28835861`)
An existing documentation-standard page that overlaps the framework's "How This Manual Works" node and
the framework README. **Recommendation:** manual review → **merge/link** so there is one documentation
standard (the framework standard is canonical in Git; 360 Standards becomes a rendered link or is
absorbed). **Risk:** Med (existing contributors may reference it).

### `🗄️ Atlas Archive` (`25755689`)
Purpose-built store for legacy/duplicate/superseded pages, with its own "do not delete without
approval" rules. **Recommendation:** **retain in place** and use it as the **destination** for any
pages the future reconciliation decides to archive. **Risk:** Low. It is an asset to the
reconciliation, not a conflict.

### `CAP-xxx` capability pages (items 4–18 above)
These are the pre-existing, capability-based operating manual (`CAP-001`…`CAP-014`). They map to
framework **areas** under nodes 10/20/30 (and GOV), **not** to the framework nodes themselves — which
is why P1's node creation was not duplicative. Most are internal doc-status **Draft**; a few
(`CAP-006`, `CAP-011`, HOME) are **Published**. **General recommendation:** **eventually move/link**
each under its framework area during area provisioning (P3+), with **manual review** because each has
real authored content and its own CAP identifier/taxonomy. Pages whose subject is **Git-canonical**
(DR `CAP-012`, Technology `CAP-006`, Compliance controls `CAP-005`) must become **rendered links**,
not second canonical copies. **`CAP-001 Client Lifecycle` carries many child pages** (SOPs, checklists,
policies, templates — including a self-labeled "⚠️ DUPLICATE - DELETE" page `23887873`) → highest
dependency and migration risk; review its subtree as a unit.

### Overlaps with `00 · Company Home`
`360OS Operations Home` (`24117290`) and `🏠 Home` (`23166977`) both overlap `00 · Company Home`
(`28966913`). Three home-like pages exist. **Recommendation:** consolidate to one canonical home under
manual review; **no action now.**

### Overlaps with `01 · How This Manual Works`
`📐 360 Standards` (`23199768`) overlaps `01 · How This Manual Works` (`28835861`). Consolidate the
documentation standard to one canonical home (Git framework standard + Confluence rendering).

### Overlaps with area nodes under `10`, `20`, `30`
- **Node 10 (Client-Facing):** Tax (`23494657`), Client Experience (`25657365`), Client Lifecycle
  (`23330817`), Schwab (`23330825`), AssetMark (`23265282`), Business Intelligence (`23035917`) →
  areas TAXOPS, DOC/CLM360, CRM, WLTH, RPT.
- **Node 20 (Tech & Infra):** Technology Operations (`24051794`), Business Continuity (`24510526`) →
  M365/AD/NET/SRV/SEC, DR. **DR/Tech subjects are Git-canonical** (`governance/dr`, `docs/*`).
- **Node 30 (Business Ops):** Compliance (`23560193`), HR (`23560201`), Finance (`24510485`),
  Marketing (`25034803`), Vendor Management (`24510566`), Office Operations (`23625729`), Risk
  Management (`25886741`) → CMP, HR, ACCT, MKT, VEND, OFFICE, (risk → CMP/GOV).

## 3. Cross-cutting observations

- **Third taxonomy.** The CAP-xxx / emoji scheme is a **third** area taxonomy alongside the crosswalk
  letters (A–N) and the framework area codes — it **expands D10's input set**. D10's migration
  (approved for P3) was assessed before these live pages were visible; its validation checklist should
  be extended to cover these ~23 pages before execution.
- **Canonical-home conflicts** concentrate where the framework says a subject is Git-canonical (DR,
  Technology architecture, Compliance controls). Those Confluence pages should become **links**, not
  duplicate canonical copies, to honor one-canonical-home.
- **Migration is metadata/parentage**, not content: page IDs and URLs are stable under re-parenting;
  risk is concentrated in the home/standards consolidation and the Client Lifecycle subtree.

## 4. Recommended next step (not executed)

Before P3 register promotion and the D10 migration, obtain a **reconciliation decision** that assigns
each page above one of: retain / link / move / merge / archive / manual-review — and a canonical home.
Feed those dispositions into the P3 `pages.yml` seeding so the register is correct from the start.

_Inventory and recommendation only. No Confluence page or new Operations Manual node/template was
changed by this artifact._
