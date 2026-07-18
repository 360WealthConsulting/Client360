"""Workflow → platform registry binding (F4.1 / Epic 4, ADR-016).

Additive and reversible. Binds a workflow instance to a platform F1.5 registry
template identity (``template_id`` @ ``version``) via two nullable columns on
``workflow_instances`` plus a lookup index. The existing execution engine and its
DB ``workflow_templates`` snapshot are unchanged; these columns are an *additive
association* consumed by the platform adapter (``app/platform/workflow_adapter.py``).

Immutability: once a binding is set it cannot be changed or cleared (ADR-016
"immutable template binding"), enforced by a BEFORE UPDATE trigger that rejects
*only* changes to the binding columns — every other update to ``workflow_instances``
(status, timestamps, …) is unaffected, so existing execution semantics are preserved.

Idempotent DDL (IF NOT EXISTS / IF EXISTS), consistent with the F3.2/F3.3
migrations. The columns are intentionally NOT declared in work_tables.py /
schema.py; app.db reflects them at runtime (ADR-016 reflection preservation; see
docs/DATABASE.md).
"""
from alembic import op

revision = "f41b2n3d4c5e"
down_revision = "f3d4e5v6i7d8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS platform_template_ref TEXT")
    op.execute("ALTER TABLE workflow_instances ADD COLUMN IF NOT EXISTS platform_template_version INTEGER")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_instances_platform_template "
        "ON workflow_instances (platform_template_ref, platform_template_version)"
    )
    # Immutable binding: reject changes to the binding columns once set; allow the
    # initial NULL -> value bind and no-op rewrites; never block other updates.
    op.execute(
        "CREATE OR REPLACE FUNCTION protect_workflow_platform_binding() RETURNS trigger AS $$ "
        "BEGIN "
        "  IF OLD.platform_template_ref IS NOT NULL "
        "     AND (NEW.platform_template_ref IS DISTINCT FROM OLD.platform_template_ref "
        "          OR NEW.platform_template_version IS DISTINCT FROM OLD.platform_template_version) THEN "
        "    RAISE EXCEPTION 'workflow platform template binding is immutable once set'; "
        "  END IF; "
        "  RETURN NEW; "
        "END; $$ LANGUAGE plpgsql"
    )
    op.execute("DROP TRIGGER IF EXISTS workflow_instance_binding_immutable ON workflow_instances")
    op.execute(
        "CREATE TRIGGER workflow_instance_binding_immutable BEFORE UPDATE ON workflow_instances "
        "FOR EACH ROW EXECUTE FUNCTION protect_workflow_platform_binding()"
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS workflow_instance_binding_immutable ON workflow_instances")
    op.execute("DROP FUNCTION IF EXISTS protect_workflow_platform_binding()")
    op.execute("DROP INDEX IF EXISTS ix_workflow_instances_platform_template")
    op.execute("ALTER TABLE workflow_instances DROP COLUMN IF EXISTS platform_template_version")
    op.execute("ALTER TABLE workflow_instances DROP COLUMN IF EXISTS platform_template_ref")
