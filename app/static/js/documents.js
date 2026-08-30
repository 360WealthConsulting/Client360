/* Client document workspace — progressive enhancement ONLY.
 *
 * Narrows the rows ALREADY rendered on the page. It issues no request, adds no query parameter and
 * knows nothing about the API: with JavaScript disabled the full server-rendered list is still
 * there and every action still works. External file because the CSP is `default-src 'self'` with
 * no 'unsafe-inline'.
 */
(function () {
  "use strict";

  function init(toolbar) {
    var root = toolbar.closest(".c360-panel") || document;
    var table = root.querySelector("[data-docs-table]");
    var noResult = root.querySelector("[data-docs-noresult]");
    var count = toolbar.querySelector("[data-docs-count]");
    if (!table) { return; }

    var rows = Array.prototype.slice.call(table.querySelectorAll("[data-doc-row]"));
    var search = toolbar.querySelector("[data-docs-search]");
    var facets = Array.prototype.slice.call(toolbar.querySelectorAll("[data-docs-facet]"));
    var total = rows.length;

    function apply() {
      var q = (search && search.value || "").trim().toLowerCase();
      var want = {};
      facets.forEach(function (f) { want[f.getAttribute("data-docs-facet")] = f.value; });

      var shown = 0;
      rows.forEach(function (row) {
        var ok = true;
        if (q && (row.getAttribute("data-haystack") || "").indexOf(q) === -1) { ok = false; }
        if (ok && want.type && row.getAttribute("data-type") !== want.type) { ok = false; }
        if (ok && want.year && row.getAttribute("data-year") !== want.year) { ok = false; }
        if (ok && want.source) {
          var srcs = (row.getAttribute("data-source") || "").split(" ");
          if (srcs.indexOf(want.source) === -1) { ok = false; }
        }
        row.hidden = !ok;
        if (ok) { shown += 1; }
      });

      if (count) {
        count.innerHTML = shown === total
          ? "<b>" + total + "</b> documents"
          : "<b>" + shown + "</b> of " + total + " documents";
      }
      if (noResult) { noResult.hidden = shown !== 0 || total === 0; }
      if (table) { table.hidden = shown === 0 && total > 0; }
    }

    if (search) { search.addEventListener("input", apply); }
    facets.forEach(function (f) { f.addEventListener("change", apply); });
    apply();
  }

  /* Row overflow menu.
   *
   * The documents table scrolls horizontally, and overflow-x:auto forces overflow-y to a
   * non-visible value - so an absolutely positioned menu is clipped by that scroll container and
   * spills into the rows instead of floating over them. Removing the scroller is not an option,
   * and CSS alone cannot escape an ancestor's overflow.
   *
   * So an OPEN menu is promoted to position:fixed with coordinates measured from its button.
   * Fixed positioning is not clipped by ancestor overflow. The markup stays <details>/<summary>,
   * so keyboard operation and the no-JS fallback are unchanged; without this script the menu is
   * simply the absolute one it has always been.
   *
   * Coordinates are set through the CSSOM. That is deliberate: the CSP is `default-src 'self'`
   * with no 'unsafe-inline', which blocks style="" attributes in markup but does not govern
   * programmatic style mutation.
   */
  var GAP = 4;

  function place(details) {
    var summary = details.querySelector("summary");
    var menu = details.querySelector(".menu");
    if (!summary || !menu) { return; }

    menu.classList.add("is-floating");
    menu.style.top = "0px";
    menu.style.left = "0px";                       // measure unconstrained, then place

    var btn = summary.getBoundingClientRect();
    var box = menu.getBoundingClientRect();
    var vw = document.documentElement.clientWidth;
    var vh = document.documentElement.clientHeight;

    // Right-align to the button, then clamp so it can never leave the viewport.
    var left = btn.right - box.width;
    if (left + box.width > vw - GAP) { left = vw - GAP - box.width; }
    if (left < GAP) { left = GAP; }

    // Below the button, or flipped above when there is not room underneath.
    var top = btn.bottom + GAP;
    if (top + box.height > vh - GAP) {
      var above = btn.top - GAP - box.height;
      top = above >= GAP ? above : Math.max(GAP, vh - GAP - box.height);
    }

    menu.style.left = Math.round(left) + "px";
    menu.style.top = Math.round(top) + "px";
  }

  function unplace(details) {
    var menu = details.querySelector(".menu");
    if (!menu) { return; }
    menu.classList.remove("is-floating");
    menu.style.top = "";
    menu.style.left = "";
  }

  function closeAll(except) {
    document.querySelectorAll("details.rowmenu[open]").forEach(function (d) {
      if (d !== except) { d.removeAttribute("open"); unplace(d); }
    });
  }

  function wireMenus() {
    /* One menu at a time, and position it the moment it opens. */
    document.addEventListener("toggle", function (ev) {
      var d = ev.target;
      if (!d || !d.classList || !d.classList.contains("rowmenu")) { return; }
      if (d.open) { closeAll(d); place(d); } else { unplace(d); }
    }, true);

    document.addEventListener("click", function (ev) {
      document.querySelectorAll("details.rowmenu[open]").forEach(function (d) {
        if (!d.contains(ev.target)) { d.removeAttribute("open"); unplace(d); }
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") { return; }
      closeAll(null);
    });

    /* A fixed menu does not travel with the content it belongs to, so close it rather than let
       it float away from its row. Capture phase catches the table's own scroller too. */
    window.addEventListener("scroll", function () { closeAll(null); }, true);
    window.addEventListener("resize", function () { closeAll(null); });
  }

  function boot() {
    document.querySelectorAll("[data-docs-toolbar]").forEach(init);
    wireMenus();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
