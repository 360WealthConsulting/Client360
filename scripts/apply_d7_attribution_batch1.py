#!/usr/bin/env python3
"""D7 machine attribution Batch 1 — the guarded apply. ALL-OR-NOTHING.

    # default: reads, validates, reports, writes NOTHING
    python scripts/apply_d7_attribution_batch1.py

    # writes, and only with every key turned at once
    python scripts/apply_d7_attribution_batch1.py \\
        --confirm APPLY-D7-ATTRIB-BATCH1-39 --apply

WHAT IT WRITES
--------------
For each frozen row: the identifier's ``drake_business_identity`` row gains its owning entity and
this service's trust evidence, one ``entity_source_links`` row per source contact, and one
unattended audit entry. Nothing else. A single refusal aborts the whole batch.

THE BATCH IS THE MANIFEST
-------------------------
Rows come only from the reviewed manifest, pinned by SHA-256 and by a plan digest over
(dbi_id, identifier_hash, entity, contacts). Nothing is discovered at run time, so the batch cannot
grow, shrink or retarget between review and apply. A manifest that hashes differently is refused
before a connection is opened.

WHY THE RAW IDENTIFIER IS NOT IN THE MANIFEST
---------------------------------------------
``attribute_entity_by_provenance`` refuses a caller-supplied hash: it derives one from the raw
identifier itself, so a caller cannot assert an identity. The raw identifier is a taxpayer EIN and
has no place in a committed artifact, so the manifest freezes only the hash and the runner recovers
the raw value from the filed returns, then **refuses unless it re-derives to the frozen hash**. The
service derives it again independently. The value is never logged.

WHY THE ROLLBACK MANIFEST IS BUILT BEFORE THE COMMIT
-----------------------------------------------------
``entity_source_links.id`` is generated on insert, so the ids needed to reverse this batch exist
only inside the transaction, after the writes and before the commit. They are captured there,
written to disk and hashed while the transaction can still be rolled back. A receipt written after
the commit would be a record of something already irreversible.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BATCH_ID = "d7_machine_attribution_batch1"
REPORT_ROOT = REPO_ROOT / "var" / BATCH_ID
DEFAULT_MANIFEST = (REPO_ROOT / "reports" / "d7_attribution_batch1"
                    / "d7_machine_attribution_batch1_manifest.csv")

#: The reviewed artifact. Both are checked; neither alone would catch a reordered file.
APPROVED_MANIFEST_SHA256 = "2cbfadecb448ce3be60abc8bcbe83aa181f3c347a8552d5a8f887623927bc6b4"
APPROVED_PLAN_DIGEST = "2b4066bdbe60650fbdefe763b497b5f7c2af7659c47e3e6a3f18a99b501894ec"
APPROVED_ROWS = 39
APPROVED_ESL_INSERTS = 112

#: Serialised against itself, so two Batch 1 applies cannot interleave.
ADVISORY_LOCK_KEY = 0x0D7A0001

#: Tables this batch must not change. Fingerprinted before and after, inside the transaction.
FORBIDDEN_TABLES = ("drake_identity", "person_source_links", "people", "relationship_entities",
                    "source_contacts", "drake_client_returns", "documents",
                    "drake_identity_match_candidates")

MANIFEST_COLUMNS = ("dbi_id", "identifier_hash", "subject_name", "subject_type",
                    "proposed_relationship_entity_id", "source_contact_ids",
                    "source_contact_count", "provenance_form", "complete_coverage",
                    "expected_esl_inserts", "expected_trust_level",
                    "expected_confirmation_source", "expected_evidence_method",
                    "dbi_row_sha256", "target_entity_sha256")

#: Batch 1 policy. The manifest already satisfies these; the runner refuses to be handed a file
#: that does not, so a future manifest cannot quietly widen the reviewed policy.
MIN_SOURCE_CONTACTS = 2


class Abort(SystemExit):
    """A gate refused. Always raised before any write, or before a commit."""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def confirm_phrase(rows: int) -> str:
    return f"APPLY-D7-ATTRIB-BATCH1-{int(rows)}"


def _ints(value: str) -> list[int]:
    return [int(x) for x in str(value).split("|") if str(x).strip()]


def plan_digest(rows) -> str:
    """The reviewed plan, canonically. Identity of the batch, independent of column order."""
    plan = [{"dbi_id": int(r["dbi_id"]), "identifier_hash": r["identifier_hash"],
             "entity": int(r["proposed_relationship_entity_id"]),
             "contacts": _ints(r["source_contact_ids"])} for r in rows]
    canonical = json.dumps(plan, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_manifest(path: Path, *, expect_sha: str | None = APPROVED_MANIFEST_SHA256,
                  expect_digest: str | None = APPROVED_PLAN_DIGEST,
                  expect_rows: int | None = APPROVED_ROWS) -> list[dict]:
    """Read the frozen manifest, or refuse. No connection is opened until this returns."""
    path = Path(path)
    digest = sha256_of(path)
    if expect_sha and digest != expect_sha:
        raise Abort(f"ABORT: manifest SHA256 {digest} != approved {expect_sha}")
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    if not rows:
        raise Abort("ABORT: manifest is empty")
    missing = [c for c in MANIFEST_COLUMNS if c not in rows[0]]
    if missing:
        raise Abort(f"ABORT: manifest is missing columns {missing}")
    if expect_rows is not None and len(rows) != expect_rows:
        raise Abort(f"ABORT: manifest has {len(rows)} rows, approved is {expect_rows}")

    ids = [int(r["dbi_id"]) for r in rows]
    if len(set(ids)) != len(ids):
        raise Abort("ABORT: manifest contains a duplicate dbi_id")
    hashes = [r["identifier_hash"] for r in rows]
    if len(set(hashes)) != len(hashes):
        raise Abort("ABORT: manifest contains a duplicate identifier_hash")
    entities = [int(r["proposed_relationship_entity_id"]) for r in rows]
    if len(set(entities)) != len(entities):
        raise Abort("ABORT: manifest targets one entity more than once — "
                    "an entity holding two identifiers is held for review, not applied")

    seen_contacts: dict[int, int] = {}
    for row in rows:
        contacts = _ints(row["source_contact_ids"])
        if len(contacts) < MIN_SOURCE_CONTACTS:
            raise Abort(f"ABORT: dbi {row['dbi_id']} has {len(contacts)} source contact(s); "
                        f"Batch 1 policy requires at least {MIN_SOURCE_CONTACTS}")
        if len(contacts) != int(row["source_contact_count"]):
            raise Abort(f"ABORT: dbi {row['dbi_id']} contact count disagrees with its contact list")
        if int(row["expected_esl_inserts"]) != len(contacts):
            raise Abort(f"ABORT: dbi {row['dbi_id']} expected_esl_inserts != contact count")
        if str(row["complete_coverage"]).lower() != "true":
            raise Abort(f"ABORT: dbi {row['dbi_id']} is not marked complete_coverage")
        for contact in contacts:
            if contact in seen_contacts:
                raise Abort(f"ABORT: source contact {contact} appears on dbi "
                            f"{seen_contacts[contact]} and {row['dbi_id']}")
            seen_contacts[contact] = int(row["dbi_id"])

    total = sum(len(_ints(r["source_contact_ids"])) for r in rows)
    if expect_rows is not None and total != APPROVED_ESL_INSERTS:
        raise Abort(f"ABORT: manifest names {total} source contacts, "
                    f"approved is {APPROVED_ESL_INSERTS}")

    found = plan_digest(rows)
    if expect_digest and found != expect_digest:
        raise Abort(f"ABORT: plan digest {found} != approved {expect_digest}")
    return rows


def _fingerprints(connection, text):
    out = {}
    for table in FORBIDDEN_TABLES:
        out[table] = connection.execute(text(
            f"select count(*)::text || '/' || coalesce(md5(string_agg(t::text, '~|~' "
            f"order by t::text)), '') from {table} t")).scalar()
    return out


def _raw_identifier(connection, text, derive, identifier_hash: str) -> str:
    """The raw identifier from the filed returns, proven by re-derivation. Never returned to logs.

    The service derives the hash again from whatever this returns, so a wrong value cannot attribute
    the wrong subject — it would refuse. This check exists so the failure is a clear abort here
    rather than an opaque refusal three frames down.
    """
    for value in connection.execute(text(
            "select raw_data->>'TP_Social' from drake_client_returns "
            "where taxpayer_identifier_hash = :h and raw_data ? 'TP_Social' "
            "order by tax_year desc"), {"h": identifier_hash}).scalars():
        if value and derive(value) == identifier_hash:
            return value
    raise Abort(f"ABORT: no filed return carries a raw identifier deriving to "
                f"{identifier_hash[:12]}..; this row cannot be attributed without one")


def _revalidate(connection, text, row) -> None:
    """Every frozen fact, re-read under lock. Anything that moved since the freeze aborts."""
    dbi_id = int(row["dbi_id"])
    entity_id = int(row["proposed_relationship_entity_id"])
    contacts = _ints(row["source_contact_ids"])

    current = connection.execute(text(
        "select id, identifier_hash, subject_type, relationship_entity_id, trust_level, "
        "confirmation_source, evidence_method from drake_business_identity where id = :i "
        "for update"), {"i": dbi_id}).mappings().one_or_none()
    if current is None:
        raise Abort(f"ABORT: dbi {dbi_id} no longer exists")
    if current["identifier_hash"] != row["identifier_hash"]:
        raise Abort(f"ABORT: dbi {dbi_id} now carries a different identifier")
    if current["subject_type"] != row["subject_type"]:
        raise Abort(f"ABORT: dbi {dbi_id} subject_type changed since the freeze")
    if current["relationship_entity_id"] is not None:
        raise Abort(f"ABORT: dbi {dbi_id} is already attributed to entity "
                    f"{current['relationship_entity_id']}; a re-run is not silently repeated")
    for column in ("trust_level", "confirmation_source", "evidence_method"):
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
        raise Abort(f"ABORT: dbi {dbi_id} source-contact set changed since the freeze: "
                    f"{live_contacts} != {sorted(contacts)}")

    live_returns = sorted(int(x) for x in connection.execute(text(
        "select id from drake_client_returns where taxpayer_identifier_hash = :h "
        "or spouse_identifier_hash = :h"), {"h": row["identifier_hash"]}).scalars())
    if live_returns != sorted(_ints(row["drake_return_ids"])):
        raise Abort(f"ABORT: dbi {dbi_id} return set changed since the freeze")

    if connection.execute(text(
            "select count(*) from drake_identity where identifier_hash = :h"),
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

    # The provenance that justified this row must still cover the COMPLETE contact set, through the
    # canonical form the freeze recorded. An intersection is what the service accepts; this batch
    # was reviewed on full coverage and will not settle for less.
    covered = _covered_contacts(connection, text, entity_id)
    if not set(contacts) <= covered:
        raise Abort(f"ABORT: entity {entity_id} provenance no longer covers the complete "
                    f"contact set of dbi {dbi_id}: missing {sorted(set(contacts) - covered)}")


def _covered_contacts(connection, text, entity_id: int) -> set[int]:
    """Every source contact the entity's stored provenance denotes, in any canonical form."""
    details = connection.execute(text(
        "select details from relationship_entities where id = :e"), {"e": entity_id}).scalar()
    if isinstance(details, str):
        details = json.loads(details or "{}")
    details = details or {}
    nested = details.get("canonical_repair") or {}
    nested = nested if isinstance(nested, dict) else {}

    covered = {int(x) for x in (set(details.get("source_contact_ids") or [])
                                | set(nested.get("source_contact_ids") or []))}
    records = [str(x) for x in (details.get("source_record_ids") or [])]
    if records:
        covered |= {int(x) for x in connection.execute(text(
            "select id from source_contacts where source_record_id = any(:r)"),
            {"r": records}).scalars()}
    returns = []
    for value in (details.get("drake_return_ids") or []):
        try:
            returns.append(int(value))
        except (TypeError, ValueError):
            continue
    if returns:
        covered |= {int(x) for x in connection.execute(text(
            "select id from source_contacts where source_system = 'Drake' "
            "and (raw_data->>'drake_return_id')::bigint = any(:t)"), {"t": returns}).scalars()}
    if details.get("identifier_hash"):
        covered |= {int(x) for x in connection.execute(text(
            "select id from source_contacts where source_system = 'Drake' "
            "and raw_data->>'identifier_hash' = :h"),
            {"h": str(details["identifier_hash"])}).scalars()}
    return covered


def run(manifest_path=DEFAULT_MANIFEST, *, apply_changes=False, confirm=None,
        expect_sha=APPROVED_MANIFEST_SHA256, expect_digest=APPROVED_PLAN_DIGEST,
        expect_rows=APPROVED_ROWS, output_root=REPORT_ROOT, out=print) -> dict:
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

    manifest_path = Path(manifest_path)
    rows = load_manifest(manifest_path, expect_sha=expect_sha, expect_digest=expect_digest,
                         expect_rows=expect_rows)
    digest = plan_digest(rows)
    want = confirm_phrase(len(rows))
    expected_links = sum(len(_ints(r["source_contact_ids"])) for r in rows)

    out(f"frozen manifest: {manifest_path}")
    out(f"  sha256 : {sha256_of(manifest_path)}")
    out(f"  rows   : {len(rows)}  contacts: {expected_links}")
    out(f"  digest : {digest}")

    if apply_changes and confirm != want:
        raise Abort(f"ABORT: --apply requires --confirm {want}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(output_root) / f"d7-attrib-batch1-{stamp}"
    report = {"batch": BATCH_ID, "rows": len(rows), "manifest": str(manifest_path),
              "manifest_sha256": sha256_of(manifest_path), "plan_digest": digest,
              "attributed": 0, "links_created": 0, "committed": False, "refusals": [],
              "report_dir": str(out_dir), "rollback_manifest": None,
              "rollback_manifest_sha256": None}

    connection = engine.connect()
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

        # Every frozen fact, re-read under lock, for EVERY row, before the first mutation.
        for row in rows:
            _revalidate(connection, text, row)
        out(f"  revalidated {len(rows)}/{len(rows)} rows against live state")

        requests = []
        for row in rows:
            requests.append((row, AttributionRequest(
                relationship_entity_id=int(row["proposed_relationship_entity_id"]),
                identifier=_raw_identifier(connection, text, derive, row["identifier_hash"]),
                identifier_type="ein",
                source_contact_ids=tuple(_ints(row["source_contact_ids"])),
                reason=f"{BATCH_ID} (plan {digest[:12]})")))

        if not apply_changes:
            # Prove every gate without writing: the service is the authority, so the whole batch is
            # applied together in a savepoint that is discarded — the interaction is measured, not
            # assumed.
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
            report["refusals"] = refusals
            report["links_created"] = links
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
                raise Abort(f"ABORT: dbi {row['dbi_id']} refused — [{exc.code}] {exc.message}") \
                    from exc
        report["attributed"] = len(results)
        report["links_created"] = sum(len(r.source_links_created) for _, r in results)
        out(f"  attributed {len(results)} identities, {report['links_created']} links")

        _check_write_shape(connection, text, rows, before, expected_links, AUDIT_ACTION,
                           IDENTIFIER_VERIFIED, MACHINE, EVIDENCE_METHOD)
        _check_forbidden(connection, text, before_fp)

        # --- rollback capture, written and hashed BEFORE the commit
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = _rollback_payload(connection, text, rows, results, report, digest)
        blob = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
        rollback_path = out_dir / "batch1_rollback_manifest.json"
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
    """The batch moved exactly as far as the plan said, in every direction."""
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
        "drake_business_identity": before["drake_business_identity"],          # UPDATE, not INSERT
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
        "  and d.confirmation_source = :s and d.evidence_method = :m)"),
        {"i": ids, "t": trust, "s": source, "m": method}).scalar()
    if wrong:
        raise Abort(f"ABORT: {wrong} attributed rows do not carry this service's evidence")

    mistargeted = 0
    for row in rows:
        mistargeted += connection.execute(text(
            "select count(*) from drake_business_identity where id = :i "
            "and relationship_entity_id <> :e"),
            {"i": int(row["dbi_id"]), "e": int(row["proposed_relationship_entity_id"])}).scalar()
    if mistargeted:
        raise Abort(f"ABORT: {mistargeted} rows are bound to an entity the manifest did not name")


def _check_forbidden(connection, text, before_fp) -> None:
    after_fp = _fingerprints(connection, text)
    changed = [t for t in FORBIDDEN_TABLES if before_fp[t] != after_fp[t]]
    if changed:
        raise Abort(f"ABORT: forbidden table(s) changed: {changed}")


def _jsonable(value):
    """Database values the rollback must reproduce exactly, in a form JSON can hold.

    ``match_score`` is NUMERIC and arrives as ``Decimal``; a float would round it. Rendering it as
    its own string keeps the recorded value identical to the stored one.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    return str(value)


def _rollback_payload(connection, text, rows, results, report, digest) -> dict:
    """Everything needed to reverse this batch exactly, captured inside the transaction."""
    by_id = {int(r["dbi_id"]): r for r in rows}
    payload = {"batch": BATCH_ID, "created_at": datetime.now(UTC).isoformat(),
               "manifest_sha256": report["manifest_sha256"], "plan_digest": digest, "rows": []}
    for row, result in results:
        dbi_id = int(row["dbi_id"])
        manifest_row = by_id[dbi_id]
        post = connection.execute(text(
            "select relationship_entity_id, trust_level, confirmation_source, evidence_method, "
            "updated_at from drake_business_identity where id = :i"), {"i": dbi_id}).mappings().one()
        links = [dict(r) for r in connection.execute(text(
            "select id, source_contact_id, relationship_entity_id, match_method, match_score, "
            "confirmed, trust_level, confirmation_source, evidence_method "
            "from entity_source_links where source_contact_id = any(:i) order by id"),
            {"i": _ints(manifest_row["source_contact_ids"])}).mappings()]
        payload["rows"].append({
            "dbi_id": dbi_id,
            "identifier_hash": row["identifier_hash"],
            "target_entity_id": int(row["proposed_relationship_entity_id"]),
            "source_contact_ids": _ints(manifest_row["source_contact_ids"]),
            # The pre-image is the reviewed one: every field this batch may set was NULL, proven by
            # _revalidate before any write. Restoring means putting all four back to NULL.
            "dbi_pre_image": {"relationship_entity_id": None, "trust_level": None,
                              "confirmation_source": None, "evidence_method": None},
            "dbi_post_image": {k: _jsonable(v) for k, v in post.items()},
            "created_entity_source_link_ids": [int(link["id"]) for link in links],
            "created_entity_source_links": [
                {k: _jsonable(v) for k, v in link.items()} for link in links],
            "audit_event_id": int(result.audit_event_id),
        })
    created = [i for r in payload["rows"] for i in r["created_entity_source_link_ids"]]
    if len(created) != len(set(created)):
        raise Abort("ABORT: rollback manifest captured a duplicate link id")
    if len(created) != sum(len(_ints(r["source_contact_ids"])) for r in rows):
        raise Abort(f"ABORT: rollback manifest captured {len(created)} link ids, "
                    f"expected {sum(len(_ints(r['source_contact_ids'])) for r in rows)}")
    if any(r["audit_event_id"] <= 0 for r in payload["rows"]):
        raise Abort("ABORT: rollback manifest is missing an audit event id")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="D7 machine attribution Batch 1 guarded apply")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--expect-sha", default=APPROVED_MANIFEST_SHA256)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--output-root", default=str(REPORT_ROOT))
    args = parser.parse_args()
    try:
        run(args.manifest, apply_changes=args.apply, confirm=args.confirm,
            expect_sha=args.expect_sha, output_root=args.output_root)
    except Abort as exc:
        print(exc)
        return 0 if str(exc).startswith("DRY RUN") else 2
    return 0


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())
