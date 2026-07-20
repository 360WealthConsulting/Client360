# Client360 — Product Decisions Register

Authoritative list of **deferred business / product-policy decisions**. Engineering has built the
mechanism, UI, audit trail, tests, and documentation for each; only the decision itself is
unresolved. Nothing here is a code gap — each is a policy the business must set.

Blocking level: **B0** = blocks production · **B1** = blocks a feature's activation (safe default
holds) · **B2** = enhancement, non-blocking.

_Last reviewed against `release/0.13.0`._

---

## PD-1 — Household auto-derivation grouping rule
- **Status:** Engine built (`app/services/household_derivation.py`): injected policy, dry-run,
  tests. **Not enabled** — default policy groups nothing.
- **Why engineering cannot decide:** which real-world signal constitutes "same household" is a
  firm business/relationship judgment with privacy consequences (wrongly grouping distinct clients
  co-mingles records). Not derivable from code.
- **Options & technical impact:**
  - **A. No derivation (current default).** Zero risk; households only created manually. No new value.
  - **B. Group by normalized address (line 1 + postal).** `group_by_normalized_address` already
    implemented. Groups cohabitants; risk of grouping unrelated people at the same building/PO box.
  - **C. Address + shared last name.** Lower false-positive rate; misses same-household different
    surnames (partners, dependents). Small code addition (compose a policy).
  - **D. Explicit source household field.** Most reliable, but the Wealthbox export in hand has **no**
    household field — not currently possible without a source change.
- **Current default behavior:** no auto-derivation; households are created manually only.
- **Decision owner:** Michael Shelton (business owner).
- **Blocking level:** **B1** (feature activation; safe default holds — not a production blocker).

## PD-2 — Automatic match-merge (whether to enable, and threshold)
- **Status:** Human-in-the-loop path fully built — Match Review "unresolved" queue, link/create
  resolution, promotion + backfill, all audited. Automatic merge is **not built** and **not enabled**.
- **Why engineering cannot decide:** auto-merging client identities without a human is a
  risk-tolerance and compliance judgment (a wrong merge blends two clients' records). The
  acceptable confidence threshold, and whether to allow auto-merge at all, are business calls.
- **Options & technical impact:**
  - **A. Human review only (current default).** Safest; every ambiguous case is decided by staff via
    the Match Review queue. No auto-merge code path exists.
  - **B. Auto-merge above a confidence threshold.** Would require building an auto-merge engine on
    the existing match-scoring/plan flow plus a threshold value; faster throughput, non-zero
    wrong-merge risk that scales with a lower threshold.
- **Current default behavior:** no automatic merges; all ambiguous cases go to human review.
- **Decision owner:** Michael Shelton (business owner).
- **Blocking level:** **B2** (human review already covers the need; auto-merge is an optional
  throughput enhancement).

## PD-3 — Communication metadata scope
- **Status:** Type, body, author, timestamp, and **direction** (inbound/outbound) shipped.
  Participants and duration are **not** modeled.
- **Why engineering cannot decide:** whether staff need structured participants/duration (vs. free
  text in the body) is a workflow/product preference.
- **Options & technical impact:**
  - **A. Keep current fields (default).** No change; participants/duration live in the note body if
    needed.
  - **B. Add structured participants + duration.** Additive `person_notes` columns + form fields +
    display; small, reversible.
- **Current default behavior:** type/body/author/time/direction only.
- **Decision owner:** Michael Shelton (product).
- **Blocking level:** **B2** (enhancement).

## PD-4 — Regulated insurance content (AD-5) — pre-existing compliance gate
- **Status:** Carried from prior releases. Regulated insurance functionality (suitability,
  replacement/1035, licensing validation) remains **deferred behind the AD-5 gate**; non-regulated
  plumbing only is built.
- **Why engineering cannot decide:** requires a **named, qualified compliance reviewer** and an
  approved sign-off artifact — a regulatory/compliance decision, not an engineering one.
- **Current default behavior:** regulated logic not built/enabled; tests assert its absence.
- **Decision owner:** **UNFILLED** — accountable compliance reviewer not yet appointed.
- **Blocking level:** **B0** for the regulated insurance scope (out of Sprint 2's CRM scope, tracked
  here for completeness).

---

### How to resolve a decision
Record the chosen option and owner sign-off here, then engineering enables the corresponding
mechanism behind it (policy value / feature flag) in a normal reviewed PR with tests.
