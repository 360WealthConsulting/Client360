# Parallel OCR corpus runner

Status: **proposed, not deployed.** The single-worker runner remains the production path. Nothing in
this document has been cut over.

## The problem

`C:\Client360Data\ocr-fullcorpus\worker.py` sweeps the corpus behind one session-level PostgreSQL
advisory lock (`511005777`) held for the worker's entire life. A second copy exits immediately. That
lock is the only thing preventing two workers from OCR-ing the same document, and the price of it is
that the corpus is swept by exactly one process.

Measured on 360SRV while the production worker ran: 4 physical cores (8 logical), 32 GB RAM, CPU
15.6–23.5%, disk queue 0.0. The box is roughly three-quarters idle and disk is not a factor. The
constraint is the lock, not the hardware.

## The change

Replace the lifetime lock with a **claim per document**.

```
documents ──┐
            ├── ocr_document_claims (document_id PK, worker_id, state, claim_seq,
document_ocr┘                        claimed_at, heartbeat_at, lease_expires_at, outcome)
```

A claim is taken by one statement:

```sql
INSERT INTO ocr_document_claims (...)
SELECT ... FROM (candidate select LIMIT :limit) c
    ON CONFLICT (document_id) DO UPDATE SET ... , claim_seq = t.claim_seq + 1
 WHERE t.state <> 'done' AND t.lease_expires_at < now()
 RETURNING t.document_id, t.claim_seq
```

Two workers racing for the same document serialise on the primary key. The loser's `DO UPDATE` is
gated on the lease having already expired, so it updates nothing and the document is never returned
to it. There is no window in which both believe they hold the document.

### How each requirement is met

| Requirement | Mechanism |
|---|---|
| No duplicate processing | Primary key + lease-gated `ON CONFLICT DO UPDATE ... RETURNING` |
| Short atomic claims | One statement against one table; no lock is held across OCR |
| Leases + stale recovery | `lease_expires_at`; a dead worker's lease simply lapses and is retaken |
| Idempotent writes | `run_ocr` skips completed, content-unchanged documents; `claim_seq` detects a stolen lease before writing |
| Crash and restart safety | Candidates are recomputed from the database on every claim; nothing is held in memory |
| Completed never reprocessed | Candidate predicate excludes `completed`; `state='done'` is never re-handed |
| Slow document cannot block | A lease covers one document; other workers keep claiming freely |
| Configurable worker count | `OCR_PARALLEL_WORKERS`, warned above 4 physical cores |
| Automatic pause | `ocr_throttle.may_claim()` before every claim |

### What deliberately does not change

The runner schedules `app.services.document_ocr.run_ocr`; it does not reimplement it. Preserved
because they stay where they already live:

1. **Existing OCR results** — a completed, content-unchanged document is skipped, never rewritten.
2. **Database-authoritative checkpoint and counters** — recomputed per claim, as today.
3. **Unsupported, encrypted and timeout handling** — untouched inside `_ocr_one`.
4. **Classification and retry lanes** — same modes, and the claim predicates mirror `worker.py`'s
   `SQL_NEVER_ATTEMPTED` and `SQL_RETRYABLE` exactly.
5. **Duplicate-hash reuse** — the `reused`/`skipped` path is `run_ocr`'s and is unchanged.
6. **Audit behaviour** — one audit row per `run_ocr` call, the same granularity as a 50-document
   chunk today.
7. **BELOW_NORMAL process priority** — set per worker process at startup.

`run_sweep`'s per-batch advisory lock (`511005002`) is **not** used. Per-document claiming is a
strictly stronger guarantee, and keeping the coarse lock would serialise the workers.

## Admission control

`ocr_throttle.may_claim()` is consulted before each claim and never mid-document, so throttling can
never truncate OCR or leave a half-written result. Three independent gates:

- **Client360 health — fail closed, on by default.** Both `http://127.0.0.1:8360/health` and
  `/readiness` must answer 200 with a healthy status; unreachable, non-200, or a body reporting
  anything else pauses claiming, and recovery resumes it with no operator action. This needs no
  configuration to be correct. `CLIENT360_HEALTH_URLS` overrides the pair; an explicit empty value
  opts out, and `OCR_HEALTH_GATE=0` disables the gate for tests only.
- **Memory floor** — `OCR_MIN_FREE_MB`, default 2048, mirroring `worker.py`.
- **CPU ceiling** — `OCR_MAX_CPU_PERCENT`, default 85.

## Safety interlock

`run_parallel` refuses to start while the legacy worker holds `511005777`, so the new runner cannot
race the old one by accident. `--allow-beside-legacy` exists for controlled testing and is marked
dangerous.

## The supervisor is the service; the runner is one lane

`ocr_parallel` drains a single lane and exits. It is not a replacement for `worker.py`, which loops
over initial OCR, then retry OCR, then classification catch-up, forever. Swapping one for the other
would silently stop classification and never touch the retry lane.

`app.jobs.ocr_supervisor` is that service. Per pass it drains initial OCR with N workers, drains
retry OCR with the same claim system, runs classification catch-up **sequentially at the existing
batch size of 200**, publishes heartbeat and counters, then repeats. Classification is deliberately
not parallelised: `run_knowledge_pipeline` has no claim system and nothing establishes that
concurrent invocations are safe, so it keeps exactly the behaviour it had.

A second supervisor exits immediately on its own advisory lock (`511005888`), which PostgreSQL
releases when the process dies, so a crash needs no cleanup.

```bash
python -m app.jobs.ocr_supervisor --workers 4 --keep-running
python -m app.jobs.ocr_parallel   --workers 4 --mode initial   # one lane, for diagnostics
```

Install the production task (exports a restorable backup first, and changes no existing task):

```powershell
deploy\windows\install_ocr_supervisor_task.ps1 -Workers 4 -WhatIf
```

Claim-table health:

```sql
SELECT state, count(*) FROM ocr_document_claims GROUP BY state;
SELECT * FROM ocr_document_claims WHERE state='claimed' AND lease_expires_at < now();
```

`ocr_claims.reconcile()` returns claim totals beside the OCR truth so global counts can be proven to
line up.

## Cutover

Controlled, reversible at every step.

1. Apply `ocrclaim01`. Purely additive: a new table, no existing table altered. The single worker is
   unaffected and keeps running.
2. Let the current pass finish, or stop the scheduled task at a chunk boundary. The database is the
   checkpoint, so stopping is always safe.
3. Confirm `511005777` is free.
4. Run `--workers 2` for one lane and reconcile: `done` claims should equal newly completed OCR
   rows, and `duplicate_live_claims()` must be empty.
5. Raise to the recommended worker count.
6. Repoint the scheduled task once a full pass has completed cleanly.

## Rollback

At any point, and at any step:

1. Stop the parallel runner. In-flight documents finish or their leases lapse; either way no OCR row
   is left half-written, because `run_ocr` writes each document's state in one transaction.
2. Re-enable the scheduled task. The single worker recomputes its own outstanding set from the
   database and resumes. It does not read `ocr_document_claims` at all.
3. Optionally `DELETE FROM ocr_document_claims` — the table is scheduling metadata, not OCR results,
   so dropping its contents loses nothing.
4. `alembic downgrade -1` removes the table if the approach is abandoned.

No OCR result, count, or lane is affected by a rollback, because the claim table never held any of
them.
