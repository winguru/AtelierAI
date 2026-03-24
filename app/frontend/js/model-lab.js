(() => {
  const civitaiForm = document.getElementById('civitai-form');
  const civitaiInput = document.getElementById('civitai-id');
  const localForm = document.getElementById('local-form');
  const localInput = document.getElementById('local-hash');
  const catalogForm = document.getElementById('catalog-form');
  const catalogUrlInput = document.getElementById('catalog-url');
  const checkpointsUrlInput = document.getElementById('checkpoints-url');
  const lorasUrlInput = document.getElementById('loras-url');
  const includeCatalogInput = document.getElementById('include-catalog');
  const imageLimitInput = document.getElementById('image-limit');
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
    activeTabId: 'catalog',
    currentPayloads: [],
  };

  if (
    !civitaiForm
    || !civitaiInput
    || !localForm
    || !localInput
    || !catalogForm
    || !catalogUrlInput
    || !checkpointsUrlInput
    || !lorasUrlInput
    || !includeCatalogInput
    || !imageLimitInput
    || !exportPanel
    || !copyExportButton
    || !downloadExportButton
  ) {
    return;
  }

  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  function setStatus(nextState, title, message) {
    statusPanel.className = `status-panel ${nextState}`;
    statusTitle.textContent = title;
    statusMessage.textContent = message;
  }

  function getPayloadByMode() {
    return new Map(state.currentPayloads.map((payload) => [String(payload?.mode || ''), payload]));
  }

  function getFormState() {
    return {
      civitaiId: civitaiInput.value.trim(),
      fileHash: localInput.value.trim(),
      catalogUrl: catalogUrlInput.value.trim(),
      checkpointsUrl: checkpointsUrlInput.value.trim(),
      lorasUrl: lorasUrlInput.value.trim(),
      includeCatalog: includeCatalogInput.checked,
      imageLimit: imageLimitInput.value.trim() || '250',
    };
  }

  function setFormState(nextState) {
    civitaiInput.value = String(nextState.civitaiId || '');
    localInput.value = String(nextState.fileHash || '');
    catalogUrlInput.value = String(nextState.catalogUrl || '');
    checkpointsUrlInput.value = String(nextState.checkpointsUrl || '');
    lorasUrlInput.value = String(nextState.lorasUrl || '');
    includeCatalogInput.checked = Boolean(nextState.includeCatalog);
    imageLimitInput.value = String(nextState.imageLimit || '250');
  }

  function syncUrlState(nextState) {
    const nextUrl = new URL(window.location.href);
    const setOrDelete = (key, value) => {
      if (value) {
        nextUrl.searchParams.set(key, value);
      } else {
        nextUrl.searchParams.delete(key);
      }
    };
    setOrDelete('civitai', nextState.civitaiId);
    setOrDelete('fileHash', nextState.fileHash);
    setOrDelete('catalogUrl', nextState.catalogUrl);
    setOrDelete('checkpointsUrl', nextState.checkpointsUrl);
    setOrDelete('lorasUrl', nextState.lorasUrl);
    setOrDelete('imageLimit', nextState.imageLimit);
    if (nextState.includeCatalog) {
      nextUrl.searchParams.set('includeCatalog', '1');
    } else {
      nextUrl.searchParams.delete('includeCatalog');
    }
    window.history.replaceState({}, '', nextUrl);
  }

  function buildQueryString(config) {
    const params = new URLSearchParams();
    if (config.catalogUrl) {
      params.set('catalog_url', config.catalogUrl);
    }
    if (config.checkpointsUrl) {
      params.set('checkpoints_url', config.checkpointsUrl);
    }
    if (config.lorasUrl) {
      params.set('loras_url', config.lorasUrl);
    }
    return params.toString();
  }

  function buildExportPayload() {
    if (!Array.isArray(state.currentPayloads) || !state.currentPayloads.length) {
      return null;
    }
    return {
      export_type: state.currentPayloads.length > 1 ? 'bundle' : 'inspection',
      active_tab: state.activeTabId,
      exported_at: new Date().toISOString(),
      payloads: state.currentPayloads,
    };
  }

  function getExportFilename() {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const payloadByMode = getPayloadByMode();
    if (state.activeTabId === 'catalog' && payloadByMode.get('catalog')) {
      return `model-lab-catalog-${stamp}.json`;
    }
    if (state.activeTabId === 'civitai' && payloadByMode.get('civitai')) {
      return `model-lab-civitai-${payloadByMode.get('civitai')?.target?.image_id || 'unknown'}-${stamp}.json`;
    }
    if (state.activeTabId === 'local' && payloadByMode.get('local')) {
      return `model-lab-local-${payloadByMode.get('local')?.target?.file_hash || 'unknown'}-${stamp}.json`;
    }
    return `model-lab-export-${stamp}.json`;
  }

  function updateExportPanel() {
    const exportPayload = buildExportPayload();
    const isReady = Boolean(exportPayload);
    exportPanel.classList.toggle('is-disabled', !isReady);
    copyExportButton.disabled = !isReady;
    downloadExportButton.disabled = !isReady;
    if (!isReady) {
      exportTitle.textContent = 'Export JSON';
      exportMessage.textContent = 'Load one or more model-reference views to export structured JSON for analysis or follow-up normalization work.';
      return;
    }
    exportTitle.textContent = 'Export Model JSON';
    exportMessage.textContent = 'Exports the active model-reference inspection bundle, including extracted references, validation, and any local catalog source details.';
  }

  async function copyExportPayload() {
    const exportPayload = buildExportPayload();
    if (!exportPayload) {
      return;
    }
    await navigator.clipboard.writeText(JSON.stringify(exportPayload, null, 2));
    setStatus('is-success', 'JSON copied', 'Structured model-reference JSON was copied to the clipboard.');
  }

  function downloadExportPayload() {
    const exportPayload = buildExportPayload();
    if (!exportPayload) {
      return;
    }
    const blob = new Blob([JSON.stringify(exportPayload, null, 2)], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getExportFilename();
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    setStatus('is-success', 'JSON downloaded', 'Structured model-reference JSON was downloaded to a file.');
  }

  function createOverviewGrid(overview) {
    const grid = document.createElement('section');
    grid.className = 'overview-grid';
    if (!overview || typeof overview !== 'object') {
      return grid;
    }
    const fieldConfig = {
      image_id: { order: 10 },
      file_hash: { order: 20, className: 'overview-card-wide' },
      image_count: { order: 30 },
      images_with_references: { order: 40 },
      reference_count: { order: 50 },
      matched_local_count: { order: 60 },
      remote_hosted_count: { order: 70 },
      local_catalog_entry_count: { order: 80 },
      source_url: { order: 90, className: 'overview-card-full' },
      file_name: { order: 100, className: 'overview-card-wide' },
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
        if (fieldConfig[key]?.className) {
          card.classList.add(fieldConfig[key].className);
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
    if (!Array.isArray(messages) || !messages.length) {
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

  function createSummaryPills(summary) {
    const wrap = document.createElement('div');
    wrap.className = 'summary-pills';
    if (!summary || typeof summary !== 'object') {
      return wrap;
    }
    const items = [
      ['References', summary.reference_count],
      ['Local Matches', summary.matched_local_count],
      ['Remote Hosted', summary.remote_hosted_count],
      ['Catalog Entries', summary.local_catalog_entry_count],
    ];
    Object.entries(summary.by_type || {}).forEach(([key, value]) => {
      items.push([key, value]);
    });
    items.forEach(([label, value]) => {
      if (value === null || value === undefined || value === '') {
        return;
      }
      const pill = document.createElement('div');
      pill.className = 'summary-pill';
      const labelNode = document.createElement('span');
      labelNode.textContent = String(label);
      const valueNode = document.createElement('strong');
      valueNode.textContent = String(value);
      pill.append(labelNode, valueNode);
      wrap.append(pill);
    });
    return wrap;
  }

  function appendReferenceField(container, label, value, asList = false) {
    if (value === null || value === undefined || value === '' || (Array.isArray(value) && !value.length)) {
      return;
    }
    const field = document.createElement('div');
    field.className = 'reference-field';
    const heading = document.createElement('span');
    heading.textContent = label;
    field.append(heading);
    if (asList && Array.isArray(value)) {
      const list = document.createElement('ul');
      list.className = 'reference-list';
      value.forEach((item) => {
        const row = document.createElement('li');
        row.textContent = String(item);
        list.append(row);
      });
      field.append(list);
    } else {
      const content = document.createElement('strong');
      content.textContent = Array.isArray(value) ? value.join(', ') : String(value);
      field.append(content);
    }
    container.append(field);
  }

  function createReferenceCard(reference) {
    const card = document.createElement('article');
    card.className = 'reference-card';

    const heading = document.createElement('div');
    heading.className = 'reference-card-heading';
    const titleWrap = document.createElement('div');
    const title = document.createElement('h4');
    title.textContent = reference.display_name || reference.normalized_name || 'Unnamed resource';
    const subtitle = document.createElement('div');
    subtitle.className = 'reference-subtitle';
    subtitle.textContent = reference.version_name || reference.base_model_name || reference.source_identifier || reference.resource_type || 'reference';
    titleWrap.append(title, subtitle);

    const flags = document.createElement('div');
    flags.className = 'reference-flags';
    const typeFlag = document.createElement('span');
    typeFlag.className = 'reference-flag';
    typeFlag.textContent = reference.resource_type || 'other';
    flags.append(typeFlag);
    if (reference.is_primary) {
      const primaryFlag = document.createElement('span');
      primaryFlag.className = 'reference-flag is-primary';
      primaryFlag.textContent = 'primary';
      flags.append(primaryFlag);
    }
    if (reference.local_installed) {
      const localFlag = document.createElement('span');
      localFlag.className = 'reference-flag is-local';
      localFlag.textContent = 'local';
      flags.append(localFlag);
    }
    if (reference.remote_hosted) {
      const remoteFlag = document.createElement('span');
      remoteFlag.className = 'reference-flag is-remote';
      remoteFlag.textContent = 'remote';
      flags.append(remoteFlag);
    }
    heading.append(titleWrap, flags);

    const fields = document.createElement('div');
    fields.className = 'reference-fields';
    appendReferenceField(fields, 'Usage Count', reference.observation_count);
    appendReferenceField(fields, 'Targets', (reference.targets || []).map((item) => item.target_label || item.target_key).slice(0, 4), true);
    appendReferenceField(fields, 'Hashes', reference.hashes, true);
    appendReferenceField(fields, 'CivitAI Model ID', reference.civitai_model_id);
    appendReferenceField(fields, 'CivitAI Version ID', reference.civitai_model_version_id);
    appendReferenceField(fields, 'Local Matches', (reference.local_matches || []).map((item) => `${item.display_name} (${item.match_basis})`).slice(0, 5), true);

    card.append(heading, fields);
    return card;
  }

  function createReferenceSection(payload) {
    const section = document.createElement('section');
    section.className = 'panel-section';
    const title = document.createElement('h3');
    title.textContent = 'References';
    section.append(title);
    const references = payload?.normalized?.references;
    if (!Array.isArray(references) || !references.length) {
      const empty = document.createElement('div');
      empty.className = 'reference-empty';
      empty.textContent = 'No references extracted for this source.';
      section.append(empty);
      return section;
    }
    const grid = document.createElement('div');
    grid.className = 'reference-grid';
    references.forEach((reference) => {
      grid.append(createReferenceCard(reference));
    });
    section.append(grid);
    return section;
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
    title.textContent = options.title || (mode === 'catalog' ? 'Known Model Catalog' : mode === 'civitai' ? 'CivitAI Model Inspection' : 'Local Model Inspection');
    const note = document.createElement('div');
    note.className = 'card-note';
    note.textContent = options.note || (mode === 'catalog'
      ? 'Aggregated known references from stored local images.'
      : mode === 'civitai'
        ? `Remote source${payload?.target?.image_id ? ` ${payload.target.image_id}` : ''}`
        : `Local image${payload?.target?.file_hash ? ` ${payload.target.file_hash}` : ''}`);
    titleWrap.append(title, note);
    const badge = document.createElement('span');
    badge.className = `badge badge-${validationStatus}`;
    badge.textContent = validationStatus;
    heading.append(titleWrap, badge);
    panel.append(heading);

    panel.append(createOverviewGrid(payload?.overview));

    const summarySection = document.createElement('section');
    summarySection.className = 'panel-section';
    const summaryTitle = document.createElement('h3');
    summaryTitle.textContent = 'Summary';
    summarySection.append(summaryTitle, createSummaryPills(payload?.normalized?.summary));
    panel.append(summarySection);

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

    panel.append(createReferenceSection(payload));

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
    if (!Array.isArray(payloads) || !payloads.length) {
      state.currentPayloads = [];
      const emptyCard = document.createElement('article');
      emptyCard.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'No inspection loaded';
      const text = document.createElement('p');
      text.textContent = 'Run one of the model reference inspections to review extracted checkpoints, LoRAs, hashes, and local-install matches.';
      emptyCard.append(title, text);
      inspectionPanels.append(emptyCard);
      updateExportPanel();
      return;
    }

    state.currentPayloads = payloads;
    const tabs = payloads.map((payload) => {
      const mode = String(payload?.mode || 'inspection');
      return {
        id: mode,
        label: mode === 'catalog' ? 'Known Catalog' : mode === 'civitai' ? 'CivitAI Inspection' : 'Local Inspection',
        render: () => createInspectionPanel(payload),
      };
    });

    const availableTabIds = new Set(tabs.map((tab) => tab.id));
    if (!availableTabIds.has(state.activeTabId)) {
      state.activeTabId = tabs[0].id;
    }

    if (uiKit?.createTabbedWorkspace) {
      const workspace = uiKit.createTabbedWorkspace({
        tabs,
        activeTabId: state.activeTabId,
        ariaLabel: 'Model inspection tabs',
        onTabChange: (nextTabId) => {
          state.activeTabId = nextTabId;
        },
        onRender: () => {
          updateExportPanel();
        },
      });
      if (workspace) {
        state.activeTabId = workspace.getActiveTabId();
        inspectionPanels.append(workspace.root);
        return;
      }
    }

    // Fallback keeps the page usable if shared helpers fail to load.
    const fallbackWorkspace = document.createElement('section');
    fallbackWorkspace.className = 'folder-workspace';
    const fallbackBody = document.createElement('div');
    fallbackBody.className = 'folder-body';
    fallbackBody.append(tabs[0].render());
    fallbackWorkspace.append(fallbackBody);
    inspectionPanels.append(fallbackWorkspace);
    state.activeTabId = tabs[0].id;
    updateExportPanel();
  }

  async function runInspectionSequence(formState) {
    const configQuery = buildQueryString(formState);
    const withQuery = (baseUrl) => (configQuery ? `${baseUrl}?${configQuery}` : baseUrl);
    const targets = [];
    if (formState.civitaiId) {
      targets.push({
        descriptor: `CivitAI image ${formState.civitaiId}`,
        url: withQuery(`/model-prototype/civitai/${encodeURIComponent(formState.civitaiId)}`),
      });
    }
    if (formState.fileHash) {
      targets.push({
        descriptor: `local image ${formState.fileHash}`,
        url: withQuery(`/images/${encodeURIComponent(formState.fileHash)}/model-prototype`),
      });
    }
    if (formState.includeCatalog) {
      const catalogParams = new URLSearchParams();
      if (formState.catalogUrl) {
        catalogParams.set('catalog_url', formState.catalogUrl);
      }
      if (formState.checkpointsUrl) {
        catalogParams.set('checkpoints_url', formState.checkpointsUrl);
      }
      if (formState.lorasUrl) {
        catalogParams.set('loras_url', formState.lorasUrl);
      }
      if (formState.imageLimit) {
        catalogParams.set('image_limit', formState.imageLimit);
      }
      targets.push({
        descriptor: 'known model catalog',
        url: `/model-prototype/catalog?${catalogParams.toString()}`,
      });
    }

    if (!targets.length) {
      renderInspectionPanels([]);
      setStatus('is-idle', 'Ready', 'Inspect a remote image, a local image, a library catalog, or any combination of those sources.');
      return;
    }

    setStatus('is-loading', 'Loading', `Inspecting ${targets.map((target) => target.descriptor).join(' and ')}...`);
    try {
      const payloads = await Promise.all(targets.map((target) => fetchPayload(target.url)));
      renderInspectionPanels(payloads);
      const statuses = payloads.map((payload) => String(payload?.validation?.status || 'ok'));
      const hasError = statuses.includes('error');
      const hasWarning = statuses.includes('warning');
      setStatus(
        hasError ? 'is-error' : hasWarning ? 'is-warning' : 'is-success',
        hasError ? 'Inspection completed with errors' : hasWarning ? 'Inspection completed with warnings' : 'Inspection completed',
        payloads.length > 1 ? `Loaded ${payloads.length} model views.` : `Loaded ${targets[0].descriptor}.`,
      );
    } catch (error) {
      renderInspectionPanels([]);
      setStatus('is-error', 'Request failed', error instanceof Error ? error.message : String(error));
    }
  }

  civitaiForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const formState = getFormState();
    if (!formState.civitaiId) {
      setStatus('is-warning', 'Missing value', 'Enter a CivitAI image ID first.');
      return;
    }
    syncUrlState(formState);
    runInspectionSequence(formState);
  });

  localForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const formState = getFormState();
    if (!formState.fileHash) {
      setStatus('is-warning', 'Missing value', 'Enter a local file hash first.');
      return;
    }
    syncUrlState(formState);
    runInspectionSequence(formState);
  });

  catalogForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const formState = getFormState();
    formState.includeCatalog = true;
    includeCatalogInput.checked = true;
    syncUrlState(formState);
    runInspectionSequence(formState);
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
  const initialState = {
    civitaiId: params.get('civitai') || '',
    fileHash: params.get('fileHash') || '',
    catalogUrl: params.get('catalogUrl') || '',
    checkpointsUrl: params.get('checkpointsUrl') || '',
    lorasUrl: params.get('lorasUrl') || '',
    includeCatalog: params.get('includeCatalog') === '1',
    imageLimit: params.get('imageLimit') || '250',
  };
  setFormState(initialState);
  updateExportPanel();
  if (initialState.civitaiId || initialState.fileHash || initialState.includeCatalog) {
    runInspectionSequence(initialState);
  }
})();