# D10 — Documentation Taxonomy Impact Assessment

_Informational only. Prepared for Release 0.11.0 at the request following P0 approval. **This
document changes nothing**: no taxonomy, no plan, no crosswalk, no Publication Register design, no
Confluence, no labels. It assesses the consequences of decision **D10** (reconcile the register to
the framework's area-code taxonomy) so the reviewer can choose **Keep / Adopt / Hybrid** before any
implementation. D10 remains **deferred and unimplemented**. Date 2026-07-17._

> Context: P0 approved **D1–D9**. **D10 was explicitly held** pending this assessment. Nothing here
> should be read as having applied D10.

---

## 1. Current taxonomy

Two taxonomies coexist in the repository today:

**(a) Crosswalk business-section lettering** — `docs/DOCUMENTATION_CROSSWALK.md` §1 "company-wide
manual section map" organizes the firm into **lettered business sections**:

| Letter | Section (as written in the crosswalk) |
|---|---|
| A | Executive Management |
| B | Sales and Marketing |
| C | Client Experience |
| D | Tax Operations |
| E | Wealth Management |
| F | Employee Benefits |
| H | Insurance Operations |
| I | Finance and Accounting |
| K | Compliance |
| L | Technology and Cybersecurity |
| … | (additional business sections as the manual grows) |

These letters are **document-internal organization** of the crosswalk; they are not code
identifiers, not labels, and not URL components.

**(b) Framework IA area codes** — `documentation-framework/01-INFORMATION-ARCHITECTURE.md` §3 defines
**26 area codes** under structural nodes `10/20/30/80`:

- **Node 10 (Software/Hybrid):** CLM360, TAXOPS, WLTH, INS, BEN, RET, CRM, WORK, DOC, RPT, AIA
- **Node 20 (Infrastructure):** M365, AD, NET, SRV, SEC, DR
- **Node 30 (Business Ops):** CMP, VEND, OFFICE, HR, ACCT, MKT
- **Node 80 (Libraries/Programs):** SOPLIB, TRAIN, RELMGMT
- Structural (not "areas"): 00·Company Home, 01·How This Manual Works, 40·Cross-Platform/Shared, 90·Registers & Governance

The IA, the templates (`page_id: <AREA>-<TYPE>`), the label scheme (`area:<code>`), and the register
schema (06 §2, `area: DR`) **all already key off area codes**. The letters exist only in the
crosswalk narrative.

## 2. Proposed taxonomy (D10)

Adopt the **framework IA area-code taxonomy** as the single taxonomy for the Publication Register,
labels, and page identifiers — plus the pseudo-areas **`SHARED`** (node 40) and **`GOV`** (node 90)
from D2 — and **map** the crosswalk's lettered sections onto it. One taxonomy across IA, templates,
labels, register, and (future) automation.

## 3. Mapping between the two

| Crosswalk letter → | Framework area code(s) | Node | Note |
|---|---|---|---|
| A · Executive Management | *(no software area)* → `GOV` / node 00 | 90/00 | Structural, not a capability area |
| B · Sales and Marketing | MKT (+ CRM where software-backed) | 30 (+10) | 1→2 (business + CRM software) |
| C · Client Experience | DOC, CLM360 (portal) | 10 | Spans portal/document surfaces |
| D · Tax Operations | TAXOPS | 10 | 1→1 (Hybrid) |
| E · Wealth Management | WLTH | 10 | 1→1 (Hybrid) |
| F · Employee Benefits | BEN (+ RET linked) | 10 | 1→1 (+ linked area) |
| H · Insurance Operations | INS | 10 | 1→1 (Hybrid); 5 published pages |
| I · Finance and Accounting | ACCT | 30 | 1→1 |
| K · Compliance | CMP | 30 | 1→1; AD-5-flagged |
| L · Technology and Cybersecurity | SEC, M365, AD, NET, SRV | 20 | **1→many** (infra split) |
| — | RET, WORK, RPT, AIA, VEND, OFFICE, HR, SOPLIB, TRAIN, RELMGMT | 10/30/80 | Framework areas with no distinct crosswalk letter yet |

**Complexity concentrates in two shapes:** 1→many (L → five infra codes) and letter→structural
(A → GOV/00). The remaining rows are 1→1. Framework areas without a current letter simply gain rows.

## 4. Confluence impact

**Low, and mostly pre-provisioning.** The space is **not yet node-provisioned** (P1 has not run);
only the **5 published 0.10.0 Insurance pages** (+ parent `28770305`) exist. Adopting area codes
*before* P1 means shells are created under codes from the start — no re-parenting of a populated
tree. Keeping letters, by contrast, would put the space (letters) at odds with the IA/templates/
labels (codes) that P1 provisions — a built-in inconsistency. Existing Insurance pages would live
under node 10 · Insurance (INS); Benefits drafts under BEN.

## 5. Crosswalk impact

**Low marginal cost.** Under **D1**, the crosswalk becomes a **generated view** of
`docs/registers/pages.yml`. Its section ordering/keying is regenerated regardless, so aligning it to
area codes happens **at generation time** rather than as a separate hand migration. The letter table
can be retained as a transitional "legend" for traceability.

## 6. Publication Register impact

**Positive / consolidating.** The register schema (06 §2) already uses `area: <code>`. Adopting
codes makes register + IA + labels consistent and **removes risk R13** (register-vs-crosswalk
taxonomy mismatch). Keeping letters would force the register to either use letters (diverging from
06 §2) or use codes while the crosswalk uses letters (the mismatch itself).

## 7. Existing page impact

**Minimal.** 5 published Insurance pages + 3 Benefits drafts + 7 held Insurance drafts. Adopting
codes = assigning `area:INS`/`area:BEN` labels and node parentage. Page **bodies are unchanged**;
the 0.10.0 published content and its AD-5 boundaries are untouched (release-isolation preserved).

## 8. URL impact

**Low.** Confluence canonical URLs are **numeric-ID based** (`/spaces/3WCO/pages/28803073/<slug>`);
the title slug is cosmetic and the numeric ID is immutable. Moving a page under a node changes its
**breadcrumb**, not its `/pages/<id>/` URL. The five 0.10.0 page URLs remain valid. No external
system references the crosswalk letters, so no inbound links break.

## 9. Label impact

**Low, additive.** IA §3 labels are `area:<code>`, `type:<code>`, `profile:…`, etc. Adopting codes
means labels are applied as intended (`area:INS`). Existing pages currently carry **no** formal
`area:` labels (they were published with prose banners), so this is additive, not a relabel-and-
remove. Letters were never a label value.

## 10. Automation impact

**Positive.** Phase-E `docs_sync.py` keys off `area`/`page_id` (codes) in `pages.yml`. Adopting codes
now means automation is consistent from first build; keeping letters would require a permanent
letter↔code translation layer in the sync tool and the docs gate. **Adopting reduces future
automation complexity.**

## 11. Search impact

**Neutral-to-positive.** CQL/label search by `area:INS` is more precise than a free letter. Full-text
search is unaffected (page bodies unchanged). No degradation; a modest precision gain once labels
exist.

## 12. Migration complexity

**Low–Moderate, and front-loaded to the cheapest moment.** Drivers:
- **Low** because the space is unprovisioned and only 5 pages exist — no populated-tree migration.
- **Moderate** for the one-time **letter→code mapping**, concentrated in the 1→many (L → infra) and
  letter→structural (A → GOV/00) cases.
- Folding the mapping into **P3** (register promotion, when the crosswalk is regenerated) means the
  incremental effort is the mapping table itself, not a separate migration project.

## 13. Backward compatibility

**High.** Letters are internal crosswalk organization with **no code, URL, or external consumer**
dependence. The 0.10.0 pages/tag/release don't reference them. A transitional legend (letter→code)
preserves human traceability during the switch. Nothing downstream breaks.

## 14. Rollback strategy

**Low-risk, metadata-only.** Taxonomy lives in `pages.yml` (register) + Confluence labels/parentage —
**not** in page content or IDs. Rollback =
1. `git revert` the `pages.yml` + generated-crosswalk change (restores the prior taxonomy verbatim);
2. bulk-remove/re-apply `area:` labels via the Atlassian MCP;
3. re-parent the ≤8 affected pages (breadcrumb only; URLs stable).
Because IDs/URLs/bodies never change, rollback is a regeneration + relabel operation, reversible from
git history — not a content migration.

## 15. Recommendation

**ADOPT the framework area-code taxonomy — sequenced into P3, with a retained letter→code mapping
legend for traceability.**

Rationale:
- The IA, templates, `area:` labels, and the register schema (06 §2) **already assume area codes**;
  the letters are the outlier. "Keep" perpetuates the register-vs-crosswalk mismatch (**R13**) and
  fights the framework the rest of Phase A implements.
- Cost is **lowest now**: space unprovisioned, only 5 pages, crosswalk regenerated anyway under D1.
- URL/existing-page/backward-compat impact is **low**; rollback is metadata-only and git-reversible.

**Not recommended as an end-state — Hybrid (maintain both taxonomies).** A permanent dual taxonomy
defeats the single-register principle and adds an automation translation layer forever. However, a
**transitional** hybrid — the letter→code legend retained during and shortly after P3 — is prudent
and is included in the Adopt recommendation.

**Not recommended — Keep (letters).** It leaves register/IA/labels/automation inconsistent and only
defers the reconciliation to a more expensive moment (after the tree is populated).

### Suggested execution (only if the reviewer approves Adopt — not done here)
1. Approve D10 explicitly (it remains deferred until then).
2. Fold the letter→code mapping (§3) into the P3 register-promotion step, as the crosswalk is
   generated from `pages.yml`.
3. Apply `area:<code>` labels/parentage during P1 provisioning for the ≤8 existing pages.
4. Retain the mapping legend in the crosswalk for one release as a traceability aid.

_This assessment is advisory. No action is taken until D10 is separately approved._
