(() => {
  function normalizeKey(value) {
    return String(value || '').trim().toLowerCase();
  }

  function isLikelyImageUrl(value) {
    const text = String(value || '').trim().toLowerCase();
    return Boolean(text) && /(https?:\/\/|^\/)/.test(text);
  }

  function encodeRelativeStaticPath(pathValue) {
    return String(pathValue || '')
      .split('/')
      .filter((part) => part.length > 0)
      .map((part) => encodeURIComponent(part))
      .join('/');
  }

  function buildLibraryImageUrl(item) {
    const relativePath = String(item?.file_path || '').trim();
    if (!relativePath) {
      return '';
    }
    const encoded = encodeRelativeStaticPath(relativePath);
    return encoded ? `/image_library/${encoded}` : '';
  }

  function resolveDisplayImageUrl(item) {
    if (!item || typeof item !== 'object') {
      return '';
    }
    const candidates = [
      item.preview_image_url,
      item.display_url,
      item.poster_url,
      item.video_poster_url,
      item.video_thumbnail_url,
      item.image_url,
    ];
    const found = candidates.find((candidate) => isLikelyImageUrl(candidate));
    if (found) {
      return String(found || '').trim();
    }
    return buildLibraryImageUrl(item);
  }

  async function fetchGalleryItems(searchText, limit = 30) {
    const query = String(searchText || '').trim();
    if (!query) {
      return [];
    }
    const params = new URLSearchParams();
    params.set('group_variants', 'false');
    params.set('limit', String(Math.max(1, Math.min(100, Number(limit) || 30))));
    params.set('search', query);
    const response = await fetch(`/api/images/?${params.toString()}`);
    const payload = await response.json().catch(() => []);
    if (!response.ok) {
      throw new Error(`Gallery query failed with HTTP ${response.status}.`);
    }
    return Array.isArray(payload) ? payload : [];
  }

  function createPickerDom(titleText) {
    const overlay = document.createElement('div');
    overlay.className = 'ihp-overlay';
    overlay.hidden = true;

    const dialog = document.createElement('section');
    dialog.className = 'ihp-dialog';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');

    const header = document.createElement('header');
    header.className = 'ihp-header';
    const title = document.createElement('h3');
    title.className = 'ihp-title';
    title.textContent = titleText || 'Select Gallery Image';
    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'ihp-close';
    closeButton.textContent = 'Close';
    header.append(title, closeButton);

    const body = document.createElement('div');
    body.className = 'ihp-body';

    const selectedCol = document.createElement('section');
    selectedCol.className = 'ihp-selected';
    const selectedHeading = document.createElement('h4');
    selectedHeading.textContent = 'Active Image';
    const selectedPanel = document.createElement('div');
    selectedPanel.className = 'ihp-selected-panel';
    selectedCol.append(selectedHeading, selectedPanel);

    const searchCol = document.createElement('section');
    searchCol.className = 'ihp-search';
    const searchRow = document.createElement('div');
    searchRow.className = 'ihp-search-row';
    const searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.placeholder = 'Search by hash, filename, collection, source...';
    const searchButton = document.createElement('button');
    searchButton.type = 'button';
    searchButton.textContent = 'Search';
    searchRow.append(searchInput, searchButton);

    const status = document.createElement('p');
    status.className = 'ihp-status';
    status.textContent = 'Enter a search query to find gallery images.';

    const results = document.createElement('div');
    results.className = 'ihp-results';
    searchCol.append(searchRow, status, results);

    body.append(selectedCol, searchCol);
    dialog.append(header, body);
    overlay.append(dialog);

    return {
      overlay,
      dialog,
      closeButton,
      searchInput,
      searchButton,
      status,
      results,
      selectedPanel,
    };
  }

  function createImageHashPicker(options = {}) {
    const title = String(options.title || 'Select Gallery Image');
    const maxResults = Number(options.maxResults) || 30;
    const onSelect = typeof options.onSelect === 'function' ? options.onSelect : null;
    const mount = options.mount instanceof HTMLElement ? options.mount : document.body;

    const dom = createPickerDom(title);
    mount.append(dom.overlay);

    let active = false;
    let selectedItem = null;
    let activeResults = [];

    function setStatus(text, kind = '') {
      dom.status.className = `ihp-status${kind ? ` is-${kind}` : ''}`;
      dom.status.textContent = String(text || '');
    }

    function renderSelected(item) {
      dom.selectedPanel.innerHTML = '';
      if (!item) {
        const empty = document.createElement('p');
        empty.className = 'ihp-empty';
        empty.textContent = 'No image selected yet.';
        dom.selectedPanel.append(empty);
        return;
      }

      const imageUrl = resolveDisplayImageUrl(item);
      if (imageUrl) {
        const image = document.createElement('img');
        image.className = 'ihp-selected-image';
        image.src = imageUrl;
        image.alt = String(item.file_name || item.original_file_name || item.file_hash || 'Selected image');
        image.loading = 'lazy';
        dom.selectedPanel.append(image);
      }

      const meta = document.createElement('div');
      meta.className = 'ihp-selected-meta';
      const hash = document.createElement('code');
      hash.textContent = String(item.file_hash || 'n/a');
      const name = document.createElement('p');
      name.textContent = String(item.file_name || item.original_file_name || 'Unnamed image');
      meta.append(hash, name);
      dom.selectedPanel.append(meta);
    }

    function chooseItem(item) {
      selectedItem = item && typeof item === 'object' ? item : null;
      renderSelected(selectedItem);
      if (onSelect && selectedItem) {
        onSelect(selectedItem);
      }
      const selectedHash = normalizeKey(selectedItem?.file_hash);
      Array.from(dom.results.querySelectorAll('.ihp-result')).forEach((element) => {
        const hash = normalizeKey(element.getAttribute('data-file-hash'));
        element.classList.toggle('is-active', Boolean(selectedHash) && hash === selectedHash);
      });
    }

    function renderResults(items) {
      activeResults = Array.isArray(items) ? items : [];
      dom.results.innerHTML = '';
      if (!activeResults.length) {
        const empty = document.createElement('p');
        empty.className = 'ihp-empty';
        empty.textContent = 'No gallery results matched this query.';
        dom.results.append(empty);
        return;
      }

      activeResults.forEach((item) => {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = 'ihp-result';
        card.setAttribute('data-file-hash', String(item?.file_hash || ''));

        const imageUrl = resolveDisplayImageUrl(item);
        if (imageUrl) {
          const thumb = document.createElement('img');
          thumb.className = 'ihp-result-thumb';
          thumb.src = imageUrl;
          thumb.alt = String(item?.file_name || item?.original_file_name || item?.file_hash || 'Gallery image');
          thumb.loading = 'lazy';
          card.append(thumb);
        } else {
          const placeholder = document.createElement('div');
          placeholder.className = 'ihp-result-thumb ihp-result-thumb-placeholder';
          placeholder.setAttribute('aria-hidden', 'true');
          placeholder.textContent = 'No Preview';
          card.append(placeholder);
        }

        const meta = document.createElement('div');
        meta.className = 'ihp-result-meta';
        const name = document.createElement('strong');
        name.textContent = String(item?.file_name || item?.original_file_name || 'Unnamed image');
        const hash = document.createElement('code');
        hash.textContent = String(item?.file_hash || 'n/a');
        const source = document.createElement('span');
        source.textContent = String(item?.source_url || item?.source_site || '').trim();
        meta.append(name, hash);
        if (source.textContent) {
          meta.append(source);
        }
        card.append(meta);

        card.addEventListener('click', () => {
          chooseItem(item);
          close();
        });

        dom.results.append(card);
      });

      const selectedHash = normalizeKey(selectedItem?.file_hash);
      if (selectedHash) {
        const exact = activeResults.find((item) => normalizeKey(item?.file_hash) === selectedHash);
        if (exact) {
          chooseItem(exact);
        }
      }
    }

    async function runSearch() {
      const query = dom.searchInput.value.trim();
      if (!query) {
        setStatus('Enter a search query to find gallery images.', 'warning');
        renderResults([]);
        return;
      }
      setStatus('Searching gallery...', 'loading');
      dom.searchButton.disabled = true;
      try {
        const items = await fetchGalleryItems(query, maxResults);
        renderResults(items);
        setStatus(`Found ${items.length} result${items.length === 1 ? '' : 's'}.`, 'success');
      } catch (error) {
        renderResults([]);
        setStatus(error instanceof Error ? error.message : String(error), 'error');
      } finally {
        dom.searchButton.disabled = false;
      }
    }

    function close() {
      active = false;
      dom.overlay.hidden = true;
      document.body.classList.remove('ihp-open');
    }

    async function open(config = {}) {
      active = true;
      dom.overlay.hidden = false;
      document.body.classList.add('ihp-open');

      const query = String(config.initialQuery || selectedItem?.file_hash || '').trim();
      if (query) {
        dom.searchInput.value = query;
      }
      dom.searchInput.focus();

      const selected = config.selectedItem && typeof config.selectedItem === 'object'
        ? config.selectedItem
        : selectedItem;
      renderSelected(selected);

      if (query) {
        await runSearch();
        const preferredHash = normalizeKey(config.autoSelectHash || '');
        if (preferredHash) {
          const exact = activeResults.find((item) => normalizeKey(item?.file_hash) === preferredHash);
          if (exact) {
            chooseItem(exact);
          }
        }
      }
    }

    dom.searchButton.addEventListener('click', () => {
      runSearch();
    });

    dom.searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        runSearch();
      }
      if (event.key === 'Escape' && active) {
        close();
      }
    });

    dom.closeButton.addEventListener('click', () => {
      close();
    });

    dom.overlay.addEventListener('click', (event) => {
      if (event.target === dom.overlay) {
        close();
      }
    });

    return {
      open,
      close,
      getSelected: () => selectedItem,
      setSelected: (item) => {
        selectedItem = item && typeof item === 'object' ? item : null;
        renderSelected(selectedItem);
      },
    };
  }

  window.AtelierImageHashPicker = {
    createImageHashPicker,
  };
})();
