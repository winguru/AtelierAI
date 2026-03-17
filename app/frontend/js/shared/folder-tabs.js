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

  /**
   * Evenly distribute `count` slots over `gridCols` columns.
   * Returns [{ colStart, colSpan }] left-to-right.
   */
  function distributeSlots(count, gridCols) {
    const safeCount = clampInt(count, 0, 1000);
    const safeGridCols = clampInt(gridCols, 1, 1000);
    if (safeCount <= 0) return [];

    const spanBase = Math.floor(safeGridCols / safeCount);
    const remainder = safeGridCols % safeCount;
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
    gridCols = 13,
    idPrefix = 'tab',
    backRow = 1,
    frontRow = 2,
    createRender,
  }) {
    const tabs = [];
    const backPositions = distributeSlots(backLabels.length, gridCols);
    const frontPositions = distributeSlots(frontLabels.length, gridCols);

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
  };
})();
