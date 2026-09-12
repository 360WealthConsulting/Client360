# Changelog

All notable Client360 releases are documented here.

## [Unreleased]

### Added
- **Canonical document publication — a client can finally read their own ingested documents, with no second copy of a single byte.** The client portal read `vault_documents` and nothing else, while every importer — Drake, TaxDome, SharePoint — wrote to the canonical `documents` table. The two stores shared no rows, so a correctly owned, correctly filed, active client document was structurally invisible to the client it belonged to: in production, 678 Drake and 20,818 TaxDome documents, none of them blocked by ownership, lifecycle or a visibility flag, and none of them reachable. The obvious fix, copying files into the vault, is the wrong one — it duplicates every byte, forks the OCR/classification/version pipeline that only the canonical row has, and creates a second row whose ownership can drift from the first, which is exactly what ADR-072 exists to prevent. **A publication is a reference plus a decision** (migration `docpub01`): `document_publications` names a canonical `document_id`, an audience, the `client_visible` decision, where that decision came from, the document type and tax year as decided, who made it, and how it was withdrawn. No file moves, no document row is created or modified, and there is no endpoint on the new router that accepts a file. **Ownership is not visibility, and this is the change that separates them.** `documents.person_id` says who a file belongs to; it has never said who may read it, and it cannot, because the canonical resolver deduplicates by content hash and fills only NULL ownership — so one row can legitimately be the same file for two unrelated clients while carrying a single owner. Production already contains 320 such rows across 46 content groups, 232 of them spanning different households. Client access therefore derives ONLY from a publication row, publishing the same document to two audiences creates two independent grants, and revoking one leaves the other standing. **Person, household and organization are separate audiences** with separate anchors, enforced by a CHECK so a row can never be ambiguous about who it addresses, and by partial unique indexes so one audience holds at most one live publication of a document while a revoked row frees the slot for a fresh, separately audited decision. A household publication reaches a member whose grant names that household; a person publication reaches that person and nobody else. **Every exclusion lives in one query** — revoked publication, archived publication, withdrawn visibility, deleted canonical document, archived canonical document — so the list and the download cannot disagree, and archiving or deleting a document withdraws client access by itself without anyone remembering to revoke. Downloads are keyed on the PUBLICATION id, not the document id, so the identifier a client holds names a grant made to them rather than a shared object; every resource-level refusal returns the same generic 404, matching the vault route's denial contract. The portal's Download link now comes from the row rather than being hard-coded in the template: the old literal was a real route, but the VAULT one, and a template cannot tell which of two stores a row came from. **Authority reuses `vault.manage`** — the capability that already governs "may this person decide what a client sees" — bounded by record scope on the AUDIENCE, so no new capability is introduced and neither gate substitutes for the other. Publish, revoke, archive and every visibility change write to an append-only ledger whose `document_id` carries no foreign key, so deleting a document cannot erase the record that it was once client-visible. A **read-only preview service** bands the corpus for review — proposed client-visible, proposed staff-only, review-required, cross-client-content conflict, missing audience mapping — under three rules it enforces rather than describes: only an explicit classification can propose client-visible (a filename may push toward staff-only and never toward visible), source system is not an input at all, and a contested attribution outranks any classification. It proposes and never publishes, and there is deliberately no endpoint that applies its output. The migration's downgrade **refuses while live client-visible publications exist**, so a schema rollback can never be a silent withdrawal of client access; revoke first, which is an audited decision, then roll back. `app/safety.py` additionally refuses `client360` and `client360_test` outright, because a suffix rule is a shape check and cannot express "that database is spoken for". 109 new tests. Nothing was published, no production visibility changed, and no production data was written.
- **An authoritative source answers once: durable source-to-owner mappings, and one review per source client.** These were the two blockers holding activation of the continuous document pipeline, and they are the same mistake seen twice — treating a question about a CLIENT as a question about a DOCUMENT. **Mappings.** Drake identifies a client by client id and TaxDome by account; neither identifies them by name. The pipeline nonetheless re-derived the owner for every document, every time, which means a folder rename, a typo or an OCR result could silently move a client's paperwork — exactly the failure an authoritative lane exists to prevent. Now the stable identity is resolved once and persisted, and every later document carrying it inherits the owner without any inference at all. `ownership.resolve` consults the mapping BEFORE anything looks at content, so a verified answer cannot be overridden by a confident-looking filename. **The mapping store is not new.** `folder_resolution_decisions` already was exactly this: a subject-generic `(subject_system, subject_type, subject_key)` identity with `display_name` kept separate, a PARTIAL UNIQUE index admitting one ACTIVE decision per subject, append-only supersession (`active`/`superseded_at`/`superseded_by`) so a correction is versioned rather than an overwrite, a fail-closed CHECK tying each decision to a matching entity type, and a service that validates the target entity EXISTS and refuses to change an active decision without an explicit `supersede=True`. Every requirement a durable mapping has, it already satisfied; a second table would have meant two answers to "who owns this source identity" and an eventual argument about which was right. The pipeline writes only `link_*` decisions, never a `create_*`, and never supersedes — changing an established mapping stays a human act, because an automated writer that can supersede is one that can silently move a client's documents. **Reviews.** `docpipe02` adds `document_pipeline_source_reviews` keyed on the same subject triple, with a partial unique index admitting ONE OPEN review per identity, plus a membership table so documents join it. On the production corpus 474 conflicts come from 18 folders and one folder accounts for 121 documents; document-level rows put 474 items in front of a reviewer for 18 decisions, 121 of them the same decision restated, and a reviewer shown the same question 121 times stops reading it. Resolving records the decision once, persists it as a mapping so future documents skip review entirely, and applies it ONLY to documents still genuinely unowned — an existing owner is never touched, and the canonical write is never asked to overwrite one. The client label is stored once on the review instead of copied onto every affected document row. `docpipe01` is not modified: SharePoint evidence has no stable client key in the general case, so it keeps document-level review and that table keeps serving it. The membership table's reference to `documents` is registered as `dedup_keyed` on `review_id` — repointed on merge, redundant rows dropped, memberships of other reviews carried across.
- **One definition of "a pipeline may act on this document" (`document_platform.lifecycle.live_document_clause`).** The continuous pipeline originally selected work with `status <> 'deleted'`, the same single-column filter this module was created to stamp out. Against the production corpus of 121,865 documents that predicate accepted **50 rows the firm had already retired**: 49 carrying `deleted_at` AND `archived = true` while `status` still read `'active'` — the half-written soft deletes the module's own header describes, left behind when a merge run is interrupted between its two statements — and 1 archived through `services.documents.archive_document`, which stamps `archived`/`archived_at` and never touches `status`. A read surface getting this wrong shows a row it should not; a pipeline getting it wrong spends an OCR engine on a retired document and can then write an owner onto paperwork the firm has put away. So the four conditions — `status = 'active'`, `deleted_at IS NULL`, `archived = false`, `archived_at IS NULL` — are defined once and imported by discovery, by the extract stage's re-check, and by the read-only planner. It is deliberately **stricter** than the existing `active_unarchived_clause`: it requires `status = 'active'` positively, so a status nobody has thought about yet (a future `quarantined`, `legal_hold`) is excluded by default rather than silently processed, and it checks `archived_at` as well as `archived`, because a writer must not be the thing that discovers the two archive markers disagreed. `tests/test_live_document_predicate.py` pins each condition separately — a test of the conjunction alone would still pass if one condition were dropped and another happened to reject the fixture — and pins the SQL clause and its Python counterpart to the same answer on every combination.
- **Read-only full-corpus ownership plan — what the pipeline would decide, before it decides it (`scripts/plan_document_ownership.py`).** Turning a pipeline loose on a client corpus is a decision, and the numbers that inform it — how many documents each lane would claim, how many would link cleanly, how many contradict an owner the firm already recorded, and how many land on a person's desk — should be readable BEFORE anything is enabled rather than discovered afterwards. **Read-only is enforced by the server, not by this file**: the connection sets `default_transaction_read_only=on`, so PostgreSQL refuses any INSERT/UPDATE/DELETE/DDL from that session and a bug in the planner can crash but cannot write. The check runs and prints at startup, so the operator sees the guard rather than trusting it. **It does not compete with a running OCR sweep**: it never opens a document to extract text (every judgement comes from the database — filename, folder path, source system, and whatever OCR text has already been extracted), it takes no advisory lock, claims no document, and drops itself to below-normal process priority so the OCR workers keep the CPU. A document whose OCR has not finished is reported as awaiting OCR, not forced through an engine and not counted as a failure. **The plan predicts the pipeline** rather than approximating it: each lane calls the same function the runtime lane calls (`propose_drake_document_owner`, `taxdome_drive.resolve_folder`, `analyze_identity`), the confidence-to-outcome mapping is imported from `document_pipeline_continuous.ownership` instead of restated, and cached OCR text is truncated in SQL to the same `_MAX_TEXT_CHARS` the extractor applies — scoring on evidence the pipeline will never see would predict matches it will never make. Drake and TaxDome are treated as authoritative with no corroboration requirement, exactly as the runtime lanes treat them. **No corpus cap**: there is no `--limit`, the walk ends when the corpus ends, and `--chunk-size` exists so a crash costs one chunk — the checkpoint records the last document id completed and `--resume` (the default) continues from it. Reports unique high-confidence matches (documents and distinct owners), ambiguous matches, conflicts with existing ownership, and unmatched documents per lane, plus an inventory of unsupported types, zero-byte files, OCR timeouts, extraction failures and missing source files.
- **Continuous document pipeline — a server-side service that keeps the document corpus processed, with no interactive session of any kind (code, tests and documentation only; nothing installed, migrated or started).** The existing document paths are SWEEPS: `app/jobs/ocr_runner.py` and `document_pipeline.run_batch` each re-select candidates from the top of the corpus and work forward. That is the right shape for a one-off migration and the wrong shape for a corpus that is continuously fed — a sweep cannot resume mid-corpus after a restart, it re-reads what it already finished, it takes a `limit` that silently truncates (30 here, 200 there, 500 in the review queue) and a truncated sweep looks exactly like a finished one, and it holds no per-document claim, so two runs started a minute apart process the same document twice. This adds the durable state those sweeps do not have and ORCHESTRATES them rather than replacing them: extraction, OCR, classification, matching and the ownership rules each still live in exactly one place, and it is not in this package. **One row per document is the whole design.** `document_pipeline_tasks.document_id` is UNIQUE, and a worker takes it with `FOR UPDATE SKIP LOCKED` in the same statement that stamps `lease_owner`/`lease_expires_at` — so "two workers never process the same document" is a database invariant rather than a convention, and a worker that dies does not strand its documents, because the lease lapses and any other worker may take them. The stage is persisted the moment it completes, which is what makes a restart RESUME: a process killed during classification comes back to a task already past extraction and OCR and re-runs only the stage that did not finish. **The backlog is never capped.** Discovery's `page_size` paginates the walk and nothing more; it keeps requesting pages until one comes back short, committing each page separately and walking `documents.id` forward from a persisted cursor, so a crash costs one page and the next run resumes rather than re-scanning. Changed documents are found by comparing a task's `content_sha256` to the document's current hash — which is also exactly the test that makes re-processing an UNCHANGED document impossible, so idempotency and change detection are one mechanism seen from two sides. **Four stages, each delegating.** `extract` pulls embedded text (PDF text layer, Excel, Word, plaintext) and, when it succeeds, SKIPS OCR entirely — the single biggest saving, because most of a firm's corpus is born-digital; `ocr` first looks for a byte-identical document whose text is already extracted (SHA-256) and copies it, so the second filing of the same W-2 costs one row rather than minutes of engine time; `classify` runs the existing analysis pipeline; `ownership` applies the lanes. **Three ownership lanes, in order of authority, first applicable lane decides.** Drake identity attribution (taxpayer/spouse identifier hashes) is AUTHORITATIVE and a Drake HOLD never falls through to weaker evidence; the TaxDome account/folder mapping is AUTHORITATIVE and an unresolved folder goes to review with candidate people attached rather than to a content guess; SharePoint is EVIDENCE (path, filename, OCR text, identity, address, account, household) where only HIGH links and MEDIUM/AMBIGUOUS are the ambiguous documents the single review queue exists for. A no-match is UNRESOLVED, not review — no evidence is not ambiguity, and a review queue full of documents nobody can decide is unusable. **Ownership is never overwritten, guarded twice:** every link goes through `households.resolve_document_ownership`, whose UPDATE re-checks all-NULL in the same statement, and a lane that contradicts a stored owner opens an `ownership_conflict` review instead of changing anything. Permanent failures (an encrypted PDF, a deleted source) leave the queue on the FIRST occurrence for a visible blocker queue — retrying an encrypted PDF five times is five ways of learning the same thing — while transient ones back off exponentially and are retried; anything unrecognised is treated as transient, because a wrongly-transient failure costs a few retries and a wrongly-permanent one silently drops a document. **It yields to the existing sweeps rather than fighting them:** the OCR stage READS the sweeps' PostgreSQL advisory lock without taking it and defers while it is held (taking it would serialise the whole worker pool behind one connection, and the per-document leases already provide mutual exclusion among pipeline workers). Backpressure asks before every claim — CPU, memory, and connection-pool headroom that protects the staff-facing application, not the pipeline — and psutil is optional: its absence is reported honestly and the system gates are SKIPPED rather than guessed, because a fabricated 0% reading is worse than no reading. Operations get the nine numbers that matter (backlog, running, completed, linked, review, blocked, failed, throughput, last heartbeat) through a CLI and three read-only endpoints gated on the existing `documents.view` capability — no new capability is seeded — plus stall detection that distinguishes a wedged worker from one grinding through a 400-page scan (the heartbeat advances DURING a document) and refuses to call a finished backlog an incident. **Ships OFF** (`DOCUMENT_PIPELINE_ENABLED`, default false), so merging it changes no runtime behaviour on any host. Additive migration `docpipe01` (five tables, no change to any existing table), install/start/stop/resume/rollback in `docs/CONTINUOUS_DOCUMENT_PIPELINE.md`, a Windows service installer in `deploy/windows/Install-DocumentPipelineService.ps1`, and tests covering the queue, discovery, backpressure, the stages, the lanes, concurrency, restart-resume and failure-retry.
- **Continuous document pipeline — a server-side service that keeps the document corpus processed, with no interactive session of any kind (code, tests and documentation only; nothing installed, migrated or started).** The existing document paths are SWEEPS: `app/jobs/ocr_runner.py` and `document_pipeline.run_batch` each re-select candidates from the top of the corpus and work forward. That is the right shape for a one-off migration and the wrong shape for a corpus that is continuously fed — a sweep cannot resume mid-corpus after a restart, it re-reads what it already finished, it takes a `limit` that silently truncates (30 here, 200 there, 500 in the review queue) and a truncated sweep looks exactly like a finished one, and it holds no per-document claim, so two runs started a minute apart process the same document twice. This adds the durable state those sweeps do not have and ORCHESTRATES them rather than replacing them: extraction, OCR, classification, matching and the ownership rules each still live in exactly one place, and it is not in this package. **One row per document is the whole design.** `document_pipeline_tasks.document_id` is UNIQUE, and a worker takes it with `FOR UPDATE SKIP LOCKED` in the same statement that stamps `lease_owner`/`lease_expires_at` — so "two workers never process the same document" is a database invariant rather than a convention, and a worker that dies does not strand its documents, because the lease lapses and any other worker may take them. The stage is persisted the moment it completes, which is what makes a restart RESUME: a process killed during classification comes back to a task already past extraction and OCR and re-runs only the stage that did not finish. **The backlog is never capped.** Discovery's `page_size` paginates the walk and nothing more; it keeps requesting pages until one comes back short, committing each page separately and walking `documents.id` forward from a persisted cursor, so a crash costs one page and the next run resumes rather than re-scanning. Changed documents are found by comparing a task's `content_sha256` to the document's current hash — which is also exactly the test that makes re-processing an UNCHANGED document impossible, so idempotency and change detection are one mechanism seen from two sides. **Four stages, each delegating.** `extract` pulls embedded text (PDF text layer, Excel, Word, plaintext) and, when it succeeds, SKIPS OCR entirely — the single biggest saving, because most of a firm's corpus is born-digital; `ocr` first looks for a byte-identical document whose text is already extracted (SHA-256) and copies it, so the second filing of the same W-2 costs one row rather than minutes of engine time; `classify` runs the existing analysis pipeline; `ownership` applies the lanes. **Three ownership lanes, in order of authority, first applicable lane decides.** Drake identity attribution (taxpayer/spouse identifier hashes) is AUTHORITATIVE and a Drake HOLD never falls through to weaker evidence; the TaxDome account/folder mapping is AUTHORITATIVE and an unresolved folder goes to review with candidate people attached rather than to a content guess; SharePoint is EVIDENCE (path, filename, OCR text, identity, address, account, household) where only HIGH links and MEDIUM/AMBIGUOUS are the ambiguous documents the single review queue exists for. A no-match is UNRESOLVED, not review — no evidence is not ambiguity, and a review queue full of documents nobody can decide is unusable. **Ownership is never overwritten, guarded twice:** every link goes through `households.resolve_document_ownership`, whose UPDATE re-checks all-NULL in the same statement, and a lane that contradicts a stored owner opens an `ownership_conflict` review instead of changing anything. Permanent failures (an encrypted PDF, a deleted source) leave the queue on the FIRST occurrence for a visible blocker queue — retrying an encrypted PDF five times is five ways of learning the same thing — while transient ones back off exponentially and are retried; anything unrecognised is treated as transient, because a wrongly-transient failure costs a few retries and a wrongly-permanent one silently drops a document. **It yields to the existing sweeps rather than fighting them:** the OCR stage READS the sweeps' PostgreSQL advisory lock without taking it and defers while it is held (taking it would serialise the whole worker pool behind one connection, and the per-document leases already provide mutual exclusion among pipeline workers). Backpressure asks before every claim — CPU, memory, and connection-pool headroom that protects the staff-facing application, not the pipeline — and psutil is optional: its absence is reported honestly and the system gates are SKIPPED rather than guessed, because a fabricated 0% reading is worse than no reading. Operations get the nine numbers that matter (backlog, running, completed, linked, review, blocked, failed, throughput, last heartbeat) through a CLI and three read-only endpoints gated on the existing `documents.view` capability — no new capability is seeded — plus stall detection that distinguishes a wedged worker from one grinding through a 400-page scan (the heartbeat advances DURING a document) and refuses to call a finished backlog an incident. **Ships OFF** (`DOCUMENT_PIPELINE_ENABLED`, default false), so merging it changes no runtime behaviour on any host. Additive migration `docpipe01` (five tables, no change to any existing table), install/start/stop/resume/rollback in `docs/CONTINUOUS_DOCUMENT_PIPELINE.md`, a Windows service installer in `deploy/windows/Install-DocumentPipelineService.ps1`, and 91 tests covering the queue, discovery, backpressure, the stages, the lanes, concurrency, restart-resume and failure-retry.
- **3CX Phone System v20 custom-CRM connector — caller identification, click-to-call, and idempotent call journaling (code and tests only; nothing installed on the PBX).** Client360 answers two questions for the phone system: who is calling this number, so 3CX opens the client's profile on the answering advisor's screen, and this call just ended, so it lands in the client's communication history beside their email and text. It is the SERVER half only — Client360 never calls 3CX, holds no 3CX credential, and reads no recording, transcript, summary or sentiment, none of which the template even asks for; a call journal records THAT a call happened, and the moment it holds what was said it becomes a different artefact under a different retention rule. **No migration and no schema change**: `phone_log` has been a legal `communication_messages.channel` since the communications platform shipped and `communication_message_sources` already enforces `UNIQUE (source_system, source_external_id)`, so a call lands in the same conversation ledger as every other channel, under the same retention and authorization, and de-duplication is enforced by the database rather than by application logic. Two endpoints, both POST — a GET would put a client's number in the request line, where every access log and proxy along the path records it. **Authentication is a dedicated integration secret and nothing else.** 3CX stores template parameters as readable configuration, so whatever goes there is disclosed to every PBX administrator and replayed on every call; a Client360 login there would hand the application to the phone system. The secret is compared with `hmac.compare_digest`, grants only these two endpoints, and is revoked by changing one environment variable. Both paths join `/mcp` and the SharePoint webhook in `PUBLIC_EXACT` — "no session cookie required", not "unauthenticated": the route authenticates every request before any query runs, and because the endpoints honour no ambient credential a browser cannot be made to call them with a signed-in user's authority. Both **404 rather than 401** unless the connector is switched on *and* a secret of at least 32 characters is configured, so a probe learns nothing about whether this deployment has a telephony surface and a half-finished rollout cannot leave an unauthenticated surface live. **Lookup is exact-match or nothing.** A screen-pop is acted on before anyone speaks, so a wrong answer is worse than no answer: an advisor who opens Jane Smith's profile and greets the caller by her name has disclosed that Jane is a client of this firm to whoever actually rang. Matching is equality against `people.normalized_phone` under the repository's one phone convention — no prefix, suffix, substring, fuzzy or last-N-digits relaxation and no fallback to name or email — and an inactive record counts as no match, so a closed relationship is never presented as a live one. Zero matches, several matches and an inactive match all return an EMPTY `contacts` array carrying a count and nothing else; the template's `<Rule Type="Any">contacts</Rule>` does not fire on an empty array, so "never pop on an ambiguous number" is a property of the response shape rather than of 3CX-side configuration anyone could edit. Unlike SMS ingestion, a couple sharing a mobile is deliberately NOT resolved to their household: a household has no single profile to pop and no single name to greet. **De-duplication works around a real vendor gap, and says so.** 3CX v20's `ReportCall` scenario exposes no call identifier — the documented variables are `[CallType]`, `[Number]`, `[Name]`, `[Agent]`, `[Duration]`, `[DateTime]`, `[CallStartTimeLocal]` and `[CallStartTimeUTC]`, and 3CX support states a call id is available from the CDR only — so writing `[CallID]` into a template would render as an empty string and silently destroy idempotency. When no id is supplied the key is a SHA-256 over the tuple that identifies a call anyway (start instant to the second, agent extension, normalized number, direction): re-reporting the same call writes nothing, while two genuinely different calls would have to share all four to collide, which is the same call reported twice. It is hashed rather than concatenated so the stored identifier — which appears in query output and exports — does not itself contain a phone number, and every row records which rule produced its key (`identity_kind`) so nobody later mistakes a derived identity for a vendor-guaranteed one. The endpoint already accepts and prefers a `call_id`, so a future 3CX release or a CDR-driven poster needs no server change. Only completed inbound and outbound calls are journaled; `Missed` and `Notanswered` are refused by the endpoint *and* skipped by the template, because admitting them would quietly turn "calls with this client" into "call attempts". A client id the PBX hands back is CHECKED against a fresh lookup rather than trusted — without that, anyone holding the secret could file a call against any client in the book — and an unmatched or ambiguous caller is accepted and not filed (HTTP 200, `journaled: false`) rather than rejected, since there is nothing for the PBX to retry and a 4xx would only make it keep trying. **Full phone numbers reach no log line, audit entry, subject line, error body or dedup key**: everything human-readable carries the last-four form, and the audit chain records the masked number, match count and outcome, because a trail holding every number the PBX ever asked about would *be* a call-detail record. The XML contract was **verified, not invented** — every element, attribute, scenario id, output type and variable comes from 3CX's published server-side CRM template specification and a shipping vendor template, including that a JSON body is built from `<PostValues>` with `RequestEncoding="Json"` (mutually exclusive with the `Message` attribute, and `RequestContentType` left empty) and that `[[CallStartTimeUTC].ToString("…")]` is the confirmed date-formatting syntax. The template is rendered from the live endpoint constants rather than checked in by hand, and a test fails if the operator-facing copy in `deploy/3cx/` drifts from it, if a variable outside the documented `ReportCall` set appears, or if the JSON paths it reads are not the ones the lookup returns — otherwise a renamed field would ship as a silent absence of screen-pops on a Monday morning. The client profile's phone number is now a click-to-call link built by a shared `dial` Jinja filter rather than by concatenating `tel:` with a stored number, because records hold whatever punctuation was typed, URI handlers disagree about what they strip, and 3CX needs an unambiguous country code; an international number keeps its own prefix rather than being guessed at. The repository's phone normalizer moved from `sms_ingest` into a shared `phone_numbers` module and is re-exported, so its warning against a second "better" normalizer stays true now that it has a second caller. 77 new tests. Nothing has been uploaded to 3CX, no 3CX credential was created, and no production data was written. See `docs/THREECX_INTEGRATION.md`.
- **Strict-safe document ownership BATCH 5 — a guarded apply and scoped rollback for the 55 documents the refreshed owner-proposal engine newly qualified (code and tests only; nothing applied).** The 2026-09-10 owner-proposal refresh re-derived 29,890 facts and left 1,256 HIGH proposals in that cohort, of which exactly 55 clear the canonical strict-safe rule; the other 2,009 current HIGH rows are refused (1,042 non-person targets, 649 already owned, 223 short of two corroborators, 53 archived or deleted, 21 organization scope, 19 household scope, 2 resting on shared-value contact evidence). Batch 1's apply could not carry them: its approved shape is a record of what a human reviewed on 2026-09-05 — 541 rows, composition `{3: 104, 2: 437}`, 205 distinct people — and editing those constants to fit a different batch would make the gate prove nothing, so batch 5 gets its own identity, constants and snapshot root exactly as batches 2, 3 and 4 each did. The structural difference from batch 1 is that **the manifest is the only source of rows**: batch 1 recomputed `build_plan` and applied what came back, whereas batch 5 applies the frozen 55 or nothing, and `build_plan` is recomputed purely as a gate — it can remove a row from consideration, never add one, so a document that qualifies today but was never reviewed cannot reach a write (`test_qualifying_document_outside_the_manifest_is_never_assigned`). Each row is locked `FOR UPDATE` and re-proved under the lock against four fingerprints recorded at freeze time — of the document, the `owner_proposal` fact (id *and* version), the classification row, and the target person — so ownership is not merely "still unowned" but "still exactly the state a human reviewed": a rename, a re-proposal, a re-classification or a changed person email each abort the whole batch before the first write. The mutation itself goes through `households.resolve_document_ownership(..., conn=...)`, the canonical single-document write path, so batch 5 inherits its atomic `WHERE all-NULL AND NOT permanent-reject` re-check and its `document.ownership_resolved` audit event rather than restating ownership rules; this script contributes transaction policy, locking and refusals only. The expected write shape is exactly 55 `documents.person_id` updates and 55 audit events, with **zero** writes to proposal facts, classifications, people, households, relationship entities, source links, sources or OCR — each asserted by before/after fingerprints inside the transaction, and the deferral clauses that can move `review_status`/`tags` are proved to stay no-ops for all 55. Batch 5 also deliberately depends on neither of two known defects in the shared corroborator code, which are **not** repaired here: the refreshed engine emits `✓ street address matched` while the canonical helper still recognises only the retired `✓ address/ZIP matched` (so address credit is unreachable for refreshed facts, suppressing 10 otherwise-eligible rows), and an email/phone line tagged `(shared value — context only)` is still counted as a corroborator by that helper. All 55 rows rest on exact name + matched email + matched phone with no address, ZIP or shared-value credit whatsoever, and `forbidden_evidence` enforces that as a hard gate rather than leaving it a property of today's data. Rollback is scoped to one committed snapshot, which records each row's pre-image, post-image, ownership audit id and authorising fingerprints, is hashed **before** the commit, and is only replayable once the apply drops a receipt marking the transaction committed — a snapshot that outlived a rolled-back transaction is refused rather than "restoring" a state that never changed. It fails closed if ownership moved or another owner scope appeared, restores `review_status` and `tags` from the record rather than assuming NULL, retains the historical `document.ownership_resolved` events (the ledger is append-only at the database level) and appends a compensating `document.ownership_rollback` event naming the audit id it reverses. Dry run is the default for both scripts, `--apply` additionally requires `--actor-user-id` and the phrase `APPLY-STRICT-SAFE-OWNERSHIP-BATCH5-55`, and every manifest gate — SHA-256, row count, composition, distinct people, duplicate ids, declared address/ZIP/shared-value flags, fingerprint well-formedness — runs on bytes alone before a database connection is opened. 58 new tests; batches 1-4 and the canonical strict-safe tests are unchanged and still pass. No migration, no schema change, and no ownership has been applied.
- **The Drake import driver can now be told which years to import, instead of always importing all of them.** `python -m scripts.import_drake_all_years --year 2021 --year 2022` imports exactly those years; `--year` is repeatable, and with no `--year` the driver discovers and imports every numeric year directory exactly as it always has. This existed because re-importing 2021 and 2022 after the 1120S short-row fix meant re-importing 2023-2025 as a side effect — a wider blast radius than the operation called for, with no supported way to avoid it. Selection **can only ever narrow**: every requested year is fully resolved before the write transaction opens (the year must be plausible, its directory must exist, and a client export must resolve inside it by the same rule discovery uses), one unresolvable year aborts the whole invocation with exit 2 and imports nothing, and the driver never falls back to all years or substitutes a different one. All selected years share **one transaction**, which is the atomicity the all-years run always had. The driver stays a thin wrapper over the canonical `read_client_rows` / `upsert_return_rows` — there is no second importer — and its per-year output now names the source file and reports short rows normalized and short rows *not* normalized alongside the existing inserted/updated/quarantined counts, using a new optional `counters` argument on `read_client_rows` so nothing is parsed twice. Two related safety improvements came out of the same work: the driver no longer connects at module scope, so `--help` and year validation run without a database (and the module is importable by tests without side effects), and it prints `Target database: <name>` before writing — because `load_dotenv(app/.env)` fills in any variable the shell has *not* set, so unsetting `DATABASE_URL` or `MICROSOFT_TOKEN_KEY` to make an invocation "safe" does the opposite. `docs/DRAKE_1120S_SHORT_ROW.md` also now states explicitly that a source re-import legitimately refreshes `raw_data`, that `drake03`'s downgrade therefore refuses (in both directions) once that has happened rather than synthesizing historical values, and that the verified pre-migration backup is the authoritative rollback mechanism from that point on. No schema change and no migration.

### Fixed
- **Drake's 2021 and 2022 exports drop one field from every 1120S return, and the importer was reading those rows one column early.** `csv.DictReader` maps values to header names by POSITION and tolerates a short row silently. Both Drake exports for 2021 and 2022 emit 122 fields instead of 123 for an 1120S return — 52 rows and 57 rows, and in those two years there is not one well-formed 1120S row, so the short row *is* the 1120S shape there; Drake fixed it from 2023 onward. Every value from the omission onward therefore landed one column early: the form token that belongs in `Type` landed in `Paid`, so `return_type` imported as NULL for all 109 rows, and `agi` (wrong on 102), `preparer_fee` (97), `complete_date` (109) and the six e-file columns (~79 each) took their neighbour's value too. `TP_Social`, the names, `TP_DoB`, `FS` and `Prep` sit before the omission and were always correct, which is why the affected rows still carry the right identifier hashes. Parsing now happens in `app/importers/drake_client_csv.py`, which restores the single missing structural slot on the RAW ROW before anything is mapped to a column name — one rule, correcting every displaced field at once, rather than a per-column patch. It is structural, not name- or year-based: a row is normalized only when it is short by exactly one field, the header carries `Paid` immediately followed by `Type`, the value that landed in `Paid` is a recognised Drake return form, the `Type` slot is blank, and there is an empty field to restore into. The omitted field cannot be located exactly — every column between `Fee` and `Misc4` is empty on all 109 rows — so the rule restores into that empty run, where every position is provably equivalent. **Any other short row fails closed**: it is imported exactly as it always was, never realigned on a guess, and reported for review. Because `return_type` is an input to `return_identity_key`, and that key is the returns upsert's `ON CONFLICT` target, the importer fix alone would make the next import insert 109 duplicate returns; migration `drake03` therefore re-reads those exact 109 rows from their untouched `raw_data` and re-keys them in the same release, so a re-import resolves each existing row through its new key and updates it in place. The cohort is 109 frozen primary keys each paired with the identity key it must still carry — no `WHERE return_type IS NULL` sweep — and the migration refuses entirely if any row has drifted, if two rows would be given one key, or if a target key is already owned. No row is inserted or deleted, `raw_data` is never rewritten (it is the audit trail, and both upgrade and downgrade derive their values from it, which is what makes the pair lossless without a single taxpayer figure in version control), and the table has no timestamp or audit column for the migration to disturb. Downstream, `is_personal_return_type` answers False for both NULL and `1120S`, so person linking, document ownership, filing and portal access are unchanged; what does change is that 13 identifiers stop being unclassifiable and one — an identifier that is both an 1120S taxpayer and a 1040 spouse — stops being confidently mis-classified as a natural person and becomes `conflicting_subjects` for review, which is the correct answer and is deliberately not auto-resolved.
- **Drake ingestion now routes each identifier to the identity table its filed return says it belongs in, and a business identifier can no longer reach a person.** Every Drake identifier used to land on a `people` row; 150 of the 1,802 production identifiers are not natural persons, and 46 of those sit on a person today. The routing that put them there leaned on contact evidence, which is exactly wrong for a business — an owner's phone *is* the business's phone. Person 1314 accumulated two S-corp identifiers and an unrelated person's identifier because the firm's own phone number matched all three. Subject typing now happens at one shared boundary (`app/services/drake_subject_routing.py`) over the Phase A classifier, and the four writers that could reach an identity all consult it: the identity rebuild, the auto-linker and the review-queue builder (both through the shared evaluator, which refuses to auto-link a non-natural identifier however strong the contact match), and the identity-approval route, which is the only runtime writer of `primary_person_id` and now returns 409 rather than approving a business onto a person. A 1040/1040NR routes to `drake_identity`; 1065/1120/1120S/990 and 1041 route to `drake_business_identity` under the matching `subject_type`; a decedent-then-estate identifier yields **both** legal subjects, each bounded to its own years with the succession recorded in `decedent_identifier_hash` rather than inferred; and a contradictory identifier, an unrecognised form or no form at all is written to **neither** table and returned with a deterministic reason code (`CONFLICTING_SUBJECTS`, `UNKNOWN_RETURN_TYPE`, `INSUFFICIENT_YEAR_SEPARATION`). Non-natural identities are upserted with the same non-destructive discipline as the person rebuild: derived fields refresh, while `relationship_entity_id`, the trust and confirmation fields and `created_at` survive re-ingestion, so an established entity adjudication is never wiped by a later import. No entity is created or matched — a new business identity is left with `relationship_entity_id` NULL, because a name is not evidence of entity identity and all 18 D7 candidate matches rested on name alone with no EIN in production to corroborate them. `entity_source_links` stays dormant for the same reason: it requires an authoritative entity, and this phase resolves none. No migration, no schema change, and nothing is backfilled — the 46 non-natural identities already in `drake_identity` are retained untouched and reported as a visible backlog for a later, separately authorised phase.

### Added
- **Schema foundation for non-natural Drake tax identities — businesses, estates and trusts (dormant; nothing is routed to it yet).** `drake_identity` has one owner column, `primary_person_id`, so every Drake identifier was forced onto a `people` row. 150 of the 1,802 production identifiers are not natural persons — 141 file entity returns (1120/1120S/1065/990), 4 are estates, 4 are decedent-then-estate identifiers and 1 is contradictory — and 46 of those sit on a person row today. `person_source_links` and `source_contacts` have no entity column either, so the source contacts behind a business identifier could only ever attach to a person; that is a schema limitation, not a tooling gap. Migration `dbi01` adds `drake_business_identity`, keyed `UNIQUE (identifier_hash, subject_type)` and pointing at `relationship_entities` — **not** at `people`, and with no person column at all, so a business identity cannot require a dummy person row. The key is deliberately not `identifier_hash` alone: four production identifiers carry 1040 history with a date of birth and later 1041 history, a decedent and the estate that succeeds them, and keying on the hash would force one of the two legal subjects to lose its history. It also adds `entity_source_links`, a parallel to `person_source_links` rather than a polymorphic retrofit — making `person_id` nullable would weaken an invariant across 11,129 rows, break `uq_person_source_link` and turn every existing reader, including the person-merge registry, into a NULL-handling hazard — carrying the same trust vocabulary and the same attribution constraint, so a human-approved entity link cannot be recorded without an actor. A new pure classifier (`app/services/drake_return_subject.py`) decides the subject **from the filed return, never from the name**: a corporate suffix is not consulted, because "DAVID KEETER LLC" is a name-token superset of the human "DAVID KEETER" and only the return types (1120S against 1040) separate them. A decedent/estate identifier returns both subjects, each bounded to its own years; a person return and an entity return on one identifier fails closed with no subject proposed. Estates keep the existing production convention, `relationship_entities.entity_type = 'trust'` — all five are stored that way and `canonical_population` already maps 1041 to `trust` — with the legal distinction moving to `organization_profiles.entity_form`, now extended with `estate`, `revocable_trust` and `irrevocable_trust`; the unused and divergent `entity_type='estate'` path is removed from the creation allowlists, which no caller used and no row carried. Both tables are created empty. No production data is migrated, moved, relinked or deleted, no entity is created, and `drake_identity`, `person_source_links` and `people` are untouched.
- **Read-only MCP (Model Context Protocol) interface, so an assistant such as ChatGPT can query Client360 without being given the database.** A new `app/mcp/` package exposes exactly six read-only tools — `search_clients`, `get_client`, `list_client_documents`, `get_document`, `search_documents`, `get_document_text` — as a THIN adapter over the existing service layers: client search delegates to `universal_search`, documents to `app/services/document_platform/service.py`, extracted text to `document_ocr`. No second search subsystem, no generic SQL/filesystem/shell/URL tool, and no write path of any kind; `tests/test_mcp_no_mutation.py` asserts structurally that none can be added unnoticed. Authorization is default-deny in six layers: the `CLIENT360_MCP_ENABLED` flag (off by default — the endpoint 404s), a valid token, the `mcp.access` door capability, the token's own scopes (`client:read` / `document:read` / `document:content:read`), the ordinary app capabilities the web UI requires, and finally per-row record scope enforced by the services themselves. Migration `mcp01` adds `mcp_access_tokens` and seeds four `mcp.*` capabilities **granted to no role at all**, so a fresh upgrade exposes nothing until an administrator grants them. MCP tokens are a credential class of their own — stored only as SHA-256, independently revocable, and not interchangeable with staff browser sessions in either direction. Outbound payloads are an explicit allow-list (`app/mcp/projection.py`): no email or phone in any tool, no `storage_path`/`storage_uri`/`stored_name`/`sha256`, and documents referenced by the authenticated `/documents/{id}/download` route instead; display names go through the 0.13.0 sensitive-identifier gate. Soft-deleted documents are excluded from every listing, search and direct fetch, and `get_document_text` returns only text OCR has ALREADY produced — it never triggers extraction. Every call (success, denial and malformed request alike) is appended to the existing tamper-evident audit chain with timestamp, actor, tool, target and outcome, and never with the caller's query text or document contents. Two transports, no new dependency: `python -m app.mcp.stdio` for local testing and `POST /mcp` for the OpenAI Secure MCP Tunnel. Operator CLI: `scripts/mcp_token.py`. Setup, ChatGPT Developer Mode connection, tunnel deployment and the four-level rollback are documented in `docs/mcp/README.md`. Not deployed and not enabled.
- **iPhone HEIC/HEIF image support across every upload path, with a normalized JPEG derivative for downstream use.** `.heic`/`.heif` (and `image/heic`/`image/heif`) are now accepted by the workspace document uploads, the staff Client Vault and the client-portal upload, validated by CONTENT (ISO-BMFF `ftyp` brands) rather than by extension; multi-frame HEIF image sequences (`image/heic-sequence`/`image/heif-sequence`) are refused explicitly at acceptance rather than half-converted. The uploaded ORIGINAL is stored exactly as uploaded — same filename, MIME type, storage path and SHA-256 — and is never rewritten. A new single normalization engine (`app/services/image_normalization.py`, Pillow + the already-pinned `pillow-heif`) produces a separate JPEG derivative honoring EXIF orientation, converting to RGB, shrinking (never enlarging) to 2048 px and staying inside the AI image-input size limit; `app/services/document_derivatives.py` records it against the canonical `documents` row in the new `document_derivatives` table (source/derivative MIME + SHA-256, derivative path and pixel size, engine, and a pending → processing → completed/failed/skipped/unsupported state with the conversion timestamp), mirroring the status onto the existing `documents.preview_status`. OCR, the admin image preview and any AI image call now go through that one seam and receive the JPEG — no caller implements HEIC conversion of its own — while Download still serves the untouched original. A conversion that cannot succeed is recorded truthfully (`unsupported` for a corrupt/multi-frame/spoofed file, retryable `failed` for a host problem) and OCR reports the gap instead of claiming success. Derivatives live under `IMAGE_DERIVATIVE_ROOT` (development default `data/derivatives`, beside `data/vault`); production fails closed unless it is set to an absolute path, so generated files can never land in a deployed source tree, and `python -m app.deploy check-config` — step 1 of the Windows deploy — now validates that store alongside the Vault root. Growth is bounded by content addressing (one JPEG per distinct image, shared by duplicate uploads); `python -m app.services.document_derivatives --prune` reports and, with `--apply`, removes derivative files no live document can still claim — it can only ever delete a generated `<sha256>.jpg` inside the derivative root, never an original. Migration `docnorm01` (`ON DELETE CASCADE`); no new dependency. Verify a host with `python -m app.services.image_normalization --preflight`.
- Household service (`app/services/households.py`, `assign_people_to_household`): the supported, migration-safe API for grouping a **human-verified** set of people (e.g. spouses) into one household when the automatic derivation engine cannot (its policies group nothing by default or by address pending approval, and shared surname alone is too weak). UI-first — the future Household Management UI calls the service directly (passing the principal for an audited change); the thin `python -m app.services.households <person_id> …` CLI is one caller kept for deployment/automation. Reuses an existing shared household or creates one, sets `people.household_id`, and records a `household_relationships` `member` row — the same tables/conventions as `household_derivation`. Preserves all person records and document/source links, never creates a duplicate household, refuses to auto-merge people already in different households, and is idempotent (`--dry-run` supported). Unblocks TaxDome joint-folder resolution (e.g. "Michael and Debra White") whose members were not yet in a common household. No schema change.

### Fixed
- **Rebuilding `drake_identity` no longer destroys Drake-to-person linkage.** `scripts/build_drake_identity.py` rebuilt the table with `DELETE FROM drake_identity` followed by an `INSERT ... SELECT` whose column list omitted `primary_person_id`. Every run therefore discarded the person link on every identity — 931 links in production, including links established by human adjudication through the identity review queue and by individually authorised manual repair — and reported success. The table mixes fields Drake derives (`first_year`, `last_year`, `return_count`, `taxpayer_name`, `spouse_name`) with state Drake does not know about (`primary_person_id` linkage, `confidence` adjudication, `created_at` first-observed history), and the old statement treated them alike. Rebuild semantics now live in `app/services/drake_identity_rebuild.py` and upsert on `identifier_hash`, the existing primary key, so persistent state is never detached from its row and cannot be restored onto a different identifier. An identity that disappears from source data is retained and reported rather than deleted — silently dropping a linked identity is the behaviour being removed, and the table has no tombstone column — with linked ones called out for review. The rebuild runs in one transaction and refuses, rolling back, if the source yields no identities at all (a truncated import must not read as an empty world), if any pre-existing identity's persistent state changed, or if any identity would point at a person id that does not exist. `drake_identity.primary_person_id` still has no foreign key, so that last check is enforced in code; adding the constraint is separate schema work. No migration is required and no production data is changed by this fix.
- **Drake identity linkage now decides on evidence semantics, in one place, and fails closed.** Linkage was decided in two code paths that had drifted into different opinions about the same signals, each carrying its own copy of the normalisation helpers (`scripts/link_drake_to_people.py` defined `normalize_name(first, last)`; `scripts/build_drake_identity_review.py` defined `normalize_name(value)`). A read-only replay of all 1,802 production Drake identities established four defects. The review scorer pooled `taxpayer_name` and `spouse_name` into one set, so a person carrying the SPOUSE's name scored an exact-name hit against a TAXPAYER identity (64 identities carry both names). Drake's bare `Email` field carries no attribution, yet both paths treated it as person-specific: on a joint return that mailbox is the household's, which is how one taxpayer identity came to be linked to his own spouse. Drake has no spouse-attributed contact field at all, so 147 linked spouse identities rest on a name alone. And `confidence` described nothing — `build_drake_identity` wrote a literal `100` for every identity merely because the identity existed. Both paths now build an `IdentityEvidence` and call one shared, pure evaluator (`app/services/drake_linkage_evidence.py`), so they cannot diverge again. An identity is scored only against its OWN role's name; a spouse never inherits taxpayer contact evidence; a bare `Email` is person-attributed only when the return carries no spouse, and is otherwise retained as household context that can never identify anyone; `TP_*` phones support the taxpayer only; city/state corroborate a name but never find one; DOB is used only when the source attributes it to this role AND the roster holds a DOB to compare (`people.birth_date` is populated on 1 of 7,794 rows, so in practice it disambiguates nothing); an exact name may propose a review candidate but can NEVER auto-link, because uniqueness in the current roster is not uniqueness in the world; two candidates, or two signals pointing at different people, resolve to nothing; and where the resolved person has a name twin the decision fails closed, since choosing between duplicate person records on a contact point is arbitrary and the remediation is a merge. The review queue's `-person_id` tie-break — which turned a coin-flip into an identity decision — is removed, candidates are produced per role, and their score is the evaluator's evidence-derived confidence. Trust is recorded explicitly (`trust_level`/`confirmation_source`/`evidence_method`) rather than inferred from the `confirmed` boolean. Replayed against production, the new evaluator proposes **zero** automatic relinks, rejects all three known deterministic wrong links, rejects all 46 ambiguous/unsupported current links, and rejects all 155 name-only proposals the old semantics would have accepted. No existing link, identity or person row is modified, and no schema migration is required.
- **Sensitive identifiers can no longer reach a Client Vault or client-portal download filename.** `vault_documents` is a separate storage model that keeps the uploaded name in `original_filename`, and the three vault download paths — the staff vault route, the client-portal route and the portal JSON API — returned that column verbatim as the response `Content-Disposition`, which is the name the browser saves the file under. A client who uploaded `2024 W2 SSN 123-45-6789.pdf` had that name delivered straight back on every one of those surfaces. All three now resolve their delivered name through the SAME centralized component introduced for the canonical `documents` table (`app/services/document_name_safety.py`, applied via `document_delivery_filename`); there is no second detector and no second policy, so a protection added centrally applies here automatically. A safe name is delivered unchanged; an unsafe one is scrubbed with surrounding non-sensitive wording preserved (a custodian such as "Chase" survives, the account number does not); and when nothing meaningful survives, the download falls back to `Document <id>` with the original extension — never to the unsafe `original_filename`. The client-facing portal document list label goes through the same seam. `vault_documents.original_filename`, `display_name`, `storage_key` and `checksum_sha256` are untouched, no stored file is renamed or moved, and the bytes served are byte-for-byte the original — only the label on the response changes. Staff provenance views continue to read `original_filename` directly. No schema migration.
- **Sensitive identifiers can no longer reach a document's displayed or delivered filename.** A new centralized component (`app/services/document_name_safety.py`) detects and removes Social Security, ITIN, EIN/TIN/tax, bank account, routing/ABA, payment card, CVV/CVC, insurance policy/member and labelled date-of-birth identifiers, reporting only value-free reason codes — a matched value is never returned, logged, or persisted. It is enforced at four points. The naming engine strips identifiers from a filename **before** composing a candidate, preserving surrounding non-sensitive wording such as an employer or custodian. The normalization preview runs a **final scan after candidate construction and fails closed**: an unsafe candidate can never enter the SAFE bucket and is forced to staff REVIEW with a non-sensitive reason code. `apply_display_names` refuses such a row independently of its bucket, so `safe_all` cannot write one. Every delivery path — download, document-email compose, and mail attachment — re-checks the name it is about to emit, so a `display_name` stored before this gate existed is scrubbed, or replaced by a name built from structured fields, and **never** by an unsafe original filename. Global search still matches on the original filename so reconciliation and staff lookups are unaffected, while result labels go through the safe display-name path. `original_name`, `stored_name`, `storage_path`, `storage_uri`, `sha256` and `tags` are untouched and no file is renamed or moved — provenance is preserved exactly.
- TaxDome documents in **joint/household folders are now linked and visible to both spouses**. Previously a folder like "Michael and Debra White" matched no single person, so its documents were left `person_id = NULL` and never appeared on either client's Documents tab. The importer now resolves each folder to the canonical household and/or person: a joint folder whose matched people share one household sets `household_id` (both members see it); a single-person folder with a unique name match sets `person_id`. `get_person_documents` now returns documents linked to the person **or** to the person's household, so household documents (joint returns, estate documents) are visible to every household member. Links are filled only where NULL, so manual links are preserved and reruns are idempotent. Adds a repair command — `python -m app.importers.taxdome_drive --repair-links` (and `repair_person_links()`) — that relinks already-imported rows by the stored `tags->>'taxdome_folder'` metadata **without re-copying files, inserting rows, or altering storage_path/hashes/OCR metadata/version history** (supports `--dry-run`). Resolution stays conservative: ambiguous folders (no unique match, or matched people without one common household) are left for review.
- TaxDome sync now recognizes and **upgrades legacy metadata-only rows** created by the previous importer instead of treating them as missing and inserting duplicates. Identity is resolved by BOTH the new relative-path `stored_name` and the legacy absolute-path `stored_name`; a matched legacy row is upgraded in place (converted to the stable relative-path key, `storage_provider="Client360 Local"`, verified local `storage_uri`, preserving `person_id`/`household_id`/`category`/`classification` and updating the sync tags), and a legacy+new duplicate pair is reconciled to one canonical row (the person-associated row is preserved; the retained local file is never deleted). Missing-source reconciliation runs only after identity resolution, so a first dry run against the production database no longer reports nearly every legacy row as missing. Office/OS temporary files (`~$…`, `.tmp`, `Thumbs.db`, `desktop.ini`) are skipped as `ignored` rather than erroring, and the path sanitizer no longer rejects legitimate `~`-prefixed filenames (traversal/absolute/drive-qualified/escape paths are still rejected). Adds `legacy_rows_to_upgrade` (dry-run) and `legacy_rows_upgraded` (live) summary fields.

### Added
- TaxDome Drive one-way local document synchronization (`app/importers/taxdome_drive.py`): TaxDome Drive (`Z:\`) is now a **read-only external source** and Client360 keeps durable, verified local copies under `CLIENT360_TAXDOME_DOCUMENT_ROOT` (default `C:\Client360\Data\Documents\TaxDome`), preserving the source-relative directory structure. New files are copied; changed files are copied to a temp file, verified by size + SHA-256, and atomically swapped (never a partial file); unchanged files are skipped via a size+mtime fast path (hashing only when needed); rescans are idempotent. When a source file disappears the local copy is **retained** and flagged `available_from_source=false` (never archived by disappearance); an explicit, off-by-default `--purge-missing` removes retained copies. Synced documents use `storage_provider="Client360 Local"` with `tags.source_system="TaxDome Drive"`, appear on the existing person Documents tab, and download the local copy (never `Z:\`). Conservative folder→person auto-link (unique exact normalized-name only) is preserved. CLI supports `--dry-run`, `--source-root`, `--destination-root`, `--purge-missing`, periodic progress, and graceful Ctrl+C (recorded as `interrupted`). Adds `scripts/sync_taxdome_documents.ps1` (Windows runner with logging + single-instance lock) and `docs/TAXDOME_DOCUMENT_SYNC.md` (runbook + Scheduled Task setup). No schema migration; uses the existing `documents`/`import_jobs` tables.
- Index-assisted global search (pg_trgm GIN indexes) with results de-duplicated per canonical person.
- Timeline display styling for activity-note, communication, and client-update events.
- Development-only sign-in provider (`/dev-auth`, gated by `CLIENT360_DEV_AUTH`; impossible to enable in production) and authenticated Playwright browser E2E coverage across login, dashboard, people, households, search, notes, tasks, and communications.
- Staff-editable canonical contact/address fields on the client profile (audited + added to the timeline).
- Human-readable timestamps across the client surface via a shared Jinja `humandt` filter.
- Task-submission idempotency: a DB-backed `tasks.idempotency_key` (unique) + hidden form token make a resubmitted create-task form a conflict-safe no-op (no duplicate task on browser back/resubmit or retried POST).
- Optional inbound/outbound direction on logged communications (call/email/meeting), captured in the Log form and shown in the activity feed.
- Match Review "unresolved contacts" queue (`/matches/unresolved`): single-source contacts that promotion leaves ambiguous (multiple candidate people, or a contact detail shared with another unlinked contact) are surfaced for a human to link to an existing client or create a new one. Human decision only — no automatic merge thresholds; every resolution is audited.
- Household detail roll-up: member count, aggregate household AUM, and open tasks across all members.
- `docs/RELEASE_READINESS.md` — a living release-readiness tracker maintained through Sprint 2.
- Household-derivation engine (`app/services/household_derivation.py`): groups un-householded people by an injected policy (safe no-op default; candidate address policy provided but not enabled), with a dry-run mode. The mechanism is complete; the grouping rule and auto-apply are business decisions.
- `docs/PRODUCT_DECISIONS.md` — authoritative register of deferred business/product-policy decisions (mechanism built, decision awaited).
- `docs/PROJECT_STATUS.md` — 5-minute project orientation (version, milestones, readiness, blockers, decisions, debt, next work).
- `Dockerfile` — reproducible python:3.12 runtime image (pinned requirements, non-root, healthcheck).
- `scripts/deploy.sh` — deploy orchestration (migrate → start → smoke → rollback-on-failure), dry-run verified.
- `docs/RC_READINESS.md` — Release Candidate readiness definition (build, artifact, tagging, deploy, rollback, smoke, monitoring, backup/restore, environment, acceptance) with owner/evidence/verification/status per item.
- `scripts/smoke.sh` — post-deploy smoke test for a running instance (liveness, readiness with DB + migration-drift, static assets, auth gate); verified live.
- `scripts/rollback.sh` — migration rollback helper (downgrade to a target revision, guarded, dry-run); verified end-to-end.
- `docs/V1_OPERATIONAL_OWNERSHIP.md` — accountability document for the V1.0 cutover and production support: nine roles (Release Manager, Deployment, Rollback Decision Authority, Incident, Monitoring, Backup/Restore, Support, Business Acceptance, Executive Sponsor) with responsibilities, authorized decisions, required availability, primary/backup owner (`[NAME REQUIRED]`), contact, escalation, and sign-off; plus a release ownership gate.
- `docs/V1_CUTOVER_CHECKLIST.md` — operational production-cutover checklist (5 phases: release prep, production readiness, deployment, business acceptance, stabilization) with per-item status/owner/evidence/completion/notes and owner placeholders where none is assigned.
- `docs/V1_RISK_REGISTER.md` — authoritative home for program risks outside engineering (operational, organizational, governance) from the V1.0 pre-mortem, each with evidence/likelihood/impact/mitigation/remaining-exposure/owner/cadence.
- `docs/ENGINEERING_DECISIONS.md` — intent record: why the significant engineering choices were made (matching, household derivation, human-review boundaries, import, audit, security, release, documentation, deferred capabilities, operational boundaries), with alternatives, tradeoffs, and revisit guidance.
- `docs/USER_GUIDE.md` — staff user guide for the v1.0 CRM (search, profile, notes, communications, tasks, households, Match Review).
- `docs/V1_RELEASE_PLAN.md` — authoritative Version 1.0 definition: product scope (included/excluded/deferred), measurable release criteria, categorized remaining work, risk register, and staged release sequence with entry/exit criteria.
- Verified the backup/restore mechanism against the current schema: `restore_rehearsal.sh` restored a `pg_dump` of head `d4c5o6m7d8i9` into a scratch DB with a single Alembic head and a green suite (recorded in `RELEASE_READINESS.md`).
- Match Review page links to the unresolved single-source contacts queue.

### Changed
- Task/note assignee picker scoped to provisioned staff (active users holding an active role).

### Security
- Production startup now fails fast if `CLIENT360_DEV_AUTH` is set — the development-only sign-in provider is refused in production, and a set toggle is treated as a deployment mistake rather than silently ignored.

### Fixed
- Documentation governance: moved operational/business risks out of `V1_RELEASE_PLAN` §4 (engineering risks retained) into the single-owner `V1_RISK_REGISTER`; added brief cross-references from `V1_RELEASE_PLAN`, `RELEASE_READINESS`, `PROJECT_STATUS`, and README — no risk descriptions duplicated across documents.
- Consistency: corrected a stale test count in RELEASE_READINESS (1206 -> 1217) to match PROJECT_STATUS / V1_RELEASE_PLAN.
- Handoff: README release status now reflects Version 1.0 / Sprint 2 and links the authoritative docs (was stale at 0.9.10); disambiguated the duplicate `PROJECT_STATUS.md` (top-level is the historical release log, `docs/PROJECT_STATUS.md` is the current authoritative status).
- Order-dependent event-loop test flakiness (global conftest fixture); a non-portable test path that failed CI.
- Single-source contacts were never promoted to canonical people: the Wealthbox import now runs `promote_unlinked` after ingest (same transaction), so imported single-source contacts become people (ambiguous cases left for Match Review) instead of being stranded.
- Promotion backfill action (`POST /matches/promote-unlinked`, button on the unresolved queue) promotes contacts imported before the wiring fix; conservative and audited.

## [0.11.0] — 2026-07-17 — Documentation Foundation

**Documentation-only release (Roadmap Phase A). No application code or database migrations
changed.** Establishes the Documentation Foundation & Governance layer for the 360 Wealth
Consulting Operations Manual. Signed off:
[`docs/releases/0.11.0/RELEASE_SIGNOFF.md`](docs/releases/0.11.0/RELEASE_SIGNOFF.md); RC-validated by
[`P5_RELEASE_CANDIDATE_VALIDATION.md`](docs/releases/0.11.0/P5_RELEASE_CANDIDATE_VALIDATION.md).

> ⚠️ **Foundation only — no substantive content.** Governance content authoring, legacy Atlas
> reconciliation execution, Confluence migration, advisory→blocking enforcement, and all regulated
> insurance rule sets remain **deferred**. **AD-5 is unresolved**; the accountable compliance
> reviewer is UNFILLED and regulated content stays blocked (`compliance_gate: AD-5 ⇒ never
> published`). Michael Shelton approved business/operational scope only — not regulatory certification.

### Added
- Framework ratification + architecture decisions **D1–D10** ([`P0_ARCHITECTURE_CHECKPOINT.md`](docs/releases/0.11.0/P0_ARCHITECTURE_CHECKPOINT.md)).
- **Confluence skeleton** — 8 Operations Manual nodes + 3 Area Shell template pages ([`P1_CONFLUENCE_SKELETON_REPORT.md`](docs/releases/0.11.0/P1_CONFLUENCE_SKELETON_REPORT.md)).
- **Git governance skeleton** — `governance/` tree (README, CONTRIBUTING, 6 directory READMEs), skeleton only ([`P2_GOVERNANCE_TREE_REPORT.md`](docs/releases/0.11.0/P2_GOVERNANCE_TREE_REPORT.md)).
- **Canonical Publication Register** — `docs/registers/pages.yml` (554 rows: 26 areas + `SHARED` + `GOV`, complete per-profile coverage, 27-type Hybrid union) with schema, generator, and validator (`scripts/registers/`).
- **Generated crosswalk** — `docs/DOCUMENTATION_CROSSWALK.md` is a deterministic generated view.
- **D10 taxonomy migration** — framework area-code taxonomy; legacy crosswalk letters preserved.
- **Legacy Atlas inventory** — 23 pre-existing pages recorded as non-canonical `manual_review` (none moved/edited).
- **Advisory documentation DoD** — `scripts/docs/check_documentation_dod.py`, `.github/pull_request_template.md`, and a non-blocking `documentation-advisory.yml` workflow ([`P4_DOD_GATE_REPORT.md`](docs/releases/0.11.0/P4_DOD_GATE_REPORT.md)).

## [0.10.0] — 2026-07-16 — Insurance Operations

**Release 0.10.0 contains the completed non-regulated Insurance Operations implementation
(Phases 0–9). AD-5-regulated functionality remains intentionally excluded pending compliance
review and approval.**
Individual **life insurance & annuities** (advisor-sold, in-force-managed) as a domain inside
Client360 — not group/employer benefits (0.9.11), not P&C. Built additively on the
0.9.11 platform and 0.9.13 test/CI/release infrastructure. RC-validated by
[RC-0.10.0](docs/RC_0.10.0_VALIDATION.md) (717 passed, 5 skipped, 0 failed) and approved by
[RELEASE_0.10.0_APPROVAL](docs/RELEASE_0.10.0_APPROVAL.md). Design of record:
[`docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md`](docs/RELEASE_0.10.0_INSURANCE_ARCHITECTURE.md).

> ⚠️ **Non-regulated skeletons only.** Phases 2–4 ship the operational/non-regulated
> plumbing only. All regulated logic — suitability determination, replacement/1035
> recommendation, licensing/CE **validation**, and any compliance approval or
> regulatory decision engine — is **deferred behind the AD-5 gate** and is not built
> or enabled. A qualified, named compliance reviewer plus an approved sign-off
> artifact is required before any regulated phase may proceed (see AD-5 below).

### Added — Phase 0 · Schema foundation (`v2b3d4f5a6c7`)
- Insurance schema foundation: product catalog (carrier profiles → product families
  → product versions), `insurance_case` coordinator (1:1 with an engagement),
  policy/party/producer tables.
- `insurance.*` capabilities and roles seeded; `insurance` registered in the shared
  Exception Engine (`SUPPORTED_DOMAINS` + CHECK) and Work Management (`work_items` domain).

### Added — Phase 1 · Policies core (`w3c4e5g6b7d8`, `x4d5f6h7c8e9`)
- Product-version evolution: carrier codes (NAIC) + rider compatibility as first-class,
  versioned data (not hard-coded).
- Policies core with coverages/riders/parties/values; multi-owner / multi-insured /
  multi-beneficiary support; policy CRUD JSON API and book/detail UI.
- Policy lifecycle statuses (issued, delivered, reinstated) and lifecycle events on the
  **shared Timeline/Audit** (no separate history model); name-resolved UI.

### Added — Phase 2 · New-business pipeline — non-regulated skeleton (`y5e6g7i8d9f0`)
- Application/case progression (case status transitions), requirement tracking
  (`insurance_requirements`: requested → satisfied — an operational checklist, **not** a
  determination), underwriting-**status** tracking (records the carrier's status; the
  platform does not decide it), document collection via the shared `documents` table,
  workflow-driven carrier-communication orchestration, Timeline/Audit events, operational
  pipeline reporting (counts only), case-workspace + pipeline UI, and JSON APIs.
- **Not built (AD-5-gated):** suitability determination, replacement/1035 recommendation
  logic, automated compliance approvals, any regulatory decision engine. A test asserts no
  such function exists in the service.

### Added — Phase 3 · In-force servicing — non-regulated skeleton (`z6f7h8j9e0g1`)
- Policy reviews as a first-class **state machine** (due → scheduled → in_progress →
  completed / deferred / overdue / cancelled); obligation calendar (annual reviews
  materialize their next occurrence on completion); a scheduled/manual scan flips past-due
  reviews to `overdue` and raises `INS_REVIEW_OVERDUE` through the **shared Exception
  Engine** (idempotent, auto-resolving); operational review metrics (completion rate,
  overdue/deferred counts); reviews-board UI + JSON APIs; Timeline/Audit review events.
- **Not built (AD-5-gated):** suitability determination (the `suitability` review type and
  `insurance.suitability` capability stay reserved), replacement/1035 recommendation logic,
  and any compliance/regulatory decision engine. Tests assert the scan result carries no
  compliance field. Live cron wiring of the scan is deferred to Phase 6; the callable +
  manual endpoint ship now.

### Added — Phase 4 · Producer licensing & CE — non-regulated skeleton (`a7g8i9k0f1h2`)
- Producer **licensing records** (`insurance_licenses`) and **CE records**
  (`insurance_ce_records`) — firm-internal, capability-gated
  (`insurance.licensing.read`/`.write`), audited, staff-entered; date-driven expiry
  reminders (`detect_licenses_expiring` / `detect_ce_period_ending` raise
  `INS_LICENSE_EXPIRING` / `INS_CE_PERIOD_ENDING` through the shared Exception Engine,
  firm-level/unanchored for oversight roles); operational licensing counts; licensing
  dashboard UI + JSON APIs.
- **Not built (AD-5-gated):** licensing **validation** (whether a producer may sell a
  product in a state), CE **satisfaction determination**, sale/issue **blocking** on
  licensing status, and any compliance/regulatory decision engine. Stored
  `credits_required` / `credits_completed` are staff-entered figures — the platform draws
  no conclusion from them. Tests assert no validation/determination function exists.

### Added — Phase 5 · Insurance commissions — expected/received ledger & reconciliation (`b8i9k1l2g3j4`)
- **Split-aware expected ledger.** `insurance_commissions` — one expected/received row per
  producer split; `generate_expected` fans a commission basis across a policy's active
  producers by `split_percentage` (an `override` role credits an upline entity), so a
  split-commission policy credits each producer correctly. `record_expected` captures a
  single entry. Schedules: `first_year | renewal | trail | override | other`.
- **Received posting & reconciliation.** `record_received` posts a payment and recomputes
  status (received / partial / variance within a one-cent tolerance). Carrier statements
  import (`insurance_commission_statements` + `_statement_lines`) and reconcile against
  expected rows (`reconcile_line` auto-matches by policy + schedule; `reconcile_statement`
  rolls the whole statement up) — where variance surfaces.
- **Operational exceptions.** `INS_COMMISSION_VARIANCE` (received ≠ expected) and
  `INS_COMMISSION_OUTSTANDING` (expected past due, unpaid) raise through the **shared
  Exception Engine** — idempotent, auto-resolving, anchored to the policy's owners. Callable
  + manual scan endpoint ship now; live cron wiring is Phase 6.
- **Revenue rollup.** `insurance_reporting.commission_report` — expected/received/outstanding/
  variance totals by schedule and by organization, tagged with the `insurance_commissions`
  revenue category. Operational reporting only.
- **Surface.** JSON API (`/api/v1/insurance/commissions*`, `/commission-statements*`,
  `/commission-lines/*`) + a `/insurance/commissions` staff console. New capability
  `insurance.commissions.write` (read capability was seeded in Phase 0), granted to
  administrator / insurance_agent / insurance_operations; ledger scoped by policy record scope.
- **Non-regulated.** This is money movement and reconciliation only — no suitability,
  replacement/1035, licensing, or CE determination; nothing is blocked. A test asserts no
  regulated-determination function or verb leaked into the commission surface.

### Fixed — Phase 5 audit & revenue-validation pass
- **Adjustment / reversal / chargeback** — added `record_adjustment` (a signed delta applied
  to an entry's canonical net `received_amount`, distinguished by kind in the audit trail) so
  true-ups, reversals, and carrier chargebacks are first-class and flow through the rollup;
  audited as `insurance.commission.adjusted`. `write_off` remains for uncollectible expected.
- **Audit completeness** — `reconcile_statement` now writes its own
  `insurance.commission.statement_reconciled` event for the statement-level status roll-up
  (in addition to the per-line events). Every commission mutation is now covered by an
  immutable audit event; a test asserts one per mutation. Variance exception open/resolve is
  audited by the shared engine (`exception.raised` / `exception.resolved`).
- **Timeline privacy** — commission variance/outstanding exceptions are now **firm-internal
  (unanchored)**: they carry no person/household, so the shared engine no longer publishes a
  client-facing "Commission variance" Timeline event. Commission/compensation activity stays
  in the immutable audit log and the firm-internal exception queue — never the client Timeline
  (test-enforced).
- **Revenue source of truth** — the rollup now reads the **full scoped ledger (uncapped)** so
  totals cannot silently truncate; it derives every figure from `insurance_commissions`
  (`service_revenue` is never written by the ledger), is idempotent and non-duplicating on
  repeated runs, and reflects corrections/reversals immediately. Added **producer-payout vs
  agency-retained** and **by-producer** breakdowns, derived from the ledger + split data.
- **Robustness** — statement→policy auto-match no longer crashes on a duplicate policy number
  (deterministic oldest-first pick).

### Added — Phase 6 · Insurance exceptions, work management & scheduled scanning (`c9k0m1n2h3j4`)
- **Single `run_insurance_scan()`** orchestrates every insurance detector (in-force reviews,
  producer licensing/CE expiry, commission variance/outstanding) through the **shared Exception
  Engine** — no insurance-specific engine. Idempotent (stable dedupe), auto-resolving/reopening,
  with **per-detector failure isolation** so one detector or one organization's bad data never
  aborts the scan. Honest aggregate reporting: **organizations scanned, exceptions opened /
  resolved / reopened / skipped, failures**, plus each detector's own result.
- **Scheduled scanning** via the **existing scheduler** — `run_insurance_detector_scan`
  registered as `insurance-detector-scan` (interval `INSURANCE_SCAN_INTERVAL_MINUTES`, default
  30; `max_instances=1`, `coalesce=True` — no overlap). No new scheduler framework.
- **Insurance work queues** seeded through the **existing queue framework**
  (`work_queues.criteria`): `insurance_unassigned`, `insurance_exceptions`, `insurance_reviews`,
  `insurance_licensing`, `insurance_commissions`, `insurance_high_priority` — projected through
  the same `work_items` surface as tax/benefits. No new queue framework.
- **Automatic assignment** via the **existing assignment rules** (`app/services/insurance_work.py`
  reuses `apply_assignment_rules`) — `auto_assign_unassigned` applies rules to unassigned open
  insurance exceptions; with no rule configured, items stay in *Insurance — Unassigned*. No new
  assignment model.
- **Organization-based record scope** — commission exceptions now anchor the client
  **organization** (`related_entity_type='organization'`) for org-scoped queues/assignment while
  keeping `person_id`/`household_id` NULL, so **no compensation ever reaches the client
  Timeline** (client-facing exception visibility remains out of scope). Reviews keep their
  existing org/person/household anchor.
- **Manual twin** `POST /api/v1/insurance/scan` (capability `insurance.write`) runs the same
  orchestrated scan + auto-assignment.
- Non-regulated throughout: no suitability, replacement/1035, or licensing determination; the
  **AD-5 gate is unaffected**.

### Changed — Pre-Phase-7 architecture cleanup (`d0l1n2o3i4k5`)
Behavior-preserving cleanup from the Release 0.10.0 architecture review (items #1–#3):
- **Docs refresh** — removed stale "cron wiring is Phase 6 (future)" wording from
  `insurance_detectors.py` and the architecture doc now that the scheduled scan is live;
  corrected the commission-exception privacy comment to reflect the Phase 6 organization anchor.
- **De-duplicated scan plumbing** — introduced shared `_exception_status`, `_scan_delta`, and
  `_run_detector_deltas` helpers; the four scan functions now share one diff implementation
  instead of four copies. **No functional change** (identical return shapes/values; all detector
  tests unchanged).
- **Dedicated scan authorization** — new capability **`insurance.scan`** (data-only migration
  `d0l1n2o3i4k5`) gates the operational scans (`/scan`, `/reviews/scan`, `/commissions/scan`)
  instead of overloading `insurance.write`/`.commissions.write`. Running a non-mutating detection
  sweep is now its own authority. Granted to the same roles that could scan before
  (administrator, insurance_agent, insurance_operations) — **no expansion, no weakening**; the
  producer-licensing scan keeps its tighter `insurance.licensing.write` gate.

### Added — Phase 7 · Policyholder portal surface (no migration; reuse the portal)
- **Read-only policyholder policy view** through the **existing** portal framework
  (`app/services/insurance_portal.py` + portal routes/template) — no insurance-specific portal
  engine, auth, session, or scope model. Scope is **opt-in**: resolved with
  `portal_scope(account_id, permission="insurance")`, so only a grant that allows the
  `insurance` permission sees anything; policies match by person / shared-household /
  organization scope.
- **Proportional disclosure** — carrier, product, policy number, status, issue date, face
  amount, premium, coverages, riders, and the policyholder's own owner/insured/beneficiary
  designations. `GET /api/v1/portal/insurance/policies[/{id}]` + a `/portal/insurance` page; the
  portal dashboard gains an `insurance_policies` slice.
- **Out-of-scope policy ids deny existence with 404**; unauthenticated portal access → 401.
- **Client-facing exception visibility stays out of scope** — the surface never exposes
  producers, commissions/compensation/splits, licensing/CE, exceptions, or internal metadata.
  Insurance exceptions cannot reach the client action-needed surface (the shared
  `client_action_items` is hard-scoped to `domain='tax'`). Factual policy data only — no
  suitability/replacement determination; **AD-5 unaffected**.
- No schema change (read-only over existing tables; the `insurance` grant permission is JSON).

### Added — Phase 8 · Reporting & dashboards (no migration; extend `insurance_reporting`)
- **Consolidated operations dashboard** (`insurance_reporting.operations_dashboard` +
  `GET /api/v1/insurance/dashboard` / `/insurance/dashboard`) — a **firm-internal staff** surface
  that composes the existing per-domain reports (pipeline, reviews, commissions, licensing) plus
  three new operational summaries, **proportional to the viewer's capabilities**: each optional
  section is included only if the viewer holds its capability (commissions →
  `insurance.commissions.read`, licensing → `insurance.licensing.read`, exceptions →
  `exception.read`, work_queues → `work.read`, portal_adoption → `record.read_all`); the response
  names the `sections_included`.
- **New summaries, all derived from a scope-filtered list** (authorization before aggregation):
  `exception_summary` (reuses `exception_engine.list_exceptions(domain="insurance")`, counts by
  code/severity/status), `work_queue_report` (reuses Work Management `work_items` + the existing
  queue criteria for depths), and `portal_activity_report` (firm-internal policyholder-portal
  adoption — oversight only).
- **Reuse only** — extends `insurance_reporting.py`; no parallel reporting engine, dashboard
  framework, authorization system, or record-scope model. Record scope is applied before every
  aggregation; `record.read_all` aggregates firm-wide.
- **Firm-internal boundary** — a staff surface under `/insurance/*` (401 without auth), never the
  client portal; producer compensation, commissions, licensing, exceptions, and queue internals
  are shown only to staff who already hold those capabilities. The Phase 7 portal is untouched.
- **Non-regulated** — operational counts, workflow status, and financial reconciliation only; no
  suitability, replacement/1035, licensing-validation, sale-blocking, compliance-approval, or any
  compliance metric. A test asserts the dashboard carries no compliance/determination content.
  **AD-5 unaffected.** No schema change (read-only; reuses existing capabilities).

### Added — Phase 9 · Integration ports as disabled stubs (no migration; reuse the provider idiom)
- **Vendor-neutral extension points, disabled** (`app/services/insurance_integrations.py`) — six
  ports: `carrier_policy_feed`, `case_status_feed`, `commission_statement_feed`,
  `licensing_appointment_feed`, `document_evidence_intake` (inbound), and `operational_export_hook`
  (outbound). Ships the neutral interfaces + **disabled** stubs only — same registry idiom as
  `benefits_providers` / `tax_filing_providers` / `portal.providers`; **no parallel integration
  framework**.
- **Inert by construction** — every port reports `enabled=False` / `status='not_connected'`.
  Calling a disabled port **fails safe** (`outcome='disabled'`, no external I/O — no HTTP, file
  transfer, auth, polling, or vendor API call). `enabled` is hardcoded and **never read from
  configuration or environment** — no port activates because a config value exists; activation is
  an explicit code decision (a concrete adapter + registry row) in a future release.
- **No secrets / endpoints / scheduled jobs** — no credentials, tokens, certificates, URLs, or
  production config are added; no scheduler job is registered. Read-only registry/status routes
  (`GET /api/v1/insurance/integration/ports[/{key}]`, `insurance.read`) and an inert invoke
  (`POST …/{key}/invoke`, `insurance.write`); invoking writes an **audit-safe** event
  (`insurance.integration.port_invoked`) with metadata only — never the payload or secrets.
- Non-regulated transport extension points only — no suitability, replacement/1035, licensing
  validation, sale-blocking, or compliance approval; **AD-5 unaffected**. No schema change.

### Blocked / deferred
- **AD-5 — compliance reviewer NOT YET NAMED → all regulated insurance logic BLOCKED.**
  Michael Shelton is recorded as the **business** owner (workflow/operational scope); this
  is **not** regulatory certification. No regulated phase passes its RC gate without a
  completed, approved sign-off artifact from a qualified, named compliance reviewer. This
  is not resolvable in code and remains open.
- **Remaining phases:** 10 (RC validation + release), plus the AD-5-gated regulated portions of
  Phases 2–4.

### Infrastructure / hygiene (0.10.0 pre-Phase-5 checkpoint)
- **Interpreter portability** — `scripts/lib/pyenv.sh` resolves a Python 3 interpreter
  (active virtualenv → repo-local `.venv` → `python3` → `python` if Python 3, else a clear
  failure) with no hardcoded paths; `test.sh`, `restore_rehearsal.sh`, `release.sh`,
  `demo.sh`, `check_migrations_reversible.sh`, `check_migration_heads.sh`, and
  `check_schema_at_head.sh` now source it and invoke `python`/`alembic`/`pytest`/`uvicorn`
  through `$PYTHON`. Fixes the bare-`python` failure that broke the harness on
  venv-only/py3.12 environments (previously 1 failing safety test).

### Migrations
Additive, off head `u1f9c0i9h8g7`, single head `d0l1n2o3i4k5`, reversible:
`v2b3d4f5a6c7` → `w3c4e5g6b7d8` → `x4d5f6h7c8e9` → `y5e6g7i8d9f0` → `z6f7h8j9e0g1` →
`a7g8i9k0f1h2` → `b8i9k1l2g3j4` → `c9k0m1n2h3j4` (Phase 6: data-only insurance work queues) →
`d0l1n2o3i4k5` (pre-Phase-7: data-only `insurance.scan` capability).

## [0.9.13] — 2026-07-16 — Platform Foundation

Developer platform, testing, and release hardening. **No product or business-logic
change; no schema change** (Alembic head unchanged at `u1f9c0i9h8g7`). Validated by
[RC-0.9.13](docs/RC_0.9.13_VALIDATION.md). Delivers issue #24.

### Added
- **Isolated test database** (#24) — the suite ran against the real development
  database (`client360`); it now refuses any non-disposable target. `app/safety.py`
  guard, `tests/conftest.py`, and `scripts/test.sh` (setup/reset/run/verify/status).
  A full run leaves `client360` byte-for-byte unchanged; local suite **287s → ~11s**.
- **Ruff lint gate** (#26) — `pyproject.toml` config and `scripts/ruff_gate.py`, a
  count-based ratchet that baselines the legacy backlog and fails only on *new*
  violations. Backlog tracked in #26.
- **CI/CD hardening** — pip caching, single-Alembic-head, migration-reversibility,
  schema-at-head, and test-DB-isolation checks; CHANGELOG lint; failure artifacts;
  branch protection requiring the `build` check on `main`.
- **Release tooling** — `scripts/release.sh` (guarded, dry-run), `scripts/check_changelog.py`,
  and `scripts/gen_rc.py` + `docs/templates/RC_TEMPLATE.md`.
- **Developer Demo Mode** (previously unreleased tooling) — safety-guarded local demo
  on a `client360_demo` database reusing real auth; `scripts/demo.sh`, role-aware
  landings, docs.

### Changed
- **Runtime Python 3.9 → 3.12.** Resolved the Typer/Click constraint by removing an
  orphaned `typer` pin (imported nowhere, required by nothing) — `click` unchanged.
  `requirements-py39.lock` retained one cycle for rollback.

### Fixed
- **Importers no longer run on import** — `schwab`, `wealthbox`, and `dave_ramsey`
  read `app/.env`, built an engine, and ran a real client-data import merely as a
  side effect of being imported. `dave_ramsey` alone wrote 7,755+ records per test run.
- `benefits` routes: `payload.dict()` → `model_dump()` (Pydantic v2 deprecation).
- Latent Jinja template bugs where `data.items`/`work.items`/`tax.items` resolved to
  the dict `.items` method; `/work`, `/tax/intake`, `/tax`, and the work queue pages render.

### Migrations
None — 0.9.13 is tooling/infrastructure only. Single head `u1f9c0i9h8g7`.

## [0.9.12] — 2026-07-16 — Application Shell & UI Consolidation

Consolidated every staff-facing page into a single application shell on a shared design
system, with progressive-enhancement interaction polish. **Frontend only** — no business
logic, routes, authorization, or record-scope semantics changed; no schema change (Alembic
head unchanged at `u1f9c0i9h8g7`). Validated by [RC-1](docs/RC1_UI_VALIDATION.md)
(0 unmet criteria). Merge commit `98b0622`.

### Added
- **Application shell + design system** — all 21 staff routes render inside one shell
  (`docs/UI_DESIGN_SYSTEM.md`); shared components, styled 403/404/500 pages, empty states.
- **Interaction polish** — client-side sortable data tables (numeric-aware, `aria-sort`),
  skip-to-content link, `aria-current`/`aria-expanded` navigation state. Progressive
  enhancement: every page works fully with JavaScript disabled.

### Fixed
- **Authorization denials now content-negotiate** — browser navigations get a styled HTML
  403, API/JSON clients keep the JSON body; the denial itself (status, audit) is unchanged.
  Denials now also carry the standard security headers (`x-frame-options`, CSP
  `frame-ancestors`, `nosniff`, `referrer-policy`), which they previously lacked.
- **CI was never running** — the workflow was tab-indented (invalid YAML) and failed at 0s on
  every commit, including the 0.9.11 merge. It now parses, provisions Postgres, and gates.
- **Importers are side-effect free on import** — `app/importers/schwab.py` and `wealthbox.py`
  no longer read `app/.env`, build an engine, or run a client-data import merely on import.
- Flaky securities-symbol collision fixed in the portfolio query tests.

### Migrations
None — 0.9.12 is frontend/tooling only. Single head `u1f9c0i9h8g7`.

## [0.9.11] — 2026-07-15 — Employer Operations & Employee Benefits

Usable **Employer Operations** product on shared Client360 concepts (Organizations,
relationship roles, service lines, universal Engagement) with **Employee Benefits + Retirement**
first-class (ADR-18). Reuses Person/Household, Documents, Work Management, Timeline, Audit, the
Exception Engine, the Portal, and the scheduler — no second engine/scheduler/portal/workflow/
reporting framework/data model. Tax untouched. Validated by [RC14](docs/RC14_VALIDATION.md)
(**SAFE TO MERGE**, 0 defects). See [Release 0.9.11 Notes](docs/RELEASE_0.9.11.md). Alembic
head `u1f9c0i9h8g7`.

### Added
- **Organization foundation** — `relationship_entities` + `organization_profiles` (EIN
  encrypted); permanent relationship roles; typed ownership (`relationship_ownership`); service
  lines; universal `engagements`; canonical services with Organization record scope; disabled
  carrier/recordkeeper(Betterment)/payroll/HRIS ports.
- **Benefits & retirement** — 17 plan types, plans/plan-years, employments/enrollments/deferral
  elections; Betterment seeded (no integration).
- **Detectors** — 18 health + retirement detectors (`domain='benefits'`; idempotent/auto-resolve/
  reopen); date-driven obligation detector; documented inert gaps (never inferred).
- **Compliance & renewal obligations** — templates + instantiated obligations (verified dates);
  shared SLA sweep extended to benefits (internal-only, honest outcomes).
- **Work Management** — benefits exceptions in the canonical `work_items()` + seven benefits
  queues; assignment rules; scheduled scan (overlap-prevented, per-org isolation, honest metrics).
- **Staff API + consoles** — `/api/v1/organizations` + `/api/v1/benefits`; `/organizations`,
  `/benefits`, `/benefits/reporting` on the modern shell (names not IDs; EIN gated).
- **Employer portal** — org-scoped Action Needed (PII-free allowlist), census upload, secure
  messages, auditable employer notifications.
- **Dashboards & reporting** — proportional benefits dashboard (book, participation,
  compliance/renewal calendar, exceptions); authorization-filtered; reuses `exception_reporting`.
- New `organization.*` / `benefits.*` capabilities + `benefits_*` roles (no role widened; no new
  `record.read_all`).

### Migrations
`r8c69f7e6d5c` · `s9d7a8g7f6e5` · `t0e8b9h8g7f6` (data-only) · `u1f9c0i9h8g7`. Single head.

## [0.9.10] — 2026-07-14 — Exception Engine

Platform-wide **Exception Engine** (ADR-17), implemented **tax domain only**. Validated by
[RC13](docs/RC13_VALIDATION.md) (**SAFE TO MERGE**, 0 defects); merged to `main` and tagged
`v0.9.10`. See [Release 0.9.10 Notes](docs/RELEASE_0.9.10.md). Alembic head `q7b58f6c5d4e`.

### Added

- **Canonical Exception Engine** — domain-neutral `exceptions` / `exception_events` /
  `exception_types` (required CHECK-constrained `domain`); one state machine, idempotent
  dedupe, stale-action rejection, immutable append-only event ledger, audit + timeline on
  every mutation, and record-scope authorization on every read/write.
- **15 tax detectors** translating existing tax source-of-truth conditions into exceptions
  (stable dedupe keys; auto-resolve on clear, reopen on recurrence).
- **Deterministic, replay-safe SLA sweep** with severity-based escalation and **honest
  notification outcomes** (email/SMS stubbed → `disabled`, never fabricated).
- **Work Management integration** — exceptions project through the single `work_items()`
  point into My/Team Work, queues (`tax_exceptions`, `tax_exceptions_critical`,
  `compliance_exceptions`), agenda, capacity, and bottlenecks; reuses `record_assignments`
  (no second assignment model).
- **Versioned API + staff console** (`/api/v1/exceptions/*`, `/exceptions`) — thin routes
  over canonical services; out-of-scope → 404; blocker/compliance resolution segregation.
- **Client portal "Action Needed"** (`/portal/action-needed`,
  `/api/v1/portal/exceptions[/{id}]`) — strict client-visible allowlist, plain-language,
  scoped, portal-safe, read-only; no internal-field/event/audit leakage.
- **Exception dashboards & reporting** (`/exceptions/reporting`,
  `/api/v1/exceptions/report`) — authorization-filtered metrics (open/blocker/high/at-risk/
  breached/unassigned/compliance, by category/owner/team/client/return, aging, escalation
  distribution, MTTA, MTTR, reopen rate, SLA compliance, real trend); role-appropriate
  audiences; compact summary embedded on advisor/tax/operations dashboards.
- New least-privilege capabilities `exception.read` / `exception.write` /
  `exception.resolve` / `exception.compliance` (no role widened; no new `record.read_all`).

### Migrations

- `p6a47e5d4f3b` — exception engine schema (additive/reversible).
- `q7b58f6c5d4e` — data-only work-queue criteria (reversible). Single head.

## [0.9.9] — 2026-07-14

Platform Consolidation — a security, performance, and production-readiness
release with no new end-user features. See
[Release 0.9.9 Notes](docs/RELEASE_0.9.9.md) and
[RC12 Validation](docs/RC12_VALIDATION.md).

### Security

- Microsoft 365 OAuth tokens encrypted at rest (Fernet-encrypted MSAL cache keyed
  by `MICROSOFT_TOKEN_KEY`) with a durable `acquire_token_silent` refresh
  lifecycle; crypto fails closed when the key is absent; no plaintext token is
  written to the database or logs.
- Delegated Graph scopes reduced to least-privilege read-only (no `Mail.Send`, no
  `*.ReadWrite`).
- CSRF defense-in-depth: `Referer` fallback added to the `Origin` check.
- Config hardening: production boot fails without `SESSION_SECRET`; startup warns
  on a development fallback or a missing `MICROSOFT_TOKEN_KEY`.

### Performance

- 24 hot-path foreign-key indexes (built `CONCURRENTLY`, reversible) making the
  client/household/portal/workflow read paths index-bound.
- Eliminated four verified N+1 / full-scan hot paths (intake dashboard 28→7,
  concentration filter 28→2, portal `/notifications` 21→1, `work_items()`
  authorization pushed into SQL → O(caller's book)), preserving output and
  authorization semantics.

### Changed

- Consolidated the Microsoft Graph connector onto a single delegated path and the
  portal provider registries onto one canonical `ProviderRegistry`.
- Per-account Microsoft sync-health surfaced on `/microsoft365/status` and the new
  `/readiness` endpoint.

### Added

- `GET /readiness` (DB, Alembic head drift, scheduler, sync-health; 200/503);
  `GET /health` remains DB-independent liveness.
- Backup/restore runbook and rehearsal script.

### Removed

- `POST /timeline/test` debug endpoint, the unused app-only Graph connector
  modules, and verified-unused imports across 18 files.

### Migrations

- `m3d14a2f1e0c` (token security columns), `n4e25b3c2f1d` + `o5f36c4d3e2a`
  (hot-path indexes). Additive and reversible; single head `o5f36c4d3e2a`.

## [0.9.8] — 2026-07-14

Sprint 5.4 — Tax Document Intelligence & Missing Information. See
[Release 0.9.8 Notes](docs/RELEASE_0.9.8.md) and
[Tax Document Intelligence](docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md).

### Added

- Deterministic tax document matching engine (exact identifiers, confidence
  scoring, ambiguity floor) with mandatory human review for anything not
  deterministically resolved. Replaces the substring-based Microsoft document
  matching (RC8 H13).
- Authorization-aware ownership validation and record-scope-checked reviewer
  actions (accept/reject/reassign/classify/duplicate/revert) with immutable,
  append-only review and evidence ledgers.
- Missing-information engine that recomputes from accepted document links and
  drives the existing checklist / portal-request / workflow-gating mechanisms.
- Staff document-review workspace and `/api/v1/tax/documents` + checklist/missing
  APIs; new `tax.document.review` capability and four document review queues.
- AI classifier port (interface only; inert — no vendor, no external call).
- Shared tax dashboard stylesheet (`tax.css`), closing an RC8 unstyled-class gap.

- RC11 remediation: wired ingestion end-to-end — portal uploads and Microsoft
  documents now flow through the engine (dual-source links reference either a
  canonical or a Microsoft document, no binary duplicated); made ingestion
  idempotent; added review-state guards (HTTP 409 on stale actions); re-validate
  document owner vs return client on accept/reassign (HTTP 403 + denied audit);
  and persist unmatched documents reviewably without fabricating ownership.

### Database

- Added `tax_document_links`, `tax_document_classifications`,
  `tax_document_match_evidence`, `tax_document_review_events` (append-only), the
  `tax.document.review` capability, four review queues, and the
  `tax_missing_items` FK index (RC9 H20); legacy free-text Microsoft matching
  rules deactivated. RC11 remediation adds a dual-source link model (nullable
  `document_id` + `microsoft_document_id` with an exactly-one-source CHECK) and a
  nullable return for unmatched links. Parent `j0a81f9c8d7e`; new head
  `l2c03f1e0d9b`.

### Security

- Eliminated all substring/containment ownership matching for tax documents
  (H13). Auto-assignment requires a single exact-identifier candidate above the
  auto-match threshold with no competing candidate above the ambiguity floor.

### Validation

- 136 automated tests passed; independent RC11 adversarial validation and retest
  (43/43 checks) confirmed H13 cannot be recreated across nine datasets and that
  the RC11 remediation introduced no new gap (SAFE TO MERGE). Clean installation,
  v0.9.7 upgrade/downgrade/re-upgrade, and sentinel preservation validated. See
  [RC11 Validation](docs/RC11_VALIDATION.md) and [RC11 Retest](docs/RC11_RETEST.md).

## [0.9.7] — 2026-07-14

Security hardening release. Fixes the confirmed, RC9-verified authorization,
record-scope, and workflow-permission defects before Sprint 5.4. No new feature
work; least privilege, immutable audit, and record-level authorization
preserved. See [Security Hardening 0.9.7](docs/SECURITY_HARDENING_0.9.7.md).

### Security

- Fixed work-assignment privilege escalation: assigning a client record now
  requires `assignment.manage` plus record scope, separated from ordinary
  `work.write` mutation (H1); reassign/remove now enforce assignment ownership
  (H8).
- Fixed role-composition privilege escalation: `role.manage` can only grant
  capabilities it holds and cannot assign a more-powerful role or recompose the
  protected administrator role (H2).
- Enforced record-scope authorization consistently on tax return review and
  correction endpoints (H3).
- Corrected the middleware/route capability mismatch that locked the compliance
  role out of workflow approvals (H4).
- Required authorization over a relationship's owning record before
  deactivation (H5).
- Scoped client-profile pickers to prevent firm-wide name/email enumeration
  (H6).
- Enforced the portal `messages` grant on secure-message read/send/mark-read
  with default-deny (H7).
- Restricted the firm-wide reminder trigger to firm-wide record authority (H9).

### Fixed

- Rewrote the always-zero "Unassigned" tax dashboard metric (H11) and the
  always-zero "pending matches" dashboard metric (H14).
- Eliminated a duplicate database connection pool created at startup via the
  `person_merge` import chain (H22, narrow fix).

### Added

- Canonical record-scope authorization service (`app/security/authorization.py`)
  and 20 authorization regression tests.
- Immutable `outcome="denied"` audit events for denied high-risk mutations.

### Database

- Migration `j0a81f9c8d7e` aligns `tax_engagement_returns.status` server default
  to `received` (parent `i970d9f7b8c9`; new head `j0a81f9c8d7e`).

### Validation

- 94 automated tests passed (74 existing + 20 new), clean installation, v0.9.6
  upgrade/downgrade/re-upgrade, sentinel preservation, startup, route, OpenAPI,
  template, authorization-matrix, and immutable-audit validation.
- Independent RC10 adversarial validation passed (52/52 attack cases blocked;
  no unintended regressions; SAFE TO MERGE). See
  [RC10 Validation](docs/RC10_VALIDATION.md).

## [0.9.6] — 2026-07-14

### Added

- Canonical 15-state tax return lifecycle with immutable transition history.
- Preparer, manager, and partner reviews linked to the existing independent
  approval engine, including corrections and return-to-preparer behavior.
- Portal return approval, e-file authorization, delivery acknowledgement,
  provider-neutral filing events, nine production queues, four dashboards, and
  versioned staff/portal APIs.

### Database

- Added five production tables and ten return lifecycle/filing columns with
  parent `h860c8e6a7b8`; new head `i970d9f7b8c9`.

### Validation

- Added the Tax Return Lifecycle architecture and PR #16 RC7 validation
  record.
- Passed 74 automated tests, clean installation, v0.9.5 upgrade/downgrade/
  re-upgrade, sentinel preservation, startup, route, OpenAPI, and template
  validation.
- Found and fixed two template defects during release-candidate validation:
  a missing shared staff base template and a Jinja/dict-key collision on the
  production dashboard.

## [0.9.5] — 2026-07-14

### Added

- Versioned engagement-letter, organizer, questionnaire, and document-checklist
  templates with immutable published definitions and launch-time snapshots.
- Tax intake orchestration, saved progress, conditional/required questions,
  missing-information tracking, portal completion, daily reminders, readiness
  dashboards, and automatic workflow advancement.
- Versioned staff and portal APIs for tax intake, backed by existing document,
  notification, assignment, queue, timeline, audit, and authorization services.

### Database

- Added 12 intake tables with parent revision `g750b7d5f6a7`; new head
  `h860c8e6a7b8`.

### Validation

- Added the Tax Engagement Intake architecture and RC6 validation report.
- Passed 69 automated tests, clean installation, v0.9.4 rollback/re-upgrade,
  sentinel preservation, startup, route, OpenAPI, and template validation.

## [0.9.4] — 2026-07-14

### Added

- Provider-neutral tax firms, offices, staff office roles, tax years, seasons,
  filing jurisdictions, return types, filing statuses, engagements, returns,
  calendars, versioned deadline rules, and workflow links.
- Authorized tax production dashboard and versioned `/api/v1/tax` reference,
  dashboard, engagement, and deadline operations.
- Five reusable tax work queues, four tax capabilities, eight baseline return
  types, six filing statuses, and a versioned Tax Engagement Foundation workflow.
- Automatic engagement workflow generation with existing assignment, queue,
  timeline, immutable audit, and record-level authorization integration.

### Documentation

- Added the nine-sprint Epic 5 Tax Practice Platform technical design.
- Defined normalized tax, workflow, portal, document, provider, security,
  reporting, migration, testing, and Release 1.0 readiness architecture.
- Added Tax Domain Foundation operating documentation and the RC5 release
  validation report.

### Database

- Alembic head: `g750b7d5f6a7`.
- Added 14 normalized tax-domain tables while preserving Release v0.9.3 data.

## [0.9.3] — 2026-07-14

### Added

- Separate portal identities, household/delegated grants, invitations,
  MFA-ready sessions, password-reset handoff, and device tracking.
- Secure client messaging, internal-note isolation, attachments, and receipts.
- Document requests, upload versions, approvals, client workflow tasks,
  notifications, and provider-neutral e-signature abstractions.
- Versioned portal APIs and eight portal pages.

### Security

- Portal accounts and sessions are isolated from staff identities.
- Self-only, joint, trusted-contact, and delegated household grants are
  explicitly scoped and time bounded.
- Messages, read receipts, route mutations, and security events are audited;
  client-visible queries exclude internal staff notes.

### Database

- Alembic head: `f640a6c4e5f6`.
- Added 15 portal identity, access, session, collaboration, notification, and
  signature-request tables without changing Release 0.9.2 data.

## [0.9.2] — 2026-07-14

### Added

- Immutable, versioned workflow templates with complete launch-time snapshots.
- Dependency-aware sequential, parallel, and conditional workflow execution.
- Pause, resume, cancel, complete, and reopen controls.
- Independent approval routing with segregation-of-duties enforcement.
- SLA escalation processing and five-minute scheduler automation.
- Event-driven triggers and an idempotent automation action ledger.
- Workflow UI, metrics, reporting data, and `/api/v1/workflows` APIs.
- Twelve published templates for prospecting, onboarding, Schwab operations,
  transfers, reviews, tax, estate, insurance, termination, and compliance.

### Changed

- Workflow-instance assignments now authorize and expose child workflow steps
  in My Work.
- Published template definitions and workflow/audit event ledgers are protected
  by database triggers.

### Database

- Alembic head: `e530f5b3d4e5`.
- Added seven tables for templates, dependencies, events, triggers, actions, and
  escalations.
- Added execution snapshots and lifecycle metadata to Release 0.9.1 workflow
  records without replacing existing data.

## [0.9.1] — 2026-07-14

- Added Operational Work Management, assignments, reusable queues, My Work,
  Team Work, capacity, SLA risk, and versioned work APIs.
- Alembic head: `d420f4a2c3d4`.

## [0.9.0] — 2026-07-14

- Integrated Microsoft 365, Relationship Intelligence, Schwab Portfolio
  Intelligence, firm identity, capability authorization, and immutable audit.
- Alembic head: `c410f4a1b2c3`.
