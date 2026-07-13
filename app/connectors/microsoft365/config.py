import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv("app/.env")


@dataclass(frozen=True)
class Microsoft365Config:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authority: str
    graph_base_url: str


def get_microsoft365_config() -> Microsoft365Config:
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    client_id = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET", "").strip()

    redirect_uri = os.getenv(
        "MICROSOFT_REDIRECT_URI",
        "http://localhost:8000/microsoft365/callback",
    ).strip()

    missing = [
        name
        for name, value in {
            "MICROSOFT_TENANT_ID": tenant_id,
            "MICROSOFT_CLIENT_ID": client_id,
            "MICROSOFT_CLIENT_SECRET": client_secret,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing Microsoft 365 configuration: "
            + ", ".join(missing)
        )

    return Microsoft365Config(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authority=(
            "https://login.microsoftonline.com/"
            f"{tenant_id}"
        ),
        graph_base_url="https://graph.microsoft.com/v1.0",
    )
