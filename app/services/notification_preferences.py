"""Notification preferences, consent & suppression decision layer (F5.3 / Epic 5, ADR-017).

A **decision layer only**: given a notification intent (recipient, channel, purpose), it
answers whether delivery through that channel is ``allowed``, ``suppressed``, ``disabled``,
or ``not_applicable`` — with a stable machine-readable reason code. It **never** delivers,
retries, calls providers, consumes events, mutates workflow/domain/notification-ledger
state, emits audit/evidence, or exposes content.

Two separate concepts (never conflated):
- **Preference** (``notification_preferences``): how a recipient wishes to be contacted
  (``opted_in`` / ``opted_out`` / ``default``).
- **Consent** (``notification_consents``): whether communication is legally/operationally
  permitted (``granted`` / ``withdrawn``, with effective/expiry/revoked timestamps). A
  positive preference **never** overrides missing, withdrawn, or expired consent; a
  preference is never proof of consent.

Reconciliation: no pre-existing preference/consent model exists, so these are the
authoritative records; the **F5.2 provider-state** remains authoritative for whether a
*channel* is disabled. Reference-only (recipient/source/authority references); no content.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

# --- decisions & reason codes ------------------------------------------------

ALLOWED = "allowed"
SUPPRESSED = "suppressed"
DISABLED = "disabled"
NOT_APPLICABLE = "not_applicable"
DECISIONS: frozenset[str] = frozenset({ALLOWED, SUPPRESSED, DISABLED, NOT_APPLICABLE})

# Stable reason codes (machine-readable; human-safe reasons carry no protected data).
REASON_CHANNEL_ALLOWED = "channel_allowed"
REASON_PROVIDER_CHANNEL_DISABLED = "provider_channel_disabled"
REASON_GLOBAL_SUPPRESSION = "global_suppression"
REASON_RECIPIENT_OPTED_OUT = "recipient_opted_out"
REASON_CONSENT_MISSING = "consent_missing"
REASON_CONSENT_EXPIRED = "consent_expired"
REASON_NO_APPLICABLE_PREFERENCE = "no_applicable_preference"

# Preference / consent states.
OPTED_IN = "opted_in"
OPTED_OUT = "opted_out"
DEFAULT = "default"
GRANTED = "granted"
WITHDRAWN = "withdrawn"

#: Channels that require explicit, effective consent before delivery. External channels
#: require consent; in-app does not (consistent with existing product behavior + ADR-017).
CONSENT_REQUIRED_CHANNELS: frozenset[str] = frozenset({"email", "sms", "push"})

WILDCARD = "*"


@dataclass(frozen=True)
class DeliveryDecision:
    """Structured, deterministic, content-free delivery eligibility decision."""

    decision: str          # allowed | suppressed | disabled | not_applicable
    channel: str
    recipient_ref: str
    purpose: str
    reason_code: str
    reason: str            # human-safe; never contains protected data or content
    source_ref: str | None = None       # preference/consent source reference
    effective_ref: str | None = None     # effective timestamp/version reference

    def to_dict(self) -> dict:
        return {
            "decision": self.decision, "channel": self.channel, "recipient_ref": self.recipient_ref,
            "purpose": self.purpose, "reason_code": self.reason_code, "reason": self.reason,
            "source_ref": self.source_ref, "effective_ref": self.effective_ref,
        }


# --- table access (reflection) -----------------------------------------------

def _pref_table():
    from sqlalchemy import Table

    from app.db import engine, metadata
    t = metadata.tables.get("notification_preferences")
    return t if t is not None else Table("notification_preferences", metadata, autoload_with=engine)


def _consent_table():
    from sqlalchemy import Table

    from app.db import engine, metadata
    t = metadata.tables.get("notification_consents")
    return t if t is not None else Table("notification_consents", metadata, autoload_with=engine)


# --- write helpers (persistence model; upsert current-state by scope) --------

def record_preference(*, recipient_type, recipient_ref, preference_state, channel=WILDCARD,
                      purpose=WILDCARD, source_ref=None, effective_at=None, conn=None):
    """Record/replace the current preference for a scope (recipient, channel, purpose)."""
    if preference_state not in (OPTED_IN, OPTED_OUT, DEFAULT):
        raise ValueError(f"Invalid preference_state: {preference_state!r}")
    t = _pref_table()

    def _do(c):
        from datetime import UTC, datetime
        existing = c.execute(select(t.c.id).where(
            t.c.recipient_type == recipient_type, t.c.recipient_ref == recipient_ref,
            t.c.channel == channel, t.c.purpose == purpose)).scalar()
        if existing is not None:
            c.execute(t.update().where(t.c.id == existing).values(
                preference_state=preference_state, source_ref=source_ref,
                effective_at=effective_at, updated_at=datetime.now(UTC)))
            return existing
        return c.execute(t.insert().values(
            preference_uid=str(uuid.uuid4()), recipient_type=recipient_type, recipient_ref=recipient_ref,
            channel=channel, purpose=purpose, preference_state=preference_state,
            source_ref=source_ref, effective_at=effective_at).returning(t.c.id)).scalar_one()

    return _with(conn, _do)


def record_consent(*, recipient_type, recipient_ref, consent_state, channel=WILDCARD, purpose=WILDCARD,
                   authority_ref=None, source_ref=None, effective_at=None, expires_at=None,
                   revoked_at=None, conn=None):
    """Record/replace the current consent for a scope. Withdrawal sets ``consent_state='withdrawn'``
    (+ ``revoked_at``); it is never a delete."""
    if consent_state not in (GRANTED, WITHDRAWN):
        raise ValueError(f"Invalid consent_state: {consent_state!r}")
    t = _consent_table()

    def _do(c):
        from datetime import UTC, datetime
        existing = c.execute(select(t.c.id).where(
            t.c.recipient_type == recipient_type, t.c.recipient_ref == recipient_ref,
            t.c.channel == channel, t.c.purpose == purpose)).scalar()
        if existing is not None:
            c.execute(t.update().where(t.c.id == existing).values(
                consent_state=consent_state, authority_ref=authority_ref, source_ref=source_ref,
                effective_at=effective_at, expires_at=expires_at, revoked_at=revoked_at,
                updated_at=datetime.now(UTC)))
            return existing
        return c.execute(t.insert().values(
            consent_uid=str(uuid.uuid4()), recipient_type=recipient_type, recipient_ref=recipient_ref,
            channel=channel, purpose=purpose, consent_state=consent_state, authority_ref=authority_ref,
            source_ref=source_ref, effective_at=effective_at, expires_at=expires_at,
            revoked_at=revoked_at).returning(t.c.id)).scalar_one()

    return _with(conn, _do)


def _with(conn, fn):
    if conn is not None:
        return fn(conn)
    from app.db import engine
    with engine.begin() as c:
        return fn(c)


# --- lookup helpers (most-specific scope wins) -------------------------------

def _most_specific(rows, channel, purpose):
    """Pick the most-specific matching row: (channel,purpose) > (channel,*) > (*,purpose) > (*,*)."""
    def rank(r):
        return (2 if r["channel"] == channel else 0) + (1 if r["purpose"] == purpose else 0)
    scoped = [r for r in rows if r["channel"] in (channel, WILDCARD) and r["purpose"] in (purpose, WILDCARD)]
    return max(scoped, key=rank) if scoped else None


def _preference_for(c, recipient_type, recipient_ref, channel, purpose):
    t = _pref_table()
    rows = c.execute(select(t).where(t.c.recipient_type == recipient_type, t.c.recipient_ref == recipient_ref)).mappings().all()
    return _most_specific(rows, channel, purpose)


def _consent_for(c, recipient_type, recipient_ref, channel, purpose):
    t = _consent_table()
    rows = c.execute(select(t).where(t.c.recipient_type == recipient_type, t.c.recipient_ref == recipient_ref)).mappings().all()
    return _most_specific(rows, channel, purpose)


def _consent_effective(row, now) -> bool:
    if row is None or row["consent_state"] != GRANTED or row["revoked_at"] is not None:
        return False
    if row["effective_at"] is not None and row["effective_at"] > now:
        return False
    if row["expires_at"] is not None and row["expires_at"] <= now:
        return False
    return True


# --- the decision service (deterministic, normative precedence) --------------

def evaluate_delivery(recipient_type, recipient_ref, channel, purpose, *, registry=None,
                      consent_required=None, now=None, conn=None) -> DeliveryDecision:
    """Deterministic delivery-eligibility decision. Decision layer only — never delivers,
    mutates state, or emits audit/evidence.

    Precedence (normative):
      1. Unknown channel                         -> not_applicable (no_applicable_preference)
      2. Provider channel disabled (F5.2 state)  -> disabled (provider_channel_disabled)
      3. Global suppression (do-not-contact)     -> suppressed (global_suppression)
      4. Recipient opt-out (preference)          -> suppressed (recipient_opted_out)
      5. Required consent missing/withdrawn       -> suppressed (consent_missing)
         Required consent expired                -> suppressed (consent_expired)
      6. Otherwise                               -> allowed (channel_allowed)
    """
    from datetime import UTC, datetime
    now = now or datetime.now(UTC)
    consent_required = CONSENT_REQUIRED_CHANNELS if consent_required is None else consent_required
    from app.services.notification_providers import default_registry
    registry = registry or default_registry()

    def _mk(decision, reason_code, reason, source_ref=None, effective_ref=None):
        return DeliveryDecision(decision=decision, channel=channel, recipient_ref=recipient_ref,
                                purpose=purpose, reason_code=reason_code, reason=reason,
                                source_ref=source_ref, effective_ref=effective_ref)

    # 1. unknown channel
    if channel not in registry:
        return _mk(NOT_APPLICABLE, REASON_NO_APPLICABLE_PREFERENCE, "channel is not a registered notification channel")
    # 2. provider channel disabled (F5.2 state boundary; no provider delivery invocation)
    if not registry.get(channel).is_ready():
        return _mk(DISABLED, REASON_PROVIDER_CHANNEL_DISABLED, "channel provider is disabled")

    def _do(c):
        # 3. global suppression / do-not-contact (a withdrawn consent scoped to all channels/purposes)
        gs = _consent_for(c, recipient_type, recipient_ref, WILDCARD, WILDCARD)
        if gs is not None and gs["channel"] == WILDCARD and gs["purpose"] == WILDCARD and gs["consent_state"] == WITHDRAWN:
            return _mk(SUPPRESSED, REASON_GLOBAL_SUPPRESSION, "recipient is under global communication suppression",
                       source_ref=gs["consent_uid"], effective_ref=str(gs["revoked_at"] or gs["updated_at"] or gs["created_at"]))
        # 4. recipient opt-out (preference)
        pref = _preference_for(c, recipient_type, recipient_ref, channel, purpose)
        if pref is not None and pref["preference_state"] == OPTED_OUT:
            return _mk(SUPPRESSED, REASON_RECIPIENT_OPTED_OUT, "recipient opted out of this channel",
                       source_ref=pref["preference_uid"], effective_ref=str(pref["effective_at"] or pref["updated_at"] or pref["created_at"]))
        # 5. required consent
        if channel in consent_required:
            consent = _consent_for(c, recipient_type, recipient_ref, channel, purpose)
            if not _consent_effective(consent, now):
                if consent is not None and consent["expires_at"] is not None and consent["expires_at"] <= now:
                    return _mk(SUPPRESSED, REASON_CONSENT_EXPIRED, "required consent has expired",
                               source_ref=consent["consent_uid"], effective_ref=str(consent["expires_at"]))
                return _mk(SUPPRESSED, REASON_CONSENT_MISSING, "required consent is missing or withdrawn",
                           source_ref=(consent["consent_uid"] if consent is not None else None))
        # 6. allowed
        src = pref["preference_uid"] if pref is not None else None
        return _mk(ALLOWED, REASON_CHANNEL_ALLOWED, "delivery permitted for this channel", source_ref=src)

    return _with(conn, _do)
