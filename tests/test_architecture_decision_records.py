"""Architecture Decision Record (ADR) enforcement tests (Phase D.12B).

Verifies durable structural facts about docs/adr/* without snapshotting prose: the index and all
17 ADRs exist, numbers are sequential and unique, every ADR has the required headings + a valid
status + References, the index links every ADR, and the platform/advisor-workspace docs reference
the ADR index. Prose wording is not asserted.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADR_DIR = REPO / "docs" / "adr"
README = ADR_DIR / "README.md"

EXPECTED_COUNT = 23
REQUIRED_HEADINGS = [
    "## Status", "## Date", "## Decision owners", "## Context", "## Decision",
    "## Alternatives considered", "## Reasons for the decision", "## Consequences",
    "### Positive consequences", "### Negative consequences and tradeoffs",
    "## Enforcement", "## Exceptions", "## Revisit conditions", "## References",
]
VALID_STATUSES = {"Accepted", "Superseded", "Deprecated", "Proposed"}


def _adr_files():
    return sorted(p for p in ADR_DIR.glob("ADR-*.md"))


def _number(path):
    m = re.match(r"ADR-(\d{3})-", path.name)
    assert m, f"bad ADR filename: {path.name}"
    return int(m.group(1))


# --- existence + numbering ---------------------------------------------------

def test_adr_index_exists():
    assert README.is_file()


def test_all_seventeen_adrs_exist():
    nums = [_number(p) for p in _adr_files()]
    assert len(nums) == EXPECTED_COUNT, f"expected {EXPECTED_COUNT} ADRs, found {len(nums)}"
    assert nums == list(range(1, EXPECTED_COUNT + 1)), f"ADR numbers not sequential 1..17: {nums}"


def test_adr_numbers_and_filenames_unique():
    files = _adr_files()
    nums = [_number(p) for p in files]
    assert len(set(nums)) == len(nums), "duplicate ADR number"
    assert len({p.name for p in files}) == len(files), "duplicate ADR filename"


# --- structure ---------------------------------------------------------------

def test_every_adr_has_required_headings():
    for p in _adr_files():
        text = p.read_text()
        assert text.startswith(f"# ADR-{_number(p):03d} —"), f"{p.name} title heading malformed"
        for heading in REQUIRED_HEADINGS:
            assert heading in text, f"{p.name} missing heading: {heading!r}"


def test_every_adr_has_valid_status():
    for p in _adr_files():
        text = p.read_text()
        m = re.search(r"## Status\s*\n+([A-Za-z]+)", text)
        assert m, f"{p.name} has no parseable Status"
        assert m.group(1) in VALID_STATUSES, f"{p.name} invalid status {m.group(1)!r}"


def test_no_adr_is_proposed_or_empty_placeholder():
    for p in _adr_files():
        text = p.read_text()
        status = re.search(r"## Status\s*\n+([A-Za-z]+)", text).group(1)
        # This set is all Accepted; a Proposed here would be unintentional.
        assert status != "Proposed", f"{p.name} unexpectedly Proposed"
        # Not an empty placeholder: a References section with at least one repo-relative link.
        assert len(text) > 1200, f"{p.name} looks like an empty placeholder"
        refs = text.split("## References", 1)[1]
        assert re.search(r"`?(app/|docs/|tests/|migrations/)", refs), \
            f"{p.name} References has no repository-relative reference"


# --- index links every ADR ---------------------------------------------------

def test_index_links_every_adr():
    index = README.read_text()
    for p in _adr_files():
        assert f"]({p.name})" in index, f"ADR index does not link {p.name}"


def test_index_has_required_sections():
    index = README.read_text()
    for marker in ("## Purpose", "numbering", "Status definitions", "supersede",
                   "## ADR index", "change process"):
        assert marker.lower() in index.lower(), f"ADR index missing: {marker!r}"


# --- cross-references from the top-level docs ---------------------------------

def test_platform_architecture_references_adr_index():
    text = (REPO / "docs" / "PLATFORM_ARCHITECTURE.md").read_text()
    assert "docs/adr/README.md" in text or "adr/README.md" in text


def test_advisor_workspace_doc_references_adr_index():
    text = (REPO / "docs" / "ADVISOR_WORKSPACE_ARCHITECTURE.md").read_text()
    assert "docs/adr/README.md" in text or "adr/README.md" in text


# --- referenced implementation files exist (where practical) -----------------

def test_key_referenced_implementation_files_exist():
    # A representative set of files ADRs cite must actually exist.
    for rel in ("app/services/business_owner.py", "app/services/advisor_work.py",
                "app/services/activity_timeline/service.py", "app/services/annual_review.py",
                "app/services/compliance/reviews.py", "app/services/organization_service.py",
                "app/database/business_planning_tables.py",
                "docs/platform_architecture_manifest.yaml",
                "tests/test_platform_architecture.py"):
        assert (REPO / rel).is_file(), f"ADR-referenced file missing: {rel}"
