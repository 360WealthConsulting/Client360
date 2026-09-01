"""Dedicated capabilities for secure client Messages (staff Communication Hub).

Data-only, reversible, no schema change. Same shape as ``d0l1n2o3i4k5`` (insurance.scan): name an
authority explicitly instead of overloading a coarser one.

WHY. The staff Messages surface (``/admin/client-portal/threads``) sat under the generic
``^/admin`` -> ``identity.manage`` middleware rule, so only the administrator could open it. Gating
it on ``client.read`` instead would have made it reachable, but ``client.read`` is held by eleven
roles - including Accounting, Payroll, Reviewer and Read Only, who have no business reading a
client's correspondence. Reading client messages is a narrower authority than reading a client
record, so it gets its own capability.

Two capabilities, not one, because the middleware infers the mutating capability from the read one
by ``.read`` -> ``.write`` (see ``app/security/middleware.py``): a lone read capability would leave
every POST demanding a ``communications.message.write`` that does not exist, and nobody could reply.
The pair is what keeps view and reply separately gated:

    communications.message.read   administrator, advisor, client_service, operations, senior_tax,
                                  tax_staff
    communications.message.write  administrator, advisor, client_service, operations, senior_tax

Placed in the existing ``communications.*`` family alongside ``communications.view`` /
``communications.send`` / ``communications.admin``. Neither of the existing pair fit:
``communications.view`` reaches Compliance but not Senior Tax or Tax Staff, and ``communication.read``
reaches Compliance and Read Only. This is additive - no existing capability, grant or role changes,
so no principal loses anything.

Record scope is untouched: ``communication_hub.thread_in_staff_scope()`` still decides WHICH threads
a holder can see, per row. These capabilities gate the door, not the contents.

``app/security/role_library.py`` is updated in the same commit for the three NEW_PROFILES affected
(client_service, senior_tax, tax_staff), so the library and this seed cannot drift - the exact-set
assertion in tests/test_production_role_library.py enforces that. The administrator/advisor/operations
grants are on pre-existing profiles, which that library only smoke-asserts.

Single Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "msgcap01"
down_revision = "b4f1a207c9d3"
branch_labels = None
depends_on = None

_READ = "communications.message.read"
_WRITE = "communications.message.write"

_READ_ROLES = ("administrator", "advisor", "client_service", "operations", "senior_tax", "tax_staff")
_WRITE_ROLES = ("administrator", "advisor", "client_service", "operations", "senior_tax")

_CAPABILITIES = (
    (_READ, "View secure client message threads (staff Communication Hub)"),
    (_WRITE, "Reply to, assign, and resolve secure client message threads"),
)


def _grant(bind, code, role_codes):
    bind.execute(sa.text(
        "INSERT INTO role_capabilities (role_id, capability_id) "
        "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
        "WHERE c.code = :code AND r.code IN :roles "
        "ON CONFLICT DO NOTHING").bindparams(sa.bindparam("roles", expanding=True)),
        {"code": code, "roles": list(role_codes)})


def upgrade():
    bind = op.get_bind()
    for code, description in _CAPABILITIES:
        bind.execute(sa.text(
            "INSERT INTO capabilities (code, description, sensitive) "
            "VALUES (:code, :description, false) "
            "ON CONFLICT (code) DO NOTHING"), {"code": code, "description": description})
    _grant(bind, _READ, _READ_ROLES)
    _grant(bind, _WRITE, _WRITE_ROLES)


def downgrade():
    bind = op.get_bind()
    codes = [code for code, _ in _CAPABILITIES]
    bind.execute(sa.text(
        "DELETE FROM role_capabilities WHERE capability_id IN "
        "(SELECT id FROM capabilities WHERE code IN :codes)").bindparams(
            sa.bindparam("codes", expanding=True)), {"codes": codes})
    bind.execute(sa.text(
        "DELETE FROM capabilities WHERE code IN :codes").bindparams(
            sa.bindparam("codes", expanding=True)), {"codes": codes})
