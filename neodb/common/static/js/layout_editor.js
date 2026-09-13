// Layout editor: a dialog listing the page sections with a switch and move
// buttons each, instead of dragging the sections around the page. Works with
// _layout_editor.html; sections are `.sortable > .entity-sort` elements whose
// `data-visible="0"` marks one the member hid (set by the page's init script).
(function () {
  const dialog = document.getElementById("layoutEditor");
  const openButton = document.getElementById("layoutEditButton");
  if (!dialog || !openButton) return;
  const list = document.getElementById("layoutEditorList");
  const rowTemplate = document.getElementById("layoutRowTemplate");
  const form = document.getElementById("layoutForm");

  function sections() {
    return Array.from(document.querySelectorAll(".sortable > .entity-sort"));
  }

  function titleOf(section) {
    if (section.dataset.title) return section.dataset.title;
    const heading = section.querySelector("h1, h2, h3");
    return heading ? heading.firstChild.textContent.trim() : section.id;
  }

  function refresh() {
    const rows = Array.from(list.children);
    rows.forEach((row, i) => {
      row.querySelector('[data-move="-1"]').disabled = i === 0;
      row.querySelector('[data-move="1"]').disabled = i === rows.length - 1;
      row.classList.toggle("off", !row.querySelector("input").checked);
    });
  }

  function build() {
    list.innerHTML = "";
    sections().forEach((section) => {
      const row = rowTemplate.content.firstElementChild.cloneNode(true);
      row.dataset.id = section.id;
      row.querySelector("input").checked = section.dataset.visible !== "0";
      row.querySelector(".dc-layout-title").textContent = titleOf(section);
      list.appendChild(row);
    });
    refresh();
  }

  list.addEventListener("click", (event) => {
    const button = event.target.closest("[data-move]");
    if (!button) return;
    const row = button.closest("li");
    if (button.dataset.move === "-1") {
      if (row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
    } else if (row.nextElementSibling) {
      list.insertBefore(row.nextElementSibling, row);
    }
    refresh();
    button.focus();
  });
  list.addEventListener("change", refresh);

  openButton.addEventListener("click", () => {
    build();
    dialog.showModal();
  });
  document.getElementById("layoutEditorCancel").addEventListener("click", () => dialog.close());
  document.getElementById("layoutEditorClose").addEventListener("click", () => dialog.close());
  // a click on the backdrop closes the dialog too
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  document.getElementById("layoutEditorSave").addEventListener("click", () => {
    const layout = Array.from(list.children).map((row) => ({
      id: row.dataset.id,
      visibility: row.querySelector("input").checked,
    }));
    form.querySelector("[name=layout]").value = JSON.stringify(layout);
    form.submit();
  });
})();
