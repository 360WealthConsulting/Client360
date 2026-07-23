"""Deterministic edition & license evaluator (Phase D.28) — consumes D.27 metadata, never mutates it.

Resolves the effective edition for a scope (organization assignment wins over the tenant-wide
assignment), the capabilities that edition includes (referencing the authoritative RBAC
``capabilities.code``), and the governing license policy. This is a read-only *view* — it grants
nothing at runtime (RBAC remains the sole access authority); it only reports what edition/license
apply so features can be edition-gated deterministically.
"""
from __future__ import annotations

from . import metadata_reader


def resolve_edition(*, organization_id=None, editions=None, assignments=None) -> dict | None:
    """The effective edition for the scope: an active organization-scoped assignment (if any) wins,
    else the active tenant-scoped assignment. Returns the edition dict, or None."""
    editions = metadata_reader.read_editions() if editions is None else editions
    assignments = metadata_reader.read_edition_assignments() if assignments is None else assignments
    by_id = {e["id"]: e for e in editions}

    # Only consider assignments whose edition still resolves (an assignment to a deleted edition
    # SET-NULLs its edition_id — it must not resolve to a null/missing edition).
    org_assign = next((a for a in assignments if a["scope"] == "organization"
                       and a.get("organization_id") == organization_id and organization_id is not None
                       and a.get("edition_id") in by_id), None)
    tenant_assign = next((a for a in assignments
                          if a["scope"] == "tenant" and a.get("edition_id") in by_id), None)
    chosen = org_assign or tenant_assign
    if chosen is None:
        return None
    return by_id.get(chosen["edition_id"])


def edition_capabilities(edition_id, *, edition_caps=None) -> set[str]:
    """The set of capability codes an edition includes (referencing RBAC capabilities.code)."""
    edition_caps = metadata_reader.read_edition_capabilities() if edition_caps is None else edition_caps
    return {c["capability_code"] for c in edition_caps
            if c["edition_id"] == edition_id and c.get("included", True)}


def license_for(edition_id, *, licenses=None) -> dict | None:
    licenses = metadata_reader.read_license_policies() if licenses is None else licenses
    active = [lp for lp in licenses if lp.get("edition_id") == edition_id and lp["status"] == "active"]
    return active[0] if active else None
