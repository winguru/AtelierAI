/* global AtelierUi, AtelierFolderTabs, applyThemePreference */

(function () {
  'use strict';

  /* ── Constants ── */
  const API_SEARCH = '/api/civitai-search';
  const API_AUTH_STATUS = '/api/civitai-search/auth-status';
  const API_LIBRARY_STATUS = '/api/civitai-search/library-status';
  const API_RATED = '/api/civitai-search/rated';
  const API_RATE_IMAGE = '/api/civitai-search/rate';
  const API_RATINGS = '/api/civitai-search/ratings';
  const API_SINGLE_IMAGE = (id) => `/api/civitai-search/image/${id}`;
  const API_SEARCH_RECORD = '/api/civitai-search/search-record';
  const API_BATCH_IMPORT = '/api/import_civitai/batch';

  /* ── Sort options for review mode (value format: "key:order") ── */
  const REVIEW_SORT_OPTIONS = [
    { value: 'recent:desc', label: 'Most Recently Rated' },
    { value: 'reactions:desc', label: 'Most Reactions' },
    { value: 'likes:desc', label: 'Most Collected' },
    { value: 'artist:asc', label: 'Artist Name A-Z' },
  ];
  const API_COLLECTIONS = '/api/collections/';
  const API_ARTIST_SUMMARY = '/api/civitai-search/artist-summary';
  const API_ARTIST_BLOCK = '/api/civitai-search/artist-block';
  const API_RATED_ARTISTS = '/api/civitai-search/rated/artists';

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
    'mode-bar', 'review-rating-bar',
    'search-form', 'search-query', 'search-advanced',
    'filter-tags', 'filter-sort', 'filter-base-model', 'filter-username', 'filter-nsfw', 'filter-match',
    'facet-bar', 'search-status', 'search-status-text',
    'gallery-grid', 'gallery-footer', 'load-more-btn', 'results-count',
    'selection-count',
    'hide-filter-bar', 'hide-seen', 'hide-saved', 'hide-keep', 'hide-skip', 'hide-discard',
    'details-pane', 'details-empty', 'details-content',
    'detail-media-frame', 'detail-image', 'detail-video',
    'detail-title', 'detail-subtitle',
    'fullscreen-btn', 'open-civitai-link', 'send-to-gen-lab-link',
    'detail-folder-mount', 'detail-panel-stash',
    'detail-panel-info', 'detail-panel-tags', 'detail-panel-stats',
    'detail-meta', 'detail-tags-list', 'detail-stats',
    'fullscreen-preview', 'fullscreen-image', 'fullscreen-video', 'fullscreen-counter',
    'fullscreen-image-info',
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
    imageRatings: new Map(),   // civitaiId → "keep" | "discard" | "skip"
    currentSearchId: null,     // DB id of the current search record
    imageLoadErrors: new Map(), // civitaiId → { attempts, permanent }
    hideFilters: { seen: false, saved: false, keep: false, skip: false, discard: true },
    autoLoading: false,         // guards against recursive auto-load
    fullscreenAdvanceOnLoad: false, // when true, advance fullscreen to first new visible tile after search
    fullscreenImageSource: null,   // 'thumbnail' | 'mid-res' | 'original' — currently displayed source
    fullscreenImageWidth: null,    // natural width of currently displayed image (px)
    mode: 'search',               // 'search' | 'review'
    reviewRating: 'keep',         // active rating tab in review mode
    reviewSort: 'recent',         // sort key for review mode
    reviewOrder: 'desc',          // sort direction for review mode
    reviewArtistFacets: [],       // [{artist, count}] from /rated/artists
    reviewSelectedArtists: new Set(), // selected artist names (filter)
    reviewFacetFilter: '',        // text filter within the facets panel
  };

  /* ── Image load retry utilities ── */
  const MAX_IMAGE_RETRIES = 3;
  const RETRY_BASE_DELAY = 500; // ms — doubles each attempt

  /**
   * Attempt to load an image URL with exponential-backoff retries.
   *
   * Distinguishes permanent failures (HTTP 404/403/410 — image deleted
   * or removed) from temporary ones (network errors, 5xx).  Only temporary
   * failures are retried.
   *
   * @returns {Promise<boolean>} true if image loaded, false if permanently failed
   */
  function retryImageLoad(url, civitaiId, { maxRetries = MAX_IMAGE_RETRIES } = {}) {
    return new Promise((resolve) => {
      let attempt = 0;

      function tryLoad() {
        const probe = new Image();
        probe.onload = () => {
          state.imageLoadErrors.delete(civitaiId);
          resolve(true);
        };
        probe.onerror = () => {
          attempt++;
          const errorInfo = state.imageLoadErrors.get(civitaiId) || { attempts: 0, permanent: false };
          errorInfo.attempts = attempt;
          state.imageLoadErrors.set(civitaiId, errorInfo);

          if (attempt >= maxRetries) {
            errorInfo.permanent = true;
            resolve(false);
            return;
          }
          // Exponential backoff: 500ms, 1s, 2s
          const delay = RETRY_BASE_DELAY * Math.pow(2, attempt - 1);
          setTimeout(tryLoad, delay);
        };
        probe.src = url;
      }

      tryLoad();
    });
  }

  /**
   * Reload metadata for the currently selected image from the backend.
   *
   * Fetches fresh data (tags, models, prompt, etc.) via the single-image
   * endpoint and merges it into the hit in state.hits, then re-renders.
   */
  async function reloadCurrentImage() {
    const idx = state.selectedHitIndex;
    if (idx < 0 || !state.hits[idx]) return;

    const hit = state.hits[idx];
    const civitaiId = hit.id;

    setStatus(`Reloading image #${civitaiId}…`, 'is-loading');

    try {
      const res = await fetch(API_SINGLE_IMAGE(civitaiId));

      if (res.status === 404) {
        setStatus(`Image #${civitaiId} no longer exists on CivitAI.`, 'is-error');
        return;
      }
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      const freshHit = data.hit;

      if (!freshHit) {
        setStatus(`No data returned for image #${civitaiId}.`, 'is-error');
        return;
      }

      // Merge: preserve local-only fields, update everything else from fresh data
      const merged = {
        ...hit,          // keep existing fields as fallback
        ...freshHit,     // overwrite with fresh metadata
        // Preserve local UI state that isn't in the API response
      };
      state.hits[idx] = merged;

      // Re-render everything that depends on this hit
      const fullscreenOpen = !els.fullscreen_preview.classList.contains('hidden');
      if (fullscreenOpen) {
        _setFullscreenImage(merged);
        renderFullscreenTags(merged);
      } else {
        renderResults(false);
        showDetails(merged);
      }

      setStatus(`Reloaded image #${civitaiId}.`, '');
    } catch (err) {
      setStatus(`Reload failed: ${err.message}`, 'is-error', {
        label: 'Retry',
        onClick: () => reloadCurrentImage(),
      });
    }
  }

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
    updateModeUI();
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
      syncHideFiltersFromUrl(saved);
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
          if (state.mode === 'review') {
            // Review-mode sort values are "key" or "key:order"
            const [sort, ord] = value.includes(':')
              ? value.split(':')
              : [value, state.reviewOrder];
            state.reviewSort = sort;
            state.reviewOrder = ord || 'desc';
            state.offset = 0;
            state.hits = [];
            executeSearch();
          } else {
            // Search-mode: sync the advanced-filters sort dropdown and re-search
            els.filter_sort.value = value;
            state.offset = 0;
            state.hits = [];
            executeSearch();
          }
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
      // Saved status affects hide-filter visibility
      applyHideFilters();
      ensureVisibleSelection();
      checkAutoLoadIfAllHidden();
      // Update import button if details are showing
      if (state.selectedHitIndex >= 0 && state.hits[state.selectedHitIndex]) {
        updateImportButtonState(state.hits[state.selectedHitIndex]);
      }
    } catch {
      // Silently fail — badges are non-essential
    }
  }

  /**
   * Fetch persisted keep/discard/skip ratings for the current result set.
   * Merges them into state.imageRatings so badges survive across sessions.
   */
  async function fetchImageRatings() {
    const ids = state.hits.map(h => h.id).filter(Boolean);
    if (!ids.length) return;

    const BATCH_SIZE = 200;
    let merged = {};

    try {
      for (let i = 0; i < ids.length; i += BATCH_SIZE) {
        const batch = ids.slice(i, i + BATCH_SIZE);
        const res = await fetch(`${API_RATINGS}?civitai_image_ids=${batch.join(',')}`);
        if (!res.ok) continue;
        const data = await res.json();
        Object.assign(merged, data.ratings || {});
      }

      let changed = false;
      for (const [idStr, rating] of Object.entries(merged)) {
        const id = Number(idStr);
        // Don't overwrite a rating the user just made in this session
        if (!state.imageRatings.has(id)) {
          state.imageRatings.set(id, rating);
          changed = true;
        }
      }

      if (changed) {
        refreshAllRatingIndicators();
        updateFullscreenCounter();
        applyHideFilters();
        ensureVisibleSelection();
        checkAutoLoadIfAllHidden();
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
            applyHideFilters();
            ensureVisibleSelection();
            checkAutoLoadIfAllHidden();
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

    // Persist hide filters (only non-default values to keep URL short)
    const hf = state.hideFilters;
    if (hf.seen) p.set('hideSeen', '1');
    if (hf.saved) p.set('hideSaved', '1');
    if (hf.keep) p.set('hideKeep', '1');
    if (hf.skip) p.set('hideSkip', '1');
    // discard defaults to true, so we persist the inverse
    if (!hf.discard) p.set('hideDiscard', '0');

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
      hideSeen: p.has('hideSeen') ? p.get('hideSeen') === '1' : null,
      hideSaved: p.has('hideSaved') ? p.get('hideSaved') === '1' : null,
      hideKeep: p.has('hideKeep') ? p.get('hideKeep') === '1' : null,
      hideSkip: p.has('hideSkip') ? p.get('hideSkip') === '1' : null,
      hideDiscard: p.has('hideDiscard') ? p.get('hideDiscard') === '1' : null,
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
    // Mode toggle (Search ↔ Review)
    els.mode_bar.addEventListener('click', (e) => {
      const btn = e.target.closest('.mode-btn');
      if (!btn) return;
      const mode = btn.dataset.mode;
      if (mode === state.mode) return;
      switchMode(mode);
    });

    // Review rating sub-tabs
    els.review_rating_bar.addEventListener('click', (e) => {
      const btn = e.target.closest('.review-rating-btn');
      if (!btn) return;
      const rating = btn.dataset.rating;
      if (rating === state.reviewRating) return;
      state.reviewRating = rating;
      updateReviewRatingUI();
      if (state.mode === 'review') {
        state.offset = 0;
        state.hits = [];
        executeSearch();
      }
    });

    // Review artist facet panel — text filter and clear button
    const facetFilterInput = document.getElementById('review-facets-filter');
    if (facetFilterInput) {
      facetFilterInput.addEventListener('input', (e) => {
        state.reviewFacetFilter = e.target.value;
        renderReviewArtistFacets();
      });
    }
    const facetClearBtn = document.getElementById('review-facets-clear');
    if (facetClearBtn) {
      facetClearBtn.addEventListener('click', () => clearReviewArtists());
    }

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

    // Hide filter checkboxes
    els.hide_filter_bar.addEventListener('change', (e) => {
      if (e.target.matches('input[type="checkbox"][data-hide]')) {
        const key = e.target.dataset.hide;
        state.hideFilters[key] = e.target.checked;
        applyHideFilters();
        ensureVisibleSelection();
        saveStateToUrl();
        checkAutoLoadIfAllHidden();
      }
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
    els.detail_video.addEventListener('click', openFullscreen);
    els.fullscreen_close_btn.addEventListener('click', closeFullscreen);
    els.fullscreen_prev.addEventListener('click', () => navigateFullscreen(-1));
    els.fullscreen_next.addEventListener('click', () => navigateFullscreen(1));
    els.fullscreen_preview.querySelector('.fullscreen-backdrop').addEventListener('click', closeFullscreen);

    // Keyboard
    document.addEventListener('keydown', handleKeyboard);
  }

  /* ── Image preference (keep / discard / skip) ── */
  async function rateImage(rating) {
    const idx = state.selectedHitIndex;
    if (idx < 0 || idx >= state.hits.length) return;

    const hit = state.hits[idx];
    const body = {
      civitai_image_id: hit.id,
      rating,
      post_id: hit.postId ?? null,
      artist_id: hit.user?.id ?? null,
      artist_name: hit.user?.username ?? null,
      file_name: hit.file_name ?? null,
      blurhash: hit.blurhash ?? null,
      uuid: hit.url ?? null,
      image_url: hit.url ?? null,
      tags: hit.tagNames ?? null,
      generation_prompt: hit.prompt ?? null,
      generation_models: hit.models ?? null,
      reactions: hit.stats?.reactionCount ?? null,
      likes: hit.stats?.likeCount ?? null,
      position: idx,
      search_id: state.currentSearchId,
    };

    // Optimistic update — reflect rating immediately in the UI.
    state.imageRatings.set(hit.id, rating);
    updateTileRatingIndicator(idx);
    applyHideFilters();
    checkAutoLoadIfAllHidden();

    try {
      const res = await fetch(API_RATE_IMAGE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // Roll back optimistic state on failure.
        state.imageRatings.delete(hit.id);
        updateTileRatingIndicator(idx);
        console.error('Failed to rate image:', await res.text());
      } else {
        // Refresh artist summary with the new rating data.
        scheduleArtistSummaryRefresh();
      }
    } catch (err) {
      state.imageRatings.delete(hit.id);
      updateTileRatingIndicator(idx);
      console.error('Failed to rate image:', err);
    }
  }

  /* ── Hide filters ── */

  /** Restore hide-filter state from URL params and sync checkbox DOM. */
  function syncHideFiltersFromUrl(saved) {
    if (!saved) return;
    const hf = state.hideFilters;
    if (saved.hideSeen !== null) hf.seen = saved.hideSeen;
    if (saved.hideSaved !== null) hf.saved = saved.hideSaved;
    if (saved.hideKeep !== null) hf.keep = saved.hideKeep;
    if (saved.hideSkip !== null) hf.skip = saved.hideSkip;
    if (saved.hideDiscard !== null) hf.discard = saved.hideDiscard;

    if (els.hide_seen) els.hide_seen.checked = hf.seen;
    if (els.hide_saved) els.hide_saved.checked = hf.saved;
    if (els.hide_keep) els.hide_keep.checked = hf.keep;
    if (els.hide_skip) els.hide_skip.checked = hf.skip;
    if (els.hide_discard) els.hide_discard.checked = hf.discard;
  }

  /**
   * Determine whether a hit should be hidden by the active hide filters.
   * A hit is hidden if it matches ANY checked filter category.
   */
  function isHiddenByFilter(hit) {
    if (!hit || !hit.id) return false;
    const f = state.hideFilters;
    const id = hit.id;
    const rating = state.imageRatings.get(id);
    const isSaved = state.importedIds.has(id);

    if (f.seen && (isSaved || rating)) return true;
    if (f.saved && isSaved) return true;
    if (f.keep && rating === 'keep') return true;
    if (f.skip && rating === 'skip') return true;
    if (f.discard && rating === 'discard') return true;
    return false;
  }

  /** Find the next visible hit index from `from` (inclusive), stepping by ±1. */
  function nextVisibleIndex(from, step = 1) {
    for (let i = from; i >= 0 && i < state.hits.length; i += step) {
      if (!isHiddenByFilter(state.hits[i])) return i;
    }
    return -1;
  }

  /** Count currently visible (non-hidden) tiles. */
  function countVisibleTiles() {
    return state.hits.filter((h) => !isHiddenByFilter(h)).length;
  }

  /** Apply / remove the `.tile-hidden` class on all tiles based on current filters. */
  function applyHideFilters() {
    const tiles = els.gallery_grid.querySelectorAll('.tile[data-index]');
    tiles.forEach((tile) => {
      const idx = parseInt(tile.dataset.index, 10);
      const hidden = idx < state.hits.length && isHiddenByFilter(state.hits[idx]);
      tile.classList.toggle('tile-hidden', hidden);
    });
  }

  /**
   * If the currently-selected tile is hidden by filters, move selection to
   * the nearest visible neighbour.  Called by contexts that don't have a
   * subsequent navigation step (e.g. async rating/library loads, checkbox
   * toggles).  NOT called from rateImage() because advanceToNext() handles
   * navigation immediately after.
   */
  function ensureVisibleSelection() {
    if (state.selectedHitIndex >= 0 && state.selectedHitIndex < state.hits.length) {
      if (isHiddenByFilter(state.hits[state.selectedHitIndex])) {
        const next = nextVisibleIndex(state.selectedHitIndex + 1, 1);
        const fallback = next < 0 ? nextVisibleIndex(state.selectedHitIndex - 1, -1) : next;
        selectTile(fallback >= 0 ? fallback : state.selectedHitIndex);
      }
    }
  }

  /**
   * If too few visible tiles remain, auto-load additional pages until at
   * least one page worth (`state.limit`) of images are visible — or until
   * there are no more pages to load.  Guarded by `state.autoLoading` to
   * prevent infinite recursion while async ratings are fetched.
   */
  async function checkAutoLoadIfAllHidden() {
    if (state.autoLoading) return;
    if (state.hits.length === 0) return;

    state.autoLoading = true;
    try {
      // Keep loading pages while:
      //   • we haven't reached a full page of visible tiles, AND
      //   • there are more pages available
      while (countVisibleTiles() < state.limit && state.hits.length < state.total) {
        const prevHitCount = state.hits.length;
        state.offset += state.limit;
        await executeSearch(true);

        // Re-apply filters after the new hits are loaded so
        // countVisibleTiles() reflects the updated state.
        applyHideFilters();
        ensureVisibleSelection();

        // Safety valve: if the last fetch added zero hits (e.g. backend
        // post-filtering removed all hits for this page), bail out to
        // avoid an infinite loop even when state.total hasn't been capped.
        if (state.hits.length === prevHitCount) break;
      }
    } finally {
      state.autoLoading = false;
    }
  }

  /** Advance to the next image (gallery or fullscreen). */
  function advanceToNext() {
    const fullscreenOpen = !els.fullscreen_preview.classList.contains('hidden');
    if (fullscreenOpen) {
      navigateFullscreen(1);
    } else {
      const next = nextVisibleIndex(state.selectedHitIndex + 1, 1);
      if (next >= 0) {
        selectTile(next);
      } else if (_hasMorePages() && !state.loading) {
        // At the last visible tile in gallery mode — load next page.
        state.offset += state.limit;
        executeSearch(true);
      }
    }
  }

  /** Update the rating badge on a single tile after rating changes. */
  function updateTileRatingIndicator(idx) {
    const tile = els.gallery_grid.querySelector(`.tile[data-index="${idx}"]`);
    if (!tile) return;
    const hit = state.hits[idx];
    const rating = state.imageRatings.get(hit.id);

    // Remove old badge.
    const old = tile.querySelector('.tile-rating-badge');
    if (old) old.remove();

    if (!rating) return;

    const badge = document.createElement('span');
    badge.className = `tile-rating-badge is-${rating}`;
    if (rating === 'keep') badge.textContent = '★';
    else if (rating === 'discard') badge.textContent = '✕';
    else if (rating === 'skip') badge.textContent = '○';
    badge.title = rating;
    tile.appendChild(badge);
  }

  /** Re-apply rating badges to all currently rendered tiles. */
  function refreshAllRatingIndicators() {
    for (let i = 0; i < state.hits.length; i++) {
      updateTileRatingIndicator(i);
    }
  }

  /** Persist the search query so image ratings can be linked to it. */
  async function recordSearch(query, tags, sortBy, baseModel, username, nsfwLevels, matchStrategy, resultCount) {
    const body = {
      search_text: query || null,
      search_terms: {
        tags: tags || null,
        sort_by: sortBy || null,
        base_model: baseModel || null,
        username: username || null,
        nsfw_levels: nsfwLevels || null,
        match_strategy: matchStrategy || null,
      },
      result_count: resultCount,
    };
    try {
      const res = await fetch(API_SEARCH_RECORD, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        state.currentSearchId = data.id;
      }
    } catch (err) {
      // Non-critical — ratings still work without a search record.
      console.warn('Could not record search:', err);
    }
  }

  /* ── Artist score summary ── */
  let _artistSummaryTimer = null;

  /** Fetch the artist summary from the backend and render it. */
  async function fetchArtistSummary() {
    try {
      const res = await fetch(API_ARTIST_SUMMARY);
      if (!res.ok) return;
      const data = await res.json();
      // Backend returns a flat list of artist objects.
      const artists = Array.isArray(data) ? data : [];
      renderArtistSummary(artists);
    } catch (err) {
      console.warn('Artist summary fetch failed:', err);
    }
  }

  /** Schedule an artist-summary refresh (debounced). */
  function scheduleArtistSummaryRefresh() {
    if (_artistSummaryTimer) clearTimeout(_artistSummaryTimer);
    _artistSummaryTimer = setTimeout(() => fetchArtistSummary(), 600);
  }

  /** Render the artist summary panel. */
  function renderArtistSummary(artists) {
    const container = document.getElementById('artist-summary');
    if (!container) return;
    const body = container.querySelector('.artist-summary-body');
    if (!body) return;

    if (!artists || artists.length === 0) {
      body.innerHTML = '<p class="artist-summary-empty">No artist data yet — rate some images to see scores.</p>';
      return;
    }

    body.innerHTML = '';
    for (const a of artists) {
      const row = document.createElement('div');
      row.className = 'artist-row' + (a.is_blocked ? ' is-blocked' : '');

      const name = document.createElement('span');
      name.className = 'artist-row-name';
      name.textContent = a.artist_name;
      row.appendChild(name);

      const stats = document.createElement('span');
      stats.className = 'artist-row-stats';

      if (a.keeps > 0) {
        const keep = document.createElement('span');
        keep.className = 'artist-row-stat is-keep';
        keep.textContent = `↑${a.keeps}`;
        stats.appendChild(keep);
      }
      if (a.discards > 0) {
        const discard = document.createElement('span');
        discard.className = 'artist-row-stat is-discard';
        discard.textContent = `↓${a.discards}`;
        stats.appendChild(discard);
      }
      row.appendChild(stats);

      const score = document.createElement('span');
      const scoreClass = a.score > 0 ? 'is-positive' : a.score < 0 ? 'is-negative' : '';
      score.className = 'artist-row-score ' + scoreClass;
      score.textContent = (a.score > 0 ? '+' : '') + a.score;
      row.appendChild(score);

      const blockBtn = document.createElement('button');
      blockBtn.type = 'button';
      blockBtn.className = 'artist-block-btn' + (a.is_blocked ? ' is-blocked' : '');
      blockBtn.textContent = a.is_blocked ? 'Blocked' : 'Block';
      blockBtn.title = a.is_blocked ? 'Unblock this artist' : 'Block this artist from search results';
      blockBtn.addEventListener('click', () => toggleArtistBlock(a.artist_name, a.artist_id, !a.is_blocked));
      row.appendChild(blockBtn);

      body.appendChild(row);
    }
  }

  /** Toggle the blocked state of an artist. */
  async function toggleArtistBlock(artistName, artistId, shouldBlock) {
    try {
      const res = await fetch(API_ARTIST_BLOCK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          artist_name: artistName,
          artist_id: artistId ?? null,
          is_blocked: shouldBlock,
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }
      // Refresh the summary and re-run the current search
      fetchArtistSummary();
      // Re-search to apply/remove the block filter
      state.offset = 0;
      state.hits = [];
      executeSearch();
    } catch (err) {
      console.error('Failed to toggle artist block:', err);
      setStatus(`Failed to ${shouldBlock ? 'block' : 'unblock'} artist: ${err.message}`, 'is-error');
    }
  }

  /* ── Review-mode artist facets ── */

  /**
   * Fetch artist facet counts from the backend and render the panel.
   *
   * The rating and text-search (``q``) params mirror the current review
   * search so facet counts stay in sync with the displayed gallery.
   */
  async function fetchReviewArtistFacets() {
    try {
      const params = new URLSearchParams({ rating: state.reviewRating });
      const q = els.search_query?.value.trim() || '';
      if (q) params.set('q', q);

      const res = await fetch(`${API_RATED_ARTISTS}?${params}`);
      if (!res.ok) return;
      const data = await res.json();
      state.reviewArtistFacets = Array.isArray(data) ? data : [];
      renderReviewArtistFacets();
    } catch (err) {
      console.warn('Artist facets fetch failed:', err);
    }
  }

  /**
   * Render the artist facet rows inside the review-mode panel.
   *
   * Each row shows the artist name, image count, and a checkbox that
   * toggles membership in ``state.reviewSelectedArtists``.  The panel
   * also respects the in-panel text filter (``reviewFacetFilter``).
   */
  function renderReviewArtistFacets() {
    const body = document.getElementById('review-artist-facets-body');
    if (!body) return;
    const countBadge = document.getElementById('review-facets-count');

    const facets = state.reviewArtistFacets || [];
    if (countBadge) {
      countBadge.textContent = facets.length > 0 ? facets.length : '';
    }

    if (facets.length === 0) {
      body.innerHTML = '<p class="artist-summary-empty">No artists found for this rating.</p>';
      return;
    }

    const filterText = state.reviewFacetFilter.trim().toLowerCase();
    const visible = filterText
      ? facets.filter((f) => f.artist.toLowerCase().includes(filterText))
      : facets;

    body.innerHTML = '';
    for (const f of visible) {
      const row = document.createElement('label');
      row.className = 'artist-facet-row'
        + (state.reviewSelectedArtists.has(f.artist) ? ' is-selected' : '');

      const check = document.createElement('input');
      check.type = 'checkbox';
      check.checked = state.reviewSelectedArtists.has(f.artist);
      check.addEventListener('change', () => toggleReviewArtist(f.artist));

      const name = document.createElement('span');
      name.className = 'artist-facet-name';
      name.textContent = f.artist;
      name.title = f.artist;

      const count = document.createElement('span');
      count.className = 'artist-facet-count';
      count.textContent = f.count;

      row.append(check, name, count);
      body.appendChild(row);
    }

    // Show a note when the filter excludes all rows
    if (visible.length === 0 && facets.length > 0) {
      const note = document.createElement('p');
      note.className = 'artist-summary-empty';
      note.textContent = 'No artists match your filter.';
      body.appendChild(note);
    }
  }

  /**
   * Toggle an artist in the review selection set and re-run the search.
   */
  function toggleReviewArtist(artistName) {
    if (state.reviewSelectedArtists.has(artistName)) {
      state.reviewSelectedArtists.delete(artistName);
    } else {
      state.reviewSelectedArtists.add(artistName);
    }
    // Re-render checkboxes without re-fetching
    renderReviewArtistFacets();
    // Re-search with the updated artist filter
    state.offset = 0;
    state.hits = [];
    executeSearch();
  }

  /** Clear all selected artists and re-search. */
  function clearReviewArtists() {
    if (state.reviewSelectedArtists.size === 0) return;
    state.reviewSelectedArtists.clear();
    renderReviewArtistFacets();
    state.offset = 0;
    state.hits = [];
    executeSearch();
  }

  /* ── Mode switching (Search ↔ Review) ── */

  function updateModeUI() {
    // Mode bar active states
    els.mode_bar.querySelectorAll('.mode-btn').forEach((b) => {
      const active = b.dataset.mode === state.mode;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    // Show/hide review rating sub-bar
    els.review_rating_bar.classList.toggle('hidden', state.mode !== 'review');

    // Show/hide the artist-facets panel (review mode only)
    const facetPanel = document.getElementById('review-artist-facets');
    if (facetPanel) {
      facetPanel.hidden = state.mode !== 'review';
      // Auto-open the panel when entering review mode
      if (state.mode === 'review') facetPanel.open = true;
    }

    // Toggle CSS class on the toolbar to dim search form in review mode
    els.mode_bar.closest('.search-toolbar').classList.toggle('review-active', state.mode === 'review');

    // Swap sort options and search placeholder for the active mode
    if (_galleryToolbar) {
      if (state.mode === 'review') {
        _galleryToolbar.setSortOptions(REVIEW_SORT_OPTIONS);
        // Select the option matching current reviewSort/reviewOrder
        const combined = `${state.reviewSort}:${state.reviewOrder}`;
        _galleryToolbar.setSortValue(combined);
      } else {
        // Restore search-mode sort options
        _galleryToolbar.setSortOptions([
          { value: 'stats.reactionCountAllTime:desc', label: 'Most Reactions' },
          { value: 'stats.commentCountAllTime:desc', label: 'Most Comments' },
          { value: 'stats.collectedCountAllTime:desc', label: 'Most Collected' },
          { value: 'createdAt:desc', label: 'Newest' },
          { value: 'createdAtUnix:asc', label: 'Oldest' },
        ]);
        if (els.filter_sort.value) {
          _galleryToolbar.setSortValue(els.filter_sort.value);
        }
      }
    }

    // Update search placeholder for review mode
    if (els.search_query) {
      els.search_query.placeholder = state.mode === 'review'
        ? 'Filter rated images…'
        : 'Search CivitAI images…';
    }

    updateReviewRatingUI();
  }

  function updateReviewRatingUI() {
    els.review_rating_bar.querySelectorAll('.review-rating-btn').forEach((b) => {
      const active = b.dataset.rating === state.reviewRating;
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function switchMode(mode) {
    if (mode === state.mode) return;
    state.mode = mode;
    updateModeUI();

    // Reset result state and re-search
    state.offset = 0;
    state.hits = [];
    state.selectedHitIndex = -1;
    state.selectedIndices.clear();
    // Clear artist facet selection when switching modes
    state.reviewSelectedArtists.clear();
    state.reviewFacetFilter = '';

    if (mode === 'review') {
      setStatus('Loading rated images…', '');
    } else {
      setStatus('Enter a search query to get started.', '');
    }
    executeSearch();
  }

  /* ── Review mode search ── */
  async function executeReviewSearch(append = false) {
    state.loading = true;
    const savedOffset = state.offset;
    const rLabel = state.reviewRating === 'any' ? '' : state.reviewRating + ' ';
    setStatus(`Loading ${rLabel}images…`, 'is-loading');

    try {
      const params = new URLSearchParams({
        rating: state.reviewRating,
        limit: String(state.limit),
        offset: String(state.offset),
        sort: state.reviewSort,
        order: state.reviewOrder,
      });

      // Add text query if present — filters across tags, prompt, and artist
      const q = els.search_query?.value.trim() || '';
      if (q) params.set('q', q);

      // Add selected artist filter (comma-separated names)
      if (state.reviewSelectedArtists.size > 0) {
        params.set('artists', [...state.reviewSelectedArtists].join(','));
      }

      const res = await fetch(`${API_RATED}?${params}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();

      // Merge ratings from the backend response into our local map so
      // badges render correctly on first paint.
      if (data.ratings) {
        for (const [cid, r] of Object.entries(data.ratings)) {
          state.imageRatings.set(parseInt(cid, 10), r);
        }
      }

      const newHitsCount = append ? (data.hits || []).length : 0;
      if (append) {
        state.hits = state.hits.concat(data.hits || []);
      } else {
        state.hits = data.hits || [];
        state.selectedIndices.clear();
        state.lastSelectionAnchor = -1;
      }
      state.total = data.total || 0;
      if (append && newHitsCount === 0) {
        state.total = state.hits.length;
      }
      state.facets = data.facets || null;

      renderResults(append);
      fetchLibraryStatus();
      fetchReviewArtistFacets();

      const count = data.hits?.length || 0;
      if (count === 0 && !append) {
        setStatus(`No ${state.reviewRating === 'any' ? '' : state.reviewRating + ' '}images found. Rate some images first!`, '');
      } else if (append) {
        setStatus(`Loaded ${state.hits.length} of ${state.total} rated images.`, '');
      } else {
        const rLabel = state.reviewRating === 'any' ? '' : state.reviewRating + ' ';
        setStatus(`Showing ${state.hits.length} of ${state.total} ${rLabel}images.`, '');
      }
    } catch (err) {
      if (append) state.offset = savedOffset;
      setStatus(`Review load failed: ${err.message}`, 'is-error', {
        label: 'Retry',
        onClick: () => executeSearch(append),
      });
    } finally {
      state.loading = false;
    }
  }

  /* ── Search ── */
  async function executeSearch(append = false) {
    if (state.loading) return;

    if (!append) _preloadCache.clear();
    const savedOffset = state.offset;

    // ── Review mode: fetch rated images from DB ──
    if (state.mode === 'review') {
      return executeReviewSearch(append);
    }

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

      const newHitsCount = append ? (data.hits || []).length : 0;
      if (append) {
        state.hits = state.hits.concat(data.hits || []);
      } else {
        state.hits = data.hits || [];
        // Reset multi-select on fresh search
        state.selectedIndices.clear();
        state.lastSelectionAnchor = -1;
      }
      state.total = data.total || 0;
      // If append returned zero new hits, we've exhausted all results.
      // Cap total to current hit count so pagination loops terminate.
      // This must come AFTER the `state.total = data.total` assignment
      // so the cap isn't immediately overwritten.
      if (append && newHitsCount === 0) {
        state.total = state.hits.length;
      }
      state.facets = data.facets || null;

      renderResults(append);
      renderFacets();
      refreshAllRatingIndicators();
      applyHideFilters();

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

      // Select first visible tile on fresh search (respecting hide filters)
      if (!append && state.hits.length > 0) {
        const firstVisible = nextVisibleIndex(0, 1);
        setSingleSelection(firstVisible >= 0 ? firstVisible : 0);
      }

      // Fetch library status for current hits
      fetchLibraryStatus();

      // Restore persisted ratings so badges survive across sessions
      fetchImageRatings();

      // Refresh artist summary panel
      fetchArtistSummary();

      // Record the search to the backend for history (fresh searches only)
      if (!append) {
        recordSearch(query, tags, sortBy, baseModel, username, nsfwLevels, matchStrategy, data.total || 0);
      }

      // If we loaded a new page because fullscreen was at the last visible
      // tile, advance the fullscreen preview to the first new visible tile.
      if (append && state.fullscreenAdvanceOnLoad) {
        state.fullscreenAdvanceOnLoad = false;
        const firstNew = nextVisibleIndex(savedOffset, 1);
        if (firstNew >= 0) {
          selectTile(firstNew);
          const hit = state.hits[firstNew];
          _setFullscreenImage(hit);
          updateFullscreenCounter();
          renderFullscreenTags(hit);
        } else {
          // New page had no visible tiles either — keep loading or close.
          navigateFullscreen(1);
        }
      }
    } catch (err) {
      // Revert offset on failure so "Load More" can be retried.
      if (append) state.offset = savedOffset;
      // If we were advancing fullscreen and the load failed, restore state.
      if (state.fullscreenAdvanceOnLoad) {
        state.fullscreenAdvanceOnLoad = false;
        const idx = state.selectedHitIndex;
        if (idx >= 0 && idx < state.hits.length) {
          const hit = state.hits[idx];
          _setFullscreenImage(hit);
          updateFullscreenCounter();
        }
      }
      setStatus(`Search failed: ${err.message}`, 'is-error', {
        label: 'Retry search',
        onClick: () => executeSearch(append),
      });
    } finally {
      state.loading = false;
    }
  }

  /* ── Render gallery tiles ── */
  function isVideoHit(hit) {
    return !!(
      hit?.is_video ||
      hit?.type === 'video' ||
      (hit?.mimeType && String(hit.mimeType).startsWith('video/'))
    );
  }

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

    const video = isVideoHit(hit);
    const mediaURL = hit.thumbnail_url || hit.url || '';

    if (video) {
      // ── Video tile ──
      // Use the B2 playable URL directly.  No poster/thumbnail variants
      // exist for CivitAI videos, so we show the first frame.
      const vid = document.createElement('video');
      vid.alt = hit.prompt ? hit.prompt.substring(0, 80) : `Video ${hit.id}`;
      vid.className = 'tile-real-img';
      vid.muted = true;
      vid.loop = true;
      vid.playsInline = true;
      vid.preload = 'metadata';
      vid.src = mediaURL;

      // Hover-to-play: start the video when the user's mouse enters the tile.
      btn.addEventListener('mouseenter', () => {
        const p = vid.play();
        if (p) p.catch(() => {});
      });
      btn.addEventListener('mouseleave', () => {
        vid.pause();
        vid.currentTime = 0;
      });

      btn.appendChild(vid);

      // ▶ badge so video tiles are visually distinct
      const vbadge = document.createElement('span');
      vbadge.className = 'tile-video-badge';
      vbadge.textContent = '▶';
      btn.appendChild(vbadge);
    } else {
      // ── Image tile (existing flow) ──
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
        // Load real image with automatic retry (exponential backoff).
        // On success swap from blurhash placeholder to the real thumbnail.
        // On permanent failure show a visual error indicator on the tile.
        retryImageLoad(thumbURL, hit.id).then((ok) => {
          if (ok) {
            img.src = thumbURL;
            img.style.filter = '';
          } else {
            img.style.filter = 'blur(4px) grayscale(0.6)';
            img.style.opacity = '0.4';
            btn.classList.add('tile-image-error');
          }
        });
      } else {
        img.loading = 'lazy';
        img.src = thumbURL;
      }

      btn.appendChild(img);
    }

    const overlay = document.createElement('div');
    overlay.className = 'tile-overlay';
    const reactions = hit.stats?.reactionCount ?? '';
    const comments = hit.stats?.commentCount ?? '';
    overlay.innerHTML = `<span>♥ ${reactions}</span><span>💬 ${comments}</span>`;
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

    // Rating badge (★ keep, ✕ discard, ○ skip) for voted images
    const rating = state.imageRatings.get(hit.id);
    if (rating) {
      const badge = document.createElement('span');
      badge.className = `tile-rating-badge is-${rating}`;
      if (rating === 'keep') badge.textContent = '★';
      else if (rating === 'discard') badge.textContent = '✕';
      else if (rating === 'skip') badge.textContent = '○';
      badge.title = rating;
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
    if (isVideoHit(hit)) {
      els.detail_image.classList.add('hidden');
      els.detail_image.src = '';
      els.detail_video.classList.remove('hidden');
      els.detail_video.src = hit.video_url || hit.url || '';
    } else {
      els.detail_video.classList.add('hidden');
      els.detail_video.pause();
      els.detail_video.removeAttribute('src');
      els.detail_video.load();
      els.detail_image.classList.remove('hidden');
      const imageUrl = hit.thumbnail_url || hit.mid_res_url || hit.url || '';
      els.detail_image.src = imageUrl;
      els.detail_image.alt = hit.prompt ? hit.prompt.substring(0, 120) : `CivitAI image ${hit.id}`;
    }

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
    const video = isVideoHit(hit);

    if (video) {
      // ── Video fullscreen ──
      els.fullscreen_image.classList.add('hidden');
      els.fullscreen_image.src = '';
      els.fullscreen_video.classList.remove('hidden');
      els.fullscreen_video.src = hit.video_url || hit.url || '';
      state.fullscreenImageSource = 'original';
      state.fullscreenImageWidth = null;
      updateFullscreenCounter();
      return;
    }

    // ── Image fullscreen (existing tiered loading) ──
    els.fullscreen_video.classList.add('hidden');
    els.fullscreen_video.pause();
    els.fullscreen_video.removeAttribute('src');
    els.fullscreen_video.load();
    els.fullscreen_image.classList.remove('hidden');

    // Tiered loading strategy:
    //   1. Show thumbnail immediately (already cached from tiles)
    //   2. Upgrade to full-res original when loaded
    // The backend now serves the original image as mid_res_url, because
    // the CivitAI CDN's 1260px tier upscales typical AI images (512-1216px),
    // causing interpolation artifacts.
    const thumbUrl = hit.thumbnail_url || '';
    const midUrl = hit.mid_res_url || hit.url || '';

    const _applySource = (sourceTier, url) => {
      els.fullscreen_image.src = url;
      state.fullscreenImageSource = sourceTier;
      // naturalWidth is only available after load; set a fallback from the tier
      state.fullscreenImageWidth = sourceTier === 'thumbnail' ? 450 : null;
      updateFullscreenCounter();
    };

    // If the full-res is already preloaded, use it directly
    if (_preloadCache.get(hit.id) === 'loaded' && midUrl) {
      _applySource('original', midUrl);
      return;
    }

    // Show thumbnail first (instant from browser cache)
    if (thumbUrl) {
      _applySource('thumbnail', thumbUrl);
    }

    // Upgrade to full-res original in background with automatic retry
    if (midUrl && midUrl !== thumbUrl) {
      retryImageLoad(midUrl, `mid_${hit.id}`).then((ok) => {
        // Only apply if user hasn't navigated away
        if (ok && state.hits[state.selectedHitIndex]?.id === hit.id) {
          _applySource('original', midUrl);
          _preloadCache.set(hit.id, 'loaded');
        }
      });
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
    // Pause and clear video if it was playing
    els.fullscreen_video.pause();
    els.fullscreen_video.removeAttribute('src');
    els.fullscreen_video.load();
    els.fullscreen_video.classList.add('hidden');
  }

  /** True if there are more result pages available to load. */
  function _hasMorePages() {
    return state.total <= 0 || state.hits.length < state.total;
  }

  /**
   * Show a loading/blank state in the fullscreen preview while waiting for
   * the next cursor page.  Clears the image, tags, and updates the counter.
   */
  function _setFullscreenLoading() {
    els.fullscreen_image.src = '';
    els.fullscreen_image.alt = '';
    els.fullscreen_video.pause();
    els.fullscreen_video.removeAttribute('src');
    els.fullscreen_video.classList.add('hidden');
    els.fullscreen_counter.textContent = 'Loading more…';
    const cloud = els.fullscreen_tags_cloud;
    if (cloud) cloud.innerHTML = '';
  }

  function navigateFullscreen(delta) {
    const next = nextVisibleIndex(state.selectedHitIndex + delta, delta);
    if (next >= 0 && next < state.hits.length) {
      selectTile(next);
      // Tiered loading: show thumbnail instantly, upgrade to mid-res.
      const hit = state.hits[next];
      _setFullscreenImage(hit);
      updateFullscreenCounter();
      renderFullscreenTags(hit);
      return;
    }

    // No visible tile in the requested direction.
    // Forward at the end: try to load the next cursor page.
    if (delta > 0) {
      if (_hasMorePages() && !state.loading) {
        _setFullscreenLoading();
        state.fullscreenAdvanceOnLoad = true;
        state.offset += state.limit;
        executeSearch(true);
      } else {
        // No more pages — exit fullscreen.
        closeFullscreen();
      }
    }
    // delta < 0 (going backwards past the first tile): do nothing.
  }

  function updateFullscreenCounter() {
    const idx = state.selectedHitIndex;
    const selected = state.selectedIndices.has(idx) ? ' ✓' : '';
    const hit = state.hits[idx];
    const rating = hit && state.imageRatings.get(hit.id);
    const ratingLabel = rating ? ` [${rating}]` : '';
    els.fullscreen_counter.textContent = `${idx + 1} / ${state.hits.length}${selected}${ratingLabel}`;

    // Image source debug info (CivitAI ID + source tier + resolution)
    if (els.fullscreen_image_info && hit) {
      const civitaiId = hit.id ?? '—';
      const source = state.fullscreenImageSource || '—';
      // Use naturalWidth if the image has loaded, otherwise fall back to the
      // tier-based estimate set by _applySource.
      const natW = els.fullscreen_image.naturalWidth || state.fullscreenImageWidth;
      const natH = els.fullscreen_image.naturalHeight || null;
      const resStr = natW
        ? (natH ? `${natW}×${natH}` : `${natW}px`)
        : '—';
      els.fullscreen_image_info.textContent = `#${civitaiId} · ${source} · ${resStr}`;
    }
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
    // Ignore all single-key shortcuts when the user is typing in an
    // input, select, or textarea. Arrow-key gallery nav and other
    // non-character shortcuts are fine inside inputs.
    const tag = document.activeElement?.tagName;
    const isTyping = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';

    // Fullscreen nav
    const fullscreenOpen = !els.fullscreen_preview.classList.contains('hidden');

    // Preference shortcuts (z/x/c/r) only active when not typing and
    // there are gallery hits to vote on.
    if (!isTyping && state.hits.length > 0) {
      if (e.key === 'z' || e.key === 'Z') {
        e.preventDefault();
        rateImage('skip');
        advanceToNext();
        return;
      }
      if (e.key === 'x' || e.key === 'X') {
        e.preventDefault();
        rateImage('discard');
        advanceToNext();
        return;
      }
      if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        rateImage('keep');
        advanceToNext();
        return;
      }
      if (e.key === 'r' || e.key === 'R') {
        e.preventDefault();
        reloadCurrentImage();
        return;
      }
    }

    if (fullscreenOpen) {
      if (e.key === 'Escape') { closeFullscreen(); return; }
      if (e.key === 'ArrowLeft') { navigateFullscreen(-1); return; }
      if (e.key === 'ArrowRight') { navigateFullscreen(1); return; }
      if (e.key === 'Home') { const fi = nextVisibleIndex(0, 1); if (fi >= 0) { selectTile(fi); navigateFullscreenTo(fi); } return; }
      if (e.key === 'End') { const li = nextVisibleIndex(state.hits.length - 1, -1); if (li >= 0) { selectTile(li); navigateFullscreenTo(li); } return; }
      if (e.key === ' ') { e.preventDefault(); toggleFullscreenSelection(); return; }
      return;
    }

    // Gallery nav (when not in input/select)
    if (isTyping) return;

    if (e.key === 'ArrowLeft' && state.selectedHitIndex > 0) {
      e.preventDefault();
      const prev = nextVisibleIndex(state.selectedHitIndex - 1, -1);
      if (prev >= 0) selectTile(prev);
    }
    if (e.key === 'ArrowRight' && state.selectedHitIndex < state.hits.length - 1) {
      e.preventDefault();
      const next = nextVisibleIndex(state.selectedHitIndex + 1, 1);
      if (next >= 0) selectTile(next);
    }
    if (e.key === 'Home' && state.hits.length > 0) {
      e.preventDefault();
      const fi = nextVisibleIndex(0, 1);
      if (fi >= 0) selectTile(fi);
    }
    if (e.key === 'End' && state.hits.length > 0) {
      e.preventDefault();
      const li = nextVisibleIndex(state.hits.length - 1, -1);
      if (li >= 0) selectTile(li);
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
  function setStatus(text, cls, action) {
    els.search_status_text.textContent = text;
    els.search_status.className = 'search-status';
    if (cls) els.search_status.classList.add(cls);

    // Remove any previous action button
    const oldBtn = els.search_status.querySelector('.status-action-btn');
    if (oldBtn) oldBtn.remove();

    // Optionally add an action button (e.g. "Retry")
    if (action) {
      const btn = document.createElement('button');
      btn.className = 'status-action-btn';
      btn.textContent = action.label;
      btn.addEventListener('click', () => {
        btn.remove();
        action.onClick();
      });
      els.search_status.appendChild(btn);
    }
  }

  /* ── Boot ── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
