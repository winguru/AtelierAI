(() => {
  /*
    shared/folder-tabs.js
    Lightweight helpers for building stacked folder-tab descriptors.
  */

  function toInt(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.trunc(n) : fallback;
  }

  function clampInt(value, min, max) {
    return Math.max(min, Math.min(max, toInt(value, min)));
  }

  const THEME_TOKENS = {
    manila: {
      '--ftab-edge': '#d4c1a8',
      '--ftab-back-top': '#d7cbc0',
      '--ftab-back-bottom': '#cab9a9',
      '--ftab-back-body': '#ac9c88',
      '--ftab-front-top': '#dfd4c8',
      '--ftab-front-bottom': '#d5cab8',
      '--ftab-front-body': '#eadcc3',
      '--ftab-active-top': '#f7efdf',
      '--ftab-active-bottom': '#eee1c9',
      '--ftab-active-border': '#cdb79b',
    },
    slate: {
      '--ftab-edge': '#94afc7',
      '--ftab-back-top': '#afc3d8',
      '--ftab-back-bottom': '#9cb5cc',
      '--ftab-back-body': '#7896b2',
      '--ftab-front-top': '#c2d4e6',
      '--ftab-front-bottom': '#b4c8dc',
      '--ftab-front-body': '#d0e1ef',
      '--ftab-active-top': '#e6f0f9',
      '--ftab-active-bottom': '#daeaf6',
      '--ftab-active-border': '#8dafc9',
    },
    forest: {
      '--ftab-edge': '#91b89a',
      '--ftab-back-top': '#a8c8ae',
      '--ftab-back-bottom': '#96b99e',
      '--ftab-back-body': '#6e9878',
      '--ftab-front-top': '#bcd5c3',
      '--ftab-front-bottom': '#adc8b5',
      '--ftab-front-body': '#cfe2d4',
      '--ftab-active-top': '#e2f0e6',
      '--ftab-active-bottom': '#d5eadb',
      '--ftab-active-border': '#88b593',
    },
    terracotta: {
      '--ftab-edge': '#c4906e',
      '--ftab-back-top': '#d4a588',
      '--ftab-back-bottom': '#c49278',
      '--ftab-back-body': '#a86e58',
      '--ftab-front-top': '#e0b89e',
      '--ftab-front-bottom': '#d4a88e',
      '--ftab-front-body': '#edc9b0',
      '--ftab-active-top': '#fae4d4',
      '--ftab-active-bottom': '#f0d5be',
      '--ftab-active-border': '#c0836a',
    },
    iris: {
      '--ftab-edge': '#ab9ec8',
      '--ftab-back-top': '#c0b4d9',
      '--ftab-back-bottom': '#b0a4cc',
      '--ftab-back-body': '#8c7eb0',
      '--ftab-front-top': '#d0c8e4',
      '--ftab-front-bottom': '#c4bada',
      '--ftab-front-body': '#ddd6ee',
      '--ftab-active-top': '#ede8f6',
      '--ftab-active-bottom': '#e5dff2',
      '--ftab-active-border': '#a494c4',
    },
  };

  function normalizeThemeKey(theme) {
    if (theme === 'manila' || theme === '' || theme === null || theme === undefined) {
      return 'manila';
    }
    return Object.prototype.hasOwnProperty.call(THEME_TOKENS, theme) ? theme : 'manila';
  }

  function getThemeTokens(theme) {
    return THEME_TOKENS[normalizeThemeKey(theme)];
  }

  /**
   * Evenly distribute `count` slots over `columnBudget` columns.
   * Returns [{ colStart, colSpan }] left-to-right.
   */
  function distributeSlots(count, columnBudget) {
    const safeCount = clampInt(count, 0, 1000);
    const safeColumnBudget = clampInt(columnBudget, 1, 1000);
    if (safeCount <= 0) return [];

    const spanBase = Math.floor(safeColumnBudget / safeCount);
    const remainder = safeColumnBudget % safeCount;
    const positions = [];
    let colStart = 1;

    for (let i = 0; i < safeCount; i += 1) {
      const colSpan = spanBase + (i < remainder ? 1 : 0);
      positions.push({ colStart, colSpan: Math.max(1, colSpan) });
      colStart += Math.max(1, colSpan);
    }

    return positions;
  }

  /**
   * Build descriptors for a two-row folder-tab layout.
   *
   * createRender receives: { label, row, index, id, lane } and must return a Node.
   */
  function buildTwoRowTabs({
    backLabels = [],
    frontLabels = [],
    columnBudget = 13,
    idPrefix = 'tab',
    backRow = 1,
    frontRow = 2,
    createRender,
  }) {
    const tabs = [];
    const backPositions = distributeSlots(backLabels.length, columnBudget);
    const frontPositions = distributeSlots(frontLabels.length, columnBudget);

    const renderFactory = typeof createRender === 'function'
      ? createRender
      : ({ label }) => {
          const fallback = document.createElement('span');
          fallback.textContent = label;
          return fallback;
        };

    backLabels.forEach((label, index) => {
      const id = `${idPrefix}-back-${index}`;
      const pos = backPositions[index] || { colStart: 1, colSpan: 1 };
      tabs.push({
        id,
        label,
        row: backRow,
        colStart: pos.colStart,
        colSpan: pos.colSpan,
        render: () => renderFactory({ label, row: backRow, index, id, lane: 'back' }),
      });
    });

    frontLabels.forEach((label, index) => {
      const id = `${idPrefix}-front-${index}`;
      const pos = frontPositions[index] || { colStart: 1, colSpan: 1 };
      tabs.push({
        id,
        label,
        row: frontRow,
        colStart: pos.colStart,
        colSpan: pos.colSpan,
        render: () => renderFactory({ label, row: frontRow, index, id, lane: 'front' }),
      });
    });

    return tabs;
  }

  function createThemeCatalog(extraThemes = []) {
    const baseThemes = [
      { value: '', label: 'manila' },
      { value: 'slate', label: 'slate' },
      { value: 'forest', label: 'forest' },
      { value: 'terracotta', label: 'terracotta' },
      { value: 'iris', label: 'iris' },
    ];

    if (!Array.isArray(extraThemes) || extraThemes.length === 0) {
      return baseThemes;
    }

    return [...baseThemes, ...extraThemes];
  }

  window.AtelierFolderTabs = {
    clampInt,
    distributeSlots,
    buildTwoRowTabs,
    createThemeCatalog,
    getThemeTokens,
    normalizeThemeKey,
  };
})();
