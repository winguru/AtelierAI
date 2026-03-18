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
   */
  function buildTabDescriptors(
    backLabels,
    frontLabels,
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
      columnBudget: 13,
      idPrefix,
      backRow,
      frontRow,
      createRender: ({ label }) => makePlaceholder(label),
    });
  }

  const MIXED_THEME_VALUES = ['', 'slate', 'forest', 'terracotta', 'iris'];

  function hashString(value) {
    let hash = 2166136261;
    for (let i = 0; i < value.length; i += 1) {
      hash ^= value.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function applyMixedThemes(tabs, salt = 'mixed-default') {
    if (!Array.isArray(tabs)) return tabs;
    return tabs.map((tab) => ({
      ...tab,
      colorTheme: MIXED_THEME_VALUES[
        hashString(`${salt}:${tab.id}:${tab.label}:${tab.row}`) % MIXED_THEME_VALUES.length
      ],
    }));
  }

  function resolveWorkspaceTheme(theme, { mixedSalt = 'mixed-default', scope = 'workspace' } = {}) {
    if (!theme) return undefined;
    if (theme !== 'mixed') return theme;
    return MIXED_THEME_VALUES[
      hashString(`${mixedSalt}:${scope}:empty`) % MIXED_THEME_VALUES.length
    ];
  }

  function pickDefaultActiveTabId(tabs) {
    if (!Array.isArray(tabs) || tabs.length === 0) return undefined;
    return (tabs.find((t) => Number(t.row) === 2) || tabs[0]).id;
  }

  function newMixedSalt() {
    return `mixed-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  }

  /* ── Preset definitions ──────────────────────────────────────────────────── */

  const PRESETS = [
    {
      label: 'Manila · 4 + 4 tabs',
      theme: '',
      themeLabel: 'manila',
      backLabels: ['Attributes', 'Generation', 'Collections', 'CivitAI Tags'],
      frontLabels: ['Danbooru', 'Prompt Tags', 'User Tags', 'Utilities'],
    },
    {
      label: 'Slate · 4 + 4 tabs',
      theme: 'slate',
      themeLabel: 'slate',
      backLabels: ['Overview', 'History', 'Settings', 'Details'],
      frontLabels: ['Output', 'Logs', 'Debug', 'Export'],
    },
    {
      label: 'Forest · 3 + 3 tabs',
      theme: 'forest',
      themeLabel: 'forest',
      backLabels: ['Plants', 'Animals', 'Landscape'],
      frontLabels: ['Light', 'Season', 'Weather'],
    },
    {
      label: 'Terracotta · 3 + 4 tabs',
      theme: 'terracotta',
      themeLabel: 'terracotta',
      backLabels: ['Material', 'Texture', 'Finish'],
      frontLabels: ['Palette', 'Glaze', 'Fire', 'Form'],
    },
    {
      label: 'Iris · 5 + 5 tabs',
      theme: 'iris',
      themeLabel: 'iris',
      backLabels: ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon'],
      frontLabels: ['One', 'Two', 'Three', 'Four', 'Five'],
    },
    {
      label: 'Mixed · 4 + 4 tabs',
      theme: 'mixed',
      themeLabel: 'mixed',
      backLabels: ['Blend A', 'Blend B', 'Blend C', 'Blend D'],
      frontLabels: ['Probe 1', 'Probe 2', 'Probe 3', 'Probe 4'],
    },
    {
      label: 'Manila · back row only',
      theme: '',
      themeLabel: 'manila',
      backLabels: ['Summary', 'Data', 'Notes'],
      frontLabels: [],
    },
  ];

  function initThemeOptions() {
    const themeSelect = document.getElementById('builder-theme');
    const folderTabs = window.AtelierFolderTabs;
    if (!(themeSelect instanceof HTMLSelectElement)) return;
    if (!folderTabs || typeof folderTabs.createThemeCatalog !== 'function') return;

    const themes = folderTabs.createThemeCatalog([{ value: 'mixed', label: 'mixed' }]);
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

    let tabs = buildTabDescriptors(
      preset.backLabels,
      preset.frontLabels,
      `preset-${preset.themeLabel || 'manila'}`,
    );
    if (preset.theme === 'mixed') {
      tabs = applyMixedThemes(tabs, `preset:${preset.label}`);
    }
    const ws = window.AtelierUi.createStackedFolderWorkspace({
      tabs,
      activeTabId: pickDefaultActiveTabId(tabs),
      ariaLabel: preset.label,
      colorTheme: preset.theme && preset.theme !== 'mixed' ? preset.theme : undefined,
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
  let builderMixedSalt = newMixedSalt();
  let builderSnapshot = {
    theme: '',
    backCount: 4,
    frontCount: 0,
    tabOffset: 20,
    tabGap: 5,
    activeLift: 2,
  };

  function buildInteractive(opts) {
    const preview = document.getElementById('builder-preview');
    if (!preview) return;

    const {
      theme = '',
      backCount = 4,
      frontCount = 0,
      tabOffset = 20,
      tabGap = 5,
      activeLift = 2,
      resetActiveTab = false,
    } = opts;

    builderSnapshot = {
      theme,
      backCount,
      frontCount,
      tabOffset,
      tabGap,
      activeLift,
    };

    const backLabels = Array.from({ length: backCount }, (_, i) => `Back ${i + 1}`);
    const frontLabels = Array.from({ length: frontCount }, (_, i) => `Front ${i + 1}`);

    let tabs = buildTabDescriptors(backLabels, frontLabels, 'builder');
    if (theme === 'mixed') {
      tabs = applyMixedThemes(tabs, builderMixedSalt);
    }
    if (builderInstance) {
      preview.innerHTML = '';
      builderInstance = null;
    }

    if (tabs.length === 0) {
      builderActiveTabId = null;
      builderInstance = window.AtelierUi.createStackedFolderWorkspace({
        tabs: [],
        ariaLabel: 'Interactive builder preview',
        colorTheme: resolveWorkspaceTheme(theme, {
          mixedSalt: builderMixedSalt,
          scope: 'builder-empty-state',
        }),
        cssVars: {
          '--tab-offset': `${tabOffset}px`,
          '--tab-gap': `${tabGap}px`,
          '--ftab-active-lift': `${activeLift}px`,
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
      activeTabId: chosenActiveTabId,
      ariaLabel: 'Interactive builder preview',
      colorTheme: theme && theme !== 'mixed' ? theme : undefined,
      cssVars: {
        '--tab-offset': `${tabOffset}px`,
        '--tab-gap': `${tabGap}px`,
        '--ftab-active-lift': `${activeLift}px`,
      },
      onTabChange: (tabId) => {
        builderActiveTabId = tabId;
      },
    });
    builderActiveTabId = chosenActiveTabId;

    if (builderInstance) {
      preview.append(builderInstance.root);
    }

    refreshEmbedSnippets();
  }

  function quoteLines(values) {
    return (values || []).map((v) => `'${String(v).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`).join(', ');
  }

  function sourceFromBuilder() {
    const backLabels = Array.from({ length: builderSnapshot.backCount || 0 }, (_, i) => `Back ${i + 1}`);
    const frontLabels = Array.from({ length: builderSnapshot.frontCount || 0 }, (_, i) => `Front ${i + 1}`);
    return {
      id: 'builder-current',
      label: 'Interactive builder (current)',
      mode: 'builder',
      config: {
        theme: builderSnapshot.theme || '',
        backLabels,
        frontLabels,
        tabOffset: Number(builderSnapshot.tabOffset || 0),
        tabGap: Number(builderSnapshot.tabGap || 0),
        activeLift: Number(builderSnapshot.activeLift || 0),
        mixedSalt: builderMixedSalt,
      },
    };
  }

  function sourceFromPreset(preset, index) {
    return {
      id: `preset-${index}`,
      label: `Preset: ${preset.label}`,
      mode: 'preset',
      config: {
        theme: preset.theme || '',
        backLabels: [...preset.backLabels],
        frontLabels: [...preset.frontLabels],
        tabOffset: 20,
        tabGap: 5,
        activeLift: 2,
        mixedSalt: `preset:${preset.label}`,
      },
    };
  }

  function getSelectedEmbedSource() {
    const select = document.getElementById('embed-source');
    if (!(select instanceof HTMLSelectElement)) return sourceFromBuilder();

    const selected = select.value || 'builder-current';
    if (selected === 'builder-current') return sourceFromBuilder();

    const m = selected.match(/^preset-(\d+)$/);
    if (!m) return sourceFromBuilder();
    const idx = Number(m[1]);
    const preset = PRESETS[idx];
    if (!preset) return sourceFromBuilder();
    return sourceFromPreset(preset, idx);
  }

  function buildEmbedJsSnippet(source) {
    const { config } = source;
    const themeValue = config.theme || '';
    const backArray = quoteLines(config.backLabels);
    const frontArray = quoteLines(config.frontLabels);
    const hasTabs = (config.backLabels.length + config.frontLabels.length) > 0;

    const lines = [];
    lines.push('const mount = document.getElementById(\'folder-tab-mount\');');
    lines.push('if (!mount) throw new Error(\'Missing #folder-tab-mount\');');
    lines.push('');
    lines.push(`const backLabels = [${backArray}];`);
    lines.push(`const frontLabels = [${frontArray}];`);
    lines.push(`const selectedTheme = '${themeValue}'; // '', slate, forest, terracotta, iris, mixed`);
    lines.push('');
    lines.push('let tabs = window.AtelierFolderTabs.buildTwoRowTabs({');
    lines.push('  backLabels,');
    lines.push('  frontLabels,');
    lines.push(`  idPrefix: '${source.id}',`);
    lines.push('  backRow: 1,');
    lines.push('  frontRow: 2,');
    lines.push('  createRender: ({ label }) => {');
    lines.push('    const el = document.createElement(\'p\');');
    lines.push('    el.className = \'ftab-placeholder\';');
    lines.push('    el.textContent = `Content area for "${label}"`;');
    lines.push('    return el;');
    lines.push('  },');
    lines.push('});');
    lines.push('');
    lines.push('if (selectedTheme === \'mixed\') {');
    lines.push(`  const mixedSalt = '${String(config.mixedSalt || 'mixed-default').replace(/\\/g, '\\\\').replace(/'/g, "\\'")}';`);
    lines.push("  const mixedThemes = ['', 'slate', 'forest', 'terracotta', 'iris'];");
    lines.push('  const hash = (value) => {');
    lines.push('    let h = 2166136261;');
    lines.push('    for (let i = 0; i < value.length; i += 1) {');
    lines.push('      h ^= value.charCodeAt(i);');
    lines.push('      h = Math.imul(h, 16777619);');
    lines.push('    }');
    lines.push('    return h >>> 0;');
    lines.push('  };');
    lines.push('  tabs = tabs.map((tab) => ({');
    lines.push('    ...tab,');
    lines.push('    colorTheme: mixedThemes[hash(`${mixedSalt}:${tab.id}:${tab.label}:${tab.row}`) % mixedThemes.length],');
    lines.push('  }));');
    lines.push('}');
    lines.push('');
    lines.push('const activeTabId = (tabs.find((t) => Number(t.row) === 2) || tabs[0])?.id;');
    lines.push('const workspaceTheme = (() => {');
    lines.push('  if (tabs.length > 0) return selectedTheme && selectedTheme !== \'mixed\' ? selectedTheme : undefined;');
    lines.push('  if (selectedTheme !== \'mixed\') return selectedTheme || undefined;');
    lines.push(`  const emptyScope = 'embed-empty-state';`);
    lines.push("  const mixedThemes = ['', 'slate', 'forest', 'terracotta', 'iris'];");
    lines.push('  let h = 2166136261;');
    lines.push('  const raw = `${selectedTheme}:${emptyScope}`;');
    lines.push('  for (let i = 0; i < raw.length; i += 1) {');
    lines.push('    h ^= raw.charCodeAt(i);');
    lines.push('    h = Math.imul(h, 16777619);');
    lines.push('  }');
    lines.push('  return mixedThemes[(h >>> 0) % mixedThemes.length] || undefined;');
    lines.push('})();');
    lines.push('');
    lines.push('const workspace = window.AtelierUi.createStackedFolderWorkspace({');
    lines.push('  tabs,');
    lines.push('  activeTabId,');
    lines.push(`  ariaLabel: '${String(source.label).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}',`);
    lines.push('  colorTheme: workspaceTheme,');
    lines.push('  emptyStateLabel: \'Default\',');
    if (source.mode === 'builder') {
      lines.push('  cssVars: {');
      lines.push(`    '--tab-offset': '${config.tabOffset}px',`);
      lines.push(`    '--tab-gap': '${config.tabGap}px',`);
      lines.push(`    '--ftab-active-lift': '${config.activeLift}px',`);
      lines.push('  },');
    }
    lines.push('});');
    lines.push('');
    lines.push('mount.innerHTML = \'\';');
    lines.push('if (workspace) mount.append(workspace.root);');

    return lines.join('\n');
  }

  function refreshEmbedSnippets() {
    const assetsCode = document.getElementById('embed-assets-code');
    const htmlCode = document.getElementById('embed-html-code');
    const jsCode = document.getElementById('embed-js-code');
    if (!(assetsCode && htmlCode && jsCode)) return;

    const source = getSelectedEmbedSource();
    assetsCode.textContent = [
      '<link rel="stylesheet" href="/frontend/css/folder-tabs.css">',
      '<script src="/frontend/js/shared/ui-kit.js" defer></script>',
      '<script src="/frontend/js/shared/folder-tabs.js" defer></script>',
    ].join('\n');

    htmlCode.textContent = '<div id="folder-tab-mount"></div>';
    jsCode.textContent = buildEmbedJsSnippet(source);
  }

  function initEmbedSection() {
    const select = document.getElementById('embed-source');
    if (!(select instanceof HTMLSelectElement)) return;

    const options = [sourceFromBuilder(), ...PRESETS.map((preset, index) => sourceFromPreset(preset, index))];
    select.innerHTML = '';
    options.forEach((entry) => {
      const option = document.createElement('option');
      option.value = entry.id;
      option.textContent = entry.label;
      select.append(option);
    });
    select.value = 'builder-current';
    select.addEventListener('change', refreshEmbedSnippets);
    refreshEmbedSnippets();
  }

  function initBuilder() {
    const form = document.getElementById('builder-form');
    const rebuildBtn = document.getElementById('rebuild-btn');
    const reshuffleMixedBtn = document.getElementById('reshuffle-mixed-btn');
    if (!form || !rebuildBtn) return;

    const backCountInput = form.elements['backCount'];
    const frontCountInput = form.elements['frontCount'];
    const tabOffsetInput = form.elements['tabOffset'];
    const tabGapInput = form.elements['tabGap'];
    const activeLiftInput = form.elements['activeLift'];
    const themeInput = form.elements['theme'];
    const backCountVal = document.getElementById('back-count-val');
    const frontCountVal = document.getElementById('front-count-val');
    const tabOffsetVal = document.getElementById('tab-offset-val');
    const tabGapVal = document.getElementById('tab-gap-val');
    const activeLiftVal = document.getElementById('active-lift-val');

    function syncLabels() {
      if (backCountVal) backCountVal.textContent = backCountInput.value;
      if (frontCountVal) frontCountVal.textContent = frontCountInput.value;
      if (tabOffsetVal) tabOffsetVal.textContent = tabOffsetInput.value;
      if (tabGapVal) tabGapVal.textContent = tabGapInput.value;
      if (activeLiftVal) activeLiftVal.textContent = activeLiftInput.value;
    }

    function rebuild({ resetActiveTab = false } = {}) {
      syncLabels();
      buildInteractive({
        theme: form.elements['theme'].value,
        backCount: Number(backCountInput.value),
        frontCount: Number(frontCountInput.value),
        tabOffset: Number(tabOffsetInput.value),
        tabGap: Number(tabGapInput.value),
        activeLift: Number(activeLiftInput.value),
        resetActiveTab,
      });
      if (reshuffleMixedBtn) {
        reshuffleMixedBtn.disabled = themeInput.value !== 'mixed';
      }
    }

    function rebuildPreserve() { rebuild(); }
    function rebuildReset() { rebuild({ resetActiveTab: true }); }

    backCountInput.addEventListener('input', syncLabels);
    frontCountInput.addEventListener('input', syncLabels);
    tabOffsetInput.addEventListener('input', syncLabels);
    tabGapInput.addEventListener('input', syncLabels);
    activeLiftInput.addEventListener('input', syncLabels);

    // Count changes reset active tab to first bottom-row tab.
    backCountInput.addEventListener('input', rebuildReset);
    frontCountInput.addEventListener('input', rebuildReset);
    // Other controls preserve active tab.
    tabOffsetInput.addEventListener('input', rebuildPreserve);
    tabGapInput.addEventListener('input', rebuildPreserve);
    activeLiftInput.addEventListener('input', rebuildPreserve);
    if (themeInput) {
      themeInput.addEventListener('change', rebuildPreserve);
    }

    if (reshuffleMixedBtn) {
      reshuffleMixedBtn.addEventListener('click', () => {
        if (themeInput.value !== 'mixed') return;
        builderMixedSalt = newMixedSalt();
        rebuildPreserve();
      });
    }

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
    initEmbedSection();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
