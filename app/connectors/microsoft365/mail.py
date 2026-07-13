from typing import Any, Dict, List, Optional

from app.connectors.microsoft365.graph import MicrosoftGraphClient


DEFAULT_MESSAGE_FIELDS = [
    "id",
    "subject",
    "from",
    "toRecipients",
    "ccRecipients",
    "receivedDateTime",
    "sentDateTime",
    "bodyPreview",
    "webLink",
    "hasAttachments",
]


def list_user_messages(
    user_id: str,
    top: int = 25,
    search: Optional[str] = None,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    params: Dict[str, Any] = {
        "$top": top,
        "$select": ",".join(DEFAULT_MESSAGE_FIELDS),
        "$orderby": "receivedDateTime desc",
    }

    if search:
        params["$search"] = f'"{search}"'
        params["$count"] = "true"

    response = graph.get(
        f"/users/{user_id}/messages",
        params=params,
    )

    return response.get("value", [])


def get_user_message(
    user_id: str,
    message_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    return graph.get(
        f"/users/{user_id}/messages/{message_id}",
        params={
            "$select": ",".join(
                DEFAULT_MESSAGE_FIELDS
                + [
                    "body",
                    "internetMessageId",
                    "conversationId",
                ]
            )
        },
    )


def send_user_email(
    user_id: str,
    subject: str,
    body: str,
    to_addresses: List[str],
    cc_addresses: Optional[List[str]] = None,
    save_to_sent_items: bool = True,
    client: Optional[MicrosoftGraphClient] = None,
) -> None:
    graph = client or MicrosoftGraphClient()

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": address,
                    }
                }
                for address in to_addresses
            ],
            "ccRecipients": [
                {
                    "emailAddress": {
                        "address": address,
                    }
                }
                for address in (cc_addresses or [])
            ],
        },
        "saveToSentItems": save_to_sent_items,
    }

    graph.post(
        f"/users/{user_id}/sendMail",
        payload=payload,
    )
