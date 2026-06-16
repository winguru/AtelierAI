/* ────────────────────────────────────────────────────────────────────
   ConceptLookup — reusable dual-field widget (ID + Name + autosuggest)

   Usage:
     const widget = ConceptLookup.create({
       container:   "#my-container",       // parent element
       idInputId:   "my-concept-id",       // <input id=…> for the ID
       nameInputId: "my-concept-name",     // <input id=…> for the name
       placeholder: "Concept name…",       // name input placeholder
       apiBase:     "/api/taxonomy",       // (optional) defaults to "/api/taxonomy"
     });

   Public API:
     widget.getId()        → number | NaN
     widget.getName()      → string
     widget.setId(id)      → fetches & populates name
     widget.reset()        → clears both fields
     widget.onSelect(cb)   → callback({id, name}) when user picks a concept
   ──────────────────────────────────────────────────────────────────── */

const ConceptLookup = (() => {
  /* ── tiny helpers ── */
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const esc = s => {
    const d = document.createElement("span");
    d.textContent = s;
    return d.innerHTML;
  };

  /* ── debounce ── */
  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  /* ── fetch JSON (simple, uses same-origin) ── */
  async function fetchJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  /* ══════════════════════════════════════════════════════════════════
     .create(opts) → widget instance
     ══════════════════════════════════════════════════════════════════ */
  function create(opts) {
    const apiBase   = opts.apiBase || "/api/taxonomy";
    const container = typeof opts.container === "string"
      ? $(opts.container) : opts.container;

    /* ── Build DOM ── */
    container.classList.add("concept-lookup");

    const idInput = document.createElement("input");
    idInput.type = "number";
    idInput.id = opts.idInputId;
    idInput.placeholder = "ID";
    idInput.classList.add("cl-id-input");

    const nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.id = opts.nameInputId;
    nameInput.placeholder = opts.placeholder || "Concept name…";
    nameInput.autocomplete = "off";
    nameInput.classList.add("cl-name-input");

    const dropdown = document.createElement("div");
    dropdown.classList.add("cl-dropdown");
    dropdown.setAttribute("role", "listbox");

    container.appendChild(idInput);
    container.appendChild(nameInput);
    container.appendChild(dropdown);

    /* ── State ── */
    let activeIndex = -1;
    let currentResults = [];
    let selectCallbacks = [];

    /* ── Dropdown rendering ── */
    function renderDropdown(results) {
      currentResults = results;
      activeIndex = -1;
      if (!results.length) {
        dropdown.classList.remove("open");
        dropdown.innerHTML = "";
        return;
      }
      dropdown.innerHTML = results.map((r, i) =>
        `<div class="cl-item" data-idx="${i}" role="option">${esc(r.id)} — ${esc(r.canonical_name)}</div>`
      ).join("");
      dropdown.classList.add("open");
    }

    function setActive(idx) {
      const items = dropdown.querySelectorAll(".cl-item");
      items.forEach(el => el.classList.remove("active"));
      if (idx >= 0 && idx < items.length) {
        items[idx].classList.add("active");
        items[idx].scrollIntoView({ block: "nearest" });
      }
      activeIndex = idx;
    }

    function closeDropdown() {
      dropdown.classList.remove("open");
      activeIndex = -1;
      currentResults = [];
    }

    function selectResult(idx) {
      const r = currentResults[idx];
      if (!r) return;
      idInput.value = r.id;
      nameInput.value = r.canonical_name;
      closeDropdown();
      selectCallbacks.forEach(cb => cb({ id: r.id, name: r.canonical_name }));
    }

    /* ── Name → search (debounced) ── */
    const searchByName = debounce(async (query) => {
      if (!query || query.length < 1) { closeDropdown(); return; }
      try {
        const data = await fetchJSON(`${apiBase}/concept-lookup?q=${encodeURIComponent(query)}&limit=20`);
        renderDropdown(data.results || []);
      } catch {
        closeDropdown();
      }
    }, 200);

    /* ── ID → fetch name ── */
    async function lookupById(id) {
      if (!id || isNaN(id)) return;
      try {
        const profile = await fetchJSON(`${apiBase}/concepts/${id}/profile`);
        nameInput.value = profile.canonical_name || "";
      } catch {
        nameInput.value = "";
      }
    }

    /* ── Event: ID field changed ── */
    idInput.addEventListener("change", () => {
      const id = parseInt(idInput.value, 10);
      if (id) {
        lookupById(id);
      } else {
        nameInput.value = "";
      }
    });

    /* ── Event: Name field typing ── */
    nameInput.addEventListener("input", () => {
      const val = nameInput.value.trim();
      searchByName(val);
    });

    /* ── Event: Name field blur (close dropdown after a tick so click can fire) ── */
    nameInput.addEventListener("blur", () => {
      setTimeout(closeDropdown, 200);
    });

    /* ── Event: Name field focus (re-search if there's text) ── */
    nameInput.addEventListener("focus", () => {
      const val = nameInput.value.trim();
      if (val && val.length >= 1) searchByName(val);
    });

    /* ── Event: Keyboard navigation ── */
    nameInput.addEventListener("keydown", (e) => {
      if (!dropdown.classList.contains("open")) return;
      const items = dropdown.querySelectorAll(".cl-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(Math.min(activeIndex + 1, items.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (activeIndex >= 0) selectResult(activeIndex);
      } else if (e.key === "Escape") {
        closeDropdown();
      }
    });

    /* ── Event: Click on dropdown item ── */
    dropdown.addEventListener("mousedown", (e) => {
      const item = e.target.closest(".cl-item");
      if (item) {
        e.preventDefault();   // prevent blur stealing focus
        selectResult(parseInt(item.dataset.idx, 10));
      }
    });

    /* ═══ Public API ═══ */
    return {
      getId()   { return parseInt(idInput.value, 10); },
      getName() { return nameInput.value; },
      setId(id) {
        idInput.value = id;
        if (id) lookupById(id); else nameInput.value = "";
      },
      reset() { idInput.value = ""; nameInput.value = ""; closeDropdown(); },
      onSelect(cb) { selectCallbacks.push(cb); },
      /** Direct access to underlying inputs for advanced wiring */
      idInputEl:   idInput,
      nameInputEl: nameInput,
    };
  }

  return { create };
})();
