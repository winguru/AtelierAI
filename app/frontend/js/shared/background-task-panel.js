/**
 * BackgroundTaskPanel — lightweight active-jobs widget.
 *
 * A trimmed version of the main gallery's Background Tasks panel: a single
 * collapsible strip showing ACTIVE jobs with live progress, ETA (parsed
 * from the backend's progress messages), and a small recent-history list.
 * Designed for the lab pages (search-lab, sync-lab) where a full task
 * inspector is overkill.
 *
 * Usage:
 *   const panel = BackgroundTaskPanel.create({
 *     container: document.getElementById('bg-tasks-mount'),
 *     pollIntervalMs: 3000,        // active jobs present
 *     idleIntervalMs: 15000,       // no active jobs
 *   });
 *   // panel.destroy() on teardown
 *
 * Implementation notes:
 * - Polls GET /api/tasks/?limit=20 (same endpoint as the gallery panel).
 * - A task whose id 404s mid-poll is reported as LOST — the in-memory task
 *   registry is wiped on every uvicorn --reload, and silent disappearance
 *   previously hid lost imports (2026-09-24 incident).
 * - ETA / rate come straight from the backend task message
 *   ("... completed 12/36 (14.3/min, ETA 00:05)") — no client math needed.
 */
(() => {
  'use strict';

  const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

  function isActive(task) {
    return !TERMINAL.has(String(task.status || ''));
  }

  /** Parse "12/36 (14.3/min, ETA 00:05)" → {current, total, rate, eta} */
  function parseProgress(task) {
    const out = { current: task.progress_current || 0, total: task.progress_total || 0, rate: null, eta: null };
    const m = String(task.message || '').match(/(\d+)\s*\/\s*(\d+)(?:\s*\(([\d.]+)\/min,\s*ETA\s*([\d:]+)\))?/);
    if (m) {
      out.current = Number(m[1]);
      out.total = Number(m[2]);
      if (m[3]) out.rate = `${m[3]}/min`;
      if (m[4]) out.eta = m[4];
    }
    return out;
  }

  function create(options) {
    const {
      container,
      pollIntervalMs = 3000,
      idleIntervalMs = 15000,
    } = options;

    if (!container) throw new Error('BackgroundTaskPanel: container is required');

    let _timer = null;
    let _knownTaskIds = new Set();   // ids seen alive (for 404-loss detection)
    let _destroyed = false;

    // ── DOM ────────────────────────────────────────────────────────────
    container.classList.add('bg-task-panel');
    container.innerHTML = `
      <details class="bg-task-details">
        <summary class="bg-task-summary">
          <span class="bg-task-icon" aria-hidden="true">⧗</span>
          <span class="bg-task-label">Background Jobs</span>
          <span class="bg-task-count hidden" aria-live="polite"></span>
          <span class="bg-task-chevron" aria-hidden="true">▸</span>
        </summary>
        <div class="bg-task-body">
          <div class="bg-task-active" aria-label="Active jobs"></div>
          <div class="bg-task-recent" aria-label="Recent jobs"></div>
        </div>
      </details>`;

    const countEl = container.querySelector('.bg-task-count');
    const activeEl = container.querySelector('.bg-task-active');
    const recentEl = container.querySelector('.bg-task-recent');
    const detailsEl = container.querySelector('.bg-task-details');

    // ── rendering ──────────────────────────────────────────────────────
    function rowEl(task, { lost = false } = {}) {
      const p = parseProgress(task);
      const row = document.createElement('div');
      row.className = `bg-task-row bg-task-${lost ? 'lost' : task.status}`;
      const pct = p.total > 0 ? Math.round((p.current / p.total) * 100) : null;

      const title = document.createElement('span');
      title.className = 'bg-task-title';
      title.textContent = lost
        ? `${task.title || task.kind || 'Job'} — lost (server restarted?)`
        : (task.title || task.kind || task.id);

      const status = document.createElement('span');
      status.className = `bg-task-status bg-task-status-${lost ? 'lost' : task.status}`;
      status.textContent = lost ? 'LOST' : (task.status || 'queued');

      const progress = document.createElement('span');
      progress.className = 'bg-task-progress';
      let bits = [];
      if (pct !== null && !lost) bits.push(`${p.current}/${p.total}`);
      if (p.rate) bits.push(p.rate);
      if (p.eta) bits.push(`ETA ${p.eta}`);
      progress.textContent = bits.join(' · ') || (lost ? 'status unknown' : 'pending');

      row.append(title, status, progress);
      if (pct !== null && !lost) {
        const bar = document.createElement('div');
        bar.className = 'bg-task-bar';
        const fill = document.createElement('div');
        fill.className = 'bg-task-bar-fill';
        fill.style.width = `${Math.min(100, Math.max(2, pct))}%`;
        bar.appendChild(fill);
        row.appendChild(bar);
      }
      return row;
    }

    function render(tasks) {
      const active = tasks.filter(isActive);
      const recent = tasks.filter((t) => !isActive(t)).slice(0, 5);

      // Loss detection: any previously-known id that's absent from BOTH the
      // list and the terminal set vanished — registry wipe (server reload).
      const currentIds = new Set(tasks.map((t) => t.id));
      const lostRows = [];
      for (const id of _knownTaskIds) {
        if (!currentIds.has(id)) lostRows.push(id);
      }

      activeEl.innerHTML = '';
      const empty = document.createElement('div');
      empty.className = 'bg-task-empty';
      empty.textContent = 'No active jobs.';
      if (!active.length) activeEl.appendChild(empty);
      active.forEach((t) => activeEl.appendChild(rowEl(t)));

      recentEl.innerHTML = '';
      if (recent.length) {
        const h = document.createElement('div');
        h.className = 'bg-task-recent-title';
        h.textContent = 'Recent';
        recentEl.appendChild(h);
        recent.forEach((t) => recentEl.appendChild(rowEl(t)));
      }

      const badge = active.length + (lostRows.length ? lostRows.length : 0);
      countEl.textContent = badge > 0 ? String(badge) : '';
      countEl.classList.toggle('hidden', badge === 0);

      // Auto-expand when active work exists or jobs were lost; collapse
      // (only if the user hasn't manually opened it) when idle.
      const userOpened = detailsEl.dataset.userOpen === '1';
      if ((active.length || lostRows.length) && !detailsEl.open && !userOpened) {
        detailsEl.open = true;
      }
    }

    // ── polling ────────────────────────────────────────────────────────
    async function poll() {
      try {
        const res = await fetch('/api/tasks/?limit=20');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const tasks = await res.json();
        if (!Array.isArray(tasks)) return;
        // Track ids we've seen ALIVE for loss detection (terminal tasks can
        // legitimately age out of the list — only ACTIVE ids disappearing is
        // suspicious, but a reload kills terminal history too, so track all).
        for (const t of tasks) _knownTaskIds.add(t.id);
        render(tasks);
        schedule(tasks.some(isActive) ? pollIntervalMs : idleIntervalMs);
      } catch {
        // Server unreachable (mid-reload) — retry on the slow cadence.
        schedule(idleIntervalMs);
      }
    }

    function schedule(ms) {
      if (_destroyed) return;
      if (_timer) clearTimeout(_timer);
      _timer = setTimeout(poll, ms);
    }

    detailsEl.addEventListener('toggle', () => {
      detailsEl.dataset.userOpen = detailsEl.open ? '1' : '0';
    });

    // Kick off immediately.
    poll();

    return {
      destroy() {
        _destroyed = true;
        if (_timer) clearTimeout(_timer);
        container.innerHTML = '';
      },
    };
  }

  window.BackgroundTaskPanel = { create };
})();
