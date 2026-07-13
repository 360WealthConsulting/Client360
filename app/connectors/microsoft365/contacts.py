from typing import Any, Dict, List, Optional

from app.connectors.microsoft365.graph import MicrosoftGraphClient


DEFAULT_CONTACT_FIELDS = [
    "id",
    "displayName",
    "givenName",
    "surname",
    "emailAddresses",
    "businessPhones",
    "homePhones",
    "mobilePhone",
    "companyName",
    "jobTitle",
    "businessAddress",
    "homeAddress",
    "personalNotes",
]


def list_user_contacts(
    user_id: str,
    top: int = 100,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/users/{user_id}/contacts",
        params={
            "$top": top,
            "$select": ",".join(DEFAULT_CONTACT_FIELDS),
            "$orderby": "displayName",
        },
    )

    return response.get("value", [])


def get_user_contact(
    user_id: str,
    contact_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    return graph.get(
        f"/users/{user_id}/contacts/{contact_id}",
        params={
            "$select": ",".join(DEFAULT_CONTACT_FIELDS)
        },
    )


def create_user_contact(
    user_id: str,
    display_name: str,
    given_name: Optional[str] = None,
    surname: Optional[str] = None,
    email_addresses: Optional[List[str]] = None,
    business_phones: Optional[List[str]] = None,
    mobile_phone: Optional[str] = None,
    company_name: Optional[str] = None,
    job_title: Optional[str] = None,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    payload: Dict[str, Any] = {
        "displayName": display_name,
        "emailAddresses": [
            {
                "address": address,
                "name": display_name,
            }
            for address in (email_addresses or [])
        ],
        "businessPhones": business_phones or [],
    }

    if given_name:
        payload["givenName"] = given_name

    if surname:
        payload["surname"] = surname

    if mobile_phone:
        payload["mobilePhone"] = mobile_phone

    if company_name:
        payload["companyName"] = company_name

    if job_title:
        payload["jobTitle"] = job_title

    return graph.post(
        f"/users/{user_id}/contacts",
        payload=payload,
    )
