"""Strict-safe ownership BATCH 5 — the frozen-manifest contract.

WHY BATCH 5 IS A NEW HARNESS AND NOT A RE-RUN OF BATCH 1
-------------------------------------------------------
Batch 1 selected its rows LIVE: :func:`document_strict_safe_ownership.build_plan` enumerated
whatever qualified at apply time, and the apply pinned the shape that selection produced on
2026-09-05 — 541 rows, composition ``{3: 104, 2: 437}``, 205 distinct people. Those constants are a
record of what a human approved that day, not a policy, and they must not be edited: a script whose
approved composition can be rewritten proves nothing about what was reviewed.

Batch 5 is the residue that the refreshed proposal engine left behind — 55 documents that the
2026-09-10 owner-proposal refresh newly qualified. It is a strictly smaller, differently shaped
population, so it gets its own identity, its own approved constants and its own snapshot root,
exactly as batches 2, 3 and 4 each did.

MANIFEST-BOUND, NOT PLAN-BOUND
------------------------------
The important structural difference from batch 1: batch 1 recomputed ``build_plan`` and applied
whatever it returned, using the manifest only as a cross-check. Batch 5 applies THE MANIFEST and
nothing else. ``build_plan`` is still recomputed and its digest still has to match, because a moved
plan means a moved corpus — but the plan is a gate, never a source of rows. There is no path by
which a document absent from the frozen 55 can be assigned, even if it qualifies today.

WHAT EACH ROW MUST STILL PROVE
------------------------------
The freeze recorded four fingerprints per row — of the document, of the owner_proposal fact, of the
classification, and of the target person. Every one is recomputed under the row lock and must match
byte for byte. Ownership is not merely "still unowned"; it is "still exactly the state a human
reviewed". A renamed document, a re-proposed owner, a re-classified file or a person whose email
changed all abort the whole batch.

WHAT BATCH 5 DELIBERATELY DOES NOT DEPEND ON
--------------------------------------------
Two known defects live in the shared corroborator code and are OUT OF SCOPE here:

  * the refreshed engine emits ``✓ street address matched`` while
    :func:`document_strict_safe_ownership.corroborators` still recognises only the retired
    ``✓ address/ZIP matched``, so address credit is unreachable for refreshed facts;
  * an email/phone line tagged ``(shared value — context only)`` is still counted as a corroborator.

Batch 5 is immune to both by construction: all 55 rows carry exact name + matched email + matched
phone, none carries address or ZIP credit, and none carries a shared-value tag. :func:`forbidden_evidence`
enforces that as a hard gate rather than leaving it as a property of the data, so a future manifest
that drifted into either class could not be applied through this harness.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

#: Batch identity. Encoded into the confirmation phrases so a phrase cannot be reused elsewhere.
BATCH_ID = "STRICT-SAFE-OWNERSHIP-BATCH5"

#: The approved manifest, frozen 2026-09-10 against deployed head ccdd9a3.
FROZEN_MANIFEST_SHA256 = "759de1e9394b05b39e69bf2d6d0596f473d2ad4472c5401c891a75d43160efea"

#: The canonical strict-safe plan digest at freeze time. A gate, never a source of rows.
FROZEN_PLAN_DIGEST = "a3e68a9c02c388a7ff51950948abb286e2239ee03c252fd4cc9a0199e84e70cc"

#: The approved shape. Not editable to make a different batch fit.
EXPECTED_ROWS = 55
EXPECTED_DISTINCT_PEOPLE = 36
EXPECTED_COMPOSITION = {2: 55}

#: The minimum corroborators, restated from batch 1 rather than relaxed.
MIN_CORROBORATORS = 2

MANIFEST_REQUIRED_COLUMNS = (
    "document_id", "person_id", "person_name", "corroborator_count",
    "owner_proposal_fact_id", "owner_proposal_fact_version",
    "document_fingerprint", "proposal_fingerprint", "classification_fingerprint",
    "target_person_fingerprint",
    "address_corroboration_used", "zip_corroboration_used", "shared_value_corroboration_used",
)

#: Evidence the engine writes. Batch 5 requires the first two and forbids the rest.
EXACT_NAME_PREFIX = "✓ exact name"
EMAIL_PREFIX = "✓ email"
PHONE_PREFIX = "✓ phone"
LEGACY_ADDRESS_PREFIX = "✓ address/ZIP matched"
STREET_PREFIX = "✓ street address matched"
ZIP_CONTEXT_PREFIX = "• ZIP matched"
SHARED_VALUE_MARKER = "(shared value — context only)"


class ManifestError(ValueError):
    """The manifest is not the reviewed one, or a row no longer proves what it claimed."""


# --- fingerprints ---------------------------------------------------------------------------
# These four functions REPRODUCE the freeze. Their field order and formatting are part of the
# approved artifact: changing any of them silently invalidates every frozen fingerprint, so they
# are pinned by test_fingerprint_formulas_are_stable rather than left to convention.

_SEP = "\x1f"


def _fp(parts) -> str:
    return hashlib.sha256(
        _SEP.join("" if p is None else str(p) for p in parts).encode("utf-8")).hexdigest()


def document_fingerprint(doc) -> str:
    """Identity + ownership + lifecycle + name + content hash of one document."""
    return _fp([doc["id"], doc["person_id"], doc["household_id"], doc["organization_id"],
                doc["status"], doc["archived"], doc["deleted_at"] is not None,
                doc["review_status"], doc["original_name"], doc["sha256"]])


def proposal_fingerprint(fact_id, fact_version, fact_value) -> str:
    """The owner_proposal fact, canonicalised so formatting never moves the hash."""
    return _fp([fact_id, fact_version,
                json.dumps(fact_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)])


def classification_fingerprint(classification_id, doc_type, confidence, classifier_version) -> str:
    """The document's most recent classification row."""
    return _fp([classification_id, doc_type, str(confidence), classifier_version])


def target_person_fingerprint(person) -> str:
    """The proposed owner as a person record — name, contacts, household, active flag."""
    return _fp([person["id"], person["first_name"], person["last_name"], person["full_name"],
                person["normalized_email"], person["normalized_phone"],
                person["household_id"], person["active"]])


# --- evidence gates -------------------------------------------------------------------------

def corroborator_flags(evidence) -> dict:
    """The two corroborator classes batch 5 accepts. Address/ZIP is deliberately absent."""
    lines = [str(e) for e in (evidence or [])]
    return {
        "email_match": any(line.startswith(EMAIL_PREFIX) and "matched" in line
                           and SHARED_VALUE_MARKER not in line for line in lines),
        "phone_match": any(line.startswith(PHONE_PREFIX) and "matched" in line
                           and SHARED_VALUE_MARKER not in line for line in lines),
    }


def has_exact_name_evidence(evidence) -> bool:
    return any(str(e).startswith(EXACT_NAME_PREFIX) for e in (evidence or []))


def forbidden_evidence(evidence) -> list[str]:
    """Evidence classes batch 5 refuses to rest on, whatever the corroborator count says.

    Returns the reasons, so the caller can name exactly which gate a row tripped.
    """
    lines = [str(e) for e in (evidence or [])]
    bad = []
    if any(line.startswith(LEGACY_ADDRESS_PREFIX) for line in lines):
        bad.append("address/ZIP corroboration present")
    if any(SHARED_VALUE_MARKER in line for line in lines):
        bad.append("shared-value contact evidence present")
    return bad


def evidence_is_batch5_shaped(evidence) -> list[str]:
    """The whole per-row evidence contract: exact name + email + phone, nothing forbidden."""
    problems = list(forbidden_evidence(evidence))
    if not has_exact_name_evidence(evidence):
        problems.append("no exact-name evidence")
    flags = corroborator_flags(evidence)
    if not flags["email_match"]:
        problems.append("no matched email")
    if not flags["phone_match"]:
        problems.append("no matched phone")
    if sum(flags.values()) < MIN_CORROBORATORS:
        problems.append(f"fewer than {MIN_CORROBORATORS} corroborators")
    return problems


# --- manifest -------------------------------------------------------------------------------

def sha256_of(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path, *, expect_sha=None, expect_rows=None,
                  expect_composition=None, expect_people=None) -> list[dict]:
    """Read and fully validate the frozen manifest. Never touches the database.

    Every gate here runs on bytes alone, so a tampered manifest is refused before a connection is
    opened. The defaults are the approved constants; the fixtures override them to exercise the
    real code path against a small batch.
    """
    path = Path(path)
    expect_sha = FROZEN_MANIFEST_SHA256 if expect_sha is None else expect_sha
    expect_rows = EXPECTED_ROWS if expect_rows is None else expect_rows
    expect_composition = EXPECTED_COMPOSITION if expect_composition is None else expect_composition
    expect_people = EXPECTED_DISTINCT_PEOPLE if expect_people is None else expect_people

    if not path.is_file():
        raise ManifestError(f"manifest not found at {path}")
    digest = sha256_of(path)
    if digest != expect_sha:
        raise ManifestError(f"manifest SHA256 {digest} != approved {expect_sha}")

    with path.open(newline="", encoding="utf-8-sig") as fh:
        raw = list(csv.DictReader(fh))
    if len(raw) != expect_rows:
        raise ManifestError(f"manifest has {len(raw)} rows, approved {expect_rows}")

    rows, seen = [], set()
    for r in raw:
        for col in MANIFEST_REQUIRED_COLUMNS:
            if col not in r:
                raise ManifestError(f"manifest is missing the {col!r} column")
        try:
            did = int(r["document_id"])
            pid = int(r["person_id"])
            corro = int(r["corroborator_count"])
            fact_id = int(r["owner_proposal_fact_id"])
            fact_version = int(r["owner_proposal_fact_version"])
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"unreadable manifest row: {r!r}") from exc
        if did in seen:
            raise ManifestError(f"duplicate document_id {did} in manifest")
        seen.add(did)
        if corro < MIN_CORROBORATORS:
            raise ManifestError(f"document {did} carries {corro} corroborators, "
                                f"fewer than {MIN_CORROBORATORS}")
        for col in ("address_corroboration_used", "zip_corroboration_used",
                    "shared_value_corroboration_used"):
            if (r[col] or "").strip().upper() != "NO":
                raise ManifestError(f"document {did}: {col} is {r[col]!r}, approved 'NO'")
        for col in ("document_fingerprint", "proposal_fingerprint",
                    "classification_fingerprint", "target_person_fingerprint"):
            if len((r[col] or "").strip()) != 64:
                raise ManifestError(f"document {did}: {col} is not a sha256 digest")
        rows.append({
            "document_id": did, "person_id": pid,
            "person_name": (r.get("person_name") or "").strip(),
            "corroborator_count": corro,
            "owner_proposal_fact_id": fact_id,
            "owner_proposal_fact_version": fact_version,
            "document_fingerprint": r["document_fingerprint"].strip(),
            "proposal_fingerprint": r["proposal_fingerprint"].strip(),
            "classification_fingerprint": r["classification_fingerprint"].strip(),
            "target_person_fingerprint": r["target_person_fingerprint"].strip(),
            "original_name": (r.get("original_name") or "").strip(),
        })

    composition = {}
    for r in rows:
        composition[r["corroborator_count"]] = composition.get(r["corroborator_count"], 0) + 1
    if composition != expect_composition:
        raise ManifestError(f"composition {composition} != approved {expect_composition}")
    people = {r["person_id"] for r in rows}
    if len(people) != expect_people:
        raise ManifestError(f"{len(people)} distinct people, approved {expect_people}")
    return sorted(rows, key=lambda r: r["document_id"])


# --- live state -----------------------------------------------------------------------------

LIVE_STATE_SQL = """
    SELECT d.id, d.person_id, d.household_id, d.organization_id, d.status, d.archived,
           d.deleted_at, d.review_status, d.original_name, d.sha256, d.tags,
           f.id            AS fact_id,
           f.version       AS fact_version,
           f.fact_value    AS fact_value,
           cl.id           AS classification_id,
           cl.doc_type     AS classification_doc_type,
           cl.confidence   AS classification_confidence,
           cl.classifier_version AS classifier_version
      FROM documents d
      LEFT JOIN document_facts f
        ON f.document_id = d.id AND f.fact_type = 'owner_proposal' AND f.is_current
      LEFT JOIN LATERAL (
            SELECT c.id, c.doc_type, c.confidence, c.classifier_version
              FROM document_classifications c
             WHERE c.document_id = d.id ORDER BY c.id DESC LIMIT 1) cl ON true
     WHERE d.id = ANY(:ids)
     ORDER BY d.id
"""

PERSON_STATE_SQL = """
    SELECT id, first_name, last_name, full_name, normalized_email, normalized_phone,
           household_id, active
      FROM people WHERE id = ANY(:ids)
"""


def verify_row(want, live, person) -> str | None:
    """Re-prove ONE frozen manifest row against live state. Returns a reason, or None if it holds.

    Ordered so the most specific cause is reported first: a drifted fingerprint says more than
    "still unowned" does.
    """
    if live is None:
        return "document not found"
    if live["person_id"] is not None:
        return f"already owned by person {live['person_id']}"
    if live["household_id"] is not None:
        return f"household scope present ({live['household_id']})"
    if live["organization_id"] is not None:
        return f"organization scope present ({live['organization_id']})"
    if live["archived"]:
        return "archived"
    if live["status"] == "deleted" or live["deleted_at"] is not None:
        return "deleted"
    if (live["review_status"] or "") != "not_required":
        return f"review_status is {live['review_status']!r}"

    if live["fact_id"] is None:
        return "no current owner_proposal fact"
    if live["fact_id"] != want["owner_proposal_fact_id"]:
        return (f"owner_proposal fact id drifted "
                f"{want['owner_proposal_fact_id']} -> {live['fact_id']}")
    if live["fact_version"] != want["owner_proposal_fact_version"]:
        return (f"owner_proposal fact version drifted "
                f"{want['owner_proposal_fact_version']} -> {live['fact_version']}")

    fv = live["fact_value"]
    fv = fv if isinstance(fv, dict) else json.loads(fv)
    if (fv.get("route") or "") != "HIGH":
        return f"route drifted to {fv.get('route')!r}"
    if (fv.get("entity_type") or "") != "person":
        return f"owner kind drifted to {fv.get('entity_type')!r}"
    if fv.get("entity_id") in (None, "") or int(fv["entity_id"]) != want["person_id"]:
        return f"proposed person drifted {want['person_id']} -> {fv.get('entity_id')}"

    problems = evidence_is_batch5_shaped(fv.get("evidence"))
    if problems:
        return "evidence no longer qualifies: " + "; ".join(problems)

    if document_fingerprint(live) != want["document_fingerprint"]:
        return "document fingerprint drifted"
    if proposal_fingerprint(live["fact_id"], live["fact_version"], fv) != want["proposal_fingerprint"]:
        return "proposal fingerprint drifted"
    if classification_fingerprint(
            live["classification_id"], live["classification_doc_type"],
            live["classification_confidence"], live["classifier_version"]) \
            != want["classification_fingerprint"]:
        return "classification fingerprint drifted"

    if person is None:
        return f"target person {want['person_id']} not found"
    if not person["active"]:
        return f"target person {want['person_id']} is inactive"
    if target_person_fingerprint(person) != want["target_person_fingerprint"]:
        return "target person fingerprint drifted"
    return None


def confirm_phrase(kind: str, rows: int) -> str:
    """``APPLY-STRICT-SAFE-OWNERSHIP-BATCH5-55`` / ``ROLLBACK-...``. Row count bound in."""
    return f"{kind}-{BATCH_ID}-{rows}"
