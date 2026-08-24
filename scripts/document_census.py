"""Document normalization CENSUS — READ-ONLY (issues SELECT statements only; writes nothing).

    python scripts/document_census.py                 # census of the trusted population
    python scripts/document_census.py --show 40       # more detail rows per section
    python scripts/document_census.py --all           # include the excluded population too

Answers the questions the naming/classification design depends on: how many documents are actually
trusted, who owns them, where they came from, how many already carry a usable type or year, and how
bad the filenames really are. It never renames, reclassifies, moves, or writes anything.

TRUSTED population (the normalization target) =
    status <> 'deleted'  AND  NOT archived  AND  ownership is proven
    (person_id OR household_id OR organization_id IS NOT NULL)  AND  a storage reference exists.

Everything else is reported separately as EXCLUDED with its reason, so the excluded set is visible
rather than silently dropped. Quarantine is not a modelled concept in the schema, so this script
REPORTS candidate quarantine markers (category/classification/folder/tag values that look like
backup or personal content) instead of assuming one — confirm them before treating them as excluded.
"""
from __future__ import annotations

import argparse
import re
from collections import Counter

from sqlalchemy import text

from app.db import engine

TRUSTED_WHERE = """
    status <> 'deleted'
    AND NOT archived
    AND (person_id IS NOT NULL OR household_id IS NOT NULL OR organization_id IS NOT NULL)
    AND (coalesce(storage_path, '') <> '' OR coalesce(storage_uri, '') <> '')
"""

# A filename carries no information if it is a bare scan/camera/export artifact or is too short to
# say anything about the document. These are the rows a canonical display name helps most.
_GENERIC_RE = re.compile(
    r"^(doc|document|scan|scanned|image|img|file|untitled|new|copy|attachment|d|x|temp|tmp)"
    r"[ _\-]*\d*$", re.I)
_CAMERA_RE = re.compile(r"^(img|dsc|photo|scan|screenshot)[ _\-]?\d{2,}$", re.I)
_YEAR_RE = re.compile(r"(19|20)\d{2}")


def _stem(name):
    return re.sub(r"\.[A-Za-z0-9]{1,6}$", "", name or "").strip()


def _is_generic(name):
    s = _stem(name)
    return bool(_GENERIC_RE.match(s) or _CAMERA_RE.match(s) or len(s) <= 3)


def _shape(name):
    """Collapse a filename to a comparable shape: digits -> 9, letters -> a. Reveals conventions."""
    s = _stem(name).lower()
    s = re.sub(r"\d+", "9", s)
    s = re.sub(r"[a-z]+", "a", s)
    return re.sub(r"\s+", " ", s)[:40] or "(empty)"


def _section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def _rows(conn, sql, **p):
    return list(conn.execute(text(sql), p).all())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only document census (writes nothing).")
    ap.add_argument("--show", type=int, default=20, help="max detail rows per section")
    ap.add_argument("--all", action="store_true", help="also census the excluded population")
    args = ap.parse_args(argv)
    n = args.show

    with engine.connect() as c:
        print("DOCUMENT NORMALIZATION CENSUS — READ-ONLY (nothing written)")

        _section("Population")
        for label, where in (("all documents", "TRUE"),
                             ("deleted", "status = 'deleted'"),
                             ("archived", "archived"),
                             ("no storage reference",
                              "coalesce(storage_path,'') = '' AND coalesce(storage_uri,'') = ''"),
                             ("unowned (no person/household/org)",
                              "person_id IS NULL AND household_id IS NULL AND organization_id IS NULL"),
                             ("TRUSTED (normalization target)", TRUSTED_WHERE)):
            v = c.execute(text(f"SELECT count(*) FROM documents WHERE {where}")).scalar()
            print(f"  {label:<38} {v}")

        _section("Trusted by owner type")
        for r in _rows(c, f"""SELECT CASE WHEN organization_id IS NOT NULL THEN 'business'
                WHEN household_id IS NOT NULL THEN 'household' ELSE 'person' END owner_type,
                count(*) FROM documents WHERE {TRUSTED_WHERE} GROUP BY 1 ORDER BY 2 DESC"""):
            print(f"  {r[0]:<38} {r[1]}")

        _section("Trusted by source system")
        # source_system may live in tags (object form), in storage_provider, or nowhere.
        for r in _rows(c, f"""SELECT coalesce(
                    CASE WHEN jsonb_typeof(tags)='object' THEN tags->>'source_system' END,
                    storage_provider, '(unknown)') src, count(*)
                FROM documents WHERE {TRUSTED_WHERE} GROUP BY 1 ORDER BY 2 DESC LIMIT :n""", n=n):
            print(f"  {str(r[0]):<38} {r[1]}")

        _section("tags column shape (data-model consistency)")
        for r in _rows(c, f"SELECT jsonb_typeof(tags), count(*) FROM documents WHERE {TRUSTED_WHERE}"
                          " GROUP BY 1 ORDER BY 2 DESC"):
            print(f"  {str(r[0]):<38} {r[1]}")

        _section("Classification / type coverage (trusted)")
        for r in _rows(c, f"""SELECT
                count(*) FILTER (WHERE coalesce(classification,'') <> '') AS classification,
                count(*) FILTER (WHERE coalesce(category,'') <> '')       AS category,
                count(*) FILTER (WHERE coalesce(subcategory,'') <> '')    AS subcategory,
                count(*) FILTER (WHERE effective_date IS NOT NULL)        AS effective_date,
                count(*) FILTER (WHERE folder_id IS NOT NULL)             AS folder,
                count(*) FILTER (WHERE ocr_status = 'complete')           AS ocr_complete,
                count(*) AS total
                FROM documents WHERE {TRUSTED_WHERE}"""):
            total = r[-1] or 1
            for key, val in zip(("classification", "category", "subcategory", "effective_date",
                                 "folder_id", "ocr complete"), r[:-1], strict=True):
                print(f"  {key:<38} {val:>7}  ({val * 100 // total}%)")

        _section("Category values in use (trusted)")
        for r in _rows(c, f"""SELECT coalesce(nullif(category,''),'(none)'), count(*)
                FROM documents WHERE {TRUSTED_WHERE} GROUP BY 1 ORDER BY 2 DESC LIMIT :n""", n=n):
            print(f"  {str(r[0]):<38} {r[1]}")

        _section("Candidate quarantine / personal markers (CONFIRM before excluding)")
        hits = _rows(c, f"""SELECT coalesce(nullif(category,''),'') || '|' || coalesce(nullif(classification,''),''),
                count(*) FROM documents
                WHERE {TRUSTED_WHERE} AND (category ~* 'backup|personal|quarantine|archive|junk'
                   OR classification ~* 'backup|personal|quarantine|archive|junk')
                GROUP BY 1 ORDER BY 2 DESC LIMIT :n""", n=n)
        for r in hits:
            print(f"  {str(r[0]):<38} {r[1]}")
        if not hits:
            print("  (none found by category/classification keyword)")

        rows = _rows(c, f"SELECT id, original_name FROM documents WHERE {TRUSTED_WHERE}")
        names = [r[1] or "" for r in rows]

        _section("Filename quality (trusted)")
        generic = [x for x in names if _is_generic(x)]
        with_year = [x for x in names if _YEAR_RE.search(x)]
        print(f"  {'total filenames':<38} {len(names)}")
        print(f"  {'generic / uninformative':<38} {len(generic)}"
              f"  ({len(generic) * 100 // max(len(names), 1)}%)")
        print(f"  {'contain a 4-digit year':<38} {len(with_year)}"
              f"  ({len(with_year) * 100 // max(len(names), 1)}%)")

        _section(f"Top filename shapes (digits->9, letters->a) — top {n}")
        for shape, cnt in Counter(_shape(x) for x in names).most_common(n):
            print(f"  {cnt:>7}  {shape}")

        _section(f"Duplicate filenames — top {n}")
        for r in _rows(c, f"""SELECT original_name, count(*) k FROM documents WHERE {TRUSTED_WHERE}
                GROUP BY 1 HAVING count(*) > 1 ORDER BY k DESC LIMIT :n""", n=n):
            print(f"  {r[1]:>7}  {str(r[0])[:80]}")

        _section("Year signal available (trusted)")
        for r in _rows(c, f"""SELECT
                count(*) FILTER (WHERE effective_date IS NOT NULL) AS from_effective_date,
                count(*) FILTER (WHERE original_name ~ '(19|20)[0-9]{{2}}') AS from_filename,
                count(*) FILTER (WHERE effective_date IS NULL
                                  AND original_name !~ '(19|20)[0-9]{{2}}') AS no_year_signal
                FROM documents WHERE {TRUSTED_WHERE}"""):
            for key, val in zip(("from effective_date", "from filename", "NO year signal"), r, strict=True):
                print(f"  {key:<38} {val}")

        _section("Duplicate SHA-256 (same bytes, possibly many names)")
        for r in _rows(c, f"""SELECT count(*) FROM (SELECT sha256 FROM documents WHERE {TRUSTED_WHERE}
                GROUP BY 1 HAVING count(*) > 1) t"""):
            print(f"  {'sha256 values appearing >1 time':<38} {r[0]}")

        if args.all:
            _section("EXCLUDED population by reason")
            for label, where in (("deleted", "status = 'deleted'"),
                                 ("archived (not deleted)", "archived AND status <> 'deleted'"),
                                 ("unowned",
                                  "person_id IS NULL AND household_id IS NULL "
                                  "AND organization_id IS NULL AND status <> 'deleted'"),
                                 ("no storage reference",
                                  "coalesce(storage_path,'') = '' AND coalesce(storage_uri,'') = ''")):
                v = c.execute(text(f"SELECT count(*) FROM documents WHERE {where}")).scalar()
                print(f"  {label:<38} {v}")

    print("\nREAD-ONLY census complete — no rows were created, updated, renamed, or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
