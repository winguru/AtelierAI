// ═══════════════════════════════════════════════════════════════════
//  Prototype Lab — Frontend controller
// ═══════════════════════════════════════════════════════════════════

const API = "/api/taxonomy";

// ── State ──────────────────────────────────────────────────────────
let allConcepts = [];
let filteredConcepts = [];
let selectedIds = new Set();

// ── DOM refs ───────────────────────────────────────────────────────
const $statsLoading   = document.getElementById("stats-loading");
const $statsPanel     = document.getElementById("stats-panel");
const $statTotal      = document.getElementById("stat-total");
const $statObserved   = document.getElementById("stat-observed");
const $statPrototypes = document.getElementById("stat-prototypes");
const $statCoverage   = document.getElementById("stat-coverage");
const $bracketsBody   = document.getElementById("brackets-body");

const $statusTitle    = document.getElementById("status-title");
const $statusMessage  = document.getElementById("status-message");
const $statusPanel    = document.getElementById("status-panel");

const $bracketFilter  = document.getElementById("bracket-filter");
const $nameSearch     = document.getElementById("name-search");
const $conceptsBody   = document.getElementById("concepts-body");
const $tableFooter    = document.getElementById("table-footer");
const $selectionCount = document.getElementById("selection-count");
const $checkAll       = document.getElementById("check-all");
const $buildBtn       = document.getElementById("build-selected-btn");
const $maxImages      = document.getElementById("max-images");

const $buildProgress  = document.getElementById("build-progress");
const $progressFill   = document.getElementById("progress-fill");
const $progressText   = document.getElementById("progress-text");
const $buildTable     = document.getElementById("build-results-table");
const $buildBody      = document.getElementById("build-results-body");

// ── Theme ──────────────────────────────────────────────────────────
const $themeToggle = document.getElementById("theme-toggle");

function initTheme() {
  const saved = localStorage.getItem("atelier-theme");
  const dark = saved === "dark" || (!saved && matchMedia("(prefers-color-scheme:dark)").matches);
  if (dark) {
    document.body.dataset.theme = "dark";
    $themeToggle.checked = true;
  }
  $themeToggle.addEventListener("change", () => {
    if ($themeToggle.checked) {
      document.body.dataset.theme = "dark";
      localStorage.setItem("atelier-theme", "dark");
    } else {
      delete document.body.dataset.theme;
      localStorage.setItem("atelier-theme", "light");
    }
  });
}

// ── Helpers ────────────────────────────────────────────────────────
function setStatus(title, message, level = "info") {
  $statusTitle.textContent = title;
  $statusMessage.textContent = ` — ${message}`;
  $statusPanel.className = `step-status ${level}`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

const BRACKET_LABELS = {
  "1_to_5":   "1–5",
  "6_to_20":  "6–20",
  "21_to_50": "21–50",
  "51_to_100":"51–100",
  "101_plus": "101+",
};

function obsBracket(count) {
  if (count <= 5) return "1_to_5";
  if (count <= 20) return "6_to_20";
  if (count <= 50) return "21_to_50";
  if (count <= 100) return "51_to_100";
  return "101_plus";
}

// ── Stats ──────────────────────────────────────────────────────────
async function loadStats() {
  $statsLoading.style.display = "";
  $statsPanel.style.display = "none";
  try {
    const res = await fetch(`${API}/prototypes/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    $statTotal.textContent = data.total_concepts.toLocaleString();
    $statObserved.textContent = data.with_observations.toLocaleString();
    $statPrototypes.textContent = data.with_prototypes.toLocaleString();
    const pct = data.with_observations > 0
      ? ((data.with_prototypes / data.with_observations) * 100).toFixed(1)
      : "0.0";
    $statCoverage.textContent = `${pct}%`;

    // Brackets table
    const obs = data.observation_brackets || {};
    const proto = data.prototype_brackets || {};
    const totalObs = Object.values(obs).reduce((s, v) => s + v, 0);
    const totalProto = Object.values(proto).reduce((s, v) => s + v, 0);
    $bracketsBody.innerHTML = "";

    for (const [key, label] of Object.entries(BRACKET_LABELS)) {
      const c = obs[key] || 0;
      const p = proto[key] || 0;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${label}</td>
        <td>${c}</td>
        <td>${p}</td>
        <td>
          <span class="btn-group">
            <button class="btn small build-bracket-btn" data-bracket="${key}">Build all</button>
            <button class="btn small btn-secondary build-missing-bracket-btn" data-bracket="${key}">Build missing</button>
          </span>
        </td>`;
      $bracketsBody.appendChild(tr);
    }

    // Totals row (at bottom)
    const totalsTr = document.createElement("tr");
    totalsTr.className = "totals-row";
    totalsTr.innerHTML = `
      <td><strong>Total</strong></td>
      <td><strong>${totalObs}</strong></td>
      <td><strong>${totalProto}</strong></td>
      <td>
        <span class="btn-group">
          <button class="btn small build-all-totals-btn">Build All</button>
          <button class="btn small btn-secondary build-missing-totals-btn">Build Missing</button>
        </span>
      </td>`;
    $bracketsBody.appendChild(totalsTr);

    // Wire totals buttons
    $bracketsBody.querySelector(".build-all-totals-btn")
      .addEventListener("click", () => {
        const allIds = allConcepts.map(c => c.concept_id);
        if (allIds.length === 0) { setStatus("Empty", "No concepts loaded.", "warning"); return; }
        batchBuild(allIds);
      });
    $bracketsBody.querySelector(".build-missing-totals-btn")
      .addEventListener("click", () => {
        const missingIds = allConcepts.filter(c => !c.has_prototype).map(c => c.concept_id);
        if (missingIds.length === 0) { setStatus("Empty", "All concepts already have prototypes.", "success"); return; }
        batchBuild(missingIds);
      });

    // Wire bracket build buttons
    for (const btn of $bracketsBody.querySelectorAll(".build-bracket-btn")) {
      btn.addEventListener("click", () => buildBracket(btn.dataset.bracket));
    }
    for (const btn of $bracketsBody.querySelectorAll(".build-missing-bracket-btn")) {
      btn.addEventListener("click", () => buildBracketMissing(btn.dataset.bracket));
    }

    $statsLoading.style.display = "none";
    $statsPanel.style.display = "";
  } catch (err) {
    $statsLoading.textContent = `Error loading stats: ${err.message}`;
    setStatus("Error", err.message, "error");
  }
}

// ── Concepts Index ─────────────────────────────────────────────────
async function loadConcepts() {
  setStatus("Loading", "Fetching concept index…", "info");
  try {
    const res = await fetch(`${API}/concept-search/concepts-index`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allConcepts = data.concepts.filter(c => c.observation_count > 0);
    filteredConcepts = [...allConcepts];
    setStatus("Ready", `${allConcepts.length} concepts with observations loaded.`, "info");
    renderConcepts();
  } catch (err) {
    setStatus("Error", `Failed to load concepts: ${err.message}`, "error");
  }
}

function filterConcepts() {
  const bracket = $bracketFilter.value;
  const name = $nameSearch.value.trim().toLowerCase();

  filteredConcepts = allConcepts.filter(c => {
    // Bracket filter
    if (bracket === "no_proto") {
      if (c.has_prototype) return false;
    } else if (bracket !== "all") {
      if (obsBracket(c.observation_count) !== bracket) return false;
    }
    // Name filter
    if (name) {
      const haystack = `${c.canonical_name} ${(c.aliases || []).join(" ")}`.toLowerCase();
      if (!haystack.includes(name)) return false;
    }
    return true;
  });

  renderConcepts();
}

function renderConcepts() {
  $conceptsBody.innerHTML = "";

  // Sort: no prototype first, then by observation count descending
  filteredConcepts.sort((a, b) => {
    if (a.has_prototype !== b.has_prototype) return a.has_prototype ? 1 : -1;
    return b.observation_count - a.observation_count;
  });

  const max = 200; // Render cap for performance
  const slice = filteredConcepts.slice(0, max);

  for (const c of slice) {
    const tr = document.createElement("tr");
    const checked = selectedIds.has(c.concept_id) ? "checked" : "";
    const protoBadge = c.has_prototype
      ? '<span class="badge badge-yes">✓</span>'
      : '<span class="badge badge-no">✗</span>';

    tr.innerHTML = `
      <td class="col-check"><input type="checkbox" data-id="${c.concept_id}" ${checked}></td>
      <td>${c.concept_id}</td>
      <td><strong>${esc(c.canonical_name)}</strong></td>
      <td>${c.concept_type || "—"}</td>
      <td>${c.observation_count}</td>
      <td>${protoBadge}</td>
      <td>${c.prototype_source_count ?? "—"}</td>
      <td>${fmtDate(c.prototype_updated_at)}</td>
      <td><button class="btn small build-one-btn" data-id="${c.concept_id}">Build</button></td>`;
    $conceptsBody.appendChild(tr);
  }

  // Wire checkboxes
  for (const cb of $conceptsBody.querySelectorAll('input[type="checkbox"]')) {
    cb.addEventListener("change", () => {
      const id = Number(cb.dataset.id);
      cb.checked ? selectedIds.add(id) : selectedIds.delete(id);
      updateSelection();
    });
  }

  // Wire single build buttons
  for (const btn of $conceptsBody.querySelectorAll(".build-one-btn")) {
    btn.addEventListener("click", () => buildOne(Number(btn.dataset.id)));
  }

  // Footer
  const total = filteredConcepts.length;
  $tableFooter.textContent = total > max
    ? `Showing ${max} of ${total} concepts. Use filters to narrow results.`
    : `${total} concepts`;

  updateSelection();
}

function updateSelection() {
  $selectionCount.textContent = `${selectedIds.size} selected`;
  $buildBtn.disabled = selectedIds.size === 0;
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ── Build Operations ───────────────────────────────────────────────

async function buildOne(conceptId) {
  setStatus("Building", `Building prototype for concept ${conceptId}…`, "info");
  try {
    const res = await fetch(
      `${API}/concepts/${conceptId}/auto-build-prototype?max_images=${$maxImages.value}`,
      { method: "POST" },
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.status === "built") {
      setStatus("Built", `${data.concept_name}: ${data.message}`, "success");
    } else {
      setStatus("Skipped", `${data.concept_name}: ${data.message}`, "warning");
    }

    // Refresh both stats and concepts
    await Promise.all([loadStats(), loadConcepts()]);
  } catch (err) {
    setStatus("Error", err.message, "error");
  }
}

async function buildBracket(bracket) {
  const ids = allConcepts
    .filter(c => obsBracket(c.observation_count) === bracket)
    .map(c => c.concept_id);

  if (ids.length === 0) {
    setStatus("Empty", "No concepts in this bracket.", "warning");
    return;
  }
  await batchBuild(ids);
}

async function buildBracketMissing(bracket) {
  const ids = allConcepts
    .filter(c => obsBracket(c.observation_count) === bracket && !c.has_prototype)
    .map(c => c.concept_id);

  if (ids.length === 0) {
    setStatus("Empty", "No missing prototypes in this bracket.", "warning");
    return;
  }
  await batchBuild(ids);
}

async function batchBuild(ids) {
  if (ids.length === 0) return;

  $buildProgress.style.display = "";
  $buildTable.style.display = "";
  $buildBody.innerHTML = "";
  $progressFill.style.width = "0%";
  $progressText.textContent = `0 / ${ids.length}`;

  setStatus("Building", `Building ${ids.length} prototypes…`, "info");
  $buildBtn.disabled = true;

  let built = 0;
  let failed = 0;

  try {
    const res = await fetch(`${API}/prototypes/stream-build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        concept_ids: ids,
        max_images: Number($maxImages.value),
      }),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let currentEvent = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      // Keep the last (potentially incomplete) line in the buffer
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          const raw = line.slice(6);
          try {
            const data = JSON.parse(raw);

            if (currentEvent === "progress" && data.type === "result") {
              built = data.built;
              failed = data.failed;
              const statusClass = data.status === "built" ? "status-built"
                : data.status === "no_images" ? "status-no-images"
                : "status-failed";
              const tr = document.createElement("tr");
              tr.innerHTML = `
                <td>${data.concept_id}</td>
                <td class="${statusClass}">${data.status}</td>
                <td>${data.source_count}</td>
                <td>${esc(data.message)}</td>`;
              $buildBody.appendChild(tr);
            } else if (currentEvent === "progress" && data.type === "error") {
              built = data.built;
              failed = data.failed;
              const tr = document.createElement("tr");
              tr.innerHTML = `
                <td>${data.concept_id}</td>
                <td class="status-failed">error</td>
                <td>—</td>
                <td>${esc(data.message)}</td>`;
              $buildBody.appendChild(tr);
            }

            if (data.index !== undefined) {
              const pct = (data.index / ids.length * 100).toFixed(0);
              $progressFill.style.width = `${pct}%`;
              $progressText.textContent = `${data.index} / ${ids.length}`;
            }

            if (currentEvent === "complete") {
              built = data.built;
              failed = data.failed;
            }
          } catch { /* ignore malformed JSON */ }
          currentEvent = "";
        }
      }
    }
  } catch (err) {
    setStatus("Error", err.message, "error");
    $buildBtn.disabled = false;
    return;
  }

  setStatus(
    built > 0 ? "Complete" : "Finished",
    `Built ${built}, skipped/failed ${failed} of ${ids.length}.`,
    built > 0 ? "success" : "warning",
  );

  $buildBtn.disabled = false;
  await Promise.all([loadStats(), loadConcepts()]);
}

// ── Event wiring ───────────────────────────────────────────────────
function init() {
  initTheme();

  document.getElementById("refresh-stats-btn")
    .addEventListener("click", () => { loadStats(); loadConcepts(); });

  $bracketFilter.addEventListener("change", filterConcepts);
  $nameSearch.addEventListener("input", filterConcepts);

  document.getElementById("select-all-btn").addEventListener("click", () => {
    for (const c of filteredConcepts) selectedIds.add(c.concept_id);
    renderConcepts();
  });

  document.getElementById("select-none-btn").addEventListener("click", () => {
    selectedIds.clear();
    renderConcepts();
  });

  document.getElementById("select-no-proto-btn").addEventListener("click", () => {
    selectedIds.clear();
    for (const c of filteredConcepts) {
      if (!c.has_prototype) selectedIds.add(c.concept_id);
    }
    renderConcepts();
  });

  $checkAll.addEventListener("change", () => {
    if ($checkAll.checked) {
      for (const c of filteredConcepts) selectedIds.add(c.concept_id);
    } else {
      selectedIds.clear();
    }
    renderConcepts();
  });

  $buildBtn.addEventListener("click", () => {
    batchBuild([...selectedIds]);
  });

  // Initial load
  loadStats();
  loadConcepts();
}

init();
