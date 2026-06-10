/* global AtelierUi, AtelierFolderTabs, applyThemePreference */

(function () {
  'use strict';

  /* ── Constants ── */
  const API_SEARCH = '/api/civitai-search';
  const API_AUTH_STATUS = '/api/civitai-search/auth-status';
  const API_LIBRARY_STATUS = '/api/civitai-search/library-status';
  const API_BATCH_IMPORT = '/api/import_civitai/batch';
  const API_COLLECTIONS = '/api/collections/';

  // Fallback; overwritten once /api/config resolves.
  let CIVITAI_IMAGE_URL = 'https://civitai.red/images/';

  fetch('/api/config')
    .then(r => r.json())
    .then(cfg => { if (cfg.civitai_web_base_url) CIVITAI_IMAGE_URL = cfg.civitai_web_base_url + '/images/'; })
    .catch(() => {});

  const THUMB_SIZE = 180;
  const PAGE_SIZE = 51;
  const MAX_RESULTS = 100_000; // Display cap — matches CivitAI's max reported result count
  const STORAGE_KEYS = {
    theme: 'atelier.searchLab.theme',
  };

  /* ── BlurHash decoder (minimal, pure JS) ── */
  // Based on the reference implementation by Wolt.
  const _BH_CHARS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~';
  const _BH_BASE = _BH_CHARS.length;

  function _bhDecode83(str) {
    let val = 0;
    for (const ch of str) {
      const i = _BH_CHARS.indexOf(ch);
      if (i < 0) return 0;
      val = val * _BH_BASE + i;
    }
    return val;
  }

  function _bhPow(base, exp) {
    let val = 1;
    for (let i = 0; i < exp; i++) val *= base;
    return val;
  }

  function _bhSRGBToLinear(value) {
    const v = value / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  }

  function _bhLinearToSRGB(value) {
    const v = Math.max(0, Math.min(1, value));
    return v <= 0.0031308 ? Math.round(v * 12.92 * 255 + 0.5) : Math.round((1.055 * Math.pow(v, 1 / 2.4) - 0.055) * 255 + 0.5);
  }

  function _bhSign(val) { return val < 0 ? -1 : 1; }

  function _bhDecodeDC(val) {
    return [_bhSRGBToLinear((val >> 16) & 0xff), _bhSRGBToLinear((val >> 8) & 0xff), _bhSRGBToLinear(val & 0xff)];
  }

  function _bhDecodeAC(val, maxAC) {
    const qR = Math.floor(val / (19 * 19));
    const qG = Math.floor(val / 19) % 19;
    const qB = val % 19;
    return [
      _bhSign(qR - 9) * _bhPow((qR - 9) / 9, 2) * maxAC,
      _bhSign(qG - 9) * _bhPow((qG - 9) / 9, 2) * maxAC,
      _bhSign(qB - 9) * _bhPow((qB - 9) / 9, 2) * maxAC,
    ];
  }

  /**
   * Decode a BlurHash string to pixel data.
   * @param {string} hash  The BlurHash string.
   * @param {number} width Output width (small, e.g. 32).
   * @param {number} height Output height (small, e.g. 32).
   * @returns {Uint8ClampedArray|null} RGBA pixel data or null if invalid.
   */
  function decodeBlurHash(hash, width, height) {
    if (!hash || hash.length < 6) return null;
    try {
      const sizeFlag = _bhDecode83(hash[0]);
      const ny = Math.floor(sizeFlag / 9) + 1;
      const nx = (sizeFlag % 9) + 1;
      if (hash.length !== 4 + 2 * nx * ny) return null;

      const quantMaxVal = _bhDecode83(hash[1]);
      const maxAC = (quantMaxVal + 1) / 166;

      const colors = [];
      for (let i = 0; i < nx * ny; i++) {
        if (i === 0) {
          const val = _bhDecode83(hash.substring(2, 6));
          colors.push(_bhDecodeDC(val));
        } else {
          const val = _bhDecode83(hash.substring(4 + i * 2, 6 + i * 2));
          colors.push(_bhDecodeAC(val, maxAC));
        }
      }

      const pixels = new Uint8ClampedArray(width * height * 4);
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          let r = 0, g = 0, b = 0;
          for (let j = 0; j < ny; j++) {
            for (let i = 0; i < nx; i++) {
              const basis = Math.cos((Math.PI * x * i) / width) * Math.cos((Math.PI * y * j) / height);
              const color = colors[j * nx + i];
              r += color[0] * basis;
              g += color[1] * basis;
              b += color[2] * basis;
            }
          }
          const idx = (y * width + x) * 4;
          pixels[idx] = _bhLinearToSRGB(r);
          pixels[idx + 1] = _bhLinearToSRGB(g);
          pixels[idx + 2] = _bhLinearToSRGB(b);
          pixels[idx + 3] = 255;
        }
      }
      return pixels;
    } catch {
      return null;
    }
  }

  /**
   * Render a BlurHash to a small canvas and return a data-URL.
   * Returns empty string if decoding fails.
   */
  function blurHashToDataURL(hash, w = 32, h = 32) {
    const pixels = decodeBlurHash(hash, w, h);
    if (!pixels) return '';
    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(w, h);
    imgData.data.set(pixels);
    ctx.putImageData(imgData, 0, 0);
    return canvas.toDataURL('image/png');
  }

  /* ── DOM refs ── */
  const els = {};
  const requiredIds = [
    'search-form', 'search-query', 'search-advanced',
    'filter-tags', 'filter-sort', 'filter-base-model', 'filter-username', 'filter-nsfw', 'filter-match',
    'facet-bar', 'search-status', 'search-status-text',
    'gallery-grid', 'gallery-footer', 'load-more-btn', 'results-count',
    'selection-count',
    'details-pane', 'details-empty', 'details-content',
    'detail-media-frame', 'detail-image',
    'detail-title', 'detail-subtitle',
    'fullscreen-btn', 'open-civitai-link', 'send-to-gen-lab-link',
    'detail-folder-mount', 'detail-panel-stash',
    'detail-panel-info', 'detail-panel-tags', 'detail-panel-stats',
    'detail-meta', 'detail-tags-list', 'detail-stats',
    'fullscreen-preview', 'fullscreen-image', 'fullscreen-counter',
    'fullscreen-close-btn', 'fullscreen-prev', 'fullscreen-next',
    'fullscreen-tags-panel', 'fullscreen-tags-cloud',
    'theme-toggle',
  ];

  /* ── State ── */
  const state = {
    hits: [],
    total: 0,
    offset: 0,
    limit: PAGE_SIZE,
    facets: null,
    selectedHitIndex: -1,
    selectedIndices: new Set(),   // multi-select set of hit indices
    lastSelectionAnchor: -1,     // anchor index for shift-click range
    loading: false,
    currentQuery: null,
    detailFolderWorkspace: null,
    importedIds: new Set(),     // civitai_image_ids already in library
    importTasks: new Map(),    // civitaiId → task_id (pending imports)
  };

  /* ── Initialise ── */
  function init() {
    // Collect DOM references
    for (const id of requiredIds) {
      els[id.replace(/-/g, '_')] = document.getElementById(id);
    }

    if (typeof applyThemePreference === 'function') {
      applyThemePreference(els.theme_toggle);
    }

    bindEvents();
    initFolderTabs();
    checkAuthStatus();
    createImportUI();

    // Restore state from URL if present — just repopulate form and search
    const saved = loadStateFromUrl();
    if (saved) {
      els.search_query.value = saved.query;
      els.filter_tags.value = saved.tags;
      els.filter_sort.value = saved.sortBy;
      els.filter_base_model.value = saved.baseModel;
      els.filter_username.value = saved.username;
      els.filter_nsfw.value = saved.nsfw;
      els.filter_match.value = saved.match || 'last';
      executeSearch();
    }

    // Save scroll position before unload for restore after reload
    window.addEventListener('beforeunload', saveScrollPosition);

    // Set up shared infinite scroll on the gallery-grid container
    _infiniteScroll = InfiniteScroll.create({
      scrollContainer: els.gallery_grid,
      hasMore: () => state.total <= 0 || state.hits.length < state.total,
      isLoading: () => state.loading,
      onLoadMore: () => {
        state.offset += state.limit;
        executeSearch(true);
      },
    });

    // Set up shared gallery toolbar (thumb size, infinite scroll toggle, sort)
    const toolbarMount = document.getElementById('gallery-toolbar-mount');
    if (toolbarMount && typeof GalleryToolbar !== 'undefined') {
      _galleryToolbar = GalleryToolbar.create({
        container: toolbarMount,
        position: 'beforeend',
        cssVariableHost: els.gallery_grid,
        idPrefix: 'lab',
        storagePrefix: 'atelier.searchLab.',
        sortOptions: [
          { value: 'stats.reactionCountAllTime:desc', label: 'Most Reactions' },
          { value: 'stats.commentCountAllTime:desc', label: 'Most Comments' },
          { value: 'stats.collectedCountAllTime:desc', label: 'Most Collected' },
          { value: 'createdAt:desc', label: 'Newest' },
          { value: 'createdAtUnix:asc', label: 'Oldest' },
        ],
        onThumbSizeChange(/* size */) {
          // CSS variable is updated by the module; nothing else needed
        },
        onInfiniteScrollToggle(enabled) {
          _infiniteScroll.setEnabled(enabled);
          // Show/hide manual load-more button based on infinite scroll state
          if (els.load_more_btn) {
            if (enabled && state.hits.length > 0) {
              els.load_more_btn.style.display = 'none';
            } else {
              els.load_more_btn.style.display = '';
            }
          }
        },
        onSortChange(value) {
          // Sync the advanced-filters sort dropdown and re-search
          els.filter_sort.value = value;
          state.offset = 0;
          state.hits = [];
          executeSearch();
        },
      });

      // Apply stored infinite scroll state to the controller
      _infiniteScroll.setEnabled(_galleryToolbar.getInfiniteScroll());

      // Sync toolbar sort with advanced-filters sort (e.g. from URL state restore)
      const filterSortValue = els.filter_sort.value;
      if (filterSortValue) {
        _galleryToolbar.setSortValue(filterSortValue);
      }
    }
  }

  /* ── Import feature: fetch library status ── */
  async function fetchLibraryStatus() {
    const ids = state.hits.map(h => h.id).filter(Boolean);
    if (!ids.length) return;

    const BATCH_SIZE = 200;
    let allImported = {};

    try {
      for (let i = 0; i < ids.length; i += BATCH_SIZE) {
        const batch = ids.slice(i, i + BATCH_SIZE);
        const res = await fetch(`${API_LIBRARY_STATUS}?civitai_image_ids=${batch.join(',')}`);
        if (!res.ok) continue;
        const data = await res.json();
        Object.assign(allImported, data.imported || {});
      }
      state.importedIds = new Set(Object.keys(allImported).map(Number));
      // Re-render badges on existing tiles
      refreshLibraryBadges();
      // Update import button if details are showing
      if (state.selectedHitIndex >= 0 && state.hits[state.selectedHitIndex]) {
        updateImportButtonState(state.hits[state.selectedHitIndex]);
      }
    } catch {
      // Silently fail — badges are non-essential
    }
  }

  /* ── Import feature: refresh badge overlays on tiles ── */
  function refreshLibraryBadges() {
    const tiles = els.gallery_grid.querySelectorAll('.tile');
    tiles.forEach((tile, idx) => {
      if (idx >= state.hits.length) return;
      const hit = state.hits[idx];
      const existing = tile.querySelector('.tile-in-library-badge');
      if (state.importedIds.has(hit.id)) {
        if (!existing) {
          const badge = document.createElement('span');
          badge.className = 'tile-in-library-badge';
          badge.textContent = '✓ In Library';
          badge.title = 'This image is already in your library';
          tile.appendChild(badge);
        }
      } else if (existing) {
        existing.remove();
      }
    });
  }

  /* ── Import feature: get importable IDs from selected indices ── */
  function getImportableSelectedIds() {
    const ids = [];
    for (const idx of state.selectedIndices) {
      const hit = state.hits[idx];
      if (hit && hit.id && !state.importedIds.has(hit.id)) {
        ids.push(hit.id);
      }
    }
    return ids;
  }

  /* ── Import feature: create import UI elements ── */
  function createImportUI() {
    // Import button in detail header actions
    const actionsDiv = document.querySelector('.detail-header-actions');
    if (actionsDiv) {
      const importBtn = document.createElement('button');
      importBtn.id = 'import-single-btn';
      importBtn.className = 'btn ghost btn-sm import-btn';
      importBtn.type = 'button';
      importBtn.textContent = '⬇ Import';
      importBtn.title = 'Import this image to your library';
      importBtn.style.display = 'none';
      importBtn.addEventListener('click', () => {
        const hit = state.hits[state.selectedHitIndex];
        if (hit) showImportDialog([hit.id]);
      });
      // Insert before the fullscreen button
      actionsDiv.insertBefore(importBtn, actionsDiv.firstChild);
    }

    // Bulk import button in gallery footer
    const footer = els.gallery_footer;
    if (footer) {
      const bulkBtn = document.createElement('button');
      bulkBtn.id = 'bulk-import-btn';
      bulkBtn.type = 'button';
      bulkBtn.textContent = 'Import Selected';
      bulkBtn.style.display = 'none';
      bulkBtn.addEventListener('click', () => {
        const ids = getImportableSelectedIds();
        if (ids.length) showImportDialog(ids);
      });
      // Insert before results-count
      footer.insertBefore(bulkBtn, els.results_count);
    }
  }

  /* ── Import feature: update single-import button visibility ── */
  function updateImportButtonState(hit) {
    const btn = document.getElementById('import-single-btn');
    if (!btn) return;
    if (!hit || !hit.id) {
      btn.style.display = 'none';
      return;
    }
    const isImported = state.importedIds.has(hit.id);
    const isPending = state.importTasks.has(hit.id);
    btn.style.display = '';
    if (isPending) {
      btn.textContent = '⏳ Importing…';
      btn.disabled = true;
    } else if (isImported) {
      btn.textContent = '✓ In Library';
      btn.disabled = true;
    } else {
      btn.textContent = '⬇ Import';
      btn.disabled = false;
    }
  }

  /* ── Import feature: show collection picker dialog ── */
  function showImportDialog(civitaiIds) {
    // Remove any existing dialog
    const existing = document.getElementById('import-dialog-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'import-dialog-overlay';
    overlay.className = 'import-dialog-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'import-dialog';

    const plural = civitaiIds.length !== 1 ? 's' : '';
    dialog.innerHTML = `
      <div class="import-dialog-header">
        <h3>Import ${civitaiIds.length} Image${plural}</h3>
        <button type="button" class="import-dialog-close" title="Close">✕</button>
      </div>
      <div class="import-dialog-body">
        <label class="import-dialog-label">Add to collection (optional)</label>
        <select id="import-collection-select" class="import-dialog-select">
          <option value="">Don't add to a collection</option>
          <option value="__new__">➕ Create new collection…</option>
        </select>
        <input id="import-new-collection-name" type="text" class="import-dialog-input hidden"
               placeholder="New collection name">
        <p class="import-dialog-hint">
          Images will be downloaded and added to your library.
        </p>
      </div>
      <div class="import-dialog-footer">
        <button type="button" id="import-dialog-cancel" class="btn ghost">Cancel</button>
        <button type="button" id="import-dialog-confirm" class="btn solid import-dialog-confirm-btn">Import</button>
      </div>
    `;

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    // Populate collections dropdown
    fetchCollections(dialog.querySelector('#import-collection-select'));

    // Show/hide new collection name input
    const select = dialog.querySelector('#import-collection-select');
    const nameInput = dialog.querySelector('#import-new-collection-name');
    select.addEventListener('change', () => {
      nameInput.classList.toggle('hidden', select.value !== '__new__');
    });

    // Close handlers
    dialog.querySelector('.import-dialog-close').addEventListener('click', () => overlay.remove());
    dialog.querySelector('#import-dialog-cancel').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    // Confirm handler
    dialog.querySelector('#import-dialog-confirm').addEventListener('click', async () => {
      const confirmBtn = dialog.querySelector('#import-dialog-confirm');
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'Starting import…';

      const body = { civitai_image_ids: civitaiIds };
      const colVal = select.value;
      if (colVal === '__new__') {
        const name = nameInput.value.trim();
        if (!name) {
          nameInput.style.borderColor = '#c0392b';
          confirmBtn.disabled = false;
          confirmBtn.textContent = 'Import';
          return;
        }
        body.create_collection_name = name;
      } else if (colVal) {
        body.collection_id = parseInt(colVal, 10);
      }

      try {
        const res = await fetch(API_BATCH_IMPORT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => null);
          throw new Error(err?.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        // Mark as pending
        for (const id of civitaiIds) {
          state.importTasks.set(id, data.task?.id);
        }
        overlay.remove();
        // Poll for completion
        pollImportTasks(civitaiIds, data.task?.id);
      } catch (err) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = 'Import';
        alert(`Import failed: ${err.message}`);
      }
    });
  }

  /* ── Import feature: fetch user collections for the picker ── */
  async function fetchCollections(selectEl) {
    try {
      const res = await fetch(API_COLLECTIONS);
      if (!res.ok) return;
      const collections = await res.json();
      if (!Array.isArray(collections)) return;
      for (const col of collections) {
        const opt = document.createElement('option');
        opt.value = String(col.id);
        opt.textContent = col.name;
        selectEl.appendChild(opt);
      }
    } catch {
      // Non-essential — user can still import without collection
    }
  }

  /* ── Import feature: poll task for completion ── */
  async function pollImportTasks(civitaiIds, taskId) {
    if (!taskId) return;
    const maxPolls = 120; // ~2 min at 1s intervals
    let polls = 0;

    updateImportButtonState(state.hits[state.selectedHitIndex]);
    updateSelectionCounter();

    const interval = setInterval(async () => {
      polls++;
      if (polls >= maxPolls) {
        clearInterval(interval);
        return;
      }
      try {
        const res = await fetch(`/api/tasks/${taskId}`);
        if (!res.ok) { clearInterval(interval); return; }
        const task = await res.json();
        if (task.status === 'completed' || task.status === 'failed' || task.status === 'cancelled') {
          clearInterval(interval);
          if (task.status === 'completed') {
            for (const id of civitaiIds) {
              state.importedIds.add(id);
              state.importTasks.delete(id);
            }
            refreshLibraryBadges();
            updateSelectionCounter();
            if (state.selectedHitIndex >= 0 && state.hits[state.selectedHitIndex]) {
              updateImportButtonState(state.hits[state.selectedHitIndex]);
            }
          } else {
            for (const id of civitaiIds) {
              state.importTasks.delete(id);
            }
            updateImportButtonState(state.hits[state.selectedHitIndex]);
            updateSelectionCounter();
            // Show error to the user
            const msg = task.error || task.message || `Import ${task.status}`;
            setStatus(`Import failed: ${msg}`, 'is-error');
          }
        }
      } catch {
        // Keep polling
      }
    }, 1000);
  }

  /* ── Auth check ── */
  async function checkAuthStatus() {
    try {
      const res = await fetch(API_AUTH_STATUS);
      if (!res.ok) {
        setStatus('⚠️ Cannot reach auth-status endpoint — search may be limited.', 'is-error');
        return;
      }
      const data = await res.json();
      if (!data.authenticated) {
        setStatus('ℹ️ ' + (data.message || 'Using REST API fallback.'), '');
      }
    } catch (_e) {
      setStatus('⚠️ Cannot reach auth-status endpoint.', 'is-error');
    }
  }

  /* ── URL State Persistence ── */
  const SCROLL_KEY = 'searchLab_scrollY';

  function saveStateToUrl() {
    const p = new URLSearchParams();
    const q = els.search_query.value.trim();
    const tags = els.filter_tags.value.trim();
    const sort = els.filter_sort.value;
    const baseModel = els.filter_base_model.value;
    const username = els.filter_username.value.trim();
    const nsfw = els.filter_nsfw.value;
    const match = els.filter_match.value;

    if (q) p.set('q', q);
    if (tags) p.set('tags', tags);
    if (sort) p.set('sort', sort);
    if (baseModel) p.set('baseModel', baseModel);
    if (username) p.set('username', username);
    if (nsfw) p.set('nsfw', nsfw);
    if (match && match !== 'last') p.set('match', match);
    if (state.hits.length > 0) p.set('offset', String(state.hits.length));

    const search = p.toString();
    const url = window.location.pathname + (search ? '?' + search : '');
    history.replaceState(null, '', url);
  }

  function loadStateFromUrl() {
    const p = new URLSearchParams(window.location.search);
    if (!p.has('q') && !p.has('tags') && !p.has('baseModel') && !p.has('username')) return null;

    return {
      query: p.get('q') || '',
      tags: p.get('tags') || '',
      sortBy: p.get('sort') || '',
      baseModel: p.get('baseModel') || '',
      username: p.get('username') || '',
      nsfw: p.get('nsfw') || '',
      match: p.get('match') || 'last',
      offset: parseInt(p.get('offset') || '0', 10),
    };
  }

  function saveScrollPosition() {
    try { sessionStorage.setItem(SCROLL_KEY, String(window.scrollY)); } catch (_e) { /* quota */ }
  }

  function restoreScrollPosition() {
    try {
      const y = parseInt(sessionStorage.getItem(SCROLL_KEY) || '0', 10);
      sessionStorage.removeItem(SCROLL_KEY);
      if (y > 0) {
        requestAnimationFrame(() => window.scrollTo(0, y));
      }
    } catch (_e) { /* unavailable */ }
  }

  /* ── Infinite Scroll & Toolbar ── */
  // InfiniteScroll controller (set up after DOM ready)
  let _infiniteScroll = null;
  // GalleryToolbar controller (set up after DOM ready)
  let _galleryToolbar = null;

  /* ── Events ── */
  function bindEvents() {
    // Search form
    els.search_form.addEventListener('submit', (e) => {
      e.preventDefault();
      state.offset = 0;
      state.hits = [];
      executeSearch();
    });

    // Load more — manual fallback; infinite scroll is primary
    els.load_more_btn.addEventListener('click', () => {
      if (state.loading) return;
      state.offset += state.limit;
      executeSearch(true);
    });

    // Facet chip clicks (delegated)
    els.facet_bar.addEventListener('click', (e) => {
      const chip = e.target.closest('.facet-chip');
      if (!chip) return;
      const facet = chip.dataset.facet;
      const value = chip.dataset.value;
      // Apply the facet as a tag filter
      const tagsInput = els.filter_tags;
      const existing = tagsInput.value.split(',').map((s) => s.trim()).filter(Boolean);
      if (!existing.includes(value)) {
        existing.push(value);
        tagsInput.value = existing.join(', ');
      }
      // Re-search
      state.offset = 0;
      state.hits = [];
      executeSearch();
    });

    // Tile clicks (delegated) — shift-click range, ctrl/cmd-click toggle, plain click single
    els.gallery_grid.addEventListener('click', (e) => {
      const tile = e.target.closest('.tile');
      if (!tile) return;
      const idx = parseInt(tile.dataset.index, 10);
      if (isNaN(idx)) return;

      if (e.shiftKey) {
        e.preventDefault();
        selectRange(idx, { additive: e.metaKey || e.ctrlKey });
        return;
      }
      if (e.metaKey || e.ctrlKey) {
        e.preventDefault();
        toggleSelection(idx);
        return;
      }
      // Plain click → single select
      setSingleSelection(idx);
    });

    // Fullscreen
    els.fullscreen_btn.addEventListener('click', openFullscreen);
    els.detail_image.addEventListener('click', openFullscreen);
    els.fullscreen_close_btn.addEventListener('click', closeFullscreen);
    els.fullscreen_prev.addEventListener('click', () => navigateFullscreen(-1));
    els.fullscreen_next.addEventListener('click', () => navigateFullscreen(1));
    els.fullscreen_preview.querySelector('.fullscreen-backdrop').addEventListener('click', closeFullscreen);

    // Keyboard
    document.addEventListener('keydown', handleKeyboard);
  }

  /* ── Search ── */
  async function executeSearch(append = false) {
    if (state.loading) return;

    if (!append) _preloadCache.clear();
    const savedOffset = state.offset;

    const query = els.search_query.value.trim();
    const tags = els.filter_tags.value.trim();
    const sortBy = els.filter_sort.value;
    const baseModel = els.filter_base_model.value;
    const username = els.filter_username.value.trim();
    const nsfwLevels = els.filter_nsfw.value;
    const matchStrategy = els.filter_match.value;

    if (!query && !tags && !baseModel && !username) {
      setStatus('Enter a search query or apply filters.', '');
      return;
    }

    state.loading = true;
    state.currentQuery = { query, tags, sortBy, baseModel, username, nsfwLevels, matchStrategy };
    setStatus('Searching…', 'is-loading');

    const body = {
      query: query || undefined,
      tags: tags ? tags.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
      sort_by: sortBy,
      limit: state.limit,
      offset: state.offset,
      nsfw_levels: nsfwLevels ? nsfwLevels.split(',').map(Number) : undefined,
      base_models: baseModel ? [baseModel] : undefined,
      username: username || undefined,
      matching_strategy: matchStrategy !== 'last' ? matchStrategy : undefined,
    };

    try {
      const res = await fetch(API_SEARCH, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();

      if (append) {
        const newHits = data.hits || [];
        // If append returned zero new hits, we've exhausted all results.
        // Set total to current count so UI knows we're done.
        if (newHits.length === 0 && state.total <= 0) {
          state.total = state.hits.length;
        }
        state.hits = state.hits.concat(newHits);
      } else {
        state.hits = data.hits || [];
        // Reset multi-select on fresh search
        state.selectedIndices.clear();
        state.lastSelectionAnchor = -1;
      }
      state.total = data.total || 0;
      state.facets = data.facets || null;

      renderResults(append);
      renderFacets();

      // Display: cap total at MAX_RESULTS — when total equals the cap, assume it's truncated
      const capped = state.total >= MAX_RESULTS;
      const displayTotal = capped ? MAX_RESULTS : state.total;
      const totalLabel = displayTotal > 0
        ? (capped
            ? `Showing ${state.hits.length} of ${displayTotal.toLocaleString()}+ results`
            : `Showing ${state.hits.length} of ${displayTotal.toLocaleString()} results`)
        : `Showing ${state.hits.length} results`;
      setStatus(totalLabel, '');

      // Persist search state to URL
      saveStateToUrl();

      // Select first tile on fresh search
      if (!append && state.hits.length > 0) {
        setSingleSelection(0);
      }

      // Fetch library status for current hits
      fetchLibraryStatus();
    } catch (err) {
      // Revert offset on failure so "Load More" can be retried.
      if (append) state.offset = savedOffset;
      setStatus(`Search failed: ${err.message}`, 'is-error');
    } finally {
      state.loading = false;
    }
  }

  /* ── Render gallery tiles ── */
  function renderTile(hit, idx) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tile';
    btn.dataset.index = idx;

    if (idx === state.selectedHitIndex) {
      btn.classList.add('selected', 'active');
    } else if (state.selectedIndices.has(idx)) {
      btn.classList.add('selected');
    }

    // BlurHash placeholder — decode instantly so the tile has colour
    // before the real thumbnail loads over the network.
    const placeholderURL = hit.blurhash ? blurHashToDataURL(hit.blurhash, 32, 32) : '';

    const img = document.createElement('img');
    img.alt = hit.prompt ? hit.prompt.substring(0, 80) : `Image ${hit.id}`;
    img.classList.add('tile-real-img');

    // Start with blurhash placeholder, swap to real thumbnail on load.
    const thumbURL = hit.thumbnail_url || hit.url || '';
    if (placeholderURL) {
      img.src = placeholderURL;
      img.style.filter = 'blur(8px)';
      img.style.transition = 'filter 300ms ease, opacity 300ms ease';
      // Load real image in background, swap on ready.
      // We use a hidden preloader instead of lazy loading so tiles that
      // scroll into view *after* the preload finishes still swap instantly.
      const realImg = new Image();
      realImg.onload = () => {
        img.src = thumbURL;
        img.style.filter = '';
      };
      realImg.onerror = () => {
        // Keep blurhash placeholder — real image failed.
        img.style.filter = 'blur(4px)';
      };
      realImg.src = thumbURL;
    } else {
      img.loading = 'lazy';
      img.src = thumbURL;
    }

    const overlay = document.createElement('div');
    overlay.className = 'tile-overlay';
    const reactions = hit.stats?.reactionCount ?? '';
    const comments = hit.stats?.commentCount ?? '';
    overlay.innerHTML = `<span>♥ ${reactions}</span><span>💬 ${comments}</span>`;

    btn.appendChild(img);
    btn.appendChild(overlay);

    // Selection indicator badge (inserted as first child for z-index layering)
    if (state.selectedIndices.size > 0) {
      const indicator = document.createElement('span');
      indicator.className = 'tile-selection-indicator';
      indicator.setAttribute('aria-hidden', 'true');
      btn.insertBefore(indicator, btn.firstChild);
    }

    // "In Library" badge for imported images
    if (state.importedIds.has(hit.id)) {
      const badge = document.createElement('span');
      badge.className = 'tile-in-library-badge';
      badge.textContent = '✓ In Library';
      badge.title = 'This image is already in your library';
      btn.appendChild(badge);
    }

    return btn;
  }

  function renderResults(append = false) {
    const grid = els.gallery_grid;

    if (!append) grid.innerHTML = '';

    const startIdx = append ? grid.children.length : 0;
    for (let i = startIdx; i < state.hits.length; i++) {
      grid.appendChild(renderTile(state.hits[i], i));
    }

    // Footer visibility + infinite scroll
    if (state.hits.length > 0) {
      els.gallery_footer.classList.remove('hidden');
      const displayTotal = state.total > MAX_RESULTS ? MAX_RESULTS : state.total;
      els.results_count.textContent = displayTotal > 0
        ? (state.total > MAX_RESULTS
            ? `${state.hits.length} / ${displayTotal.toLocaleString()}+`
            : `${state.hits.length} / ${displayTotal.toLocaleString()}`)
        : `${state.hits.length} results`;
      const allLoaded = state.total > 0 && state.hits.length >= state.total;
      els.load_more_btn.disabled = allLoaded;
      els.load_more_btn.textContent = allLoaded ? 'All results loaded' : 'Load more';
      // Hide manual load-more when infinite scroll is active
      if (_galleryToolbar && _galleryToolbar.getInfiniteScroll()) {
        els.load_more_btn.style.display = 'none';
      } else {
        els.load_more_btn.style.display = '';
      }
    } else {
      els.gallery_footer.classList.add('hidden');
    }
  }

  /* ── Render facet chips ── */
  function renderFacets() {
    const bar = els.facet_bar;
    bar.innerHTML = '';

    if (!state.facets) return;

    const distribution = state.facets.distribution || {};
    // Show top tag facets as clickable chips
    const tagNames = distribution.tagNames;
    if (tagNames) {
      const sorted = Object.entries(tagNames)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 12);
      for (const [tag, count] of sorted) {
        const chip = document.createElement('span');
        chip.className = 'facet-chip';
        chip.dataset.facet = 'tagNames';
        chip.dataset.value = tag;
        chip.innerHTML = `<span class="facet-chip-label">${tag}</span> ${count}`;
        bar.appendChild(chip);
      }
    }
  }

  /* ── Select tile (navigation focus — manages active class only) ── */
  function selectTile(index) {
    if (index < 0 || index >= state.hits.length) return;

    // Remove active from old tile
    const oldTile = els.gallery_grid.querySelector(`.tile[data-index="${state.selectedHitIndex}"]`);
    if (oldTile) {
      oldTile.classList.remove('active');
    }

    state.selectedHitIndex = index;

    // Set active on new tile
    const newTile = els.gallery_grid.querySelector(`.tile[data-index="${index}"]`);
    if (newTile) {
      newTile.classList.add('active');
      newTile.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    showDetails(state.hits[index]);
    // Preload adjacent images' mid-res URLs for faster navigation.
    _preloadAdjacent(index);
  }

  /* ── Multi-select: toggle one index ── */
  function toggleSelection(index) {
    if (index < 0 || index >= state.hits.length) return;

    if (state.selectedIndices.has(index)) {
      state.selectedIndices.delete(index);
    } else {
      state.selectedIndices.add(index);
    }

    // If newly selected, make it the active tile too
    if (state.selectedIndices.has(index)) {
      state.selectedHitIndex = index;
      state.lastSelectionAnchor = index;
    }

    updateSelectionTiles();
    updateSelectionCounter();
    showDetails(state.hits[state.selectedHitIndex]);
  }

  /* ── Multi-select: range select (shift-click) ── */
  function selectRange(targetIndex, { additive = false } = {}) {
    if (targetIndex < 0 || targetIndex >= state.hits.length) return;

    const anchor = state.lastSelectionAnchor >= 0 ? state.lastSelectionAnchor : state.selectedHitIndex;
    const from = Math.min(anchor, targetIndex);
    const to = Math.max(anchor, targetIndex);

    const next = additive ? new Set(state.selectedIndices) : new Set();
    for (let i = from; i <= to; i++) {
      next.add(i);
    }

    state.selectedIndices = next;
    state.selectedHitIndex = targetIndex;
    state.lastSelectionAnchor = anchor >= 0 ? anchor : targetIndex;

    updateSelectionTiles();
    updateSelectionCounter();
    showDetails(state.hits[state.selectedHitIndex]);
  }

  /* ── Multi-select: set single (plain click) ── */
  function setSingleSelection(index) {
    state.selectedIndices = new Set([index]);
    state.lastSelectionAnchor = index;
    selectTile(index);
    updateSelectionTiles();
    updateSelectionCounter();
  }

  /* ── Multi-select: clear all ── */
  function clearSelection() {
    state.selectedIndices.clear();
    state.lastSelectionAnchor = -1;
    updateSelectionTiles();
    updateSelectionCounter();
  }

  /* ── Update selection indicators on tiles ── */
  function updateSelectionTiles() {
    const tiles = els.gallery_grid.querySelectorAll('.tile[data-index]');
    const hasAny = state.selectedIndices.size > 0;

    tiles.forEach((tile) => {
      const idx = parseInt(tile.dataset.index, 10);
      const isSelected = state.selectedIndices.has(idx);
      const isActive = idx === state.selectedHitIndex;

      tile.classList.toggle('selected', isSelected);
      tile.classList.toggle('active', isActive);
      tile.setAttribute('aria-pressed', isSelected ? 'true' : 'false');

      let indicator = tile.querySelector('.tile-selection-indicator');
      if (hasAny && !indicator) {
        indicator = document.createElement('span');
        indicator.className = 'tile-selection-indicator';
        indicator.setAttribute('aria-hidden', 'true');
        tile.insertBefore(indicator, tile.firstChild || null);
      } else if (!hasAny && indicator) {
        indicator.remove();
      }
    });
  }

  /* ── Update selection counter in footer ── */
  function updateSelectionCounter() {
    if (!els.selection_count) return;
    const count = state.selectedIndices.size;
    if (count > 0) {
      els.selection_count.textContent = `${count} selected`;
      els.selection_count.classList.remove('hidden');
    } else {
      els.selection_count.textContent = '';
      els.selection_count.classList.add('hidden');
    }

    // Show/hide bulk import button
    const bulkBtn = document.getElementById('bulk-import-btn');
    if (bulkBtn) {
      const importableCount = getImportableSelectedIds().length;
      bulkBtn.style.display = importableCount > 0 ? '' : 'none';
      bulkBtn.textContent = `Import ${importableCount} Image${importableCount !== 1 ? 's' : ''}`;
    }
  }

  /* ── Show details ── */
  function showDetails(hit) {
    els.details_empty.classList.add('hidden');
    els.details_content.classList.remove('hidden');

    // Image — use thumbnail (already cached from tiles) for instant display.
    // Full-res loading only happens in fullscreen mode.
    const imageUrl = hit.thumbnail_url || hit.mid_res_url || hit.url || '';
    els.detail_image.src = imageUrl;
    els.detail_image.alt = hit.prompt ? hit.prompt.substring(0, 120) : `CivitAI image ${hit.id}`;

    // Title & subtitle
    els.detail_title.textContent = hit.prompt
      ? (hit.prompt.length > 100 ? hit.prompt.substring(0, 100) + '…' : hit.prompt)
      : `Image #${hit.id}`;
    const userName = hit.user?.username || '';
    const userDeleted = !!(hit.user?.deletedAt);
    els.detail_subtitle.innerHTML = [
      userName ? (userDeleted ? `by <span class="deleted-user">${userName.replace(/</g,'&lt;')}</span>` : `by ${userName.replace(/</g,'&lt;')}`) : '',
      (hit.baseModel || '').replace(/</g,'&lt;'),
      hit.createdAt ? new Date(hit.createdAt).toLocaleDateString() : '',
    ].filter(Boolean).join(' · ');

    // Action links
    els.open_civitai_link.href = `${CIVITAI_IMAGE_URL}${hit.id}`;
    els.send_to_gen_lab_link.href = `/frontend/generation-lab.html?civitaiId=${hit.id}`;

    // Update import button state
    updateImportButtonState(hit);

    // Info panel (meta grid)
    renderMetaGrid(hit);

    // Tags panel
    renderTagsPanel(hit);

    // Stats panel
    renderStatsPanel(hit);
  }

  /* ── Render meta grid (Info tab) ── */
  function renderMetaGrid(hit) {
    const grid = els.detail_meta;
    grid.innerHTML = '';

    const fields = [
      ['ID', hit.id],
      ['Width × Height', hit.width && hit.height ? `${hit.width} × ${hit.height}` : '—'],
      ['Base Model', hit.baseModel || '—'],
      ['Type', hit.type || '—'],
      ['User', (hit.user?.deletedAt ? '🗑 ' : '') + (hit.user?.username || '—')],
      ['Created', hit.createdAt ? new Date(hit.createdAt).toLocaleString() : '—'],
      ['Aspect Ratio', hit.aspectRatio || '—'],
      ['Hash', hit.hash || '—'],
      ['NSFW Level', hit.nsfwLevel ?? '—'],
      ['Technique', hit.techniqueNames?.join(', ') || '—'],
    ];

    for (const [label, value] of fields) {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = String(value);
      grid.appendChild(dt);
      grid.appendChild(dd);
    }
  }

  /* ── Render tags panel ── */
  function renderTagsPanel(hit) {
    const container = els.detail_tags_list;
    container.innerHTML = '';

    // Try tagNames first, then fallback to meta.tags or tags
    let tags = hit.tagNames || hit.tags || (hit.meta && hit.meta.tags) || [];
    // Normalise: handle objects with .name, strip whitespace, filter empties
    tags = tags
      .map(t => (typeof t === 'string' ? t.trim() : (t && t.name ? String(t.name).trim() : '')))
      .filter(Boolean);

    if (!tags.length) {
      container.innerHTML = '<p style="color:var(--ink-soft);font-size:0.82rem;">No tags available.</p>';
      return;
    }

    for (const tag of tags) {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.textContent = tag;
      chip.title = 'Click to search for this tag';
      chip.addEventListener('click', () => {
        els.search_query.value = '';
        els.filter_tags.value = tag;
        state.offset = 0;
        state.hits = [];
        executeSearch();
      });
      container.appendChild(chip);
    }
  }

  /* ── Render stats panel ── */
  function renderStatsPanel(hit) {
    const grid = els.detail_stats;
    grid.innerHTML = '';

    const stats = hit.stats || {};
    const fields = [
      ['Reactions', stats.reactionCount ?? '—'],
      ['Comments', stats.commentCount ?? '—'],
      ['Collected', stats.collectedCount ?? '—'],
      ['Downloads', stats.downloadCount ?? '—'],
    ];

    for (const [label, value] of fields) {
      const dt = document.createElement('dt');
      dt.textContent = label;
      const dd = document.createElement('dd');
      dd.textContent = typeof value === 'number' ? value.toLocaleString() : String(value);
      grid.appendChild(dt);
      grid.appendChild(dd);
    }
  }

  /* ── Folder tabs ── */
  function initFolderTabs() {
    const uiKit = window.AtelierUi;
    if (!uiKit || typeof uiKit.createStackedFolderWorkspace !== 'function') return;

    const panels = [
      els.detail_panel_info,
      els.detail_panel_tags,
      els.detail_panel_stats,
    ];

    const labels = {
      info: 'Info',
      tags: 'Tags',
      stats: 'Stats',
    };

    const tabIds = ['info', 'tags', 'stats'];

    // Move panels to stash
    if (els.detail_panel_stash) {
      panels.forEach((panel) => {
        panel.hidden = true;
        panel.classList.remove('is-active');
        els.detail_panel_stash.append(panel);
      });
    }

    const tabs = tabIds.map((id) => ({
      id,
      label: labels[id],
      row: 1,
      render: () => {
        const panel = panels[tabIds.indexOf(id)];
        if (panel) {
          panel.hidden = false;
          panel.classList.add('is-active');
        }
        return panel;
      },
    }));

    state.detailFolderWorkspace = uiKit.createStackedFolderWorkspace({
      tabs,
      activeTabId: 'info',
      ariaLabel: 'Image detail tabs',
      wrapperClassName: 'detail-folder-workspace',
      stackClassName: 'detail-folder-stack',
      bodyClassName: 'detail-folder-body',
    });

    if (els.detail_folder_mount) {
      els.detail_folder_mount.replaceChildren(state.detailFolderWorkspace.el);
    }
  }

  /* ── Fullscreen ── */

  /* Preload cache: mid-res URLs for adjacent images so navigation is instant. */
  const _preloadCache = new Map(); // civitaiId → 'loaded' | HTMLImageElement
  const PRELOAD_RANGE = 2; // preload ±2 neighbors

  function _preloadAdjacent(index) {
    for (let d = -PRELOAD_RANGE; d <= PRELOAD_RANGE; d++) {
      const i = index + d;
      if (i < 0 || i >= state.hits.length) continue;
      const hit = state.hits[i];
      const midUrl = hit.mid_res_url || hit.url;
      if (!midUrl || _preloadCache.has(hit.id)) continue;
      const img = new Image();
      img.decoding = 'async';
      img.onload = () => _preloadCache.set(hit.id, 'loaded');
      img.onerror = () => {}; // silent — preloading is best-effort
      img.src = midUrl;
      _preloadCache.set(hit.id, img); // store the HTMLImageElement while loading
    }
  }

  function _setFullscreenImage(hit) {
    // Tiered loading strategy:
    //   1. Show thumbnail immediately (already cached from tiles)
    //   2. Upgrade to mid-res (1260px) when loaded — sweet spot for fullscreen
    //   3. Don't load original unless user explicitly requests it (future: "view original" button)
    const thumbUrl = hit.thumbnail_url || '';
    const midUrl = hit.mid_res_url || hit.url || '';

    // If the mid-res is already preloaded, use it directly
    if (_preloadCache.get(hit.id) === 'loaded' && midUrl) {
      els.fullscreen_image.src = midUrl;
      return;
    }

    // Show thumbnail first (instant from browser cache)
    if (thumbUrl) {
      els.fullscreen_image.src = thumbUrl;
    }

    // Upgrade to mid-res in background
    if (midUrl && midUrl !== thumbUrl) {
      const upgrade = new Image();
      upgrade.onload = () => {
        // Only apply if user hasn't navigated away
        if (state.hits[state.selectedHitIndex]?.id === hit.id) {
          els.fullscreen_image.src = midUrl;
        }
      };
      upgrade.src = midUrl;
    }
  }

  function openFullscreen() {
    if (state.selectedHitIndex < 0 || !state.hits[state.selectedHitIndex]) return;

    const hit = state.hits[state.selectedHitIndex];
    _setFullscreenImage(hit);
    els.fullscreen_image.alt = hit.prompt ? hit.prompt.substring(0, 120) : '';
    updateFullscreenCounter();
    renderFullscreenTags(hit);

    els.fullscreen_preview.classList.remove('hidden');
    els.fullscreen_preview.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  }

  function closeFullscreen() {
    els.fullscreen_preview.classList.add('hidden');
    els.fullscreen_preview.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
  }

  function navigateFullscreen(delta) {
    const next = state.selectedHitIndex + delta;
    if (next < 0 || next >= state.hits.length) return;
    selectTile(next);
    // Tiered loading: show thumbnail instantly, upgrade to mid-res.
    const hit = state.hits[next];
    _setFullscreenImage(hit);
    updateFullscreenCounter();
    renderFullscreenTags(hit);
  }

  function updateFullscreenCounter() {
    const idx = state.selectedHitIndex;
    const selected = state.selectedIndices.has(idx) ? ' ✓' : '';
    els.fullscreen_counter.textContent = `${idx + 1} / ${state.hits.length}${selected}`;
  }

  /* ── Fullscreen: toggle selection of current image via Space ── */
  function toggleFullscreenSelection() {
    const idx = state.selectedHitIndex;
    if (idx < 0 || idx >= state.hits.length) return;

    if (state.selectedIndices.has(idx)) {
      state.selectedIndices.delete(idx);
    } else {
      state.selectedIndices.add(idx);
    }

    updateSelectionTiles();
    updateFullscreenCounter();
    updateSelectionCounter();

    // Brief visual flash on the fullscreen image
    els.fullscreen_image.classList.add('fullscreen-select-flash');
    setTimeout(() => els.fullscreen_image.classList.remove('fullscreen-select-flash'), 300);
  }

  function renderFullscreenTags(hit) {
    const cloud = els.fullscreen_tags_cloud;
    const panel = els.fullscreen_tags_panel;
    cloud.innerHTML = '';

    // Try tagNames first, then fallback to meta.tags or tags
    let tags = hit.tagNames || hit.tags || (hit.meta && hit.meta.tags) || [];
    // Normalise: handle objects with .name, strip whitespace, filter empties
    tags = tags
      .map(t => (typeof t === 'string' ? t.trim() : (t && t.name ? String(t.name).trim() : '')))
      .filter(Boolean);

    if (!tags.length) {
      panel.classList.remove('hidden');
      cloud.innerHTML = '<p class="fullscreen-tags-empty">No tags available.</p>';
      return;
    }

    panel.classList.remove('hidden');

    for (const tag of tags) {
      const chip = document.createElement('span');
      chip.className = 'tag-chip';
      chip.textContent = tag;
      chip.title = 'Click to search for this tag';
      chip.addEventListener('click', () => {
        closeFullscreen();
        els.search_query.value = '';
        els.filter_tags.value = tag;
        state.offset = 0;
        state.hits = [];
        executeSearch();
      });
      cloud.appendChild(chip);
    }
  }

  /* ── Keyboard ── */
  function handleKeyboard(e) {
    // Fullscreen nav
    const fullscreenOpen = !els.fullscreen_preview.classList.contains('hidden');

    if (fullscreenOpen) {
      if (e.key === 'Escape') { closeFullscreen(); return; }
      if (e.key === 'ArrowLeft') { navigateFullscreen(-1); return; }
      if (e.key === 'ArrowRight') { navigateFullscreen(1); return; }
      if (e.key === 'Home') { selectTile(0); navigateFullscreenTo(0); return; }
      if (e.key === 'End') { selectTile(state.hits.length - 1); navigateFullscreenTo(state.hits.length - 1); return; }
      if (e.key === ' ') { e.preventDefault(); toggleFullscreenSelection(); return; }
      return;
    }

    // Gallery nav (when not in input/select)
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

    if (e.key === 'ArrowLeft' && state.selectedHitIndex > 0) {
      e.preventDefault();
      selectTile(state.selectedHitIndex - 1);
    }
    if (e.key === 'ArrowRight' && state.selectedHitIndex < state.hits.length - 1) {
      e.preventDefault();
      selectTile(state.selectedHitIndex + 1);
    }
    if (e.key === 'Home' && state.hits.length > 0) {
      e.preventDefault();
      selectTile(0);
    }
    if (e.key === 'End' && state.hits.length > 0) {
      e.preventDefault();
      selectTile(state.hits.length - 1);
    }
    if (e.key === 'f' || e.key === 'F') {
      openFullscreen();
    }
  }

  function navigateFullscreenTo(index) {
    if (index < 0 || index >= state.hits.length) return;
    const hit = state.hits[index];
    _setFullscreenImage(hit);
    updateFullscreenCounter();
    renderFullscreenTags(hit);
  }

  /* ── Status bar ── */
  function setStatus(text, cls) {
    els.search_status_text.textContent = text;
    els.search_status.className = 'search-status';
    if (cls) els.search_status.classList.add(cls);
  }

  /* ── Boot ── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
