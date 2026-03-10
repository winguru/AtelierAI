document.addEventListener('DOMContentLoaded', () => {
    const STORAGE_KEYS = {
        infinite: 'atelier.gallery.infiniteScroll',
        debug: 'atelier.gallery.debugVisible',
        thumbSize: 'atelier.gallery.thumbSize',
    };
    const TEST_PAGE_SIZE = 120;
    const BASE_SYNC_MARGIN_PX = 4;
    // Fine-tuning offset for near-equal pane heights in this environment.
    // If this causes overfitting across devices/zoom/font settings, set to 0.
    const PANE_HEIGHT_CALIBRATION_PX = 2;

    function readStoredBool(key, fallback) {
        try {
            const raw = window.localStorage.getItem(key);
            if (raw === null) {
                return fallback;
            }
            return raw === 'true';
        } catch {
            return fallback;
        }
    }

    function writeStoredBool(key, value) {
        try {
            window.localStorage.setItem(key, String(Boolean(value)));
        } catch {
            // Ignore storage errors (private mode, blocked storage, etc.)
        }
    }

    function readStoredNumber(key, fallback, min, max) {
        try {
            const raw = window.localStorage.getItem(key);
            if (raw === null) {
                return fallback;
            }
            const parsed = Number(raw);
            if (!Number.isFinite(parsed)) {
                return fallback;
            }
            return Math.max(min, Math.min(max, parsed));
        } catch {
            return fallback;
        }
    }

    function writeStoredNumber(key, value) {
        try {
            window.localStorage.setItem(key, String(value));
        } catch {
            // Ignore storage errors (private mode, blocked storage, etc.)
        }
    }

    const state = {
        allImages: [],
        filteredImages: [],
        artistNames: [],
        selectedKey: null,
        // Lower page size is intentional for testing end-of-library UI transitions.
        pageSize: TEST_PAGE_SIZE,
        offset: 0,
        hasMore: true,
        loadingPage: false,
        infiniteEnabled: readStoredBool(STORAGE_KEYS.infinite, true),
        debugVisible: readStoredBool(STORAGE_KEYS.debug, true),
        thumbSize: readStoredNumber(STORAGE_KEYS.thumbSize, 165, 120, 260),
    };

    const artistDatalist = document.getElementById('artist-suggestions');
    const licenseSelect = document.getElementById('license-select');
    const appShell = document.querySelector('.app-shell');
    const galleryPane = document.querySelector('.gallery-pane');
    const galleryToolbar = document.querySelector('.gallery-toolbar');
    const galleryFooter = document.querySelector('.gallery-footer');
    const galleryGrid = document.getElementById('gallery-grid');
    const galleryStatus = document.getElementById('gallery-status');
    const loadMoreBtn = document.getElementById('load-more-btn');
    const infiniteScrollToggle = document.getElementById('infinite-scroll-toggle');
    const debugToggle = document.getElementById('debug-toggle');
    const thumbSizeSlider = document.getElementById('thumb-size-slider');
    const thumbSizeValue = document.getElementById('thumb-size-value');
    const thumbPresetButtons = Array.from(document.querySelectorAll('.thumb-preset'));
    const imageCount = document.getElementById('image-count');
    const searchInput = document.getElementById('search-input');
    const refreshBtn = document.getElementById('refresh-btn');
    const scanBtn = document.getElementById('scan-btn');
    const scanOutput = document.getElementById('scan-output');
    const uploadForm = document.getElementById('upload-form');
    const uploadOutput = document.getElementById('upload-output');

    const detailsEmpty = document.getElementById('details-empty');
    const detailsContent = document.getElementById('details-content');
    const detailImage = document.getElementById('detail-image');
    const detailTitle = document.getElementById('detail-title');
    const detailSubtitle = document.getElementById('detail-subtitle');
    const detailMeta = document.getElementById('detail-meta');
    const detailExif = document.getElementById('detail-exif');
    const detailCivitai = document.getElementById('detail-civitai');
    const detailsPane = document.querySelector('.details-pane');
    const debugFields = document.getElementById('debug-fields');
    const debugBadge = document.getElementById('debug-badge');
    let currentDebugImage = null;
    let resizeObserver = null;
    let heightSyncRaf = 0;
    let lastSyncedGalleryHeight = -1;

    function syncLayoutMode() {
        appShell.classList.toggle('debug-on', state.debugVisible);
    }

    function syncGalleryGridHeight() {
        const isMobile = window.matchMedia('(max-width: 980px)').matches;
        if (isMobile) {
            galleryGrid.style.height = '';
            galleryGrid.style.maxHeight = '';
            lastSyncedGalleryHeight = -1;
            return;
        }

        const detailsRect = detailsPane.getBoundingClientRect();
        const effectiveDetailsHeight = Math.max(320, Math.floor(detailsRect.height));

        const paneStyle = window.getComputedStyle(galleryPane);
        const padTop = Number.parseFloat(paneStyle.paddingTop || '0') || 0;
        const padBottom = Number.parseFloat(paneStyle.paddingBottom || '0') || 0;
        const toolbarStyle = window.getComputedStyle(galleryToolbar);
        const toolbarMarginBottom = Number.parseFloat(toolbarStyle.marginBottom || '0') || 0;
        const footerStyle = window.getComputedStyle(galleryFooter);
        const footerMarginTop = Number.parseFloat(footerStyle.marginTop || '0') || 0;
        const toolbarHeight = galleryToolbar.offsetHeight;
        const footerHeight = galleryFooter.offsetHeight;
        const available = effectiveDetailsHeight
            + PANE_HEIGHT_CALIBRATION_PX
            - toolbarHeight
            - toolbarMarginBottom
            - footerHeight
            - footerMarginTop
            - padTop
            - padBottom
            - BASE_SYNC_MARGIN_PX;
        const target = Math.max(220, Math.floor(available));

        // Avoid style churn if value is effectively unchanged.
        if (Math.abs(target - lastSyncedGalleryHeight) < 1) {
            return;
        }

        lastSyncedGalleryHeight = target;
        galleryGrid.style.height = `${target}px`;
        galleryGrid.style.maxHeight = `${target}px`;
    }

    function scheduleGalleryGridHeightSync() {
        if (heightSyncRaf) {
            cancelAnimationFrame(heightSyncRaf);
        }
        heightSyncRaf = requestAnimationFrame(() => {
            heightSyncRaf = 0;
            syncGalleryGridHeight();
        });
    }

    function syncThumbSize() {
        const px = `${state.thumbSize}px`;
        appShell.style.setProperty('--thumb-size', px);
        thumbSizeSlider.value = String(state.thumbSize);
        thumbSizeValue.textContent = px;
        thumbPresetButtons.forEach((button) => {
            const size = Number(button.dataset.size);
            button.classList.toggle('active', size === state.thumbSize);
        });
    }

    function safeText(value, fallback = 'N/A') {
        if (value === null || value === undefined || value === '') {
            return fallback;
        }
        return String(value);
    }

    function formatBytes(bytes) {
        const size = Number(bytes);
        if (!Number.isFinite(size) || size <= 0) {
            return 'N/A';
        }
        const units = ['B', 'KB', 'MB', 'GB'];
        const i = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
        const value = size / Math.pow(1024, i);
        return `${value.toFixed(value >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
    }

    function getImageUrl(image) {
        const filePath = image.file_path || image.file_name;
        if (!filePath) {
            return '';
        }

        // Encode each path segment to preserve slashes while handling special chars.
        const encodedPath = String(filePath)
            .split('/')
            .map((segment) => encodeURIComponent(segment))
            .join('/');
        return `/image_library/${encodedPath}`;
    }

    function toClientImage(image, indexOffset) {
        const stablePart = image.file_hash || image.file_path || image.file_name || `row-${indexOffset}`;
        return {
            ...image,
            __key: `${stablePart}::${indexOffset}`,
        };
    }

    function renderMetaItem(label, value) {
        const wrapper = document.createElement('div');
        wrapper.className = 'meta-item';

        const labelNode = document.createElement('span');
        labelNode.className = 'label';
        labelNode.textContent = label;

        const valueNode = document.createElement('span');
        valueNode.className = 'value';
        valueNode.textContent = safeText(value);

        wrapper.appendChild(labelNode);
        wrapper.appendChild(valueNode);
        return wrapper;
    }

    function renderEditableMetaItem(config) {
        const {
            label,
            value,
            inputType = 'text',
            placeholder = '',
            suggestions = null,
            isUrlValue = false,
            displayLinkUrl = null,
            onSave,
        } = config;
        let currentValue = value;

        const wrapper = document.createElement('div');
        wrapper.className = 'meta-item';

        const head = document.createElement('div');
        head.className = 'meta-item-head';

        const labelNode = document.createElement('span');
        labelNode.className = 'label';
        labelNode.textContent = label;

        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'edit-icon-btn';
        editBtn.title = 'Edit';
        editBtn.setAttribute('aria-label', `Edit ${label}`);
        editBtn.textContent = '✎';

        head.appendChild(labelNode);
        head.appendChild(editBtn);

        const valueNode = document.createElement('span');
        valueNode.className = 'value';

        function renderDisplayValue(nextValue) {
            valueNode.innerHTML = '';
            const text = safeText(nextValue);
            const resolvedDisplayLink =
                typeof displayLinkUrl === 'function'
                    ? displayLinkUrl()
                    : displayLinkUrl;
            const shouldLinkByOwnValue =
                isUrlValue && typeof nextValue === 'string' && /^https?:\/\//i.test(nextValue);
            const shouldLinkByDisplayUrl =
                !shouldLinkByOwnValue
                && typeof resolvedDisplayLink === 'string'
                && /^https?:\/\//i.test(resolvedDisplayLink)
                && typeof nextValue === 'string'
                && nextValue.trim().length > 0;

            if (shouldLinkByOwnValue || shouldLinkByDisplayUrl) {
                const link = document.createElement('a');
                link.className = 'value-link';
                link.href = shouldLinkByOwnValue ? nextValue : resolvedDisplayLink;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.textContent = shouldLinkByOwnValue ? nextValue : nextValue;
                valueNode.appendChild(link);
            } else {
                valueNode.textContent = text;
            }
        }

        renderDisplayValue(currentValue);

        const editRow = document.createElement('div');
        editRow.className = 'meta-edit-row hidden';

        const input = document.createElement('input');
        input.className = 'meta-edit-input';
        input.type = inputType;
        input.placeholder = placeholder;
        input.value = currentValue || '';

        if (Array.isArray(suggestions) && suggestions.length) {
            const listId = `meta-edit-list-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
            input.setAttribute('list', listId);
            const datalist = document.createElement('datalist');
            datalist.id = listId;
            suggestions.forEach((entry) => {
                if (!entry) {
                    return;
                }
                const option = document.createElement('option');
                option.value = String(entry);
                datalist.appendChild(option);
            });
            editRow.appendChild(datalist);
        }

        const actions = document.createElement('div');
        actions.className = 'meta-edit-actions';

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn ghost btn-sm';
        saveBtn.textContent = 'Save';

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn ghost btn-sm';
        cancelBtn.textContent = 'Cancel';

        actions.appendChild(saveBtn);
        actions.appendChild(cancelBtn);
        editRow.appendChild(input);
        editRow.appendChild(actions);

        function setEditing(editing) {
            editRow.classList.toggle('hidden', !editing);
            valueNode.classList.toggle('hidden', editing);
            if (editing) {
                input.focus();
                input.select();
            }
        }

        editBtn.addEventListener('click', () => setEditing(true));
        cancelBtn.addEventListener('click', () => {
            input.value = currentValue || '';
            setEditing(false);
        });
        const saveCurrentValue = async () => {
            const nextValue = input.value.trim();
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            try {
                const savedValue = await onSave(nextValue);
                currentValue = savedValue;
                renderDisplayValue(currentValue);
                input.value = savedValue || '';
                setEditing(false);
            } catch (error) {
                alert(`Could not save ${label}: ${error.message}`);
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        };

        saveBtn.addEventListener('click', saveCurrentValue);
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                if (!saveBtn.disabled) {
                    saveCurrentValue();
                }
            } else if (event.key === 'Escape') {
                event.preventDefault();
                input.value = currentValue || '';
                setEditing(false);
            }
        });

        wrapper.appendChild(head);
        wrapper.appendChild(valueNode);
        wrapper.appendChild(editRow);
        return wrapper;
    }

    async function saveImageMetadata(fileHash, patchData) {
        const response = await fetch(`/images/${encodeURIComponent(fileHash)}`, {
            method: 'PATCH',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(patchData),
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    function setDebugBadge(fields) {
        const pairs = Object.entries(fields || {});
        debugFields.innerHTML = '';
        pairs.forEach(([label, value]) => {
            const dt = document.createElement('dt');
            dt.textContent = label;
            const dd = document.createElement('dd');
            dd.textContent = safeText(value);
            debugFields.appendChild(dt);
            debugFields.appendChild(dd);
        });
    }

    function getImageLayoutDebug() {
        const computed = window.getComputedStyle(detailImage);
        const pane = detailImage.closest('.details-pane');
        return {
            class: detailImage.className,
            display: computed.display,
            visibility: computed.visibility,
            opacity: computed.opacity,
            client_size: `${detailImage.clientWidth}x${detailImage.clientHeight}`,
            pane_client: pane ? `${pane.clientWidth}x${pane.clientHeight}` : 'N/A',
        };
    }

    function showDetails(image) {
        if (!image) {
            detailsContent.classList.add('hidden');
            detailsEmpty.classList.remove('hidden');
            currentDebugImage = null;
            debugBadge.classList.add('hidden');
            return;
        }

        detailsEmpty.classList.add('hidden');
        detailsContent.classList.remove('hidden');
        debugBadge.classList.toggle('hidden', !state.debugVisible);

        const imageUrl = getImageUrl(image);
        currentDebugImage = image;

        // Force visible state to avoid stale hidden-class/layout edge cases.
        detailImage.classList.remove('hidden');
        detailImage.style.display = 'block';
        detailImage.style.visibility = 'visible';
        detailImage.style.opacity = '1';

        if (imageUrl) {
            detailImage.src = imageUrl;
        } else {
            detailImage.removeAttribute('src');
        }
        detailImage.alt = safeText(image.file_name, 'Selected image');
        detailTitle.textContent = safeText(image.file_name, image.file_hash || 'Untitled');
        detailSubtitle.textContent = [
            image.generation_software,
            image.source_site,
            image.mimetype,
        ].filter(Boolean).join(' • ');

        detailMeta.innerHTML = '';
        const metaNodes = [
            renderMetaItem('Hash', image.file_hash),
            renderMetaItem('Dimensions', image.width && image.height ? `${image.width} x ${image.height}` : null),
            renderMetaItem('Size', formatBytes(image.file_size)),
            renderMetaItem('Created', image.date_created),
            renderMetaItem('Modified', image.date_modified),
            renderEditableMetaItem({
                label: 'Artist',
                value: image.artist_name,
                placeholder: 'Artist name (leave blank to clear)',
                suggestions: state.artistNames,
                displayLinkUrl: () => image.artist_profile,
                onSave: async (nextValue) => {
                    const result = await saveImageMetadata(image.file_hash, {
                        artist_name: nextValue,
                    });

                    image.artist_id = result.artist_id ?? null;
                    image.artist_name = result.artist_name ?? null;
                    return image.artist_name;
                },
            }),
            renderEditableMetaItem({
                label: 'Artist Profile',
                value: image.artist_profile,
                inputType: 'url',
                placeholder: 'https://... (leave blank to clear)',
                isUrlValue: true,
                onSave: async (nextValue) => {
                    const result = await saveImageMetadata(image.file_hash, {
                        artist_profile: nextValue,
                    });

                    image.artist_profile = result.artist_profile ?? null;
                    return image.artist_profile;
                },
            }),
            renderEditableMetaItem({
                label: 'Source URL',
                value: image.source_url,
                inputType: 'url',
                placeholder: 'https://... (leave blank to clear)',
                isUrlValue: true,
                onSave: async (nextValue) => {
                    const result = await saveImageMetadata(image.file_hash, {
                        source_url: nextValue,
                    });

                    image.source_url = result.source_url || null;
                    image.source_site = result.source_site || null;
                    detailSubtitle.textContent = [
                        image.generation_software,
                        image.source_site,
                        image.mimetype,
                    ].filter(Boolean).join(' • ');
                    return image.source_url;
                },
            }),
        ];
        metaNodes.forEach((node) => detailMeta.appendChild(node));

        detailExif.textContent = JSON.stringify(image.exif_data || {}, null, 2);
        detailCivitai.textContent = JSON.stringify(image.civitai_data || image.civitai || {}, null, 2);

        setDebugBadge({
            key: image.__key,
            file_hash: image.file_hash,
            file_path: image.file_path,
            url: imageUrl,
            status: imageUrl ? 'loading' : 'missing-url',
            ...getImageLayoutDebug(),
        });

        scheduleGalleryGridHeightSync();
    }

    function renderGallery() {
        imageCount.textContent = `${state.filteredImages.length} image${state.filteredImages.length === 1 ? '' : 's'}`;

        if (!state.filteredImages.length) {
            galleryGrid.innerHTML = '<p>No images match your filter.</p>';
            showDetails(null);
            return;
        }

        galleryGrid.innerHTML = '';
        const fragment = document.createDocumentFragment();

        state.filteredImages.forEach((image) => {
            const caption = image.file_name || image.file_path || image.file_hash || 'Untitled';
            const tile = document.createElement('button');
            tile.className = `tile ${state.selectedKey === image.__key ? 'active' : ''}`;
            tile.type = 'button';
            tile.dataset.key = image.__key;

            const img = document.createElement('img');
            img.loading = 'lazy';
            img.alt = safeText(caption);
            img.src = getImageUrl(image);

            const captionSpan = document.createElement('span');
            captionSpan.className = 'tile-caption';
            captionSpan.textContent = safeText(caption);

            tile.appendChild(img);
            tile.appendChild(captionSpan);
            fragment.appendChild(tile);
        });

        galleryGrid.appendChild(fragment);
        scheduleGalleryGridHeightSync();
    }

    function applyFilter() {
        const query = searchInput.value.trim().toLowerCase();
        if (!query) {
            state.filteredImages = [...state.allImages];
        } else {
            state.filteredImages = state.allImages.filter((image) => {
                const haystack = [
                    image.file_name,
                    image.file_hash,
                    image.source_url,
                    image.source_site,
                    image.generation_software,
                    image.mimetype,
                ].filter(Boolean).join(' ').toLowerCase();
                return haystack.includes(query);
            });
        }

        if (!state.filteredImages.some((i) => i.__key === state.selectedKey)) {
            state.selectedKey = state.filteredImages[0]?.__key || null;
        }

        renderGallery();
        showDetails(state.filteredImages.find((i) => i.__key === state.selectedKey) || null);
    }

    function updatePagingUi() {
        loadMoreBtn.classList.toggle('hidden', state.infiniteEnabled || !state.hasMore);
        if (state.loadingPage) {
            galleryStatus.textContent = 'Loading more images...';
        } else if (!state.hasMore) {
            galleryStatus.textContent = state.allImages.length ? 'Reached end of library.' : '';
        } else {
            galleryStatus.textContent = '';
        }

        scheduleGalleryGridHeightSync();
    }

    async function loadNextPage() {
        if (state.loadingPage || !state.hasMore) {
            return;
        }

        state.loadingPage = true;
        updatePagingUi();

        try {
            const response = await fetch(`/images/?skip=${state.offset}&limit=${state.pageSize}`);
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const page = await response.json();
            const normalizedPage = Array.isArray(page)
                ? page.map((img, idx) => toClientImage(img, state.offset + idx))
                : [];

            state.allImages = state.allImages.concat(normalizedPage);
            state.offset += normalizedPage.length;
            if (normalizedPage.length < state.pageSize) {
                state.hasMore = false;
            }

            if (!state.selectedKey && state.allImages.length) {
                state.selectedKey = state.allImages[0].__key;
            }

            applyFilter();
        } catch (error) {
            galleryStatus.textContent = `Failed to load images: ${error.message}`;
        } finally {
            state.loadingPage = false;
            updatePagingUi();
        }
    }

    async function loadReferenceData() {
        const [artistsRes, licensesRes] = await Promise.all([
            fetch('/artists/'),
            fetch('/licenses/'),
        ]);

        const artists = await artistsRes.json();
        const licenses = await licensesRes.json();

        state.artistNames = artists
            .map((artist) => (artist && typeof artist.name === 'string' ? artist.name : ''))
            .filter((name) => name.length > 0);

        artistDatalist.innerHTML = '';
        artists.forEach((artist) => {
            const option = document.createElement('option');
            option.value = artist.name;
            artistDatalist.appendChild(option);
        });

        licenses.forEach((license) => {
            const option = document.createElement('option');
            option.value = license.id;
            option.textContent = `${license.short_name} - ${license.name}`;
            licenseSelect.appendChild(option);
        });
    }

    async function resetAndLoadImages() {
        refreshBtn.disabled = true;
        refreshBtn.textContent = 'Refreshing...';
        state.allImages = [];
        state.filteredImages = [];
        state.selectedKey = null;
        state.offset = 0;
        state.hasMore = true;
        state.loadingPage = false;
        galleryGrid.innerHTML = '';
        updatePagingUi();

        try {
            await loadNextPage();
        } catch (error) {
            galleryGrid.innerHTML = `<p>Error loading images: ${error.message}</p>`;
            imageCount.textContent = '0 images';
            showDetails(null);
        } finally {
            refreshBtn.disabled = false;
            refreshBtn.textContent = 'Refresh';
        }
    }

    galleryGrid.addEventListener('click', (event) => {
        const tile = event.target.closest('.tile');
        if (!tile) {
            return;
        }

        state.selectedKey = tile.dataset.key || null;
        renderGallery();
        showDetails(state.filteredImages.find((i) => i.__key === state.selectedKey) || null);
    });

    searchInput.addEventListener('input', applyFilter);
    detailImage.addEventListener('load', () => {
        if (!currentDebugImage) {
            return;
        }
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            url: detailImage.currentSrc || getImageUrl(currentDebugImage),
            status: 'loaded',
            natural_size: `${detailImage.naturalWidth}x${detailImage.naturalHeight}`,
            ...getImageLayoutDebug(),
        });
    });
    detailImage.addEventListener('error', () => {
        if (!currentDebugImage) {
            return;
        }
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            url: detailImage.currentSrc || getImageUrl(currentDebugImage),
            status: 'error-loading-image',
            ...getImageLayoutDebug(),
        });
    });
    galleryGrid.addEventListener('scroll', () => {
        if (!state.infiniteEnabled || !state.hasMore || state.loadingPage) {
            return;
        }

        const distanceToBottom = galleryGrid.scrollHeight - galleryGrid.scrollTop - galleryGrid.clientHeight;
        if (distanceToBottom < 240) {
            loadNextPage();
        }
    });
    refreshBtn.addEventListener('click', resetAndLoadImages);
    loadMoreBtn.addEventListener('click', loadNextPage);
    infiniteScrollToggle.addEventListener('change', () => {
        state.infiniteEnabled = infiniteScrollToggle.checked;
        writeStoredBool(STORAGE_KEYS.infinite, state.infiniteEnabled);
        updatePagingUi();
    });
    debugToggle.addEventListener('change', () => {
        state.debugVisible = debugToggle.checked;
        writeStoredBool(STORAGE_KEYS.debug, state.debugVisible);
        syncLayoutMode();
        debugBadge.classList.toggle('hidden', !state.debugVisible || !currentDebugImage);
        scheduleGalleryGridHeightSync();
    });
    thumbSizeSlider.addEventListener('input', () => {
        const next = Math.max(120, Math.min(260, Number(thumbSizeSlider.value) || 165));
        state.thumbSize = next;
        syncThumbSize();
        writeStoredNumber(STORAGE_KEYS.thumbSize, next);
        scheduleGalleryGridHeightSync();
    });
    thumbPresetButtons.forEach((button) => {
        button.addEventListener('click', () => {
            const preset = Number(button.dataset.size);
            if (!Number.isFinite(preset)) {
                return;
            }
            state.thumbSize = Math.max(120, Math.min(260, preset));
            syncThumbSize();
            writeStoredNumber(STORAGE_KEYS.thumbSize, state.thumbSize);
            scheduleGalleryGridHeightSync();
        });
    });

    scanBtn.addEventListener('click', async () => {
        scanBtn.disabled = true;
        scanOutput.textContent = 'Scanning library...';
        try {
            const response = await fetch('/scan_library/', { method: 'POST' });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }
            scanOutput.textContent = JSON.stringify(result, null, 2);
            await resetAndLoadImages();
        } catch (error) {
            scanOutput.textContent = `Error: ${error.message}`;
        } finally {
            scanBtn.disabled = false;
        }
    });

    uploadForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const fileInput = document.getElementById('image-files');
        const artistName = document.getElementById('artist-name').value;
        const sourceUrl = document.getElementById('source-url').value;
        const licenseId = document.getElementById('license-select').value;
        const submitButton = uploadForm.querySelector('button[type="submit"]');

        if (!fileInput.files.length) {
            uploadOutput.textContent = 'Please select at least one image file.';
            return;
        }

        const formData = new FormData();
        for (const file of fileInput.files) {
            formData.append('files', file);
        }
        if (artistName) formData.append('artist_name', artistName);
        if (sourceUrl) formData.append('source_url', sourceUrl);
        if (licenseId) formData.append('license_id', licenseId);

        uploadOutput.textContent = 'Uploading...';
        submitButton.disabled = true;

        try {
            const response = await fetch('/upload_images/', {
                method: 'POST',
                body: formData,
            });

            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            uploadOutput.textContent = JSON.stringify(result, null, 2);
            uploadForm.reset();
            await resetAndLoadImages();
        } catch (error) {
            uploadOutput.textContent = `Error: ${error.message}`;
        } finally {
            submitButton.disabled = false;
        }
    });

    infiniteScrollToggle.checked = state.infiniteEnabled;
    debugToggle.checked = state.debugVisible;
    syncLayoutMode();
    syncThumbSize();
    updatePagingUi();

    resizeObserver = new ResizeObserver(() => {
        scheduleGalleryGridHeightSync();
    });
    resizeObserver.observe(detailsPane);
    resizeObserver.observe(detailsContent);
    resizeObserver.observe(detailsEmpty);
    window.addEventListener('resize', scheduleGalleryGridHeightSync);
    scheduleGalleryGridHeightSync();

    Promise.all([loadReferenceData(), resetAndLoadImages()]).catch((error) => {
        galleryGrid.innerHTML = `<p>Startup error: ${error.message}</p>`;
    });
});