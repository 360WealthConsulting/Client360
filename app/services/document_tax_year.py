"""Tax-year inference for a canonical document — PURE, READ-ONLY, never writes.

There is no ``tax_year`` column on ``documents`` (the Documents tab reads ``tags['tax_year']``, which
production has on 488 rows firm-wide and none of the active SharePoint set). Until a real field
exists, this module *derives* the year for display and filtering only, and reports the evidence that
produced it so staff can see why.

Two independent sources of evidence, both already captured at ingestion:

* the FILENAME — via :func:`document_naming.extract_years`, which already rejects hash fragments
  (``c4aa9e2000``) and scanner timestamps, so this module inherits those guards rather than
  re-implementing a looser regex;
* the SOURCE FOLDER PATH — SharePoint's own hierarchy, where the firm files by year
  (``…/WHITE, MICHAEL AND DEBRA/2020/Supporting Documents/…``). Only a path segment that *is* a year,
  or that clearly leads with one (``2023 Receipts``), counts. A year inside a client folder name is
  never read as the document's year.

Confidence is deliberately conservative, because a wrong year is worse than a missing one:

``strong``    filename and folder agree, or the filename carries exactly one year
``moderate``  no year in the filename, but the folder path carries exactly one
``conflict``  filename and folder disagree, or either carries several distinct years — no proposal
``none``      no year anywhere

Only ``strong`` and ``moderate`` yield a proposed year. Nothing here writes; the caller decides
whether to display it, and a bulk backfill remains a separate, explicitly-authorised task.
"""
from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import unquote, urlparse

from app.services.document_naming import extract_years

#: A path segment that IS a year (``2021``) or leads with one (``2023 Receipts``, ``2020-Tax``).
#: Anchored at the start so ``WHITE, MICHAEL AND DEBRA 2020`` — a client folder that happens to end
#: in a year — is not read as a filing year.
_YEAR_SEGMENT_RE = re.compile(r"^(19[89]\d|20[0-4]\d)(?:\s*[-_. ].*)?$")

#: Folder segments that never carry filing meaning, so a year inside them is ignored.
_IGNORED_SEGMENTS = {"shared documents", "documents", "sites", "personal"}


class TaxYearProposal(NamedTuple):
    """A proposed tax year plus the evidence and confidence behind it. Display-only."""

    year: int | None
    confidence: str          # "strong" | "moderate" | "conflict" | "none"
    evidence: list[str]
    source: str | None       # "filename" | "folder" | "filename+folder" | None

    @property
    def is_proposed(self) -> bool:
        """True when this proposal is safe enough to show as the document's year."""
        return self.year is not None and self.confidence in ("strong", "moderate")


def source_path_for(row) -> str:
    """The human-readable SharePoint/TaxDome folder path recorded for a document, or ''.

    Prefers the captured ``web_url`` (decoded to its library-relative path) because that is
    SharePoint's own hierarchy; falls back to the recorded source/storage path.
    """
    tags = (row.get("tags") if hasattr(row, "get") else None) or {}
    if not isinstance(tags, dict):
        tags = {}
    web_url = tags.get("web_url")
    if web_url:
        path = unquote(urlparse(str(web_url)).path or "")
        for marker in ("/Shared Documents/", "/Documents/"):
            index = path.find(marker)
            if index >= 0:
                return path[index + len(marker):]
        return path
    for key in ("source_path", "relative_path", "source_relative_path"):
        if tags.get(key):
            return str(tags[key])
    return str(row.get("storage_path") or "")


def _folder_years(path: str) -> list[int]:
    """Distinct years named by the FOLDER segments of ``path`` (the filename leaf is excluded)."""
    segments = [s for s in re.split(r"[\\/]+", path or "") if s]
    years: list[int] = []
    for segment in segments[:-1] if len(segments) > 1 else []:
        if segment.strip().lower() in _IGNORED_SEGMENTS:
            continue
        match = _YEAR_SEGMENT_RE.match(segment.strip())
        if match:
            years.append(int(match.group(1)))
    return years


def infer_tax_year(row) -> TaxYearProposal:
    """Infer a document's tax year from its filename and source folder path. Never writes.

    ``row`` is a canonical document row (or any mapping carrying ``original_name``, ``tags`` and
    ``storage_path``). A year already recorded in ``tags`` is authoritative and returned as-is.
    """
    tags = (row.get("tags") if hasattr(row, "get") else None) or {}
    if not isinstance(tags, dict):
        tags = {}
    recorded = tags.get("tax_year") or tags.get("year")
    if recorded:
        try:
            return TaxYearProposal(int(str(recorded)[:4]), "strong",
                                   ["recorded on the document at ingestion"], "recorded")
        except (TypeError, ValueError):
            pass

    filename = row.get("original_name") or ""
    path = source_path_for(row)
    name_years = sorted(set(extract_years(filename)))
    folder_years = sorted(set(_folder_years(path)))
    evidence: list[str] = []

    if len(name_years) == 1:
        evidence.append(f"filename names {name_years[0]}")
    elif len(name_years) > 1:
        evidence.append("filename names several years: "
                        + ", ".join(str(y) for y in name_years))
    if len(folder_years) == 1:
        evidence.append(f"filed in a {folder_years[0]} folder")
    elif len(folder_years) > 1:
        evidence.append("folder path names several years: "
                        + ", ".join(str(y) for y in folder_years))

    # Several distinct years on either side is a judgement call, never an automatic proposal.
    if len(name_years) > 1 or len(folder_years) > 1:
        return TaxYearProposal(None, "conflict", evidence, None)

    if len(name_years) == 1 and len(folder_years) == 1:
        if name_years[0] == folder_years[0]:
            return TaxYearProposal(name_years[0], "strong",
                                   [*evidence, "filename and folder agree"], "filename+folder")
        return TaxYearProposal(None, "conflict",
                               [*evidence, "filename and folder disagree"], None)

    if len(name_years) == 1:
        return TaxYearProposal(name_years[0], "strong", evidence, "filename")
    if len(folder_years) == 1:
        return TaxYearProposal(folder_years[0], "moderate", evidence, "folder")
    return TaxYearProposal(None, "none", [], None)


def preview_tax_years(rows) -> list[dict]:
    """READ-ONLY preview: the proposed tax year, evidence and confidence for each row given.

    This is the whole mechanism a reviewer needs before any backfill is authorised — it reports what
    *would* be written without writing anything. Rows with no safe proposal are included with
    ``proposed_year: None`` so the reviewer sees the misses as well as the hits.
    """
    out = []
    for row in rows:
        proposal = infer_tax_year(row)
        out.append({
            "document_id": row.get("id"),
            "original_name": row.get("original_name"),
            "source_path": source_path_for(row),
            "proposed_year": proposal.year if proposal.is_proposed else None,
            "confidence": proposal.confidence,
            "evidence": proposal.evidence,
            "evidence_source": proposal.source,
            "would_write": False,       # this task never writes a tax year
        })
    return out


def preview_summary(rows) -> dict:
    """Counts by confidence over ``rows`` — the headline a reviewer reads before authorising a write."""
    previews = preview_tax_years(rows)
    by_confidence: dict[str, int] = {}
    for p in previews:
        by_confidence[p["confidence"]] = by_confidence.get(p["confidence"], 0) + 1
    proposed = [p for p in previews if p["proposed_year"] is not None]
    return {
        "total": len(previews),
        "proposed": len(proposed),
        "by_confidence": by_confidence,
        "years": sorted({p["proposed_year"] for p in proposed}),
        "rows": previews,
    }
