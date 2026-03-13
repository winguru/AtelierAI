document.addEventListener('DOMContentLoaded', () => {
    const STORAGE_KEYS = {
        infinite: 'atelier.gallery.infiniteScroll',
        debug: 'atelier.gallery.debugVisible',
        autoRefresh: 'atelier.gallery.autoRefresh',
        fullscreenLoop: 'atelier.gallery.fullscreenLoop',
        thumbSize: 'atelier.gallery.thumbSize',
        sortOrder: 'atelier.gallery.sortOrder',
    };
    const TEST_PAGE_SIZE = 120;
    const BASE_SYNC_MARGIN_PX = 4;
    // Fine-tuning offset for near-equal pane heights in this environment.
    // If this causes overfitting across devices/zoom/font settings, set to 0.
    const PANE_HEIGHT_CALIBRATION_PX = 2;
    const VIDEO_POSTER_CAPTURE_CONCURRENCY = 1;
    const VIDEO_POSTER_CAPTURE_TIMEOUT_MS = 8000;

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
        totalImageCount: 0,
        artistNames: [],
        collections: [],
        imagesStateSignature: null,
        selectedKey: null,
        fullscreenSelectedKey: null,
        searchRunId: 0,
        // Lower page size is intentional for testing end-of-library UI transitions.
        pageSize: TEST_PAGE_SIZE,
        offset: 0,
        hasMore: true,
        loadingPage: false,
        infiniteEnabled: readStoredBool(STORAGE_KEYS.infinite, true),
        debugVisible: readStoredBool(STORAGE_KEYS.debug, true),
        autoRefreshEnabled: readStoredBool(STORAGE_KEYS.autoRefresh, true),
        fullscreenLoopEnabled: readStoredBool(STORAGE_KEYS.fullscreenLoop, true),
        thumbSize: readStoredNumber(STORAGE_KEYS.thumbSize, 165, 120, 260),
        sortOrder: readStoredString(STORAGE_KEYS.sortOrder, 'first_added', ['first_added', 'last_added']),
        treeTagFilter: null,
        runtimeWarnings: [],
        mediaCapabilities: null,
        videoPosterCache: new Map(),
        videoPosterInflight: new Map(),
        videoPosterQueue: [],
        videoPosterActiveCaptures: 0,
    };

    const artistDatalist = document.getElementById('artist-suggestions');
    const licenseSelect = document.getElementById('license-select');
    const appShell = document.querySelector('.app-shell');
    const galleryPane = document.querySelector('.gallery-pane');
    const galleryToolbar = document.querySelector('.gallery-toolbar');
    const runtimeWarningBanner = document.getElementById('runtime-warning-banner');
    const galleryFooter = document.querySelector('.gallery-footer');
    const galleryGrid = document.getElementById('gallery-grid');
    const galleryStatus = document.getElementById('gallery-status');
    const loadMoreBtn = document.getElementById('load-more-btn');
    const infiniteScrollToggle = document.getElementById('infinite-scroll-toggle');
    const debugToggle = document.getElementById('debug-toggle');
    const treeTagFilterIndicator = document.getElementById('tree-tag-filter-indicator');
    const treeTagFilterChip = document.getElementById('tree-tag-filter-chip');
    const treeTagFilterText = document.getElementById('tree-tag-filter-text');
    const treeTagFilterClear = document.getElementById('tree-tag-filter-clear');
    const thumbSizeSlider = document.getElementById('thumb-size-slider');
    const thumbSizeValue = document.getElementById('thumb-size-value');
    const thumbPresetButtons = Array.from(document.querySelectorAll('.thumb-preset'));
    const sortOrderSelect = document.getElementById('sort-order-select');
    const imageCount = document.getElementById('image-count');
    const searchInput = document.getElementById('search-input');
    const refreshBtn = document.getElementById('refresh-btn');
    const autoRefreshToggle = document.getElementById('auto-refresh-toggle');
    const scanBtn = document.getElementById('scan-btn');
    const purgeDeletedBtn = document.getElementById('purge-deleted-btn');
    const scanOutput = document.getElementById('scan-output');
    const uploadForm = document.getElementById('upload-form');
    const uploadOutput = document.getElementById('upload-output');
    const importForm = document.getElementById('import-form');
    const importTypeSelect = document.getElementById('import-type');
    const importValueInput = document.getElementById('import-value');
    const importLimitInput = document.getElementById('import-limit');
    const syncCivitaiCollectionsBtn = document.getElementById('sync-civitai-collections-btn');
    const importOutput = document.getElementById('import-output');
    const inactiveStatusFilter = document.getElementById('inactive-status-filter');
    const refreshInactiveListBtn = document.getElementById('refresh-inactive-list-btn');
    const inactiveImagesList = document.getElementById('inactive-images-list');
    const utilitiesStatusBadge = document.getElementById('utilities-status-badge');
    const utilitiesOutput = document.getElementById('utilities-output');
    const taxonomySummaryBadge = document.getElementById('taxonomy-summary-badge');
    const taxonomyRefreshBtn = document.getElementById('taxonomy-refresh-btn');
    const taxonomyMergeDryRunToggle = document.getElementById('taxonomy-merge-dry-run');
    const taxonomyParentDryRunToggle = document.getElementById('taxonomy-parent-dry-run');
    const taxonomyBootstrapForm = document.getElementById('taxonomy-bootstrap-form');
    const taxonomyBootstrapAuthority = document.getElementById('taxonomy-bootstrap-authority');
    const taxonomyBootstrapFormat = document.getElementById('taxonomy-bootstrap-format');
    const taxonomyBootstrapDryRunToggle = document.getElementById('taxonomy-bootstrap-dry-run');
    const taxonomyBootstrapRaw = document.getElementById('taxonomy-bootstrap-raw');
    const taxonomyBootstrapFile = document.getElementById('taxonomy-bootstrap-file');
    const taxonomyBootstrapDropzone = document.getElementById('taxonomy-bootstrap-dropzone');
    const taxonomyConceptSearchForm = document.getElementById('taxonomy-concept-search-form');
    const taxonomyConceptQuery = document.getElementById('taxonomy-concept-query');
    const taxonomyConceptsList = document.getElementById('taxonomy-concepts-list');
    const taxonomyAliasForm = document.getElementById('taxonomy-alias-form');
    const taxonomyAliasConceptId = document.getElementById('taxonomy-alias-concept-id');
    const taxonomyAliasValue = document.getElementById('taxonomy-alias-value');
    const taxonomyAliasType = document.getElementById('taxonomy-alias-type');
    const taxonomyAliasAuthority = document.getElementById('taxonomy-alias-authority');
    const taxonomyMergeForm = document.getElementById('taxonomy-merge-form');
    const taxonomyMergeSourceId = document.getElementById('taxonomy-merge-source-id');
    const taxonomyMergeTargetId = document.getElementById('taxonomy-merge-target-id');
    const taxonomyCreateConceptForm = document.getElementById('taxonomy-create-concept-form');
    const taxonomyNewConceptName = document.getElementById('taxonomy-new-concept-name');
    const taxonomyNewConceptParentId = document.getElementById('taxonomy-new-concept-parent-id');
    const taxonomyParentForm = document.getElementById('taxonomy-parent-form');
    const taxonomyParentConceptId = document.getElementById('taxonomy-parent-concept-id');
    const taxonomyParentNewParentId = document.getElementById('taxonomy-parent-new-parent-id');
    const taxonomyConceptUpdateForm = document.getElementById('taxonomy-concept-update-form');
    const taxonomyUpdateConceptId = document.getElementById('taxonomy-update-concept-id');
    const taxonomyUpdateCanonicalName = document.getElementById('taxonomy-update-canonical-name');
    const taxonomyUpdateDescription = document.getElementById('taxonomy-update-description');
    const taxonomyTreeExpandAllBtn = document.getElementById('taxonomy-tree-expand-all');
    const taxonomyTreeCollapseAllBtn = document.getElementById('taxonomy-tree-collapse-all');
    const taxonomyTreeRootDrop = document.getElementById('taxonomy-tree-root-drop');
    const taxonomyDuplicatesList = document.getElementById('taxonomy-duplicates-list');
    const taxonomyTree = document.getElementById('taxonomy-tree');
    const taxonomyOutput = document.getElementById('taxonomy-output');
    const treeEmbedFrame = document.getElementById('tree-embed-frame');
    let taxonomyDragSourceConceptId = null;
    const taxonomyExpandedNodeIds = new Set();
    let taxonomyTreeData = [];
    let taxonomyRootRenderLimit = 200;
    let toastTimer = null;
    let taxonomyBootstrapDroppedFile = null;

    function ensureToastNode() {
        let node = document.getElementById('app-toast');
        if (node) {
            return node;
        }
        node = document.createElement('div');
        node.id = 'app-toast';
        node.className = 'app-toast hidden';
        node.setAttribute('role', 'status');
        node.setAttribute('aria-live', 'polite');
        document.body.appendChild(node);
        return node;
    }

    function showToast(message, variant = 'info') {
        const node = ensureToastNode();
        node.textContent = message;
        node.classList.remove('hidden', 'toast-info', 'toast-success', 'toast-warn');
        node.classList.add(`toast-${variant}`);

        if (toastTimer) {
            window.clearTimeout(toastTimer);
        }
        toastTimer = window.setTimeout(() => {
            node.classList.add('hidden');
        }, 2300);
    }

    const detailsEmpty = document.getElementById('details-empty');
    const detailsContent = document.getElementById('details-content');
    const detailMediaFrame = document.getElementById('detail-media-frame');
    const detailImage = document.getElementById('detail-image');
    const detailVideo = document.getElementById('detail-video');
    const fullscreenPreview = document.getElementById('fullscreen-preview');
    const fullscreenLoopBtn = document.getElementById('fullscreen-loop-btn');
    const fullscreenCloseBtn = document.getElementById('fullscreen-close-btn');
    const fullscreenImage = document.getElementById('fullscreen-image');
    const fullscreenVideo = document.getElementById('fullscreen-video');
    const detailTitle = document.getElementById('detail-title');
    const detailSubtitle = document.getElementById('detail-subtitle');
    const repairImageBtn = document.getElementById('repair-image-btn');
    const deleteImageFileBtn = document.getElementById('delete-image-file-btn');
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
    let searchDebounceTimer = null;
    let posterCaptureObserver = null;

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

    function renderRuntimeWarnings() {
        if (!runtimeWarningBanner) {
            return;
        }

        const warnings = Array.isArray(state.runtimeWarnings)
            ? state.runtimeWarnings.filter((message) => typeof message === 'string' && message.trim())
            : [];

        runtimeWarningBanner.innerHTML = '';
        if (!warnings.length) {
            runtimeWarningBanner.classList.add('hidden');
            return;
        }

        const title = document.createElement('p');
        title.className = 'runtime-warning-title';
        title.textContent = 'Runtime Warnings';

        const list = document.createElement('ul');
        list.className = 'runtime-warning-list';
        warnings.forEach((message) => {
            const item = document.createElement('li');
            item.textContent = message;
            list.appendChild(item);
        });

        runtimeWarningBanner.appendChild(title);
        runtimeWarningBanner.appendChild(list);
        runtimeWarningBanner.classList.remove('hidden');
    }

    function normalizeTagName(value) {
        return String(value || '').trim().toLowerCase();
    }

    function addTagName(setRef, raw) {
        const normalized = normalizeTagName(raw);
        if (!normalized) {
            return;
        }
        setRef.add(normalized);
    }

    function addTagCollection(setRef, value) {
        if (value == null) {
            return;
        }
        if (Array.isArray(value)) {
            value.forEach((item) => addTagCollection(setRef, item));
            return;
        }
        if (typeof value === 'string') {
            value
                .split(',')
                .map((part) => part.trim())
                .filter(Boolean)
                .forEach((part) => addTagName(setRef, part));
            return;
        }
        if (typeof value === 'object') {
            if (typeof value.name === 'string') {
                addTagName(setRef, value.name);
            }
            if (typeof value.tag === 'string') {
                addTagName(setRef, value.tag);
            }
            if (typeof value.label === 'string') {
                addTagName(setRef, value.label);
            }
        }
    }

    function extractImageScopeTags(image) {
        const bySource = {
            civitai: new Set(),
            danbooru: new Set(),
            prompt: new Set(),
            user: new Set(),
        };

        if (!image || typeof image !== 'object') {
            return {
                civitai: [],
                danbooru: [],
                prompt: [],
                user: [],
            };
        }

        const civitai = image.civitai_data || image.civitai || {};
        addTagCollection(bySource.civitai, civitai?.tags);
        addTagCollection(bySource.civitai, civitai?.meta?.tags);
        addTagCollection(bySource.civitai, civitai?.image?.tags);

        const exif = image.exif_data && typeof image.exif_data === 'object' ? image.exif_data : {};
        addTagCollection(bySource.prompt, image.prompt_tags);
        addTagCollection(bySource.prompt, exif?.prompt_tags);
        addTagCollection(bySource.prompt, exif?.prompt);
        addTagCollection(bySource.danbooru, image.danbooru_tags);
        addTagCollection(bySource.danbooru, exif?.danbooru_tags);
        addTagCollection(bySource.danbooru, exif?.danbooru);
        addTagCollection(bySource.user, image.user_tags);
        addTagCollection(bySource.user, exif?.user_tags);

        if (Array.isArray(image.tags)) {
            image.tags.forEach((tag) => {
                if (typeof tag === 'string') {
                    addTagName(bySource.user, tag);
                    return;
                }
                if (!tag || typeof tag !== 'object') {
                    return;
                }
                const name = typeof tag.name === 'string' ? tag.name : (typeof tag.tag === 'string' ? tag.tag : '');
                if (!name) {
                    return;
                }
                const source = normalizeTagName(tag.source);
                if (source === 'civitai' || source === 'danbooru' || source === 'prompt' || source === 'user') {
                    addTagName(bySource[source], name);
                } else {
                    addTagName(bySource.user, name);
                }
            });
        }

        return {
            civitai: Array.from(bySource.civitai),
            danbooru: Array.from(bySource.danbooru),
            prompt: Array.from(bySource.prompt),
            user: Array.from(bySource.user),
        };
    }

    function postSelectedImageTagsToTree(image) {
        if (!treeEmbedFrame || !treeEmbedFrame.contentWindow) {
            return;
        }

        const payload = {
            imageKey: image?.__key || null,
            bySource: extractImageScopeTags(image),
        };

        treeEmbedFrame.contentWindow.postMessage(
            {
                type: 'atelier:selected-image-tags',
                payload,
            },
            window.location.origin,
        );
    }

    function setTreeTagFilter(payload) {
        if (!payload || typeof payload !== 'object') {
            state.treeTagFilter = null;
            renderTreeTagFilterIndicator();
            return;
        }

        const source = normalizeTagName(payload.source);
        const name = normalizeTagName(payload.name);
        if (!source || !name) {
            state.treeTagFilter = null;
            renderTreeTagFilterIndicator();
            return;
        }

        state.treeTagFilter = {
            source,
            name,
        };
        renderTreeTagFilterIndicator();
    }

    function renderTreeTagFilterIndicator() {
        if (!treeTagFilterIndicator || !treeTagFilterText || !treeTagFilterChip) {
            return;
        }

        if (!state.treeTagFilter) {
            treeTagFilterIndicator.classList.add('hidden');
            treeTagFilterText.textContent = '';
            treeTagFilterChip.className = 'tree-tag-filter-chip';
            return;
        }

        treeTagFilterIndicator.classList.remove('hidden');
        treeTagFilterChip.className = `tree-tag-filter-chip source-${state.treeTagFilter.source}`;
        treeTagFilterText.innerHTML = `<strong>${state.treeTagFilter.source}</strong>: ${state.treeTagFilter.name}`;
    }

    function imageMatchesTreeTagFilter(image) {
        if (!state.treeTagFilter) {
            return true;
        }
        const bySource = extractImageScopeTags(image);
        const sourceTags = Array.isArray(bySource?.[state.treeTagFilter.source])
            ? bySource[state.treeTagFilter.source]
            : [];
        return sourceTags.some((tagName) => normalizeTagName(tagName) === state.treeTagFilter.name);
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
        const versionToken = image.date_modified || image.id || image.file_size || image.file_hash;
        const version = versionToken ? `?v=${encodeURIComponent(String(versionToken))}` : '';
        return `/image_library/${encodedPath}${version}`;
    }

    function isVideoAsset(image) {
        const mimetype = typeof image?.mimetype === 'string' ? image.mimetype : '';
        return mimetype.toLowerCase().startsWith('video/');
    }

    function isPngAsset(image) {
        const mimetype = typeof image?.mimetype === 'string' ? image.mimetype.toLowerCase() : '';
        const filePath = typeof image?.file_path === 'string' ? image.file_path.toLowerCase() : '';
        return mimetype === 'image/png' || filePath.endsWith('.png');
    }

    function releaseVideoElement(video) {
        if (!(video instanceof HTMLVideoElement)) {
            return;
        }
        video.pause();
        video.removeAttribute('src');
        video.load();
    }

    function primeVideoElement(video, src) {
        if (!(video instanceof HTMLVideoElement) || !src) {
            return false;
        }
        if (video.dataset.previewSrc !== src || !video.getAttribute('src')) {
            video.src = src;
            video.dataset.previewSrc = src;
        }
        return true;
    }

    function getVideoPosterCacheKey(image, mediaUrl) {
        if (image && typeof image.file_hash === 'string' && image.file_hash.trim()) {
            return `hash:${image.file_hash}`;
        }
        return mediaUrl ? `url:${mediaUrl}` : '';
    }

    function getVideoPosterUrl(image) {
        const candidates = [
            image?.video_poster_url,
            image?.poster_url,
            image?.preview_image_url,
            image?.json_metadata?.video_poster_url,
            image?.json_metadata?.poster_url,
        ];
        for (const candidate of candidates) {
            if (typeof candidate === 'string' && candidate.trim()) {
                return candidate;
            }
        }
        return '';
    }

    function applyTileVideoPoster(tile, video, posterUrl) {
        if (!(video instanceof HTMLVideoElement)) {
            return;
        }
        if (posterUrl) {
            video.poster = posterUrl;
            tile.classList.add('has-poster');
        } else {
            video.removeAttribute('poster');
            tile.classList.remove('has-poster');
        }
    }

    function cleanupPosterCaptureVideo(video) {
        if (!(video instanceof HTMLVideoElement)) {
            return;
        }
        video.pause();
        video.removeAttribute('src');
        video.load();
    }

    function captureVideoPosterFromSource(src) {
        return new Promise((resolve) => {
            const loader = document.createElement('video');
            let finished = false;
            let timeoutId = 0;

            const finalize = (posterUrl) => {
                if (finished) {
                    return;
                }
                finished = true;
                if (timeoutId) {
                    window.clearTimeout(timeoutId);
                }
                cleanupPosterCaptureVideo(loader);
                resolve(posterUrl || null);
            };

            const captureFrame = () => {
                const width = loader.videoWidth || 0;
                const height = loader.videoHeight || 0;
                if (!width || !height) {
                    finalize(null);
                    return;
                }

                const scale = Math.min(1, 320 / Math.max(width, height));
                const canvas = document.createElement('canvas');
                canvas.width = Math.max(1, Math.round(width * scale));
                canvas.height = Math.max(1, Math.round(height * scale));
                const context = canvas.getContext('2d');
                if (!context) {
                    finalize(null);
                    return;
                }

                try {
                    context.drawImage(loader, 0, 0, canvas.width, canvas.height);
                    finalize(canvas.toDataURL('image/jpeg', 0.82));
                } catch {
                    finalize(null);
                }
            };

            const onLoadedMetadata = () => {
                const duration = Number.isFinite(loader.duration) ? loader.duration : 0;
                const targetTime = duration > 0.18 ? Math.min(0.15, duration / 3) : 0;
                if (targetTime > 0) {
                    loader.addEventListener('seeked', captureFrame, { once: true });
                    try {
                        loader.currentTime = targetTime;
                        return;
                    } catch {
                        // Fall through to loadeddata capture.
                    }
                }

                if (loader.readyState >= 2) {
                    captureFrame();
                } else {
                    loader.addEventListener('loadeddata', captureFrame, { once: true });
                }
            };

            loader.muted = true;
            loader.preload = 'metadata';
            loader.playsInline = true;
            loader.addEventListener('loadedmetadata', onLoadedMetadata, { once: true });
            loader.addEventListener('error', () => finalize(null), { once: true });
            timeoutId = window.setTimeout(() => finalize(null), VIDEO_POSTER_CAPTURE_TIMEOUT_MS);
            loader.src = src;
            loader.load();
        });
    }

    function pumpVideoPosterQueue() {
        while (
            state.videoPosterActiveCaptures < VIDEO_POSTER_CAPTURE_CONCURRENCY
            && state.videoPosterQueue.length
        ) {
            const job = state.videoPosterQueue.shift();
            if (!job) {
                return;
            }

            state.videoPosterActiveCaptures += 1;
            captureVideoPosterFromSource(job.mediaUrl)
                .then((posterUrl) => {
                    state.videoPosterCache.set(job.cacheKey, posterUrl || null);
                    const callbacks = state.videoPosterInflight.get(job.cacheKey) || [];
                    state.videoPosterInflight.delete(job.cacheKey);
                    callbacks.forEach((callback) => callback(posterUrl || ''));
                })
                .finally(() => {
                    state.videoPosterActiveCaptures = Math.max(0, state.videoPosterActiveCaptures - 1);
                    pumpVideoPosterQueue();
                });
        }
    }

    function requestVideoPoster(image, mediaUrl, onReady) {
        const cacheKey = getVideoPosterCacheKey(image, mediaUrl);
        if (!cacheKey || !mediaUrl) {
            onReady('');
            return;
        }

        const directPosterUrl = getVideoPosterUrl(image);
        if (directPosterUrl) {
            state.videoPosterCache.set(cacheKey, directPosterUrl);
            onReady(directPosterUrl);
            return;
        }

        if (state.videoPosterCache.has(cacheKey)) {
            onReady(state.videoPosterCache.get(cacheKey) || '');
            return;
        }

        const inflight = state.videoPosterInflight.get(cacheKey);
        if (inflight) {
            inflight.push(onReady);
            return;
        }

        state.videoPosterInflight.set(cacheKey, [onReady]);
        state.videoPosterQueue.push({ cacheKey, mediaUrl });
        pumpVideoPosterQueue();
    }

    function ensurePosterCaptureObserver() {
        if (posterCaptureObserver || !(galleryGrid instanceof HTMLElement)) {
            return;
        }

        posterCaptureObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) {
                    return;
                }

                posterCaptureObserver.unobserve(entry.target);
                const tile = entry.target;
                const image = tile.__posterImage;
                const video = tile.__posterVideo;
                const mediaUrl = tile.__posterMediaUrl;

                if (!(video instanceof HTMLVideoElement) || !image || !mediaUrl) {
                    return;
                }

                requestVideoPoster(image, mediaUrl, (posterUrl) => {
                    if (!tile.isConnected || tile.__posterVideo !== video) {
                        return;
                    }
                    applyTileVideoPoster(tile, video, posterUrl);
                });
            });
        }, {
            root: galleryGrid,
            rootMargin: '220px 0px',
            threshold: 0.01,
        });
    }

    function observeTileForPosterCapture(tile, image, video, mediaUrl) {
        ensurePosterCaptureObserver();
        if (!posterCaptureObserver) {
            return;
        }

        tile.__posterImage = image;
        tile.__posterVideo = video;
        tile.__posterMediaUrl = mediaUrl;
        posterCaptureObserver.observe(tile);
    }

    function startTileVideoPreview(tile, video, src) {
        if (!primeVideoElement(video, src)) {
            return;
        }
        tile.classList.add('previewing');
        video.play().catch(() => {});
    }

    function stopTileVideoPreview(tile, video) {
        tile.classList.remove('previewing');
        releaseVideoElement(video);
        delete video.dataset.previewSrc;
    }

    function closeFullscreenPreview() {
        fullscreenPreview.classList.add('hidden');
        fullscreenPreview.setAttribute('aria-hidden', 'true');
        state.fullscreenSelectedKey = null;
        fullscreenImage.classList.add('hidden');
        fullscreenImage.removeAttribute('src');
        releaseVideoElement(fullscreenVideo);
        fullscreenVideo.classList.add('hidden');
    }

    function syncFullscreenLoopUi() {
        if (!fullscreenLoopBtn) {
            return;
        }
        fullscreenLoopBtn.textContent = state.fullscreenLoopEnabled ? 'Loop: On' : 'Loop: Off';
        fullscreenLoopBtn.setAttribute('aria-pressed', state.fullscreenLoopEnabled ? 'true' : 'false');
    }

    function openFullscreenPreviewFromImage(image) {
        const mediaUrl = getImageUrl(image);
        if (!mediaUrl) {
            return;
        }

        state.fullscreenSelectedKey = image.__key || null;

        const videoMode = isVideoAsset(image);
        fullscreenPreview.classList.remove('hidden');
        fullscreenPreview.setAttribute('aria-hidden', 'false');
        if (videoMode) {
            fullscreenImage.classList.add('hidden');
            fullscreenImage.removeAttribute('src');
            fullscreenVideo.classList.remove('hidden');
            fullscreenVideo.loop = state.fullscreenLoopEnabled;
            fullscreenVideo.preload = 'metadata';
            primeVideoElement(fullscreenVideo, mediaUrl);
            fullscreenVideo.play().catch(() => {});
        } else {
            releaseVideoElement(fullscreenVideo);
            fullscreenVideo.classList.add('hidden');
            fullscreenImage.classList.remove('hidden');
            fullscreenImage.src = mediaUrl;
            fullscreenImage.alt = safeText(image.file_name, 'Fullscreen preview');
        }
    }

    async function navigateFullscreenBy(delta) {
        if (fullscreenPreview.classList.contains('hidden')) {
            return;
        }
        if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
            return;
        }

        const currentKey = state.fullscreenSelectedKey || state.selectedKey;
        const currentIndex = state.filteredImages.findIndex((img) => img.__key === currentKey);
        const baseIndex = currentIndex >= 0 ? currentIndex : 0;

        if (delta < 0 && baseIndex <= 0) {
            return;
        }

        let nextIndex = baseIndex + delta;
        if (delta > 0 && nextIndex >= state.filteredImages.length) {
            if (state.hasMore && !state.loadingPage) {
                const priorLength = state.filteredImages.length;
                await loadNextPage();

                // If the page load did not expand results (or filter excludes them), stay put.
                if (state.filteredImages.length <= priorLength) {
                    return;
                }

                const refreshedKey = state.fullscreenSelectedKey || state.selectedKey;
                const refreshedIndex = state.filteredImages.findIndex((img) => img.__key === refreshedKey);
                const anchoredIndex = refreshedIndex >= 0 ? refreshedIndex : baseIndex;
                nextIndex = anchoredIndex + 1;
            } else {
                return;
            }
        }

        if (nextIndex < 0 || nextIndex >= state.filteredImages.length) {
            return;
        }

        const nextImage = state.filteredImages[nextIndex];
        if (!nextImage) {
            return;
        }

        state.fullscreenSelectedKey = nextImage.__key;
        state.selectedKey = nextImage.__key;
        renderGallery();
        showDetails(nextImage);
        openFullscreenPreviewFromImage(nextImage);
    }

    function navigateFullscreenToBoundary(target) {
        if (fullscreenPreview.classList.contains('hidden')) {
            return;
        }
        if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
            return;
        }

        const nextImage = target === 'first'
            ? state.filteredImages[0]
            : state.filteredImages[state.filteredImages.length - 1];
        if (!nextImage) {
            return;
        }

        state.fullscreenSelectedKey = nextImage.__key;
        state.selectedKey = nextImage.__key;
        renderGallery();
        showDetails(nextImage);
        openFullscreenPreviewFromImage(nextImage);
    }

    function isTypingTarget(event) {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
            return false;
        }

        if (target.isContentEditable) {
            return true;
        }

        const tag = target.tagName.toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') {
            return true;
        }

        return false;
    }

    function selectGalleryImage(nextImage) {
        if (!nextImage) {
            return;
        }

        state.selectedKey = nextImage.__key;
        renderGallery();
        showDetails(nextImage);

        const activeTile = galleryGrid.querySelector(`.tile[data-key="${CSS.escape(nextImage.__key)}"]`);
        if (activeTile) {
            activeTile.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
    }

    async function navigateGalleryBy(delta) {
        if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
            return;
        }

        const currentIndex = state.filteredImages.findIndex((img) => img.__key === state.selectedKey);
        const baseIndex = currentIndex >= 0 ? currentIndex : 0;

        if (delta < 0 && baseIndex <= 0) {
            return;
        }

        let nextIndex = baseIndex + delta;
        if (delta > 0 && nextIndex >= state.filteredImages.length) {
            if (state.hasMore && !state.loadingPage) {
                const priorLength = state.filteredImages.length;
                await loadNextPage();
                if (state.filteredImages.length <= priorLength) {
                    return;
                }

                const refreshedIndex = state.filteredImages.findIndex((img) => img.__key === state.selectedKey);
                const anchoredIndex = refreshedIndex >= 0 ? refreshedIndex : baseIndex;
                nextIndex = anchoredIndex + 1;
            } else {
                return;
            }
        }

        if (nextIndex < 0 || nextIndex >= state.filteredImages.length) {
            return;
        }

        selectGalleryImage(state.filteredImages[nextIndex]);
    }

    async function navigateGalleryToBoundary(target) {
        if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
            return;
        }

        if (target === 'first') {
            selectGalleryImage(state.filteredImages[0]);
            return;
        }

        // Ensure End can target the real last loaded/available image.
        while (state.hasMore && !state.loadingPage) {
            const priorLength = state.filteredImages.length;
            await loadNextPage();
            if (state.filteredImages.length <= priorLength) {
                break;
            }
        }

        selectGalleryImage(state.filteredImages[state.filteredImages.length - 1]);
    }

    function toClientImage(image, indexOffset) {
        // Keep keys stable across refresh/import reordering so detail/fullscreen state
        // stays attached to the same logical image.
        const stablePart = image.file_hash
            || (image.id ? `id:${image.id}` : '')
            || (image.file_path ? `path:${image.file_path}` : '')
            || (image.file_name ? `name:${image.file_name}` : '')
            || `row-${indexOffset}`;
        return {
            ...image,
            __key: stablePart,
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
        state.totalImageCount = count;
        state.runtimeWarnings = Array.isArray(result.warnings) ? result.warnings : [];
        state.mediaCapabilities = result.capabilities && typeof result.capabilities === 'object'
            ? result.capabilities
            : null;
        renderRuntimeWarnings();
        return `${count}:${latestId}`;
    }

    async function fetchImageStatusCounts() {
        const response = await fetch('/utilities/image_status_counts');
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    function renderUtilitiesStatusBadge(counts) {
        const deleted = Number(counts?.deleted || 0);
        const tombstoned = Number(counts?.tombstoned || 0);
        utilitiesStatusBadge.textContent = `Deleted: ${deleted} | Tombstoned: ${tombstoned}`;
    }

    async function fetchInactiveImages(status = 'all') {
        const response = await fetch(`/utilities/inactive_images?status=${encodeURIComponent(status)}&limit=300`);
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return Array.isArray(result) ? result : [];
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

    async function focusImageByHash(fileHash) {
        if (!fileHash) {
            return;
        }

        const target = state.allImages.find((img) => img.file_hash === fileHash);
        if (!target) {
            return;
        }

        state.selectedKey = target.__key;
        await applyFilter({ ensureSearchCoverage: true });
    }

    function renderInactiveImagesList(rows) {
        inactiveImagesList.innerHTML = '';
        if (!rows.length) {
            inactiveImagesList.textContent = 'No inactive images for this filter.';
            return;
        }

        const fragment = document.createDocumentFragment();
        rows.forEach((row) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'inactive-image-row';

            const meta = document.createElement('div');
            meta.className = 'inactive-image-meta';

            const name = document.createElement('div');
            name.className = 'inactive-image-name';
            name.textContent = row.file_name || row.file_path || row.file_hash;

            const sub = document.createElement('div');
            sub.className = 'inactive-image-sub';
            const reason = row.status_reason ? ` (${row.status_reason})` : '';
            sub.textContent = `${row.image_status}${reason} • ${row.file_hash}`;

            meta.appendChild(name);
            meta.appendChild(sub);

            const restoreBtn = document.createElement('button');
            restoreBtn.type = 'button';
            restoreBtn.className = 'btn ghost btn-sm';
            restoreBtn.textContent = 'Restore';
            restoreBtn.addEventListener('click', async () => {
                restoreBtn.disabled = true;
                try {
                    const response = await fetch(`/utilities/images/${encodeURIComponent(row.file_hash)}/restore`, {
                        method: 'POST',
                    });
                    const result = await response.json();
                    if (!response.ok) {
                        throw new Error(result.detail || `HTTP ${response.status}`);
                    }

                    const [counts, rowsNext] = await Promise.all([
                        fetchImageStatusCounts(),
                        fetchInactiveImages(inactiveStatusFilter.value || 'all'),
                    ]);
                    utilitiesOutput.textContent = JSON.stringify({ restore: result, status_counts: counts }, null, 2);
                    renderInactiveImagesList(rowsNext);
                    await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
                    await focusImageByHash(row.file_hash);
                } catch (error) {
                    utilitiesOutput.textContent = `Error: ${error.message}`;
                } finally {
                    restoreBtn.disabled = false;
                }
            });

            wrapper.appendChild(meta);
            wrapper.appendChild(restoreBtn);
            fragment.appendChild(wrapper);
        });

        inactiveImagesList.appendChild(fragment);
    }

    async function refreshInactiveUtilities() {
        const status = inactiveStatusFilter.value || 'all';
        const [counts, rows] = await Promise.all([
            fetchImageStatusCounts(),
            fetchInactiveImages(status),
        ]);
        renderUtilitiesStatusBadge(counts);
        utilitiesOutput.textContent = JSON.stringify({ status_counts: counts, listed_status: status, listed_count: rows.length }, null, 2);
        renderInactiveImagesList(rows);
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, options);
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || `HTTP ${response.status}`);
        }
        return payload;
    }

    function renderTaxonomySummary(summary) {
        if (!taxonomySummaryBadge) {
            return;
        }
        taxonomySummaryBadge.textContent = [
            `Concepts: ${summary.concepts_total || 0}`,
            `Aliases: ${summary.aliases_total || 0}`,
            `Terms: ${summary.authority_terms_total || 0}`,
            `Unresolved: ${summary.unresolved_terms_total || 0}`,
        ].join(' | ');
    }

    function renderTaxonomyConcepts(concepts) {
        if (!taxonomyConceptsList) {
            return;
        }
        taxonomyConceptsList.innerHTML = '';
        if (!Array.isArray(concepts) || !concepts.length) {
            taxonomyConceptsList.innerHTML = '<p class="inactive-image-sub">No concepts found.</p>';
            return;
        }

        concepts.forEach((concept) => {
            const row = document.createElement('div');
            row.className = 'inactive-image-row taxonomy-concept-row';
            const labelPrefix = safeText(concept.display_prefix || 'Concept');
            row.innerHTML = `
                <div class="taxonomy-concept-title">${labelPrefix}: ${safeText(concept.canonical_name)}</div>
                <div class="inactive-image-sub">id=${concept.id} | aliases ${concept.alias_count || 0} | terms ${concept.authority_term_count || 0} | obs ${concept.observation_count || 0}</div>
            `;
            taxonomyConceptsList.appendChild(row);
        });
    }

    function renderTaxonomyDuplicates(rows) {
        if (!taxonomyDuplicatesList) {
            return;
        }
        taxonomyDuplicatesList.innerHTML = '';
        if (!Array.isArray(rows) || !rows.length) {
            taxonomyDuplicatesList.innerHTML = '<p class="inactive-image-sub">No duplicate clusters found.</p>';
            return;
        }

        rows.forEach((group) => {
            const members = Array.isArray(group.concepts) ? group.concepts : [];
            if (members.length < 2) {
                return;
            }
            const target = members[0];

            const wrapper = document.createElement('div');
            wrapper.className = 'inactive-image-row taxonomy-dup-row';

            const subtitle = document.createElement('div');
            subtitle.className = 'inactive-image-sub';
            subtitle.textContent = `key=${group.duplicate_key} | candidates=${group.count}`;

            const chips = document.createElement('div');
            chips.className = 'taxonomy-dup-members';
            members.forEach((concept) => {
                const chip = document.createElement('span');
                chip.className = 'taxonomy-dup-chip';
                chip.textContent = `#${concept.id} ${concept.canonical_name}`;
                chips.appendChild(chip);
            });

            const actions = document.createElement('div');
            actions.className = 'taxonomy-dup-actions';
            members.slice(1).forEach((source) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'btn ghost btn-sm';
                btn.textContent = `Merge #${source.id} -> #${target.id}`;
                btn.addEventListener('click', async () => {
                    taxonomyMergeSourceId.value = String(source.id);
                    taxonomyMergeTargetId.value = String(target.id);
                    await runTaxonomyMerge(source.id, target.id);
                });
                actions.appendChild(btn);
            });

            wrapper.appendChild(subtitle);
            wrapper.appendChild(chips);
            wrapper.appendChild(actions);
            taxonomyDuplicatesList.appendChild(wrapper);
        });
    }

    function collectNodeIds(nodes, sink = []) {
        (nodes || []).forEach((node) => {
            sink.push(Number(node.id));
            if (Array.isArray(node.children) && node.children.length) {
                collectNodeIds(node.children, sink);
            }
        });
        return sink;
    }

    function makeTreeNodeElement(node) {
        const li = document.createElement('li');
        li.className = 'taxonomy-tree-node';

        const row = document.createElement('div');
        row.className = 'taxonomy-tree-row';

        const hasChildren = Array.isArray(node.children) && node.children.length > 0;
        if (hasChildren) {
            const toggle = document.createElement('button');
            toggle.type = 'button';
            toggle.className = 'taxonomy-tree-toggle';
            const expanded = taxonomyExpandedNodeIds.has(Number(node.id));
            toggle.textContent = expanded ? '-' : '+';
            toggle.title = expanded ? 'Collapse' : 'Expand';
            toggle.addEventListener('click', () => {
                if (taxonomyExpandedNodeIds.has(Number(node.id))) {
                    taxonomyExpandedNodeIds.delete(Number(node.id));
                } else {
                    taxonomyExpandedNodeIds.add(Number(node.id));
                }
                renderTaxonomyTree(taxonomyTreeData, { keepRootLimit: true });
            });
            row.appendChild(toggle);
        }

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'taxonomy-tree-btn';
        btn.draggable = true;
        const prefix = safeText(node.display_prefix || 'Concept');
        btn.textContent = `${prefix}: ${node.canonical_name}`;
        btn.addEventListener('click', () => {
            if (taxonomyParentConceptId) {
                taxonomyParentConceptId.value = String(node.id);
            }
            if (taxonomyUpdateConceptId) {
                taxonomyUpdateConceptId.value = String(node.id);
            }
            if (taxonomyUpdateCanonicalName) {
                taxonomyUpdateCanonicalName.value = safeText(node.canonical_name, '');
            }
            if (taxonomyUpdateDescription) {
                taxonomyUpdateDescription.value = safeText(node.description, '');
            }
        });

        btn.addEventListener('dragstart', (event) => {
            taxonomyDragSourceConceptId = Number(node.id);
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('text/plain', String(node.id));
            }
        });

        btn.addEventListener('dragover', (event) => {
            event.preventDefault();
            btn.classList.add('drag-over');
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
        });

        btn.addEventListener('dragleave', () => {
            btn.classList.remove('drag-over');
        });

        btn.addEventListener('drop', async (event) => {
            event.preventDefault();
            btn.classList.remove('drag-over');
            const sourceId = Number(taxonomyDragSourceConceptId || Number(event.dataTransfer?.getData('text/plain')));
            const targetId = Number(node.id);
            if (!Number.isInteger(sourceId) || sourceId <= 0 || sourceId === targetId) {
                return;
            }
            await runTaxonomyParentUpdate(sourceId, targetId, { forceCommit: event.altKey });
        });

        row.appendChild(btn);
        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'btn ghost btn-sm';
        editBtn.textContent = 'Edit';
        row.appendChild(editBtn);
        li.appendChild(row);

        const inlineEditor = document.createElement('div');
        inlineEditor.className = 'taxonomy-inline-editor hidden';

        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.value = safeText(node.canonical_name, '');
        nameInput.placeholder = 'Canonical concept name';

        const descInput = document.createElement('textarea');
        descInput.value = safeText(node.description, '');
        descInput.placeholder = 'Brief concept description';

        const actions = document.createElement('div');
        actions.className = 'taxonomy-inline-actions';
        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn solid btn-sm';
        saveBtn.textContent = 'Save';
        const closeBtn = document.createElement('button');
        closeBtn.type = 'button';
        closeBtn.className = 'btn ghost btn-sm';
        closeBtn.textContent = 'Close';

        actions.appendChild(saveBtn);
        actions.appendChild(closeBtn);
        inlineEditor.appendChild(nameInput);
        inlineEditor.appendChild(descInput);
        inlineEditor.appendChild(actions);
        li.appendChild(inlineEditor);

        editBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            inlineEditor.classList.toggle('hidden');
        });

        closeBtn.addEventListener('click', () => {
            inlineEditor.classList.add('hidden');
        });

        saveBtn.addEventListener('click', async () => {
            const nextName = (nameInput.value || '').trim();
            const nextDescription = (descInput.value || '').trim();
            const payload = {
                canonical_name: nextName || null,
                description: nextDescription,
            };

            try {
                const result = await fetchJson(`/taxonomy/concepts/${node.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = JSON.stringify(result, null, 2);
                }
                inlineEditor.classList.add('hidden');
                await refreshTaxonomyAdmin();
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });

        if (hasChildren && taxonomyExpandedNodeIds.has(Number(node.id))) {
            const ul = document.createElement('ul');
            node.children.forEach((child) => {
                ul.appendChild(makeTreeNodeElement(child));
            });
            li.appendChild(ul);
        }

        return li;
    }

    function renderTaxonomyTree(nodes, options = {}) {
        if (!taxonomyTree) {
            return;
        }
        taxonomyTreeData = Array.isArray(nodes) ? nodes : [];
        const keepRootLimit = options.keepRootLimit === true;
        if (!keepRootLimit) {
            taxonomyRootRenderLimit = 200;
        }
        taxonomyTree.innerHTML = '';

        if (!Array.isArray(taxonomyTreeData) || !taxonomyTreeData.length) {
            taxonomyTree.innerHTML = '<p class="inactive-image-sub">No root concepts yet. Assign parent IDs to shape the tree.</p>';
            return;
        }

        const roots = taxonomyTreeData.slice(0, taxonomyRootRenderLimit);
        const ul = document.createElement('ul');
        ul.className = 'taxonomy-tree-root';
        roots.forEach((node) => {
            ul.appendChild(makeTreeNodeElement(node));
        });
        taxonomyTree.appendChild(ul);

        if (taxonomyRootRenderLimit < taxonomyTreeData.length) {
            const loadMore = document.createElement('button');
            loadMore.type = 'button';
            loadMore.className = 'btn ghost btn-sm taxonomy-tree-load-more';
            loadMore.textContent = `Load More Roots (${taxonomyRootRenderLimit}/${taxonomyTreeData.length})`;
            loadMore.addEventListener('click', () => {
                taxonomyRootRenderLimit = Math.min(taxonomyTreeData.length, taxonomyRootRenderLimit + 200);
                renderTaxonomyTree(taxonomyTreeData, { keepRootLimit: true });
            });
            taxonomyTree.appendChild(loadMore);
        }

        if (taxonomyTreeRootDrop) {
            taxonomyTreeRootDrop.classList.remove('drag-over');
        }
    }

    async function refreshTaxonomyAdmin(options = {}) {
        if (!taxonomySummaryBadge) {
            return;
        }

        const conceptQuery = options.query ?? (taxonomyConceptQuery?.value || '').trim();
        const [summary, duplicates, concepts, tree] = await Promise.all([
            fetchJson('/taxonomy/review/summary'),
            fetchJson('/taxonomy/review/potential_duplicates?limit=50'),
            fetchJson(`/taxonomy/concepts?status=active&limit=50${conceptQuery ? `&query=${encodeURIComponent(conceptQuery)}` : ''}`),
            fetchJson('/taxonomy/tree?status=active'),
        ]);

        renderTaxonomySummary(summary);
        renderTaxonomyDuplicates(duplicates);
        renderTaxonomyConcepts(concepts);
        renderTaxonomyTree(tree);
    }

    async function runTaxonomyMerge(sourceId, targetId) {
        if (!Number.isInteger(sourceId) || sourceId <= 0 || !Number.isInteger(targetId) || targetId <= 0) {
            alert('Source and target concept IDs must be positive integers.');
            return;
        }
        if (sourceId === targetId) {
            alert('Source and target IDs must differ.');
            return;
        }

        const dryRun = taxonomyMergeDryRunToggle ? taxonomyMergeDryRunToggle.checked : true;
        const payload = {
            source_concept_id: sourceId,
            target_concept_id: targetId,
            create_source_alias: true,
            deactivate_source: true,
            dry_run: dryRun,
        };

        const result = await fetchJson('/taxonomy/review/merge_concepts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (taxonomyOutput) {
            taxonomyOutput.textContent = JSON.stringify(result, null, 2);
        }
        if (result?.dry_run) {
            const previewText = [
                `terms ${result.would_move_authority_terms || 0}`,
                `aliases ${result.would_move_aliases || 0}`,
                `obs ${result.would_move_observations || 0}`,
            ].join(', ');
            showToast(`[DRY RUN] Merge Preview: #${sourceId} -> #${targetId} (${previewText})`, 'warn');
        } else {
            showToast(`[COMMIT] Merge Applied: #${sourceId} -> #${targetId}`, 'success');
        }
        if (!dryRun) {
            await refreshTaxonomyAdmin();
        }
    }

    async function runTaxonomyParentUpdate(conceptId, parentConceptId, options = {}) {
        if (!Number.isInteger(conceptId) || conceptId <= 0) {
            alert('Concept ID must be a positive integer.');
            return;
        }
        const forceCommit = options.forceCommit === true;
        const dryRun = forceCommit ? false : (taxonomyParentDryRunToggle ? taxonomyParentDryRunToggle.checked : true);
        const result = await fetchJson(`/taxonomy/concepts/${conceptId}/parent`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                parent_concept_id: parentConceptId,
                dry_run: dryRun,
            }),
        });
        if (taxonomyOutput) {
            taxonomyOutput.textContent = JSON.stringify(result, null, 2);
        }

        const parentText = parentConceptId === null ? 'root' : `#${parentConceptId}`;
        if (result?.dry_run) {
            showToast(`[DRY RUN] Parent Preview: #${conceptId} -> ${parentText}`, 'warn');
        } else {
            const suffix = forceCommit ? ' (Alt override)' : '';
            showToast(`[COMMIT] Parent Updated: #${conceptId} -> ${parentText}${suffix}`, 'success');
        }

        if (!dryRun) {
            await refreshTaxonomyAdmin();
        }
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
                    await applyFilter({ ensureSearchCoverage: true });
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
            closeFullscreenPreview();
            detailImage.classList.add('hidden');
            detailImage.style.display = 'none';
            detailImage.removeAttribute('src');
            releaseVideoElement(detailVideo);
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            detailsContent.classList.add('hidden');
            detailsEmpty.classList.remove('hidden');
            repairImageBtn.disabled = true;
            deleteImageFileBtn.disabled = true;
            currentDebugImage = null;
            debugBadge.classList.add('hidden');
            postSelectedImageTagsToTree(null);
            return;
        }

        detailsEmpty.classList.add('hidden');
        detailsContent.classList.remove('hidden');
        debugBadge.classList.toggle('hidden', !state.debugVisible);

        const imageUrl = getImageUrl(image);
        const videoMode = isVideoAsset(image);
        currentDebugImage = image;
        repairImageBtn.disabled = !isPngAsset(image);
        deleteImageFileBtn.disabled = false;

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
            detailVideo.preload = 'metadata';
            if (imageUrl) {
                primeVideoElement(detailVideo, imageUrl);
                // Autoplay can be blocked by browser policy; ignore rejection.
                detailVideo.play().catch(() => {});
            } else {
                releaseVideoElement(detailVideo);
            }
        } else {
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            releaseVideoElement(detailVideo);
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

        postSelectedImageTagsToTree(image);

        scheduleGalleryGridHeightSync();
    }

    function renderGallery() {
        const queryActive = searchInput.value.trim().length > 0;
        const treeTagFilterActive = Boolean(state.treeTagFilter);
        if (!queryActive && !treeTagFilterActive) {
            imageCount.textContent = `${state.totalImageCount} image${state.totalImageCount === 1 ? '' : 's'}`;
        } else {
            imageCount.textContent = `${state.filteredImages.length} match${state.filteredImages.length === 1 ? '' : 'es'} of ${state.totalImageCount}`;
        }

        if (!state.filteredImages.length) {
            galleryGrid.innerHTML = '<p>No images match your filter.</p>';
            showDetails(null);
            return;
        }

        galleryGrid.querySelectorAll('video').forEach((node) => releaseVideoElement(node));
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
                video.loop = true;
                video.playsInline = true;
                video.preload = 'none';
                video.disablePictureInPicture = true;
                video.tabIndex = -1;
                video.setAttribute('aria-hidden', 'true');
                tile.classList.add('video-tile');

                const placeholder = document.createElement('span');
                placeholder.className = 'tile-video-placeholder';
                placeholder.textContent = 'Preview on hover';

                const beginPreview = () => startTileVideoPreview(tile, video, mediaUrl);
                const endPreview = () => stopTileVideoPreview(tile, video);

                tile.addEventListener('pointerenter', beginPreview);
                tile.addEventListener('pointerleave', endPreview);
                tile.addEventListener('focus', beginPreview);
                tile.addEventListener('blur', endPreview);

                const directPosterUrl = getVideoPosterUrl(image);
                if (directPosterUrl) {
                    applyTileVideoPoster(tile, video, directPosterUrl);
                } else {
                    applyTileVideoPoster(tile, video, '');
                    observeTileForPosterCapture(tile, image, video, mediaUrl);
                }

                mediaNode = video;
                tile.appendChild(placeholder);
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

    function computeFilteredImages(query) {
        return state.allImages.filter((image) => {
            if (!imageMatchesTreeTagFilter(image)) {
                return false;
            }

            if (!query) {
                return true;
            }

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

    async function applyFilter(options = {}) {
        const ensureSearchCoverage = options.ensureSearchCoverage !== false;
        const query = searchInput.value.trim().toLowerCase();
        const runId = ++state.searchRunId;

        state.filteredImages = computeFilteredImages(query);

        if (!state.filteredImages.some((i) => i.__key === state.selectedKey)) {
            state.selectedKey = state.filteredImages[0]?.__key || null;
        }

        renderGallery();
        showDetails(state.filteredImages.find((i) => i.__key === state.selectedKey) || null);

        // For search, keep loading additional pages until we have enough matches
        // to fill the initial page size or we hit the end of records.
        if ((!query && !state.treeTagFilter) || !ensureSearchCoverage) {
            return;
        }

        while (state.filteredImages.length < state.pageSize && state.hasMore) {
            if (runId !== state.searchRunId) {
                return;
            }

            await loadNextPage({ recomputeFilter: false });
            if (runId !== state.searchRunId) {
                return;
            }

            state.filteredImages = computeFilteredImages(query);
            if (!state.filteredImages.some((i) => i.__key === state.selectedKey)) {
                state.selectedKey = state.filteredImages[0]?.__key || null;
            }
            renderGallery();
            showDetails(state.filteredImages.find((i) => i.__key === state.selectedKey) || null);

            // Yield between page loads so typing stays responsive.
            await new Promise((resolve) => window.setTimeout(resolve, 0));
        }
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

    async function loadNextPage(options = {}) {
        const recomputeFilter = options.recomputeFilter !== false;
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

            if (recomputeFilter) {
                await applyFilter({ ensureSearchCoverage: false });
            }
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
        state.totalImageCount = 0;
        state.selectedKey = preserveSelection ? previousSelectedKey : null;
        state.offset = 0;
        state.hasMore = true;
        state.loadingPage = false;
        galleryGrid.innerHTML = '';
        updatePagingUi();

        try {
            state.imagesStateSignature = await fetchImagesStateSignature();
            await loadNextPage({ recomputeFilter: false });
            await applyFilter({ ensureSearchCoverage: true });
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

    async function maybeAutoRefreshGallery(options = {}) {
        const preserveSelection = options.preserveSelection !== false;
        if (!state.autoRefreshEnabled) {
            return;
        }

        const latestSignature = await fetchImagesStateSignature();
        if (latestSignature !== state.imagesStateSignature) {
            await resetAndLoadImages({ preserveSelection, showRefreshUi: false });
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

    if (treeTagFilterClear) {
        treeTagFilterClear.addEventListener('click', () => {
            state.treeTagFilter = null;
            renderTreeTagFilterIndicator();
            void applyFilter({ ensureSearchCoverage: true });
        });
    }

    searchInput.addEventListener('input', () => {
        if (searchDebounceTimer !== null) {
            window.clearTimeout(searchDebounceTimer);
        }

        // Cancel in-flight progressive-search loops started by previous input.
        state.searchRunId += 1;

        searchDebounceTimer = window.setTimeout(() => {
            searchDebounceTimer = null;
            void applyFilter({ ensureSearchCoverage: true });
        }, 180);
    });
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
    detailImage.addEventListener('click', () => {
        const image = getSelectedImage();
        if (!image || detailImage.classList.contains('hidden')) {
            return;
        }
        openFullscreenPreviewFromImage(image);
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
    detailVideo.addEventListener('click', () => {
        const image = getSelectedImage();
        if (!image || detailVideo.classList.contains('hidden')) {
            return;
        }
        openFullscreenPreviewFromImage(image);
    });
    fullscreenCloseBtn.addEventListener('click', closeFullscreenPreview);
    if (fullscreenLoopBtn) {
        fullscreenLoopBtn.addEventListener('click', () => {
            state.fullscreenLoopEnabled = !state.fullscreenLoopEnabled;
            writeStoredBool(STORAGE_KEYS.fullscreenLoop, state.fullscreenLoopEnabled);
            fullscreenVideo.loop = state.fullscreenLoopEnabled;
            syncFullscreenLoopUi();

            // If it already ended and user enabled loop, restart playback.
            if (state.fullscreenLoopEnabled && !fullscreenVideo.classList.contains('hidden') && fullscreenVideo.ended) {
                fullscreenVideo.currentTime = 0;
                fullscreenVideo.play().catch(() => {});
            }
        });
    }
    fullscreenPreview.addEventListener('click', (event) => {
        if (event.target === fullscreenPreview) {
            closeFullscreenPreview();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) {
            return;
        }

        if (isTypingTarget(event)) {
            return;
        }

        if (fullscreenPreview.classList.contains('hidden')) {
            if (event.key === 'ArrowLeft') {
                event.preventDefault();
                void navigateGalleryBy(-1);
                return;
            }
            if (event.key === 'ArrowRight') {
                event.preventDefault();
                void navigateGalleryBy(1);
                return;
            }
            if (event.key === 'Home') {
                event.preventDefault();
                void navigateGalleryToBoundary('first');
                return;
            }
            if (event.key === 'End') {
                event.preventDefault();
                void navigateGalleryToBoundary('last');
            }
            return;
        }

        if (event.key === 'Escape') {
            event.preventDefault();
            closeFullscreenPreview();
            return;
        }
        if (event.key === 'ArrowLeft') {
            event.preventDefault();
            void navigateFullscreenBy(-1);
            return;
        }
        if (event.key === 'ArrowRight') {
            event.preventDefault();
            void navigateFullscreenBy(1);
            return;
        }
        if (event.key === 'Home') {
            event.preventDefault();
            navigateFullscreenToBoundary('first');
            return;
        }
        if (event.key === 'End') {
            event.preventDefault();
            navigateFullscreenToBoundary('last');
        }
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
            await applyFilter({ ensureSearchCoverage: true });
        } catch (error) {
            alert(`Could not add image to collection: ${error.message}`);
        }
    });
    repairImageBtn.addEventListener('click', async () => {
        const image = getSelectedImage();
        if (!image?.file_hash) {
            return;
        }
        if (!isPngAsset(image)) {
            alert('Repair is currently supported for PNG files only.');
            return;
        }

        if (!window.confirm('Run PNG repair for this image? This will create a new repaired image when bytes change.')) {
            return;
        }

        repairImageBtn.disabled = true;
        const oldLabel = repairImageBtn.textContent;
        repairImageBtn.textContent = '...';
        try {
            const response = await fetch(`/images/${encodeURIComponent(image.file_hash)}/repair_png`, {
                method: 'POST',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
            await focusImageByHash(result.repaired_file_hash || image.file_hash);

            const outcome = result.created_new_image ? 'Created repaired image.' : 'Repair produced no new file.';
            alert(`${outcome}\nChunks: ${result.parsed_chunks}\nEXIF tags: ${result.exif_tags}\nText chunks: ${result.text_chunks}`);
        } catch (error) {
            alert(`Could not repair image: ${error.message}`);
        } finally {
            repairImageBtn.textContent = oldLabel;
            const selected = getSelectedImage();
            repairImageBtn.disabled = !selected || !isPngAsset(selected);
        }
    });
    deleteImageFileBtn.addEventListener('click', async () => {
        const image = getSelectedImage();
        if (!image?.file_hash) {
            return;
        }

        const message = [
            'Mark this image as deleted?',
            '',
            'Files will be preserved on disk until you run Trash Purge.',
            'The image will be hidden from the gallery.',
            '',
            `Hash: ${image.file_hash}`,
            `Path: ${image.file_path || image.file_name || 'N/A'}`,
        ].join('\n');
        if (!window.confirm(message)) {
            return;
        }

        deleteImageFileBtn.disabled = true;
        try {
            const response = await fetch(`/images/${encodeURIComponent(image.file_hash)}/file`, {
                method: 'DELETE',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
            alert(result.message || 'Image marked deleted.');
        } catch (error) {
            alert(`Could not delete image: ${error.message}`);
        } finally {
            const selected = getSelectedImage();
            deleteImageFileBtn.disabled = !selected;
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
            await applyFilter({ ensureSearchCoverage: true });
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
            await applyFilter({ ensureSearchCoverage: true });
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

    purgeDeletedBtn.addEventListener('click', async () => {
        const warning = [
            'Purge deleted images from disk?',
            '',
            'This permanently removes files and JSON sidecars for records marked deleted.',
            'This operation is destructive and cannot be undone.',
        ].join('\n');

        if (!window.confirm(warning)) {
            return;
        }

        purgeDeletedBtn.disabled = true;
        try {
            const response = await fetch('/utilities/purge_deleted_files', {
                method: 'POST',
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            await refreshInactiveUtilities();
            const counts = await fetchImageStatusCounts();
            utilitiesOutput.textContent = JSON.stringify({ purge: result, status_counts: counts }, null, 2);
            await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
        } catch (error) {
            alert(`Could not purge deleted files: ${error.message}`);
        } finally {
            purgeDeletedBtn.disabled = false;
        }
    });

    refreshInactiveListBtn.addEventListener('click', async () => {
        refreshInactiveListBtn.disabled = true;
        try {
            await refreshInactiveUtilities();
        } catch (error) {
            utilitiesOutput.textContent = `Error: ${error.message}`;
        } finally {
            refreshInactiveListBtn.disabled = false;
        }
    });
    inactiveStatusFilter.addEventListener('change', refreshInactiveUtilities);

    if (taxonomyRefreshBtn) {
        taxonomyRefreshBtn.addEventListener('click', async () => {
            taxonomyRefreshBtn.disabled = true;
            try {
                await refreshTaxonomyAdmin();
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            } finally {
                taxonomyRefreshBtn.disabled = false;
            }
        });
    }

    if (taxonomyConceptSearchForm) {
        taxonomyConceptSearchForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            try {
                await refreshTaxonomyAdmin({ query: (taxonomyConceptQuery?.value || '').trim() });
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyBootstrapForm) {
        const updateBootstrapDropzoneLabel = () => {
            if (!taxonomyBootstrapDropzone) {
                return;
            }
            const selectedFile = taxonomyBootstrapDroppedFile || taxonomyBootstrapFile?.files?.[0] || null;
            if (selectedFile) {
                const bytes = Number(selectedFile.size || 0);
                const kb = (bytes / 1024).toFixed(1);
                taxonomyBootstrapDropzone.textContent = `Selected file: ${selectedFile.name} (${kb} KB)`;
                return;
            }
            taxonomyBootstrapDropzone.textContent = 'Drag and drop a `.json` or `.csv` bootstrap file here, or use the file picker above.';
        };

        if (taxonomyBootstrapFile) {
            taxonomyBootstrapFile.addEventListener('change', () => {
                taxonomyBootstrapDroppedFile = null;
                updateBootstrapDropzoneLabel();
            });
        }

        if (taxonomyBootstrapDropzone) {
            const prevent = (event) => {
                event.preventDefault();
                event.stopPropagation();
            };
            taxonomyBootstrapDropzone.addEventListener('dragenter', (event) => {
                prevent(event);
                taxonomyBootstrapDropzone.classList.add('drag-over');
            });
            taxonomyBootstrapDropzone.addEventListener('dragover', (event) => {
                prevent(event);
                taxonomyBootstrapDropzone.classList.add('drag-over');
            });
            taxonomyBootstrapDropzone.addEventListener('dragleave', (event) => {
                prevent(event);
                taxonomyBootstrapDropzone.classList.remove('drag-over');
            });
            taxonomyBootstrapDropzone.addEventListener('drop', (event) => {
                prevent(event);
                taxonomyBootstrapDropzone.classList.remove('drag-over');
                const file = event.dataTransfer?.files?.[0] || null;
                if (!file) {
                    return;
                }

                taxonomyBootstrapDroppedFile = file;
                if (taxonomyBootstrapFile) {
                    taxonomyBootstrapFile.value = '';
                }
                updateBootstrapDropzoneLabel();
            });
        }

        updateBootstrapDropzoneLabel();

        taxonomyBootstrapForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const authority = (taxonomyBootstrapAuthority?.value || 'user').trim();
            const format = (taxonomyBootstrapFormat?.value || 'json').trim();
            const dryRun = taxonomyBootstrapDryRunToggle ? taxonomyBootstrapDryRunToggle.checked : true;
            const fileForUpload = taxonomyBootstrapDroppedFile || taxonomyBootstrapFile?.files?.[0] || null;
            const rawText = taxonomyBootstrapRaw?.value || '';
            if (!fileForUpload && !rawText.trim()) {
                alert('Provide JSON/CSV text, or upload a JSON/CSV file.');
                return;
            }

            try {
                let result;
                if (fileForUpload) {
                    const formData = new FormData();
                    formData.append('authority_name', authority);
                    formData.append('format', format);
                    formData.append('create_missing_concepts', 'true');
                    formData.append('dry_run', dryRun ? 'true' : 'false');
                    formData.append('file', fileForUpload);

                    result = await fetchJson('/taxonomy/bootstrap/import_file', {
                        method: 'POST',
                        body: formData,
                    });
                } else {
                    result = await fetchJson('/taxonomy/bootstrap/import', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            authority_name: authority,
                            format,
                            raw_text: rawText,
                            create_missing_concepts: true,
                            dry_run: dryRun,
                        }),
                    });
                }
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = JSON.stringify(result, null, 2);
                }
                const stats = result?.stats || {};
                const summaryText = [
                    `rows ${stats.rows_processed || 0}/${stats.rows_received || 0}`,
                    `concepts +${stats.concepts_created || 0}`,
                    `terms +${stats.authority_terms_created || 0}`,
                ].join(', ');
                if (result?.dry_run) {
                    showToast(`[DRY RUN] Import (${result.authority}): ${summaryText}`, 'warn');
                } else {
                    showToast(`[COMMIT] Import (${result.authority}): ${summaryText}`, 'success');
                }
                if (fileForUpload) {
                    taxonomyBootstrapDroppedFile = null;
                    if (taxonomyBootstrapFile) {
                        taxonomyBootstrapFile.value = '';
                    }
                    updateBootstrapDropzoneLabel();
                }
                if (!dryRun) {
                    await refreshTaxonomyAdmin();
                }
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
                showToast(`[ERROR] Import failed: ${error.message}`, 'warn');
            }
        });
    }

    if (taxonomyAliasForm) {
        taxonomyAliasForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const conceptId = Number(taxonomyAliasConceptId.value);
            const alias = (taxonomyAliasValue.value || '').trim();
            const aliasType = (taxonomyAliasType.value || 'synonym').trim() || 'synonym';
            const authorityName = (taxonomyAliasAuthority.value || '').trim();

            if (!Number.isInteger(conceptId) || conceptId <= 0 || !alias) {
                alert('Provide a valid concept ID and alias.');
                return;
            }

            try {
                const result = await fetchJson(`/taxonomy/concepts/${conceptId}/aliases`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        alias,
                        alias_type: aliasType,
                        authority_name: authorityName || null,
                    }),
                });
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = JSON.stringify(result, null, 2);
                }
                taxonomyAliasValue.value = '';
                await refreshTaxonomyAdmin({ query: (taxonomyConceptQuery?.value || '').trim() });
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyMergeForm) {
        taxonomyMergeForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const sourceId = Number(taxonomyMergeSourceId.value);
            const targetId = Number(taxonomyMergeTargetId.value);
            try {
                await runTaxonomyMerge(sourceId, targetId);
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyCreateConceptForm) {
        taxonomyCreateConceptForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const canonicalName = (taxonomyNewConceptName?.value || '').trim();
            const parentConceptIdRaw = (taxonomyNewConceptParentId?.value || '').trim();
            if (!canonicalName) {
                alert('Concept name is required.');
                return;
            }

            const payload = {
                canonical_name: canonicalName,
                parent_concept_id: parentConceptIdRaw ? Number(parentConceptIdRaw) : null,
            };

            try {
                const result = await fetchJson('/taxonomy/concepts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = JSON.stringify(result, null, 2);
                }
                taxonomyNewConceptName.value = '';
                await refreshTaxonomyAdmin();
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyParentForm) {
        taxonomyParentForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const conceptId = Number(taxonomyParentConceptId?.value || 0);
            const parentRaw = (taxonomyParentNewParentId?.value || '').trim();
            if (!Number.isInteger(conceptId) || conceptId <= 0) {
                alert('Provide a valid concept ID.');
                return;
            }
            const parentConceptId = parentRaw ? Number(parentRaw) : null;

            try {
                await runTaxonomyParentUpdate(conceptId, parentConceptId);
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyConceptUpdateForm) {
        taxonomyConceptUpdateForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            const conceptId = Number(taxonomyUpdateConceptId?.value || 0);
            if (!Number.isInteger(conceptId) || conceptId <= 0) {
                alert('Provide a valid concept ID.');
                return;
            }

            const canonicalName = (taxonomyUpdateCanonicalName?.value || '').trim();
            const description = (taxonomyUpdateDescription?.value || '').trim();
            const payload = {
                canonical_name: canonicalName || null,
                description,
            };

            try {
                const result = await fetchJson(`/taxonomy/concepts/${conceptId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = JSON.stringify(result, null, 2);
                }
                await refreshTaxonomyAdmin();
            } catch (error) {
                if (taxonomyOutput) {
                    taxonomyOutput.textContent = `Error: ${error.message}`;
                }
            }
        });
    }

    if (taxonomyTreeRootDrop) {
        taxonomyTreeRootDrop.addEventListener('dragover', (event) => {
            event.preventDefault();
            taxonomyTreeRootDrop.classList.add('drag-over');
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = 'move';
            }
        });
        taxonomyTreeRootDrop.addEventListener('dragleave', () => {
            taxonomyTreeRootDrop.classList.remove('drag-over');
        });
        taxonomyTreeRootDrop.addEventListener('drop', async (event) => {
            event.preventDefault();
            taxonomyTreeRootDrop.classList.remove('drag-over');
            const sourceId = Number(taxonomyDragSourceConceptId || Number(event.dataTransfer?.getData('text/plain')));
            if (!Number.isInteger(sourceId) || sourceId <= 0) {
                return;
            }
            await runTaxonomyParentUpdate(sourceId, null, { forceCommit: event.altKey });
        });
    }

    if (taxonomyTreeExpandAllBtn) {
        taxonomyTreeExpandAllBtn.addEventListener('click', () => {
            collectNodeIds(taxonomyTreeData).forEach((id) => taxonomyExpandedNodeIds.add(id));
            taxonomyRootRenderLimit = taxonomyTreeData.length;
            renderTaxonomyTree(taxonomyTreeData, { keepRootLimit: true });
        });
    }

    if (taxonomyTreeCollapseAllBtn) {
        taxonomyTreeCollapseAllBtn.addEventListener('click', () => {
            taxonomyExpandedNodeIds.clear();
            taxonomyRootRenderLimit = 200;
            renderTaxonomyTree(taxonomyTreeData, { keepRootLimit: true });
        });
    }

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

    function formatCollectionSyncCounters(result) {
        const collectionsSynced = Number(result?.collections_synced || 0);
        const collectionsRequested = Number(result?.collections_requested || 0);
        const added = Number(result?.images_added || 0);
        const skipped = Number(result?.images_skipped || 0);
        const recovered = Number(result?.images_recovered || 0);
        const removed = Number(result?.memberships_removed || 0);
        return `Collections ${collectionsSynced}/${collectionsRequested} | Added ${added} | Skipped ${skipped} | Recovered ${recovered} | Removed ${removed}`;
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
        if (syncCivitaiCollectionsBtn) {
            syncCivitaiCollectionsBtn.disabled = true;
        }

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
            submitButton.disabled = false;
            if (syncCivitaiCollectionsBtn) {
                syncCivitaiCollectionsBtn.disabled = false;
            }
        }
    });

    if (syncCivitaiCollectionsBtn) {
        syncCivitaiCollectionsBtn.addEventListener('click', async () => {
            const rawLimit = importLimitInput.value.trim();
            const importSubmitBtn = document.getElementById('import-submit');
            const payload = {};

            if (rawLimit) {
                const parsedLimit = Number(rawLimit);
                if (!Number.isInteger(parsedLimit) || parsedLimit <= 0) {
                    importOutput.textContent = 'Limit must be a positive whole number.';
                    return;
                }
                payload.limit = parsedLimit;
            }

            importOutput.textContent = 'Synchronizing CivitAI collections...';
            syncCivitaiCollectionsBtn.disabled = true;
            if (importSubmitBtn) {
                importSubmitBtn.disabled = true;
            }

            try {
                const response = await fetch('/collections/sync/civitai', {
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

                const summaryLine = formatCollectionSyncCounters(result);
                importOutput.textContent = `${summaryLine}\n\n${JSON.stringify(result, null, 2)}`;
                await refreshCollectionsState();
                await resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
            } catch (error) {
                importOutput.textContent = `Error: ${error.message}`;
            } finally {
                syncCivitaiCollectionsBtn.disabled = false;
                if (importSubmitBtn) {
                    importSubmitBtn.disabled = false;
                }
            }
        });
    }

    infiniteScrollToggle.checked = state.infiniteEnabled;
    debugToggle.checked = state.debugVisible;
    autoRefreshToggle.checked = state.autoRefreshEnabled;
    sortOrderSelect.value = state.sortOrder;
    syncLayoutMode();
    syncThumbSize();
    syncFullscreenLoopUi();
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

    Promise.all([loadReferenceData(), resetAndLoadImages(), refreshInactiveUtilities(), refreshTaxonomyAdmin()]).catch((error) => {
        galleryGrid.innerHTML = `<p>Startup error: ${error.message}</p>`;
    });

    if (treeEmbedFrame) {
        treeEmbedFrame.addEventListener('load', () => {
            postSelectedImageTagsToTree(getSelectedImage());
        });
    }

    window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) {
            return;
        }
        if (!event.data || event.data.type !== 'atelier:gallery-tag-filter') {
            return;
        }
        setTreeTagFilter(event.data.payload || null);
        void applyFilter({ ensureSearchCoverage: true });
    });

    let autoRefreshInFlight = false;
    window.setInterval(async () => {
        if (autoRefreshInFlight || !state.autoRefreshEnabled) {
            return;
        }
        autoRefreshInFlight = true;
        try {
            await maybeAutoRefreshGallery({ preserveSelection: true });
        } catch {
            // Ignore transient polling issues.
        } finally {
            autoRefreshInFlight = false;
        }
    }, 2500);

    autoRefreshToggle.addEventListener('change', () => {
        state.autoRefreshEnabled = autoRefreshToggle.checked;
        writeStoredBool(STORAGE_KEYS.autoRefresh, state.autoRefreshEnabled);
        if (state.autoRefreshEnabled) {
            void maybeAutoRefreshGallery({ preserveSelection: true });
        }
    });
});