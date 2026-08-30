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

  /* Close an open row menu when focus or a click lands outside it. */
  function wireMenus() {
    document.addEventListener("click", function (ev) {
      document.querySelectorAll("details.rowmenu[open]").forEach(function (d) {
        if (!d.contains(ev.target)) { d.removeAttribute("open"); }
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") { return; }
      document.querySelectorAll("details.rowmenu[open]").forEach(function (d) {
        d.removeAttribute("open");
      });
    });
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
