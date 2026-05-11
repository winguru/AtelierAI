// ── Memory ───────────────────────────────────────────────────────────────────
// 📄 docs: app/docs/memories/model-reference.md
// ──────────────────────────────────────────────────────────────────────────────
/**
 * Models Browser — hierarchy view for checkpoints and LoRAs.
 *
 * Adapted from tree.js patterns. Shows 4-level hierarchy:
 *   base_model → model → version → precision
 * across two type panes (checkpoints, loras).
 *
 * Scope works like the tag tree:
 * - all: every model in the catalog
 * - gallery: models used by gallery images
 * - selected: models used by the selected image
 * - none: hide everything
 */

(() => {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────────

  const API_BASE = "/api/models/tree";
  const LEVEL_NAMES = ["Base Model", "Model", "Version", "Precision"];

  const PANE_CONFIG = [
    { type: "checkpoint", label: "Checkpoints", icon: "🏛" },
    { type: "lora", label: "LoRAs", icon: "🔧" },
  ];

  // ── State ──────────────────────────────────────────────────────────────────

  const state = {
    /** Current scope: all | gallery | selected | none */
    scope: "all",
    /** Sort mode: name | count */
    sortMode: "name",
    /** Search query */
    searchQuery: "",
    /** Hierarchy tree from backend */
    hierarchy: { checkpoint: [], lora: [] },
    /** Usage counts by scope → type → path_key → count */
    usageByScope: {
      all: { checkpoint: {}, lora: {} },
      gallery: { checkpoint: {}, lora: {} },
      selected: { checkpoint: {}, lora: {} },
    },
    /** Selected image data from parent (via postMessage) */
    selectedImageKey: null,
    selectedImageModels: { checkpoint: [], lora: [] },
    /** Gallery keys from parent */
    galleryFilter: null,
    /** Pane scroll positions */
    scrollTopByType: { checkpoint: 0, lora: 0 },
    /** Loading state */
    loading: false,
    /** Render token for cancelling stale renders */
    renderToken: 0,
  };

  // ── DOM refs ───────────────────────────────────────────────────────────────

  let scopeControlsEl = null;
  let typeBoardEl = null;

  // ── Init ───────────────────────────────────────────────────────────────────

  function init() {
    scopeControlsEl = document.getElementById("scope-controls");
    typeBoardEl = document.getElementById("type-board");

    if (!scopeControlsEl || !typeBoardEl) {
      console.error("[models] Required DOM elements not found");
      return;
    }

    // Listen for postMessage from parent (gallery/selected image data)
    window.addEventListener("message", handleMessage);

    // Initial load
    loadState().then(() => {
      render();
    });
  }

  // ── API ────────────────────────────────────────────────────────────────────

  async function apiPost(url, body) {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      throw new Error(`API error: ${resp.status}`);
    }
    return resp.json();
  }

  async function loadState() {
    state.loading = true;

    try {
      const body = {};
      // Gallery scope: send filter + search from parent page
      if (state.galleryFilter) {
        body.filter = state.galleryFilter.filter;
        if (state.galleryFilter.search) {
          body.search = state.galleryFilter.search;
        }
      }
      // Selected scope: send single image key
      if (state.selectedImageKey) {
        body.selected_keys = [state.selectedImageKey];
      }

      const payload = await apiPost(`${API_BASE}/state`, body);

      if (payload?.hierarchy) {
        state.hierarchy = payload.hierarchy;
      }
      if (payload?.usage_by_scope) {
        state.usageByScope = payload.usage_by_scope;
      }
    } catch (err) {
      console.error("[models] Failed to load state:", err);
    } finally {
      state.loading = false;
    }
  }

  // ── PostMessage handling ───────────────────────────────────────────────────

  function handleMessage(event) {
    const data = event.data;
    if (!data || typeof data !== "object") return;

    // Gallery sends selected image model data
    if (data.type === "atelier:selected-image-models") {
      const payload = data.payload || {};
      state.selectedImageKey = payload.imageKey || null;
      state.selectedImageModels = {
        checkpoint: payload.checkpoint || [],
        lora: payload.lora || [],
      };

      // Reload state to get updated selected scope counts
      loadState().then(() => render());
      return;
    }

    // Gallery sends filter body for server-side resolution
    if (data.type === "atelier:gallery-filter") {
      state.galleryFilter = data.payload || null;
      loadState().then(() => render());
      return;
    }
  }

  // ── Counting helpers ───────────────────────────────────────────────────────

  /**
   * Get the usage count for a hierarchy node at the current scope.
   * Traverses descendants and sums matching usage keys.
   */
  function getUsageCount(node, type, scope, parentPath) {
    const path = parentPath ? `${parentPath}.${node.key}` : node.key;
    const usageMap = state.usageByScope[scope]?.[type] || {};

    // Direct match
    const direct = usageMap[path];
    if (direct != null) {
      return direct;
    }

    // Sum children
    let total = 0;
    if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        total += getUsageCount(child, type, scope, path);
      }
    }

    return total;
  }

  /**
   * Count direct children of a node.
   */
  function countDescendants(node) {
    return node.children ? node.children.length : 0;
  }

  /**
   * Check if a node or any descendant matches the search query.
   */
  function matchesSearch(node, query) {
    if (!query) return true;
    const q = query.toLowerCase();
    if (node.label.toLowerCase().includes(q)) return true;
    if (node.key.toLowerCase().includes(q)) return true;
    if (node.children) {
      return node.children.some((child) => matchesSearch(child, query));
    }
    return false;
  }

  /**
   * Check if a node has any usage at the current scope.
   */
  function hasScopeUsage(node, type, scope, parentPath) {
    if (scope === "all" || scope === "none") return true;
    return getUsageCount(node, type, scope, parentPath) > 0;
  }

  // ── Formatting ─────────────────────────────────────────────────────────────

  function formatCompactCount(count) {
    if (!Number.isFinite(count) || count <= 0) return "";
    const units = ["", "k", "m", "b"];
    let scaled = count;
    let unitIndex = 0;
    while (scaled >= 1000 && unitIndex < units.length - 1) {
      scaled /= 1000;
      unitIndex += 1;
    }
    let rounded =
      scaled >= 100 ? Math.round(scaled) : Math.round(scaled * 10) / 10;
    if (rounded >= 1000 && unitIndex < units.length - 1) {
      rounded /= 1000;
      unitIndex += 1;
      rounded =
        rounded >= 100 ? Math.round(rounded) : Math.round(rounded * 10) / 10;
    }
    if (unitIndex === 0) return String(Math.round(rounded));
    if (rounded >= 100 || Number.isInteger(rounded))
      return `${Math.round(rounded)}${units[unitIndex]}`;
    return `${rounded.toFixed(1)}${units[unitIndex]}`;
  }

  function scopeLabel(scope) {
    if (scope === "gallery") return "Gallery";
    if (scope === "selected") return "Selected";
    if (scope === "all") return "All";
    if (scope === "none") return "None";
    return scope;
  }

  function levelName(level) {
    return LEVEL_NAMES[level] || `Level ${level + 1}`;
  }

  // ── Sorting ────────────────────────────────────────────────────────────────

  function sortNodes(nodes, type, parentPath) {
    const items = [...nodes];
    const scope = state.scope;
    const mode = state.sortMode;

    items.sort((a, b) => {
      if (mode === "count") {
        // Sort by image usage count only — no child-count fallback so that
        // models with 0 matching images sort alphabetically, not by version count
        const ca = getUsageCount(a, type, scope, parentPath);
        const cb = getUsageCount(b, type, scope, parentPath);
        const delta = cb - ca;
        if (delta !== 0) return delta;
      }
      return String(a.label || a.key)
        .toLowerCase()
        .localeCompare(String(b.label || b.key).toLowerCase());
    });

    return items;
  }

  // ── Rendering ──────────────────────────────────────────────────────────────

  function render() {
    state.renderToken++;
    renderScopeControls();
    renderTypeBoard();
  }

  function renderScopeControls() {
    if (!scopeControlsEl) return;
    scopeControlsEl.innerHTML = "";

    const buttonGroup = document.createElement("div");
    buttonGroup.className = "tag-filter-buttons";

    ["all", "gallery", "selected", "none"].forEach((scope) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `tag-filter-btn ${state.scope === scope ? "active" : ""}`;
      btn.textContent = scopeLabel(scope);
      btn.addEventListener("click", () => {
        state.scope = scope;
        render();
      });
      buttonGroup.appendChild(btn);
    });

    const sortDivider = document.createElement("span");
    sortDivider.className = "tag-controls-divider";
    sortDivider.setAttribute("aria-hidden", "true");

    const sortLabel = document.createElement("span");
    sortLabel.className = "tag-filter-title";
    sortLabel.textContent = "Sort";

    const sortGroup = document.createElement("div");
    sortGroup.className = "tag-filter-buttons";

    [
      { mode: "name", label: "Name" },
      { mode: "count", label: "Count" },
    ].forEach(({ mode, label }) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `tag-filter-btn ${state.sortMode === mode ? "active" : ""}`;
      btn.textContent = label;
      btn.addEventListener("click", () => {
        state.sortMode = mode;
        render();
      });
      sortGroup.appendChild(btn);
    });

    const searchDivider = document.createElement("span");
    searchDivider.className = "tag-controls-divider";
    searchDivider.setAttribute("aria-hidden", "true");

    const searchInput = document.createElement("input");
    searchInput.type = "search";
    searchInput.className = "tag-search-input";
    searchInput.placeholder = "Search models";
    searchInput.value = state.searchQuery;
    searchInput.setAttribute("aria-label", "Search models by name");
    searchInput.addEventListener("input", () => {
      state.searchQuery = searchInput.value;
      render();
    });

    scopeControlsEl.append(
      buttonGroup,
      sortDivider,
      sortLabel,
      sortGroup,
      searchDivider,
      searchInput,
    );
  }

  function renderTypeBoard() {
    if (!typeBoardEl) return;
    typeBoardEl.innerHTML = "";

    PANE_CONFIG.forEach((config) => {
      const pane = renderTypePane(config);
      typeBoardEl.appendChild(pane);
    });
  }

  function renderTypePane(config) {
    const { type, label, icon } = config;

    const wrapper = document.createElement("section");
    wrapper.className = `model-type-section source-${type}`;

    const meta = document.createElement("div");
    meta.className = "model-type-meta";

    const titleBar = document.createElement("div");
    titleBar.className = "model-type-title-bar";

    const title = document.createElement("h2");
    title.textContent = `${icon} ${label}`;
    titleBar.appendChild(title);

    // Count summary
    const countSpan = document.createElement("span");
    countSpan.className = "tag-pane-count";
    const nodes = state.hierarchy[type] || [];
    countSpan.textContent = `${nodes.length} base models`;
    titleBar.appendChild(countSpan);

    meta.appendChild(titleBar);
    wrapper.appendChild(meta);

    const pane = document.createElement("div");
    pane.className = `pane tag-pane source-${type}`;

    // Content area with 4-level hierarchy columns
    const content = document.createElement("div");
    content.className = "model-hierarchy-content";

    // Build 4 columns for the hierarchy levels
    const columns = [];
    for (let level = 0; level < 4; level++) {
      const col = document.createElement("div");
      col.className = `model-hierarchy-col model-col-level-${level}`;
      col.dataset.level = String(level);
      col.dataset.type = type;

      const colHeader = document.createElement("div");
      colHeader.className = "model-col-header";
      colHeader.textContent = levelName(level);
      col.appendChild(colHeader);

      const colBody = document.createElement("div");
      colBody.className = "model-col-body";
      col.appendChild(colBody);

      columns.push(col);
    }

    // Populate level 0 (base models) with top-level nodes
    const rootNode = {
      key: "__root__",
      label: "Root",
      children: nodes,
    };
    populateColumn(columns[0], rootNode, type, "", 0);

    content.append(...columns);
    pane.appendChild(content);
    wrapper.appendChild(pane);

    return wrapper;
  }

  function populateColumn(colEl, parentNode, type, parentPath, level) {
    const body = colEl.querySelector(".model-col-body");
    if (!body) return;
    body.innerHTML = "";

    let children = parentNode.children || [];

    // Apply scope filter (hide nodes with zero usage in gallery/selected)
    if (state.scope === "gallery" || state.scope === "selected") {
      children = children.filter((child) =>
        hasScopeUsage(child, type, state.scope, parentPath),
      );
    }

    // Apply search filter
    if (state.searchQuery) {
      children = children.filter((child) =>
        matchesSearch(child, state.searchQuery),
      );
    }

    // Sort
    children = sortNodes(children, type, parentPath);

    if (children.length === 0) {
      const empty = document.createElement("div");
      empty.className = "model-col-empty";
      empty.textContent = state.scope === "none" ? "—" : "No items";
      body.appendChild(empty);
      return;
    }

    children.forEach((node) => {
      const chip = createChip(node, type, parentPath, level, colEl);
      body.appendChild(chip);
    });
  }

  function createChip(node, type, parentPath, level, currentCol) {
    const path = parentPath ? `${parentPath}.${node.key}` : node.key;
    const scope = state.scope;
    // parentPath, not path: getUsageCount appends node.key internally
    const usageCount = getUsageCount(node, type, scope, parentPath);
    const descendantCount = countDescendants(node);

    const chip = document.createElement("div");
    chip.className = "model-chip";
    chip.dataset.key = node.key;
    chip.dataset.path = path;
    chip.dataset.level = String(level);
    chip.title = `${node.label} (${LEVEL_NAMES[level]})\nKey: ${path}`;

    // Label
    const labelEl = document.createElement("span");
    labelEl.className = "model-chip-label";
    labelEl.textContent = node.label || node.key;
    chip.appendChild(labelEl);

    // Badges
    const badgesEl = document.createElement("span");
    badgesEl.className = "model-chip-badges";

    // Child count badge (↳ symbol via CSS ::before) — always shown when children exist
    if (descendantCount > 0) {
      const childLabel = levelName(level + 1).toLowerCase() + (descendantCount === 1 ? "" : "s");
      const descBadge = document.createElement("span");
      descBadge.className = "model-chip-badge model-badge-children";
      descBadge.textContent = String(descendantCount);
      descBadge.title = `${descendantCount} ${childLabel}`;
      badgesEl.appendChild(descBadge);
    }

    // Image count badge (scope usage) — shown when scope has matching images
    if (usageCount > 0 && scope !== "none") {
      const usageBadge = document.createElement("span");
      usageBadge.className = "model-chip-badge model-badge-images";
      usageBadge.textContent = formatCompactCount(usageCount);
      usageBadge.title = `${usageCount} ${scopeLabel(scope).toLowerCase()} image${usageCount === 1 ? "" : "s"}`;
      badgesEl.appendChild(usageBadge);
    }

    if (badgesEl.children.length > 0) {
      chip.appendChild(badgesEl);
    }

    // Click handler: populate next column
    chip.addEventListener("click", () => {
      // Clear selection in current column
      const siblings = currentCol.querySelectorAll(".model-chip.selected");
      siblings.forEach((s) => s.classList.remove("selected"));
      chip.classList.add("selected");

      // Populate next columns
      const typePane = currentCol.closest(".pane");
      if (!typePane) return;

      const cols = typePane.querySelectorAll(".model-hierarchy-col");

      // Clear columns after current level
      for (let i = level + 1; i < cols.length; i++) {
        const body = cols[i].querySelector(".model-col-body");
        if (body) body.innerHTML = "";
        // Reset header
        const header = cols[i].querySelector(".model-col-header");
        if (header) header.textContent = levelName(i);
      }

      // Populate next level if node has children
      if (node.children && node.children.length > 0 && level + 1 < cols.length) {
        populateColumn(
          cols[level + 1],
          node,
          type,
          path,
          level + 1,
        );
      }
    });

    return chip;
  }

  // ── Bootstrap ──────────────────────────────────────────────────────────────

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
