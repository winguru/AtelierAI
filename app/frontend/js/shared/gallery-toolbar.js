/**
 * GalleryToolbar — shared IIFE module for gallery toolbar controls.
 *
 * Provides a consistent toolbar row with:
 *   - Thumbnail size slider + preset buttons
 *   - Infinite scroll toggle
 *   - Sort dropdown (options supplied by each page)
 *
 * Usage:
 *   const ctrl = GalleryToolbar.create({
 *     container:        DOM element to insert toolbar into,
 *     position:         'beforeend' | 'beforebegin' | etc. (default 'beforeend'),
 *     cssVariableHost:  element whose --thumb-size CSS var is updated (required),
 *     sortOptions:      [{ value, label }] for the sort dropdown,
 *     initialThumbSize: number (default 165),
 *     initialInfiniteScroll: boolean (default true),
 *     initialSortValue: string (default first option's value),
 *     storagePrefix:    string for localStorage keys (default 'atelier.galleryToolbar.'),
 *     onThumbSizeChange(size): callback,
 *     onInfiniteScrollToggle(enabled): callback,
 *     onSortChange(value): callback,
 *     idPrefix:         string to namespace element IDs (default ''),
 *     hiddenControls:   Set of control names to hide ('sort', 'infiniteScroll', 'thumbSize'),
 *   });
 *
 *   ctrl.getThumbSize()        → number
 *   ctrl.setThumbSize(n)       → void
 *   ctrl.getInfiniteScroll()   → boolean
 *   ctrl.setInfiniteScroll(b)  → void
 *   ctrl.getSortValue()        → string
 *   ctrl.setSortValue(v)       → void
 *   ctrl.destroy()             → void (removes DOM + listeners)
 */
window.GalleryToolbar = (function () {
  'use strict';

  const MIN_THUMB = 120;
  const MAX_THUMB = 260;
  const THUMB_STEP = 5;
  const DEFAULT_THUMB = 165;

  const PRESETS = [
    { label: 'Compact', size: 130 },
    { label: 'Balanced', size: 165 },
    { label: 'Large', size: 220 },
  ];

  function readStored(key, fallback) {
    try { const v = localStorage.getItem(key); return v !== null ? v : fallback; } catch { return fallback; }
  }
  function writeStored(key, value) {
    try { localStorage.setItem(key, String(value)); } catch { /* ignore */ }
  }
  function readStoredBool(key, fallback) {
    const v = readStored(key, null);
    if (v === null) return fallback;
    return v === 'true';
  }
  function readStoredNumber(key, fallback) {
    const v = readStored(key, null);
    if (v === null) return fallback;
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function create(options) {
    const {
      container,
      position = 'beforeend',
      cssVariableHost,
      sortOptions = [],
      initialThumbSize,
      initialInfiniteScroll,
      initialSortValue,
      storagePrefix = 'atelier.galleryToolbar.',
      onThumbSizeChange = () => {},
      onInfiniteScrollToggle = () => {},
      onSortChange = () => {},
      idPrefix = '',
      hiddenControls = new Set(),
    } = options;

    if (!container) throw new Error('GalleryToolbar: container is required');

    const prefix = idPrefix ? idPrefix + '-' : '';
    const thumbSizeKey = storagePrefix + 'thumbSize';
    const infiniteKey = storagePrefix + 'infinite';
    const sortKey = storagePrefix + 'sort';

    let thumbSize = readStoredNumber(thumbSizeKey, initialThumbSize ?? DEFAULT_THUMB);
    thumbSize = Math.max(MIN_THUMB, Math.min(MAX_THUMB, thumbSize));

    let infiniteEnabled = readStoredBool(infiniteKey, initialInfiniteScroll ?? true);
    let sortValue = readStored(sortKey, initialSortValue ?? (sortOptions[0]?.value ?? ''));

    // ── Build DOM ──
    const toolbar = document.createElement('div');
    toolbar.className = 'toolbar-row toolbar-row-secondary gallery-toolbar-shared';

    // -- Leading: thumb size --
    const leading = document.createElement('div');
    leading.className = 'toolbar-secondary-leading';

    if (!hiddenControls.has('thumbSize')) {
      const sliderWrap = document.createElement('label');
      sliderWrap.className = 'slider-wrap';
      sliderWrap.htmlFor = prefix + 'thumb-size-slider';

      const sliderLabel = document.createElement('span');
      sliderLabel.textContent = 'Thumb Size';

      const slider = document.createElement('input');
      slider.id = prefix + 'thumb-size-slider';
      slider.type = 'range';
      slider.min = MIN_THUMB;
      slider.max = MAX_THUMB;
      slider.step = THUMB_STEP;
      slider.value = thumbSize;

      const output = document.createElement('output');
      output.id = prefix + 'thumb-size-value';
      output.htmlFor = prefix + 'thumb-size-slider';
      output.textContent = thumbSize + 'px';

      sliderWrap.append(sliderLabel, slider, output);

      const presetWrap = document.createElement('div');
      presetWrap.className = 'preset-wrap';
      presetWrap.setAttribute('role', 'group');
      presetWrap.setAttribute('aria-label', 'Thumbnail size presets');

      const presetButtons = PRESETS.map((p) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn ghost btn-sm thumb-preset' + (p.size === thumbSize ? ' active' : '');
        btn.dataset.size = String(p.size);
        btn.textContent = p.label;
        return btn;
      });
      presetWrap.append(...presetButtons);
      leading.append(sliderWrap, presetWrap);
    }

    // -- Trailing: infinite scroll + sort --
    const trailing = document.createElement('div');
    trailing.className = 'toolbar-secondary-trailing';

    if (!hiddenControls.has('infiniteScroll')) {
      const toggleWrap = document.createElement('label');
      toggleWrap.className = 'toggle-wrap toolbar-inline-toggle';
      toggleWrap.htmlFor = prefix + 'infinite-scroll-toggle';

      const checkbox = document.createElement('input');
      checkbox.id = prefix + 'infinite-scroll-toggle';
      checkbox.type = 'checkbox';
      checkbox.checked = infiniteEnabled;

      const toggleSpan = document.createElement('span');
      toggleSpan.textContent = 'Infinite scroll';

      toggleWrap.append(checkbox, toggleSpan);
      trailing.appendChild(toggleWrap);
    }

    if (!hiddenControls.has('sort') && sortOptions.length > 0) {
      const sortWrap = document.createElement('label');
      sortWrap.className = 'sort-wrap';
      sortWrap.htmlFor = prefix + 'sort-order-select';

      const sortLabel = document.createElement('span');
      sortLabel.textContent = 'Sort';

      const select = document.createElement('select');
      select.id = prefix + 'sort-order-select';
      sortOptions.forEach((opt) => {
        const option = document.createElement('option');
        option.value = opt.value;
        option.textContent = opt.label;
        if (opt.value === sortValue) option.selected = true;
        select.appendChild(option);
      });

      sortWrap.append(sortLabel, select);
      trailing.appendChild(sortWrap);
    }

    toolbar.append(leading, trailing);
    container.insertAdjacentElement(position, toolbar);

    // ── Sync helpers ──
    function syncThumbSize() {
      const px = thumbSize + 'px';
      if (cssVariableHost) cssVariableHost.style.setProperty('--thumb-size', px);
      const slider = toolbar.querySelector(`#${CSS.escape(prefix + 'thumb-size-slider')}`);
      const output = toolbar.querySelector(`#${CSS.escape(prefix + 'thumb-size-value')}`);
      if (slider) slider.value = String(thumbSize);
      if (output) output.textContent = px;
      toolbar.querySelectorAll('.thumb-preset').forEach((btn) => {
        btn.classList.toggle('active', Number(btn.dataset.size) === thumbSize);
      });
    }

    function updatePagingBtn() {
      // The parent page can handle load-more visibility in its callback
    }

    // ── Event listeners ──
    const listeners = [];

    function addListener(el, event, fn) {
      if (!el) return;
      el.addEventListener(event, fn);
      listeners.push({ el, event, fn });
    }

    // Slider
    const slider = toolbar.querySelector(`#${CSS.escape(prefix + 'thumb-size-slider')}`);
    addListener(slider, 'input', () => {
      thumbSize = Math.max(MIN_THUMB, Math.min(MAX_THUMB, Number(slider.value) || DEFAULT_THUMB));
      syncThumbSize();
      writeStored(thumbSizeKey, thumbSize);
      onThumbSizeChange(thumbSize);
    });

    // Presets
    toolbar.querySelectorAll('.thumb-preset').forEach((btn) => {
      addListener(btn, 'click', () => {
        const preset = Number(btn.dataset.size);
        if (!Number.isFinite(preset)) return;
        thumbSize = Math.max(MIN_THUMB, Math.min(MAX_THUMB, preset));
        syncThumbSize();
        writeStored(thumbSizeKey, thumbSize);
        onThumbSizeChange(thumbSize);
      });
    });

    // Infinite scroll toggle
    const checkbox = toolbar.querySelector(`#${CSS.escape(prefix + 'infinite-scroll-toggle')}`);
    addListener(checkbox, 'change', () => {
      infiniteEnabled = checkbox.checked;
      writeStored(infiniteKey, infiniteEnabled);
      onInfiniteScrollToggle(infiniteEnabled);
    });

    // Sort select
    const select = toolbar.querySelector(`#${CSS.escape(prefix + 'sort-order-select')}`);
    addListener(select, 'change', () => {
      sortValue = select.value;
      writeStored(sortKey, sortValue);
      onSortChange(sortValue);
    });

    // Apply initial state
    syncThumbSize();

    // ── Public API ──
    return {
      getThumbSize() { return thumbSize; },
      setThumbSize(n) {
        thumbSize = Math.max(MIN_THUMB, Math.min(MAX_THUMB, n));
        syncThumbSize();
        writeStored(thumbSizeKey, thumbSize);
        onThumbSizeChange(thumbSize);
      },
      getInfiniteScroll() { return infiniteEnabled; },
      setInfiniteScroll(b) {
        infiniteEnabled = !!b;
        if (checkbox) checkbox.checked = infiniteEnabled;
        writeStored(infiniteKey, infiniteEnabled);
        onInfiniteScrollToggle(infiniteEnabled);
      },
      getSortValue() { return sortValue; },
      setSortValue(v) {
        sortValue = v;
        if (select) select.value = v;
        writeStored(sortKey, v);
      },
      getElement() { return toolbar; },
      destroy() {
        listeners.forEach(({ el, event, fn }) => el.removeEventListener(event, fn));
        listeners.length = 0;
        toolbar.remove();
      },
    };
  }

  return { create };
})();
