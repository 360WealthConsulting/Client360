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


# --- system/cache library exclusion (PersonalCacheLibrary only) ---------------------------

def test_is_system_library_matches_personal_cache_case_insensitively():
    from app.jobs.microsoft_document_sync import is_system_library
    assert is_system_library("PersonalCacheLibrary")
    assert is_system_library("personalcachelibrary")
    assert is_system_library("  PERSONALCACHELIBRARY  ")


def test_is_system_library_allows_content_libraries():
    from app.jobs.microsoft_document_sync import is_system_library
    # Legitimate libraries (and empty/None) are NOT treated as system libraries.
    for name in ("Documents", "Shared Documents", "Client Files", "Archive 2023", None, ""):
        assert not is_system_library(name)


def _stub_scope_site(monkeypatch, site_id="SITE1"):
    """Point the SharePoint scope at one site id so discover_drives enumerates /sites/{id}/drives."""
    import types

    from app.services import policy
    monkeypatch.setattr(policy, "evaluate", lambda *a, **k: types.SimpleNamespace(decision=site_id))


def _stub_graph(monkeypatch, me_drives, site_drives, site_id="SITE1"):
    import app.jobs.microsoft_document_sync as mds

    def pages(url, access_token, params=None):
        if "/me/drives" in url:
            return (me_drives, None)
        if f"/sites/{site_id}/drives" in url:
            return (site_drives, None)
        return ([], None)
    monkeypatch.setattr(mds, "_graph_pages", pages)


def test_discover_drives_excludes_personal_onedrive_and_cache(monkeypatch):
    # Personal OneDrive (/me/drives -> source_type='onedrive') and PersonalCacheLibrary are NOT admitted;
    # only the company SharePoint site library is.
    import app.jobs.microsoft_document_sync as mds
    _stub_scope_site(monkeypatch)
    _stub_graph(monkeypatch,
                me_drives=[{"id": "od-docs", "name": "Documents"},
                           {"id": "od-cache", "name": "PersonalCacheLibrary"}],
                site_drives=[{"id": "sp-docs", "name": "Documents"}])   # 360Data/Shared Documents
    out = mds.discover_drives("token")
    assert {d["id"] for d in out} == {"sp-docs"}              # only the SharePoint site drive admitted
    assert all(d["source_type"] == "sharepoint" for d in out)


def test_discover_drives_retains_sharepoint_site_libraries(monkeypatch):
    # Multiple company SharePoint site libraries are retained; personal OneDrive is excluded.
    import app.jobs.microsoft_document_sync as mds
    _stub_scope_site(monkeypatch)
    _stub_graph(monkeypatch,
                me_drives=[{"id": "od-docs", "name": "Documents"}],
                site_drives=[{"id": "sp-1", "name": "Shared Documents"},
                             {"id": "sp-2", "name": "Archive"}])
    out = mds.discover_drives("token")
    assert {d["id"] for d in out} == {"sp-1", "sp-2"}         # both SharePoint site libraries retained
    assert "od-docs" not in {d["id"] for d in out}            # personal OneDrive excluded


# --- composite site-id parsing -------------------------------------------------------------------
# A Graph composite site id is ``hostname,siteGuid,webGuid`` — it CONTAINS commas. Splitting the
# configured scope on commas turned one valid id into three invalid lookups, and the trailing
# web-guid fragment answered 404, which aborted discovery entirely.

COMPOSITE = "contoso.sharepoint.com,11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222"
COMPOSITE_2 = "contoso.sharepoint.com,33333333-3333-3333-3333-333333333333,44444444-4444-4444-4444-444444444444"


def test_parse_site_ids_keeps_one_composite_whole():
    from app.jobs.microsoft_document_sync import parse_site_ids
    assert parse_site_ids(COMPOSITE) == [COMPOSITE]          # never split on internal commas


def test_parse_site_ids_supports_multiple_sites_via_json_array():
    # The configuration system's existing ``json`` value type is the unambiguous outer format.
    from app.jobs.microsoft_document_sync import parse_site_ids
    assert parse_site_ids([COMPOSITE, COMPOSITE_2]) == [COMPOSITE, COMPOSITE_2]   # list from a json item
    assert parse_site_ids(f'["{COMPOSITE}", "{COMPOSITE_2}"]') == [COMPOSITE, COMPOSITE_2]  # still encoded


def test_parse_site_ids_handles_malformed_input():
    from app.jobs.microsoft_document_sync import parse_site_ids
    assert parse_site_ids(None) == []                        # unset
    assert parse_site_ids("") == []                          # blank
    assert parse_site_ids("   ") == []                        # whitespace only
    assert parse_site_ids([]) == []                          # empty array
    assert parse_site_ids([" ", ""]) == []                    # array of blanks
    assert parse_site_ids("[not json") == ["[not json"]       # unparseable -> one opaque id, no crash
    assert parse_site_ids('{"a": 1}') == ['{"a": 1}']         # json object -> one opaque id


def test_discover_drives_sends_the_whole_composite_to_graph(monkeypatch):
    # Proves the composite reaches Graph unchanged: exactly one /sites/{id}/drives call, whose URL
    # carries the full id including both internal commas.
    import app.jobs.microsoft_document_sync as mds
    _stub_scope_site(monkeypatch, site_id=COMPOSITE)
    seen = []

    def pages(url, access_token, params=None):
        seen.append(url)
        if "/me/drives" in url:
            return ([], None)
        return ([{"id": "sp-360data", "name": "Documents"}], None)
    monkeypatch.setattr(mds, "_graph_pages", pages)

    out = mds.discover_drives("token")
    site_calls = [u for u in seen if "/sites/" in u]
    assert len(site_calls) == 1                                          # one id -> one lookup
    assert site_calls[0] == f"{mds.GRAPH_BASE_URL}/sites/{COMPOSITE}/drives"
    assert site_calls[0].count(",") == 2                                 # both commas survived
    assert [d["id"] for d in out] == ["sp-360data"]
    assert out[0]["site_id"] == COMPOSITE                                # recorded whole


def test_discover_drives_isolates_a_failing_site(monkeypatch):
    # A 404 (or any error) from one configured site must not discard drives already discovered
    # from the others.
    import requests

    import app.jobs.microsoft_document_sync as mds
    _stub_scope_site(monkeypatch, site_id=[COMPOSITE, COMPOSITE_2])

    def pages(url, access_token, params=None):
        if "/me/drives" in url:
            return ([], None)
        if COMPOSITE_2 in url:
            raise requests.exceptions.HTTPError("404 Client Error: Not Found")
        return ([{"id": "sp-good", "name": "Documents"}], None)
    monkeypatch.setattr(mds, "_graph_pages", pages)

    out = mds.discover_drives("token")
    assert [d["id"] for d in out] == ["sp-good"]              # good site survives the bad one


def test_discover_drives_excludes_personal_onedrive_with_a_composite_scope(monkeypatch):
    # The real production shape: a composite scope plus personal drives that must stay excluded.
    import app.jobs.microsoft_document_sync as mds
    _stub_scope_site(monkeypatch, site_id=COMPOSITE)
    _stub_graph(monkeypatch,
                me_drives=[{"id": "od-docs", "name": "OneDrive"},
                           {"id": "od-cache", "name": "PersonalCacheLibrary"}],
                site_drives=[{"id": "sp-360data", "name": "Documents"}],
                site_id=COMPOSITE)
    out = mds.discover_drives("token")
    assert {d["id"] for d in out} == {"sp-360data"}           # only the team-site library
    assert all(d["source_type"] == "sharepoint" for d in out)
