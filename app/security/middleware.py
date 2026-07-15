import re
import uuid
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.db import documents, engine, record_assignments
from app.security.audit import write_audit_event
from app.security.policy import has_record_scope
from app.security.service import resolve_principal

def _is_cross_site(origin, referer, base_url):
    """Same-origin check for state-changing requests (CSRF defense-in-depth).

    Prefers the Origin header (unchanged behaviour). When Origin is absent it
    falls back to Referer, rejecting only when a Referer is present and its
    scheme+host differ. A request with neither header still passes.
    """
    base = base_url.rstrip("/")
    if origin:
        return origin.rstrip("/") != base
    if referer:
        ref = urlsplit(referer)
        b = urlsplit(base)
        return (ref.scheme, ref.netloc) != (b.scheme, b.netloc)
    return False


PUBLIC_EXACT = frozenset({"/health", "/readiness", "/auth/login", "/auth/callback", "/portal/login",
    "/api/v1/portal/auth/invitations/accept", "/api/v1/portal/auth/password-reset/request",
    "/api/v1/portal/auth/password-reset/consume"})
RULES = (
    # Approval / review decisions use dedicated segregation-of-duty capabilities
    # (work.approve, tax.review). These carve-outs must precede the generic
    # workflow/tax prefix rules so the coarse ".read"->".write" inference does
    # not demand work.write/tax.write and lock out those roles (H4).
    (re.compile(r"^/api/v1/workflows/approvals/"), "work.approve"),
    # Tax document review actions use the dedicated tax.document.review capability;
    # carve out before the generic tax rule so the .read->.write inference does not
    # demand tax.write and lock out a reviewer-only role (same shape as the H4 fix).
    (re.compile(r"^/api/v1/tax/documents/\d+/(accept|reject|reassign|classify|duplicate|revert)"), "tax.document.review"),
    (re.compile(r"^/tax/returns/reviews|^/api/v1/tax/returns/reviews|^/api/v1/tax/returns/\d+/reviews"), "tax.review"),
    (re.compile(r"^/tax/returns|^/api/v1/tax/returns"), "tax.read"),
    (re.compile(r"^/tax/intake|^/api/v1/tax/intake"), "tax.intake.read"),
    (re.compile(r"^/tax|^/api/v1/tax"), "tax.read"),
    # Exception Engine console + API (Sprint 5.5 Phase 6). GET → exception.read;
    # the .read→.write inference gates mutations as exception.write. The engine
    # service enforces the finer exception.resolve / exception.compliance on top.
    (re.compile(r"^/exceptions|^/api/v1/exceptions"), "exception.read"),
    # Organization & Employee Benefits staff console + API (Release 0.9.11 Phase 6). GET →
    # .read; the .read→.write inference gates mutations; the canonical services enforce the
    # finer benefits.enroll / benefits.compliance / benefits.sensitive.read and record scope.
    (re.compile(r"^/organizations|^/api/v1/organizations"), "organization.read"),
    (re.compile(r"^/benefits|^/api/v1/benefits"), "benefits.read"),
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
    if "text/html" in request.headers.get("accept", "") and not request.url.path.startswith("/api"):
        from app.templating import render_error
        return render_error(request, 403, detail=detail)
    return JSONResponse(
        {"detail": detail, "request_id": request.state.request_id}, status_code=403
    )


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        if request.url.path in PUBLIC_EXACT or request.url.path.startswith("/static/"):
            return await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if _is_cross_site(
                request.headers.get("origin"),
                request.headers.get("referer"),
                str(request.base_url),
            ):
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
