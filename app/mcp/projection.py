"""Outbound field projection — the allow-list that decides what an assistant may ever see.

Every payload the MCP surface returns is built HERE, field by field, from an explicit allow-list.
Nothing is passed through wholesale: internal rows carry columns the assistant has no business with,
and a service layer that starts selecting a new column tomorrow must not silently start publishing
it. Two categories are excluded on principle:

  Contact PII        primary_email / primary_phone and their normalized twins. An assistant
                     identifying a client does not need their phone number, and search results are
                     exactly where a bulk-extraction attempt would aim. Staff have the web UI.
  Storage internals  storage_path / storage_uri / stored_name / sha256. These locate bytes on disk;
                     publishing them turns a metadata read into a filesystem map. Documents are
                     referenced by the authenticated download ROUTE instead, which re-checks
                     authorization when it is actually followed.

Display names go through ``document_naming.document_display_name`` and person names through
``person_names.person_row_display_name``, so the assistant sees the same label staff do — including
the 0.13.0 sensitive-identifier gate that keeps an SSN or account number out of a document label.
"""
from __future__ import annotations

from app.services.document_naming import document_display_name
from app.services.person_names import person_row_display_name

#: Download references are the authenticated Client360 route, never a path or a storage URI. The
#: route re-checks authorization when followed, so handing out the reference grants nothing on its
#: own — it is a pointer for a human who is already signed in.
_DOWNLOAD_ROUTE = "/documents/{id}/download"


def iso_timestamp(value):
    """Timestamps as ISO-8601 strings; None stays None. Models read text, not datetime objects."""
    return value.isoformat() if hasattr(value, "isoformat") else (value if value is None else str(value))


def _tags(row) -> dict:
    """``documents.tags`` as a dict.

    The column is JSONB that ingestion sometimes writes as an object and sometimes leaves as the
    default empty ARRAY. Callers want ``.get``, so a non-object shape becomes ``{}`` here rather
    than raising deep inside a projection.
    """
    tags = row.get("tags")
    return tags if isinstance(tags, dict) else {}


def search_result(row: dict) -> dict:
    """One ``universal_search`` row, reduced to identity + relationship context.

    ``subtitle`` is dropped deliberately: for a person it holds their email or phone, which is
    precisely the field this surface does not publish.
    """
    return {
        "entity_type": row.get("entity_type") or row.get("kind"),
        "id": row.get("id"),
        "display_name": row.get("name") or "",
        "household_id": row.get("household_id"),
        "status": row.get("quick_status") or "",
        "relationship_context": list(row.get("relationship_context") or []),
    }


def person_summary(row: dict, *, household=None, members=(), related=()) -> dict:
    """The canonical person payload for ``get_client``."""
    return {
        "entity_type": "person",
        "id": row["id"],
        "display_name": person_row_display_name(row, fallback=f"Person {row['id']}"),
        "status": "active" if row.get("active") else "inactive",
        "household": household,
        "household_members": list(members),
        "related_entities": list(related),
        "created_at": iso_timestamp(row.get("created_at")),
    }


def household_summary(row: dict, *, members=(), related=()) -> dict:
    """The canonical household payload for ``get_client``."""
    return {
        "entity_type": "household",
        "id": row["id"],
        "display_name": (row.get("name") or "").strip() or f"Household {row['id']}",
        "status": "active",
        "city": row.get("city") or None,
        "household": None,
        "household_members": list(members),
        "related_entities": list(related),
        "created_at": iso_timestamp(row.get("created_at")),
    }


def entity_summary(row: dict, *, members=(), related=()) -> dict:
    """A business / trust / estate payload for ``get_client``."""
    return {
        "entity_type": row.get("entity_type") or "business",
        "id": row["id"],
        "display_name": (row.get("name") or "").strip() or f"Entity {row['id']}",
        "status": "active" if row.get("active") else "inactive",
        "household": None,
        "linked_person_id": row.get("person_id"),
        "household_id": row.get("household_id"),
        "household_members": list(members),
        "related_entities": list(related),
    }


def member(row: dict) -> dict:
    """A household member — identity and role only; contact columns are dropped here."""
    return {
        "person_id": row.get("id"),
        "display_name": person_row_display_name(row, fallback=f"Person {row.get('id')}"),
        "relationship_type": row.get("relationship_type") or None,
        "is_primary": bool(row.get("is_primary")),
    }


def document_summary(row: dict, *, ocr=None) -> dict:
    """A document LIST entry: what it is, whose it is, when, and whether text exists.

    ``ocr`` is the row's ``document_ocr`` record when one was loaded. ``ocr_text_available`` is the
    honest answer to "can get_document_text return anything for this?" — completed AND non-empty —
    so an assistant never burns a call on a document with no text.
    """
    tags = _tags(row)
    status = (ocr or {}).get("status")
    return {
        "document_id": row["id"],
        "display_name": document_display_name(row) or f"Document {row['id']}",
        "document_type": tags.get("document_type") or row.get("subcategory") or None,
        "category": row.get("classification") or row.get("category") or tags.get("category") or None,
        "tax_year": tags.get("tax_year") or tags.get("year") or None,
        "person_id": row.get("person_id"),
        "household_id": row.get("household_id"),
        "organization_id": row.get("organization_id"),
        "source": tags.get("source_system") or row.get("storage_provider") or None,
        "status": row.get("status"),
        "is_current_version": True,
        "version": row.get("current_version") or 1,
        "ocr_status": status or row.get("ocr_status") or None,
        "ocr_text_available": bool(status == "completed" and (ocr or {}).get("char_count")),
        "received_at": iso_timestamp(row.get("created_at")),
        "modified_at": iso_timestamp(row.get("updated_at")),
        "content_type": row.get("content_type"),
        "size_bytes": row.get("size_bytes"),
    }


def document_detail(row: dict, *, ocr=None, relationships=(), versions=()) -> dict:
    """A document DETAIL payload: the list shape plus ownership, provenance and version identity.

    ``original_name`` is included here and NOT in the list shape: the detail view is where staff
    reconcile a Client360 record against the source system, and the filename is the provenance they
    reconcile by. It is still only a NAME — nothing here locates the bytes.
    """
    tags = _tags(row)
    detail = document_summary(row, ocr=ocr)
    detail.update({
        "original_name": row.get("original_name"),
        "description": row.get("description") or None,
        "notes": row.get("notes") or None,
        "review_status": row.get("review_status"),
        "effective_date": iso_timestamp(row.get("effective_date")),
        "expiration_date": iso_timestamp(row.get("expiration_date")),
        "source_system": tags.get("source_system") or None,
        "source_folder": tags.get("taxdome_folder") or None,
        "storage_provider": row.get("storage_provider"),
        "download_reference": _DOWNLOAD_ROUTE.format(id=row["id"]),
        "linked_entities": [
            {"entity_type": r.get("entity_type"), "entity_id": r.get("entity_id"),
             "relationship_type": r.get("relationship_type")}
            for r in relationships or ()],
        "versions": [
            {"version_number": v.get("version_number"), "is_current": bool(v.get("is_current")),
             "created_at": iso_timestamp(v.get("created_at")),
             "approved_at": iso_timestamp(v.get("approved_at"))}
            for v in versions or ()],
    })
    return detail
