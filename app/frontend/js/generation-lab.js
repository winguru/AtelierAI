(() => {
  const civitaiForm = document.getElementById('civitai-form');
  const civitaiInput = document.getElementById('civitai-id');
  const localForm = document.getElementById('local-form');
  const localInput = document.getElementById('local-hash');
  const statusPanel = document.getElementById('status-panel');
  const statusTitle = document.getElementById('status-title');
  const statusMessage = document.getElementById('status-message');
  const exportPanel = document.getElementById('export-panel');
  const exportTitle = document.getElementById('export-title');
  const exportMessage = document.getElementById('export-message');
  const copyExportButton = document.getElementById('copy-export-btn');
  const downloadExportButton = document.getElementById('download-export-btn');
  const inspectionPanels = document.getElementById('inspection-panels');
  const themeToggle = document.getElementById('theme-toggle');
  const preferences = window.AtelierPreferences || null;
  const uiKit = window.AtelierUi || null;
  const state = {
    activeTabId: 'compare',
    currentInspectionTabIds: [],
    currentPayloads: [],
  };
  let inspectionFolderWorkspace = null;

  if (!civitaiForm || !civitaiInput || !localForm || !localInput || !exportPanel || !exportTitle || !exportMessage || !copyExportButton || !downloadExportButton) {
    return;
  }

  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  function setFormValues({ civitaiId, fileHash }) {
    civitaiInput.value = String(civitaiId || '');
    localInput.value = String(fileHash || '');
  }

  function setStatus(state, title, message) {
    statusPanel.className = `status-panel ${state}`;
    statusTitle.textContent = title;
    statusMessage.textContent = message;
  }

  function getPayloadByMode() {
    return new Map(state.currentPayloads.map((payload) => [String(payload?.mode || ''), payload]));
  }

  function buildExportPayload() {
    const payloadByMode = getPayloadByMode();
    const civitaiPayload = payloadByMode.get('civitai') || null;
    const localPayload = payloadByMode.get('local') || null;

    if (state.activeTabId === 'compare' && civitaiPayload && localPayload) {
      return {
        export_type: 'compare',
        active_tab: 'compare',
        exported_at: new Date().toISOString(),
        civitai: civitaiPayload,
        local: localPayload,
      };
    }

    if (state.activeTabId === 'local' && localPayload) {
      return {
        export_type: 'inspection',
        active_tab: 'local',
        exported_at: new Date().toISOString(),
        payload: localPayload,
      };
    }

    if (civitaiPayload) {
      return {
        export_type: 'inspection',
        active_tab: 'civitai',
        exported_at: new Date().toISOString(),
        payload: civitaiPayload,
      };
    }

    if (localPayload) {
      return {
        export_type: 'inspection',
        active_tab: 'local',
        exported_at: new Date().toISOString(),
        payload: localPayload,
      };
    }

    return null;
  }

  function getExportFilename() {
    const payloadByMode = getPayloadByMode();
    const civitaiPayload = payloadByMode.get('civitai') || null;
    const localPayload = payloadByMode.get('local') || null;
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');

    if (state.activeTabId === 'compare' && civitaiPayload && localPayload) {
      const civitaiId = civitaiPayload?.target?.image_id || 'unknown';
      const fileHash = localPayload?.target?.file_hash || 'unknown';
      return `generation-lab-compare-civitai-${civitaiId}-local-${fileHash}-${stamp}.json`;
    }

    if (state.activeTabId === 'local' && localPayload) {
      const fileHash = localPayload?.target?.file_hash || 'unknown';
      return `generation-lab-local-${fileHash}-${stamp}.json`;
    }

    if (civitaiPayload) {
      const civitaiId = civitaiPayload?.target?.image_id || 'unknown';
      return `generation-lab-civitai-${civitaiId}-${stamp}.json`;
    }

    if (localPayload) {
      const fileHash = localPayload?.target?.file_hash || 'unknown';
      return `generation-lab-local-${fileHash}-${stamp}.json`;
    }

    return `generation-lab-export-${stamp}.json`;
  }

  function updateExportPanel() {
    const payloadByMode = getPayloadByMode();
    const hasCivitai = payloadByMode.has('civitai');
    const hasLocal = payloadByMode.has('local');
    const exportPayload = buildExportPayload();
    const isReady = Boolean(exportPayload);

    exportPanel.classList.toggle('is-disabled', !isReady);
    copyExportButton.disabled = !isReady;
    downloadExportButton.disabled = !isReady;

    if (!isReady) {
      exportTitle.textContent = 'Export JSON';
      exportMessage.textContent = 'Load an inspection to copy or download structured JSON for collaboration.';
      return;
    }

    if (state.activeTabId === 'compare' && hasCivitai && hasLocal) {
      exportTitle.textContent = 'Export Compare JSON';
      exportMessage.textContent = 'Includes both the remote CivitAI payload and the local inspection payload in one JSON document.';
      return;
    }

    if (state.activeTabId === 'local' && hasLocal) {
      exportTitle.textContent = 'Export Local JSON';
      exportMessage.textContent = 'Includes local DB fields, sidecar JSON, JSON metadata, EXIF payloads, normalized output, and validation.';
      return;
    }

    exportTitle.textContent = 'Export CivitAI JSON';
    exportMessage.textContent = 'Includes fetched CivitAI payloads, normalized output, and validation for the active remote inspection.';
  }

  function createInspectionTabPanel(tabId, renderPanel) {
    return () => {
      const panel = renderPanel();
      if (panel instanceof HTMLElement) {
        panel.id = `generation-lab-panel-${tabId}`;
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', `generation-lab-tab-${tabId}`);
      }
      return panel;
    };
  }

  function buildInspectionTabs(civitaiPayload, localPayload) {
    const tabs = [];

    if (civitaiPayload) {
      tabs.push({
        id: 'civitai',
        label: 'CivitAI Inspection',
        row: 2,
        render: createInspectionTabPanel('civitai', () => createInspectionPanel(civitaiPayload)),
      });
    }

    if (localPayload) {
      tabs.push({
        id: 'local',
        label: 'Local Inspection',
        row: 2,
        render: createInspectionTabPanel('local', () => createInspectionPanel(localPayload)),
      });
    }

    if (civitaiPayload && localPayload) {
      tabs.push({
        id: 'compare',
        label: 'Compare Inspections',
        row: 2,
        render: createInspectionTabPanel('compare', () => createComparePanel(civitaiPayload, localPayload)),
      });
    }

    return tabs;
  }

  function focusActiveInspectionTabButton() {
    const activeButton = inspectionPanels.querySelector('.ftab.is-active');
    if (activeButton instanceof HTMLElement) {
      activeButton.focus();
    }
  }

  function resolveInspectionTabId(tabId) {
    if (state.currentInspectionTabIds.includes(tabId)) {
      return tabId;
    }
    return state.currentInspectionTabIds[0] || null;
  }

  function setActiveInspectionTab(nextTabId, { focus = false } = {}) {
    const resolvedTabId = resolveInspectionTabId(nextTabId);
    if (!resolvedTabId) {
      return;
    }

    state.activeTabId = resolvedTabId;
    if (inspectionFolderWorkspace) {
      const selector = `[data-tab-id="${resolvedTabId}"]`;
      const targetButton = inspectionFolderWorkspace.stackEl.querySelector(selector);
      if (targetButton instanceof HTMLButtonElement) {
        if (targetButton.getAttribute('aria-selected') !== 'true') {
          targetButton.click();
        }
        if (focus) {
          targetButton.focus();
        }
        return;
      }

      inspectionFolderWorkspace.setActiveTabId(resolvedTabId);
    }
    updateExportPanel();

    if (focus) {
      focusActiveInspectionTabButton();
    }
  }

  function bindInspectionFolderKeyboardSupport() {
    if (!inspectionFolderWorkspace?.stackEl) {
      return;
    }

    inspectionFolderWorkspace.stackEl.addEventListener('keydown', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.closest('.ftab')) {
        return;
      }

      const orderedTabIds = state.currentInspectionTabIds.slice();
      if (!orderedTabIds.length) {
        return;
      }

      const currentId = resolveInspectionTabId(state.activeTabId);
      const currentIndex = currentId ? orderedTabIds.indexOf(currentId) : -1;
      if (currentIndex < 0) {
        return;
      }

      const moveToIndex = (nextIndex) => {
        const wrappedIndex = (nextIndex + orderedTabIds.length) % orderedTabIds.length;
        setActiveInspectionTab(orderedTabIds[wrappedIndex], { focus: true });
      };

      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        event.preventDefault();
        moveToIndex(currentIndex + 1);
        return;
      }

      if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        event.preventDefault();
        moveToIndex(currentIndex - 1);
        return;
      }

      if (event.key === 'Home') {
        event.preventDefault();
        moveToIndex(0);
        return;
      }

      if (event.key === 'End') {
        event.preventDefault();
        moveToIndex(orderedTabIds.length - 1);
      }
    });
  }

  async function copyExportPayload() {
    const exportPayload = buildExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    await navigator.clipboard.writeText(jsonText);
    setStatus('is-success', 'JSON copied', 'Structured inspection JSON was copied to the clipboard.');
  }

  function downloadExportPayload() {
    const exportPayload = buildExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    const blob = new Blob([jsonText], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getExportFilename();
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    setStatus('is-success', 'JSON downloaded', 'Structured inspection JSON was downloaded to a file.');
  }

  function createOverviewGrid(overview) {
    const grid = document.createElement('section');
    grid.className = 'overview-grid';

    if (!overview || typeof overview !== 'object') {
      return grid;
    }

    const fieldConfig = {
      image_id: { order: 10 },
      mime_type: { order: 20 },
      artist_name: { order: 30 },
      platform_name: { order: 40 },
      method_family: { order: 50 },
      process_count: { order: 60 },
      stage_count: { order: 70 },
      original_filename: { order: 80, className: 'overview-card-wide' },
      source_url: { order: 90, className: 'overview-card-wider' },
      image_url: { order: 100, className: 'overview-card-full' },
    };

    Object.entries(overview)
      .sort(([leftKey], [rightKey]) => {
        const leftOrder = fieldConfig[leftKey]?.order ?? 1000;
        const rightOrder = fieldConfig[rightKey]?.order ?? 1000;
        return leftOrder - rightOrder || leftKey.localeCompare(rightKey);
      })
      .forEach(([key, value]) => {
      if (value === null || value === undefined || value === '') {
        return;
      }

      const card = document.createElement('article');
      card.className = 'overview-card';
      const config = fieldConfig[key];
      if (config?.className) {
        card.classList.add(config.className);
      }

      const label = document.createElement('span');
      label.className = 'overview-label';
      label.textContent = key.replace(/_/g, ' ');

      const content = document.createElement('strong');
      content.className = 'overview-value';
      content.textContent = String(value);

      card.append(label, content);
      grid.append(card);
      });

    return grid;
  }

  function createMessagesList(messages, emptyLabel) {
    const target = document.createElement('ul');
    target.className = 'message-list';
    if (!Array.isArray(messages) || messages.length === 0) {
      const item = document.createElement('li');
      item.className = 'message-empty';
      item.textContent = emptyLabel;
      target.append(item);
      return target;
    }

    messages.forEach((message) => {
      const item = document.createElement('li');
      item.textContent = String(message);
      target.append(item);
    });

    return target;
  }

  function createInspectionPanel(payload, options = {}) {
    const validation = payload && typeof payload === 'object' ? payload.validation || {} : {};
    const validationStatus = String(validation.status || 'neutral');
    const mode = String(payload?.mode || 'inspection');
    const panel = document.createElement('div');
    panel.className = 'inspection-panel-content';

    const heading = document.createElement('div');
    heading.className = 'card-heading';

    const titleWrap = document.createElement('div');
    const title = document.createElement('h2');
    title.textContent = options.title || (mode === 'civitai' ? 'CivitAI Inspection' : mode === 'local' ? 'Local Inspection' : 'Inspection');
    const note = document.createElement('div');
    note.className = 'card-note';
    note.textContent = options.note || (mode === 'civitai'
      ? `Remote source${payload?.target?.image_id ? ` ${payload.target.image_id}` : ''}`
      : `Local image${payload?.target?.file_hash ? ` ${payload.target.file_hash}` : ''}`);
    titleWrap.append(title, note);

    const badge = document.createElement('span');
    badge.className = `badge badge-${validationStatus}`;
    badge.textContent = validationStatus;
    heading.append(titleWrap, badge);
    panel.append(heading);

    panel.append(createOverviewGrid(payload?.overview));

    const validationCard = document.createElement('section');
    validationCard.className = 'panel-section';
    const validationTitle = document.createElement('h3');
    validationTitle.textContent = 'Validation';
    validationCard.append(validationTitle);

    const validationColumns = document.createElement('div');
    validationColumns.className = 'validation-columns';

    const warningsWrap = document.createElement('div');
    const warningsTitle = document.createElement('h4');
    warningsTitle.textContent = 'Warnings';
    warningsWrap.append(warningsTitle, createMessagesList(validation.warnings, 'No warnings.'));

    const errorsWrap = document.createElement('div');
    const errorsTitle = document.createElement('h4');
    errorsTitle.textContent = 'Errors';
    errorsWrap.append(errorsTitle, createMessagesList(validation.errors, 'No errors.'));

    validationColumns.append(warningsWrap, errorsWrap);
    validationCard.append(validationColumns);
    panel.append(validationCard);

    const outputGrid = document.createElement('div');
    outputGrid.className = 'panel-output-grid';

    const normalizedSection = document.createElement('section');
    normalizedSection.className = 'panel-section';
    const normalizedTitle = document.createElement('h3');
    normalizedTitle.textContent = 'Normalized Output';
    const normalizedPre = document.createElement('pre');
    normalizedPre.className = 'json-panel';
    normalizedPre.textContent = JSON.stringify(payload?.normalized || {}, null, 2);
    normalizedSection.append(normalizedTitle, normalizedPre);

    const rawSection = document.createElement('section');
    rawSection.className = 'panel-section';
    const rawTitle = document.createElement('h3');
    rawTitle.textContent = 'Raw Payloads';
    const rawPre = document.createElement('pre');
    rawPre.className = 'json-panel';
    rawPre.textContent = JSON.stringify(payload?.raw || {}, null, 2);
    rawSection.append(rawTitle, rawPre);

    outputGrid.append(normalizedSection, rawSection);
    panel.append(outputGrid);

    return panel;
  }

  function normalizeCompareValue(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    return String(value).trim();
  }

  function createCompareGrid(civitaiPayload, localPayload) {
    const section = document.createElement('section');
    section.className = 'compare-grid';

    const comparisons = [
      ['Source URL', civitaiPayload?.overview?.source_url, localPayload?.overview?.source_url],
      ['Dimensions', civitaiPayload?.overview?.dimensions, localPayload?.raw?.merged?.width && localPayload?.raw?.merged?.height ? `${localPayload.raw.merged.width}x${localPayload.raw.merged.height}` : null],
      ['MIME Type', civitaiPayload?.overview?.mime_type, localPayload?.overview?.mimetype],
      ['Artist', civitaiPayload?.overview?.artist_name, localPayload?.raw?.merged?.artist_name],
      ['Sampler', civitaiPayload?.overview?.sampler_name, localPayload?.normalized?.processes?.[0]?.stages?.[0]?.sampler_name],
      ['Process Count', civitaiPayload?.overview?.process_count, localPayload?.overview?.process_count],
      ['Stage Count', civitaiPayload?.overview?.stage_count, localPayload?.overview?.stage_count],
    ];

    comparisons.forEach(([label, remoteValue, localValue]) => {
      const remoteText = normalizeCompareValue(remoteValue);
      const localText = normalizeCompareValue(localValue);
      const status = !remoteText || !localText ? 'missing' : remoteText === localText ? 'match' : 'mismatch';

      const card = document.createElement('article');
      card.className = `compare-card is-${status}`;

      const heading = document.createElement('div');
      heading.className = 'compare-card-heading';

      const title = document.createElement('strong');
      title.textContent = label;

      const badge = document.createElement('span');
      badge.className = `compare-badge is-${status}`;
      badge.textContent = status;

      heading.append(title, badge);

      const remoteNode = document.createElement('div');
      remoteNode.className = 'compare-value';
      remoteNode.innerHTML = `<span>CivitAI</span><strong>${remoteText || 'Missing'}</strong>`;

      const localNode = document.createElement('div');
      localNode.className = 'compare-value';
      localNode.innerHTML = `<span>Local</span><strong>${localText || 'Missing'}</strong>`;

      card.append(heading, remoteNode, localNode);
      section.append(card);
    });

    return section;
  }

  function createComparePanel(civitaiPayload, localPayload) {
    const panel = document.createElement('div');
    panel.className = 'inspection-panel-content';

    const heading = document.createElement('div');
    heading.className = 'card-heading';

    const titleWrap = document.createElement('div');
    const title = document.createElement('h2');
    title.textContent = 'Compare Inspections';
    const note = document.createElement('div');
    note.className = 'card-note';
    note.textContent = 'Quick comparison of the remote CivitAI source versus the local stored image.';
    titleWrap.append(title, note);

    const badge = document.createElement('span');
    badge.className = 'badge badge-neutral';
    badge.textContent = 'compare';
    heading.append(titleWrap, badge);
    panel.append(heading);

    panel.append(createCompareGrid(civitaiPayload, localPayload));

    const outputGrid = document.createElement('div');
    outputGrid.className = 'panel-output-grid compare-output-grid';
    outputGrid.append(
      createInspectionPanel(civitaiPayload, {
        title: 'CivitAI Snapshot',
        note: `Remote source${civitaiPayload?.target?.image_id ? ` ${civitaiPayload.target.image_id}` : ''}`,
      }),
      createInspectionPanel(localPayload, {
        title: 'Local Snapshot',
        note: `Local image${localPayload?.target?.file_hash ? ` ${localPayload.target.file_hash}` : ''}`,
      }),
    );
    panel.append(outputGrid);

    return panel;
  }

  async function fetchPayload(url) {
    const response = await fetch(url);
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      const detail = payload && typeof payload === 'object' && payload.detail
        ? String(payload.detail)
        : `Request failed with HTTP ${response.status}.`;
      throw new Error(detail);
    }

    return payload || {};
  }

  function renderInspectionPanels(payloads) {
    inspectionPanels.innerHTML = '';
    inspectionFolderWorkspace = null;
    if (!Array.isArray(payloads) || payloads.length === 0) {
      state.currentPayloads = [];
      state.currentInspectionTabIds = [];
      const emptyCard = document.createElement('article');
      emptyCard.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'No inspection loaded';
      const text = document.createElement('p');
      text.textContent = 'Run one or both inspections to compare the local record against the CivitAI source in one place.';
      emptyCard.append(title, text);
      inspectionPanels.append(emptyCard);
      updateExportPanel();
      return;
    }

    state.currentPayloads = payloads;
    const payloadByMode = new Map(payloads.map((payload) => [String(payload?.mode || ''), payload]));
    const civitaiPayload = payloadByMode.get('civitai');
    const localPayload = payloadByMode.get('local');
    const tabs = buildInspectionTabs(civitaiPayload, localPayload);

    if (!tabs.length) {
      state.currentInspectionTabIds = [];
      return;
    }

    state.currentInspectionTabIds = tabs.map((tab) => tab.id);
    const availableTabIds = new Set(tabs.map((tab) => tab.id));
    if (!availableTabIds.has(state.activeTabId)) {
      state.activeTabId = availableTabIds.has('compare') ? 'compare' : tabs[0].id;
    }

    if (uiKit?.createStackedFolderWorkspace) {
      const workspace = uiKit.createStackedFolderWorkspace({
        tabs,
        activeTabId: state.activeTabId,
        ariaLabel: 'Inspection tabs',
        wrapperClassName: 'generation-folder-workspace',
        stackClassName: 'generation-folder-stack',
        bodyClassName: 'generation-folder-body',
        onTabChange: (nextTabId) => {
          state.activeTabId = nextTabId;
          updateExportPanel();
        },
        onRender: () => {
          updateExportPanel();
        },
      });
      if (workspace) {
        inspectionFolderWorkspace = workspace;
        state.activeTabId = workspace.getActiveTabId();
        const tabButtons = Array.from(workspace.stackEl.querySelectorAll('.ftab'));
        tabs.forEach((tab, index) => {
          const button = tabButtons[index];
          if (!(button instanceof HTMLButtonElement)) {
            return;
          }
          button.id = `generation-lab-tab-${tab.id}`;
          button.dataset.tabId = tab.id;
          button.setAttribute('aria-controls', `generation-lab-panel-${tab.id}`);
        });
        inspectionPanels.append(workspace.root);
        bindInspectionFolderKeyboardSupport();
        return;
      }
    }

    // Fallback keeps the page usable if shared helpers fail to load.
    const fallbackWorkspace = document.createElement('section');
    fallbackWorkspace.className = 'generation-folder-workspace ftabs-workspace';
    const fallbackBody = document.createElement('div');
    fallbackBody.className = 'generation-folder-body ftab-body is-plain';
    fallbackBody.append(tabs[0].render());
    fallbackWorkspace.append(fallbackBody);
    inspectionPanels.append(fallbackWorkspace);
    state.activeTabId = tabs[0].id;
    updateExportPanel();
  }

  async function runInspectionSequence({ civitaiId, fileHash }) {
    const targets = [];
    if (civitaiId) {
      targets.push({
        descriptor: `CivitAI image ${civitaiId}`,
        url: `/generation-prototype/civitai/${encodeURIComponent(civitaiId)}`,
      });
    }
    if (fileHash) {
      targets.push({
        descriptor: `local image ${fileHash}`,
        url: `/images/${encodeURIComponent(fileHash)}/generation-prototype`,
      });
    }

    if (!targets.length) {
      state.currentPayloads = [];
      renderInspectionPanels([]);
      setStatus('is-idle', 'Ready', 'Choose a CivitAI image ID, a local file hash, or both to inspect together.');
      return;
    }

    const label = targets.map((target) => target.descriptor).join(' and ');
    setStatus('is-loading', 'Loading', `Inspecting ${label}...`);

    try {
      const payloads = await Promise.all(targets.map((target) => fetchPayload(target.url)));
      renderInspectionPanels(payloads);

      const statuses = payloads.map((payload) => String(payload?.validation?.status || 'ok'));
      const hasError = statuses.includes('error');
      const hasWarning = statuses.includes('warning');
      const title = hasError
        ? 'Inspection completed with errors'
        : hasWarning
          ? 'Inspection completed with warnings'
          : 'Inspection completed';
      const message = payloads.length > 1 ? `Loaded ${payloads.length} inspection panels.` : `Loaded ${label}.`;
      setStatus(hasError ? 'is-error' : hasWarning ? 'is-warning' : 'is-success', title, message);
    } catch (error) {
      renderInspectionPanels([]);
      setStatus('is-error', 'Request failed', error instanceof Error ? error.message : String(error));
    }
  }

  function syncUrlState({ civitaiId, fileHash }) {
    const nextUrl = new URL(window.location.href);
    if (civitaiId) {
      nextUrl.searchParams.set('civitai', civitaiId);
    } else {
      nextUrl.searchParams.delete('civitai');
    }
    if (fileHash) {
      nextUrl.searchParams.set('fileHash', fileHash);
      nextUrl.searchParams.delete('hash');
    } else {
      nextUrl.searchParams.delete('fileHash');
      nextUrl.searchParams.delete('hash');
    }
    window.history.replaceState({}, '', nextUrl);
  }

  civitaiForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const imageId = civitaiInput.value.trim();
    if (!imageId) {
      setStatus('is-warning', 'Missing value', 'Enter a CivitAI image ID first.');
      return;
    }
    const fileHash = localInput.value.trim();
    setFormValues({ civitaiId: imageId, fileHash });
    syncUrlState({ civitaiId: imageId, fileHash });
    runInspectionSequence({ civitaiId: imageId, fileHash });
  });

  localForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const fileHash = localInput.value.trim();
    if (!fileHash) {
      setStatus('is-warning', 'Missing value', 'Enter a local file hash first.');
      return;
    }
    const civitaiId = civitaiInput.value.trim();
    setFormValues({ civitaiId, fileHash });
    syncUrlState({ civitaiId, fileHash });
    runInspectionSequence({ civitaiId, fileHash });
  });

  copyExportButton.addEventListener('click', async () => {
    try {
      await copyExportPayload();
    } catch (error) {
      setStatus('is-error', 'Copy failed', error instanceof Error ? error.message : String(error));
    }
  });

  downloadExportButton.addEventListener('click', () => {
    try {
      downloadExportPayload();
    } catch (error) {
      setStatus('is-error', 'Download failed', error instanceof Error ? error.message : String(error));
    }
  });

  const params = new URLSearchParams(window.location.search);
  const civitaiQuery = params.get('civitai');
  const fileHashQuery = params.get('fileHash') || params.get('hash');
  setFormValues({ civitaiId: civitaiQuery || '', fileHash: fileHashQuery || '' });
  updateExportPanel();
  if (civitaiQuery || fileHashQuery) {
    runInspectionSequence({ civitaiId: civitaiQuery || '', fileHash: fileHashQuery || '' });
  }
})();