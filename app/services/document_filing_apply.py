"""RETIRED — the batch-1 one-step filing apply. Historical record only; nothing here executes.

WHAT THIS WAS
--------------
Batch 1 (PR #253) created the whole folder tree and set ``documents.folder_id`` under a single
confirmation phrase, against a frozen preview of 16,304 AUTO_FILE_SAFE documents. It was never
applied to production.

WHY IT IS RETIRED RATHER THAN DELETED
--------------------------------------
Its filing POLICY is obsolete on three counts, each of which is now a hard rule:

1. **Two-level destinations.** Its approved depth census was ``{2: 9361, 3: 6943}`` — 57% of the
   batch filed at ``CLIENT/SERVICE`` with no year at all. The canonical hierarchy is
   ``CLIENT > SERVICE_LINE > TAX_YEAR`` and a depth-2 destination can no longer be AUTO_FILE_SAFE.
2. **Moderate tax years.** It accepted a year segment whenever the preview offered one. Only
   ``strong`` confidence may enter an automatic path now; moderate is REVIEW.
3. **One authorization, both phases.** It created folders and mutated documents in a single
   transaction behind one phrase. Folder materialization (Phase A) and document filing (Phase B) are
   now separately previewed, separately manifested and separately confirmed.

The constants below are kept because the numbers are the historical record of what was reviewed at
the time, and tests assert they can no longer be executed. They are documentation, not parameters.

THE RETIREMENT IS ENFORCED, NOT ADVISORY
-----------------------------------------
Every executable entry point raises :class:`LegacyBatchRetired`. There is no flag, environment
variable or argument that re-enables it, and the two scripts that drove it
(``scripts/apply_document_filing.py``, ``scripts/rollback_document_filing.py``) are deleted. The old
frozen CSV can still be read by a human, but nothing in this codebase will turn it into a plan, a
confirmation phrase, or a write.

The replacement lives in :mod:`app.services.canonical_filing`,
:mod:`app.services.canonical_filing_phases` and the ``scripts/*_canonical_*`` commands.
"""
from __future__ import annotations

#: True. Asserted by tests so the retirement cannot be quietly undone.
RETIRED = True

REPLACEMENT_MODULES = (
    "app.services.canonical_filing",
    "app.services.canonical_filing_phases",
)

# --- historical record: what batch 1 was reviewed as. NOT parameters to anything. ----------------

LEGACY_BATCH_NAME = "DOCUMENT-FILING-BATCH1"
LEGACY_FROZEN_CSV_SHA256 = "b944fd1a92ab3c9516b75a17aac9d4b2aac04f616573dd228b12eef6c6daa4c2"
LEGACY_EXPECTED_PREVIEW_ROWS = 73240
LEGACY_EXPECTED_AUTO_ROWS = 16304
LEGACY_EXPECTED_FOLDER_NODES = 3134

#: The reason the batch is retired, in one number: 9,361 documents with no year segment.
LEGACY_EXPECTED_DEPTH_CENSUS = {2: 9361, 3: 6943}

#: The phrase that used to authorize the one-step apply. Recorded so tests can prove it is dead.
LEGACY_CONFIRM_PHRASE = "APPLY-DOCUMENT-FILING-BATCH1-16304"
LEGACY_ROLLBACK_PHRASE = "ROLLBACK-DOCUMENT-FILING-BATCH1-16304"


class LegacyBatchRetired(RuntimeError):
    """Raised by every batch-1 entry point. The canonical two-phase path replaces it."""

    def __init__(self, entry_point):
        super().__init__(
            f"{entry_point}: the batch-1 one-step filing apply is RETIRED. Its policy allowed "
            f"depth-2 destinations ({LEGACY_EXPECTED_DEPTH_CENSUS[2]} documents with no tax year) "
            "and combined folder creation with document mutation under one authorization. Use the "
            f"canonical two-phase path: {', '.join(REPLACEMENT_MODULES)}."
        )


def _retired(entry_point):
    raise LegacyBatchRetired(entry_point)


def build_plan(*_args, **_kwargs):
    _retired("build_plan")


def read_frozen_rows(*_args, **_kwargs):
    _retired("read_frozen_rows")


def confirm_phrase(*_args, **_kwargs):
    _retired("confirm_phrase")


def rollback_phrase(*_args, **_kwargs):
    _retired("rollback_phrase")


def plan_digest(*_args, **_kwargs):
    _retired("plan_digest")


def folder_manifest_digest(*_args, **_kwargs):
    _retired("folder_manifest_digest")


def client_code(*_args, **_kwargs):
    _retired("client_code")


def category_code(*_args, **_kwargs):
    _retired("category_code")


def year_code(*_args, **_kwargs):
    _retired("year_code")


def slugify(*_args, **_kwargs):
    _retired("slugify")


def sha256_of(*_args, **_kwargs):
    _retired("sha256_of")
