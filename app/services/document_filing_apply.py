"""RETIRED — the batch-1 one-step filing apply. Its APPLY PATH is dead; its arithmetic is not.

WHAT THIS WAS
--------------
Batch 1 (PR #253) created the whole folder tree and set ``documents.folder_id`` under a single
confirmation phrase, against a frozen preview of 16,304 AUTO_FILE_SAFE documents.

WHY THE POLICY IS RETIRED
--------------------------
1. **Two-level destinations.** Its approved depth census was ``{2: 9361, 3: 6943}`` — 57% of the
   batch filed at ``CLIENT/SERVICE`` with no year at all. The canonical hierarchy is
   ``CLIENT > SERVICE_LINE > TAX_YEAR`` and a depth-2 destination can no longer be AUTO_FILE_SAFE.
2. **Moderate tax years.** It accepted a year segment whenever the preview offered one. Only
   ``strong`` confidence may enter an automatic path now; moderate is REVIEW.
3. **One authorization, both phases.** It created folders and mutated documents in a single
   transaction behind one phrase. Reconciliation (R), folder materialization (A) and document
   filing (B) are now separately previewed, manifested and confirmed.

WHY THE HELPERS ARE STILL LIVE
-------------------------------
Batch 2 (PR #254) is merged and applied in production, and it imports ten neutral helpers from this
module — the slug rule, the folder codes, the content digests, and the field tuples those digests
hash over. Retiring a policy must not break code that merely borrowed its arithmetic, and batch 2's
pinned digests are computed from these exact bytes.

So the helpers now live in :mod:`app.services.legacy_filing_codes` and are re-exported here. They
are frozen: 16,854 production documents sit in folders whose codes :func:`client_code` and
:func:`category_code` generated, so changing them would orphan live references.

WHAT REMAINS FAIL-CLOSED
-------------------------
Only the apply path: :func:`build_plan`, :func:`read_frozen_rows`, :func:`confirm_phrase` and
:func:`rollback_phrase` raise :class:`LegacyBatchRetired`. The two scripts that drove it
(``scripts/apply_document_filing.py``, ``scripts/rollback_document_filing.py``) are deleted. The old
frozen CSV can still be read by a human, but nothing here will turn it into a plan, a confirmation
phrase, or a write.

The replacement lives in :mod:`app.services.canonical_filing`,
:mod:`app.services.canonical_filing_reconcile` and :mod:`app.services.canonical_filing_phases`.
"""
from __future__ import annotations

# Re-exported for the merged batch-2 code. Neutral arithmetic, no policy, no writes.
from app.services.legacy_filing_codes import (  # noqa: F401
    FOLDER_FIELDS,
    FOLDER_KINDS,
    PLAN_FIELDS,
    PlanError,
    category_code,
    client_code,
    folder_manifest_digest,
    plan_digest,
    sha256_of,
    slugify,
    year_code,
)

#: True. Asserted by tests so the retirement cannot be quietly undone.
RETIRED = True

REPLACEMENT_MODULES = (
    "app.services.canonical_filing",
    "app.services.canonical_filing_reconcile",
    "app.services.canonical_filing_phases",
)

#: The entry points that are dead. Everything else in this module is neutral arithmetic.
RETIRED_ENTRY_POINTS = ("build_plan", "read_frozen_rows", "confirm_phrase", "rollback_phrase")

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
    """Raised by the batch-1 APPLY entry points. The canonical R/A/B path replaces them."""

    def __init__(self, entry_point):
        super().__init__(
            f"{entry_point}: the batch-1 one-step filing apply is RETIRED. Its policy allowed "
            f"depth-2 destinations ({LEGACY_EXPECTED_DEPTH_CENSUS[2]} documents with no tax year) "
            "and combined folder creation with document mutation under one authorization. Use the "
            f"canonical path: {', '.join(REPLACEMENT_MODULES)}."
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
