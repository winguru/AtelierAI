document.addEventListener('DOMContentLoaded', () => {
    const STORAGE_KEYS = {
        infinite: 'atelier.gallery.infiniteScroll',
        debug: 'atelier.gallery.debugVisible',
        thumbSize: 'atelier.gallery.thumbSize',
        sortOrder: 'atelier.gallery.sortOrder',
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

    function readStoredString(key, fallback, allowedValues) {
        try {
            const raw = window.localStorage.getItem(key);
            if (raw === null) {
                return fallback;
            }
            return allowedValues.includes(raw) ? raw : fallback;
        } catch {
            return fallback;
        }
    }

    function writeStoredString(key, value) {
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
        collections: [],
        imagesStateSignature: null,
        selectedKey: null,
        // Lower page size is intentional for testing end-of-library UI transitions.
        pageSize: TEST_PAGE_SIZE,
        offset: 0,
        hasMore: true,
        loadingPage: false,
        infiniteEnabled: readStoredBool(STORAGE_KEYS.infinite, true),
        debugVisible: readStoredBool(STORAGE_KEYS.debug, true),
        thumbSize: readStoredNumber(STORAGE_KEYS.thumbSize, 165, 120, 260),
        sortOrder: readStoredString(STORAGE_KEYS.sortOrder, 'first_added', ['first_added', 'last_added']),
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
    const sortOrderSelect = document.getElementById('sort-order-select');
    const imageCount = document.getElementById('image-count');
    const searchInput = document.getElementById('search-input');
    const refreshBtn = document.getElementById('refresh-btn');
    const scanBtn = document.getElementById('scan-btn');
    const scanOutput = document.getElementById('scan-output');
    const uploadForm = document.getElementById('upload-form');
    const uploadOutput = document.getElementById('upload-output');
    const importForm = document.getElementById('import-form');
    const importTypeSelect = document.getElementById('import-type');
    const importValueInput = document.getElementById('import-value');
    const importLimitInput = document.getElementById('import-limit');
    const importOutput = document.getElementById('import-output');

    const detailsEmpty = document.getElementById('details-empty');
    const detailsContent = document.getElementById('details-content');
    const detailMediaFrame = document.getElementById('detail-media-frame');
    const detailImage = document.getElementById('detail-image');
    const detailVideo = document.getElementById('detail-video');
    const detailTitle = document.getElementById('detail-title');
    const detailSubtitle = document.getElementById('detail-subtitle');
    const detailMeta = document.getElementById('detail-meta');
    const detailExif = document.getElementById('detail-exif');
    const detailCivitai = document.getElementById('detail-civitai');
    const imageCollectionsList = document.getElementById('image-collections-list');
    const collectionSelect = document.getElementById('collection-select');
    const addToCollectionBtn = document.getElementById('add-to-collection-btn');
    const newCollectionNameInput = document.getElementById('new-collection-name');
    const createCollectionBtn = document.getElementById('create-collection-btn');
    const renameCollectionNameInput = document.getElementById('rename-collection-name');
    const renameCollectionBtn = document.getElementById('rename-collection-btn');
    const deleteCollectionBtn = document.getElementById('delete-collection-btn');
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

    function isVideoAsset(image) {
        const mimetype = typeof image?.mimetype === 'string' ? image.mimetype : '';
        return mimetype.toLowerCase().startsWith('video/');
    }

    function toClientImage(image, indexOffset) {
        const stablePart = image.file_hash || image.file_path || image.file_name || `row-${indexOffset}`;
        return {
            ...image,
            __key: `${stablePart}::${indexOffset}`,
        };
    }

    function pickCaption(image) {
        if (isVideoAsset(image) && typeof image.file_path === 'string' && image.file_path.trim()) {
            return image.file_path.split('/').pop() || image.file_path;
        }
        return image.file_name || image.file_path || image.file_hash || 'Untitled';
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

    async function fetchCollections() {
        const response = await fetch('/collections/');
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return Array.isArray(result) ? result : [];
    }

    async function fetchImagesStateSignature() {
        const response = await fetch('/images/state');
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        const count = Number(result.count) || 0;
        const latestId = Number(result.latest_id) || 0;
        return `${count}:${latestId}`;
    }

    function syncCollectionSelect() {
        const previous = collectionSelect.value;
        collectionSelect.innerHTML = '';

        if (!state.collections.length) {
            const emptyOption = document.createElement('option');
            emptyOption.value = '';
            emptyOption.textContent = '-- No collections --';
            collectionSelect.appendChild(emptyOption);
            return;
        }

        state.collections.forEach((collection) => {
            const option = document.createElement('option');
            option.value = String(collection.id);
            option.textContent = collection.name;
            collectionSelect.appendChild(option);
        });

        if (state.collections.some((c) => String(c.id) === previous)) {
            collectionSelect.value = previous;
        }
    }

    function getSelectedImage() {
        if (!state.selectedKey) {
            return null;
        }
        return state.filteredImages.find((image) => image.__key === state.selectedKey) || null;
    }

    async function refreshCollectionsState() {
        state.collections = await fetchCollections();
        syncCollectionSelect();
    }

    function renderImageCollections(image) {
        imageCollectionsList.innerHTML = '';
        const names = Array.isArray(image?.collection_names) ? image.collection_names : [];
        const ids = Array.isArray(image?.collection_ids) ? image.collection_ids : [];

        if (!names.length) {
            imageCollectionsList.textContent = 'Not in any collections yet.';
            return;
        }

        names.forEach((name, idx) => {
            const pill = document.createElement('span');
            pill.className = 'collection-pill';

            const text = document.createElement('span');
            text.textContent = String(name);

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'collection-pill-remove';
            removeBtn.textContent = 'x';
            removeBtn.title = `Remove from ${name}`;

            const collectionId = ids[idx];
            removeBtn.addEventListener('click', async () => {
                if (!image?.file_hash || !collectionId) {
                    return;
                }
                try {
                    const response = await fetch(`/images/${encodeURIComponent(image.file_hash)}/collections/${collectionId}`, {
                        method: 'DELETE',
                    });
                    const result = await response.json();
                    if (!response.ok) {
                        throw new Error(result.detail || `HTTP ${response.status}`);
                    }
                    image.collection_names = names.filter((_, i) => i !== idx);
                    image.collection_ids = ids.filter((_, i) => i !== idx);
                    renderImageCollections(image);
                    applyFilter();
                } catch (error) {
                    alert(`Could not remove collection: ${error.message}`);
                }
            });

            pill.appendChild(text);
            pill.appendChild(removeBtn);
            imageCollectionsList.appendChild(pill);
        });
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

    function getImageLayoutDebug(activeMedia = detailImage) {
        const computed = window.getComputedStyle(activeMedia);
        const pane = activeMedia.closest('.details-pane');
        return {
            media_tag: activeMedia.tagName.toLowerCase(),
            class: activeMedia.className,
            display: computed.display,
            visibility: computed.visibility,
            opacity: computed.opacity,
            client_size: `${activeMedia.clientWidth}x${activeMedia.clientHeight}`,
            frame_client: detailMediaFrame ? `${detailMediaFrame.clientWidth}x${detailMediaFrame.clientHeight}` : 'N/A',
            pane_client: pane ? `${pane.clientWidth}x${pane.clientHeight}` : 'N/A',
        };
    }

    function showDetails(image) {
        if (!image) {
            detailImage.classList.add('hidden');
            detailImage.style.display = 'none';
            detailImage.removeAttribute('src');
            detailVideo.pause();
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            detailVideo.removeAttribute('src');
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
        const videoMode = isVideoAsset(image);
        currentDebugImage = image;

        // Guard against layout expansion glitches in some browser/video combinations.
        detailMediaFrame.style.width = '100%';
        detailMediaFrame.style.maxWidth = '100%';
        detailMediaFrame.style.minWidth = '0';
        detailVideo.style.width = '100%';
        detailVideo.style.maxWidth = '100%';
        detailVideo.style.minWidth = '0';
        detailVideo.style.height = '100%';
        detailVideo.style.maxHeight = '100%';

        // Force visible state to avoid stale hidden-class/layout edge cases.
        if (videoMode) {
            detailImage.classList.add('hidden');
            detailImage.style.display = 'none';
            detailImage.removeAttribute('src');
            detailVideo.classList.remove('hidden');
            detailVideo.style.display = 'block';
            detailVideo.style.visibility = 'visible';
            detailVideo.style.opacity = '1';
            detailVideo.muted = true;
            detailVideo.loop = true;
            detailVideo.autoplay = true;
            detailVideo.playsInline = true;
            if (imageUrl) {
                detailVideo.src = imageUrl;
                // Autoplay can be blocked by browser policy; ignore rejection.
                detailVideo.play().catch(() => {});
            } else {
                detailVideo.removeAttribute('src');
            }
        } else {
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            detailVideo.pause();
            detailVideo.removeAttribute('src');
            detailImage.classList.remove('hidden');
            detailImage.style.display = 'block';
            detailImage.style.visibility = 'visible';
            detailImage.style.opacity = '1';

            if (imageUrl) {
                detailImage.src = imageUrl;
            } else {
                detailImage.removeAttribute('src');
            }
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
        renderImageCollections(image);

        setDebugBadge({
            key: image.__key,
            file_hash: image.file_hash,
            file_path: image.file_path,
            url: imageUrl,
            status: imageUrl ? 'loading' : 'missing-url',
            ...getImageLayoutDebug(videoMode ? detailVideo : detailImage),
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
            const caption = pickCaption(image);
            const tile = document.createElement('button');
            tile.className = `tile ${state.selectedKey === image.__key ? 'active' : ''}`;
            tile.type = 'button';
            tile.dataset.key = image.__key;

            const mediaUrl = getImageUrl(image);
            const videoMode = isVideoAsset(image);

            let mediaNode;
            if (videoMode) {
                const video = document.createElement('video');
                video.muted = true;
                video.autoplay = true;
                video.loop = true;
                video.playsInline = true;
                video.preload = 'metadata';
                video.src = mediaUrl;
                video.play().catch(() => {});
                mediaNode = video;
            } else {
                const img = document.createElement('img');
                img.loading = 'lazy';
                img.alt = safeText(caption);
                img.src = mediaUrl;
                mediaNode = img;
            }

            const captionSpan = document.createElement('span');
            captionSpan.className = 'tile-caption';

            const primaryCaption = document.createElement('span');
            primaryCaption.className = 'tile-caption-primary';
            primaryCaption.textContent = safeText(caption);
            captionSpan.appendChild(primaryCaption);

            const collectionNames = Array.isArray(image.collection_names)
                ? image.collection_names.filter((name) => typeof name === 'string' && name.trim())
                : [];
            if (collectionNames.length) {
                const collectionsOverlay = document.createElement('div');
                collectionsOverlay.className = 'tile-collections';
                collectionNames.slice(0, 5).forEach((name) => {
                    const item = document.createElement('span');
                    item.className = 'tile-collection-item';
                    item.textContent = name;
                    collectionsOverlay.appendChild(item);
                });
                tile.appendChild(collectionsOverlay);
            }

            tile.appendChild(mediaNode);
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
                    ...(Array.isArray(image.collection_names) ? image.collection_names : []),
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
            const response = await fetch(`/images/?skip=${state.offset}&limit=${state.pageSize}&sort_by=${encodeURIComponent(state.sortOrder)}`);
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
        const [artistsRes, licensesRes, collections] = await Promise.all([
            fetch('/artists/'),
            fetch('/licenses/'),
            fetchCollections(),
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

        state.collections = collections;
        syncCollectionSelect();
    }

    async function resetAndLoadImages(options = {}) {
        const preserveSelection = options.preserveSelection === true;
        const showRefreshUi = options.showRefreshUi !== false;
        const previousSelectedKey = preserveSelection ? state.selectedKey : null;

        if (showRefreshUi) {
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Refreshing...';
        }

        state.allImages = [];
        state.filteredImages = [];
        state.selectedKey = preserveSelection ? previousSelectedKey : null;
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
            try {
                state.imagesStateSignature = await fetchImagesStateSignature();
            } catch {
                // Ignore state-signature errors and keep gallery usable.
            }

            if (showRefreshUi) {
                refreshBtn.disabled = false;
                refreshBtn.textContent = 'Refresh';
            }
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
    detailVideo.addEventListener('loadedmetadata', () => {
        if (!currentDebugImage) {
            return;
        }

        // Re-apply constraints after metadata load, as some browsers adjust replaced-element sizing.
        detailMediaFrame.style.width = '100%';
        detailMediaFrame.style.maxWidth = '100%';
        detailMediaFrame.style.minWidth = '0';
        detailVideo.style.width = '100%';
        detailVideo.style.maxWidth = '100%';
        detailVideo.style.minWidth = '0';
        detailVideo.style.height = '100%';
        detailVideo.style.maxHeight = '100%';

        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            url: detailVideo.currentSrc || getImageUrl(currentDebugImage),
            status: 'video-loadedmetadata',
            video_natural: `${detailVideo.videoWidth}x${detailVideo.videoHeight}`,
            ...getImageLayoutDebug(detailVideo),
        });
        scheduleGalleryGridHeightSync();
    });
    detailVideo.addEventListener('error', () => {
        if (!currentDebugImage) {
            return;
        }
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            url: detailVideo.currentSrc || getImageUrl(currentDebugImage),
            status: 'video-error',
            ...getImageLayoutDebug(detailVideo),
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
    sortOrderSelect.addEventListener('change', async () => {
        state.sortOrder = sortOrderSelect.value === 'last_added' ? 'last_added' : 'first_added';
        writeStoredString(STORAGE_KEYS.sortOrder, state.sortOrder);
        await resetAndLoadImages();
    });
    addToCollectionBtn.addEventListener('click', async () => {
        const image = getSelectedImage();
        if (!image?.file_hash) {
            return;
        }

        const collectionId = Number(collectionSelect.value);
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection first.');
            return;
        }

        try {
            const response = await fetch(`/images/${encodeURIComponent(image.file_hash)}/collections/${collectionId}`, {
                method: 'POST',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            const selectedCollection = state.collections.find((c) => c.id === collectionId);
            if (!selectedCollection) {
                return;
            }

            const names = Array.isArray(image.collection_names) ? image.collection_names : [];
            const ids = Array.isArray(image.collection_ids) ? image.collection_ids : [];
            if (!ids.includes(collectionId)) {
                image.collection_ids = ids.concat(collectionId);
                image.collection_names = names.concat(selectedCollection.name);
            }
            renderImageCollections(image);
            applyFilter();
        } catch (error) {
            alert(`Could not add image to collection: ${error.message}`);
        }
    });
    createCollectionBtn.addEventListener('click', async () => {
        const name = newCollectionNameInput.value.trim();
        if (!name) {
            alert('Enter a collection name first.');
            return;
        }

        try {
            const response = await fetch('/collections/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ name }),
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }
            newCollectionNameInput.value = '';
            await refreshCollectionsState();
            collectionSelect.value = String(result.id);
        } catch (error) {
            alert(`Could not create collection: ${error.message}`);
        }
    });
    renameCollectionBtn.addEventListener('click', async () => {
        const collectionId = Number(collectionSelect.value);
        const name = renameCollectionNameInput.value.trim();
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection to rename.');
            return;
        }
        if (!name) {
            alert('Enter a new collection name first.');
            return;
        }

        try {
            const response = await fetch(`/collections/${collectionId}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ name }),
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }
            renameCollectionNameInput.value = '';
            await refreshCollectionsState();
            collectionSelect.value = String(collectionId);

            state.allImages.forEach((img) => {
                if (!Array.isArray(img.collection_ids) || !Array.isArray(img.collection_names)) {
                    return;
                }
                const idx = img.collection_ids.indexOf(collectionId);
                if (idx >= 0) {
                    img.collection_names[idx] = result.name;
                }
            });
            applyFilter();
        } catch (error) {
            alert(`Could not rename collection: ${error.message}`);
        }
    });
    deleteCollectionBtn.addEventListener('click', async () => {
        const collectionId = Number(collectionSelect.value);
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection to delete.');
            return;
        }

        if (!window.confirm('Delete this collection? This removes membership links but keeps images.')) {
            return;
        }

        try {
            const response = await fetch(`/collections/${collectionId}`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            await refreshCollectionsState();
            state.allImages.forEach((img) => {
                if (!Array.isArray(img.collection_ids) || !Array.isArray(img.collection_names)) {
                    return;
                }
                const idx = img.collection_ids.indexOf(collectionId);
                if (idx >= 0) {
                    img.collection_ids.splice(idx, 1);
                    img.collection_names.splice(idx, 1);
                }
            });
            applyFilter();
        } catch (error) {
            alert(`Could not delete collection: ${error.message}`);
        }
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

    function updateImportInputPlaceholder() {
        if (importTypeSelect.value === 'image') {
            importValueInput.placeholder = 'https://civitai.com/images/... or 123456';
        } else {
            importValueInput.placeholder = 'https://civitai.com/collections/... or 123456';
        }
    }

    importTypeSelect.addEventListener('change', updateImportInputPlaceholder);

    function formatImportCounters(result) {
        const added = Number(result?.images_added || 0);
        const skipped = Number(result?.images_skipped || 0);
        const recovered = Number(result?.images_recovered || 0);
        const requested = Number(result?.requested || 0);
        return `Added ${added} | Skipped ${skipped} | Recovered ${recovered} | Requested ${requested}`;
    }

    importForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const importType = importTypeSelect.value;
        const rawValue = importValueInput.value.trim();
        const rawLimit = importLimitInput.value.trim();
        const submitButton = document.getElementById('import-submit');

        if (!rawValue) {
            importOutput.textContent = 'Please provide a CivitAI URL or numeric ID.';
            return;
        }

        const payload = {
            import_type: importType,
            value: rawValue,
        };

        if (importType === 'collection' && rawLimit) {
            const parsedLimit = Number(rawLimit);
            if (!Number.isInteger(parsedLimit) || parsedLimit <= 0) {
                importOutput.textContent = 'Limit must be a positive whole number.';
                return;
            }
            payload.limit = parsedLimit;
        }

        importOutput.textContent = 'Importing from CivitAI...';
        submitButton.disabled = true;
        let liveRefreshTimer = null;
        let liveRefreshInFlight = false;

        // While import is running, poll backend state and only refresh gallery when data changed.
        liveRefreshTimer = window.setInterval(async () => {
            if (liveRefreshInFlight) {
                return;
            }
            liveRefreshInFlight = true;
            try {
                const latestSignature = await fetchImagesStateSignature();
                if (latestSignature !== state.imagesStateSignature) {
                    await resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
                }
            } catch {
                // Ignore transient polling/refresh errors during long-running imports.
            } finally {
                liveRefreshInFlight = false;
            }
        }, 2500);

        try {
            const response = await fetch('/import_civitai/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            const summaryLine = formatImportCounters(result);
            importOutput.textContent = `${summaryLine}\n\n${JSON.stringify(result, null, 2)}`;
            importForm.reset();
            importTypeSelect.value = importType;
            updateImportInputPlaceholder();
            await refreshCollectionsState();
            if (result && result.local_collection && result.local_collection.id) {
                collectionSelect.value = String(result.local_collection.id);
            }
            await resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
        } catch (error) {
            importOutput.textContent = `Error: ${error.message}`;
        } finally {
            if (liveRefreshTimer !== null) {
                window.clearInterval(liveRefreshTimer);
            }
            submitButton.disabled = false;
        }
    });

    infiniteScrollToggle.checked = state.infiniteEnabled;
    debugToggle.checked = state.debugVisible;
    sortOrderSelect.value = state.sortOrder;
    syncLayoutMode();
    syncThumbSize();
    updatePagingUi();
    updateImportInputPlaceholder();

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