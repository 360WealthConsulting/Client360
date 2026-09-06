import logging
from datetime import UTC, datetime
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    microsoft_unmatched_messages,
    people,
)
from app.services.communications import email_ingest
from app.services.microsoft_identity import (
    account_by_id,
    connected_accounts,
    get_microsoft_access_token,
    record_sync_health,
)
from app.services.timeline import add_timeline_event

logger = logging.getLogger(__name__)

GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _parse_graph_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def sync_recent_mail(top: int = 50, *, account_id: int | None = None) -> dict[str, Any]:
    """Ingest recent mail from the connected Microsoft account(s).

    This is a BACKGROUND job with no authenticated principal -- the scheduler
    (``jobs.scheduler.run_microsoft_mail_sync``), the automation dispatcher
    (``m365_mail_sync``) and the module's ``__main__`` all call it unattended -- so it cannot bind to
    a principal the way the mail ROUTE now does. It used to resolve its mailbox with
    ``ORDER BY updated_at DESC LIMIT 1``, meaning whichever account happened to reconnect last was
    the only one ever synced and the others silently went stale.

    Account selection is now explicit in both directions: pass ``account_id`` to sync exactly one
    named mailbox, or omit it to enumerate EVERY connected account in a deterministic order. Neither
    path picks an account by recency.

    A per-account failure is recorded on that account's sync health and does not abort the run, so
    one expired connection cannot starve every other mailbox. If every account fails the first error
    is re-raised, preserving the "sync failed" signal the scheduler logs today.
    """
    # (D.32) Sync ELIGIBILITY (behavior) is decided by the centralized Runtime Policy Engine
    # (microsoft365.sync_eligibility), which consumes the runtime engine — behavior-preserving: with no
    # runtime feature ``microsoft365.sync`` defined, the legacy default (enabled) is used, so sync runs
    # as before. Provider init / OAuth / credential loading are unaffected (infrastructure).
    from app.services.policy import evaluate as policy_evaluate
    if not policy_evaluate("microsoft365.sync_eligibility").decision:
        return {"skipped": True, "reason": "runtime_disabled"}

    if account_id is not None:
        named = account_by_id(account_id)
        # A named mailbox that does not exist fails closed. It never degrades to "some other account".
        accounts = [named] if named is not None else []
    else:
        accounts = connected_accounts()

    if not accounts:
        raise RuntimeError(
            "No Microsoft 365 account is connected."
        )

    with engine.connect() as connection:
        person_rows = connection.execute(
            select(
                people.c.id,
                people.c.household_id,
                people.c.primary_email,
                people.c.normalized_email,
            )
        ).mappings().all()

    # ``{normalized email: (person_id, household_id)}``. The household comes along so an email can be
    # anchored to the family when several members are addressed — see communications.email_ingest.
    person_by_email: dict[str, tuple[int, int | None]] = {}

    for person in person_rows:
        for candidate in (
            person["normalized_email"],
            person["primary_email"],
        ):
            normalized = _normalize_email(candidate)

            if normalized:
                person_by_email[normalized] = (person["id"], person["household_id"])

    totals = {"messages_reviewed": 0, "matched_messages": 0,
              "unmatched_messages": 0, "published_events": 0}
    first_error: Exception | None = None
    succeeded = 0

    for account in accounts:
        try:
            result = _sync_one_account(account, top, person_by_email)
        except Exception as exc:            # one mailbox must not starve the rest
            if first_error is None:
                first_error = exc
            continue
        succeeded += 1
        for key in totals:
            totals[key] += result[key]

    if succeeded == 0 and first_error is not None:
        raise first_error

    return {**totals, "accounts_synced": succeeded, "accounts_total": len(accounts)}


def _sync_one_account(account, top: int, person_by_email: dict[str, int]) -> dict[str, int]:
    """Ingest one mailbox. Records sync health on that account, ok or error."""
    try:
        access_token = get_microsoft_access_token(account)
    except Exception as exc:
        record_sync_health(account["id"], "error", exc)
        raise

    try:
        return _ingest_messages(account, access_token, top, person_by_email)
    except Exception as exc:
        record_sync_health(account["id"], "error", exc)
        raise


def _ingest_messages(account, access_token: str, top: int,
                     person_by_email: dict[str, int]) -> dict[str, int]:
    response = requests.get(
        GRAPH_MESSAGES_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={
            "$top": str(top),
            # conversationId / internetMessageId / recipients are what make an email addressable and
            # de-duplicable; the read-only preview route has selected them against the live API since
            # it shipped, so this adds no permission and no new call. `sender` accompanies `from`
            # because a delegated send sets them differently.
            "$select": (
                "id,subject,from,sender,toRecipients,ccRecipients,receivedDateTime,"
                "bodyPreview,webLink,hasAttachments,isRead,"
                "conversationId,internetMessageId"
            ),
            "$orderby": "receivedDateTime desc",
        },
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Microsoft rejected the access token. "
            "Reconnect Microsoft 365 before syncing."
        )

    response.raise_for_status()

    messages = response.json().get("value", [])

    matched = 0
    unmatched = 0
    published = 0
    normalized = 0

    owner_address = _normalize_email(account.get("email"))

    for message in messages:
        # One resolution per message, shared by every branch below: direction, the counterparty
        # addresses, and which client (if any) this email belongs to. Ambiguity is preserved, never
        # resolved by guessing — see communications.email_ingest.resolve_match.
        match = email_ingest.resolve_match(message, person_by_email, owner_address)
        sender_address = match.sender_address
        sender_name = match.sender_name

        # The TIMELINE contract is unchanged: an event is written exactly when the SENDER is a known
        # client, exactly as before. Recipient matching widens what can be NORMALIZED, never what
        # appears on a client's timeline, so no new user-visible event is introduced by this batch.
        sender_person = person_by_email.get(sender_address)
        person_id = sender_person[0] if sender_person else None

        if person_id is None:
            # A message the mailbox owner SENT is this firm's own copy, not an unrecognised inbound
            # sender: /me/messages is not folder-scoped, so Sent Items arrive here too. It is still
            # normalized below when it names a client, but it no longer pollutes the review queue.
            if match.direction != email_ingest.OUTBOUND:
                unmatched += 1

                message_id = message.get("id")

                if message_id:
                    statement = (
                        pg_insert(microsoft_unmatched_messages)
                        .values(
                            microsoft_message_id=message_id,
                            sender_name=sender_name,
                            sender_address=sender_address,
                            subject=message.get("subject"),
                            body_preview=message.get("bodyPreview"),
                            received_at=_parse_graph_datetime(
                                message.get("receivedDateTime")
                            ),
                            web_link=message.get("webLink"),
                            has_attachments=bool(
                                message.get("hasAttachments")
                            ),
                            status="pending",
                        )
                        .on_conflict_do_update(
                            constraint=(
                                "uq_microsoft_unmatched_message_id"
                            ),
                            set_={
                                "sender_name": sender_name,
                                "sender_address": sender_address,
                                "subject": message.get("subject"),
                                "body_preview": message.get(
                                    "bodyPreview"
                                ),
                                "received_at": (
                                    _parse_graph_datetime(
                                        message.get(
                                            "receivedDateTime"
                                        )
                                    )
                                ),
                                "web_link": message.get("webLink"),
                                "has_attachments": bool(
                                    message.get(
                                        "hasAttachments"
                                    )
                                ),
                                "updated_at": datetime.now(
                                    UTC
                                ),
                            },
                        )
                    )

                    with engine.begin() as connection:
                        connection.execute(statement)

            # Still normalize: an email may name a client as a RECIPIENT even when its sender is
            # unknown, and the firm's own outbound copies name one too. This writes no timeline
            # event, so the client's history is untouched by it.
            if _normalize(account, message, match):
                normalized += 1

            continue

        matched += 1

        message_id = message.get("id")

        if not message_id:
            continue

        subject = message.get("subject") or "(No subject)"
        preview = (message.get("bodyPreview") or "").strip()

        if len(preview) > 500:
            preview = preview[:497] + "..."

        # ONE transaction for the timeline event and the canonical rows, so the two can never
        # disagree about whether this email was ingested. `add_timeline_event` already accepts a
        # connection; the event itself — source, event_type, external_id, summary — is unchanged, so
        # nothing user-visible moves and the email still produces exactly one timeline row.
        with engine.begin() as connection:
            add_timeline_event(
                person_id=person_id,
                source="microsoft",
                event_type="email_received",
                title=subject,
                summary=preview or None,
                event_time=_parse_graph_datetime(
                    message.get("receivedDateTime")
                ),
                external_id=f"outlook-message-{message_id}",
                event_metadata={
                    "sender_name": sender_name,
                    "sender_address": sender_address,
                    "web_link": message.get("webLink"),
                    "has_attachments": bool(
                        message.get("hasAttachments")
                    ),
                    "is_read": bool(message.get("isRead")),
                    "microsoft_message_id": message_id,
                },
                conn=connection,
            )
            if email_ingest.normalize_email(
                connection, account=account, message=message, match=match
            ) is not None:
                normalized += 1

        published += 1

    record_sync_health(account["id"], "ok")
    return {
        "messages_reviewed": len(messages),
        "matched_messages": matched,
        "unmatched_messages": unmatched,
        "published_events": published,
        "normalized_messages": normalized,
    }


def _normalize(account, message, match) -> bool:
    """Normalize one message on its own transaction. Returns whether a record was written.

    Used for the branches that write no timeline event, so there is nothing to keep atomic with.
    Failure-isolated: normalization is derived data, and it must never stop the mail sync that
    populates the review queue and the timeline.
    """
    if not match.anchored:
        return False
    try:
        with engine.begin() as connection:
            return email_ingest.normalize_email(
                connection, account=account, message=message, match=match
            ) is not None
    except Exception:
        logger.exception("Communication normalization failed for one message; sync continues.")
        return False


if __name__ == "__main__":
    result = sync_recent_mail()

    print("Microsoft mail sync complete.")

    for key, value in result.items():
        print(f"{key}: {value}")
