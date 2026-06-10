/* ── Concept Search Lab ── */

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const searchForm      = $("#search-form");
const searchInput     = $("#search-query");
const decomposeBtn    = $("#decompose-btn");
const resultLimit     = $("#result-limit");
const poolMultiplier  = $("#pool-multiplier");
const statusPanel     = $("#status-panel");
const statusTitle     = $("#status-title");
const statusMessage   = $("#status-message");

const decomposeSection = $("#decompose-section");
const decomposeOriginal = $("#decompose-original");
const decomposeContext  = $("#decompose-context");
const decomposeTotalForms = $("#decompose-total-forms");
const decomposeTable  = $("#decompose-table tbody");
const flowToResults   = $("#flow-to-results");

const resultsSection  = $("#results-section");
const resultsMeta     = $("#results-meta");
const resultsStats    = $("#results-stats");
const resultsGrid     = $("#results-grid");

const indexSection    = $("#index-section");
const indexSummary    = $("#index-summary");
const indexFilter     = $("#index-filter");
const indexTable      = $("#index-table tbody");
const refreshIndexBtn = $("#refresh-index-btn");

const themeToggle     = $("#theme-toggle");

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------
function applyTheme(dark) {
  document.body.setAttribute("data-theme", dark ? "dark" : "light");
  localStorage.setItem("lab-theme", dark ? "dark" : "light");
}

themeToggle.addEventListener("change", () => applyTheme(themeToggle.checked));

// Restore saved theme
if (localStorage.getItem("lab-theme") === "dark") {
  themeToggle.checked = true;
  applyTheme(true);
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------
function setStatus(title, message, state = "idle") {
  statusTitle.textContent = title;
  statusMessage.textContent = " — " + message;
  statusPanel.className = `step-status ${state}`;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function apiPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Decompose
// ---------------------------------------------------------------------------
async function runDecompose(query) {
  setStatus("Decomposing…", query, "working");
  try {
    const data = await apiPost("/api/taxonomy/concept-search/decompose", {
      query,
      limit: parseInt(resultLimit.value, 10) || 30,
    });
    renderDecomposition(data);
    decomposeSection.classList.add("complete");
    setStatus("Decomposition complete", `${data.matched_concepts.length} concepts matched`, "success");
  } catch (e) {
    decomposeSection.classList.add("error");
    setStatus("Decomposition failed", e.message, "error");
  }
}

function renderDecomposition(data) {
  decomposeSection.style.display = "";
  decomposeOriginal.innerHTML = `<strong>Query:</strong> ${esc(data.original_query)}`;
  decomposeContext.innerHTML = `<strong>Context:</strong> ${esc(data.context_text || "(none)")}`;
  decomposeTotalForms.innerHTML = `<strong>Surface forms indexed:</strong> ${data.total_surface_forms}`;

  decomposeTable.innerHTML = "";
  for (const mc of data.matched_concepts) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${esc(mc.concept_name)}</strong> <span style="color:var(--ink-soft)">#${mc.concept_id}</span></td>
      <td><code>${esc(mc.surface_form)}</code></td>
      <td>${esc(mc.match_type)}</td>
    `;
    decomposeTable.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// Full search
// ---------------------------------------------------------------------------
async function runSearch(query) {
  setStatus("Searching…", `Decomposing and scoring "${query}"`, "working");
  resultsSection.style.display = "none";
  flowToResults.style.display = "none";

  try {
    const data = await apiPost("/api/taxonomy/concept-search", {
      query,
      limit: parseInt(resultLimit.value, 10) || 30,
      pool_multiplier: parseInt(poolMultiplier.value, 10) || 3,
    });

    renderDecomposition(data.decomposition);
    decomposeSection.classList.add("complete");
    renderResults(data);
    resultsSection.classList.add("complete");
    flowToResults.style.display = "";
    setStatus(
      "Search complete",
      `${data.results.length} results from ${data.candidates_total} candidates (CLIP ${data.clip_available ? "on" : "off"})`,
      "success"
    );
  } catch (e) {
    setStatus("Search failed", e.message, "error");
  }
}

function renderResults(data) {
  resultsSection.style.display = "";
  flowToResults.style.display = "";

  resultsMeta.textContent =
    `${data.results.length} results · ${data.candidates_total} candidates · CLIP ${data.clip_available ? "✓" : "✗"}`;

  // Stats badges
  resultsStats.innerHTML = "";
  if (!data.clip_available) {
    const badge = document.createElement("span");
    badge.className = "stat-badge clip-off";
    badge.textContent = "CLIP unavailable — results unscored";
    resultsStats.appendChild(badge);
  }

  resultsGrid.innerHTML = "";
  for (const r of data.results) {
    const card = document.createElement("div");
    card.className = "result-card";

    const thumbUrl = r.thumbnail_url || `/api/images/${r.image_id}/thumb`;
    const scores = buildScoreBars(r);

    card.innerHTML = `
      <img src="${esc(thumbUrl)}" alt="${esc(r.file_name)}" loading="lazy"
           onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%22220%22 height=%22220%22><rect fill=%22%23f7f1e8%22 width=%22220%22 height=%22220%22/><text x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%23999%22 font-size=%2214%22>No thumb</text></svg>'">
      <div class="score-bar">
        ${scores}
      </div>
      <div class="card-footer" title="${esc(r.file_name)}">
        #${r.image_id} · ${esc(r.file_name)}
      </div>
    `;
    resultsGrid.appendChild(card);
  }
}

function buildScoreBars(r) {
  const rows = [];
  if (r.identity_score != null) {
    rows.push(scoreRow("Identity", r.identity_score, "identity"));
  }
  if (r.context_score != null) {
    rows.push(scoreRow("Context", r.context_score, "context"));
  }
  if (r.composite_score != null) {
    rows.push(scoreRow("Composite", r.composite_score, "composite"));
  }
  return rows.join("");
}

function scoreRow(label, value, cls) {
  const pct = Math.max(0, Math.min(100, value * 100));
  return `
    <div class="score-row">
      <span class="score-label">${label}</span>
      <span class="score-value">${value.toFixed(4)}</span>
    </div>
    <div class="score-bar-track">
      <div class="score-bar-fill ${cls}" style="width:${pct.toFixed(1)}%"></div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Concepts index
// ---------------------------------------------------------------------------
let conceptsCache = [];

async function loadIndex() {
  refreshIndexBtn.disabled = true;
  try {
    const data = await apiGet("/api/taxonomy/concept-search/concepts-index");
    conceptsCache = data.concepts;
    renderIndexSummary(data);
    renderIndexTable(conceptsCache);
  } catch (e) {
    indexSummary.textContent = `Error: ${e.message}`;
  } finally {
    refreshIndexBtn.disabled = false;
  }
}

function renderIndexSummary(data) {
  const withProto = data.concepts.filter(c => c.has_prototype).length;
  const withObs = data.concepts.filter(c => c.observation_count > 0).length;
  const searchable = data.concepts.filter(c => c.has_prototype && c.observation_count > 0).length;
  indexSummary.innerHTML =
    `<strong>${data.total_concepts}</strong> concepts · ` +
    `<strong>${withProto}</strong> with prototype · ` +
    `<strong>${withObs}</strong> with observations · ` +
    `<strong>${searchable}</strong> fully searchable`;
}

function renderIndexTable(concepts) {
  indexTable.innerHTML = "";
  for (const c of concepts) {
    const tr = document.createElement("tr");
    const aliasTags = c.aliases.map(a => `<span class="alias-tag">${esc(a)}</span>`).join("");
    tr.innerHTML = `
      <td>${c.concept_id}</td>
      <td><strong>${esc(c.canonical_name)}</strong></td>
      <td>${esc(c.concept_type || "—")}</td>
      <td>${aliasTags || "—"}</td>
      <td>${c.has_prototype
        ? `<span class="prototype-yes">✓</span> (${c.prototype_source_count || 0})`
        : `<span class="prototype-no">✗</span>`}</td>
      <td>${c.observation_count}</td>
    `;
    indexTable.appendChild(tr);
  }
}

indexFilter.addEventListener("input", () => {
  const q = indexFilter.value.toLowerCase();
  if (!q) {
    renderIndexTable(conceptsCache);
    return;
  }
  const filtered = conceptsCache.filter(c =>
    c.canonical_name.toLowerCase().includes(q) ||
    c.aliases.some(a => a.toLowerCase().includes(q)) ||
    String(c.concept_id).includes(q)
  );
  renderIndexTable(filtered);
});

// ---------------------------------------------------------------------------
// Event listeners
// ---------------------------------------------------------------------------
searchForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const query = searchInput.value.trim();
  if (query) runSearch(query);
});

decomposeBtn.addEventListener("click", () => {
  const query = searchInput.value.trim();
  if (query) runDecompose(query);
});

refreshIndexBtn.addEventListener("click", loadIndex);

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
loadIndex();

// ---------------------------------------------------------------------------
// Util
// ---------------------------------------------------------------------------
function esc(s) {
  if (s == null) return "";
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}
