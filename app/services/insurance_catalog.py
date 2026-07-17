"""Insurance product-catalog lookups (Release 0.10.0, Phase 1).

Read-only helpers over the three-level catalog (carrier -> family -> version) and
the structured rider-compatibility table. No record-scope: the catalog is firm
reference data, not client records. Policy/case scope is enforced by the policy
services in later steps.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db import (
    engine,
    insurance_product_families,
    insurance_product_versions,
)
from app.db import (
    insurance_product_rider_compatibility as rider_compat,
)

# Requirements under which a rider MAY attach to a product version.
_ATTACHABLE = ("included", "available", "optional")


def find_by_carrier_product_code(carrier_id: int, code: str, *, connection=None):
    """Return product-version rows for a carrier's product code, newest first.

    Scoped by carrier because carrier product codes are only unique within a
    carrier; a family's versions share the code, so several rows can match.
    """
    query = (
        select(insurance_product_versions)
        .join(insurance_product_families,
              insurance_product_families.c.id == insurance_product_versions.c.family_id)
        .where(insurance_product_families.c.carrier_id == carrier_id,
               insurance_product_versions.c.carrier_product_code == code)
        .order_by(insurance_product_versions.c.effective_from.desc().nullslast())
    )
    with _conn(connection) as c:
        return c.execute(query).mappings().all()


def find_by_illustration_identifier(identifier: str, *, connection=None):
    query = select(insurance_product_versions).where(
        insurance_product_versions.c.illustration_identifier == identifier)
    with _conn(connection) as c:
        return c.execute(query).mappings().all()


def compatible_riders(product_version_id: int, *, connection=None):
    """Rider types that may attach to this product version (incl. included/optional)."""
    query = select(rider_compat.c.rider_type).where(
        rider_compat.c.product_version_id == product_version_id,
        rider_compat.c.requirement.in_(_ATTACHABLE))
    with _conn(connection) as c:
        return sorted(c.execute(query).scalars())


def is_rider_compatible(product_version_id: int, rider_type: str, *, connection=None) -> bool:
    """A rider is compatible only if listed with an attachable requirement.

    A rider that is absent, or explicitly ``excluded``, is not compatible.
    """
    query = select(rider_compat.c.requirement).where(
        rider_compat.c.product_version_id == product_version_id,
        rider_compat.c.rider_type == rider_type)
    with _conn(connection) as c:
        requirement = c.execute(query).scalar_one_or_none()
    return requirement in _ATTACHABLE


class _conn:
    """Use the caller's connection if given, else open (and close) our own."""

    def __init__(self, connection):
        self._external = connection
        self._own = None

    def __enter__(self):
        if self._external is not None:
            return self._external
        self._own = engine.connect()
        return self._own

    def __exit__(self, *exc):
        if self._own is not None:
            self._own.close()
