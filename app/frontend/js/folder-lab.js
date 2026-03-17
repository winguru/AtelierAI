(() => {
  /* ─────────────────────────────────────────────────────────────────────────
     folder-lab.js — Folder Tab Lab prototype controller
     Renders preset gallery + powers the interactive builder.
     Depends on: window.AtelierUi.createStackedFolderWorkspace (ui-kit.js)
     Uses: window.AtelierFolderTabs (shared/folder-tabs.js)
     ───────────────────────────────────────────────────────────────────────── */

  /* ── Helpers ─────────────────────────────────────────────────────────────── */

  /** Build sample placeholder content for a tab panel. */
  function makePlaceholder(label) {
    const el = document.createElement('p');
    el.className = 'ftab-placeholder';
    el.textContent = `Content area for "${label}"`;
    return el;
  }

  /**
   * Build a tab descriptor array suitable for createStackedFolderWorkspace.
   * @param {string[]} backLabels   Labels for back-row (row 1) tabs.
   * @param {string[]} frontLabels  Labels for front-row (row 2) tabs.
   * @param {number}   gridCols     Number of grid columns.
   */
  function buildTabDescriptors(
    backLabels,
    frontLabels,
    gridCols,
    idPrefix = 'tab',
    { backRow = 1, frontRow = 2 } = {},
  ) {
    const folderTabs = window.AtelierFolderTabs;
    if (!folderTabs || typeof folderTabs.buildTwoRowTabs !== 'function') {
      return [];
    }

    return folderTabs.buildTwoRowTabs({
      backLabels,
      frontLabels,
      gridCols,
      idPrefix,
      backRow,
      frontRow,
      createRender: ({ label }) => makePlaceholder(label),
    });
  }

  /* ── Preset definitions ──────────────────────────────────────────────────── */

  const PRESETS = [
    {
      label: 'Manila · 4 + 4 tabs',
      theme: '',
      themeLabel: 'manila',
      backLabels: ['Attributes', 'Generation', 'Collections', 'CivitAI Tags'],
      frontLabels: ['Danbooru', 'Prompt Tags', 'User Tags', 'Utilities'],
      gridCols: 13,
    },
    {
      label: 'Slate · 4 + 4 tabs',
      theme: 'slate',
      themeLabel: 'slate',
      backLabels: ['Overview', 'History', 'Settings', 'Details'],
      frontLabels: ['Output', 'Logs', 'Debug', 'Export'],
      gridCols: 13,
    },
    {
      label: 'Forest · 3 + 3 tabs',
      theme: 'forest',
      themeLabel: 'forest',
      backLabels: ['Plants', 'Animals', 'Landscape'],
      frontLabels: ['Light', 'Season', 'Weather'],
      gridCols: 12,
    },
    {
      label: 'Terracotta · 3 + 4 tabs',
      theme: 'terracotta',
      themeLabel: 'terracotta',
      backLabels: ['Material', 'Texture', 'Finish'],
      frontLabels: ['Palette', 'Glaze', 'Fire', 'Form'],
      gridCols: 13,
    },
    {
      label: 'Iris · 5 + 5 tabs',
      theme: 'iris',
      themeLabel: 'iris',
      backLabels: ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'],
      frontLabels: ['One', 'Two', 'Three', 'Four', 'Five'],
      gridCols: 15,
    },
    {
      label: 'Manila · back row only',
      theme: '',
      themeLabel: 'manila',
      backLabels: ['Summary', 'Data', 'Notes'],
      frontLabels: [],
      gridCols: 12,
    },
  ];

  function initThemeOptions() {
    const themeSelect = document.getElementById('builder-theme');
    const folderTabs = window.AtelierFolderTabs;
    if (!(themeSelect instanceof HTMLSelectElement)) return;
    if (!folderTabs || typeof folderTabs.createThemeCatalog !== 'function') return;

    const themes = folderTabs.createThemeCatalog();
    if (!Array.isArray(themes) || themes.length === 0) return;

    const selected = themeSelect.value;
    themeSelect.innerHTML = '';
    themes.forEach(({ value, label }) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value ? String(label || value) : 'Manila (default)';
      themeSelect.append(option);
    });
    themeSelect.value = selected;
  }

  /* ── Render a preset card ─────────────────────────────────────── */

  function renderPresetCard(preset) {
    const card = document.createElement('article');
    card.className = 'preset-card';

    const header = document.createElement('div');
    header.className = 'preset-card-header';

    const cardLabel = document.createElement('span');
    cardLabel.className = 'preset-card-label';
    cardLabel.textContent = preset.label;

    const pill = document.createElement('span');
    pill.className = 'theme-pill';
    pill.textContent = preset.themeLabel;

    header.append(cardLabel, pill);

    const body = document.createElement('div');
    body.className = 'preset-card-body';

    const tabs = buildTabDescriptors(
      preset.backLabels,
      preset.frontLabels,
      preset.gridCols,
      `preset-${preset.themeLabel || 'manila'}`,
    );
    const ws = window.AtelierUi.createStackedFolderWorkspace({
      tabs,
      gridCols: preset.gridCols,
      activeTabId: tabs[0]?.id,
      ariaLabel: preset.label,
      colorTheme: preset.theme || undefined,
    });

    if (ws) {
      body.append(ws.root);
    }

    card.append(header, body);
    return card;
  }

  /* ── Populate preset gallery ───────────────────────────────────── */

  function initGallery() {
    const gallery = document.getElementById('preset-gallery');
    if (!gallery) return;
    PRESETS.forEach((preset) => {
      gallery.append(renderPresetCard(preset));
    });
  }

  /* ── Interactive builder ───────────────────────────────────────── */

  let builderInstance = null;
  let builderActiveTabId = null;

  function buildInteractive(opts) {
    const preview = document.getElementById('builder-preview');
    if (!preview) return;

    const {
      theme = '',
      backCount = 4,
      frontCount = 0,
      gridCols = 13,
      tabOffset = 20,
      tabGap = 5,
      repositionTabs = false,
      resetActiveTab = false,
    } = opts;

    const backLabels = Array.from({ length: backCount }, (_, i) => `Back ${i + 1}`);
    const frontLabels = Array.from({ length: frontCount }, (_, i) => `Front ${i + 1}`);

    const tabs = buildTabDescriptors(backLabels, frontLabels, gridCols, 'builder', {
      backRow: repositionTabs ? 2 : 1,
      frontRow: repositionTabs ? 1 : 2,
    });
    if (builderInstance) {
      preview.innerHTML = '';
      builderInstance = null;
    }

    if (tabs.length === 0) {
      builderActiveTabId = null;
      builderInstance = window.AtelierUi.createStackedFolderWorkspace({
        tabs: [],
        gridCols,
        ariaLabel: 'Interactive builder preview',
        colorTheme: theme || undefined,
        cssVars: {
          '--tab-offset': `${tabOffset}px`,
          '--tab-gap': `${tabGap}px`,
        },
      });
      if (builderInstance) {
        preview.append(builderInstance.root);
      }
      return;
    }

    // Pick active tab: on count change reset to first bottom-row tab,
    // otherwise preserve current selection.
    let chosenActiveTabId = builderActiveTabId;
    const tabIds = new Set(tabs.map((t) => t.id));
    if (resetActiveTab || !chosenActiveTabId || !tabIds.has(chosenActiveTabId)) {
      // First bottom-row (row 2) tab, else first tab overall
      const bottomTab = tabs.find((t) => t.row === 2) || tabs[0];
      chosenActiveTabId = bottomTab.id;
    }

    builderInstance = window.AtelierUi.createStackedFolderWorkspace({
      tabs,
      gridCols,
      activeTabId: chosenActiveTabId,
      ariaLabel: 'Interactive builder preview',
      colorTheme: theme || undefined,
      cssVars: {
        '--tab-offset': `${tabOffset}px`,
        '--tab-gap': `${tabGap}px`,
      },
      onTabChange: (tabId) => {
        builderActiveTabId = tabId;
      },
    });
    builderActiveTabId = chosenActiveTabId;

    if (builderInstance) {
      preview.append(builderInstance.root);
    }
  }

  function initBuilder() {
    const form = document.getElementById('builder-form');
    const rebuildBtn = document.getElementById('rebuild-btn');
    if (!form || !rebuildBtn) return;

    const backCountInput = form.elements['backCount'];
    const frontCountInput = form.elements['frontCount'];
    const gridColsInput = form.elements['gridCols'];
    const tabOffsetInput = form.elements['tabOffset'];
    const tabGapInput = form.elements['tabGap'];
    const repositionTabsInput = form.elements['repositionTabs'];
    const backCountVal = document.getElementById('back-count-val');
    const frontCountVal = document.getElementById('front-count-val');
    const gridColsVal = document.getElementById('grid-cols-val');
    const tabOffsetVal = document.getElementById('tab-offset-val');
    const tabGapVal = document.getElementById('tab-gap-val');

    function syncLabels() {
      if (backCountVal) backCountVal.textContent = backCountInput.value;
      if (frontCountVal) frontCountVal.textContent = frontCountInput.value;
      if (gridColsVal) gridColsVal.textContent = gridColsInput.value;
      if (tabOffsetVal) tabOffsetVal.textContent = tabOffsetInput.value;
      if (tabGapVal) tabGapVal.textContent = tabGapInput.value;
    }

    function rebuild({ resetActiveTab = false } = {}) {
      syncLabels();
      buildInteractive({
        theme: form.elements['theme'].value,
        backCount: Number(backCountInput.value),
        frontCount: Number(frontCountInput.value),
        gridCols: Number(gridColsInput.value),
        tabOffset: Number(tabOffsetInput.value),
        tabGap: Number(tabGapInput.value),
        repositionTabs: Boolean(repositionTabsInput.checked),
        resetActiveTab,
      });
    }

    function rebuildPreserve() { rebuild(); }
    function rebuildReset() { rebuild({ resetActiveTab: true }); }

    backCountInput.addEventListener('input', syncLabels);
    frontCountInput.addEventListener('input', syncLabels);
    gridColsInput.addEventListener('input', syncLabels);
    tabOffsetInput.addEventListener('input', syncLabels);
    tabGapInput.addEventListener('input', syncLabels);

    // Count changes reset active tab to first bottom-row tab.
    backCountInput.addEventListener('input', rebuildReset);
    frontCountInput.addEventListener('input', rebuildReset);
    // Other controls preserve active tab.
    gridColsInput.addEventListener('input', rebuildPreserve);
    tabOffsetInput.addEventListener('input', rebuildPreserve);
    tabGapInput.addEventListener('input', rebuildPreserve);
    repositionTabsInput.addEventListener('change', rebuildPreserve);

    rebuildBtn.addEventListener('click', rebuildPreserve);

    // Render initial state
    rebuild();
  }

  /* ── Dark mode ───────────────────────────────────────────────────── */

  function initThemeToggle() {
    const toggle = document.getElementById('theme-toggle');
    if (!toggle) return;
    const savedTheme = localStorage.getItem('atelier-theme');
    if (savedTheme === 'dark') {
      document.body.dataset.theme = 'dark';
      toggle.checked = true;
    }
    toggle.addEventListener('change', () => {
      const dark = toggle.checked;
      document.body.dataset.theme = dark ? 'dark' : '';
      localStorage.setItem('atelier-theme', dark ? 'dark' : '');
    });
  }

  /* ── Boot ─────────────────────────────────────────────────────────── */

  function init() {
    initThemeToggle();
    initThemeOptions();
    initGallery();
    initBuilder();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
