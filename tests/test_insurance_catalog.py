"""Insurance product catalog — carrier codes, illustration ids, rider compatibility
(Release 0.10.0, Phase 1 step 1).

Pins the product-version evolution refinement: carrier_product_code and
illustration_identifier are first-class matching keys, and rider compatibility is a
structured, queryable relationship (allowed vs. rejected riders) — not a JSON list.
"""
from __future__ import annotations

import uuid

from app.db import (
    engine,
    insurance_product_families,
    insurance_product_versions,
    relationship_entities,
)
from app.db import (
    insurance_product_rider_compatibility as rider_compat,
)
from app.services import insurance_catalog as cat


def _sfx() -> str:
    return uuid.uuid4().hex


def _carrier(c, name) -> int:
    return c.execute(relationship_entities.insert().values(
        entity_type="insurance_carrier", name=name, details={}, active=True
    ).returning(relationship_entities.c.id)).scalar_one()


def _version(c, carrier_id, *, code=None, illustration=None, label="2026.1"):
    fam = c.execute(insurance_product_families.insert().values(
        carrier_id=carrier_id, name=f"Fam {_sfx()}", product_type="iul", line="life"
    ).returning(insurance_product_families.c.id)).scalar_one()
    return c.execute(insurance_product_versions.insert().values(
        family_id=fam, version_label=label, carrier_product_code=code,
        illustration_identifier=illustration,
    ).returning(insurance_product_versions.c.id)).scalar_one(), fam


def test_carrier_product_code_lookup_is_scoped_to_the_carrier():
    with engine.begin() as c:
        sfx = _sfx()
        code = f"IUL-{sfx[:6]}"
        c1 = _carrier(c, f"Carrier One {sfx}")
        c2 = _carrier(c, f"Carrier Two {sfx}")
        v1, _ = _version(c, c1, code=code)
        # a DIFFERENT carrier legitimately reuses the same product code
        _version(c, c2, code=code)

    hit = cat.find_by_carrier_product_code(c1, code)
    assert len(hit) == 1 and hit[0]["id"] == v1  # scoped to carrier one only


def test_illustration_identifier_lookup():
    with engine.begin() as c:
        ill = f"ILL-{_sfx()[:8]}"
        carrier = _carrier(c, f"C {_sfx()}")
        vid, _ = _version(c, carrier, illustration=ill)
    hits = cat.find_by_illustration_identifier(ill)
    assert [h["id"] for h in hits] == [vid]


def test_allowed_rider_combinations():
    with engine.begin() as c:
        carrier = _carrier(c, f"C {_sfx()}")
        vid, _ = _version(c, carrier)
        c.execute(rider_compat.insert(), [
            {"product_version_id": vid, "rider_type": "waiver_of_premium", "requirement": "available"},
            {"product_version_id": vid, "rider_type": "accelerated_death_benefit", "requirement": "included"},
            {"product_version_id": vid, "rider_type": "child_term", "requirement": "optional"},
            {"product_version_id": vid, "rider_type": "long_term_care", "requirement": "excluded"},
        ])
    allowed = cat.compatible_riders(vid)
    assert allowed == ["accelerated_death_benefit", "child_term", "waiver_of_premium"]
    assert cat.is_rider_compatible(vid, "waiver_of_premium") is True
    assert cat.is_rider_compatible(vid, "accelerated_death_benefit") is True


def test_rejected_incompatible_riders():
    with engine.begin() as c:
        carrier = _carrier(c, f"C {_sfx()}")
        vid, _ = _version(c, carrier)
        c.execute(rider_compat.insert().values(
            product_version_id=vid, rider_type="long_term_care", requirement="excluded"))
    # explicitly excluded -> incompatible
    assert cat.is_rider_compatible(vid, "long_term_care") is False
    # absent from the compatibility list -> incompatible (not silently allowed)
    assert cat.is_rider_compatible(vid, "never_listed_rider") is False
    assert "long_term_care" not in cat.compatible_riders(vid)


def test_rider_compat_is_unique_per_version_and_rider():
    import pytest
    with engine.begin() as c:
        carrier = _carrier(c, f"C {_sfx()}")
        vid, _ = _version(c, carrier)
        c.execute(rider_compat.insert().values(
            product_version_id=vid, rider_type="waiver_of_premium", requirement="available"))
    with engine.begin() as c, pytest.raises(Exception):
        c.execute(rider_compat.insert().values(
            product_version_id=vid, rider_type="waiver_of_premium", requirement="excluded"))


def test_carrier_product_code_is_indexed_for_matching():
    from sqlalchemy import inspect
    idx = {i["name"] for i in inspect(engine).get_indexes("insurance_product_versions")}
    assert "ix_ins_pv_carrier_product_code" in idx
    assert "ix_ins_pv_illustration_identifier" in idx


def test_spec_json_retained_for_unstructured_attributes():
    """The extension adds structured columns; the spec JSON stays for genuinely
    unstructured carrier-specific attributes (not replaced)."""
    cols = {c.name for c in insurance_product_versions.c}
    assert "spec" in cols  # untyped carrier extras still available
    assert {"carrier_product_code", "illustration_identifier"} <= cols
