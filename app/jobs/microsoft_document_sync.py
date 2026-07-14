import os
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Optional

import requests
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import (
    engine,
    microsoft_accounts,
    microsoft_document_matching_rules,
    microsoft_documents,
    microsoft_drives,
    people,
)
from app.services.timeline import add_timeline_event


GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def normalize_email(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def normalize_text(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def parse_datetime(value: Optional[str]):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def drive_item_external_id(drive_id: str, item_id: str) -> str:
    return f"microsoft-drive-item-{drive_id}-{item_id}"


def _identity_email(item: Mapping[str, Any], field: str) -> str:
    identity = item.get(field, {}).get("user", {})
    return normalize_email(identity.get("email") or identity.get("userPrincipalName"))


def match_drive_item(
    item: Mapping[str, Any],
    people_rows: Iterable[Mapping[str, Any]],
    rules: Iterable[Mapping[str, Any]],
) -> tuple[Optional[int], Optional[str]]:
    name = item.get("name") or ""
    parent_path = item.get("parentReference", {}).get("path") or ""
    created_by = _identity_email(item, "createdBy")
    modified_by = _identity_email(item, "lastModifiedBy")
    search_text = " ".join((name.lower(), parent_path.lower(), created_by, modified_by))

    for rule in sorted(
        rules,
        key=lambda row: (
            int(row.get("priority", 100)),
            int(row.get("id", 0)),
        ),
    ):
        pattern = (rule.get("pattern") or "").strip().lower()
        rule_type = rule.get("rule_type")
        target = {
            "filename": name.lower(),
            "folder": parent_path.lower(),
            "email": " ".join((created_by, modified_by)),
            "metadata": search_text,
        }.get(rule_type, search_text)

        if pattern and pattern in target:
            return int(rule["person_id"]), f"rule:{rule_type}"

    matches: dict[int, str] = {}
    normalized_parent = normalize_text(parent_path)

    for person in people_rows:
        person_id = int(person["id"])
        email = normalize_email(
            person.get("normalized_email") or person.get("primary_email")
        )
        full_name = normalize_text(person.get("full_name"))

        if email and email in (created_by, modified_by):
            matches[person_id] = "metadata_email"
        elif email and email in search_text:
            matches[person_id] = "embedded_email"
        elif full_name and full_name in normalized_parent:
            matches[person_id] = "folder_name"

    if len(matches) == 1:
        person_id, method = next(iter(matches.items()))
        return person_id, method

    return None, None


def process_drive_items(
    drive: Mapping[str, Any],
    items: Iterable[Mapping[str, Any]],
    *,
    people_rows: Iterable[Mapping[str, Any]],
    rules: Iterable[Mapping[str, Any]],
    store: Callable[..., Any],
    publish: Callable[..., Any],
) -> dict[str, int]:
    reviewed = stored = matched = unmatched = deleted = published = 0
    people_list = list(people_rows)
    rules_list = list(rules)
    drive_id = str(drive["id"])

    for item in items:
        reviewed += 1
        item_id = item.get("id")

        if not item_id:
            continue

        if item.get("deleted"):
            store(drive=drive, item=item, person_id=None, match_method=None)
            deleted += 1
            continue

        if not item.get("file"):
            continue

        person_id, match_method = match_drive_item(item, people_list, rules_list)
        store(
            drive=drive,
            item=item,
            person_id=person_id,
            match_method=match_method,
        )
        stored += 1

        if person_id is None:
            unmatched += 1
            continue

        matched += 1
        metadata = {
            "microsoft_drive_id": drive_id,
            "microsoft_item_id": item_id,
            "drive_name": drive.get("name"),
            "source_type": drive.get("source_type"),
            "name": item.get("name"),
            "mime_type": item.get("file", {}).get("mimeType"),
            "size_bytes": item.get("size") or 0,
            "web_url": item.get("webUrl"),
            "parent_path": item.get("parentReference", {}).get("path"),
            "match_method": match_method,
        }
        publish(
            person_id=person_id,
            source="microsoft",
            event_type="microsoft_document",
            title="Microsoft Document Updated",
            summary=item.get("name") or "Microsoft document",
            event_time=(
                parse_datetime(item.get("lastModifiedDateTime"))
                or datetime.now(timezone.utc)
            ),
            external_id=drive_item_external_id(drive_id, str(item_id)),
            event_metadata=metadata,
        )
        published += 1

    return {
        "items_reviewed": reviewed,
        "documents_stored": stored,
        "matched_documents": matched,
        "unmatched_documents": unmatched,
        "deleted_items": deleted,
        "published_events": published,
    }


def _graph_pages(url: str, access_token: str, params=None):
    items: list[dict[str, Any]] = []
    delta_link = None

    while url:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            params=params,
            timeout=30,
        )
        if response.status_code == 401:
            raise RuntimeError("Microsoft access token expired; reconnect Microsoft 365.")
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("value", []))
        url = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink") or delta_link
        params = None

    return items, delta_link


def discover_drives(access_token: str) -> list[dict[str, Any]]:
    drives, _ = _graph_pages(f"{GRAPH_BASE_URL}/me/drives", access_token)
    discovered = [{**drive, "source_type": "onedrive", "site_id": None} for drive in drives]

    site_ids = [
        value.strip()
        for value in os.getenv("MICROSOFT_SHAREPOINT_SITE_IDS", "").split(",")
        if value.strip()
    ]
    for site_id in site_ids:
        site_drives, _ = _graph_pages(
            f"{GRAPH_BASE_URL}/sites/{site_id}/drives", access_token
        )
        discovered.extend(
            {**drive, "source_type": "sharepoint", "site_id": site_id}
            for drive in site_drives
        )

    return list({str(drive["id"]): drive for drive in discovered}.values())


def store_microsoft_document(*, drive, item, person_id, match_method):
    drive_id = str(drive["id"])
    item_id = str(item["id"])

    if item.get("deleted"):
        with engine.begin() as connection:
            connection.execute(
                microsoft_documents.update()
                .where(
                    microsoft_documents.c.microsoft_drive_id == drive_id,
                    microsoft_documents.c.microsoft_item_id == item_id,
                )
                .values(deleted=True, updated_at=datetime.now(timezone.utc))
            )
        return

    values = {
        "microsoft_drive_id": drive_id,
        "microsoft_item_id": item_id,
        "person_id": person_id,
        "name": item.get("name") or "Microsoft document",
        "mime_type": item.get("file", {}).get("mimeType"),
        "size_bytes": item.get("size") or 0,
        "web_url": item.get("webUrl"),
        "parent_path": item.get("parentReference", {}).get("path"),
        "created_at_microsoft": parse_datetime(item.get("createdDateTime")),
        "modified_at_microsoft": parse_datetime(item.get("lastModifiedDateTime")),
        "created_by_email": _identity_email(item, "createdBy"),
        "modified_by_email": _identity_email(item, "lastModifiedBy"),
        "match_method": match_method,
        "status": "matched" if person_id else "pending",
        "deleted": False,
        "raw_metadata": {
            key: value
            for key, value in item.items()
            if key != "@microsoft.graph.downloadUrl"
        },
    }
    if person_id is None:
        update_values = {
            **values,
            "person_id": microsoft_documents.c.person_id,
            "match_method": microsoft_documents.c.match_method,
            "status": case(
                (microsoft_documents.c.status == "pending", "pending"),
                else_=microsoft_documents.c.status,
            ),
            "updated_at": datetime.now(timezone.utc),
        }
    else:
        update_values = {**values, "updated_at": datetime.now(timezone.utc)}

    statement = (
        pg_insert(microsoft_documents)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_microsoft_document_drive_item",
            set_=update_values,
        )
    )
    with engine.begin() as connection:
        connection.execute(statement)


def sync_microsoft_documents() -> dict[str, int]:
    with engine.connect() as connection:
        account = connection.execute(
            select(microsoft_accounts)
            .order_by(microsoft_accounts.c.updated_at.desc())
            .limit(1)
        ).mappings().one_or_none()
        people_rows = connection.execute(
            select(people.c.id, people.c.full_name, people.c.primary_email, people.c.normalized_email)
        ).mappings().all()
        rules = connection.execute(
            select(microsoft_document_matching_rules)
            .where(microsoft_document_matching_rules.c.active.is_(True))
            .order_by(microsoft_document_matching_rules.c.priority)
        ).mappings().all()

    if account is None or not account["access_token"]:
        raise RuntimeError("No Microsoft 365 account is connected.")
    if account["expires_at"] and account["expires_at"] <= datetime.now(timezone.utc):
        raise RuntimeError("Microsoft access token expired; reconnect Microsoft 365.")

    access_token = account["access_token"]
    totals = {
        "drives_synced": 0,
        "items_reviewed": 0,
        "documents_stored": 0,
        "matched_documents": 0,
        "unmatched_documents": 0,
        "deleted_items": 0,
        "published_events": 0,
    }

    for drive in discover_drives(access_token):
        drive_id = str(drive["id"])
        with engine.connect() as connection:
            existing = connection.execute(
                select(microsoft_drives).where(
                    microsoft_drives.c.microsoft_drive_id == drive_id
                )
            ).mappings().one_or_none()
        delta_url = (
            existing["delta_link"]
            if existing and existing["delta_link"]
            else f"{GRAPH_BASE_URL}/drives/{drive_id}/root/delta"
        )
        items, delta_link = _graph_pages(delta_url, access_token)
        result = process_drive_items(
            drive,
            items,
            people_rows=people_rows,
            rules=rules,
            store=store_microsoft_document,
            publish=add_timeline_event,
        )
        totals["drives_synced"] += 1
        for key, value in result.items():
            totals[key] += value

        drive_values = {
            "microsoft_drive_id": drive_id,
            "name": drive.get("name"),
            "drive_type": drive.get("driveType"),
            "source_type": drive.get("source_type"),
            "site_id": drive.get("site_id"),
            "web_url": drive.get("webUrl"),
            "delta_link": delta_link,
            "last_synced_at": datetime.now(timezone.utc),
        }
        with engine.begin() as connection:
            connection.execute(
                pg_insert(microsoft_drives)
                .values(**drive_values)
                .on_conflict_do_update(
                    index_elements=[microsoft_drives.c.microsoft_drive_id],
                    set_={**drive_values, "updated_at": datetime.now(timezone.utc)},
                )
            )

    return totals


if __name__ == "__main__":
    print(sync_microsoft_documents())
