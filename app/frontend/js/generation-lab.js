(() => {
  const civitaiForm = document.getElementById('civitai-form');
  const civitaiInput = document.getElementById('civitai-id');
  const localForm = document.getElementById('local-form');
  const localInput = document.getElementById('local-hash');
  const civitaiPreviewButton = document.getElementById('civitai-preview-btn');
  const civitaiPreviewPanel = document.getElementById('civitai-preview-panel');
  const localPreviewButton = document.getElementById('local-preview-btn');
  const localPreviewPanel = document.getElementById('local-preview-panel');
  const catalogForm = document.getElementById('catalog-form');
  const catalogUrlInput = document.getElementById('catalog-url');
  const catalogAdvancedOverrides = document.getElementById('catalog-advanced-overrides');
  const checkpointsUrlInput = document.getElementById('checkpoints-url');
  const lorasUrlInput = document.getElementById('loras-url');
  const includeFullCatalogRawInput = document.getElementById('include-full-catalog-raw');
  const statusPanel = document.getElementById('status-panel');
  const statusTitle = document.getElementById('status-title');
  const statusMessage = document.getElementById('status-message');
  const exportPanel = document.getElementById('export-panel');
  const exportTitle = document.getElementById('export-title');
  const exportMessage = document.getElementById('export-message');
  const copyExportButton = document.getElementById('copy-export-btn');
  const downloadExportButton = document.getElementById('download-export-btn');
  const copyComfyExportButton = document.getElementById('copy-comfy-export-btn');
  const downloadComfyExportButton = document.getElementById('download-comfy-export-btn');
  const copyComfyApiExportButton = document.getElementById('copy-comfy-api-export-btn');
  const downloadComfyApiExportButton = document.getElementById('download-comfy-api-export-btn');
  const templateSelect = document.getElementById('template-select');
  const templateRefreshButton = document.getElementById('template-refresh-btn');
  const templateNameInput = document.getElementById('template-name');
  const templateDescriptionInput = document.getElementById('template-description');
  const templateWorkflowFileInput = document.getElementById('template-workflow-file');
  const templateLoadFileButton = document.getElementById('template-load-file-btn');
  const templateWorkflowJsonInput = document.getElementById('template-workflow-json');
  const templateMappingsBody = document.getElementById('template-mappings-body');
  const templateAddMappingButton = document.getElementById('template-add-mapping-btn');
  const templateDefaultTokensInput = document.getElementById('template-default-tokens');
  const templateTokenOverridesInput = document.getElementById('template-token-overrides');
  const templateImportButton = document.getElementById('template-import-btn');
  const templateSaveButton = document.getElementById('template-save-btn');
  const templateDeleteButton = document.getElementById('template-delete-btn');
  const templateDiscoverButton = document.getElementById('template-discover-btn');
  const templateResolveButton = document.getElementById('template-resolve-btn');
  const templateDownloadResolvedButton = document.getElementById('template-download-resolved-btn');
  const templateTokenPicker = document.getElementById('template-token-picker');
  const templateApplyTokenButton = document.getElementById('template-apply-token-btn');
  const templateGuessPathButton = document.getElementById('template-guess-path-btn');
  const templateFieldFilterInput = document.getElementById('template-field-filter');
  const templateFieldCandidates = document.getElementById('template-field-candidates');
  const templateDiscoveredTokensPanel = document.getElementById('template-discovered-tokens');
  const templateResolveResultPanel = document.getElementById('template-resolve-result');
  const templateLabSection = document.getElementById('template-lab-section');
  const a1111BridgeSection = document.getElementById('a1111-bridge-section');
  const generateImageSection = document.getElementById('generate-image-section');
  const parityWorkbenchSection = document.getElementById('parity-workbench-section');
  const bridgeFileHashInput = document.getElementById('bridge-file-hash');
  const bridgeUseLocalHashButton = document.getElementById('bridge-use-local-hash-btn');
  const bridgeWorkflowJsonInput = document.getElementById('bridge-workflow-json');
  const bridgeIncludeGenerationPayloadInput = document.getElementById('bridge-include-generation-payload');
  const bridgeAnalyzeButton = document.getElementById('bridge-analyze-btn');
  const bridgeDownloadButton = document.getElementById('bridge-download-btn');
  const bridgeSaveButton = document.getElementById('bridge-save-btn');
  const bridgeClearButton = document.getElementById('bridge-clear-btn');
  const bridgeParseResultPanel = document.getElementById('bridge-parse-result');
  const bridgeCompareResultPanel = document.getElementById('bridge-compare-result');
  const bridgeDatasetRefreshButton = document.getElementById('bridge-dataset-refresh-btn');
  const bridgeDatasetRunAnalyzeButton = document.getElementById('bridge-dataset-run-analyze-btn');
  const bridgeDatasetDownloadButton = document.getElementById('bridge-dataset-download-btn');
  const bridgeDatasetCopyPathButton = document.getElementById('bridge-dataset-copy-path-btn');
  const bridgeDatasetSummary = document.getElementById('bridge-dataset-summary');
  const bridgeDatasetReadiness = document.getElementById('bridge-dataset-readiness');
  const bridgeDatasetConfidence = document.getElementById('bridge-dataset-confidence');
  const bridgeDatasetSamples = document.getElementById('bridge-dataset-samples');
  const bridgeDatasetCoreCoverage = document.getElementById('bridge-dataset-core-coverage');
  const bridgeDatasetMissingSeed = document.getElementById('bridge-dataset-missing-seed');
  const bridgeDatasetMissingModel = document.getElementById('bridge-dataset-missing-model');
  const generateWorkflowFileInput = document.getElementById('generate-workflow-file');
  const generateLoadFileButton = document.getElementById('generate-load-file-btn');
  const generateWorkflowJsonInput = document.getElementById('generate-workflow-json');
  const generateReferenceFileHashInput = document.getElementById('generate-reference-file-hash');
  const generateIncludeAllImagesInput = document.getElementById('generate-include-all-images');
  const generateThresholdOverrideInput = document.getElementById('generate-threshold-override');
  const generateTweakLabelInput = document.getElementById('generate-tweak-label');
  const generateTweakedParametersInput = document.getElementById('generate-tweaked-parameters');
  const generateUseLocalHashButton = document.getElementById('generate-use-local-hash-btn');
  const generateRunButton = document.getElementById('generate-run-btn');
  const generateAttemptsRefreshButton = document.getElementById('generate-attempts-refresh-btn');
  const generateStatusTitle = document.getElementById('generate-status-title');
  const generateStatusMessage = document.getElementById('generate-status-message');
  const generateStatusPanel = document.getElementById('generate-status-panel');
  const generateReferencePanel = document.getElementById('generate-reference-panel');
  const generateResultsGrid = document.getElementById('generate-results-grid');
  const generateResponseJsonPanel = document.getElementById('generate-response-json');
  const generateAttemptsPanel = document.getElementById('generate-attempts-panel');
  const parityFileHashInput = document.getElementById('parity-file-hash');
  const parityOpenPickerButton = document.getElementById('parity-open-picker-btn');
  const parityUseLocalHashButton = document.getElementById('parity-use-local-hash-btn');
  const paritySelectedImagePanel = document.getElementById('parity-selected-image-panel');
  const parityWorkflowJsonInput = document.getElementById('parity-workflow-json');
  const parityAuditButton = document.getElementById('parity-audit-btn');
  const parityStatusPanel = document.getElementById('parity-status-panel');
  const parityStatusTitle = document.getElementById('parity-status-title');
  const parityStatusMessage = document.getElementById('parity-status-message');
  const parityClassificationPanel = document.getElementById('parity-classification-json');
  const parityIssuesPanel = document.getElementById('parity-issues-json');
  const parityExtractedPanel = document.getElementById('parity-extracted-json');
  const parityNormalizedPanel = document.getElementById('parity-normalized-json');
  const parityWorkflowMatchPanel = document.getElementById('parity-workflow-match-json');
  const parityModelVerificationPanel = document.getElementById('parity-model-hash-evidence-json');
  const paritySummaryCard = document.getElementById('parity-summary-card');
  const parityClassificationBadge = document.getElementById('parity-classification-badge');
  const parityReadinessScore = document.getElementById('parity-readiness-score');
  const paritySummaryText = document.getElementById('parity-summary-text');
  const parityActionItemsList = document.getElementById('parity-action-items');
  const parityFieldDiffSection = document.getElementById('parity-field-diff');
  const parityFieldTbody = document.getElementById('parity-field-tbody');
  const parityLoadTemplateButton = document.getElementById('parity-load-template-btn');
  const parityUploadWorkflowButton = document.getElementById('parity-upload-workflow-btn');
  const parityWorkflowFileInput = document.getElementById('parity-workflow-file');
  const parityTemplateSelect = document.getElementById('parity-template-select');
  const inspectionPanels = document.getElementById('inspection-panels');
  const themeToggle = document.getElementById('theme-toggle');
  const preferences = window.AtelierPreferences || null;
  const uiKit = window.AtelierUi || null;
  const imageHashPickerApi = window.AtelierImageHashPicker || null;
  const CATALOG_SETTINGS_STORAGE_KEY = 'atelierai.generationLab.catalogSettings.v1';
  const MAX_INDEXED_LORA_TOKENS = 8;
  const STANDARD_TEMPLATE_TOKENS = [
    'prompt.positive',
    'prompt.negative',
    'sampler.seed',
    'sampler.steps',
    'sampler.cfg',
    'sampler.name',
    'sampler.scheduler',
    'sampler.denoise',
    'image.width',
    'image.height',
    'model.checkpoint_name',
    'model.checkpoint_path',
    'model.lora_name',
    'model.lora_path',
    'model.lora_names',
    'model.lora_paths',
    'model.lora_model_strength',
    'model.lora_model_strengths',
    'model.lora_clip_strength',
    'model.lora_clip_strengths',
    'model.lora.0.name',
    'model.lora.0.path',
    'model.lora.0.model_strength',
    'model.lora.0.clip_strength',
  ];
  const TOKEN_PATH_HINTS = {
    'prompt.positive': ['positive', 'text', 'prompt', 'clip', 'encode'],
    'prompt.negative': ['negative', 'text', 'prompt', 'clip', 'encode'],
    'sampler.seed': ['seed', 'noise_seed', 'ksampler'],
    'sampler.steps': ['steps', 'ksampler'],
    'sampler.cfg': ['cfg', 'guidance', 'scale', 'ksampler'],
    'sampler.name': ['sampler', 'ksampler'],
    'sampler.scheduler': ['scheduler', 'ksampler'],
    'sampler.denoise': ['denoise', 'ksampler'],
    'image.width': ['width', 'latent', 'emptylatent'],
    'image.height': ['height', 'latent', 'emptylatent'],
    'model.checkpoint_name': ['checkpoint', 'ckpt', 'model', 'loadcheckpoint'],
    'model.checkpoint_path': ['checkpoint', 'ckpt', 'model', 'loadcheckpoint'],
    'model.lora_name': ['lora', 'loadlora'],
    'model.lora_path': ['lora', 'loadlora'],
  };
  const TOKEN_VALUE_TYPE_HINTS = {
    'prompt.positive': 'string',
    'prompt.negative': 'string',
    'sampler.seed': 'integer',
    'sampler.steps': 'integer',
    'sampler.cfg': 'number',
    'sampler.name': 'string',
    'sampler.scheduler': 'string',
    'sampler.denoise': 'number',
    'image.width': 'integer',
    'image.height': 'integer',
    'model.checkpoint_name': 'string',
    'model.checkpoint_path': 'string',
    'model.lora_name': 'string',
    'model.lora_path': 'string',
    'model.lora_names': 'json',
    'model.lora_paths': 'json',
    'model.lora_model_strength': 'number',
    'model.lora_model_strengths': 'json',
    'model.lora_clip_strength': 'number',
    'model.lora_clip_strengths': 'json',
    'model.lora.0.model_strength': 'number',
    'model.lora.0.clip_strength': 'number',
    'model.lora.strength': 'number',
  };
  const NODE_TYPE_TOKEN_WIDGET_HINTS = {
    ksampler: {
      'sampler.seed': [0],
      'sampler.steps': [2],
      'sampler.cfg': [3],
      'sampler.name': [4],
      'sampler.scheduler': [5],
      'sampler.denoise': [6],
    },
    ksampleradvanced: {
      'sampler.seed': [0],
      'sampler.steps': [2],
      'sampler.cfg': [3],
      'sampler.name': [4],
      'sampler.scheduler': [5],
      'sampler.denoise': [6],
    },
    cliptextencode: {
      'prompt.positive': [0],
      'prompt.negative': [0],
    },
    checkpointloadersimple: {
      'model.checkpoint_name': [0],
      'model.checkpoint_path': [0],
    },
    loraloader: {
      'model.lora_name': [0],
      'model.lora_path': [0],
      'model.lora.strength': [1, 2],
      'model.lora_model_strength': [1],
      'model.lora_clip_strength': [2],
    },
    loraloaderloramanager: {
      'model.lora_name': [1],
      'model.lora_path': [1],
      'model.lora.strength': [1],
      'model.lora_model_strength': [1],
      'model.lora_clip_strength': [1],
    },
    emptylatentimage: {
      'image.width': [0],
      'image.height': [1],
    },
  };
  const NODE_TITLE_TOKEN_HINTS = {
    cliptextencode: {
      'prompt.positive': ['positive'],
      'prompt.negative': ['negative'],
    },
  };
  const state = {
    activeTabId: 'compare',
    currentInspectionTabIds: [],
    currentPayloads: [],
    currentComfyPayloads: [],
    generationTemplates: [],
    selectedTemplateId: null,
    resolvedTemplateWorkflow: null,
    templateTokenPreview: null,
    templateFieldPaths: [],
    activeTemplateMappingRowId: null,
    a1111BridgeAnalysis: null,
    bridgeDatasetQuality: null,
    generateImageResult: null,
    generateAttempts: [],
    paritySelectedImage: null,
  };
  let parityImageHashPicker = null;
  let inspectionFolderWorkspace = null;

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
    || !exportPanel
    || !exportTitle
    || !exportMessage
    || !copyExportButton
    || !downloadExportButton
    || !copyComfyExportButton
    || !downloadComfyExportButton
    || !copyComfyApiExportButton
    || !downloadComfyApiExportButton
  ) {
    return;
  }

  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  if (templateLabSection instanceof HTMLElement) {
    templateLabSection.hidden = true;
  }
  if (a1111BridgeSection instanceof HTMLElement) {
    a1111BridgeSection.hidden = true;
  }
  if (generateImageSection instanceof HTMLElement) {
    generateImageSection.hidden = true;
  }
  if (parityWorkbenchSection instanceof HTMLElement) {
    parityWorkbenchSection.hidden = true;
  }

  function setFormValues({ civitaiId, fileHash, catalogUrl, checkpointsUrl, lorasUrl, includeFullCatalogRaw }) {
    if (civitaiId !== undefined) {
      civitaiInput.value = String(civitaiId || '');
    }
    if (fileHash !== undefined) {
      localInput.value = String(fileHash || '');
    }
    if (catalogUrl !== undefined) {
      catalogUrlInput.value = String(catalogUrl || '');
    }
    if (checkpointsUrl !== undefined) {
      checkpointsUrlInput.value = String(checkpointsUrl || '');
    }
    if (lorasUrl !== undefined) {
      lorasUrlInput.value = String(lorasUrl || '');
    }
    if (includeFullCatalogRaw !== undefined) {
      includeFullCatalogRawInput.checked = Boolean(includeFullCatalogRaw);
    }
    if (catalogAdvancedOverrides instanceof HTMLDetailsElement) {
      const hasOverrides = Boolean(
        String(checkpointsUrlInput.value || '').trim()
        || String(lorasUrlInput.value || '').trim(),
      );
      catalogAdvancedOverrides.open = hasOverrides;
    }
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

  function getFormState() {
    return {
      civitaiId: civitaiInput.value.trim(),
      fileHash: localInput.value.trim(),
      catalogUrl: catalogUrlInput.value.trim(),
      checkpointsUrl: checkpointsUrlInput.value.trim(),
      lorasUrl: lorasUrlInput.value.trim(),
      includeFullCatalogRaw: includeFullCatalogRawInput.checked,
    };
  }

  function normalizeKey(value) {
    return String(value || '').trim().toLowerCase();
  }

  function encodeRelativeStaticPath(pathValue) {
    return String(pathValue || '')
      .split('/')
      .filter((part) => part.length > 0)
      .map((part) => encodeURIComponent(part))
      .join('/');
  }

  function buildLibraryImageUrl(item) {
    const relativePath = String(item?.file_path || '').trim();
    if (!relativePath) {
      return '';
    }
    const encoded = encodeRelativeStaticPath(relativePath);
    return encoded ? `/image_library/${encoded}` : '';
  }

  function isLikelyImageUrl(value) {
    const text = String(value || '').trim().toLowerCase();
    return Boolean(text) && /(https?:\/\/|^\/)/.test(text);
  }

  function resolveDisplayImageUrl(item) {
    if (!item || typeof item !== 'object') {
      return '';
    }
    const candidates = [
      item.preview_image_url,
      item.display_url,
      item.poster_url,
      item.video_poster_url,
      item.video_thumbnail_url,
      item.image_url,
    ];
    const found = candidates.find((candidate) => isLikelyImageUrl(candidate));
    if (found) {
      return String(found || '').trim();
    }
    return buildLibraryImageUrl(item);
  }

  function setPreviewPanelState(panel, payload) {
    if (!(panel instanceof HTMLElement)) {
      return;
    }
    const imageUrl = String(payload?.imageUrl || '').trim();
    const sourceLabel = String(payload?.sourceLabel || '').trim();
    const sourceUrl = String(payload?.sourceUrl || '').trim();
    const caption = String(payload?.caption || '').trim();
    const details = String(payload?.details || '').trim();

    panel.innerHTML = '';
    panel.classList.remove('is-empty', 'is-error');

    if (!imageUrl) {
      panel.classList.add('is-empty');
      const empty = document.createElement('p');
      empty.className = 'control-preview-empty';
      empty.textContent = details || 'Preview not available.';
      panel.append(empty);
      return;
    }

    const image = document.createElement('img');
    image.className = 'control-preview-image';
    image.src = imageUrl;
    image.alt = caption || 'Preview image';
    image.loading = 'lazy';
    panel.append(image);

    const meta = document.createElement('div');
    meta.className = 'control-preview-meta';
    if (sourceLabel) {
      const label = document.createElement('span');
      label.className = 'control-preview-source';
      label.textContent = sourceLabel;
      meta.append(label);
    }
    if (sourceUrl) {
      const link = document.createElement('a');
      link.href = sourceUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = sourceUrl;
      meta.append(link);
    }
    if (caption) {
      const text = document.createElement('p');
      text.textContent = caption;
      meta.append(text);
    }
    panel.append(meta);
  }

  function setPreviewPanelError(panel, message) {
    if (!(panel instanceof HTMLElement)) {
      return;
    }
    panel.classList.remove('is-empty');
    panel.classList.add('is-error');
    panel.innerHTML = '';
    const error = document.createElement('p');
    error.className = 'control-preview-empty';
    error.textContent = String(message || 'Preview request failed.');
    panel.append(error);
  }

  async function fetchGalleryItems(searchText, limit = 20) {
    const query = String(searchText || '').trim();
    if (!query) {
      return [];
    }
    const params = new URLSearchParams();
    params.set('group_variants', 'false');
    params.set('limit', String(Math.max(1, Math.min(100, Number(limit) || 20))));
    params.set('search', query);
    const response = await fetch(`/images/?${params.toString()}`);
    const payload = await response.json().catch(() => []);
    if (!response.ok) {
      throw new Error(`Gallery query failed with HTTP ${response.status}.`);
    }
    return Array.isArray(payload) ? payload : [];
  }

  async function previewLocalImageByHash() {
    const hash = localInput.value.trim();
    if (!hash) {
      setStatus('is-warning', 'Missing value', 'Enter a local file hash first.');
      setPreviewPanelState(localPreviewPanel, { details: 'Enter a local file hash first.' });
      return;
    }

    if (localPreviewButton instanceof HTMLButtonElement) {
      localPreviewButton.disabled = true;
    }
    try {
      const items = await fetchGalleryItems(hash, 25);
      const normalizedHash = normalizeKey(hash);
      const exact = items.find((item) => normalizeKey(item?.file_hash) === normalizedHash) || null;
      const fallback = items[0] || null;
      const selected = exact || fallback;
      const previewUrl = resolveDisplayImageUrl(selected);
      setPreviewPanelState(localPreviewPanel, {
        imageUrl: previewUrl,
        sourceLabel: 'Local gallery image',
        sourceUrl: selected?.source_url || '',
        caption: selected?.file_name || selected?.original_file_name || hash,
        details: selected ? 'No displayable image URL found for this local item.' : 'No local gallery item found for that hash.',
      });
      if (!selected) {
        setStatus('is-warning', 'Preview unavailable', 'No local gallery item matched that file hash.');
      }
    } catch (error) {
      setPreviewPanelError(localPreviewPanel, error instanceof Error ? error.message : String(error));
      setStatus('is-error', 'Preview failed', error instanceof Error ? error.message : String(error));
    } finally {
      if (localPreviewButton instanceof HTMLButtonElement) {
        localPreviewButton.disabled = false;
      }
    }
  }

  async function previewCivitaiImageById() {
    const imageIdText = civitaiInput.value.trim();
    const imageId = Number(imageIdText);
    if (!imageIdText || !Number.isFinite(imageId) || imageId <= 0) {
      setStatus('is-warning', 'Missing value', 'Enter a CivitAI image ID first.');
      setPreviewPanelState(civitaiPreviewPanel, { details: 'Enter a CivitAI image ID first.' });
      return;
    }

    if (civitaiPreviewButton instanceof HTMLButtonElement) {
      civitaiPreviewButton.disabled = true;
    }
    const expectedSourceUrl = `https://civitai.com/images/${imageId}`;
    try {
      const localItems = await fetchGalleryItems(String(imageId), 40);
      const localCached = localItems.find((item) => {
        const source = String(item?.source_url || '').trim();
        return source === expectedSourceUrl || source === `${expectedSourceUrl}/`;
      }) || null;
      const localPreviewUrl = resolveDisplayImageUrl(localCached);
      if (localCached && localPreviewUrl) {
        setPreviewPanelState(civitaiPreviewPanel, {
          imageUrl: localPreviewUrl,
          sourceLabel: 'Local cache',
          sourceUrl: expectedSourceUrl,
          caption: localCached?.file_name || `CivitAI image ${imageId}`,
        });
        return;
      }

      const remotePayload = await fetchPayload(`/generation-prototype/civitai/${encodeURIComponent(imageId)}`);
      const remotePreviewUrl = String(
        remotePayload?.overview?.image_url
        || remotePayload?.raw?.prepared_import_target?.image_url
        || remotePayload?.raw?.image_data?.url
        || '',
      ).trim();
      setPreviewPanelState(civitaiPreviewPanel, {
        imageUrl: remotePreviewUrl,
        sourceLabel: 'CivitAI remote',
        sourceUrl: expectedSourceUrl,
        caption: `CivitAI image ${imageId}`,
        details: 'No preview image URL was returned for this CivitAI item.',
      });
    } catch (error) {
      setPreviewPanelError(civitaiPreviewPanel, error instanceof Error ? error.message : String(error));
      setStatus('is-error', 'Preview failed', error instanceof Error ? error.message : String(error));
    } finally {
      if (civitaiPreviewButton instanceof HTMLButtonElement) {
        civitaiPreviewButton.disabled = false;
      }
    }
  }

  function setStatus(state, title, message) {
    statusPanel.className = `status-panel ${state}`;
    statusTitle.textContent = title;
    statusMessage.textContent = message;
  }

  function setGenerateStatus(state, title, message) {
    if (generateStatusPanel instanceof HTMLElement) {
      generateStatusPanel.className = `status-panel ${state}`;
    }
    if (generateStatusTitle instanceof HTMLElement) {
      generateStatusTitle.textContent = title;
    }
    if (generateStatusMessage instanceof HTMLElement) {
      generateStatusMessage.textContent = message;
    }
  }

  function setParityStatus(state, title, message) {
    if (parityStatusPanel instanceof HTMLElement) {
      parityStatusPanel.className = `status-panel ${state}`;
    }
    if (parityStatusTitle instanceof HTMLElement) {
      parityStatusTitle.textContent = title;
    }
    if (parityStatusMessage instanceof HTMLElement) {
      parityStatusMessage.textContent = message;
    }
  }

  function setParitySelectedImage(item) {
    state.paritySelectedImage = item && typeof item === 'object' ? item : null;
    if (!(paritySelectedImagePanel instanceof HTMLElement)) {
      return;
    }
    if (!state.paritySelectedImage) {
      setPreviewPanelState(paritySelectedImagePanel, {
        details: 'No active image selected yet.',
      });
      return;
    }
    const previewUrl = resolveDisplayImageUrl(state.paritySelectedImage);
    setPreviewPanelState(paritySelectedImagePanel, {
      imageUrl: previewUrl,
      sourceLabel: 'Active gallery image',
      sourceUrl: state.paritySelectedImage?.source_url || '',
      caption: state.paritySelectedImage?.file_name || state.paritySelectedImage?.original_file_name || state.paritySelectedImage?.file_hash || 'Selected image',
      details: 'No displayable image URL found for this selected item.',
    });
  }

  async function resolveGalleryItemByHash(fileHash) {
    const hash = String(fileHash || '').trim();
    if (!hash) {
      return null;
    }
    const items = await fetchGalleryItems(hash, 30);
    const normalized = normalizeKey(hash);
    return items.find((item) => normalizeKey(item?.file_hash) === normalized) || items[0] || null;
  }

  function applyParitySelectedItem(item) {
    if (!(parityFileHashInput instanceof HTMLInputElement)) {
      return;
    }
    const hash = String(item?.file_hash || '').trim();
    if (!hash) {
      return;
    }
    parityFileHashInput.value = hash;
    setParitySelectedImage(item);
    setParityStatus('is-success', 'Image selected', 'Selected gallery image is now active for parity audit.');
  }

  function ensureParityImageHashPicker() {
    if (parityImageHashPicker || !imageHashPickerApi || typeof imageHashPickerApi.createImageHashPicker !== 'function') {
      return parityImageHashPicker;
    }
    parityImageHashPicker = imageHashPickerApi.createImageHashPicker({
      title: 'Select Reference Gallery Image',
      maxResults: 36,
      onSelect: (item) => {
        applyParitySelectedItem(item);
      },
    });
    return parityImageHashPicker;
  }

  function renderParityAudit(payload) {
    const candidate = payload?.candidate || {};
    const comparison = payload?.comparison || {};
    const extracted = candidate?.parsed_fields || {};
    const normalized = candidate?.canonical_fields || {};
    const issues = {
      missing_required_fields: Array.isArray(candidate?.missing_required_fields) ? candidate.missing_required_fields : [],
      conflicts: Array.isArray(candidate?.conflicts) ? candidate.conflicts : [],
      warnings: Array.isArray(candidate?.warnings) ? candidate.warnings : [],
    };
    const classification = {
      classification: String(candidate?.classification || 'unknown'),
      target: payload?.target || {},
      mapping_notes: Array.isArray(candidate?.mapping_notes) ? candidate.mapping_notes : [],
    };

    const workflowSupplied = Boolean(comparison?.provided_workflow_supplied);
    const semanticBuckets = comparison?.workflow_match_buckets_semantic;
    let workflowMatchBuckets = null;

    if (workflowSupplied
      && semanticBuckets
      && typeof semanticBuckets === 'object') {
      workflowMatchBuckets = {
        provided_workflow_supplied: true,
        source: 'semantic',
        ...semanticBuckets,
      };
    } else if (workflowSupplied) {
      const fieldAlignmentFields = (comparison?.field_alignment && typeof comparison.field_alignment.fields === 'object')
        ? comparison.field_alignment.fields
        : {};
      const matched = [];
      const localOnly = [];
      Object.entries(fieldAlignmentFields).forEach(([fieldName, info]) => {
        const matchCount = Number(info?.match_count || 0);
        const entry = {
          field: String(fieldName || ''),
          value: info?.value,
          match_count: matchCount,
          path_samples: Array.isArray(info?.path_samples) ? info.path_samples : [],
        };
        if (matchCount > 0) {
          matched.push(entry);
        } else {
          localOnly.push(entry);
        }
      });

      const structuralMismatches = Array.isArray(comparison?.structural?.mismatch_samples)
        ? comparison.structural.mismatch_samples
        : [];
      const mismatchSamples = structuralMismatches.map((item) => ({
        path: item?.path,
        expected: item?.right,
        actual: item?.left,
      }));

      workflowMatchBuckets = {
        provided_workflow_supplied: true,
        source: 'legacy_scalar_lookup',
        counts: {
          matched: matched.length,
          mismatched: mismatchSamples.length,
          local_only: localOnly.length,
        },
        matched,
        mismatched: mismatchSamples,
        local_only: localOnly,
      };
    } else {
      workflowMatchBuckets = {
        provided_workflow_supplied: false,
        source: 'none',
        message: 'No workflow JSON was provided, so comparison is unavailable.',
        counts: {
          matched: 0,
          mismatched: 0,
          local_only: 0,
        },
        matched: [],
        mismatched: [],
        local_only: [],
      };
    }

    if (parityClassificationPanel instanceof HTMLElement) {
      parityClassificationPanel.textContent = stringifyJson(classification);
    }
    if (parityIssuesPanel instanceof HTMLElement) {
      parityIssuesPanel.textContent = stringifyJson(issues);
    }
    if (parityExtractedPanel instanceof HTMLElement) {
      parityExtractedPanel.textContent = stringifyJson(extracted);
    }
    if (parityNormalizedPanel instanceof HTMLElement) {
      parityNormalizedPanel.textContent = stringifyJson(normalized);
    }
    if (parityWorkflowMatchPanel instanceof HTMLElement) {
      parityWorkflowMatchPanel.textContent = stringifyJson(workflowMatchBuckets);
    }
    if (parityModelVerificationPanel instanceof HTMLElement) {
      parityModelVerificationPanel.textContent = stringifyJson(comparison?.model_hash_evidence || null);
    }

    // Render summary card
    renderParitySummary(candidate, comparison);

    // Render visual field diff
    renderParityFieldDiff(comparison?.unified_field_status || {});
  }

  function renderParitySummary(candidate, comparison) {
    if (!paritySummaryCard) return;

    const classification = String(candidate?.classification || 'unknown');
    const readinessScore = Number(candidate?.readiness_score ?? 0);
    const actionItems = Array.isArray(candidate?.action_items) ? candidate.action_items : [];
    const missingFields = Array.isArray(candidate?.missing_required_fields) ? candidate.missing_required_fields : [];
    const conflicts = Array.isArray(candidate?.conflicts) ? candidate.conflicts : [];
    const modelVerification = comparison?.model_hash_evidence;

    // Build classification badge
    const classLabel = {
      generatable_now: 'Generatable Now',
      generatable_with_inference: 'Needs Inference',
      needs_manual_intervention: 'Needs Intervention',
      non_generatable_missing_generation_data: 'No Generation Data',
    }[classification] || classification;

    const badgeColors = {
      generatable_now: 'parity-badge-green',
      generatable_with_inference: 'parity-badge-yellow',
      needs_manual_intervention: 'parity-badge-red',
      non_generatable_missing_generation_data: 'parity-badge-gray',
    };
    const badgeClass = badgeColors[classification] || 'parity-badge-gray';

    if (parityClassificationBadge) {
      parityClassificationBadge.textContent = classLabel;
      parityClassificationBadge.className = 'parity-badge ' + badgeClass;
    }
    if (parityReadinessScore) {
      parityReadinessScore.textContent = `${readinessScore}% ready`;
    }

    // Build summary text
    let summaryText = `Classification: ${classLabel}. `;
    if (missingFields.length === 0) {
      summaryText += 'All required fields present. ';
    } else {
      summaryText += `Missing ${missingFields.length} required field(s): ${missingFields.join(', ')}. `;
    }
    if (modelVerification) {
      if (modelVerification.confirmed_exact_match) {
        const tier = modelVerification.confirmation_tier || 'unknown';
        summaryText += `Model hash verified (${tier}). `;
      } else {
        summaryText += 'Model hash not confirmed. ';
      }
    }
    if (conflicts.length > 0) {
      summaryText += `${conflicts.length} conflict(s) detected. `;
    }

    if (paritySummaryText) {
      paritySummaryText.textContent = summaryText;
    }

    // Render action items
    if (parityActionItemsList) {
      parityActionItemsList.innerHTML = '';
      if (actionItems.length === 0) {
        const li = document.createElement('li');
        li.textContent = 'No action items — everything looks good.';
        li.className = 'parity-action-item parity-action-ok';
        parityActionItemsList.appendChild(li);
      } else {
        actionItems.forEach((item) => {
          const li = document.createElement('li');
          li.textContent = String(item);
          li.className = 'parity-action-item';
          parityActionItemsList.appendChild(li);
        });
      }
    }

    paritySummaryCard.style.display = '';
  }

  function renderParityFieldDiff(unifiedFieldStatus) {
    if (!parityFieldDiffSection || !parityFieldTbody) return;

    if (!unifiedFieldStatus || typeof unifiedFieldStatus !== 'object' || Object.keys(unifiedFieldStatus).length === 0) {
      parityFieldDiffSection.style.display = 'none';
      return;
    }

    parityFieldTbody.innerHTML = '';
    const statusIcons = {
      verified: '✅',
      matched: '✅',
      mismatched: '⚠️',
      local_only: '➖',
      workflow_only: '➕',
      not_checked: '—',
    };

    // Friendly field names
    const fieldLabels = {
      prompt_positive: 'Positive Prompt',
      prompt_negative: 'Negative Prompt',
      sampler_name: 'Sampler',
      scheduler_name: 'Scheduler',
      seed: 'Seed',
      steps: 'Steps',
      cfg_scale: 'CFG Scale',
      width: 'Width',
      height: 'Height',
      denoise: 'Denoise',
      clip_skip: 'Clip Skip',
      model: 'Model',
      model_hash: 'Model Hash',
    };

    Object.entries(unifiedFieldStatus).forEach(([fieldName, info]) => {
      const tr = document.createElement('tr');
      const status = String(info?.status || 'not_checked');
      const icon = statusIcons[status] || '—';
      const label = fieldLabels[fieldName] || fieldName;

      const localVal = info?.local_value != null ? truncateFieldValue(String(info.local_value)) : '—';
      const workflowVal = info?.workflow_value != null ? truncateFieldValue(String(info.workflow_value)) : '—';
      const detail = info?.detail || '';

      tr.innerHTML = `
        <td title="${fieldName}">${label}</td>
        <td class="parity-status-${status}">${icon} ${status.replace('_', ' ')}</td>
        <td title="${String(info?.local_value ?? '')}">${localVal}</td>
        <td title="${String(info?.workflow_value ?? '')}">${workflowVal}</td>
        <td>${detail}</td>
      `;
      parityFieldTbody.appendChild(tr);
    });

    parityFieldDiffSection.style.display = '';
  }

  function truncateFieldValue(value, maxLen = 60) {
    if (!value) return '—';
    if (value.length <= maxLen) return value;
    return value.substring(0, maxLen - 3) + '...';
  }

  function buildParityAuditRequestPayload() {
    if (!(parityFileHashInput instanceof HTMLInputElement)) {
      throw new Error('Parity file hash input is unavailable.');
    }
    const fileHash = parityFileHashInput.value.trim();
    if (!fileHash) {
      throw new Error('Enter a local file hash to run audit.');
    }
    let comfyWorkflowJson = null;
    if (parityWorkflowJsonInput instanceof HTMLTextAreaElement && parityWorkflowJsonInput.value.trim()) {
      const parsed = parseJsonText(parityWorkflowJsonInput.value, {
        fieldLabel: 'Workflow JSON',
        fallbackValue: null,
      });
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('Workflow JSON must be an object.');
      }
      comfyWorkflowJson = parsed;
    }

    return {
      file_hash: fileHash,
      comfy_workflow_json: comfyWorkflowJson,
    };
  }

  async function runParityAuditFlow() {
    const requestPayload = buildParityAuditRequestPayload();
    setParityStatus(
      'is-loading',
      'Analyzing',
      'Extracting fields and running audit...'
    );
    const response = await fetch('/generation-audit/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(payload?.detail || `Audit failed with HTTP ${response.status}.`));
    }

    renderParityAudit(payload);
    const classification = String(payload?.candidate?.classification || 'unknown');
    setParityStatus('is-success', 'Analysis complete', `Candidate classification: ${classification}.`);
  }

  function renderGenerateReferencePanel(reference) {
    if (!(generateReferencePanel instanceof HTMLElement)) {
      return;
    }
    generateReferencePanel.innerHTML = '';
    const imageUrl = String(reference?.image_url || '').trim();
    if (!imageUrl) {
      generateReferencePanel.classList.add('is-empty');
      const empty = document.createElement('p');
      empty.className = 'control-preview-empty';
      empty.textContent = 'Reference image appears after a successful generation run.';
      generateReferencePanel.append(empty);
      return;
    }
    generateReferencePanel.classList.remove('is-empty');
    const image = document.createElement('img');
    image.className = 'control-preview-image';
    image.src = imageUrl;
    image.alt = String(reference?.file_name || 'Reference image');
    image.loading = 'lazy';
    generateReferencePanel.append(image);

    const meta = document.createElement('div');
    meta.className = 'control-preview-meta';
    const fileHash = document.createElement('p');
    fileHash.textContent = `File hash: ${String(reference?.file_hash || '').trim() || 'unknown'}`;
    const phash = document.createElement('p');
    phash.textContent = `Reference pHash: ${String(reference?.phash || '').trim() || 'n/a'}`;
    meta.append(fileHash, phash);
    generateReferencePanel.append(meta);
  }

  function renderGenerateOutputs(outputs, bestMatchFilename) {
    if (!(generateResultsGrid instanceof HTMLElement)) {
      return;
    }
    generateResultsGrid.innerHTML = '';
    const rows = Array.isArray(outputs) ? outputs : [];
    if (!rows.length) {
      const empty = document.createElement('p');
      empty.className = 'control-preview-empty';
      empty.textContent = 'No generated outputs yet.';
      generateResultsGrid.append(empty);
      return;
    }

    rows.forEach((output) => {
      const card = document.createElement('article');
      card.className = 'generate-result-card';
      const filename = String(output?.filename || '').trim();
      if (bestMatchFilename && filename && filename === bestMatchFilename) {
        card.classList.add('is-best');
      }

      const imageUrl = String(output?.image_data_url || '').trim();
      if (imageUrl) {
        const image = document.createElement('img');
        image.src = imageUrl;
        image.alt = filename || 'Generated image';
        image.loading = 'lazy';
        card.append(image);
      }

      const meta = document.createElement('div');
      meta.className = 'generate-result-meta';
      const distance = Number(output?.phash?.distance);
      const similarity = Number(output?.phash?.similarity);
      const nodeClassType = String(output?.node_class_type || '').trim();
      const nodeLabel = nodeClassType
        ? `Node: ${String(output?.node_id || 'n/a')} (${nodeClassType})`
        : `Node: ${String(output?.node_id || 'n/a')}`;
      const similarityPercent = Number.isFinite(similarity) ? `${Math.round(similarity * 10000) / 100}%` : 'n/a';
      meta.innerHTML = [
        `<strong>${filename || 'output'}</strong>`,
        nodeLabel,
        `pHash distance: ${Number.isFinite(distance) ? distance : 'n/a'}`,
        `Similarity: ${similarityPercent}`,
      ].join('<br>');
      card.append(meta);

      generateResultsGrid.append(card);
    });
  }

  function renderGenerateImageResponse(payload) {
    state.generateImageResult = payload && typeof payload === 'object' ? payload : null;
    if (generateResponseJsonPanel instanceof HTMLElement) {
      generateResponseJsonPanel.textContent = stringifyJson(payload || {});
    }
    const reference = payload?.reference || {};
    const generated = payload?.generated || {};
    const outputs = Array.isArray(generated?.outputs) ? generated.outputs : [];
    const bestMatchFilename = String(generated?.best_match?.filename || '').trim();
    renderGenerateReferencePanel(reference);
    renderGenerateOutputs(outputs, bestMatchFilename);
  }

  function renderGenerateAttempts(attempts) {
    state.generateAttempts = Array.isArray(attempts) ? attempts : [];
    if (!(generateAttemptsPanel instanceof HTMLElement)) {
      return;
    }
    generateAttemptsPanel.innerHTML = '';
    if (!state.generateAttempts.length) {
      const empty = document.createElement('p');
      empty.className = 'control-preview-empty';
      empty.textContent = 'No attempts recorded yet for this reference hash.';
      generateAttemptsPanel.append(empty);
      return;
    }

    state.generateAttempts.forEach((attempt) => {
      const row = document.createElement('article');
      row.className = 'generate-attempt-row';
      if (Boolean(attempt?.fundamental_generation_issue)) {
        row.classList.add('is-issue');
      }

      const similarity = Number(attempt?.best_similarity);
      const threshold = Number(attempt?.threshold_used);
      const similarityText = Number.isFinite(similarity)
        ? `${Math.round(similarity * 10000) / 100}%`
        : 'n/a';
      const thresholdText = Number.isFinite(threshold)
        ? `${Math.round(threshold * 10000) / 100}%`
        : 'n/a';
      const statusText = Boolean(attempt?.matched) ? 'Matched' : 'Not Matched';
      const issueText = Boolean(attempt?.fundamental_generation_issue)
        ? 'Fundamental Generation Issue'
        : 'No Fundamental Issue';

      const title = document.createElement('strong');
      title.textContent = `Attempt #${String(attempt?.attempt_index || '?')} - ${statusText}`;
      row.append(title);

      const meta = document.createElement('div');
      meta.className = 'generate-attempt-meta';
      meta.innerHTML = [
        `Similarity: ${similarityText}`,
        `Threshold: ${thresholdText}`,
        `Issue: ${issueText}`,
        `Prompt ID: ${String(attempt?.comfy_prompt_id || 'n/a')}`,
        `Tweak: ${String(attempt?.tweak_label || 'none')}`,
      ].join('<br>');
      row.append(meta);

      generateAttemptsPanel.append(row);
    });
  }

  async function loadGenerateAttempts(referenceFileHash) {
    const hash = String(referenceFileHash || '').trim();
    if (!hash) {
      renderGenerateAttempts([]);
      return;
    }
    const params = new URLSearchParams({
      reference_file_hash: hash,
      limit: '30',
      offset: '0',
    });
    const response = await fetch(`/generation-prototype/comfy/attempts?${params.toString()}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(payload?.detail || `Attempt history request failed with HTTP ${response.status}.`));
    }
    renderGenerateAttempts(Array.isArray(payload?.attempts) ? payload.attempts : []);
  }

  function buildGenerateImageRequestPayload() {
    if (!(generateWorkflowJsonInput instanceof HTMLTextAreaElement)) {
      throw new Error('Generate workflow JSON input is unavailable.');
    }
    if (!(generateReferenceFileHashInput instanceof HTMLInputElement)) {
      throw new Error('Reference file hash input is unavailable.');
    }

    const workflowJson = parseJsonText(generateWorkflowJsonInput.value, {
      fieldLabel: 'Generate workflow JSON',
      fallbackValue: null,
    });
    if (!workflowJson || typeof workflowJson !== 'object' || Array.isArray(workflowJson)) {
      throw new Error('Generate workflow JSON must be an object.');
    }

    const referenceFileHash = generateReferenceFileHashInput.value.trim();
    if (!referenceFileHash) {
      throw new Error('Enter a reference local file hash before generating.');
    }

    const thresholdOverride = generateThresholdOverrideInput instanceof HTMLInputElement
      ? Number(generateThresholdOverrideInput.value)
      : Number.NaN;
    const tweakLabel = generateTweakLabelInput instanceof HTMLInputElement
      ? generateTweakLabelInput.value.trim()
      : '';
    let tweakedParameters = {};
    if (generateTweakedParametersInput instanceof HTMLTextAreaElement
      && generateTweakedParametersInput.value.trim()) {
      const parsedTweaks = parseJsonText(generateTweakedParametersInput.value, {
        fieldLabel: 'Tweaked parameters JSON',
        fallbackValue: {},
      });
      if (!parsedTweaks || typeof parsedTweaks !== 'object' || Array.isArray(parsedTweaks)) {
        throw new Error('Tweaked parameters JSON must be an object.');
      }
      tweakedParameters = parsedTweaks;
    }

    return {
      workflow_json: workflowJson,
      reference_file_hash: referenceFileHash,
      include_all_workspace_images: Boolean(generateIncludeAllImagesInput instanceof HTMLInputElement && generateIncludeAllImagesInput.checked),
      match_threshold_override: Number.isFinite(thresholdOverride) ? Math.max(0, Math.min(1, thresholdOverride)) : null,
      tweak_label: tweakLabel || null,
      tweaked_parameters: tweakedParameters,
    };
  }

  async function runGenerateImageFlow() {
    const requestPayload = buildGenerateImageRequestPayload();
    setGenerateStatus('is-loading', 'Submitting', 'Submitting prompt to ComfyUI and waiting for completion...');
    const response = await fetch('/generation-prototype/comfy/generate-and-compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(payload?.detail || `Comfy generation failed with HTTP ${response.status}.`));
    }

    renderGenerateImageResponse(payload);
    await loadGenerateAttempts(String(requestPayload.reference_file_hash || ''));
    const outputCount = Number(payload?.generated?.count || 0);
    const bestSimilarity = Number(payload?.generated?.best_match?.phash?.similarity);
    const closeEnoughThreshold = Number(payload?.generated?.close_enough_threshold);
    const thresholdPercent = Number.isFinite(closeEnoughThreshold)
      ? `${Math.round(closeEnoughThreshold * 10000) / 100}%`
      : '95%';
    const closeEnough = Boolean(payload?.generated?.close_enough);
    const bestSimilarityText = Number.isFinite(bestSimilarity)
      ? `${Math.round(bestSimilarity * 10000) / 100}%`
      : 'n/a';
    const closeEnoughText = closeEnough
      ? `Image Matched (${thresholdPercent}+): yes`
      : `Image Matched (${thresholdPercent}+): no`;
    setGenerateStatus(
      payload?.ok && closeEnough ? 'is-success' : 'is-warning',
      payload?.ok
        ? (closeEnough ? 'Generation complete (Matched)' : 'Generation complete (Not Matched)')
        : 'Generation returned partial results',
      `Received ${outputCount} output(s). Best pHash similarity: ${bestSimilarityText}. ${closeEnoughText}.`,
    );
  }

  function toPercentLabel(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return '0%';
    }
    return `${Math.max(0, Math.min(100, Math.round(numeric * 100) / 100))}%`;
  }

  function setBridgeDatasetReadinessBadge({ ready, confidence }) {
    if (!(bridgeDatasetReadiness instanceof HTMLElement)) {
      return;
    }
    const confidenceText = String(confidence || '').trim().toLowerCase();
    const isReady = Boolean(ready);
    const badgeClass = isReady
      ? 'badge-ok'
      : confidenceText === 'moderate'
        ? 'badge-warning'
        : 'badge-error';
    bridgeDatasetReadiness.className = `badge ${badgeClass}`;
    bridgeDatasetReadiness.textContent = isReady ? 'Ready' : 'Not Ready';
  }

  function renderBridgeDatasetQuality(payload) {
    state.bridgeDatasetQuality = payload && typeof payload === 'object' ? payload : null;
    const summary = payload && typeof payload === 'object' ? (payload.summary || {}) : {};
    const coverage = payload && typeof payload === 'object' ? (payload.coverage || {}) : {};
    const qualityIssues = payload && typeof payload === 'object' ? (payload.quality_issues || {}) : {};
    const readiness = payload && typeof payload === 'object' ? (payload.inference_readiness || {}) : {};

    const uniqueSamples = Number(summary.unique_record_count || 0);
    const duplicateCount = Number(summary.duplicate_count_estimate || 0);
    const recordCount = Number(summary.loaded_record_count || 0);
    const coreCoveragePercent = Number(coverage.core_fields_aggregate_coverage_percent || 0);
    const missingSeedPercent = Number((qualityIssues.missing_seed || {}).percent || 0);
    const missingModelPercent = Number((qualityIssues.missing_model_identity || {}).percent || 0);
    const confidence = String(readiness.confidence || 'unknown');
    const ready = Boolean(readiness.reliable_for_process_inference);
    const suggestedBasic = Number(readiness.recommended_min_samples_basic || 50);

    if (bridgeDatasetSummary instanceof HTMLElement) {
      bridgeDatasetSummary.textContent = `${uniqueSamples} unique samples (${recordCount} loaded, ${duplicateCount} duplicate estimate). Suggested minimum: ${suggestedBasic} samples.`;
    }
    setBridgeDatasetReadinessBadge({ ready, confidence });

    if (bridgeDatasetConfidence instanceof HTMLElement) {
      bridgeDatasetConfidence.textContent = confidence.charAt(0).toUpperCase() + confidence.slice(1);
    }
    if (bridgeDatasetSamples instanceof HTMLElement) {
      bridgeDatasetSamples.textContent = String(uniqueSamples);
    }
    if (bridgeDatasetCoreCoverage instanceof HTMLElement) {
      bridgeDatasetCoreCoverage.textContent = toPercentLabel(coreCoveragePercent);
    }
    if (bridgeDatasetMissingSeed instanceof HTMLElement) {
      bridgeDatasetMissingSeed.textContent = toPercentLabel(missingSeedPercent);
    }
    if (bridgeDatasetMissingModel instanceof HTMLElement) {
      bridgeDatasetMissingModel.textContent = toPercentLabel(missingModelPercent);
    }
  }

  async function loadBridgeDatasetQuality() {
    if (bridgeDatasetRefreshButton instanceof HTMLButtonElement) {
      bridgeDatasetRefreshButton.disabled = true;
    }
    try {
      const response = await fetch('/generation-prototype/a1111-bridge/dataset-quality');
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(String(payload?.detail || `Bridge dataset quality failed with HTTP ${response.status}.`));
      }
      renderBridgeDatasetQuality(payload);
    } catch (error) {
      state.bridgeDatasetQuality = null;
      if (bridgeDatasetSummary instanceof HTMLElement) {
        bridgeDatasetSummary.textContent = error instanceof Error
          ? error.message
          : String(error);
      }
      setBridgeDatasetReadinessBadge({ ready: false, confidence: 'low' });
      if (bridgeDatasetConfidence instanceof HTMLElement) {
        bridgeDatasetConfidence.textContent = 'Unavailable';
      }
      if (bridgeDatasetSamples instanceof HTMLElement) {
        bridgeDatasetSamples.textContent = '0';
      }
      if (bridgeDatasetCoreCoverage instanceof HTMLElement) {
        bridgeDatasetCoreCoverage.textContent = '0%';
      }
      if (bridgeDatasetMissingSeed instanceof HTMLElement) {
        bridgeDatasetMissingSeed.textContent = '0%';
      }
      if (bridgeDatasetMissingModel instanceof HTMLElement) {
        bridgeDatasetMissingModel.textContent = '0%';
      }
    } finally {
      if (bridgeDatasetRefreshButton instanceof HTMLButtonElement) {
        bridgeDatasetRefreshButton.disabled = false;
      }
    }
  }

  function getPayloadByMode() {
    return new Map(state.currentPayloads.map((payload) => [String(payload?.mode || ''), payload]));
  }

  function getComfyPayloadByMode() {
    return new Map(state.currentComfyPayloads.map((payload) => [String(payload?.mode || ''), payload]));
  }

  function buildCatalogQueryString(formState) {
    const params = new URLSearchParams();
    if (formState.catalogUrl) {
      params.set('catalog_url', formState.catalogUrl);
    }
    if (formState.checkpointsUrl) {
      params.set('checkpoints_url', formState.checkpointsUrl);
    }
    if (formState.lorasUrl) {
      params.set('loras_url', formState.lorasUrl);
    }
    if (formState.includeFullCatalogRaw) {
      params.set('include_full_catalog_raw', 'true');
    }
    return params.toString();
  }

  function stringifyJson(value) {
    return JSON.stringify(value, null, 2);
  }

  function parseJsonText(text, { fieldLabel, fallbackValue }) {
    const raw = String(text || '').trim();
    if (!raw) {
      return fallbackValue;
    }
    try {
      return JSON.parse(raw);
    } catch (error) {
      throw new Error(`${fieldLabel} is not valid JSON.`);
    }
  }

  function attachJsonFileDrop(textarea, { fieldLabel, onLoaded } = {}) {
    if (!(textarea instanceof HTMLTextAreaElement)) {
      return;
    }

    const label = String(fieldLabel || 'JSON');
    let dragDepth = 0;

    const setDragState = (active) => {
      textarea.classList.toggle('is-dragover', Boolean(active));
    };

    textarea.addEventListener('dragenter', (event) => {
      event.preventDefault();
      dragDepth += 1;
      setDragState(true);
    });

    textarea.addEventListener('dragover', (event) => {
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = 'copy';
      }
      setDragState(true);
    });

    textarea.addEventListener('dragleave', (event) => {
      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) {
        setDragState(false);
      }
    });

    textarea.addEventListener('drop', async (event) => {
      event.preventDefault();
      dragDepth = 0;
      setDragState(false);

      const files = Array.from(event.dataTransfer?.files || []);
      const file = files[0] || null;
      if (!file) {
        return;
      }

      try {
        const text = await file.text();
        const parsed = parseJsonText(text, {
          fieldLabel: `${label} file`,
          fallbackValue: {},
        });
        textarea.value = stringifyJson(parsed);
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        if (typeof onLoaded === 'function') {
          onLoaded(parsed, file);
        }
        setStatus('is-success', `${label} loaded`, `Loaded ${file.name} via drag and drop.`);
      } catch (error) {
        setStatus('is-error', `${label} load failed`, error instanceof Error ? error.message : String(error));
      }
    });
  }

  function downloadJsonBlob(data, filename) {
    const jsonText = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonText], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  function createDefaultTemplateMappingRow(seed = {}) {
    return {
      token: String(seed.token || '').trim(),
      target_path: String(seed.target_path || '').trim(),
      required: seed.required !== false,
      value_type: String(seed.value_type || 'auto').trim() || 'auto',
      default_value: seed.default_value !== undefined ? seed.default_value : null,
    };
  }

  function buildIndexedLoraTokens(maxIndex = MAX_INDEXED_LORA_TOKENS) {
    const tokens = [];
    for (let index = 0; index < maxIndex; index += 1) {
      tokens.push(
        `model.lora.${index}.name`,
        `model.lora.${index}.path`,
        `model.lora.${index}.model_strength`,
        `model.lora.${index}.clip_strength`,
      );
    }
    return tokens;
  }

  function escapeHtml(text) {
    return String(text || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeNodeType(nodeType) {
    return String(nodeType || '').trim().toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function canonicalizeTemplateToken(token) {
    let value = String(token || '').trim().toLowerCase();
    value = value.replace(/^step\.\d+\./, '');
    value = value.replace(/^model\.lora\.\d+\.name$/, 'model.lora_name');
    value = value.replace(/^model\.lora\.\d+\.path$/, 'model.lora_path');
    value = value.replace(/^model\.lora\.\d+\.strength$/, 'model.lora.strength');
    value = value.replace(/^model\.lora\.\d+\.model_strength$/, 'model.lora_model_strength');
    value = value.replace(/^model\.lora\.\d+\.clip_strength$/, 'model.lora_clip_strength');
    return value;
  }

  function inferTokenValueType(token) {
    const normalized = canonicalizeTemplateToken(token);
    if (TOKEN_VALUE_TYPE_HINTS[normalized]) {
      return TOKEN_VALUE_TYPE_HINTS[normalized];
    }
    if (/^model\.lora\.\d+\.(model_strength|clip_strength)$/i.test(String(token || '').trim())) {
      return 'number';
    }
    if (/^model\.lora\.\d+\.(name|path)$/i.test(String(token || '').trim())) {
      return 'string';
    }
    return null;
  }

  function buildNodePathPrefix(node) {
    if (!node || typeof node !== 'object') {
      return null;
    }
    const nodeId = node.id;
    if (nodeId === null || nodeId === undefined || nodeId === '') {
      return null;
    }
    return `nodes[id=${nodeId}]`;
  }

  function appendLeafPaths(target, value, pathPrefix, depth = 0) {
    if (!pathPrefix || depth > 4) {
      return;
    }
    if (value === null || value === undefined) {
      return;
    }
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      target.push(pathPrefix);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => {
        appendLeafPaths(target, item, `${pathPrefix}[${index}]`, depth + 1);
      });
      return;
    }
    if (typeof value === 'object') {
      Object.entries(value).forEach(([key, item]) => {
        appendLeafPaths(target, item, `${pathPrefix}.${key}`, depth + 1);
      });
    }
  }

  function collectTemplateFieldPaths(workflowJson) {
    const nodes = Array.isArray(workflowJson?.nodes) ? workflowJson.nodes : [];
    const paths = [];
    nodes.forEach((node) => {
      if (!node || typeof node !== 'object') {
        return;
      }
      const prefix = buildNodePathPrefix(node);
      if (!prefix) {
        return;
      }
      const nodeTypeRaw = String(node.class_type || node.type || node.title || 'node');
      const nodeType = normalizeNodeType(nodeTypeRaw);
      const nodeTitle = String(node.title || node._meta?.title || '').trim();
      const candidatePaths = [];
      appendLeafPaths(candidatePaths, node.widgets_values, `${prefix}.widgets_values`);
      if (Array.isArray(node.inputs)) {
        node.inputs.forEach((input, inputIndex) => {
          if (!input || typeof input !== 'object') {
            return;
          }
          const inputName = String(input.name || input.label || '').trim();
          if (inputName) {
            appendLeafPaths(candidatePaths, input, `${prefix}.inputs[${inputIndex}]`);
          }
        });
      }

      candidatePaths.forEach((path) => {
        const normalizedPath = String(path || '').replace(/\.\[(\d+)\]/g, '[$1]');
        if (!normalizedPath) {
          return;
        }
        const widgetMatch = normalizedPath.match(/widgets_values\[(\d+)\]/i);
        const widgetIndex = widgetMatch ? Number(widgetMatch[1]) : null;
        const leafKeyMatch = normalizedPath.match(/\.([a-zA-Z_][a-zA-Z0-9_]*)$/);
        const leafKey = leafKeyMatch ? leafKeyMatch[1] : '';
        paths.push({
          path: normalizedPath,
          node_id: node.id,
          node_type_raw: nodeTypeRaw,
          node_type: nodeType,
          node_title: nodeTitle,
          widget_index: Number.isFinite(widgetIndex) ? widgetIndex : null,
          leaf_key: leafKey,
          search_text: `${normalizedPath} ${nodeTypeRaw} ${nodeTitle} ${leafKey}`,
        });
      });
    });

    const dedup = new Map();
    paths.forEach((entry) => {
      const key = String(entry.path || '').trim();
      if (!key || dedup.has(key)) {
        return;
      }
      dedup.set(key, entry);
    });
    return Array.from(dedup.values());
  }

  function getTokenPathHints(token) {
    const normalized = canonicalizeTemplateToken(token);
    if (!normalized) {
      return [];
    }
    if (TOKEN_PATH_HINTS[normalized]) {
      return TOKEN_PATH_HINTS[normalized];
    }
    if (normalized.includes('lora')) {
      if (normalized.includes('clip_strength')) {
        return ['lora', 'clipstrength', 'clip_weight', 'clip'];
      }
      if (normalized.includes('model_strength') || normalized.includes('strength')) {
        return ['lora', 'strength', 'weight', 'model'];
      }
      return ['lora', 'loadlora'];
    }
    if (normalized.includes('checkpoint') || normalized.includes('ckpt')) {
      return ['checkpoint', 'ckpt', 'model'];
    }
    if (normalized.includes('prompt')) {
      return ['prompt', 'text', 'clip'];
    }
    if (normalized.includes('seed')) {
      return ['seed', 'ksampler'];
    }
    return normalized.split('.').filter(Boolean);
  }

  function getNodeTokenWidgetHints(nodeType, token) {
    const normalizedType = normalizeNodeType(nodeType);
    const normalizedToken = canonicalizeTemplateToken(token);
    const tokenMap = NODE_TYPE_TOKEN_WIDGET_HINTS[normalizedType] || null;
    if (!tokenMap) {
      return [];
    }
    return Array.isArray(tokenMap[normalizedToken]) ? tokenMap[normalizedToken] : [];
  }

  function getNodeTitleTokenHints(nodeType, token) {
    const normalizedType = normalizeNodeType(nodeType);
    const normalizedToken = canonicalizeTemplateToken(token);
    const nodeMap = NODE_TITLE_TOKEN_HINTS[normalizedType] || null;
    if (!nodeMap) {
      return [];
    }
    return Array.isArray(nodeMap[normalizedToken]) ? nodeMap[normalizedToken] : [];
  }

  function scoreFieldCandidateForToken(token, candidate) {
    const path = String(candidate?.path || '').toLowerCase();
    const searchText = String(candidate?.search_text || path).toLowerCase();
    const hints = getTokenPathHints(token);
    const widgetHints = getNodeTokenWidgetHints(candidate?.node_type, token);
    const titleHints = getNodeTitleTokenHints(candidate?.node_type, token);
    const title = String(candidate?.node_title || '').toLowerCase();
    const widgetIndex = Number.isFinite(candidate?.widget_index) ? candidate.widget_index : null;
    const leafKey = String(candidate?.leaf_key || '').toLowerCase();
    const normalizedToken = canonicalizeTemplateToken(token);
    if (!path) {
      return 0;
    }

    let score = 0;
    hints.forEach((hint) => {
      const key = String(hint || '').trim().toLowerCase();
      if (!key) {
        return;
      }
      if (path.includes(key)) {
        score += 12;
      }
      if (searchText.includes(key)) {
        score += 4;
      }
    });

    if (path.includes('widgets_values')) {
      score += 4;
    }

    if (normalizedToken.includes('lora_clip_strength')) {
      if (leafKey === 'clipstrength' || leafKey === 'clip_weight') {
        score += 30;
      } else if (leafKey === 'strength') {
        score -= 6;
      }
    }
    if (normalizedToken.includes('lora_model_strength') || normalizedToken.endsWith('lora.strength')) {
      if (leafKey === 'strength' || leafKey === 'weight') {
        score += 30;
      } else if (leafKey === 'clipstrength' || leafKey === 'clip_weight') {
        score -= 6;
      }
    }

    if (widgetHints.length && widgetIndex !== null) {
      if (widgetHints.includes(widgetIndex)) {
        score += 42;
      } else {
        score -= 7;
      }
    }

    if (titleHints.length) {
      const titleMatched = titleHints.some((hint) => title.includes(String(hint || '').toLowerCase()));
      if (titleMatched) {
        score += 12;
      } else if (title) {
        score -= 3;
      }
    }

    if (path.endsWith('[0]')) {
      score += 2;
    }
    if (String(token || '').toLowerCase().includes('negative') && !path.includes('negative')) {
      score -= 3;
    }
    if (String(token || '').toLowerCase().includes('positive') && !path.includes('positive')) {
      score -= 2;
    }
    return score;
  }

  function classifyCandidateConfidence(token, candidate, score) {
    const widgetHints = getNodeTokenWidgetHints(candidate?.node_type, token);
    const widgetIndex = Number.isFinite(candidate?.widget_index) ? candidate.widget_index : null;
    const deterministicMatch = widgetHints.length > 0 && widgetIndex !== null && widgetHints.includes(widgetIndex);

    if (deterministicMatch) {
      return {
        level: 'high',
        label: 'High (deterministic)',
      };
    }

    if (score >= 28) {
      return { level: 'high', label: 'High' };
    }
    if (score >= 14) {
      return { level: 'medium', label: 'Medium' };
    }
    return { level: 'low', label: 'Low' };
  }

  function getActiveMappingRow() {
    if (!(templateMappingsBody instanceof HTMLElement) || !state.activeTemplateMappingRowId) {
      return null;
    }
    return templateMappingsBody.querySelector(`tr[data-mapping-row-id="${state.activeTemplateMappingRowId}"]`);
  }

  function getActiveMappingToken() {
    const row = getActiveMappingRow();
    if (!(row instanceof HTMLElement)) {
      return '';
    }
    const tokenInput = row.querySelector('[data-mapping-field="token"]');
    return tokenInput instanceof HTMLInputElement ? tokenInput.value.trim() : '';
  }

  function setActiveMappingRow(row) {
    if (!(templateMappingsBody instanceof HTMLElement)) {
      return;
    }
    templateMappingsBody.querySelectorAll('tr').forEach((item) => item.classList.remove('is-active'));
    if (!(row instanceof HTMLTableRowElement)) {
      state.activeTemplateMappingRowId = null;
      renderTemplateFieldCandidates();
      return;
    }
    row.classList.add('is-active');
    state.activeTemplateMappingRowId = String(row.dataset.mappingRowId || '');
    renderTemplateFieldCandidates();
  }

  function refreshTemplateFieldPathsFromWorkflow() {
    if (!(templateWorkflowJsonInput instanceof HTMLTextAreaElement)) {
      state.templateFieldPaths = [];
      renderTemplateFieldCandidates();
      return;
    }
    try {
      const workflowJson = parseJsonText(templateWorkflowJsonInput.value, {
        fieldLabel: 'Workspace JSON',
        fallbackValue: {},
      });
      state.templateFieldPaths = collectTemplateFieldPaths(workflowJson);
    } catch {
      state.templateFieldPaths = [];
    }
    renderTemplateFieldCandidates();
  }

  function buildAvailableTemplateTokens() {
    const tokenSet = new Set([
      ...STANDARD_TEMPLATE_TOKENS,
      ...buildIndexedLoraTokens(),
    ]);
    const previewTokens = state.templateTokenPreview?.tokens || {};
    Object.keys(previewTokens).forEach((token) => {
      if (token) {
        tokenSet.add(String(token));
      }
    });
    return Array.from(tokenSet).sort((left, right) => left.localeCompare(right));
  }

  function renderTemplateTokenPicker() {
    if (!(templateTokenPicker instanceof HTMLSelectElement)) {
      return;
    }
    const activeToken = getActiveMappingToken();
    const previous = templateTokenPicker.value || activeToken;
    const tokens = buildAvailableTemplateTokens();
    templateTokenPicker.innerHTML = '';
    tokens.forEach((token) => {
      const option = document.createElement('option');
      option.value = token;
      option.textContent = token;
      templateTokenPicker.append(option);
    });
    const preferred = previous && tokens.includes(previous) ? previous : (tokens[0] || '');
    templateTokenPicker.value = preferred;
  }

  function renderTemplateFieldCandidates() {
    if (!(templateFieldCandidates instanceof HTMLElement)) {
      return;
    }
    templateFieldCandidates.innerHTML = '';
    const token = getActiveMappingToken() || (templateTokenPicker instanceof HTMLSelectElement ? templateTokenPicker.value : '');
    const query = templateFieldFilterInput instanceof HTMLInputElement
      ? templateFieldFilterInput.value.trim().toLowerCase()
      : '';

    const ranked = state.templateFieldPaths
      .filter((item) => {
        if (!query) {
          return true;
        }
        const haystack = String(item.search_text || item.path || '').toLowerCase();
        return haystack.includes(query);
      })
      .map((item) => ({
        ...item,
        score: scoreFieldCandidateForToken(token, item),
        confidence: classifyCandidateConfidence(token, item, scoreFieldCandidateForToken(token, item)),
      }))
      .sort((left, right) => right.score - left.score || String(left.path).localeCompare(String(right.path)));

    if (!ranked.length) {
      const empty = document.createElement('div');
      empty.className = 'template-field-empty';
      empty.textContent = 'No candidate paths found. Load valid workflow JSON and adjust search.';
      templateFieldCandidates.append(empty);
      return;
    }

    ranked.slice(0, 120).forEach((item) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'template-field-candidate';
      button.dataset.targetPath = String(item.path || '');
      const widgetLabel = Number.isFinite(item.widget_index) ? `widget ${item.widget_index}` : 'nested';
      const titleLabel = item.node_title ? ` · ${escapeHtml(item.node_title)}` : '';
      button.innerHTML = `${escapeHtml(item.path)}<small>node ${escapeHtml(item.node_id)} · ${escapeHtml(item.node_type_raw || item.node_type)} · ${widgetLabel}${titleLabel} · score ${item.score} · <span class="template-confidence template-confidence-${escapeHtml(item.confidence?.level || 'low')}">${escapeHtml(item.confidence?.label || 'Low')}</span></small>`;
      templateFieldCandidates.append(button);
    });
  }

  function applyTokenToActiveRow(token) {
    const row = getActiveMappingRow();
    if (!(row instanceof HTMLElement)) {
      throw new Error('Select a mapping row first.');
    }
    const tokenInput = row.querySelector('[data-mapping-field="token"]');
    const valueTypeSelect = row.querySelector('[data-mapping-field="value_type"]');
    if (!(tokenInput instanceof HTMLInputElement)) {
      throw new Error('Token field is unavailable on selected row.');
    }
    tokenInput.value = String(token || '').trim();
    if (valueTypeSelect instanceof HTMLSelectElement) {
      const suggestedType = inferTokenValueType(token);
      if (suggestedType && Array.from(valueTypeSelect.options).some((item) => item.value === suggestedType)) {
        valueTypeSelect.value = suggestedType;
      }
    }
    tokenInput.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function applyTargetPathToActiveRow(targetPath) {
    const row = getActiveMappingRow();
    if (!(row instanceof HTMLElement)) {
      throw new Error('Select a mapping row first.');
    }
    const pathInput = row.querySelector('[data-mapping-field="target_path"]');
    if (!(pathInput instanceof HTMLInputElement)) {
      throw new Error('Target path field is unavailable on selected row.');
    }
    pathInput.value = String(targetPath || '').trim();
    pathInput.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function applyBestGuessPathToActiveRow() {
    const token = getActiveMappingToken() || (templateTokenPicker instanceof HTMLSelectElement ? templateTokenPicker.value : '');
    if (!token) {
      throw new Error('Choose or enter a token first.');
    }
    const ranked = state.templateFieldPaths
      .map((item) => {
        const score = scoreFieldCandidateForToken(token, item);
        return {
          ...item,
          score,
          confidence: classifyCandidateConfidence(token, item, score),
        };
      })
      .sort((left, right) => right.score - left.score || String(left.path).localeCompare(String(right.path)));
    const best = ranked.find((item) => item.score > 0) || ranked[0] || null;
    if (!best) {
      throw new Error('No candidate paths are available for this workflow JSON.');
    }
    applyTargetPathToActiveRow(best.path);
    renderTemplateFieldCandidates();
    return best;
  }

  function serializeDefaultValueForInput(value) {
    if (value === undefined || value === null) {
      return '';
    }
    if (typeof value === 'string') {
      return value;
    }
    return JSON.stringify(value);
  }

  function parseDefaultValueInput(rawValue) {
    const text = String(rawValue || '').trim();
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }

  function renderTemplateMappingRows(mappings = [], options = {}) {
    if (!(templateMappingsBody instanceof HTMLElement)) {
      return;
    }
    const preferredActiveRowId = options.activeRowId !== undefined
      ? String(options.activeRowId || '')
      : String(state.activeTemplateMappingRowId || '');
    const selectLastRow = Boolean(options.selectLastRow);
    templateMappingsBody.innerHTML = '';
    const rows = Array.isArray(mappings) && mappings.length ? mappings : [createDefaultTemplateMappingRow()];
    rows.forEach((mapping, index) => {
      const row = document.createElement('tr');
      row.dataset.mappingRowId = String(index + 1);
      row.innerHTML = `
        <td><input data-mapping-field="token" type="text" value="${escapeHtml(String(mapping.token || ''))}" placeholder="prompt.positive"></td>
        <td><input data-mapping-field="target_path" type="text" value="${escapeHtml(String(mapping.target_path || ''))}" placeholder="nodes[id=6].widgets_values[0]"></td>
        <td>
          <select data-mapping-field="value_type">
            <option value="auto">auto</option>
            <option value="string">string</option>
            <option value="integer">integer</option>
            <option value="number">number</option>
            <option value="boolean">boolean</option>
            <option value="json">json</option>
          </select>
        </td>
        <td><input data-mapping-field="required" type="checkbox"></td>
        <td><input data-mapping-field="default_value" type="text" value="${escapeHtml(String(serializeDefaultValueForInput(mapping.default_value)))}" placeholder="optional"></td>
        <td>
          <div class="template-row-actions">
            <button type="button" data-template-action="guess-row">Guess</button>
            <button type="button" data-template-action="remove-row">Remove</button>
          </div>
        </td>
      `;
      const valueTypeSelect = row.querySelector('[data-mapping-field="value_type"]');
      const requiredInput = row.querySelector('[data-mapping-field="required"]');
      if (valueTypeSelect instanceof HTMLSelectElement) {
        valueTypeSelect.value = String(mapping.value_type || 'auto');
      }
      if (requiredInput instanceof HTMLInputElement) {
        requiredInput.checked = mapping.required !== false;
      }
      templateMappingsBody.append(row);
    });
    const resolvedActiveRow = selectLastRow
      ? templateMappingsBody.querySelector('tr:last-child')
      : templateMappingsBody.querySelector(`tr[data-mapping-row-id="${preferredActiveRowId}"]`) || templateMappingsBody.querySelector('tr');
    setActiveMappingRow(resolvedActiveRow);
    renderTemplateTokenPicker();
  }

  function readTemplateMappingsFromTable() {
    if (!(templateMappingsBody instanceof HTMLElement)) {
      return [];
    }
    const rows = Array.from(templateMappingsBody.querySelectorAll('tr'));
    return rows
      .map((row) => {
        const tokenInput = row.querySelector('[data-mapping-field="token"]');
        const targetInput = row.querySelector('[data-mapping-field="target_path"]');
        const valueTypeSelect = row.querySelector('[data-mapping-field="value_type"]');
        const requiredInput = row.querySelector('[data-mapping-field="required"]');
        const defaultValueInput = row.querySelector('[data-mapping-field="default_value"]');
        const token = tokenInput instanceof HTMLInputElement ? tokenInput.value.trim() : '';
        const targetPath = targetInput instanceof HTMLInputElement ? targetInput.value.trim() : '';
        return {
          token,
          target_path: targetPath,
          value_type: valueTypeSelect instanceof HTMLSelectElement ? valueTypeSelect.value : 'auto',
          required: requiredInput instanceof HTMLInputElement ? requiredInput.checked : true,
          default_value: defaultValueInput instanceof HTMLInputElement ? parseDefaultValueInput(defaultValueInput.value) : null,
        };
      })
      .filter((item) => item.token || item.target_path)
      .map((item) => createDefaultTemplateMappingRow(item));
  }

  function setTemplateDiscoverPanel(payload) {
    state.templateTokenPreview = payload && typeof payload === 'object' ? payload : null;
    if (templateDiscoveredTokensPanel instanceof HTMLElement) {
      templateDiscoveredTokensPanel.textContent = stringifyJson(payload || {});
    }
    renderTemplateTokenPicker();
    renderTemplateFieldCandidates();
  }

  function setTemplateResolvePanel(payload) {
    if (templateResolveResultPanel instanceof HTMLElement) {
      templateResolveResultPanel.textContent = stringifyJson(payload || {});
    }
  }

  function buildTemplateWorkflowFilename(template, source) {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const templateId = template?.id || 'template';
    const mode = String(source?.source_mode || 'unknown');
    const targetId = mode === 'local'
      ? String(source?.file_hash || 'unknown')
      : String(source?.image_id || 'unknown');
    return `generation-template-${templateId}-${mode}-${targetId}-${stamp}.json`;
  }

  function renderTemplateSelectOptions() {
    if (!(templateSelect instanceof HTMLSelectElement)) {
      return;
    }
    const previous = state.selectedTemplateId ? String(state.selectedTemplateId) : '';
    templateSelect.innerHTML = '<option value="">Create new template</option>';
    state.generationTemplates.forEach((template) => {
      const option = document.createElement('option');
      option.value = String(template.id);
      option.textContent = `${template.name} (#${template.id})`;
      templateSelect.append(option);
    });
    if (previous && state.generationTemplates.some((template) => String(template.id) === previous)) {
      templateSelect.value = previous;
    } else {
      templateSelect.value = '';
      state.selectedTemplateId = null;
    }
  }

  function hydrateTemplateForm(template) {
    if (!(templateNameInput instanceof HTMLInputElement)
      || !(templateDescriptionInput instanceof HTMLTextAreaElement)
      || !(templateWorkflowJsonInput instanceof HTMLTextAreaElement)
      || !(templateDefaultTokensInput instanceof HTMLTextAreaElement)
      || !(templateTokenOverridesInput instanceof HTMLTextAreaElement)) {
      return;
    }

    if (!template) {
      templateNameInput.value = '';
      templateDescriptionInput.value = '';
      templateWorkflowJsonInput.value = '';
      templateDefaultTokensInput.value = stringifyJson({});
      templateTokenOverridesInput.value = stringifyJson({});
      renderTemplateMappingRows([]);
      state.selectedTemplateId = null;
      state.resolvedTemplateWorkflow = null;
      state.templateTokenPreview = null;
      if (templateDownloadResolvedButton instanceof HTMLButtonElement) {
        templateDownloadResolvedButton.disabled = true;
      }
      setTemplateDiscoverPanel({});
      setTemplateResolvePanel({});
      refreshTemplateFieldPathsFromWorkflow();
      return;
    }

    state.selectedTemplateId = Number(template.id);
    templateNameInput.value = String(template.name || '');
    templateDescriptionInput.value = String(template.description || '');
    templateWorkflowJsonInput.value = stringifyJson(template.workflow_json || {});
    templateDefaultTokensInput.value = stringifyJson(template.default_tokens || {});
    templateTokenOverridesInput.value = stringifyJson({});
    renderTemplateMappingRows(Array.isArray(template.mappings) ? template.mappings : []);
    state.resolvedTemplateWorkflow = null;
    state.templateTokenPreview = null;
    if (templateDownloadResolvedButton instanceof HTMLButtonElement) {
      templateDownloadResolvedButton.disabled = true;
    }
    setTemplateDiscoverPanel({});
    setTemplateResolvePanel({});
    refreshTemplateFieldPathsFromWorkflow();
  }

  async function loadTemplates({ preferTemplateId = null } = {}) {
    if (!(templateSelect instanceof HTMLSelectElement)) {
      return;
    }

    const response = await fetchPayload('/generation-templates?limit=200');
    state.generationTemplates = Array.isArray(response.templates) ? response.templates : [];
    if (preferTemplateId !== null && preferTemplateId !== undefined) {
      state.selectedTemplateId = Number(preferTemplateId);
    }
    renderTemplateSelectOptions();

    if (state.selectedTemplateId) {
      const selected = state.generationTemplates.find((template) => Number(template.id) === Number(state.selectedTemplateId));
      if (selected) {
        hydrateTemplateForm(selected);
        return;
      }
    }

    if (state.generationTemplates.length) {
      const firstTemplate = state.generationTemplates[0];
      if (templateSelect) {
        templateSelect.value = String(firstTemplate.id);
      }
      hydrateTemplateForm(firstTemplate);
      return;
    }

    hydrateTemplateForm(null);
  }

  function getActiveTemplateSource() {
    const payloadByMode = getPayloadByMode();
    const localPayload = payloadByMode.get('local') || null;
    const civitaiPayload = payloadByMode.get('civitai') || null;

    if (state.activeTabId === 'local' && localPayload?.target?.file_hash) {
      return { source_mode: 'local', file_hash: String(localPayload.target.file_hash) };
    }
    if (state.activeTabId === 'civitai' && civitaiPayload?.target?.image_id) {
      return { source_mode: 'civitai', image_id: Number(civitaiPayload.target.image_id) };
    }

    if (civitaiPayload && localPayload && state.activeTabId === 'compare') {
      return null;
    }

    if (localPayload?.target?.file_hash) {
      return { source_mode: 'local', file_hash: String(localPayload.target.file_hash) };
    }
    if (civitaiPayload?.target?.image_id) {
      return { source_mode: 'civitai', image_id: Number(civitaiPayload.target.image_id) };
    }

    const formState = getFormState();
    if (formState.fileHash) {
      return { source_mode: 'local', file_hash: formState.fileHash };
    }
    if (formState.civitaiId) {
      const imageId = Number(formState.civitaiId);
      if (!Number.isNaN(imageId) && imageId > 0) {
        return { source_mode: 'civitai', image_id: imageId };
      }
    }
    return null;
  }

  function buildTemplateDraftPayload({ requireName = true } = {}) {
    if (!(templateNameInput instanceof HTMLInputElement)
      || !(templateDescriptionInput instanceof HTMLTextAreaElement)
      || !(templateWorkflowJsonInput instanceof HTMLTextAreaElement)
      || !(templateDefaultTokensInput instanceof HTMLTextAreaElement)) {
      throw new Error('Template form is unavailable.');
    }

    const name = templateNameInput.value.trim();
    if (requireName && !name) {
      throw new Error('Template name is required.');
    }

    const workflowJson = parseJsonText(templateWorkflowJsonInput.value, {
      fieldLabel: 'Workspace JSON',
      fallbackValue: {},
    });
    if (!workflowJson || typeof workflowJson !== 'object' || Array.isArray(workflowJson)) {
      throw new Error('Workspace JSON must be a JSON object.');
    }
    const mappings = readTemplateMappingsFromTable();
    const defaultTokens = parseJsonText(templateDefaultTokensInput.value, {
      fieldLabel: 'Default tokens JSON',
      fallbackValue: {},
    });
    if (!defaultTokens || typeof defaultTokens !== 'object' || Array.isArray(defaultTokens)) {
      throw new Error('Default tokens JSON must be an object.');
    }

    return {
      name,
      description: templateDescriptionInput.value.trim() || null,
      workflow_json: workflowJson,
      mappings,
      default_tokens: defaultTokens,
    };
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

  function resolveActiveComfyPayload() {
    const comfyByMode = getComfyPayloadByMode();
    const civitaiPayload = comfyByMode.get('civitai') || null;
    const localPayload = comfyByMode.get('local') || null;

    if (state.activeTabId === 'local' && localPayload) {
      return localPayload;
    }

    if (state.activeTabId === 'compare' && civitaiPayload && localPayload) {
      return null;
    }

    if (civitaiPayload) {
      return civitaiPayload;
    }

    if (localPayload) {
      return localPayload;
    }

    return null;
  }

  function buildComfyWorkspaceExportPayload() {
    const activePayload = resolveActiveComfyPayload();
    const workspace = activePayload?.workspace_bundle?.comfy_workflow_ui;
    if (!workspace || typeof workspace !== 'object') {
      return null;
    }
    return workspace;
  }

  function buildComfyApiPromptExportPayload() {
    const activePayload = resolveActiveComfyPayload();
    const promptApi = activePayload?.workspace_bundle?.comfy_prompt_api;
    if (!promptApi || typeof promptApi !== 'object') {
      return null;
    }
    return promptApi;
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

  function getComfyWorkspaceExportFilename() {
    const activePayload = resolveActiveComfyPayload();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const mode = String(activePayload?.mode || 'unknown');
    const target = activePayload?.target || {};
    const targetId = mode === 'local'
      ? (target?.file_hash || 'unknown')
      : (target?.image_id || target?.image_db_id || 'unknown');
    return `generation-lab-comfy-workflow-${mode}-${targetId}-${stamp}.json`;
  }

  function getComfyApiPromptExportFilename() {
    const activePayload = resolveActiveComfyPayload();
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const mode = String(activePayload?.mode || 'unknown');
    const target = activePayload?.target || {};
    const targetId = mode === 'local'
      ? (target?.file_hash || 'unknown')
      : (target?.image_id || target?.image_db_id || 'unknown');
    return `generation-lab-comfy-api-prompt-${mode}-${targetId}-${stamp}.json`;
  }

  function updateExportPanel() {
    const payloadByMode = getPayloadByMode();
    const hasCivitai = payloadByMode.has('civitai');
    const hasLocal = payloadByMode.has('local');
    const exportPayload = buildExportPayload();
    const isReady = Boolean(exportPayload);
    const comfyWorkspacePayload = buildComfyWorkspaceExportPayload();
    const comfyApiPayload = buildComfyApiPromptExportPayload();
    const isComfyWorkspaceReady = Boolean(comfyWorkspacePayload);
    const isComfyApiReady = Boolean(comfyApiPayload);
    const isCompareWithBoth = state.activeTabId === 'compare' && hasCivitai && hasLocal;

    exportPanel.classList.toggle('is-disabled', !isReady);
    copyExportButton.disabled = !isReady;
    downloadExportButton.disabled = !isReady;
    copyComfyExportButton.disabled = !isComfyWorkspaceReady;
    downloadComfyExportButton.disabled = !isComfyWorkspaceReady;
    copyComfyApiExportButton.disabled = !isComfyApiReady;
    downloadComfyApiExportButton.disabled = !isComfyApiReady;

    if (!isReady) {
      exportTitle.textContent = 'Export JSON';
      exportMessage.textContent = 'Load an inspection to copy/download structured JSON, raw Comfy workflow JSON, and raw Comfy API prompt JSON.';
      return;
    }

    if (isCompareWithBoth) {
      exportTitle.textContent = 'Export Compare JSON';
      exportMessage.textContent = 'Comfy exports are single-target raw files. Select CivitAI or Local tab for import-ready Comfy JSON.';
      return;
    }

    if (state.activeTabId === 'local' && hasLocal) {
      exportTitle.textContent = 'Export Local JSON';
      exportMessage.textContent = (isComfyWorkspaceReady || isComfyApiReady)
        ? 'Comfy exports are import-ready raw JSON: workspace format and API prompt format.'
        : 'Includes local DB fields, sidecar JSON, JSON metadata, EXIF payloads, normalized output, and validation.';
      return;
    }

    exportTitle.textContent = 'Export CivitAI JSON';
    exportMessage.textContent = (isComfyWorkspaceReady || isComfyApiReady)
      ? 'Comfy exports are import-ready raw JSON: workspace format and API prompt format.'
      : 'Includes fetched CivitAI payloads, normalized output, and validation for the active remote inspection.';
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

  function createTemplateStudioTabPanel() {
    return () => {
      if (templateLabSection instanceof HTMLElement) {
        templateLabSection.hidden = false;
        return templateLabSection;
      }
      const fallback = document.createElement('article');
      fallback.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'Template Studio unavailable';
      const text = document.createElement('p');
      text.textContent = 'Template Studio panel was not found in the page markup.';
      fallback.append(title, text);
      return fallback;
    };
  }

  function createA1111BridgeTabPanel() {
    return () => {
      if (a1111BridgeSection instanceof HTMLElement) {
        a1111BridgeSection.hidden = false;
        return a1111BridgeSection;
      }
      const fallback = document.createElement('article');
      fallback.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'A1111 Bridge unavailable';
      const text = document.createElement('p');
      text.textContent = 'A1111 Bridge panel was not found in the page markup.';
      fallback.append(title, text);
      return fallback;
    };
  }

  function createGenerateImageTabPanel() {
    return () => {
      if (generateImageSection instanceof HTMLElement) {
        generateImageSection.hidden = false;
        return generateImageSection;
      }
      const fallback = document.createElement('article');
      fallback.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'Generate Image unavailable';
      const text = document.createElement('p');
      text.textContent = 'Generate Image panel was not found in the page markup.';
      fallback.append(title, text);
      return fallback;
    };
  }

  function createParityWorkbenchTabPanel() {
    return () => {
      if (parityWorkbenchSection instanceof HTMLElement) {
        parityWorkbenchSection.hidden = false;
        return parityWorkbenchSection;
      }
      const fallback = document.createElement('article');
      fallback.className = 'result-card inspection-empty';
      const title = document.createElement('h2');
      title.textContent = 'Parity Workbench unavailable';
      const text = document.createElement('p');
      text.textContent = 'Parity Workbench panel was not found in the page markup.';
      fallback.append(title, text);
      return fallback;
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

    tabs.push({
      id: 'template-studio',
      label: 'Template Studio',
      row: 2,
      render: createInspectionTabPanel('template-studio', createTemplateStudioTabPanel()),
    });

    tabs.push({
      id: 'a1111-bridge',
      label: 'A1111 Bridge',
      row: 2,
      render: createInspectionTabPanel('a1111-bridge', createA1111BridgeTabPanel()),
    });

    tabs.push({
      id: 'generate-image',
      label: 'Generate Image',
      row: 2,
      render: createInspectionTabPanel('generate-image', createGenerateImageTabPanel()),
    });

    tabs.push({
      id: 'parity-workbench',
      label: 'Parity Workbench',
      row: 2,
      render: createInspectionTabPanel('parity-workbench', createParityWorkbenchTabPanel()),
    });

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

  async function copyComfyWorkspaceExportPayload() {
    const exportPayload = buildComfyWorkspaceExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    await navigator.clipboard.writeText(jsonText);
    setStatus('is-success', 'Comfy workspace copied', 'Import-ready Comfy workflow JSON was copied to the clipboard.');
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

  function downloadComfyWorkspaceExportPayload() {
    const exportPayload = buildComfyWorkspaceExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    const blob = new Blob([jsonText], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getComfyWorkspaceExportFilename();
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    setStatus('is-success', 'Comfy workspace downloaded', 'Import-ready Comfy workflow JSON was downloaded to a file.');
  }

  async function copyComfyApiPromptExportPayload() {
    const exportPayload = buildComfyApiPromptExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    await navigator.clipboard.writeText(jsonText);
    setStatus('is-success', 'Comfy API prompt copied', 'Comfy API prompt JSON was copied to the clipboard.');
  }

  function downloadComfyApiPromptExportPayload() {
    const exportPayload = buildComfyApiPromptExportPayload();
    if (!exportPayload) {
      return;
    }

    const jsonText = JSON.stringify(exportPayload, null, 2);
    const blob = new Blob([jsonText], { type: 'application/json' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = getComfyApiPromptExportFilename();
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    setStatus('is-success', 'Comfy API prompt downloaded', 'Comfy API prompt JSON was downloaded to a file.');
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
    const normalizedPayloads = Array.isArray(payloads) ? payloads : [];
    state.currentPayloads = normalizedPayloads;
    if (!normalizedPayloads.length) {
      state.currentComfyPayloads = [];
    }
    const payloadByMode = new Map(normalizedPayloads.map((payload) => [String(payload?.mode || ''), payload]));
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
    const formState = getFormState();
    const catalogQuery = buildCatalogQueryString(formState);
    const targets = [];
    if (civitaiId) {
      targets.push({
        mode: 'civitai',
        descriptor: `CivitAI image ${civitaiId}`,
        url: `/generation-prototype/civitai/${encodeURIComponent(civitaiId)}`,
        comfyUrl: `/generation-prototype/civitai/${encodeURIComponent(civitaiId)}/comfy-workspace${catalogQuery ? `?${catalogQuery}` : ''}`,
      });
    }
    if (fileHash) {
      targets.push({
        mode: 'local',
        descriptor: `local image ${fileHash}`,
        url: `/images/${encodeURIComponent(fileHash)}/generation-prototype`,
        comfyUrl: `/images/${encodeURIComponent(fileHash)}/generation-prototype/comfy-workspace${catalogQuery ? `?${catalogQuery}` : ''}`,
      });
    }

    if (!targets.length) {
      state.currentPayloads = [];
      state.currentComfyPayloads = [];
      renderInspectionPanels([]);
      setStatus('is-idle', 'Ready', 'Choose a CivitAI image ID, a local file hash, or both to inspect together.');
      return;
    }

    const label = targets.map((target) => target.descriptor).join(' and ');
    setStatus('is-loading', 'Loading', `Inspecting ${label}...`);

    try {
      const results = await Promise.all(targets.map(async (target) => {
        const inspectionPayload = await fetchPayload(target.url);
        let comfyPayload = null;
        try {
          comfyPayload = await fetchPayload(target.comfyUrl);
        } catch (error) {
          comfyPayload = {
            ok: false,
            mode: target.mode,
            target: inspectionPayload?.target || {},
            validation: {
              status: 'error',
              warnings: [],
              errors: [error instanceof Error ? error.message : String(error)],
            },
            error: {
              type: 'comfy_export_fetch_failed',
              detail: error instanceof Error ? error.message : String(error),
            },
          };
        }
        return { inspectionPayload, comfyPayload };
      }));
      const payloads = results.map((item) => item.inspectionPayload);
      state.currentComfyPayloads = results
        .map((item) => item.comfyPayload)
        .filter((item) => item && typeof item === 'object');
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
      state.currentComfyPayloads = [];
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
    const formState = getFormState();
    if (formState.catalogUrl) {
      nextUrl.searchParams.set('catalogUrl', formState.catalogUrl);
    } else {
      nextUrl.searchParams.delete('catalogUrl');
    }
    if (formState.checkpointsUrl) {
      nextUrl.searchParams.set('checkpointsUrl', formState.checkpointsUrl);
    } else {
      nextUrl.searchParams.delete('checkpointsUrl');
    }
    if (formState.lorasUrl) {
      nextUrl.searchParams.set('lorasUrl', formState.lorasUrl);
    } else {
      nextUrl.searchParams.delete('lorasUrl');
    }
    if (formState.includeFullCatalogRaw) {
      nextUrl.searchParams.set('includeFullCatalogRaw', '1');
    } else {
      nextUrl.searchParams.delete('includeFullCatalogRaw');
    }
    window.history.replaceState({}, '', nextUrl);
    persistCatalogSettings();
  }

  async function importTemplateFromForm() {
    const payload = buildTemplateDraftPayload({ requireName: true });
    const response = await fetch('/generation-templates/import-workspace', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data?.detail || `Template import failed with HTTP ${response.status}.`));
    }
    const newTemplateId = data?.template?.id;
    await loadTemplates({ preferTemplateId: newTemplateId });
    setStatus('is-success', 'Template imported', `Saved template ${payload.name}.`);
  }

  async function saveTemplateChangesFromForm() {
    if (!state.selectedTemplateId) {
      throw new Error('Select a saved template first.');
    }
    const payload = buildTemplateDraftPayload({ requireName: true });
    const response = await fetch(`/generation-templates/${encodeURIComponent(state.selectedTemplateId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data?.detail || `Template update failed with HTTP ${response.status}.`));
    }
    await loadTemplates({ preferTemplateId: state.selectedTemplateId });
    setStatus('is-success', 'Template updated', `Updated template #${state.selectedTemplateId}.`);
  }

  async function deleteSelectedTemplate() {
    if (!state.selectedTemplateId) {
      throw new Error('Select a saved template first.');
    }
    const response = await fetch(`/generation-templates/${encodeURIComponent(state.selectedTemplateId)}`, {
      method: 'DELETE',
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data?.detail || `Template delete failed with HTTP ${response.status}.`));
    }
    const deletedName = String(data?.deleted?.name || `#${state.selectedTemplateId}`);
    state.selectedTemplateId = null;
    await loadTemplates();
    setTemplateDiscoverPanel({});
    setStatus('is-success', 'Template deleted', `Deleted template ${deletedName}.`);
  }

  async function discoverTemplateTokens() {
    const activeSource = getActiveTemplateSource();
    if (!activeSource) {
      throw new Error('Load an inspection target first. For compare mode, select CivitAI or Local tab before discovery.');
    }
    const formState = getFormState();
    const params = new URLSearchParams();
    params.set('source_mode', activeSource.source_mode);
    if (activeSource.source_mode === 'local') {
      params.set('file_hash', activeSource.file_hash);
    } else {
      params.set('image_id', String(activeSource.image_id));
    }
    if (formState.catalogUrl) {
      params.set('catalog_url', formState.catalogUrl);
    }
    if (formState.checkpointsUrl) {
      params.set('checkpoints_url', formState.checkpointsUrl);
    }
    if (formState.lorasUrl) {
      params.set('loras_url', formState.lorasUrl);
    }
    if (formState.includeFullCatalogRaw) {
      params.set('include_full_catalog_raw', 'true');
    }

    const payload = await fetchPayload(`/generation-templates/token-preview?${params.toString()}`);
    setTemplateDiscoverPanel(payload);
    setStatus('is-success', 'Tokens discovered', `Derived ${Number(payload?.token_count || 0)} tokens from active source.`);
  }

  async function resolveTemplateFromForm() {
    if (!state.selectedTemplateId) {
      throw new Error('Select a saved template before resolving.');
    }
    if (!(templateTokenOverridesInput instanceof HTMLTextAreaElement)) {
      throw new Error('Token overrides input is unavailable.');
    }

    const activeSource = getActiveTemplateSource();
    if (!activeSource) {
      throw new Error('Load an inspection target first. For compare mode, select CivitAI or Local tab before resolving.');
    }

    const tokenOverrides = parseJsonText(templateTokenOverridesInput.value, {
      fieldLabel: 'Resolve token overrides JSON',
      fallbackValue: {},
    });
    if (!tokenOverrides || typeof tokenOverrides !== 'object' || Array.isArray(tokenOverrides)) {
      throw new Error('Resolve token overrides JSON must be an object.');
    }

    const body = {
      source_mode: activeSource.source_mode,
      file_hash: activeSource.file_hash,
      image_id: activeSource.image_id,
      token_overrides: tokenOverrides,
      include_generation_payload: true,
    };

    const formState = getFormState();
    const params = new URLSearchParams();
    if (formState.catalogUrl) {
      params.set('catalog_url', formState.catalogUrl);
    }
    if (formState.checkpointsUrl) {
      params.set('checkpoints_url', formState.checkpointsUrl);
    }
    if (formState.lorasUrl) {
      params.set('loras_url', formState.lorasUrl);
    }
    if (formState.includeFullCatalogRaw) {
      params.set('include_full_catalog_raw', 'true');
    }

    const response = await fetch(`/generation-templates/${encodeURIComponent(state.selectedTemplateId)}/resolve${params.toString() ? `?${params.toString()}` : ''}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(data?.detail || `Template resolve failed with HTTP ${response.status}.`));
    }

    state.resolvedTemplateWorkflow = data?.resolved_workflow_json || null;
    if (templateDownloadResolvedButton instanceof HTMLButtonElement) {
      templateDownloadResolvedButton.disabled = !state.resolvedTemplateWorkflow;
    }
    setTemplateResolvePanel(data);

    const validationStatus = String(data?.validation?.status || 'ok');
    if (validationStatus === 'error') {
      setStatus('is-error', 'Template resolve completed with errors', 'Review resolve validation before using workflow output.');
    } else if (validationStatus === 'warning') {
      setStatus('is-warning', 'Template resolve completed with warnings', 'Review unresolved placeholders or optional token gaps.');
    } else {
      setStatus('is-success', 'Template resolved', 'Resolved workflow JSON is ready to download.');
    }
  }

  function attachTemplateStudioHandlers() {
    if (!(templateSelect instanceof HTMLSelectElement)
      || !(templateRefreshButton instanceof HTMLButtonElement)
      || !(templateNameInput instanceof HTMLInputElement)
      || !(templateDescriptionInput instanceof HTMLTextAreaElement)
      || !(templateWorkflowFileInput instanceof HTMLInputElement)
      || !(templateLoadFileButton instanceof HTMLButtonElement)
      || !(templateWorkflowJsonInput instanceof HTMLTextAreaElement)
      || !(templateMappingsBody instanceof HTMLElement)
      || !(templateAddMappingButton instanceof HTMLButtonElement)
      || !(templateImportButton instanceof HTMLButtonElement)
      || !(templateSaveButton instanceof HTMLButtonElement)
      || !(templateDeleteButton instanceof HTMLButtonElement)
      || !(templateDiscoverButton instanceof HTMLButtonElement)
      || !(templateResolveButton instanceof HTMLButtonElement)
      || !(templateDownloadResolvedButton instanceof HTMLButtonElement)) {
      return;
    }

    const hasAssistControls = templateTokenPicker instanceof HTMLSelectElement
      && templateApplyTokenButton instanceof HTMLButtonElement
      && templateGuessPathButton instanceof HTMLButtonElement
      && templateFieldFilterInput instanceof HTMLInputElement
      && templateFieldCandidates instanceof HTMLElement;

    templateSelect.addEventListener('change', () => {
      const value = templateSelect.value.trim();
      if (!value) {
        hydrateTemplateForm(null);
        return;
      }
      const selected = state.generationTemplates.find((template) => String(template.id) === value) || null;
      hydrateTemplateForm(selected);
    });

    templateRefreshButton.addEventListener('click', async () => {
      try {
        await loadTemplates({ preferTemplateId: state.selectedTemplateId });
        setStatus('is-success', 'Templates refreshed', 'Template list was refreshed from the API.');
      } catch (error) {
        setStatus('is-error', 'Refresh failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateLoadFileButton.addEventListener('click', () => {
      templateWorkflowFileInput.click();
    });

    templateWorkflowFileInput.addEventListener('change', async () => {
      const [file] = Array.from(templateWorkflowFileInput.files || []);
      if (!file) {
        return;
      }
      try {
        const text = await file.text();
        const parsed = parseJsonText(text, { fieldLabel: 'Workspace JSON file', fallbackValue: {} });
        templateWorkflowJsonInput.value = stringifyJson(parsed);
        refreshTemplateFieldPathsFromWorkflow();
        setStatus('is-success', 'Workspace loaded', `Loaded ${file.name}.`);
      } catch (error) {
        setStatus('is-error', 'Workspace load failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateWorkflowJsonInput.addEventListener('input', () => {
      refreshTemplateFieldPathsFromWorkflow();
    });

    attachJsonFileDrop(templateWorkflowJsonInput, {
      fieldLabel: 'Template workflow JSON',
      onLoaded: () => {
        refreshTemplateFieldPathsFromWorkflow();
      },
    });

    templateAddMappingButton.addEventListener('click', () => {
      const currentMappings = readTemplateMappingsFromTable();
      currentMappings.push(createDefaultTemplateMappingRow());
      renderTemplateMappingRows(currentMappings, { selectLastRow: true });
      setStatus('is-success', 'Mapping row added', 'New mapping row is active. Token picker and path actions now target it.');
    });

    templateMappingsBody.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      if (target.getAttribute('data-template-action') !== 'remove-row') {
        if (target.getAttribute('data-template-action') === 'guess-row') {
          const row = target.closest('tr');
          if (row instanceof HTMLTableRowElement) {
            setActiveMappingRow(row);
          }
          try {
            const best = applyBestGuessPathToActiveRow();
            setStatus('is-success', 'Best guess applied', `Mapped with ${best?.confidence?.label || 'Low'} confidence.`);
          } catch (error) {
            setStatus('is-warning', 'No strong guess', error instanceof Error ? error.message : String(error));
          }
        }
        return;
      }
      const row = target.closest('tr');
      if (!row) {
        return;
      }
      row.remove();
      if (!templateMappingsBody.querySelector('tr')) {
        renderTemplateMappingRows([]);
        return;
      }
      const remainingRows = Array.from(templateMappingsBody.querySelectorAll('tr'));
      const nextActive = remainingRows.find((item) => item.classList.contains('is-active'))
        || remainingRows[remainingRows.length - 1]
        || null;
      setActiveMappingRow(nextActive);
    });

    templateMappingsBody.addEventListener('focusin', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) {
        return;
      }
      const row = target.closest('tr');
      if (row instanceof HTMLTableRowElement) {
        setActiveMappingRow(row);
      }
    });

    templateMappingsBody.addEventListener('input', () => {
      renderTemplateTokenPicker();
      renderTemplateFieldCandidates();
    });

    if (hasAssistControls) {
      templateApplyTokenButton.addEventListener('click', () => {
        try {
          applyTokenToActiveRow(templateTokenPicker.value);
          renderTemplateFieldCandidates();
          setStatus('is-success', 'Token applied', `Assigned ${templateTokenPicker.value} to active mapping row.`);
        } catch (error) {
          setStatus('is-warning', 'Token not applied', error instanceof Error ? error.message : String(error));
        }
      });

      templateGuessPathButton.addEventListener('click', () => {
        try {
          const best = applyBestGuessPathToActiveRow();
          setStatus('is-success', 'Best guess applied', `Mapped with ${best?.confidence?.label || 'Low'} confidence.`);
        } catch (error) {
          setStatus('is-warning', 'No strong guess', error instanceof Error ? error.message : String(error));
        }
      });

      templateTokenPicker.addEventListener('change', () => {
        try {
          applyTokenToActiveRow(templateTokenPicker.value);
        } catch {
          // Ignore auto-apply errors when no row is active; manual button remains available.
        }
        renderTemplateFieldCandidates();
      });

      templateFieldFilterInput.addEventListener('input', () => {
        renderTemplateFieldCandidates();
      });

      templateFieldCandidates.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
          return;
        }
        const button = target.closest('button.template-field-candidate');
        if (!(button instanceof HTMLButtonElement)) {
          return;
        }
        const targetPath = String(button.dataset.targetPath || '').trim();
        if (!targetPath) {
          return;
        }
        try {
          applyTargetPathToActiveRow(targetPath);
          setStatus('is-success', 'Path applied', `Mapped ${targetPath} on active row.`);
        } catch (error) {
          setStatus('is-warning', 'Path not applied', error instanceof Error ? error.message : String(error));
        }
      });
    }

    templateImportButton.addEventListener('click', async () => {
      try {
        await importTemplateFromForm();
      } catch (error) {
        setStatus('is-error', 'Template import failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateSaveButton.addEventListener('click', async () => {
      try {
        await saveTemplateChangesFromForm();
      } catch (error) {
        setStatus('is-error', 'Template save failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateDeleteButton.addEventListener('click', async () => {
      if (!state.selectedTemplateId) {
        setStatus('is-warning', 'Missing template', 'Select a template before deleting.');
        return;
      }
      const confirmed = window.confirm(`Delete template #${state.selectedTemplateId}?`);
      if (!confirmed) {
        return;
      }
      try {
        await deleteSelectedTemplate();
      } catch (error) {
        setStatus('is-error', 'Template delete failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateDiscoverButton.addEventListener('click', async () => {
      try {
        await discoverTemplateTokens();
      } catch (error) {
        setStatus('is-error', 'Token discovery failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateResolveButton.addEventListener('click', async () => {
      try {
        await resolveTemplateFromForm();
      } catch (error) {
        setStatus('is-error', 'Template resolve failed', error instanceof Error ? error.message : String(error));
      }
    });

    templateDownloadResolvedButton.addEventListener('click', () => {
      if (!state.resolvedTemplateWorkflow) {
        return;
      }
      const selectedTemplate = state.generationTemplates.find((template) => Number(template.id) === Number(state.selectedTemplateId)) || null;
      const source = getActiveTemplateSource();
      downloadJsonBlob(
        state.resolvedTemplateWorkflow,
        buildTemplateWorkflowFilename(selectedTemplate, source),
      );
      setStatus('is-success', 'Resolved workflow downloaded', 'Template-resolved workflow JSON was downloaded.');
    });
  }

  function setA1111BridgePanels(parsePayload, comparePayload) {
    if (bridgeParseResultPanel instanceof HTMLElement) {
      bridgeParseResultPanel.textContent = stringifyJson(parsePayload || {});
    }
    if (bridgeCompareResultPanel instanceof HTMLElement) {
      bridgeCompareResultPanel.textContent = stringifyJson(comparePayload || {});
    }
  }

  function clearA1111BridgePanels() {
    state.a1111BridgeAnalysis = null;
    setA1111BridgePanels({}, {});
    if (bridgeDownloadButton instanceof HTMLButtonElement) {
      bridgeDownloadButton.disabled = true;
    }
    if (bridgeSaveButton instanceof HTMLButtonElement) {
      bridgeSaveButton.disabled = true;
    }
  }

  function buildA1111BridgeExportFilename() {
    const payload = state.a1111BridgeAnalysis || {};
    const fileHash = String(payload?.target?.file_hash || '').trim();
    const hashPart = fileHash ? fileHash.slice(0, 12) : 'unknown';
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    return `a1111-bridge-comparison-${hashPart}-${stamp}.json`;
  }

  function downloadA1111BridgeAnalysis() {
    if (!state.a1111BridgeAnalysis || typeof state.a1111BridgeAnalysis !== 'object') {
      throw new Error('Run A1111 Bridge analysis before exporting.');
    }
    downloadJsonBlob(state.a1111BridgeAnalysis, buildA1111BridgeExportFilename());
  }

  async function saveA1111BridgeAnalysisToServer() {
    if (!state.a1111BridgeAnalysis || typeof state.a1111BridgeAnalysis !== 'object') {
      throw new Error('Run A1111 Bridge analysis before saving.');
    }

    const response = await fetch('/generation-prototype/a1111-bridge/save-analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        analysis_payload: state.a1111BridgeAnalysis,
        file_name: buildA1111BridgeExportFilename(),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(payload?.detail || `A1111 Bridge save failed with HTTP ${response.status}.`));
    }
    return payload;
  }

  function buildA1111BridgeRequestPayload() {
    if (!(bridgeFileHashInput instanceof HTMLInputElement)) {
      throw new Error('A1111 Bridge file hash input is unavailable.');
    }

    const fileHash = bridgeFileHashInput.value.trim();
    if (!fileHash) {
      throw new Error('Enter a local file hash for A1111 Bridge analysis.');
    }

    const includeGenerationPayload = bridgeIncludeGenerationPayloadInput instanceof HTMLInputElement
      ? bridgeIncludeGenerationPayloadInput.checked
      : false;

    let workflowJson = null;
    if (bridgeWorkflowJsonInput instanceof HTMLTextAreaElement) {
      const parsed = parseJsonText(bridgeWorkflowJsonInput.value, {
        fieldLabel: 'Comfy workflow JSON',
        fallbackValue: null,
      });
      if (parsed !== null && (typeof parsed !== 'object' || Array.isArray(parsed))) {
        throw new Error('Comfy workflow JSON must be an object when provided.');
      }
      workflowJson = parsed;
    }

    return {
      file_hash: fileHash,
      comfy_workflow_json: workflowJson,
      include_generation_payload: includeGenerationPayload,
    };
  }

  async function runA1111BridgeAnalysis() {
    const requestPayload = buildA1111BridgeRequestPayload();
    const response = await fetch('/generation-prototype/a1111-bridge/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestPayload),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(String(payload?.detail || `A1111 Bridge analysis failed with HTTP ${response.status}.`));
    }

    state.a1111BridgeAnalysis = payload;
    setA1111BridgePanels(
      {
        target: payload?.target || {},
        user_comment: payload?.user_comment || {},
        validation: payload?.validation || {},
      },
      {
        comparison: payload?.comparison || {},
        comfy: payload?.comfy || {},
      },
    );

    const validationStatus = String(payload?.validation?.status || 'ok');
    if (bridgeDownloadButton instanceof HTMLButtonElement) {
      bridgeDownloadButton.disabled = false;
    }
    if (bridgeSaveButton instanceof HTMLButtonElement) {
      bridgeSaveButton.disabled = false;
    }
    if (validationStatus === 'error') {
      setStatus('is-error', 'A1111 Bridge completed with errors', 'Review bridge validation and comparison output.');
    } else if (validationStatus === 'warning') {
      setStatus('is-warning', 'A1111 Bridge completed with warnings', 'Parsed metadata was generated, but review warning details before trusting mappings.');
    } else {
      setStatus('is-success', 'A1111 Bridge completed', 'Parsed A1111 metadata and Comfy comparison outputs are ready.');
    }
  }

  function attachA1111BridgeHandlers() {
    if (!(bridgeAnalyzeButton instanceof HTMLButtonElement)
      || !(bridgeClearButton instanceof HTMLButtonElement)
      || !(bridgeFileHashInput instanceof HTMLInputElement)) {
      return;
    }

    bridgeAnalyzeButton.addEventListener('click', async () => {
      bridgeAnalyzeButton.disabled = true;
      try {
        await runA1111BridgeAnalysis();
      } catch (error) {
        setStatus('is-error', 'A1111 Bridge failed', error instanceof Error ? error.message : String(error));
      } finally {
        bridgeAnalyzeButton.disabled = false;
      }
    });

    bridgeClearButton.addEventListener('click', () => {
      clearA1111BridgePanels();
      if (bridgeWorkflowJsonInput instanceof HTMLTextAreaElement) {
        bridgeWorkflowJsonInput.value = '';
      }
      setStatus('is-idle', 'A1111 Bridge reset', 'A1111 Bridge inputs and output panels were reset.');
    });

    if (bridgeUseLocalHashButton instanceof HTMLButtonElement) {
      bridgeUseLocalHashButton.addEventListener('click', () => {
        bridgeFileHashInput.value = String(localInput?.value || '').trim();
      });
    }

    if (bridgeDownloadButton instanceof HTMLButtonElement) {
      bridgeDownloadButton.addEventListener('click', () => {
        try {
          downloadA1111BridgeAnalysis();
          setStatus('is-success', 'A1111 Bridge export downloaded', 'Comparison payload JSON was downloaded.');
        } catch (error) {
          setStatus('is-error', 'A1111 Bridge export failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    if (bridgeSaveButton instanceof HTMLButtonElement) {
      bridgeSaveButton.addEventListener('click', async () => {
        bridgeSaveButton.disabled = true;
        try {
          const saveResult = await saveA1111BridgeAnalysisToServer();
          const relativePath = String(saveResult?.saved?.relative_path || '').trim();
          setStatus(
            'is-success',
            'A1111 Bridge export saved',
            relativePath
              ? `Saved comparison payload to ${relativePath}.`
              : 'Saved comparison payload to AtelierAI data folder.',
          );
        } catch (error) {
          setStatus('is-error', 'A1111 Bridge save failed', error instanceof Error ? error.message : String(error));
        } finally {
          bridgeSaveButton.disabled = false;
        }
      });
    }

    if (bridgeDatasetRefreshButton instanceof HTMLButtonElement) {
      bridgeDatasetRefreshButton.addEventListener('click', () => {
        loadBridgeDatasetQuality();
      });
    }

    if (bridgeDatasetRunAnalyzeButton instanceof HTMLButtonElement) {
      bridgeDatasetRunAnalyzeButton.addEventListener('click', () => {
        if (!(bridgeFileHashInput instanceof HTMLInputElement)) {
          setStatus('is-warning', 'Bridge hash unavailable', 'A1111 Bridge hash input is not available in this view.');
          return;
        }
        if (!bridgeFileHashInput.value.trim()) {
          setStatus('is-warning', 'Missing value', 'Set a local file hash before running A1111 Bridge analysis.');
          bridgeFileHashInput.focus();
          return;
        }
        if (bridgeAnalyzeButton instanceof HTMLButtonElement) {
          bridgeAnalyzeButton.click();
        }
      });
    }

    if (bridgeDatasetDownloadButton instanceof HTMLButtonElement) {
      bridgeDatasetDownloadButton.addEventListener('click', async () => {
        try {
          if (!state.bridgeDatasetQuality) {
            await loadBridgeDatasetQuality();
          }
          if (!state.bridgeDatasetQuality) {
            throw new Error('Dataset quality report is unavailable.');
          }
          const stamp = new Date().toISOString().replace(/[:.]/g, '-');
          downloadJsonBlob(state.bridgeDatasetQuality, `a1111-bridge-dataset-quality-${stamp}.json`);
          setStatus('is-success', 'Quality report downloaded', 'Bridge dataset quality JSON was downloaded.');
        } catch (error) {
          setStatus('is-error', 'Quality download failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    if (bridgeDatasetCopyPathButton instanceof HTMLButtonElement) {
      bridgeDatasetCopyPathButton.addEventListener('click', async () => {
        try {
          if (!state.bridgeDatasetQuality) {
            await loadBridgeDatasetQuality();
          }
          const paths = state.bridgeDatasetQuality && typeof state.bridgeDatasetQuality === 'object'
            ? (state.bridgeDatasetQuality.paths || {})
            : {};
          const absolutePath = String(paths.export_dir_absolute || '').trim();
          const relativePath = String(paths.export_dir_relative || '').trim();
          const pathText = absolutePath || relativePath;
          if (!pathText) {
            throw new Error('Export path is unavailable in the dataset quality report.');
          }
          await navigator.clipboard.writeText(pathText);
          setStatus('is-success', 'Export path copied', `Copied ${pathText} to clipboard.`);
        } catch (error) {
          setStatus('is-error', 'Copy path failed', error instanceof Error ? error.message : String(error));
        }
      });
    }
  }

  function attachGenerateImageHandlers() {
    if (!(generateRunButton instanceof HTMLButtonElement)
      || !(generateWorkflowJsonInput instanceof HTMLTextAreaElement)
      || !(generateReferenceFileHashInput instanceof HTMLInputElement)) {
      return;
    }

    generateRunButton.addEventListener('click', async () => {
      generateRunButton.disabled = true;
      try {
        await runGenerateImageFlow();
      } catch (error) {
        setGenerateStatus('is-error', 'Generation failed', error instanceof Error ? error.message : String(error));
      } finally {
        generateRunButton.disabled = false;
      }
    });

    if (generateLoadFileButton instanceof HTMLButtonElement
      && generateWorkflowFileInput instanceof HTMLInputElement) {
      generateLoadFileButton.addEventListener('click', () => {
        generateWorkflowFileInput.click();
      });
      generateWorkflowFileInput.addEventListener('change', async () => {
        const [file] = Array.from(generateWorkflowFileInput.files || []);
        if (!file) {
          return;
        }
        try {
          const text = await file.text();
          const parsed = parseJsonText(text, { fieldLabel: 'Generate workflow JSON file', fallbackValue: {} });
          generateWorkflowJsonInput.value = stringifyJson(parsed);
          setGenerateStatus('is-success', 'Workflow loaded', `Loaded ${file.name}.`);
        } catch (error) {
          setGenerateStatus('is-error', 'Workflow load failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    if (generateUseLocalHashButton instanceof HTMLButtonElement) {
      generateUseLocalHashButton.addEventListener('click', () => {
        generateReferenceFileHashInput.value = String(localInput?.value || '').trim();
        loadGenerateAttempts(generateReferenceFileHashInput.value).catch(() => {
          // Ignore history refresh failures on hash sync action.
        });
      });
    }

    if (generateAttemptsRefreshButton instanceof HTMLButtonElement) {
      generateAttemptsRefreshButton.addEventListener('click', async () => {
        try {
          await loadGenerateAttempts(generateReferenceFileHashInput.value.trim());
        } catch (error) {
          setGenerateStatus('is-error', 'Attempt history failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    attachJsonFileDrop(generateWorkflowJsonInput, { fieldLabel: 'Generate workflow JSON' });
  }

  function attachParityWorkbenchHandlers() {
    if (!(parityAuditButton instanceof HTMLButtonElement)) {
      return;
    }

    parityAuditButton.addEventListener('click', async () => {
      parityAuditButton.disabled = true;
      try {
        await runParityAuditFlow();
      } catch (error) {
        setParityStatus('is-error', 'Analysis failed', error instanceof Error ? error.message : String(error));
      } finally {
        parityAuditButton.disabled = false;
      }
    });

    if (parityUseLocalHashButton instanceof HTMLButtonElement
      && parityFileHashInput instanceof HTMLInputElement) {
      parityUseLocalHashButton.addEventListener('click', async () => {
        const localHash = String(localInput?.value || '').trim();
        if (!localHash) {
          setParityStatus('is-warning', 'Missing local hash', 'Local Input is empty. Paste a hash manually or set Local Input first.');
          return;
        }
        const picker = ensureParityImageHashPicker();
        if (!picker) {
          parityFileHashInput.value = localHash;
          resolveGalleryItemByHash(localHash)
            .then((item) => {
              if (item) {
                setParitySelectedImage(item);
              }
            })
            .catch(() => {
              // Ignore preview lookup failures when picker script is unavailable.
            });
          setParityStatus('is-success', 'Hash copied', 'Copied current Local Input hash into Parity Workbench.');
          return;
        }
        try {
          await picker.open({
            initialQuery: localHash,
            autoSelectHash: localHash,
            selectedItem: state.paritySelectedImage,
          });
        } catch (error) {
          setParityStatus('is-error', 'Gallery search failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    if (parityOpenPickerButton instanceof HTMLButtonElement) {
      parityOpenPickerButton.addEventListener('click', async () => {
        const picker = ensureParityImageHashPicker();
        if (!picker) {
          setParityStatus('is-error', 'Picker unavailable', 'Gallery picker script did not load.');
          return;
        }
        const activeHash = String(parityFileHashInput?.value || '').trim();
        try {
          await picker.open({
            initialQuery: activeHash,
            autoSelectHash: activeHash,
            selectedItem: state.paritySelectedImage,
          });
        } catch (error) {
          setParityStatus('is-error', 'Gallery search failed', error instanceof Error ? error.message : String(error));
        }
      });
    }

    if (parityWorkflowJsonInput instanceof HTMLTextAreaElement) {
      attachJsonFileDrop(parityWorkflowJsonInput, { fieldLabel: 'Workflow JSON' });
    }

    // Template selector
    if (parityLoadTemplateButton instanceof HTMLButtonElement) {
      parityLoadTemplateButton.addEventListener('click', async () => {
        try {
          const resp = await fetch('/generation-templates/list');
          if (!resp.ok) {
            setParityStatus('is-error', 'Templates unavailable', `Failed to load templates (HTTP ${resp.status}).`);
            return;
          }
          const data = await resp.json();
          const templates = Array.isArray(data) ? data : (data?.templates || []);
          if (!parityTemplateSelect instanceof HTMLSelectElement) return;
          parityTemplateSelect.innerHTML = '<option value="">— Select a template —</option>';
          templates.forEach((t) => {
            const opt = document.createElement('option');
            opt.value = String(t?.id || t?.template_id || '');
            opt.textContent = String(t?.name || t?.label || t?.id || 'Unnamed');
            parityTemplateSelect.appendChild(opt);
          });
          parityTemplateSelect.style.display = '';
        } catch (err) {
          setParityStatus('is-error', 'Template load failed', err instanceof Error ? err.message : String(err));
        }
      });
    }

    if (parityTemplateSelect instanceof HTMLSelectElement) {
      parityTemplateSelect.addEventListener('change', async () => {
        const templateId = parityTemplateSelect.value.trim();
        if (!templateId || !(parityWorkflowJsonInput instanceof HTMLTextAreaElement)) return;
        try {
          const resp = await fetch(`/generation-templates/get/${encodeURIComponent(templateId)}`);
          if (!resp.ok) {
            setParityStatus('is-error', 'Template fetch failed', `HTTP ${resp.status}`);
            return;
          }
          const data = await resp.json();
          const workflow = data?.resolved_workflow || data?.workflow_json || data?.workflow || data;
          parityWorkflowJsonInput.value = typeof workflow === 'string' ? workflow : JSON.stringify(workflow, null, 2);
          setParityStatus('is-success', 'Template loaded', `Loaded template "${templateId}" into workflow input.`);
        } catch (err) {
          setParityStatus('is-error', 'Template fetch failed', err instanceof Error ? err.message : String(err));
        }
      });
    }

    // File upload
    if (parityUploadWorkflowButton instanceof HTMLButtonElement && parityWorkflowFileInput instanceof HTMLInputElement) {
      parityUploadWorkflowButton.addEventListener('click', () => {
        parityWorkflowFileInput.click();
      });
      parityWorkflowFileInput.addEventListener('change', () => {
        const file = parityWorkflowFileInput.files?.[0];
        if (!file || !(parityWorkflowJsonInput instanceof HTMLTextAreaElement)) return;
        const reader = new FileReader();
        reader.onload = () => {
          parityWorkflowJsonInput.value = String(reader.result || '');
          setParityStatus('is-success', 'File loaded', `Loaded "${file.name}" into workflow input.`);
        };
        reader.onerror = () => {
          setParityStatus('is-error', 'File read failed', 'Could not read the selected file.');
        };
        reader.readAsText(file);
      });
    }
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

  if (civitaiPreviewButton instanceof HTMLButtonElement) {
    civitaiPreviewButton.addEventListener('click', () => {
      previewCivitaiImageById();
    });
  }

  if (localPreviewButton instanceof HTMLButtonElement) {
    localPreviewButton.addEventListener('click', () => {
      previewLocalImageByHash();
    });
  }

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

  catalogForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const civitaiId = civitaiInput.value.trim();
    const fileHash = localInput.value.trim();
    syncUrlState({ civitaiId, fileHash });
    if (!civitaiId && !fileHash) {
      setStatus('is-success', 'Validation settings updated', 'Catalog settings were saved. Run an inspection to refresh Comfy workspace validation.');
      updateExportPanel();
      return;
    }
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

  copyComfyExportButton.addEventListener('click', async () => {
    try {
      await copyComfyWorkspaceExportPayload();
    } catch (error) {
      setStatus('is-error', 'Copy failed', error instanceof Error ? error.message : String(error));
    }
  });

  downloadComfyExportButton.addEventListener('click', () => {
    try {
      downloadComfyWorkspaceExportPayload();
    } catch (error) {
      setStatus('is-error', 'Download failed', error instanceof Error ? error.message : String(error));
    }
  });

  copyComfyApiExportButton.addEventListener('click', async () => {
    try {
      await copyComfyApiPromptExportPayload();
    } catch (error) {
      setStatus('is-error', 'Copy failed', error instanceof Error ? error.message : String(error));
    }
  });

  downloadComfyApiExportButton.addEventListener('click', () => {
    try {
      downloadComfyApiPromptExportPayload();
    } catch (error) {
      setStatus('is-error', 'Download failed', error instanceof Error ? error.message : String(error));
    }
  });

  const params = new URLSearchParams(window.location.search);
  const persistedCatalogSettings = getPersistedCatalogSettings();
  const civitaiQuery = params.get('civitai');
  const fileHashQuery = params.get('fileHash') || params.get('hash');
  const catalogUrlQuery = params.get('catalogUrl');
  const checkpointsUrlQuery = params.get('checkpointsUrl');
  const lorasUrlQuery = params.get('lorasUrl');
  const includeFullCatalogRawQuery = params.get('includeFullCatalogRaw');

  const catalogUrlInitial = catalogUrlQuery ?? persistedCatalogSettings?.catalogUrl ?? '';
  const checkpointsUrlInitial = checkpointsUrlQuery ?? persistedCatalogSettings?.checkpointsUrl ?? '';
  const lorasUrlInitial = lorasUrlQuery ?? persistedCatalogSettings?.lorasUrl ?? '';
  const includeFullCatalogRawInitial = includeFullCatalogRawQuery !== null
    ? (includeFullCatalogRawQuery === '1' || includeFullCatalogRawQuery === 'true')
    : Boolean(persistedCatalogSettings?.includeFullCatalogRaw);

  setFormValues({
    civitaiId: civitaiQuery || '',
    fileHash: fileHashQuery || '',
    catalogUrl: catalogUrlInitial,
    checkpointsUrl: checkpointsUrlInitial,
    lorasUrl: lorasUrlInitial,
    includeFullCatalogRaw: includeFullCatalogRawInitial,
  });
  if (bridgeFileHashInput instanceof HTMLInputElement && fileHashQuery) {
    bridgeFileHashInput.value = String(fileHashQuery).trim();
  }
  if (generateReferenceFileHashInput instanceof HTMLInputElement && fileHashQuery) {
    generateReferenceFileHashInput.value = String(fileHashQuery).trim();
    loadGenerateAttempts(generateReferenceFileHashInput.value).catch(() => {
      // Ignore initial attempt history loading failures.
    });
  }
  if (parityFileHashInput instanceof HTMLInputElement && fileHashQuery) {
    parityFileHashInput.value = String(fileHashQuery).trim();
    resolveGalleryItemByHash(parityFileHashInput.value)
      .then((item) => {
        if (item) {
          setParitySelectedImage(item);
          const picker = ensureParityImageHashPicker();
          if (picker) {
            picker.setSelected(item);
          }
        }
      })
      .catch(() => {
        setParitySelectedImage(null);
      });
  }
  persistCatalogSettings();

  [catalogUrlInput, checkpointsUrlInput, lorasUrlInput, includeFullCatalogRawInput].forEach((element) => {
    element.addEventListener('change', () => {
      persistCatalogSettings();
    });
  });

  attachTemplateStudioHandlers();
  attachA1111BridgeHandlers();
  attachGenerateImageHandlers();
  attachParityWorkbenchHandlers();
  attachJsonFileDrop(bridgeWorkflowJsonInput, { fieldLabel: 'Comfy workflow JSON' });
  loadBridgeDatasetQuality();
  renderTemplateTokenPicker();
  refreshTemplateFieldPathsFromWorkflow();
  clearA1111BridgePanels();
  renderInspectionPanels([]);
  loadTemplates().catch((error) => {
    setStatus('is-warning', 'Template API unavailable', error instanceof Error ? error.message : String(error));
  });

  updateExportPanel();
  if (civitaiQuery || fileHashQuery) {
    runInspectionSequence({ civitaiId: civitaiQuery || '', fileHash: fileHashQuery || '' });
  }
})();