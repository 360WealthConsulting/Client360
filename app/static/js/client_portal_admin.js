/* Client Portal administration — staff client search for the invite form.

   This lives in an external file on purpose. The site CSP is
   `default-src 'self'; frame-ancestors 'none'; base-uri 'self'` with no `script-src` and no
   'unsafe-inline', so `default-src` governs scripts and an inline <script> is blocked outright —
   which is exactly why typing in the search box produced no request at all. Same-origin
   `<script src>` satisfies 'self' with no CSP change. Inline `on*` attributes are blocked for the
   same reason, so the activation-link select-on-click is bound here too.

   Progressive enhancement: without JavaScript the page still renders and every server-side
   control (record scope, person re-resolution, access validation) is unchanged — this only
   provides the picker. No framework, no dependencies, matching app/static/js/app.js. */
(function () {
  "use strict";

  var MIN_QUERY = 2;
  var DEBOUNCE_MS = 200;
  var SEARCH_URL = "/admin/client-portal/client-search";
  var NO_RESULTS = "No clients found. Check the spelling, or search by email or phone number.";
  /* Deliberately generic. A server error body may carry internal detail, so it is never shown. */
  var SEARCH_ERROR = "Search is unavailable right now. Please try again in a moment.";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var box = document.getElementById("client-search");
    var results = document.getElementById("client-results");
    var form = document.getElementById("invite-form");
    if (!box || !results || !form) {
      bindActivationLink();
      return;                       // not the invite page (or the form is not rendered)
    }

    var fields = {
      personId: document.getElementById("sel-person-id"),
      first: document.getElementById("sel-first"),
      last: document.getElementById("sel-last"),
      phone: document.getElementById("sel-phone"),
      email: document.getElementById("sel-email"),
      household: document.getElementById("sel-household")
    };

    var timer = null;
    /* Monotonic token: only the newest request may render, so a slow earlier response can never
       overwrite the results of a later search. */
    var latest = 0;

    function clear(node) {
      while (node.firstChild) {
        node.removeChild(node.firstChild);
      }
    }

    function message(text) {
      clear(results);
      var p = document.createElement("p");
      p.className = "subtle";
      p.textContent = text;
      results.appendChild(p);
    }

    function detailLine(person) {
      /* Enough to tell two people with the same name apart, without showing internal ids. */
      return [person.email, person.phone, person.household_name, person.location]
        .filter(Boolean).join(" · ");
    }

    function select(person) {
      fields.personId.value = person.person_id;
      fields.first.value = person.first_name || "";
      fields.last.value = person.last_name || "";
      fields.phone.value = person.phone || "";
      fields.email.value = person.email || "";
      fields.household.textContent = person.household_name
        ? "Household: " + person.household_name
        : "No household on record yet.";
      clear(results);
      box.value = person.full_name || "";
      form.hidden = false;
    }

    function render(rows) {
      if (!rows.length) {
        message(NO_RESULTS);
        return;
      }
      clear(results);
      rows.forEach(function (person) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "btn secondary client-result";
        var name = document.createElement("b");
        name.textContent = person.full_name || "";
        var detail = document.createElement("span");
        detail.className = "subtle";
        /* textContent, never innerHTML: server-supplied client data is never parsed as markup. */
        detail.textContent = detailLine(person);
        button.appendChild(name);
        button.appendChild(document.createElement("br"));
        button.appendChild(detail);
        button.addEventListener("click", function () { select(person); });
        results.appendChild(button);
      });
    }

    function search(query, token) {
      fetch(SEARCH_URL + "?q=" + encodeURIComponent(query), { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) {
            /* Status only — the response body is never read, so no server text can be rendered. */
            throw new Error("search request failed");
          }
          return response.json();
        })
        .then(function (data) {
          if (token !== latest) { return; }
          render((data && data.results) || []);
        })
        .catch(function () {
          if (token !== latest) { return; }
          message(SEARCH_ERROR);
        });
    }

    box.addEventListener("input", function () {
      /* Any edit invalidates the current selection: the form must never post a person the staff
         member is no longer looking at. The server re-resolves it regardless. */
      form.hidden = true;
      fields.personId.value = "";
      if (timer) { clearTimeout(timer); }
      latest += 1;                                  // cancels anything already in flight
      var query = box.value.trim();
      if (query.length < MIN_QUERY) {
        clear(results);
        return;
      }
      var token = latest;
      timer = setTimeout(function () { search(query, token); }, DEBOUNCE_MS);
    });

    bindActivationLink();
  });

  function bindActivationLink() {
    /* Select-on-click for the one-time activation link. Replaces an inline onclick attribute,
       which the CSP blocks exactly like an inline <script>. */
    var activation = document.getElementById("activation-link");
    if (!activation) { return; }
    function selectAll() { activation.select(); }
    activation.addEventListener("click", selectAll);
    activation.addEventListener("focus", selectAll);
  }
})();
