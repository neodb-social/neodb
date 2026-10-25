// Scroll arrows of a card row: each one is disabled at its end of the row,
// and both hide when the row fits. A button scrolls the first <ul> after it
// in its own section, the element its hyperscript handler scrolls; a row
// that htmx loads later replaces that <ul>, so the lookup runs again then.
(function () {
  function rowOf(button) {
    const scope = button.closest("section") || document;
    for (const ul of scope.querySelectorAll("ul")) {
      if (button.compareDocumentPosition(ul) & Node.DOCUMENT_POSITION_FOLLOWING) {
        return ul;
      }
    }
    return null;
  }

  function update(button) {
    const ul = button._dcScrollRow;
    const max = ul.scrollWidth - ul.clientWidth;
    button.hidden = max <= 1;
    button.disabled = button.querySelector(".fa-chevron-left")
      ? ul.scrollLeft <= 1
      : ul.scrollLeft >= max - 1;
  }

  function onScroll(event) {
    for (const button of event.currentTarget._dcScrollButtons) update(button);
  }

  function scan() {
    for (const button of document.querySelectorAll("button.dc-scroll")) {
      const ul = rowOf(button);
      if (!ul) continue;
      if (button._dcScrollRow !== ul) {
        button._dcScrollRow = ul;
        if (!ul._dcScrollButtons) {
          ul._dcScrollButtons = [];
          ul.addEventListener("scroll", onScroll, { passive: true });
        }
        ul._dcScrollButtons.push(button);
      }
      update(button);
    }
  }

  document.addEventListener("DOMContentLoaded", scan);
  document.addEventListener("htmx:afterSettle", scan);
  window.addEventListener("load", scan);
  window.addEventListener("resize", scan);
})();
