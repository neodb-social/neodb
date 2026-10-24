// Progressive enhancement only: archives and publishing work without JS.
(() => {
  const tagPicker = document.querySelector('[data-tag-picker]');
  if (tagPicker) {
    const field = tagPicker.querySelector('input[name="tags"]');
    const entry = tagPicker.querySelector('#article-tag-entry');
    const selected = tagPicker.querySelector('[data-selected-tags]');
    const suggestions = tagPicker.querySelector('[data-tag-suggestions]');
    const vocabulary = JSON.parse(document.getElementById('article-tag-suggestions').textContent);
    let values = [...new Set(field.value.split(',').map((tag) => tag.trim()).filter(Boolean))];
    const maxLength = field.maxLength;

    function renderSuggestions() {
      suggestions.replaceChildren();
      const query = entry.value.trim().toLocaleLowerCase();
      vocabulary.filter((tag) => !values.includes(tag) && tag.toLocaleLowerCase().includes(query))
        .slice(0, 8).forEach((tag) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'outline secondary';
          button.textContent = tag;
          button.addEventListener('pointerdown', (event) => { event.preventDefault(); });
          button.addEventListener('click', () => { if (addTags(tag)) entry.focus(); });
          suggestions.append(button);
        });
    }

    function renderTags() {
      field.value = values.join(', ');
      selected.replaceChildren();
      values.forEach((tag) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `${tag} ×`;
        button.setAttribute('aria-label', `${tagPicker.dataset.removeLabel}: ${tag}`);
        button.addEventListener('click', () => {
          values = values.filter((value) => value !== tag);
          entry.setCustomValidity('');
          renderTags();
          entry.focus();
        });
        selected.append(button);
      });
      renderSuggestions();
    }

    function addTags(text) {
      const next = [...new Set([...values, ...text.split(',').map((tag) => tag.trim()).filter(Boolean)])];
      if (next.length > 30 || next.join(', ').length > maxLength) {
        entry.setCustomValidity(tagPicker.dataset.limitLabel);
        entry.reportValidity();
        return false;
      }
      values = next;
      entry.value = '';
      entry.setCustomValidity('');
      renderTags();
      return true;
    }

    field.type = 'hidden';
    tagPicker.querySelector('label').htmlFor = entry.id;
    tagPicker.querySelector('[data-tag-controls]').hidden = false;
    tagPicker.querySelector('[data-add-tag]').addEventListener('click', () => {
      if (addTags(entry.value)) entry.focus();
    });
    entry.addEventListener('input', (event) => {
      entry.setCustomValidity('');
      if (!event.isComposing && entry.value.includes(',')) addTags(entry.value);
      else renderSuggestions();
    });
    entry.addEventListener('keydown', (event) => {
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addTags(entry.value);
      }
    });
    // Keep partially typed tags when publishing or moving to another setting.
    tagPicker.addEventListener('focusout', (event) => {
      if (event.target === entry && !tagPicker.contains(event.relatedTarget) && entry.value.trim()) addTags(entry.value);
    });
    field.form.addEventListener('submit', (event) => {
      if (!addTags(entry.value)) event.preventDefault();
    });
    renderTags();
  }

  const title = document.querySelector('.article-writing-canvas textarea#id_title');
  if (title) {
    const resizeTitle = () => {
      title.style.height = 'auto';
      title.style.height = `${title.scrollHeight}px`;
    };
    title.style.resize = 'none';
    title.style.overflow = 'hidden';
    title.addEventListener('input', resizeTitle);
    let titleWidth;
    new ResizeObserver(([entry]) => {
      // Editing the body changes the form's height, not the title's wrapping.
      // Ignore that change, including the height change from resizeTitle itself.
      if (entry.contentRect.width === titleWidth) return;
      titleWidth = entry.contentRect.width;
      resizeTitle();
    }).observe(title.parentElement);
    resizeTitle();
  }

  document.querySelectorAll('.article-date-picker').forEach((picker) => {
    document.addEventListener('click', (event) => {
      if (!picker.contains(event.target)) picker.open = false;
    });
    picker.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        picker.open = false;
        picker.querySelector('summary').focus();
      }
    });
  });

  const tags = document.querySelector('.article-tag-disclosure');
  if (tags) {
    const narrow = window.matchMedia('(max-width: 768px)');
    tags.open = !narrow.matches;
    narrow.addEventListener('change', (event) => { tags.open = !event.matches; });
  }

  const publish = document.querySelector('[data-article-publish]');
  if (publish) {
    publish.addEventListener('click', (event) => {
      event.preventDefault();
      const settings = document.getElementById('article-publish-settings');
      settings.open = true;
      settings.scrollIntoView({ block: 'nearest' });
      settings.querySelector('select, input').focus({ preventScroll: true });
    });
    // Native validation cannot focus a required control in a closed details.
    document.querySelector('.article-editor-form').addEventListener('invalid', (event) => {
      for (let parent = event.target.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === 'DETAILS') parent.open = true;
      }
    }, true);
  }

  const reviewDateToggle = document.getElementById('id_mark_anotherday');
  const reviewDateField = document.querySelector('[data-review-date-field]');
  if (reviewDateToggle && reviewDateField) {
    const syncReviewDate = () => {
      reviewDateField.hidden = !reviewDateToggle.checked && !reviewDateField.querySelector('.errorlist');
      reviewDateField.querySelector('input').disabled = !reviewDateToggle.checked;
    };
    reviewDateToggle.addEventListener('change', syncReviewDate);
    syncReviewDate();
  }

  document.querySelectorAll('[data-copy-article]').forEach((button) => {
    if (!navigator.clipboard) { button.hidden = true; return; }
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyArticle);
        const status = document.querySelector('[data-copy-status]');
        if (status) status.textContent = button.dataset.copiedLabel;
        button.textContent = button.dataset.copiedLabel;
      } catch {
        // The canonical URL remains available in the browser's address bar.
        button.hidden = true;
      }
    });
  });

  function buildContents() {
    const content = document.querySelector('[data-article-content]');
    if (!content || content.querySelector('.article-contents')) return;
    const headings = [...content.querySelectorAll('h2, h3')].filter((h) => !h.closest('.spoiler'));
    if (headings.length < 3) return;
    const details = document.createElement('details');
    details.className = 'article-contents';
    const summary = document.createElement('summary');
    summary.textContent = content.dataset.contentsLabel;
    const list = document.createElement('ol');
    headings.forEach((heading, index) => {
      if (!heading.id) {
        let id = `article-section-${index + 1}`;
        while (document.getElementById(id)) id += '-section';
        heading.id = id;
      }
      const item = document.createElement('li');
      if (heading.tagName === 'H3') item.className = 'subheading';
      const link = document.createElement('a');
      link.href = `#${encodeURIComponent(heading.id)}`;
      link.textContent = heading.textContent;
      item.append(link);
      list.append(item);
    });
    details.append(summary, list);
    content.prepend(details);
  }
  buildContents();
  document.addEventListener('htmx:afterSwap', buildContents);
  document.addEventListener('click', (event) => {
    const spoiler = event.target.closest('.spoiler');
    if (spoiler) spoiler.classList.toggle('revealed');
  });
})();
