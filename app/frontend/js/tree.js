// ── Memory ───────────────────────────────────────────────────────────────────
// 📄 docs: app/docs/memories/image-api.md
// 📄 docs: app/docs/memories/taxonomy-import.md
// ──────────────────────────────────────────────────────────────────────────────
(() => {
  const MIN_VISIBLE_PANES = 4;
  const TREE_API_BASE = '/api/taxonomy/tree';
  const conceptBoard = document.getElementById('concept-board');
  const tagBoard = document.getElementById('tag-board');
  const tagDetailsPanel = document.getElementById('tag-details');
  const scopeControls = document.getElementById('scope-controls');
  const trashZone = document.getElementById('concept-trash');
  const conceptSearchInput = document.getElementById('concept-search-input');
  const conceptSearchSuggestions = document.getElementById('concept-search-suggestions');
  const conceptSortControls = document.getElementById('concept-sort-controls');
  const themeToggle = document.getElementById('theme-toggle');
  const countSingleTagsToggle = document.getElementById('count-single-tags-toggle');
  const editModeToggle = document.getElementById('edit-mode-toggle');
  const preferences = window.AtelierPreferences || null;
  const uiKit = window.AtelierUi || null;
  const TREE_STORAGE_KEYS = {
    countSingleTags: 'atelier_tree_count_single_tags',
    editMode: 'atelier_tree_edit_mode',
  };
  let statusTimer = null;
  if (!conceptBoard || !tagBoard || !tagDetailsPanel || !scopeControls) return;

  if (preferences) {
    preferences.initThemeFromCookie();
    preferences.bindThemeToggle(themeToggle);
  }

  const CIVITAI_SEED_TAGS = ["!", "!!", "3koma", "69", ":>=", ":d", ":o", ";d", "?", "^ ^", "^^^", "abs", "adult toys", "after anal", "after fellatio", "after sex", "after vaginal", "against wall", "age difference", "ahegao", "ahoge", "alien", "all fours", "alternate breast size", "alternate costume", "alternate hairstyle", "american flag bikini", "anal", "anal beads", "anal object insertion", "anal tail", "android", "angel", "angel wings", "animal", "animal ear fluff", "animal ears", "animal nose", "animal print", "animated", "animated alcohol", "animated animal genitalia", "anime", "ankle cuffs", "anklet", "antenna hair", "anus", "apron", "aqua eyes", "aqua hair"];
  const DANBOORU_SEED_TAGS = ["!", "!!", "!!_(p7kwzcwyyhxsz6u)", "!?", "!_block", "\"pile_'em_up\"_(genshin_impact)", "\"rouhou\"_ore_no_iinazuke_ni_natta_jimiko_ie_dewa_kawaii_shikanai.", "\"snow_on_the_hearth\"_(genshin_impact)", "\"sweet_dream\"_(genshin_impact)", "#104", "#b7282e", "#compass", "#unicus_(idolmaster)", "#yuki#", "$hu", "&ether", "'&'_-sora_no_mukou_de_sakimasu_you_ni-", "'free'_(arknights)", "'o'ne", "+++", "++_(9oafxjjhuktkdef)", "+1_(yakusoku0722)", "+7_(vin)", "+_(tas28282000)", "+_+", "+_-", "+tic_nee-san", "...", "...!", "...?", ".357-inch", ".52_gal_(splatoon)", ".96_gal_(splatoon)", "._(kometto333)", "._.", ".co", ".com_(bot_com1)", ".flow", ".hack//", ".hack//g.u.", ".hack//g.u._last_recode", ".hack//games", ".hack//link", ".hack//quantum", ".hack//roots", ".hack//sign", ".hack//tasogare_no_udewa_densetsu", ".l.l", ".live", ".noz"];
  const PROMPT_SEED_TAGS = ['masterpiece', 'best quality', 'cinematic lighting', 'dramatic shadow', 'high detail', 'volumetric light', 'depth of field', 'soft focus', 'film grain', 'studio photo', 'rim lighting', 'vibrant colors', 'sharp focus', 'ultra detailed', 'dynamic pose', 'full body', 'close-up portrait', 'beautiful face', 'clean background', 'professional composition'];
  const USER_SEED_TAGS = ['favorites', 'to-review', 'portfolio', 'new-style', 'client-work', 'concept-sketch', 'character-ideas', 'scene-ideas', 'color-tests', 'render-target', 'reference-match', 'story-moment', 'cover-candidate', 'batch-a', 'batch-b', 'experiment-1', 'experiment-2', 'monthly-picks', 'save-for-later', 'priority'];

  function buildSeedTag(name, source, idx, sourceSeedList) {
    const normalizedAlias = String(name || '').replace(/[_-]+/g, ' ').trim();
    const aliases = [];
    if (normalizedAlias && normalizedAlias.toLowerCase() !== String(name).toLowerCase()) {
      aliases.push(normalizedAlias);
    }

    const implies = idx > 0 ? [sourceSeedList[idx - 1]] : [];
    const examples =
      source === 'civitai'
        ? [`${window.__ATELIER_CONFIG?.civitai_web_base_url || 'https://civitai.red'}/images/${100000 + idx}`]
        : source === 'danbooru'
          ? [`https://danbooru.donmai.us/posts?tags=${encodeURIComponent(name)}`]
          : source === 'prompt'
            ? [`${name}, best quality, cinematic lighting`]
            : [`User collection note for '${name}'.`];

    return {
      id: `${source}:${idx + 1}`,
      name,
      source,
      scope: idx % 2 === 0 ? 'gallery' : 'selected',
      postCount: null,
      description: `${sourceLabel(source)} tag '${name}' (prototype description).`,
      aliases,
      implies,
      examples,
    };
  }

  function buildTagSeedData() {
    const tags = [];
    CIVITAI_SEED_TAGS.forEach((name, idx) => {
      tags.push(buildSeedTag(name, 'civitai', idx, CIVITAI_SEED_TAGS));
    });
    DANBOORU_SEED_TAGS.forEach((name, idx) => {
      tags.push(buildSeedTag(name, 'danbooru', idx, DANBOORU_SEED_TAGS));
    });
    PROMPT_SEED_TAGS.forEach((name, idx) => {
      tags.push(buildSeedTag(name, 'prompt', idx, PROMPT_SEED_TAGS));
    });
    USER_SEED_TAGS.forEach((name, idx) => {
      tags.push(buildSeedTag(name, 'user', idx, USER_SEED_TAGS));
    });
    return tags;
  }

  function ensureStatusNode() {
    let node = document.getElementById('concept-status-toast');
    if (node) {
      return node;
    }
    node = document.createElement('div');
    node.id = 'concept-status-toast';
    node.className = 'concept-status-toast is-hidden';
    document.body.appendChild(node);
    return node;
  }

  function showStatus(message, variant = 'info') {
    const node = ensureStatusNode();
    node.textContent = message;
    node.className = `concept-status-toast ${variant}`;
    if (statusTimer) {
      window.clearTimeout(statusTimer);
    }
    statusTimer = window.setTimeout(() => {
      node.className = 'concept-status-toast is-hidden';
      statusTimer = null;
    }, 2200);
  }

  function notifyGalleryTagFilter() {
    if (window.parent === window || !window.parent) {
      return;
    }

    if (state.multiSelectEnabled && state.selectedTagModes.size > 0) {
      // Multi-select mode: send array of {source, name, mode} entries
      const filters = [];
      state.selectedTagModes.forEach((mode, tagId) => {
        const tag = findTagById(tagId);
        if (!tag) return;
        const effectiveTag = resolveFilterEquivalentTag(tag);
        if (!effectiveTag) return;
        filters.push({
          tagId: effectiveTag.id,
          source: effectiveTag.source,
          name: effectiveTag.name,
          mode,
        });
      });
      window.parent.postMessage(
        {
          type: 'atelier:gallery-tag-filter',
          payload: filters.length ? filters : null,
          multiSelect: true,
        },
        window.location.origin,
      );
      return;
    }

    // Single-select mode (original behavior)
    const selectedTag = findTagById(state.selectedTagId);
    const effectiveTag = selectedTag ? resolveFilterEquivalentTag(selectedTag) : null;
    window.parent.postMessage(
      {
        type: 'atelier:gallery-tag-filter',
        payload: effectiveTag
          ? {
              tagId: effectiveTag.id,
              source: effectiveTag.source,
              name: effectiveTag.name,
            }
          : null,
      },
      window.location.origin,
    );
  }

  function beginTagDrag(tagId) {
    if (state.dragTagCleanupTimer) {
      window.clearTimeout(state.dragTagCleanupTimer);
      state.dragTagCleanupTimer = null;
    }
    state.dragTagId = tagId;
    state.lastDraggedTagId = tagId;
    state.dragTagAlternateMode = false;
  }

  function endTagDrag({ defer = true } = {}) {
    const cleanup = () => {
      state.dragTagId = null;
      state.lastDraggedTagId = null;
      state.dragTagAlternateMode = false;
      state.dragHoverConceptId = null;
      state.dragHoverConceptLevel = null;
      state.tagDropHandled = false;
      state.dragTagCleanupTimer = null;
    };

    if (!defer) {
      cleanup();
      return;
    }

    if (state.dragTagCleanupTimer) {
      window.clearTimeout(state.dragTagCleanupTimer);
    }
    state.dragTagCleanupTimer = window.setTimeout(cleanup, 80);
  }

  // Prototype state is intentionally local and blank-by-default.
  const state = {
    conceptsByParent: {
      root: [],
    },
    selectedByLevel: {},
    nextId: 1,
    nextTagId: 1,
    dragConceptId: null,
    dragTagId: null,
    lastDraggedTagId: null,
    dragTagAlternateMode: false,
    dragHoverConceptId: null,
    dragHoverConceptLevel: null,
    tagDropHandled: false,
    dragTagCleanupTimer: null,
    dragTagGhostEl: null,
    clickTimerByConcept: {},
    conceptTagIds: {},
    openAssociationConceptId: null,
    selectedTagId: null,
    tagPaneScrollTopBySource: {},
    selectedTagScope: 'all',
    tagSortMode: 'name',
    conceptSortMode: 'entry',
    tagSearchQuery: '',
    includeRelatedInSearch: true,
    includeDescriptionsInSearch: false,
    countSingleTags: false,
    editMode: false,
    showAssignedTags: false,
    showUnassignedTags: false,
    multiSelectEnabled: false,
    /** Multi-select filter state: Map<tagId, 'include'|'exclude'> */
    selectedTagModes: new Map(),
    selectedImageKey: null,
    selectedImageTagNamesBySource: {
      civitai: new Set(),
      danbooru: new Set(),
      prompt: new Set(),
      user: new Set(),
    },
    galleryTagNamesBySource: {
      civitai: new Set(),
      danbooru: new Set(),
      prompt: new Set(),
      user: new Set(),
    },
    tagUsageByScope: {
      gallery: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
      selected: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
      all: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
    },
    tagRenderTokenBySource: {
      civitai: 0,
      danbooru: 0,
      prompt: 0,
      user: 0,
    },
    tagLoadingBySource: {
      civitai: false,
      danbooru: false,
      prompt: false,
      user: false,
    },
    missingDataFilterBySource: {
      civitai: false,
      danbooru: false,
      prompt: false,
      user: false,
    },
    tagDetailsLoading: false,
    tagDetailsFetchedIds: new Set(),
    apiReady: false,
    tags: buildTagSeedData(),
    tagById: new Map(),
  };

  function rebuildTagLookup() {
    state.tagById = new Map();
    state.tags.forEach((tag) => {
      if (tag?.id != null) {
        state.tagById.set(String(tag.id), tag);
      }
    });
  }

  rebuildTagLookup();

  function normalizeTagName(value) {
    return String(value || '').trim().toLowerCase();
  }

  function emptyTagUsageByScope() {
    return {
      gallery: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
      selected: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
      all: {
        civitai: {},
        danbooru: {},
        prompt: {},
        user: {},
      },
    };
  }

  function normalizeTagUsageByScope(rawUsageByScope) {
    const normalized = emptyTagUsageByScope();
    if (!rawUsageByScope || typeof rawUsageByScope !== 'object') {
      return normalized;
    }

    ['gallery', 'selected', 'all'].forEach((scope) => {
      const scopePayload = rawUsageByScope[scope];
      if (!scopePayload || typeof scopePayload !== 'object') {
        return;
      }

      ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
        const sourcePayload = scopePayload[source];
        if (!sourcePayload || typeof sourcePayload !== 'object') {
          return;
        }

        const bucket = {};
        Object.entries(sourcePayload).forEach(([name, count]) => {
          const normalizedName = normalizeTagName(name);
          const normalizedCount = Number(count);
          if (!normalizedName || !Number.isFinite(normalizedCount) || normalizedCount <= 0) {
            return;
          }
          bucket[normalizedName] = Math.floor(normalizedCount);
        });
        normalized[scope][source] = bucket;
      });
    });

    return normalized;
  }

  function setSelectedImageTags(payload) {
    const bySource = payload && typeof payload === 'object' ? payload.bySource : null;
    const countsBySource = payload && typeof payload === 'object' ? payload.countsBySource : null;
    state.selectedImageKey = payload && typeof payload === 'object' ? (payload.imageKey || null) : null;

    ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
      const raw = bySource && Array.isArray(bySource[source]) ? bySource[source] : [];
      const normalized = raw
        .map((name) => normalizeTagName(name))
        .filter(Boolean);
      state.selectedImageTagNamesBySource[source] = new Set(normalized);

      const explicitCounts = countsBySource && countsBySource[source] && typeof countsBySource[source] === 'object'
        ? countsBySource[source]
        : null;

      if (explicitCounts) {
        const bucket = {};
        Object.entries(explicitCounts).forEach(([name, count]) => {
          const normalizedName = normalizeTagName(name);
          const normalizedCount = Number(count);
          if (!normalizedName || !Number.isFinite(normalizedCount) || normalizedCount <= 0) {
            return;
          }
          bucket[normalizedName] = Math.floor(normalizedCount);
        });
        state.tagUsageByScope.selected[source] = bucket;
        return;
      }

      state.tagUsageByScope.selected[source] = Object.fromEntries(
        normalized.map((name) => [name, 1])
      );
    });

    renderTagBoard();
  }

  function sourceKey(rawSource) {
    const source = String(rawSource || '').trim().toLowerCase();
    if (source === 'civitai' || source === 'danbooru' || source === 'prompt' || source === 'user') {
      return source;
    }
    return 'user';
  }

  async function apiRequest(path, options = {}) {
    const response = await fetch(path, {
      headers: {
        'Content-Type': 'application/json',
      },
      ...options,
    });
    if (!response.ok) {
      throw new Error(`Request failed (${response.status}) for ${path}`);
    }
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }

  function hydrateConceptBuckets(concepts) {
    const buckets = { root: [] };
    (concepts || []).forEach((concept) => {
      const parentKey = concept.parent_concept_id == null ? 'root' : String(concept.parent_concept_id);
      if (!buckets[parentKey]) {
        buckets[parentKey] = [];
      }
      buckets[parentKey].push({ id: Number(concept.id), name: concept.canonical_name });
      if (!buckets[String(concept.id)]) {
        buckets[String(concept.id)] = [];
      }
    });
    return buckets;
  }

  // Decode a columnar tag payload from /taxonomy/tree/tags/{source} into
  // hydrated tag objects that match the shape used by the rest of the UI.
  function decodeColumnarTags(source, cols, rows) {
    const ci = Object.fromEntries(cols.map((c, i) => [c, i]));
    const idIdx = ci.id ?? 0;
    const nameIdx = ci.name ?? 1;
    const extIdx = ci.ext_id ?? 2;
    const scopeIdx = ci.scope ?? 3;
    const pcIdx = ci.post_count ?? 4;
    const conceptIdx = ci.concept_id ?? 5;
    const mdTagIdx = ci.mdtag_id ?? 6;
    const mdNameIdx = ci.mdtag_name ?? 7;

    return rows.map((row) => {
      const rawId = row[idIdx];
      const rawPc = row[pcIdx];
      const rawConceptId = row[conceptIdx];
      const rawMdTagId = row[mdTagIdx];
      const rawMdName = row[mdNameIdx];
      const rawScope = row[scopeIdx];
      const parsedPc = Number.isFinite(Number(rawPc)) && Number(rawPc) > 0 ? Math.floor(Number(rawPc)) : null;

      return {
        id: `term:${rawId}`,
        authorityTermId: rawId != null ? Number(rawId) : null,
        externalTagId: row[extIdx] != null ? String(row[extIdx]) : null,
        name: String(row[nameIdx] || ''),
        source,
        scope: String(rawScope || 'image') === 'image' ? 'selected' : String(rawScope),
        postCount: parsedPc,
        conceptId: rawConceptId != null ? Number(rawConceptId) : null,
        mappedDanbooruTagId: rawMdTagId != null ? String(rawMdTagId) : null,
        mappedDanbooruName: rawMdName != null ? String(rawMdName) : null,
        description: '',
        aliases: [],
        implies: [],
        examples: [],
      };
    });
  }

  // Replace the tags for one source in state and update conceptTagIds accordingly.
  function mergeSourceTagsIntoState(source, newTags) {
    const oldTagIds = new Set(
      state.tags.filter((t) => t.source === source).map((t) => t.id),
    );
    // Replace tags for this source.
    const keptTags = state.tags.filter((t) => t.source !== source);
    state.tags = keptTags.concat(newTags);
    rebuildTagLookup();

    // Remove old tag IDs from conceptTagIds.
    for (const [conceptId, tagIds] of Object.entries(state.conceptTagIds)) {
      const filtered = tagIds.filter((id) => !oldTagIds.has(id));
      if (filtered.length === 0) {
        delete state.conceptTagIds[conceptId];
      } else {
        state.conceptTagIds[conceptId] = filtered;
      }
    }

    // Add new tag IDs from this source.
    for (const tag of newTags) {
      if (tag.conceptId != null) {
        const key = String(tag.conceptId);
        const existing = state.conceptTagIds[key] || [];
        if (!existing.includes(tag.id)) {
          state.conceptTagIds[key] = [...existing, tag.id];
        }
      }
    }
  }

  // Re-render one source pane in-place without touching the others.
  function refreshTagPane(source) {
    state.tagRenderTokenBySource[source] = (state.tagRenderTokenBySource[source] || 0) + 1;
    const renderToken = state.tagRenderTokenBySource[source];
    const existing = tagBoard.querySelector(`.tag-pane.source-${CSS.escape(source)}`);
    const newPane = renderTagPane(source, renderToken);
    if (existing instanceof HTMLElement) {
      tagBoard.replaceChild(newPane, existing);
      restoreTagPaneScrollState();
    }
  }

  // Fetch and merge tags for one source from the per-source columnar endpoint.
  async function loadTagsForSource(source) {
    state.tagLoadingBySource[source] = true;
    try {
      const payload = await apiRequest(`${TREE_API_BASE}/tags/${source}`);
      const { cols, rows } = payload ?? {};
      if (Array.isArray(cols) && Array.isArray(rows)) {
        const newTags = decodeColumnarTags(source, cols, rows);
        mergeSourceTagsIntoState(source, newTags);
      }
    } catch {
      // Keep whatever tags were there before; the pane will show stale or empty state.
    } finally {
      state.tagLoadingBySource[source] = false;
      refreshTagPane(source);
    }
  }

  // Fire all four source fetches in parallel.
  function loadTagsForAllSources() {
    ['civitai', 'danbooru', 'prompt', 'user'].forEach((src) => {
      loadTagsForSource(src);
    });
  }

  async function loadTaxonomyState() {
    try {
      const payload = await apiRequest(`${TREE_API_BASE}/state?include_tag_details=0&include_tags=0`);
      const concepts = Array.isArray(payload?.concepts) ? payload.concepts : [];
      const galleryTagNamesBySource = payload && typeof payload === 'object' ? payload.gallery_tag_names_by_source : null;
      const tagUsageByScope = payload && typeof payload === 'object' ? payload.tag_usage_by_scope : null;

      state.conceptsByParent = hydrateConceptBuckets(concepts);
      state.tagUsageByScope = normalizeTagUsageByScope(tagUsageByScope);

      ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
        const raw = galleryTagNamesBySource && Array.isArray(galleryTagNamesBySource[source])
          ? galleryTagNamesBySource[source]
          : [];
        state.galleryTagNamesBySource[source] = new Set(
          raw
            .map((name) => normalizeTagName(name))
            .filter(Boolean)
        );
      });

      const maxConceptId = concepts.reduce((max, c) => Math.max(max, Number(c.id) || 0), 0);
      state.nextId = maxConceptId + 1;
      state.apiReady = true;
    } catch {
      state.apiReady = false;
    }
    // Fire per-source tag loads in parallel (does not block concept render).
    loadTagsForAllSources();
  }

  function parentKeyToParentId(parentKey) {
    return parentKey === 'root' ? null : Number(parentKey);
  }

  async function persistTagAssociation(tagId, conceptId) {
    if (!state.apiReady) return;
    const tag = findTagById(tagId);
    if (!tag || !conceptId) return;

    const isSynthetic = tag.authorityTermId != null && Number(tag.authorityTermId) < 0;
    const body = {
      authority_term_id: Number(tag.authorityTermId),
      concept_id: Number(conceptId),
    };
    if (isSynthetic) {
      body.tag_name = String(tag.name || '');
      body.tag_source = String(tag.source || 'user');
    }

    try {
      const result = await apiRequest(`${TREE_API_BASE}/associate`, {
        method: 'POST',
        body: JSON.stringify(body),
      });

      // If a synthetic tag was promoted to a real AuthorityTerm, update
      // local state so future operations use the real ID.
      if (isSynthetic && result?.authority_term_id != null) {
        const realId = Number(result.authority_term_id);
        const oldId = String(tag.id);
        tag.authorityTermId = realId;
        tag.id = `term:${realId}`;
        // Migrate conceptTagIds references from old synthetic ID to new real ID.
        for (const [cId, ids] of Object.entries(state.conceptTagIds)) {
          const idx = ids.indexOf(oldId);
          if (idx !== -1) {
            ids[idx] = tag.id;
          }
        }
        // Rebuild the lookup so findTagById resolves the new ID.
        rebuildTagLookup();
      }
    } catch (err) {
      // Keep UI optimistic for prototype usage, but log the error.
      console.warn('[tree] persistTagAssociation failed:', err, body);
    }
  }

  async function persistTagDisassociation(tagId) {
    if (!state.apiReady) return;
    const tag = findTagById(tagId);
    if (!tag?.authorityTermId) return;
    try {
      await apiRequest(`${TREE_API_BASE}/associate/${Number(tag.authorityTermId)}`, {
        method: 'DELETE',
      });
    } catch {
      // Keep UI optimistic for prototype usage.
    }
  }

  async function persistDeletePromptTag(authorityTermId) {
    if (!state.apiReady || !authorityTermId) return false;
    try {
      await apiRequest(`${TREE_API_BASE}/tag/${Number(authorityTermId)}`, {
        method: 'DELETE',
      });
      return true;
    } catch {
      return false;
    }
  }

  async function loadTagDetails(tag) {
    if (!state.apiReady || !tag?.authorityTermId) return false;
    try {
      const details = await apiRequest(`${TREE_API_BASE}/tag/${Number(tag.authorityTermId)}/details`);
      tag.description = String(details?.description || '');
      tag.aliases = Array.isArray(details?.aliases) ? [...details.aliases] : [];
      tag.implies = Array.isArray(details?.implies) ? [...details.implies] : [];
      tag.examples = Array.isArray(details?.examples) ? [...details.examples] : [];
      return true;
    } catch {
      return false;
    }
  }

  async function persistTagDetails(tag, partial) {
    if (!state.apiReady || !tag?.authorityTermId) return false;
    try {
      await apiRequest(`${TREE_API_BASE}/tag/${Number(tag.authorityTermId)}/details`, {
        method: 'PATCH',
        body: JSON.stringify(partial),
      });
      return true;
    } catch {
      return false;
    }
  }

  async function persistConceptRename(conceptId, canonicalName) {
    if (!state.apiReady) return;
    try {
      await apiRequest(`/api/taxonomy/concepts/${Number(conceptId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ canonical_name: canonicalName }),
      });
    } catch {
      // Keep UI optimistic for prototype usage.
    }
  }

  async function persistConceptParent(conceptId, parentKey) {
    if (!state.apiReady) return true;
    try {
      await apiRequest(`/api/taxonomy/concepts/${Number(conceptId)}/parent`, {
        method: 'POST',
        body: JSON.stringify({ parent_concept_id: parentKeyToParentId(parentKey) }),
      });
      return true;
    } catch {
      return false;
    }
  }

  async function persistDeleteConceptBranch(conceptId) {
    if (!state.apiReady) return true;
    try {
      await apiRequest(`/api/taxonomy/concepts/${Number(conceptId)}`, {
        method: 'DELETE',
      });
      return true;
    } catch {
      return false;
    }
  }

  function findTagById(tagId) {
    if (!tagId) return null;
    const normalizedTagId = String(tagId);
    return state.tagById.get(normalizedTagId) || null;
  }

  function removeTagFromState(tagId) {
    const normalizedTagId = String(tagId || '').trim();
    if (!normalizedTagId) {
      return;
    }

    state.tags = state.tags.filter((tag) => String(tag.id) !== normalizedTagId);
    state.tagById.delete(normalizedTagId);
    Object.keys(state.conceptTagIds).forEach((conceptId) => {
      const filtered = (state.conceptTagIds[conceptId] || []).filter((id) => String(id) !== normalizedTagId);
      if (filtered.length) {
        state.conceptTagIds[conceptId] = filtered;
      } else {
        delete state.conceptTagIds[conceptId];
      }
    });
    if (state.selectedTagId === normalizedTagId) {
      state.selectedTagId = null;
      notifyGalleryTagFilter();
    }
    if (state.selectedTagModes.has(normalizedTagId)) {
      state.selectedTagModes.delete(normalizedTagId);
      notifyGalleryTagFilter();
    }
  }

  async function deletePromptTag(tag) {
    if (!tag) {
      return;
    }
    if (sourceKey(tag.source) !== 'prompt') {
      showStatus('Only prompt tags can be deleted from this control.', 'warn');
      return;
    }

    const confirmed = window.confirm(`Delete prompt tag "${tag.name}" from the database?`);
    if (!confirmed) {
      return;
    }

    if (state.apiReady) {
      if (!tag.authorityTermId) {
        showStatus('Prompt tag cannot be deleted because it has no authority term id.', 'warn');
        return;
      }
      const deleted = await persistDeletePromptTag(tag.authorityTermId);
      if (!deleted) {
        showStatus(`Could not delete prompt tag "${tag.name}".`, 'warn');
        return;
      }
    }

    removeTagFromState(tag.id);
    showStatus(`Deleted prompt tag "${tag.name}".`, 'success');
    render();
  }

  function getDraggedTagId(event) {
    if (state.dragTagId) {
      return state.dragTagId;
    }
    if (state.lastDraggedTagId) {
      return state.lastDraggedTagId;
    }
    const transfer = event?.dataTransfer;
    if (!transfer) {
      return null;
    }
    const explicitId = String(transfer.getData('application/x-atelier-tag-id') || '').trim();
    if (explicitId) {
      return explicitId;
    }

    const source = String(transfer.getData('application/x-atelier-tag-source') || '').trim();
    const name = String(transfer.getData('application/x-atelier-tag-name') || '').trim();
    if (!source || !name) {
      return null;
    }
    const matched = findTagBySourceAndName(source, name);
    return matched?.id || null;
  }

  function isActiveConceptHoverTarget() {
    if (state.dragHoverConceptId == null) {
      return false;
    }
    const selector = `.concept-block[data-concept-id="${CSS.escape(String(state.dragHoverConceptId))}"]`;
    const hoveredConcept = document.querySelector(selector);
    if (!(hoveredConcept instanceof HTMLElement)) {
      return false;
    }
    return hoveredConcept.classList.contains('drag-over') || hoveredConcept.classList.contains('drag-over-alt');
  }

  function associatedTagIdsForConcept(conceptId) {
    return state.conceptTagIds[String(conceptId)] || [];
  }

  function assignedTagIdSet() {
    const assigned = new Set();
    Object.values(state.conceptTagIds).forEach((ids) => {
      (ids || []).forEach((id) => assigned.add(id));
    });
    return assigned;
  }

  function mappedDanbooruTagForPromptTag(tag) {
    if (!tag || sourceKey(tag.source) !== 'prompt') {
      return null;
    }

    const mappedTagId = String(tag.mappedDanbooruTagId || '').trim();
    if (mappedTagId) {
      const byId = state.tags.find(
        (candidate) => sourceKey(candidate?.source) === 'danbooru' && String(candidate?.externalTagId || '').trim() === mappedTagId,
      );
      if (byId) {
        return byId;
      }
    }

    const mappedName = String(tag.mappedDanbooruName || '').trim();
    if (mappedName) {
      const byName = findTagBySourceAndName('danbooru', mappedName);
      if (byName) {
        return byName;
      }
    }

    return null;
  }

  function isTagAssigned(tag, assignedSet) {
    if (!tag) {
      return false;
    }

    if (assignedSet.has(tag.id)) {
      return true;
    }

    const mappedDanbooruTag = mappedDanbooruTagForPromptTag(tag);
    return Boolean(mappedDanbooruTag);
  }

  function shouldIncludeTagByAssignment(tag, assignedSet) {
    const includeAssigned = state.showAssignedTags;
    const includeUnassigned = state.showUnassignedTags;
    if (includeAssigned === includeUnassigned) {
      return true;
    }
    const isAssigned = isTagAssigned(tag, assignedSet);
    return includeAssigned ? isAssigned : !isAssigned;
  }

  function resolveFilterEquivalentTag(tag) {
    if (!tag) {
      return null;
    }

    const mappedDanbooruTag = mappedDanbooruTagForPromptTag(tag);
    if (mappedDanbooruTag) {
      return mappedDanbooruTag;
    }

    return tag;
  }

  function associatedTagsForConcept(conceptId) {
    return associatedTagIdsForConcept(conceptId)
      .map((tagId) => findTagById(tagId))
      .filter(Boolean);
  }

  function associatedConceptIdsForTag(tagId) {
    const normalizedTagId = String(tagId || '').trim();
    if (!normalizedTagId) {
      return [];
    }

    return Object.entries(state.conceptTagIds)
      .filter(([, ids]) => Array.isArray(ids) && ids.includes(normalizedTagId))
      .map(([conceptId]) => Number(conceptId))
      .filter((conceptId) => Number.isInteger(conceptId));
  }

  function primaryAssociatedConceptIdForTag(tag) {
    const tagId = String(tag?.id || '').trim();
    const normalizedTagName = String(tag?.name || '').trim().toLowerCase();
    const conceptIds = associatedConceptIdsForTag(tagId);
    if (!conceptIds.length) {
      return null;
    }

    // Prefer a concept whose canonical name matches the tag name exactly.
    if (normalizedTagName) {
      const exactMatch = conceptIds.find((conceptId) => {
        const concept = findConceptById(conceptId);
        return String(concept?.name || '').trim().toLowerCase() === normalizedTagName;
      });
      if (exactMatch != null) {
        return exactMatch;
      }
    }

    // Otherwise prefer the deepest concept path (most specific assignment).
    conceptIds.sort((left, right) => {
      const leftDepth = conceptIdPathForId(left).length;
      const rightDepth = conceptIdPathForId(right).length;
      if (leftDepth !== rightDepth) {
        return rightDepth - leftDepth;
      }
      return left - right;
    });

    return conceptIds[0] ?? null;
  }

  function associateTagToConcept(tagId, conceptId) {
    if (!tagId || !conceptId) return false;
    const key = String(conceptId);
    const existing = state.conceptTagIds[key] || [];
    if (existing.includes(tagId)) {
      return false;
    }
    state.conceptTagIds[key] = [...existing, tagId];
    persistTagAssociation(tagId, conceptId);
    return true;
  }

  function disassociateTagFromConcept(tagId, conceptId) {
    if (!tagId || !conceptId) return false;
    const key = String(conceptId);
    const existing = state.conceptTagIds[key] || [];
    if (!existing.length || !existing.includes(tagId)) {
      return false;
    }
    const next = existing.filter((id) => id !== tagId);
    if (!next.length) {
      delete state.conceptTagIds[key];
      if (state.openAssociationConceptId === conceptId) {
        state.openAssociationConceptId = null;
      }
      persistTagDisassociation(tagId);
      return true;
    }
    state.conceptTagIds[key] = next;
    persistTagDisassociation(tagId);
    return true;
  }

  function findConceptInBucketByName(parentKey, name) {
    if (parentKey == null) return null;
    const bucket = ensureBucket(parentKey);
    const target = String(name || '').trim().toLowerCase();
    if (!target) return null;
    return bucket.find((item) => String(item.name || '').trim().toLowerCase() === target) || null;
  }

  function findConceptByName(name) {
    const target = String(name || '').trim().toLowerCase();
    if (!target) return null;
    for (const bucket of Object.values(state.conceptsByParent)) {
      if (!Array.isArray(bucket)) continue;
      const found = bucket.find((item) => String(item.name || '').trim().toLowerCase() === target);
      if (found) return found;
    }
    return null;
  }

  function conceptSearchNames() {
    const seen = new Set();
    const names = [];

    for (const bucket of Object.values(state.conceptsByParent)) {
      if (!Array.isArray(bucket)) continue;
      for (const concept of bucket) {
        const name = String(concept?.name || '').trim();
        const key = name.toLowerCase();
        if (!name || seen.has(key)) continue;
        seen.add(key);
        names.push(name);
      }
    }

    names.sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
    return names;
  }

  function conceptIdPathForId(conceptId) {
    const path = [];
    let currentId = Number(conceptId);
    let safety = 0;

    while (Number.isInteger(currentId) && safety < 256) {
      safety += 1;
      const source = getBucketAndIndexForConcept(currentId);
      if (!source) break;

      const node = source.bucket[source.index];
      path.unshift(Number(node?.id || currentId));
      if (source.parentKey === 'root') {
        break;
      }

      const parentId = Number(source.parentKey);
      if (!Number.isInteger(parentId)) {
        break;
      }
      currentId = parentId;
    }

    return path;
  }

  function selectConceptInHierarchy(conceptId, fallbackLevel = 0) {
    const id = Number(conceptId);
    const path = conceptIdPathForId(id);
    if (!path.length) {
      selectConcept(fallbackLevel, id);
      return;
    }

    state.selectedByLevel = {};
    path.forEach((conceptPathId, index) => {
      state.selectedByLevel[index] = conceptPathId;
    });
    clearSelectionFromLevel(path.length);
    renderConceptBoard();
    refreshConceptSearchOptions();
  }

  function revealAssociatedConcept(level, parentConceptId, conceptId) {
    const path = conceptIdPathForId(conceptId);
    const isDirectChild = path.length >= 2
      && Number(path[path.length - 2]) === Number(parentConceptId)
      && Number(path[path.length - 1]) === Number(conceptId);

    if (isDirectChild) {
      state.selectedByLevel[level] = Number(parentConceptId);
      state.selectedByLevel[level + 1] = Number(conceptId);
      clearSelectionFromLevel(level + 2);
    } else if (path.length) {
      state.selectedByLevel = {};
      path.forEach((conceptPathId, index) => {
        state.selectedByLevel[index] = conceptPathId;
      });
      clearSelectionFromLevel(path.length);
    }

    state.openAssociationConceptId = null;
    return isDirectChild;
  }

  function conceptSearchMatches(query, names) {
    const needle = String(query || '').trim().toLowerCase();
    if (!needle) {
      return [];
    }

    const starts = [];
    const contains = [];
    names.forEach((name) => {
      const lower = name.toLowerCase();
      if (lower.startsWith(needle)) {
        starts.push(name);
      } else if (lower.includes(needle)) {
        contains.push(name);
      }
    });
    return [...starts, ...contains].slice(0, 120);
  }

  function closeConceptSearchSuggestions() {
    if (!conceptSearchSuggestions) return;
    conceptSearchSuggestions.classList.remove('is-open');
    conceptSearchSuggestions.innerHTML = '';
  }

  function refreshConceptSearchOptions() {
    if (!conceptSearchSuggestions || !conceptSearchInput) return;

    if (document.activeElement !== conceptSearchInput) {
      closeConceptSearchSuggestions();
      return;
    }

    const names = conceptSearchNames();
    const matches = conceptSearchMatches(conceptSearchInput.value, names);

    if (!matches.length) {
      closeConceptSearchSuggestions();
      return;
    }

    if (uiKit?.renderSuggestionList) {
      uiKit.renderSuggestionList(conceptSearchSuggestions, matches, {
        itemClass: 'concept-search-suggestion',
        onSelect: (name) => {
          conceptSearchInput.value = name;
          selectConceptFromSearchInput();
          closeConceptSearchSuggestions();
        },
      });
    } else {
      conceptSearchSuggestions.innerHTML = '';
      matches.forEach((name) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'concept-search-suggestion';
        button.textContent = name;
        button.addEventListener('mousedown', (event) => {
          event.preventDefault();
        });
        button.addEventListener('click', () => {
          conceptSearchInput.value = name;
          selectConceptFromSearchInput();
          closeConceptSearchSuggestions();
        });
        conceptSearchSuggestions.appendChild(button);
      });
    }

    conceptSearchSuggestions.classList.add('is-open');
  }

  function selectConceptFromSearchInput() {
    if (!conceptSearchInput) return;
    const query = String(conceptSearchInput.value || '').trim();
    if (!query) return;

    const match = findConceptByName(query);
    if (!match) {
      return;
    }

    selectConceptInHierarchy(match.id, 0);
  }

  function initConceptSearchControls() {
    if (!conceptSearchInput) return;

    conceptSearchInput.addEventListener('change', () => {
      selectConceptFromSearchInput();
    });

    conceptSearchInput.addEventListener('focus', () => {
      refreshConceptSearchOptions();
    });

    conceptSearchInput.addEventListener('input', () => {
      refreshConceptSearchOptions();
    });

    conceptSearchInput.addEventListener('blur', () => {
      window.setTimeout(() => {
        closeConceptSearchSuggestions();
      }, 120);
    });

    conceptSearchInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      selectConceptFromSearchInput();
      closeConceptSearchSuggestions();
    });

    conceptSearchInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      closeConceptSearchSuggestions();
    });
  }

  function initConceptSortControls() {
    if (!conceptSortControls) return;
    const buttons = Array.from(conceptSortControls.querySelectorAll('.concept-sort-btn'));
    const syncButtons = () => {
      buttons.forEach((button) => {
        const isActive = button.dataset.sortMode === state.conceptSortMode;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      });
    };

    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        const nextMode = button.dataset.sortMode || 'entry';
        if (state.conceptSortMode === nextMode) {
          return;
        }
        state.conceptSortMode = nextMode;
        syncButtons();
        renderConceptBoard();
      });
    });

    syncButtons();
  }

  function conceptDescendantCount(conceptId) {
    const rootId = Number(conceptId);
    if (!Number.isInteger(rootId)) {
      return 0;
    }

    let total = 0;
    const queue = [String(rootId)];
    while (queue.length) {
      const parentKey = queue.shift();
      const children = state.conceptsByParent[parentKey] || [];
      children.forEach((child) => {
        total += 1;
        queue.push(String(child.id));
      });
    }
    return total;
  }

  function sortedConceptsForParent(parentKey) {
    const concepts = [...ensureBucket(parentKey)];
    const sortMode = state.conceptSortMode;

    if (sortMode === 'alpha') {
      concepts.sort((left, right) => left.name.localeCompare(right.name, undefined, { sensitivity: 'base' }));
      return concepts;
    }

    if (sortMode === 'children') {
      concepts.sort((left, right) => {
        const childDelta = conceptDescendantCount(right.id) - conceptDescendantCount(left.id);
        if (childDelta !== 0) return childDelta;
        const nameDelta = left.name.localeCompare(right.name, undefined, { sensitivity: 'base' });
        if (nameDelta !== 0) return nameDelta;
        return Number(left.id) - Number(right.id);
      });
      return concepts;
    }

    concepts.sort((left, right) => Number(left.id) - Number(right.id));
    return concepts;
  }

  async function createOrSelectConceptForTag(parentKey, level, tag) {
    if (!tag || parentKey == null) return null;
    const existing = findConceptInBucketByName(parentKey, tag.name);
    if (existing) {
      selectConceptInHierarchy(existing.id, level);
      return existing;
    }

    const creation = await createConcept(tag.name, parentKey);
    if (!creation.concept) {
      return null;
    }

    const createdSource = getBucketAndIndexForConcept(creation.concept.id);
    const landedUnderRequestedParent =
      createdSource != null && String(createdSource.parentKey) === String(parentKey);

    // Avoid UI depth jumps when backend returns an existing concept from another
    // parent path; keep current pane context in that case.
    if (landedUnderRequestedParent) {
      // Always resolve selection from real hierarchy path to avoid pane-level drift
      // when asynchronous updates occur during drag/drop.
      selectConceptInHierarchy(creation.concept.id, level);
    } else {
      const requestedParentId = parentKey === 'root' ? null : Number(parentKey);
      if (requestedParentId != null && Number.isInteger(requestedParentId)) {
        state.selectedByLevel[Math.max(0, level - 1)] = requestedParentId;
        clearSelectionFromLevel(level);
      }
      renderConceptBoard();
      refreshConceptSearchOptions();
      showStatus(
        `Using existing concept "${creation.concept.name}" from another path.`,
        'info',
      );
    }
    return creation.concept;
  }

  async function ensureConceptForTag(parentKey, tag) {
    if (!tag || parentKey == null) return null;
    const existing = findConceptInBucketByName(parentKey, tag.name);
    if (existing) {
      return existing;
    }

    const creation = await createConcept(tag.name, parentKey);
    return creation.concept || null;
  }

  async function handleTagDropOnConcept(targetConcept, level, tagId, alternateTagDropMode) {
    if (!targetConcept || !tagId) {
      return false;
    }

    const tag = findTagById(tagId);
    if (!tag) {
      showStatus('Dragged tag could not be resolved.', 'warn');
      return false;
    }

    if (alternateTagDropMode) {
      const childConcept = await ensureConceptForTag(String(targetConcept.id), tag);
      if (childConcept) {
        associateTagToConcept(tag.id, childConcept.id);
        refreshAfterTagAssociation(tag.id);
        const placedUnderTarget = revealAssociatedConcept(level, targetConcept.id, childConcept.id);
        if (placedUnderTarget) {
          showStatus(`Associated ${tag.name} under ${targetConcept.name}.`, 'success');
        } else {
          showStatus(`Associated ${tag.name} to existing concept ${childConcept.name}.`, 'info');
        }
      } else {
        showStatus(`Could not associate ${tag.name} under ${targetConcept.name}.`, 'warn');
      }
      return true;
    }

    associateTagToConcept(tagId, targetConcept.id);
    refreshAfterTagAssociation(tagId);
    return true;
  }

  function refreshAfterTagAssociation(tagId) {
    const associatedTag = findTagById(tagId);
    if (associatedTag) {
      state.selectedTagId = String(tagId);
      state.tagDetailsLoading = false;
      notifyGalleryTagFilter();
      renderTagDetails();
    }

    renderConceptBoard();
    refreshConceptSearchOptions();
    if (associatedTag?.source) {
      const assignmentFilterActive = state.showAssignedTags !== state.showUnassignedTags;
      if (assignmentFilterActive) {
        refreshTagPane(associatedTag.source);
        window.requestAnimationFrame(() => {
          const chip = tagBoard.querySelector(`.tag-block[data-tag-id="${CSS.escape(String(tagId))}"]`);
          if (chip instanceof HTMLElement) {
            chip.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            const pool = chip.closest('.tag-pool');
            if (pool instanceof HTMLElement && pool.dataset.source) {
              state.tagPaneScrollTopBySource[pool.dataset.source] = pool.scrollTop;
            }
          }
        });
      } else {
        const chip = tagBoard.querySelector(`.tag-block[data-tag-id="${CSS.escape(String(tagId))}"]`);
        if (chip instanceof HTMLElement) {
          chip.classList.add('assigned');
          chip.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          const pool = chip.closest('.tag-pool');
          if (pool instanceof HTMLElement && pool.dataset.source) {
            state.tagPaneScrollTopBySource[pool.dataset.source] = pool.scrollTop;
          }
        }
      }
    }
  }

  function parseLineValues(value) {
    return String(value || '')
      .split(/\r?\n/)
      .map((part) => part.trim())
      .filter(Boolean);
  }

  // Detect URLs in text - matches http(s):// and simple URL patterns
  function extractUrlFromLine(line) {
    const urlPattern = /https?:\/\/[^\s]+/i;
    const match = String(line || '').match(urlPattern);
    return match ? match[0] : null;
  }

  // Handle clicks on the examples textarea to open URLs
  function handleExamplesTextareaClick(event) {
    const textarea = event.target;
    if (textarea.tagName !== 'TEXTAREA') return;

    // Get the position of the click within the textarea
    const clickX = event.clientX - textarea.getBoundingClientRect().left;
    const clickY = event.clientY - textarea.getBoundingClientRect().top;

    // Approximate the line number based on Y coordinate
    // This is a best-effort approach; for precise detection we'd need a more complex method
    const lineHeight = parseInt(window.getComputedStyle(textarea).lineHeight) || 20;
    const scrollTop = textarea.scrollTop;
    const approximateLineIndex = Math.floor((clickY + scrollTop) / lineHeight);

    const lines = textarea.value.split('\n');
    if (approximateLineIndex >= 0 && approximateLineIndex < lines.length) {
      const line = lines[approximateLineIndex];
      const url = extractUrlFromLine(line);
      if (url) {
        event.preventDefault();
        window.open(url, '_blank');
      }
    }
  }

  function matchesTagSearch(tag, query) {
    const needle = normalizeTagName(query);
    if (!needle) return true;

    if (normalizeTagName(tag.name).includes(needle)) {
      return true;
    }

    if (state.includeRelatedInSearch) {
      const aliases = Array.isArray(tag.aliases) ? tag.aliases : [];
      if (aliases.some((item) => normalizeTagName(item).includes(needle))) {
        return true;
      }

      const implies = Array.isArray(tag.implies) ? tag.implies : [];
      if (implies.some((item) => normalizeTagName(item).includes(needle))) {
        return true;
      }
    }

    if (state.includeDescriptionsInSearch) {
      const description = normalizeTagName(tag.description || '');
      if (description.includes(needle)) {
        return true;
      }
    }

    return false;
  }

  function findTagBySourceAndName(source, name) {
    const target = normalizeTagName(name);
    if (!source || !target) return null;
    return state.tags.find((tag) => tag.source === source && normalizeTagName(tag.name) === target) || null;
  }

  function createTagInSource(name, source, scope) {
    const trimmed = String(name || '').trim();
    if (!trimmed || !source) return null;
    const existing = findTagBySourceAndName(source, trimmed);
    if (existing) return existing;

    const created = {
      id: `${source}:new:${state.nextTagId}`,
      name: trimmed,
      source,
      scope: scope || 'selected',
      postCount: null,
      description: `${sourceLabel(source)} tag '${trimmed}' (prototype description).`,
      aliases: [],
      implies: [],
      examples: [],
    };
    state.nextTagId += 1;
    state.tags.push(created);
    state.tagById.set(String(created.id), created);
    return created;
  }

  function namesForSource(source, excludeName) {
    const excluded = normalizeTagName(excludeName);
    return state.tags
      .filter((tag) => tag.source === source)
      .map((tag) => tag.name)
      .filter((name) => normalizeTagName(name) !== excluded)
      .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
  }

  function renderChipEditor({
    host,
    labelText,
    values,
    onChange,
    placeholder,
    helpTextRight,
    helpTextBelow,
    source,
    selfName,
    scope,
  }) {
    const field = document.createElement('div');
    field.className = 'tag-field';

    const head = document.createElement('div');
    head.className = 'tag-field-head';
    const label = document.createElement('label');
    label.textContent = labelText;
    head.appendChild(label);
    if (helpTextRight) {
      const help = document.createElement('span');
      help.className = 'tag-field-help-inline';
      help.textContent = helpTextRight;
      head.appendChild(help);
    }
    field.appendChild(head);

    if (helpTextBelow) {
      const below = document.createElement('div');
      below.className = 'tag-field-help-below';
      below.textContent = helpTextBelow;
      field.appendChild(below);
    }

    const chipList = document.createElement('div');
    chipList.className = 'tag-chip-list';

    if (uiKit?.renderRemovableChips) {
      uiKit.renderRemovableChips(chipList, values || [], {
        chipClass: 'tag-chip',
        removeClass: 'tag-chip-remove',
        removeLabel: 'x',
        removeTitlePrefix: 'Remove ',
        onRemove: (_value, idx) => {
          const next = (values || []).filter((_, i) => i !== idx);
          onChange(next);
          renderTagDetails();
        },
      });
    } else {
      (values || []).forEach((value, idx) => {
        const chip = document.createElement('span');
        chip.className = 'tag-chip';

        const text = document.createElement('span');
        text.textContent = value;

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'tag-chip-remove';
        remove.textContent = 'x';
        remove.title = `Remove ${value}`;
        remove.addEventListener('click', () => {
          const next = (values || []).filter((_, i) => i !== idx);
          onChange(next);
          renderTagDetails();
        });

        chip.append(text, remove);
        chipList.appendChild(chip);
      });
    }

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'new-block';
    addButton.textContent = '+new';

    const tryAddEntry = (rawValue) => {
      const entry = String(rawValue || '').trim();
      if (!entry) return true;

      if (normalizeTagName(entry) === normalizeTagName(selfName)) {
        window.alert('Self-references are not allowed.');
        return false;
      }

      let resolvedName = entry;
      if (source) {
        let existing = findTagBySourceAndName(source, entry);
        if (!existing) {
          if (source !== 'user') {
            const confirmed = window.confirm(
              `'${entry}' is not an existing ${sourceLabel(source)} tag. Create it?`
            );
            if (!confirmed) {
              return false;
            }
          }
          existing = createTagInSource(entry, source, scope);
        }
        resolvedName = existing?.name || entry;
      }

      const next = [...(values || [])];
      if (next.some((item) => normalizeTagName(item) === normalizeTagName(resolvedName))) {
        return true;
      }

      next.push(resolvedName);
      onChange(next);
      return true;
    };

    const mountInput = () => {
      const input = document.createElement('input');
      input.className = 'new-chip-input';
      input.type = 'text';
      input.placeholder = placeholder;

      let suggestions = null;
      if (source) {
        const options = namesForSource(source, selfName);
        if (options.length) {
          suggestions = document.createElement('datalist');
          suggestions.id = `tag-suggest-${source}-${labelText.toLowerCase()}-${state.nextTagId}`;
          if (uiKit?.populateDatalist) {
            uiKit.populateDatalist(suggestions, options);
          } else {
            options.forEach((name) => {
              const option = document.createElement('option');
              option.value = name;
              suggestions.appendChild(option);
            });
          }
          field.appendChild(suggestions);
          input.setAttribute('list', suggestions.id);
        }
      }

      chipList.replaceChild(input, addButton);
      input.focus();

      const reset = () => {
        if (suggestions?.parentNode) {
          suggestions.remove();
        }
        if (input.parentNode === chipList) {
          chipList.replaceChild(addButton, input);
        }
      };

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          const shouldClose = tryAddEntry(input.value);
          if (shouldClose) {
            reset();
            renderTagDetails();
          }
          return;
        }
        if (event.key === 'Escape') {
          reset();
        }
      });

      input.addEventListener('blur', () => {
        reset();
      });
    };

    addButton.addEventListener('click', mountInput);
    addButton.addEventListener('dragover', (event) => {
      const draggedTagId = getDraggedTagId(event);
      if (!draggedTagId) {
        return;
      }
      const dragged = findTagById(draggedTagId);
      if (!dragged || dragged.source !== source) {
        return;
      }
      if (normalizeTagName(dragged.name) === normalizeTagName(selfName)) {
        return;
      }
      event.preventDefault();
      addButton.classList.add('drag-over');
    });
    addButton.addEventListener('dragleave', () => {
      addButton.classList.remove('drag-over');
    });
    addButton.addEventListener('drop', (event) => {
      addButton.classList.remove('drag-over');
      const draggedTagId = getDraggedTagId(event);
      if (!draggedTagId) {
        return;
      }
      event.preventDefault();
      const dragged = findTagById(draggedTagId);
      if (!dragged || dragged.source !== source) {
        return;
      }
      if (normalizeTagName(dragged.name) === normalizeTagName(selfName)) {
        window.alert('Self-references are not allowed.');
        return;
      }
      const shouldClose = tryAddEntry(dragged.name);
      if (shouldClose) {
        renderTagDetails();
      }
    });
    chipList.appendChild(addButton);
    field.appendChild(chipList);

    host.appendChild(field);
  }

  function renderTagDetails() {
    tagDetailsPanel.innerHTML = '';
    tagDetailsPanel.classList.remove('source-civitai', 'source-danbooru', 'source-prompt', 'source-user');
    const selected = findTagById(state.selectedTagId);
    if (!selected) {
      const empty = document.createElement('div');
      empty.className = 'tag-empty';
      empty.textContent = 'Select one tag from CivitAI, Danbooru, Prompt, or User to edit its details.';
      tagDetailsPanel.appendChild(empty);
      return;
    }

    if (state.tagDetailsLoading) {
      const loading = document.createElement('div');
      loading.className = 'tag-details-loading';
      const spinner = document.createElement('span');
      spinner.className = 'tag-details-spinner';
      loading.appendChild(spinner);
      const text = document.createElement('span');
      text.textContent = `Loading details for \u201C${selected.name}\u201D\u2026`;
      loading.appendChild(text);
      tagDetailsPanel.appendChild(loading);
      return;
    }

    tagDetailsPanel.classList.add(`source-${selected.source}`);

    const grid = document.createElement('div');
    grid.className = 'tag-details-grid';

    const descriptionField = document.createElement('div');
    descriptionField.className = 'tag-field';
    const descriptionHead = document.createElement('div');
    descriptionHead.className = 'tag-field-head';
    const descriptionLabel = document.createElement('label');
    descriptionLabel.textContent = 'Description';
    const descriptionHelp = document.createElement('span');
    descriptionHelp.className = 'tag-field-help-inline';
    descriptionHelp.textContent = `${(selected.description || '').length}/60`;
    descriptionHead.append(descriptionLabel, descriptionHelp);
    descriptionField.appendChild(descriptionHead);
    const descriptionSubHelp = document.createElement('div');
    descriptionSubHelp.className = 'tag-field-help-below';
    descriptionSubHelp.textContent = 'Short description of characteristics.';
    descriptionField.appendChild(descriptionSubHelp);

    const descriptionInput = document.createElement('input');
    descriptionInput.type = 'text';
    descriptionInput.maxLength = 60;
    descriptionInput.className = 'tag-desc-input';
    descriptionInput.value = selected.description || '';
    descriptionInput.placeholder = 'Short description (max 60 chars)';
    descriptionInput.addEventListener('input', () => {
      selected.description = String(descriptionInput.value || '').slice(0, 60);
      descriptionHelp.textContent = `${(selected.description || '').length}/60`;
    });
    descriptionInput.addEventListener('blur', () => {
      persistTagDetails(selected, { description: selected.description || '' });
    });
    descriptionField.appendChild(descriptionInput);
    grid.appendChild(descriptionField);

    renderChipEditor({
      host: grid,
      labelText: 'Aliases',
      values: selected.aliases || [],
      onChange: (next) => {
        selected.aliases = next;
        persistTagDetails(selected, { aliases: next });
      },
      placeholder: 'Add alias and press Enter',
      helpTextBelow: 'Synonymous tags from the same source.',
      source: selected.source,
      selfName: selected.name,
      scope: selected.scope,
    });

    renderChipEditor({
      host: grid,
      labelText: 'Implies',
      values: selected.implies || [],
      onChange: (next) => {
        selected.implies = next;
        persistTagDetails(selected, { implies: next });
      },
      placeholder: 'Add implied tag and press Enter',
      helpTextBelow: 'Always includes underlying tags.',
      source: selected.source,
      selfName: selected.name,
      scope: selected.scope,
    });

    const examplesField = document.createElement('div');
    examplesField.className = 'tag-field';
    const examplesHead = document.createElement('div');
    examplesHead.className = 'tag-field-head';
    const examplesLabel = document.createElement('label');
    examplesLabel.textContent = 'Examples';
    const examplesHelp = document.createElement('span');
    examplesHelp.className = 'tag-field-help-inline';
    examplesHelp.textContent = 'One example per line. Scroll when list is long.';
    examplesHead.append(examplesLabel, examplesHelp);
    examplesField.appendChild(examplesHead);
    const examplesInput = document.createElement('textarea');
    examplesInput.className = 'tag-examples-input';
    examplesInput.value = (selected.examples || []).join('\n');
    examplesInput.placeholder = 'one example per line';
    examplesInput.title = 'Click on URLs to open them. Edit to add/modify examples.';
    examplesInput.addEventListener('input', () => {
      selected.examples = parseLineValues(examplesInput.value);
    });
    examplesInput.addEventListener('blur', () => {
      persistTagDetails(selected, { examples: selected.examples || [] });
    });
    examplesInput.addEventListener('click', handleExamplesTextareaClick);
    examplesField.appendChild(examplesInput);
    grid.appendChild(examplesField);

    tagDetailsPanel.append(grid);
  }

  function getParentKey(level) {
    if (level === 0) return 'root';
    const parentId = state.selectedByLevel[level - 1];
    return parentId != null ? String(parentId) : null;
  }

  function ensureBucket(parentKey) {
    if (!state.conceptsByParent[parentKey]) {
      state.conceptsByParent[parentKey] = [];
    }
    return state.conceptsByParent[parentKey];
  }

  async function createConcept(name, parentKey) {
    const trimmed = String(name || '').trim();
    if (!trimmed) return { concept: null, existed: false };

    const bucket = ensureBucket(parentKey);
    const duplicate = bucket.find((item) => item.name.toLowerCase() === trimmed.toLowerCase());
    if (duplicate) return { concept: duplicate, existed: true };

    if (state.apiReady) {
      try {
        const result = await apiRequest('/api/taxonomy/concepts', {
          method: 'POST',
          body: JSON.stringify({
            canonical_name: trimmed,
            parent_concept_id: parentKeyToParentId(parentKey),
          }),
        });

        const conceptPayload = result && typeof result === 'object' ? result.concept : null;
        if (!conceptPayload || conceptPayload.id == null) {
          return { concept: null, existed: false };
        }

        const alreadyExists =
          typeof result?.message === 'string'
          && result.message.toLowerCase().includes('already exists');

        const concept = {
          id: Number(conceptPayload.id),
          name: String(conceptPayload.canonical_name || trimmed),
        };

        const serverParentKey =
          conceptPayload.parent_concept_id == null
            ? 'root'
            : String(Number(conceptPayload.parent_concept_id));

        // Keep local buckets aligned with backend truth. If API returned an existing
        // concept from a different parent, do not force it into the requested bucket.
        Object.keys(state.conceptsByParent).forEach((key) => {
          const bucket = state.conceptsByParent[key];
          if (!Array.isArray(bucket)) return;
          state.conceptsByParent[key] = bucket.filter((item) => Number(item.id) !== concept.id);
        });

        const targetBucket = ensureBucket(serverParentKey);
        const byId = targetBucket.find((item) => Number(item.id) === concept.id);
        if (!byId) {
          targetBucket.push(concept);
        }

        // Ensure downstream panes have a bucket anchor for this concept id.
        ensureBucket(String(concept.id));
        state.nextId = Math.max(state.nextId, concept.id + 1);
        return { concept, existed: alreadyExists };
      } catch {
        return { concept: null, existed: false };
      }
    }

    const concept = { id: state.nextId++, name: trimmed };
    bucket.push(concept);
    return { concept, existed: false };
  }

  function renameConcept(conceptId, nextName) {
    const source = getBucketAndIndexForConcept(conceptId);
    if (!source) {
      return { ok: false, reason: 'Concept not found.' };
    }

    const trimmed = String(nextName || '').trim();
    if (!trimmed) {
      return { ok: false, reason: 'Name cannot be empty.' };
    }

    const duplicate = source.bucket.find(
      (item, idx) => idx !== source.index && item.name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (duplicate) {
      return { ok: false, reason: `A concept named "${trimmed}" already exists here.` };
    }

    source.bucket[source.index].name = trimmed;
    persistConceptRename(conceptId, trimmed);
    return { ok: true };
  }

  function beginRenameConcept(button, concept) {
    if (!button || !concept) return;
    if (button.dataset.renaming === '1') return;

    button.dataset.renaming = '1';
    button.draggable = false;
    const originalName = concept.name;
    const input = document.createElement('input');
    input.className = 'new-input';
    input.type = 'text';
    input.value = originalName;
    button.textContent = '';
    button.appendChild(input);
    input.focus();
    input.select();

    const cleanup = () => {
      button.dataset.renaming = '0';
      button.draggable = true;
      render();
    };

    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        const result = renameConcept(concept.id, input.value);
        if (result.ok) {
          cleanup();
        }
        return;
      }
      if (event.key === 'Escape') {
        cleanup();
      }
    });

    input.addEventListener('blur', () => {
      const result = renameConcept(concept.id, input.value);
      if (!result.ok) {
        renameConcept(concept.id, originalName);
      }
      cleanup();
    });
  }

  function getBucketAndIndexForConcept(conceptId) {
    const id = Number(conceptId);
    for (const [parentKey, bucket] of Object.entries(state.conceptsByParent)) {
      if (!Array.isArray(bucket)) continue;
      const index = bucket.findIndex((item) => Number(item.id) === id);
      if (index >= 0) {
        return { parentKey, bucket, index };
      }
    }
    return null;
  }

  function getDescendantIds(conceptId) {
    const descendants = new Set();
    const queue = [String(conceptId)];

    while (queue.length) {
      const parentKey = queue.shift();
      const children = state.conceptsByParent[parentKey] || [];
      for (const child of children) {
        const childId = Number(child.id);
        if (!descendants.has(childId)) {
          descendants.add(childId);
          queue.push(String(childId));
        }
      }
    }

    return descendants;
  }

  async function deleteConceptBranch(conceptId) {
    const rootId = Number(conceptId);
    if (!Number.isInteger(rootId)) {
      return { ok: false, reason: 'Invalid concept.' };
    }

    const source = getBucketAndIndexForConcept(rootId);
    if (!source) {
      return { ok: false, reason: 'Concept not found.' };
    }

    const descendants = getDescendantIds(rootId);
    const toDelete = new Set([rootId, ...descendants]);
    const branchSize = toDelete.size;

    const sourceName = source.bucket[source.index]?.name || `#${rootId}`;
    const okToDelete = window.confirm(
      `Delete concept "${sourceName}" and ${branchSize - 1} descendant concept(s)? This cannot be undone in this prototype view.`,
    );
    if (!okToDelete) {
      return { ok: false, reason: 'Cancelled' };
    }

    const persisted = await persistDeleteConceptBranch(rootId);
    if (!persisted) {
      return { ok: false, reason: 'Delete failed.' };
    }

    for (const parentKey of Object.keys(state.conceptsByParent)) {
      const bucket = state.conceptsByParent[parentKey];
      if (!Array.isArray(bucket)) continue;
      state.conceptsByParent[parentKey] = bucket.filter((item) => !toDelete.has(Number(item.id)));
    }

    for (const id of toDelete) {
      delete state.conceptsByParent[String(id)];
      delete state.clickTimerByConcept[String(id)];
    }

    Object.keys(state.selectedByLevel).forEach((levelKey) => {
      const level = Number(levelKey);
      if (toDelete.has(Number(state.selectedByLevel[level]))) {
        delete state.selectedByLevel[level];
      }
    });
    normalizeSelectionChain();

    render();
    return { ok: true, deleted: branchSize };
  }

  function canDropConceptIntoPane(conceptId, level) {
    const dragId = Number(conceptId);
    if (!Number.isInteger(dragId)) {
      return { ok: false, reason: 'Invalid concept.' };
    }

    const source = getBucketAndIndexForConcept(dragId);
    if (!source) {
      return { ok: false, reason: 'Concept not found.' };
    }

    const targetParentKey = getParentKey(level);
    if (targetParentKey == null) {
      // Rule 3: cannot create hierarchy gaps.
      return { ok: false, reason: 'Select a concept in the pane to the left first.' };
    }

    return canDropConceptUnderParentKey(conceptId, targetParentKey, source);
  }

  function canDropConceptUnderParentKey(conceptId, targetParentKey, sourceHint = null) {
    const dragId = Number(conceptId);
    if (!Number.isInteger(dragId)) {
      return { ok: false, reason: 'Invalid concept.' };
    }

    const source = sourceHint || getBucketAndIndexForConcept(dragId);
    if (!source) {
      return { ok: false, reason: 'Concept not found.' };
    }

    if (targetParentKey == null) {
      return { ok: false, reason: 'Missing target parent.' };
    }

    if (String(source.parentKey) === String(targetParentKey)) {
      return { ok: false, reason: 'Already in this pane context.' };
    }

    if (targetParentKey !== 'root') {
      const targetParentId = Number(targetParentKey);
      if (targetParentId === dragId) {
        return { ok: false, reason: 'Concept cannot become its own parent.' };
      }

      const descendants = getDescendantIds(dragId);
      if (descendants.has(targetParentId)) {
        return { ok: false, reason: 'Concept cannot be moved under its descendant.' };
      }
    }

    const targetBucket = ensureBucket(targetParentKey);
    const moving = source.bucket[source.index];
    const nameConflict = targetBucket.some(
      (item) => Number(item.id) !== dragId && item.name.toLowerCase() === moving.name.toLowerCase(),
    );
    if (nameConflict) {
      return { ok: false, reason: `A concept named "${moving.name}" already exists here.` };
    }

    return { ok: true, targetParentKey, source };
  }

  function moveConceptToPane(conceptId, level) {
    const verdict = canDropConceptIntoPane(conceptId, level);
    if (!verdict.ok) {
      return verdict;
    }
    return moveConceptUsingVerdict(conceptId, verdict);
  }

  function moveConceptUnderParent(conceptId, parentConceptId) {
    const verdict = canDropConceptUnderParentKey(conceptId, String(parentConceptId));
    if (!verdict.ok) {
      return verdict;
    }
    return moveConceptUsingVerdict(conceptId, verdict);
  }

  function moveConceptUsingVerdict(conceptId, verdict) {
    if (!verdict.ok) {
      return verdict;
    }

    const moving = verdict.source.bucket[verdict.source.index];
    verdict.source.bucket.splice(verdict.source.index, 1);
    ensureBucket(verdict.targetParentKey).push(moving);
    persistConceptParent(conceptId, verdict.targetParentKey).then((ok) => {
      if (ok) return;
      // Resync if persistence failed so local layout does not drift from DB state.
      loadTaxonomyState().then(() => {
        render();
      });
    });

    // Clear stale selection references if needed, then redraw.
    for (const levelKey of Object.keys(state.selectedByLevel)) {
      const level = Number(levelKey);
      if (Number(state.selectedByLevel[level]) === Number(conceptId)) {
        clearSelection(level);
        return { ok: true };
      }
    }

    render();
    return { ok: true };
  }

  function findConceptById(conceptId) {
    if (conceptId == null) return null;
    const id = Number(conceptId);
    const buckets = Object.values(state.conceptsByParent);
    for (const bucket of buckets) {
      if (!Array.isArray(bucket)) continue;
      const match = bucket.find((item) => Number(item.id) === id);
      if (match) return match;
    }
    return null;
  }

  function clearSelectionFromLevel(level) {
    Object.keys(state.selectedByLevel).forEach((levelKey) => {
      const n = Number(levelKey);
      if (n >= level) {
        delete state.selectedByLevel[n];
      }
    });
  }

  function normalizeSelectionChain() {
    const levels = Object.keys(state.selectedByLevel)
      .map((key) => Number(key))
      .sort((a, b) => a - b);
    let firstGap = null;
    for (let i = 0; i < levels.length; i += 1) {
      if (levels[i] !== i) {
        firstGap = i;
        break;
      }
    }
    if (firstGap == null) {
      return;
    }
    clearSelectionFromLevel(firstGap);
  }

  function maxSelectedLevel() {
    let level = 0;
    while (state.selectedByLevel[level] != null) {
      level += 1;
    }
    return level - 1;
  }

  function conceptPaneCount() {
    const selectedNeed = maxSelectedLevel() + 2;
    return Math.max(MIN_VISIBLE_PANES, selectedNeed);
  }

  function selectConcept(level, conceptId) {
    state.selectedByLevel[level] = conceptId;
    clearSelectionFromLevel(level + 1);
    renderConceptBoard();
    refreshConceptSearchOptions();
  }

  function clearSelection(level) {
    clearSelectionFromLevel(level);
    renderConceptBoard();
    refreshConceptSearchOptions();
  }

  function paneTitleForLevel(level) {
    if (level === 0) return 'Root Concepts';
    const parentId = state.selectedByLevel[level - 1];
    const parent = findConceptById(parentId);
    if (parent?.name) {
      return `${parent.name} concepts`;
    }
    return `Level ${level + 1}`;
  }

  function filteredTagsForSource(source) {
    const scope = state.selectedTagScope;

    if (scope === 'none') {
      return [];
    }

    const assignedSet = assignedTagIdSet();

    return sortTags(state.tags
      .filter((tag) => tag.source === source)
      .filter((tag) => {
        const normalizedName = normalizeTagName(tag.name);
        if (!normalizedName) {
          return false;
        }

        if (scope === 'gallery') {
          const galleryTagSet = state.galleryTagNamesBySource[source] || new Set();
          return galleryTagSet.has(normalizedName);
        }

        if (scope === 'selected') {
          const imageTagSet = state.selectedImageTagNamesBySource[source] || new Set();
          return imageTagSet.has(normalizedName);
        }

        return true;
      })
        .filter((tag) => shouldIncludeTagByAssignment(tag, assignedSet))
      .filter((tag) => matchesTagSearch(tag, state.tagSearchQuery)));
  }

  function scopeLabel(scope) {
    if (scope === 'gallery') return 'Gallery';
    if (scope === 'selected') return 'Selected';
    if (scope === 'all') return 'All';
    if (scope === 'none') return 'None';
    return scope;
  }

  function formatCompactBadgeCount(count) {
    const value = Number(count);
    if (!Number.isFinite(value) || value <= 0) {
      return '';
    }

    const units = ['', 'k', 'm', 'b'];
    let scaled = value;
    let unitIndex = 0;

    while (scaled >= 1000 && unitIndex < units.length - 1) {
      scaled /= 1000;
      unitIndex += 1;
    }

    let rounded = scaled >= 100 ? Math.round(scaled) : Math.round(scaled * 10) / 10;
    if (rounded >= 1000 && unitIndex < units.length - 1) {
      rounded /= 1000;
      unitIndex += 1;
      rounded = rounded >= 100 ? Math.round(rounded) : Math.round(rounded * 10) / 10;
    }

    if (unitIndex === 0) {
      return String(Math.round(rounded));
    }
    if (rounded >= 100 || Number.isInteger(rounded)) {
      return `${Math.round(rounded)}${units[unitIndex]}`;
    }
    return `${rounded.toFixed(1)}${units[unitIndex]}`;
  }

  function getTagUsageCountTitle(tag, scope, count) {
    const source = sourceKey(tag?.source);
    const hasPopularityCount = Number.isFinite(Number(tag?.postCount)) && Number(tag?.postCount) > 0;
    if (source === 'danbooru' && scope === 'all' && hasPopularityCount) {
      return `Danbooru posts with this tag: ${count}`;
    }
    if (source === 'civitai' && scope === 'all' && hasPopularityCount) {
      return `CivitAI posts with this tag: ${count}`;
    }
    const label = scopeLabel(scope).toLowerCase();
    return `${count} ${label} item${count === 1 ? '' : 's'} with this tag`;
  }

  function getTagUsageCount(tag, scope) {
    if (!tag || !scope || scope === 'none') {
      return null;
    }

    if ((sourceKey(tag.source) === 'danbooru' || sourceKey(tag.source) === 'civitai') && scope === 'all') {
      const popularity = Number(tag.postCount);
      if (Number.isFinite(popularity) && popularity > 0) {
        return Math.floor(popularity);
      }
    }

    const normalizedName = normalizeTagName(tag.name);
    const source = sourceKey(tag.source);
    const count = Number(state.tagUsageByScope?.[scope]?.[source]?.[normalizedName]);
    return Number.isFinite(count) && count > 0 ? count : null;
  }

  function shouldDisplayTagUsageCount(usageCount) {
    if (usageCount == null) {
      return false;
    }
    if (Number(usageCount) === 1 && !state.countSingleTags) {
      return false;
    }
    return true;
  }

  function tagNumericIdentifier(tag) {
    if (tag?.authorityTermId != null && Number.isFinite(Number(tag.authorityTermId))) {
      return Number(tag.authorityTermId);
    }

    const rawId = String(tag?.id || '').trim();
    const trailingNumber = rawId.match(/(\d+)(?!.*\d)/);
    if (trailingNumber) {
      return Number(trailingNumber[1]);
    }

    return Number.POSITIVE_INFINITY;
  }

  function sortTags(tags) {
    const items = [...(Array.isArray(tags) ? tags : [])];
    const scope = state.selectedTagScope;
    const mode = state.tagSortMode || 'name';

    items.sort((left, right) => {
      if (mode === 'id') {
        const idDelta = tagNumericIdentifier(left) - tagNumericIdentifier(right);
        if (idDelta !== 0) return idDelta;
      }

      if (mode === 'count') {
        const leftCount = Number(getTagUsageCount(left, scope) || 0);
        const rightCount = Number(getTagUsageCount(right, scope) || 0);
        const countDelta = rightCount - leftCount;
        if (countDelta !== 0) return countDelta;
      }

      const nameDelta = String(left?.name || '').localeCompare(String(right?.name || ''), undefined, { sensitivity: 'base' });
      if (nameDelta !== 0) return nameDelta;

      return tagNumericIdentifier(left) - tagNumericIdentifier(right);
    });

    return items;
  }

  function sourceLabel(source) {
    if (source === 'civitai') return 'CivitAI';
    if (source === 'danbooru') return 'Danbooru';
    if (source === 'prompt') return 'Prompt';
    if (source === 'user') return 'User';
    return source;
  }

  function clearTagDragGhost() {
    if (!state.dragTagGhostEl) return;
    state.dragTagGhostEl.remove();
    state.dragTagGhostEl = null;
  }

  function createTagDragGhost(tag) {
    const ghost = document.createElement('div');
    ghost.className = `tag-drag-ghost source-${tag.source}`;
    ghost.textContent = tag.name;
    document.body.appendChild(ghost);
    return ghost;
  }

  function isAlternateTagDropMode(event) {
    return Boolean(event?.altKey || event?.ctrlKey);
  }

  function renderScopeControls() {
    scopeControls.innerHTML = '';
    const buttonGroup = document.createElement('div');
    buttonGroup.className = 'tag-filter-buttons';

    ['all', 'gallery', 'selected', 'none'].forEach((scope) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `tag-filter-btn ${state.selectedTagScope === scope ? 'active' : ''}`;
      btn.textContent = scopeLabel(scope);
      btn.addEventListener('click', () => {
        state.selectedTagScope = scope;
        render();
      });
      buttonGroup.appendChild(btn);
    });

    const sortDivider = document.createElement('span');
    sortDivider.className = 'tag-controls-divider';
    sortDivider.setAttribute('aria-hidden', 'true');

    const sortLabel = document.createElement('span');
    sortLabel.className = 'tag-filter-title';
    sortLabel.textContent = 'Sort';

    const sortButtonGroup = document.createElement('div');
    sortButtonGroup.className = 'tag-filter-buttons';

    [
      { mode: 'id', label: 'ID' },
      { mode: 'name', label: 'Name' },
      { mode: 'count', label: 'Count' },
    ].forEach(({ mode, label }) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `tag-filter-btn ${state.tagSortMode === mode ? 'active' : ''}`;
      btn.textContent = label;
      btn.addEventListener('click', () => {
        state.tagSortMode = mode;
        renderTagBoard();
        renderScopeControls();
      });
      sortButtonGroup.appendChild(btn);
    });

    const divider = document.createElement('span');
    divider.className = 'tag-controls-divider';
    divider.setAttribute('aria-hidden', 'true');

    const searchInput = document.createElement('input');
    searchInput.type = 'search';
    searchInput.className = 'tag-search-input';
    searchInput.placeholder = 'Search tags';
    searchInput.value = state.tagSearchQuery;
    searchInput.setAttribute('aria-label', 'Search tags by name, aliases, or implies');
    searchInput.addEventListener('input', () => {
      state.tagSearchQuery = searchInput.value;
      renderTagBoard();
    });

    const relatedLabel = document.createElement('label');
    relatedLabel.className = 'tag-search-opt';
    const relatedCheck = document.createElement('input');
    relatedCheck.type = 'checkbox';
    relatedCheck.checked = state.includeRelatedInSearch;
    relatedCheck.addEventListener('change', () => {
      state.includeRelatedInSearch = relatedCheck.checked;
      renderTagBoard();
    });
    const relatedText = document.createElement('span');
    relatedText.textContent = 'Include related';
    relatedLabel.append(relatedCheck, relatedText);

    const descLabel = document.createElement('label');
    descLabel.className = 'tag-search-opt';
    const descCheck = document.createElement('input');
    descCheck.type = 'checkbox';
    descCheck.checked = state.includeDescriptionsInSearch;
    descCheck.addEventListener('change', () => {
      state.includeDescriptionsInSearch = descCheck.checked;
      renderTagBoard();
    });
    const descText = document.createElement('span');
    descText.textContent = 'Include descriptions';
    descLabel.append(descCheck, descText);

    const assignmentDivider = document.createElement('span');
    assignmentDivider.className = 'tag-controls-divider';
    assignmentDivider.setAttribute('aria-hidden', 'true');

    const assignedLabel = document.createElement('label');
    assignedLabel.className = 'tag-search-opt';
    const assignedCheck = document.createElement('input');
    assignedCheck.type = 'checkbox';
    assignedCheck.checked = state.showAssignedTags;
    assignedCheck.addEventListener('change', () => {
      state.showAssignedTags = assignedCheck.checked;
      renderTagBoard();
    });
    const assignedText = document.createElement('span');
    assignedText.textContent = 'Assigned';
    assignedLabel.append(assignedCheck, assignedText);

    const unassignedLabel = document.createElement('label');
    unassignedLabel.className = 'tag-search-opt';
    const unassignedCheck = document.createElement('input');
    unassignedCheck.type = 'checkbox';
    unassignedCheck.checked = state.showUnassignedTags;
    unassignedCheck.addEventListener('change', () => {
      state.showUnassignedTags = unassignedCheck.checked;
      renderTagBoard();
    });
    const unassignedText = document.createElement('span');
    unassignedText.textContent = 'Unassigned';
    unassignedLabel.append(unassignedCheck, unassignedText);

    const multiSelectDivider = document.createElement('span');
    multiSelectDivider.className = 'tag-controls-divider';
    multiSelectDivider.setAttribute('aria-hidden', 'true');

    const multiSelectLabel = document.createElement('label');
    multiSelectLabel.className = 'tag-search-opt';
    const multiSelectCheck = document.createElement('input');
    multiSelectCheck.type = 'checkbox';
    multiSelectCheck.checked = state.multiSelectEnabled;
    multiSelectCheck.addEventListener('change', () => {
      state.multiSelectEnabled = multiSelectCheck.checked;
      // Clear selections when switching modes
      state.selectedTagModes.clear();
      state.selectedTagId = null;
      notifyGalleryTagFilter();
      renderTagBoard();
    });
    const multiSelectText = document.createElement('span');
    multiSelectText.textContent = 'Multi-Select';
    multiSelectLabel.append(multiSelectCheck, multiSelectText);

    scopeControls.append(
      buttonGroup,
      sortDivider,
      sortLabel,
      sortButtonGroup,
      divider,
      searchInput,
      relatedLabel,
      descLabel,
      assignmentDivider,
      assignedLabel,
      unassignedLabel,
      multiSelectDivider,
      multiSelectLabel,
    );
  }

  function renderTagPane(source, renderToken) {
    const pane = document.createElement('section');
    pane.className = `pane tag-pane source-${source}`;
    pane.addEventListener('click', (event) => {
      if (event.target === pane) {
        if (state.selectedTagId != null || state.selectedTagModes.size > 0) {
          state.selectedTagId = null;
          state.selectedTagModes.clear();
          notifyGalleryTagFilter();
          render();
        }
      }
    });

    const titleBar = document.createElement('div');
    titleBar.className = 'tag-pane-title-bar';

    const title = document.createElement('h2');
    title.textContent = `${sourceLabel(source)} Tags`;
    titleBar.appendChild(title);

    const missingBtn = document.createElement('button');
    missingBtn.className = 'tag-pane-icon-btn tag-pane-missing-btn';
    missingBtn.title = 'Filter gallery images missing tags from this source';
    missingBtn.innerHTML = '<span class="icon-missing" aria-hidden="true"><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8.5" cy="8.5" r="5.5"/><line x1="12.5" y1="12.5" x2="17" y2="17"/><line x1="6" y1="6" x2="11" y2="11"/><line x1="11" y1="6" x2="6" y2="11"/></svg></span>';
    missingBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      state.missingDataFilterBySource[source] = !state.missingDataFilterBySource[source];
      missingBtn.classList.toggle('active', state.missingDataFilterBySource[source]);
      // Notify gallery to filter images missing tags from this source
      window.parent.postMessage({
        type: 'atelier:gallery-missing-source-filter',
        source,
        active: state.missingDataFilterBySource[source],
      }, '*');
    });
    missingBtn.classList.toggle('active', state.missingDataFilterBySource[source]);
    titleBar.appendChild(missingBtn);

    const wrenchBtn = document.createElement('button');
    wrenchBtn.className = 'tag-pane-icon-btn tag-pane-wrench-btn';
    wrenchBtn.title = 'Open tag maintenance page';
    wrenchBtn.innerHTML = '<span class="icon-wrench" aria-hidden="true"><svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 3.3a3.5 3.5 0 0 0-4.95 0L8.05 5l1.4 1.4-5 5L3.05 10l-1.7 1.7a3.5 3.5 0 0 0 4.95 4.95l1.7-1.7-1.4-1.4 5-5 1.4 1.4 1.7-1.7a3.5 3.5 0 0 0 0-4.95z"/></svg></span>';
    wrenchBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      window.open(`/frontend/tag-maint.html?source=${source}`, '_blank');
    });
    titleBar.appendChild(wrenchBtn);

    pane.appendChild(titleBar);

    const pool = document.createElement('div');
    pool.className = 'tag-pool';
    pool.dataset.source = source;
    const savedScrollTop = Number(state.tagPaneScrollTopBySource[source]);
    if (Number.isFinite(savedScrollTop) && savedScrollTop > 0) {
      pool.scrollTop = savedScrollTop;
    }
    let requestMoreTags = null;
    pool.addEventListener('scroll', () => {
      state.tagPaneScrollTopBySource[source] = pool.scrollTop;
      if (
        typeof requestMoreTags === 'function'
        && pool.scrollTop + pool.clientHeight >= pool.scrollHeight - 900
      ) {
        requestMoreTags();
      }
    });
    pool.addEventListener('click', (event) => {
      if (event.target === pool) {
        if (state.selectedTagId != null || state.selectedTagModes.size > 0) {
          state.selectedTagId = null;
          state.selectedTagModes.clear();
          notifyGalleryTagFilter();
          render();
        }
      }
    });
    // Show a loading placeholder while the per-source tag list is being fetched
    // and no tags have arrived yet.
    if (state.tagLoadingBySource[source] && !state.tags.some((t) => t.source === source)) {
      const loading = document.createElement('div');
      loading.className = 'tag-loading-indicator';
      loading.textContent = `Loading ${sourceLabel(source)} tags\u2026`;
      pool.appendChild(loading);
      pane.appendChild(pool);
      return pane;
    }

    const tags = filteredTagsForSource(source);
    const assignedSet = assignedTagIdSet();

    const createTagChip = (tag) => {
      const isAssigned = isTagAssigned(tag, assignedSet);
      const mappedDanbooruTag = mappedDanbooruTagForPromptTag(tag);
      const usageCount = getTagUsageCount(tag, state.selectedTagScope);
      const multiMode = state.multiSelectEnabled ? (state.selectedTagModes.get(tag.id) || null) : null;
      const isActive = state.multiSelectEnabled ? Boolean(multiMode) : state.selectedTagId === tag.id;
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = `tag-block source-${source} ${isActive ? (state.multiSelectEnabled ? '' : 'active') : ''} ${multiMode ? `mode-${multiMode}` : ''} ${isAssigned ? 'assigned' : ''} ${mappedDanbooruTag ? 'mapped' : ''}`;
      chip.dataset.tagId = String(tag.id);
      const label = document.createElement('span');
      label.className = 'tag-block-label';
      label.textContent = tag.name;
      chip.appendChild(label);
      if (state.editMode && source === 'prompt') {
        const deleteBtn = document.createElement('button');
        deleteBtn.type = 'button';
        deleteBtn.className = 'tag-delete-btn';
        deleteBtn.textContent = 'x';
        deleteBtn.title = `Delete prompt tag ${tag.name}`;
        deleteBtn.addEventListener('click', async (event) => {
          event.preventDefault();
          event.stopPropagation();
          await deletePromptTag(tag);
        });
        chip.appendChild(deleteBtn);
      }
      if (mappedDanbooruTag) {
        const mappedBadge = document.createElement('span');
        mappedBadge.className = 'tag-mapped-badge';
        mappedBadge.textContent = 'M';
        mappedBadge.title = `Mapped to Danbooru tag: ${mappedDanbooruTag.name}`;
        chip.appendChild(mappedBadge);
      }
      if (shouldDisplayTagUsageCount(usageCount)) {
        const compactUsageCount = formatCompactBadgeCount(usageCount);
        const usageTitle = getTagUsageCountTitle(tag, state.selectedTagScope, usageCount);
        let badge;
        if (uiKit?.createCountBadge) {
          badge = uiKit.createCountBadge(usageCount, {
            badgeClass: 'tag-count-badge',
            titleFn: () => usageTitle,
          });
          badge.textContent = compactUsageCount;
        } else {
          badge = document.createElement('span');
          badge.className = 'tag-count-badge';
          badge.textContent = compactUsageCount;
          badge.title = usageTitle;
        }
        chip.appendChild(badge);
      }
      chip.draggable = true;
      chip.title = `${tag.source} | ${tag.scope}${usageCount != null ? ` | ${getTagUsageCountTitle(tag, state.selectedTagScope, usageCount)}` : ''}${state.multiSelectEnabled ? ' | Click: include → exclude → remove' : ''}`;
      chip.addEventListener('click', (event) => {
        event.stopPropagation();

        if (state.multiSelectEnabled) {
          // Multi-select three-state cycling: null → include → exclude → null
          const currentMode = state.selectedTagModes.get(tag.id) || null;
          if (currentMode === null) {
            state.selectedTagModes.set(tag.id, 'include');
            chip.classList.add('mode-include');
            chip.classList.remove('mode-exclude');
          } else if (currentMode === 'include') {
            state.selectedTagModes.set(tag.id, 'exclude');
            chip.classList.remove('mode-include');
            chip.classList.add('mode-exclude');
          } else {
            state.selectedTagModes.delete(tag.id);
            chip.classList.remove('mode-include', 'mode-exclude');
          }

          // Notify gallery filter.
          notifyGalleryTagFilter();
          return;
        }

        // Single-select mode (original behavior)
        // Immediately update DOM for instant visual feedback without re-rendering.
        {
          // Remove "active" class from previously selected chip if any.
          if (state.selectedTagId != null) {
            const prevChip = tagBoard.querySelector(`.tag-block[data-tag-id="${CSS.escape(String(state.selectedTagId))}"]`);
            if (prevChip instanceof HTMLElement) {
              prevChip.classList.remove('active');
            }
          }

          // Update state and add "active" class to newly selected chip.
          state.selectedTagId = state.selectedTagId === tag.id ? null : tag.id;
          if (state.selectedTagId === tag.id) {
            const newChip = tagBoard.querySelector(`.tag-block[data-tag-id="${CSS.escape(String(state.selectedTagId))}"]`);
            if (newChip instanceof HTMLElement) {
              newChip.classList.add('active');
            }
          }
        }

        // Update tag details pane (fast, only affects details section).
        renderTagDetails();

        // Notify gallery filter and gallery tags state.
        notifyGalleryTagFilter();

        // Load tag details and reveal hierarchy in background (no blocking).
        if (state.selectedTagId === tag.id) {
          if (state.tagDetailsFetchedIds.has(tag.id)) {
            // Already fetched — re-render details immediately, then reveal concept
            // hierarchy on the next frame so chip activation can paint first.
            renderTagDetails();
            const conceptId = primaryAssociatedConceptIdForTag(tag);
            if (conceptId != null) {
              window.requestAnimationFrame(() => {
                if (state.selectedTagId !== tag.id) {
                  return;
                }
                selectConceptInHierarchy(conceptId, 0);
                state.openAssociationConceptId = conceptId;
              });
            }
          } else {
            // Show loading state immediately, then fetch.
            state.tagDetailsLoading = true;
            renderTagDetails();
            loadTagDetails(tag).then(() => {
              const selectedId = state.selectedTagId;
              state.tagDetailsFetchedIds.add(tag.id);
              state.tagDetailsLoading = false;
              // Only update if this tag is still selected.
              if (selectedId === tag.id) {
                renderTagDetails();
                const conceptId = primaryAssociatedConceptIdForTag(tag);
                if (conceptId != null) {
                  window.requestAnimationFrame(() => {
                    if (state.selectedTagId !== tag.id) {
                      return;
                    }
                    selectConceptInHierarchy(conceptId, 0);
                    state.openAssociationConceptId = conceptId;
                  });
                }
              }
            });
          }
        } else {
          state.tagDetailsLoading = false;
        }
      });
      chip.addEventListener('dragstart', (event) => {
        beginTagDrag(tag.id);
        state.dragHoverConceptId = null;
        state.dragHoverConceptLevel = null;
        state.tagDropHandled = false;
        chip.classList.add('dragging');
        try {
          const ghost = createTagDragGhost(tag);
          state.dragTagGhostEl = ghost;
          event.dataTransfer.setData('application/x-atelier-tag-id', String(tag.id));
          event.dataTransfer.setData('application/x-atelier-tag-source', String(tag.source));
          event.dataTransfer.setData('application/x-atelier-tag-name', String(tag.name));
          event.dataTransfer.setData('text/plain', tag.name);
          event.dataTransfer.effectAllowed = 'copyMove';
          if (event.dataTransfer.setDragImage) {
            const rect = ghost.getBoundingClientRect();
            event.dataTransfer.setDragImage(ghost, Math.min(20, rect.width / 2), rect.height / 2);
          }
        } catch {
          // Ignore browser differences during prototype mode.
        }
      });
      chip.addEventListener('dragend', async () => {
        if (!state.tagDropHandled && state.dragHoverConceptId != null && isActiveConceptHoverTarget()) {
          const hoveredConcept = findConceptById(state.dragHoverConceptId);
          const hoveredLevel = Number(state.dragHoverConceptLevel);
          const draggedTagId = state.dragTagId || state.lastDraggedTagId;
          if (hoveredConcept && draggedTagId) {
            await handleTagDropOnConcept(
              hoveredConcept,
              Number.isInteger(hoveredLevel) ? hoveredLevel : 0,
              draggedTagId,
              state.dragTagAlternateMode,
            );
          }
        }
        endTagDrag({ defer: false });
        chip.classList.remove('dragging');
        clearTagDragGhost();
        document.querySelectorAll('.new-block.drag-over').forEach((el) => el.classList.remove('drag-over'));
        document.querySelectorAll('.concept-block.drag-over').forEach((el) => el.classList.remove('drag-over'));
        document.querySelectorAll('.concept-block.drag-over-alt').forEach((el) => el.classList.remove('drag-over-alt'));
      });
      return chip;
    };

    if (!tags.length) {
      const empty = document.createElement('div');
      empty.className = 'tag-empty';
      empty.textContent = state.tagSearchQuery
        ? 'No tags match current scope/search.'
        : 'No tags for this source and scope.';
      pool.appendChild(empty);
    } else {
      const initialVisibleCount = 700;
      const growthStep = 1200;
      const frameBatchSize = 260;
      let renderedCount = 0;
      let desiredCount = 0;
      let pumpQueued = false;

      const selectedTagIndex = state.selectedTagId == null
        ? -1
        : tags.findIndex((tag) => tag.id === state.selectedTagId);

      const queuePump = () => {
        if (pumpQueued || renderToken !== state.tagRenderTokenBySource[source]) {
          return;
        }
        pumpQueued = true;
        window.requestAnimationFrame(() => {
          pumpQueued = false;
          if (renderToken !== state.tagRenderTokenBySource[source] || renderedCount >= desiredCount || renderedCount >= tags.length) {
            return;
          }

          const endIndex = Math.min(renderedCount + frameBatchSize, desiredCount, tags.length);
          const fragment = document.createDocumentFragment();
          for (let index = renderedCount; index < endIndex; index += 1) {
            fragment.appendChild(createTagChip(tags[index]));
          }
          pool.appendChild(fragment);
          renderedCount = endIndex;

          if (
            Number.isFinite(savedScrollTop)
            && savedScrollTop > 0
            && pool.scrollHeight < savedScrollTop + pool.clientHeight + 900
            && desiredCount < tags.length
          ) {
            desiredCount = Math.min(tags.length, desiredCount + growthStep);
          }

          if (renderedCount < desiredCount && renderedCount < tags.length) {
            queuePump();
          }
        });
      };

      requestMoreTags = (targetCount = renderedCount + growthStep) => {
        if (renderToken !== state.tagRenderTokenBySource[source]) {
          return;
        }
        desiredCount = Math.min(tags.length, Math.max(desiredCount, targetCount));
        queuePump();
      };

      const selectedTarget = selectedTagIndex >= 0
        ? selectedTagIndex + 180
        : 0;
      requestMoreTags(Math.max(initialVisibleCount, selectedTarget));
    }

    // Allow concept chips to be dropped on the user tag pane to create a user tag.
    if (source === 'user') {
      pool.addEventListener('dragover', (event) => {
        if (state.dragConceptId == null) return;
        event.preventDefault();
        pool.classList.add('drag-over');
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
      });

      pool.addEventListener('dragleave', (event) => {
        if (!pool.contains(event.relatedTarget)) {
          pool.classList.remove('drag-over');
        }
      });

      pool.addEventListener('drop', (event) => {
        event.preventDefault();
        pool.classList.remove('drag-over');
        const conceptId = state.dragConceptId;
        if (conceptId == null) return;
        const concept = findConceptById(conceptId);
        if (!concept) { showStatus(`Concept #${conceptId} not found.`, 'warn'); return; }
        const tagName = concept.name.trim();
        if (!tagName) return;
        const scope = state.selectedTagScope || 'all';
        const created = createTagInSource(tagName, 'user', scope);
        if (created) {
          showStatus(`Added user tag "${tagName}" from concept #${conceptId}`, 'success');
          // Select the newly created tag so it's immediately visible.
          state.selectedTagId = created.id;
          render();
        } else {
          showStatus(`User tag "${tagName}" already exists.`, 'info');
        }
      });
    }

    pane.appendChild(pool);

    return pane;
  }

  function restoreTagPaneScrollState() {
    ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
      const pool = tagBoard.querySelector(`.tag-pool[data-source="${source}"]`);
      if (!(pool instanceof HTMLElement)) {
        return;
      }
      const savedScrollTop = Number(state.tagPaneScrollTopBySource[source]);
      if (Number.isFinite(savedScrollTop) && savedScrollTop >= 0) {
        pool.scrollTop = savedScrollTop;
      }
    });

    if (!state.selectedTagId) {
      return;
    }
    const activeChip = tagBoard.querySelector(`.tag-block[data-tag-id="${CSS.escape(String(state.selectedTagId))}"]`);
    if (activeChip instanceof HTMLElement) {
      activeChip.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      const pool = activeChip.closest('.tag-pool');
      const source = pool instanceof HTMLElement ? pool.dataset.source : null;
      if (source) {
        state.tagPaneScrollTopBySource[source] = pool.scrollTop;
      }
    }
  }

  function renderTagBoard() {
    tagBoard.querySelectorAll('.tag-pool').forEach((pool) => {
      if (!(pool instanceof HTMLElement)) {
        return;
      }
      const source = pool.dataset.source;
      if (!source) {
        return;
      }
      state.tagPaneScrollTopBySource[source] = pool.scrollTop;
    });
    tagBoard.innerHTML = '';
    ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
      state.tagRenderTokenBySource[source] = (state.tagRenderTokenBySource[source] || 0) + 1;
      tagBoard.appendChild(renderTagPane(source, state.tagRenderTokenBySource[source]));
    });
    restoreTagPaneScrollState();
  }

  function renderNewPlaceholder(parentKey, host, level) {
    const wrap = document.createElement('button');
    wrap.className = 'new-block';
    wrap.type = 'button';
    wrap.textContent = '+new';

    host.appendChild(wrap);

    const mountInput = () => {
      wrap.disabled = true;
      wrap.style.display = 'none';

      const input = document.createElement('input');
      input.className = 'new-input';
      input.type = 'text';
      input.placeholder = 'Name concept and press Enter';
      host.insertBefore(input, wrap);
      input.focus();

      const clearInput = () => {
        input.remove();
        wrap.style.display = '';
        wrap.disabled = false;
      };

      input.addEventListener('keydown', async (event) => {
        if (event.key === 'Enter') {
          const creation = await createConcept(input.value, parentKey);
          if (creation.concept) {
            clearInput();
            if (creation.existed) {
              selectConceptInHierarchy(creation.concept.id, level);
            } else {
              selectConcept(level, creation.concept.id);
            }
            return;
          }
          return;
        }
        if (event.key === 'Escape') {
          clearInput();
        }
      });

      input.addEventListener('blur', () => {
        if (!input.value.trim()) {
          clearInput();
        }
      });
    };

    wrap.addEventListener('click', () => {
      const existing = host.querySelector('.new-input');
      if (existing) return;
      mountInput();
    });

    wrap.addEventListener('dragenter', (event) => {
      const draggedTagId = getDraggedTagId(event);
      if (!draggedTagId) return;
      event.preventDefault();
      wrap.classList.add('drag-over');
    });

    wrap.addEventListener('dragover', (event) => {
      const draggedTagId = getDraggedTagId(event);
      if (!draggedTagId) {
        return;
      }
      event.preventDefault();
      wrap.classList.add('drag-over');
    });

    wrap.addEventListener('dragleave', (event) => {
      if (!wrap.contains(/** @type {Node|null} */ (event.relatedTarget))) {
        wrap.classList.remove('drag-over');
      }
    });

    wrap.addEventListener('drop', async (event) => {
      const draggedTagId = getDraggedTagId(event);
      if (!draggedTagId) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      wrap.classList.remove('drag-over');
      const tag = findTagById(draggedTagId);
      const concept = await createOrSelectConceptForTag(parentKey, level, tag);
      if (concept && tag) {
        associateTagToConcept(tag.id, concept.id);
      }
      state.tagDropHandled = true;
      endTagDrag({ defer: false });
      render();
    });
  }

  function renderPane(level, getDescendantCount) {
    const pane = document.createElement('section');
    pane.className = 'pane';

    const title = document.createElement('h2');
    title.textContent = paneTitleForLevel(level);

    const body = document.createElement('div');
    body.className = 'pane-body';
    body.addEventListener('click', (event) => {
      // Clicking empty pane space clears selection for that pane and anything to the right.
      if (event.target === body && state.selectedByLevel[level] != null) {
        clearSelection(level);
      }
    });

    body.addEventListener('dragenter', (event) => {
      // Only activate when entering the pane background directly (not a child concept block).
      if (event.target !== body) return;
      const draggedTagId = getDraggedTagId(event);
      if (draggedTagId) {
        event.preventDefault();
        body.classList.add('drag-over');
        return;
      }
      if (state.dragConceptId == null) return;
      const verdict = canDropConceptIntoPane(state.dragConceptId, level);
      if (!verdict.ok) return;
      event.preventDefault();
      body.classList.add('drag-over');
    });

    body.addEventListener('dragover', (event) => {
      // Let child targets (+new, concept buttons) own tag drag interactions.
      if (event.target !== body) {
        return;
      }
      if (state.dragTagId || getDraggedTagId(event)) {
        event.preventDefault();
        body.classList.add('drag-over');
        return;
      }
      if (state.dragConceptId == null) {
        return;
      }
      const verdict = canDropConceptIntoPane(state.dragConceptId, level);
      if (!verdict.ok) {
        return;
      }
      event.preventDefault();
      body.classList.add('drag-over');
    });

    body.addEventListener('dragleave', () => {
      body.classList.remove('drag-over');
    });

    body.addEventListener('drop', async (event) => {
      const draggedTagId = getDraggedTagId(event);
      if (draggedTagId) {
        const conceptTarget = event.target instanceof Element
          ? event.target.closest('.concept-block')
          : null;
        if (conceptTarget) {
          event.preventDefault();
          event.stopPropagation();
          const targetConcept = findConceptById(Number(conceptTarget.dataset.conceptId));
          const targetLevel = Number(conceptTarget.dataset.level);
          await handleTagDropOnConcept(
            targetConcept,
            Number.isInteger(targetLevel) ? targetLevel : level,
            draggedTagId,
            state.dragTagAlternateMode || isAlternateTagDropMode(event),
          );
          state.tagDropHandled = true;
          endTagDrag({ defer: false });
          body.classList.remove('drag-over');
          return;
        }

        // If a child element handled the drop, do not run pane-level fallback.
        if (event.target !== body) {
          return;
        }

        // Only process the pane-level drop if the drag was actually over this pane.
        if (!body.classList.contains('drag-over')) {
          return;
        }

        event.preventDefault();
        const tag = findTagById(draggedTagId);
        const dropParentKey = getParentKey(level);
        createOrSelectConceptForTag(dropParentKey, level, tag).then((concept) => {
          if (concept && tag) {
            associateTagToConcept(tag.id, concept.id);
            refreshAfterTagAssociation(tag.id);
          }
          state.tagDropHandled = true;
          endTagDrag({ defer: false });
        });
        body.classList.remove('drag-over');
        return;
      }

      event.preventDefault();
      body.classList.remove('drag-over');
      if (state.dragConceptId == null) {
        return;
      }
      moveConceptToPane(state.dragConceptId, level);
    });

    const parentKey = getParentKey(level);
    if (parentKey == null) {
      const info = document.createElement('p');
      info.className = 'pane-empty';
      info.textContent = 'Select a concept to the left to continue.';
      body.appendChild(info);
      pane.append(title, body);
      return pane;
    }

    const concepts = sortedConceptsForParent(parentKey);
    concepts.forEach((concept) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'concept-block';
      button.dataset.conceptId = String(concept.id);
      button.dataset.level = String(level);
      button.draggable = true;
      if (state.selectedByLevel[level] === concept.id) {
        button.classList.add('active');
      }
      const name = document.createElement('span');
      name.className = 'concept-label';
      name.textContent = concept.name;

      button.appendChild(name);

      const descendantCount = Number(getDescendantCount(Number(concept.id)) || 0);
      if (descendantCount > 0) {
        let descendantBadge;
        if (uiKit?.createCountBadge) {
          descendantBadge = uiKit.createCountBadge(descendantCount, {
            badgeClass: 'concept-desc-count',
            titleFn: (n) => `${n} descendant concept${n === 1 ? '' : 's'}`,
          });
        } else {
          descendantBadge = document.createElement('span');
          descendantBadge.className = 'concept-desc-count';
          descendantBadge.textContent = String(descendantCount);
          descendantBadge.title = `${descendantCount} descendant concept${descendantCount === 1 ? '' : 's'}`;
        }
        button.appendChild(descendantBadge);
      }

      const associatedTagIds = associatedTagIdsForConcept(concept.id);
      if (associatedTagIds.length) {
        let badge;
        if (uiKit?.createCountBadge) {
          badge = uiKit.createCountBadge(associatedTagIds.length, {
            badgeClass: 'concept-assoc-count',
            elementType: 'button',
            titleFn: (n) => `${n} associated tag${n === 1 ? '' : 's'}`,
          });
        } else {
          badge = document.createElement('button');
          badge.type = 'button';
          badge.className = 'concept-assoc-count';
          badge.textContent = String(associatedTagIds.length);
          badge.title = `${associatedTagIds.length} associated tag${associatedTagIds.length === 1 ? '' : 's'}`;
        }
        badge.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          state.openAssociationConceptId =
            state.openAssociationConceptId === concept.id ? null : concept.id;
          renderConceptBoard();
        });
        button.appendChild(badge);

        const isPopoverOpen = state.openAssociationConceptId === concept.id;
        if (isPopoverOpen) {
          const popover = document.createElement('div');
          popover.className = 'concept-assoc-popover';
          button.classList.add('assoc-open');
          popover.classList.add('is-open');
          popover.addEventListener('click', (event) => {
            event.stopPropagation();
          });

          const popoverTitle = document.createElement('div');
          popoverTitle.className = 'concept-assoc-title';
          popoverTitle.textContent = 'Associated tags';
          popover.appendChild(popoverTitle);

          const chipList = document.createElement('div');
          chipList.className = 'concept-assoc-list';

          associatedTagIds
            .map((tagId) => findTagById(tagId))
            .filter(Boolean)
            .forEach((tag) => {
              const chip = document.createElement('span');
              chip.className = `concept-assoc-chip source-${tag.source}`;

              const label = document.createElement('span');
              label.textContent = tag.name;

              const remove = document.createElement('button');
              remove.type = 'button';
              remove.className = 'concept-assoc-remove';
              remove.textContent = 'x';
              remove.title = `Remove ${tag.name} from ${concept.name}`;
              remove.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                disassociateTagFromConcept(tag.id, concept.id);
                renderConceptBoard();
              });

              chip.append(label, remove);
              chipList.appendChild(chip);
            });

          popover.appendChild(chipList);
          button.appendChild(popover);
        }
      }
      button.addEventListener('click', () => {
        selectConcept(level, concept.id);
      });
      button.addEventListener('dblclick', (event) => {
        event.preventDefault();
        event.stopPropagation();
        beginRenameConcept(button, concept);
      });
      button.addEventListener('dragstart', (event) => {
        if (button.dataset.renaming === '1') {
          event.preventDefault();
          return;
        }
        state.dragConceptId = concept.id;
        button.classList.add('dragging');
        try {
          event.dataTransfer.setData('text/plain', String(concept.id));
          event.dataTransfer.effectAllowed = 'move';
        } catch {
          // ignore browser differences in drag data support
        }
      });
      button.addEventListener('dragend', () => {
        state.dragConceptId = null;
        button.classList.remove('dragging');
        document.querySelectorAll('.pane-body.drag-over').forEach((el) => el.classList.remove('drag-over'));
        document.querySelectorAll('.concept-block.drag-over').forEach((el) => el.classList.remove('drag-over'));
        document.querySelectorAll('.concept-block.drag-over-alt').forEach((el) => el.classList.remove('drag-over-alt'));
      });
      button.addEventListener('dragenter', (event) => {
        const draggedTagId = getDraggedTagId(event);
        if (draggedTagId) {
          state.dragHoverConceptId = concept.id;
          state.dragHoverConceptLevel = level;
          event.preventDefault();
          event.stopPropagation();
          button.classList.add('drag-over');
          return;
        }
        if (state.dragConceptId == null) return;
        const verdict = canDropConceptUnderParentKey(state.dragConceptId, String(concept.id));
        if (!verdict.ok) return;
        event.preventDefault();
        event.stopPropagation();
        button.classList.add('drag-over');
      });
      button.addEventListener('dragover', (event) => {
        const draggedTagId = getDraggedTagId(event);
        if (draggedTagId) {
          const alternateTagDropMode = isAlternateTagDropMode(event);
          state.dragTagAlternateMode = alternateTagDropMode;
          state.dragHoverConceptId = concept.id;
          state.dragHoverConceptLevel = level;
          event.preventDefault();
          event.stopPropagation();
          button.classList.add('drag-over');
          button.classList.toggle('drag-over-alt', alternateTagDropMode);
          try {
            if (event.dataTransfer) {
              event.dataTransfer.dropEffect = alternateTagDropMode ? 'move' : 'copy';
            }
          } catch {
            // ignore browser differences
          }
          return;
        }
        if (state.dragConceptId == null) {
          return;
        }
        const verdict = canDropConceptUnderParentKey(state.dragConceptId, String(concept.id));
        if (!verdict.ok) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        button.classList.add('drag-over');
      });
      button.addEventListener('dragleave', (event) => {
        if (button.contains(/** @type {Node|null} */ (event.relatedTarget))) return;
        button.classList.remove('drag-over');
        button.classList.remove('drag-over-alt');
        if (state.dragHoverConceptId === concept.id) {
          state.dragHoverConceptId = null;
          state.dragHoverConceptLevel = null;
          state.dragTagAlternateMode = false;
        }
      });
      button.addEventListener('drop', async (event) => {
        const alternateTagDropMode = state.dragTagAlternateMode || isAlternateTagDropMode(event);
        event.preventDefault();
        event.stopPropagation();
        button.classList.remove('drag-over');
        button.classList.remove('drag-over-alt');
        const draggedTagId = getDraggedTagId(event);
        if (draggedTagId) {
          await handleTagDropOnConcept(concept, level, draggedTagId, alternateTagDropMode);
          state.tagDropHandled = true;
          endTagDrag({ defer: false });
          return;
        }
        if (state.dragConceptId == null) {
          return;
        }
        moveConceptUnderParent(state.dragConceptId, concept.id);
      });
      body.appendChild(button);
    });

    renderNewPlaceholder(parentKey, body, level);

    pane.append(title, body);
    return pane;
  }

  function renderConceptBoard() {
    conceptBoard.innerHTML = '';
    const paneCount = conceptPaneCount();

    const memo = new Map();
    const getDescendantCount = (conceptId) => {
      const id = Number(conceptId);
      if (!Number.isInteger(id)) return 0;
      if (memo.has(id)) return memo.get(id);

      const children = state.conceptsByParent[String(id)] || [];
      let total = 0;
      children.forEach((child) => {
        const childId = Number(child.id);
        total += 1;
        total += getDescendantCount(childId);
      });
      memo.set(id, total);
      return total;
    };

    for (let level = 0; level < paneCount; level += 1) {
      conceptBoard.appendChild(renderPane(level, getDescendantCount));
    }
  }

  function render() {
    renderScopeControls();
    renderTagBoard();
    renderTagDetails();
    renderConceptBoard();
    refreshConceptSearchOptions();
  }

  if (trashZone) {
    trashZone.addEventListener('dragover', (event) => {
      if (state.dragConceptId == null) return;
      event.preventDefault();
      trashZone.classList.add('drag-over');
    });

    trashZone.addEventListener('dragleave', () => {
      trashZone.classList.remove('drag-over');
    });

    trashZone.addEventListener('drop', async (event) => {
      event.preventDefault();
      trashZone.classList.remove('drag-over');
      if (state.dragConceptId == null) return;
      await deleteConceptBranch(state.dragConceptId);
      state.dragConceptId = null;
    });
  }

  document.addEventListener('click', () => {
    if (state.openAssociationConceptId == null) {
      return;
    }
    state.openAssociationConceptId = null;
    render();
  });

  window.addEventListener('message', (event) => {
    if (event.origin !== window.location.origin) {
      return;
    }
    if (!event.data || typeof event.data.type !== 'string') {
      return;
    }
    if (event.data.type === 'atelier:selected-image-tags') {
      setSelectedImageTags(event.data.payload || null);
    }
  });

  async function init() {
    initConceptSearchControls();
    initConceptSortControls();

    if (countSingleTagsToggle instanceof HTMLInputElement) {
      const storedCountSingleTags = preferences?.readStoredBool
        ? preferences.readStoredBool(TREE_STORAGE_KEYS.countSingleTags, false)
        : false;
      state.countSingleTags = Boolean(storedCountSingleTags);
      countSingleTagsToggle.checked = state.countSingleTags;
      countSingleTagsToggle.addEventListener('change', () => {
        state.countSingleTags = Boolean(countSingleTagsToggle.checked);
        if (preferences?.writeStoredBool) {
          preferences.writeStoredBool(TREE_STORAGE_KEYS.countSingleTags, state.countSingleTags);
        }
        renderTagBoard();
      });
    }

    if (editModeToggle instanceof HTMLInputElement) {
      const storedEditMode = preferences?.readStoredBool
        ? preferences.readStoredBool(TREE_STORAGE_KEYS.editMode, false)
        : false;
      state.editMode = Boolean(storedEditMode);
      editModeToggle.checked = state.editMode;
      editModeToggle.addEventListener('change', () => {
        state.editMode = Boolean(editModeToggle.checked);
        if (preferences?.writeStoredBool) {
          preferences.writeStoredBool(TREE_STORAGE_KEYS.editMode, state.editMode);
        }
        renderTagBoard();
      });
    }

    await loadTaxonomyState();
    render();
  }

  init();
})();
