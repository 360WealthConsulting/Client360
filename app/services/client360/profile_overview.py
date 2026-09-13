"""The Overview profile blocks: Contact, Identity, Investments and Tax snapshot.

WHY THIS MODULE EXISTS
----------------------
The Client 360 Overview showed cross-domain *work* — tasks, documents, meetings, alerts — and no
profile at all. Nothing in ``app/templates/client360/`` referenced a phone, an email or an address,
so a client whose phone was sitting in ``people.primary_phone`` appeared to have none. That was a
missing surface, not missing data.

WHERE EACH FIELD ACTUALLY COMES FROM
------------------------------------
Client360's normalized schema carries exactly one phone and one email per person
(``people.primary_phone`` / ``primary_email``) and no phone *type* at all. Drake's import keeps the
original CSV in ``drake_client_returns.raw_data``, and that carries the three types the firm
actually uses — ``TP_Cell_Phone``, ``TP_Day_Phone``, ``TP_Eve_Phone`` — plus ``TP_DoB`` and a
mailing address. So mobile / work / home are Drake-sourced, and every row says which system it came
from rather than pretending one canonical value exists.

Nothing here is a second source of truth: this module only reads, and every row is labelled with its
origin so staff can tell a Client360 value from a Drake one.

WHAT IS DELIBERATELY ABSENT
---------------------------
*Dependents* has no store anywhere in the schema — not a table, not a column, not a Drake raw key.
The block reports that rather than inventing a number, because a confident "0 dependents" on a
client who has three is worse than an honest blank.

SCOPE AND PII
-------------
Every entry point takes a principal and verifies record scope before reading anything; an
out-of-scope caller gets empty blocks, never a partial one.

The SSN is the sharp edge. This module derives only the last four digits, and derives them IN SQL,
so the complete number never enters application memory, never reaches a template, and cannot appear
in a log line or a traceback. There is deliberately no way to obtain the rest through the
application: no reveal control, no endpoint behind one, and no script that could fetch it. The full
value stays in Postgres, where the Drake import left it.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import text

from app.db import engine
from app.security.authorization import record_in_scope

#: Drake raw-CSV keys for the phone types the firm records, in the order staff read them.
_DRAKE_PHONE_KEYS = (("mobile", "TP_Cell_Phone"), ("work", "TP_Day_Phone"), ("home", "TP_Eve_Phone"))

_DIGITS = re.compile(r"\D+")


def _table_exists(connection, name: str) -> bool:
    """Drake tables are provisioned separately and may not exist yet in every environment.

    The workspace already degrades rather than 500s when that is true (see
    ``client360._drake_returns_for_person``); these blocks follow the same rule.
    """
    return connection.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}).scalar() is not None


def normalize_phone(value) -> str | None:
    """Digits only, so ``(540) 555-0143``, ``540-555-0143`` and ``5405550143`` dedupe to one row.

    A US 11-digit number written with its country code collapses onto the 10-digit form, which is
    how the same phone reaches us from Client360 and Drake in different shapes.
    """
    if not value:
        return None
    digits = _DIGITS.sub("", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def format_phone(digits: str | None) -> str | None:
    """``5405550143`` -> ``(540) 555-0143``. Anything not ten digits is shown as stored."""
    if not digits:
        return None
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return digits


def normalize_email(value) -> str | None:
    """Lowercased and trimmed — the same rule ``people.normalized_email`` already uses."""
    if not value:
        return None
    cleaned = str(value).strip().lower()
    return cleaned or None


def _person_row(connection, person_id: int):
    return connection.execute(text("""
        SELECT id, household_id, first_name, last_name, full_name,
               primary_email, normalized_email, primary_phone, normalized_phone,
               address_line_1, address_line_2, city, state, postal_code, birth_date
        FROM people WHERE id = :id
    """), {"id": person_id}).mappings().first()


def _drake_raw_for_person(connection, person_id: int):
    """The most recent Drake row for this person, by canonical identity link where one exists.

    ``drake_identity.primary_person_id`` is the adjudicated link and is preferred. The name join is
    the fallback the workspace already uses for clients Drake has not been adjudicated for.
    """
    if not _table_exists(connection, "drake_client_returns"):
        return None
    if _table_exists(connection, "drake_identity"):
        row = connection.execute(text("""
            SELECT d.raw_data, d.tax_year
            FROM drake_identity di
            JOIN drake_client_returns d ON d.taxpayer_identifier_hash = di.identifier_hash
            WHERE di.primary_person_id = :id AND d.raw_data IS NOT NULL
            ORDER BY d.tax_year DESC, d.id DESC LIMIT 1
        """), {"id": person_id}).mappings().first()
        if row:
            return row
    return connection.execute(text("""
        SELECT d.raw_data, d.tax_year
        FROM people p
        JOIN drake_client_returns d
          ON lower(trim(d.taxpayer_first_name)) = lower(trim(p.first_name))
         AND lower(trim(d.taxpayer_last_name))  = lower(trim(p.last_name))
        WHERE p.id = :id AND d.raw_data IS NOT NULL
        ORDER BY d.tax_year DESC, d.id DESC LIMIT 1
    """), {"id": person_id}).mappings().first()


def _raw_get(raw, key):
    """One value out of Drake's raw CSV blob, whether it arrives as dict or JSON text."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw.get(key)
    try:
        import json
        return json.loads(raw).get(key)
    except (ValueError, TypeError):
        return None


# --- Contact -------------------------------------------------------------------------------------

def contact_block(person_id: int, principal) -> dict:
    """Phones, emails and the mailing address, normalized, deduplicated and attributed.

    Deduplication is by normalized value, and the FIRST source to supply a value keeps it — the
    Client360 record is listed first, so a Drake row carrying the same number is folded into it
    rather than shown twice. ``sources`` on each row names every system that supplied it, which is
    what tells staff a value is corroborated rather than merely present.
    """
    # Every key the populated shape carries, so an out-of-scope caller and a caller looking at a
    # client with nothing on file take the same code path through the template.
    empty = {"phones": [], "emails": [], "address": None, "preferred_contact_method": None,
             "household": None, "in_scope": False}
    if not record_in_scope(principal, "person", person_id):
        return empty

    with engine.connect() as connection:
        person = _person_row(connection, person_id)
        if not person:
            return empty
        drake = _drake_raw_for_person(connection, person_id)
        raw = drake["raw_data"] if drake else None

        # --- phones: Client360's single number, then Drake's typed ones
        phones: dict[str, dict] = {}

        def add_phone(value, label, source, verified):
            key = normalize_phone(value)
            if not key:
                return
            existing = phones.get(key)
            if existing:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                existing["verified"] = existing["verified"] or verified
                return
            phones[key] = {"label": label, "display": format_phone(key),
                           "sources": [source], "verified": verified, "primary": not phones}

        add_phone(person["primary_phone"], "primary", "Client360",
                  bool(person["normalized_phone"]))
        for label, raw_key in _DRAKE_PHONE_KEYS:
            add_phone(_raw_get(raw, raw_key), label, "Drake", False)

        # --- emails: Client360's primary, then any linked source contact
        emails: dict[str, dict] = {}

        def add_email(value, label, source, verified):
            key = normalize_email(value)
            if not key:
                return
            existing = emails.get(key)
            if existing:
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                existing["verified"] = existing["verified"] or verified
                return
            emails[key] = {"label": label, "display": key, "sources": [source],
                           "verified": verified, "primary": not emails,
                           "mailto": f"mailto:{key}"}

        add_email(person["primary_email"], "primary", "Client360",
                  bool(person["normalized_email"]))
        for row in connection.execute(text("""
            SELECT sc.email, sc.source_system
            FROM person_source_links psl JOIN source_contacts sc ON sc.id = psl.source_contact_id
            WHERE psl.person_id = :id AND sc.email IS NOT NULL AND sc.email <> ''
            ORDER BY sc.source_system, sc.id
        """), {"id": person_id}).mappings():
            add_email(row["email"], "additional", row["source_system"] or "Imported", False)
        add_email(_raw_get(raw, "Email"), "additional", "Drake", False)

        # --- mailing address: Client360's, else Drake's
        address = None
        if person["address_line_1"]:
            # ``people.postal_code`` is a real column and was previously discarded here, so a
            # Client360 address rendered without its ZIP while a Drake-sourced one kept it.
            address = {"lines": [x for x in (person["address_line_1"], person["address_line_2"]) if x],
                       "city": person["city"], "state": person["state"],
                       "postal_code": person["postal_code"], "source": "Client360"}
        elif _raw_get(raw, "Address"):
            # `City`/`State` are the populated pair (3,682 rows); the `Res_*` variants are a
            # residency override Drake fills on only 26, so they are the fallback, not the default.
            address = {"lines": [_raw_get(raw, "Address")],
                       "city": _raw_get(raw, "City") or _raw_get(raw, "Res_City"),
                       "state": _raw_get(raw, "State") or _raw_get(raw, "Res_State"),
                       "postal_code": _raw_get(raw, "Zip"), "source": "Drake"}

        # Preferred contact method is recorded on the PORTAL ACCOUNT, not on the person: it is a
        # statement about how this client wants the portal to reach them, and a client with no
        # portal account has never been asked. Read rather than inferred — nothing here guesses a
        # preference from which channel happens to have the most traffic.
        preferred = connection.execute(text("""
            SELECT preferred_contact_method FROM portal_accounts
            WHERE person_id = :id AND preferred_contact_method IS NOT NULL
            ORDER BY id DESC LIMIT 1
        """), {"id": person_id}).scalar()

        # Household membership, shown beside the contact details because "who else is on this
        # record" is a contact question. It is the same value identity_block returns; the Overview
        # renders it here only, so one screen does not state the same fact twice.
        household = None
        if person["household_id"]:
            row = connection.execute(text("""
                SELECT h.id, h.name, (SELECT count(*) FROM people m
                                      WHERE m.household_id = h.id AND m.active) AS member_count
                FROM households h WHERE h.id = :hid
            """), {"hid": person["household_id"]}).mappings().first()
            if row:
                household = {"id": row["id"], "name": row["name"],
                             "member_count": row["member_count"]}

    return {"phones": list(phones.values()), "emails": list(emails.values()),
            "address": address, "preferred_contact_method": preferred,
            "household": household, "in_scope": True}


# --- Identity ------------------------------------------------------------------------------------

def identity_block(person_id: int, principal) -> dict:
    """Date of birth, the SSN's last four digits, household and dependents.

    THE SSN NEVER LEAVES POSTGRES IN FULL. Drake's import keeps the original CSV row verbatim, so
    ``raw_data->>'TP_Social'`` is a real nine-digit number. The query below slices the last four
    server-side and returns only those, so the complete value is never materialised in Python, never
    reaches a template, and cannot appear in a log line or a traceback. Nothing in the application
    can obtain the remaining five digits — that is the point, and it is what the ``ssn`` tests in
    ``tests/test_client_profile_overview.py`` hold the whole app to.

    ``dependents`` is ``None``, not ``0``. No table, column or Drake key records dependents, and a
    confident zero on a client with three children is worse than an honest blank.
    """
    empty = {"birth_date": None, "birth_date_source": None, "ssn_last4": None,
             "ssn_on_file": False, "household": None, "dependents": None,
             "dependents_available": False, "in_scope": False}
    if not record_in_scope(principal, "person", person_id):
        return empty

    with engine.connect() as connection:
        person = _person_row(connection, person_id)
        if not person:
            return empty

        birth_date, birth_source = person["birth_date"], "Client360" if person["birth_date"] else None
        ssn_last4, ssn_on_file = None, False

        if _table_exists(connection, "drake_client_returns"):
            row = connection.execute(text("""
                SELECT
                  -- last four only; the full value is never selected
                  right(regexp_replace(coalesce(d.raw_data::jsonb->>'TP_Social',''), '[^0-9]', '', 'g'), 4)
                    AS ssn_last4,
                  length(regexp_replace(coalesce(d.raw_data::jsonb->>'TP_Social',''), '[^0-9]', '', 'g'))
                    AS ssn_digits,
                  d.raw_data::jsonb->>'TP_DoB' AS drake_dob
                FROM people p
                JOIN drake_client_returns d
                  ON lower(trim(d.taxpayer_first_name)) = lower(trim(p.first_name))
                 AND lower(trim(d.taxpayer_last_name))  = lower(trim(p.last_name))
                WHERE p.id = :id AND d.raw_data IS NOT NULL
                ORDER BY d.tax_year DESC, d.id DESC LIMIT 1
            """), {"id": person_id}).mappings().first()
            if row and row["ssn_digits"] == 9:
                ssn_last4, ssn_on_file = row["ssn_last4"], True
            if birth_date is None and row and row["drake_dob"]:
                parsed = _parse_drake_date(row["drake_dob"])
                # Only claim a source when a date was actually recovered; an unparseable value
                # leaves both blank rather than labelling an absent date "from Drake".
                if parsed:
                    birth_date, birth_source = parsed, "Drake"

        household = None
        if person["household_id"]:
            hh = connection.execute(text("""
                SELECT h.id, h.name, (SELECT count(*) FROM people m
                                      WHERE m.household_id = h.id AND m.active) AS member_count
                FROM households h WHERE h.id = :hid
            """), {"hid": person["household_id"]}).mappings().first()
            if hh:
                household = {"id": hh["id"], "name": hh["name"], "member_count": hh["member_count"]}

    return {"birth_date": birth_date, "birth_date_source": birth_source,
            "ssn_last4": ssn_last4, "ssn_on_file": ssn_on_file,
            "household": household, "dependents": None, "dependents_available": False,
            "in_scope": True}


def _parse_drake_date(value) -> date | None:
    """Drake writes dates of birth as eight bare digits, MMDDYYYY.

    Determined from the corpus rather than assumed: across all 3,258 populated rows the first pair
    never exceeds 12, the second never exceeds 31, and the last four span 1925-2016 — so the order
    is month, day, year and not the ISO arrangement. Slashed and ISO forms are still accepted in
    case a future export writes them, and anything else is left unparsed rather than guessed at,
    because a wrong date of birth is worse than a blank one.
    """
    if not value:
        return None
    from datetime import datetime
    raw = str(value).strip()
    digits = _DIGITS.sub("", raw)
    if len(digits) == 8 and not any(sep in raw for sep in "/-"):
        try:
            return datetime.strptime(digits, "%m%d%Y").date()
        except ValueError:
            return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# --- Investments ---------------------------------------------------------------------------------

def investments_block(person_id: int, principal) -> dict:
    """Accounts grouped by custodian, with a total.

    Grouping is generic rather than a fixed Schwab/AssetMark pair: a custodian appears when it has
    accounts and disappears when it does not, so onboarding a new one needs no code change. The
    firm currently holds Schwab data only.

    ``registration_type`` is what the schema records; there is no ``account_type`` column, so that
    is what "account type" resolves to here.
    """
    empty = {"custodians": [], "total_value": 0, "account_count": 0, "in_scope": False}
    if not record_in_scope(principal, "person", person_id):
        return empty

    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT a.custodian, a.account_name, a.registration_type, a.status,
                   a.total_value, a.cash_value, a.last_review_date
            FROM accounts a
            WHERE a.person_id = :id
               OR (a.household_id IS NOT NULL
                   AND a.household_id = (SELECT household_id FROM people WHERE id = :id))
            ORDER BY a.custodian, a.account_name
        """), {"id": person_id}).mappings().all()

    grouped: dict[str, dict] = {}
    total = 0
    for row in rows:
        name = row["custodian"] or "Unspecified custodian"
        bucket = grouped.setdefault(name, {"custodian": name, "accounts": [], "subtotal": 0})
        value = float(row["total_value"]) if row["total_value"] is not None else None
        bucket["accounts"].append({
            "name": row["account_name"], "account_type": row["registration_type"],
            "status": row["status"], "value": value,
            "value_available": value is not None,
            "last_review_date": row["last_review_date"]})
        if value:
            bucket["subtotal"] += value
            total += value

    return {"custodians": list(grouped.values()), "total_value": total,
            "account_count": len(rows), "in_scope": True}


# --- Tax snapshot --------------------------------------------------------------------------------

def tax_block(person_id: int, principal) -> dict:
    """The latest tax year: AGI, filing status, refund or balance due, and both e-file states.

    ``drake_efile_records`` is the authority for money and acknowledgement — it is the only place
    carrying ``refund_amount`` and ``balance_due`` — while ``drake_client_returns`` carries the
    filing status and the separate federal/state acknowledgement pair the firm tracks.
    """
    empty = {"tax_year": None, "agi": None, "filing_status": None,
             "refund_amount": None, "balance_due": None,
             "federal": None, "state": None, "dependents": None,
             "dependents_available": False, "in_scope": False}
    if not record_in_scope(principal, "person", person_id):
        return empty

    with engine.connect() as connection:
        if not _table_exists(connection, "drake_client_returns"):
            return {**empty, "in_scope": True}
        ret = connection.execute(text("""
            SELECT d.tax_year, d.agi, d.filing_status,
                   d.federal_ack_code, d.federal_ack_date, d.federal_product,
                   d.state_ack_code, d.state_ack_date, d.state_product,
                   d.taxpayer_identifier_hash
            FROM people p
            JOIN drake_client_returns d
              ON lower(trim(d.taxpayer_first_name)) = lower(trim(p.first_name))
             AND lower(trim(d.taxpayer_last_name))  = lower(trim(p.last_name))
            WHERE p.id = :id
            ORDER BY d.tax_year DESC, d.id DESC LIMIT 1
        """), {"id": person_id}).mappings().first()
        if not ret:
            return {**empty, "in_scope": True}

        refund = balance = None
        agi = ret["agi"]
        if _table_exists(connection, "drake_efile_records"):
            efile = connection.execute(text("""
                SELECT refund_amount, balance_due, agi
                FROM drake_efile_records
                WHERE taxpayer_identifier_hash = :h AND tax_year = :y
                ORDER BY id DESC LIMIT 1
            """), {"h": ret["taxpayer_identifier_hash"], "y": ret["tax_year"]}).mappings().first()
            if efile:
                refund, balance = efile["refund_amount"], efile["balance_due"]
                agi = agi if agi is not None else efile["agi"]

    return {"tax_year": ret["tax_year"], "agi": agi, "filing_status": ret["filing_status"],
            "refund_amount": refund, "balance_due": balance,
            "federal": _efile_state(ret["federal_ack_code"], ret["federal_ack_date"],
                                    ret["federal_product"]),
            "state": _efile_state(ret["state_ack_code"], ret["state_ack_date"],
                                  ret["state_product"]),
            "dependents": None, "dependents_available": False, "in_scope": True}


#: Drake acknowledgement codes. "A" is the accepted ack; "R"/"D" are rejections. Anything else that
#: has a product but no ack is still in flight, which is a different fact from never filed.
_ACK = {"A": "accepted", "R": "rejected", "D": "rejected"}


def _efile_state(code, ack_date, product) -> dict | None:
    """Accepted / rejected / in progress, with the acknowledgement date when there is one."""
    if not product and not code:
        return None
    status = _ACK.get((code or "").strip().upper())
    if status is None:
        status = "in_progress"
    return {"status": status, "code": (code or "").strip() or None,
            "acknowledged_at": ack_date, "product": product}


def overview_profile(person_id: int, principal) -> dict:
    """All four blocks for the Overview tab. One scope check per block, by design: a block that
    cannot be shown comes back empty rather than making the whole page fail."""
    return {"contact": contact_block(person_id, principal),
            "identity": identity_block(person_id, principal),
            "investments": investments_block(person_id, principal),
            "tax": tax_block(person_id, principal)}
