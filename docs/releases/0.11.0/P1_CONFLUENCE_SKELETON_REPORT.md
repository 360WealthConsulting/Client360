# Release 0.11.0 — 0.11-P1 Confluence Space Skeleton Report

_Phase **0.11-P1 — Confluence Space Skeleton**. Branch `release/0.11.0`. Space **3WCO** (id
`21266437`), homepage `21266602`. Executed 2026-07-17. Provisioning only — no operational content
authored, no released Insurance pages altered, no Publication Register work begun._

> **Guardrails honored:** no Publication Register begun; D10 not implemented; `DOCUMENTATION_CROSSWALK.md`
> not modified; `docs/registers/pages.yml` not created; no `governance/` tree; no DoD gate; no app
> code/migrations; no regulated Insurance content published; compliance reviewer not assumed named;
> P2/P3/P4 not started. Only the P1 report was added to the repository.

---

## 1. Hierarchy created (top-level Operations Manual nodes)

All eight nodes were **created new** (pre-provision CQL search returned **0** existing equivalents),
`status: current`, parent = space homepage `21266602`.

| Node title | Page ID | Parent ID | Created / Reused |
|---|---|---|---|
| 00 · Company Home | `28966913` | `21266602` | **Created** |
| 01 · How This Manual Works | `28835861` | `21266602` | **Created** |
| 10 · Client-Facing Operations | `28999681` | `21266602` | **Created** |
| 20 · Technology & Infrastructure | `29032449` | `21266602` | **Created** |
| 30 · Business Operations | `29032469` | `21266602` | **Created** |
| 40 · Cross-Platform & Shared | `28868631` | `21266602` | **Created** |
| 80 · Libraries & Programs | `28835881` | `21266602` | **Created** |
| 90 · Registers & Governance | `28868651` | `21266602` | **Created** |

Each node carries a "structure only" banner, a one-line purpose from the Information Architecture,
and an explicit "content authored in later phases" note. No operational content.

## 2. Area Shell templates

Three reusable Area Shell templates were **created new** as child pages of node `01` (`28835861` ·
*Page Template Library*), `status: current`.

| Template | Page ID | Parent ID | Created / Reused |
|---|---|---|---|
| Area Shell Template — Software Profile | `28966933` | `28835861` | **Created** |
| Area Shell Template — Infrastructure Profile | `28999701` | `28835861` | **Created** |
| Area Shell Template — Business Operations Profile | `28835901` | `28835861` | **Created** |

**Each template contains** (and nothing more): the "clone-me, don't author here" banner; the required
YAML **front-matter** block (title, `page_id`, `area`, `profile`, `doc_type`, `canonical_source`,
`git_source`, `confluence_page_id`, `owner`, `reviewer`, `status`, effective/release, `last_reviewed`,
`review_cycle`, `next_review`, `related`, and the approved D9 `compliance_gate`); the D4 status enum
`planned|draft|published|needs_review`; **canonical-source guidance**; the profile's **document-type
section headings** as empty placeholders; and the minimum-viable page set. The Software template
documents the **Hybrid union** rule (D3) for node-10 areas; the Business-Ops template flags the
**AD-5 / UNFILLED compliance-reviewer** gate.

**They contain no** authored business policies, SOPs, regulated rules, client data, secrets, or
operational decisions.

## 3. Idempotency validation

| Pass | Check | Result |
|---|---|---|
| 1 (pre-create) | CQL for all node + template titles in 3WCO | **0 results** → safe to create |
| 2 (post-create) | Templates under node `01` (`parent = 28835861`) | **exactly 3**, one each |
| 2 (post-create) | All 8 nodes present among homepage children | **each exactly once**; all IDs resolve |
| Re-run simulation | Search-by-exact-title before create | would match the existing page and **create nothing** |

The provisioning approach is search-before-create and records the resulting page ID, so repeating it
is safe. **No duplicate pages were created.**

## 4. Existing Insurance pages — treatment

**Preserved, untouched.** Not renamed, moved, rewritten, unpublished, or stripped of AD-5 language.

| Page | ID | Status | Action |
|---|---|---|---|
| Insurance Operations — Release 0.10.0 (parent) | `28770305` | current | **Unchanged**, remains under homepage `21266602` |
| Insurance Commissions — Operating Procedure | `28803073` | current | Unchanged |
| Insurance Exceptions & Work Queues — Operating Procedure | `28835841` | current | Unchanged |
| Insurance Policyholder Portal — Operating Procedure | `28868609` | current | Unchanged |
| Insurance Reporting & Operations Dashboard — Operating Procedure | `28901377` | current | Unchanged |
| Insurance Integrations — Extension Points (Reference) | `28901397` | current | Unchanged |

**No movement was performed in P1** (movement is not essential this phase — the Insurance *area* page
under node 10 is not provisioned in P1). Node `10` records the plan to re-parent these under an
Insurance area page in a later phase, **preserving page IDs** and recording old/new parents.

## 5. Existing Benefits pages — treatment

**Preserved as drafts, untouched.** Verified `27951106` returns `status: draft`; `27983873` and
`27918338` are the same draft set (per `DOCUMENTATION_CROSSWALK.md` §2). Not published, not
rewritten. *(CQL title search returns 0 for these because Confluence search indexes only `current`
pages — the drafts are intact.)*

## 6. AD-5 boundary confirmation

- Published Insurance page body re-verified (`28901397`): the non-regulated release banner **and**
  the "§ Compliance boundary (AD-5)" section are **intact**.
- No regulated Insurance content was published or created.
- Every new node/template carries "No AD-5-regulated content" and the Business-Ops template + node
  30 state the **compliance-reviewer role remains UNFILLED (AD-5)**; regulated duties stay blocked.
- The D9 `compliance_gate` field is embedded in all three templates' front-matter for downstream
  enforcement (never `published` while gated).

## 7. Exceptions / deviations

1. **Source filename.** The task cited `docs/documentation-framework/02-TEMPLATE-LIBRARY.md`; the
   actual file is **`02-DOCUMENT-TYPE-TEMPLATES.md`** (deliverable 2). Used the actual file; content
   matches the intent (templates + profiles). No impact.
2. **Node title wording.** Used the **task's exact titles** (e.g. "10 · Client-Facing Operations",
   "40 · Cross-Platform & Shared"), which differ slightly from IA §1 ("Client-Facing Capabilities",
   "Cross-Platform / Shared"). Recommend treating the task titles as canonical or reconciling the IA
   wording in a later doc pass.
3. **Templates as pages, not native Space Templates.** The Confluence MCP can create pages but not
   native **Space Templates**. The three Area Shells are provisioned as **template pages** under the
   Page Template Library (node 01), ready to be cloned during area provisioning or promoted to Space
   Templates by an admin in the Confluence UI. Functionally sufficient for P1; noted for follow-up.
4. **Pre-existing 360OS/Atlas structure overlap (material).** The space already contains an organic
   capability-based operating manual (CAP-xxx pages + a "360OS Operations Home") that overlaps the
   framework **at the area level** (see §8). No existing page was modified; the framework nodes were
   still required (no node-level equivalent existed — pre-search returned 0). Reconciliation is
   deferred to later phases.

## 8. Unresolved issues

**A pre-existing "360OS / Atlas" operating structure coexists with the new framework skeleton.**
Discovered during idempotency pass 2 (homepage now has 32 direct children). It overlaps the framework
at the **area** level, not the node level:

| Legacy page (existing) | ID | Overlaps framework |
|---|---|---|
| 360OS Operations Home (`HOME-001`) | `24117290` | node `00 · Company Home` |
| 📐 360 Standards | `23199768` | node `01 · How This Manual Works` |
| 🗄️ Atlas Archive (legacy/duplicate store) | `25755689` | destination for superseded pages |
| 🧾 Tax Operations (`CAP-004`) | `23494657` | area `TAXOPS` (node 10) |
| ⚖️ Compliance (`CAP-005`) | `23560193` | area `CMP` (node 30) |
| 👥 HR / People Operations (`CAP-007`) | `23560201` | area `HR` (node 30) |
| 💰 Finance Operations (`CAP-009`) | `24510485` | area `ACCT` (node 30) |
| 📣 Marketing Operations (`CAP-010`) | `25034803` | area `MKT` (node 30) |
| 🤝 Vendor Management (`CAP-011`) | `24510566` | area `VEND` (node 30) |
| 💻 Technology Operations (`CAP-006`) | `24051794` | node 20 areas |
| 🛡️ Business Continuity (`CAP-012`) | `24510526` | area `DR` (node 20) |
| 💎 Client Experience / 👤 Client Lifecycle / 📈 Schwab / 📊 AssetMark | `25657365` / `23330817` / `23330825` / `23265282` | node 10 areas |
| 🏛️ Executive Management / ⚠️ Risk Management / 💼 Office Operations | `25493545` / `25886741` / `23625729` | GOV / node 30 |

**Implications:**
- This is a **third taxonomy** (CAP-xxx / emoji titles) alongside the crosswalk letters and the
  framework area codes — it **expands the D10 scope**, which previously assumed only 5 Insurance + 3
  Benefits pages existed. D10's migration must account for these live capability pages before P3.
- Whether "00 · Company Home" should supersede/merge with "360OS Operations Home", and "01" with
  "📐 360 Standards", is a reconciliation decision — **not made in P1** (no existing page altered).

**No action taken** — surfaced for a decision before P3 area provisioning / D10 migration.

## 9. Validation summary

| Check | Result |
|---|---|
| All 8 top-level nodes exist exactly once | ✅ |
| All 3 Area Shell templates exist exactly once | ✅ |
| All recorded page IDs resolve | ✅ |
| Published Insurance pages available & unchanged | ✅ (5 + parent, `current`) |
| Deferred Insurance pages remain unpublished | ✅ (7 register rows still draft; none published) |
| Benefits drafts remain drafts | ✅ (`27951106` verified draft) |
| AD-5 boundary language intact | ✅ (verified on `28901397`) |
| No duplicate pages created | ✅ (search-before-create; pass-2 confirms one each) |
| No repository files changed except this report | ✅ |
| `git diff --check` | ✅ clean |
| Working tree clean after commit | ✅ |

## 10. Recommendation for P2 / P3

1. **P2 (`governance/` Git tree)** can proceed independently — it is unaffected by the Confluence
   overlap and touches only the repository.
2. **Before P3 area provisioning and the D10 migration**, resolve the **360OS/Atlas reconciliation**:
   decide, per legacy capability page, *absorb into the framework node* vs *archive* (the existing
   "🗄️ Atlas Archive" `25755689` is the intended destination for superseded pages). Recommend a short
   **addendum to the D10 assessment/validation** covering the live CAP-xxx pages, since D10 was scoped
   before this structure was visible.
3. **Reconcile the two "home"/"standards" pages** ("00 · Company Home" ↔ "360OS Operations Home";
   "01 · How This Manual Works" ↔ "📐 360 Standards") — merge or designate one canonical — in that
   same reconciliation step, preserving IDs.
4. P1 objective is **met**: the eight approved nodes and three Area Shell templates are provisioned,
   idempotent, with existing Insurance/Benefits pages preserved and AD-5 boundaries intact.

---

**Stopping after P1.** Awaiting explicit approval before beginning P2 or P3 (and before the D10
migration, which should first absorb the 360OS/Atlas reconciliation noted in §8).
