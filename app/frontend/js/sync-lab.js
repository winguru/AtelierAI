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

  function getCivitaiWebBaseUrl() {
    return window.__ATELIER_CONFIG?.civitai_web_base_url || 'https://civitai.red';
  }

  /* ── Timing formatter ── */
  function fmtMs(ms) {
    if (ms == null) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  }

  /* ── Step error formatter ── */
  function formatStepError(resp, data) {
    const errs = data.errors || [data.detail || 'Unknown error'];
    const joined = errs.join('; ');
    if (resp.status === 503) {
      return `CivitAI Unavailable: ${joined}`;
    }
    return `Failed: ${joined}`;
  }

  /* ── Timestamp formatter (ISO → relative or short date) ── */
  function fmtTimestamp(iso) {
    if (!iso) return '—';
    try {
      const d = new Date(iso);
      const now = Date.now();
      const diffMs = now - d.getTime();
      const diffMins = Math.floor(diffMs / 60000);
      if (diffMins < 1) return 'just now';
      if (diffMins < 60) return `${diffMins}m ago`;
      const diffHrs = Math.floor(diffMins / 60);
      if (diffHrs < 24) return `${diffHrs}h ago`;
      const diffDays = Math.floor(diffHrs / 24);
      if (diffDays < 30) return `${diffDays}d ago`;
      return d.toLocaleDateString();
    } catch { return '—'; }
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
      const resp = await fetch('/api/sync-lab/collections');
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(1, 'error');
        setStatus(1, 'error', formatStepError(resp, data));
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
      const synced = c.lastSyncedAt ? fmtTimestamp(c.lastSyncedAt) : '—';
      return `<tr data-idx="${i}">
        <td>${id}</td>
        <td>${escHtml(name)}</td>
        <td>${type}</td>
        <td>${count}</td>
        <td>${synced}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <table class="collection-table">
        <thead><tr><th>ID</th><th>Name</th><th>Type</th><th>Items</th><th>Last Synced</th></tr></thead>
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
    const urlMatch = collectionId.match(/(?:https?:\/\/)?(?:www\.)?civitai\.(?:com|red)\/collections\/(\d+)/i);
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
      const url = `/api/sync-lab/collection-items/${state.selectedCollectionId}`;
      const result = await new Promise((resolve, reject) => {
        const es = new EventSource(url);
        let lastProgress = null;

        es.onmessage = (event) => {
          let data;
          try { data = JSON.parse(event.data); } catch { return; }

          if (data.type === 'progress') {
            lastProgress = data;
            setStatus(3, 'info',
              `Fetching… Page ${data.page}: ${data.page_items} items (total: ${data.total})`);
          } else if (data.type === 'complete') {
            es.close();
            resolve(data);
          } else if (data.type === 'error') {
            es.close();
            reject({ status_code: data.status_code, detail: data.detail });
          }
        };

        es.onerror = () => {
          es.close();
          reject({ status_code: 0, detail: 'Connection lost during fetch' });
        };
      });

      setTiming(3, result.timing?.duration_ms);
      state.collectionItems = result.data?.items || [];

      const total = state.collectionItems.length;

      let summary = `Fetched ${total} item(s) in ${fmtMs(result.timing?.duration_ms)}`;
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
      if (err.status_code === 503) {
        setStatus(3, 'error', `CivitAI Unavailable: ${err.detail}`);
      } else {
        setStatus(3, 'error', err.detail || `Error: ${err}`);
      }
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
      const rawName = item.name || item.title || '—';
      const name = rawName.length > 45 ? rawName.slice(0, 45) + '…' : rawName;
      const type = item.type || '—';
      const nsfwLevel = item.nsfwLevel ?? '—';
      const nsfwBadge = renderNsfwBadge(nsfwLevel);
      const mimeType = item.mimeType || '';
      const size = (item.width && item.height) ? `${item.width}×${item.height}` : '';
      const publishedAt = item.publishedAt || item.createdAt || '';
      const dateDisplay = publishedAt ? fmtTimestamp(publishedAt) : '—';
      const rawUser = (item.user && item.user.username) || '—';
      const isUserDeleted = !!(item.user && item.user.deletedAt);
      const userDisplay = isUserDeleted && rawUser !== '—'
        ? `<span class="deleted-user" title="CivitAI account deleted">${escHtml(rawUser.length > 25 ? rawUser.slice(0, 25) + '…' : rawUser)}</span>`
        : escHtml(rawUser.length > 25 ? rawUser.slice(0, 25) + '…' : rawUser);

      const imageUrl = `${getCivitaiWebBaseUrl()}/images/${id}`;
      return `<tr data-id="${id}">
        <td class="col-id"><a href="${imageUrl}" target="_blank" rel="noopener">${id}</a></td>
        <td class="col-name" title="${escHtml(rawName)}${mimeType ? ' · ' + escHtml(mimeType) : ''}${size ? ' · ' + size : ''}">${escHtml(name)}</td>
        <td class="col-type">${escHtml(type)}</td>
        <td class="col-user" title="${escHtml(rawUser)}">${userDisplay}</td>
        <td class="col-date" title="${escHtml(publishedAt)}">${escHtml(dateDisplay)}</td>
        <td class="col-rating">${nsfwBadge}</td>
      </tr>`;
    }).join('');

    container.innerHTML = `
      <div class="items-scroll-pane">
        <table class="collection-table items-table">
          <thead><tr>
            <th class="col-id">ID</th>
            <th class="col-name">Filename</th>
            <th class="col-type">Type</th>
            <th class="col-user">User</th>
            <th class="col-date">Published</th>
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
      const resp = await fetch('/api/sync-lab/analyze-local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: imageIds }),
      });
      const data = await resp.json();

      if (!resp.ok || data.status === 'error') {
        setStepState(4, 'error');
        setStatus(4, 'error', formatStepError(resp, data));
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
        const paddedId = String(id).padStart(9, '\u2007'); // figure space for uniform width
        html += `<span class="item-chip ${sec.chipClass}">#${paddedId}</span>`;
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
      const url = `/api/sync-lab/fetch-metadata?image_ids=${imageIds.join(',')}`;
      const result = await new Promise((resolve, reject) => {
        const es = new EventSource(url);

        es.onmessage = (event) => {
          let data;
          try { data = JSON.parse(event.data); } catch { return; }

          if (data.type === 'progress') {
            const errSuffix = data.error ? ' ⚠' : '';
            setStatus(5, 'info',
              `Fetching metadata… ${data.done}/${data.total} — #${data.image_id} (${fmtMs(data.timing_ms)})${errSuffix}`);
          } else if (data.type === 'complete') {
            es.close();
            resolve(data);
          } else if (data.type === 'error') {
            es.close();
            reject({ status_code: data.status_code, detail: data.detail });
          }
        };

        es.onerror = () => {
          es.close();
          reject({ status_code: 0, detail: 'Connection lost during fetch' });
        };
      });

      setTiming(5, result.timing?.duration_ms);
      const rawResults = result.data?.results || {};
      // Backend returns a dict keyed by image ID — convert to array
      state.metadataResults = typeof rawResults === 'object' && !Array.isArray(rawResults)
        ? Object.values(rawResults)
        : rawResults;

      const ok = state.metadataResults.filter(r => !r.error).length;
      const failed = state.metadataResults.filter(r => r.error).length;

      setStatus(5, 'success',
        `Metadata fetched: ${ok} OK, ${failed} failed in ${fmtMs(result.timing?.duration_ms)}`
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
      if (err.status_code === 503) {
        setStatus(5, 'error', `CivitAI Unavailable: ${err.detail}`);
      } else {
        setStatus(5, 'error', err.detail || `Network error: ${err.message || err}`);
      }
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
    state.metadataResults.forEach((r, i) => {
      const id = r.image_id || '?';
      const cls = r.error ? 'failed' : 'new';
      const timing = r.timing_ms ? ` (${fmtMs(r.timing_ms)})` : '';
      html += `<span class="item-chip ${cls}" data-idx="${i}" title="Click to inspect metadata${r.error ? ' — ' + escHtml(r.error) : ''}${timing}">#${id}</span>`;
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

  /** Show a popup with the full metadata for one image result. */
  function showMetadataPopup(idx) {
    const r = state.metadataResults[idx];
    if (!r) return;

    const backdrop = document.createElement('div');
    backdrop.className = 'meta-popup-backdrop';
    backdrop.addEventListener('click', e => {
      if (e.target === backdrop) backdrop.remove();
    });

    const popup = document.createElement('div');
    popup.className = 'meta-popup';

    const id = r.image_id || '?';
    const statusCls = r.error ? 'failed' : 'new';

    let sections = '';

    // Header
    sections += `<div class="meta-popup-header">`;
    sections += `  <span class="item-chip ${statusCls}">#${id}</span>`;
    if (r.timing_ms) sections += `  <span class="meta-popup-timing">${fmtMs(r.timing_ms)}</span>`;
    sections += `  <button class="meta-popup-close" title="Close">&times;</button>`;
    sections += `</div>`;

    if (r.error) {
      sections += `<div class="meta-popup-error"><strong>Error:</strong> ${escHtml(r.error)}</div>`;
    }

    // Basic info section
    if (r.basic_info && typeof r.basic_info === 'object') {
      const bi = r.basic_info;
      sections += `<div class="meta-popup-section">`;
      sections += `  <h4>Basic Info</h4>`;
      sections += `  <table class="meta-popup-table">`;
      // Key fields in a sensible order
      const biFields = [
        ['id', 'Image ID'],
        ['url', 'URL'],
        ['nsfw', 'NSFW'],
        ['nsfwLevel', 'NSFW Level'],
        ['width', 'Width'],
        ['height', 'Height'],
        ['hash', 'Hash'],
        ['type', 'Type'],
        ['postId', 'Post ID'],
        ['stats', 'Stats'],
        ['createdAt', 'Created'],
        ['tags', 'Tags'],
      ];
      for (const [key, label] of biFields) {
        if (bi[key] !== undefined) {
          sections += renderMetaRow(label, bi[key]);
        }
      }
      // Catch any remaining keys
      const shown = new Set(biFields.map(f => f[0]));
      for (const [key, val] of Object.entries(bi)) {
        if (!shown.has(key)) {
          sections += renderMetaRow(key, val);
        }
      }
      sections += `  </table>`;
      sections += `</div>`;
    }

    // Generation data section
    if (r.generation_data && typeof r.generation_data === 'object') {
      const gd = r.generation_data;
      sections += `<div class="meta-popup-section">`;
      sections += `  <h4>Generation Data</h4>`;
      sections += `  <table class="meta-popup-table">`;
      // Key fields in a sensible order
      const gdFields = [
        ['prompt', 'Prompt'],
        ['negativePrompt', 'Negative Prompt'],
        ['sampler', 'Sampler'],
        ['steps', 'Steps'],
        ['cfgScale', 'CFG Scale'],
        ['seed', 'Seed'],
        ['clipSkip', 'Clip Skip'],
        ['Model', 'Model'],
        ['modelHash', 'Model Hash'],
        ['schedule_type', 'Schedule Type'],
        ['guidance_rescale', 'Guidance Rescale'],
        ['denoise', 'Denoise'],
        ['hires upscale', 'Hires Upscale'],
        ['hires upscaler', 'Hires Upscaler'],
        ['hires_latent_upscale', 'Hires Latent Upscale'],
        ['resources', 'Resources'],
        ['workflow', 'Workflow'],
      ];
      for (const [key, label] of gdFields) {
        if (gd[key] !== undefined) {
          sections += renderMetaRow(label, gd[key]);
        }
      }
      // Catch any remaining keys
      const shown = new Set(gdFields.map(f => f[0]));
      for (const [key, val] of Object.entries(gd)) {
        if (!shown.has(key)) {
          sections += renderMetaRow(key, val);
        }
      }
      sections += `  </table>`;
      sections += `</div>`;
    }

    // Raw JSON toggle
    sections += `<details class="meta-popup-raw">`;
    sections += `  <summary>Raw JSON</summary>`;
    sections += `  <pre>${escHtml(JSON.stringify({ basic_info: r.basic_info, generation_data: r.generation_data }, null, 2))}</pre>`;
    sections += `</details>`;

    popup.innerHTML = sections;

    // Close button
    popup.querySelector('.meta-popup-close').addEventListener('click', () => backdrop.remove());

    // Escape key
    const onKey = e => {
      if (e.key === 'Escape') {
        backdrop.remove();
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);

    backdrop.appendChild(popup);
    document.body.appendChild(backdrop);
  }

  /** Render one key-value row for the metadata popup table. */
  function renderMetaRow(label, value) {
    let display;
    if (value === null || value === undefined) {
      display = '<em class="meta-null">null</em>';
    } else if (Array.isArray(value)) {
      if (value.length === 0) {
        display = '<em class="meta-null">empty</em>';
      } else if (typeof value[0] === 'string') {
        display = value.map(v => escHtml(String(v))).join(', ');
      } else {
        display = escHtml(JSON.stringify(value, null, 2));
      }
    } else if (typeof value === 'object') {
      display = escHtml(JSON.stringify(value, null, 2));
    } else if (typeof value === 'string' && (value.startsWith('http://') || value.startsWith('https://'))) {
      display = `<a href="${escHtml(value)}" target="_blank" rel="noopener">${escHtml(value.length > 80 ? value.slice(0, 77) + '…' : value)}</a>`;
    } else {
      display = escHtml(String(value));
    }
    return `<tr><td class="meta-key">${escHtml(label)}</td><td class="meta-val">${display}</td></tr>`;
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
      const url = `/api/sync-lab/download?image_ids=${imageIds.join(',')}`;
      const result = await new Promise((resolve, reject) => {
        const es = new EventSource(url);

        es.onmessage = (event) => {
          let data;
          try { data = JSON.parse(event.data); } catch { return; }

          if (data.type === 'progress') {
            const errSuffix = data.error ? ' ⚠' : '';
            setStatus(6, 'info',
              `Downloading… ${data.done}/${data.total} — #${data.image_id} (${fmtMs(data.timing_ms)})${errSuffix}`);
          } else if (data.type === 'complete') {
            es.close();
            resolve(data);
          } else if (data.type === 'error') {
            es.close();
            reject({ status_code: data.status_code, detail: data.detail });
          }
        };

        es.onerror = () => {
          es.close();
          reject({ status_code: 0, detail: 'Connection lost during download' });
        };
      });

      setTiming(6, result.timing?.duration_ms);
      const rawDlResults = result.data?.results || {};
      // Backend returns a dict keyed by image ID — convert to array
      state.downloadResults = typeof rawDlResults === 'object' && !Array.isArray(rawDlResults)
        ? Object.values(rawDlResults)
        : rawDlResults;

      const ok = state.downloadResults.filter(r => r.status === 'downloaded').length;
      const failed = state.downloadResults.filter(r => r.error).length;

      setStatus(6, 'success',
        `Downloaded: ${ok} OK, ${failed} failed in ${fmtMs(result.timing?.duration_ms)}`
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
      setStatus(6, 'error', err.detail || `Network error: ${err.message || err}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderDownloadDetails() {
    const container = el('step6-results');
    if (!state.downloadResults.length) {
      container.innerHTML = '<p class="detail-label">No results.</p>';
      return;
    }
    let html = '<div class="item-chips">';
    state.downloadResults.forEach((r, i) => {
      const id = r.image_id || '?';
      const cls = r.status === 'downloaded' ? 'existing' : 'failed';
      const size = r.file_size ? ` (${(r.file_size / 1024).toFixed(0)}KB)` : '';
      html += `<span class="item-chip ${cls}" data-idx="${i}" title="Click to inspect download details${r.error ? ' — ' + escHtml(r.error) : ''}${size}">#${id}</span>`;
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
      const url = `/api/sync-lab/ingest?image_ids=${okIds.join(',')}&collection_id=${state.selectedCollectionId || ''}`;
      const result = await new Promise((resolve, reject) => {
        const es = new EventSource(url);

        es.onmessage = (event) => {
          let data;
          try { data = JSON.parse(event.data); } catch { return; }

          if (data.type === 'progress') {
            const errSuffix = data.error ? ' ⚠' : '';
            setStatus(7, 'info',
              `Ingesting… ${data.done}/${data.total} — #${data.image_id} (${fmtMs(data.timing_ms)})${errSuffix}`);
          } else if (data.type === 'complete') {
            es.close();
            resolve(data);
          } else if (data.type === 'error') {
            es.close();
            reject({ status_code: data.status_code, detail: data.detail });
          }
        };

        es.onerror = () => {
          es.close();
          reject({ status_code: 0, detail: 'Connection lost during ingest' });
        };
      });

      setTiming(7, result.timing?.duration_ms);
      const rawIngestResults = result.data?.results || {};
      // Backend returns a dict keyed by image ID — convert to array
      state.ingestResults = typeof rawIngestResults === 'object' && !Array.isArray(rawIngestResults)
        ? Object.values(rawIngestResults)
        : rawIngestResults;

      const ok = state.ingestResults.filter(r => r.status === 'ingested').length;
      const dups = state.ingestResults.filter(r => r.ingest_result?.is_duplicate_asset).length;
      const skipped = state.ingestResults.filter(r => r.status === 'skipped').length;
      const failed = state.ingestResults.filter(r => r.error).length;

      let msg = `Ingested: ${ok} OK`;
      if (dups) msg += ` (${dups} duplicate assets)`;
      if (skipped) msg += `, ${skipped} skipped`;
      if (failed) msg += `, ${failed} failed`;
      msg += ` in ${fmtMs(result.timing?.duration_ms)}`;
      setStatus(7, failed ? 'warning' : 'success', msg);

      renderIngestDetails();
      setStepState(7, 'complete');
    } catch (err) {
      setStepState(7, 'error');
      setStatus(7, 'error', err.detail || `Network error: ${err.message || err}`);
    } finally {
      btnLoading(btn, false);
    }
  }

  function renderIngestDetails() {
    const container = el('step7-results');
    if (!state.ingestResults.length) {
      container.innerHTML = '<p class="detail-label">No results.</p>';
      return;
    }
    let html = '<div class="item-chips">';
    state.ingestResults.forEach((r, i) => {
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
      const titleParts = ['Click to inspect ingest details'];
      if (r.error) titleParts.push(r.error);
      if (isDup) {
        titleParts.push(`Duplicate of DB #${r.ingest_result?.duplicate_of_image_db_id || '?'}`);
      }
      html += `<span class="item-chip ${cls}" data-idx="${i}" title="${escHtml(titleParts.filter(Boolean).join(' · '))}">${escHtml(label)}</span>`;
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

  /** Show a popup with download details for one image result. */
  function showDownloadPopup(idx) {
    const r = state.downloadResults[idx];
    if (!r) return;

    const backdrop = document.createElement('div');
    backdrop.className = 'meta-popup-backdrop';
    backdrop.addEventListener('click', e => {
      if (e.target === backdrop) backdrop.remove();
    });

    const popup = document.createElement('div');
    popup.className = 'meta-popup';

    const id = r.image_id || '?';
    const statusCls = r.status === 'downloaded' ? 'existing' : 'failed';

    let sections = '';

    // Header
    sections += `<div class="meta-popup-header">`;
    sections += `  <span class="item-chip ${statusCls}">#${id}</span>`;
    if (r.timing_ms) sections += `  <span class="meta-popup-timing">${fmtMs(r.timing_ms)}</span>`;
    sections += `  <button class="meta-popup-close" title="Close">&times;</button>`;
    sections += `</div>`;

    if (r.error) {
      sections += `<div class="meta-popup-error"><strong>Error:</strong> ${escHtml(r.error)}</div>`;
    }

    // Download details
    sections += `<div class="meta-popup-section">`;
    sections += `  <h4>Download Details</h4>`;
    sections += `  <table class="meta-popup-table">`;
    const dlFields = [
      ['status', 'Status'],
      ['temp_path', 'Temp Path'],
      ['mime_type', 'MIME Type'],
      ['selected_url', 'Selected URL'],
      ['timing_ms', 'Timing'],
    ];
    for (const [key, label] of dlFields) {
      if (r[key] !== undefined && r[key] !== null) {
        const val = key === 'timing_ms' ? fmtMs(r[key]) : r[key];
        sections += renderMetaRow(label, val);
      }
    }
    sections += `  </table>`;
    sections += `</div>`;

    // Raw JSON toggle
    sections += `<details class="meta-popup-raw">`;
    sections += `  <summary>Raw JSON</summary>`;
    sections += `  <pre>${escHtml(JSON.stringify(r, null, 2))}</pre>`;
    sections += `</details>`;

    popup.innerHTML = sections;
    popup.querySelector('.meta-popup-close').addEventListener('click', () => backdrop.remove());

    const onKey = e => {
      if (e.key === 'Escape') {
        backdrop.remove();
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);

    backdrop.appendChild(popup);
    document.body.appendChild(backdrop);
  }

  /** Show a popup with ingest details for one image result. */
  function showIngestPopup(idx) {
    const r = state.ingestResults[idx];
    if (!r) return;

    const backdrop = document.createElement('div');
    backdrop.className = 'meta-popup-backdrop';
    backdrop.addEventListener('click', e => {
      if (e.target === backdrop) backdrop.remove();
    });

    const popup = document.createElement('div');
    popup.className = 'meta-popup';

    const id = r.image_id || '?';
    const isDup = r.ingest_result?.is_duplicate_asset === true;
    let statusCls;
    if (r.status === 'ingested') statusCls = isDup ? 'duplicate' : 'existing';
    else if (r.status === 'skipped') statusCls = 'tombstoned';
    else statusCls = 'failed';

    let sections = '';

    // Header
    sections += `<div class="meta-popup-header">`;
    sections += `  <span class="item-chip ${statusCls}">#${id}${isDup ? ' (dup)' : ''}</span>`;
    if (r.timing_ms) sections += `  <span class="meta-popup-timing">${fmtMs(r.timing_ms)}</span>`;
    sections += `  <button class="meta-popup-close" title="Close">&times;</button>`;
    sections += `</div>`;

    if (r.error) {
      sections += `<div class="meta-popup-error"><strong>Error:</strong> ${escHtml(r.error)}</div>`;
    }

    // Ingest details
    sections += `<div class="meta-popup-section">`;
    sections += `  <h4>Ingest Details</h4>`;
    sections += `  <table class="meta-popup-table">`;
    const ingestFields = [
      ['status', 'Status'],
      ['timing_ms', 'Timing'],
    ];
    for (const [key, label] of ingestFields) {
      if (r[key] !== undefined && r[key] !== null) {
        const val = key === 'timing_ms' ? fmtMs(r[key]) : r[key];
        sections += renderMetaRow(label, val);
      }
    }
    // Ingest result sub-fields
    if (r.ingest_result && typeof r.ingest_result === 'object') {
      const ir = r.ingest_result;
      const irFields = [
        ['is_duplicate_asset', 'Duplicate Asset'],
        ['duplicate_of_image_db_id', 'Duplicate Of DB ID'],
        ['file_path', 'Library Path'],
        ['file_hash', 'File Hash'],
        ['image_db_id', 'Image DB ID'],
      ];
      for (const [key, label] of irFields) {
        if (ir[key] !== undefined) {
          sections += renderMetaRow(label, ir[key]);
        }
      }
    }
    sections += `  </table>`;
    sections += `</div>`;

    // Raw JSON toggle
    sections += `<details class="meta-popup-raw">`;
    sections += `  <summary>Raw JSON</summary>`;
    sections += `  <pre>${escHtml(JSON.stringify(r, null, 2))}</pre>`;
    sections += `</details>`;

    popup.innerHTML = sections;
    popup.querySelector('.meta-popup-close').addEventListener('click', () => backdrop.remove());

    const onKey = e => {
      if (e.key === 'Escape') {
        backdrop.remove();
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);

    backdrop.appendChild(popup);
    document.body.appendChild(backdrop);
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

    // Delegated click handler for step 5 metadata chip inspection
    el('step5-results').addEventListener('click', e => {
      const chip = e.target.closest('.item-chip[data-idx]');
      if (chip) {
        const idx = parseInt(chip.dataset.idx, 10);
        if (!isNaN(idx) && state.metadataResults[idx]) {
          showMetadataPopup(idx);
        }
      }
    });

    // Delegated click handler for step 6 download chip inspection
    el('step6-results').addEventListener('click', e => {
      const chip = e.target.closest('.item-chip[data-idx]');
      if (chip) {
        const idx = parseInt(chip.dataset.idx, 10);
        if (!isNaN(idx) && state.downloadResults[idx]) {
          showDownloadPopup(idx);
        }
      }
    });

    // Delegated click handler for step 7 ingest chip inspection
    el('step7-results').addEventListener('click', e => {
      const chip = e.target.closest('.item-chip[data-idx]');
      if (chip) {
        const idx = parseInt(chip.dataset.idx, 10);
        if (!isNaN(idx) && state.ingestResults[idx]) {
          showIngestPopup(idx);
        }
      }
    });
  }

  /* ── Boot ── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
