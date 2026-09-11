"""Continuous document pipeline — durable queue, blockers, one review lane, worker registry.

The existing document path is a set of SWEEPS: ``ocr_runner`` walks the corpus in batches,
``document_pipeline.run_batch`` re-analyses every unassigned document, and both start from the
beginning of the corpus on every invocation. A sweep is fine for a migration and wrong for a
continuously-fed corpus: it cannot resume mid-document after a restart, it re-reads work it already
finished, and it holds no per-document claim, so two runners started a minute apart process the same
document twice.

This migration adds the state a CONTINUOUS pipeline needs and the sweeps deliberately do not have:

``document_pipeline_tasks``
    One row per canonical document — the unit of work and the mutual-exclusion boundary. A worker
    claims a row (``lease_owner`` + ``lease_expires_at``), advances it through the stages, and either
    completes it or schedules a retry at ``available_at``. One row per document is what makes "two
    workers never process the same document" a database invariant rather than a convention: the claim
    is ``UPDATE ... WHERE state='queued' ... FOR UPDATE SKIP LOCKED`` against a UNIQUE document_id.
    ``content_sha256`` records the content the task was enqueued for, so a document whose bytes change
    is re-queued while an unchanged one is never re-processed.

``document_pipeline_blockers``
    Permanent failures. A task that exhausts its attempts, or fails in a way retrying cannot fix
    (an encrypted PDF, a missing file), leaves the queue and lands here, where it is countable and
    visible instead of silently re-attempted forever. One open blocker per document.

``document_pipeline_ownership_reviews``
    THE review lane — one queue, not one per source. Only ambiguous SharePoint-evidence documents
    arrive here: Drake and TaxDome are authoritative lanes that either resolve or block. This table
    records why a document is ambiguous and what the defensible candidates were, so the existing
    per-document approval path (``households.resolve_document_ownership``) can act on it unchanged.

``document_pipeline_workers``
    Worker registry + heartbeat. Health detection for a stalled pipeline reads ``last_heartbeat_at``
    here; lease reclamation reads it to tell a slow worker from a dead one.

``document_pipeline_checkpoints``
    Discovery resume points. Discovery is incremental and unbounded — it walks forward from the last
    document it saw rather than re-scanning the corpus — so a restart continues instead of restarting.

Additive and reversible. NOTHING in this migration changes ownership, documents, OCR state, or any
existing table: the pipeline's authoritative writes go through the services that already own those
rules. No new capability is seeded — the read surfaces reuse ``documents.view``.

Revision ID: docpipe01
Revises: drake03
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "docpipe01"
down_revision = "drake03"
branch_labels = None
depends_on = None

# The stage a task is currently at. Stages advance in this order and never go backwards within one
# attempt; a re-queued document (changed content) restarts at 'discovered'.
STAGES = ("discovered", "extract", "ocr", "classify", "ownership", "done")

# The task's lifecycle state. 'leased' is the only state a worker holds; everything else is at rest.
STATES = ("queued", "leased", "succeeded", "failed", "blocked", "review")

# What the ownership stage concluded, once a task reaches a terminal state. Kept as a column rather
# than derived from the other tables so the operational counters (linked / review / unresolved) are one
# GROUP BY over one table instead of a join across three.
OUTCOMES = ("linked", "review", "unresolved", "already_owned", "blocked")

_WORKER_STATES = ("starting", "running", "draining", "stopped")
_BLOCKER_STATUSES = ("open", "resolved", "dismissed")
_REVIEW_STATUSES = ("open", "resolved", "dismissed")


def _in(column, values):
    return f"{column} IN (" + ",".join(f"'{v}'" for v in values) + ")"


def _ts(name, **kw):
    return sa.Column(name, sa.TIMESTAMP(timezone=True), **kw)


def _now(name):
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.create_table(
        "document_pipeline_tasks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        # The content this task was enqueued for. A document whose sha256 later differs is re-queued;
        # one whose sha256 is unchanged is never re-processed, which is the whole of idempotency here.
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("stage", sa.Text, nullable=False, server_default="discovered"),
        sa.Column("state", sa.Text, nullable=False, server_default="queued"),
        # Lower runs sooner. Retries keep their priority so a failing document cannot starve new work
        # by jumping the queue, and cannot be starved by it either.
        sa.Column("priority", sa.SmallInteger, nullable=False, server_default="100"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("stage_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        # Retry backoff: a failed task is invisible to claiming until now() >= available_at.
        _now("available_at"),
        sa.Column("lease_owner", sa.Text),
        _ts("leased_at"),
        _ts("lease_expires_at"),
        # Set when this document's OCR was satisfied by an identical (same sha256) completed document
        # rather than by running the engine again.
        sa.Column("reused_ocr_from_document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="SET NULL")),
        sa.Column("outcome", sa.Text),
        sa.Column("last_error", sa.Text),
        sa.Column("last_error_class", sa.Text),
        sa.Column("stage_history", JSONB, nullable=False, server_default="[]"),
        _now("enqueued_at"),
        _ts("started_at"),
        _ts("completed_at"),
        _now("updated_at"),
        sa.CheckConstraint(_in("stage", STAGES), name="ck_document_pipeline_task_stage"),
        sa.CheckConstraint(_in("state", STATES), name="ck_document_pipeline_task_state"),
        sa.CheckConstraint("attempts >= 0 AND stage_attempts >= 0 AND max_attempts > 0",
                           name="ck_document_pipeline_task_attempts"),
        sa.CheckConstraint("outcome IS NULL OR " + _in("outcome", OUTCOMES),
                           name="ck_document_pipeline_task_outcome"),
        # One task per canonical document: the claim's mutual exclusion rests on this.
        sa.UniqueConstraint("document_id", name="uq_document_pipeline_task_document"),
    )
    # The claim query's index: claimable work ordered the way the claim orders it. Partial, so it stays
    # small as the completed corpus grows past the work still to do.
    op.create_index("ix_document_pipeline_tasks_claimable", "document_pipeline_tasks",
                    ["priority", "available_at", "id"],
                    postgresql_where=sa.text("state = 'queued'"))
    # Lease reclamation: find expired leases without scanning the finished corpus.
    op.create_index("ix_document_pipeline_tasks_lease", "document_pipeline_tasks",
                    ["lease_expires_at"], postgresql_where=sa.text("state = 'leased'"))
    op.create_index("ix_document_pipeline_tasks_state_stage", "document_pipeline_tasks",
                    ["state", "stage"])
    # Dedupe lookup: "is there a completed task for this exact content?"
    op.create_index("ix_document_pipeline_tasks_sha", "document_pipeline_tasks", ["content_sha256"])
    # Throughput windows read completed_at; only finished rows have one.
    op.create_index("ix_document_pipeline_tasks_completed_at", "document_pipeline_tasks",
                    ["completed_at"], postgresql_where=sa.text("completed_at IS NOT NULL"))

    op.create_table(
        "document_pipeline_blockers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("detail", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        _now("first_seen_at"),
        _now("last_seen_at"),
        _ts("resolved_at"),
        sa.Column("resolved_by_user_id", sa.Integer),
        sa.Column("resolution_note", sa.Text),
        sa.CheckConstraint(_in("status", _BLOCKER_STATUSES), name="ck_document_pipeline_blocker_status"),
        sa.CheckConstraint(_in("stage", STAGES), name="ck_document_pipeline_blocker_stage"),
        # One blocker row per document: a re-blocked document updates its row rather than growing a
        # pile of duplicates that makes the queue depth meaningless.
        sa.UniqueConstraint("document_id", name="uq_document_pipeline_blocker_document"),
    )
    op.create_index("ix_document_pipeline_blockers_status", "document_pipeline_blockers",
                    ["status", "reason_code"])

    op.create_table(
        "document_pipeline_ownership_reviews",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("document_id", sa.Integer,
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        # Which ownership lane produced this row. Drake and TaxDome are authoritative and normally
        # resolve or block; 'sharepoint' is the lane whose ambiguity this queue exists for.
        sa.Column("lane", sa.Text, nullable=False),
        sa.Column("reason_code", sa.Text, nullable=False),
        # Already-sanitised evidence (no raw document text, no full SSN/TIN) and the defensible
        # candidates, so a reviewer decides from the queue instead of re-opening the document.
        sa.Column("evidence", JSONB, nullable=False, server_default="[]"),
        sa.Column("candidates", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.Text, nullable=False, server_default="open"),
        _now("opened_at"),
        _now("updated_at"),
        _ts("resolved_at"),
        sa.Column("resolved_by_user_id", sa.Integer),
        sa.Column("resolution_note", sa.Text),
        sa.CheckConstraint(_in("status", _REVIEW_STATUSES), name="ck_document_pipeline_review_status"),
        sa.UniqueConstraint("document_id", name="uq_document_pipeline_review_document"),
    )
    op.create_index("ix_document_pipeline_reviews_status", "document_pipeline_ownership_reviews",
                    ["status", "lane"])

    op.create_table(
        "document_pipeline_workers",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("worker_id", sa.Text, nullable=False),
        sa.Column("host", sa.Text),
        sa.Column("pid", sa.Integer),
        sa.Column("state", sa.Text, nullable=False, server_default="starting"),
        sa.Column("current_document_id", sa.Integer),
        sa.Column("claimed_total", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("completed_total", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("failed_total", sa.BigInteger, nullable=False, server_default="0"),
        _now("started_at"),
        _now("last_heartbeat_at"),
        _ts("stopped_at"),
        sa.CheckConstraint(_in("state", _WORKER_STATES), name="ck_document_pipeline_worker_state"),
        sa.UniqueConstraint("worker_id", name="uq_document_pipeline_worker"),
    )
    op.create_index("ix_document_pipeline_workers_heartbeat", "document_pipeline_workers",
                    ["last_heartbeat_at"])

    op.create_table(
        "document_pipeline_checkpoints",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        # Discovery walks forward by document id; the timestamp cursor catches content changes to
        # documents already behind the id cursor.
        sa.Column("cursor_document_id", sa.BigInteger, nullable=False, server_default="0"),
        _ts("cursor_updated_at"),
        sa.Column("documents_seen", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("documents_enqueued", sa.BigInteger, nullable=False, server_default="0"),
        _ts("last_run_at"),
        sa.Column("last_error", sa.Text),
        _now("updated_at"),
        sa.UniqueConstraint("name", name="uq_document_pipeline_checkpoint_name"),
    )
    # Seed the single discovery checkpoint so a first run resumes from a row that already exists
    # rather than racing two workers to create it.
    op.execute(sa.text(
        "INSERT INTO document_pipeline_checkpoints (name) VALUES ('discovery') "
        "ON CONFLICT ON CONSTRAINT uq_document_pipeline_checkpoint_name DO NOTHING"))


def downgrade() -> None:
    op.drop_table("document_pipeline_checkpoints")
    op.drop_index("ix_document_pipeline_workers_heartbeat", table_name="document_pipeline_workers")
    op.drop_table("document_pipeline_workers")
    op.drop_index("ix_document_pipeline_reviews_status",
                  table_name="document_pipeline_ownership_reviews")
    op.drop_table("document_pipeline_ownership_reviews")
    op.drop_index("ix_document_pipeline_blockers_status", table_name="document_pipeline_blockers")
    op.drop_table("document_pipeline_blockers")
    for index in ("ix_document_pipeline_tasks_completed_at", "ix_document_pipeline_tasks_sha",
                  "ix_document_pipeline_tasks_state_stage", "ix_document_pipeline_tasks_lease",
                  "ix_document_pipeline_tasks_claimable"):
        op.drop_index(index, table_name="document_pipeline_tasks")
    op.drop_table("document_pipeline_tasks")
