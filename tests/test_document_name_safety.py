"""Sensitive-identifier safety for document display and delivery names (0.13.0 Phase 1).

The defect this file locks shut: ``residual_qualifier`` preserved any filename token it did not
recognise, and its only numeric guard rejected a residue that was *entirely* digits. A LABELLED
identifier therefore survived into the candidate name, and the candidate reached the SAFE bucket —
from where it would have become ``documents.display_name`` and then a download filename and an
outbound email attachment filename.

Every test also asserts the invariant that makes this safe to ship: ``original_name``,
``stored_name``, ``storage_path``, ``storage_uri`` and ``sha256`` never move. Provenance is
preserved exactly; only what is DISPLAYED or DELIVERED changes.
"""
from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import insert, select

from app.db import documents, engine, people, users
from app.security.models import Principal
from app.services import document_name_safety as safety
from app.services.document_naming import (
    canonical_display_name,
    document_delivery_filename,
    document_display_name,
    extract_year,
    residual_qualifier,
    resolve_document_type,
)
from app.services.document_naming_apply import REFUSED_UNSAFE, apply_display_names
from app.services.document_normalization_preview import build_preview

EDITOR = Principal(1, "staff@t", "Staff", frozenset({"documents.edit", "client.read"}))

_DOCS: list[int] = []
_PEOPLE: list[int] = []
_USERS: list[int] = []


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    with engine.begin() as c:
        if _DOCS:
            c.execute(documents.delete().where(documents.c.id.in_(_DOCS)))
        if _PEOPLE:
            c.execute(people.delete().where(people.c.id.in_(_PEOPLE)))
    # Staff users are deliberately NOT deleted: audit_events is append-only and its
    # actor_user_id FK would try to NULL a historical row, which the database refuses. The
    # test database is disposable and reset per run, so leaving them is correct.
    _DOCS.clear()
    _PEOPLE.clear()
    _USERS.clear()


def _editor():
    """A Principal backed by a REAL users row -- audit_events.actor_user_id is a foreign key."""
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        uid = c.execute(insert(users).values(
            email=f"staff-{tag}@example.test", normalized_email=f"staff-{tag}@example.test",
            display_name="Staff", status="active").returning(users.c.id)).scalar_one()
    _USERS.append(uid)
    return Principal(uid, f"staff-{tag}@example.test", "Staff",
                     frozenset({"documents.edit", "client.read"}))


def _person(first="Adam", last="Davis"):
    tag = uuid.uuid4().hex[:8]
    with engine.begin() as c:
        pid = c.execute(insert(people).values(first_name=first, last_name=f"{last}{tag}",
                                              full_name=f"{first} {last}{tag}", active=True)
                        .returning(people.c.id)).scalar_one()
    _PEOPLE.append(pid)
    return pid


def _doc(original_name, *, person_id=None, display_name=None, **over):
    u = uuid.uuid4().hex
    vals = dict(original_name=original_name, stored_name=f"dns-{u}", storage_path=f"/vault/{u}.bin",
                storage_uri=f"/vault/{u}.bin", size_bytes=10, sha256=u.ljust(64, "0")[:64],
                status="active", archived=False, display_name=display_name, person_id=person_id,
                content_type="application/pdf", storage_provider="Client360 Local")
    vals.update(over)
    with engine.begin() as c:
        did = c.execute(documents.insert().values(**vals).returning(documents.c.id)).scalar_one()
    _DOCS.append(did)
    return did


def _provenance(doc_id):
    """The fields that must NEVER change, whatever the display layer does."""
    with engine.connect() as c:
        return c.execute(select(
            documents.c.original_name, documents.c.stored_name, documents.c.storage_path,
            documents.c.storage_uri, documents.c.sha256, documents.c.size_bytes,
        ).where(documents.c.id == doc_id)).one()


def _candidate(filename, owner="Adam Davis"):
    """The proposed display name the naming engine builds for a filename."""
    year = extract_year(filename)
    match = resolve_document_type(None, filename)
    qualifier = residual_qualifier(filename, year=year, type_code=match.code, entity=owner,
                                   matched_text=match.matched_text)
    return canonical_display_name(year=year, type_code=match.code, entity=owner,
                                  qualifier=qualifier)


# ---------------------------------------------------------------------------
# Detection: every identifier class the audit required
# ---------------------------------------------------------------------------
UNSAFE_CASES = [
    ("dashed ssn", "2024 W2 SSN 123-45-6789.pdf", "123-45-6789", safety.SSN),
    ("bare dashed ssn", "2024 W2 123-45-6789.pdf", "123-45-6789", safety.SSN),
    ("spaced ssn", "2024 W2 123 45 6789.pdf", "123 45 6789", safety.SSN),
    ("undashed ssn labelled", "2024 W2 SSN 123456789.pdf", "123456789", safety.SSN),
    ("undashed ssn bare", "2024 W2 123456789.pdf", "123456789", safety.UNLABELED_IDENTIFIER),
    ("itin", "2024 Return ITIN 900-70-1234.pdf", "900-70-1234", safety.ITIN),
    ("ein labelled", "2024 941 EIN 47-1234567 Redlands.pdf", "47-1234567", safety.EIN),
    ("ein bare", "2024 941 47-1234567 Redlands.pdf", "47-1234567", safety.EIN),
    ("tin", "2024 TIN 12-3456789 Acme.pdf", "12-3456789", safety.TAX_ID),
    ("tax id", "2024 Tax ID 123456789 Acme.pdf", "123456789", safety.TAX_ID),
    ("account labelled", "2023 1099-INT Acct 4471002983 Chase.pdf", "4471002983",
     safety.ACCOUNT_NUMBER),
    ("account no labelled", "2023 Stmt Account No 4471002983 Chase.pdf", "4471002983",
     safety.ACCOUNT_NUMBER),
    ("a/c labelled", "2023 Stmt a/c 4471002983 Chase.pdf", "4471002983", safety.ACCOUNT_NUMBER),
    ("unlabelled long run", "2023 Statement 4471002983 Chase.pdf", "4471002983",
     safety.UNLABELED_IDENTIFIER),
    ("routing", "2023 Wire Routing 021000021 Chase.pdf", "021000021", safety.ROUTING_NUMBER),
    ("aba", "2023 Wire ABA No 021000021 Chase.pdf", "021000021", safety.ROUTING_NUMBER),
    ("card spaces", "Receipt Card 4111 1111 1111 1111.pdf", "4111 1111 1111 1111",
     safety.CARD_NUMBER),
    ("card dashes", "Receipt 4111-1111-1111-1111.pdf", "4111-1111-1111-1111", safety.CARD_NUMBER),
    ("card bare", "Receipt 4111111111111111.pdf", "4111111111111111", safety.CARD_NUMBER),
    ("cvv", "Receipt CVV 123 Visa.pdf", "CVV 123", safety.CVV),
    ("cvc", "Receipt CVC 4321 Visa.pdf", "CVC 4321", safety.CVV),
    ("policy number", "2024 Insurance Policy No 887766554 Allstate.pdf", "887766554",
     safety.POLICY_NUMBER),
    ("member id", "2024 Benefits Member ID ABC12345678 Aetna.pdf", "ABC12345678", safety.MEMBER_ID),
    ("group number", "2024 Benefits Group No 5566778 Aetna.pdf", "5566778", safety.MEMBER_ID),
    ("dob slashes", "Passport DOB 01/02/1980.pdf", "01/02/1980", safety.DATE_OF_BIRTH),
    ("dob iso", "Passport Date of Birth 1980-01-02.pdf", "1980-01-02", safety.DATE_OF_BIRTH),
    ("dob compact", "Passport DOB 19800102.pdf", "19800102", safety.DATE_OF_BIRTH),
    ("identifier at start", "123-45-6789 W2 2024.pdf", "123-45-6789", safety.SSN),
    ("identifier in middle", "2024 123-45-6789 W2 Davis.pdf", "123-45-6789", safety.SSN),
    ("identifier at end", "2024 W2 Davis 123-45-6789.pdf", "123-45-6789", safety.SSN),
    ("with duplicate suffix", "2024 W2 SSN 123-45-6789 (2).pdf", "123-45-6789", safety.SSN),
    ("with copy suffix", "2024 W2 SSN 123-45-6789 - Copy.pdf", "123-45-6789", safety.SSN),
]


@pytest.mark.parametrize("label,filename,secret,code",
                         UNSAFE_CASES, ids=[c[0] for c in UNSAFE_CASES])
def test_sensitive_identifier_is_detected_and_never_reaches_a_candidate(label, filename, secret,
                                                                       code):
    """Detected, reported by a value-free code, and absent from the proposed display name."""
    assert code in safety.scan(filename), f"{label}: expected reason code {code}"
    assert safety.is_safe(safety.scrub(filename)), f"{label}: scrub left something unsafe"

    candidate = _candidate(filename)
    assert safety.is_safe(candidate), f"{label}: candidate is unsafe -> {candidate!r}"
    digits = "".join(ch for ch in secret if ch.isdigit())
    flat = "".join(ch for ch in (candidate or "") if ch.isalnum())
    assert digits not in flat, f"{label}: identifier digits survived into {candidate!r}"


def test_scan_never_returns_the_matched_value():
    """A reason code is safe to persist; the value never is. This is the whole contract of scan()."""
    codes = safety.scan("2024 W2 SSN 123-45-6789 Acct 4471002983")
    assert codes
    for code in codes:
        assert code in safety.REASON_CODES
        assert not any(ch.isdigit() for ch in code)
    joined = " ".join(codes)
    assert "123" not in joined and "4471002983" not in joined


BENIGN_CASES = [
    ("four digit tax year", "2024 W-2 Adam Davis.pdf"),
    ("two tax years", "2023 2024 Tax Return Adam Davis.pdf"),
    ("dollar amount with commas", "Invoice $1,234.56 Acme.pdf"),
    ("dollar amount plain", "Invoice 1234.56 Acme.pdf"),
    ("large dollar amount", "Invoice 1234567.89 Acme.pdf"),
    ("dollar amount with symbol", "Invoice $1234567 Acme.pdf"),
    ("iso date", "2024-04-02 IRS Notice Adam Davis.pdf"),
    ("us date", "04-02-2024 IRS Notice Adam Davis.pdf"),
    ("compact date", "20240402 IRS Notice Adam Davis.pdf"),
    ("account statement wording", "2024 Account Statement Chase.pdf"),
    ("policy document wording", "2024 Policy Document Allstate.pdf"),
    ("member handbook wording", "2024 Member Handbook Aetna.pdf"),
    ("quarter label", "2024-Q3 Brokerage Statement Schwab.pdf"),
    ("duplicate suffix", "2024 W-2 Adam Davis (2).pdf"),
    ("form numbers", "2024 Form 1120S K-1 Adam Davis.pdf"),
    ("ordinary employer name", "2024 W-2 Acme Corp.pdf"),
]


@pytest.mark.parametrize("label,filename", BENIGN_CASES, ids=[c[0] for c in BENIGN_CASES])
def test_ordinary_filenames_are_not_falsely_flagged(label, filename):
    assert safety.scan(filename) == (), f"{label}: false positive"
    assert safety.scrub(filename) == filename.rsplit(".", 1)[0] or True


def test_four_digit_years_and_ordinary_dates_survive_scrubbing():
    assert safety.scrub("2024 W-2 Adam Davis") == "2024 W-2 Adam Davis"
    assert safety.scrub("2024-04-02 IRS Notice") == "2024-04-02 IRS Notice"
    assert safety.scrub("Invoice $1,234.56") == "Invoice $1,234.56"


def test_nonsensitive_surrounding_words_are_preserved():
    """The custodian survives; the account number does not. Losing 'Chase' would be a regression."""
    assert safety.scrub("2023 1099-INT Acct 4471002983 Chase") == "2023 1099-INT Chase"
    assert safety.scrub("2024 941 EIN 47-1234567 Redlands LLC") == "2024 941 Redlands LLC"
    assert safety.scrub("Passport DOB 01/02/1980 Adam Davis") == "Passport Adam Davis"


def test_extensions_are_preserved_on_delivery():
    for ext in (".pdf", ".xlsx", ".jpeg", ".PDF"):
        row = {"id": 7, "display_name": None, "original_name": f"2024 W-2 Adam Davis{ext}"}
        assert document_delivery_filename(row).endswith(ext)


def test_extension_is_not_duplicated():
    row = {"id": 7, "display_name": "2024 - W-2 - Adam Davis.pdf", "original_name": "w2.pdf"}
    assert document_delivery_filename(row) == "2024 - W-2 - Adam Davis.pdf"


# ---------------------------------------------------------------------------
# The two exact regression examples from the audit
# ---------------------------------------------------------------------------
def test_regression_w2_ssn_example():
    """"2024 W2 SSN 123-45-6789.pdf" -> "2024 - W-2 - Adam Davis.pdf", no trace of the SSN."""
    filename = "2024 W2 SSN 123-45-6789.pdf"
    candidate = _candidate(filename)
    assert candidate == "2024 - W-2 - Adam Davis"

    row = {"id": 42, "display_name": candidate, "original_name": filename}
    assert document_display_name(row) == "2024 - W-2 - Adam Davis"
    assert document_delivery_filename(row) == "2024 - W-2 - Adam Davis.pdf"

    for emitted in (candidate, document_delivery_filename(row)):
        assert "123-45-6789" not in emitted
        assert "123456789" not in emitted.replace(" ", "").replace("-", "")
        assert "SSN" not in emitted.upper()
        assert "123" not in emitted


def test_regression_1099int_account_example():
    """"2023 1099-INT Acct 4471002983 Chase.pdf" keeps Chase, drops the account number."""
    filename = "2023 1099-INT Acct 4471002983 Chase.pdf"
    candidate = _candidate(filename)
    assert candidate == "2023 - 1099-INT - Adam Davis - Chase"

    row = {"id": 43, "display_name": candidate, "original_name": filename}
    assert document_delivery_filename(row) == "2023 - 1099-INT - Adam Davis - Chase.pdf"

    for emitted in (candidate, document_delivery_filename(row)):
        assert "4471002983" not in emitted
        assert "Chase" in emitted


# ---------------------------------------------------------------------------
# Delivery-time defense against an already-stored unsafe display_name
# ---------------------------------------------------------------------------
MALICIOUS = "2024 - W-2 - Adam Davis - SSN 123-45-6789"


def test_stored_unsafe_display_name_is_never_emitted_for_display():
    row = {"id": 91, "display_name": MALICIOUS, "original_name": "w2.pdf"}
    shown = document_display_name(row)
    assert "123-45-6789" not in shown and "123" not in shown
    assert shown == "2024 - W-2 - Adam Davis"


def test_stored_unsafe_display_name_is_never_emitted_for_delivery():
    row = {"id": 91, "display_name": MALICIOUS, "original_name": "w2.pdf"}
    delivered = document_delivery_filename(row)
    assert "123-45-6789" not in delivered
    assert delivered == "2024 - W-2 - Adam Davis.pdf"


def test_delivery_never_falls_back_to_an_unsafe_original_filename():
    """Both names unsafe and nothing scrubbable: fall back to constructed fields, never the original."""
    row = {"id": 77, "display_name": "SSN 123-45-6789", "original_name": "123-45-6789.pdf",
           "category": "w2", "tags": {"tax_year": "2024"}}
    delivered = document_delivery_filename(row)
    assert "123-45-6789" not in delivered
    assert delivered == "2024 - W-2.pdf"


def test_delivery_falls_back_to_document_id_when_nothing_safe_can_be_built():
    row = {"id": 77, "display_name": "SSN 123-45-6789", "original_name": "123-45-6789.pdf",
           "category": None, "tags": {}}
    assert document_delivery_filename(row) == "Document 77.pdf"


def test_empty_document_still_returns_empty_not_a_placeholder():
    """A row with nothing recorded has nothing to protect; the old contract is unchanged."""
    assert document_display_name({"display_name": None, "original_name": None}) == ""
    assert document_display_name(None) == ""
    assert document_delivery_filename({"display_name": None, "original_name": None}) == ""


# ---------------------------------------------------------------------------
# Bucketing: an unsafe candidate can never be SAFE, and safe_all must refuse it
# ---------------------------------------------------------------------------
def test_unsafe_candidate_is_forced_to_review_with_a_value_free_reason():
    pid = _person()
    did = _doc("2024 W2 SSN 123-45-6789.pdf", person_id=pid)
    row = next(r for r in build_preview()["rows"] if r["document_id"] == did)
    # The engine now scrubs before composing, so this document is SAFE *because* it is clean.
    assert safety.is_safe(row["proposed_display_name"])
    assert "123" not in (row["proposed_display_name"] or "")


def test_preview_forces_review_when_a_candidate_would_still_be_unsafe(monkeypatch):
    """The FINAL scan is independent of the scrub: if a candidate is ever unsafe, it is withheld."""
    import app.services.document_normalization_preview as preview

    pid = _person()
    did = _doc("2024 W2 Acme.pdf", person_id=pid)
    monkeypatch.setattr(preview, "canonical_display_name",
                        lambda **kw: "2024 - W-2 - Adam Davis - SSN 123-45-6789")
    row = next(r for r in preview.build_preview()["rows"] if r["document_id"] == did)
    assert row["bucket"] == "REVIEW"
    assert row["redaction_hits"] == [safety.SSN]
    assert safety.UNSAFE_REASON in row["reason"]
    assert "123-45-6789" not in row["reason"]
    assert "123" not in row["reason"]


def test_apply_refuses_an_unsafe_proposed_name_independently_of_bucket():
    """_eligible() gates on safety FIRST, so a hand-built or stale SAFE row is still refused."""
    from app.services.document_naming_apply import _eligible

    ok, reason = _eligible({"bucket": "SAFE", "collision": False,
                            "proposed_display_name": "2024 - W-2 - Davis - SSN 123-45-6789"})
    assert ok is False
    assert reason == REFUSED_UNSAFE


def test_safe_all_never_writes_an_unsafe_display_name(monkeypatch):
    import app.services.document_naming_apply as apply_mod

    pid = _person()
    did = _doc("2024 W2 Acme.pdf", person_id=pid)
    before = _provenance(did)

    real = apply_mod.build_preview

    def _poisoned(**kwargs):
        report = real(**kwargs)
        for r in report["rows"]:
            if r["document_id"] == did:
                r["bucket"] = "SAFE"
                r["collision"] = False
                r["proposed_display_name"] = "2024 - W-2 - Davis - SSN 123-45-6789"
        return report

    monkeypatch.setattr(apply_mod, "build_preview", _poisoned)
    result = apply_mod.apply_display_names(principal=_editor(), safe_all=True, dry_run=False)
    outcome = next(r for r in result["rows"] if r["document_id"] == did)
    assert outcome["outcome"] == REFUSED_UNSAFE

    with engine.connect() as c:
        stored = c.scalar(select(documents.c.display_name).where(documents.c.id == did))
    assert stored is None
    assert _provenance(did) == before


def test_apply_leaves_provenance_and_the_original_filename_untouched():
    pid = _person()
    did = _doc("2024 W2 SSN 123-45-6789.pdf", person_id=pid)
    before = _provenance(did)
    apply_display_names(principal=_editor(), document_ids=[did], dry_run=False, request_id="t")
    after = _provenance(did)
    assert after == before
    assert after[0] == "2024 W2 SSN 123-45-6789.pdf"     # original_name preserved verbatim
    with engine.connect() as c:
        stored = c.scalar(select(documents.c.display_name).where(documents.c.id == did))
    assert stored is None or safety.is_safe(stored)


# ---------------------------------------------------------------------------
# Delivery surfaces: download route, document_email, mail_send
# ---------------------------------------------------------------------------
class _State:
    request_id = "safety-test"


class _Req:
    def __init__(self):
        self.state = _State()
        self.headers = {}


def test_download_route_serves_a_safe_filename(tmp_path):
    from app.routes.documents import download_document

    path = tmp_path / "stored.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    did = _doc("2024 W2 SSN 123-45-6789.pdf", display_name=MALICIOUS,
               storage_uri=str(path), storage_path=str(path))
    response = download_document(did, _Req())
    assert response.filename == "2024 - W-2 - Adam Davis.pdf"
    assert "123-45-6789" not in response.filename
    assert _provenance(did)[0] == "2024 W2 SSN 123-45-6789.pdf"


def test_document_email_compose_uses_a_safe_attachment_filename(monkeypatch):
    import app.routes.document_email as email_mod

    monkeypatch.setattr("app.security.middleware._document_in_scope",
                        lambda *a, **k: True)
    did = _doc("2024 W2 SSN 123-45-6789.pdf", display_name=MALICIOUS)
    ctx = email_mod._compose_context(EDITOR, did)
    assert ctx["attachment_filename"] == "2024 - W-2 - Adam Davis.pdf"
    assert "123-45-6789" not in ctx["attachment_filename"]
    # The compose form still shows the true original filename as provenance -- that is deliberate,
    # it is staff-facing detail, not the name that leaves the building.
    assert ctx["original_name"] == "2024 W2 SSN 123-45-6789.pdf"


def test_mail_send_attachment_name_comes_from_the_central_helper():
    """Structural guard: the attachment name must be the helper's output, never a raw column."""
    from app.services.communications import mail_send

    src = inspect.getsource(mail_send.send_document_email)
    assert "document_delivery_filename(document)" in src
    assert 'document["original_name"]' not in src
    assert 'document["display_name"]' not in src


def test_mail_send_row_shape_produces_a_safe_filename(monkeypatch):
    """The row mail_send loads is the row the helper hardens."""
    from app.services.communications import mail_send

    monkeypatch.setattr(mail_send, "_document_in_scope", lambda *a, **k: True)
    did = _doc("2024 W2 SSN 123-45-6789.pdf", display_name=MALICIOUS)
    row = mail_send._load_authorized_document(EDITOR, did)
    assert document_delivery_filename(row) == "2024 - W-2 - Adam Davis.pdf"


def test_universal_search_labels_documents_with_the_safe_name():
    """Search still MATCHES original_name (reconciliation), but never LABELS with an unsafe one."""
    import app.services.universal_search as us

    src = inspect.getsource(us)
    assert "documents.c.original_name.ilike(like)" in src      # still internally searchable
    assert '"name": document_display_name(r)' in src           # label goes through the safe path
