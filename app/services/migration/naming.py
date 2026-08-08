"""Repository Naming / Path Policy — source-agnostic human-readable placement.

Computes the permanent, human-readable location of a canonical document inside the Client360 repository
(``D:\\Client360\\Content``) from its CANONICAL FIELDS ALONE — never from the source system or a source
path. Every source (existing Client360 uploads, TaxDome, SharePoint, OneDrive, Wealthbox, future
adapters) is placed by the identical rules.

Two invariants make a human-readable tree safe:
  * Uniqueness is guaranteed by embedding the canonical ``documents.id`` in the filename, e.g.
    ``Form 1040 [40122].pdf`` — no folder is ever opaque and two documents can never collide.
  * The path is a rebuildable PROJECTION of the database: identity is only ever an OUTPUT of naming,
    never an input. A rename/merge re-plans the path; the id in the filename keeps every file traceable.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Human business areas placed directly under the Content root (dest_root = D:\Client360\Content).
AREA_CLIENTS = "Clients"
AREA_BUSINESSES = "Businesses"
AREA_HOUSEHOLDS = "Households"
AREA_FIRM = "Firm"

# Canonical documents.classification / category -> human category folder.
_CATEGORY_MAP = {
    "tax": "Tax", "estate": "Estate", "insurance": "Insurance",
    "investment": "Investments", "investments": "Investments", "retirement": "Retirement",
    "benefits": "Benefits", "compliance": "Compliance", "legal": "Legal", "hr": "HR",
    "operations": "Operations", "marketing": "Marketing", "internal": "Internal",
    "client": "Client", "archived": "Archive",
}


def sanitize(component: str) -> str:
    """Make one path component safe for NTFS (and portable). Never returns empty."""
    s = _ILLEGAL.sub("_", component or "").strip().rstrip(".")
    return s or "_"


@dataclass(frozen=True)
class PlacedPath:
    """The planned, human-readable location of one document (relative parts + composition helpers)."""

    area: str            # Clients | Businesses | Households | Firm
    entity: str          # "Last, First" | household name | org name | "Unfiled"
    category: str        # Tax | Estate | Insurance | Investments | ...
    year: str            # "2024" | "Undated"
    filename: str        # "<name> [<document_id>]<ext>"

    def relative(self) -> str:
        return os.path.join(self.area, self.entity, self.category, self.year, self.filename)

    def full(self, dest_root) -> str:
        return os.path.join(str(dest_root), self.relative())


class RepositoryNaming:
    """Computes a :class:`PlacedPath` for a canonical documents row + entity display maps.

    ``doc`` is a mapping of documents columns (id, person_id, household_id, organization_id,
    original_name, classification, category, effective_date, tags). The display maps translate ids to
    human names; missing names fall back to a stable ``"<Kind> <id>"`` so placement never fails.
    """

    def plan(self, doc, *, people=None, households=None, organizations=None) -> PlacedPath:
        area, entity = self._route(doc, people or {}, households or {}, organizations or {})
        return PlacedPath(area=area, entity=sanitize(entity), category=self._category(doc),
                          year=self._year(doc), filename=self._filename(doc))

    def _route(self, doc, people, households, organizations) -> tuple[str, str]:
        org_id, hh_id, pid = doc.get("organization_id"), doc.get("household_id"), doc.get("person_id")
        if org_id:
            return AREA_BUSINESSES, organizations.get(org_id) or f"Organization {org_id}"
        if hh_id:
            return AREA_HOUSEHOLDS, households.get(hh_id) or f"Household {hh_id}"
        if pid:
            return AREA_CLIENTS, people.get(pid) or f"Person {pid}"
        return AREA_FIRM, "Unfiled"

    def _category(self, doc) -> str:
        raw = (doc.get("classification") or doc.get("category") or "").strip().lower()
        if not raw:
            return "Documents"
        return _CATEGORY_MAP.get(raw, sanitize(raw.title()))

    def _year(self, doc) -> str:
        eff = doc.get("effective_date")
        if eff is not None and hasattr(eff, "year"):
            return str(eff.year)
        tags = doc.get("tags")
        if isinstance(tags, dict):
            for k in ("tax_year", "year"):
                v = str(tags.get(k, "")).strip()
                if _YEAR_RE.match(v):
                    return v
        return "Undated"

    def _filename(self, doc) -> str:
        did = doc.get("id")
        stem, ext = os.path.splitext(doc.get("original_name") or f"document-{did}")
        stem = sanitize(stem)
        if stem == "_":
            stem = f"document-{did}"
        return f"{stem} [{did}]{ext}"
