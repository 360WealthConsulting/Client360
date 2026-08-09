"""Exception Engine 'linkage' domain — unresolved-subject review.

Extends the existing Exception Engine (no new queue framework) with a ``linkage`` domain so unresolved
ingestion subjects (TaxDome folders today; acquired advisor books / firms, scanned-paper batches, CRM
records later) become review work in the same exceptions / exception_events / assignment / SLA / audit /
capability model.

Two changes, mirroring how the benefits and insurance domains were added:
  1. Extend the ``domain`` CHECK on ``exceptions`` and ``exception_types`` to allow ``linkage``.
  2. Seed ONE exception type, ``linkage.unresolved_subject`` (domain='linkage', category='document',
     severity='low'), carrying the review resolution options as reference metadata. Idempotent
     (ON CONFLICT (code) DO NOTHING).

No queue framework, no new capabilities (the linkage domain reuses the existing ``exception.*``
capabilities), and no document / canonical / file changes.

Downgrade removes the linkage exception data, then restores the original domain CHECK.

Revision ID: lnkg01
Revises: reskn01
Create Date: 2026-08-09
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "lnkg01"
down_revision = "reskn01"
branch_labels = None
depends_on = None

# Current allowed exception domains (through the insurance foundation) + the new 'linkage' domain.
EXC_DOMAINS_OLD = ("tax", "wealth", "operations", "compliance", "portal", "microsoft",
                   "benefits", "insurance")
EXC_DOMAINS_NEW = EXC_DOMAINS_OLD + ("linkage",)

_LINKAGE_CODE = "linkage.unresolved_subject"
_LINKAGE_NAME = "Unresolved document linkage subject"
_LINKAGE_DESC = ("Documents from an unresolved ingestion subject (e.g. a TaxDome folder) have no canonical "
                 "owner. A reviewer resolves the subject to a canonical person / household / business, "
                 "marks it firm material, or defers it.")
_LINKAGE_SLA = 14400   # SLA_BY_SEVERITY['low'] in the exception-engine schema
# Reference options for the later review UI (PR-5) — NOT applied here.
_RESOLUTION_OPTIONS = ["link_person", "create_person", "link_household", "create_household",
                       "link_business", "create_business", "firm_material", "defer", "reject"]


def _check(col, allowed):
    values = ", ".join(f"'{v}'" for v in allowed)
    return f"{col} IN ({values})"


def upgrade() -> None:
    # 1) extend the exception domain CHECK to allow 'linkage'
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_NEW))

    # 2) seed the single linkage exception type (idempotent)
    op.get_bind().execute(sa.text(
        "INSERT INTO exception_types "
        "(domain, code, category, name, description, default_severity, trigger_kind, sla_minutes, "
        " resolution_options, blocks_lifecycle, compliance_visible, active) "
        "VALUES ('linkage', :code, 'document', :name, :descr, 'low', 'auto', :sla, "
        " CAST(:opts AS json), false, false, true) "
        "ON CONFLICT (code) DO NOTHING"),
        {"code": _LINKAGE_CODE, "name": _LINKAGE_NAME, "descr": _LINKAGE_DESC,
         "sla": _LINKAGE_SLA, "opts": json.dumps(_RESOLUTION_OPTIONS)})


def downgrade() -> None:
    bind = op.get_bind()
    # remove linkage exception data BEFORE narrowing the CHECK, so the constraint can be restored
    bind.execute(sa.text("DELETE FROM exceptions WHERE domain = 'linkage'"))
    bind.execute(sa.text("DELETE FROM exception_types WHERE domain = 'linkage'"))
    for table in ("exceptions", "exception_types"):
        op.drop_constraint(f"ck_{table}_domain", table, type_="check")
        op.create_check_constraint(f"ck_{table}_domain", table, _check("domain", EXC_DOMAINS_OLD))
