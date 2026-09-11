"""HTTP surface for the 3CX Phone System v20 custom-CRM connector.

Two endpoints, both POST, both called by the PBX and never by a browser:

  ``POST /api/integrations/3cx/lookup``  — who is calling this number?
  ``POST /api/integrations/3cx/calls``   — record one completed call.

AUTHENTICATION IS THIS ROUTE'S OWN JOB. Both paths are listed in
``app.security.middleware.PUBLIC_EXACT`` so the staff session middleware lets them through,
exactly as ``/mcp`` and the SharePoint webhook are. "Public" there means "no session cookie
required", not "unauthenticated": every request goes through
:func:`app.integrations.threecx.auth.authenticate` before anything else runs, and a request
without the dedicated integration secret never reaches a query. Honouring no ambient credential —
no cookie, no session — the endpoints are not CSRF-reachable, so the listing weakens nothing.

WHY POST, INCLUDING FOR THE LOOKUP. A phone number is client-identifying, and a GET would put it
in the request line, where it is written to every access log, proxy log and error report along the
path. The lookup reads and changes nothing; it is a POST purely to keep the number out of URLs.

WHAT THIS ROUTE REFUSES TO DO, and why each refusal is load-bearing:

  * **Pop a profile on anything but one exact match.** Zero matches and two matches both answer
    ``found: false`` and carry no contact detail at all. See
    :mod:`app.integrations.threecx.lookup` for what a wrong screen-pop costs.
  * **Trust a client id the PBX hands back.** ``entity_id`` on a journal request is CHECKED
    against a fresh lookup of the number, never used in place of one. Without that check, anyone
    holding the integration secret could file a call against any client in the book.
  * **Say anything a full phone number would leak.** Every log line, audit entry and error body
    carries the masked last-four form only.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import engine
from app.integrations.threecx import auth as threecx_auth
from app.integrations.threecx import config as threecx_config
from app.integrations.threecx import lookup as threecx_lookup
from app.integrations.threecx.errors import ThreeCxUnauthenticated, ThreeCxUnavailable
from app.security.audit import write_audit_event
from app.security.origin import CanonicalOriginError, canonical_origin
from app.services.communications import call_journal
from app.services.communications.phone_numbers import mask_number

logger = logging.getLogger("client360.threecx")
router = APIRouter()

#: Re-exported from the integration package, which owns them: the XML template and the middleware
#: exemption list need the same two strings, and neither should have to import a route module.
LOOKUP_PATH = threecx_config.LOOKUP_PATH
JOURNAL_PATH = threecx_config.JOURNAL_PATH

#: Bodies larger than this are refused unread. A lookup is a few dozen bytes and a journal entry a
#: few hundred; anything approaching this is a mistake or an attempt to spend memory before
#: authentication.
MAX_BODY_BYTES = 16 * 1024


def _context(request: Request) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _unauthorized() -> JSONResponse:
    """A 401 that says HOW to authenticate but never why the attempt failed."""
    return JSONResponse({"error": "unauthorized",
                         "detail": "A valid 3CX integration secret is required."},
                        status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _not_found() -> JSONResponse:
    """Switched off: behave as though the endpoint does not exist, so a probe learns nothing about
    whether this deployment has a telephony surface."""
    return JSONResponse({"detail": "Not Found"}, status_code=404)


def _unprocessable(detail: str) -> JSONResponse:
    return JSONResponse({"error": "unprocessable", "detail": detail}, status_code=422)


async def _authenticate_and_read(request: Request) -> tuple[JSONResponse | None, dict]:
    """Gate one request and return its parsed body. ``(response, {})`` when it must not proceed."""
    try:
        threecx_auth.authenticate(request.headers.get("authorization"))
    except ThreeCxUnavailable:
        return _not_found(), {}
    except ThreeCxUnauthenticated:
        logger.warning("3CX request rejected: no valid integration secret (request_id=%s)",
                       getattr(request.state, "request_id", None))
        return _unauthorized(), {}

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return _unprocessable(f"Request body exceeds {MAX_BODY_BYTES} bytes."), {}
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return _unprocessable("Request body is not valid JSON."), {}
    if not isinstance(payload, dict):
        return _unprocessable("Request body must be a JSON object."), {}
    return None, payload


def _base_url() -> str | None:
    """The validated canonical origin, or ``None``. A malformed ``PUBLIC_BASE_URL`` costs the
    screen-pop URL and nothing else — the lookup still answers with the client's name."""
    try:
        return canonical_origin()
    except CanonicalOriginError as exc:
        logger.error("3CX screen-pop URL unavailable: %s", exc)
        return None


@router.post(LOOKUP_PATH)
async def lookup_number(request: Request):
    """Answer "who is calling?" for one number, for a 3CX screen-pop.

    Reads only. The response carries contact detail ONLY for exactly one active match; a zero or
    ambiguous result carries a count and nothing more.
    """
    refusal, payload = await _authenticate_and_read(request)
    if refusal is not None:
        return refusal

    number = payload.get("number") or payload.get("Number")
    masked = mask_number(number)

    with engine.begin() as conn:
        result = threecx_lookup.lookup_by_number(conn, number)
        write_audit_event(
            action="threecx.lookup", entity_type="person",
            entity_id=result.person_id, outcome="success" if result.found else "no_match",
            # Masked number only. An audit trail that records every number a PBX ever asked about
            # would be a call-detail record in the audit chain, which is not what it is for.
            metadata={"number_masked": masked, "match_count": result.match_count,
                      "ambiguous": result.ambiguous},
            conn=conn, **_context(request),
        )

    logger.info("3CX lookup number=%s matches=%d", masked, result.match_count)

    # ``contacts`` holds EXACTLY ONE entry for a single active match and is EMPTY for both no
    # match and several matches. The template's ``<Rule Type="Any">contacts</Rule>`` does not fire
    # on an empty array, so "never pop a profile on zero or multiple matches" is enforced by the
    # response shape rather than by 3CX-side configuration anyone could edit.
    body: dict = {"found": result.found, "match_count": result.match_count, "contacts": []}
    if result.ambiguous:
        body["ambiguous"] = True
    if result.found:
        body["contacts"] = [{
            "person_id": result.person_id,
            "entity_id": str(result.person_id),
            "entity_type": "person",
            "display_name": result.display_name,
            "first_name": result.first_name or "",
            "last_name": result.last_name or "",
            "email": result.email or "",
            "contact_url": result.profile_url(_base_url()) or "",
        }]
    return JSONResponse(body)


@router.post(JOURNAL_PATH)
async def journal_call(request: Request):
    """Record one COMPLETED call against the client on the other end of it.

    Idempotent: a PBX retry returns the original message id with ``duplicate: true`` and writes
    nothing. A call whose number does not resolve to exactly one active client is ACCEPTED and
    not journaled (``journaled: false``) rather than rejected — there is nothing for the PBX to
    retry, and a 4xx would make it keep trying.
    """
    refusal, payload = await _authenticate_and_read(request)
    if refusal is not None:
        return refusal

    number = payload.get("number") or payload.get("Number")
    masked = mask_number(number)
    slug = f"3cx-{threecx_config.instance_slug()}"

    try:
        direction = call_journal.parse_direction(payload.get("call_type"))
        started_at = call_journal.parse_started_at(payload.get("started_at_utc"))
        duration_seconds = call_journal.parse_duration(payload.get("duration"))
    except call_journal.CallJournalError as exc:
        logger.info("3CX journal refused number=%s: %s", masked, exc)
        return _unprocessable(str(exc))

    agent = (str(payload.get("agent") or "").strip() or None)
    call_id = (str(payload.get("call_id") or "").strip() or None)
    claimed_entity = (str(payload.get("entity_id") or "").strip() or None)

    with engine.begin() as conn:
        match = threecx_lookup.lookup_by_number(conn, number)

        if not match.found:
            # Unknown or ambiguous caller: nothing to file this call against. Recorded in the
            # audit chain so the gap is visible, but no conversation is created — an unanchored
            # call belongs in a review decision, not in a thread nobody owns.
            write_audit_event(
                action="threecx.call_unmatched", entity_type="person", outcome="no_match",
                metadata={"number_masked": masked, "match_count": match.match_count,
                          "ambiguous": match.ambiguous, "direction": direction},
                conn=conn, **_context(request),
            )
            logger.info("3CX journal skipped (unmatched) number=%s matches=%d",
                        masked, match.match_count)
            return JSONResponse({
                "journaled": False,
                "reason": "ambiguous_match" if match.ambiguous else "no_match",
                "match_count": match.match_count,
            })

        if claimed_entity is not None and claimed_entity != str(match.person_id):
            # The PBX echoed back a client id that is not who this number belongs to now. Either
            # the record changed between the lookup and the journal, or the id was not ours.
            # Either way, filing the call would attribute a client's call to someone else.
            write_audit_event(
                action="threecx.call_entity_mismatch", entity_type="person",
                entity_id=match.person_id, outcome="denied",
                metadata={"number_masked": masked, "direction": direction},
                conn=conn, **_context(request),
            )
            logger.warning("3CX journal refused number=%s: entity id does not match the number",
                           masked)
            return _unprocessable(
                "The supplied entity_id is not the client this number resolves to. "
                "Refusing to journal the call against a different client.")

        event = call_journal.CallEvent(
            provider_slug=slug, direction=direction, counterparty_number=str(number),
            started_at=started_at, person_id=match.person_id, agent=agent,
            duration_seconds=duration_seconds, household_id=match.household_id, call_id=call_id,
        )
        try:
            message_id, created = call_journal.journal_call(conn, event)
        except call_journal.CallJournalError as exc:
            logger.info("3CX journal refused number=%s: %s", masked, exc)
            return _unprocessable(str(exc))

        write_audit_event(
            action="threecx.call_journaled", entity_type="communication_message",
            entity_id=message_id, outcome="success" if created else "duplicate",
            metadata={"number_masked": masked, "direction": direction, "person_id": match.person_id,
                      "duration_seconds": duration_seconds,
                      "identity_kind": event.identity_kind},
            conn=conn, **_context(request),
        )

    logger.info("3CX call journaled number=%s direction=%s message_id=%s duplicate=%s",
                masked, direction, message_id, not created)
    return JSONResponse({"journaled": True, "created": created, "duplicate": not created,
                         "message_id": message_id, "person_id": match.person_id})
