/* Client360 UI enhancements (Release 0.9.12). Progressive enhancement only —
   every page works fully without JavaScript; this adds client-side niceties on top.
   No framework, no dependencies. */
(function () {
  "use strict";

  /* ---- Sortable tables -------------------------------------------------
     Click (or Enter/Space) a column header to sort the rows it heads.
     Numeric columns (th.num) sort numerically; others sort as text with
     natural number ordering. Sorting is announced via aria-sort. */
  function cellText(row, idx) {
    var cell = row.children[idx];
    if (!cell) return "";
    var explicit = cell.getAttribute("data-sort");
    return (explicit !== null ? explicit : cell.textContent).trim();
  }

  function comparer(idx, asc, numeric) {
    return function (a, b) {
      var x = cellText(a, idx), y = cellText(b, idx);
      if (numeric) {
        var nx = parseFloat(x.replace(/[^0-9.\-]/g, ""));
        var ny = parseFloat(y.replace(/[^0-9.\-]/g, ""));
        if (isNaN(nx)) nx = -Infinity;
        if (isNaN(ny)) ny = -Infinity;
        return asc ? nx - ny : ny - nx;
      }
      return asc
        ? x.localeCompare(y, undefined, { numeric: true, sensitivity: "base" })
        : y.localeCompare(x, undefined, { numeric: true, sensitivity: "base" });
    };
  }

  function makeSortable(table) {
    if (table.hasAttribute("data-sortable-init")) return;
    var head = table.tHead;
    var body = table.tBodies[0];
    if (!head || !head.rows.length || !body) return;
    table.setAttribute("data-sortable-init", "1");

    var headers = head.rows[head.rows.length - 1].cells;
    Array.prototype.forEach.call(headers, function (th, idx) {
      if (th.hasAttribute("data-nosort")) return;
      th.classList.add("c-sortable");
      th.setAttribute("role", "button");
      th.setAttribute("tabindex", "0");
      th.setAttribute("aria-sort", "none");
      var numeric = th.classList.contains("num");

      function sort() {
        var ascending = th.getAttribute("aria-sort") !== "ascending";
        Array.prototype.forEach.call(headers, function (other) {
          if (other !== th) other.setAttribute("aria-sort", "none");
        });
        th.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        var rows = Array.prototype.slice.call(body.rows).filter(function (r) {
          return r.children.length === headers.length; // skip full-width "empty" rows
        });
        rows.sort(comparer(idx, ascending, numeric));
        rows.forEach(function (r) { body.appendChild(r); });
      }

      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
      });
    });
  }

  /* ---- Mobile nav toggle: reflect state for assistive tech ---- */
  function wireNavToggle() {
    var box = document.getElementById("c360-nav");
    var btn = document.querySelector(".nav-toggle-btn");
    if (!box || !btn) return;
    var sync = function () { btn.setAttribute("aria-expanded", box.checked ? "true" : "false"); };
    sync();
    box.addEventListener("change", sync);
    btn.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); box.checked = !box.checked; sync(); }
    });
  }

  function init() {
    var seen = [];
    document.querySelectorAll("table.data, .table-wrap table").forEach(function (t) {
      if (seen.indexOf(t) === -1) { seen.push(t); makeSortable(t); }
    });
    wireNavToggle();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
