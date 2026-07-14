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
    # Uploaded by an advisor into a "Jane Client" folder. Under deterministic
    # matching (Sprint 5.4 / H13) this must NOT auto-assign to Jane.
    return {
        "id": "item-123",
        "name": "2026 Tax Return.pdf",
        "size": 4096,
        "webUrl": "https://contoso.sharepoint.com/item-123",
        "file": {"mimeType": "application/pdf"},
        "parentReference": {"driveId": "drive-1", "path": "/drives/drive-1/root:/Clients/Jane Client"},
        "createdDateTime": "2026-07-01T12:00:00Z",
        "lastModifiedDateTime": "2026-07-12T14:00:00Z",
        "createdBy": {"user": {"email": "advisor@example.com"}},
        "lastModifiedBy": {"user": {"email": "advisor@example.com"}},
    }


def client_item():
    # Uploaded by the client themselves (exact email identity) -> deterministic.
    item = sample_item()
    item["createdBy"] = {"user": {"email": "jane@example.com"}}
    item["lastModifiedBy"] = {"user": {"email": "jane@example.com"}}
    return item


def test_folder_name_substring_no_longer_auto_matches():
    # H13: a document merely sitting in a folder named after a client, uploaded by
    # someone else, must not be auto-assigned. Deterministic matcher returns none.
    assert match_drive_item(sample_item(), PEOPLE, []) == (None, None)


def test_exact_uploader_email_matches():
    assert match_drive_item(client_item(), PEOPLE, []) == (42, "metadata_email")


def test_exact_email_rule_matches():
    rules = [{"person_id": 99, "rule_type": "email_exact", "pattern": "advisor@example.com", "priority": 1, "id": 1}]
    assert match_drive_item(sample_item(), PEOPLE, rules) == (99, "rule:email_exact")


def test_exact_drive_id_rule_matches():
    rules = [{"person_id": 77, "rule_type": "drive_id", "pattern": "drive-1", "priority": 1, "id": 1}]
    assert match_drive_item(sample_item(), PEOPLE, rules) == (77, "rule:drive_id")


def test_legacy_substring_rule_is_ignored():
    # Legacy free-text rule types (filename/folder/email/metadata) are inert; they
    # must not produce a substring match.
    rules = [{"person_id": 99, "rule_type": "filename", "pattern": "tax return", "priority": 1, "id": 1}]
    assert match_drive_item(sample_item(), PEOPLE, rules) == (None, None)


def test_process_publishes_matched_document_with_stable_item_key():
    stored = []
    timeline = {}

    def publish(**values):
        timeline[values["external_id"]] = values

    for _ in range(2):
        result = process_drive_items(
            {"id": "drive-1", "name": "Documents", "source_type": "sharepoint"},
            [client_item()],
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
    stored = []
    published = []

    result = process_drive_items(
        {"id": "drive-1", "source_type": "onedrive"},
        [sample_item()],  # advisor uploader, no rules -> unmatched
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
