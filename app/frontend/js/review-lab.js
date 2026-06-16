const $ = (sel) => document.querySelector(sel);

const themeToggle = $("#theme-toggle");
const loadConceptBtn = $("#load-concept-btn");
const refreshScoresBtn = $("#refresh-scores-btn");
const statusTitle = $("#status-title");
const statusMessage = $("#status-message");

const scoreLimit = $("#score-limit");
const alphaIdentity = $("#alpha-identity");
const alphaAttribute = $("#alpha-attribute");
const alphaContext = $("#alpha-context");
const alphaStyle = $("#alpha-style");
const scoreEpsilon = $("#score-epsilon");

const summaryChips = $("#summary-chips");
const scoresGrid = $("#scores-grid");
const reviewEmpty = $("#review-empty");

const fsOverlay = $("#review-fullscreen");
const fsImage = $("#fs-review-image");
const fsImageTitle = $("#fs-review-image-title");
const fsImageSubtitle = $("#fs-review-image-subtitle");
const fsConceptName = $("#fs-concept-name");
const fsConceptAliases = $("#fs-concept-aliases");
const fsConceptHierarchy = $("#fs-concept-hierarchy");
const fsConceptDescription = $("#fs-concept-description");
const fsCloseBtn = $("#fs-close-btn");
const fsSaveMsg = $("#fs-save-msg");

const assessmentForm = $("#fs-assessment-form");
const qPredominance = $("#q-predominance");
const qQuality = $("#q-quality");
const qAccuracy = $("#q-accuracy");
const qAttributeSupport = $("#q-attribute-support");
const qContextIncongruent = $("#q-context-incongruent");
const qContextKinds = $("#q-context-kinds");
const qContextAnachronistic = $("#q-context-anachronistic");
const qContextAnatopismic = $("#q-context-anatopismic");
const qContextNonsensical = $("#q-context-nonsensical");
const qContextAnomalousForm = $("#q-context-anomalous-form");
const qDeviationPresent = $("#q-deviation-present");
const qDeviationFields = $("#q-deviation-fields");
const qDeviationBodyVariant = $("#q-deviation-body-variant");
const qDeviationExaggerated = $("#q-deviation-exaggerated");
const qDeviationExtraFeature = $("#q-deviation-extra-feature");
const qDeviationFusion = $("#q-deviation-fusion");
const qDeviationKind = $("#q-deviation-kind");
const qDeviationDegree = $("#q-deviation-degree");
const qAnomalyPresent = $("#q-anomaly-present");
const qAnomalyFields = $("#q-anomaly-fields");
const qAnomalyKind = $("#q-anomaly-kind");
const qAnomalyDegree = $("#q-anomaly-degree");
const qImageStyle = $("#q-image-style");
const qImageStyleConfidence = $("#q-image-style-confidence");
const qNotes = $("#q-notes");
const qAttributeList = $("#q-attribute-list");

/** Tracks attribute checkbox state: { conceptId: "present" | "absent" | "not_visible" } */
let attributeChecks = {};

const appState = {
  conceptId: null,
  conceptName: "",
  conceptProfile: null,
  scoredImages: [],
  selectedImageId: null,
  styleOptions: [],
  sessionId: null,
  assessmentsByImage: new Map(),
};

const conceptLookup = ConceptLookup.create({
  container: "#concept-lookup",
  idInputId: "review-concept-id",
  nameInputId: "review-concept-name",
  placeholder: "Find concept name...",
  apiBase: "/api/taxonomy",
});

conceptLookup.onSelect(({ id, name }) => {
  appState.conceptId = id;
  appState.conceptName = name;
});

function applyTheme(dark) {
  document.body.setAttribute("data-theme", dark ? "dark" : "light");
  localStorage.setItem("lab-theme", dark ? "dark" : "light");
}

themeToggle.addEventListener("change", () => applyTheme(themeToggle.checked));
if (localStorage.getItem("lab-theme") === "dark") {
  themeToggle.checked = true;
  applyTheme(true);
}

function setStatus(title, message, mode = "info") {
  const panel = $("#status-panel");
  panel.className = `step-status ${mode}`;
  statusTitle.textContent = title;
  statusMessage.textContent = message;
}

function esc(value) {
  const span = document.createElement("span");
  span.textContent = value ?? "";
  return span.innerHTML;
}

function buildThumbUrl(item) {
  if (item?.image_file_hash) {
    return `/api/images/${item.image_file_hash}/thumb`;
  }
  return item?.image_thumbnail_url || "/frontend/logo.svg";
}

function buildFullscreenUrl(item) {
  if (item?.image_file_hash) {
    return `/api/images/${item.image_file_hash}/original`;
  }
  return item?.image_thumbnail_url || "/frontend/logo.svg";
}

function attachImageFallback(imgEl) {
  if (!imgEl) return;
  imgEl.addEventListener("error", () => {
    if (imgEl.dataset.fallbackApplied === "1") return;
    imgEl.dataset.fallbackApplied = "1";
    imgEl.src = "/frontend/logo.svg";
  }, { once: true });
}

function parseIntOrNull(v) {
  if (v === "" || v == null) return null;
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? null : n;
}

function parseFloatOrNull(v) {
  if (v === "" || v == null) return null;
  const n = parseFloat(v);
  return Number.isNaN(n) ? null : n;
}

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

async function apiPut(url, body) {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || res.statusText);
  }
  return res.json();
}

function readScoringParams() {
  return {
    limit: parseInt(scoreLimit.value, 10) || 50,
    alpha_identity: parseFloat(alphaIdentity.value) || 0.55,
    alpha_attribute: parseFloat(alphaAttribute.value) || 0.25,
    alpha_context: parseFloat(alphaContext.value) || 0.15,
    alpha_style: parseFloat(alphaStyle.value) || 0.05,
    epsilon: parseFloat(scoreEpsilon.value) || 0.05,
  };
}

function getConceptId() {
  const lookupId = conceptLookup.getId();
  if (!Number.isNaN(lookupId) && lookupId > 0) return lookupId;
  return appState.conceptId;
}

async function resolveConceptId() {
  const currentId = getConceptId();
  if (currentId) return currentId;
  const name = conceptLookup.getName().trim();
  if (!name) return null;
  const lookup = await apiGet(`/api/taxonomy/concept-lookup?q=${encodeURIComponent(name)}&limit=5`);
  const results = lookup.results || [];
  if (!results.length) return null;
  const exact = results.find((row) => (row.canonical_name || "").toLowerCase() === name.toLowerCase());
  const chosen = exact || results[0];
  conceptLookup.setId(chosen.id);
  appState.conceptId = chosen.id;
  appState.conceptName = chosen.canonical_name || name;
  return chosen.id;
}

async function ensureSession(conceptId) {
  const openSessions = await apiGet(`/api/concepts/${conceptId}/review-sessions?status=open`);
  if (openSessions.length > 0) {
    appState.sessionId = openSessions[0].id;
    return;
  }
  const created = await apiPost(`/api/concepts/${conceptId}/review-sessions`, { notes: null });
  appState.sessionId = created.id;
}

async function loadAssessments() {
  appState.assessmentsByImage = new Map();
  if (!appState.sessionId) return;
  const rows = await apiGet(`/api/concepts/review-sessions/${appState.sessionId}/assessments`);
  for (const row of rows) {
    appState.assessmentsByImage.set(row.image_id, row);
  }
}

function updateSummary(scoring, weighting) {
  summaryChips.innerHTML = [
    `<span class="chip">Concept: ${esc(scoring.concept_name || appState.conceptName || "#" + scoring.concept_id)}</span>`,
    `<span class="chip">Scored images: ${scoring.total_images}</span>`,
    `<span class="chip">Session: ${appState.sessionId || "n/a"}</span>`,
    `<span class="chip">Assessed: ${appState.assessmentsByImage.size}</span>`,
    `<span class="chip">Observations: ${weighting.total_observations}</span>`,
  ].join("");
}

function renderScores() {
  scoresGrid.innerHTML = "";
  if (!appState.scoredImages.length) {
    scoresGrid.innerHTML = `<div class="empty-note">No scored images available for this concept yet.</div>`;
    return;
  }

  for (const item of appState.scoredImages) {
    const assessment = appState.assessmentsByImage.get(item.image_id);
    const classes = ["score-card"];
    if (item.image_id === appState.selectedImageId) classes.push("is-active");
    if (assessment) classes.push("assessed");

    const card = document.createElement("button");
    card.type = "button";
    card.className = classes.join(" ");
    card.innerHTML = `
      <img class="score-thumb" src="${esc(buildThumbUrl(item))}" alt="Image ${item.image_id}">
      <div class="score-body">
        <h3 class="score-title">${esc(item.image_file_name || `image #${item.image_id}`)}</h3>
        <div class="score-line"><span>Final</span><strong>${item.final_score.toFixed(4)}</strong></div>
        <div class="score-line"><span>Identity</span><span>${item.identity_score.toFixed(4)}</span></div>
        <div class="score-line"><span>Attribute</span><span>${item.attribute_score.toFixed(4)}</span></div>
        <div class="score-line"><span>Anomaly penalty</span><span>${item.anomaly_penalty.toFixed(4)}</span></div>
      </div>
    `;
    attachImageFallback(card.querySelector(".score-thumb"));
    card.addEventListener("click", () => openFullscreenReview(item));
    scoresGrid.appendChild(card);
  }
}

function setConditionalGroup(container, visible) {
  container.classList.toggle("hidden", !visible);
}

function setupSectionToggles() {
  const sections = document.querySelectorAll("[data-q-section]");
  for (const section of sections) {
    const toggle = section.querySelector(".q-section-toggle");
    if (!toggle || toggle.dataset.bound === "1") continue;
    toggle.dataset.bound = "1";
    toggle.addEventListener("click", () => {
      const isOpen = section.classList.toggle("open");
      const chevron = section.querySelector(".q-section-chevron");
      if (chevron) chevron.textContent = isOpen ? "▾" : "▸";
    });
  }
}

function renderAttributeChecklist(profile) {
  attributeChecks = {};
  qAttributeList.innerHTML = "";

  if (!profile?.attributes?.length) {
    qAttributeList.innerHTML = '<p class="q-hint">No visual attributes defined.</p>';
    return;
  }

  const visualAttrs = profile.attributes.filter((a) => a.attribute_kind === "visual");
  if (!visualAttrs.length) {
    qAttributeList.innerHTML = '<p class="q-hint">No visual attributes defined.</p>';
    return;
  }

  for (const attr of visualAttrs) {
    const row = document.createElement("div");
    row.className = "attribute-row";
    row.dataset.attrId = String(attr.attribute_concept_id);

    const name = document.createElement("span");
    name.className = "attr-name";
    name.textContent = attr.attribute_concept_name || `#${attr.attribute_concept_id}`;

    const states = document.createElement("div");
    states.className = "attr-states";

    for (const [state, label] of Object.entries({ present: "✓", absent: "✕", not_visible: "—" })) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "attr-state-btn";
      btn.dataset.state = state;
      btn.textContent = label;
      btn.title = state.replace("_", " ");
      btn.addEventListener("click", () => {
        const id = row.dataset.attrId;
        if (attributeChecks[id] === state) {
          delete attributeChecks[id];
          btn.classList.remove("active");
        } else {
          attributeChecks[id] = state;
          states.querySelectorAll(".attr-state-btn").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
        }
      });
      states.appendChild(btn);
    }

    row.appendChild(name);
    row.appendChild(states);
    qAttributeList.appendChild(row);
  }
}

function applyAttributeChecks(savedChecks) {
  attributeChecks = {};
  qAttributeList.querySelectorAll(".attribute-row").forEach((row) => {
    const id = row.dataset.attrId;
    const state = savedChecks?.[id];
    row.querySelectorAll(".attr-state-btn").forEach((btn) => btn.classList.remove("active"));
    if (state) {
      attributeChecks[id] = state;
      const btn = row.querySelector(`.attr-state-btn[data-state="${state}"]`);
      if (btn) btn.classList.add("active");
    }
  });
}

function renderConceptSummary() {
  const profile = appState.conceptProfile;
  if (!profile) return;

  fsConceptName.textContent = profile.canonical_name || appState.conceptName || "Concept";

  const aliases = (profile.aliases || []).slice(0, 20);
  fsConceptAliases.innerHTML = aliases.length
    ? aliases.map((a) => `<span class="chip-mini">${esc(a.alias)}</span>`).join("")
    : '<span class="chip-mini">No aliases</span>';

  const hierarchyLines = [];
  if (profile.parent_concept?.canonical_name) hierarchyLines.push(`Parent: ${profile.parent_concept.canonical_name}`);
  hierarchyLines.push(`Current: ${profile.canonical_name}`);
  if ((profile.children || []).length) {
    const childNames = profile.children.slice(0, 8).map((c) => c.canonical_name).join(", ");
    hierarchyLines.push(`Children: ${childNames}`);
  }
  fsConceptHierarchy.innerHTML = hierarchyLines.map((line) => `<div>${esc(line)}</div>`).join("");

  fsConceptDescription.textContent = profile.description || "No description.";

  renderAttributeChecklist(profile);
}

function fillStyleOptions(selectedId) {
  qImageStyle.innerHTML = `<option value="">(unset)</option>`;
  for (const row of appState.styleOptions) {
    const option = document.createElement("option");
    option.value = String(row.id);
    option.textContent = row.canonical_name;
    if (selectedId && row.id === selectedId) option.selected = true;
    qImageStyle.appendChild(option);
  }
}

function resetQuestionnaireFrom(item) {
  const assessment = appState.assessmentsByImage.get(item.image_id);

  qPredominance.value = assessment?.predominance_rating ?? "";
  qQuality.value = assessment?.quality_rating ?? "";
  qAccuracy.value = assessment?.accuracy_rating ?? "";
  qAttributeSupport.value = assessment?.attribute_support_rating ?? "";

  qContextIncongruent.checked = !!assessment?.context_incongruent;
  qContextAnachronistic.checked = !!assessment?.context_anachronistic;
  qContextAnatopismic.checked = !!assessment?.context_anatopismic;
  qContextNonsensical.checked = !!assessment?.context_nonsensical;
  qContextAnomalousForm.checked = !!assessment?.context_anomalous_form;
  setConditionalGroup(qContextKinds, qContextIncongruent.checked);

  qDeviationPresent.checked = !!assessment?.deviation_present;
  qDeviationBodyVariant.checked = !!assessment?.deviation_body_variant;
  qDeviationExaggerated.checked = !!assessment?.deviation_exaggerated;
  qDeviationExtraFeature.checked = !!assessment?.deviation_extra_feature;
  qDeviationFusion.checked = !!assessment?.deviation_fusion;
  qDeviationKind.value = assessment?.deviation_kind || "";
  qDeviationDegree.value = assessment?.deviation_degree ?? "";
  setConditionalGroup(qDeviationFields, qDeviationPresent.checked);

  qAnomalyPresent.checked = !!assessment?.anomaly_present;
  qAnomalyKind.value = assessment?.anomaly_kind || "";
  qAnomalyDegree.value = assessment?.anomaly_degree ?? "";
  setConditionalGroup(qAnomalyFields, qAnomalyPresent.checked);

  applyAttributeChecks(assessment?.attribute_checks);

  const styleId = assessment?.image_style_concept_id || item.image_style_concept_id || null;
  fillStyleOptions(styleId);
  qImageStyleConfidence.value = assessment?.image_style_confidence ?? item.image_style_confidence ?? "";
  qNotes.value = assessment?.notes || "";
}

function openFullscreenReview(item) {
  appState.selectedImageId = item.image_id;
  renderScores();

  reviewEmpty.style.display = "none";
  fsImage.dataset.fallbackApplied = "0";
  fsImage.src = buildFullscreenUrl(item);
  attachImageFallback(fsImage);
  fsImageTitle.textContent = item.image_file_name || `Image #${item.image_id}`;
  fsImageSubtitle.textContent = `ID ${item.image_id} | final ${item.final_score.toFixed(4)} | identity ${item.identity_score.toFixed(4)} | attributes ${item.attribute_score.toFixed(4)}`;

  renderConceptSummary();
  resetQuestionnaireFrom(item);
  setupSectionToggles();
  fsSaveMsg.textContent = "";

  fsOverlay.classList.remove("hidden");
  fsOverlay.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
}

function closeFullscreenReview() {
  fsOverlay.classList.add("hidden");
  fsOverlay.setAttribute("aria-hidden", "true");
  document.body.style.overflow = "";
}

async function loadStyleOptions() {
  if (appState.styleOptions.length) return;
  const rows = await apiGet("/api/taxonomy/concepts?status=all&limit=1000&offset=0");
  const styleLike = rows.filter((row) =>
    /(style|anime|cartoon|comic|realistic|photo|painter|watercolou?r|sketch|3d|render)/i.test(row.canonical_name || "")
  );
  styleLike.sort((a, b) => (a.canonical_name || "").localeCompare(b.canonical_name || ""));
  appState.styleOptions = styleLike.slice(0, 300);
}

async function loadConceptData() {
  const conceptId = await resolveConceptId();
  if (!conceptId) {
    setStatus("Concept required", "Pick a concept first.", "error");
    return;
  }

  appState.conceptId = conceptId;
  appState.conceptName = conceptLookup.getName() || appState.conceptName;

  const params = readScoringParams();
  const query = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

  setStatus("Loading", `Computing scores for concept #${conceptId}`, "info");
  try {
    const [scoring, weighting, profile] = await Promise.all([
      apiGet(`/api/concepts/${conceptId}/scored-images?${query}`),
      apiGet(`/api/concepts/${conceptId}/weighting-summary`),
      apiGet(`/api/taxonomy/concepts/${conceptId}/profile`),
    ]);

    appState.scoredImages = scoring.results || [];
    appState.conceptProfile = profile;

    await ensureSession(conceptId);
    await Promise.all([loadAssessments(), loadStyleOptions()]);

    updateSummary(scoring, weighting);
    renderScores();
    setStatus("Loaded", `${appState.scoredImages.length} scored images, session #${appState.sessionId}`, "success");
  } catch (error) {
    setStatus("Load failed", error.message, "error");
  }
}

qContextIncongruent.addEventListener("change", () => {
  setConditionalGroup(qContextKinds, qContextIncongruent.checked);
});

qDeviationPresent.addEventListener("change", () => {
  setConditionalGroup(qDeviationFields, qDeviationPresent.checked);
});

qAnomalyPresent.addEventListener("change", () => {
  setConditionalGroup(qAnomalyFields, qAnomalyPresent.checked);
});

fsCloseBtn.addEventListener("click", closeFullscreenReview);
fsOverlay.querySelector(".review-fullscreen-backdrop")?.addEventListener("click", closeFullscreenReview);

assessmentForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const imageId = appState.selectedImageId;
  const sessionId = appState.sessionId;
  if (!imageId || !sessionId || !appState.conceptId) {
    fsSaveMsg.textContent = "Missing concept/session/image context";
    return;
  }

  const payload = {
    image_id: imageId,
    predominance_rating: parseIntOrNull(qPredominance.value),
    quality_rating: parseIntOrNull(qQuality.value),
    accuracy_rating: parseIntOrNull(qAccuracy.value),
    attribute_support_rating: parseIntOrNull(qAttributeSupport.value),
    context_incongruent: qContextIncongruent.checked,
    context_anachronistic: qContextAnachronistic.checked,
    context_anatopismic: qContextAnatopismic.checked,
    context_nonsensical: qContextNonsensical.checked,
    context_anomalous_form: qContextAnomalousForm.checked,
    deviation_present: qDeviationPresent.checked,
    deviation_body_variant: qDeviationBodyVariant.checked,
    deviation_exaggerated: qDeviationExaggerated.checked,
    deviation_extra_feature: qDeviationExtraFeature.checked,
    deviation_fusion: qDeviationFusion.checked,
    deviation_kind: qDeviationKind.value || null,
    deviation_degree: parseIntOrNull(qDeviationDegree.value),
    anomaly_present: qAnomalyPresent.checked,
    anomaly_kind: qAnomalyKind.value || null,
    anomaly_degree: parseIntOrNull(qAnomalyDegree.value),
    attribute_checks: Object.keys(attributeChecks).length ? { ...attributeChecks } : null,
    image_style_concept_id: parseIntOrNull(qImageStyle.value),
    image_style_source: parseIntOrNull(qImageStyle.value) ? "review" : null,
    image_style_confidence: parseFloatOrNull(qImageStyleConfidence.value),
    notes: qNotes.value || null,
  };

  try {
    fsSaveMsg.textContent = "Saving...";
    const saved = await apiPut(`/api/concepts/review-sessions/${sessionId}/assessments/${imageId}`, payload);
    appState.assessmentsByImage.set(imageId, saved);
    renderScores();
    fsSaveMsg.textContent = "Saved";
    setStatus("Assessment saved", `Image #${imageId} in session #${sessionId}`, "success");
  } catch (error) {
    fsSaveMsg.textContent = error.message;
    setStatus("Assessment failed", error.message, "error");
  }
});

loadConceptBtn.addEventListener("click", loadConceptData);
refreshScoresBtn.addEventListener("click", loadConceptData);
