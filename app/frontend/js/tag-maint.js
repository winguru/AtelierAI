// ── Memory ───────────────────────────────────────────────────────────────────
// 📄 docs: app/docs/memories/taxonomy-import.md
// ──────────────────────────────────────────────────────────────────────────────
(() => {
  'use strict';

  // ── Source from query param ──
  const params = new URLSearchParams(window.location.search);
  const source = (params.get('source') || '').trim().toLowerCase();
  const validSources = ['civitai', 'danbooru', 'prompt', 'user'];
  if (!validSources.includes(source)) {
    document.body.innerHTML = '<p style="padding:2rem;color:#dc2626;">Invalid source. Use ?source=civitai|danbooru|prompt|user</p>';
    return;
  }

  const sourceLabels = { civitai: 'CivitAI', danbooru: 'Danbooru', prompt: 'Prompt', user: 'User' };

  // ── DOM refs ──
  const pageTitle = document.getElementById('page-title');
  const themeToggle = document.getElementById('theme-toggle');
  const importFile = document.getElementById('import-file');
  const importDryRun = document.getElementById('import-dry-run');
  const importBtn = document.getElementById('import-btn');
  const importOutput = document.getElementById('import-output');
  const exportBtn = document.getElementById('export-btn');
  const exportOutput = document.getElementById('export-output');
  const purgeDryRun = document.getElementById('purge-dry-run');
  const purgeBtn = document.getElementById('purge-btn');
  const purgeOutput = document.getElementById('purge-output');
  const snapshotExportUserBindings = document.getElementById('snapshot-export-user-bindings');
  const snapshotExportBtn = document.getElementById('snapshot-export-btn');
  const snapshotExportOutput = document.getElementById('snapshot-export-output');
  const snapshotImportFile = document.getElementById('snapshot-import-file');
  const snapshotImportDryRun = document.getElementById('snapshot-import-dry-run');
  const snapshotImportBtn = document.getElementById('snapshot-import-btn');
  const snapshotImportOutput = document.getElementById('snapshot-import-output');
  const snapshotRebuildObservations = document.getElementById('snapshot-rebuild-observations');
  const snapshotObservationsMode = document.getElementById('snapshot-observations-mode');
  const snapshotRebuildPromptTags = document.getElementById('snapshot-rebuild-prompt-tags');
  const snapshotPromptMode = document.getElementById('snapshot-prompt-mode');
  const snapshotRebuildDanbooruTags = document.getElementById('snapshot-rebuild-danbooru-tags');
  const snapshotDanbooruMode = document.getElementById('snapshot-danbooru-mode');
  const snapshotRebuildUserTags = document.getElementById('snapshot-rebuild-user-tags');
  const snapshotUserMode = document.getElementById('snapshot-user-mode');
  const snapshotBackfillCivitaiIds = document.getElementById('snapshot-backfill-civitai-ids');
  const snapshotFetchCivitaiMetadata = document.getElementById('snapshot-fetch-civitai-metadata');
  const snapshotCivitaiFetchLimit = document.getElementById('snapshot-civitai-fetch-limit');
  const snapshotRestoreUserBindings = document.getElementById('snapshot-restore-user-bindings');
  const searchInput = document.getElementById('search-input');
  const selectAllBtn = document.getElementById('select-all-btn');
  const deselectAllBtn = document.getElementById('deselect-all-btn');
  const bulkDryRun = document.getElementById('bulk-dry-run');
  const bulkDeleteBtn = document.getElementById('bulk-delete-btn');
  const selectionCount = document.getElementById('selection-count');
  const headerCheck = document.getElementById('header-check');
  const tagTbody = document.getElementById('tag-tbody');
  const tableInfo = document.getElementById('table-info');
  const prevPageBtn = document.getElementById('prev-page-btn');
  const nextPageBtn = document.getElementById('next-page-btn');
  const pageIndicator = document.getElementById('page-indicator');
  const toastContainer = document.getElementById('toast-container');

  // ── Theme ──
  pageTitle.textContent = `${sourceLabels[source]} Tag Maintenance`;
  const preferences = window.AtelierPreferences || null;
  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  // ── State ──
  const PAGE_SIZE = 100;
  let currentPage = 1;
  let sortCol = 'name';
  let sortDir = 'asc';
  let totalRows = 0;
  const selectedIds = new Set();
  let editingCell = null; // { td, termId, field, originalValue }

  // ── Helpers ──
  function apiUrl(path) {
    return `/api/taxonomy/tag-maint/${source}${path}`;
  }

  function toast(message, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    toastContainer.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function showOutput(el, data) {
    el.hidden = false;
    el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  }

  // ── Load Table ──
  async function loadTable() {
    const q = new URLSearchParams({
      page: currentPage,
      page_size: PAGE_SIZE,
      sort_col: sortCol,
      sort_dir: sortDir,
    });
    const search = searchInput.value.trim();
    if (search) q.set('search', search);

    try {
      const resp = await fetch(`${apiUrl('/list')}?${q}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        toast(err.detail || `Load failed: ${resp.status}`, 'error');
        return;
      }
      const data = await resp.json();
      totalRows = data.total || 0;
      renderTable(data.cols, data.rows);
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    }
  }

  function renderTable(cols, rows) {
    tagTbody.innerHTML = '';
    headerCheck.checked = false;

    for (const row of rows) {
      const tr = document.createElement('tr');
      const termId = row[0];

      // Checkbox
      const tdCheck = document.createElement('td');
      tdCheck.className = 'col-check';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = selectedIds.has(termId);
      cb.addEventListener('change', () => {
        if (cb.checked) selectedIds.add(termId);
        else selectedIds.delete(termId);
        updateSelectionUI();
      });
      tdCheck.appendChild(cb);
      tr.appendChild(tdCheck);

      // Data cells (cols: id, name, ext_id, scope, post_count, concept_id, mdtag_id, mdtag_name)
      const fieldNames = ['id', 'name', 'ext_id', 'scope', 'post_count', 'concept_id', 'mdtag_id', 'mdtag_name'];
      const editableFields = new Set(['name', 'ext_id', 'concept_id']);
      const fieldToCol = { name: 'external_name', ext_id: 'external_tag_id', concept_id: 'concept_id' };

      for (let i = 0; i < fieldNames.length; i++) {
        const td = document.createElement('td');
        const cellValue = row[i];
        td.textContent = cellValue != null ? String(cellValue) : '—';
        td.dataset.field = fieldNames[i];
        td.dataset.termId = termId;

        if (editableFields.has(fieldNames[i])) {
          td.classList.add('editable');
          td.addEventListener('dblclick', () => startEdit(td, termId, fieldToCol[fieldNames[i]] || fieldNames[i], cellValue));
        }
        tr.appendChild(td);
      }

      if (selectedIds.has(termId)) tr.classList.add('selected');
      tagTbody.appendChild(tr);
    }

    // Update footer
    const totalPages = Math.max(1, Math.ceil(totalRows / PAGE_SIZE));
    tableInfo.textContent = `${totalRows} tags total`;
    pageIndicator.textContent = `Page ${currentPage} of ${totalPages}`;
    prevPageBtn.disabled = currentPage <= 1;
    nextPageBtn.disabled = currentPage >= totalPages;

    // Update sort arrows
    document.querySelectorAll('#tag-table th.sortable').forEach(th => {
      const arrow = th.querySelector('.sort-arrow');
      arrow.className = 'sort-arrow';
      if (th.dataset.col === sortCol) {
        arrow.classList.add(sortDir);
      }
    });

    updateSelectionUI();
  }

  function updateSelectionUI() {
    const count = selectedIds.size;
    selectionCount.hidden = count === 0;
    selectionCount.textContent = `${count} selected`;
    bulkDeleteBtn.disabled = count === 0;
  }

  // ── Inline Edit ──
  function startEdit(td, termId, field, currentValue) {
    if (editingCell) cancelEdit();

    const input = document.createElement('input');
    input.type = 'text';
    input.value = currentValue != null ? String(currentValue) : '';
    td.textContent = '';
    td.classList.add('editing');
    td.appendChild(input);
    input.focus();
    input.select();

    const commit = () => commitEdit(input);
    const cancel = () => cancelEdit();

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
    input.addEventListener('blur', commit);
    editingCell = { td, termId, field, originalValue: currentValue, input, blurHandler: commit };
  }

  async function commitEdit(input) {
    if (!editingCell) return;
    const { td, termId, field, originalValue, blurHandler } = editingCell;
    const newValue = input.value.trim();

    // Remove blur handler to prevent re-trigger
    input.removeEventListener('blur', blurHandler);
    editingCell = null;

    if (newValue === (originalValue != null ? String(originalValue) : '')) {
      restoreCell(td, originalValue);
      return;
    }

    try {
      const resp = await fetch(apiUrl('/update'), {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          authority_term_id: termId,
          field,
          value: field === 'concept_id' ? (newValue ? parseInt(newValue, 10) : null) : newValue,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        toast(err.detail || 'Update failed', 'error');
        restoreCell(td, originalValue);
        return;
      }
      const result = await resp.json().catch(() => ({}));
      if (result.images_updated > 0) {
        toast(`Tag updated — ${result.images_updated} image${result.images_updated === 1 ? '' : 's'} synced.`, 'success');
      } else {
        toast('Tag updated.', 'success');
      }
      loadTable();
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
      restoreCell(td, originalValue);
    }
  }

  function cancelEdit() {
    if (!editingCell) return;
    const { td, originalValue, input, blurHandler } = editingCell;
    if (input && blurHandler) input.removeEventListener('blur', blurHandler);
    editingCell = null;
    restoreCell(td, originalValue);
  }

  function restoreCell(td, value) {
    td.classList.remove('editing');
    td.textContent = value != null ? String(value) : '—';
  }

  // ── Sort ──
  document.querySelectorAll('#tag-table th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol === col) {
        sortDir = sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        sortCol = col;
        sortDir = 'asc';
      }
      currentPage = 1;
      loadTable();
    });
  });

  // ── Search ──
  let searchTimer = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      currentPage = 1;
      loadTable();
    }, 350);
  });

  // ── Pagination ──
  prevPageBtn.addEventListener('click', () => { if (currentPage > 1) { currentPage--; loadTable(); } });
  nextPageBtn.addEventListener('click', () => { currentPage++; loadTable(); });

  // ── Select All / Deselect ──
  selectAllBtn.addEventListener('click', () => {
    tagTbody.querySelectorAll('tr').forEach(tr => {
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) { cb.checked = true; selectedIds.add(parseInt(cb.closest('tr').querySelector('[data-field="id"]').textContent, 10)); }
    });
    updateSelectionUI();
  });

  deselectAllBtn.addEventListener('click', () => {
    selectedIds.clear();
    tagTbody.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = false; });
    tagTbody.querySelectorAll('tr.selected').forEach(tr => tr.classList.remove('selected'));
    headerCheck.checked = false;
    updateSelectionUI();
  });

  headerCheck.addEventListener('change', () => {
    const checked = headerCheck.checked;
    tagTbody.querySelectorAll('tr').forEach(tr => {
      const cb = tr.querySelector('input[type="checkbox"]');
      if (cb) {
        cb.checked = checked;
        const id = parseInt(tr.querySelector('[data-field="id"]').textContent, 10);
        if (checked) selectedIds.add(id); else selectedIds.delete(id);
        tr.classList.toggle('selected', checked);
      }
    });
    updateSelectionUI();
  });

  // ── Bulk Delete ──
  bulkDeleteBtn.addEventListener('click', async () => {
    if (selectedIds.size === 0) return;
    const dryRun = bulkDryRun.checked;
    const ids = Array.from(selectedIds);
    if (!dryRun && !confirm(`Delete ${ids.length} tag(s)? This cannot be undone.`)) return;

    try {
      const resp = await fetch(apiUrl('/bulk-delete'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ authority_term_ids: ids, dry_run: dryRun }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        toast(data.detail || 'Bulk delete failed', 'error');
        return;
      }
      toast(`${dryRun ? '[DRY RUN] ' : ''}Deleted ${data.deleted} tag(s).`, 'success');
      if (!dryRun) {
        selectedIds.clear();
        loadTable();
      }
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    }
  });

  // ── Import ──
  importBtn.addEventListener('click', async () => {
    const file = importFile.files[0];
    if (!file) { toast('Select a file first.', 'error'); return; }
    const dryRun = importDryRun.checked;

    try {
      const text = await file.text();
      // Detect format from extension
      const ext = file.name.split('.').pop().toLowerCase();
      const format = ext === 'csv' ? 'csv' : 'json';

      const resp = await fetch('/api/taxonomy/bootstrap/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          authority_name: source,
          format,
          raw_text: text,

          dry_run: dryRun,
        }),
      });
      const data = await resp.json();
      showOutput(importOutput, data);
      if (!resp.ok) {
        toast(data.detail || 'Import failed', 'error');
        return;
      }
      toast(`${dryRun ? '[DRY RUN] ' : ''}Import complete.`, 'success');
      if (!dryRun) loadTable();
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    }
  });

  // ── Export ──
  exportBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch(apiUrl('/export'));
      const data = await resp.json();
      if (!resp.ok) {
        toast(data.detail || 'Export failed', 'error');
        return;
      }
      showOutput(exportOutput, `Exported ${data.total} terms at ${data.exported_at}`);

      // Trigger download
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${source}-tags-export.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast(`Exported ${data.total} tags.`, 'success');
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    }
  });

  // ── Purge ──
  purgeBtn.addEventListener('click', async () => {
    const dryRun = purgeDryRun.checked;
    if (!dryRun && !confirm(`Purge ALL ${sourceLabels[source]} tags? This cannot be undone.`)) return;

    try {
      const resp = await fetch(apiUrl('/purge'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run }),
      });
      const data = await resp.json();
      showOutput(purgeOutput, data);
      if (!resp.ok) {
        toast(data.detail || 'Purge failed', 'error');
        return;
      }
      toast(`${dryRun ? '[DRY RUN] ' : ''}Purged ${data.deleted} tag(s).`, 'success');
      if (!dryRun) loadTable();
    } catch (e) {
      toast(`Network error: ${e.message}`, 'error');
    }
  });

  // ── Scan Missing (SSE) ──
  const scanMissingBtn = document.getElementById('scan-missing-btn');
  const scanMissingDryRun = document.getElementById('scan-missing-dry-run');
  const scanMissingApiLimit = document.getElementById('scan-missing-api-limit');
  const scanMissingStatus = document.getElementById('scan-missing-status');
  const scanMissingProgressWrap = document.getElementById('scan-missing-progress-wrap');
  const scanMissingProgressBar = document.getElementById('scan-missing-progress-bar');
  const scanMissingProgressLabel = document.getElementById('scan-missing-progress-label');
  const scanMissingPhase1Metrics = document.getElementById('scan-missing-phase1-metrics');
  const scanMissingPhase2Metrics = document.getElementById('scan-missing-phase2-metrics');
  const scanMissingPhase1Label = document.getElementById('sm-phase1-label');
  const scanMissingPhase2Label = document.getElementById('sm-phase2-label');
  const scanMissingResults = document.getElementById('scan-missing-results');
  const scanMissingCard = document.getElementById('scan-missing-card');
  const scanMissingTierIndicator = document.getElementById('scan-missing-tier-indicator');
  const scanMissingTierBadge = document.getElementById('scan-missing-tier-badge');
  const scanMissingTierDesc = document.getElementById('scan-missing-tier-desc');
  let scanMissingEventSource = null;

  function setScanMissingStatus(text, type) {
    if (scanMissingStatus) {
      scanMissingStatus.textContent = text;
      scanMissingStatus.className = `step-status ${type}`;
    }
  }

  function updateScanMissingMetric(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function resetScanMissingUI() {
    setScanMissingStatus('', '');
    if (scanMissingProgressWrap) scanMissingProgressWrap.hidden = true;
    if (scanMissingProgressBar) scanMissingProgressBar.style.width = '0%';
    if (scanMissingProgressLabel) scanMissingProgressLabel.textContent = '0 / 0 images';
    if (scanMissingPhase1Label) scanMissingPhase1Label.hidden = true;
    if (scanMissingPhase1Metrics) scanMissingPhase1Metrics.hidden = true;
    if (scanMissingPhase2Label) scanMissingPhase2Label.hidden = true;
    if (scanMissingPhase2Metrics) scanMissingPhase2Metrics.hidden = true;
    if (scanMissingResults) { scanMissingResults.hidden = true; scanMissingResults.textContent = ''; }
    if (scanMissingTierIndicator) scanMissingTierIndicator.hidden = true;
    // Phase 1 — Image resolution
    updateScanMissingMetric('sm-metric-missing', '0');
    updateScanMissingMetric('sm-metric-processed', '0');
    updateScanMissingMetric('sm-metric-tier1', '0');
    updateScanMissingMetric('sm-metric-tier2', '0');
    updateScanMissingMetric('sm-metric-tier3', '0');
    updateScanMissingMetric('sm-metric-errors', '0');
    // Phase 2 — Tag processing (Rescan Gallery parity)
    updateScanMissingMetric('sm-metric-tags-processed', '0');
    updateScanMissingMetric('sm-metric-unique-tags', '0');
    updateScanMissingMetric('sm-metric-preexisting', '0');
    updateScanMissingMetric('sm-metric-new-tags', '0');
    updateScanMissingMetric('sm-metric-obs-created', '0');
    updateScanMissingMetric('sm-metric-obs-skipped', '0');
  }

  const tierDescriptions = {
    1: 'Sidecar JSON files',
    2: 'Archived API response files',
    3: 'Live CivitAI API',
  };

  if (scanMissingBtn) {
    scanMissingBtn.addEventListener('click', async () => {
      if (scanMissingEventSource) {
        scanMissingEventSource.close();
        scanMissingEventSource = null;
      }
      resetScanMissingUI();

      const dryRun = scanMissingDryRun ? scanMissingDryRun.checked : true;
      const apiLimitParsed = scanMissingApiLimit ? parseInt(scanMissingApiLimit.value, 10) : NaN;
      const apiLimit = Number.isNaN(apiLimitParsed) ? 100 : apiLimitParsed;
      scanMissingBtn.disabled = true;
      scanMissingCard.classList.add('active');
      setScanMissingStatus(dryRun ? 'Dry run: scanning for missing tag observations…' : 'Scanning for missing tag observations…', 'info');
      if (scanMissingProgressWrap) scanMissingProgressWrap.hidden = false;
      if (scanMissingPhase1Label) scanMissingPhase1Label.hidden = false;
      if (scanMissingPhase1Metrics) scanMissingPhase1Metrics.hidden = false;
      if (scanMissingPhase2Label) scanMissingPhase2Label.hidden = false;
      if (scanMissingPhase2Metrics) scanMissingPhase2Metrics.hidden = false;

      const url = `/api/taxonomy/tag-maint/civitai/scan-missing?dry_run=${dryRun ? '1' : '0'}&api_limit=${apiLimit}`;
      scanMissingEventSource = new EventSource(url);

      scanMissingEventSource.addEventListener('tier_start', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (scanMissingTierIndicator) scanMissingTierIndicator.hidden = false;
          if (scanMissingTierBadge) {
            scanMissingTierBadge.textContent = `Tier ${d.tier}`;
            scanMissingTierBadge.className = `tier-badge tier-${d.tier}`;
          }
          if (scanMissingTierDesc) scanMissingTierDesc.textContent = d.description || tierDescriptions[d.tier] || '';
        } catch (_) { /* ignore */ }
      });

      scanMissingEventSource.addEventListener('progress', (e) => {
        try {
          const d = JSON.parse(e.data);
          const totalImg = d.total_images ?? d.total_missing ?? 0;
          const pct = totalImg > 0 ? Math.round((d.processed / totalImg) * 100) : 0;
          if (scanMissingProgressBar) scanMissingProgressBar.style.width = `${pct}%`;
          if (scanMissingProgressLabel) scanMissingProgressLabel.textContent = `${d.processed} / ${totalImg} images (${pct}%)`;
          // Phase 1 — Image resolution
          updateScanMissingMetric('sm-metric-processed', totalImg);
          updateScanMissingMetric('sm-metric-missing', d.total_missing ?? 0);
          updateScanMissingMetric('sm-metric-tier1', d.tier1_resolved ?? 0);
          updateScanMissingMetric('sm-metric-tier2', d.tier2_resolved ?? 0);
          updateScanMissingMetric('sm-metric-tier3', d.tier3_resolved ?? 0);
          // Phase 2 — Tag processing
          updateScanMissingMetric('sm-metric-tags-processed', d.tags_processed ?? 0);
          updateScanMissingMetric('sm-metric-unique-tags', d.unique_tags ?? 0);
          updateScanMissingMetric('sm-metric-preexisting', d.pre_existing_tags ?? 0);
          updateScanMissingMetric('sm-metric-new-tags', d.new_tags ?? 0);
          updateScanMissingMetric('sm-metric-obs-created', d.observations_created ?? 0);
          updateScanMissingMetric('sm-metric-obs-skipped', d.observations_skipped ?? 0);
        } catch (_) { /* ignore */ }
      });

      scanMissingEventSource.addEventListener('api_limit_warning', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (scanMissingResults) {
            scanMissingResults.hidden = false;
            scanMissingResults.textContent += `⚠ API limit reached: ${d.remaining} images remaining (limit: ${d.api_limit}, est. ${d.estimated_time_min} min for all)\n`;
          }
        } catch (_) { /* ignore */ }
      });

      scanMissingEventSource.addEventListener('error_event', (e) => {
        try {
          const d = JSON.parse(e.data);
          if (scanMissingResults) {
            scanMissingResults.hidden = false;
            scanMissingResults.textContent += `Error (image ${d.image_id || '?'}): ${d.error}\n`;
          }
          const errEl = document.getElementById('sm-metric-errors');
          if (errEl) errEl.textContent = parseInt(errEl.textContent || '0', 10) + 1;
        } catch (_) { /* ignore */ }
      });

      scanMissingEventSource.addEventListener('complete', (e) => {
        try {
          const d = JSON.parse(e.data);
          const totalImg = d.total_images ?? d.total_missing ?? 0;
          if (scanMissingProgressBar) scanMissingProgressBar.style.width = '100%';
          if (scanMissingProgressLabel) scanMissingProgressLabel.textContent = `${totalImg} / ${totalImg} images (100%)`;
          // Phase 1 — Image resolution
          updateScanMissingMetric('sm-metric-processed', totalImg);
          updateScanMissingMetric('sm-metric-missing', d.total_missing ?? 0);
          updateScanMissingMetric('sm-metric-tier1', d.tier1_resolved ?? 0);
          updateScanMissingMetric('sm-metric-tier2', d.tier2_resolved ?? 0);
          updateScanMissingMetric('sm-metric-tier3', d.tier3_resolved ?? 0);
          updateScanMissingMetric('sm-metric-errors', d.errors ?? 0);
          // Phase 2 — Tag processing
          updateScanMissingMetric('sm-metric-tags-processed', d.tags_processed ?? 0);
          updateScanMissingMetric('sm-metric-unique-tags', d.unique_tags ?? 0);
          updateScanMissingMetric('sm-metric-preexisting', d.pre_existing_tags ?? 0);
          updateScanMissingMetric('sm-metric-new-tags', d.new_tags ?? 0);
          updateScanMissingMetric('sm-metric-obs-created', d.observations_created ?? 0);
          updateScanMissingMetric('sm-metric-obs-skipped', d.observations_skipped ?? 0);

          const prefix = d.dry_run ? '[DRY RUN] ' : '';
          setScanMissingStatus(
            `${prefix}Scan complete. ${d.total_missing} missing images: T1=${d.tier1_resolved}, T2=${d.tier2_resolved}, T3=${d.tier3_resolved}. ${d.observations_created} observations created.`,
            d.errors > 0 ? 'warning' : 'success'
          );
          toast(`${prefix}Scan missing complete.`, d.errors > 0 ? 'warning' : 'success');
        } catch (_) { /* ignore */ }

        scanMissingEventSource.close();
        scanMissingEventSource = null;
        scanMissingBtn.disabled = false;
        scanMissingCard.classList.remove('active');

        if (!dryRun) loadTable();
      });

      scanMissingEventSource.onerror = () => {
        setScanMissingStatus('Connection lost or server error.', 'error');
        if (scanMissingEventSource) {
          scanMissingEventSource.close();
          scanMissingEventSource = null;
        }
        scanMissingBtn.disabled = false;
        scanMissingCard.classList.remove('active');
      };

  // ── Snapshot Export ──
  if (snapshotExportBtn) {
    snapshotExportBtn.addEventListener('click', async () => {
      const includeUserBindings = snapshotExportUserBindings ? snapshotExportUserBindings.checked : true;
      try {
        const q = new URLSearchParams({ include_user_bindings: includeUserBindings ? '1' : '0' });
        const resp = await fetch(`/api/taxonomy/snapshot/export?${q}`);
        const data = await resp.json();
        if (!resp.ok) {
          toast(data.detail || 'Snapshot export failed', 'error');
          showOutput(snapshotExportOutput, data);
          return;
        }
        showOutput(snapshotExportOutput, {
          message: 'Snapshot exported.',
          exported_at: data.exported_at,
          format: data.format,
          version: data.version,
          concepts: Array.isArray(data.concepts) ? data.concepts.length : 0,
          authority_terms: Array.isArray(data.authority_terms) ? data.authority_terms.length : 0,
          authorities: Array.isArray(data.authorities) ? data.authorities.length : 0,
          aliases: Array.isArray(data.aliases) ? data.aliases.length : 0,
          group_memberships: Array.isArray(data.group_memberships) ? data.group_memberships.length : 0,
          user_bindings: Array.isArray(data.user_bindings) ? data.user_bindings.length : 0,
        });

        const fileDate = (new Date()).toISOString().replace(/[:.]/g, '-');
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `taxonomy-snapshot-${fileDate}.json`;
        a.click();
        URL.revokeObjectURL(url);
        toast('Taxonomy snapshot exported.', 'success');
      } catch (e) {
        toast(`Network error: ${e.message}`, 'error');
      }
    });
  }

  function buildSnapshotPostImportOptions() {
    const rawLimit = parseInt(snapshotCivitaiFetchLimit?.value || '100', 10);
    const civitaiFetchLimit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 100;
    return {
      rebuild_observations: Boolean(snapshotRebuildObservations?.checked),
      observations_mode: snapshotObservationsMode?.value || 'merge',
      rebuild_prompt_tags: Boolean(snapshotRebuildPromptTags?.checked),
      prompt_rebuild_mode: snapshotPromptMode?.value || 'merge',
      rebuild_danbooru_tags: Boolean(snapshotRebuildDanbooruTags?.checked),
      danbooru_rebuild_mode: snapshotDanbooruMode?.value || 'merge',
      rebuild_user_tags: Boolean(snapshotRebuildUserTags?.checked),
      user_rebuild_mode: snapshotUserMode?.value || 'merge',
      backfill_missing_civitai_tag_ids: Boolean(snapshotBackfillCivitaiIds?.checked),
      fetch_missing_civitai_tag_metadata: Boolean(snapshotFetchCivitaiMetadata?.checked),
      civitai_fetch_limit: civitaiFetchLimit,
      restore_user_bindings_by_hash: Boolean(snapshotRestoreUserBindings?.checked),
    };
  }

  // ── Snapshot Import ──
  if (snapshotImportBtn) {
    snapshotImportBtn.addEventListener('click', async () => {
      const file = snapshotImportFile?.files?.[0];
      if (!file) {
        toast('Select a snapshot file first.', 'error');
        return;
      }
      const dryRun = snapshotImportDryRun ? snapshotImportDryRun.checked : true;
      let snapshot;
      try {
        const text = await file.text();
        snapshot = JSON.parse(text);
      } catch (e) {
        toast(`Invalid snapshot JSON: ${e.message}`, 'error');
        return;
      }

      const payload = {
        snapshot,
        dry_run: dryRun,
        post_import: buildSnapshotPostImportOptions(),
      };

      try {
        const resp = await fetch('/api/taxonomy/snapshot/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        showOutput(snapshotImportOutput, data);
        if (!resp.ok) {
          toast(data.detail || 'Snapshot import failed', 'error');
          return;
        }
        const conflictCount = Array.isArray(data.conflicts) ? data.conflicts.length : 0;
        toast(`${dryRun ? '[DRY RUN] ' : ''}Snapshot import complete${conflictCount ? ` (${conflictCount} conflict notes)` : ''}.`, 'success');
        if (!dryRun) {
          loadTable();
        }
      } catch (e) {
        toast(`Network error: ${e.message}`, 'error');
      }
    });
  }

  // ── Rescan (SSE) ──
  const rescanBtn = document.getElementById('rescan-btn');
  const rescanDryRun = document.getElementById('rescan-dry-run');
  const rescanStatus = document.getElementById('rescan-status');
  const rescanProgressWrap = document.getElementById('rescan-progress-wrap');
  const rescanProgressBar = document.getElementById('rescan-progress-bar');
  const rescanProgressLabel = document.getElementById('rescan-progress-label');
  const rescanMetrics = document.getElementById('rescan-metrics');
  const rescanResults = document.getElementById('rescan-results');
  const rescanCard = document.getElementById('rescan-card');
  let rescanEventSource = null;

  function setRescanStatus(text, type) {
    rescanStatus.textContent = text;
    rescanStatus.className = `step-status ${type}`;
  }

  function updateRescanMetric(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function resetRescanUI() {
    setRescanStatus('', '');
    rescanProgressWrap.hidden = true;
    rescanProgressBar.style.width = '0%';
    rescanProgressLabel.textContent = '0 / 0 images';
    rescanMetrics.hidden = true;
    rescanResults.hidden = true;
    rescanResults.textContent = '';
    updateRescanMetric('metric-tags-processed', '0');
    updateRescanMetric('metric-unique-tags', '0');
    updateRescanMetric('metric-preexisting', '0');
    updateRescanMetric('metric-new-tags', '0');
    updateRescanMetric('metric-obs-created', '0');
    updateRescanMetric('metric-obs-skipped', '0');
  }

  if (rescanBtn) {
    rescanBtn.addEventListener('click', async () => {
      if (rescanEventSource) {
        rescanEventSource.close();
        rescanEventSource = null;
      }
      resetRescanUI();

      const dryRun = rescanDryRun ? rescanDryRun.checked : false;
      rescanBtn.disabled = true;
      rescanCard.classList.add('active');
      setRescanStatus(dryRun ? 'Dry run: scanning gallery sidecars…' : 'Scanning gallery sidecars…', 'info');
      rescanProgressWrap.hidden = false;
      rescanMetrics.hidden = false;

      const url = `/api/taxonomy/tag-maint/civitai/rescan-observations?dry_run=${dryRun ? '1' : '0'}`;

      rescanEventSource = new EventSource(url);

      rescanEventSource.addEventListener('progress', (e) => {
        try {
          const d = JSON.parse(e.data);
          const pct = d.total_images > 0 ? Math.round((d.current_image / d.total_images) * 100) : 0;
          rescanProgressBar.style.width = `${pct}%`;
          rescanProgressLabel.textContent = `${d.current_image} / ${d.total_images} images (${pct}%)`;
          updateRescanMetric('metric-tags-processed', d.tags_processed ?? 0);
          updateRescanMetric('metric-unique-tags', d.unique_tags ?? 0);
          updateRescanMetric('metric-preexisting', d.pre_existing_tags ?? 0);
          updateRescanMetric('metric-new-tags', d.new_tags ?? 0);
          updateRescanMetric('metric-obs-created', d.observations_created ?? 0);
          updateRescanMetric('metric-obs-skipped', d.observations_skipped ?? 0);
        } catch (_) { /* ignore parse errors */ }
      });

      rescanEventSource.addEventListener('error_event', (e) => {
        try {
          const d = JSON.parse(e.data);
          rescanResults.hidden = false;
          rescanResults.textContent += `Error (image ${d.current_image}): ${d.error}\n`;
        } catch (_) { /* ignore */ }
      });

      rescanEventSource.addEventListener('complete', (e) => {
        try {
          const d = JSON.parse(e.data);
          rescanProgressBar.style.width = '100%';
          rescanProgressLabel.textContent = `${d.total_images} / ${d.total_images} images (100%)`;
          updateRescanMetric('metric-tags-processed', d.tags_processed ?? 0);
          updateRescanMetric('metric-unique-tags', d.unique_tags ?? 0);
          updateRescanMetric('metric-preexisting', d.pre_existing_tags ?? 0);
          updateRescanMetric('metric-new-tags', d.new_tags ?? 0);
          updateRescanMetric('metric-obs-created', d.observations_created ?? 0);
          updateRescanMetric('metric-obs-skipped', d.observations_skipped ?? 0);

          const prefix = d.dry_run ? '[DRY RUN] ' : '';
          setRescanStatus(`${prefix}Rescan complete. ${d.total_images} images processed, ${d.unique_tags} unique tags, ${d.observations_created} observations created.`, 'success');
          toast(`${prefix}Rescan complete.`, 'success');
        } catch (_) { /* ignore */ }

        rescanEventSource.close();
        rescanEventSource = null;
        rescanBtn.disabled = false;
        rescanCard.classList.remove('active');

        if (!dryRun) loadTable();
      });

      rescanEventSource.onerror = () => {
        setRescanStatus('Connection lost or server error.', 'error');
        if (rescanEventSource) {
          rescanEventSource.close();
          rescanEventSource = null;
        }
        rescanBtn.disabled = false;
        rescanCard.classList.remove('active');
      };
    });
  }

  // ── Init ──
  loadTable();
})();
