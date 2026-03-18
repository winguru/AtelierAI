(() => {
  function uniqueSortedText(values) {
    const bucket = new Map();
    (Array.isArray(values) ? values : []).forEach((value) => {
      const text = String(value || '').trim();
      if (!text) {
        return;
      }
      const key = text.toLowerCase();
      if (!bucket.has(key)) {
        bucket.set(key, text);
      }
    });
    return Array.from(bucket.values()).sort((left, right) => left.localeCompare(right, undefined, { sensitivity: 'base' }));
  }

  function populateDatalist(datalist, values) {
    if (!(datalist instanceof HTMLDataListElement)) {
      return;
    }
    datalist.innerHTML = '';
    uniqueSortedText(values).forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      datalist.append(option);
    });
  }

  function renderActionChips(container, values, options = {}) {
    if (!(container instanceof HTMLElement)) {
      return;
    }
    const {
      chipClass = '',
      titlePrefix = '',
      onClick,
    } = options;

    container.innerHTML = '';
    const list = Array.isArray(values) ? values : [];
    list.forEach((value) => {
      const text = String(value || '').trim();
      if (!text) {
        return;
      }
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = chipClass;
      chip.title = titlePrefix ? `${titlePrefix}${text}` : text;
      chip.textContent = text;
      if (typeof onClick === 'function') {
        chip.addEventListener('click', () => onClick(text));
      }
      container.append(chip);
    });
  }

  function renderRemovableChips(container, values, options = {}) {
    if (!(container instanceof HTMLElement)) {
      return;
    }
    const {
      chipClass = 'tag-chip',
      labelClass = '',
      removeClass = 'tag-chip-remove',
      removeLabel = 'x',
      removeTitlePrefix = 'Remove ',
      onRemove,
    } = options;

    container.innerHTML = '';
    const list = Array.isArray(values) ? values : [];
    list.forEach((value, index) => {
      const text = String(value || '').trim();
      if (!text) {
        return;
      }
      const chip = document.createElement('span');
      chip.className = chipClass;

      const label = document.createElement('span');
      if (labelClass) {
        label.className = labelClass;
      }
      label.textContent = text;

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = removeClass;
      remove.textContent = removeLabel;
      remove.title = `${removeTitlePrefix}${text}`;
      if (typeof onRemove === 'function') {
        remove.addEventListener('click', () => onRemove(text, index));
      }

      chip.append(label, remove);
      container.append(chip);
    });
  }

  function renderSuggestionList(container, items, options = {}) {
    if (!(container instanceof HTMLElement)) {
      return 0;
    }
    const {
      itemClass = 'concept-search-suggestion',
      onSelect,
      onPointerDown,
    } = options;

    container.innerHTML = '';
    const list = Array.isArray(items) ? items : [];
    list.forEach((item) => {
      const text = String(item || '').trim();
      if (!text) {
        return;
      }
      const button = document.createElement('button');
      button.type = 'button';
      button.className = itemClass;
      button.textContent = text;
      button.addEventListener('mousedown', (event) => {
        if (typeof onPointerDown === 'function') {
          onPointerDown(event, text);
          return;
        }
        event.preventDefault();
      });
      if (typeof onSelect === 'function') {
        button.addEventListener('click', () => onSelect(text));
      }
      container.append(button);
    });
    return container.children.length;
  }

  function createTabbedWorkspace({
    tabs,
    activeTabId,
    ariaLabel,
    onTabChange,
    onRender,
  }) {
    if (!Array.isArray(tabs) || tabs.length === 0) {
      return null;
    }

    let currentActiveTabId = activeTabId;
    const availableTabIds = new Set(tabs.map((tab) => tab.id));
    if (!availableTabIds.has(currentActiveTabId)) {
      currentActiveTabId = tabs[0].id;
    }

    const workspace = document.createElement('section');
    workspace.className = 'folder-workspace';

    const tabRow = document.createElement('div');
    tabRow.className = 'folder-tab-row';
    tabRow.setAttribute('role', 'tablist');
    tabRow.setAttribute('aria-label', ariaLabel || 'Tabbed workspace');

    const body = document.createElement('div');
    body.className = 'folder-body';

    function renderActiveTab() {
      body.innerHTML = '';
      const activeTab = tabs.find((tab) => tab.id === currentActiveTabId) || tabs[0];
      tabs.forEach((tab, index) => {
        const button = tabRow.children[index];
        if (!(button instanceof HTMLButtonElement)) {
          return;
        }
        const active = tab.id === activeTab.id;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
      });
      body.append(activeTab.render());
      if (typeof onRender === 'function') {
        onRender(activeTab.id);
      }
    }

    tabs.forEach((tab) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'folder-tab';
      button.textContent = tab.label;
      button.setAttribute('role', 'tab');
      button.addEventListener('click', () => {
        if (currentActiveTabId === tab.id) {
          return;
        }
        currentActiveTabId = tab.id;
        if (typeof onTabChange === 'function') {
          onTabChange(tab.id);
        }
        renderActiveTab();
      });
      tabRow.append(button);
    });

    workspace.append(tabRow, body);
    renderActiveTab();

    return {
      root: workspace,
      getActiveTabId: () => currentActiveTabId,
      setActiveTabId: (nextTabId) => {
        if (!availableTabIds.has(nextTabId)) {
          return;
        }
        currentActiveTabId = nextTabId;
        renderActiveTab();
      },
      renderActiveTab,
    };
  }

  /**
   * Creates a single count badge element (span or button).
   *
   * @param {number} count - The numeric value to display.
   * @param {object} [options]
   * @param {string} [options.badgeClass='count-badge'] - CSS class name(s).
   * @param {'span'|'button'} [options.elementType='span'] - Tag to create.
   * @param {function(number): string} [options.titleFn] - Returns the title attribute.
   * @returns {HTMLElement}
   */
  function createCountBadge(count, options = {}) {
    const {
      badgeClass = 'count-badge',
      elementType = 'span',
      titleFn,
    } = options;
    const el = document.createElement(elementType);
    if (elementType === 'button') {
      el.type = 'button';
    }
    el.className = badgeClass;
    el.textContent = String(count);
    if (typeof titleFn === 'function') {
      el.title = titleFn(count);
    }
    return el;
  }

  /**
   * Mount a reusable click-expand choice control.
   *
   * @param {object} options
   * @param {HTMLElement} options.root
   * @param {HTMLButtonElement} options.currentButton
   * @param {HTMLElement[]} options.optionButtons
   * @param {function(): string} options.getValue
   * @param {function(string): void} options.setValue
   * @param {function(string): void} [options.onChange]
   * @param {function(string): string} [options.formatLabel]
   * @param {string[]} [options.allowedValues]
  * @param {HTMLElement} [options.optionsContainer]
  * @returns {{sync: function(): void, destroy: function(): void, close: function(): void}|null}
   */
  function mountHoverChoiceControl(options = {}) {
    const {
      root,
      currentButton,
      optionButtons,
      getValue,
      setValue,
      onChange,
      formatLabel,
      allowedValues = [],
      optionsContainer,
    } = options;

    if (!(root instanceof HTMLElement) || !(currentButton instanceof HTMLButtonElement)) {
      return null;
    }

    const buttons = Array.isArray(optionButtons)
      ? optionButtons.filter((button) => button instanceof HTMLElement)
      : [];
    const menu = optionsContainer instanceof HTMLElement
      ? optionsContainer
      : root.querySelector('.nsfw-visibility-options');
    const allowed = new Set((Array.isArray(allowedValues) ? allowedValues : []).map((value) => String(value).toLowerCase()));
    const listeners = [];

    const addListener = (target, eventName, handler) => {
      target.addEventListener(eventName, handler);
      listeners.push(() => target.removeEventListener(eventName, handler));
    };

    const closeMenu = () => {
      root.classList.remove('is-open');
      currentButton.setAttribute('aria-expanded', 'false');
      if (menu instanceof HTMLElement) {
        menu.setAttribute('aria-hidden', 'true');
      }
    };

    const setMenuOpen = (open) => {
      root.classList.toggle('is-open', Boolean(open));
      currentButton.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (menu instanceof HTMLElement) {
        menu.setAttribute('aria-hidden', open ? 'false' : 'true');
      }
    };

    const sync = () => {
      const rawValue = typeof getValue === 'function' ? getValue() : '';
      const value = String(rawValue || '').toLowerCase();
      const label = typeof formatLabel === 'function' ? formatLabel(value) : value;
      currentButton.textContent = label;
      buttons.forEach((button) => {
        const candidate = String(button.dataset.nsfwLevel || button.dataset.value || '').toLowerCase();
        const isActive = candidate === value;
        button.classList.toggle('is-active', isActive);
        button.setAttribute('aria-checked', isActive ? 'true' : 'false');
      });
    };

    addListener(currentButton, 'click', (event) => {
      event.preventDefault();
      setMenuOpen(!root.classList.contains('is-open'));
    });

    buttons.forEach((button) => {
      addListener(button, 'click', () => {
        const nextValue = String(button.dataset.nsfwLevel || button.dataset.value || '').toLowerCase();
        if (allowed.size && !allowed.has(nextValue)) {
          return;
        }
        if (typeof setValue === 'function') {
          setValue(nextValue);
        }
        sync();
        closeMenu();
        if (typeof onChange === 'function') {
          onChange(nextValue);
        }
      });
    });

    addListener(document, 'click', (event) => {
      if (!root.contains(event.target)) {
        closeMenu();
      }
    });

    addListener(root, 'keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeMenu();
        currentButton.focus();
      }
    });

    closeMenu();
    sync();

    return {
      sync,
      close: closeMenu,
      destroy: () => {
        while (listeners.length) {
          const unbind = listeners.pop();
          if (typeof unbind === 'function') {
            unbind();
          }
        }
      },
    };
  }

  window.AtelierUi = {
    uniqueSortedText,
    populateDatalist,
    renderActionChips,
    renderRemovableChips,
    renderSuggestionList,
    createTabbedWorkspace,
    createCountBadge,
    mountHoverChoiceControl,
    createStackedFolderWorkspace,
  };

  function applyFolderThemeTokens(element, theme) {
    if (!(element instanceof HTMLElement)) return;
    const folderTabs = window.AtelierFolderTabs;
    if (!folderTabs || typeof folderTabs.getThemeTokens !== 'function') return;

    const tokens = folderTabs.getThemeTokens(theme);
    Object.entries(tokens).forEach(([key, value]) => {
      element.style.setProperty(key, value);
    });
  }

  function createStackedFolderWorkspace({
    tabs,
    activeTabId,
    ariaLabel,
    emptyStateLabel = 'Default',
    colorTheme,
    wrapperClassName,
    stackClassName,
    bodyClassName,
    cssVars,
    onTabChange,
    onTabActivate,
    onRender,
  }) {
    tabs = Array.isArray(tabs) ? tabs : [];

    let currentActiveTabId = activeTabId;
    const availableTabIds = new Set(tabs.map((tab) => tab.id));
    if (tabs.length > 0 && !availableTabIds.has(currentActiveTabId)) {
      currentActiveTabId = tabs[0].id;
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'ftabs-workspace';
    if (colorTheme) {
      wrapper.dataset.theme = colorTheme;
    }
    applyFolderThemeTokens(wrapper, colorTheme);
    if (cssVars && typeof cssVars === 'object') {
      Object.entries(cssVars).forEach(([key, rawValue]) => {
        if (rawValue === undefined || rawValue === null) return;
        const cssVar = key.startsWith('--') ? key : `--${key}`;
        wrapper.style.setProperty(cssVar, String(rawValue));
      });
    }

    const stack = document.createElement('div');
    stack.className = 'ftabs-stack';
    if (stackClassName) {
      stack.classList.add(...String(stackClassName).split(/\s+/).filter(Boolean));
    }
    stack.setAttribute('role', 'tablist');
    stack.setAttribute('aria-label', ariaLabel || 'Tabbed folder');

    const body = document.createElement('div');
    body.className = 'ftab-body';
    if (bodyClassName) {
      body.classList.add(...String(bodyClassName).split(/\s+/).filter(Boolean));
    }

    function updateSeam() {
      const activeButton = stack.querySelector('.ftab.is-active');
      if (!activeButton) return;
      const bodyRect = body.getBoundingClientRect();
      const tabRect = activeButton.getBoundingClientRect();
      if (!bodyRect.width) return;
      const rawLeft = ((tabRect.left - bodyRect.left) / bodyRect.width) * 100;
      const rawWidth = (tabRect.width / bodyRect.width) * 100;
      const left = Math.max(0, Math.min(100, rawLeft));
      const width = Math.max(0, Math.min(100 - left, rawWidth));
      body.style.setProperty('--ftab-active-left', `${left}%`);
      body.style.setProperty('--ftab-active-width', `${width}%`);

      const flushLeft = left < 0.5;
      const flushRight = (left + width) > 99.5;
      const r = 'var(--ftab-body-radius)';
      const tl = flushLeft ? '0' : r;
      const tr = flushRight ? '0' : r;
      body.style.borderRadius = `${tl} ${tr} ${r} ${r}`;
      body.style.setProperty('--ftab-seam-left', flushLeft ? '0px' : 'var(--ftab-body-radius)');
      body.style.setProperty('--ftab-seam-right', flushRight ? '0px' : 'var(--ftab-body-radius)');
    }

    function renderActiveTab() {
      body.innerHTML = '';
      const activeTab = tabs.find((tab) => tab.id === currentActiveTabId) || tabs[0];
      const hasBackTabs = backWrap.children.length > 0;
      const hasFrontTabs = frontWrap.children.length > 0;
      const backLeftBtn = backWrap.firstElementChild;
      const backLeftIndex = backLeftBtn ? tabButtons.indexOf(backLeftBtn) : -1;
      const backLeftTab = backLeftIndex >= 0 ? tabs[backLeftIndex] : null;
      const workspaceTheme = (hasBackTabs && hasFrontTabs)
        ? (backLeftTab?.colorTheme || colorTheme)
        : (activeTab?.colorTheme || colorTheme);
      const bodyTheme = activeTab?.colorTheme || colorTheme;

      applyFolderThemeTokens(wrapper, workspaceTheme);
      applyFolderThemeTokens(body, bodyTheme);
      if (workspaceTheme) {
        wrapper.dataset.theme = workspaceTheme === 'manila' ? '' : workspaceTheme;
      } else {
        delete wrapper.dataset.theme;
      }
      const activeIndex = tabs.indexOf(activeTab);
      const activeButton = tabButtons[activeIndex];
      body.classList.toggle('has-front-active-tab',
        activeButton ? activeButton.classList.contains('is-front-row') : false);
      tabs.forEach((tab, index) => {
        const button = tabButtons[index];
        if (!button) return;
        const active = tab.id === activeTab.id;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
      });
      if (!activeTab) {
        const emptyState = document.createElement('p');
        emptyState.className = 'ftab-placeholder';
        emptyState.textContent = `Content area for "${emptyStateLabel}"`;
        body.append(emptyState);
      }
      if (activeTab && typeof activeTab.render === 'function') {
        body.append(activeTab.render());
      }
      requestAnimationFrame(updateSeam);
      if (activeTab && typeof onRender === 'function') {
        onRender(activeTab.id);
      }
    }

    const rowColumnCounters = new Map();
    const tabButtons = [];
    const backWrap = document.createElement('div');
    backWrap.className = 'ftab-row-wrap is-row-back';
    backWrap.style.marginLeft = 'var(--tab-offset, 20px)';
    const frontWrap = document.createElement('div');
    frontWrap.className = 'ftab-row-wrap is-row-front';

    tabs.forEach((tab) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ftab';
      applyFolderThemeTokens(button, tab.colorTheme || colorTheme);
      const tabRow = Number(tab.row ?? 1);
      const rowKey = String(tabRow);
      const rowColumnIndex = (rowColumnCounters.get(rowKey) || 0) + 1;
      rowColumnCounters.set(rowKey, rowColumnIndex);
      button.textContent = tab.label;
      button.setAttribute('role', 'tab');
      button.style.setProperty('--tab-column', String(rowColumnIndex));
      if (tabRow === 1) {
        button.classList.add('is-back-row');
        backWrap.append(button);
      } else if (tabRow === 2) {
        button.classList.add('is-front-row');
        frontWrap.append(button);
      }
      button.addEventListener('click', () => {
        if (currentActiveTabId === tab.id) return;

        // If a back-row tab is clicked and both rows exist, swap the two rows.
        if (button.classList.contains('is-back-row')
            && backWrap.children.length > 0
            && frontWrap.children.length > 0) {
          swapRows();
        }

        const previousTabId = currentActiveTabId;
        currentActiveTabId = tab.id;
        if (typeof onTabChange === 'function') {
          onTabChange(tab.id);
        }
        if (typeof onTabActivate === 'function') {
          onTabActivate({
            previousTabId,
            nextTabId: tab.id,
            source: 'click',
          });
        }
        renderActiveTab();
      });
      tabButtons.push(button);
    });

    function refreshEdgeClasses() {
      [backWrap, frontWrap].forEach((wrap) => {
        Array.from(wrap.children).forEach((child) => {
          child.classList.remove('is-edge-left', 'is-edge-right');
        });
        if (wrap.firstElementChild) wrap.firstElementChild.classList.add('is-edge-left');
        if (wrap.lastElementChild) wrap.lastElementChild.classList.add('is-edge-right');
      });
    }

    function swapRows() {
      // Toggle row classes on every tab button
      tabButtons.forEach((btn) => {
        const wasBack = btn.classList.contains('is-back-row');
        btn.classList.toggle('is-back-row', !wasBack);
        btn.classList.toggle('is-front-row', wasBack);
      });

      // Move buttons to their new wrapper
      const toBack = [];
      const toFront = [];
      tabButtons.forEach((btn) => {
        if (btn.classList.contains('is-back-row')) toBack.push(btn);
        else if (btn.classList.contains('is-front-row')) toFront.push(btn);
      });
      backWrap.replaceChildren(...toBack);
      frontWrap.replaceChildren(...toFront);

      // Re-assign per-row column indices
      [backWrap, frontWrap].forEach((wrap) => {
        Array.from(wrap.children).forEach((btn, idx) => {
          btn.style.setProperty('--tab-column', String(idx + 1));
        });
      });

      // Swap the inline offset between the two wrappers so it follows the tabs
      const backMargin = backWrap.style.marginLeft;
      const frontMargin = frontWrap.style.marginLeft;
      backWrap.style.marginLeft = frontMargin;
      frontWrap.style.marginLeft = backMargin;

      refreshEdgeClasses();

      // Re-order wrappers in the stack so backWrap is first
      if (stack.firstElementChild !== backWrap) {
        stack.insertBefore(backWrap, frontWrap);
      }
    }

    refreshEdgeClasses();
    const hasBackRow = backWrap.children.length > 0;
    const hasFrontRow = frontWrap.children.length > 0;
    if (hasFrontRow) {
      stack.classList.add('has-front-row');
    }
    if (hasBackRow) stack.append(backWrap);
    if (hasFrontRow) stack.append(frontWrap);

    // When there are no tabs at all, show just the body as a plain pane.
    // When there is no back row, hide the back-strip pseudo.
    if (!hasBackRow && !hasFrontRow) {
      stack.style.display = 'none';
      body.classList.add('is-plain');
      body.style.borderTop = `1px solid var(--ftab-edge)`;
      body.style.borderRadius = `var(--ftab-body-radius)`;
      body.style.marginTop = '0';
    } else if (!hasBackRow) {
      stack.classList.add('no-back-row');
    }

    if (wrapperClassName) {
      wrapper.classList.add(...String(wrapperClassName).split(/\s+/).filter(Boolean));
    }
    wrapper.append(stack, body);
    renderActiveTab();

    return {
      root: wrapper,
      stackEl: stack,
      bodyEl: body,
      getActiveTabId: () => currentActiveTabId,
      setActiveTabId: (nextTabId) => {
        if (!availableTabIds.has(nextTabId)) return;
        const previousTabId = currentActiveTabId;
        currentActiveTabId = nextTabId;
        if (typeof onTabActivate === 'function') {
          onTabActivate({
            previousTabId,
            nextTabId,
            source: 'api',
          });
        }
        renderActiveTab();
      },
      updateSeam,
      renderActiveTab,
    };
  }

  window.AtelierUi.createStackedFolderWorkspace = createStackedFolderWorkspace;
})();
