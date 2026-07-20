# Client360 — Engineering Decisions (intent record)

A historical record of **why** the significant engineering choices were made — not how they are
implemented (see the architecture docs and code for that). The purpose is that a team who never
worked on this project can understand the reasoning, the alternatives, the accepted tradeoffs, and
when a decision should be reconsidered.

Each entry: **Decision · Context · Alternatives considered · Why chosen · Tradeoffs accepted ·
Long-term implications · Revisit?**

---

## ED-1 — Conservative canonical matching; ambiguity goes to a human
- **Decision:** Identity resolution auto-links/creates canonical people only when unambiguous
  (a unique or single exact email/phone match); anything ambiguous is left for a human in Match
  Review. No automatic identity **merge**.
- **Context:** Imported contacts must become one canonical client record without blending two real
  clients into one.
- **Alternatives:** Fuzzy auto-merge above a similarity threshold; ML-based entity resolution.
- **Why chosen:** A wrong merge co-mingles two clients' financial records — high-severity, hard to
  unwind, and a trust/compliance problem. Conservatism plus human review makes the failure mode
  "a human decides" instead of "silent corruption."
- **Tradeoffs:** More manual review work; single-source uniques are auto-created (accepted as low
  risk).
- **Long-term implications:** Throughput is bounded by human review at scale.
- **Revisit?** Only if review volume becomes a real bottleneck — and then only via **PD-2** (a
  business-set auto-merge threshold), never as an engineering default.

## ED-2 — Household derivation is a mechanism with an injected policy, off by default
- **Decision:** Ship a household-derivation engine that groups people by an injected policy; the
  default policy groups nothing. A candidate address-based policy exists but is not enabled.
- **Context:** The import data carries no explicit household identifier; the only signal is address.
- **Alternatives:** Auto-group by shared address on import; require manual households only.
- **Why chosen:** "What constitutes a household" is a firm/relationship judgment with privacy
  consequences (address-sharing ≠ same household). Engineering owns the mechanism; the business owns
  the policy. Building it to the policy boundary keeps the decision reversible and explicit.
- **Tradeoffs:** No automatic households until a policy is chosen (manual management meanwhile).
- **Long-term implications:** Any grouping rule plugs into the existing engine without redesign.
- **Revisit?** When the business sets **PD-1** (grouping signal + auto-apply vs. review).

## ED-3 — Human-review boundary for identity-altering actions
- **Decision:** Any action that changes who a record *is* (merge, household assignment) requires a
  human when ambiguous; the system never auto-performs an irreversible identity change on a guess.
- **Context:** Financial/PII records; regulated domain.
- **Alternatives:** Automate everything above confidence thresholds.
- **Why chosen:** Reversibility and accountability. A human decision is auditable and correctable;
  a silent automated merge is neither.
- **Tradeoffs:** Slower for ambiguous cases.
- **Revisit?** Tied to ED-1/PD-2.

## ED-4 — Import architecture: staging table, import-inert modules, in-transaction promotion
- **Decision:** Imports write `source_contacts` (a staging layer) and never overwrite canonical
  `people`; promotion runs in the same import transaction; importer modules do no work at import
  time (all side effects behind `main()`).
- **Context:** Earlier importers did work as an import side effect and wrote client data on module
  import, which broke tests and safety.
- **Alternatives:** Write directly to `people`; promote in a separate job.
- **Why chosen:** Separation of source-of-record (people, staff-editable) from source-of-import
  (source_contacts) means re-imports can't clobber staff edits; in-transaction promotion means an
  import either fully lands or fully rolls back; import-inert modules make the code testable and safe.
- **Tradeoffs:** A second concept (staging vs. canonical) to understand; promotion coupled to import.
- **Revisit?** If a streaming/CDC import model is ever needed.

## ED-5 — Audit + timeline are append-only and pervasive; audit metadata is PII-light
- **Decision:** Every user-facing write emits an immutable timeline event and an append-only audit
  event. Audit metadata records field **names**, not PII values.
- **Context:** Regulated books-and-records; supportability (trace who did what).
- **Alternatives:** Mutable history tables; audit only sensitive actions; log full values.
- **Why chosen:** Append-only is the compliance-grade, tamper-evident model; pervasive coverage means
  "everything is traceable" by default rather than per-feature opt-in; field-name-only keeps the
  audit trail from becoming a second copy of client PII.
- **Tradeoffs:** Storage growth (needs partitioning/archival at scale); no in-place edit of history.
- **Long-term implications:** Audit/timeline become the largest tables — a planned scaling concern.
- **Revisit?** Storage strategy (partitioning/tiering) before high volume; never the append-only rule.

## ED-6 — Security: capability + record scope, external IdP, dev-auth impossible in production
- **Decision:** Authorization is capability-based with record scoping and defense-in-depth (CSRF,
  append-only audit). Authentication is delegated to an external IdP. A development-only sign-in
  provider exists but cannot be enabled in production (double-guarded; startup fails fast if the
  toggle is set in production).
- **Context:** Regulated data; need per-user/role access and test/dev ergonomics without weakening prod.
- **Alternatives:** Build in-house auth; a single admin role; a test bypass that "shouldn't" run in prod.
- **Why chosen:** Own the authorization kernel (the moat, compliance-relevant) but not credential
  handling (delegate to the IdP). The dev provider gives E2E/local login with zero risk because
  production activation is structurally impossible, not merely discouraged.
- **Tradeoffs:** A hard runtime dependency on the IdP; capability model has a learning curve.
- **Revisit?** IdP coupling if multi-tenant SaaS is pursued; the kernel should never be weakened.

## ED-7 — Release philosophy: small, reversible, CI-green-before-merge, additive migrations
- **Decision:** Ship in small increments, one issue at a time, each with tests, behind a PR that
  merges only after CI is green. All migrations are additive and reversible; a single Alembic head is
  enforced; release/rollback/restore are guarded scripts.
- **Context:** A prior release merged with CI broken and a drifted changelog; this discipline exists
  to prevent that.
- **Alternatives:** Large feature branches; merge-then-fix; irreversible migrations for speed.
- **Why chosen:** A continuously shippable mainline and reversible changes make every step safe to
  deploy or undo. Small increments localize risk and keep review meaningful.
- **Tradeoffs:** More PR/CI overhead; occasional duplicate CI runs.
- **Revisit?** Not the principles; the CI ergonomics (e.g., duplicate runs) may be tuned.

## ED-8 — Build to the policy boundary; never invent business policy
- **Decision:** For every deferred business decision, build the full mechanism, UI, audit, tests, and
  documentation, leaving only the decision value unresolved — recorded in `PRODUCT_DECISIONS.md`.
- **Context:** Several capabilities (household grouping, auto-merge, comm metadata, regulated
  insurance/AD-5) hinge on business/compliance judgments engineering cannot make.
- **Alternatives:** Pick a default policy and ship it; block the feature entirely.
- **Why chosen:** Keeps engineering unblocked and the product decision explicit, reversible, and
  owned by the right person — no policy is smuggled in as an engineering default.
- **Tradeoffs:** Some built mechanisms sit unused until a decision is made (tested, not dead).
- **Revisit?** Each entry is revisited when its owner decides (PRODUCT_DECISIONS).

## ED-9 — Documentation philosophy: living governance docs, evidence-based reporting
- **Decision:** Maintain living governance docs (`RELEASE_READINESS`, `PROJECT_STATUS`,
  `PRODUCT_DECISIONS`, `V1_RELEASE_PLAN`, `RC_READINESS`, `USER_GUIDE`) and separate the four
  engineering categories (Application / Release / Operations / Product-Compliance). Never claim
  "production ready"/"pilot ready" without every applicable checklist item objectively verified.
- **Context:** Handoff durability and honest release decisions.
- **Alternatives:** One monolithic status doc; optimistic status reporting.
- **Why chosen:** Category separation prevents "implementation done" from being mistaken for
  "shippable"; evidence-based language prevents over-claiming a regulated system's readiness.
- **Tradeoffs:** Several documents overlap on readiness state (drift risk) — a known debt; multiple
  files to keep current.
- **Revisit?** Consolidate the overlapping readiness docs into one source of truth post-V1.0.

## ED-10 — Operational boundary: engineering builds mechanisms; operations executes
- **Decision:** Engineering builds and verifies release mechanisms in-repo (backup/restore rehearsal,
  readiness probe, smoke/rollback/deploy scripts, reproducible image); Operations executes them on
  real infrastructure (scheduled backups, deploys, monitoring wiring, SSO). Readiness is reported as
  verified-in-repo vs. execution-is-operational.
- **Context:** The repo cannot provision infrastructure; conflating the two led to over-claiming.
- **Alternatives:** Treat "runbook exists" as "done"; or attempt infra from the repo.
- **Why chosen:** A clean seam: another engineer can *run* the mechanisms from the repo; operators own
  the environment. It keeps the readiness picture honest.
- **Revisit?** If CI/CD is adopted, some operational steps move in-repo as automation.

## ED-11 — Testing strategy: service/route-level core, real-browser E2E via a dev provider
- **Decision:** The core suite tests at service/route level (fast, deterministic); browser E2E runs
  via Playwright authenticated by the dev-only provider, as an advisory (non-gating) workflow until
  proven stable. The suite uses a disposable, reset-first test database.
- **Context:** No HTTP client was available early; the SSR UI still needed real coverage; a shared DB
  caused order-dependent flakiness.
- **Alternatives:** Only unit tests; only heavy E2E; a shared mutable test DB.
- **Why chosen:** Service/route tests give speed and breadth; a thin real-browser layer catches
  render/redirect/auth issues unit tests can't; reset-first isolation removes cross-test contamination.
- **Tradeoffs:** E2E is advisory (must be promoted to a required check by an admin); reset-first costs
  ~1s per run.
- **Revisit?** Promote E2E to a required check once stable; add a coverage gate if desired.

## ED-12 — SQLAlchemy Core + dual table-management (accepted debt)
- **Decision:** Data access uses SQLAlchemy **Core** (explicit SQL), with some tables declared in
  `schema.py` and many created by hand-written migrations and reflected at runtime.
- **Context:** Financial domain where predictable SQL matters; the schema grew via migrations.
- **Alternatives:** The ORM; a single fully-declared metadata.
- **Why chosen:** Core keeps query behavior explicit and predictable. The declared/reflected split
  arose pragmatically as the schema evolved.
- **Tradeoffs:** Alembic autogenerate is **not** safe (declared ≠ full live schema); contributors must
  know which tier a table lives in. This is real, documented debt.
- **Long-term implications:** Onboarding friction and manual migration authoring.
- **Revisit?** **Yes** — document and converge the table-management model before the schema doubles;
  do not let it calcify.

---

_Deferred business/compliance decisions themselves live in [`PRODUCT_DECISIONS.md`](PRODUCT_DECISIONS.md);
this file records the engineering reasoning behind the mechanisms and boundaries around them._
