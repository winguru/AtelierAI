/**
 * ComfyUI Lab – Send AtelierAI gallery images to a ComfyUI instance.
 *
 * Two transfer methods:
 *  1. **Send to ComfyUI** button – uploads the image to ComfyUI via its
 *     `/upload/image` REST API.  This is the reliable method that works
 *     regardless of cross-origin restrictions.
 *  2. **Native drag-and-drop** – the preview image is draggable.  For
 *     same-origin ComfyUI, the browser may allow a direct drop onto the
 *     iframe.  For cross-origin setups, the overlay technique from
 *     https://github.com/James-E-Adams/iframe-drag-n-drop is used:
 *     an invisible overlay intercepts the drop, then forwards the data
 *     via postMessage (ComfyUI or a companion extension must listen).
 */

(() => {
  'use strict';

  // ── DOM refs ──────────────────────────────────────────────
  const themeToggle  = document.getElementById('theme-toggle');
  const urlForm      = document.getElementById('comfyui-url-form');
  const urlInput     = document.getElementById('comfyui-url');
  const imageForm    = document.getElementById('image-form');
  const imageHash    = document.getElementById('image-hash');
  const previewPanel = document.getElementById('image-preview-panel');
  const dropLog      = document.getElementById('drop-log');
  const iframe       = document.getElementById('comfyui-iframe');
  const overlay      = document.getElementById('iframe-overlay');
  const placeholder  = document.getElementById('iframe-placeholder');
  const sendBtn      = document.getElementById('send-to-comfyui-btn');
  const sendRefBtn   = document.getElementById('send-as-ref-btn');

  // ── State ─────────────────────────────────────────────────
  let currentImage   = null;   // last fetched image record
  let previewImgEl   = null;   // the <img> inside previewPanel (draggable)
  let comfyuiOrigin  = '';     // e.g. "http://127.0.0.1:8188"

  // ── Theme ─────────────────────────────────────────────────
  if (window.AtelierPreferences?.bindThemeToggle) {
    window.AtelierPreferences.bindThemeToggle(themeToggle);
  }

  // ── Helpers ───────────────────────────────────────────────
  function logDrop(msg, type = 'ok') {
    const entry = document.createElement('div');
    entry.className = 'drop-log-entry';
    const time = new Date().toLocaleTimeString();
    const cls  = type === 'ok' ? 'log-ok' : 'log-err';
    entry.innerHTML = `<span class="log-time">${time}</span><span class="${cls}">${msg}</span>`;
    // Clear placeholder text on first entry
    const empty = dropLog.querySelector('.control-preview-empty');
    if (empty) empty.remove();
    dropLog.prepend(entry);
    // Keep log bounded
    while (dropLog.children.length > 50) {
      dropLog.lastChild.remove();
    }
  }

  function clearPreview() {
    previewPanel.classList.add('is-empty');
    previewPanel.innerHTML = '<p class="control-preview-empty">No image loaded.</p>';
    previewImgEl = null;
    currentImage = null;
    updateButtons();
  }

  /**
   * Build a local image URL from the gallery item, matching main.js getImageUrl().
   */
  function getImageUrl(image) {
    const filePath = image.file_path || image.file_name;
    if (!filePath) return '';
    const encodedPath = String(filePath)
      .split('/')
      .map((seg) => encodeURIComponent(seg))
      .join('/');
    const token = image.date_modified || image.id || image.file_size || image.file_hash;
    const version = token ? `?v=${encodeURIComponent(String(token))}` : '';
    return `/image_library/${encodedPath}${version}`;
  }

  function renderPreview(image) {
    previewPanel.classList.remove('is-empty');

    const src = getImageUrl(image) || image.source_url || '';
    if (!src) {
      previewPanel.innerHTML = '<p class="control-preview-empty">No preview URL available for this image.</p>';
      return;
    }

    const wrap = document.createElement('div');
    wrap.className = 'preview-image-wrap';

    const img = document.createElement('img');
    img.src = src;
    img.alt = image.file_name || 'Preview';
    img.draggable = true;
    img.id = 'drag-preview-img';
    previewImgEl = img;

    const meta = document.createElement('div');
    meta.className = 'preview-image-meta';
    meta.innerHTML =
      `<strong>${image.file_name || 'Unknown'}</strong><br>` +
      `Hash: <code>${image.file_hash || '?'}</code><br>` +
      `MIME: <code>${image.mimetype || '?'}</code><br>` +
      `<span style="color:var(--accent)">⬆ Drag this image onto the ComfyUI workspace</span>`;

    wrap.appendChild(img);
    wrap.appendChild(meta);
    previewPanel.innerHTML = '';
    previewPanel.appendChild(wrap);

    updateButtons();
    wireDrag(img, image);
  }

  // ── ComfyUI URL form ─────────────────────────────────────
  urlForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const url = urlInput.value.trim();
    if (!url) return;

    try {
      const parsed = new URL(url);  // validate
      comfyuiOrigin = parsed.origin;
    } catch {
      logDrop('Invalid URL – must include scheme (e.g. http://)', 'err');
      return;
    }

    iframe.src = url;
    iframe.classList.remove('is-hidden');
    placeholder.classList.add('is-hidden');
    updateButtons();
    logDrop(`Connecting to ${url} …`);
  });

  // ── Image load form ──────────────────────────────────────
  imageForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const hash = imageHash.value.trim();
    if (!hash) return;

    try {
      const params = new URLSearchParams({
        search: hash,
        group_variants: 'false',
        limit: '1',
      });
      const resp = await fetch(`/images/?${params.toString()}`);
      if (!resp.ok) {
        clearPreview();
        logDrop(`Image query failed: ${resp.status}`, 'err');
        return;
      }
      const items = await resp.json();
      if (!Array.isArray(items) || items.length === 0) {
        clearPreview();
        logDrop(`No image found for hash: ${hash}`, 'err');
        return;
      }
      const image = items[0];
      currentImage = image;
      renderPreview(image);
      logDrop(`Loaded image ${image.file_name || hash}`);
    } catch (err) {
      clearPreview();
      logDrop(`Fetch error: ${err.message}`, 'err');
    }
  });

  // ── ComfyUI Upload API ──────────────────────────────────
  //
  // Uses ComfyUI's native /upload/image endpoint to push an image
  // directly into ComfyUI's input folder.  This bypasses all
  // cross-origin drag-and-drop limitations.

  function updateButtons() {
    const ready = currentImage && comfyuiOrigin;
    sendBtn.disabled    = !ready;
    sendRefBtn.disabled = !ready;
  }

  /**
   * Fetch the image binary from AtelierAI, then upload it to ComfyUI.
   * @param {object} image  Gallery item
   * @param {object} [opts]
   * @param {boolean} [opts.overwrite]  Overwrite if already exists in ComfyUI
   * @param {string}  [opts.subfolder]  Target subfolder inside ComfyUI input/
   * @param {string}  [opts.type]       "input" (default) or "temp"
   * @returns {Promise<{name:string, subfolder:string, type:string}>}
   */
  async function uploadToComfyUI(image, opts = {}) {
    const src = getImageUrl(image);
    if (!src) throw new Error('No image URL available');

    const {
      overwrite = true,
      subfolder = '',
      type      = 'input',
    } = opts;

    // 1. Fetch the image binary from our backend
    const imgResp = await fetch(src);
    if (!imgResp.ok) throw new Error(`Failed to fetch image: ${imgResp.status}`);
    const blob = await imgResp.blob();

    // 2. Build multipart form for ComfyUI /upload/image
    const fileName = image.file_name || `atelier-${image.file_hash || 'image'}.png`;
    const formData = new FormData();
    formData.append('image', blob, fileName);
    formData.append('overwrite', String(overwrite));
    if (subfolder) formData.append('subfolder', subfolder);
    formData.append('type', type);

    // 3. POST to ComfyUI via backend proxy (avoids CORS)
    const uploadUrl = `/comfyui-proxy/upload/image?target=${encodeURIComponent(comfyuiOrigin)}`;
    const uploadResp = await fetch(uploadUrl, {
      method: 'POST',
      body: formData,
    });

    if (!uploadResp.ok) {
      const text = await uploadResp.text().catch(() => '');
      throw new Error(`Upload failed (${uploadResp.status}): ${text}`);
    }

    return uploadResp.json(); // { name, subfolder, type }
  }

  /**
   * Load an uploaded image into ComfyUI's canvas by sending a minimal
   * LoadImage node via the /prompt endpoint.
   */
  async function loadImageInComfyUI(uploadResult) {
    const promptUrl = `/comfyui-proxy/prompt?target=${encodeURIComponent(comfyuiOrigin)}`;
    const nodeId = `${Date.now()}`;

    const workflow = {
      [nodeId]: {
        class_type: 'LoadImage',
        inputs: {
          image: uploadResult.name,
        },
      },
    };

    const resp = await fetch(promptUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: workflow }),
    });

    if (!resp.ok) {
      const text = await resp.text().catch(() => '');
      throw new Error(`Queue prompt failed (${resp.status}): ${text}`);
    }

    return resp.json();
  }

  // ── Button handlers ─────────────────────────────────────
  sendBtn.addEventListener('click', async () => {
    if (!currentImage || !comfyuiOrigin) return;
    const fileName = currentImage.file_name || 'image';
    try {
      sendBtn.disabled = true;
      sendBtn.textContent = 'Uploading…';
      logDrop(`Uploading "${fileName}" to ComfyUI…`);

      const result = await uploadToComfyUI(currentImage);
      logDrop(`✓ Uploaded as "${result.name}" (subfolder: ${result.subfolder || '/'}, type: ${result.type})`);
    } catch (err) {
      logDrop(`Upload error: ${err.message}`, 'err');
    } finally {
      sendBtn.disabled = !currentImage || !comfyuiOrigin;
      sendBtn.textContent = 'Send to ComfyUI';
    }
  });

  sendRefBtn.addEventListener('click', async () => {
    if (!currentImage || !comfyuiOrigin) return;
    const fileName = currentImage.file_name || 'image';
    try {
      sendRefBtn.disabled = true;
      sendRefBtn.textContent = 'Sending…';
      logDrop(`Uploading "${fileName}" and loading in ComfyUI canvas…`);

      const uploadResult = await uploadToComfyUI(currentImage);
      logDrop(`✓ Uploaded as "${uploadResult.name}"`);

      const promptResult = await loadImageInComfyUI(uploadResult);
      logDrop(`✓ LoadImage node queued (prompt_id: ${promptResult.prompt_id || '?'})`);
    } catch (err) {
      logDrop(`Send-as-ref error: ${err.message}`, 'err');
    } finally {
      sendRefBtn.disabled = !currentImage || !comfyuiOrigin;
      sendRefBtn.textContent = 'Send as Reference';
    }
  });

  // ── Drag-and-drop: overlay approach ──────────────────────
  //
  // When the user drags the preview image we show a transparent overlay
  // over the iframe.  For same-origin ComfyUI, the overlay is hidden
  // immediately so the native drop hits the iframe directly.  For
  // cross-origin, we intercept the drop and forward via postMessage.

  function getDragData(image) {
    const src = getImageUrl(image) || image.source_url || '';
    const fileName = image.file_name || 'atelier-image.png';
    const mimeType = image.mimetype || 'image/png';
    return { src, fileName, mimeType };
  }

  function wireDrag(imgEl, image) {
    imgEl.addEventListener('dragstart', (e) => {
      const { src, fileName, mimeType } = getDragData(image);
      const transfer = e.dataTransfer;
      if (!transfer) return;

      transfer.effectAllowed = 'copy';

      // Standard MIME types that ComfyUI / browsers understand
      transfer.setData('text/uri-list', src);
      transfer.setData('text/plain', src);
      transfer.setData('text/html', `<img src="${src}" alt="${fileName}">`);
      transfer.setData('DownloadURL', `${mimeType}:${fileName}:${src}`);

      // Show the overlay only for cross-origin setups.
      // Same-origin: the browser can deliver native drops to the iframe.
      try {
        const iframeOrigin = new URL(iframe.src).origin;
        if (iframeOrigin !== window.location.origin) {
          overlay.style.display = 'block';
        }
      } catch {
        // No iframe src or invalid URL – no overlay needed
      }
    });
  }

  overlay.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    overlay.classList.add('is-drag-active');
  });

  overlay.addEventListener('dragleave', (e) => {
    // Only hide if truly leaving the overlay bounds
    const rect = overlay.getBoundingClientRect();
    const { clientX: x, clientY: y } = e;
    if (x <= rect.left || x >= rect.right || y <= rect.top || y >= rect.bottom) {
      overlay.classList.remove('is-drag-active');
    }
  });

  overlay.addEventListener('drop', (e) => {
    e.preventDefault();
    overlay.classList.remove('is-drag-active');
    overlay.style.display = 'none';

    const { src, fileName } = getDragData(currentImage || {});
    const pageX = e.pageX;
    const pageY = e.pageY;

    logDrop(`Dropped "${fileName}" at (${pageX.toFixed(0)}, ${pageY.toFixed(0)})`);

    // ── Forward into iframe ───────────────────────────────
    const iframeRect = iframe.getBoundingClientRect();
    const iframeX = pageX - iframeRect.left;
    const iframeY = pageY - iframeRect.top;

    // Post message to iframe (works cross-origin).
    // ComfyUI or a companion extension could listen for this.
    const payload = {
      type: 'atelier:drop-image',
      src,
      fileName,
      position: { x: iframeX, y: iframeY },
    };

    try {
      iframe.contentWindow.postMessage(JSON.stringify(payload), '*');
      logDrop(`postMessage sent to iframe at iframe-local (${iframeX.toFixed(0)}, ${iframeY.toFixed(0)})`);
    } catch {
      logDrop('postMessage failed – iframe may be blocked', 'err');
    }

    // Also try to set a hash fragment so the URL carries the image
    // reference (useful for same-origin setups).
    try {
      if (iframe.contentWindow) {
        const iframeDoc = iframe.contentDocument;
        if (iframeDoc) {
          // Same-origin: attempt to find ComfyUI's canvas and dispatch
          // a synthetic drop event.
          const target = iframeDoc.elementFromPoint(iframeX, iframeY);
          if (target) {
            const synth = new DragEvent('drop', {
              bubbles: true,
              cancelable: true,
              clientX: iframeRect.left + iframeX,
              clientY: iframeRect.top + iframeY,
            });
            // Override dataTransfer
            Object.defineProperty(synth, 'dataTransfer', {
              value: {
                getData: (fmt) => {
                  if (['text/uri-list', 'text/plain', 'URL'].includes(fmt)) return src;
                  if (fmt === 'text/html') return `<img src="${src}" alt="${fileName}">`;
                  return '';
                },
                dropEffect: 'copy',
              },
            });
            target.dispatchEvent(synth);
            logDrop(`Synthetic drop dispatched on <${target.tagName.toLowerCase()}> (same-origin)`);
          }
        }
      }
    } catch {
      // Cross-origin – contentDocument access throws.
      logDrop('Same-origin dispatch skipped (cross-origin)');
    }
  });

  // Global dragend to ensure overlay is always cleaned up
  document.addEventListener('dragend', () => {
    overlay.classList.remove('is-drag-active');
    overlay.style.display = 'none';
  });
})();
