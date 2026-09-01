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

/* Client360 Vault external handlers */
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("vault-upload-toggle");
  const form = document.getElementById("vault-upload");

  if (toggle && form) {
    toggle.addEventListener("click", () => {
      form.hidden = !form.hidden;

      if (!form.hidden) {
        const fileInput = form.querySelector('input[type="file"]');
        if (fileInput) {
          fileInput.focus();
        }
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();

      const submitButton = form.querySelector('button[type="submit"]');
      const originalText = submitButton ? submitButton.textContent : "";

      try {
        if (submitButton) {
          submitButton.disabled = true;
          submitButton.textContent = "Uploading...";
        }

        const response = await fetch("/api/vault/documents", {
          method: "POST",
          body: new FormData(form),
        });

        if (!response.ok) {
          const message = await response.text();
          throw new Error(`${response.status} ${message}`);
        }

        const url = new URL(window.location.href);
        url.searchParams.set("tab", "vault");
        url.searchParams.delete("doc");
        window.location.href = url.toString();
      } catch (error) {
        window.alert(`Upload failed: ${error.message}`);
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
          submitButton.textContent = originalText;
        }
      }
    });
  }
});

/* ---- Header quick-access menus (Phase 4) -------------------------------
   Progressive enhancement only. The <details> disclosures in the global header
   already open and close on their own without JavaScript; this adds the three
   dismissals a native <details> does not give you — click-away, Escape, and
   closing a sibling when another opens — so the menus behave like menus.
   documents.js does the same for `.rowmenu`, but it loads on one page only and
   these controls are in the shell on every page. */
(function () {
  "use strict";

  function closeAll(except) {
    document.querySelectorAll("details.qa-menu[open]").forEach(function (d) {
      if (d !== except) { d.removeAttribute("open"); }
    });
  }

  document.addEventListener("toggle", function (ev) {
    var d = ev.target;
    if (d && d.classList && d.classList.contains("qa-menu") && d.open) { closeAll(d); }
  }, true);

  document.addEventListener("click", function (ev) {
    document.querySelectorAll("details.qa-menu[open]").forEach(function (d) {
      if (!d.contains(ev.target)) { d.removeAttribute("open"); }
    });
  });

  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") { return; }
    /* Return focus to the trigger, so Escape does not strand a keyboard user. */
    var open = document.querySelector("details.qa-menu[open]");
    closeAll(null);
    if (open) { var s = open.querySelector("summary"); if (s) { s.focus(); } }
  });
})();
