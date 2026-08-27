/* Client Portal administration — the client picker on the invite form.

   External file on purpose. The site CSP is
   `default-src 'self'; frame-ancestors 'none'; base-uri 'self'` with no `script-src` and no
   'unsafe-inline', so `default-src` governs scripts and an inline <script> is blocked outright.
   Inline `on*` attributes are blocked the same way, so the activation-link select-on-click is
   bound here too.

   The four human fields — first name, last name, email, phone — ARE the search. Every non-empty
   one narrows the results (the server intersects them), staff click a match, and the fields are
   filled in from that client's record. The person id is hidden implementation state: it is never
   typed, never displayed, and the server re-resolves and re-authorizes it on submit regardless of
   what the browser sends.

   Progressive enhancement: with JavaScript blocked the page still renders and the server still
   refuses a submission with no client selected, showing the same message. */
(function () {
  "use strict";

  var MIN_TERM = 2;
  var DEBOUNCE_MS = 200;
  var SEARCH_URL = "/admin/client-portal/client-search";
  var NO_RESULTS = "No clients found. Check the spelling, or try an email or phone number.";
  /* Deliberately generic. A server error body may carry internal detail, so it is never shown. */
  var SEARCH_ERROR = "Search is unavailable right now. Please try again in a moment.";
  var NO_SELECTION = "Select a client before sending the invitation.";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var form = document.getElementById("invite-form");
    var results = document.getElementById("client-results");
    if (!form || !results) {
      bindActivationLink();
      return;
    }

    var f = {
      first: document.getElementById("sel-first"),
      last: document.getElementById("sel-last"),
      email: document.getElementById("sel-email"),
      phone: document.getElementById("sel-phone"),
      personId: document.getElementById("sel-person-id"),
      household: document.getElementById("sel-household"),
      selected: document.getElementById("selected-client"),
      error: document.getElementById("invite-error")
    };
    /* Editing any of these means "I am looking for a different person", so a stale selection must
       not survive. Email is deliberately NOT here: once a client is chosen it is the invitation
       address, which staff are allowed to change without re-picking the client. */
    var IDENTIFYING = ["first", "last", "phone"];

    var timer = null;
    var latest = 0;          // only the newest request may render
    var chosen = false;

    function clear(node) {
      while (node.firstChild) { node.removeChild(node.firstChild); }
    }

    function message(node, text) {
      clear(node);
      var p = document.createElement("p");
      p.className = "subtle";
      p.textContent = text;
      node.appendChild(p);
    }

    function showError(text) {
      if (!f.error) { return; }
      f.error.textContent = text;
      f.error.hidden = !text;
    }

    function clearSelection() {
      chosen = false;
      f.personId.value = "";
      f.household.textContent = "";
      if (f.selected) {
        f.selected.textContent = "";
        f.selected.hidden = true;
      }
    }

    function detailLine(person) {
      return [person.email, person.phone, person.household_name, person.location]
        .filter(Boolean).join(" · ");
    }

    function select(person) {
      /* Fill the human fields from the record. Assigning .value fires no input event, so this
         does not re-trigger a search or clear the selection we are establishing. */
      f.first.value = person.first_name || "";
      f.last.value = person.last_name || "";
      f.email.value = person.email || "";
      f.phone.value = person.phone || "";
      f.personId.value = person.person_id;      // hidden implementation state only
      f.household.textContent = person.household_name
        ? "Household: " + person.household_name
        : "No household on record yet.";
      if (f.selected) {
        f.selected.textContent = "Selected: " + (person.full_name || "") +
          (person.household_name ? " — " + person.household_name : "");
        f.selected.hidden = false;
      }
      chosen = true;
      clear(results);
      showError("");
    }

    function render(rows) {
      if (!rows.length) { message(results, NO_RESULTS); return; }
      clear(results);
      rows.forEach(function (person) {
        var button = document.createElement("button");
        button.type = "button";                 // never submits the form it lives in
        button.className = "btn secondary client-result";
        var name = document.createElement("b");
        name.textContent = person.full_name || "";
        var detail = document.createElement("span");
        detail.className = "subtle";
        /* textContent, never innerHTML: client data is never parsed as markup. */
        detail.textContent = detailLine(person);
        button.appendChild(name);
        button.appendChild(document.createElement("br"));
        button.appendChild(detail);
        button.addEventListener("click", function () { select(person); });
        results.appendChild(button);
      });
    }

    function currentTerms() {
      return {
        first_name: (f.first.value || "").trim(),
        last_name: (f.last.value || "").trim(),
        email: (f.email.value || "").trim(),
        phone: (f.phone.value || "").trim()
      };
    }

    function queryString(terms) {
      var parts = [];
      Object.keys(terms).forEach(function (key) {
        if (terms[key].length >= MIN_TERM) {
          parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(terms[key]));
        }
      });
      return parts;
    }

    function search() {
      var parts = queryString(currentTerms());
      if (!parts.length) { clear(results); return; }
      latest += 1;
      var token = latest;
      fetch(SEARCH_URL + "?" + parts.join("&"), { credentials: "same-origin" })
        .then(function (response) {
          if (!response.ok) { throw new Error("search request failed"); }
          return response.json();
        })
        .then(function (data) {
          if (token !== latest) { return; }     // a newer search already answered
          render((data && data.results) || []);
        })
        .catch(function () {
          if (token !== latest) { return; }
          message(results, SEARCH_ERROR);       // generic: no server text is surfaced
        });
    }

    function schedule() {
      if (timer) { clearTimeout(timer); }
      timer = setTimeout(search, DEBOUNCE_MS);
    }

    Object.keys(f).forEach(function (key) {
      if (["first", "last", "email", "phone"].indexOf(key) === -1 || !f[key]) { return; }
      f[key].addEventListener("input", function () {
        if (IDENTIFYING.indexOf(key) !== -1 && chosen) {
          clearSelection();                     // a different person is being looked for
        }
        if (chosen && key === "email") { return; }   // editing the invitation address only
        showError("");
        latest += 1;                            // invalidate anything already in flight
        schedule();
      });
    });

    form.addEventListener("submit", function (event) {
      if (!(f.personId.value || "").trim()) {
        /* The server refuses this too, with the same wording — this only spares staff a round
           trip. It is not the protection. */
        event.preventDefault();
        showError(NO_SELECTION);
        (f.first || form).focus();
      }
    });

    bindActivationLink();
  });

  function bindActivationLink() {
    var activation = document.getElementById("activation-link");
    if (!activation) { return; }
    function selectAll() { activation.select(); }
    activation.addEventListener("click", selectAll);
    activation.addEventListener("focus", selectAll);
  }
})();
