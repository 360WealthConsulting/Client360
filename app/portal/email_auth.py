"""Client-portal authentication by emailed one-time code.

WHY THIS EXISTS. The client portal previously authenticated clients through an external Microsoft
identity provider. Clients are not tenant users — requiring them to hold and manage a Microsoft
identity to read their own documents is the wrong bar, and it is not the intended model. Client
authentication is now possession of the mailbox the firm already invited: a short-lived six-digit
code is emailed to the address bound to the portal account, and entering it proves possession.

WHAT THE SECURITY RESTS ON. Possession of the registered mailbox, and nothing the browser supplies.
Every destination address is read from the portal account row; no request field can redirect a code.
A code is bound to one account by construction (see ``_code_hash``), so it cannot be replayed against
another. This is deliberately NOT a click-the-link flow: the code must be typed back, so simply
receiving or forwarding the mail is not sufficient and link prefetchers cannot consume an activation.

WHAT IS NEVER RECORDED. The raw code exists only in the frame that generates it and in the message
body. It is never persisted, logged, audited, put in a URL or query string, or placed in a session
cookie. What is stored is an HMAC keyed by the server secret over ``account_id:code`` — useless
without the key, and useless against a different account even with it.

RELATIONSHIP TO THE EXISTING LIFECYCLE. This module issues and verifies codes. It does not invent a
second account model: activation still consumes a real ``portal_invitations`` row through
``app.portal.service``, and a verified sign-in still ends in ``create_portal_session``. Revocation
still flows through ``revoke_account_access``, which invalidates outstanding codes alongside
invitations, sessions and grants.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db import engine, portal_accounts, portal_email_verifications, portal_invitations

#: Six digits. Short enough to read off a phone and type; the strength comes from the bounded
#: attempt count and short lifetime, not from length. 1e6 space against 5 attempts is ~1 in 200,000.
CODE_DIGITS = 6
CODE_TTL_MINUTES = 10
#: Wrong guesses allowed against ONE code before it is destroyed. The client can always request a
#: fresh code; an attacker gets five tries per issued code and cannot farm attempts.
MAX_ATTEMPTS = 5
#: Codes a single account may be sent inside the window, and the minimum gap between two sends.
#: Bounds both mailbox flooding and using resend to widen the guessing surface.
RESEND_WINDOW_MINUTES = 15
MAX_SENDS_PER_WINDOW = 5
RESEND_COOLDOWN_SECONDS = 30

PURPOSE_ACTIVATION = "activation"
PURPOSE_LOGIN = "login"

#: The one sentence every start-of-sign-in returns, whatever actually happened. An unknown address, a
#: revoked account and a real send are indistinguishable to the caller, so the form cannot be used to
#: discover who holds a portal account.
GENERIC_SENT_MESSAGE = ("If an active portal account exists for that email, a verification code has "
                        "been sent.")
#: Shown for every verification failure: wrong code, expired code, spent code, too many attempts, no
#: challenge open. Distinguishing them would tell an attacker which guess was closer.
GENERIC_VERIFY_ERROR = ("That code is not valid. It may have expired or already been used. Request a "
                        "new code and try again.")
GENERIC_UNAVAILABLE = ("Sign-in is temporarily unavailable. Please try again shortly or contact your "
                       "advisory team.")


class EmailAuthError(Exception):
    """Any failure a client may see. Its text is always one of the generic sentences above."""


class RateLimited(EmailAuthError):
    """Too many codes requested for one account inside the window."""


def _now():
    return datetime.now(timezone.utc)


def _secret() -> bytes:
    """The HMAC key. Reuses the session signing secret, which is already required in production and
    already treated as a secret by configuration validation."""
    from app.config import SESSION_SECRET
    return (SESSION_SECRET or "").encode("utf-8")


def _code_hash(account_id: int, code: str) -> str:
    """HMAC over ``account_id:code``.

    Keyed, so a stolen database cannot be brute-forced offline against a six-digit space without also
    stealing the server secret. Account-bound, so a code observed for one account cannot be tested
    against another — cross-account reuse fails at the hash, not at a check someone might forget."""
    message = f"{int(account_id)}:{code}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def generate_code() -> str:
    """A cryptographically random six-digit code. ``secrets`` — never ``random``."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def _table_ready() -> bool:
    return portal_email_verifications is not None


def invalidate_codes(connection, account_id, *, now=None) -> int:
    """Kill every live code for one account, inside the caller's transaction.

    Called when a newer code is issued, when a code is verified, and by ``revoke_account_access``.
    Rows are marked, never deleted: an issued-and-superseded code is part of the account's history."""
    if not _table_ready():
        return 0
    now = now or _now()
    return connection.execute(portal_email_verifications.update().where(
        portal_email_verifications.c.portal_account_id == account_id,
        portal_email_verifications.c.consumed_at.is_(None),
        portal_email_verifications.c.invalidated_at.is_(None)).values(invalidated_at=now)).rowcount


def _recent_sends(connection, account_id, now):
    """(count in window, timestamp of the newest send) for the resend limiter."""
    window_start = now - timedelta(minutes=RESEND_WINDOW_MINUTES)
    row = connection.execute(select(
        func.count(portal_email_verifications.c.id),
        func.max(portal_email_verifications.c.created_at)).where(
        portal_email_verifications.c.portal_account_id == account_id,
        portal_email_verifications.c.created_at >= window_start)).one()
    return row[0] or 0, row[1]


def _issue(connection, account, *, purpose, invitation_id, now):
    """Create one code row and return the RAW code. Caller emails it and then drops it.

    The destination is ``account["email"]`` — read from the account row, never from the request, so
    there is no parameter anywhere in the system that can point a code at another mailbox."""
    sends, newest = _recent_sends(connection, account["id"], now)
    if sends >= MAX_SENDS_PER_WINDOW:
        raise RateLimited(GENERIC_SENT_MESSAGE)
    if newest is not None:
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        if (now - newest).total_seconds() < RESEND_COOLDOWN_SECONDS:
            raise RateLimited(GENERIC_SENT_MESSAGE)

    # A newly issued code supersedes every earlier one: at most one code is ever live per account.
    invalidate_codes(connection, account["id"], now=now)
    code = generate_code()
    connection.execute(portal_email_verifications.insert().values(
        portal_account_id=account["id"], purpose=purpose,
        code_hash=_code_hash(account["id"], code), sent_to_email=account["normalized_email"],
        portal_invitation_id=invitation_id, expires_at=now + timedelta(minutes=CODE_TTL_MINUTES)))
    return code


def _deliver(account, code, *, purpose):
    """Hand the code to the dedicated portal mailbox sender.

    Uses the SAME app-only Microsoft Graph registration the portal invitation already uses
    (``PORTAL_EMAIL_*``) — never a staff mailbox token, never a delegated sign-in. Delivery failure is
    reported as a value, never an exception, and never reveals provider detail to the client."""
    from app.services import email_delivery
    return email_delivery.send_portal_verification_code(
        recipient_email=account["email"], display_name=account["display_name"], code=code,
        purpose=purpose)


def _eligible_account(connection, *, account_id=None, normalized_email=None):
    """The account row, only if it may authenticate at all. ``None`` otherwise — the caller must not
    be able to tell 'no such address' from 'revoked'."""
    where = (portal_accounts.c.id == account_id) if account_id is not None else \
        (portal_accounts.c.normalized_email == normalized_email)
    account = connection.execute(select(portal_accounts).where(where)).mappings().one_or_none()
    if not account or account["status"] == "revoked":
        return None
    return account


# --- start: activation (first sign-in, from the invitation email) --------------------------

def start_activation(invitation_token):
    """Resolve a live invitation and email a code to the address ON THE ACCOUNT.

    Returns ``(account_id, delivery)``. Raises :class:`EmailAuthError` when the invitation is not
    usable — expired, spent, revoked, or for a revoked account — with the generic sentence.

    The invitation is NOT consumed here. It is spent only by a successful code verification, so a
    mail scanner that fetches the link cannot burn a client's activation."""
    if not _table_ready():
        raise EmailAuthError(GENERIC_UNAVAILABLE)
    from app.portal.service import _hash

    now = _now()
    with engine.begin() as connection:
        invitation = connection.execute(select(portal_invitations).where(
            portal_invitations.c.token_hash == _hash(invitation_token),
            portal_invitations.c.accepted_at.is_(None),
            portal_invitations.c.revoked_at.is_(None),
            portal_invitations.c.expires_at > now).with_for_update()).mappings().one_or_none()
        if not invitation:
            raise EmailAuthError(GENERIC_VERIFY_ERROR)
        account = _eligible_account(connection, account_id=invitation["portal_account_id"])
        if not account:
            raise EmailAuthError(GENERIC_VERIFY_ERROR)
        code = _issue(connection, account, purpose=PURPOSE_ACTIVATION,
                      invitation_id=invitation["id"], now=now)
    delivery = _deliver(account, code, purpose=PURPOSE_ACTIVATION)
    del code
    return account["id"], delivery


# --- start: repeat login (email entry) -----------------------------------------------------

def start_login(email):
    """Email a code to an ACTIVE account for this address, or do nothing at all.

    Returns ``(account_id_or_None, delivery_or_None)`` and NEVER raises for an unknown or ineligible
    address: the caller shows :data:`GENERIC_SENT_MESSAGE` either way, so the form cannot enumerate
    who has a portal account. Only an already-activated account may use this path — a client who has
    never accepted their invitation has no account to sign in to yet and must use their invitation."""
    normalized = (email or "").strip().lower()
    if not normalized or not _table_ready():
        return None, None
    now = _now()
    try:
        with engine.begin() as connection:
            account = _eligible_account(connection, normalized_email=normalized)
            if not account or account["status"] != "active":
                return None, None
            code = _issue(connection, account, purpose=PURPOSE_LOGIN, invitation_id=None, now=now)
    except RateLimited:
        return None, None                       # indistinguishable from every other outcome
    delivery = _deliver(account, code, purpose=PURPOSE_LOGIN)
    del code
    return account["id"], delivery


def resend(account_id):
    """Issue a fresh code for an open challenge, invalidating the previous one.

    ``account_id`` comes from the signed server-side session, never from a form field."""
    if not _table_ready():
        return None
    now = _now()
    with engine.begin() as connection:
        account = _eligible_account(connection, account_id=account_id)
        if not account:
            return None
        open_challenge = connection.execute(select(portal_email_verifications).where(
            portal_email_verifications.c.portal_account_id == account_id)
            .order_by(portal_email_verifications.c.id.desc()).limit(1)).mappings().one_or_none()
        purpose = open_challenge["purpose"] if open_challenge else PURPOSE_LOGIN
        invitation_id = open_challenge["portal_invitation_id"] if open_challenge else None
        if purpose == PURPOSE_ACTIVATION and invitation_id is not None:
            # Only resend an activation while its invitation is still usable.
            live = connection.execute(select(portal_invitations.c.id).where(
                portal_invitations.c.id == invitation_id,
                portal_invitations.c.accepted_at.is_(None),
                portal_invitations.c.revoked_at.is_(None),
                portal_invitations.c.expires_at > now)).scalars().first()
            if live is None:
                return None
        try:
            code = _issue(connection, account, purpose=purpose, invitation_id=invitation_id, now=now)
        except RateLimited:
            return None
    delivery = _deliver(account, code, purpose=purpose)
    del code
    return delivery


# --- verify ---------------------------------------------------------------------------------

def verify(account_id, code):
    """Check a code for ONE account and complete sign-in. Returns a portal session token.

    ``account_id`` is read from the signed session the browser cannot forge, and the code is only
    ever hashed against THAT account, so a code issued for someone else can never match here.

    On success, inside one transaction: the code is consumed, every other live code for the account
    is invalidated, and — for an activation — the invitation is accepted through the normal service
    path, which activates the account and records ``auth_method='email_code'``. The portal session is
    created only after all of that has committed.

    Every failure raises :class:`EmailAuthError` with the same sentence."""
    if not _table_ready():
        raise EmailAuthError(GENERIC_UNAVAILABLE)
    code = (code or "").strip().replace(" ", "").replace("-", "")
    now = _now()
    with engine.begin() as connection:
        account = _eligible_account(connection, account_id=account_id)
        if not account:
            raise EmailAuthError(GENERIC_VERIFY_ERROR)
        challenge = connection.execute(select(portal_email_verifications).where(
            portal_email_verifications.c.portal_account_id == account_id,
            portal_email_verifications.c.consumed_at.is_(None),
            portal_email_verifications.c.invalidated_at.is_(None),
            portal_email_verifications.c.expires_at > now)
            .order_by(portal_email_verifications.c.id.desc()).limit(1)
            .with_for_update()).mappings().one_or_none()
        if not challenge:
            raise EmailAuthError(GENERIC_VERIFY_ERROR)

        if not code or not hmac.compare_digest(challenge["code_hash"],
                                               _code_hash(account_id, code)):
            attempts = challenge["attempts"] + 1
            # Destroy the code once the budget is spent: guessing cannot continue against it.
            values = {"attempts": attempts}
            if attempts >= MAX_ATTEMPTS:
                values["invalidated_at"] = now
            connection.execute(portal_email_verifications.update().where(
                portal_email_verifications.c.id == challenge["id"]).values(**values))
            # Raised AFTER this block, never inside it. Raising here would roll the transaction back
            # and discard the increment with it — the attempt limit would count to one for ever and
            # brute force would be unbounded. The counter must COMMIT even though the call fails.
            wrong_code = True
        else:
            wrong_code = False

        if not wrong_code:
            connection.execute(portal_email_verifications.update().where(
                portal_email_verifications.c.id == challenge["id"]).values(consumed_at=now))
            invalidate_codes(connection, account_id, now=now)

            if challenge["purpose"] == PURPOSE_ACTIVATION:
                from app.portal.service import accept_invitation_row
                # Raised INSIDE the transaction on purpose: if the invitation died between issue and
                # verification, the code must NOT be recorded as spent.
                accepted = accept_invitation_row(connection, challenge["portal_invitation_id"],
                                                 now=now, auth_method="email_code")
                if not accepted:
                    raise EmailAuthError(GENERIC_VERIFY_ERROR)
            elif account["status"] != "active":
                raise EmailAuthError(GENERIC_VERIFY_ERROR)

    if wrong_code:
        raise EmailAuthError(GENERIC_VERIFY_ERROR)

    from app.portal.service import create_portal_session
    return create_portal_session(account["id"], device_fingerprint="portal-email-code")
