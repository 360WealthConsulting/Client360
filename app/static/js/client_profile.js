/* Client 360 Overview — the SSN reveal control.
 *
 * The page ships the last four digits and nothing more. This fetches the full number from
 * /client/<id>/ssn only when someone actually asks for it, so an unopened record never puts an SSN
 * in the browser, its cache, or any proxy in between. That endpoint is capability-gated on
 * tax.read, record-scoped, and writes an audit entry before it answers — which is the point of
 * fetching rather than hiding a value that was already in the HTML.
 *
 * Re-clicking restores the mask and drops the value from memory, so it does not linger in the DOM
 * once the person is done reading it.
 */
(function () {
  "use strict";

  function restore(field, button) {
    field.textContent = field.dataset.ssnMasked;
    button.textContent = "Show";
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Show full Social Security number");
  }

  async function reveal(field, button) {
    button.disabled = true;
    try {
      const response = await fetch("/client/" + encodeURIComponent(button.dataset.ssnPerson) + "/ssn", {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store"
      });
      if (!response.ok) {
        // No number, or not permitted. Say so without implying the record does not exist.
        field.textContent = field.dataset.ssnMasked;
        button.textContent = response.status === 404 ? "Unavailable" : "Failed";
        return;
      }
      const body = await response.json();
      field.textContent = body.ssn;
      button.textContent = "Hide";
      button.setAttribute("aria-pressed", "true");
      button.setAttribute("aria-label", "Hide Social Security number");
    } catch (err) {
      field.textContent = field.dataset.ssnMasked;
      button.textContent = "Failed";
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("click", function (event) {
    const button = event.target.closest(".c360-ssn-reveal");
    if (!button) return;
    const field = document.querySelector('.c360-ssn[data-ssn-person="' + button.dataset.ssnPerson + '"]');
    if (!field) return;
    event.preventDefault();
    if (button.getAttribute("aria-pressed") === "true") {
      restore(field, button);
    } else {
      reveal(field, button);
    }
  });
})();
