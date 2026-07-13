from typing import Any, Dict, Optional

import requests

from app.connectors.microsoft365.auth import (
    acquire_application_token,
)
from app.connectors.microsoft365.config import (
    Microsoft365Config,
    get_microsoft365_config,
)


class MicrosoftGraphError(RuntimeError):
    pass


class MicrosoftGraphClient:
    def __init__(
        self,
        config: Optional[Microsoft365Config] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.config = config or get_microsoft365_config()
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": (
                f"Bearer {acquire_application_token()}"
            ),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        normalized_path = path.lstrip("/")

        return (
            f"{self.config.graph_base_url}/"
            f"{normalized_path}"
        )

    def _handle_response(
        self,
        response: requests.Response,
    ) -> Dict[str, Any]:
        if response.ok:
            if not response.content:
                return {}

            return response.json()

        try:
            error_payload = response.json()
        except ValueError:
            error_payload = {
                "error": {
                    "message": response.text
                    or "Microsoft Graph returned an error."
                }
            }

        raise MicrosoftGraphError(
            "Microsoft Graph request failed "
            f"with status {response.status_code}: "
            f"{error_payload}"
        )

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        response = requests.get(
            self._url(path),
            headers=self._headers(),
            params=params,
            timeout=self.timeout_seconds,
        )

        return self._handle_response(response)

    def post(
        self,
        path: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        response = requests.post(
            self._url(path),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout_seconds,
        )

        return self._handle_response(response)
