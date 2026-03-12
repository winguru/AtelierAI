(() => {
  const MAX_PANES = 4;
  const conceptBoard = document.getElementById('concept-board');
  const tagBoard = document.getElementById('tag-board');
  const tagDetailsPanel = document.getElementById('tag-details');
  const scopeControls = document.getElementById('scope-controls');
  const trashZone = document.getElementById('concept-trash');
  if (!conceptBoard || !tagBoard || !tagDetailsPanel || !scopeControls) return;

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
        ? [`https://civitai.com/images/${100000 + idx}`]
        : source === 'danbooru'
          ? [`https://danbooru.donmai.us/posts?tags=${encodeURIComponent(name)}`]
          : source === 'prompt'
            ? [`${name}, best quality, cinematic lighting`]
            : [`User collection note for '${name}'.`];

    return {
      id: `${source}:${idx + 1}`,
      name,
      source,
      scope: idx % 2 === 0 ? 'gallery' : 'image',
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

  // Prototype state is intentionally local and blank-by-default.
  const state = {
    conceptsByParent: {
      root: [],
    },
    selectedByLevel: {},
    nextId: 1,
    dragConceptId: null,
    clickTimerByConcept: {},
    selectedTagId: null,
    selectedTagScope: 'all',
    tags: buildTagSeedData(),
  };

  function findTagById(tagId) {
    if (!tagId) return null;
    return state.tags.find((tag) => tag.id === tagId) || null;
  }

  function parseCsvValues(value) {
    return String(value || '')
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean);
  }

  function parseLineValues(value) {
    return String(value || '')
      .split(/\r?\n/)
      .map((part) => part.trim())
      .filter(Boolean);
  }

  function renderChipEditor({ host, labelText, values, onChange, placeholder, helpTextRight, helpTextBelow }) {
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

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'new-block';
    addButton.textContent = '+new';

    const mountInput = () => {
      addButton.disabled = true;
      addButton.style.display = 'none';

      const input = document.createElement('input');
      input.className = 'new-input';
      input.type = 'text';
      input.placeholder = placeholder;
      chipList.appendChild(input);
      input.focus();

      const reset = () => {
        input.remove();
        addButton.style.display = '';
        addButton.disabled = false;
      };

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          const entry = String(input.value || '').trim();
          if (entry) {
            const next = [...(values || [])];
            if (!next.some((item) => item.toLowerCase() === entry.toLowerCase())) {
              next.push(entry);
              onChange(next);
            }
          }
          reset();
          renderTagDetails();
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
    chipList.appendChild(addButton);
    field.appendChild(chipList);

    host.appendChild(field);
  }

  function renderTagDetails() {
    tagDetailsPanel.innerHTML = '';
    const selected = findTagById(state.selectedTagId);
    if (!selected) {
      const empty = document.createElement('div');
      empty.className = 'tag-empty';
      empty.textContent = 'Select one tag from CivitAI, Danbooru, Prompt, or User to edit its details.';
      tagDetailsPanel.appendChild(empty);
      return;
    }

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
    descriptionField.appendChild(descriptionInput);
    grid.appendChild(descriptionField);

    renderChipEditor({
      host: grid,
      labelText: 'Aliases',
      values: selected.aliases || [],
      onChange: (next) => {
        selected.aliases = next;
      },
      placeholder: 'Add alias and press Enter',
      helpTextBelow: 'Synonymous tags from the same source.',
    });

    renderChipEditor({
      host: grid,
      labelText: 'Implies',
      values: selected.implies || [],
      onChange: (next) => {
        selected.implies = next;
      },
      placeholder: 'Add implied tag and press Enter',
      helpTextBelow: 'Always includes underlying tags.',
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
    examplesInput.addEventListener('input', () => {
      selected.examples = parseLineValues(examplesInput.value);
    });
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

  function createConcept(name, parentKey) {
    const trimmed = String(name || '').trim();
    if (!trimmed) return null;

    const bucket = ensureBucket(parentKey);
    const duplicate = bucket.find((item) => item.name.toLowerCase() === trimmed.toLowerCase());
    if (duplicate) return null;

    const concept = { id: state.nextId++, name: trimmed };
    bucket.push(concept);
    return concept;
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

  function deleteConceptBranch(conceptId) {
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

    for (const parentKey of Object.keys(state.conceptsByParent)) {
      const bucket = state.conceptsByParent[parentKey];
      if (!Array.isArray(bucket)) continue;
      state.conceptsByParent[parentKey] = bucket.filter((item) => !toDelete.has(Number(item.id)));
    }

    for (const id of toDelete) {
      delete state.conceptsByParent[String(id)];
      delete state.clickTimerByConcept[String(id)];
    }

    for (let i = 0; i < MAX_PANES; i += 1) {
      if (toDelete.has(Number(state.selectedByLevel[i]))) {
        delete state.selectedByLevel[i];
      }
    }
    for (let i = 0; i < MAX_PANES; i += 1) {
      const current = state.selectedByLevel[i];
      if (current == null) {
        for (let j = i + 1; j < MAX_PANES; j += 1) {
          delete state.selectedByLevel[j];
        }
        break;
      }
    }

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

    // Clear stale selection references if needed, then redraw.
    for (let i = 0; i < MAX_PANES; i += 1) {
      if (Number(state.selectedByLevel[i]) === Number(conceptId)) {
        clearSelection(i);
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

  function selectConcept(level, conceptId) {
    state.selectedByLevel[level] = conceptId;
    for (let i = level + 1; i < MAX_PANES; i += 1) {
      delete state.selectedByLevel[i];
    }
    render();
  }

  function clearSelection(level) {
    delete state.selectedByLevel[level];
    for (let i = level + 1; i < MAX_PANES; i += 1) {
      delete state.selectedByLevel[i];
    }
    render();
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

    return state.tags
      .filter((tag) => tag.source === source)
      .filter((tag) => (scope === 'all' ? true : tag.scope === scope))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }

  function sourceLabel(source) {
    if (source === 'civitai') return 'CivitAI';
    if (source === 'danbooru') return 'Danbooru';
    if (source === 'prompt') return 'Prompt';
    if (source === 'user') return 'User';
    return source;
  }

  function renderScopeControls() {
    scopeControls.innerHTML = '';
    ['all', 'gallery', 'image', 'none'].forEach((scope) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `tag-filter-btn ${state.selectedTagScope === scope ? 'active' : ''}`;
      btn.textContent = scope.charAt(0).toUpperCase() + scope.slice(1);
      btn.addEventListener('click', () => {
        state.selectedTagScope = scope;
        render();
      });
      scopeControls.appendChild(btn);
    });
  }

  function renderTagPane(source) {
    const pane = document.createElement('section');
    pane.className = 'pane tag-pane';
    pane.addEventListener('click', (event) => {
      if (event.target === pane && state.selectedTagId != null) {
        state.selectedTagId = null;
        render();
      }
    });

    const title = document.createElement('h2');
    title.textContent = `${sourceLabel(source)} Tags`;
    pane.appendChild(title);

    const pool = document.createElement('div');
    pool.className = 'tag-pool';
    pool.addEventListener('click', (event) => {
      if (event.target === pool && state.selectedTagId != null) {
        state.selectedTagId = null;
        render();
      }
    });
    const tags = filteredTagsForSource(source);
    if (!tags.length) {
      const empty = document.createElement('div');
      empty.className = 'tag-empty';
      empty.textContent = 'No tags for this source and scope.';
      pool.appendChild(empty);
    } else {
      tags.forEach((tag) => {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `tag-block ${state.selectedTagId === tag.id ? 'active' : ''}`;
        chip.textContent = tag.name;
        chip.draggable = true;
        chip.title = `${tag.source} | ${tag.scope}`;
        chip.addEventListener('click', () => {
          state.selectedTagId = state.selectedTagId === tag.id ? null : tag.id;
          render();
        });
        chip.addEventListener('dragstart', (event) => {
          try {
            event.dataTransfer.setData('text/plain', tag.name);
            event.dataTransfer.effectAllowed = 'copyMove';
          } catch {
            // Ignore browser differences during prototype mode.
          }
        });
        pool.appendChild(chip);
      });
    }
    pane.appendChild(pool);

    return pane;
  }

  function renderTagBoard() {
    tagBoard.innerHTML = '';
    ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
      tagBoard.appendChild(renderTagPane(source));
    });
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

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          const created = createConcept(input.value, parentKey);
          if (created) {
            clearInput();
            selectConcept(level, created.id);
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
  }

  function renderPane(level) {
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

    body.addEventListener('dragover', (event) => {
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

    body.addEventListener('drop', (event) => {
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

    const concepts = ensureBucket(parentKey);
    concepts.forEach((concept) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'concept-block';
      button.draggable = true;
      if (state.selectedByLevel[level] === concept.id) {
        button.classList.add('active');
      }
      button.textContent = concept.name;
      button.addEventListener('click', (event) => {
        const conceptKey = String(concept.id);
        const existingTimer = state.clickTimerByConcept[conceptKey];

        if (event.detail >= 2) {
          if (existingTimer) {
            window.clearTimeout(existingTimer);
            delete state.clickTimerByConcept[conceptKey];
          }
          event.preventDefault();
          event.stopPropagation();
          beginRenameConcept(button, concept);
          return;
        }

        // Delay single-click selection slightly to allow a second click to trigger rename.
        state.clickTimerByConcept[conceptKey] = window.setTimeout(() => {
          delete state.clickTimerByConcept[conceptKey];
          selectConcept(level, concept.id);
        }, 220);
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
      });
      button.addEventListener('dragover', (event) => {
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
      button.addEventListener('dragleave', () => {
        button.classList.remove('drag-over');
      });
      button.addEventListener('drop', (event) => {
        event.preventDefault();
        event.stopPropagation();
        button.classList.remove('drag-over');
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
    for (let level = 0; level < MAX_PANES; level += 1) {
      conceptBoard.appendChild(renderPane(level));
    }
  }

  function render() {
    renderScopeControls();
    renderTagBoard();
    renderTagDetails();
    renderConceptBoard();
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

    trashZone.addEventListener('drop', (event) => {
      event.preventDefault();
      trashZone.classList.remove('drag-over');
      if (state.dragConceptId == null) return;
      deleteConceptBranch(state.dragConceptId);
      state.dragConceptId = null;
    });
  }

  render();
})();
