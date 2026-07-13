from typing import Any, Dict

import msal

from app.connectors.microsoft365.config import (
    Microsoft365Config,
    get_microsoft365_config,
)


GRAPH_SCOPES = [
    "https://graph.microsoft.com/.default",
]


class Microsoft365AuthenticationError(RuntimeError):
    pass


def build_confidential_client(
    config: Microsoft365Config = None,
) -> msal.ConfidentialClientApplication:
    resolved_config = config or get_microsoft365_config()

    return msal.ConfidentialClientApplication(
        client_id=resolved_config.client_id,
        authority=resolved_config.authority,
        client_credential=resolved_config.client_secret,
    )


def acquire_application_token() -> str:
    client = build_confidential_client()

    result: Dict[str, Any] = client.acquire_token_for_client(
        scopes=GRAPH_SCOPES,
    )

    access_token = result.get("access_token")

    if access_token:
        return access_token

    error = result.get("error", "unknown_error")
    description = result.get(
        "error_description",
        "Microsoft did not return an access token.",
    )
    correlation_id = result.get("correlation_id", "unavailable")

    raise Microsoft365AuthenticationError(
        f"Microsoft authentication failed: {error}. "
        f"{description} Correlation ID: {correlation_id}"
    )
