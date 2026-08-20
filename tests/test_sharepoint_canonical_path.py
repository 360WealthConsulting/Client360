"""Windows MAX_PATH bounding of the LOCAL canonical SharePoint storage path.

The delta baseline writes each item to <destination>/<site>/<library>/<folder…>/<filename>. On Windows the
composite siteId, the ``b!…`` driveId, and a deep client-folder hierarchy push that absolute path (and the
``.sync-*.part`` temp ``_copy_verified`` writes beside it) past the legacy MAX_PATH (260) -> [WinError 3].
``_bounded_canonical_relpath`` bounds ONLY the on-disk path; the original SharePoint identifiers stay on the
source reference (source_path + metadata), the document tags, and documents.original_name.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest
from sqlalchemy import func, select

from app.db import documents, engine, metadata
from app.importers import sharepoint as sp
from app.importers.taxdome_drive import sanitize_relative_path

document_sources = metadata.tables["document_sources"]

_CEIL = sp._CANONICAL_MAX_PATH                       # 240
# Mirror the real production root length: C:\Client360\Data\Documents\SharePoint
_DEST = "C:\\Client360\\Data\\Documents\\SharePoint"


def _abs_len(destination, rel) -> int:
    return len(str(Path(destination) / Path(*rel.parts)))


def _matthews_safe_rel():
    site = "contoso.sharepoint.com,3f2504e0-4f89-11d3-9a0c-0305e82c3301,6b29fc40-ca47-1067-b31d-00dd010662da"
    drive = "b!an9eHY8SsUSoKpMiW4vy9GHKx9mbaS1Eo_ha68ZssFa86B3K4zuaRJVGisg9UoId"
    folder = "Clients/Matthews Family/Dr. Fletcher & Sarah Matthews/2024 Tax Year/Federal and State Returns"
    fname = "Dr. Fletcher & Sarah Matthews 2024 Individual Income Tax Return - Federal and State.pdf"
    return sanitize_relative_path(f"{site}/{drive}/{folder}/{fname}")


# --- unit: path bounding ------------------------------------------------------

def test_deeply_nested_long_path_bounded_below_ceiling():
    safe_rel = _matthews_safe_rel()
    assert _abs_len(_DEST, safe_rel) > _CEIL                      # precondition: natural path overflows
    bounded = sp._bounded_canonical_relpath(_DEST, safe_rel)
    assert _abs_len(_DEST, bounded) <= _CEIL                      # final path bounded


def test_temp_part_path_also_within_ceiling():
    bounded = sp._bounded_canonical_relpath(_DEST, _matthews_safe_rel())
    parent = (Path(_DEST) / Path(*bounded.parts)).parent
    # the ".sync-*.part" temp _copy_verified writes IN the parent must also stay under the ceiling
    assert len(str(parent)) + 1 + sp._CANONICAL_TEMP_RESERVE <= _CEIL


def test_short_path_is_unchanged():
    safe_rel = sanitize_relative_path("Site/Docs/Clients/Jane/return.pdf")
    bounded = sp._bounded_canonical_relpath(_DEST, safe_rel)
    assert bounded == PurePosixPath("Site", "Docs", "Clients", "Jane", "return.pdf")   # human hierarchy kept


def test_extension_is_preserved_when_bounded():
    bounded = sp._bounded_canonical_relpath(_DEST, _matthews_safe_rel())
    assert bounded.suffix == ".pdf" and bounded.parts[0] == sp._BOUNDED_DIR


def test_same_original_path_maps_to_same_local_path():
    r = _matthews_safe_rel()
    assert sp._bounded_canonical_relpath(_DEST, r) == sp._bounded_canonical_relpath(_DEST, r)


def test_two_different_long_originals_do_not_collide():
    def _long(tag):
        site = "contoso.sharepoint.com,3f2504e0-4f89-11d3-9a0c-0305e82c3301,6b29fc40-ca47-1067-b31d-0011"
        drive = "b!" + "Z" * 64
        folder = "/".join(f"Folder-{tag}-{i}" for i in range(8))
        return sanitize_relative_path(f"{site}/{drive}/{folder}/{tag}-document.pdf")
    a, b = _long("alpha"), _long("beta")
    ba = sp._bounded_canonical_relpath(_DEST, a)
    bb = sp._bounded_canonical_relpath(_DEST, b)
    assert _abs_len(_DEST, a) > _CEIL and _abs_len(_DEST, b) > _CEIL   # both overflow (so both bounded)
    assert ba != bb and _abs_len(_DEST, ba) <= _CEIL and _abs_len(_DEST, bb) <= _CEIL


def test_impossible_destination_root_raises_clearly():
    with pytest.raises(ValueError) as ei:
        sp._bounded_canonical_relpath("C:\\" + "x" * 250, _matthews_safe_rel())
    assert "destination root too long" in str(ei.value)


# --- integration: metadata preserved + sha dedupe unchanged -------------------

@pytest.fixture()
def _cleanup_docs():
    created: list[int] = []
    yield created
    if created:
        with engine.begin() as c:
            c.execute(document_sources.delete().where(document_sources.c.document_id.in_(created)))
            c.execute(documents.delete().where(documents.c.id.in_(created)))


def _long_item(tmp_path, *, content, item_id, name):
    staged = tmp_path / f"{item_id}.bin"
    staged.write_bytes(content)
    return {
        "name": name, "item_id": item_id,
        "web_url": f"https://contoso.sharepoint.com/sites/Wealth/{item_id}",
        "site": "contoso.sharepoint.com,3f2504e0-4f89-11d3-9a0c-0305e82c3301,6b29fc40-ca47-1067-b31d-00dd01",
        "library": "b!an9eHY8SsUSoKpMiW4vy9GHKx9mbaS1Eo_ha68ZssFa86B3K4zuaRJVGisg9UoId",
        "folder_path": "Clients/Matthews Family/Dr. Fletcher & Sarah Matthews/2024 Tax Year/Federal Returns",
        "modified_at": "2024-01-01T00:00:00", "local_path": str(staged), "size": len(content),
    }


def test_long_path_import_preserves_sharepoint_provenance_but_bounds_local(tmp_path, _cleanup_docs):
    content = b"a Matthews family tax return body, long SharePoint path"
    item = _long_item(tmp_path, content=content, item_id="ITEMlong1", name="2024 Individual Tax Return.pdf")
    expected_human = sanitize_relative_path(sp._rel_path(item, item["name"]))   # the original human path

    summary = sp.import_sharepoint_items([item], destination_root=str(tmp_path / "canon"),
                                         authoritative=False)
    doc_id = summary["affected_document_ids"][0]
    _cleanup_docs.append(doc_id)

    with engine.connect() as c:
        doc = c.execute(select(documents).where(documents.c.id == doc_id)).mappings().one()
        ref = c.execute(select(document_sources).where(document_sources.c.document_id == doc_id)).mappings().one()

    # LOCAL path was bounded (and the file physically exists there — the copy did not fail):
    assert doc["storage_path"].startswith(sp._BOUNDED_DIR)
    assert len(str(Path(doc["storage_uri"]))) <= _CEIL
    assert Path(doc["storage_uri"]).read_bytes() == content
    assert expected_human.as_posix() != doc["storage_path"]     # bounded != the long human path
    # ORIGINAL SharePoint provenance preserved untouched:
    assert ref["source_path"] == str(expected_human)            # source ref keeps the human path
    assert ref["metadata"]["site"] == item["site"]
    assert ref["metadata"]["library"] == item["library"]
    assert ref["metadata"]["folder"] == item["folder_path"]
    assert doc["tags"]["web_url"] == item["web_url"]
    assert doc["tags"]["sharepoint_folder"] == item["folder_path"]
    assert doc["original_name"] == item["name"]                 # filename unchanged


def test_sha_dedupe_unchanged_under_path_bounding(tmp_path, _cleanup_docs):
    content = b"identical bytes arriving as two different long SharePoint items"
    a = _long_item(tmp_path, content=content, item_id="ITEMdupA", name="Return A.pdf")
    b = _long_item(tmp_path, content=content, item_id="ITEMdupB", name="Return B.pdf")

    s1 = sp.import_sharepoint_items([a], destination_root=str(tmp_path / "canon"), authoritative=False)
    s2 = sp.import_sharepoint_items([b], destination_root=str(tmp_path / "canon"), authoritative=False)
    for did in s1["affected_document_ids"] + s2["affected_document_ids"]:
        _cleanup_docs.append(did)

    import hashlib
    sha = hashlib.sha256(content).hexdigest()
    with engine.connect() as c:
        n_docs = c.execute(select(func.count()).select_from(documents)
                           .where(documents.c.sha256 == sha)).scalar()
    assert s1["canonical_created"] == 1                          # first item creates the canonical
    assert s2["reused_canonical"] == 1 and s2["canonical_created"] == 0   # second reuses by content sha
    assert n_docs == 1                                           # ONE canonical document (dedupe intact)
