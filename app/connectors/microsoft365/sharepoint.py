from typing import Any, Dict, List, Optional

from app.connectors.microsoft365.graph import MicrosoftGraphClient


def get_site_by_path(
    hostname: str,
    site_path: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    normalized_path = site_path.strip("/")

    return graph.get(
        f"/sites/{hostname}:/{normalized_path}"
    )


def list_site_drives(
    site_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/sites/{site_id}/drives"
    )

    return response.get("value", [])


def list_drive_root_items(
    drive_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/drives/{drive_id}/root/children",
        params={
            "$select": (
                "id,name,size,webUrl,file,folder,"
                "createdDateTime,lastModifiedDateTime"
            ),
            "$orderby": "name",
        },
    )

    return response.get("value", [])


def list_drive_folder_items(
    drive_id: str,
    item_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/drives/{drive_id}/items/{item_id}/children",
        params={
            "$select": (
                "id,name,size,webUrl,file,folder,"
                "createdDateTime,lastModifiedDateTime"
            ),
            "$orderby": "name",
        },
    )

    return response.get("value", [])


def get_drive_item(
    drive_id: str,
    item_id: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> Dict[str, Any]:
    graph = client or MicrosoftGraphClient()

    return graph.get(
        f"/drives/{drive_id}/items/{item_id}",
        params={
            "$select": (
                "id,name,size,webUrl,file,folder,parentReference,"
                "createdDateTime,lastModifiedDateTime"
            )
        },
    )


def search_drive(
    drive_id: str,
    query: str,
    client: Optional[MicrosoftGraphClient] = None,
) -> List[Dict[str, Any]]:
    graph = client or MicrosoftGraphClient()

    response = graph.get(
        f"/drives/{drive_id}/root/search(q='{query}')",
        params={
            "$select": (
                "id,name,size,webUrl,file,folder,parentReference,"
                "createdDateTime,lastModifiedDateTime"
            )
        },
    )

    return response.get("value", [])
