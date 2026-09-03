/* Documents screen — preview drawer. PROGRESSIVE ENHANCEMENT ONLY.
 *
 * Every row link and every panel tab in the markup is a real URL that renders the panel fragment
 * as its own page. This script intercepts those links and swaps the fragment into the drawer
 * instead, so selecting a document does not lose the user's place in a 291-row table. With
 * JavaScript disabled nothing here is required: the links still navigate, and the panel still
 * renders.
 *
 * The filter bar is deliberately NOT enhanced. It is a server-side GET form, so the URL always
 * describes exactly what is on screen and a filtered view can be linked, bookmarked and reloaded.
 * The only convenience added is auto-submitting the selects, which saves a click without changing
 * where the filtering happens.
 *
 * External file because the CSP is `default-src 'self'` with no 'unsafe-inline'.
 */
(function () {
  "use strict";

  function drawerOf(screen) { return screen.querySelector("[data-docdrawer]"); }
  function bodyOf(screen) { return screen.querySelector("[data-docdrawer-body]"); }
  function emptyOf(screen) { return screen.querySelector("[data-docdrawer-empty]"); }

  /* The panel is a permanent third pane, not a drawer that appears: it shows either its
     "select a document" state or a document. Toggling those two keeps the workspace's column
     widths fixed, so choosing a document never reflows the list underneath the cursor. */
  function showEmpty(screen, empty) {
    var placeholder = emptyOf(screen);
    if (placeholder) { placeholder.hidden = !empty; }
  }

  function markSelected(screen, row) {
    screen.querySelectorAll("[data-doc-row].is-selected").forEach(function (r) {
      r.classList.remove("is-selected");
    });
    if (row) { row.classList.add("is-selected"); }
  }

  function closeDrawer(screen) {
    var drawer = drawerOf(screen);
    if (!drawer) { return; }
    bodyOf(screen).innerHTML = "";
    showEmpty(screen, true);
    markSelected(screen, null);
  }

  /* Fetch a panel fragment and place it in the drawer.
   *
   * `token` guards against out-of-order responses: clicking three rows quickly must leave the
   * THIRD panel on screen, not whichever request happened to finish last. */
  var token = 0;

  function load(screen, url, row) {
    var drawer = drawerOf(screen);
    var body = bodyOf(screen);
    if (!drawer || !body) { return; }

    var mine = ++token;
    showEmpty(screen, false);
    markSelected(screen, row);
    body.setAttribute("aria-busy", "true");

    fetch(url, { credentials: "same-origin", headers: { "Accept": "text/html" } })
      .then(function (res) {
        if (!res.ok) { throw new Error(String(res.status)); }
        return res.text();
      })
      .then(function (html) {
        if (mine !== token) { return; }               // superseded by a later click
        body.innerHTML = html;
        body.removeAttribute("aria-busy");
        drawer.scrollTop = 0;      /* a tall previous panel must not leave the new one scrolled */
        var heading = body.querySelector(".docpanel-head h2");
        if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus(); }
      })
      .catch(function () {
        if (mine !== token) { return; }
        showEmpty(screen, true);
        /* Never strand the user in a half-open drawer: fall back to the plain navigation the
           link would have done on its own. */
        window.location.href = url;
      });
  }

  function init(screen) {
    /* The placeholder is visible in the server-rendered markup so a no-script user sees the
       panel explain itself. Script hides nothing until a document is chosen. */
    showEmpty(screen, !bodyOf(screen).firstElementChild);

    /* Row selection and panel tabs. Delegated, so rows swapped in by a later page load (or a
       panel that replaces itself) keep working without re-binding. */
    screen.addEventListener("click", function (ev) {
      var closer = ev.target.closest("[data-docpanel-close]");
      if (closer) { ev.preventDefault(); closeDrawer(screen); return; }

      var tab = ev.target.closest("[data-docpanel-tab]");
      if (tab) {
        ev.preventDefault();
        load(screen, tab.getAttribute("href"),
             screen.querySelector("[data-doc-row].is-selected"));
        return;
      }

      var opener = ev.target.closest("[data-doc-open]");
      if (!opener) { return; }
      /* Let the user escape to a real tab/window when they ask for one. */
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) { return; }
      ev.preventDefault();
      load(screen, opener.getAttribute("href"), opener.closest("[data-doc-row]"));
    });

    /* Auto-submit the facet selects. The form still submits normally on Apply, so this only
       removes a click — it does not become the only way to filter. */
    screen.querySelectorAll("[data-autosubmit]").forEach(function (sel) {
      sel.addEventListener("change", function () {
        var form = sel.closest("form");
        if (form) { form.submit(); }
      });
    });

    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { closeDrawer(screen); }
    });
  }

  function boot() { document.querySelectorAll("[data-docscreen]").forEach(init); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
