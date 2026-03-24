(() => {
  const targetForm = document.getElementById('target-form');
  const targetHashInput = document.getElementById('target-hash');
  const hashSizeInput = document.getElementById('hash-size');
  const similarityAlgorithmSelect = document.getElementById('similarity-algorithm');
  const maxDistanceInput = document.getElementById('max-distance');
  const matchLimitInput = document.getElementById('match-limit');
  const maxCandidatesInput = document.getElementById('max-candidates');
  const runSimilarityBtn = document.getElementById('run-similarity-btn');
  const analysisOutput = document.getElementById('analysis-output');
  const similarityOutput = document.getElementById('similarity-output');
  const similarityVisual = document.getElementById('similarity-visual');
  const blurhashCompare = document.getElementById('blurhash-compare');
  const statusPanel = document.getElementById('status-panel');
  const statusTitle = document.getElementById('status-title');
  const statusMessage = document.getElementById('status-message');
  const themeToggle = document.getElementById('theme-toggle');
  const preferences = window.AtelierPreferences || null;

  if (!targetForm || !targetHashInput || !hashSizeInput || !similarityAlgorithmSelect || !maxDistanceInput || !matchLimitInput || !maxCandidatesInput || !runSimilarityBtn || !analysisOutput || !similarityOutput || !similarityVisual || !blurhashCompare || !statusPanel || !statusTitle || !statusMessage) {
    return;
  }

  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  function setStatus(mode, title, message) {
    statusPanel.className = `status-panel ${mode}`;
    statusTitle.textContent = title;
    statusMessage.textContent = message;
  }

  function asInt(input, fallback) {
    const parsed = Number(input);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.trunc(parsed);
  }

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function formatMetric(value, digits = 4) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
      return 'n/a';
    }
    return num.toFixed(digits);
  }

  function sanitizeForJsonDisplay(value) {
    if (Array.isArray(value)) {
      return value.map((item) => sanitizeForJsonDisplay(item));
    }
    if (!value || typeof value !== 'object') {
      if (typeof value === 'string' && value.startsWith('data:image/')) {
        return `[embedded image data omitted, ${value.length} chars]`;
      }
      return value;
    }

    const out = {};
    for (const [key, entry] of Object.entries(value)) {
      if (typeof entry === 'string' && entry.startsWith('data:image/')) {
        out[key] = `[embedded image data omitted, ${entry.length} chars]`;
        continue;
      }
      out[key] = sanitizeForJsonDisplay(entry);
    }
    return out;
  }

  function renderPreviewPanel(title, payload, extraMeta = []) {
    const hashValue = String(payload?.hash ?? payload?.value ?? '');
    const imageDataUrl = String(payload?.image_data_url ?? payload?.preview?.image_data_url ?? '');
    const decodable = Boolean(payload?.decodable ?? payload?.preview?.decodable ?? imageDataUrl);
    const metaRows = extraMeta
      .filter((item) => item && item.label)
      .map((item) => `<div><strong>${escapeHtml(item.label)}:</strong> ${escapeHtml(item.value ?? 'n/a')}</div>`)
      .join('');

    return `
      <section class="blurhash-panel">
        <h3>${escapeHtml(title)}</h3>
        ${imageDataUrl ? `<img class="blurhash-preview" src="${imageDataUrl}" alt="${escapeHtml(title)} preview">` : '<div class="blurhash-preview" aria-hidden="true"></div>'}
        <div class="blurhash-meta">
          <div><strong>Decodable:</strong> ${decodable ? 'yes' : 'no'}</div>
          ${metaRows}
        </div>
        <pre class="blurhash-code">${escapeHtml(hashValue || 'Unavailable')}</pre>
      </section>
    `;
  }

  function renderBlurhashComparison(payload) {
    const blurhash = payload?.blurhash;
    if (!blurhash || !blurhash.available) {
      blurhashCompare.className = 'blurhash-compare is-empty';
      blurhashCompare.innerHTML = `<p class="blurhash-empty">${escapeHtml(blurhash?.reason || 'BlurHash comparison is unavailable for this image.')}</p>`;
      return;
    }

    const targetPreview = blurhash?.target_preview || {};
    const bestKey = String(blurhash?.best_candidate_key || '');
    const bestCandidate = blurhash?.best_candidate || null;
    const bestDistance = bestCandidate?.preview_distance || null;
    const summaryBits = [
      { label: 'Exact match', value: blurhash?.exact_match ? 'yes' : 'no' },
      { label: 'Best candidate', value: bestKey || 'n/a' },
      { label: 'Target length', value: blurhash?.target_length ?? 'n/a' },
      { label: 'Analysis size', value: blurhash?.analysis_size ? `${blurhash.analysis_size.width}x${blurhash.analysis_size.height}` : 'n/a' },
    ];

    const summaryHtml = summaryBits
      .map((item) => `<div class="blurhash-pill"><strong>${escapeHtml(item.label)}:</strong> ${escapeHtml(item.value)}</div>`)
      .join('');

    const targetPanel = renderPreviewPanel('CivitAI hash', targetPreview, [
      { label: 'Length', value: blurhash?.target_length ?? 'n/a' },
    ]);

    const bestPanel = bestCandidate
      ? renderPreviewPanel(`Local candidate ${bestKey}`, bestCandidate, [
          { label: 'Length', value: bestCandidate?.length ?? 'n/a' },
          { label: 'String distance', value: bestCandidate?.string_distance ?? 'n/a' },
          { label: 'Exact match', value: bestCandidate?.matches_civitai_hash ? 'yes' : 'no' },
        ])
      : '<section class="blurhash-panel"><h3>Local candidate</h3><p>No decodable candidate available.</p></section>';

    const metricsHtml = bestDistance
      ? `
        <div class="blurhash-metrics">
          <div class="blurhash-metric">
            <strong>Mean Absolute Error</strong>
            <span>${escapeHtml(formatMetric(bestDistance.mean_absolute_error))}</span>
          </div>
          <div class="blurhash-metric">
            <strong>RMSE</strong>
            <span>${escapeHtml(formatMetric(bestDistance.rmse))}</span>
          </div>
          <div class="blurhash-metric">
            <strong>Normalized Similarity</strong>
            <span>${escapeHtml(formatMetric(bestDistance.normalized_similarity, 6))}</span>
          </div>
          <div class="blurhash-metric">
            <strong>Preview Size</strong>
            <span>${escapeHtml(`${bestDistance.preview_width}x${bestDistance.preview_height}`)}</span>
          </div>
        </div>
      `
      : '<div class="blurhash-metrics"><div class="blurhash-metric"><strong>Distance</strong><span>Not available</span></div></div>';

    blurhashCompare.className = 'blurhash-compare';
    blurhashCompare.innerHTML = `
      <div class="blurhash-summary">${summaryHtml}</div>
      <div class="blurhash-panels">
        ${targetPanel}
        ${bestPanel}
      </div>
      ${metricsHtml}
    `;
  }

  function renderSimilarityVisual(payload) {
    const target = payload?.target || {};
    const matches = Array.isArray(payload?.matches) ? payload.matches : [];
    const distanceType = String(target?.distance_type || payload?.search?.distance_type || 'distance');

    if (!target?.image_url) {
      similarityVisual.className = 'similarity-visual is-empty';
      similarityVisual.innerHTML = '<p class="similarity-empty">Target preview is unavailable for this search result.</p>';
      return;
    }

    const targetLabel = target?.file_name || target?.file_hash || 'Target image';
    const targetUuid = String(target?.civitai_uuid || '').trim();
    const targetBlurhash = String(target?.blurhash || '').trim();
    const targetMeta = [
      `<div><strong>Hash:</strong> ${escapeHtml(target?.file_hash || 'n/a')}</div>`,
      `<div><strong>UUID:</strong> ${escapeHtml(targetUuid || 'n/a')}</div>`,
      `<div><strong>Algorithm:</strong> ${escapeHtml(target?.algorithm || 'n/a')}</div>`,
      targetBlurhash ? `<div><strong>BlurHash:</strong> ${escapeHtml(targetBlurhash)}</div>` : '',
    ].join('');

    const matchCards = matches
      .map((match) => {
        const label = match?.file_name || match?.file_hash || 'Candidate';
        const score = Number(match?.distance);
        const similarity = Number(match?.similarity);
        const uuidText = String(match?.civitai_uuid || '').trim();
        const metaLines = [
          `<div><strong>${escapeHtml(distanceType)}:</strong> ${escapeHtml(Number.isFinite(score) ? String(score) : 'n/a')}</div>`,
          Number.isFinite(similarity) ? `<div><strong>similarity:</strong> ${escapeHtml(similarity.toFixed(6))}</div>` : '',
          `<div><strong>UUID:</strong> ${escapeHtml(uuidText || 'n/a')}</div>`,
          `<div><strong>Hash:</strong> ${escapeHtml(match?.file_hash || 'n/a')}</div>`,
        ].join('');

        return `
          <article class="similarity-thumb-card">
            ${match?.image_url ? `<img class="similarity-thumb" src="${escapeHtml(match.image_url)}" alt="${escapeHtml(label)}">` : '<div class="similarity-thumb" aria-hidden="true"></div>'}
            <div class="similarity-thumb-meta">
              <h4>${escapeHtml(label)}</h4>
              ${metaLines}
            </div>
          </article>
        `;
      })
      .join('');

    similarityVisual.className = 'similarity-visual';
    similarityVisual.innerHTML = `
      <section class="similarity-target-card">
        <h3>Target</h3>
        <img class="similarity-target-image" src="${escapeHtml(target.image_url)}" alt="${escapeHtml(targetLabel)}">
        <div class="similarity-target-meta">${targetMeta}</div>
      </section>
      <section class="similarity-match-grid-wrap">
        <h3>Matches (${matches.length})</h3>
        ${matchCards ? `<div class="similarity-match-grid">${matchCards}</div>` : '<p class="similarity-empty">No matches found for this search.</p>'}
      </section>
    `;
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    return payload;
  }

  function getFormValues() {
    const fileHash = String(targetHashInput.value || '').trim();
    const hashSize = Math.max(4, Math.min(32, asInt(hashSizeInput.value, 8)));
    const algorithm = String(similarityAlgorithmSelect.value || 'phash').trim().toLowerCase();
    const maxDistance = Math.max(0, Math.min(256, asInt(maxDistanceInput.value, 12)));
    const limit = Math.max(1, Math.min(200, asInt(matchLimitInput.value, 40)));
    const maxCandidates = Math.max(50, Math.min(5000, asInt(maxCandidatesInput.value, 1200)));
    return {
      fileHash,
      hashSize,
      algorithm,
      maxDistance,
      limit,
      maxCandidates,
    };
  }

  async function runAnalysis() {
    const values = getFormValues();
    if (!values.fileHash) {
      throw new Error('File hash is required.');
    }

    const params = new URLSearchParams({
      hash_size: String(values.hashSize),
    });

    setStatus('is-loading', 'Analyzing', `Computing hashes for ${values.fileHash}...`);
    const payload = await fetchJson(`/images/${encodeURIComponent(values.fileHash)}/perceptual-lab/analyze?${params.toString()}`);
    renderBlurhashComparison(payload);
    analysisOutput.textContent = JSON.stringify(sanitizeForJsonDisplay(payload), null, 2);
    const uuid = String(payload?.civitai_uuid || '').trim();
    const suffix = uuid ? ` UUID: ${uuid}` : '';
    setStatus('is-ready', 'Analysis complete', `Computed perceptual hashes for ${values.fileHash}.${suffix}`);
  }

  async function runSimilaritySearch() {
    const values = getFormValues();
    if (!values.fileHash) {
      throw new Error('File hash is required.');
    }

    const params = new URLSearchParams({
      algorithm: values.algorithm,
      hash_size: String(values.hashSize),
      max_distance: String(values.maxDistance),
      limit: String(values.limit),
      max_candidates: String(values.maxCandidates),
    });

    setStatus('is-loading', 'Searching', `Running ${values.algorithm} similarity scan...`);
    const payload = await fetchJson(`/images/${encodeURIComponent(values.fileHash)}/perceptual-lab/similarity?${params.toString()}`);
    similarityOutput.textContent = JSON.stringify(payload, null, 2);
    renderSimilarityVisual(payload);
    const count = Number(payload?.search?.returned_count || 0);
    setStatus('is-ready', 'Similarity complete', `Found ${count} match${count === 1 ? '' : 'es'} for ${values.fileHash}.`);
  }

  targetForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await runAnalysis();
    } catch (error) {
      setStatus('is-error', 'Analysis failed', String(error?.message || error));
    }
  });

  runSimilarityBtn.addEventListener('click', async () => {
    try {
      await runSimilaritySearch();
    } catch (error) {
      setStatus('is-error', 'Similarity failed', String(error?.message || error));
    }
  });

  const params = new URLSearchParams(window.location.search);
  const fileHash = String(params.get('fileHash') || '').trim();
  if (fileHash) {
    targetHashInput.value = fileHash;
    void runAnalysis().catch((error) => {
      setStatus('is-error', 'Auto-analysis failed', String(error?.message || error));
    });
  }
})();
