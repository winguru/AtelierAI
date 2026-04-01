(() => {
  const civitaiForm = document.getElementById('civitai-form');
  const civitaiInput = document.getElementById('civitai-id');
  const localForm = document.getElementById('local-form');
  const localInput = document.getElementById('local-hash');
  const catalogForm = document.getElementById('catalog-form');
  const catalogUrlInput = document.getElementById('catalog-url');
  const catalogAdvancedOverrides = document.getElementById('catalog-advanced-overrides');
  const checkpointsUrlInput = document.getElementById('checkpoints-url');
  const lorasUrlInput = document.getElementById('loras-url');
  const includeFullCatalogRawInput = document.getElementById('include-full-catalog-raw');
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
  const openGenerationLabLink = document.getElementById('open-generation-lab-link');
  const themeToggle = document.getElementById('theme-toggle');
  const preferences = window.AtelierPreferences || null;
  const uiKit = window.AtelierUi || null;
  const CATALOG_SETTINGS_STORAGE_KEY = 'atelierai.modelLab.catalogSettings.v1';

  const state = {
    activeTabId: 'catalog',
    currentPayloads: [],
  };
  const localMatchPreviewCache = new Map();
  let localMatchPreviewCard = null;
  let localMatchPreviewHideTimer = null;
  let localMatchPreviewAnchor = null;

  if (
    !civitaiForm
    || !civitaiInput
    || !localForm
    || !localInput
    || !catalogForm
    || !catalogUrlInput
    || !checkpointsUrlInput
    || !lorasUrlInput
    || !includeFullCatalogRawInput
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
      includeFullCatalogRaw: includeFullCatalogRawInput.checked,
      includeCatalog: includeCatalogInput.checked,
      imageLimit: imageLimitInput.value.trim() || '250',
    };
  }

  function getPersistedCatalogSettings() {
    try {
      const raw = window.localStorage.getItem(CATALOG_SETTINGS_STORAGE_KEY);
      if (!raw) {
        return null;
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') {
        return null;
      }
      return {
        catalogUrl: String(parsed.catalogUrl || ''),
        checkpointsUrl: String(parsed.checkpointsUrl || ''),
        lorasUrl: String(parsed.lorasUrl || ''),
        includeFullCatalogRaw: Boolean(parsed.includeFullCatalogRaw),
      };
    } catch {
      return null;
    }
  }

  function persistCatalogSettings() {
    const formState = getFormState();
    const payload = {
      catalogUrl: formState.catalogUrl,
      checkpointsUrl: formState.checkpointsUrl,
      lorasUrl: formState.lorasUrl,
      includeFullCatalogRaw: formState.includeFullCatalogRaw,
    };
    try {
      window.localStorage.setItem(CATALOG_SETTINGS_STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Ignore storage failures (private mode/quota) and continue.
    }
  }

  function buildGenerationLabHref(nextState) {
    const target = new URL('/generation-lab', window.location.origin);
    if (nextState.civitaiId) {
      target.searchParams.set('civitai', nextState.civitaiId);
    }
    if (nextState.fileHash) {
      target.searchParams.set('fileHash', nextState.fileHash);
    }
    return `${target.pathname}${target.search}`;
  }

  function updateGenerationLabLink(nextState) {
    if (!openGenerationLabLink) {
      return;
    }
    openGenerationLabLink.setAttribute('href', buildGenerationLabHref(nextState || getFormState()));
  }

  function setFormState(nextState) {
    if (nextState.civitaiId !== undefined) {
      civitaiInput.value = String(nextState.civitaiId || '');
    }
    if (nextState.fileHash !== undefined) {
      localInput.value = String(nextState.fileHash || '');
    }
    if (nextState.catalogUrl !== undefined) {
      catalogUrlInput.value = String(nextState.catalogUrl || '');
    }
    if (nextState.checkpointsUrl !== undefined) {
      checkpointsUrlInput.value = String(nextState.checkpointsUrl || '');
    }
    if (nextState.lorasUrl !== undefined) {
      lorasUrlInput.value = String(nextState.lorasUrl || '');
    }
    if (nextState.includeFullCatalogRaw !== undefined) {
      includeFullCatalogRawInput.checked = Boolean(nextState.includeFullCatalogRaw);
    }
    if (nextState.includeCatalog !== undefined) {
      includeCatalogInput.checked = Boolean(nextState.includeCatalog);
    }
    if (nextState.imageLimit !== undefined) {
      imageLimitInput.value = String(nextState.imageLimit || '250');
    }
    if (catalogAdvancedOverrides instanceof HTMLDetailsElement) {
      const hasOverrides = Boolean(
        String(checkpointsUrlInput.value || '').trim() || String(lorasUrlInput.value || '').trim(),
      );
      catalogAdvancedOverrides.open = hasOverrides;
    }
    updateGenerationLabLink(nextState);
  }

  function maybeHydrateCatalogSourcesFromPayloads(payloads) {
    const formState = getFormState();
    const hasManualValues = Boolean(formState.catalogUrl || formState.checkpointsUrl || formState.lorasUrl);
    if (hasManualValues) {
      return;
    }

    if (!Array.isArray(payloads) || !payloads.length) {
      return;
    }

    const firstSources = payloads
      .map((payload) => payload?.normalized?.local_catalog?.sources || payload?.raw?.local_catalog_fetch?.sources)
      .find((sources) => sources && typeof sources === 'object');
    if (!firstSources) {
      return;
    }

    const nextState = {
      ...formState,
      catalogUrl: String(firstSources.catalog_url || formState.catalogUrl || ''),
      checkpointsUrl: String(firstSources.checkpoints_url || formState.checkpointsUrl || ''),
      lorasUrl: String(firstSources.loras_url || formState.lorasUrl || ''),
    };

    setFormState(nextState);
    persistCatalogSettings();
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
    if (nextState.includeFullCatalogRaw) {
      nextUrl.searchParams.set('includeFullCatalogRaw', '1');
    } else {
      nextUrl.searchParams.delete('includeFullCatalogRaw');
    }
    if (nextState.includeCatalog) {
      nextUrl.searchParams.set('includeCatalog', '1');
    } else {
      nextUrl.searchParams.delete('includeCatalog');
    }
    window.history.replaceState({}, '', nextUrl);
    persistCatalogSettings();
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
    if (config.includeFullCatalogRaw) {
      params.set('include_full_catalog_raw', 'true');
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

  function buildCivitaiModelUrl(modelId, versionId) {
    const model = Number(modelId);
    if (!Number.isFinite(model) || model <= 0) {
      return null;
    }
    const version = Number(versionId);
    if (Number.isFinite(version) && version > 0) {
      return `https://civitai.com/models/${model}?modelVersionId=${version}`;
    }
    return `https://civitai.com/models/${model}`;
  }

  function appendReferenceLinkListField(container, label, links) {
    if (!Array.isArray(links) || !links.length) {
      return;
    }
    const field = document.createElement('div');
    field.className = 'reference-field';
    const heading = document.createElement('span');
    heading.textContent = label;
    field.append(heading);

    const list = document.createElement('ul');
    list.className = 'reference-list';
    links.forEach((item) => {
      if (!item || typeof item !== 'object') {
        return;
      }
      const url = String(item.url || '').trim();
      if (!url) {
        return;
      }
      const row = document.createElement('li');
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      anchor.className = 'local-match-preview-link';
      anchor.textContent = String(item.label || url);
      row.append(anchor);
      list.append(row);
    });

    if (!list.childElementCount) {
      return;
    }
    field.append(list);
    container.append(field);
  }

  function appendSingleReferenceLinkField(container, label, link) {
    if (!link || typeof link !== 'object') {
      return;
    }
    const url = String(link.url || '').trim();
    if (!url) {
      return;
    }
    const field = document.createElement('div');
    field.className = 'reference-field';
    const heading = document.createElement('span');
    heading.textContent = label;
    field.append(heading);

    const row = document.createElement('div');
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.className = 'local-match-preview-link';
    anchor.textContent = String(link.label || url);
    row.append(anchor);
    field.append(row);
    container.append(field);
  }

  function buildLocalMatchPreviewRequest(match) {
    const params = new URLSearchParams();
    params.set('display_name', String(match?.display_name || '').trim());
    if (match?.resource_type) {
      params.set('resource_type', String(match.resource_type));
    }
    const matchFilePath = String(match?.file_path || '').trim();
    if (matchFilePath) {
      params.set('file_path', matchFilePath);
    }
    const matchFileName = String(match?.file_name || '').trim();
    if (matchFileName) {
      params.set('file_name', matchFileName);
    }
    const matchModelName = String(match?.model_name || '').trim();
    if (matchModelName) {
      params.set('model_name', matchModelName);
    }
    const matchVersionName = String(match?.version_name || '').trim();
    if (matchVersionName) {
      params.set('version_name', matchVersionName);
    }
    if (match?.civitai_model_id !== null && match?.civitai_model_id !== undefined && match?.civitai_model_id !== '') {
      params.set('civitai_model_id', String(match.civitai_model_id));
    }
    if (match?.civitai_model_version_id !== null && match?.civitai_model_version_id !== undefined && match?.civitai_model_version_id !== '') {
      params.set('civitai_model_version_id', String(match.civitai_model_version_id));
    }
    const formState = getFormState();
    if (formState.catalogUrl) {
      params.set('catalog_url', formState.catalogUrl);
    }
    if (formState.checkpointsUrl) {
      params.set('checkpoints_url', formState.checkpointsUrl);
    }
    if (formState.lorasUrl) {
      params.set('loras_url', formState.lorasUrl);
    }
    return `/model-prototype/local-match-preview?${params.toString()}`;
  }

  function buildFallbackLocalMatchPreviewPayload(match) {
    const civitaiModelId = match?.civitai_model_id;
    const civitaiVersionId = match?.civitai_model_version_id;
    const civitaiUrl = String(match?.civitai_url || '').trim() || buildCivitaiModelUrl(civitaiModelId, civitaiVersionId) || null;
    return {
      preview: {
        display_name: String(match?.display_name || '').trim() || 'Local model',
        version_name: String(match?.version_name || '').trim() || null,
        model_name: String(match?.model_name || '').trim() || null,
        file_name: String(match?.file_name || '').trim() || null,
        model_type: String(match?.resource_type || '').trim() || null,
        civitai_model_id: civitaiModelId ?? null,
        civitai_model_version_id: civitaiVersionId ?? null,
        civitai_url: civitaiUrl,
        description: 'Preview metadata unavailable from local catalog; showing local match details.',
      },
    };
  }

  async function requestLocalModelDownload(reference) {
    if (!reference || typeof reference !== 'object') {
      throw new Error('Missing reference payload.');
    }
    const modelId = Number(reference.civitai_model_id);
    const versionId = Number(reference.civitai_model_version_id);
    if (!Number.isFinite(modelId) || modelId <= 0 || !Number.isFinite(versionId) || versionId <= 0) {
      throw new Error('Missing CivitAI model/version ID for download.');
    }

    const formState = getFormState();
    const payload = {
      civitai_model_id: modelId,
      civitai_model_version_id: versionId,
      resource_type: String(reference.resource_type || '').trim() || null,
      relative_path: '',
      use_default_paths: false,
      download_id: String(Date.now()),
    };
    if (formState.catalogUrl) {
      payload.catalog_url = formState.catalogUrl;
    }
    if (formState.checkpointsUrl) {
      payload.checkpoints_url = formState.checkpointsUrl;
    }
    if (formState.lorasUrl) {
      payload.loras_url = formState.lorasUrl;
    }

    const response = await fetch('/model-prototype/local-model-download', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body && typeof body === 'object' && body.detail
        ? String(body.detail)
        : `Download request failed with HTTP ${response.status}.`;
      throw new Error(detail);
    }
    return body;
  }

  function createPreviewLine(label, value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const row = document.createElement('div');
    row.className = 'local-match-preview-line';
    const key = document.createElement('span');
    key.className = 'local-match-preview-key';
    key.textContent = `${label}:`;
    const val = document.createElement('strong');
    val.className = 'local-match-preview-value';
    val.textContent = String(value);
    row.append(key, val);
    return row;
  }

  function ensureLocalMatchPreviewCard() {
    if (localMatchPreviewCard instanceof HTMLElement) {
      return localMatchPreviewCard;
    }
    const card = document.createElement('div');
    card.className = 'local-match-preview local-match-preview-floating';
    card.hidden = true;
    card.addEventListener('mouseenter', () => {
      if (localMatchPreviewHideTimer) {
        window.clearTimeout(localMatchPreviewHideTimer);
        localMatchPreviewHideTimer = null;
      }
    });
    card.addEventListener('mouseleave', () => {
      hideLocalMatchPreviewCard();
    });
    document.body.append(card);
    localMatchPreviewCard = card;
    return card;
  }

  function clearLocalMatchPreviewCard() {
    const card = ensureLocalMatchPreviewCard();
    card.innerHTML = '';
    card.classList.remove('is-loading', 'is-error', 'is-visible');
    card.hidden = true;
    card.dataset.previewRequestKey = '';
    localMatchPreviewAnchor = null;
  }

  function hideLocalMatchPreviewCard(delayMs = 120) {
    if (localMatchPreviewHideTimer) {
      window.clearTimeout(localMatchPreviewHideTimer);
      localMatchPreviewHideTimer = null;
    }
    localMatchPreviewHideTimer = window.setTimeout(() => {
      clearLocalMatchPreviewCard();
    }, delayMs);
  }

  function positionLocalMatchPreviewCard(card, anchorElement) {
    if (!(card instanceof HTMLElement) || !(anchorElement instanceof HTMLElement)) {
      return;
    }
    const anchorRect = anchorElement.getBoundingClientRect();
    const margin = 12;
    const desiredWidth = Math.min(480, Math.max(260, Math.round(window.innerWidth * 0.72)));
    card.style.width = `${desiredWidth}px`;

    const cardRect = card.getBoundingClientRect();
    const maxLeft = Math.max(margin, window.innerWidth - cardRect.width - margin);
    let left = Math.min(Math.max(margin, anchorRect.left), maxLeft);
    let top = anchorRect.bottom + 8;
    if (top + cardRect.height > window.innerHeight - margin) {
      top = Math.max(margin, anchorRect.top - cardRect.height - 8);
    }

    card.style.left = `${Math.round(left)}px`;
    card.style.top = `${Math.round(top)}px`;
  }

  function showLocalMatchPreviewCard(match, anchorElement) {
    const card = ensureLocalMatchPreviewCard();
    if (localMatchPreviewHideTimer) {
      window.clearTimeout(localMatchPreviewHideTimer);
      localMatchPreviewHideTimer = null;
    }
    localMatchPreviewAnchor = anchorElement instanceof HTMLElement ? anchorElement : null;
    card.hidden = false;
    card.classList.add('is-visible');
    positionLocalMatchPreviewCard(card, anchorElement);
    loadLocalMatchPreview(match, card);
  }

  function renderLocalMatchPreview(previewNode, payload) {
    previewNode.innerHTML = '';
    const preview = payload?.preview;
    if (!preview || typeof preview !== 'object') {
      previewNode.classList.remove('is-loading');
      previewNode.classList.add('is-error');
      previewNode.textContent = 'No preview metadata found.';
      return;
    }

    previewNode.classList.remove('is-loading', 'is-error');
    const title = document.createElement('div');
    title.className = 'local-match-preview-title';
    title.textContent = preview.model_name || preview.display_name || 'Local model preview';
    previewNode.append(title);

    if (preview.preview_image_url) {
      const image = document.createElement('img');
      image.className = 'local-match-preview-image';
      image.src = String(preview.preview_image_url);
      image.alt = String(preview.display_name || preview.model_name || 'Model preview image');
      image.loading = 'lazy';
      previewNode.append(image);
    }

    const infoRows = [
      createPreviewLine('Version', preview.version_name),
      createPreviewLine('File', preview.file_name),
      createPreviewLine('Type', preview.model_type),
      createPreviewLine('Creator', preview.creator_username),
      createPreviewLine('Base model', preview.base_model),
      createPreviewLine('Model ID', preview.civitai_model_id),
      createPreviewLine('Version ID', preview.civitai_model_version_id),
    ].filter(Boolean);
    infoRows.forEach((row) => previewNode.append(row));

    if (preview.civitai_url) {
      const link = document.createElement('a');
      link.className = 'local-match-preview-link';
      link.href = String(preview.civitai_url);
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open on CivitAI';
      previewNode.append(link);
    }

    if (preview.description) {
      const desc = document.createElement('p');
      desc.className = 'local-match-preview-description';
      desc.textContent = String(preview.description).slice(0, 280);
      previewNode.append(desc);
    }
  }

  async function loadLocalMatchPreview(match, previewNode) {
    const displayName = String(match?.display_name || '').trim();
    if (!displayName) {
      previewNode.classList.remove('is-loading');
      previewNode.classList.add('is-error');
      previewNode.textContent = 'Missing model name.';
      return;
    }
    const cacheKey = `${String(match?.resource_type || '')}::${displayName}::${buildLocalMatchPreviewRequest(match)}`;
    previewNode.dataset.previewRequestKey = cacheKey;
    if (localMatchPreviewCache.has(cacheKey)) {
      if (previewNode.dataset.previewRequestKey === cacheKey) {
        renderLocalMatchPreview(previewNode, localMatchPreviewCache.get(cacheKey));
        if (localMatchPreviewAnchor) {
          positionLocalMatchPreviewCard(previewNode, localMatchPreviewAnchor);
        }
      }
      return;
    }

    previewNode.classList.add('is-loading');
    previewNode.classList.remove('is-error');
    previewNode.textContent = 'Loading preview...';

    try {
      const response = await fetch(buildLocalMatchPreviewRequest(match));
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const fallbackPayload = buildFallbackLocalMatchPreviewPayload(match);
        localMatchPreviewCache.set(cacheKey, fallbackPayload);
        if (previewNode.dataset.previewRequestKey === cacheKey) {
          renderLocalMatchPreview(previewNode, fallbackPayload);
          if (localMatchPreviewAnchor) {
            positionLocalMatchPreviewCard(previewNode, localMatchPreviewAnchor);
          }
        }
        return;
      }
      localMatchPreviewCache.set(cacheKey, payload || {});
      if (previewNode.dataset.previewRequestKey === cacheKey) {
        renderLocalMatchPreview(previewNode, payload || {});
        if (localMatchPreviewAnchor) {
          positionLocalMatchPreviewCard(previewNode, localMatchPreviewAnchor);
        }
      }
    } catch (error) {
      if (previewNode.dataset.previewRequestKey !== cacheKey) {
        return;
      }
      previewNode.classList.remove('is-loading');
      previewNode.classList.add('is-error');
      previewNode.textContent = error instanceof Error ? error.message : 'Preview request failed.';
    }
  }

  function appendLocalMatchesField(container, matches) {
    if (!Array.isArray(matches) || !matches.length) {
      return;
    }

    const toNumericId = (value) => {
      const parsed = Number(value);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
    };
    const referenceCard = container.closest('.reference-card');
    const referenceModelId = toNumericId(referenceCard?.dataset?.referenceModelId);
    const referenceVersionId = toNumericId(referenceCard?.dataset?.referenceVersionId);
    const hasAuthoritativeReferenceIds = referenceModelId !== null && referenceVersionId !== null;

    const normalizeBasis = (value) => String(value || '').trim().toLowerCase();

    const exactMatches = [];
    const similarMatches = [];
    const otherMatches = [];

    matches.forEach((match) => {
      if (!match || typeof match !== 'object') {
        return;
      }
      const matchModelId = toNumericId(match.civitai_model_id);
      const matchVersionId = toNumericId(match.civitai_model_version_id);
      const matchBasis = normalizeBasis(match.match_basis);
      const isExact = (
        referenceModelId !== null
        && referenceVersionId !== null
        && matchModelId === referenceModelId
        && matchVersionId === referenceVersionId
      );
      const isSimilar = (
        !isExact
        && referenceModelId !== null
        && matchModelId === referenceModelId
      );

      let fallbackExact = false;
      let fallbackSimilar = false;
      if (!hasAuthoritativeReferenceIds) {
        if (matchBasis === 'civitai_model_version_id') {
          fallbackExact = true;
        } else if (matchBasis === 'civitai_model_id') {
          fallbackSimilar = true;
        } else if (matchBasis === 'hash') {
          // Hash-only correlation is the strongest available fallback when IDs are absent.
          fallbackExact = true;
        } else if (matchBasis === 'normalized_name' || matchBasis === 'name_fuzzy') {
          fallbackSimilar = true;
        }
      }

      if (isExact || fallbackExact) {
        exactMatches.push(match);
      } else if (isSimilar || fallbackSimilar) {
        similarMatches.push(match);
      } else {
        otherMatches.push(match);
      }
    });

    const field = document.createElement('div');
    field.className = 'reference-field';
    const heading = document.createElement('span');
    heading.textContent = 'Local Matches';
    field.append(heading);

    const grouped = [
      { title: hasAuthoritativeReferenceIds ? 'Exact Matches' : 'Exact Matches (best guess)', items: exactMatches },
      { title: hasAuthoritativeReferenceIds ? 'Similar Matches' : 'Similar Matches (best guess)', items: similarMatches },
      { title: 'Other Matches', items: otherMatches },
    ];

    grouped.forEach((group) => {
      if (!group.items.length) {
        return;
      }

      const groupLabel = document.createElement('div');
      groupLabel.className = 'local-match-group-label';
      groupLabel.textContent = group.title;
      field.append(groupLabel);

      const list = document.createElement('ul');
      list.className = 'reference-list local-match-list';
      group.items.slice(0, 5).forEach((match) => {
        const row = document.createElement('li');
        row.className = 'local-match-item';

        const nameButton = document.createElement('button');
        nameButton.type = 'button';
        nameButton.className = 'local-match-name';
        nameButton.textContent = String(match?.display_name || 'Unnamed match');

        const basis = document.createElement('span');
        basis.className = 'local-match-basis';
        basis.textContent = ` (${String(match?.match_basis || 'unknown')})`;

        nameButton.addEventListener('mouseenter', () => showLocalMatchPreviewCard(match, nameButton));
        nameButton.addEventListener('focus', () => showLocalMatchPreviewCard(match, nameButton));
        nameButton.addEventListener('mouseleave', () => hideLocalMatchPreviewCard());
        nameButton.addEventListener('blur', () => hideLocalMatchPreviewCard());

        row.append(nameButton, basis);
        list.append(row);
      });

      field.append(list);
    });

    container.append(field);
  }

  function createReferenceCard(reference) {
    const card = document.createElement('article');
    card.className = 'reference-card';
    if (reference && typeof reference === 'object') {
      if (reference.civitai_model_id !== null && reference.civitai_model_id !== undefined && reference.civitai_model_id !== '') {
        card.dataset.referenceModelId = String(reference.civitai_model_id);
      }
      if (reference.civitai_model_version_id !== null && reference.civitai_model_version_id !== undefined && reference.civitai_model_version_id !== '') {
        card.dataset.referenceVersionId = String(reference.civitai_model_version_id);
      }
    }

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
    const primaryCivitaiModelUrl = String(reference?.civitai_url || '').trim()
      || buildCivitaiModelUrl(reference?.civitai_model_id, reference?.civitai_model_version_id)
      || (() => {
        const firstMatchWithUrl = (reference.local_matches || []).find((match) => {
          const matchUrl = String(match?.civitai_url || '').trim()
            || buildCivitaiModelUrl(match?.civitai_model_id, match?.civitai_model_version_id);
          return Boolean(matchUrl);
        });
        if (!firstMatchWithUrl) {
          return '';
        }
        return String(firstMatchWithUrl.civitai_url || '').trim()
          || buildCivitaiModelUrl(firstMatchWithUrl.civitai_model_id, firstMatchWithUrl.civitai_model_version_id)
          || '';
      })();
    appendSingleReferenceLinkField(fields, 'CIVITAI MODEL SOURCE', {
      label: primaryCivitaiModelUrl,
      url: primaryCivitaiModelUrl,
    });
    appendReferenceField(fields, 'Hashes', reference.hashes, true);
    appendReferenceField(fields, 'CivitAI Model ID', reference.civitai_model_id);
    appendReferenceField(fields, 'CivitAI Version ID', reference.civitai_model_version_id);

    const canDownloadMissingLocal = !reference.local_installed
      && Number.isFinite(Number(reference.civitai_model_id))
      && Number(reference.civitai_model_id) > 0
      && Number.isFinite(Number(reference.civitai_model_version_id))
      && Number(reference.civitai_model_version_id) > 0;
    if (canDownloadMissingLocal) {
      const downloadField = document.createElement('div');
      downloadField.className = 'reference-field reference-download-field';

      const heading = document.createElement('span');
      heading.textContent = 'Local Model Download';
      downloadField.append(heading);

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'reference-download-button';
      button.textContent = 'Download via LoRA Manager';
      button.addEventListener('click', async () => {
        button.disabled = true;
        const originalText = button.textContent;
        button.textContent = 'Queueing download...';
        try {
          const result = await requestLocalModelDownload(reference);
          const queuedId = String(result?.request?.download_id || '').trim();
          setStatus(
            'is-success',
            'Download queued',
            queuedId
              ? `LoRA Manager accepted the download request (id ${queuedId}).`
              : 'LoRA Manager accepted the download request.',
          );
          button.textContent = 'Download requested';
        } catch (error) {
          setStatus('is-error', 'Download failed', error instanceof Error ? error.message : String(error));
          button.disabled = false;
          button.textContent = originalText || 'Download via LoRA Manager';
        }
      });

      const note = document.createElement('small');
      note.className = 'reference-download-note';
      note.textContent = 'Only shown for references missing from local catalog with CivitAI model/version IDs.';

      downloadField.append(button, note);
      fields.append(downloadField);
    }

    appendLocalMatchesField(fields, reference.local_matches || []);

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
    clearLocalMatchPreviewCard();
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

  window.addEventListener('scroll', () => {
    if (!localMatchPreviewCard || localMatchPreviewCard.hidden || !localMatchPreviewAnchor) {
      return;
    }
    positionLocalMatchPreviewCard(localMatchPreviewCard, localMatchPreviewAnchor);
  }, true);

  window.addEventListener('resize', () => {
    if (!localMatchPreviewCard || localMatchPreviewCard.hidden || !localMatchPreviewAnchor) {
      return;
    }
    positionLocalMatchPreviewCard(localMatchPreviewCard, localMatchPreviewAnchor);
  });

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
      if (formState.includeFullCatalogRaw) {
        catalogParams.set('include_full_catalog_raw', 'true');
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
      maybeHydrateCatalogSourcesFromPayloads(payloads);
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
    updateGenerationLabLink(formState);
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
    updateGenerationLabLink(formState);
    runInspectionSequence(formState);
  });

  catalogForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const formState = getFormState();
    formState.includeCatalog = true;
    includeCatalogInput.checked = true;
    syncUrlState(formState);
    updateGenerationLabLink(formState);
    runInspectionSequence(formState);
  });

  civitaiInput.addEventListener('input', () => {
    updateGenerationLabLink(getFormState());
  });

  localInput.addEventListener('input', () => {
    updateGenerationLabLink(getFormState());
  });

  [catalogUrlInput, checkpointsUrlInput, lorasUrlInput, includeFullCatalogRawInput].forEach((element) => {
    element.addEventListener('change', () => {
      persistCatalogSettings();
    });
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
  const persistedCatalogSettings = getPersistedCatalogSettings();
  const sourceQuery = String(params.get('source') || '').trim().toLowerCase();
  const cameFromGallery = sourceQuery === 'gallery';
  const catalogUrlQuery = params.get('catalogUrl');
  const checkpointsUrlQuery = params.get('checkpointsUrl');
  const lorasUrlQuery = params.get('lorasUrl');
  const includeFullCatalogRawQuery = params.get('includeFullCatalogRaw');

  const catalogUrlInitial = catalogUrlQuery ?? persistedCatalogSettings?.catalogUrl ?? '';
  const checkpointsUrlInitial = checkpointsUrlQuery ?? persistedCatalogSettings?.checkpointsUrl ?? '';
  const lorasUrlInitial = lorasUrlQuery ?? persistedCatalogSettings?.lorasUrl ?? '';
  const includeFullCatalogRawInitial = includeFullCatalogRawQuery !== null
    ? includeFullCatalogRawQuery === '1' || includeFullCatalogRawQuery === 'true'
    : Boolean(persistedCatalogSettings?.includeFullCatalogRaw);

  const initialState = {
    civitaiId: params.get('civitai') || params.get('civitaiId') || '',
    fileHash: params.get('fileHash') || params.get('hash') || '',
    catalogUrl: catalogUrlInitial,
    checkpointsUrl: checkpointsUrlInitial,
    lorasUrl: lorasUrlInitial,
    includeFullCatalogRaw: includeFullCatalogRawInitial,
    includeCatalog: params.get('includeCatalog') === '1',
    imageLimit: params.get('imageLimit') || '250',
  };
  setFormState(initialState);
  persistCatalogSettings();

  if (
    cameFromGallery
    && catalogAdvancedOverrides instanceof HTMLDetailsElement
    && !checkpointsUrlQuery
    && !lorasUrlQuery
  ) {
    catalogAdvancedOverrides.open = false;
  }

  updateExportPanel();
  updateGenerationLabLink(initialState);
  if (initialState.civitaiId || initialState.fileHash || initialState.includeCatalog) {
    runInspectionSequence(initialState);
  }
})();