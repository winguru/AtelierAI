(() => {
  const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 180;

  function readStorageValue(storage, key) {
    if (!storage || !key) {
      return null;
    }
    try {
      return storage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeStorageValue(storage, key, value) {
    if (!storage || !key) {
      return;
    }
    try {
      storage.setItem(key, String(value));
    } catch {
      // Ignore storage errors (private mode, blocked storage, etc.)
    }
  }

  function readCookieValue(name) {
    const cookieText = document.cookie || '';
    const prefix = `${encodeURIComponent(name)}=`;
    const rawValue = cookieText
      .split(';')
      .map((part) => part.trim())
      .find((part) => part.startsWith(prefix))
      ?.slice(prefix.length);
    if (rawValue == null) {
      return null;
    }
    try {
      return decodeURIComponent(rawValue);
    } catch {
      return rawValue;
    }
  }

  function writeCookieValue(name, value, maxAgeSeconds = COOKIE_MAX_AGE_SECONDS) {
    document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(String(value))}; path=/; max-age=${maxAgeSeconds}; SameSite=Lax`;
  }

  function readCookieBool(name, fallback) {
    const raw = readCookieValue(name);
    if (raw == null) {
      return fallback;
    }
    const normalized = raw.trim().toLowerCase();
    if (normalized === 'true') {
      return true;
    }
    if (normalized === 'false') {
      return false;
    }
    return fallback;
  }

  function readCookieString(name, fallback, allowedValues) {
    const raw = readCookieValue(name);
    if (raw == null) {
      return fallback;
    }
    if (Array.isArray(allowedValues) && allowedValues.length) {
      return allowedValues.includes(raw) ? raw : fallback;
    }
    return raw || fallback;
  }

  function readStoredBool(key, fallback) {
    const raw = readStorageValue(window.localStorage, key);
    if (raw === null) {
      return fallback;
    }
    return raw === 'true';
  }

  function writeStoredBool(key, value) {
    writeStorageValue(window.localStorage, key, Boolean(value));
  }

  function readStoredString(key, fallback, allowedValues) {
    const raw = readStorageValue(window.localStorage, key);
    if (raw === null) {
      return fallback;
    }
    if (!Array.isArray(allowedValues) || !allowedValues.length) {
      return raw;
    }
    return allowedValues.includes(raw) ? raw : fallback;
  }

  function writeStoredString(key, value) {
    writeStorageValue(window.localStorage, key, value);
  }

  function applyTheme(themeMode) {
    const normalized = themeMode === 'dark' ? 'dark' : 'light';
    document.body.dataset.theme = normalized;
    return normalized;
  }

  function bindThemeToggle(toggle, { cookieKey = 'atelier_theme_mode' } = {}) {
    if (!(toggle instanceof HTMLInputElement)) {
      return null;
    }
    const current = readCookieString(cookieKey, 'light', ['light', 'dark']);
    const applied = applyTheme(current);
    toggle.checked = applied === 'dark';

    toggle.addEventListener('change', () => {
      const nextTheme = toggle.checked ? 'dark' : 'light';
      writeCookieValue(cookieKey, nextTheme);
      const nextApplied = applyTheme(nextTheme);
      document.dispatchEvent(new CustomEvent('atelier:theme-change', { detail: { themeMode: nextApplied } }));
    });

    return applied;
  }

  function initThemeFromCookie({ cookieKey = 'atelier_theme_mode' } = {}) {
    const current = readCookieString(cookieKey, 'light', ['light', 'dark']);
    return applyTheme(current);
  }

  window.AtelierPreferences = {
    readCookieValue,
    writeCookieValue,
    readCookieBool,
    readCookieString,
    readStoredBool,
    writeStoredBool,
    readStoredString,
    writeStoredString,
    applyTheme,
    bindThemeToggle,
    initThemeFromCookie,
  };
})();
