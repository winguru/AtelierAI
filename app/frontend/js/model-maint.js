(() => {
  'use strict';

  const params = new URLSearchParams(window.location.search);
  const requestedType = (params.get('type') || '').trim().toLowerCase();
  const valid = new Set(['checkpoint', 'lora']);
  if (!valid.has(requestedType)) {
    document.body.innerHTML = '<p style="padding:2rem;color:#dc2626;">Invalid type. Use ?type=checkpoint|lora</p>';
    return;
  }

  const title = document.getElementById('page-title');
  const subtitle = document.getElementById('page-subtitle');
  const themeToggle = document.getElementById('theme-toggle');
  const toastContainer = document.getElementById('toast-container');

  const rescanBtn = document.getElementById('rescan-btn');
  const rescanFetchDetails = document.getElementById('rescan-fetch-details');
  const rescanMissingOnly = document.getElementById('rescan-missing-only');
  const rescanLimit = document.getElementById('rescan-limit');
  const rescanStatus = document.getElementById('rescan-status');
  const rescanResults = document.getElementById('rescan-results');

  const importFile = document.getElementById('import-file');
  const importDryRun = document.getElementById('import-dry-run');
  const importBtn = document.getElementById('import-btn');
  const importOutput = document.getElementById('import-output');

  const exportBtn = document.getElementById('export-btn');
  const exportOutput = document.getElementById('export-output');

  const purgeDryRun = document.getElementById('purge-dry-run');
  const purgeBtn = document.getElementById('purge-btn');
  const purgeOutput = document.getElementById('purge-output');

  const localCatalogBtn = document.getElementById('local-catalog-btn');
  const localCatalogStatus = document.getElementById('local-catalog-status');
  const localCatalogResults = document.getElementById('local-catalog-results');

  // ---- Model Observation Rescan elements ----
  const obsRescanBtn = document.getElementById('obs-rescan-btn');
  const obsDryRun = document.getElementById('obs-dry-run');
  const obsRescanStatus = document.getElementById('obs-rescan-status');
  const obsRescanResults = document.getElementById('obs-rescan-results');
  const obsProgressWrap = document.getElementById('obs-progress-wrap');
  const obsProgressBar = document.getElementById('obs-progress-bar');
  const obsProgressLabel = document.getElementById('obs-progress-label');
  const obsMetrics = document.getElementById('obs-metrics');
  const obsRescanCard = document.getElementById('obs-rescan-card');

  const searchInput = document.getElementById('search-input');
  const refreshTableBtn = document.getElementById('refresh-table-btn');
  const showVersionsCheck = document.getElementById('show-versions-check');
  const tableHeader = document.getElementById('model-table-header');
  const prevPageBtn = document.getElementById('prev-page-btn');
  const nextPageBtn = document.getElementById('next-page-btn');
  const pageIndicator = document.getElementById('page-indicator');
  const tableInfo = document.getElementById('table-info');
  const tbody = document.getElementById('model-tbody');

  const prefs = window.AtelierPreferences || null;
  if (prefs) {
    prefs.initThemeFromCookie();
    prefs.bindThemeToggle(themeToggle);
  }

  const typeLabel = requestedType === 'checkpoint' ? 'Checkpoint' : 'LoRA';
  title.textContent = `${typeLabel} Model Maintenance`;
  subtitle.textContent = `Scoped to ${typeLabel} records`;

  const PAGE_SIZE = 100;
  let currentPage = 1;
  let total = 0;

  function maintenanceUrl(path, query = {}) {
    const q = new URLSearchParams({ model_type: requestedType, ...query });
    return `/api/civitai/models/maintenance/${path}?${q.toString()}`;
  }

  function modelsListUrl() {
    const expand = showVersionsCheck.checked;
    const q = new URLSearchParams({
      model_type: requestedType === 'checkpoint' ? 'Checkpoint' : 'LORA',
      skip: String((currentPage - 1) * PAGE_SIZE),
      limit: String(PAGE_SIZE),
    });
    if (expand) q.set('expand_versions', '1');
    const search = (searchInput.value || '').trim();
    if (search) q.set('search', search);
    return `/api/civitai/models/?${q.toString()}`;
  }

  function toast(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function showJson(el, payload) {
    el.hidden = false;
    el.textContent = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
  }

  function setStatus(el, msg, cls = 'info') {
    el.textContent = msg;
    el.className = `step-status ${cls}`;
  }

  function fmtDate(value) {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString();
    } catch {
      return String(value);
    }
  }

  const MODEL_HEADERS = ['Model ID', 'Name', 'Type', 'Status', 'Author', 'Downloads', 'Versions', 'In Use', 'Local'];
  const VERSION_HEADERS = ['Model ID', 'Model Name', 'Version ID', 'Version Name', 'Base Model', 'Status', 'Downloads', 'Gallery', 'Local'];

  // ---- Local catalog state ----
  let localVersionIds = null;   // null = not fetched yet; {} = fetched
  let localConfigured = null;   // null|true|false

  // ---- Gallery usage state ----
  let galleryVersionCounts = {};  // {version_id: image_count}
  let galleryModelCounts = {};   // {model_id: image_count}

  // ---- Sort state ----
  let sortColumn = null;
  let sortDirection = 'asc';

  async function fetchLocalVersionIds() {
    try {
      const resp = await fetch(maintenanceUrl('local-version-ids'));
      const data = await resp.json();
      localConfigured = data.configured ?? false;
      localVersionIds = data.versions ?? {};
    } catch {
      localConfigured = false;
      localVersionIds = {};
    }
  }

  async function fetchGalleryUsageCounts() {
    try {
      const resp = await fetch(maintenanceUrl('gallery-usage-counts'));
      const data = await resp.json();
      galleryVersionCounts = data.version_counts ?? {};
      galleryModelCounts = data.model_counts ?? {};
    } catch {
      galleryVersionCounts = {};
      galleryModelCounts = {};
    }
  }

  function _localBadge(count, total) {
    if (localConfigured === false) return '<td class="muted">N/A</td>';
    if (count === 0) return '<td class="muted">0/' + total + '</td>';
    return '<td><span class="badge-local">' + count + '/' + total + '</span></td>';
  }

  function _galleryCount(count) {
    if (!count) return '<td class="muted">\u2014</td>';
    return '<td><span class="badge-gallery">' + count + '</span></td>';
  }

  function _localCheck(versionId) {
    if (localConfigured === false) return '<td class="muted">N/A</td>';
    const info = localVersionIds[versionId];
    if (!info) return '<td class="muted">—</td>';
    const tip = (info.file_path || info.file_name || '').replace(/"/g, '&quot;');
    return '<td><span class="local-check" title="' + tip + '">✓</span></td>';
  }

  // ---- Sort accessors: map header name to a value getter ----
  const MODEL_SORT_ACCESSORS = {
    'Model ID': row => row.id,
    'Name': row => row.name,
    'Type': row => row.type,
    'Status': row => row.status,
    'Author': row => row.author?.username ?? '',
    'Downloads': row => row.rank?.download_count ?? 0,
    'Versions': row => row.version_count ?? 0,
    'In Use': row => galleryModelCounts[row.id] ?? 0,
    'Local': row => {
      if (!localVersionIds) return -1;
      const vs = Array.isArray(row.versions) ? row.versions : [];
      let c = vs.filter(v => localVersionIds[v.id]).length;
      if (c === 0 && row.version_count > 0) {
        for (const info of Object.values(localVersionIds)) {
          if (info.model_id === row.id) c++;
        }
      }
      return c;
    },
  };

  const VERSION_SORT_ACCESSORS = {
    'Model ID': (row, ctx) => ctx.model.id,
    'Model Name': (row, ctx) => ctx.model.name,
    'Version ID': row => row.id,
    'Version Name': row => row.name,
    'Base Model': row => row.base_model,
    'Status': row => row.status,
    'Downloads': row => row.rank?.download_count ?? 0,
    'Gallery': row => galleryVersionCounts[row.id] ?? 0,
    'Local': row => (localConfigured === false ? -1 : (localVersionIds && localVersionIds[row.id] ? 1 : 0)),
  };

  function setHeaderCells(headers) {
    tableHeader.innerHTML = headers.map(h => {
      const cls = [];
      if (sortColumn === h) {
        cls.push('sort-active');
        cls.push(sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');
      }
      cls.push('sortable');
      return `<th class="${cls.join(' ')}" data-col="${h}">${h}</th>`;
    }).join('');

    // Bind click handlers for sorting
    tableHeader.querySelectorAll('th.sortable').forEach(th => {
      th.addEventListener('click', () => {
        const col = th.getAttribute('data-col');
        if (sortColumn === col) {
          sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
        } else {
          sortColumn = col;
          sortDirection = 'asc';
        }
        renderRows();
      });
    });
  }

  // ---- Cached data for renderRows() ----
  let cachedRows = [];
  let cachedExpand = false;

  function _localCountForModel(row) {
    if (!localVersionIds) return 0;
    const vs = Array.isArray(row.versions) ? row.versions : [];
    let c = vs.filter(v => localVersionIds[v.id]).length;
    if (c === 0 && row.version_count > 0) {
      for (const info of Object.values(localVersionIds)) {
        if (info.model_id === row.id) c++;
      }
    }
    return c;
  }

  function _sortRows(rows, accessors) {
    if (!sortColumn) return rows;
    const accessor = accessors[sortColumn];
    if (!accessor) return rows;

    const isNumeric = sortColumn === 'Model ID' || sortColumn === 'Version ID'
      || sortColumn === 'Downloads' || sortColumn === 'Versions'
      || sortColumn === 'In Use' || sortColumn === 'Gallery' || sortColumn === 'Local';

    const dir = sortDirection === 'asc' ? 1 : -1;
    const needsCtx = accessor.length > 1;

    return [...rows].sort((a, b) => {
      const aVal = needsCtx ? accessor(a.item || a, a) : accessor(a.item || a);
      const bVal = needsCtx ? accessor(b.item || b, b) : accessor(b.item || b);

      if (aVal == null && bVal == null) return 0;
      if (aVal == null) return 1;
      if (bVal == null) return -1;

      if (isNumeric) {
        return ((aVal - bVal) || 0) * dir;
      }
      return String(aVal).localeCompare(String(bVal)) * dir;
    });
  }

  function renderRows() {
    const expand = cachedExpand;
    setHeaderCells(expand ? VERSION_HEADERS : MODEL_HEADERS);
    tbody.innerHTML = '';

    if (expand) {
      // Flatten versions into sortable items: {item: version, model}
      const items = [];
      cachedRows.forEach((model) => {
        const versions = Array.isArray(model.versions) ? model.versions : [];
        if (versions.length === 0) {
          items.push({ item: null, model });
        } else {
          versions.forEach(v => items.push({ item: v, model }));
        }
      });

      const sorted = _sortRows(items, VERSION_SORT_ACCESSORS);

      sorted.forEach(({ item: v, model }) => {
        const tr = document.createElement('tr');
        if (!v) {
          // Model with no versions
          tr.innerHTML = [
            `<td>${model.id ?? '\u2014'}</td>`,
            `<td>${model.name ?? '\u2014'}</td>`,
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
            '<td class="muted">\u2014</td>',
          ].join('');
        } else {
          const vDownloads = v.rank?.download_count ?? 0;
          tr.innerHTML = [
            `<td>${model.id ?? '\u2014'}</td>`,
            `<td>${model.name ?? '\u2014'}</td>`,
            `<td>${v.id ?? '\u2014'}</td>`,
            `<td>${v.name ?? '\u2014'}</td>`,
            `<td>${v.base_model ?? '\u2014'}</td>`,
            `<td>${v.status ?? '\u2014'}</td>`,
            `<td>${vDownloads}</td>`,
            _galleryCount(galleryVersionCounts[v.id] ?? 0),
            _localCheck(v.id),
          ].join('');
        }
        tbody.appendChild(tr);
      });
    } else {
      const sorted = _sortRows(cachedRows, MODEL_SORT_ACCESSORS);

      sorted.forEach((row) => {
        const tr = document.createElement('tr');
        const downloads = row.rank?.download_count ?? 0;
        const author = row.author?.username ?? '\u2014';
        const versionCount = row.version_count ?? 0;
        const localCount = _localCountForModel(row);
        const galleryTotal = galleryModelCounts[row.id] ?? 0;
        tr.innerHTML = [
          `<td>${row.id ?? '\u2014'}</td>`,
          `<td>${row.name ?? '\u2014'}</td>`,
          `<td>${row.type ?? '\u2014'}</td>`,
          `<td>${row.status ?? '\u2014'}</td>`,
          `<td>${author}</td>`,
          `<td>${downloads}</td>`,
          `<td>${versionCount}</td>`,
          _galleryCount(galleryTotal),
          _localBadge(localCount, versionCount),
        ].join('');
        tbody.appendChild(tr);
      });
    }
  }

  async function loadTable() {
    const expand = showVersionsCheck.checked;

    try {
      const [resp] = await Promise.all([
        fetch(modelsListUrl()),
        fetchLocalVersionIds(),
        fetchGalleryUsageCounts(),
      ]);
      const data = await resp.json();
      if (!resp.ok) {
        toast(data.detail || 'Failed loading model table', 'error');
        return;
      }

      total = Number(data.total || 0);
      cachedRows = Array.isArray(data.items) ? data.items : [];
      cachedExpand = expand;

      renderRows();

      const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      pageIndicator.textContent = `Page ${currentPage} of ${totalPages}`;
      tableInfo.textContent = `${total} ${typeLabel} models`;
      prevPageBtn.disabled = currentPage <= 1;
      nextPageBtn.disabled = currentPage >= totalPages;
    } catch (err) {
      toast(`Network error: ${err.message}`, 'error');
    }
  }

  rescanBtn.addEventListener('click', () => {
    const fetchDetails = rescanFetchDetails.checked;
    const limit = (rescanLimit.value || '').trim();

    const query = { fetch_details: fetchDetails ? '1' : '0' };
    if (rescanMissingOnly.checked) query.missing_only = '1';
    if (limit) query.limit = limit;

    // Disable button during scan
    rescanBtn.disabled = true;
    setStatus(rescanStatus, `Rescanning ${typeLabel} model references…`, 'info');

    const url = maintenanceUrl('rescan-gallery/stream', query);
    const evtSource = new EventSource(url);

    let lastProgress = null;

    evtSource.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }

      if (data.type === 'progress') {
        lastProgress = data;
        const total = data.total_refs || '?';
        const pct = data.total_refs ? Math.round((data.scanned / data.total_refs) * 100) : '';
        const models = data.resolved_models || 0;
        const versions = data.scanned;
        const totalVersions = data.total_refs || '?';
        setStatus(
          rescanStatus,
          `Scanning ${versions}/${totalVersions} versions${pct ? ` (${pct}%)` : ''} · ` +
          `${models} models resolved — ` +
          `Upserted: ${data.upserted}, Failed: ${data.failed}, ` +
          `Removed: ${data.marked_removed || 0}`,
          'info',
        );
      } else if (data.type === 'complete') {
        evtSource.close();
        rescanBtn.disabled = false;
        showJson(rescanResults, data);
        const parts = [`Upserted: ${data.upserted || 0}`];
        if (data.marked_removed) parts.push(`Removed (404): ${data.marked_removed}`);
        if (data.failed) parts.push(`Failed: ${data.failed}`);
        setStatus(rescanStatus, `Rescan completed. ${parts.join(', ')}`, 'success');
        toast('Rescan complete', 'success');
        loadTable();
      } else if (data.type === 'error') {
        evtSource.close();
        rescanBtn.disabled = false;
        setStatus(rescanStatus, data.detail || 'Rescan failed', 'error');
        toast(data.detail || 'Rescan failed', 'error');
      }
    };

    evtSource.onerror = () => {
      evtSource.close();
      rescanBtn.disabled = false;
      if (lastProgress) {
        // Stream closed unexpectedly — show partial results if we got any
        showJson(rescanResults, lastProgress);
        setStatus(rescanStatus, 'Rescan interrupted (connection lost)', 'error');
      } else {
        setStatus(rescanStatus, 'Rescan failed (connection error)', 'error');
      }
      toast('Rescan connection error', 'error');
    };
  });

  // ---- Model Observation Rescan (SSE) ----
  let obsEventSource = null;

  function updateObsMetric(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function resetObsUI() {
    if (obsRescanStatus) {
      obsRescanStatus.textContent = '';
      obsRescanStatus.className = 'step-status';
    }
    if (obsProgressWrap) obsProgressWrap.hidden = true;
    if (obsProgressBar) obsProgressBar.style.width = '0%';
    if (obsProgressLabel) obsProgressLabel.textContent = '0 / 0 images';
    if (obsMetrics) obsMetrics.hidden = true;
    if (obsRescanResults) {
      obsRescanResults.hidden = true;
      obsRescanResults.textContent = '';
    }
    updateObsMetric('metric-models-processed', '0');
    updateObsMetric('metric-unique-models', '0');
    updateObsMetric('metric-pre-existing', '0');
    updateObsMetric('metric-new-models', '0');
    updateObsMetric('metric-obs-created', '0');
    updateObsMetric('metric-obs-skipped', '0');
  }

  if (obsRescanBtn) {
    obsRescanBtn.addEventListener('click', () => {
      if (obsEventSource) {
        obsEventSource.close();
        obsEventSource = null;
      }
      resetObsUI();

      const dryRun = obsDryRun ? obsDryRun.checked : false;
      obsRescanBtn.disabled = true;
      if (obsRescanCard) obsRescanCard.classList.add('active');
      setStatus(
        obsRescanStatus,
        dryRun ? 'Dry run: scanning sidecar files…' : 'Scanning sidecar files…',
        'info',
      );
      if (obsProgressWrap) obsProgressWrap.hidden = false;
      if (obsMetrics) obsMetrics.hidden = false;

      const url = maintenanceUrl('rescan-observations/stream', {
        dry_run: dryRun ? '1' : '0',
        model_type: requestedType,
      });

      obsEventSource = new EventSource(url);

      obsEventSource.addEventListener('progress', (e) => {
        try {
          const d = JSON.parse(e.data);
          const pct = d.total_images > 0 ? Math.round((d.current_image / d.total_images) * 100) : 0;
          if (obsProgressBar) obsProgressBar.style.width = `${pct}%`;
          if (obsProgressLabel) obsProgressLabel.textContent = `${d.current_image} / ${d.total_images} images (${pct}%)`;
          updateObsMetric('metric-models-processed', d.models_processed ?? 0);
          updateObsMetric('metric-unique-models', d.unique_models ?? 0);
          updateObsMetric('metric-pre-existing', d.pre_existing ?? 0);
          updateObsMetric('metric-new-models', d.new_models ?? 0);
          updateObsMetric('metric-obs-created', d.observations_created ?? 0);
          updateObsMetric('metric-obs-skipped', d.observations_skipped ?? 0);
        } catch (_) { /* ignore parse errors */ }
      });

      obsEventSource.addEventListener('error_event', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (obsRescanResults) {
            obsRescanResults.hidden = false;
            obsRescanResults.textContent += `Error (image ${d.current_image}): ${d.error}\n`;
          }
        } catch (_) { /* ignore */ }
      });

      obsEventSource.addEventListener('complete', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (obsProgressBar) obsProgressBar.style.width = '100%';
          if (obsProgressLabel) obsProgressLabel.textContent = `${d.total_images} / ${d.total_images} images (100%)`;
          updateObsMetric('metric-models-processed', d.models_processed ?? 0);
          updateObsMetric('metric-unique-models', d.unique_models ?? 0);
          updateObsMetric('metric-pre-existing', d.pre_existing ?? 0);
          updateObsMetric('metric-new-models', d.new_models ?? 0);
          updateObsMetric('metric-obs-created', d.observations_created ?? 0);
          updateObsMetric('metric-obs-skipped', d.observations_skipped ?? 0);

          const prefix = d.dry_run ? '[DRY RUN] ' : '';
          setStatus(
            obsRescanStatus,
            `${prefix}Scan complete. ${d.unique_models} unique models, ` +
            `${d.observations_created} observations created, ` +
            `${d.observations_skipped} skipped.`,
            'success',
          );
          toast(`${prefix}Model observation scan complete.`, 'success');
        } catch (_) { /* ignore */ }

        obsEventSource.close();
        obsEventSource = null;
        obsRescanBtn.disabled = false;
        if (obsRescanCard) obsRescanCard.classList.remove('active');
      });

      obsEventSource.onerror = () => {
        setStatus(obsRescanStatus, 'Connection lost or server error.', 'error');
        if (obsEventSource) {
          obsEventSource.close();
          obsEventSource = null;
        }
        obsRescanBtn.disabled = false;
        if (obsRescanCard) obsRescanCard.classList.remove('active');
      };
    });
  }

  importBtn.addEventListener('click', async () => {
    const file = importFile.files[0];
    if (!file) {
      toast('Select a JSON file first', 'error');
      return;
    }

    const dryRun = importDryRun.checked;
    try {
      const text = await file.text();
      const payload = JSON.parse(text);
      const resp = await fetch(maintenanceUrl('import', { dry_run: dryRun ? '1' : '0' }), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      showJson(importOutput, data);
      if (!resp.ok) {
        toast(data.detail || 'Import failed', 'error');
        return;
      }
      toast(`${dryRun ? '[DRY RUN] ' : ''}Import complete`, 'success');
      if (!dryRun) loadTable();
    } catch (err) {
      toast(`Import failed: ${err.message}`, 'error');
    }
  });

  exportBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch(maintenanceUrl('export'));
      const data = await resp.json();
      showJson(exportOutput, data);
      if (!resp.ok) {
        toast(data.detail || 'Export failed', 'error');
        return;
      }
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${requestedType}-models-export.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast(`Exported ${data.total || 0} records`, 'success');
    } catch (err) {
      toast(`Export failed: ${err.message}`, 'error');
    }
  });

  purgeBtn.addEventListener('click', async () => {
    const dryRun = purgeDryRun.checked;
    if (!dryRun && !window.confirm(`Purge all ${typeLabel} model records?`)) {
      return;
    }
    try {
      const resp = await fetch(maintenanceUrl('purge', { dry_run: dryRun ? '1' : '0' }), {
        method: 'POST',
      });
      const data = await resp.json();
      showJson(purgeOutput, data);
      if (!resp.ok) {
        toast(data.detail || 'Purge failed', 'error');
        return;
      }
      toast(`${dryRun ? '[DRY RUN] ' : ''}Purge complete`, 'success');
      if (!dryRun) loadTable();
    } catch (err) {
      toast(`Purge failed: ${err.message}`, 'error');
    }
  });

  localCatalogBtn.addEventListener('click', async () => {
    setStatus(localCatalogStatus, 'Loading local catalog...', 'info');
    try {
      const resp = await fetch(maintenanceUrl('local-catalog'));
      const data = await resp.json();
      showJson(localCatalogResults, data);
      if (!resp.ok) {
        setStatus(localCatalogStatus, data.detail || 'Local catalog failed', 'error');
        toast(data.detail || 'Local catalog failed', 'error');
        return;
      }
      setStatus(localCatalogStatus, `Loaded ${data.total || 0} local ${typeLabel} entries`, 'success');
      toast('Local catalog loaded', 'success');
    } catch (err) {
      setStatus(localCatalogStatus, `Network error: ${err.message}`, 'error');
      toast(`Network error: ${err.message}`, 'error');
    }
  });

  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      currentPage = 1;
      loadTable();
    }, 300);
  });

  showVersionsCheck.addEventListener('change', () => {
    currentPage = 1;
    loadTable();
  });

  refreshTableBtn.addEventListener('click', () => loadTable());
  prevPageBtn.addEventListener('click', () => {
    if (currentPage > 1) {
      currentPage -= 1;
      loadTable();
    }
  });
  nextPageBtn.addEventListener('click', () => {
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage < totalPages) {
      currentPage += 1;
      loadTable();
    }
  });

  loadTable();
})();
