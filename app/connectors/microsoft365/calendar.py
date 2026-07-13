from typing import Any, Dict, List, Optional

from app.connectors.microsoft365.graph import MicrosoftGraphClient


DEFAULT_EVENT_FIELDS = [
    "id",
    "subject",
    "start",
    "end",
    "location",
    "organizer",
    "attendees",
    "isOnlineMeeting",
    "onlineMeeting",
    "webLink",
    "bodyPreview",
]


def list_user_events(
    user_id: str,
    start_datetime: str,
    end_datetime: str,
    top: int = 50,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/users/{user_id}/calendarView",
        params={
            "startDateTime": start_datetime,
            "endDateTime": end_datetime,
            "$top": top,
            "$select": ",".join(DEFAULT_EVENT_FIELDS),
            "$orderby": "start/dateTime",
        },
    )

    return response.get("value", [])


def get_user_event(
    user_id: str,
    event_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    return graph.get(
        f"/users/{user_id}/events/{event_id}",
        params={
            "$select": ",".join(
                DEFAULT_EVENT_FIELDS
                + [
                    "body",
                    "categories",
                    "createdDateTime",
                    "lastModifiedDateTime",
                ]
            )
        },
    )


def create_user_event(
    user_id: str,
    subject: str,
    start_datetime: str,
    end_datetime: str,
    timezone_name: str,
    attendee_addresses: Optional[List[str]] = None,
    location_name: Optional[str] = None,
    body: Optional[str] = None,
    create_online_meeting: bool = False,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    payload: Dict[str, Any] = {
        "subject": subject,
        "start": {
            "dateTime": start_datetime,
            "timeZone": timezone_name,
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": timezone_name,
        },
        "attendees": [
            {
                "emailAddress": {
                    "address": address,
                },
                "type": "required",
            }
            for address in (attendee_addresses or [])
        ],
        "isOnlineMeeting": create_online_meeting,
    }

    if location_name:
        payload["location"] = {
            "displayName": location_name,
        }

    if body:
        payload["body"] = {
            "contentType": "HTML",
            "content": body,
        }

    if create_online_meeting:
        payload["onlineMeetingProvider"] = "teamsForBusiness"

    return graph.post(
        f"/users/{user_id}/events",
        payload=payload,
    )
