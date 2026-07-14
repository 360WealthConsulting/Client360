"""Tax Document Intelligence — deterministic document matching and missing info.

Sprint 5.4. This module replaces the substring-based Microsoft document matching
(RC8 H13) with a deterministic, authorization-aware, confidence-scored engine that
routes every ambiguous case to mandatory human review. See
docs/SPRINT_5_4_TAX_DOCUMENT_INTELLIGENCE.md.

Design invariants enforced here:
- No substring/containment matching. Ownership is established only from exact,
  deterministic identifiers (portal-request provenance, exact drive/folder rule,
  exact uploader email, prior manual decision). Fuzzy hints never contribute to
  the confidence score.
- Auto-assignment requires a single candidate above the auto-match threshold with
  no competing candidate above the ambiguity floor; everything else -> review.
- A match becomes an accepted link only after ownership validation, and reviewer
  actions require record-scope authorization; denials are audited immutably.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256 as _sha256
from typing import Optional, Protocol
import uuid

from sqlalchemy import or_, select

from app.db import (documents, engine, people, tax_checklist_items,
    tax_document_classifications, tax_document_links, tax_document_match_evidence,
    tax_document_review_events, tax_engagement_returns, tax_engagements,
    tax_missing_items)
from app.security.audit import audit_denied, write_audit_event
from app.services.tax_domain import list_engagements

# --- Engine constants -------------------------------------------------------

AUTO_MATCH_THRESHOLD = 0.90
AMBIGUITY_FLOOR = 0.50

# Deterministic, exact-match signals and their confidence weights. Any signal
# type not present here (e.g. fuzzy "hint") is ignored by the scorer and can
# never contribute to an auto-match.
SIGNAL_WEIGHTS = {
    "portal_request": 1.00,
    "drive_rule": 0.95,
    "email_exact": 0.90,
    "manual": 1.00,
}

REVIEW_ACTIONS = ("accept", "reject", "reassign", "classify", "duplicate", "revert")


# --- Pure matching engine (no I/O; fully unit-testable) ---------------------

@dataclass(frozen=True)
class Signal:
    """A single exact-match ownership signal resolved to (person, return)."""
    signal_type: str
    person_id: int
    return_id: Optional[int] = None
    checklist_item_id: Optional[int] = None
    value: str = ""  # opaque; hashed into evidence, never matched by substring


@dataclass
class Candidate:
    person_id: int
    return_id: Optional[int]
    checklist_item_id: Optional[int]
    confidence: float
    signals: list = field(default_factory=list)


@dataclass
class MatchDecision:
    outcome: str  # 'accept' | 'review' | 'unmatched'
    candidate: Optional[Candidate]
    candidates: list
    reason: str


def score_signals(signals):
    """Aggregate exact-match signals into per-(person, return) candidates.

    Confidence is the strongest single deterministic signal (not a sum), so weak
    signals can never combine into a false auto-match. Unknown signal types are
    ignored entirely.
    """
    buckets = {}
    for s in signals:
        weight = SIGNAL_WEIGHTS.get(s.signal_type)
        if not weight:
            continue
        key = (s.person_id, s.return_id)
        bucket = buckets.setdefault(key, {"confidence": 0.0, "signals": [], "checklist_item_id": None})
        bucket["confidence"] = min(1.0, max(bucket["confidence"], weight))
        bucket["signals"].append(s)
        if s.checklist_item_id and not bucket["checklist_item_id"]:
            bucket["checklist_item_id"] = s.checklist_item_id
    candidates = [
        Candidate(person_id=k[0], return_id=k[1], checklist_item_id=v["checklist_item_id"],
                  confidence=round(v["confidence"], 3), signals=v["signals"])
        for k, v in buckets.items()
    ]
    candidates.sort(key=lambda c: (c.confidence, c.return_id or 0), reverse=True)
    return candidates


def decide(signals):
    """Decide match outcome from exact-match signals. Never uses substring logic."""
    candidates = score_signals(signals)
    if not candidates:
        return MatchDecision("unmatched", None, [], "no deterministic evidence")
    contenders = [c for c in candidates if c.confidence >= AMBIGUITY_FLOOR]
    top = candidates[0]
    if len(contenders) > 1:
        return MatchDecision("review", top, candidates, "ambiguous: multiple candidates above floor")
    if top.confidence >= AUTO_MATCH_THRESHOLD:
        return MatchDecision("accept", top, candidates, "single deterministic candidate above threshold")
    return MatchDecision("review", top, candidates, "below auto-match threshold")


# --- AI classifier port (interface only; inert — no vendor, no external call) --

class TaxDocumentClassifier(Protocol):
    key: str
    def classify(self, document: dict) -> Optional[dict]:
        """Return {'category': str, 'confidence': float} or None. Inert by default."""
        ...


class NoopAIClassifier:
    """Default, non-AI classifier. Makes no external call and no decision."""
    key = "noop"

    def classify(self, document):
        return None


# The active classifier is inert in Sprint 5.4. Enabling any real provider is
# Epic 6 and requires explicit approval and its own review.
AI_CLASSIFIER: TaxDocumentClassifier = NoopAIClassifier()


# --- Helpers ----------------------------------------------------------------

def _hash(value):
    return _sha256((value or "").encode("utf-8")).hexdigest()


def _return_owner(connection, return_id):
    """Return (person_id, household_id) that owns a tax return, or (None, None)."""
    row = connection.execute(
        select(tax_engagements.c.person_id, tax_engagements.c.household_id)
        .select_from(tax_engagement_returns.join(tax_engagements))
        .where(tax_engagement_returns.c.id == return_id)
    ).first()
    return (row.person_id, row.household_id) if row else (None, None)


def validate_ownership(connection, person_id, return_id):
    """Deterministic ownership check: the return must belong to the candidate
    person directly, or to a household the person is a member of. No fuzzy logic;
    a document is never attached to a person the return does not belong to."""
    if person_id is None or return_id is None:
        return False
    owner_person, owner_household = _return_owner(connection, return_id)
    if owner_person is None and owner_household is None:
        return False
    if owner_person == person_id:
        return True
    if owner_household is not None:
        member = connection.scalar(
            select(people.c.id).where(people.c.id == person_id, people.c.household_id == owner_household)
        )
        return member is not None
    return False


def find_duplicate(connection, document_id):
    """Exact-hash duplicate detection (no fuzzy matching). Returns an existing
    document id with the same sha256 that already has an accepted link, else None."""
    digest = connection.scalar(select(documents.c.sha256).where(documents.c.id == document_id))
    if not digest:
        return None
    return connection.scalar(
        select(tax_document_links.c.document_id)
        .join(documents, documents.c.id == tax_document_links.c.document_id)
        .where(documents.c.sha256 == digest, documents.c.id != document_id,
               tax_document_links.c.status == "accepted")
        .limit(1)
    )


# --- Signal builders (resolve deterministic evidence to (person, return)) ----

def portal_request_signals(connection, checklist_item_id):
    """Deterministic provenance: a checklist item bound to a portal request binds
    person + return + checklist item. Confidence 1.0."""
    row = connection.execute(
        select(tax_checklist_items.c.id, tax_checklist_items.c.tax_engagement_return_id,
               tax_engagements.c.person_id)
        .select_from(tax_checklist_items
            .join(tax_engagement_returns, tax_engagement_returns.c.id == tax_checklist_items.c.tax_engagement_return_id)
            .join(tax_engagements, tax_engagements.c.id == tax_engagement_returns.c.tax_engagement_id))
        .where(tax_checklist_items.c.id == checklist_item_id)
    ).first()
    if not row:
        return []
    return [Signal("portal_request", person_id=row.person_id, return_id=row.tax_engagement_return_id,
                   checklist_item_id=row.id, value=f"checklist:{checklist_item_id}")]


def email_exact_signals(connection, uploader_email):
    """Exact normalized-email identity -> the person's active returns. If the
    person has more than one return, every return becomes a same-weight candidate,
    which the engine resolves to review (ambiguous), never an auto-assignment."""
    if not uploader_email:
        return []
    person_id = connection.scalar(select(people.c.id).where(people.c.normalized_email == uploader_email))
    if not person_id:
        return []
    return_ids = list(connection.scalars(
        select(tax_engagement_returns.c.id)
        .select_from(tax_engagement_returns.join(tax_engagements))
        .where(tax_engagements.c.person_id == person_id)
    ))
    return [Signal("email_exact", person_id=person_id, return_id=rid, value=f"email:{uploader_email}")
            for rid in return_ids]


# --- Ingestion / link persistence -------------------------------------------

def _resolve_checklist(connection, return_id, checklist_item_id, document_id):
    """Mark a checklist item received and resolve its missing-item record."""
    if not checklist_item_id:
        return
    connection.execute(tax_checklist_items.update()
        .where(tax_checklist_items.c.id == checklist_item_id)
        .values(status="received", document_id=document_id, completed_at=datetime.now(timezone.utc)))
    connection.execute(tax_missing_items.update()
        .where(tax_missing_items.c.checklist_item_id == checklist_item_id, tax_missing_items.c.status == "open")
        .values(status="resolved", resolved_at=datetime.now(timezone.utc)))


def ingest_document(document_id, signals, *, actor_user_id=None, request_id=None):
    """Run the engine over a canonical document's signals and persist a link.

    - accept  -> validated, accepted link + checklist/missing resolution
    - review  -> proposed link for mandatory human review
    - unmatched -> proposed link with no candidate (surfaced in the unmatched queue)
    Duplicates (exact hash) are routed to duplicate review, never silently merged.
    """
    decision = decide(signals)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        duplicate_of = find_duplicate(connection, document_id)
        if decision.outcome == "accept" and duplicate_of is not None:
            status, source, confidence, cand = "proposed", "hash", 0.90, decision.candidate
            reason = "duplicate: exact hash of an already-linked document"
        elif decision.outcome == "accept":
            cand = decision.candidate
            if not validate_ownership(connection, cand.person_id, cand.return_id):
                status, source, confidence = "proposed", cand.signals[0].signal_type, cand.confidence
                reason = "ownership validation failed -> review"
                cand = decision.candidate
            else:
                status, source, confidence = "accepted", cand.signals[0].signal_type, cand.confidence
                reason = decision.reason
        elif decision.outcome == "review":
            cand = decision.candidate
            status = "proposed"
            source = cand.signals[0].signal_type if cand and cand.signals else "manual"
            confidence = cand.confidence if cand else 0.0
            reason = decision.reason
        else:  # unmatched
            cand = None
            status, source, confidence, reason = "proposed", "manual", 0.0, decision.reason

        link_id = connection.execute(tax_document_links.insert().values(
            document_id=document_id,
            tax_engagement_return_id=(cand.return_id if cand else None) or _any_return(connection, document_id),
            tax_checklist_item_id=(cand.checklist_item_id if cand else None),
            status=status, confidence=confidence, match_source=source,
            matched_by_user_id=actor_user_id if status == "accepted" and actor_user_id else None,
            metadata={"reason": reason, "duplicate_of": duplicate_of},
            decided_at=now if status == "accepted" else None,
        ).returning(tax_document_links.c.id)).scalar_one()

        for signal in (cand.signals if cand else []):
            connection.execute(tax_document_match_evidence.insert().values(
                tax_document_link_id=link_id, signal_type=signal.signal_type,
                value_hash=_hash(signal.value), weight=SIGNAL_WEIGHTS.get(signal.signal_type, 0)))

        if status == "accepted":
            _resolve_checklist(connection, cand.return_id, cand.checklist_item_id, document_id)
            connection.execute(tax_document_review_events.insert().values(
                tax_document_link_id=link_id, action="accept", actor_user_id=actor_user_id,
                reason=reason, metadata={"auto": actor_user_id is None}))
    if status == "accepted":
        write_audit_event(action="tax.document.matched", entity_type="tax_document_link", entity_id=link_id,
            actor_user_id=actor_user_id, request_id=request_id or f"tax-doc-{uuid.uuid4()}",
            metadata={"document_id": document_id, "return_id": cand.return_id, "confidence": float(confidence), "source": source})
    return {"link_id": link_id, "outcome": status, "reason": reason, "confidence": float(confidence)}


def _any_return(connection, document_id):
    """Fallback return for an unmatched/review link so the row is queryable: the
    document's owning person's single return if unambiguous, else None."""
    person_id = connection.scalar(select(documents.c.person_id).where(documents.c.id == document_id))
    if not person_id:
        return None
    rows = list(connection.scalars(
        select(tax_engagement_returns.c.id).select_from(tax_engagement_returns.join(tax_engagements))
        .where(tax_engagements.c.person_id == person_id)))
    return rows[0] if len(rows) == 1 else None


# --- Authorization for reviewer actions -------------------------------------

def _authorized_return(principal, return_id):
    """Canonical tax record-scope check (same helper contract as the tax routes)."""
    return return_id is not None and return_id in {r["return_id"] for r in list_engagements(principal)}


def _link_row(connection, link_id):
    return connection.execute(select(tax_document_links).where(tax_document_links.c.id == link_id)).mappings().one_or_none()


def review_action(link_id, action, *, principal, request, return_id=None, checklist_item_id=None,
                  category=None, reason=None):
    """Apply an authorized reviewer decision to a document link (H-review workflow).

    Every action is record-scope authorized against the affected return, writes an
    append-only review event, and audits denials immutably. Ambiguity is never
    resolved silently — only an explicit accept resolves a checklist item.
    """
    if action not in REVIEW_ACTIONS:
        raise ValueError("Unsupported review action")
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        link = _link_row(connection, link_id)
        if not link:
            raise ValueError("Document link not found")
        target_return = return_id if action == "reassign" else link["tax_engagement_return_id"]
        # Authorize against both the current and (for reassign) the target return.
        if not _authorized_return(principal, link["tax_engagement_return_id"]) or (
                action == "reassign" and not _authorized_return(principal, target_return)):
            audit_denied(request, action="tax.document.review_denied", entity_type="tax_document_link",
                         entity_id=link_id, actor_user_id=principal.user_id, detail=f"{action} out of scope")
            raise PermissionError("Document is outside your authorized scope")

        if action == "accept":
            # The link is already bound to this return and the reviewer is
            # authorized for it (checked above), so ownership is established.
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(status="accepted", matched_by_user_id=principal.user_id, decided_at=now))
            _resolve_checklist(connection, link["tax_engagement_return_id"], link["tax_checklist_item_id"], link["document_id"])
        elif action == "reject":
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(status="rejected", matched_by_user_id=principal.user_id, decided_at=now))
        elif action == "reassign":
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(tax_engagement_return_id=target_return, tax_checklist_item_id=None,
                        status="proposed", match_source="manual", matched_by_user_id=principal.user_id))
        elif action == "classify":
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(tax_checklist_item_id=checklist_item_id, category=category, matched_by_user_id=principal.user_id))
            connection.execute(tax_document_classifications.insert().values(
                document_id=link["document_id"], category=category or "uncategorized", confidence=1.0,
                source="manual", reviewer_user_id=principal.user_id, provenance={"link_id": link_id}))
        elif action == "duplicate":
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(status="superseded", matched_by_user_id=principal.user_id, decided_at=now))
        elif action == "revert":
            connection.execute(tax_document_links.update().where(tax_document_links.c.id == link_id)
                .values(status="proposed", decided_at=None))
            if link["tax_checklist_item_id"]:
                connection.execute(tax_checklist_items.update()
                    .where(tax_checklist_items.c.id == link["tax_checklist_item_id"])
                    .values(status="missing", document_id=None, completed_at=None))
                connection.execute(tax_missing_items.update()
                    .where(tax_missing_items.c.checklist_item_id == link["tax_checklist_item_id"], tax_missing_items.c.status == "resolved")
                    .values(status="open", resolved_at=None))

        connection.execute(tax_document_review_events.insert().values(
            tax_document_link_id=link_id, action=action, actor_user_id=principal.user_id,
            reason=reason, metadata={"return_id": target_return, "category": category}))
    write_audit_event(action=f"tax.document.{action}", entity_type="tax_document_link", entity_id=link_id,
        actor_user_id=principal.user_id, request_id=getattr(getattr(request, "state", None), "request_id", None) or f"tax-doc-{uuid.uuid4()}",
        metadata={"action": action})
    return {"link_id": link_id, "action": action}


# --- Missing-information engine ----------------------------------------------

def compute_missing(return_id):
    """Recompute the missing set for a return from checklist + accepted-link state.

    Deterministic and explainable: a required checklist item is missing unless it
    is marked received (satisfied by an accepted document). Opens/closes
    tax_missing_items accordingly and returns the missing set with reasons.
    """
    now = datetime.now(timezone.utc)
    missing = []
    with engine.begin() as connection:
        items = connection.execute(select(tax_checklist_items)
            .where(tax_checklist_items.c.tax_engagement_return_id == return_id)).mappings().all()
        for item in items:
            satisfied = item["status"] == "received" and item["document_id"] is not None
            open_row = connection.scalar(select(tax_missing_items.c.id).where(
                tax_missing_items.c.checklist_item_id == item["id"], tax_missing_items.c.status == "open"))
            if item["required"] and not satisfied:
                missing.append({"checklist_item_id": item["id"],
                                "title": item["item_snapshot"].get("title") if item["item_snapshot"] else None,
                                "reason": "required document not yet accepted"})
                if not open_row:
                    connection.execute(tax_missing_items.insert().values(
                        tax_engagement_return_id=return_id, checklist_item_id=item["id"], item_type="document",
                        title=(item["item_snapshot"] or {}).get("title", "Required document"),
                        description=(item["item_snapshot"] or {}).get("description"), due_date=item["due_date"]))
            elif satisfied and open_row:
                connection.execute(tax_missing_items.update().where(tax_missing_items.c.id == open_row)
                    .values(status="resolved", resolved_at=now))
    return {"return_id": return_id, "missing": missing, "missing_count": len(missing)}


# --- Read models -------------------------------------------------------------

def checklist_view(return_id):
    with engine.connect() as connection:
        items = connection.execute(select(tax_checklist_items)
            .where(tax_checklist_items.c.tax_engagement_return_id == return_id)
            .order_by(tax_checklist_items.c.id)).mappings().all()
        missing = connection.execute(select(tax_missing_items)
            .where(tax_missing_items.c.tax_engagement_return_id == return_id, tax_missing_items.c.status == "open")).mappings().all()
    return {"checklist": [dict(i) for i in items], "missing": [dict(m) for m in missing]}


def documents_view(return_id):
    with engine.connect() as connection:
        links = connection.execute(select(tax_document_links)
            .where(tax_document_links.c.tax_engagement_return_id == return_id)
            .order_by(tax_document_links.c.status, tax_document_links.c.confidence.desc())).mappings().all()
    return {"return_id": return_id, "links": [dict(link) for link in links]}


def review_queue(principal, status="proposed"):
    """Scoped review list: proposed/unmatched links for the caller's authorized
    returns only (never firm-wide unless record.read_all via list_engagements)."""
    authorized = {r["return_id"] for r in list_engagements(principal)}
    if not authorized:
        return {"items": [], "count": 0}
    with engine.connect() as connection:
        rows = connection.execute(select(tax_document_links)
            .where(tax_document_links.c.status == status,
                   tax_document_links.c.tax_engagement_return_id.in_(authorized))
            .order_by(tax_document_links.c.confidence.desc())).mappings().all()
    return {"items": [dict(r) for r in rows], "count": len(rows)}
