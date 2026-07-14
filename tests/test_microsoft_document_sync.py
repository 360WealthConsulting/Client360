from app.jobs.microsoft_document_sync import (
    drive_item_external_id,
    match_drive_item,
    process_drive_items,
)


PEOPLE = [
    {
        "id": 42,
        "full_name": "Jane Client",
        "primary_email": "jane@example.com",
        "normalized_email": "jane@example.com",
    }
]


def sample_item():
    return {
        "id": "item-123",
        "name": "2026 Tax Return.pdf",
        "size": 4096,
        "webUrl": "https://contoso.sharepoint.com/item-123",
        "file": {"mimeType": "application/pdf"},
        "parentReference": {
            "path": "/drives/drive-1/root:/Clients/Jane Client"
        },
        "createdDateTime": "2026-07-01T12:00:00Z",
        "lastModifiedDateTime": "2026-07-12T14:00:00Z",
        "createdBy": {"user": {"email": "advisor@example.com"}},
        "lastModifiedBy": {"user": {"email": "advisor@example.com"}},
    }


def test_matches_document_by_client_folder_name():
    assert match_drive_item(sample_item(), PEOPLE, []) == (42, "folder_name")


def test_configurable_rule_takes_priority():
    rules = [
        {
            "person_id": 99,
            "rule_type": "filename",
            "pattern": "tax return",
            "priority": 1,
        }
    ]

    assert match_drive_item(sample_item(), PEOPLE, rules) == (
        99,
        "rule:filename",
    )


def test_process_publishes_matched_document_with_stable_item_key():
    stored = []
    timeline = {}

    def publish(**values):
        timeline[values["external_id"]] = values

    for _ in range(2):
        result = process_drive_items(
            {"id": "drive-1", "name": "Documents", "source_type": "sharepoint"},
            [sample_item()],
            people_rows=PEOPLE,
            rules=[],
            store=lambda **values: stored.append(values),
            publish=publish,
        )

    key = drive_item_external_id("drive-1", "item-123")
    assert list(timeline) == [key]
    assert timeline[key]["person_id"] == 42
    assert timeline[key]["event_metadata"]["web_url"].endswith("item-123")
    assert result["published_events"] == 1


def test_unmatched_document_is_stored_for_review_without_timeline_event():
    item = sample_item()
    item["parentReference"]["path"] = "/drives/drive-1/root:/Unsorted"
    stored = []
    published = []

    result = process_drive_items(
        {"id": "drive-1", "source_type": "onedrive"},
        [item],
        people_rows=PEOPLE,
        rules=[],
        store=lambda **values: stored.append(values),
        publish=lambda **values: published.append(values),
    )

    assert result["unmatched_documents"] == 1
    assert stored[0]["person_id"] is None
    assert published == []


def test_deleted_drive_item_is_marked_without_publishing():
    stored = []
    published = []

    result = process_drive_items(
        {"id": "drive-1", "source_type": "onedrive"},
        [{"id": "item-123", "deleted": {"state": "deleted"}}],
        people_rows=PEOPLE,
        rules=[],
        store=lambda **values: stored.append(values),
        publish=lambda **values: published.append(values),
    )

    assert result["deleted_items"] == 1
    assert stored[0]["item"]["deleted"]
    assert published == []
