import re
import uuid
from urllib.parse import quote, urlsplit

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


PUBLIC_EXACT = frozenset({"/favicon.ico", "/health", "/readiness", "/auth/login", "/auth/callback", "/portal/login",
    "/api/microsoft/sharepoint/webhook",
    "/api/v1/portal/auth/invitations/accept", "/api/v1/portal/auth/password-reset/request",
    "/api/v1/portal/auth/password-reset/consume", "/api/portal/login",
    # Client email one-time-code sign-in: reached BEFORE a portal session exists.
    "/portal/activate", "/portal/verify", "/portal/verify/resend"})
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
    # Insurance: coarse read; services enforce insurance.suitability / .sensitive.read
    # / .licensing and record scope (Release 0.10.0). No SoD carve-out precedes it
    # because suitability review is not a URL prefix — it is gated inside the service.
    (re.compile(r"^/insurance|^/api/v1/insurance"), "insurance.read"),
    # Advisor Workspace: book-scoped (NOT a firm-wide collection). Requires
    # client.read; the orchestration service scopes to accessible clients, so it
    # is deliberately absent from FIRM_WIDE_COLLECTION (no record.read_all gate).
    # MUST precede the "^/work" rule below, which would otherwise match
    # "/workspace" by prefix and mislabel it work.read.
    (re.compile(r"^/workspace"), "client.read"),
    (re.compile(r"^/workflows|^/api/v1/workflows"), "work.read"),
    (re.compile(r"^/work|^/api/v1/work"), "work.read"),
    # Secure client MESSAGING (the Communication Hub work-queue and thread pages) is staff
    # client service, not administration - it only lives under /admin because of the URL it
    # was given. The generic "^/admin" rule below demanded identity.manage, which only the
    # Administrator role holds, so a Client Service or Advisor employee whose entire job is
    # answering clients got a 403 on a link the sidebar advertised to them.
    #
    # This carve-out is deliberately the NARROWEST that makes Messages usable: the /threads
    # subtree only. Every other /admin/client-portal route - invitations, account revocation,
    # create-client, diagnostics - is genuinely administrative and still requires
    # identity.manage from the rule below. Nothing about /admin in general changes.
    #
    # The capability is DEDICATED (msgcap01), not client.read. Gating on client.read would have
    # made Messages reachable, but eleven roles hold it - including Accounting, Payroll, Reviewer
    # and Read Only, who have no business reading a client's correspondence. Reading client
    # messages is a narrower authority than reading a client record, so it has its own capability.
    # The ".read"->".write" inference below turns every mutation into
    # communications.message.write, which is granted to five roles rather than six - so viewing a
    # conversation and replying to the client are separately gated. require_capability runs ON TOP
    # of this rule, never instead of it, so the handlers' own client.read / client.write still
    # apply and /threads/diagnostics keeps its stricter observability.audit.
    #
    # Record scope is untouched and still decides WHICH threads are visible:
    # communication_hub.thread_in_staff_scope() runs per row, independent of capability. These
    # capabilities gate the door, not the contents.
    # Placement matters exactly as it does for the workflow/tax carve-outs above.
    (re.compile(r"^/admin/client-portal/threads"), "communications.message.read"),
    (re.compile(r"^/admin/audit"), "audit.read"),
    (re.compile(r"^/admin/rule-catalog"), "audit.read"),
    (re.compile(r"^/admin/(roles|user-roles)"), "role.manage"),
    (re.compile(r"^/admin/team-memberships"), "team.manage"),
    (re.compile(r"^/admin/assignments"), "assignment.manage"),
    (re.compile(r"^/admin"), "identity.manage"),
    (re.compile(r"/tasks(?:/|$)|^/tasks"), "task.read"),
    (re.compile(r"/documents(?:/|$)|^/documents"), "document.read"),
    (re.compile(r"^/microsoft|^/mail|^/calendar"), "communication.read"),
    (re.compile(r"^/portfolio"), "client.read"),
    (re.compile(r"^/wealth"), "client.read"),
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
# Phase 1A — fail-closed staff authorization. A mutating staff route matched by NEITHER the RULES map NOR
# a require_capability dependency is DENIED by default (it used to fail open). These prefixes are the only
# authenticated staff MUTATIONS that legitimately carry no capability — session/bootstrap mechanics
# (e.g. POST /auth/logout). Reads are never affected by the fail-closed rule.
STAFF_MUTATION_EXEMPT_PREFIXES = ("/auth/",)


def _staff_mutation_exempt(path: str) -> bool:
    return any(path.startswith(p) for p in STAFF_MUTATION_EXEMPT_PREFIXES)


def _route_self_protected(request) -> bool:
    """Whether the request path+method is served by a route that protects itself with require_capability.
    The self-protected matchers are built once from the app's routes and cached on ``app.state`` (routes
    are static after startup)."""
    app = request.app
    matchers = getattr(app.state, "_c360_self_protected_matchers", None)
    if matchers is None:
        from app.security.route_coverage import build_self_protected_matchers
        matchers = build_self_protected_matchers(app.routes)
        app.state._c360_self_protected_matchers = matchers
    from app.security.route_coverage import path_is_self_protected
    return path_is_self_protected(matchers, request.url.path, request.method)


RECORD_PATH = re.compile(r"^/(people|households)/(\d+)")
FIRM_WIDE_COLLECTION = re.compile(
    r"^/(?:$|api/(?:stats|search)(?:/|$)|search(?:/|$)|people/?$|households/?$|"
    r"tasks/?$|activities/?$|matches/?$|source(?:/|$)|portfolio(?:/|$)|"
    r"wealth(?:/|$)|"
    r"relationships/search(?:/|$)|api/relationships/search(?:/|$)|"
    r"relationship-entities(?:/|$))"
)
#: Firm-wide collections that STOP being firm-wide once narrowed to a single client by query
#: parameter. Only ``/tasks`` qualifies today. The firm-wide gate exists so a principal without
#: ``record.read_all`` cannot page through every client's rows; a request pinned to one
#: person/household is not that request. The narrowed rows are exactly the rows the same principal
#: already reads on /client/{id}?tab=tasks, so this discloses nothing new -- and the route itself
#: re-checks record scope, returning zero rows (never the firm-wide list) for an id out of scope.
#: Without this, every role holding task.read (Senior Tax, Tax Staff, Accounting, Payroll, Client
#: Service, Reviewer, Read Only -- none of which hold record.read_all) would be denied the very
#: page the "Create Task" quick action sends them to.
CLIENT_NARROWABLE_COLLECTION = re.compile(r"^/tasks/?$")


def _firm_wide_collection_denied(request, principal, broad_scope) -> bool:
    """Whether this request is a firm-wide collection read the principal may not perform.

    ``FIRM_WIDE_COLLECTION`` is left exactly as it was; the client-narrowing exemption is applied
    on top of it so every existing pattern assertion keeps holding.
    """
    path = request.url.path
    if not FIRM_WIDE_COLLECTION.match(path):
        return False
    if CLIENT_NARROWABLE_COLLECTION.match(path) and (
            request.query_params.get("person_id") or request.query_params.get("household_id")):
        return False
    return not principal.can(broad_scope)


def _secure_headers(response, request):
    """Stamp the standard response headers.

    dispatch() applies these to whatever call_next returns, but denial responses
    return early and never reach that block. That was harmless while a denial was
    an inert JSON body; since Release 0.9.12 renders a styled HTML 403 it matters,
    because an HTML document without `x-frame-options`/`frame-ancestors` is
    framable. The styled 404 already carries these (it is raised inside the route
    and passes through call_next) — this keeps the 403 consistent with it.
    """
    response.headers["x-request-id"] = request.state.request_id
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["referrer-policy"] = "same-origin"
    response.headers["x-frame-options"] = "DENY"
    response.headers["content-security-policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    return response


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
        return _secure_headers(render_error(request, 403, detail=detail), request)
    return _secure_headers(
        JSONResponse({"detail": detail, "request_id": request.state.request_id}, status_code=403),
        request,
    )


def _is_api_request(request) -> bool:
    """Whether an unauthenticated request should get a JSON 401 rather than a browser login redirect.

    True for anything that is not a plain browser page navigation: any ``/api/`` path, any non-GET/HEAD
    method (a redirect on a POST/PUT is useless), or a client that explicitly wants JSON and not HTML.
    A GET/HEAD to a non-API path is treated as a browser page (redirect to the staff login), regardless
    of whether the Accept header happens to include ``text/html`` — so ``Accept: */*`` navigations and
    proxied requests still get the login flow instead of a JSON body."""
    if request.url.path.startswith("/api/"):
        return True
    if request.method not in {"GET", "HEAD"}:
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


def _login_redirect(request):
    """303 to the staff OIDC entry route, preserving the requested page as a safe local ``next``."""
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(f"/auth/login?next={quote(target, safe='')}", 303)


# Capabilities that authorize the admin manual-resolution workflow (/admin/documents/unassigned): the
# workflow's own capability (client.write, which gates the review page) plus firm-wide-read / admin
# capabilities. A holder of ANY of these may READ a genuinely unassigned document (all ownership fields
# NULL) to inspect it and determine its owner — resolving the circular case where an unowned document
# could never be viewed and therefore never resolved. See _document_in_scope.
ADMIN_DOC_REVIEW_CAPABILITIES = ("client.write", "record.read_all", "identity.manage")


def _document_in_scope(connection, principal, document_id, *, write):
    """A document is in the principal's record scope if ANY of its canonical owners — person, household,
    OR organization — is in scope (``record.read_all`` bypasses via ``has_record_scope``). Household- and
    organization-owned documents have ``person_id`` NULL; checking only person_id previously denied every
    such document (including those reachable from the owning household workspace).

    Narrow admin-review exception: a holder of any ``ADMIN_DOC_REVIEW_CAPABILITIES`` may READ a document
    whose ownership is entirely NULL (person_id AND household_id AND organization_id all NULL) so they can
    inspect it in the admin manual-resolution workflow. Read-only (never for a write) and scoped strictly
    to genuinely-unassigned documents; already-owned documents fall through to the normal record-scope
    rules. This grants viewing only — it never assigns ownership (assignment is gated separately)."""
    owner = connection.execute(
        select(documents.c.person_id, documents.c.household_id, documents.c.organization_id)
        .where(documents.c.id == document_id)).mappings().first()
    if owner is None:
        return False
    if (not write
            and owner["person_id"] is None and owner["household_id"] is None
            and owner["organization_id"] is None
            and any(principal.can(cap) for cap in ADMIN_DOC_REVIEW_CAPABILITIES)):
        return True
    return any(
        entity_id is not None and has_record_scope(
            connection, principal, entity_type, entity_id,
            record_assignments=record_assignments, write=write)
        for entity_type, entity_id in (("person", owner["person_id"]),
                                       ("household", owner["household_id"]),
                                       ("organization", owner["organization_id"])))


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        # /dev-auth/* is the development-only sign-in provider. It is only *mounted*
        # in non-production with CLIENT360_DEV_AUTH set (see app.main); in production
        # there is no such route, so this prefix simply 404s. Treated as public so a
        # developer can reach the sign-in without already having a session.
        if (
            request.url.path in PUBLIC_EXACT
            or request.url.path.startswith("/static/")
            or request.url.path.startswith("/dev-auth/")
        ):
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
        if (request.url.path.startswith("/portal") or request.url.path.startswith("/api/v1/portal")
                or request.url.path.startswith("/api/portal")):
            from app.portal.service import resolve_portal_session
            portal_principal = resolve_portal_session(request.session.get("portal_session_token"))
            request.state.portal_principal = portal_principal
            if portal_principal is None:
                if "text/html" in request.headers.get("accept", ""):
                    return RedirectResponse("/portal/login", 303)
                return JSONResponse({"detail": "Portal authentication required", "request_id": request.state.request_id}, status_code=401)
            # Server-side Client Feature & Access Control enforcement, centralized here (like the staff
            # RULES map): the master portal kill switch (lifecycle status + portal_access) and the mapped
            # Core feature are checked BEFORE the route runs, so a disabled feature cannot be reached by
            # direct URL/API call. Auth/logout/security-reset paths are exempt inside evaluate().
            from app.services.features import portal_gate
            _allowed, _freason, _feature = portal_gate.evaluate(
                portal_principal, request.url.path, request.method)
            if not _allowed:
                # Never leak the internal reason to the client; the staff Access & Features screen shows it.
                # A CLIENT hitting this is usually a browser navigation, so it gets the styled 403 the
                # staff side already renders — raw JSON in the address bar is not an acceptable
                # client-facing surface. API callers keep the exact JSON body and status they had.
                from app.templating import render_error as _render_error
                from app.templating import wants_html as _wants_html
                _detail = "This part of the portal isn't available right now. Please contact your advisor if you need it."
                if _wants_html(request):
                    denied = _render_error(request, 403, detail=_detail)
                else:
                    denied = JSONResponse({"detail": _detail,
                                           "request_id": request.state.request_id}, status_code=403)
                denied.headers["x-request-id"] = request.state.request_id
                denied.headers["x-content-type-options"] = "nosniff"
                denied.headers["x-frame-options"] = "DENY"
                return denied
            # Phase 1F — FAIL CLOSED for portal MUTATIONS: an authenticated client mutation that is neither
            # feature-gated (portal_gate _RULES → client_can) nor an approved in-service-scoped mutation
            # (_MUTATION_SELF_PROTECTED) nor an exempt auth/bootstrap path is DENIED, so a newly added
            # portal mutation can never become usable on `current_portal` authentication alone. This mirrors
            # the staff-side Phase 1A invariant on the client fork. Reads (GET/HEAD/OPTIONS) are unaffected.
            if (request.method not in {"GET", "HEAD", "OPTIONS"}
                    and not portal_gate.mutation_is_covered(request.url.path, request.method)):
                write_audit_event(action="authorization.uncovered_portal_mutation_denied",
                                  entity_type="portal_route", entity_id=request.url.path,
                                  outcome="denied", request_id=request.state.request_id,
                                  ip_address=request.client.host if request.client else None,
                                  user_agent=request.headers.get("user-agent"),
                                  metadata={"portal_account_id": portal_principal.account_id,
                                            "method": request.method})
                denied = JSONResponse({"detail": "This action isn't available right now. Please contact your advisor if you need it.",
                                       "request_id": request.state.request_id}, status_code=403)
                denied.headers["x-request-id"] = request.state.request_id
                denied.headers["x-content-type-options"] = "nosniff"
                denied.headers["x-frame-options"] = "DENY"
                return denied
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
            # Browser page navigations get the staff login flow (redirect + return URL); API/JSON
            # clients and mutations get a JSON 401. Decided by route/method, not the Accept header
            # alone, so an ``Accept: */*`` navigation to a page still reaches the login.
            if _is_api_request(request):
                return JSONResponse(
                    {
                        "detail": "Authentication required",
                        "request_id": request.state.request_id,
                    },
                    status_code=401,
                )
            return _login_redirect(request)
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
        # Phase 1A — FAIL CLOSED: a mutating staff route matched by no RULES pattern (capability is None)
        # is denied UNLESS the route protects itself with require_capability or is an exempt session route.
        # This closes the fail-open default so a newly added staff mutation can never become available just
        # by being absent from RULES. Reads (GET/HEAD/OPTIONS) are deliberately unaffected.
        if (capability is None
                and request.method not in {"GET", "HEAD", "OPTIONS"}
                and not _staff_mutation_exempt(request.url.path)
                and not _route_self_protected(request)):
            return _denied(
                request,
                principal,
                "authorization.uncovered_mutation_denied",
                "route",
                request.url.path,
                "Access denied",
            )
        broad_scope = (
            "record.read_all"
            if request.method in {"GET", "HEAD", "OPTIONS"}
            else "record.write_all"
        )
        if _firm_wide_collection_denied(request, principal, broad_scope):
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
            # `record_in_scope` rather than `has_record_scope` directly: it applies the SAME
            # direct-assignment check first, then — for a READ of a person/household only —
            # the work-derived path (assignment to that client's task / tax return /
            # exception / workflow instance). Using it here keeps /people/{id} and
            # /client/{id} on one answer; they were diverging, since the client route
            # already resolves through record_in_scope.
            from app.security.authorization import record_in_scope
            with engine.connect() as connection:
                allowed = record_in_scope(
                    principal,
                    entity_type,
                    int(record_match.group(2)),
                    write=request.method not in {"GET", "HEAD", "OPTIONS"},
                    connection=connection,
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
                allowed = _document_in_scope(
                    connection, principal, int(document_match.group(1)),
                    write=request.method not in {"GET", "HEAD", "OPTIONS"})
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
