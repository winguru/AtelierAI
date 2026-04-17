/* global AtelierUi, applyThemePreference */

(function () {
  'use strict';

  /* ── State ── */
  const state = {
    collections: [],         // raw collection list from API
    selectedCollectionId: null,
    collectionItems: [],     // items from fetch-collection-items
    analysis: null,          // local state analysis results
    metadataResults: [],     // metadata fetch results
    downloadResults: [],     // download results
    ingestResults: [],       // ingest results
  };

  /* ── DOM helpers ── */
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  function el(id) { return document.getElementById(id); }

  /* ── Timing formatter ── */
  function fmtMs(ms) {
    if (ms == null) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  /* ── Status message ── */
  function setStatus(stepNum, type, msg) {
    const s = el(`step${stepNum}-status`);
    if (!s) return;
    s.className = `step-status ${type}`;
    s.textContent = msg;
  }

  function clearStatus(stepNum) {
    const s = el(`step${stepNum}-status`);
    if (!s) return;
    s.className = 'step-status';
    s.textContent = '';
  }

  /* ── Timing display ── */
  function setTiming(stepNum, ms) {
    const t = el(`step${stepNum}-timing`);
    if (t) t.textContent = fmtMs(ms);
  }

  /* ── Button loading state ── */
  function btnLoading(btn, isLoading) {
    if (!btn) return;
    if (isLoading) {
      btn.dataset.origText = btn.textContent;
      btn.classList.add('loading');
      btn.disabled = true;
    } else {
      btn.classList.remove('loading');
      btn.disabled = false;
      btn.textContent = btn.dataset.origText || btn.textContent;
    }
  }

  /* ── Step card state management ── */
  function setStepState(stepNum, cls) {
    const card = el(`step-${stepNum}`);
    if (!card) return;
    card.classList.remove('active', 'complete', 'error', 'disabled');
    if (cls) card.classList.add(cls);
  }

  function setFlowState(flowId, active, label) {
    const flow = el(flowId);
    if (!flow) return;
    flow.classList.toggle('active', !!active);
    const lbl = flow.querySelector('.flow-label');
    if (lbl && label) lbl.textContent = label;
  }

  function enableStep(stepNum) {
    const card = el(`step-${stepNum}`);
    if (card) card.classList.remove('disabled');
  }

  /* ── Enable a button ── */
  function enableBtn(id) {
    const b = el(id);
    if (b) b.disabled = false;
  }

  function disableBtn(id) {
    const b = el(id);
    if (b) b.disabled = true;
  }

  /* ────────────────────────────────────────
     Step 1: Fetch Collections
     ──────────────────────────────────────── */
  async function fetchCollections() {
    const btn = el('btn-fetch-collections');
    btnLoading(btn, true);
    setStepState(1, 'active');
    clearStatus(1);
    el('step1-results').innerHTML = '';

    try {
      const resp = await fetch('/sync-lab/collections');
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(1, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(1, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(1, data.timing?.duration_ms);
      state.collections = data.data?.collections || [];

      setStatus(1, 'success', `Found ${state.collections.length} collection(s) in ${fmtMs(data.timing?.duration_ms)}`);
      renderCollectionsTable();
      setStepState(1, 'complete');

      // Enable step 2
      enableStep(2);
      enableBtn('btn-select-collection');
      populateDropdown();
    } catch (err) {
      setStepState(1, 'error');
      setStatus(1, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderCollectionsTable() {
    const container = el('step1-results');
    if (!state.collections.length) {
      container.innerHTML = '<p class="detail-label">No collections found.</p>';
      return;
    }

    const rows = state.collections.map((c, i) => {
      const id = c.id || c.collectionId || '?';
      const name = c.name || '(unnamed)';
      const type = c.type || 'image';
      const count = c.itemCount ?? c.count ?? '—';
      return `<tr data-idx="${i}">
        <td>${id}</td>
        <td>${escHtml(name)}</td>
        <td>${type}</td>
        <td>${count}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <table class="collection-table">
        <thead><tr><th>ID</th><th>Name</th><th>Type</th><th>Items</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function populateDropdown() {
    const dd = el('collection-dropdown');
    dd.innerHTML = '<option value="">— Select a collection —</option>';
    dd.disabled = false;

    state.collections.forEach(c => {
      const id = c.id || c.collectionId;
      const name = c.name || '(unnamed)';
      const count = c.itemCount ?? c.count ?? '';
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = `${name} (${id})${count ? ` — ${count} items` : ''}`;
      dd.appendChild(opt);
    });
  }

  /* ────────────────────────────────────────
     Step 2: Select Collection
     ──────────────────────────────────────── */
  function selectCollection() {
    const dd = el('collection-dropdown');
    const manual = el('collection-manual');
    let collectionId = dd.value || manual.value.trim();

    // Parse URL form
    const urlMatch = collectionId.match(/civitai\.com\/collections\/(\d+)/);
    if (urlMatch) collectionId = urlMatch[1];

    collectionId = parseInt(collectionId, 10);
    if (!collectionId || isNaN(collectionId)) {
      setStatus(2, 'warning', 'Please select a collection from the dropdown or enter a valid ID/URL.');
      return;
    }

    state.selectedCollectionId = collectionId;
    const col = state.collections.find(c => (c.id || c.collectionId) === collectionId);
    const colName = col ? col.name : `Collection #${collectionId}`;
    setStatus(2, 'success', `Selected: ${escHtml(colName)} (ID: ${collectionId})`);
    el('step2-results').innerHTML = '';

    setStepState(2, 'complete');
    setFlowState('flow-2-3', true, `${colName} — ready to fetch items`);
    enableStep(3);
    enableBtn('btn-fetch-items');
  }

  /* ────────────────────────────────────────
     Step 3: Fetch Collection Items
     ──────────────────────────────────────── */
  async function fetchItems() {
    if (!state.selectedCollectionId) return;

    const btn = el('btn-fetch-items');
    btnLoading(btn, true);
    setStepState(3, 'active');
    clearStatus(3);
    el('step3-results').innerHTML = '';

    try {
      const resp = await fetch(`/sync-lab/collection-items/${state.selectedCollectionId}`);
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(3, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(3, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(3, data.timing?.duration_ms);
      state.collectionItems = data.data?.items || [];

      const total = state.collectionItems.length;
      const archived = data.data?.archived_count || 0;
      const dupes = data.data?.duplicate_count || 0;

      let summary = `Fetched ${total} item(s) in ${fmtMs(data.timing?.duration_ms)}`;
      if (archived) summary += ` · ${archived} archived`;
      if (dupes) summary += ` · ${dupes} duplicates removed`;
      setStatus(3, 'success', summary);

      renderItemsTable();
      setStepState(3, 'complete');

      // Enable step 4
      if (total > 0) {
        setFlowState('flow-3-4', true, `${total} items ready for analysis`);
        enableStep(4);
        enableBtn('btn-analyze-local');
      }
    } catch (err) {
      setStepState(3, 'error');
      setStatus(3, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderItemsTable() {
    const container = el('step3-results');
    if (!state.collectionItems.length) {
      container.innerHTML = '<p class="detail-label">No items found.</p>';
      return;
    }

    const rows = state.collectionItems.map(item => {
      const id = item.id || item.imageId || '?';
      const name = item.name || item.title || '—';
      const type = item.type || '—';
      const nsfwLevel = item.nsfwLevel ?? '—';
      const nsfwBadge = renderNsfwBadge(nsfwLevel);
      const mimeType = item.mimeType || '';
      const size = (item.width && item.height) ? `${item.width}×${item.height}` : '';

      return `<tr data-id="${id}">
        <td class="col-id"><a href="https://civitai.com/images/${id}" target="_blank" rel="noopener">${id}</a></td>
        <td class="col-name" title="${escHtml(mimeType)}${size ? ' · ' + size : ''}">${escHtml(name)}</td>
        <td class="col-type">${escHtml(type)}</td>
        <td class="col-rating">${nsfwBadge}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <div style="overflow-x:auto">
        <table class="collection-table items-table">
          <thead><tr>
            <th class="col-id">ID</th>
            <th class="col-name">Filename</th>
            <th class="col-type">Type</th>
            <th class="col-rating">Rating</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <p class="detail-label" style="margin-top:0.4rem">${state.collectionItems.length} item(s)</p>`;
  }

  function renderNsfwBadge(level) {
    if (level === '—' || level == null) return '<span class="status-badge info">—</span>';
    const n = parseInt(level, 10);
    if (isNaN(n)) return `<span class="status-badge info">${escHtml(String(level))}</span>`;
    if (n === 0) return '<span class="status-badge ok">SFW</span>';
    if (n <= 2) return '<span class="status-badge info">Soft</span>';
    if (n <= 8) return '<span class="status-badge warn">Mature</span>';
    return '<span class="status-badge err">Explicit</span>';
  }

  /* ────────────────────────────────────────
     Step 4: Analyze Local State
     ──────────────────────────────────────── */
  async function analyzeLocal() {
    if (!state.collectionItems.length) return;

    const btn = el('btn-analyze-local');
    btnLoading(btn, true);
    setStepState(4, 'active');
    clearStatus(4);
    el('step4-results').innerHTML = '';
    el('dedup-summary').innerHTML = '';

    const imageIds = state.collectionItems.map(it => it.id || it.imageId).filter(Boolean);

    try {
      const resp = await fetch('/sync-lab/analyze-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds }),
      });
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(4, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(4, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(4, data.timing?.duration_ms);
      state.analysis = data.data || {};

      const counts = state.analysis.summary || {};
      const existing = counts.existing || 0;
      const newCount = counts.new || 0;
      const tombstoned = counts.tombstoned || 0;
      const placeholders = counts.placeholders || 0;
      const total = existing + newCount + tombstoned + placeholders;

      setStatus(4, 'success',
        `Analyzed ${total} item(s) in ${fmtMs(data.timing?.duration_ms)}: ` +
        `${existing} existing, ${newCount} new, ${tombstoned} tombstoned, ${placeholders} placeholders`
      );

      renderDedupSummary(existing, newCount, tombstoned, placeholders);
      renderAnalysisDetails();
      setStepState(4, 'complete');

      // Enable step 5 only if there are new items to process
      if (newCount > 0) {
        setFlowState('flow-4-5', true, `${newCount} new item(s) need metadata`);
        enableStep(5);
        enableBtn('btn-fetch-metadata');
      } else if (existing > 0) {
        setStatus(4, 'info', 'All items already exist locally. No new items to process.');
      }
    } catch (err) {
      setStepState(4, 'error');
      setStatus(4, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderDedupSummary(existing, newCount, tombstoned, placeholders) {
    const container = el('dedup-summary');
    let html = '';
    if (existing) html += `<div class="dedup-stat existing">✓ Existing: ${existing}</div>`;
    if (newCount) html += `<div class="dedup-stat new">★ New: ${newCount}</div>`;
    if (tombstoned) html += `<div class="dedup-stat tombstoned">⚠ Tombstoned: ${tombstoned}</div>`;
    if (placeholders) html += `<div class="dedup-stat placeholder">◇ Placeholders: ${placeholders}</div>`;
    container.innerHTML = html;
  }

  function renderAnalysisDetails() {
    const container = el('step4-results');
    const data = state.analysis || {};
    let html = '';

    const sections = [
      { key: 'existing', label: 'Existing', items: data.existing, chipClass: 'existing' },
      { key: 'new', label: 'New', items: data.new, chipClass: 'new' },
      { key: 'tombstoned', label: 'Tombstoned', items: data.tombstoned, chipClass: 'tombstoned' },
      { key: 'placeholders', label: 'Placeholders', items: data.placeholders, chipClass: 'placeholder' },
    ];

    for (const sec of sections) {
      if (!sec.items || !sec.items.length) continue;
      html += `<h4 style="margin:0.5rem 0 0.25rem;font-size:0.85rem">${escHtml(sec.label)} (${sec.items.length})</h4>`;
      html += '<div class="item-chips">';
      sec.items.forEach(it => {
        // 'new' items are plain IDs; others are dicts with civitai_image_id
        const id = typeof it === 'number' ? it : (it.civitai_image_id || it.image_id || '?');
        html += `<span class="item-chip ${sec.chipClass}">#${id}</span>`;
      });
      html += '</div>';
    }

    container.innerHTML = html || '<p class="detail-label">No analysis details.</p>';
  }

  /* ────────────────────────────────────────
     Step 5: Fetch Metadata
     ──────────────────────────────────────── */
  async function fetchMetadata() {
    if (!state.analysis) return;

    const btn = el('btn-fetch-metadata');
    btnLoading(btn, true);
    setStepState(5, 'active');
    clearStatus(5);
    el('step5-results').innerHTML = '';

    const newItems = (state.analysis.new || []);
    const imageIds = newItems.map(it => typeof it === 'number' ? it : (it.civitai_image_id || it.image_id)).filter(Boolean);

    if (!imageIds.length) {
      setStatus(5, 'warning', 'No new items to fetch metadata for.');
      setStepState(5, 'complete');
      btnLoading(btn, false);
      return;
    }

    try {
      const resp = await fetch('/sync-lab/fetch-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds }),
      });
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(5, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(5, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(5, data.timing?.duration_ms);
      const rawResults = data.data?.results || {};
      // Backend returns a dict keyed by image ID — convert to array
      state.metadataResults = typeof rawResults === 'object' && !Array.isArray(rawResults)
        ? Object.values(rawResults)
        : rawResults;

      const ok = state.metadataResults.filter(r => !r.error).length;
      const failed = state.metadataResults.filter(r => r.error).length;

      setStatus(5, 'success',
        `Metadata fetched: ${ok} OK, ${failed} failed in ${fmtMs(data.timing?.duration_ms)}`
      );

      renderMetadataDetails();
      setStepState(5, 'complete');

      if (ok > 0) {
        setFlowState('flow-5-6', true, `${ok} item(s) ready for download`);
        enableStep(6);
        enableBtn('btn-download');
      }
    } catch (err) {
      setStepState(5, 'error');
      setStatus(5, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderMetadataDetails() {
    const container = el('step5-results');
    if (!state.metadataResults.length) {
      container.innerHTML = '<p class="detail-label">No results.</p>';
      return;
    }
    let html = '<div class="item-chips">';
    state.metadataResults.forEach(r => {
      const id = r.image_id || '?';
      const cls = r.error ? 'failed' : 'new';
      const timing = r.timing_ms ? ` (${fmtMs(r.timing_ms)})` : '';
      html += `<span class="item-chip ${cls}" title="${escHtml(r.error || '')}${timing}">#${id}</span>`;
    });
    html += '</div>';

    if (state.metadataResults.some(r => r.error)) {
      html += '<ul class="error-list">';
      state.metadataResults.filter(r => r.error).forEach(r => {
        html += `<li>#${r.image_id}: ${escHtml(r.error)}</li>`;
      });
      html += '</ul>';
    }

    container.innerHTML = html;
  }

  /* ────────────────────────────────────────
     Step 6: Download Images
     ──────────────────────────────────────── */
  async function downloadImages() {
    if (!state.metadataResults.length) return;

    const btn = el('btn-download');
    btnLoading(btn, true);
    setStepState(6, 'active');
    clearStatus(6);
    el('step6-results').innerHTML = '';

    const imageIds = state.metadataResults
      .filter(r => !r.error)
      .map(r => r.image_id)
      .filter(Boolean);

    if (!imageIds.length) {
      setStatus(6, 'warning', 'No items with successful metadata to download.');
      setStepState(6, 'complete');
      btnLoading(btn, false);
      return;
    }

    try {
      const resp = await fetch('/sync-lab/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds }),
      });
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(6, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(6, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(6, data.timing?.duration_ms);
      const rawDlResults = data.data?.results || [];
      state.downloadResults = Array.isArray(rawDlResults) ? rawDlResults : Object.values(rawDlResults);

      const ok = state.downloadResults.filter(r => r.status === 'downloaded').length;
      const failed = state.downloadResults.filter(r => r.error).length;

      setStatus(6, 'success',
        `Downloaded: ${ok} OK, ${failed} failed in ${fmtMs(data.timing?.duration_ms)}`
      );

      renderDownloadDetails();
      setStepState(6, 'complete');

      if (ok > 0) {
        setFlowState('flow-6-7', true, `${ok} image(s) ready to ingest`);
        enableStep(7);
        enableBtn('btn-ingest');
      }
    } catch (err) {
      setStepState(6, 'error');
      setStatus(6, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderDownloadDetails() {
    const container = el('step6-results');
    let html = '<div class="item-chips">';
    state.downloadResults.forEach(r => {
      const id = r.image_id || '?';
      const cls = r.status === 'downloaded' ? 'existing' : 'failed';
      const size = r.file_size ? ` (${(r.file_size / 1024).toFixed(0)}KB)` : '';
      html += `<span class="item-chip ${cls}" title="${escHtml(r.error || r.file_name || '')}${size}">#${id}</span>`;
    });
    html += '</div>';

    if (state.downloadResults.some(r => r.error)) {
      html += '<ul class="error-list">';
      state.downloadResults.filter(r => r.error).forEach(r => {
        html += `<li>#${r.image_id}: ${escHtml(r.error)}</li>`;
      });
      html += '</ul>';
    }

    container.innerHTML = html;
  }

  /* ────────────────────────────────────────
     Step 7: Ingest to Library
     ──────────────────────────────────────── */
  async function ingestToLibrary() {
    const okIds = state.downloadResults
      .filter(r => r.status === 'downloaded')
      .map(r => r.image_id)
      .filter(Boolean);

    if (!okIds.length) {
      setStatus(7, 'warning', 'No successfully downloaded items to ingest.');
      return;
    }

    const btn = el('btn-ingest');
    btnLoading(btn, true);
    setStepState(7, 'active');
    clearStatus(7);
    el('step7-results').innerHTML = '';

    try {
      const resp = await fetch('/sync-lab/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_ids: okIds,
          collection_id: state.selectedCollectionId,
        }),
      });
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(7, 'error');
        const errs = data.errors || [data.detail || 'Unknown error'];
        setStatus(7, 'error', `Failed: ${errs.join('; ')}`);
        return;
      }

      setTiming(7, data.timing?.duration_ms);
      const rawIngestResults = data.data?.results || [];
      state.ingestResults = Array.isArray(rawIngestResults) ? rawIngestResults : Object.values(rawIngestResults);

      const ok = state.ingestResults.filter(r => r.status === 'ingested').length;
      const dups = state.ingestResults.filter(r => r.ingest_result?.is_duplicate_asset).length;
      const skipped = state.ingestResults.filter(r => r.status === 'skipped').length;
      const failed = state.ingestResults.filter(r => r.error).length;

      let msg = `Ingested: ${ok} OK`;
      if (dups) msg += ` (${dups} duplicate assets)`;
      if (skipped) msg += `, ${skipped} skipped`;
      if (failed) msg += `, ${failed} failed`;
      msg += ` in ${fmtMs(data.timing?.duration_ms)}`;
      setStatus(7, failed ? 'warning' : 'success', msg);

      renderIngestDetails();
      setStepState(7, 'complete');
    } catch (err) {
      setStepState(7, 'error');
      setStatus(7, 'error', `Network error: ${err.message}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderIngestDetails() {
    const container = el('step7-results');
    let html = '<div class="item-chips">';
    state.ingestResults.forEach(r => {
      const id = r.image_id || '?';
      const isDup = r.ingest_result?.is_duplicate_asset === true;
      let cls, label;
      if (r.status === 'ingested') {
        cls = isDup ? 'duplicate' : 'existing';
        label = isDup ? `#${id} (dup)` : `#${id}`;
      } else if (r.status === 'skipped') {
        cls = 'tombstoned';
        label = `#${id}`;
      } else {
        cls = 'failed';
        label = `#${id}`;
      }
      const titleParts = [r.error || r.file_path || ''];
      if (isDup) {
        titleParts.push(`Duplicate of DB #${r.ingest_result?.duplicate_of_image_db_id || '?'}`);
      }
      html += `<span class="item-chip ${cls}" title="${escHtml(titleParts.filter(Boolean).join(' · '))}">${escHtml(label)}</span>`;
    });
    html += '</div>';

    // Show duplicate count in summary
    const dupCount = state.ingestResults.filter(r => r.ingest_result?.is_duplicate_asset).length;
    if (dupCount) {
      html += `<p class="dup-summary">${dupCount} duplicate asset(s) created with independent records.</p>`;
    }

    if (state.ingestResults.some(r => r.error)) {
      html += '<ul class="error-list">';
      state.ingestResults.filter(r => r.error).forEach(r => {
        html += `<li>#${r.image_id}: ${escHtml(r.error)}</li>`;
      });
      html += '</ul>';
    }

    container.innerHTML = html;
  }

  /* ── HTML escaping ── */
  function escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ── Wire up event listeners ── */
  function init() {
    el('btn-fetch-collections').addEventListener('click', fetchCollections);
    el('btn-select-collection').addEventListener('click', selectCollection);
    el('btn-fetch-items').addEventListener('click', fetchItems);
    el('btn-analyze-local').addEventListener('click', analyzeLocal);
    el('btn-fetch-metadata').addEventListener('click', fetchMetadata);
    el('btn-download').addEventListener('click', downloadImages);
    el('btn-ingest').addEventListener('click', ingestToLibrary);

    // Auto-select when dropdown changes
    el('collection-dropdown').addEventListener('change', () => {
      if (el('collection-dropdown').value) {
        el('collection-manual').value = '';
      }
    });

    // Enter key on manual input triggers select
    el('collection-manual').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        selectCollection();
      }
    });

    // Allow clicking a row in the collections table to populate the dropdown
    el('step1-results').addEventListener('click', (e) => {
      const row = e.target.closest('tr[data-idx]');
      if (!row) return;
      const idx = parseInt(row.dataset.idx, 10);
      const col = state.collections[idx];
      if (!col) return;
      const id = col.id || col.collectionId;
      el('collection-dropdown').value = id;
      el('collection-manual').value = '';
      selectCollection();
    });

    // Disable steps 4-7 initially
    [4, 5, 6, 7].forEach(n => setStepState(n, 'disabled'));

    // Theme toggle
    const themeToggle = el('theme-toggle');
    if (themeToggle) {
      const saved = localStorage.getItem('atelier.syncLab.theme');
      if (saved === 'dark') {
        themeToggle.checked = true;
        document.body.dataset.theme = 'dark';
      }
      themeToggle.addEventListener('change', () => {
        const isDark = themeToggle.checked;
        document.body.dataset.theme = isDark ? 'dark' : '';
        localStorage.setItem('atelier.syncLab.theme', isDark ? 'dark' : 'light');
      });
    }
  }

  /* ── Boot ── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
