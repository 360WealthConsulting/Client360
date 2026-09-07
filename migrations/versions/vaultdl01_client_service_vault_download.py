"""Client Service gains ``vault.download`` — the one capability its document access was missing.

Data-only, reversible, no schema change. Same shape as ``msgcap01``: name the authority explicitly
rather than leave a role able to SEE a document and not fetch it.

WHY. ``client_service`` is the firm's client coordinator and the primary Messages role. It already
holds ``vault.view`` and ``vault.category.general``, so it can already list and open the metadata of
every general-category vault document in its record scope — including the files clients attach to
secure messages, which are uploaded as ``category='general'``. It could not download any of them,
because the download route's door is ``vault.download`` and only that capability was missing. The
result was a coordinator looking at an attachment they were authorised to read and being refused the
bytes.

WHAT THIS ACTUALLY WIDENS. Nothing about WHICH documents are reachable: category access and record
scope are unchanged, and ``vault.download_target`` re-checks both on every call
(``can_access_category`` + ``_in_record_scope``). The grant converts existing VIEW access into FETCH
access over the same set — general-category vault documents linked to a person or household already
in the coordinator's record scope. Every other document-handling profile (senior_tax, tax_staff,
accounting, payroll) holds ``vault.view`` + ``vault.upload`` + ``vault.download`` as a set;
client_service holding view without download was the outlier.

WHAT THIS DELIBERATELY DOES NOT DO. It grants no new category (``general`` only — no tax, payroll,
accounting, benefits, insurance, compliance or wealth), no ``vault.access.all``, no
``vault.upload``, no ``vault.manage``, and no record-scope capability. It touches no other role:
``advisor`` and ``operations`` hold NO vault capability at all, so making downloads work for them
would mean granting view + a category + download — a materially broader expansion of who may read
client documents, which is a business decision and is deliberately NOT made here.

``app/security/role_library.py`` is updated in the same commit (``POST_SEED_GRANTS``) so the library
and this seed cannot drift — the exact-set assertion in tests/test_production_role_library.py
enforces that.

Single Alembic head preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "vaultdl01"
down_revision = "msgatt01"
branch_labels = None
depends_on = None

_CAPABILITY = "vault.download"
_ROLE = "client_service"


def _set_grant(bind, *, granted: bool) -> None:
    if granted:
        bind.execute(sa.text(
            "INSERT INTO role_capabilities (role_id, capability_id) "
            "SELECT r.id, c.id FROM roles r CROSS JOIN capabilities c "
            "WHERE c.code = :code AND r.code = :role "
            "ON CONFLICT DO NOTHING"), {"code": _CAPABILITY, "role": _ROLE})
    else:
        bind.execute(sa.text(
            "DELETE FROM role_capabilities rc USING roles r, capabilities c "
            "WHERE rc.role_id = r.id AND rc.capability_id = c.id "
            "AND c.code = :code AND r.code = :role"), {"code": _CAPABILITY, "role": _ROLE})


def upgrade():
    # The capability already exists (seeded by the Client Vault MVP, va01ult0mvp1); this only grants
    # it. Nothing is created, so a re-run is a no-op rather than a duplicate.
    _set_grant(op.get_bind(), granted=True)


def downgrade():
    # Revokes ONLY this role's grant. The capability itself and every other role's grant are left
    # alone, so a downgrade returns client_service to view-without-download and changes nothing else.
    _set_grant(op.get_bind(), granted=False)
