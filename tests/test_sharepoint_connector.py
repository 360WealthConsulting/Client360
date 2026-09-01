"""SharePoint content connector — mocked unit tests (Phase 0C).

Brings app/connectors/microsoft365/sharepoint_content.py under test WITHOUT any Graph downloads or network
calls: all HTTP is faked, auth/account lookups are stubbed. Covers auth/config resolution, site/drive
enumeration, nextLink pagination + incremental checkpoint (skip-unchanged), download error handling
(401 → reconnect, transient retry → failure, success), and that the source embeds no secrets/paths.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests

from app.connectors.microsoft365 import sharepoint_content as spc

_SRC = Path(spc.__file__).read_text(encoding="utf-8")


class FakeResp:
    """Serves both the JSON path (_graph_get_json) and the streaming context-manager path
    (_graph_download)."""

    def __init__(self, status_code=200, json_data=None, headers=None, content=b""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self._content = content
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._content), max(1, chunk_size)):
            yield self._content[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail loudly on any un-mocked HTTP, and make retries instant."""
    monkeypatch.setattr(spc.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected network call")))
    monkeypatch.setattr(spc.time, "sleep", lambda *a, **k: None)


def _route(monkeypatch, by_substring):
    """Route requests.get by URL substring to a FakeResp (or a callable returning one)."""
    def _get(url, headers=None, params=None, timeout=None, **kw):
        for sub, resp in by_substring.items():
            if sub in url:
                return resp() if callable(resp) else resp
        raise AssertionError(f"no fake response for URL: {url}")
    monkeypatch.setattr(spc.requests, "get", _get)


# --- auth / config resolution ------------------------------------------------

def test_resolve_site_ids_explicit_env_and_empty(monkeypatch):
    assert spc.resolve_site_ids(["s1", " s2 ", ""]) == ["s1", "s2"]
    monkeypatch.setenv("MICROSOFT_SHAREPOINT_SITE_IDS", "e1, e2")
    assert spc.resolve_site_ids() == ["e1", "e2"]
    monkeypatch.delenv("MICROSOFT_SHAREPOINT_SITE_IDS", raising=False)
    with pytest.raises(RuntimeError, match="site IDs"):
        spc.resolve_site_ids()


def test_staging_root_required_fails_closed(monkeypatch):
    monkeypatch.setattr(spc, "DEFAULT_STAGING_ROOT", None)
    with pytest.raises(RuntimeError, match="staging root"):
        spc.stage_sharepoint_content(site_ids=["s1"], staging_root=None)   # no network reached


def test_acquire_token_wraps_auth_failure_as_reconnect(monkeypatch):
    monkeypatch.setattr(spc, "get_microsoft_access_token",
                        lambda account: (_ for _ in ()).throw(RuntimeError("token expired")))
    with pytest.raises(spc.ReconnectRequired, match="reconnected"):
        spc._acquire_token({"id": 1})


# --- enumeration + pagination ------------------------------------------------

def test_enumerate_site_drives(monkeypatch):
    _route(monkeypatch, {"/sites/S1/drives": FakeResp(json_data={"value": [
        {"id": "d1", "name": "Documents"}, {"id": "d2", "name": "Shared"}]})})
    drives = spc.enumerate_site_drives("S1", "tok")
    assert [d["id"] for d in drives] == ["d1", "d2"]


def test_graph_list_follows_nextlink(monkeypatch):
    page2 = FakeResp(json_data={"value": [{"id": "b"}]})
    page1 = FakeResp(json_data={"value": [{"id": "a"}], "@odata.nextLink": "https://graph/next-page"})
    _route(monkeypatch, {"next-page": page2, "/first": page1})
    items = spc._graph_list("https://graph/first", "tok")
    assert [i["id"] for i in items] == ["a", "b"]                          # both pages concatenated


def test_walk_drive_recurses_folders_and_yields_files_only(monkeypatch):
    # Graph marks folders/files with a NON-empty facet dict (childCount / mimeType) — an empty {} is falsy.
    root = FakeResp(json_data={"value": [
        {"id": "fold1", "folder": {"childCount": 1}},
        {"id": "file1", "file": {"mimeType": "text/plain"}, "name": "a.txt"}]})
    sub = FakeResp(json_data={"value": [
        {"id": "file2", "file": {"mimeType": "text/plain"}, "name": "b.txt"}]})
    _route(monkeypatch, {"/root/children": root, "/items/fold1/children": sub})
    summary = spc.StagingSummary(dry_run=True)
    files = list(spc.walk_drive("d1", "tok", summary))
    assert {f["id"] for f in files} == {"file1", "file2"}                  # files only, folder recursed
    assert summary.files_seen == 2 and summary.folders_walked == 2


# --- transient retry + reconnect on Graph GET --------------------------------

def test_graph_get_json_401_raises_reconnect(monkeypatch):
    _route(monkeypatch, {"/x": FakeResp(status_code=401)})
    with pytest.raises(spc.ReconnectRequired):
        spc._graph_get_json("https://graph/x", "tok")


def test_graph_get_json_retries_5xx_then_succeeds(monkeypatch):
    calls = {"n": 0}
    def _get(url, **kw):
        calls["n"] += 1
        return FakeResp(status_code=500) if calls["n"] == 1 else FakeResp(json_data={"value": [1]})
    monkeypatch.setattr(spc.requests, "get", _get)
    assert spc._graph_get_json("https://graph/y", "tok") == {"value": [1]}
    assert calls["n"] == 2                                                 # retried once


# --- download error handling -------------------------------------------------

def test_download_success_streams_and_hashes(monkeypatch, tmp_path):
    body = b"hello sharepoint" * 100
    _route(monkeypatch, {"/content": FakeResp(status_code=200, content=body)})
    dest = tmp_path / "out.bin"
    size, sha = spc._graph_download("d1", "i1", "tok", dest)
    assert size == len(body) and sha == hashlib.sha256(body).hexdigest()
    assert dest.exists() and dest.read_bytes() == body
    assert not (tmp_path / "out.bin.part").exists()                        # temp finalized atomically


def test_download_401_raises_reconnect(monkeypatch, tmp_path):
    _route(monkeypatch, {"/content": FakeResp(status_code=401)})
    with pytest.raises(spc.ReconnectRequired):
        spc._graph_download("d1", "i1", "tok", tmp_path / "x.bin")


def test_download_retries_5xx_then_raises_runtimeerror(monkeypatch, tmp_path):
    calls = {"n": 0}
    def _get(url, **kw):
        calls["n"] += 1
        return FakeResp(status_code=503)
    monkeypatch.setattr(spc.requests, "get", _get)
    with pytest.raises(RuntimeError):
        spc._graph_download("d1", "i1", "tok", tmp_path / "x.bin")
    assert calls["n"] == spc._MAX_ATTEMPTS                                 # exhausted retries


def test_stage_one_records_download_error_and_continues(monkeypatch, tmp_path):
    monkeypatch.setattr(spc, "_graph_download",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad file")))
    summary = spc.StagingSummary(dry_run=False)
    manifest = []
    spc._stage_one({"id": "i1", "name": "a.txt", "size": 10}, site_id="s1", drive_id="d1",
                   drive_name="Documents", staging=tmp_path, token="tok", state={}, dry_run=False,
                   manifest=manifest, summary=summary)
    assert summary.error_count == 1 and manifest == []                    # error recorded, run continues


# --- incremental checkpoint (skip-unchanged) --------------------------------

def test_incremental_skip_unchanged(monkeypatch, tmp_path):
    item = {"id": "i1", "name": "a.txt", "size": 42, "lastModifiedDateTime": "2026-01-01T00:00:00Z"}
    dest = spc._staged_path(tmp_path, "s1", "d1", "i1", "a.txt")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"x" * 42)
    state = {"d1:i1": {"size": 42, "modified": "2026-01-01T00:00:00Z", "sha256": "abc",
                       "local_path": str(dest)}}
    # If download is attempted, fail — an unchanged item must NOT be re-downloaded.
    monkeypatch.setattr(spc, "_graph_download",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not download")))
    summary = spc.StagingSummary(dry_run=False)
    manifest = []
    spc._stage_one(item, site_id="s1", drive_id="d1", drive_name="Documents", staging=tmp_path,
                   token="tok", state=state, dry_run=False, manifest=manifest, summary=summary)
    assert summary.skipped_unchanged == 1 and len(manifest) == 1
    assert manifest[0]["sha256"] == "abc"                                 # reused prior state, not re-hashed


# --- source hygiene: no secrets / machine-local paths ------------------------

def test_source_embeds_no_secrets_or_absolute_paths():
    assert "C:\\Client360" not in _SRC and "/Users/" not in _SRC and "/home/" not in _SRC
    # staging root comes from env, not a literal path:
    assert 'os.getenv("CLIENT360_SHAREPOINT_STAGING_ROOT")' in _SRC
    # no inline credentials / tokens / tenant GUIDs:
    import re
    assert not re.search(r"client_secret\s*=\s*['\"][^'\"]+['\"]", _SRC)
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", _SRC)
    assert "Bearer ey" not in _SRC
def test_graph_get_json_managed_session_refreshes_on_401(monkeypatch):
    monkeypatch.setattr(
        spc,
        "_acquire_token",
        lambda account: account["token"],
    )
    monkeypatch.setattr(
        spc,
        "_load_connected_account",
        lambda: {"id": 1, "token": "fresh"},
    )

    session = spc._GraphTokenSession({"id": 1, "token": "stale"})
    calls = []

    def _get(url, headers=None, params=None, timeout=None, **kwargs):
        calls.append(headers["Authorization"])

        if headers["Authorization"] == "Bearer stale":
            return FakeResp(status_code=401)

        if headers["Authorization"] == "Bearer fresh":
            return FakeResp(
                status_code=200,
                json_data={"value": [{"id": "ok"}]},
            )

        raise AssertionError(headers["Authorization"])

    monkeypatch.setattr(spc.requests, "get", _get)

    result = spc._graph_get_json(
        "https://graph.example/test",
        session,
    )

    assert result == {"value": [{"id": "ok"}]}
    assert calls == ["Bearer stale", "Bearer fresh"]
    assert session.current() == "fresh"


def test_download_managed_session_refreshes_on_401(monkeypatch, tmp_path):
    monkeypatch.setattr(
        spc,
        "_acquire_token",
        lambda account: account["token"],
    )
    monkeypatch.setattr(
        spc,
        "_load_connected_account",
        lambda: {"id": 1, "token": "fresh"},
    )

    session = spc._GraphTokenSession({"id": 1, "token": "stale"})
    calls = []
    body = b"refreshed sharepoint download"

    def _get(url, headers=None, **kwargs):
        calls.append(headers["Authorization"])

        if headers["Authorization"] == "Bearer stale":
            return FakeResp(status_code=401)

        if headers["Authorization"] == "Bearer fresh":
            return FakeResp(status_code=200, content=body)

        raise AssertionError(headers["Authorization"])

    monkeypatch.setattr(spc.requests, "get", _get)

    dest = tmp_path / "refreshed.bin"

    size, sha = spc._graph_download(
        "drive1",
        "item1",
        session,
        dest,
    )

    assert calls == ["Bearer stale", "Bearer fresh"]
    assert size == len(body)
    assert sha == hashlib.sha256(body).hexdigest()
    assert dest.read_bytes() == body
    assert session.current() == "fresh"


def test_managed_session_second_401_still_fails_closed(monkeypatch):
    monkeypatch.setattr(
        spc,
        "_acquire_token",
        lambda account: account["token"],
    )
    monkeypatch.setattr(
        spc,
        "_load_connected_account",
        lambda: {"id": 1, "token": "still-bad"},
    )

    session = spc._GraphTokenSession({"id": 1, "token": "stale"})
    calls = {"n": 0}

    def _get(url, **kwargs):
        calls["n"] += 1
        return FakeResp(status_code=401)

    monkeypatch.setattr(spc.requests, "get", _get)

    with pytest.raises(spc.ReconnectRequired):
        spc._graph_get_json(
            "https://graph.example/test",
            session,
        )

    assert calls["n"] == 2

def test_canonical_metadata_fastpath_skips_graph_download(monkeypatch, tmp_path):
    item = {
        "id": "canonical-i1",
        "name": "already-canonical.pdf",
        "size": 4242,
        "lastModifiedDateTime": "2026-08-30T12:34:56Z",
        "file": {"mimeType": "application/pdf"},
    }

    monkeypatch.setattr(
        spc,
        "_graph_download",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unchanged canonical item must not download")
        ),
    )

    summary = spc.StagingSummary(dry_run=False)
    manifest = []

    spc._stage_one(
        item,
        site_id="s1",
        drive_id="d1",
        drive_name="Documents",
        staging=tmp_path,
        token="tok",
        state={},
        dry_run=False,
        manifest=manifest,
        summary=summary,
        existing_fastpath={
            "canonical-i1": {
                "size": 4242,
                "modified": "2026-08-30T12:34:56Z",
                "sha256": "a" * 64,
            }
        },
    )

    assert summary.skipped_unchanged == 1
    assert summary.files_downloaded == 0
    assert len(manifest) == 1
    assert manifest[0]["local_path"] is None
    assert manifest[0]["sha256"] == "a" * 64
    assert manifest[0]["size"] == 4242


def test_canonical_metadata_fastpath_changed_item_still_downloads(monkeypatch, tmp_path):
    item = {
        "id": "changed-i1",
        "name": "changed.pdf",
        "size": 5000,
        "lastModifiedDateTime": "2026-08-31T01:02:03Z",
        "file": {"mimeType": "application/pdf"},
    }

    calls = {"n": 0}

    def fake_download(drive_id, item_id, token, dest):
        calls["n"] += 1
        return 5000, "b" * 64

    monkeypatch.setattr(
        spc,
        "_graph_download",
        fake_download,
    )

    summary = spc.StagingSummary(dry_run=False)
    manifest = []

    spc._stage_one(
        item,
        site_id="s1",
        drive_id="d1",
        drive_name="Documents",
        staging=tmp_path,
        token="tok",
        state={},
        dry_run=False,
        manifest=manifest,
        summary=summary,
        existing_fastpath={
            "changed-i1": {
                "size": 4242,
                "modified": "2026-08-30T12:34:56Z",
                "sha256": "a" * 64,
            }
        },
    )

    assert calls["n"] == 1
    assert summary.skipped_unchanged == 0
    assert summary.files_downloaded == 1
    assert len(manifest) == 1
    assert manifest[0]["sha256"] == "b" * 64
