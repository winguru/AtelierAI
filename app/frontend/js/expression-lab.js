/**
 * Expression Lab — NSFW Level Icon Playground
 *
 * Loads 4-panel expression images from /frontend/images/expressions/,
 * lets the user split them into individual panels, reorder,
 * assign NSFW levels, and preview how they'd look as a widget.
 */
(() => {
  'use strict';

  // ── Config ──
  const EXPRESSIONS_DIR = '/frontend/images/expressions/';
  const PANELS_PER_SET = 4;

  const NSFW_LEVELS = [
    { key: 'safe',     label: 'Safe',     color: '#4caf50' },
    { key: 'soft',     label: 'Soft',     color: '#8bc34a' },
    { key: 'moderate', label: 'Moderate', color: '#ff9800' },
    { key: 'extreme',  label: 'Extreme',  color: '#f44336' },
  ];

  const SAFETY_CLASSES = [
    { key: 'safe',     label: 'Safe' },
    { key: 'mature',   label: 'Mature' },
    { key: 'explicit', label: 'Explicit' },
  ];

  const GRANULAR_RATINGS = [
    { key: 'pg',   label: 'PG' },
    { key: 'pg13', label: 'PG-13' },
    { key: 'r',    label: 'R' },
    { key: 'x',    label: 'X' },
    { key: 'xxx',  label: 'XXX' },
  ];

  // ── State ──
  let imageFiles = [];
  let activeSetIndex = 0;
  let panelSize = 220;
  let panelOrder = [0, 1, 2, 3]; // index into the 4 quadrants
  let levelAssignments = {}; // setImageIndex -> [levelKey per position]
  let widgetSelectedSafety = 'explicit';
  let widgetSelectedGranular = 'xxx';

  // Per-set, per-quadrant crop insets (percent of the 50% panel)
  // cropInsets[setIndex] = { quadrant: { top, bottom, left, right } }
  let cropInsets = {};
  let selectedCropPanel = 0; // which quadrant is selected for crop editing
  let downsampleMethod = 'auto'; // current global method

  // ── DOM refs ──
  const $setSelector   = document.getElementById('set-selector');
  const $workspace     = document.getElementById('workspace');
  const $setLabel      = document.getElementById('set-label');
  const $panelGrid     = document.getElementById('panel-grid');
  const $levelSlots    = document.getElementById('level-slots');
  const $sizeSlider    = document.getElementById('size-slider');
  const $sizeLabel     = document.getElementById('size-label');
  const $btnGrid       = document.getElementById('btn-grid');
  const $btnStrip      = document.getElementById('btn-strip');
  const $btnExport     = document.getElementById('btn-export');
  const $exportStatus  = document.getElementById('export-status');
  const $overviewGrid  = document.getElementById('overview-grid');
  const $widgetSafetyPanels  = document.getElementById('widget-safety-panels');
  const $widgetGranularPanels = document.getElementById('widget-granular-panels');
  const $themeToggle   = document.getElementById('theme-toggle');

  // Crop UI refs
  const $cropPanelSelector = document.getElementById('crop-panel-selector');
  const $cropTop      = document.getElementById('crop-top');
  const $cropBottom   = document.getElementById('crop-bottom');
  const $cropLeft     = document.getElementById('crop-left');
  const $cropRight    = document.getElementById('crop-right');
  const $cropTopVal   = document.getElementById('crop-top-val');
  const $cropBottomVal = document.getElementById('crop-bottom-val');
  const $cropLeftVal  = document.getElementById('crop-left-val');
  const $cropRightVal = document.getElementById('crop-right-val');
  const $cropPreviewCanvas = document.getElementById('crop-preview-canvas');
  const $btnCropReset = document.getElementById('btn-crop-reset');
  const $btnCropResetAll = document.getElementById('btn-crop-reset-all');

  // Downsampling refs
  const $downsampleMethod = document.getElementById('downsample-method');
  const $comparePanel     = document.getElementById('compare-panel');
  const $downsampleGrid   = document.getElementById('downsample-grid');

  // ── Dark mode ──
  function applyTheme() {
    const stored = localStorage.getItem('atelier-theme');
    const isDark = stored === 'dark' || (!stored && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.dataset.theme = isDark ? 'dark' : 'light';
    if ($themeToggle) $themeToggle.checked = isDark;
  }

  if ($themeToggle) {
    $themeToggle.addEventListener('change', () => {
      const isDark = $themeToggle.checked;
      document.body.dataset.theme = isDark ? 'dark' : 'light';
      localStorage.setItem('atelier-theme', isDark ? 'dark' : 'light');
    });
  }
  applyTheme();

  // ── Image discovery ──
  // We know the filenames; fetch directory listing or hardcode
  // Since there's no auto-index, we use a known list or fetch a manifest
  async function discoverImages() {
    try {
      // Try fetching a simple listing endpoint
      const resp = await fetch('/api/expression-sets');
      if (resp.ok) {
        const data = await resp.json();
        return data.files || [];
      }
    } catch {
      // fallback
    }
    // Fallback: hardcode known files
    return [
      '2043060221235957760.png',
      '2043064300846714880.png',
      '2043064488055279616.png',
      '2043066295942909952.png',
      '2043066672406859776.png',
      '2043069660751990784.png',
      '2043070176877875200.png',
      '2043070572883087360.png',
      '2043072057557651456.png',
    ];
  }

  // ── Panel splitting ──
  // Each image is 1280x1280 with 4 panels in a 2x2 grid
  // Panel positions: 0=top-left, 1=top-right, 2=bottom-left, 3=bottom-right
  function getPanelCrop(panelIndex) {
    const col = panelIndex % 2;
    const row = Math.floor(panelIndex / 2);
    const baseX = col * 50;
    const baseY = row * 50;
    const baseW = 50;
    const baseH = 50;

    // Apply crop insets for current set
    const insets = getCropInsets(activeSetIndex, panelIndex);
    const x = baseX + (insets.left / 100) * baseW;
    const y = baseY + (insets.top / 100) * baseH;
    const w = baseW * (1 - (insets.left + insets.right) / 100);
    const h = baseH * (1 - (insets.top + insets.bottom) / 100);
    return { x, y, w: Math.max(1, w), h: Math.max(1, h) };
  }

  function getPanelCropForSet(setIndex, panelIndex) {
    const col = panelIndex % 2;
    const row = Math.floor(panelIndex / 2);
    const baseX = col * 50;
    const baseY = row * 50;
    const baseW = 50;
    const baseH = 50;
    const insets = getCropInsets(setIndex, panelIndex);
    const x = baseX + (insets.left / 100) * baseW;
    const y = baseY + (insets.top / 100) * baseH;
    const w = baseW * (1 - (insets.left + insets.right) / 100);
    const h = baseH * (1 - (insets.top + insets.bottom) / 100);
    return { x, y, w: Math.max(1, w), h: Math.max(1, h) };
  }

  function getCropInsets(setIndex, panelIndex) {
    if (!cropInsets[setIndex]) return { top: 0, bottom: 0, left: 0, right: 0 };
    return cropInsets[setIndex][panelIndex] || { top: 0, bottom: 0, left: 0, right: 0 };
  }

  function setCropInsets(setIndex, panelIndex, insets) {
    if (!cropInsets[setIndex]) cropInsets[setIndex] = {};
    cropInsets[setIndex][panelIndex] = { ...insets };
  }

  function createPanelCanvas(img, panelIndex, size, method) {
    method = method || downsampleMethod;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');

    if (method === 'pixelated') {
      ctx.imageSmoothingEnabled = false;
    } else if (method === 'crisp-edges') {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'low';
    } else if (method === 'auto') {
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
    } else if (method === 'lanczos2' || method === 'lanczos3') {
      // Multi-step downscale for better quality
      const steps = method === 'lanczos3' ? 3 : 2;
      return createLanczosCanvas(img, panelIndex, size, steps);
    } else if (method === 'area-avg') {
      return createAreaAvgCanvas(img, panelIndex, size);
    }

    const crop = getPanelCrop(panelIndex);
    const sx = (crop.x / 100) * img.naturalWidth;
    const sy = (crop.y / 100) * img.naturalHeight;
    const sw = (crop.w / 100) * img.naturalWidth;
    const sh = (crop.h / 100) * img.naturalHeight;
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, size, size);
    return canvas;
  }

  // Multi-step downscale (step-by-step halving for better quality)
  function createLanczosCanvas(img, panelIndex, finalSize, steps) {
    const crop = getPanelCrop(panelIndex);
    const sx = (crop.x / 100) * img.naturalWidth;
    const sy = (crop.y / 100) * img.naturalHeight;
    const sw = (crop.w / 100) * img.naturalWidth;
    const sh = (crop.h / 100) * img.naturalHeight;

    // First extract the panel at full crop resolution
    let currentCanvas = document.createElement('canvas');
    currentCanvas.width = Math.round(sw);
    currentCanvas.height = Math.round(sh);
    let ctx = currentCanvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, currentCanvas.width, currentCanvas.height);

    // Step down by half each time
    for (let i = 0; i < steps; i++) {
      const halfW = Math.max(1, Math.round(currentCanvas.width / 2));
      const halfH = Math.max(1, Math.round(currentCanvas.height / 2));
      const next = document.createElement('canvas');
      next.width = halfW;
      next.height = halfH;
      const nctx = next.getContext('2d');
      nctx.imageSmoothingEnabled = true;
      nctx.imageSmoothingQuality = 'high';
      nctx.drawImage(currentCanvas, 0, 0, halfW, halfH);
      currentCanvas = next;
    }

    // Final step to target size
    const result = document.createElement('canvas');
    result.width = finalSize;
    result.height = finalSize;
    const rctx = result.getContext('2d');
    rctx.imageSmoothingEnabled = true;
    rctx.imageSmoothingQuality = 'high';
    rctx.drawImage(currentCanvas, 0, 0, finalSize, finalSize);
    return result;
  }

  // Area-average downscale: draw to small canvas, let browser average pixels
  function createAreaAvgCanvas(img, panelIndex, size) {
    const crop = getPanelCrop(panelIndex);
    const sx = (crop.x / 100) * img.naturalWidth;
    const sy = (crop.y / 100) * img.naturalHeight;
    const sw = (crop.w / 100) * img.naturalWidth;
    const sh = (crop.h / 100) * img.naturalHeight;

    // Create intermediate at a medium size first
    const midSize = Math.max(size * 2, 128);
    const mid = document.createElement('canvas');
    mid.width = midSize;
    mid.height = midSize;
    const mctx = mid.getContext('2d');
    mctx.imageSmoothingEnabled = true;
    mctx.imageSmoothingQuality = 'medium';
    mctx.drawImage(img, sx, sy, sw, sh, 0, 0, midSize, midSize);

    const result = document.createElement('canvas');
    result.width = size;
    result.height = size;
    const rctx = result.getContext('2d');
    rctx.imageSmoothingEnabled = true;
    rctx.imageSmoothingQuality = 'high';
    rctx.drawImage(mid, 0, 0, size, size);
    return result;
  }

  // ── Rendering ──
  function renderSetSelector() {
    $setSelector.innerHTML = '';
    imageFiles.forEach((file, idx) => {
      const thumb = document.createElement('div');
      thumb.className = 'set-thumb' + (idx === activeSetIndex ? ' active' : '');
      thumb.title = file;
      thumb.dataset.index = idx;

      const img = document.createElement('img');
      img.src = EXPRESSIONS_DIR + file;
      img.loading = 'lazy';
      img.alt = file;
      img.draggable = false;
      thumb.append(img);

      thumb.addEventListener('click', () => {
        activeSetIndex = idx;
        panelOrder = [0, 1, 2, 3];
        renderAll();
      });

      $setSelector.append(thumb);
    });
  }

  function renderWorkspace() {
    const file = imageFiles[activeSetIndex];
    if (!file) return;

    $setLabel.textContent = `Set ${activeSetIndex + 1} / ${imageFiles.length} — ${file}`;
    $panelGrid.innerHTML = '';

    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = EXPRESSIONS_DIR + file;
    img.onload = () => {
      panelOrder.forEach((quadIndex, displayPos) => {
        const card = document.createElement('div');
        card.className = 'panel-card';
        card.style.width = panelSize + 'px';
        card.style.height = panelSize + 'px';
        card.dataset.displayPos = displayPos;
        card.dataset.quadrant = quadIndex;
        card.draggable = true;

        const canvas = createPanelCanvas(img, quadIndex, panelSize);
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        card.append(canvas);

        // Position badge
        const idx = document.createElement('span');
        idx.className = 'panel-index';
        idx.textContent = displayPos + 1;
        card.append(idx);

        // Level badge
        const assignments = levelAssignments[activeSetIndex] || NSFW_LEVELS.map(l => l.key);
        const levelKey = assignments[displayPos];
        const level = NSFW_LEVELS.find(l => l.key === levelKey) || NSFW_LEVELS[displayPos];
        const badge = document.createElement('span');
        badge.className = 'panel-level-badge';
        badge.style.background = level.color;
        badge.textContent = level.label;
        card.append(badge);

        // Drag events
        card.addEventListener('dragstart', onDragStart);
        card.addEventListener('dragover', onDragOver);
        card.addEventListener('dragleave', onDragLeave);
        card.addEventListener('drop', onDrop);
        card.addEventListener('dragend', onDragEnd);

        $panelGrid.append(card);
      });
    };
  }

  function renderLevelAssignment() {
    $levelSlots.innerHTML = '';
    const file = imageFiles[activeSetIndex];
    if (!file) return;

    const img = document.createElement('img');
    img.src = EXPRESSIONS_DIR + file;
    img.loading = 'lazy';

    const assignments = levelAssignments[activeSetIndex] || NSFW_LEVELS.map(l => l.key);

    panelOrder.forEach((quadIndex, displayPos) => {
      const slot = document.createElement('div');
      slot.className = 'level-slot';

      // Thumbnail
      const thumbWrap = document.createElement('div');
      thumbWrap.className = 'level-slot-thumb';
      const thumbCanvas = document.createElement('canvas');
      thumbCanvas.width = 80;
      thumbCanvas.height = 80;

      const tempImg = new Image();
      tempImg.crossOrigin = 'anonymous';
      tempImg.src = EXPRESSIONS_DIR + file;
      tempImg.onload = () => {
        const ctx = thumbCanvas.getContext('2d');
        const crop = getPanelCrop(quadIndex);
        const sx = (crop.x / 100) * tempImg.naturalWidth;
        const sy = (crop.y / 100) * tempImg.naturalHeight;
        const sw = (crop.w / 100) * tempImg.naturalWidth;
        const sh = (crop.h / 100) * tempImg.naturalHeight;
        ctx.drawImage(tempImg, sx, sy, sw, sh, 0, 0, 80, 80);
      };
      thumbWrap.append(thumbCanvas);
      slot.append(thumbWrap);

      // Position label
      const posLabel = document.createElement('span');
      posLabel.className = 'slot-position';
      posLabel.textContent = `Position ${displayPos + 1}`;
      slot.append(posLabel);

      // Dropdown
      const select = document.createElement('select');
      NSFW_LEVELS.forEach(level => {
        const opt = document.createElement('option');
        opt.value = level.key;
        opt.textContent = level.label;
        if (level.key === assignments[displayPos]) opt.selected = true;
        select.append(opt);
      });
      select.addEventListener('change', () => {
        if (!levelAssignments[activeSetIndex]) {
          levelAssignments[activeSetIndex] = NSFW_LEVELS.map(l => l.key);
        }
        levelAssignments[activeSetIndex][displayPos] = select.value;
        renderWorkspace();
        renderWidgetPreview();
        renderOverview();
      });
      slot.append(select);

      $levelSlots.append(slot);
    });
  }

  function renderWidgetPreview() {
    const file = imageFiles[activeSetIndex];
    if (!file) return;

    const assignments = levelAssignments[activeSetIndex] || NSFW_LEVELS.map(l => l.key);

    // Safety class preview (3-level: safe, moderate, extreme)
    $widgetSafetyPanels.innerHTML = '';
    const safetyMap = [
      { key: 'safe', levelIdx: 0, label: 'Safe' },
      { key: 'mature', levelIdx: 2, label: 'Mature' },
      { key: 'explicit', levelIdx: 3, label: 'Explicit' },
    ];

    safetyMap.forEach(({ key, levelIdx, label }) => {
      const panel = document.createElement('div');
      panel.className = 'widget-panel' + (widgetSelectedSafety === key ? ' selected' : '');

      const imgWrap = document.createElement('div');
      imgWrap.className = 'widget-panel-img';
      const canvas = document.createElement('canvas');
      canvas.width = 52;
      canvas.height = 52;
      imgWrap.append(canvas);
      panel.append(imgWrap);

      const lbl = document.createElement('span');
      lbl.className = 'widget-panel-label';
      lbl.textContent = label;
      panel.append(lbl);

      panel.addEventListener('click', () => {
        widgetSelectedSafety = key;
        renderWidgetPreview();
      });

      $widgetSafetyPanels.append(panel);

      // Draw the panel
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = EXPRESSIONS_DIR + file;
      img.onload = () => {
        const ctx = canvas.getContext('2d');
        const quadIdx = panelOrder[levelIdx];
        const crop = getPanelCrop(quadIdx);
        const sx = (crop.x / 100) * img.naturalWidth;
        const sy = (crop.y / 100) * img.naturalHeight;
        const sw = (crop.w / 100) * img.naturalWidth;
        const sh = (crop.h / 100) * img.naturalHeight;
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 52, 52);
      };
    });

    // Granular rating preview (5-level using repeated panels)
    $widgetGranularPanels.innerHTML = '';
    // Map 5 levels onto 4 panels: X and XXX share the extreme panel
    const granularPanelMap = [0, 1, 2, 3, 3]; // X & XXX both use extreme

    GRANULAR_RATINGS.forEach(({ key, label }, idx) => {
      const panelIdx = granularPanelMap[idx];
      const panel = document.createElement('div');
      panel.className = 'widget-panel' + (widgetSelectedGranular === key ? ' selected' : '');

      const imgWrap = document.createElement('div');
      imgWrap.className = 'widget-panel-img';
      const canvas = document.createElement('canvas');
      canvas.width = 52;
      canvas.height = 52;
      imgWrap.append(canvas);
      panel.append(imgWrap);

      const lbl = document.createElement('span');
      lbl.className = 'widget-panel-label';
      lbl.textContent = label;
      panel.append(lbl);

      panel.addEventListener('click', () => {
        widgetSelectedGranular = key;
        renderWidgetPreview();
      });

      $widgetGranularPanels.append(panel);

      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = EXPRESSIONS_DIR + file;
      img.onload = () => {
        const ctx = canvas.getContext('2d');
        const quadIdx = panelOrder[panelIdx];
        const crop = getPanelCrop(quadIdx);
        const sx = (crop.x / 100) * img.naturalWidth;
        const sy = (crop.y / 100) * img.naturalHeight;
        const sw = (crop.w / 100) * img.naturalWidth;
        const sh = (crop.h / 100) * img.naturalHeight;
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 52, 52);
      };
    });
  }

  function renderOverview() {
    $overviewGrid.innerHTML = '';

    imageFiles.forEach((file, setIdx) => {
      const card = document.createElement('div');
      card.className = 'overview-card';

      const header = document.createElement('div');
      header.className = 'overview-card-header';
      const nameSpan = document.createElement('span');
      nameSpan.textContent = file;
      const link = document.createElement('a');
      link.className = 'card-link';
      link.href = '#';
      link.textContent = 'Open';
      link.addEventListener('click', (e) => {
        e.preventDefault();
        activeSetIndex = setIdx;
        panelOrder = [0, 1, 2, 3];
        renderAll();
        $workspace.scrollIntoView({ behavior: 'smooth' });
      });
      header.append(nameSpan, link);
      card.append(header);

      const body = document.createElement('div');
      body.className = 'overview-card-body';

      const assignments = levelAssignments[setIdx] || NSFW_LEVELS.map(l => l.key);

      for (let p = 0; p < PANELS_PER_SET; p++) {
        const panelDiv = document.createElement('div');
        panelDiv.className = 'overview-panel';

        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        canvas.style.width = '100%';
        canvas.style.height = '100%';
        panelDiv.append(canvas);

        const levelKey = assignments[p];
        const level = NSFW_LEVELS.find(l => l.key === levelKey) || NSFW_LEVELS[p];
        const badge = document.createElement('span');
        badge.className = 'overview-panel-badge';
        badge.textContent = level.label.charAt(0);
        panelDiv.append(badge);

        body.append(panelDiv);

        // Draw
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.src = EXPRESSIONS_DIR + file;
        img.onload = () => {
          const ctx = canvas.getContext('2d');
          const crop = getPanelCropForSet(setIdx, p);
          const sx = (crop.x / 100) * img.naturalWidth;
          const sy = (crop.y / 100) * img.naturalHeight;
          const sw = (crop.w / 100) * img.naturalWidth;
          const sh = (crop.h / 100) * img.naturalHeight;
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 64, 64);
        };
      }

      card.append(body);
      $overviewGrid.append(card);
    });
  }

  function renderCropSection() {
    const file = imageFiles[activeSetIndex];
    if (!file) return;

    $cropPanelSelector.innerHTML = '';

    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = EXPRESSIONS_DIR + file;
    img.onload = () => {
      for (let q = 0; q < 4; q++) {
        const thumb = document.createElement('div');
        thumb.className = 'crop-panel-thumb' + (q === selectedCropPanel ? ' selected' : '');
        thumb.dataset.quadrant = q;

        const canvas = document.createElement('canvas');
        canvas.width = 72;
        canvas.height = 72;
        const ctx = canvas.getContext('2d');
        const crop = getPanelCrop(q);
        const sx = (crop.x / 100) * img.naturalWidth;
        const sy = (crop.y / 100) * img.naturalHeight;
        const sw = (crop.w / 100) * img.naturalWidth;
        const sh = (crop.h / 100) * img.naturalHeight;
        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, 72, 72);
        thumb.append(canvas);

        const label = document.createElement('span');
        label.className = 'crop-thumb-label';
        label.textContent = q + 1;
        thumb.append(label);

        thumb.addEventListener('click', () => {
          selectedCropPanel = q;
          renderCropSection();
        });

        $cropPanelSelector.append(thumb);
      }

      // Update slider values
      const insets = getCropInsets(activeSetIndex, selectedCropPanel);
      $cropTop.value = insets.top;
      $cropBottom.value = insets.bottom;
      $cropLeft.value = insets.left;
      $cropRight.value = insets.right;
      $cropTopVal.textContent = insets.top + '%';
      $cropBottomVal.textContent = insets.bottom + '%';
      $cropLeftVal.textContent = insets.left + '%';
      $cropRightVal.textContent = insets.right + '%';

      // Draw preview
      renderCropPreview(img);
    };
  }

  function renderCropPreview(img) {
    if (!img) {
      const file = imageFiles[activeSetIndex];
      if (!file) return;
      img = new Image();
      img.crossOrigin = 'anonymous';
      img.src = EXPRESSIONS_DIR + file;
      img.onload = () => drawCropPreview(img);
    } else {
      drawCropPreview(img);
    }
  }

  function drawCropPreview(img) {
    const ctx = $cropPreviewCanvas.getContext('2d');
    const size = 200;
    ctx.clearRect(0, 0, size, size);

    const crop = getPanelCrop(selectedCropPanel);
    const sx = (crop.x / 100) * img.naturalWidth;
    const sy = (crop.y / 100) * img.naturalHeight;
    const sw = (crop.w / 100) * img.naturalWidth;
    const sh = (crop.h / 100) * img.naturalHeight;

    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, size, size);
  }

  function renderDownsampleComparison() {
    const file = imageFiles[activeSetIndex];
    if (!file) return;

    const comparePanelIdx = parseInt($comparePanel.value);
    $downsampleGrid.innerHTML = '';

    const methods = [
      { key: 'auto',         label: 'Auto (browser default)', cssClass: '' },
      { key: 'pixelated',    label: 'Pixelated',              cssClass: 'pixelated' },
      { key: 'crisp-edges',  label: 'Crisp edges',            cssClass: 'crisp-edges' },
      { key: 'lanczos2',     label: 'Lanczos 2-pass',         cssClass: '' },
      { key: 'lanczos3',     label: 'Lanczos 3-pass',         cssClass: '' },
      { key: 'area-avg',     label: 'Area average',           cssClass: '' },
    ];

    const sizes = [16, 24, 32, 48, 64];

    methods.forEach(({ key, label, cssClass }) => {
      const card = document.createElement('div');
      card.className = 'downsample-card';

      const cardLabel = document.createElement('div');
      cardLabel.className = 'downsample-card-label';
      cardLabel.textContent = label;
      card.append(cardLabel);

      const sizesRow = document.createElement('div');
      sizesRow.className = 'downsample-sizes';

      sizes.forEach(s => {
        const sizeDiv = document.createElement('div');
        sizeDiv.className = 'downsample-size';

        const canvas = document.createElement('canvas');
        canvas.width = s;
        canvas.height = s;
        if (cssClass) canvas.classList.add(cssClass);
        // Scale up for display but keep actual pixels small
        canvas.style.width = (s * 2) + 'px';
        canvas.style.height = (s * 2) + 'px';
        sizeDiv.append(canvas);

        const sizeLabel = document.createElement('span');
        sizeLabel.className = 'downsample-size-label';
        sizeLabel.textContent = s + 'px';
        sizeDiv.append(sizeLabel);

        sizesRow.append(sizeDiv);

        // Draw the panel
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.src = EXPRESSIONS_DIR + file;
        img.onload = () => {
          const ctx = canvas.getContext('2d');
          const crop = getPanelCropForSet(activeSetIndex, comparePanelIdx);
          const sx = (crop.x / 100) * img.naturalWidth;
          const sy = (crop.y / 100) * img.naturalHeight;
          const sw = (crop.w / 100) * img.naturalWidth;
          const sh = (crop.h / 100) * img.naturalHeight;

          if (key === 'pixelated') {
            ctx.imageSmoothingEnabled = false;
          } else if (key === 'crisp-edges') {
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'low';
          } else if (key === 'lanczos2' || key === 'lanczos3') {
            // Multi-step
            const steps = key === 'lanczos3' ? 3 : 2;
            let tmp = document.createElement('canvas');
            tmp.width = Math.round(sw);
            tmp.height = Math.round(sh);
            let tctx = tmp.getContext('2d');
            tctx.imageSmoothingEnabled = true;
            tctx.imageSmoothingQuality = 'high';
            tctx.drawImage(img, sx, sy, sw, sh, 0, 0, tmp.width, tmp.height);
            for (let i = 0; i < steps; i++) {
              const hw = Math.max(1, Math.round(tmp.width / 2));
              const hh = Math.max(1, Math.round(tmp.height / 2));
              const next = document.createElement('canvas');
              next.width = hw;
              next.height = hh;
              const nctx = next.getContext('2d');
              nctx.imageSmoothingEnabled = true;
              nctx.imageSmoothingQuality = 'high';
              nctx.drawImage(tmp, 0, 0, hw, hh);
              tmp = next;
            }
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(tmp, 0, 0, s, s);
            return;
          } else if (key === 'area-avg') {
            const midSize = Math.max(s * 2, 128);
            const mid = document.createElement('canvas');
            mid.width = midSize;
            mid.height = midSize;
            const mctx = mid.getContext('2d');
            mctx.imageSmoothingEnabled = true;
            mctx.imageSmoothingQuality = 'medium';
            mctx.drawImage(img, sx, sy, sw, sh, 0, 0, midSize, midSize);
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(mid, 0, 0, s, s);
            return;
          } else {
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
          }
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, s, s);
        };
      });

      card.append(sizesRow);
      $downsampleGrid.append(card);
    });
  }

  function renderAll() {
    renderSetSelector();
    renderCropSection();
    renderWorkspace();
    renderLevelAssignment();
    renderWidgetPreview();
    renderDownsampleComparison();
    renderOverview();
  }

  // ── Drag & Drop ──
  let dragSourcePos = null;

  function onDragStart(e) {
    dragSourcePos = parseInt(e.currentTarget.dataset.displayPos);
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  }

  function onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.currentTarget.classList.add('drag-over');
  }

  function onDragLeave(e) {
    e.currentTarget.classList.remove('drag-over');
  }

  function onDrop(e) {
    e.preventDefault();
    const targetPos = parseInt(e.currentTarget.dataset.displayPos);
    e.currentTarget.classList.remove('drag-over');

    if (dragSourcePos !== null && dragSourcePos !== targetPos) {
      // Swap
      const tmp = panelOrder[dragSourcePos];
      panelOrder[dragSourcePos] = panelOrder[targetPos];
      panelOrder[targetPos] = tmp;

      // Also swap level assignments
      if (!levelAssignments[activeSetIndex]) {
        levelAssignments[activeSetIndex] = NSFW_LEVELS.map(l => l.key);
      }
      const tmpLvl = levelAssignments[activeSetIndex][dragSourcePos];
      levelAssignments[activeSetIndex][dragSourcePos] = levelAssignments[activeSetIndex][targetPos];
      levelAssignments[activeSetIndex][targetPos] = tmpLvl;

      renderWorkspace();
      renderLevelAssignment();
      renderWidgetPreview();
      renderOverview();
    }
  }

  function onDragEnd(e) {
    e.currentTarget.classList.remove('dragging');
    document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    dragSourcePos = null;
  }

  // ── Export ──
  $btnExport.addEventListener('click', () => {
    const mapping = {};
    imageFiles.forEach((file, idx) => {
      const assignments = levelAssignments[idx] || NSFW_LEVELS.map(l => l.key);
      const panelCrops = {};
      for (let q = 0; q < 4; q++) {
        const insets = getCropInsets(idx, q);
        if (insets.top || insets.bottom || insets.left || insets.right) {
          panelCrops[q] = insets;
        }
      }
      mapping[file] = {
        panels: panelOrder.map((quad, pos) => ({
          quadrant: quad,
          position: pos,
          level: assignments[pos],
        })),
        ...(Object.keys(panelCrops).length > 0 ? { cropInsets: panelCrops } : {}),
      };
    });
    const json = JSON.stringify(mapping, null, 2);

    navigator.clipboard.writeText(json).then(() => {
      $exportStatus.textContent = '✓ Copied!';
      $exportStatus.classList.add('visible');
      setTimeout(() => $exportStatus.classList.remove('visible'), 2000);
    }).catch(() => {
      // Fallback: open in new window
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank');
    });
  });

  // ── Size slider ──
  $sizeSlider.addEventListener('input', () => {
    panelSize = parseInt($sizeSlider.value);
    $sizeLabel.textContent = panelSize + ' px';
    renderWorkspace();
  });

  // ── View toggle ──
  $btnGrid.addEventListener('click', () => {
    $btnGrid.classList.add('active');
    $btnStrip.classList.remove('active');
    $panelGrid.classList.remove('strip-view');
  });
  $btnStrip.addEventListener('click', () => {
    $btnStrip.classList.add('active');
    $btnGrid.classList.remove('active');
    $panelGrid.classList.add('strip-view');
  });

  // ── Crop slider events ──
  function updateCropFromSliders() {
    const insets = {
      top:    parseFloat($cropTop.value),
      bottom: parseFloat($cropBottom.value),
      left:   parseFloat($cropLeft.value),
      right:  parseFloat($cropRight.value),
    };
    setCropInsets(activeSetIndex, selectedCropPanel, insets);
    $cropTopVal.textContent = insets.top + '%';
    $cropBottomVal.textContent = insets.bottom + '%';
    $cropLeftVal.textContent = insets.left + '%';
    $cropRightVal.textContent = insets.right + '%';
    // Re-render everything that uses crops
    renderCropSection();
    renderWorkspace();
    renderLevelAssignment();
    renderWidgetPreview();
    renderDownsampleComparison();
    renderOverview();
  }

  [$cropTop, $cropBottom, $cropLeft, $cropRight].forEach(slider => {
    slider.addEventListener('input', updateCropFromSliders);
  });

  $btnCropReset.addEventListener('click', () => {
    setCropInsets(activeSetIndex, selectedCropPanel, { top: 0, bottom: 0, left: 0, right: 0 });
    updateCropFromSliders();
  });

  $btnCropResetAll.addEventListener('click', () => {
    for (let q = 0; q < 4; q++) {
      setCropInsets(activeSetIndex, q, { top: 0, bottom: 0, left: 0, right: 0 });
    }
    updateCropFromSliders();
  });

  // ── Downsampling events ──
  $downsampleMethod.addEventListener('change', () => {
    downsampleMethod = $downsampleMethod.value;
    renderWorkspace();
    renderWidgetPreview();
  });

  $comparePanel.addEventListener('change', () => {
    renderDownsampleComparison();
  });

  // ── Init ──
  async function init() {
    imageFiles = await discoverImages();
    // Initialize default level assignments
    imageFiles.forEach((_, idx) => {
      if (!levelAssignments[idx]) {
        levelAssignments[idx] = NSFW_LEVELS.map(l => l.key);
      }
    });
    renderAll();
  }

  init();
})();
