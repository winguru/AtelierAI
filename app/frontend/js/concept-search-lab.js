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

// Editor
const editorSection    = $("#editor-section");
const editorSubtitle   = $("#editor-subtitle");
const editorCloseBtn   = $("#editor-close-btn");
const editorTabs       = $("#editor-tabs");
const editorTabsBtns   = $$("#editor-tabs .editor-tab");

// Properties
const edName           = $("#ed-name");
const edType           = $("#ed-type");
const edStatus         = $("#ed-status");
const edDesc           = $("#ed-desc");
const edPrototypeStatus = $("#ed-prototype-status");
const edSaveProps      = $("#ed-save-props");
const edPropsFeedback  = $("#ed-props-feedback");

// Aliases
const edAliasesBody    = $("#ed-aliases-body");
const edNewAlias       = $("#ed-new-alias");
const edNewAliasType   = $("#ed-new-alias-type");
const edNewAliasPref   = $("#ed-new-alias-pref");
const edAddAlias       = $("#ed-add-alias");
const edAliasFeedback  = $("#ed-alias-feedback");

// Hierarchy
const edParentDisplay  = $("#ed-parent-display");
const edSetParent      = $("#ed-set-parent");
const edClearParent    = $("#ed-clear-parent");
const edChildrenList   = $("#ed-children-list");
const edHierarchyFeedback = $("#ed-hierarchy-feedback");

// Concept Lookup widgets (created after DOM ready)
const parentLookup = ConceptLookup.create({
  container:   "#parent-lookup",
  idInputId:   "ed-new-parent-id",
  nameInputId: "ed-new-parent-name",
  placeholder: "Parent concept name…",
});
const attrLookup = ConceptLookup.create({
  container:   "#attr-lookup",
  idInputId:   "ed-attr-id",
  nameInputId: "ed-attr-name",
  placeholder: "Attribute concept name…",
});

// Create child
const edChildName      = $("#ed-child-name");
const edChildDesc      = $("#ed-child-desc");
const edCreateChild    = $("#ed-create-child");
const edCreateFeedback = $("#ed-create-feedback");

// Attributes
const edAttrTable        = $("#ed-attributes-table");
const edAttrsBody        = edAttrTable ? edAttrTable.querySelector("tbody") : null;
const edAttrsEmpty       = $("#ed-attributes-empty");
const edAttrKind         = $("#ed-attr-kind");
const edAttrInvariance   = $("#ed-attr-invariance");
const edAttrScore        = $("#ed-attr-score");
const edAttrNotes        = $("#ed-attr-notes");
const edAttrAddBtn       = $("#ed-attr-add-btn");
const edAttrFeedback     = $("#ed-attr-feedback");

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
// Tooltip system (JS-driven — escapes overflow clipping)
// ---------------------------------------------------------------------------
const EDITOR_TIPS = {
  "ed-tip-type":
    'What kind of visual entity does this concept represent? This categorization helps organize the taxonomy.\n\n• character — A named or generic person/being (e.g. "saber", "megumin", "catgirl"). Use when the concept is a distinct entity that appears in a scene.\n\n• object — A physical thing or prop (e.g. "sword", "glasses", "ribbon"). Tangible items a character might hold or wear, or that appear in a scene independently.\n\n• style — An art style, medium, or visual technique (e.g. "watercolor", "cel shading", "3D render", "oil painting"). Use when the concept describes HOW something is drawn, not WHAT is in the image.\n\n• scene — A setting, environment, or spatial composition (e.g. "outdoor", "classroom", "sunset", "forest"). Use when the concept describes WHERE or under what conditions the image takes place.\n\n• abstract — Anything that doesn\'t fit the other categories: visual symbols (e.g. "!" manga mark, "..." thought bubble), meta-qualities (e.g. "best", "simple"), emotions (e.g. "sadness"), or other non-tangible concepts that are visually depicted but aren\'t characters, objects, styles, or scenes.\n\nIf unsure, leave as "none". Most concepts in this taxonomy are currently untyped.',

  "ed-tip-status":
    'Lifecycle status of this concept:\n\n• active — The concept is in use and appears in search/decomposition results. This is the normal state for verified concepts.\n\n• deprecated — The concept is being phased out or was created by mistake. It still exists in the taxonomy but should not be used for new work.\n\n• draft — A newly created concept that hasn\'t been reviewed or verified yet. Not yet ready for production use.\n\n• none — No status assigned. The concept exists but hasn\'t been classified by lifecycle stage.',

  "ed-tip-alias-type":
    'What kind of alias this is:\n• synonym — A different word with the same meaning (e.g. "exclamation" for "!")\n• abbreviation — A shortened form (e.g. "sfw" for "safe for work")\n• alternate_form — A spelling variant or different surface form (e.g. "colour" for "color")\n• imported — Automatically created from an external tag source (CivitAI, etc.)\n• canonical — The concept\'s primary name, stored as a special alias\n• danbooru_word — Imported from a Danbooru tag',
};

const tipEl = document.createElement("div");
tipEl.className = "tip-popup";
document.body.appendChild(tipEl);

let tipTarget = null;

function resolveTipText(el) {
  // data-tip takes priority (inline short tips)
  const inline = el.getAttribute("data-tip");
  if (inline) return inline;
  // data-tip-id references EDITOR_TIPS map
  const tipId = el.getAttribute("data-tip-id");
  if (tipId && EDITOR_TIPS[tipId]) return EDITOR_TIPS[tipId];
  return null;
}

function showTip(el) {
  const text = resolveTipText(el);
  if (!text) return;
  tipTarget = el;

  const isBelow = el.classList.contains("tip-below");
  // Preserve newlines: split on \n, rebuild with <br> elements
  tipEl.innerHTML = "";
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    if (i > 0) tipEl.appendChild(document.createElement("br"));
    if (line.startsWith("• ")) {
      const strong = document.createElement("strong");
      strong.textContent = line;
      tipEl.appendChild(strong);
    } else {
      tipEl.appendChild(document.createTextNode(line));
    }
  });
  tipEl.className = "tip-popup" + (isBelow ? " tip-below" : "");
  // Use wider tooltip for editor help icons
  if (el.closest(".editor-panel") || el.closest(".editor-tabs")) {
    tipEl.classList.add("tip-wide");
  }

  // Force layout so we can measure
  tipEl.classList.add("visible");
  const r = el.getBoundingClientRect();
  const tw = tipEl.offsetWidth;
  const th = tipEl.offsetHeight;

  let left = r.left + r.width / 2 - tw / 2;
  let top;

  if (isBelow) {
    top = r.bottom + 8;
  } else {
    top = r.top - th - 8;
  }

  // Clamp to viewport
  left = Math.max(6, Math.min(left, window.innerWidth - tw - 6));
  if (top < 6) top = r.bottom + 8; // flip below if off-screen top

  tipEl.style.left = left + "px";
  tipEl.style.top = top + "px";
}

function hideTip() {
  tipEl.classList.remove("visible");
  tipTarget = null;
}

// Event delegation — works for static and JS-rendered elements
document.addEventListener("mouseenter", (e) => {
  const el = e.target && e.target.closest
    ? e.target.closest("[data-tip], [data-tip-id]")
    : null;
  if (el) showTip(el);
}, true);
document.addEventListener("mouseleave", (e) => {
  const el = e.target && e.target.closest
    ? e.target.closest("[data-tip], [data-tip-id]")
    : null;
  if (el && el === tipTarget) hideTip();
}, true);

// Hide on scroll to prevent stale positioning
document.addEventListener("scroll", hideTip, true);

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

async function apiDelete(url) {
  const res = await fetch(url, { method: "DELETE" });
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
  decomposeOriginal.innerHTML = `<strong data-tip="The original search query you typed, before decomposition.">Query:</strong> ${esc(data.original_query)}`;
  decomposeContext.innerHTML = `<strong data-tip="The leftover text after all recognized concepts have been extracted. This context is used for CLIP text-image similarity scoring alongside the concept identity match.">Context:</strong> ${esc(data.context_text || "(none)")}`;
  decomposeTotalForms.innerHTML = `<strong data-tip="Total number of searchable name variations indexed across all matched concepts (canonical names + aliases). More surface forms = better chance of matching your query words.">Surface forms indexed:</strong> ${data.total_surface_forms}`;

  decomposeTable.innerHTML = "";
  for (const mc of data.matched_concepts) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong class="concept-link" data-concept-id="${mc.concept_id}">${esc(mc.concept_name)}</strong> <span style="color:var(--ink-soft)">#${mc.concept_id}</span></td>
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

    // Prefer local thumbnail endpoint when file_hash is available — avoids CDN
    // issues (ORB/CORS blocking) that silently prevent onerror from firing.
    const localThumb = r.file_hash ? `/api/images/${r.file_hash}/thumb` : null;
    const thumbUrl = localThumb || r.thumbnail_url || null;
    const fallbackUrl = (r.thumbnail_url && localThumb) ? r.thumbnail_url : null;
    const scores = buildScoreBars(r);

    let onerror;
    if (fallbackUrl && thumbUrl !== fallbackUrl) {
      onerror = `this.onerror=null;this.src='${fallbackUrl}'`;
    } else {
      onerror = `this.onerror=null;this.src='data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22220%22 height=%22220%22%3E%3Crect fill=%22%23f7f1e8%22 width=%22220%22 height=%22220%22/%3E%3Ctext x=%2250%25%22 y=%2250%25%22 text-anchor=%22middle%22 dy=%22.3em%22 fill=%22%23999%22 font-size=%2214%22%3ENo thumb%3C/text%3E%3C/svg%3E'`;
    }

    card.innerHTML = `
      <img src="${esc(thumbUrl || '')}" alt="${esc(r.file_name)}"
           onerror="${onerror}">
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
    rows.push(scoreRow("Identity", r.identity_score, "identity",
      "How well the image matches the specific concept(s) found in your query (e.g. the character 'Shion'). Uses CLIP visual comparison against the concept's prototype vector."));
  }
  if (r.context_score != null) {
    rows.push(scoreRow("Context", r.context_score, "context",
      "How well the image matches the remaining descriptive text (e.g. 'on a beach at sunset'). Uses CLIP text-to-image similarity."));
  }
  if (r.composite_score != null) {
    rows.push(scoreRow("Composite", r.composite_score, "composite",
      "The combined overall score blending identity and context. Images rank highest when they match both the named concept(s) AND the descriptive context."));
  }
  return rows.join("");
}

function scoreRow(label, value, cls, tip) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const tipAttr = tip ? ` data-tip="${esc(tip)}"` : '';
  const tipClass = tip ? ' tip-below' : '';
  return `
    <div class="score-row">
      <span class="score-label${tipClass}"${tipAttr}>${label}</span>
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
    `<strong data-tip="Total concepts in the taxonomy, regardless of whether they are searchable."> ${data.total_concepts}</strong> concepts · ` +
    `<strong data-tip="Concepts that have a computed CLIP visual prototype (an average embedding from example images). Only concepts with prototypes can be used for visual identity scoring."> ${withProto}</strong> with prototype · ` +
    `<strong data-tip="Concepts that have been observed on at least one image (via imported tags or manual curation). Observations link images to concepts."> ${withObs}</strong> with observations · ` +
    `<strong data-tip="Concepts that are fully searchable — they have both a CLIP prototype AND image observations. These concepts will produce scored results when used in a search query."> ${searchable}</strong> fully searchable`;
}

function renderIndexTable(concepts) {
  indexTable.innerHTML = "";
  for (const c of concepts) {
    const tr = document.createElement("tr");
    const aliasTags = c.aliases.map(a => `<span class="alias-tag">${esc(a)}</span>`).join("");
    tr.innerHTML = `
      <td>${c.concept_id}</td>
      <td><strong class="concept-link" data-concept-id="${c.concept_id}">${esc(c.canonical_name)}</strong></td>
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
// Concept Editor
// ---------------------------------------------------------------------------
let currentConceptId = null;
let currentProfile = null;

function selectConcept(id) {
  currentConceptId = id;
  loadConceptProfile(id);
}

async function loadConceptProfile(id) {
  editorSection.style.display = "";
  editorSubtitle.textContent = `Loading concept #${id}…`;
  // Activate properties tab
  switchEditorTab("properties");
  try {
    const profile = await apiGet(`/api/taxonomy/concepts/${id}/profile`);
    currentProfile = profile;
    renderConceptEditor(profile);
  } catch (e) {
    editorSubtitle.textContent = `Error: ${e.message}`;
  }
}

function renderConceptEditor(profile) {
  editorSubtitle.textContent = `#${profile.id} · ${profile.canonical_name}`;

  // Properties
  edName.value = profile.canonical_name || "";
  edType.value = profile.concept_type || "";
  edStatus.value = profile.status || "";
  edDesc.value = profile.description || "";
  edPropsFeedback.textContent = "";
  if (profile.prototype && profile.prototype.vector) {
    edPrototypeStatus.textContent = `✓ (${profile.prototype.source_count || "?"} sources, updated ${profile.prototype.updated_at || "?"})`;
    edPrototypeStatus.className = "ed-readonly ed-proto-yes";
  } else {
    edPrototypeStatus.textContent = "✗ No prototype";
    edPrototypeStatus.className = "ed-readonly ed-proto-no";
  }

  // Aliases
  renderAliasesTable(profile.aliases || []);
  edNewAlias.value = "";
  edAliasFeedback.textContent = "";

  // Hierarchy
  if (profile.parent_concept) {
    edParentDisplay.innerHTML = `<span class="concept-link" data-concept-id="${profile.parent_concept.id}">${esc(profile.parent_concept.canonical_name)}</span> <span style="color:var(--ink-soft)">#${profile.parent_concept.id}</span>`;
  } else {
    edParentDisplay.textContent = "— root concept";
  }
  if (profile.children && profile.children.length > 0) {
    edChildrenList.innerHTML = profile.children.map(ch =>
      `<span class="ed-child-chip concept-link" data-concept-id="${ch.id}">${esc(ch.canonical_name)} <span style="color:var(--ink-soft)">#${ch.id}</span></span>`
    ).join("");
  } else {
    edChildrenList.textContent = "— no children";
  }
  parentLookup.reset();
  edHierarchyFeedback.textContent = "";

  // Attributes
  renderAttributesTable(profile.attributes || []);
  attrLookup.reset();
  edAttrKind.value = "visual";
  edAttrInvariance.value = "variable";
  edAttrScore.value = "";
  edAttrNotes.value = "";
  edAttrFeedback.textContent = "";

  // Create child
  edChildName.value = "";
  edChildDesc.value = "";
  edCreateFeedback.textContent = "";

  // Scroll editor into view
  editorSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderAliasesTable(aliases) {
  edAliasesBody.innerHTML = "";
  if (!aliases || aliases.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="5" style="text-align:center;color:var(--ink-soft)">No aliases</td>`;
    edAliasesBody.appendChild(tr);
    return;
  }
  for (const a of aliases) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(a.alias)}</td>
      <td>${esc(a.alias_type || "—")}</td>
      <td>${a.is_preferred ? "★" : ""}</td>
      <td>${esc(a.authority_name || a.authority_id || "—")}</td>
      <td><button type="button" class="btn-icon btn-delete-alias" data-alias-id="${a.id}" title="Delete alias">🗑</button></td>
    `;
    edAliasesBody.appendChild(tr);
  }
}

function switchEditorTab(tabName) {
  const panels = $$("#editor-section .editor-panel");
  panels.forEach(p => p.style.display = "none");
  const target = $(`#editor-panel-${tabName}`);
  if (target) target.style.display = "";

  editorTabsBtns.forEach(b => {
    b.classList.toggle("active", b.dataset.tab === tabName);
  });
}

// Editor tab clicks
editorTabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".editor-tab");
  if (btn && btn.dataset.tab) switchEditorTab(btn.dataset.tab);
});

// Close editor
editorCloseBtn.addEventListener("click", () => {
  editorSection.style.display = "none";
  currentConceptId = null;
  currentProfile = null;
});

// Save properties
edSaveProps.addEventListener("click", async () => {
  if (!currentConceptId) return;
  edPropsFeedback.textContent = "Saving…";
  try {
    await fetch(`/api/taxonomy/concepts/${currentConceptId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canonical_name: edName.value.trim() || undefined,
        concept_type: edType.value || undefined,
        status: edStatus.value || undefined,
        description: edDesc.value.trim() || undefined,
      }),
    }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
    edPropsFeedback.textContent = "✓ Saved";
    // Reload profile to reflect changes
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edPropsFeedback.textContent = `Error: ${e.message}`;
  }
});

// Add alias
edAddAlias.addEventListener("click", async () => {
  if (!currentConceptId) return;
  const alias = edNewAlias.value.trim();
  if (!alias) { edAliasFeedback.textContent = "Alias required"; return; }
  edAliasFeedback.textContent = "Adding…";
  try {
    await apiPost(`/api/taxonomy/concepts/${currentConceptId}/aliases`, {
      alias,
      alias_type: edNewAliasType.value,
      is_preferred: edNewAliasPref.checked,
    });
    edNewAlias.value = "";
    edAliasFeedback.textContent = "✓ Added";
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edAliasFeedback.textContent = `Error: ${e.message}`;
  }
});

// Delete alias (event delegation on tbody)
edAliasesBody.addEventListener("click", async (e) => {
  const btn = e.target.closest(".btn-delete-alias");
  if (!btn || !currentConceptId) return;
  const aliasId = btn.dataset.aliasId;
  if (!confirm("Delete this alias?")) return;
  try {
    await fetch(`/api/taxonomy/concepts/${currentConceptId}/aliases/${aliasId}`, {
      method: "DELETE",
    }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edAliasFeedback.textContent = `Error: ${e.message}`;
  }
});

// Reparent
edSetParent.addEventListener("click", async () => {
  if (!currentConceptId) return;
  const parentId = parentLookup.getId();
  if (!parentId) { edHierarchyFeedback.textContent = "Enter a parent concept"; return; }
  edHierarchyFeedback.textContent = "Reparenting…";
  try {
    await apiPost(`/api/taxonomy/concepts/${currentConceptId}/parent`, {
      parent_concept_id: parentId,
    });
    edHierarchyFeedback.textContent = "✓ Reparented";
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edHierarchyFeedback.textContent = `Error: ${e.message}`;
  }
});

// Clear parent
edClearParent.addEventListener("click", async () => {
  if (!currentConceptId) return;
  edHierarchyFeedback.textContent = "Clearing parent…";
  try {
    await apiPost(`/api/taxonomy/concepts/${currentConceptId}/parent`, {
      parent_concept_id: null,
    });
    edHierarchyFeedback.textContent = "✓ Parent cleared";
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edHierarchyFeedback.textContent = `Error: ${e.message}`;
  }
});

// Create child concept
edCreateChild.addEventListener("click", async () => {
  if (!currentConceptId) return;
  const name = edChildName.value.trim();
  if (!name) { edCreateFeedback.textContent = "Name required"; return; }
  edCreateFeedback.textContent = "Creating…";
  try {
    const result = await apiPost("/api/taxonomy/concepts", {
      canonical_name: name,
      parent_concept_id: currentConceptId,
      description: edChildDesc.value.trim() || undefined,
    });
    edCreateFeedback.textContent = `✓ Created #${result.concept.id}`;
    edChildName.value = "";
    edChildDesc.value = "";
    // Reload current concept to show new child
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edCreateFeedback.textContent = `Error: ${e.message}`;
  }
});

// ── Attributes ─────────────────────────────────────────────────────────────

function renderAttributesTable(attributes) {
  if (!edAttrsBody) return;
  edAttrsBody.innerHTML = "";
  if (!attributes || attributes.length === 0) {
    edAttrTable.style.display = "none";
    if (edAttrsEmpty) edAttrsEmpty.style.display = "";
    return;
  }
  edAttrTable.style.display = "";
  if (edAttrsEmpty) edAttrsEmpty.style.display = "none";
  for (const a of attributes) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="concept-link" data-concept-id="${a.attribute_concept_id}">${esc(a.attribute_concept_name)}</span> <span style="color:var(--ink-soft)">#${a.attribute_concept_id}</span></td>
      <td>${esc(a.attribute_kind)}</td>
      <td>${esc(a.invariance)}</td>
      <td>${a.consistency_score != null ? a.consistency_score.toFixed(2) : "—"}</td>
      <td>${esc(a.notes || "—")}</td>
      <td><button type="button" class="btn-icon btn-delete-attr" data-attr-concept-id="${a.attribute_concept_id}" title="Remove attribute">🗑</button></td>
    `;
    edAttrsBody.appendChild(tr);
  }
}

// Add attribute
edAttrAddBtn.addEventListener("click", async () => {
  if (!currentConceptId) return;
  const attributeConceptId = attrLookup.getId();
  if (!attributeConceptId || isNaN(attributeConceptId)) {
    edAttrFeedback.textContent = "Enter a valid concept";
    return;
  }
  edAttrFeedback.textContent = "Adding…";
  try {
    const payload = {
      attribute_concept_id: attributeConceptId,
      attribute_kind: edAttrKind.value,
      invariance: edAttrInvariance.value,
    };
    const score = parseFloat(edAttrScore.value);
    if (!isNaN(score)) payload.consistency_score = score;
    const notes = edAttrNotes.value.trim();
    if (notes) payload.notes = notes;
    await apiPost(`/api/taxonomy/concepts/${currentConceptId}/attributes`, payload);
    edAttrFeedback.textContent = "✓ Attribute added";
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edAttrFeedback.textContent = `Error: ${e.message}`;
  }
});

// Delete attribute (delegated)
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".btn-delete-attr");
  if (!btn || !currentConceptId) return;
  const attrConceptId = parseInt(btn.dataset.attrConceptId, 10);
  if (!attrConceptId) return;
  try {
    await apiDelete(`/api/taxonomy/concepts/${currentConceptId}/attributes/${attrConceptId}`);
    await loadConceptProfile(currentConceptId);
  } catch (e) {
    edAttrFeedback.textContent = `Error: ${e.message}`;
  }
});

// Delegated click handler for concept links (in decomposition, index, and editor)
document.addEventListener("click", (e) => {
  const link = e.target.closest(".concept-link");
  if (link && link.dataset.conceptId) {
    e.preventDefault();
    selectConcept(parseInt(link.dataset.conceptId, 10));
  }
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
// Rescan Associations
// ---------------------------------------------------------------------------
const rescanBtn = $("#rescan-btn");
rescanBtn.addEventListener("click", async () => {
  rescanBtn.disabled = true;
  rescanBtn.textContent = "Scanning…";
  try {
    const res = await fetch("/api/taxonomy/concept-search/rescan", { method: "POST" });
    const data = await res.json();
    setStatus("Rescan complete", data.message, "success");
    // Refresh the coverage index to reflect changes
    await loadIndex();
  } catch (err) {
    setStatus("Rescan failed", err.message, "error");
  } finally {
    rescanBtn.disabled = false;
    rescanBtn.textContent = "Rescan Associations";
  }
});

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
