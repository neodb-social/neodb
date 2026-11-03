// Behaviour shared by every page. common_libs.html loads it right after
// cash-dom and before any page script, so the helpers below exist by then.

// Inline handlers call these on cash-dom objects, which have neither.
if (!$.fn.is_visible) $.fn.is_visible = function () {
  return this.filter((_, elt) => (elt.offsetWidth || elt.offsetHeight || elt.getClientRects().length)).length > 0;
};
if (!$.fn.submit) $.fn.submit = function () {
  this.each(function () {
    $(this).trigger('submit');
    this.submit();
  });
  return this;
};

(function () {
  // A form marked data-submit-once creates something on each post, so a second
  // click while the first request is loading would create a second copy.
  const buttons = (form) => form.querySelectorAll('button:not([type]), [type="submit"]');
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (event.defaultPrevented || !form.matches('form[data-submit-once]')) return;
    if (form.dataset.submitting) {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = '1';
    const submitter = event.submitter;
    // after the browser has read the form, so a named submitter is still sent
    setTimeout(() => {
      buttons(form).forEach((button) => { button.disabled = true; });
      if (submitter) submitter.setAttribute('aria-busy', 'true');
    });
  });
  // the back button can restore the page with its buttons still disabled
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    document.querySelectorAll('form[data-submitting]').forEach((form) => {
      delete form.dataset.submitting;
      buttons(form).forEach((button) => {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      });
    });
  });
})();

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
