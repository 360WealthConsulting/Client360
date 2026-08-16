"""New-entity DETECTION and PROPOSAL for the document pipeline.

When the pipeline extracts a coherent identity that does NOT match any existing Client360 entity, this
produces a REVIEW PROPOSAL (person / household / organization). It NEVER creates a person, household,
organization, relationship, or client automatically — creation happens only when an administrator
explicitly approves, through the canonical creation services, with a full audit trail. Rejections are
retained so the same document does not keep re-proposing the same entity.

Reuse: the existing extraction/OCR/matching engine (document_owner_proposal + document_high_validation).
A document is a new-entity candidate ONLY when its owner proposal is NO_MATCH (existing-entity matching
was exhausted and found nothing) AND a coherent, corroborated identity is present. Weak/ambiguous
identities (bare surname, two unrelated names, a name with no corroboration) produce NO proposal.

Persistence: decisions (approved / rejected / assigned_existing) are stored as a versioned
``document_facts`` fact (``new_entity_proposal``) — the same mechanism the owner-proposal pipeline uses,
so NO migration is required. Pending proposals are computed live (never persisted) and exclude any
document that already has a decision, so repeated processing creates no duplicate proposals or entities.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, select

from app.db import document_facts, engine, households, people
from app.security.audit import write_audit_event
from app.services.document_high_validation import _doc_meta, _unassigned_ids
from app.services.document_owner_proposal import (
    _EMAIL_RE,
    _INST_KW,
    _PHONE_RE,
    _SSN_RE,
    _STREET_RE,
    _ZIP_RE,
    _norm,
    _phone10,
    _valid_nanp,
    build_match_indexes,
    propose_document_owner,
)

_FACT = "new_entity_proposal"
_DECIDED = ("approved", "rejected", "assigned_existing")
ENTITY_TYPES = ("person", "household", "organization")

# Tax/document labels that introduce the document's subject person (case-insensitive; PDF text is often
# lowercase). Deliberately excludes generic words like "for"/"to" that would capture boilerplate.
# Intra-name whitespace is [ \t] (NOT \s) so a captured name never bleeds across a newline into the
# next labelled line (e.g. "Taxpayer: John Doe\nSpouse: Jane Doe" yields two separate names).
_LABEL_NAME_RE = re.compile(
    r"\b(?:dear|recipient(?:'s)?(?:[ \t]+name)?|taxpayer(?:[ \t]+name)?|employee(?:[ \t]+name)?|"
    r"policyholder|insured|spouse|primary[ \t]+applicant|applicant|covered[ \t]+individual)"
    r"[ \t]*[:,]?[ \t]+([A-Za-z][A-Za-z.'\-]+(?:[ \t]+[A-Za-z][A-Za-z.'\-]+){1,2})", re.IGNORECASE)
_CONJUNCTIONS = {"and", "or", "the", "of", "a", "an", "for", "to"}
# A business name = 1-4 consecutive Capitalised/UPPER tokens immediately before a legal suffix; requiring
# each token to be capitalised stops the match from swallowing leading lowercase words ("invoice from …").
_BUSINESS_RE = re.compile(
    r"\b((?:[A-Z][A-Za-z0-9&.'\-]*[ \t]+){1,4}(?:LLC|L\.L\.C\.|Inc\.?|Incorporated|Corp\.?|Corporation|"
    r"Company|Co\.?|LLP|PLLC|P\.C\.|PC|Ltd\.?))\b")
_ORG_SUFFIX_TOKENS = {"llc", "inc", "co", "company", "corp", "corporation", "ltd", "llp", "pc", "pllc",
                      "incorporated", "l", "l c"}
# Common salutation/boilerplate words that must never become a person name.
_NAME_STOPWORDS = {"valued", "customer", "sir", "madam", "member", "client", "account", "holder",
                   "resident", "occupant", "current", "policy", "friend", "team", "name"}


def _titlecase(name):
    return " ".join(w[:1].upper() + w[1:] for w in name.split())


def _is_institution(name, idx):
    n = _norm(name)
    return n in idx["inst"] or any(k in n for k in _INST_KW)


def _extract_identity(text):
    emails = sorted({m.group(0).lower() for m in _EMAIL_RE.finditer(text)})
    phones = sorted({p for p in (_phone10(m.group(0)) for m in _PHONE_RE.finditer(text))
                     if p and _valid_nanp(p)})
    zips = sorted({m.group(1) for m in _ZIP_RE.finditer(text)})
    streets = sorted({re.sub(r"\s+", " ", m.group(0).strip()) for m in _STREET_RE.finditer(text)})
    ssn_last4 = sorted({m.group(1) for m in _SSN_RE.finditer(text)})
    names, seen = [], set()
    for m in _LABEL_NAME_RE.finditer(text):
        parts = re.sub(r"\s+", " ", m.group(1).strip()).split()
        cut = next((i for i, t in enumerate(parts) if t.lower() in _CONJUNCTIONS), len(parts))
        parts = parts[:cut]                            # stop at a conjunction (drop "... and Jane")
        disp = _titlecase(" ".join(parts))
        k = _norm(disp)
        toks = k.split()
        if (len(toks) >= 2 and not all(len(t) == 1 for t in toks)
                and not (set(toks) & _NAME_STOPWORDS) and k not in seen):
            seen.add(k)
            names.append(disp)
    businesses, bseen = [], set()
    for m in _BUSINESS_RE.finditer(text):
        disp = re.sub(r"\s+", " ", m.group(1).strip())
        k = _norm(disp)
        if k and k not in bseen:
            bseen.add(k)
            businesses.append(disp)
    return {"emails": emails, "phones": phones, "zips": zips, "streets": streets,
            "ssn_last4": ssn_last4, "names": names, "businesses": businesses}


def _surname_candidates(surname, idx, limit=8):
    out, seen = [], set()
    for (_first, last), pids in idx["first_last"].items():
        if last == surname:
            for pid in pids:
                if pid not in seen:
                    seen.add(pid)
                    out.append({"type": "person", "id": pid,
                                "name": idx["pid"].get(pid, {}).get("name")})
    return out[:limit]


def _biz_candidates(name, idx, limit=8):
    toks = set(_norm(name).split()) - _ORG_SUFFIX_TOKENS
    out = []
    for bnorm, (bid, bname) in idx["biz"].items():
        if toks & set(bnorm.split()):
            out.append({"type": "organization", "id": bid, "name": bname})
    return out[:limit]


def _evidence_classes(ident):
    classes = []
    if ident["names"]:
        classes.append("name")
    if ident["businesses"]:
        classes.append("business_name")
    if ident["emails"]:
        classes.append("email")
    if ident["phones"]:
        classes.append("phone")
    if ident["zips"] or ident["streets"]:
        classes.append("address")
    return classes


def _detect_one(conn, did, idx, *, ocr=False):
    """Live new-entity proposal for ONE document, or None. READ-ONLY. Reuses the owner-proposal engine;
    only NO_MATCH documents (existing matching exhausted) with a coherent, corroborated identity qualify."""
    proposal = propose_document_owner(did, conn=conn, idx=idx, with_text=True, ocr=ocr)
    if not proposal.get("eligible"):
        return None
    text = proposal.pop("text", "")
    if proposal.get("confidence") in ("HIGH", "MEDIUM", "AMBIGUOUS"):
        return None                                    # matched (or ambiguous with) an EXISTING entity
    ident = _extract_identity(text)
    businesses = [b for b in ident["businesses"] if not _is_institution(b, idx)]
    ident["businesses"] = businesses
    names = ident["names"]
    has_addr = bool(ident["zips"] or ident["streets"])

    entity_type = primary = None
    members = None
    if len(names) >= 2:
        surnames = {_norm(n).split()[-1] for n in names}
        if len(surnames) == 1:                         # two people, shared surname -> household
            entity_type = "household"
            primary = f"{_titlecase(next(iter(surnames)))} Household"
            members = names[:4]
        else:
            return None                                # ambiguous multiple identities -> no proposal
    elif businesses and not names:
        entity_type, primary = "organization", businesses[0]
    elif names and (ident["emails"] or ident["phones"] or has_addr):
        entity_type, primary = "person", names[0]
    else:
        return None                                    # weak / uncorroborated -> no proposal

    if entity_type == "organization":
        candidates = _biz_candidates(primary, idx)
    else:
        surname = _norm(names[0]).split()[-1]
        candidates = _surname_candidates(surname, idx)

    folder = proposal.get("source_folder")
    source_system, source_path = _doc_meta(conn, did, folder)
    evidence = {
        "names": names[:6], "business_name": (businesses[0] if businesses else None),
        "address": (ident["streets"][:2] + ident["zips"][:2]),
        "emails": ident["emails"][:6], "phones": [f"...{p[-4:]}" for p in ident["phones"][:6]],
        "tax_last4": [f"***-**-{d}" for d in ident["ssn_last4"][:4]],
        "source_system": source_system, "source_path": source_path, "document_id": did,
        "extraction_method": proposal.get("extraction_method"),
        "confidence": "NEW_CLIENT_CANDIDATE", "evidence_classes": _evidence_classes(ident),
    }
    return {"document_id": did, "filename": proposal.get("filename"), "entity_type": entity_type,
            "primary_name": primary, "members": members, "evidence": evidence, "candidates": candidates,
            "extraction_method": proposal.get("extraction_method"),
            "source_system": source_system, "source_path": source_path,
            # raw fields needed to build the entity on approval (not shown in the report)
            "_emails": ident["emails"], "_phones": ident["phones"],
            "_zips": ident["zips"], "_streets": ident["streets"]}


def _decided_doc_ids(conn):
    decided = set()
    for did, val in conn.execute(select(document_facts.c.document_id, document_facts.c.fact_value)
                                 .where(and_(document_facts.c.fact_type == _FACT,
                                             document_facts.c.is_current.is_(True)))):
        try:
            if json.loads(val).get("status") in _DECIDED:
                decided.add(did)
        except (ValueError, TypeError):
            continue
    return decided


def detect_new_entity_candidates(*, limit=None, ocr=False):
    """READ-ONLY list of pending new-entity proposals over unassigned documents, excluding any document
    that already has a decision (approved/rejected/assigned_existing). Creates nothing."""
    out = []
    with engine.connect() as conn:
        ids = _unassigned_ids(conn, limit=limit)
        decided = _decided_doc_ids(conn)
        idx = build_match_indexes(conn)
        for did in ids:
            if did in decided:
                continue
            prop = _detect_one(conn, did, idx, ocr=ocr)
            if prop is not None:
                out.append(prop)
    return out


def _public(prop):
    """Strip the private raw-field keys before the proposal is shown/serialised."""
    return {k: v for k, v in prop.items() if not k.startswith("_")}


# --- decision persistence -------------------------------------------------------------------------

def _write_decision_fact(conn, did, payload):
    prev = conn.execute(select(document_facts.c.version).where(and_(
        document_facts.c.document_id == did, document_facts.c.fact_type == _FACT,
        document_facts.c.is_current.is_(True))).order_by(document_facts.c.version.desc())
        .limit(1)).scalar() or 0
    conn.execute(document_facts.update().where(and_(
        document_facts.c.document_id == did, document_facts.c.fact_type == _FACT,
        document_facts.c.is_current.is_(True))).values(is_current=False))
    conn.execute(document_facts.insert().values(
        document_id=did, fact_type=_FACT, fact_value=json.dumps(payload), confidence=0.0,
        extraction_engine="entity_proposal", extractor_version="entity-v1",
        extracted_at=datetime.now(UTC), version=prev + 1, is_current=True))


def _rid(request_id):
    return request_id or f"entity-proposal-{uuid.uuid4()}"


# --- canonical entity creation (used ONLY on explicit approval) -----------------------------------

def _create_person(prop, principal, request_id):
    name = prop["primary_name"]
    toks = name.split()
    email = (prop["_emails"] or [None])[0]
    phone = (prop["_phones"] or [None])[0]
    with engine.begin() as c:
        pid = c.execute(people.insert().values(
            full_name=name, first_name=toks[0], last_name=(toks[-1] if len(toks) > 1 else None),
            primary_email=email, normalized_email=(email.lower() if email else None),
            primary_phone=phone, active=True, created_by_user_id=principal.user_id
        ).returning(people.c.id)).scalar_one()
        from app.services.events import publisher
        publisher.publish_safe("people.person_created", {"person_id": pid}, conn=c,
                               producer="document.entity_proposal", subject_ref=f"person:{pid}")
    return "person", pid, name


def _create_household(prop, principal, request_id):
    name = prop["primary_name"]
    with engine.begin() as c:
        hid = c.execute(households.insert().values(name=name).returning(households.c.id)).scalar_one()
        from app.services.events import publisher
        publisher.publish_safe("households.household_created", {"household_id": hid}, conn=c,
                               producer="document.entity_proposal", subject_ref=f"household:{hid}")
    return "household", hid, name


def _create_organization(prop, principal, request_id):
    from app.services.organization_service import create_organization
    address = {"street": (prop["_streets"] or [None])[0], "zip": (prop["_zips"] or [None])[0]}
    org = create_organization(principal, name=prop["primary_name"], entity_type="business",
                              address=address, request_id=_rid(request_id))
    return "organization", org["organization_id"], org["name"]


_CREATORS = {"person": _create_person, "household": _create_household,
             "organization": _create_organization}


def approve_proposal(document_id, entity_type, *, principal, request_id=None):
    """Create exactly ONE canonical entity from the (live re-derived) proposal, assign the source document
    to it, and record the decision + a full audit event. Idempotent: a document already decided is not
    re-created. Never creates more than one entity; never touches other documents."""
    with engine.connect() as conn:
        if document_id in _decided_doc_ids(conn):
            return {"ok": False, "reason": "already_decided"}
        idx = build_match_indexes(conn)
        prop = _detect_one(conn, document_id, idx)
    if prop is None or prop["entity_type"] != entity_type:
        return {"ok": False, "reason": "not_a_current_proposal"}

    etype, eid, ename = _CREATORS[entity_type](prop, principal, request_id)

    # Assign the evidencing document to the new entity (atomic all-NULL recheck; audited by the service).
    from app.services.households import resolve_document_ownership
    col = {"person": "person_id", "household": "household_id", "organization": "organization_id"}[etype]
    try:
        resolve_document_ownership(document_id, actor_user_id=principal.user_id,
                                   request_id=_rid(request_id), **{col: eid})
    except Exception:  # noqa: BLE001 — entity creation stands even if the doc is no longer assignable
        pass

    with engine.begin() as conn:
        _write_decision_fact(conn, document_id, {
            "status": "approved", "entity_type": entity_type, "created_entity_type": etype,
            "created_entity_id": eid, "primary_name": ename, "evidence": _public(prop)["evidence"],
            "decided_by": principal.user_id})
    write_audit_event(action="document.new_entity_approved", entity_type="document",
                      entity_id=str(document_id), actor_user_id=principal.user_id,
                      request_id=_rid(request_id),
                      metadata={"created_entity_type": etype, "created_entity_id": eid,
                                "entity_name": ename, "proposal": _public(prop)["evidence"]})
    return {"ok": True, "created_entity_type": etype, "created_entity_id": eid, "name": ename}


def reject_proposal(document_id, *, principal, request_id=None, reason=""):
    """Record a rejection (retained + audited) so the document does not re-propose the same entity.
    Creates no entity."""
    with engine.connect() as conn:
        idx = build_match_indexes(conn)
        prop = _detect_one(conn, document_id, idx)
    evidence = _public(prop)["evidence"] if prop else {"document_id": document_id}
    with engine.begin() as conn:
        _write_decision_fact(conn, document_id, {"status": "rejected", "reason": reason,
                                                 "evidence": evidence, "decided_by": principal.user_id})
    write_audit_event(action="document.new_entity_rejected", entity_type="document",
                      entity_id=str(document_id), actor_user_id=principal.user_id,
                      request_id=_rid(request_id), metadata={"reason": reason, "proposal": evidence})
    return {"ok": True}


def assign_existing_instead(document_id, entity_type, entity_id, *, principal, request_id=None):
    """The proposed 'new' entity is actually an existing one: assign the document to that existing entity
    (dedupe) and retain the decision so it does not re-propose. Creates no entity."""
    if entity_type not in ("person", "household", "organization"):
        return {"ok": False, "reason": "invalid_entity_type"}
    from app.services.households import resolve_document_ownership
    col = {"person": "person_id", "household": "household_id", "organization": "organization_id"}[entity_type]
    result = resolve_document_ownership(document_id, actor_user_id=principal.user_id,
                                        request_id=_rid(request_id), **{col: entity_id})
    with engine.begin() as conn:
        _write_decision_fact(conn, document_id, {"status": "assigned_existing", "entity_type": entity_type,
                                                 "entity_id": entity_id, "decided_by": principal.user_id})
    write_audit_event(action="document.new_entity_assigned_existing", entity_type="document",
                      entity_id=str(document_id), actor_user_id=principal.user_id,
                      request_id=_rid(request_id),
                      metadata={"entity_type": entity_type, "entity_id": entity_id,
                                "assigned": bool(result.get("assigned"))})
    return {"ok": True, "assigned": bool(result.get("assigned")), "reason": result.get("reason")}
