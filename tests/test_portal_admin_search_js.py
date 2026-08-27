"""The invite-form client search must survive the site CSP, and behave when the network doesn't.

Root cause of the production defect: the search JavaScript was an inline ``<script>`` in
``admin/client_portal.html``. The site CSP is
``default-src 'self'; frame-ancestors 'none'; base-uri 'self'`` — no ``script-src``, no
``'unsafe-inline'`` — so ``default-src`` governs scripts and the browser refused to execute it. No
``/client-search`` request was ever issued; the backend was never involved.

The fix is an external same-origin file, which satisfies ``'self'`` with no CSP change at all.
Inline ``on*`` handlers and ``style="..."`` attributes are blocked by the same directive, so those
were moved out too.

The behavioural tests below run the real file in Node against a minimal DOM stub, so "empty results
render a no-results state" and "a failed request renders a generic error" are proven rather than
grepped for. They skip if Node is absent; the structural assertions still hold everywhere.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "app" / "templates" / "admin" / "client_portal.html"
SCRIPT = REPO / "app" / "static" / "js" / "client_portal_admin.js"
CSS = REPO / "app" / "static" / "css" / "app.css"
MIDDLEWARE = REPO / "app" / "security" / "middleware.py"

SEARCH_URL = "/admin/client-portal/client-search"


# --- the CSP itself is untouched ------------------------------------------------------

def test_the_content_security_policy_is_not_weakened():
    """No 'unsafe-inline', no nonce, no relaxed script-src — the fix must not touch the policy."""
    source = MIDDLEWARE.read_text()
    assert "default-src 'self'; frame-ancestors 'none'; base-uri 'self'" in source
    for weakening in ("unsafe-inline", "unsafe-eval", "nonce-", "script-src", "'unsafe-hashes'"):
        assert weakening not in source, f"the CSP was weakened with {weakening}"


def test_the_template_has_no_inline_script_or_event_handlers():
    """An inline <script> is exactly what the CSP blocked; inline on* handlers are blocked too."""
    html = TEMPLATE.read_text()
    assert "<script>" not in html, "an inline <script> block is back and will be CSP-blocked"
    for handler in ("onclick=", "oninput=", "onchange=", "onload=", "onfocus=", "onsubmit="):
        assert handler not in html, f"inline {handler} is blocked by default-src 'self'"
    assert "javascript:" not in html


def test_the_template_has_no_inline_style_attributes():
    """style="..." is governed by the same default-src 'self' directive."""
    assert "style=" not in TEMPLATE.read_text()


def test_the_external_script_is_referenced_same_origin():
    html = TEMPLATE.read_text()
    assert '<script src="/static/js/client_portal_admin.js" defer></script>' in html
    assert html.count("<script") == 1, "exactly one script reference expected"
    # Same-origin only: a third-party host would be blocked by default-src 'self' as well.
    assert "//" not in html.split('src="')[1].split('"')[0]


def test_the_script_file_exists_and_follows_the_static_convention():
    assert SCRIPT.is_file()
    assert (REPO / "app" / "static" / "js" / "app.js").is_file()   # the pattern being followed
    base = (REPO / "app" / "templates" / "base.html").read_text()
    assert '<script src="/static/js/app.js" defer></script>' in base


def test_the_replaced_inline_styles_have_real_css_rules():
    css, html = CSS.read_text(), TEMPLATE.read_text()
    for klass in ("portal-invite", "portal-invite-form",
                  "portal-invite-full", "portal-access-option", "portal-activation"):
        assert f'class="{klass}"' in html or f'{klass}"' in html, f"{klass} unused in the template"
        assert f".{klass}" in css, f"{klass} has no CSS rule, so the layout silently regresses"
    assert ".client-result" in css, "the JS-applied result class has no rule"


# --- the script talks to the right endpoint, defensively --------------------------------

def test_the_script_targets_the_authenticated_search_endpoint():
    js = SCRIPT.read_text()
    assert SEARCH_URL in js
    assert 'credentials: "same-origin"' in js, "the request would drop the staff session cookie"
    assert "encodeURIComponent" in js


def _code_only(js: str) -> str:
    """The script with comments stripped, so prose about a construct is not mistaken for it."""
    import re
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def test_the_script_never_renders_server_supplied_markup_or_error_text():
    code = _code_only(SCRIPT.read_text())
    assert "innerHTML" not in code, "server-supplied client data must not be parsed as markup"
    assert "outerHTML" not in code and "insertAdjacentHTML" not in code
    assert "response.text()" not in code, "a server error body must never reach the page"
    assert "textContent" in code
    assert "eval(" not in code and "new Function" not in code


def test_the_script_keeps_the_two_character_threshold_and_debounce():
    js = SCRIPT.read_text()
    assert "MIN_TERM = 2" in js
    assert "DEBOUNCE_MS" in js and "setTimeout" in js and "clearTimeout" in js


# --- behaviour, executed in Node ---------------------------------------------------------

def _harness(fetch_impl: str, *, activation: bool = False) -> str:
    """A minimal DOM + fetch stub, then the REAL script, then one simulated search.

    Mirrors the four-field invite form: first/last/email/phone are typeable inputs that drive
    the search, and the person id is hidden state the script fills in on selection."""
    return textwrap.dedent("""
        function el(id, tag) {
          return {
            id: id, tagName: tag || "div", value: "", textContent: "", className: "",
            hidden: false, type: "", children: [], firstChild: null, _on: {},
            addEventListener(ev, fn) { this._on[ev] = fn; },
            appendChild(c) { this.children.push(c); this.firstChild = this.children[0]; return c; },
            removeChild(c) {
              this.children = this.children.filter(x => x !== c);
              this.firstChild = this.children[0] || null;
            },
            focus() { this.focused = true; },
            select() { this.selected = true; }
          };
        }
        const nodes = {};
        ["invite-form","client-results","sel-first","sel-last","sel-email","sel-phone",
         "sel-person-id","sel-household","selected-client","invite-error"%%ACTIVATION%%]
          .forEach(id => nodes[id] = el(id));
        nodes["selected-client"].hidden = true;
        nodes["invite-error"].hidden = true;
        global.document = {
          readyState: "complete",
          getElementById: id => nodes[id] || null,
          createElement: tag => el(null, tag),
          addEventListener: () => {}
        };
        global.setTimeout = (fn) => { fn(); return 1; };   // run debounced work immediately
        global.clearTimeout = () => {};
        %%FETCH%%
        require(%%SCRIPT%%);

        function textOf(node) {
          if (node.textContent) return node.textContent;
          return node.children.map(textOf).join(" ");
        }
        const results = nodes["client-results"];
        const type = (id, value) => { nodes[id].value = value; nodes[id]._on.input(); };
        const done = () => {
          console.log(JSON.stringify({
            text: results.children.map(textOf).join(" | "),
            count: results.children.length,
            personId: nodes["sel-person-id"].value,
            first: nodes["sel-first"].value,
            last: nodes["sel-last"].value,
            phone: nodes["sel-phone"].value,
            email: nodes["sel-email"].value,
            household: nodes["sel-household"].textContent,
            selected: nodes["selected-client"].textContent,
            selectedHidden: nodes["selected-client"].hidden,
            error: nodes["invite-error"].textContent,
            errorHidden: nodes["invite-error"].hidden,
            classes: results.children.map(c => c.className).join(","),
            activationSelected: !!(nodes["activation-link"] || {}).selected
          }));
        };
        type("sel-last", "%%QUERY%%");
        %%AFTER%%
    """).replace("%%FETCH%%", fetch_impl).replace(
        "%%SCRIPT%%", json.dumps(str(SCRIPT))).replace(
        "%%ACTIVATION%%", ',"activation-link"' if activation else "")




def _run_node(body: str, tmp_path, *, query="smith", after="setImmediate(done);") -> dict:
    node = shutil.which("node")
    if not node:                                          # pragma: no cover
        pytest.skip("node is not installed; structural assertions still cover this file")
    script = tmp_path / "harness.js"
    script.write_text(body.replace("%%QUERY%%", query).replace("%%AFTER%%", after))
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"harness failed:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_empty_results_render_a_clear_no_results_state(tmp_path):
    out = _run_node(_harness("""
        global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({results: []}) });
    """), tmp_path)
    assert "No clients found" in out["text"]
    assert out["count"] == 1
    assert out["personId"] == "" and out["selectedHidden"] is True


def test_a_failed_request_renders_a_generic_error_without_server_text(tmp_path):
    """A 500 with a leaky body must produce a generic message and never echo the body."""
    out = _run_node(_harness("""
        global.fetch = () => Promise.resolve({
          ok: false, status: 500,
          text: () => Promise.resolve("Traceback: psycopg2 OperationalError at 10.0.0.5"),
          json: () => Promise.resolve({detail: "Traceback: psycopg2 OperationalError at 10.0.0.5"})
        });
    """), tmp_path)
    assert "Search is unavailable" in out["text"]
    assert "Traceback" not in out["text"] and "psycopg2" not in out["text"]
    assert "10.0.0.5" not in out["text"]


def test_a_network_failure_renders_the_same_generic_error(tmp_path):
    out = _run_node(_harness("""
        global.fetch = () => Promise.reject(new Error("ECONNREFUSED 10.0.0.5:8360"));
    """), tmp_path)
    assert "Search is unavailable" in out["text"]
    assert "ECONNREFUSED" not in out["text"]


def test_results_render_duplicate_safe_details_and_selection_populates_the_form(tmp_path):
    out = _run_node(_harness("""
        global.fetch = (url) => {
          global.__url = url;
          return Promise.resolve({ ok: true, json: () => Promise.resolve({results: [{
            person_id: 4242, first_name: "Chris", last_name: "Dup", full_name: "Chris Dup",
            email: "chris.a@example.com", phone: "555-0001",
            household_name: "Alpha Household", location: "Austin"}]}) });
        };
    """), tmp_path, after="setImmediate(() => { "
                          "document.getElementById('client-results').children[0]._on.click(); "
                          "console.log(JSON.stringify({url: global.__url})); done(); });")
    assert out["personId"] == 4242, "selection did not establish the hidden person id"
    assert out["first"] == "Chris" and out["last"] == "Dup"
    assert out["phone"] == "555-0001" and out["email"] == "chris.a@example.com"
    assert out["household"] == "Household: Alpha Household"
    assert "Chris Dup" in out["selected"] and out["selectedHidden"] is False, (
        "the chosen client is not clearly indicated")
    assert out["count"] == 0, "the result list should clear once a client is chosen"


def test_the_search_request_goes_to_the_authenticated_endpoint(tmp_path):
    node = shutil.which("node")
    if not node:                                          # pragma: no cover
        pytest.skip("node is not installed")
    body = _harness("""
        global.fetch = (url, opts) => {
          console.log(JSON.stringify({url: url, creds: opts && opts.credentials}));
          return Promise.resolve({ ok: true, json: () => Promise.resolve({results: []}) });
        };
    """)
    script = tmp_path / "h.js"
    script.write_text(body.replace("%%QUERY%%", "o'brien & co").replace("%%AFTER%%", ""))
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    call = json.loads(proc.stdout.strip().splitlines()[0])
    assert call["url"].startswith(SEARCH_URL + "?last_name="), call["url"]
    assert call["creds"] == "same-origin"
    assert "o'brien & co" not in call["url"], "the term was not URL-encoded"
    assert "%26" in call["url"] or "%27" in call["url"]


def test_every_filled_field_is_sent_so_the_server_can_narrow(tmp_path):
    """All four human fields drive one search — the server intersects them."""
    node = shutil.which("node")
    if not node:                                          # pragma: no cover
        pytest.skip("node is not installed")
    body = _harness("""
        global.fetch = (url) => {
          console.log(JSON.stringify({url: url}));
          return Promise.resolve({ ok: true, json: () => Promise.resolve({results: []}) });
        };
    """)
    script = tmp_path / "multi.js"
    script.write_text(body.replace("%%QUERY%%", "Shelton").replace(
        "%%AFTER%%", "type('sel-first', 'Michael');"
                     "type('sel-email', 'm@example.com');"
                     "type('sel-phone', '540-555-1212');"))
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    last = json.loads(proc.stdout.strip().splitlines()[-1])["url"]
    for key in ("first_name=", "last_name=", "phone=", "email="):
        assert key in last, f"{key} was not sent"


def test_a_short_term_issues_no_request(tmp_path):
    out = _run_node(_harness("""
        global.fetch = () => { throw new Error("a 1-character term must not search"); };
    """), tmp_path, query="a")
    assert out["count"] == 0 and out["text"] == ""


def test_submitting_with_no_selection_is_blocked_client_side(tmp_path):
    out = _run_node(_harness("""
        global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({results: []}) });
    """), tmp_path, after="document.getElementById('invite-form')._on.submit("
                          "{preventDefault: () => {}}); setImmediate(done);")
    assert out["error"] == "Select a client before sending the invitation."
    assert out["errorHidden"] is False


def test_editing_an_identifying_field_clears_a_previous_selection(tmp_path):
    """Changing first/last/phone after choosing invalidates the selection so the wrong person can
    never be invited. Editing the EMAIL must not, because it is the invitation address."""
    fetch = """
        global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({results: [{
          person_id: 77, first_name: "Chris", last_name: "Dup", full_name: "Chris Dup",
          email: "chris@example.com", phone: "555-0001", household_name: "HH"}]}) });
    """
    pick = "setImmediate(() => { document.getElementById('client-results').children[0]._on.click(); "
    cleared = _run_node(_harness(fetch), tmp_path,
                        after=pick + "type('sel-last', 'Different'); setImmediate(done); });")
    assert cleared["personId"] == "", "a stale selection survived an identifying edit"
    assert cleared["selectedHidden"] is True

    kept = _run_node(_harness(fetch), tmp_path,
                     after=pick + "type('sel-email', 'other@example.com'); setImmediate(done); });")
    assert kept["personId"] == 77, "editing the invitation email dropped the client selection"
    assert kept["email"] == "other@example.com"


def test_a_stale_slower_response_cannot_overwrite_a_newer_search(tmp_path):
    """Two searches in flight: the FIRST resolves last and must be discarded."""
    out = _run_node(_harness("""
        let call = 0;
        global.fetch = () => {
          call += 1;
          if (call === 1) {
            return new Promise(res => setImmediate(() => setImmediate(() => setImmediate(
              () => res({ ok: true, json: () => Promise.resolve({results: [
                {person_id: 1, full_name: "STALE RESULT"}]}) })))));
          }
          return Promise.resolve({ ok: true, json: () => Promise.resolve({results: [
            {person_id: 2, full_name: "FRESH RESULT"}]}) });
        };
    """), tmp_path, after="type('sel-last', 'smithe');"
                          "setTimeout(() => setImmediate(() => setImmediate(() => "
                          "setImmediate(() => setImmediate(done)))), 0);")
    assert "FRESH RESULT" in out["text"]
    assert "STALE RESULT" not in out["text"], "a stale response overwrote newer results"


def test_the_activation_link_selects_on_click_without_an_inline_handler(tmp_path):
    out = _run_node(_harness("""
        global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({results: []}) });
    """, activation=True), tmp_path,
        after="document.getElementById('activation-link')._on.click(); setImmediate(done);")
    assert out["activationSelected"] is True


# --- the server contract is unchanged ----------------------------------------------------

def test_the_invitation_submission_contract_is_unchanged():
    """This is a CSP/asset fix: the form still posts the same fields to the same handler."""
    import inspect

    from app.routes.portal_admin import portal_admin_invite_form

    html = TEMPLATE.read_text()
    assert 'action="/admin/client-portal/invite-form"' in html and 'method="post"' in html
    assert 'type="hidden" name="person_id"' in html
    assert 'name="email"' in html and 'name="access_type"' in html
    for gone in ('name="household_id"', 'name="organization_id"', 'name="display_name"'):
        assert gone not in html
    assert set(inspect.signature(portal_admin_invite_form).parameters) >= {
        "person_id", "email", "access_type"}


def test_server_side_resolution_and_handoff_are_untouched():
    import inspect

    from app.portal import invite_targets
    from app.routes import portal_admin

    handler = inspect.getsource(portal_admin.portal_admin_invite_form)
    assert "resolve_invite_target" in handler and "validate_access_type" in handler
    assert "_remember_activation_url" in handler
    assert invite_targets.PERMITTED_ACCESS_TYPES == {"self", "joint"}
    assert "record_in_scope" in inspect.getsource(invite_targets.resolve_invite_target)
