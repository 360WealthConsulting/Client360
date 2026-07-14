import re
import uuid

from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.db import documents, engine, record_assignments
from app.security.audit import write_audit_event
from app.security.policy import has_record_scope
from app.security.service import resolve_principal

PUBLIC_EXACT = frozenset({"/health", "/auth/login", "/auth/callback", "/portal/login",
    "/api/v1/portal/auth/invitations/accept", "/api/v1/portal/auth/password-reset/request",
    "/api/v1/portal/auth/password-reset/consume"})
RULES = (
    (re.compile(r"^/tax/returns|^/api/v1/tax/returns"), "tax.read"),
    (re.compile(r"^/tax/intake|^/api/v1/tax/intake"), "tax.intake.read"),
    (re.compile(r"^/tax|^/api/v1/tax"), "tax.read"),
    (re.compile(r"^/workflows|^/api/v1/workflows"), "work.read"),
    (re.compile(r"^/work|^/api/v1/work"), "work.read"),
    (re.compile(r"^/admin/audit"), "audit.read"),
    (re.compile(r"^/admin/(roles|user-roles)"), "role.manage"),
    (re.compile(r"^/admin/team-memberships"), "team.manage"),
    (re.compile(r"^/admin/assignments"), "assignment.manage"),
    (re.compile(r"^/admin"), "identity.manage"),
    (re.compile(r"/tasks(?:/|$)|^/tasks"), "task.read"),
    (re.compile(r"/documents(?:/|$)|^/documents"), "document.read"),
    (re.compile(r"^/microsoft|^/mail|^/calendar"), "communication.read"),
    (re.compile(r"^/portfolio"), "client.read"),
    (
        re.compile(r"^/relationships|^/relationship-entities|^/api/relationships"),
        "client.read",
    ),
    (
        re.compile(
            r"^/$|^/api/stats|^/people|^/households|^/search|^/api/search|"
            r"^/timeline|^/matches|^/source|/activities"
        ),
        "client.read",
    ),
)
RECORD_PATH = re.compile(r"^/(people|households)/(\d+)")
FIRM_WIDE_COLLECTION = re.compile(
    r"^/(?:$|api/(?:stats|search)(?:/|$)|search(?:/|$)|people/?$|households/?$|"
    r"tasks/?$|activities/?$|matches/?$|source(?:/|$)|portfolio(?:/|$)|"
    r"relationships/search(?:/|$)|api/relationships/search(?:/|$)|"
    r"relationship-entities(?:/|$))"
)


def _denied(request, principal, action, entity_type, entity_id, detail):
    write_audit_event(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=principal.user_id,
        outcome="denied",
        request_id=request.state.request_id,
        ip_address=request.client.host if request.client else None,
    )
    return JSONResponse(
        {"detail": detail, "request_id": request.state.request_id}, status_code=403
    )


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        if request.url.path in PUBLIC_EXACT or request.url.path.startswith("/static/"):
            return await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse(
                    {
                        "detail": "Cross-site request rejected",
                        "request_id": request.state.request_id,
                    },
                    status_code=403,
                )
        if request.url.path.startswith("/portal") or request.url.path.startswith("/api/v1/portal"):
            from app.portal.service import resolve_portal_session
            portal_principal = resolve_portal_session(request.session.get("portal_session_token"))
            request.state.portal_principal = portal_principal
            if portal_principal is None:
                if "text/html" in request.headers.get("accept", ""):
                    return RedirectResponse("/portal/login", 303)
                return JSONResponse({"detail": "Portal authentication required", "request_id": request.state.request_id}, status_code=401)
            response = await call_next(request)
            if response.status_code < 400 and request.method not in {"GET", "HEAD", "OPTIONS"}:
                write_audit_event(action="portal.route.mutated", entity_type="portal_route", entity_id=request.url.path, request_id=request.state.request_id, ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"), metadata={"portal_account_id": portal_principal.account_id, "method": request.method, "status_code": response.status_code})
            response.headers["x-request-id"] = request.state.request_id
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["referrer-policy"] = "same-origin"
            response.headers["x-frame-options"] = "DENY"
            response.headers["content-security-policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
            return response
        token = request.session.get("session_token")
        principal = resolve_principal(token)
        request.state.principal = principal
        if principal is None:
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/auth/login", 303)
            return JSONResponse(
                {
                    "detail": "Authentication required",
                    "request_id": request.state.request_id,
                },
                status_code=401,
            )
        capability = next((code for pattern, code in RULES if pattern.search(request.url.path)), None)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and capability:
            capability = capability.replace(".read", ".write")
        if capability and not principal.can(capability):
            return _denied(
                request,
                principal,
                "authorization.denied",
                "route",
                request.url.path,
                "Access denied",
            )
        broad_scope = (
            "record.read_all"
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else "record.write_all"
        )
        if FIRM_WIDE_COLLECTION.match(request.url.path) and not principal.can(broad_scope):
            return _denied(
                request,
                principal,
                "authorization.collection_denied",
                "route",
                request.url.path,
                "Firm-wide collection access denied",
            )
        relationship_person_id = (
            request.query_params.get("person_id")
            if request.url.path.startswith("/relationships/")
            else None
        )
        if relationship_person_id and relationship_person_id.isdigit():
            with engine.connect() as connection:
                allowed = has_record_scope(
                    connection,
                    principal,
                    "person",
                    int(relationship_person_id),
                    record_assignments=record_assignments,
                    write=request.method not in {"GET", "HEAD", "OPTIONS"},
                )
            if not allowed:
                return _denied(
                    request,
                    principal,
                    "authorization.relationship_denied",
                    "person",
                    relationship_person_id,
                    "Relationship access denied",
                )
        record_match = RECORD_PATH.match(request.url.path)
        if record_match:
            entity_type = "person" if record_match.group(1) == "people" else "household"
            with engine.connect() as connection:
                allowed = has_record_scope(
                    connection,
                    principal,
                    entity_type,
                    int(record_match.group(2)),
                    record_assignments=record_assignments,
                    write=request.method not in {"GET", "HEAD", "OPTIONS"},
                )
            if not allowed:
                return _denied(
                    request,
                    principal,
                    "authorization.record_denied",
                    entity_type,
                    record_match.group(2),
                    "Record access denied",
                )
        document_match = re.match(r"^/documents/(\d+)", request.url.path)
        if document_match:
            with engine.connect() as connection:
                person_id = connection.scalar(
                    select(documents.c.person_id).where(
                        documents.c.id == int(document_match.group(1))
                    )
                )
                allowed = person_id is not None and has_record_scope(
                    connection,
                    principal,
                    "person",
                    person_id,
                    record_assignments=record_assignments,
                    write=request.method not in {"GET", "HEAD", "OPTIONS"},
                )
            if not allowed:
                return _denied(
                    request,
                    principal,
                    "authorization.document_denied",
                    "document",
                    document_match.group(1),
                    "Document access denied",
                )
        response = await call_next(request)
        if response.status_code < 400 and request.method not in {"GET", "HEAD", "OPTIONS"}:
            write_audit_event(
                action="route.mutated",
                entity_type="route",
                entity_id=request.url.path,
                actor_user_id=principal.user_id,
                request_id=request.state.request_id,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                metadata={"method": request.method, "status_code": response.status_code},
            )
        elif response.status_code < 400 and document_match:
            write_audit_event(
                action="document.accessed",
                entity_type="document",
                entity_id=document_match.group(1),
                actor_user_id=principal.user_id,
                request_id=request.state.request_id,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                metadata={"method": request.method, "status_code": response.status_code},
            )
        response.headers["x-request-id"] = request.state.request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "same-origin"
        response.headers["x-frame-options"] = "DENY"
        response.headers["content-security-policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response
