#!/usr/bin/env python3
"""D7 document-corroborated attribution — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_d7_document_verified_attribution.py

    # writes, and only with every key turned at once
    python scripts/apply_d7_document_verified_attribution.py \\
        --confirm APPLY-D7-DOCVERIFIED-7 --apply

WHY THIS RUNNER EXISTS SEPARATELY
---------------------------------
``apply_d7_attribution_batch1`` requires at least two Drake source contacts, because an identifier
appearing on two or more filings lets the entity's provenance and the identifier's contact set
cross-check each other. These seven rows have exactly ONE contact, so that cross-check is not
available and Batch 1 rightly refuses them.

They are admissible here on a different basis: for each one, a document filed under the proposed
entity — its own filed 1120S, its e-file authorisation, its Form 940 or its W-2s — carries a
taxpayer identifier that derives to the same hash, with the entity's own name printed beside it.
That is corroboration from outside Drake entirely.

So this runner lowers the contact floor to one and, in exchange, makes the document evidence
MANDATORY. A single-contact row is never accepted merely because canonical provenance covers its one
contact. Batch 1's own policy is untouched, and this runner will not load Batch 1's manifest.

WHAT COUNTS AS A CORROBORATING IDENTIFIER
-----------------------------------------
An identifier token must not be embedded in a longer numeric run. Depreciation schedules contain
strings like ``DUMP 196810-01-2013100.0``, where a naive ``\\d{2}-\\d{7}`` match finds the in-service
date ``10-01-2013`` and reports a contradiction that does not exist. The token boundary here
excludes a leading or trailing digit, dot or dash, which removed every such false positive from the
reviewed evidence while finding MORE genuine matches, not fewer.

A match counts only when the entity's own name sits immediately beside it, so a preparer's,
shareholder's or unrelated filer's identifier can never be mistaken for the subject's. "Beside" is
measured, not guessed: across the reviewed evidence the subject's identifier sits a median of 18
normalised characters from the entity name and the preparer's is never closer than 308, so the
window below sits at 60 — wide enough for every genuine occurrence, five times narrower than the
nearest foreign one. A window wide enough to reach the preparer line would call a correct document
contradictory on a compactly laid-out form.

An identifier that appears within that window and does NOT derive to the frozen hash is a
contradiction and aborts the entire batch.

THE RAW IDENTIFIER
------------------
Never printed, never logged, never written to the manifest, the receipt or audit metadata. It is
read from the filed return, checked to derive to the frozen hash, handed to the service — which
derives it again independently — and discarded.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BATCH_ID = "d7_one_contact_document_verified"
REPORT_ROOT = REPO_ROOT / "var" / BATCH_ID
ARTIFACT_DIR = REPO_ROOT / "reports" / "d7_document_verified"
DEFAULT_MANIFEST = ARTIFACT_DIR / "d7_one_contact_document_verified_batch_manifest.csv"
DEFAULT_SIDECAR = ARTIFACT_DIR / "d7_one_contact_document_verified_batch_manifest.json"

#: The reviewed artifacts. All four are checked; no one of them alone would catch every tampering.
APPROVED_MANIFEST_SHA256 = "389824c9c383107fb4df7d9a60bac49e58bb7d842c36ed5ec53a9a0480a5ab98"
APPROVED_SIDECAR_SHA256 = "eabcb78df49208e2f05f5c96831bd8c6af5f3d02853eec8bfa553b1a0d3bce32"
APPROVED_EVIDENCE_REVIEW_SHA256 = (
    "73925d10bfb3b91a66785176367f349f2dd858ba520481da2eb9ec8b3eff8cc0")
APPROVED_PLAN_DIGEST = "53ae4b9c6629e22d50b2de955ad4b7e013329c07626ae2861588d5751b3e5e99"
APPROVED_ROWS = 7

#: Its own key: a document-verified apply and a Batch 1 apply must not be mistaken for each other.
ADVISORY_LOCK_KEY = 0x0D7A0003

FORBIDDEN_TABLES = ("drake_identity", "person_source_links", "people", "relationship_entities",
                    "source_contacts", "drake_client_returns", "documents", "document_ocr",
                    "drake_identity_match_candidates")

MANIFEST_COLUMNS = ("dbi_id", "identifier_hash", "subject_name", "subject_type",
                    "proposed_relationship_entity_id", "source_contact_ids",
                    "source_contact_count", "provenance_form", "complete_coverage",
                    "document_corroboration_count", "identifier_entity_document_match",
                    "expected_esl_inserts", "expected_trust_level",
                    "expected_confirmation_source", "expected_evidence_method",
                    "dbi_row_sha256", "target_entity_sha256",
                    "evidence_review_artifact_sha256")

#: One contact is permitted here ONLY because document corroboration is mandatory below.
MIN_SOURCE_CONTACTS = 1
DOCUMENT_CORROBORATION_REQUIRED = True

#: A taxpayer identifier, not a fragment of a longer numeric run. See the module docstring.
IDENTIFIER_TOKEN = re.compile(r"(?<![\d.\-])\d{2}-\d{7}(?![\d.\-])")

#: How close, in normalised characters, an identifier must sit to the entity's name to be about it.
#: Measured from the reviewed evidence: subject identifiers median 18 away, preparer's minimum 308.
NAME_ADJACENCY = 60
MAX_PAGES = 14


class Abort(SystemExit):
    """A gate refused. Always raised before any write, or before a commit."""


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def confirm_phrase(rows: int) -> str:
    return f"APPLY-D7-DOCVERIFIED-{int(rows)}"


def _ints(value) -> list[int]:
    return [int(x) for x in str(value).split("|") if str(x).strip()]


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def plan_digest(rows) -> str:
    return hashlib.sha256(canon([
        {"dbi_id": int(r["dbi_id"]), "identifier_hash": r["identifier_hash"],
         "entity": int(r["proposed_relationship_entity_id"]),
         "contacts": _ints(r["source_contact_ids"])} for r in rows]).encode()).hexdigest()


def flat(value: str) -> str:
    """Compare names without whitespace, case or ampersand spelling getting in the way."""
    return " ".join((value or "").upper().replace("&", "AND").split())


def extract_pdf_text(path: str) -> str:                        # pragma: no cover - I/O boundary
    from pypdf import PdfReader

    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages[:MAX_PAGES])


def load_manifest(manifest_path=DEFAULT_MANIFEST, sidecar_path=DEFAULT_SIDECAR, *,
                  expect_sha=APPROVED_MANIFEST_SHA256, expect_sidecar=APPROVED_SIDECAR_SHA256,
                  expect_digest=APPROVED_PLAN_DIGEST, expect_rows=APPROVED_ROWS,
                  expect_evidence=APPROVED_EVIDENCE_REVIEW_SHA256):
    """Read and gate the frozen pair. No connection is opened and no file is touched until this
    returns."""
    manifest_path, sidecar_path = Path(manifest_path), Path(sidecar_path)
    digest = sha256_of(manifest_path)
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    side_digest = sha256_of(sidecar_path)
    if expect_sidecar and side_digest != expect_sidecar:
        raise Abort(f"ABORT: sidecar SHA256 {side_digest} != approved {expect_sidecar}")

    rows = list(csv.DictReader(manifest_path.read_text(encoding="utf-8").splitlines()))
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not rows:
        raise Abort("ABORT: manifest is empty")
    missing = [c for c in MANIFEST_COLUMNS if c not in rows[0]]
    if missing:
        raise Abort(f"ABORT: manifest is missing columns {missing}")
    if sidecar.get("batch") != BATCH_ID:
        raise Abort(f"ABORT: sidecar is for {sidecar.get('batch')!r}, not {BATCH_ID!r}")
    if expect_rows is not None and len(rows) != expect_rows:
        raise Abort(f"ABORT: manifest has {len(rows)} rows, approved is {expect_rows}")
    if sidecar.get("row_count") != len(rows):
        raise Abort("ABORT: sidecar row_count disagrees with the manifest")
    if sidecar.get("manifest_csv_sha256") != digest:
        raise Abort("ABORT: sidecar does not pin this manifest")
    if expect_evidence and sidecar.get("evidence_review_artifact_sha256") != expect_evidence:
        raise Abort(f"ABORT: evidence-review artifact hash "
                    f"{sidecar.get('evidence_review_artifact_sha256')} != approved {expect_evidence}")

    ids = [int(r["dbi_id"]) for r in rows]
    if len(set(ids)) != len(ids):
        raise Abort("ABORT: manifest contains a duplicate dbi_id")
    hashes = [r["identifier_hash"] for r in rows]
    if len(set(hashes)) != len(hashes):
        raise Abort("ABORT: manifest contains a duplicate identifier_hash")
    entities = [int(r["proposed_relationship_entity_id"]) for r in rows]
    if len(set(entities)) != len(entities):
        raise Abort("ABORT: manifest targets one entity more than once")

    by_id = {int(r["dbi_id"]): r for r in sidecar.get("rows") or []}
    if set(by_id) != set(ids):
        raise Abort("ABORT: sidecar rows do not match the manifest rows")

    seen: dict[int, int] = {}
    for row in rows:
        dbi_id = int(row["dbi_id"])
        contacts = _ints(row["source_contact_ids"])
        if len(contacts) < MIN_SOURCE_CONTACTS:
            raise Abort(f"ABORT: dbi {dbi_id} names no source contact")
        if len(contacts) != int(row["source_contact_count"]):
            raise Abort(f"ABORT: dbi {dbi_id} contact count disagrees with its contact list")
        if int(row["expected_esl_inserts"]) != len(contacts):
            raise Abort(f"ABORT: dbi {dbi_id} expected_esl_inserts != contact count")
        if str(row["complete_coverage"]).lower() != "true":
            raise Abort(f"ABORT: dbi {dbi_id} is not marked complete_coverage")
        if str(row["identifier_entity_document_match"]).lower() != "true":
            raise Abort(f"ABORT: dbi {dbi_id} is not marked identifier_entity_document_match")
        if row["evidence_review_artifact_sha256"] != expect_evidence:
            raise Abort(f"ABORT: dbi {dbi_id} cites a different evidence-review artifact")
        docs = by_id[dbi_id].get("document_corroboration") or []
        if DOCUMENT_CORROBORATION_REQUIRED and not docs:
            raise Abort(f"ABORT: dbi {dbi_id} carries no document corroboration; this runner "
                        "accepts a single-contact row only with it")
        if int(row["document_corroboration_count"]) != len(docs):
            raise Abort(f"ABORT: dbi {dbi_id} document count disagrees with the sidecar")
        for contact in contacts:
            if contact in seen:
                raise Abort(f"ABORT: source contact {contact} appears on dbi "
                            f"{seen[contact]} and {dbi_id}")
            seen[contact] = dbi_id

    found = plan_digest(rows)
    if expect_digest and found != expect_digest:
        raise Abort(f"ABORT: plan digest {found} != approved {expect_digest}")
    return rows, sidecar


def check_documents(connection, text, derive, row, sidecar_row, *, extract=extract_pdf_text) -> list:
    """Every frozen document must still say what it said. One that does not aborts the batch."""
    dbi_id = int(row["dbi_id"])
    entity_id = int(row["proposed_relationship_entity_id"])
    target_hash = row["identifier_hash"]
    entity_name = connection.execute(text(
        "select name from relationship_entities where id = :e"), {"e": entity_id}).scalar()
    confirmed = []
    for doc in sidecar_row.get("document_corroboration") or []:
        doc_id, path = int(doc["document_id"]), doc["document_path"]
        live = connection.execute(text(
            "select id, organization_id, storage_uri from documents where id = :i"),
            {"i": doc_id}).mappings().one_or_none()
        if live is None:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} no longer exists")
        if int(live["organization_id"] or 0) != entity_id:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} is no longer filed under "
                        f"entity {entity_id}")
        if live["storage_uri"] != path:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} has moved since the freeze")
        if not os.path.exists(path):
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} is missing from storage")
        actual = sha256_of(path)
        if actual != doc["document_sha256"]:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} changed since the freeze "
                        f"({actual} != {doc['document_sha256']})")

        # Distances are measured on the normalised text, so line breaks and column padding in the
        # extracted layout cannot change how far an identifier looks from the name it belongs to.
        body = flat(extract(path))
        anchors = [m.start() for m in re.finditer(re.escape(flat(entity_name)), body)]
        corroborating = contradicting = 0
        for match in IDENTIFIER_TOKEN.finditer(body):
            if not anchors:
                break                                          # the document never names the entity
            if min(abs(match.start() - a) for a in anchors) > NAME_ADJACENCY:
                continue                                       # about somebody else on the page
            if derive(match.group(0)) == target_hash:
                corroborating += 1
            else:
                contradicting += 1
        if contradicting:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} carries {contradicting} "
                        "identifier(s) beside this entity's name that are not this identifier")
        if not corroborating:
            raise Abort(f"ABORT: dbi {dbi_id} document {doc_id} no longer corroborates this "
                        "identifier for this entity")
        confirmed.append({"document_id": doc_id, "document_path": path,
                          "document_sha256": actual, "corroborating_occurrences": corroborating})
    if DOCUMENT_CORROBORATION_REQUIRED and not confirmed:
        raise Abort(f"ABORT: dbi {dbi_id} has no surviving document corroboration")
    return confirmed


def _fingerprints(connection, text):
    out = {}
    for table in FORBIDDEN_TABLES:
        out[table] = connection.execute(text(
            f"select count(*)::text || '/' || coalesce(md5(string_agg(t::text, '~|~' "
            f"order by t::text)), '') from {table} t")).scalar()
    return out


def _raw_identifier(connection, text, derive, identifier_hash: str) -> str:
    """From the filed returns, proven by re-derivation. Never logged."""
    for value in connection.execute(text(
            "select raw_data->>'TP_Social' from drake_client_returns "
            "where taxpayer_identifier_hash = :h and raw_data ? 'TP_Social' "
            "order by tax_year desc"), {"h": identifier_hash}).scalars():
        if value and derive(value) == identifier_hash:
            return value
    raise Abort(f"ABORT: no filed return carries a raw identifier deriving to "
                f"{identifier_hash[:12]}..")


def _revalidate(connection, text, row) -> None:
    """Every frozen fact, re-read under lock, before the first mutation."""
    dbi_id = int(row["dbi_id"])
    entity_id = int(row["proposed_relationship_entity_id"])
    contacts = _ints(row["source_contact_ids"])

    current = connection.execute(text(
        "select id, identifier_hash, subject_type, relationship_entity_id, trust_level, "
        "confirmation_source, evidence_method, confirmed_by_user_id "
        "from drake_business_identity where id = :i for update"),
        {"i": dbi_id}).mappings().one_or_none()
    if current is None:
        raise Abort(f"ABORT: dbi {dbi_id} no longer exists")
    if current["identifier_hash"] != row["identifier_hash"]:
        raise Abort(f"ABORT: dbi {dbi_id} now carries a different identifier")
    if current["subject_type"] != row["subject_type"]:
        raise Abort(f"ABORT: dbi {dbi_id} subject_type changed since the freeze")
    if current["relationship_entity_id"] is not None:
        raise Abort(f"ABORT: dbi {dbi_id} is already attributed to entity "
                    f"{current['relationship_entity_id']}")
    for column in ("trust_level", "confirmation_source", "evidence_method",
                   "confirmed_by_user_id"):
        if current[column] is not None:
            raise Abort(f"ABORT: dbi {dbi_id} already carries {column}={current[column]!r}")

    entity = connection.execute(text(
        "select id, active from relationship_entities where id = :e"),
        {"e": entity_id}).mappings().one_or_none()
    if entity is None:
        raise Abort(f"ABORT: target entity {entity_id} no longer exists")
    if not entity["active"]:
        raise Abort(f"ABORT: target entity {entity_id} is inactive")

    live_contacts = [int(x) for x in connection.execute(text(
        "select id from source_contacts where source_system = 'Drake' "
        "and raw_data->>'identifier_hash' = :h order by id"),
        {"h": row["identifier_hash"]}).scalars()]
    if live_contacts != sorted(contacts):
        raise Abort(f"ABORT: dbi {dbi_id} source-contact set changed since the freeze")
    live_returns = sorted(int(x) for x in connection.execute(text(
        "select id from drake_client_returns where taxpayer_identifier_hash = :h "
        "or spouse_identifier_hash = :h"), {"h": row["identifier_hash"]}).scalars())
    if live_returns != sorted(_ints(row["drake_return_ids"])):
        raise Abort(f"ABORT: dbi {dbi_id} return set changed since the freeze")

    if connection.execute(text("select count(*) from drake_identity where identifier_hash = :h"),
                          {"h": row["identifier_hash"]}).scalar():
        raise Abort(f"ABORT: dbi {dbi_id} identifier is back in drake_identity")
    if connection.execute(text(
            "select count(*) from entity_source_links where source_contact_id = any(:i)"),
            {"i": contacts}).scalar():
        raise Abort(f"ABORT: dbi {dbi_id} has source contacts already linked to an entity")
    if connection.execute(text(
            "select count(*) from drake_identity_match_candidates where identifier_hash = :h"),
            {"h": row["identifier_hash"]}).scalar():
        raise Abort(f"ABORT: dbi {dbi_id} has a pending match candidate")
    if connection.execute(text(
            "select count(*) from person_source_links where source_contact_id = any(:i)"),
            {"i": contacts}).scalar():
        raise Abort(f"ABORT: dbi {dbi_id} source contact is linked to a person; a single-contact "
                    "row with a person link is held for human review, not attributed here")
    others = connection.execute(text(
        "select count(*) from drake_business_identity where relationship_entity_id = :e "
        "and identifier_hash <> :h"),
        {"e": entity_id, "h": row["identifier_hash"]}).scalar()
    if others:
        raise Abort(f"ABORT: entity {entity_id} already holds {others} other identifier(s)")


def run(manifest_path=DEFAULT_MANIFEST, sidecar_path=DEFAULT_SIDECAR, *, apply_changes=False,
        confirm=None, expect_sha=APPROVED_MANIFEST_SHA256, expect_sidecar=APPROVED_SIDECAR_SHA256,
        expect_digest=APPROVED_PLAN_DIGEST, expect_rows=APPROVED_ROWS,
        expect_evidence=APPROVED_EVIDENCE_REVIEW_SHA256, output_root=REPORT_ROOT,
        extract=extract_pdf_text, out=print) -> dict:
    from sqlalchemy import text

    from app.db import engine
    from app.services.drake_identifier import identifier_hash as derive
    from app.services.drake_machine_attribution import (
        AUDIT_ACTION,
        EVIDENCE_METHOD,
        MACHINE,
        AttributionRefused,
        AttributionRequest,
        attribute_entity_by_provenance,
    )
    from app.services.link_trust import IDENTIFIER_VERIFIED

    rows, sidecar = load_manifest(manifest_path, sidecar_path, expect_sha=expect_sha,
                                  expect_sidecar=expect_sidecar, expect_digest=expect_digest,
                                  expect_rows=expect_rows, expect_evidence=expect_evidence)
    sidecar_by_id = {int(r["dbi_id"]): r for r in sidecar["rows"]}
    digest = plan_digest(rows)
    want = confirm_phrase(len(rows))
    expected_links = sum(len(_ints(r["source_contact_ids"])) for r in rows)

    out(f"frozen manifest: {manifest_path}")
    out(f"  sha256   : {sha256_of(manifest_path)}")
    out(f"  sidecar  : {sha256_of(sidecar_path)}")
    out(f"  evidence : {sidecar.get('evidence_review_artifact_sha256')}")
    out(f"  rows     : {len(rows)}  contacts: {expected_links}  documents: "
        f"{sum(len(sidecar_by_id[int(r['dbi_id'])].get('document_corroboration') or []) for r in rows)}")
    out(f"  digest   : {digest}")

    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_root) / f"d7-docverified-{stamp}"
    report = {"batch": BATCH_ID, "rows": len(rows), "manifest": str(manifest_path),
              "manifest_sha256": sha256_of(manifest_path),
              "sidecar_sha256": sha256_of(sidecar_path),
              "evidence_review_sha256": sidecar.get("evidence_review_artifact_sha256"),
              "plan_digest": digest, "attributed": 0, "links_created": 0, "documents_verified": 0,
              "committed": False, "refusals": [], "report_dir": str(out_dir),
              "rollback_manifest": None, "rollback_manifest_sha256": None}

    # REPEATABLE READ so the before/after fingerprints below compare like with like. Under READ
    # COMMITTED each statement takes a fresh snapshot, so an unrelated service write — the OCR sweep
    # touches `documents` every few minutes — would appear as a forbidden-table change and abort a
    # correct apply. This transaction's own writes remain visible to it, so the guard still catches
    # what it is there to catch.
    connection = engine.connect().execution_options(isolation_level="REPEATABLE READ")
    transaction = connection.begin()
    try:
        if connection.execute(text("show transaction_read_only")).scalar() == "on":
            raise Abort("ABORT: session is read-only; this apply needs a writable session")
        connection.execute(text("select pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        before_fp = _fingerprints(connection, text)
        before = {
            "drake_business_identity": connection.execute(
                text("select count(*) from drake_business_identity")).scalar(),
            "attributed": connection.execute(text(
                "select count(*) from drake_business_identity "
                "where relationship_entity_id is not null")).scalar(),
            "entity_source_links": connection.execute(
                text("select count(*) from entity_source_links")).scalar(),
            "attribution_audits": connection.execute(text(
                "select count(*) from audit_events where action = :a"),
                {"a": AUDIT_ACTION}).scalar(),
        }
        out(f"  before: {before}")

        evidence = {}
        for row in rows:
            _revalidate(connection, text, row)
            evidence[int(row["dbi_id"])] = check_documents(
                connection, text, derive, row, sidecar_by_id[int(row["dbi_id"])], extract=extract)
        report["documents_verified"] = sum(len(v) for v in evidence.values())
        out(f"  revalidated {len(rows)}/{len(rows)} rows; "
            f"{report['documents_verified']} corroborating document(s) re-verified")

        requests = [(row, AttributionRequest(
            relationship_entity_id=int(row["proposed_relationship_entity_id"]),
            identifier=_raw_identifier(connection, text, derive, row["identifier_hash"]),
            identifier_type="ein",
            source_contact_ids=tuple(_ints(row["source_contact_ids"])),
            reason=f"{BATCH_ID} (plan {digest[:12]})")) for row in rows]

        if not apply_changes:
            savepoint = connection.begin_nested()
            refusals, links = [], 0
            try:
                for row, request in requests:
                    try:
                        result = attribute_entity_by_provenance(connection, request)
                        links += len(result.source_links_created)
                    except AttributionRefused as exc:
                        refusals.append({"dbi_id": int(row["dbi_id"]), "code": exc.code,
                                         "message": exc.message})
                if not refusals:
                    _check_write_shape(connection, text, rows, before, expected_links,
                                       AUDIT_ACTION, IDENTIFIER_VERIFIED, MACHINE, EVIDENCE_METHOD)
                    _check_forbidden(connection, text, before_fp)
            finally:
                savepoint.rollback()
            report["refusals"], report["links_created"] = refusals, links
            if refusals:
                out(f"  DRY RUN — {len(refusals)} of {len(rows)} would be REFUSED:")
                for r in refusals[:10]:
                    out(f"    dbi {r['dbi_id']}  {r['code']}: {r['message'][:90]}")
            else:
                out(f"  DRY RUN — all {len(rows)} would attribute, {links} links; nothing written")
            raise Abort("DRY RUN — no writes (pass --apply with the confirmation phrase to write)")

        results = []
        for row, request in requests:
            try:
                results.append((row, attribute_entity_by_provenance(connection, request)))
            except AttributionRefused as exc:
                raise Abort(f"ABORT: dbi {row['dbi_id']} refused — [{exc.code}] "
                            f"{exc.message}") from exc
        report["attributed"] = len(results)
        report["links_created"] = sum(len(r.source_links_created) for _, r in results)
        out(f"  attributed {len(results)} identities, {report['links_created']} links")

        _check_write_shape(connection, text, rows, before, expected_links, AUDIT_ACTION,
                           IDENTIFIER_VERIFIED, MACHINE, EVIDENCE_METHOD)
        _check_forbidden(connection, text, before_fp)

        out_dir.mkdir(parents=True, exist_ok=True)
        payload = _rollback_payload(connection, text, rows, results, report, digest, evidence)
        blob = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        rollback_path = out_dir / "docverified_rollback_manifest.json"
        rollback_path.write_bytes(blob)
        report["rollback_manifest"] = str(rollback_path)
        report["rollback_manifest_sha256"] = hashlib.sha256(blob).hexdigest()
        out(f"  rollback manifest: {rollback_path}")
        out(f"  rollback sha256  : {report['rollback_manifest_sha256']}  (written BEFORE commit)")

        transaction.commit()
        report["committed"] = True
        out(f"  COMMITTED {len(results)} attributions")
    except BaseException:
        if not report["committed"]:
            transaction.rollback()
        raise
    finally:
        connection.close()

    (out_dir / "receipt.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _check_write_shape(connection, text, rows, before, expected_links, audit_action,
                       trust, source, method) -> None:
    after = {
        "drake_business_identity": connection.execute(
            text("select count(*) from drake_business_identity")).scalar(),
        "attributed": connection.execute(text(
            "select count(*) from drake_business_identity "
            "where relationship_entity_id is not null")).scalar(),
        "entity_source_links": connection.execute(
            text("select count(*) from entity_source_links")).scalar(),
        "attribution_audits": connection.execute(text(
            "select count(*) from audit_events where action = :a"), {"a": audit_action}).scalar(),
    }
    expected = {
        "drake_business_identity": before["drake_business_identity"],
        "attributed": before["attributed"] + len(rows),
        "entity_source_links": before["entity_source_links"] + expected_links,
        "attribution_audits": before["attribution_audits"] + len(rows),
    }
    if after != expected:
        raise Abort(f"ABORT: write shape {after} != expected {expected}")

    ids = [int(r["dbi_id"]) for r in rows]
    wrong = connection.execute(text(
        "select count(*) from drake_business_identity d where d.id = any(:i) and not ("
        "  d.relationship_entity_id is not null and d.trust_level = :t "
        "  and d.confirmation_source = :s and d.evidence_method = :m "
        "  and d.confirmed_by_user_id is null)"),
        {"i": ids, "t": trust, "s": source, "m": method}).scalar()
    if wrong:
        raise Abort(f"ABORT: {wrong} attributed rows do not carry this service's evidence, "
                    "or gained a confirming user")
    for row in rows:
        if connection.execute(text(
                "select count(*) from drake_business_identity where id = :i "
                "and relationship_entity_id <> :e"),
                {"i": int(row["dbi_id"]),
                 "e": int(row["proposed_relationship_entity_id"])}).scalar():
            raise Abort(f"ABORT: dbi {row['dbi_id']} is bound to an entity the manifest did "
                        "not name")
    if connection.execute(text(
            "select count(*) from audit_events where action = :a and actor_user_id is not null"),
            {"a": audit_action}).scalar():
        raise Abort("ABORT: an attribution audit event carries an actor; these are unattended")


def _check_forbidden(connection, text, before_fp) -> None:
    after_fp = _fingerprints(connection, text)
    changed = [t for t in FORBIDDEN_TABLES if before_fp[t] != after_fp[t]]
    if changed:
        detail = "; ".join(
            f"{t}: {before_fp[t].split('/')[0]} -> {after_fp[t].split('/')[0]} rows" for t in changed)
        raise Abort(f"ABORT: forbidden table(s) changed: {changed} ({detail})")


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _rollback_payload(connection, text, rows, results, report, digest, evidence) -> dict:
    payload = {"batch": BATCH_ID, "created_at": datetime.now(UTC).isoformat(),
               "manifest_sha256": report["manifest_sha256"],
               "sidecar_sha256": report["sidecar_sha256"],
               "evidence_review_sha256": report["evidence_review_sha256"],
               "plan_digest": digest, "rows": []}
    for row, result in results:
        dbi_id = int(row["dbi_id"])
        contacts = _ints(row["source_contact_ids"])
        post = connection.execute(text(
            "select relationship_entity_id, trust_level, confirmation_source, evidence_method, "
            "confirmed_by_user_id, updated_at from drake_business_identity where id = :i"),
            {"i": dbi_id}).mappings().one()
        links = [dict(r) for r in connection.execute(text(
            "select id, source_contact_id, relationship_entity_id, match_method, match_score, "
            "confirmed, trust_level, confirmation_source, evidence_method "
            "from entity_source_links where source_contact_id = any(:i) order by id"),
            {"i": contacts}).mappings()]
        payload["rows"].append({
            "dbi_id": dbi_id, "identifier_hash": row["identifier_hash"],
            "target_entity_id": int(row["proposed_relationship_entity_id"]),
            "source_contact_ids": contacts,
            "dbi_pre_image": {"relationship_entity_id": None, "trust_level": None,
                              "confirmation_source": None, "evidence_method": None},
            "dbi_post_image": {k: _jsonable(v) for k, v in post.items()},
            "created_entity_source_link_ids": [int(link["id"]) for link in links],
            "created_entity_source_links": [{k: _jsonable(v) for k, v in link.items()}
                                            for link in links],
            "audit_event_id": int(result.audit_event_id),
            "document_evidence": evidence.get(dbi_id, []),
        })
    created = [i for r in payload["rows"] for i in r["created_entity_source_link_ids"]]
    expected = sum(len(_ints(r["source_contact_ids"])) for r in rows)
    if len(created) != len(set(created)):
        raise Abort("ABORT: rollback manifest captured a duplicate link id")
    if len(created) != expected:
        raise Abort(f"ABORT: rollback manifest captured {len(created)} link ids, "
                    f"expected {expected}")
    if any(r["audit_event_id"] <= 0 for r in payload["rows"]):
        raise Abort("ABORT: rollback manifest is missing an audit event id")
    if any(not r["document_evidence"] for r in payload["rows"]):
        raise Abort("ABORT: rollback manifest is missing document evidence for a row")
    blob = canon(payload)
    if IDENTIFIER_TOKEN.search(blob):
        raise Abort("ABORT: rollback manifest would contain a raw identifier")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="D7 document-corroborated attribution guarded apply")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--sidecar", default=str(DEFAULT_SIDECAR))
    parser.add_argument("--expect-sha", default=APPROVED_MANIFEST_SHA256)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output-root", default=str(REPORT_ROOT))
    args = parser.parse_args()
    try:
        run(args.manifest, args.sidecar, apply_changes=args.apply, confirm=args.confirm,
            expect_sha=args.expect_sha, output_root=args.output_root)
    except Abort as exc:
        print(exc)
        return 0 if str(exc).startswith("DRY RUN") else 2
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
