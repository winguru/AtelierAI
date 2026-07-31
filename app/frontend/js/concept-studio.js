// ═══════════════════════════════════════════════════════════════════
//  Concept Studio — iterative concept-association workspace
//
//  Core loop:
//    1. Load exemplar image (by file_hash or random)
//    2. Fetch visual neighbours + aggregated concept suggestions
//    3. Pick or create a concept → optionally grade (strength, role)
//    4. Confirm → POST /api/visual-lookup/{file_hash}/hydrate
//    5. Confirmed concept moves to right pane; suggestions re-sort
//    6. "Next image" advances through the queue
// ═══════════════════════════════════════════════════════════════════

const TAXONOMY_API = "/api/taxonomy";
const VISUAL_API   = "/api/visual-lookup";
const IMAGES_API   = "/api/images";

// ── State ──────────────────────────────────────────────────────────
const PAGE_SIZE = 50;

const state = {
  currentImage: null,       // { id, file_hash, file_path, ... }
  imageQueue: [],           // list of image objects for prev/next
  queueIndex: -1,
  // Offset pagination across the full filtered catalog.
  // The /api/images endpoint supports cursor pagination only when
  // group_variants=true; since the concept studio uses
  // group_variants=false, we use offset (skip) pagination instead,
  // which always emits X-Filtered-Count.
  nextOffset: 0,            // skip value for the next page (0 = first page)
  hasMore: true,            // false once a page returns < PAGE_SIZE items
  filteredCount: null,      // total matching images (from X-Filtered-Count)
  searchTerm: "",           // search term the queue was built with ("" = none)
  isLoadingPage: false,     // guards against concurrent page fetches
  visualLookup: null,       // last visual-lookup response
  confirmedConcepts: [],    // [{ concept_id, name, source_type, ... }]
  suggestions: [],          // [{ concept_id, name, score, source }]
  newConceptDebounce: null,
};

// ── DOM refs ───────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);

const els = {
  statusBar:        $("status-bar"),
  statusText:       $("status-text"),

  // Left pane
  fileHashInput:    $("file-hash-input"),
  loadHashBtn:      $("load-hash-btn"),
  randomImageBtn:   $("random-image-btn"),
  prevImageBtn:     $("prev-image-btn"),
  nextImageBtn:     $("next-image-btn"),
  queuePos:         $("queue-pos"),
  exemplarFrame:    $("exemplar-frame"),
  exemplarImage:    $("exemplar-image"),
  exemplarPlaceholder: $("exemplar-placeholder"),
  exemplarMeta:     $("exemplar-meta"),
  metaFileHash:     $("meta-file-hash"),
  metaImageId:      $("meta-image-id"),
  metaHasEmbedding: $("meta-has-embedding"),

  // Center pane
  refreshLookupBtn: $("refresh-lookup-btn"),
  neighbourStrip:   $("neighbour-strip"),
  neighbourCount:   $("neighbour-count"),
  suggestionList:   $("suggestion-list"),
  suggestionCount:  $("suggestion-count"),
  newConceptName:   $("new-concept-name"),
  newConceptPrompt: $("new-concept-prompt"),
  newConceptSearch: $("new-concept-search"),
  similarTermsHint: $("similar-terms-hint"),
  createConceptBtn: $("create-concept-btn"),

  // Right pane
  confirmedList:    $("confirmed-list"),
  confirmedCount:   $("confirmed-count"),
};

// ── Theme ──────────────────────────────────────────────────────────
function initTheme() {
  const toggle = $("theme-toggle");
  if (!toggle) return;
  const saved = localStorage.getItem("atelier-theme");
  const dark = saved === "dark" || (!saved && matchMedia("(prefers-color-scheme:dark)").matches);
  if (dark) {
    document.body.dataset.theme = "dark";
    toggle.checked = true;
  }
  toggle.addEventListener("change", () => {
    if (toggle.checked) {
      document.body.dataset.theme = "dark";
      localStorage.setItem("atelier-theme", "dark");
    } else {
      delete document.body.dataset.theme;
      localStorage.setItem("atelier-theme", "light");
    }
  });
}

// ── Helpers ────────────────────────────────────────────────────────
function setStatus(message) {
  els.statusText.textContent = message;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function thumbUrl(fileHash) {
  return `${IMAGES_API}/${encodeURIComponent(fileHash)}/thumb`;
}

function originalUrl(fileHash) {
  return `${IMAGES_API}/${encodeURIComponent(fileHash)}/original`;
}

async function fetchJson(url, options = {}) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status}: ${detail}`);
  }
  return resp.json();
}

/**
 * Fetch one page of images via offset (skip) pagination and read the
 * total-count header.  Returns { items, hasMore, filteredCount }.
 *
 * The /api/images endpoint supports cursor pagination only when
 * group_variants=true.  The concept studio uses group_variants=false,
 * so we use offset pagination here; X-Filtered-Count is always emitted.
 */
async function fetchImagesPage({ searchTerm = "", offset = 0 } = {}) {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    skip: String(offset),
    sort_by: "last_added",
    group_variants: "false",
  });
  if (searchTerm) params.set("search", searchTerm);

  const resp = await fetch(`${IMAGES_API}/?${params.toString()}`);
  if (!resp.ok) {
    const detail = await resp.text().catch(() => resp.statusText);
    throw new Error(`${resp.status}: ${detail}`);
  }
  const items = await resp.json();
  const filteredRaw = resp.headers.get("X-Filtered-Count");
  const filteredCount = filteredRaw != null ? Number(filteredRaw) : null;
  // A short page (fewer than PAGE_SIZE) means we've reached the end.
  const hasMore = items.length >= PAGE_SIZE;
  return { items, hasMore, filteredCount };
}

// ── Image loading ──────────────────────────────────────────────────

async function loadRandomImage() {
  setStatus("Fetching a random image…");
  try {
    const page = await fetchImagesPage({ searchTerm: "", offset: 0 });
    if (!page.items.length) {
      setStatus("No images found in the library.");
      return;
    }
    state.searchTerm = "";
    state.imageQueue = page.items;
    state.queueIndex = 0;
    state.nextOffset = page.items.length;
    state.hasMore = page.hasMore;
    state.filteredCount = page.filteredCount;
    await loadExemplar(page.items[0]);
  } catch (err) {
    setStatus(`Error loading images: ${err.message}`);
  }
}

async function loadByHashOrId(input) {
  const trimmed = input.trim();
  if (!trimmed) return;

  setStatus(`Looking up image "${trimmed}"…`);
  try {
    // If it's purely numeric, treat as image_id
    if (/^\d+$/.test(trimmed)) {
      const image = await fetchJson(`${IMAGES_API}/${trimmed}`);
      state.searchTerm = "";
      state.imageQueue = [image];
      state.queueIndex = 0;
      state.nextOffset = 1;
      state.hasMore = false;
      state.filteredCount = 1;
      await loadExemplar(image);
    } else {
      // Treat as file_hash — find it via search
      const page = await fetchImagesPage({ searchTerm: trimmed, offset: 0 });
      const match = page.items.find(img => img.file_hash === trimmed) || page.items[0];
      if (match) {
        state.searchTerm = trimmed;
        state.imageQueue = page.items;
        state.queueIndex = page.items.indexOf(match);
        state.nextOffset = page.items.length;
        state.hasMore = page.hasMore;
        state.filteredCount = page.filteredCount;
        await loadExemplar(match);
      } else {
        setStatus(`No image found for "${trimmed}".`);
      }
    }
  } catch (err) {
    setStatus(`Error looking up image: ${err.message}`);
  }
}

async function loadExemplar(image) {
  state.currentImage = image;
  state.confirmedConcepts = [];
  state.suggestions = [];

  // Show image
  els.exemplarImage.src = originalUrl(image.file_hash);
  els.exemplarImage.style.display = "";
  els.exemplarPlaceholder.style.display = "none";

  // Show meta
  els.exemplarMeta.style.display = "";
  els.metaFileHash.textContent = image.file_hash || "—";
  els.metaImageId.textContent = String(image.id ?? "—");

  // Update queue position
  updateQueuePos();

  // Clear old suggestions
  renderNeighbours([]);
  renderSuggestions([]);
  renderConfirmed([]);

  // Prefetch the next page when nearing the end of the loaded queue,
  // so the next ▶ press is instant. Fire-and-forget (no await).
  const remaining = state.imageQueue.length - 1 - state.queueIndex;
  if (state.hasMore && remaining <= PAGE_SIZE / 2 && !state.isLoadingPage) {
    void maybeFetchNextPage({ silent: true });
  }

  setStatus(`Loaded image ${image.file_hash}. Fetching visual lookup…`);
  await fetchVisualLookup();

  // Load any existing confirmed concepts for this image
  await loadExistingObservations();
}

function updateQueuePos() {
  if (state.imageQueue.length > 0 && state.queueIndex >= 0) {
    // Show position within the total filtered catalog when known,
    // otherwise fall back to the loaded queue length.
    const total = (state.filteredCount != null && state.filteredCount > 0)
      ? state.filteredCount
      : state.imageQueue.length;
    els.queuePos.textContent = `${state.queueIndex + 1} / ${total}`;
  } else {
    els.queuePos.textContent = "— / —";
  }
}

function setNavDisabled(disabled) {
  if (els.prevImageBtn) els.prevImageBtn.disabled = disabled;
  if (els.nextImageBtn) els.nextImageBtn.disabled = disabled;
}

/**
 * Fetch the next page (if available) and append to the queue.
 * Returns true if new items were appended, false otherwise.
 *
 * When `silent` is true (prefetch path), status-bar messages are not
 * touched — the caller owns the status line for that interaction.
 */
async function maybeFetchNextPage({ silent = false } = {}) {
  if (!state.hasMore || state.isLoadingPage) return false;
  state.isLoadingPage = true;
  setNavDisabled(true);
  if (!silent) setStatus("Loading more images…");
  try {
    const page = await fetchImagesPage({
      searchTerm: state.searchTerm,
      offset: state.nextOffset,
    });
    // De-dup by file_hash to guard against off-by-one overlaps.
    const existingHashes = new Set(state.imageQueue.map(i => i.file_hash));
    const fresh = page.items.filter(i => !existingHashes.has(i.file_hash));
    state.imageQueue.push(...fresh);
    state.nextOffset += fresh.length;
    state.hasMore = page.hasMore && fresh.length > 0;
    if (page.filteredCount != null) state.filteredCount = page.filteredCount;
    updateQueuePos();
    return fresh.length > 0;
  } catch (err) {
    if (!silent) setStatus(`Error loading more images: ${err.message}`);
    return false;
  } finally {
    state.isLoadingPage = false;
    setNavDisabled(false);
  }
}

async function nextImage() {
  if (state.isLoadingPage) return;
  if (state.queueIndex < state.imageQueue.length - 1) {
    state.queueIndex++;
    await loadExemplar(state.imageQueue[state.queueIndex]);
    return;
  }
  // At the end of the loaded queue — try to fetch the next page.
  if (state.hasMore) {
    const gotMore = await maybeFetchNextPage();
    if (gotMore && state.queueIndex < state.imageQueue.length - 1) {
      state.queueIndex++;
      await loadExemplar(state.imageQueue[state.queueIndex]);
    } else {
      setStatus("Already at the end of the library.");
    }
  } else {
    setStatus("Already at the end of the library.");
  }
}

async function prevImage() {
  if (state.queueIndex > 0) {
    state.queueIndex--;
    await loadExemplar(state.imageQueue[state.queueIndex]);
  } else {
    setStatus("Already at the start of the queue.");
  }
}

// ── Visual lookup & suggestions ────────────────────────────────────

async function fetchVisualLookup() {
  if (!state.currentImage) return;

  try {
    const data = await fetchJson(`${VISUAL_API}/${encodeURIComponent(state.currentImage.file_hash)}?k=20`);
    state.visualLookup = data;

    // Update embedding status badge
    if (data.has_embedding) {
      els.metaHasEmbedding.textContent = "has embedding";
      els.metaHasEmbedding.className = "cs-badge is-yes";
    } else {
      els.metaHasEmbedding.textContent = "no embedding";
      els.metaHasEmbedding.className = "cs-badge is-no";
    }

    // Render neighbour thumbnails
    renderNeighbours(data.neighbours || []);

    // Build suggestions from aggregated concepts
    await buildSuggestions(data);

    setStatus(`Found ${data.neighbours.length} neighbours, ${state.suggestions.length} concept suggestions.`);
  } catch (err) {
    setStatus(`Visual lookup failed: ${err.message}`);
    els.metaHasEmbedding.textContent = "error";
    els.metaHasEmbedding.className = "cs-badge is-no";
  }
}

function renderNeighbours(neighbours) {
  els.neighbourCount.textContent = String(neighbours.length);

  if (!neighbours.length) {
    els.neighbourStrip.innerHTML = '<p class="cs-empty-hint">No similar images found above threshold</p>';
    return;
  }

  els.neighbourStrip.innerHTML = neighbours.map(nb => {
    const simPct = (nb.similarity * 100).toFixed(1);
    return `
      <div class="cs-neighbour-thumb" data-file-hash="${escapeHtml(nb.file_hash)}" data-tip="similarity ${simPct}%">
        <img src="${thumbUrl(nb.file_hash)}" alt="neighbour" loading="lazy"
             onerror="this.style.display='none'">
        <div class="cs-neighbour-sim">${simPct}%</div>
      </div>
    `;
  }).join("");

  // Click to load as new exemplar
  els.neighbourStrip.querySelectorAll(".cs-neighbour-thumb").forEach(el => {
    el.addEventListener("click", () => {
      const fh = el.dataset.fileHash;
      if (fh) loadByHashOrId(fh);
    });
  });
}

async function buildSuggestions(lookupData) {
  const suggestions = [];
  const seenConceptIds = new Set();
  const confirmedIds = new Set(state.confirmedConcepts.map(c => c.concept_id));

  // Source 1: aggregated concepts from neighbours
  for (const concept of (lookupData.aggregated_concepts || [])) {
    if (confirmedIds.has(concept.concept_id)) continue;
    if (seenConceptIds.has(concept.concept_id)) continue;
    seenConceptIds.add(concept.concept_id);
    suggestions.push({
      concept_id: concept.concept_id,
      name: concept.canonical_name,
      concept_type: concept.concept_type,
      score: null,
      match_count: concept.match_count,
      source: "neighbour",
    });
  }

  // Source 2: score each aggregated concept's prototype against this image
  const scoringPromises = suggestions.map(async (s) => {
    try {
      const result = await fetchJson(
        `${VISUAL_API}/${encodeURIComponent(state.currentImage.file_hash)}/score-concept`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ concept_id: s.concept_id }),
        },
      );
      if (result.has_prototype) {
        s.score = result.cosine_similarity;
      }
    } catch {
      // Scoring is best-effort
    }
    return s;
  });

  await Promise.all(scoringPromises);

  // Sort: concepts with scores first (by score desc), then by match_count
  state.suggestions = suggestions.sort((a, b) => {
    if (a.score !== null && b.score !== null) return b.score - a.score;
    if (a.score !== null) return -1;
    if (b.score !== null) return 1;
    return (b.match_count || 0) - (a.match_count || 0);
  });

  renderSuggestions(state.suggestions);
}

function renderSuggestions(suggestions) {
  els.suggestionCount.textContent = String(suggestions.length);

  if (!suggestions.length) {
    els.suggestionList.innerHTML = '<p class="cs-empty-hint">No suggestions — try creating a concept below</p>';
    return;
  }

  els.suggestionList.innerHTML = suggestions.map(s => {
    const scoreText = s.score !== null
      ? `${(s.score * 100).toFixed(1)}% match`
      : `${s.match_count || 0} neighbour${(s.match_count || 0) === 1 ? "" : "s"}`;
    const typeText = s.concept_type ? ` · ${escapeHtml(s.concept_type)}` : "";
    return `
      <div class="cs-suggestion-item">
        <div class="cs-suggestion-info">
          <div class="cs-suggestion-name">${escapeHtml(s.name)}</div>
          <div class="cs-suggestion-detail">${scoreText}${typeText}</div>
        </div>
        <div class="cs-suggestion-actions">
          ${s.score !== null ? `<span class="cs-sim-score">${(s.score * 100).toFixed(0)}%</span>` : ""}
          <button class="btn solid btn-sm cs-confirm-btn" data-concept-id="${s.concept_id}" data-concept-name="${escapeHtml(s.name)}">Confirm</button>
        </div>
      </div>
    `;
  }).join("");

  // Wire confirm buttons
  els.suggestionList.querySelectorAll(".cs-confirm-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const conceptId = parseInt(btn.dataset.conceptId, 10);
      const conceptName = btn.dataset.conceptName;
      confirmConcept(conceptId, conceptName);
    });
  });
}

// ── Existing observations for current image ────────────────────────

async function loadExistingObservations() {
  if (!state.currentImage) return;
  try {
    const data = await fetchJson(
      `${VISUAL_API}/${encodeURIComponent(state.currentImage.file_hash)}`,
    );
    // If the image already has concepts via aggregated_concepts, pre-populate
    // We use the visual lookup's own aggregated_concepts for this image
    // (which includes ALL observation sources, not just neighbours)
    const existing = data.aggregated_concepts || [];
    if (existing.length > 0) {
      state.confirmedConcepts = existing.map(c => ({
        concept_id: c.concept_id,
        name: c.canonical_name,
        source_type: "existing",
        match_count: c.match_count,
      }));
      renderConfirmed(state.confirmedConcepts);
    }
  } catch {
    // Best-effort — ignore
  }
}

// ── Concept confirmation (hydrate) ─────────────────────────────────

async function confirmConcept(conceptId, conceptName) {
  if (!state.currentImage) {
    setStatus("Load an image first.");
    return;
  }

  // Check if already confirmed
  if (state.confirmedConcepts.some(c => c.concept_id === conceptId)) {
    setStatus(`"${conceptName}" is already associated with this image.`);
    return;
  }

  setStatus(`Associating "${conceptName}" with image…`);
  try {
    const result = await fetchJson(
      `${VISUAL_API}/${encodeURIComponent(state.currentImage.file_hash)}/hydrate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id: conceptId,
          is_curated: true,
        }),
      },
    );

    state.confirmedConcepts.push({
      concept_id: conceptId,
      name: conceptName,
      source_type: "VISUAL_CLIP",
      observation_id: result.observation_id,
    });

    renderConfirmed(state.confirmedConcepts);

    // Remove from suggestions
    state.suggestions = state.suggestions.filter(s => s.concept_id !== conceptId);
    renderSuggestions(state.suggestions);

    setStatus(`✓ "${conceptName}" confirmed for this image.`);
  } catch (err) {
    setStatus(`Error confirming concept: ${err.message}`);
  }
}

function renderConfirmed(concepts) {
  els.confirmedCount.textContent = String(concepts.length);

  if (!concepts.length) {
    els.confirmedList.innerHTML = '<p class="cs-empty-hint">Associate concepts from the center panel</p>';
    return;
  }

  els.confirmedList.innerHTML = concepts.map(c => {
    const sourceLabel = c.source_type === "VISUAL_CLIP" ? "curated" : "existing";
    return `
      <div class="cs-confirmed-item" data-concept-id="${c.concept_id}">
        <div class="cs-confirmed-top">
          <span class="cs-confirmed-name">${escapeHtml(c.name)}</span>
          <span class="cs-confirmed-source">${sourceLabel}</span>
        </div>
        <div class="cs-confirmed-controls">
          <label>Strength
            <input type="range" class="cs-strength-slider" min="0" max="100" value="${(c.strength || 0.8) * 100}">
          </label>
          <label>Role
            <select class="cs-role-select">
              <option value="train">Train</option>
              <option value="eval">Eval</option>
              <option value="exclude">Exclude</option>
            </select>
          </label>
          <button class="cs-remove-btn" data-concept-id="${c.concept_id}" data-tip="Remove association">✕</button>
        </div>
      </div>
    `;
  }).join("");

  // Wire remove buttons
  els.confirmedList.querySelectorAll(".cs-remove-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const conceptId = parseInt(btn.dataset.conceptId, 10);
      removeConfirmed(conceptId);
    });
  });
}

function removeConfirmed(conceptId) {
  const concept = state.confirmedConcepts.find(c => c.concept_id === conceptId);
  state.confirmedConcepts = state.confirmedConcepts.filter(c => c.concept_id !== conceptId);
  renderConfirmed(state.confirmedConcepts);
  if (concept) {
    setStatus(`Removed "${concept.name}" from this image (DB record remains).`);
  }
}

// ── Create new concept ─────────────────────────────────────────────

async function createConcept() {
  const name = els.newConceptName.value.trim();
  const prompt = els.newConceptPrompt.value.trim();

  if (!name) {
    setStatus("Concept name is required.");
    return;
  }

  setStatus(`Creating concept "${name}"…`);
  els.createConceptBtn.disabled = true;

  try {
    // Step 1: Create the concept
    const createResult = await fetchJson(`${TAXONOMY_API}/concepts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ canonical_name: name }),
    });

    const conceptId = createResult.concept.id;
    const isNew = createResult.message.includes("created");

    // Step 2: Build prototype from text prompt (if provided)
    if (prompt) {
      setStatus(`Building prototype from text for "${name}"…`);
      try {
        await fetchJson(`${TAXONOMY_API}/concepts/${conceptId}/build-prototype-from-text`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompts: prompt.split(",").map(p => p.trim()).filter(Boolean) }),
        });
        setStatus(`✓ "${name}" created with text-seeded prototype.`);
      } catch (err) {
        setStatus(`Concept created but prototype build failed: ${err.message}`);
      }
    } else if (isNew) {
      setStatus(`✓ "${name}" created (no prototype — add a prompt to seed one).`);
    } else {
      setStatus(`"${name}" already exists (concept #${conceptId}).`);
    }

    // Clear form
    els.newConceptName.value = "";
    els.newConceptPrompt.value = "";
    els.newConceptSearch.value = "";
    els.similarTermsHint.innerHTML = "";

    // Auto-confirm the new concept for the current image
    await confirmConcept(conceptId, name);

    // Refresh suggestions
    if (state.currentImage) {
      await fetchVisualLookup();
    }
  } catch (err) {
    setStatus(`Error creating concept: ${err.message}`);
  } finally {
    els.createConceptBtn.disabled = false;
  }
}

// ── Similar terms search (duplicate prevention) ────────────────────

async function searchSimilarTerms(query) {
  if (!query || query.length < 2) {
    els.similarTermsHint.innerHTML = "";
    return;
  }

  try {
    const data = await fetchJson(
      `${TAXONOMY_API}/concept-search/similar-terms?q=${encodeURIComponent(query)}&unlinked_only=false&limit=5`,
    );
    const terms = data.results || [];
    if (terms.length > 0) {
      els.similarTermsHint.innerHTML = terms.map(t => {
        const name = t.normalized_name || String(t);
        const conceptId = t.concept_id;
        const linked = t.linked_concept ? ` → ${escapeHtml(t.linked_concept)}` : "";
        const chip = conceptId
          ? `<span class="cs-term-chip cs-term-existing" data-concept-id="${conceptId}" data-concept-name="${escapeHtml(t.linked_concept || name)}">${escapeHtml(name)}${linked}</span>`
          : `<span class="cs-term-chip">${escapeHtml(name)}</span>`;
        return chip;
      }).join("");
      els.similarTermsHint.querySelectorAll(".cs-term-chip[data-concept-id]").forEach(chip => {
        chip.addEventListener("click", () => {
          els.newConceptName.value = chip.dataset.conceptName;
          els.similarTermsHint.innerHTML = "";
        });
      });
    } else {
      els.similarTermsHint.innerHTML = `<span style="opacity:0.6">No existing matches — safe to create.</span>`;
    }
  } catch {
    els.similarTermsHint.innerHTML = "";
  }
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// ── Event wiring ───────────────────────────────────────────────────

function initEvents() {
  els.loadHashBtn.addEventListener("click", () => {
    loadByHashOrId(els.fileHashInput.value);
  });

  els.fileHashInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      loadByHashOrId(els.fileHashInput.value);
    }
  });

  els.randomImageBtn.addEventListener("click", loadRandomImage);

  els.nextImageBtn.addEventListener("click", nextImage);
  els.prevImageBtn.addEventListener("click", prevImage);

  els.refreshLookupBtn.addEventListener("click", () => {
    if (state.currentImage) fetchVisualLookup();
  });

  els.createConceptBtn.addEventListener("click", createConcept);

  const debouncedSearch = debounce(searchSimilarTerms, 300);
  els.newConceptSearch.addEventListener("input", (e) => {
    debouncedSearch(e.target.value);
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
    if (e.key === "ArrowRight") nextImage();
    if (e.key === "ArrowLeft") prevImage();
    if (e.key === "r" || e.key === "R") loadRandomImage();
  });
}

// ── Init ───────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initEvents();
  // Auto-load a random image on startup
  loadRandomImage();
});
