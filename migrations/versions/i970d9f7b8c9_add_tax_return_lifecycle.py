"""add tax return lifecycle and production automation

Revision ID: i970d9f7b8c9
Revises: h860c8e6a7b8
"""
from alembic import op
import sqlalchemy as sa

revision="i970d9f7b8c9"; down_revision="h860c8e6a7b8"; branch_labels=None; depends_on=None

TABLES=("tax_return_lifecycle_events","tax_return_reviews","tax_review_corrections","tax_client_approvals","tax_filing_events")

def upgrade():
    for name,column in (
        ("status_entered_at",sa.Column("status_entered_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now())),
        ("preparation_started_at",sa.Column("preparation_started_at",sa.DateTime(timezone=True))),
        ("preparation_completed_at",sa.Column("preparation_completed_at",sa.DateTime(timezone=True))),
        ("filed_at",sa.Column("filed_at",sa.DateTime(timezone=True))),
        ("accepted_at",sa.Column("accepted_at",sa.DateTime(timezone=True))),
        ("delivered_at",sa.Column("delivered_at",sa.DateTime(timezone=True))),
        ("archived_at",sa.Column("archived_at",sa.DateTime(timezone=True))),
        ("filing_status",sa.Column("filing_status",sa.String(30),nullable=False,server_default="ready")),
        ("filing_provider_key",sa.Column("filing_provider_key",sa.String(100),nullable=False,server_default="manual")),
        ("filing_external_id",sa.Column("filing_external_id",sa.String(500))),
    ): op.add_column("tax_engagement_returns",column)
    op.execute("UPDATE tax_engagement_returns SET status='received' WHERE status='not_started'")
    op.create_table("tax_return_lifecycle_events",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("tax_engagement_return_id",sa.Integer(),sa.ForeignKey("tax_engagement_returns.id",ondelete="CASCADE"),nullable=False),sa.Column("from_status",sa.String(40)),sa.Column("to_status",sa.String(40),nullable=False),sa.Column("reason",sa.Text()),sa.Column("actor_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("portal_account_id",sa.Integer(),sa.ForeignKey("portal_accounts.id",ondelete="SET NULL")),sa.Column("workflow_step_id",sa.Integer(),sa.ForeignKey("workflow_steps.id",ondelete="SET NULL")),sa.Column("metadata",sa.JSON(),nullable=False,server_default="{}"),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table("tax_return_reviews",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("tax_engagement_return_id",sa.Integer(),sa.ForeignKey("tax_engagement_returns.id",ondelete="CASCADE"),nullable=False),sa.Column("review_type",sa.String(30),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="pending"),sa.Column("reviewer_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("reviewer_team_id",sa.Integer(),sa.ForeignKey("teams.id",ondelete="SET NULL")),sa.Column("work_approval_id",sa.Integer(),sa.ForeignKey("work_approvals.id",ondelete="SET NULL")),sa.Column("notes",sa.Text()),sa.Column("requested_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column("completed_at",sa.DateTime(timezone=True)),sa.Column("returned_at",sa.DateTime(timezone=True)),sa.UniqueConstraint("tax_engagement_return_id","review_type",name="uq_tax_return_review_type"))
    op.create_table("tax_review_corrections",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("tax_return_review_id",sa.Integer(),sa.ForeignKey("tax_return_reviews.id",ondelete="CASCADE"),nullable=False),sa.Column("description",sa.Text(),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="open"),sa.Column("created_by_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("resolved_by_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column("resolved_at",sa.DateTime(timezone=True)))
    op.create_table("tax_client_approvals",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("tax_engagement_return_id",sa.Integer(),sa.ForeignKey("tax_engagement_returns.id",ondelete="CASCADE"),nullable=False),sa.Column("approval_type",sa.String(40),nullable=False),sa.Column("status",sa.String(30),nullable=False,server_default="pending"),sa.Column("portal_account_id",sa.Integer(),sa.ForeignKey("portal_accounts.id",ondelete="SET NULL")),sa.Column("decision_notes",sa.Text()),sa.Column("requested_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column("decided_at",sa.DateTime(timezone=True)),sa.UniqueConstraint("tax_engagement_return_id","approval_type",name="uq_tax_client_approval_type"))
    op.create_table("tax_filing_events",sa.Column("id",sa.Integer(),primary_key=True),sa.Column("tax_engagement_return_id",sa.Integer(),sa.ForeignKey("tax_engagement_returns.id",ondelete="CASCADE"),nullable=False),sa.Column("filing_status",sa.String(30),nullable=False),sa.Column("provider_key",sa.String(100),nullable=False,server_default="manual"),sa.Column("external_id",sa.String(500)),sa.Column("submission_id",sa.String(500)),sa.Column("reason_code",sa.String(100)),sa.Column("message",sa.Text()),sa.Column("actor_user_id",sa.Integer(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("idempotency_key",sa.String(255),nullable=False,unique=True),sa.Column("metadata",sa.JSON(),nullable=False,server_default="{}"),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_index("ix_tax_return_lifecycle_status","tax_engagement_returns",["status","status_entered_at"])
    op.create_index("ix_tax_lifecycle_events_return","tax_return_lifecycle_events",["tax_engagement_return_id","created_at"])
    op.create_index("ix_tax_reviews_status","tax_return_reviews",["review_type","status"])
    op.create_index("ix_tax_filing_events_return","tax_filing_events",["tax_engagement_return_id","created_at"])
    bind=op.get_bind()
    for code,name,status in (("tax_production_ready","Tax — Ready to Prepare","ready_to_prepare"),("tax_production_preparing","Tax — Preparing","in_preparation"),("tax_production_awaiting_client","Tax — Awaiting Client","awaiting_information"),("tax_production_manager_review","Tax — Manager Review","manager_review"),("tax_production_partner_review","Tax — Partner Review","partner_review"),("tax_production_ready_to_file","Tax — Ready to File","ready_to_file"),("tax_production_rejected","Tax — Rejected","rejected"),("tax_production_delivery","Tax — Delivery","accepted"),("tax_production_completed_today","Tax — Completed Today","completed")):
        bind.execute(sa.text("INSERT INTO work_queues(code,name,description,criteria,required_capability) VALUES (:c,:n,:n,CAST(:x AS json),'tax.read')"),{"c":code,"n":name,"x":__import__('json').dumps({"work_type":"tax","status":status})})
    op.execute("CREATE FUNCTION prevent_tax_production_event_mutation() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'tax production events are append-only'; END; $$ LANGUAGE plpgsql")
    for table in ("tax_return_lifecycle_events","tax_filing_events"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_tax_production_event_mutation()")

def downgrade():
    for table in ("tax_return_lifecycle_events","tax_filing_events"): op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_tax_production_event_mutation()")
    op.execute("DELETE FROM work_queues WHERE code LIKE 'tax_production_%'")
    op.execute("DELETE FROM work_approvals WHERE entity_type='tax_return' AND approval_type LIKE 'tax_%_review'")
    op.execute("DELETE FROM portal_notifications WHERE notification_type LIKE 'tax_return_%' AND entity_type='tax_return'")
    op.drop_index("ix_tax_return_lifecycle_status",table_name="tax_engagement_returns")
    for name in reversed(TABLES): op.drop_table(name)
    for name in ("filing_external_id","filing_provider_key","filing_status","archived_at","delivered_at","accepted_at","filed_at","preparation_completed_at","preparation_started_at","status_entered_at"): op.drop_column("tax_engagement_returns",name)
