"""The six Phase 1 tools — read-only adapters over existing Client360 services.

Each tool is a thin translation: validate arguments, call the service that already owns this
question, project the result through ``app.mcp.projection``. No tool builds a query the application
does not already run for its own UI, and no tool reaches past a service layer to the database for
anything the service could answer.

  search_clients         -> app.services.universal_search.universal_search
  get_client             -> app.security.authorization.record_in_scope + canonical people/household reads
  list_client_documents  -> app.services.document_platform.service.list_documents
  get_document           -> app.services.document_platform.service.get_document
  search_documents       -> universal_search (documents) narrowed by the document platform's scope
  get_document_text      -> app.services.document_ocr (already-extracted text only)

READ-ONLY BY CONSTRUCTION. The registry below is the whole surface: there is no dispatch path to
anything not in it, every handler opens read-only connections, and none imports a mutating service.
``tests/test_mcp_no_mutation.py`` asserts that a write tool cannot be introduced unnoticed.

NEVER TRIGGERS WORK. ``get_document_text`` reports text that OCR has already produced; it does not
start OCR. An assistant must not be able to queue hours of CPU on a firm's document store by asking
about a scanned return.
"""
from __future__ import annotations

from sqlalchemy import and_, or_, select

from app.db import documents as documents_table
from app.db import engine, households, people, relationship_entities
from app.mcp import config as mcp_config
from app.mcp import projection
from app.mcp.errors import McpDenied, McpInvalidInput
from app.mcp.scopes import CLIENT_READ, DOCUMENT_CONTENT_READ, DOCUMENT_READ
from app.security.authorization import accessible_person_ids, record_in_scope
from app.services.document_platform import service as doc_service

#: Entity types ``get_client`` accepts. "person" and "household" are the canonical client records;
#: the other three are the relationship entities the firm models around them.
CLIENT_ENTITY_TYPES = ("person", "household", "business", "trust", "estate")
#: Entity types ``search_clients`` may be filtered to — the same set, since a search that could not
#: find a trust would be a different tool from the one that can open it.
SEARCH_ENTITY_TYPES = CLIENT_ENTITY_TYPES


# --- argument validation -----------------------------------------------------

def _entity_id(value, *, field: str = "id") -> int:
    """A positive integer id, or McpInvalidInput.

    Strict on purpose. Models produce ids from prose and get them wrong in specific ways: floats
    ("12.0"), padded strings, negative sentinels, ``"null"``, SQL fragments. Every one of those is a
    malformed id and is refused HERE, before it can reach a service layer that would have to guess.
    A bool is refused too — Python would otherwise accept ``True`` as the integer 1.
    """
    if isinstance(value, bool) or value is None:
        raise McpInvalidInput(f"{field} must be a positive integer")
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise McpInvalidInput(f"{field} must be a positive integer")
        value = int(text)
    if not isinstance(value, int):
        raise McpInvalidInput(f"{field} must be a positive integer")
    if value <= 0:
        raise McpInvalidInput(f"{field} must be a positive integer")
    return value


def _text(value, *, field: str, required: bool = False, max_length: int = 200) -> str:
    """A bounded, trimmed string. Over-long input is refused rather than silently truncated."""
    if value is None:
        if required:
            raise McpInvalidInput(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise McpInvalidInput(f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise McpInvalidInput(f"{field} is required")
    if len(text) > max_length:
        raise McpInvalidInput(f"{field} must be at most {max_length} characters")
    return text


def _choice(value, allowed, *, field: str):
    """An optional value from a fixed vocabulary. Unknown values are refused, never ignored —
    silently dropping an unrecognised filter would return MORE than the caller asked for."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in allowed:
        raise McpInvalidInput(f"{field} must be one of: {', '.join(allowed)}")
    return value


def _tax_year(value, *, field: str = "tax_year"):
    """A four-digit tax year, or None. Anything else is a malformed filter."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not (text.isdigit() and len(text) == 4):
        raise McpInvalidInput(f"{field} must be a four-digit year")
    return text


# --- shared helpers ----------------------------------------------------------

def _ocr_records(document_ids):
    """``document_ocr`` rows for the given documents, keyed by id — via the OCR service, one query."""
    from app.services.document_ocr import ocr_for_documents
    try:
        return ocr_for_documents(document_ids)
    except Exception:  # noqa: BLE001 — OCR state is advisory metadata, never a reason to fail a list
        return {}


def _client_in_scope(principal, entity_type: str, entity_id: int) -> bool:
    """Record scope for one client entity, matching what the services behind these tools return.

    Client360 has two established notions of record scope and they are not the same set:

      ``record_in_scope``        a DIRECT assignment on that exact entity (plus work-derived reads)
      ``accessible_person_ids``  that, PLUS every person in a household the user is assigned to

    The document listing and universal_search both scope by the second; ``_visible`` on a single
    document uses the first. This gate honours the UNION, because its job is to reject a client the
    caller could not see anyway — not to introduce a third, stricter boundary of its own. Being
    stricter here would deny ``list_client_documents(person_id=…)`` for a household-assigned advisor
    while the very same listing, asked for the household, returned those documents happily.

    Business/trust/estate rows have no scope of their own: they are in scope when the person or
    household they are anchored to is. An entity anchored to nobody is firm-level and needs
    firm-wide read.
    """
    if principal.can("record.read_all"):
        return True
    if entity_type in ("person", "household"):
        if record_in_scope(principal, entity_type, entity_id):
            return True
        with engine.connect() as c:
            accessible = accessible_person_ids(c, principal)
            if accessible is None:                       # firm-wide reader
                return True
            if not accessible:
                return False
            if entity_type == "person":
                return entity_id in accessible
            # A household is reachable when any of its members is.
            return c.scalar(select(people.c.id).where(
                people.c.household_id == entity_id,
                people.c.id.in_(tuple(accessible))).limit(1)) is not None
    with engine.connect() as c:
        row = c.execute(select(relationship_entities).where(
            relationship_entities.c.id == entity_id,
            relationship_entities.c.entity_type == entity_type)).mappings().first()
    if row is None:
        return False
    if principal.can("record.read_all"):
        return True
    if row["person_id"] and record_in_scope(principal, "person", row["person_id"]):
        return True
    return bool(row["household_id"]) and record_in_scope(principal, "household", row["household_id"])


def _related_entities(c, *, person_id=None, household_id=None):
    """Business/trust/estate rows anchored to this client. Identity and type only."""
    conds = []
    if person_id is not None:
        conds.append(relationship_entities.c.person_id == person_id)
    if household_id is not None:
        conds.append(relationship_entities.c.household_id == household_id)
    if not conds:
        return []
    rows = c.execute(select(
        relationship_entities.c.id, relationship_entities.c.name,
        relationship_entities.c.entity_type, relationship_entities.c.active)
        .where(and_(or_(*conds), relationship_entities.c.entity_type.in_(
            ("business", "trust", "estate")))).limit(50)).mappings().all()
    return [{"id": r["id"], "display_name": (r["name"] or "").strip() or f"Entity {r['id']}",
             "entity_type": r["entity_type"],
             "status": "active" if r["active"] else "inactive"} for r in rows]


def _members(household_id):
    """Household members, projected. Reuses the households service so member resolution has one home."""
    from app.services.households import household_members
    return [projection.member(r) for r in household_members(household_id)]


# --- tools -------------------------------------------------------------------

def search_clients(token, args: dict) -> dict:
    """Find people, households, businesses, trusts and estates by name/identifier."""
    query = _text(args.get("query"), field="query", required=True, max_length=200)
    entity_type = _choice(args.get("entity_type"), SEARCH_ENTITY_TYPES, field="entity_type")
    limit = mcp_config.clamp_limit(args.get("limit"))

    from app.services.universal_search import universal_search
    types = [entity_type] if entity_type else list(SEARCH_ENTITY_TYPES)
    found = universal_search(token.principal, query, types=types, limit=limit)
    results = [projection.search_result(r) for r in found.get("results", [])][:limit]
    return {"query": query, "count": len(results), "limit": limit, "results": results}


def get_client(token, args: dict) -> dict:
    """The canonical summary for one client: identity, status, household, members, related entities."""
    entity_type = _choice(args.get("entity_type"), CLIENT_ENTITY_TYPES, field="entity_type")
    if entity_type is None:
        raise McpInvalidInput(f"entity_type must be one of: {', '.join(CLIENT_ENTITY_TYPES)}")
    entity_id = _entity_id(args.get("entity_id"), field="entity_id")

    if not _client_in_scope(token.principal, entity_type, entity_id):
        # Deliberately the same answer whether the record is out of scope or does not exist.
        raise McpDenied("Client not found or not permitted")

    with engine.connect() as c:
        if entity_type == "person":
            row = c.execute(select(people).where(people.c.id == entity_id)).mappings().first()
            if row is None:
                raise McpDenied("Client not found or not permitted")
            row = dict(row)
            household, members = None, []
            if row.get("household_id"):
                hh = c.execute(select(households).where(
                    households.c.id == row["household_id"])).mappings().first()
                if hh:
                    household = {"id": hh["id"],
                                 "display_name": (hh["name"] or "").strip() or f"Household {hh['id']}"}
                    members = _members(hh["id"])
            related = _related_entities(c, person_id=entity_id,
                                        household_id=row.get("household_id"))
            return projection.person_summary(row, household=household, members=members,
                                             related=related)

        if entity_type == "household":
            row = c.execute(select(households).where(
                households.c.id == entity_id)).mappings().first()
            if row is None:
                raise McpDenied("Client not found or not permitted")
            related = _related_entities(c, household_id=entity_id)
            return projection.household_summary(dict(row), members=_members(entity_id),
                                                related=related)

        row = c.execute(select(relationship_entities).where(
            relationship_entities.c.id == entity_id,
            relationship_entities.c.entity_type == entity_type)).mappings().first()
        if row is None:
            raise McpDenied("Client not found or not permitted")
        row = dict(row)
        members = _members(row["household_id"]) if row.get("household_id") else []
        return projection.entity_summary(row, members=members)


def list_client_documents(token, args: dict) -> dict:
    """A client's documents — the person/household union — filtered and paginated."""
    person_id = args.get("person_id")
    household_id = args.get("household_id")
    if person_id in (None, "") and household_id in (None, ""):
        raise McpInvalidInput("person_id or household_id is required")
    person_id = _entity_id(person_id, field="person_id") if person_id not in (None, "") else None
    household_id = (_entity_id(household_id, field="household_id")
                    if household_id not in (None, "") else None)
    tax_year = _tax_year(args.get("tax_year"))
    category = _text(args.get("category"), field="category", max_length=100) or None
    document_type = _text(args.get("document_type"), field="document_type", max_length=100) or None
    name_filter = _text(args.get("name_contains"), field="name_contains", max_length=200) or None
    limit = mcp_config.clamp_limit(args.get("limit"))
    page = _entity_id(args.get("page"), field="page") if args.get("page") not in (None, "") else 1

    # Record scope is enforced per row by the document platform, but check the ANCHOR too: asking
    # about a client you may not see should be denied outright, not answered with an empty list that
    # confirms the id exists.
    for entity_type, entity_id in (("person", person_id), ("household", household_id)):
        if entity_id is not None and not _client_in_scope(token.principal, entity_type, entity_id):
            raise McpDenied("Client not found or not permitted")

    listed = doc_service.list_documents(
        token.principal, person_id=person_id, household_id=household_id,
        category_any=category, subcategory=document_type, tax_year=tax_year,
        name_any=name_filter, page=page, page_size=limit)
    rows = listed.get("rows", [])
    ocr = _ocr_records([r["id"] for r in rows])
    return {
        "person_id": person_id, "household_id": household_id,
        "count": len(rows), "total": listed.get("total", 0),
        "page": listed.get("page", page), "page_size": listed.get("page_size", limit),
        "pages": listed.get("pages", 0),
        "documents": [projection.document_summary(r, ocr=ocr.get(r["id"])) for r in rows],
    }


def get_document(token, args: dict) -> dict:
    """One document's metadata, ownership, provenance, version identity and OCR state."""
    document_id = _entity_id(args.get("document_id"), field="document_id")
    doc = doc_service.get_document(token.principal, document_id)
    if doc is None or doc.get("status") == "deleted" or doc.get("deleted_at") is not None:
        raise McpDenied("Document not found or not permitted")
    ocr = _ocr_records([document_id]).get(document_id)
    return projection.document_detail(doc, ocr=ocr,
                                      relationships=doc.get("relationships") or (),
                                      versions=doc.get("versions") or ())


def search_documents(token, args: dict) -> dict:
    """Search documents by name, type, metadata and already-extracted text.

    Delegates the matching to ``universal_search`` — the same index the staff search box uses,
    including its OCR-text branch — then re-checks each hit against the document platform's scope
    clause and drops soft-deleted rows. Two passes rather than one because universal_search is a
    navigation aid that includes soft-deleted documents; the MCP contract says they never appear.
    """
    query = _text(args.get("query"), field="query", required=True, max_length=200)
    person_id = (_entity_id(args.get("person_id"), field="person_id")
                 if args.get("person_id") not in (None, "") else None)
    household_id = (_entity_id(args.get("household_id"), field="household_id")
                    if args.get("household_id") not in (None, "") else None)
    tax_year = _tax_year(args.get("tax_year"))
    category = _text(args.get("category"), field="category", max_length=100) or None
    limit = mcp_config.clamp_limit(args.get("limit"))

    for entity_type, entity_id in (("person", person_id), ("household", household_id)):
        if entity_id is not None and not _client_in_scope(token.principal, entity_type, entity_id):
            raise McpDenied("Client not found or not permitted")

    from app.services.universal_search import universal_search
    # Over-fetch modestly: the scope/soft-delete re-check below removes rows, and a caller asking for
    # 25 should not get 4 because 21 were filtered. Still bounded by MAX_LIMIT * 2.
    found = universal_search(token.principal, query, types=["document"],
                             limit=min(mcp_config.MAX_LIMIT * 2, limit * 2 + 10))
    candidate_ids = [r["id"] for r in found.get("results", []) if r.get("kind") == "document"]
    if not candidate_ids:
        return {"query": query, "count": 0, "limit": limit, "documents": []}

    with engine.connect() as c:
        conds = [documents_table.c.id.in_(candidate_ids),
                 documents_table.c.status != "deleted",
                 documents_table.c.deleted_at.is_(None)]
        scope = doc_service.visible_documents_clause(token.principal, c)
        if scope is not None:
            conds.append(scope)
        anchor = doc_service.client_anchor_clause(c, person_id=person_id,
                                                  household_id=household_id)
        if anchor is not None:
            conds.append(anchor)
        year_clause = doc_service.tax_year_clause(tax_year)
        if year_clause is not None:
            conds.append(year_clause)
        if category:
            conds.append(or_(documents_table.c.classification == category,
                             documents_table.c.category == category))
        rows = [dict(r) for r in c.execute(
            select(documents_table).where(and_(*conds))
            .order_by(documents_table.c.id.desc()).limit(limit)).mappings()]

    ocr = _ocr_records([r["id"] for r in rows])
    return {"query": query, "count": len(rows), "limit": limit,
            "documents": [projection.document_summary(r, ocr=ocr.get(r["id"])) for r in rows]}


def get_document_text(token, args: dict) -> dict:
    """Already-extracted text for one document. NEVER starts OCR.

    Returns ``available: false`` with the current OCR state when there is nothing to give — pending,
    failed, unsupported, or simply never attempted. That is a normal answer, not an error: the
    assistant learns the text does not exist and moves on instead of retrying.
    """
    document_id = _entity_id(args.get("document_id"), field="document_id")
    # Authorization and existence go through the same gate as get_document, so text can never be
    # read for a document whose metadata the caller may not see.
    doc = doc_service.get_document(token.principal, document_id)
    if doc is None or doc.get("status") == "deleted" or doc.get("deleted_at") is not None:
        raise McpDenied("Document not found or not permitted")

    from app.db import document_ocr
    with engine.connect() as c:
        record = c.execute(select(
            document_ocr.c.status, document_ocr.c.text, document_ocr.c.char_count,
            document_ocr.c.page_count, document_ocr.c.engine, document_ocr.c.ocr_completed_at)
            .where(document_ocr.c.document_id == document_id)).mappings().first()

    base = {"document_id": document_id,
            "display_name": projection.document_summary(doc).get("display_name")}
    if record is None:
        return {**base, "available": False, "ocr_status": "not_extracted", "text": None,
                "reason": "No text has been extracted for this document."}
    if record["status"] != "completed" or not (record["text"] or "").strip():
        return {**base, "available": False, "ocr_status": record["status"], "text": None,
                "reason": "Extracted text is not available for this document."}

    text = record["text"]
    truncated = len(text) > mcp_config.MAX_TEXT_CHARS
    return {
        **base,
        "available": True,
        "ocr_status": record["status"],
        "text": text[:mcp_config.MAX_TEXT_CHARS] if truncated else text,
        "truncated": truncated,
        "char_count": record["char_count"],
        "page_count": record["page_count"],
        "engine": record["engine"],
        "extracted_at": projection.iso_timestamp(record["ocr_completed_at"]),
    }


# --- registry ----------------------------------------------------------------

_LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": mcp_config.MAX_LIMIT,
                 "description": f"Maximum results (1-{mcp_config.MAX_LIMIT}, "
                                f"default {mcp_config.DEFAULT_LIMIT})."}

#: The complete MCP surface. ``scope`` is enforced by ``app.mcp.auth.authorize`` before ``handler``
#: is ever called. Adding an entry here is the ONLY way to add a tool.
TOOLS: dict[str, dict] = {
    "search_clients": {
        "scope": CLIENT_READ,
        "handler": search_clients,
        "description": (
            "Search Client360 for people, households, businesses, trusts and estates by name or "
            "identifier. Returns identifiers, display names and relationship context only — no "
            "contact details. Results are limited to clients the authenticated user may see."),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or identifier to search for."},
                "entity_type": {"type": "string", "enum": list(SEARCH_ENTITY_TYPES),
                                "description": "Restrict to one entity type."},
                "limit": _LIMIT_SCHEMA,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "get_client": {
        "scope": CLIENT_READ,
        "handler": get_client,
        "description": (
            "Get the canonical summary for one client: display name, status, household membership, "
            "household members and related business/trust/estate entities."),
        "schema": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string", "enum": list(CLIENT_ENTITY_TYPES)},
                "entity_id": {"type": "integer", "minimum": 1},
            },
            "required": ["entity_type", "entity_id"],
            "additionalProperties": False,
        },
    },
    "list_client_documents": {
        "scope": DOCUMENT_READ,
        "handler": list_client_documents,
        "description": (
            "List a client's documents. Given a person, this includes their household's documents; "
            "given a household, it includes every member's. Soft-deleted documents are never "
            "returned. Results are paginated."),
        "schema": {
            "type": "object",
            "properties": {
                "person_id": {"type": "integer", "minimum": 1},
                "household_id": {"type": "integer", "minimum": 1},
                "tax_year": {"type": "string", "description": "Four-digit tax year, e.g. \"2024\"."},
                "category": {"type": "string", "description": "Document classification."},
                "document_type": {"type": "string", "description": "Document subcategory/type."},
                "name_contains": {"type": "string", "description": "Filter by document name."},
                "page": {"type": "integer", "minimum": 1, "description": "1-based page number."},
                "limit": _LIMIT_SCHEMA,
            },
            "additionalProperties": False,
        },
    },
    "get_document": {
        "scope": DOCUMENT_READ,
        "handler": get_document,
        "description": (
            "Get one document's metadata: ownership, category, tax year, source/provenance, version "
            "identity, OCR state and an authenticated download reference. Never returns storage "
            "paths or file contents."),
        "schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer", "minimum": 1}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
    "search_documents": {
        "scope": DOCUMENT_READ,
        "handler": search_documents,
        "description": (
            "Search documents by name, type, metadata and already-extracted text, optionally scoped "
            "to one client or household and filtered by tax year or category."),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "person_id": {"type": "integer", "minimum": 1},
                "household_id": {"type": "integer", "minimum": 1},
                "tax_year": {"type": "string", "description": "Four-digit tax year."},
                "category": {"type": "string"},
                "limit": _LIMIT_SCHEMA,
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "get_document_text": {
        "scope": DOCUMENT_CONTENT_READ,
        "handler": get_document_text,
        "description": (
            "Return text already extracted from a document by OCR. Does NOT start OCR: if no text "
            "has been extracted, this reports that the text is unavailable."),
        "schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer", "minimum": 1}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
    },
}


def tool_definitions() -> list[dict]:
    """The ``tools/list`` payload, in MCP's declared shape."""
    return [{"name": name, "description": spec["description"], "inputSchema": spec["schema"]}
            for name, spec in TOOLS.items()]
