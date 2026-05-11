// ── Memory ───────────────────────────────────────────────────────────────────
// 📄 docs: app/docs/memories/image-api.md
// ──────────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const STORAGE_KEYS = {
        infinite: 'atelier.gallery.infiniteScroll',
        groupVariants: 'atelier.gallery.groupVariants',
        debug: 'atelier.gallery.debugVisible',
        autoRefresh: 'atelier.gallery.autoRefresh',
        fullscreenLoop: 'atelier.gallery.fullscreenLoop',
        thumbSize: 'atelier.gallery.thumbSize',
        sortOrder: 'atelier.gallery.sortOrder',
        selectedKey: 'atelier.gallery.selectedKey',
        selectedKeys: 'atelier.gallery.selectedKeys',
        detailActiveTab: 'atelier.detail.activeTab',
    };
    const COOKIE_KEYS = {
        themeMode: 'atelier_theme_mode',
        showNsfw: 'atelier_show_nsfw',
        nsfwVisibility: 'atelier_nsfw_visibility',
    };
    const preferences = window.AtelierPreferences || null;
    const uiKit = window.AtelierUi || null;
    const TEST_PAGE_SIZE = 120;
    const BASE_SYNC_MARGIN_PX = 4;
    // Fine-tuning offset for near-equal pane heights in this environment.
    // If this causes overfitting across devices/zoom/font settings, set to 0.
    const PANE_HEIGHT_CALIBRATION_PX = 2;
    const VIDEO_POSTER_CAPTURE_CONCURRENCY = 1;
    const VIDEO_POSTER_CAPTURE_TIMEOUT_MS = 8000;
    const VIDEO_THUMBNAIL_FETCH_CONCURRENCY = 1;
    const FOREGROUND_BUSY_REVEAL_DELAY_MS = 250;
    const NSFW_RATING_PILL_ORDER = ['PG', 'PG13', 'R', 'X', 'XXX'];
    const NSFW_SAFETY_PILL_ORDER = ['Safe', 'Mature', 'Explicit'];
    const MISSING_DATA_PILL_ORDER = [
        'NSFW Rating',
        'Safety Class',
        'Artist',
        'Source URL',
        'Generation Info',
        'Prompt',
        'A1111 Metadata',
        'A1111 Hires Upscale',
        'A1111 Regional Prompter',
        'A1111 ADetailer',
        'ComfyUI Metadata',
        'Tags',
        'EXIF Data',
        'CivitAI Meta',
    ];
    const STATUS_PILL_ORDER = [
        'CivitAI Deleted',
        'No CivitAI Link',
        'Duplicate Hash',
        'Multi Resource',
        'Corrupt Image',
        'Size Mismatch',
    ];
    const STATUS_PILL_TO_SERVER = {
        'CivitAI Deleted': 'civitai_deleted',
        'No Civitai Link': 'no_civitai_link',
        'Duplicate Hash': 'duplicate_hash',
        'Multi Resource': 'multi_resource',
        'Corrupt Image': 'corrupt',
        'Size Mismatch': 'size_mismatch',
    };
    const STATUS_SERVER_TO_PILL = Object.fromEntries(
        Object.entries(STATUS_PILL_TO_SERVER).map(([k, v]) => [v, k])
    );
    const DETAIL_TAB_IDS = [
        'image-attributes',
        'generation-data',
        'generation',
        'collections',
        'civitai-tags',
        'danbooru-tags',
        'prompt-tags',
        'user-tags',
        'utilities',
    ];
    const DETAIL_TAB_LABELS = {
        'image-attributes': 'Image Attributes',
        'generation-data': 'Metadata',
        generation: 'Generation',
        collections: 'Collections',
        'civitai-tags': 'CivitAI Tags',
        'danbooru-tags': 'Danbooru Tags',
        'prompt-tags': 'Prompt Tags',
        'user-tags': 'User Tags',
        utilities: 'Utilities',
    };
    const TAG_SOURCE_ORDER = ['civitai', 'danbooru', 'prompt', 'user'];

    function createEmptyAdvancedFilters() {
        return {
            generationSoftware: [],
            sourceSite: [],
            mimetype: [],
            nsfwRating: [],
            nsfwSafety: [],
            artistName: [],
            tags: [],
            collections: [],
            missingData: [],
            status: [],
        };
    }

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

    function readCookieValue(name) {
        if (preferences?.readCookieValue) {
            return preferences.readCookieValue(name);
        }
        const cookieText = document.cookie || '';
        const prefix = `${encodeURIComponent(name)}=`;
        const parts = cookieText.split(';');
        for (const part of parts) {
            const trimmed = part.trim();
            if (!trimmed.startsWith(prefix)) {
                continue;
            }
            return decodeURIComponent(trimmed.slice(prefix.length));
        }
        return null;
    }

    function writeCookieValue(name, value, maxAgeSeconds = 60 * 60 * 24 * 180) {
        if (preferences?.writeCookieValue) {
            preferences.writeCookieValue(name, value, maxAgeSeconds);
            return;
        }
        document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(String(value))}; path=/; max-age=${maxAgeSeconds}; SameSite=Lax`;
    }

    function readCookieBool(name, fallback) {
        const raw = readCookieValue(name);
        if (raw == null) {
            return fallback;
        }
        const normalized = String(raw).trim().toLowerCase();
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
        return allowedValues.includes(raw) ? raw : fallback;
    }

    function readNsfwVisibilityMode() {
        const explicitMode = readCookieString(COOKIE_KEYS.nsfwVisibility, '', ['safe', 'mature', 'explicit']);
        if (explicitMode) {
            return explicitMode;
        }

        const legacyShowNsfw = readCookieBool(COOKIE_KEYS.showNsfw, true);
        return legacyShowNsfw ? 'explicit' : 'safe';
    }

    const state = {
        allImages: [],
        filteredImages: [],
        filteredMatchCount: 0,
        totalImageCount: 0,
        artistNames: [],
        collections: [],
        imagesStateSignature: null,
        selectedKey: null,
        selectedKeys: new Set(),
        lastSelectionAnchorKey: null,
        fullscreenSelectedKey: null,
        fullscreenIndexHint: null,
        fullscreenNavInFlight: false,
        fullscreenQueuedDelta: null,
        galleryIndexHint: null,
        searchRunId: 0,
        // Prefetch state: holds a pre-fetched next page so the UI can
        // consume it instantly without waiting for a network round-trip.
        _prefetchResult: null,       // { page: Array, filteredCount: number, nextCursor: string|null }
        _prefetchCursor: null,       // the cursor value the prefetch was started with
        _prefetchSignature: null,    // activeServerFilterSignature at prefetch time
        _prefetchAbortController: null,
        // Lower page size is intentional for testing end-of-library UI transitions.
        pageSize: TEST_PAGE_SIZE,
        offset: 0,
        cursor: null,
        hasMore: true,
        loadingPage: false,
        infiniteEnabled: readStoredBool(STORAGE_KEYS.infinite, true),
        groupVariantsEnabled: readStoredBool(STORAGE_KEYS.groupVariants, true),
        debugVisible: readStoredBool(STORAGE_KEYS.debug, true),
        autoRefreshEnabled: readStoredBool(STORAGE_KEYS.autoRefresh, true),
        fullscreenLoopEnabled: readStoredBool(STORAGE_KEYS.fullscreenLoop, true),
        thumbSize: readStoredNumber(STORAGE_KEYS.thumbSize, 165, 120, 260),
        sortOrder: readStoredString(STORAGE_KEYS.sortOrder, 'first_added', ['first_added', 'last_added']),
        themeMode: readCookieString(COOKIE_KEYS.themeMode, 'light', ['light', 'dark']),
        nsfwVisibility: readNsfwVisibilityMode(),
        selectedKey: readStoredString(STORAGE_KEYS.selectedKey, null) || null,
        selectedKeys: new Set((() => { try { const raw = window.localStorage.getItem(STORAGE_KEYS.selectedKeys); return raw ? JSON.parse(raw) : []; } catch { return []; } })()),
        detailActiveTabId: readStoredString(STORAGE_KEYS.detailActiveTab, 'image-attributes', DETAIL_TAB_IDS),
        serverFilterMode: false,
        activeServerFilterSignature: null,
        activeServerFilterConfig: null,
        treeTagFilter: null,
        missingSourceFilter: {
            civitai: false,
            danbooru: false,
            prompt: false,
            user: false,
        },
        advancedFilters: createEmptyAdvancedFilters(),
        filterOptions: {
            tagNames: [],
            tagNamesBySource: {
                civitai: [],
                danbooru: [],
                prompt: [],
                user: [],
            },
            generationSoftware: [],
            sourceSites: [],
            mimetypes: [],
            nsfwRatings: [],
            nsfwSafety: [],
            artistNames: [],
            collectionNames: [],
        },
        runtimeWarnings: [],
        mediaCapabilities: null,
        videoPosterCache: new Map(),
        videoPosterInflight: new Map(),
        videoPosterQueue: [],
        videoPosterActiveCaptures: 0,
        videoThumbnailCache: new Map(),
        videoThumbnailInflight: new Map(),
        videoThumbnailQueue: [],
        videoThumbnailActiveFetches: 0,
        generationPrototypeCache: new Map(),
        generationPrototypeInflight: new Map(),
        detailGenerationRenderToken: 0,
        tasks: [],
        highlightedTaskId: null,
        taskStatusById: new Map(),
        taskRefreshInFlight: false,
        galleryRefreshInFlight: false,
        lastRenderedGallerySignature: null,
        lastRenderedFilterSignature: null,
        lastRenderedSelectionSignature: null,
        lastRenderedDetailKey: null,
        foregroundBusy: {
            active: false,
            visible: false,
            kind: null,
            countPillLabel: '',
            statusMessage: '',
            revealTimerId: null,
        },
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
    const variantGroupingToggle = document.getElementById('variant-grouping-toggle');
    const themeToggle = document.getElementById('theme-toggle');
    const nsfwVisibilityControl = document.getElementById('nsfw-visibility-control');
    const nsfwVisibilityCurrent = document.getElementById('nsfw-visibility-current');
    const nsfwVisibilityOptionButtons = Array.from(document.querySelectorAll('[data-nsfw-level]'));
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
    const selectionCount = document.getElementById('selection-count');
    const searchInput = document.getElementById('search-input');
    const searchAutocomplete = document.getElementById('search-autocomplete');
    const quickChipsContainer = document.getElementById('advanced-filters-quick-chips');
    const advancedFiltersPanel = document.getElementById('advanced-filters-panel');
    const advancedFiltersSummary = document.getElementById('advanced-filters-summary');
    const advancedFiltersClearBtn = document.getElementById('advanced-filters-clear');
    const advancedGenerationPills = document.getElementById('advanced-generation-pills');
    const advancedSourcePills = document.getElementById('advanced-source-pills');
    const advancedMimetypePills = document.getElementById('advanced-mimetype-pills');
    const advancedNsfwRatingPills = document.getElementById('advanced-nsfw-rating-pills');
    const advancedNsfwSafetyPills = document.getElementById('advanced-nsfw-safety-pills');
    const advancedMissingDataPills = document.getElementById('advanced-missing-data-pills');
    const advancedStatusPills = document.getElementById('advanced-status-pills');
    const advancedAuthorSelected = document.getElementById('advanced-author-selected');
    const advancedAuthorInput = document.getElementById('advanced-author-input');
    const advancedAuthorOptions = document.getElementById('advanced-author-options');
    const advancedAuthorAddBtn = document.getElementById('advanced-author-add');
    const advancedTagSelected = document.getElementById('advanced-tag-selected');
    const advancedTagInput = document.getElementById('advanced-tag-input');
    const advancedTagOptions = document.getElementById('advanced-tag-options');
    const advancedTagAddBtn = document.getElementById('advanced-tag-add');
    const advancedCollectionSelected = document.getElementById('advanced-collection-selected');
    const advancedCollectionInput = document.getElementById('advanced-collection-input');
    const advancedCollectionOptions = document.getElementById('advanced-collection-options');
    const advancedCollectionAddBtn = document.getElementById('advanced-collection-add');
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
    const refreshTasksBtn = document.getElementById('refresh-tasks-btn');
    const taskSummary = document.getElementById('task-summary');
    const activeTaskList = document.getElementById('active-task-list');
    const taskHistoryList = document.getElementById('task-history-list');
    const taskDetailStatus = document.getElementById('task-detail-status');
    const taskRetryFailedBtn = document.getElementById('task-retry-failed-btn');
    const taskRetryMissingBtn = document.getElementById('task-retry-missing-btn');
    const taskRetryTemporaryBtn = document.getElementById('task-retry-temporary-btn');
    const taskDetailEmpty = document.getElementById('task-detail-empty');
    const taskDetailContent = document.getElementById('task-detail-content');
    const taskDetailSummary = document.getElementById('task-detail-summary');
    const taskCollectionProgress = document.getElementById('task-collection-progress');
    const taskPendingActivities = document.getElementById('task-pending-activities');
    const taskMissingFailures = document.getElementById('task-missing-failures');
    const taskTemporaryFailures = document.getElementById('task-temporary-failures');
    const taskFailedItems = document.getElementById('task-failed-items');
    const taskUnavailableItems = document.getElementById('task-unavailable-items');
    const taskRecentActivity = document.getElementById('task-recent-activity');
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
    const modelsEmbedFrame = document.getElementById('models-embed-frame');
    let taxonomyDragSourceConceptId = null;
    const taxonomyExpandedNodeIds = new Set();
    let taxonomyTreeData = [];
    let taxonomyRootRenderLimit = 200;
    let toastTimer = null;
    let toastHideTimer = null;
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

    function positionToastNode(node) {
        if (!(node instanceof HTMLElement)) {
            return;
        }

        const fullscreenOpen = fullscreenPreview && !fullscreenPreview.classList.contains('hidden');
        const loopVisible = fullscreenLoopBtn && fullscreenOpen;

        if (loopVisible) {
            const loopRect = fullscreenLoopBtn.getBoundingClientRect();
            const gapPx = 12;
            const rightPx = Math.max(12, window.innerWidth - loopRect.left + gapPx);
            const topPx = Math.max(8, Math.round(loopRect.top));
            const maxWidthPx = Math.max(180, Math.round(loopRect.left - 24));

            node.classList.add('app-toast--fullscreen');
            node.style.top = `${topPx}px`;
            node.style.bottom = 'auto';
            node.style.right = `${rightPx}px`;
            node.style.left = 'auto';
            node.style.maxWidth = `${maxWidthPx}px`;
            return;
        }

        node.classList.remove('app-toast--fullscreen');
        node.style.top = 'auto';
        node.style.bottom = '';
        node.style.right = '';
        node.style.left = 'auto';
        node.style.maxWidth = '';
    }

    function showToast(message, variant = 'info') {
        const node = ensureToastNode();
        positionToastNode(node);
        node.textContent = message;
        node.classList.remove('hidden', 'toast-info', 'toast-success', 'toast-warn', 'is-visible');
        node.classList.add(`toast-${variant}`);
        // Force a frame so visibility transition always runs.
        window.requestAnimationFrame(() => {
            node.classList.add('is-visible');
        });

        if (toastTimer) {
            window.clearTimeout(toastTimer);
        }
        if (toastHideTimer) {
            window.clearTimeout(toastHideTimer);
        }
        toastTimer = window.setTimeout(() => {
            node.classList.remove('is-visible');
            toastHideTimer = window.setTimeout(() => {
                node.classList.add('hidden');
            }, 170);
        }, 1650);
    }

    function isTaskTerminal(task) {
        return ['completed', 'failed', 'cancelled'].includes(String(task?.status || ''));
    }

    function isTaskActive(task) {
        return ['queued', 'running'].includes(String(task?.status || ''));
    }

    function formatTaskProgress(task) {
        const current = Number(task?.progress_current || 0);
        const total = Number(task?.progress_total || 0);
        if (total > 0) {
            return `${current}/${total}`;
        }
        return current > 0 ? `${current}` : 'Pending';
    }

    function formatTaskCounterSummary(task) {
        const counters = task && typeof task === 'object' ? (task.counters || {}) : {};
        const itemCounts = task && typeof task === 'object' ? (task.item_status_counts || {}) : {};
        const metadata = task && typeof task === 'object' ? (task.metadata || {}) : {};
        const parts = [];

        const currentCollectionDiscovered = Number(metadata.current_collection_discovered || 0);
        const currentCollectionProcessed = Number(metadata.current_collection_processed || 0);
        const currentCollectionTotal = Number(metadata.current_collection_total || 0);
        const overallProcessed = Number(metadata.overall_items_processed || 0);
        const overallDiscovered = Number(metadata.overall_items_discovered || 0);

        if (currentCollectionTotal > 0 && currentCollectionProcessed > 0) {
            parts.push(`Collection ${currentCollectionProcessed}/${currentCollectionTotal}`);
        } else if (currentCollectionDiscovered > 0) {
            parts.push(`Discovered ${currentCollectionDiscovered}`);
        }

        if (overallDiscovered > 0) {
            parts.push(`All ${overallProcessed}/${overallDiscovered}`);
        } else {
            parts.push(`Progress ${formatTaskProgress(task)}`);
        }

        if (Number(counters.images_added || 0) > 0) {
            parts.push(`Added ${Number(counters.images_added || 0)}`);
        }
        if (Number(counters.images_skipped || 0) > 0) {
            parts.push(`Skipped ${Number(counters.images_skipped || 0)}`);
        }
        if (Number(counters.images_failed || 0) > 0) {
            parts.push(`Failed ${Number(counters.images_failed || 0)}`);
        }
        if (Number(counters.images_cancelled || 0) > 0) {
            parts.push(`Cancelled ${Number(counters.images_cancelled || 0)}`);
        }
        if (Number(itemCounts.downloading || 0) > 0) {
            parts.push(`Downloading ${Number(itemCounts.downloading || 0)}`);
        }
        if (Number(itemCounts.ingesting || 0) > 0) {
            parts.push(`Ingesting ${Number(itemCounts.ingesting || 0)}`);
        }

        return parts.join(' | ');
    }

    function formatRelativeTime(isoValue) {
        if (!isoValue) {
            return 'unknown';
        }
        const timestamp = Date.parse(isoValue);
        if (!Number.isFinite(timestamp)) {
            return 'unknown';
        }
        const diffSeconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
        if (diffSeconds <= 1) {
            return 'just now';
        }
        if (diffSeconds < 60) {
            return `${diffSeconds}s ago`;
        }
        const minutes = Math.round(diffSeconds / 60);
        if (minutes < 60) {
            return `${minutes}m ago`;
        }
        const hours = Math.round(minutes / 60);
        return `${hours}h ago`;
    }

    function getSelectedTask() {
        return state.tasks.find((task) => task.id === state.highlightedTaskId) || null;
    }

    function getFailedItemCount(task) {
        if (!task || typeof task !== 'object') {
            return 0;
        }
        const failedItems = Array.isArray(task.failed_items) ? task.failed_items : [];
        if (failedItems.length > 0) {
            return failedItems.length;
        }
        const counters = task.counters || {};
        return Number(counters.images_failed || 0);
    }

    function getUnavailableItems(task) {
        if (!task || typeof task !== 'object') {
            return [];
        }
        if (task.result && Array.isArray(task.result.unavailable_items)) {
            return task.result.unavailable_items;
        }
        if (Array.isArray(task.unavailable_items)) {
            return task.unavailable_items;
        }
        return [];
    }

    function getUnavailableItemCount(task) {
        return getUnavailableItems(task).length;
    }

    function canRetryFailedItems(task) {
        return isTaskTerminal(task) && getFailedItemCount(task) > 0;
    }

    async function queueRetryFailedItems(taskId) {
        const result = await retryFailedItems(taskId);
        if (result && result.task && result.task.id) {
            state.highlightedTaskId = result.task.id;
        }
        await refreshTasks();
        showToast('Retry task queued.', 'info');
        return result;
    }

    function buildTaskPill(label) {
        const pill = document.createElement('span');
        pill.className = 'task-pill';
        pill.textContent = label;
        return pill;
    }

    function renderTaskSummary() {
        if (!taskSummary) {
            return;
        }
        taskSummary.innerHTML = '';
        const tasks = Array.isArray(state.tasks) ? state.tasks : [];
        const activeCount = tasks.filter((task) => isTaskActive(task)).length;
        const completedCount = tasks.filter((task) => task.status === 'completed').length;
        const failedCount = tasks.filter((task) => task.status === 'failed').length;
        const cancelledCount = tasks.filter((task) => task.status === 'cancelled').length;

        [
            `Active ${activeCount}`,
            `Completed ${completedCount}`,
            `Failed ${failedCount}`,
            `Cancelled ${cancelledCount}`,
        ].forEach((text) => taskSummary.appendChild(buildTaskPill(text)));
    }

    function renderTaskItems(container, tasks, emptyText) {
        if (!container) {
            return;
        }

        container.innerHTML = '';
        if (!tasks.length) {
            const empty = document.createElement('div');
            empty.className = 'task-empty';
            empty.textContent = emptyText;
            container.appendChild(empty);
            return;
        }

        tasks.forEach((task) => {
            const row = document.createElement('article');
            row.className = 'task-row';
            if (task.id === state.highlightedTaskId) {
                row.classList.add('is-selected');
            }
            if (getFailedItemCount(task) > 0 || task.status === 'failed') {
                row.classList.add('has-failures');
            }
            row.addEventListener('click', () => {
                state.highlightedTaskId = task.id;
                renderTaskLists();
                renderTaskDetail();
            });

            const head = document.createElement('div');
            head.className = 'task-row-head';

            const titleWrap = document.createElement('div');
            const title = document.createElement('p');
            title.className = 'task-row-title';
            title.textContent = task.title || task.kind || task.id;

            const meta = document.createElement('div');
            meta.className = 'task-row-meta';

            const badge = document.createElement('span');
            badge.className = `task-status-badge task-status-${String(task.status || 'queued')}`;
            badge.textContent = String(task.status || 'queued');

            meta.appendChild(badge);
            meta.appendChild(buildTaskPill(`#${task.id}`));
            meta.appendChild(buildTaskPill(`Updated ${formatRelativeTime(task.updated_at)}`));
            if (getFailedItemCount(task) > 0) {
                meta.appendChild(buildTaskPill(`Failed items ${getFailedItemCount(task)}`));
            }
            if (getUnavailableItemCount(task) > 0) {
                meta.appendChild(buildTaskPill(`Unavailable ${getUnavailableItemCount(task)}`));
            }

            titleWrap.appendChild(title);
            titleWrap.appendChild(meta);
            head.appendChild(titleWrap);
            row.appendChild(head);

            const message = document.createElement('div');
            message.className = 'task-row-message';
            message.textContent = task.message || 'Queued';
            row.appendChild(message);

            const counters = document.createElement('div');
            counters.className = 'task-row-counters';
            counters.textContent = formatTaskCounterSummary(task);
            row.appendChild(counters);

            if (task.cancellable && isTaskActive(task)) {
                const actions = document.createElement('div');
                actions.className = 'task-row-actions';
                const cancelBtn = document.createElement('button');
                cancelBtn.type = 'button';
                cancelBtn.className = 'btn ghost btn-sm';
                cancelBtn.textContent = task.cancel_requested ? 'Cancelling...' : 'Cancel';
                cancelBtn.disabled = Boolean(task.cancel_requested);
                cancelBtn.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    cancelBtn.disabled = true;
                    try {
                        await cancelTask(task.id);
                        await refreshTasks();
                    } catch (error) {
                        showToast(`Could not cancel task: ${error.message}`, 'warn');
                        cancelBtn.disabled = false;
                    }
                });
                actions.appendChild(cancelBtn);
                row.appendChild(actions);
            }

            if (canRetryFailedItems(task)) {
                const actions = row.querySelector('.task-row-actions') || document.createElement('div');
                if (!actions.className) {
                    actions.className = 'task-row-actions';
                }

                const retryBtn = document.createElement('button');
                retryBtn.type = 'button';
                retryBtn.className = 'btn ghost btn-sm';
                retryBtn.textContent = 'Retry Failed';
                retryBtn.addEventListener('click', async (event) => {
                    event.stopPropagation();
                    retryBtn.disabled = true;
                    try {
                        await queueRetryFailedItems(task.id);
                    } catch (error) {
                        showToast(`Could not queue retry task: ${error.message}`, 'warn');
                        retryBtn.disabled = false;
                    }
                });

                actions.appendChild(retryBtn);
                if (!actions.parentElement) {
                    row.appendChild(actions);
                }
            }

            container.appendChild(row);
        });
    }

    function renderTaskLists() {
        const tasks = Array.isArray(state.tasks) ? state.tasks : [];
        const activeTasks = tasks.filter((task) => isTaskActive(task));
        const historyTasks = tasks.filter((task) => isTaskTerminal(task));
        renderTaskSummary();
        renderTaskItems(activeTaskList, activeTasks, 'No active jobs.');
        renderTaskItems(taskHistoryList, historyTasks, 'No completed jobs yet.');
    }

    function renderTaskDetailList(container, items, emptyText, options = {}) {
        if (!container) {
            return;
        }
        const { failed = false } = options;
        container.innerHTML = '';
        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'task-detail-empty-list';
            empty.textContent = emptyText;
            container.appendChild(empty);
            return;
        }

        items.forEach((item) => {
            const row = document.createElement('div');
            row.className = 'task-detail-item';
            if (failed) {
                row.classList.add('is-failed');
            }

            const main = document.createElement('div');
            main.className = 'task-detail-item-main';
            main.textContent = item.message || item.item_key || 'Unknown item';

            const sub = document.createElement('div');
            sub.className = 'task-detail-item-sub';
            const bits = [];
            if (item.item_key) {
                bits.push(item.item_key);
            }
            if (item.status && !failed) {
                bits.push(item.status);
            }
            if (item.timestamp) {
                bits.push(formatRelativeTime(item.timestamp));
            }
            sub.textContent = bits.join(' | ');

            row.appendChild(main);
            row.appendChild(sub);
            container.appendChild(row);
        });
    }

    function renderTaskDetailSummary(task) {
        if (!taskDetailSummary) {
            return;
        }
        taskDetailSummary.innerHTML = '';
        const counters = task && typeof task === 'object' ? (task.counters || {}) : {};
        const metadata = task && typeof task === 'object' ? (task.metadata || {}) : {};
        const collectionName = String(metadata.current_collection_name || '').trim();
        const collectionIndex = Number(metadata.current_collection_index || 0);
        const collectionCount = Number(metadata.current_collection_count || 0);
        const discovered = Number(metadata.current_collection_discovered || 0);
        const collectionProcessed = Number(metadata.current_collection_processed || 0);
        const collectionTotal = Number(metadata.current_collection_total || 0);
        const overallProcessed = Number(metadata.overall_items_processed || 0);
        const overallDiscovered = Number(metadata.overall_items_discovered || 0);

        const collectionLabel = collectionName
            ? (collectionIndex > 0 && collectionCount > 0
                ? `${collectionIndex}/${collectionCount} ${collectionName}`
                : collectionName)
            : 'n/a';

        const detailPairs = [
            ['Title', task.title || task.kind || task.id],
            ['Status', String(task.status || 'queued')],
            ['Progress', formatTaskProgress(task)],
            ['Current Collection', collectionLabel],
            ['Collection Discovery', discovered > 0 ? `${discovered} items` : 'n/a'],
            ['Collection Progress', collectionTotal > 0 ? `${collectionProcessed}/${collectionTotal}` : 'n/a'],
            ['All Items', overallDiscovered > 0 ? `${overallProcessed}/${overallDiscovered}` : formatTaskProgress(task)],
            ['Last Update', formatRelativeTime(task.updated_at)],
            ['Created', formatRelativeTime(task.created_at)],
            ['Started', task.started_at ? formatRelativeTime(task.started_at) : 'not started'],
            ['Failed Items', String(getFailedItemCount(task))],
            ['Unavailable', String(getUnavailableItemCount(task))],
            ['Items Added', String(Number(counters.images_added || 0))],
            ['Items Skipped', String(Number(counters.images_skipped || 0))],
            ['Items Failed', String(Number(counters.images_failed || 0))],
        ];

        detailPairs.forEach(([label, value]) => {
            const card = document.createElement('div');
            card.className = 'task-detail-card';

            const labelNode = document.createElement('div');
            labelNode.className = 'task-detail-label';
            labelNode.textContent = label;

            const valueNode = document.createElement('div');
            valueNode.className = 'task-detail-value';
            valueNode.textContent = value;

            card.appendChild(labelNode);
            card.appendChild(valueNode);
            taskDetailSummary.appendChild(card);
        });
    }

    function renderPendingActivities(task) {
        if (!taskPendingActivities) {
            return;
        }
        taskPendingActivities.innerHTML = '';
        const metadata = task && typeof task === 'object' ? (task.metadata || {}) : {};
        const activities = Array.isArray(metadata.pending_activities) ? metadata.pending_activities : [];
        if (!activities.length || !activities[0]) {
            const empty = document.createElement('div');
            empty.className = 'task-detail-empty-list';
            empty.textContent = 'No pending activities.';
            taskPendingActivities.appendChild(empty);
            return;
        }
        activities.forEach((activity) => {
            if (!activity) return;
            const row = document.createElement('div');
            row.className = 'task-pending-activity-item';
            const dot = document.createElement('span');
            dot.className = 'task-pending-dot';
            row.appendChild(dot);
            const text = document.createElement('span');
            text.textContent = activity;
            row.appendChild(text);
            taskPendingActivities.appendChild(row);
        });
    }

    function renderCollectionProgress(task) {
        if (!taskCollectionProgress) {
            return;
        }
        taskCollectionProgress.innerHTML = '';
        const metadata = task && typeof task === 'object' ? (task.metadata || {}) : {};
        const collections = Array.isArray(metadata.collections_progress) ? metadata.collections_progress : [];
        if (!collections.length) {
            const empty = document.createElement('div');
            empty.className = 'task-detail-empty-list';
            empty.textContent = 'No collection progress data.';
            taskCollectionProgress.appendChild(empty);
            return;
        }

        collections.forEach((col) => {
            const row = document.createElement('div');
            row.className = `task-collection-row task-collection-${col.status || 'pending'}`;

            const header = document.createElement('div');
            header.className = 'task-collection-header';

            const nameEl = document.createElement('span');
            nameEl.className = 'task-collection-name';
            nameEl.textContent = col.collection_name || `Collection ${col.collection_id}`;
            header.appendChild(nameEl);

            const statusBadge = document.createElement('span');
            statusBadge.className = `task-collection-status task-collection-status-${col.status || 'pending'}`;
            statusBadge.textContent = col.status || 'pending';
            header.appendChild(statusBadge);

            row.appendChild(header);

            // Progress bar
            const total = Number(col.total || col.discovered || 0);
            const processed = Number(col.metadata_gathered || col.images_fetched || 0);
            if (total > 0) {
                const barWrap = document.createElement('div');
                barWrap.className = 'task-collection-progress-bar-wrap';
                const bar = document.createElement('div');
                bar.className = 'task-collection-progress-bar';
                bar.style.width = `${Math.min(100, Math.round((processed / total) * 100))}%`;
                barWrap.appendChild(bar);
                row.appendChild(barWrap);
            }

            // Stats line
            const stats = document.createElement('div');
            stats.className = 'task-collection-stats';

            const statParts = [];
            if (total > 0) {
                statParts.push(`${processed}/${total} items`);
            }
            if (Number(col.imported || 0) > 0) {
                statParts.push(`imported: ${col.imported}`);
            }
            if (Number(col.skipped || 0) > 0) {
                statParts.push(`skipped: ${col.skipped}`);
            }
            if (Number(col.errors || 0) > 0) {
                statParts.push(`errors: ${col.errors}`);
            }
            if (col.message) {
                statParts.push(col.message);
            }
            stats.textContent = statParts.join(' · ');
            row.appendChild(stats);

            taskCollectionProgress.appendChild(row);
        });
    }

    function renderTaskUnavailableItems(container, items, emptyText) {
        if (!container) {
            return;
        }

        container.innerHTML = '';
        if (!items.length) {
            const empty = document.createElement('div');
            empty.className = 'task-detail-empty-list';
            empty.textContent = emptyText;
            container.appendChild(empty);
            return;
        }

        items.forEach((item) => {
            const row = document.createElement('div');
            row.className = 'task-detail-item is-unavailable';

            const main = document.createElement('div');
            main.className = 'task-detail-item-main';

            const imageId = item && item.image_id != null ? `Image ${item.image_id}` : 'Image unavailable';
            const collectionName = String(item?.collection_name || '').trim();
            const collectionId = item?.collection_id;
            if (collectionName) {
                main.textContent = `${imageId} in ${collectionName}`;
            } else if (collectionId != null) {
                main.textContent = `${imageId} in collection ${collectionId}`;
            } else {
                main.textContent = imageId;
            }

            const sub = document.createElement('div');
            sub.className = 'task-detail-item-sub';
            const bits = [];
            if (item?.status_code != null) {
                bits.push(`HTTP ${item.status_code}`);
            }
            if (item?.endpoint) {
                bits.push(`endpoint ${item.endpoint}`);
            }
            if (item?.source_url) {
                bits.push(item.source_url);
            }
            if (item?.reason) {
                bits.push(item.reason);
            }
            sub.textContent = bits.join(' | ');

            row.appendChild(main);
            row.appendChild(sub);
            container.appendChild(row);
        });
    }

    function renderTaskDetail() {
        const selectedTask = getSelectedTask();
        if (!selectedTask) {
            if (taskDetailEmpty) {
                taskDetailEmpty.classList.remove('hidden');
            }
            if (taskDetailContent) {
                taskDetailContent.classList.add('hidden');
            }
            if (taskDetailStatus) {
                taskDetailStatus.className = 'task-status-badge task-status-queued hidden';
                taskDetailStatus.textContent = 'queued';
            }
            if (taskRetryFailedBtn) {
                taskRetryFailedBtn.classList.add('hidden');
                taskRetryFailedBtn.disabled = false;
                taskRetryFailedBtn.dataset.taskId = '';
            }
            if (taskRetryMissingBtn) {
                taskRetryMissingBtn.classList.add('hidden');
                taskRetryMissingBtn.disabled = false;
                taskRetryMissingBtn.dataset.taskId = '';
            }
            if (taskRetryTemporaryBtn) {
                taskRetryTemporaryBtn.classList.add('hidden');
                taskRetryTemporaryBtn.disabled = false;
                taskRetryTemporaryBtn.dataset.taskId = '';
            }
            importOutput.textContent = '';
            return;
        }

        if (taskDetailEmpty) {
            taskDetailEmpty.classList.add('hidden');
        }
        if (taskDetailContent) {
            taskDetailContent.classList.remove('hidden');
        }
        if (taskDetailStatus) {
            taskDetailStatus.className = `task-status-badge task-status-${String(selectedTask.status || 'queued')}`;
            taskDetailStatus.textContent = String(selectedTask.status || 'queued');
        }
        if (taskRetryFailedBtn) {
            const retryVisible = canRetryFailedItems(selectedTask);
            taskRetryFailedBtn.classList.toggle('hidden', !retryVisible);
            taskRetryFailedBtn.disabled = false;
            taskRetryFailedBtn.dataset.taskId = retryVisible ? selectedTask.id : '';
        }

        renderTaskDetailSummary(selectedTask);
        renderPendingActivities(selectedTask);
        renderCollectionProgress(selectedTask);
        renderTaskDetailList(
            taskFailedItems,
            Array.isArray(selectedTask.failed_items) ? selectedTask.failed_items : [],
            'No failed items for this job.',
            { failed: true },
        );
        renderTaskUnavailableItems(
            taskUnavailableItems,
            getUnavailableItems(selectedTask),
            'No unavailable remote items were recorded for this job.',
        );

        // Missing failures
        const missingFailures = Array.isArray(selectedTask.missing_failures) ? selectedTask.missing_failures : [];
        if (taskRetryMissingBtn) {
            const hasMissing = missingFailures.length > 0 && selectedTask.status !== 'running';
            taskRetryMissingBtn.classList.toggle('hidden', !hasMissing);
            taskRetryMissingBtn.disabled = false;
            taskRetryMissingBtn.dataset.taskId = hasMissing ? selectedTask.id : '';
        }
        if (taskMissingFailures) {
            renderTaskDetailList(taskMissingFailures, missingFailures, 'No missing-item failures.', { failed: true });
        }

        // Temporary failures
        const temporaryFailures = Array.isArray(selectedTask.temporary_failures) ? selectedTask.temporary_failures : [];
        if (taskRetryTemporaryBtn) {
            const hasTemporary = temporaryFailures.length > 0 && selectedTask.status !== 'running';
            taskRetryTemporaryBtn.classList.toggle('hidden', !hasTemporary);
            taskRetryTemporaryBtn.disabled = false;
            taskRetryTemporaryBtn.dataset.taskId = hasTemporary ? selectedTask.id : '';
        }
        if (taskTemporaryFailures) {
            renderTaskDetailList(taskTemporaryFailures, temporaryFailures, 'No temporary failures.', { failed: true });
        }

        renderTaskDetailList(
            taskRecentActivity,
            Array.isArray(selectedTask.recent_items) ? selectedTask.recent_items : [],
            'No recent activity recorded yet.',
        );

        const payload = selectedTask.result || {
            id: selectedTask.id,
            kind: selectedTask.kind,
            status: selectedTask.status,
            updated_at: selectedTask.updated_at,
            message: selectedTask.message,
            error: selectedTask.error,
            progress_current: selectedTask.progress_current,
            progress_total: selectedTask.progress_total,
            counters: selectedTask.counters,
            item_status_counts: selectedTask.item_status_counts,
            recent_errors: selectedTask.recent_errors,
            recent_items: selectedTask.recent_items,
            failed_items: selectedTask.failed_items,
            unavailable_items: getUnavailableItems(selectedTask),
            metadata: selectedTask.metadata,
        };
        importOutput.textContent = JSON.stringify(payload, null, 2);

        // Detect auth_required on failed tasks and auto-open the auth panel.
        // Only override the auth status indicator if the last known state is
        // not a verified-ok session (otherwise a stale failed task keeps
        // clobbering a freshly-validated green dot).
        const taskMeta = selectedTask.metadata || {};
        if (selectedTask.status === 'failed' && taskMeta.auth_required) {
            openCivitaiAuthPanel();
            if (civitaiAuthStatusIcon && civitaiAuthStatusIcon.textContent !== '🟢') {
                setCivitaiAuthStatus('fail', 'Session expired — re-authenticate or paste a new cookie.');
            }
        }
    }

    function updateTaskOutput() {
        renderTaskDetail();
    }

    async function cancelTask(taskId) {
        const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
            method: 'POST',
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function retryFailedItems(taskId) {
        const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/retry_failed`, {
            method: 'POST',
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function retryMissingItems(taskId) {
        const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/retry-missing`, {
            method: 'POST',
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function retryTemporaryItems(taskId) {
        const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/retry-temporary`, {
            method: 'POST',
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function refreshTasks(options = {}) {
        const { silent = false } = options;
        if (state.taskRefreshInFlight) {
            return;
        }

        state.taskRefreshInFlight = true;
        let shouldReloadGallery = false;
        try {
            const response = await fetch('/api/tasks/?limit=40');
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.detail || `HTTP ${response.status}`);
            }

            const tasks = Array.isArray(result) ? result : [];
            const nextStatusMap = new Map();
            tasks.forEach((task) => {
                const previousStatus = state.taskStatusById.get(task.id);
                nextStatusMap.set(task.id, task.status);
                if (previousStatus && previousStatus !== task.status && isTaskTerminal(task)) {
                    if (String(task.kind || '').startsWith('civitai-')) {
                        shouldReloadGallery = true;
                    }
                    if (task.id === state.highlightedTaskId) {
                        const toastVariant = task.status === 'completed' ? 'success' : 'warn';
                        showToast(`${task.title}: ${task.status}`, toastVariant);
                    }
                }
            });

            state.taskStatusById = nextStatusMap;
            state.tasks = tasks;
            if (state.highlightedTaskId && !tasks.some((task) => task.id === state.highlightedTaskId)) {
                state.highlightedTaskId = tasks[0] ? tasks[0].id : null;
            }
            if (!state.highlightedTaskId && tasks[0]) {
                state.highlightedTaskId = tasks[0].id;
            }
            renderTaskLists();
            updateTaskOutput();

            if (shouldReloadGallery) {
                await refreshCollectionsState();
                await resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
            }
        } catch (error) {
            if (!silent) {
                showToast(`Could not refresh tasks: ${error.message}`, 'warn');
            }
        } finally {
            state.taskRefreshInFlight = false;
        }
    }

    const detailsEmpty = document.getElementById('details-empty');
    const detailsContent = document.getElementById('details-content');
    const detailMediaFrame = document.getElementById('detail-media-frame');
    const detailImage = document.getElementById('detail-image');
    const detailVideo = document.getElementById('detail-video');
    const detailVideoError = document.getElementById('detail-video-error');
    const detailVideoDownload = document.getElementById('detail-video-download');
    const detailVideoRetry = document.getElementById('detail-video-retry');
    const fullscreenPreview = document.getElementById('fullscreen-preview');
    const fullscreenLoopBtn = document.getElementById('fullscreen-loop-btn');
    const fullscreenCloseBtn = document.getElementById('fullscreen-close-btn');
    const fullscreenDebugOverlay = document.getElementById('fullscreen-debug-overlay');
    const fullscreenDebugFreezeBtn = document.getElementById('fullscreen-debug-freeze-btn');
    const fullscreenDebugCopyBtn = document.getElementById('fullscreen-debug-copy-btn');
    const fullscreenDebugContent = document.getElementById('fullscreen-debug-content');
    const fullscreenDebugSnapshot = document.getElementById('fullscreen-debug-snapshot');
    const fullscreenCounter = document.getElementById('fullscreen-counter');
    const fullscreenImage = document.getElementById('fullscreen-image');
    const fullscreenVideo = document.getElementById('fullscreen-video');
    const fullscreenEffectiveTagsCloud = document.getElementById('fullscreen-effective-tags-cloud');
    const fullscreenNegativeTagsWrap = document.getElementById('fullscreen-negative-tags-wrap');
    const fullscreenNegativeTagsCloud = document.getElementById('fullscreen-negative-tags-cloud');
    const civitaiTagsCloud = document.getElementById('civitai-tags-cloud');
    const danbooruTagsCloud = document.getElementById('danbooru-tags-cloud');
    const promptTagsCloud = document.getElementById('prompt-tags-cloud');
    const userTagsCloud = document.getElementById('user-tags-cloud');
    const detailTitle = document.getElementById('detail-title');
    const detailSubtitle = document.getElementById('detail-subtitle');
    const repairImageBtn = document.getElementById('repair-image-btn');
    const rescanImageBtn = document.getElementById('rescan-image-btn');
    const deleteImageFileBtn = document.getElementById('delete-image-file-btn');
    const sendToGenerationLabBtn = document.getElementById('send-to-generation-lab-btn');
    const sendToPerceptualLabBtn = document.getElementById('send-to-perceptual-lab-btn');
    const sendToModelLabBtn = document.getElementById('send-to-model-lab-btn');
    const detailMeta = document.getElementById('detail-meta');
    const detailExif = document.getElementById('detail-exif');
    const detailCivitai = document.getElementById('detail-civitai');
    const detailGeneration = document.getElementById('detail-generation');
    const detailFolderMount = document.getElementById('detail-folder-mount');
    const detailPanelStash = document.getElementById('detail-panel-stash');
    const detailFolderPanels = Array.from(document.querySelectorAll('.detail-folder-panel[role="tabpanel"]'));
    const detailPanelByTabId = new Map(
        detailFolderPanels
            .map((panel) => {
                const panelId = String(panel.id || '');
                const tabId = panelId.startsWith('detail-panel-') ? panelId.slice('detail-panel-'.length) : '';
                return [tabId, panel];
            })
            .filter(([tabId]) => tabId.length > 0)
    );
    let detailFolderWorkspace = null;
    const imageCollectionsList = document.getElementById('image-collections-list');
    const collectionSelect = document.getElementById('collection-select');
    const addToCollectionBtn = document.getElementById('add-to-collection-btn');
    const removeFromCollectionBtn = document.getElementById('remove-from-collection-btn');
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
    let suggestDebounceTimer = null;
    let suggestRunId = 0;
    let acFocusedIndex = -1;
    let posterCaptureObserver = null;
    let videoThumbnailObserver = null;
    let fullscreenDebugFrozen = false;
    let fullscreenDebugPreviousIndex = null;

    function clearForegroundBusyRevealTimer() {
        const timerId = Number(state.foregroundBusy?.revealTimerId || 0);
        if (!timerId) {
            return;
        }
        window.clearTimeout(timerId);
        state.foregroundBusy.revealTimerId = null;
    }

    function isForegroundBusy(kind = null) {
        if (!state.foregroundBusy?.active) {
            return false;
        }
        if (!kind) {
            return true;
        }
        return state.foregroundBusy.kind === kind;
    }

    function hasActiveGalleryFilters() {
        const queryActive = searchInput.value.trim().length > 0;
        const treeTagFilterActive = Boolean(state.treeTagFilter);
        const detailFiltersActive = hasActiveDetailFilters();
        return queryActive || treeTagFilterActive || detailFiltersActive;
    }

    function getServerFilterConfig(query) {
        const generationSoftwares = getAdvancedFilterValues('generationSoftware')
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean);
        const sourceSites = getAdvancedFilterValues('sourceSite')
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean);
        const mimetypes = getAdvancedFilterValues('mimetype')
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean);
        const nsfwRatings = getAdvancedFilterValues('nsfwRating')
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean);
        const nsfwSafety = getAdvancedFilterValues('nsfwSafety')
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean);

        // Parse artist names with include/exclude modes
        const includeArtists = [];
        const excludeArtists = [];
        for (const entry of getAdvancedFilterValues('artistName')) {
            const { mode, name } = _parseModePrefixEntry(entry);
            const trimmed = name.trim();
            if (!trimmed) continue;
            if (mode === 'exclude') {
                excludeArtists.push(trimmed);
            } else {
                includeArtists.push(trimmed);
            }
        }

        // Parse collection names with include/exclude modes
        const includeCollections = [];
        const excludeCollections = [];
        for (const entry of getAdvancedFilterValues('collections')) {
            const { mode, name } = _parseModePrefixEntry(entry);
            const trimmed = name.trim();
            if (!trimmed) continue;
            if (mode === 'exclude') {
                excludeCollections.push(trimmed);
            } else {
                includeCollections.push(trimmed);
            }
        }

        const a1111Hires = getDataExtractionMode('A1111 Hires Upscale');
        const a1111RegionalPrompter = getDataExtractionMode('A1111 Regional Prompter');
        const a1111Adetailer = getDataExtractionMode('A1111 ADetailer');

        // Collect tag filters from both advanced filter pills and tree tag filter.
        const includeTags = [];
        const excludeTags = [];
        for (const tagEntry of getAdvancedFilterValues('tags')) {
            const { mode, name } = _parseModePrefixEntry(tagEntry);
            const normalized = normalizeDetailFilterValue(name);
            if (!normalized) continue;
            if (mode === 'exclude') {
                excludeTags.push(normalized);
            } else {
                includeTags.push(normalized);
            }
        }
        if (Array.isArray(state.treeTagFilter)) {
            for (const filter of state.treeTagFilter) {
                const normalized = normalizeDetailFilterValue(filter.name);
                if (!normalized) continue;
                if (filter.mode === 'exclude') {
                    excludeTags.push(normalized);
                } else {
                    includeTags.push(normalized);
                }
            }
        }

        // Collect missing-data conditions (absent mode → server "no <field>" format).
        const missingData = [];
        for (const entry of getAdvancedFilterValues('missingData')) {
            const colonIdx = entry.indexOf(':');
            if (colonIdx < 0) continue;
            const mode = entry.substring(0, colonIdx).toLowerCase();
            const label = entry.substring(colonIdx + 1);
            if (mode === 'absent') {
                const norm = normalizeDetailFilterValue(label);
                // Convert "Artist" → "no artist", matching server _normalize_missing_data_key
                const serverKey = norm && !norm.startsWith('no ') ? `no ${norm}` : norm;
                if (serverKey) missingData.push(serverKey);
            }
        }

        // Collect missing-source conditions from the missing source filter toggles.
        const missingSources = [];
        for (const [source, active] of Object.entries(state.missingSourceFilter || {})) {
            if (active) missingSources.push(source);
        }

        // Collect status filters — map display labels to server keys.
        const includeStatus = [];
        const excludeStatus = [];
        for (const entry of getAdvancedFilterValues('status')) {
            const { mode, name } = _parseModePrefixEntry(entry);
            const serverKey = STATUS_PILL_TO_SERVER[name] || name;
            if (mode === 'exclude') {
                excludeStatus.push(serverKey);
            } else {
                includeStatus.push(serverKey);
            }
        }

        // Combine include + exclude arrays for has-filter checks
        const artistNames = [...includeArtists, ...excludeArtists];
        const collectionNames = [...includeCollections, ...excludeCollections];
        const statusNames = [...includeStatus, ...excludeStatus];

        const hasSupportedStructuredFilters = Boolean(
            generationSoftwares.length || sourceSites.length || mimetypes.length || nsfwRatings.length || nsfwSafety.length || artistNames.length || collectionNames.length || a1111Hires || a1111RegionalPrompter || a1111Adetailer || includeTags.length || excludeTags.length || missingData.length || missingSources.length || statusNames.length
        );

        const normalizedQuery = String(query || '').trim();

        if (!hasSupportedStructuredFilters && !normalizedQuery) {
            return null;
        }
        return {
            search: normalizedQuery,
            generationSoftwares,
            sourceSites,
            mimetypes,
            nsfwRatings,
            nsfwSafety,
            artistNames: includeArtists,
            excludeArtists,
            collectionNames: includeCollections,
            excludeCollections,
            a1111Hires: a1111Hires ? [a1111Hires] : null,
            a1111RegionalPrompter: a1111RegionalPrompter ? [a1111RegionalPrompter] : null,
            a1111Adetailer: a1111Adetailer ? [a1111Adetailer] : null,
            includeTags,
            excludeTags,
            missingData,
            missingSources,
            includeStatus,
            excludeStatus,
            signature: JSON.stringify({
                search: normalizedQuery,
                generationSoftwares,
                sourceSites,
                mimetypes,
                nsfwRatings,
                nsfwSafety,
                includeArtists,
                excludeArtists,
                includeCollections,
                excludeCollections,
                a1111Hires,
                a1111RegionalPrompter,
                a1111Adetailer,
                includeTags,
                excludeTags,
                missingData,
                missingSources,
                includeStatus,
                excludeStatus,
            }),
        };

    }

    function _appendNsfwVisibilityParams(params) {
        // Always apply NSFW visibility preference as a base catalog constraint.
        const visibilityMode = state.nsfwVisibility || 'explicit';
        if (visibilityMode !== 'explicit') {
            const ratings = getNsfwVisibilityServerRatings(visibilityMode);
            ratings.forEach((value) => params.append('nsfw_rating', value));
        }
    }

    function buildImagesRequestUrl(skip, limit, cursor = null) {
        const params = new URLSearchParams({
            limit: String(limit),
            sort_by: state.sortOrder,
            group_variants: state.groupVariantsEnabled ? 'true' : 'false',
        });

        // Use cursor-based pagination when a cursor is available;
        // otherwise fall back to legacy offset pagination.
        if (cursor != null) {
            params.set('cursor', String(cursor));
        } else {
            params.set('skip', String(skip));
        }

        // Always include NSFW visibility preference to constrain the base catalog.
        _appendNsfwVisibilityParams(params);

        const config = state.serverFilterMode ? state.activeServerFilterConfig : null;
        if (config) {
            if (config.search) {
                params.set('search', config.search);
            }
            config.generationSoftwares.forEach((value) => params.append('generation_software', value));
            config.sourceSites.forEach((value) => params.append('source_site', value));
            config.mimetypes.forEach((value) => params.append('mimetype', value));
            config.nsfwRatings.forEach((value) => params.append('nsfw_rating', value));
            config.nsfwSafety.forEach((value) => params.append('nsfw_safety', value));
            config.artistNames.forEach((value) => params.append('artist_name', value));
            config.collectionNames.forEach((value) => params.append('collection_name', value));
            if (config.excludeArtists && config.excludeArtists.length) {
                config.excludeArtists.forEach((value) => params.append('exclude_artist_name', value));
            }
            if (config.excludeCollections && config.excludeCollections.length) {
                config.excludeCollections.forEach((value) => params.append('exclude_collection_name', value));
            }
            if (config.a1111Hires && config.a1111Hires.length) {
                config.a1111Hires.forEach((value) => params.append('a1111_hires', value));
            }
            if (config.a1111RegionalPrompter && config.a1111RegionalPrompter.length) {
                config.a1111RegionalPrompter.forEach((value) => params.append('a1111_regional_prompter', value));
            }
            if (config.a1111Adetailer && config.a1111Adetailer.length) {
                config.a1111Adetailer.forEach((value) => params.append('a1111_adetailer', value));
            }
            if (config.includeTags && config.includeTags.length) {
                config.includeTags.forEach((value) => params.append('include_tag', value));
            }
            if (config.excludeTags && config.excludeTags.length) {
                config.excludeTags.forEach((value) => params.append('exclude_tag', value));
            }
            if (config.missingData && config.missingData.length) {
                config.missingData.forEach((value) => params.append('missing_data', value));
            }
            if (config.missingSources && config.missingSources.length) {
                config.missingSources.forEach((value) => params.append('missing_source', value));
            }
        }

        return `/api/images/?${params.toString()}`;
    }

    /**
     * Build a POST /api/query request body equivalent to buildImagesRequestUrl().
     *
     * Maps the same GET query params into the structured GalleryQueryRequest
     * schema accepted by the unified query endpoint.
     *
     * @param {number} skip   - Offset for legacy pagination (used when no cursor).
     * @param {number} limit  - Page size.
     * @param {string|null} cursor - Opaque cursor from previous response, or null.
     * @returns {{ url: string, body: object }} Ready for fetch(url, { method: 'POST', body: JSON.stringify(body) }).
     */
    /**
     * Build the GalleryFilter + search portion of a request body.
     * Shared by buildQueryRequestPost() and fetchSuggestions() so both
     * endpoints send identical filter context.
     * @returns {{ filter?: object, search?: string }}
     */
    function _buildFilterBody() {
        const body = {};

        // ── NSFW visibility → filter.hidden ────────────────────────────
        const visibilityMode = state.nsfwVisibility || 'explicit';
        if (visibilityMode !== 'explicit') {
            const ratings = getNsfwVisibilityServerRatings(visibilityMode);
            if (ratings.length) {
                body.filter = body.filter || {};
                body.filter.hidden = body.filter.hidden || {};
                body.filter.hidden.nsfw = ratings;
            }
        }

        // ── Structured filters from active config ──────────────────────
        const config = state.serverFilterMode ? state.activeServerFilterConfig : null;
        if (config) {
            // Search
            if (config.search) {
                body.search = config.search;
            }

            // Ensure filter object exists
            body.filter = body.filter || {};
            body.filter.included = body.filter.included || {};
            body.filter.excluded = body.filter.excluded || {};
            const inc = body.filter.included;
            const exc = body.filter.excluded;

            // Software
            if (config.generationSoftwares && config.generationSoftwares.length) {
                inc.software = config.generationSoftwares;
            }
            // Source sites
            if (config.sourceSites && config.sourceSites.length) {
                inc.source = config.sourceSites;
            }
            // Mimetypes
            if (config.mimetypes && config.mimetypes.length) {
                inc.mimetype = config.mimetypes;
            }
            // NSFW ratings (additional to visibility)
            if (config.nsfwRatings && config.nsfwRatings.length) {
                body.filter.hidden = body.filter.hidden || {};
                body.filter.hidden.nsfw = [
                    ...(body.filter.hidden.nsfw || []),
                    ...config.nsfwRatings,
                ];
            }
            // NSFW safety
            if (config.nsfwSafety && config.nsfwSafety.length) {
                body.filter.hidden = body.filter.hidden || {};
                body.filter.hidden.nsfw_safety = config.nsfwSafety;
            }
            // Artists
            if (config.artistNames && config.artistNames.length) {
                inc.artist = config.artistNames;
            }
            if (config.excludeArtists && config.excludeArtists.length) {
                exc.artist = config.excludeArtists;
            }
            // Collections
            if (config.collectionNames && config.collectionNames.length) {
                inc.collection = config.collectionNames;
            }
            if (config.excludeCollections && config.excludeCollections.length) {
                exc.collection = config.excludeCollections;
            }
            // Tags
            if (config.includeTags && config.includeTags.length) {
                inc.tag = config.includeTags;
            }
            if (config.excludeTags && config.excludeTags.length) {
                exc.tag = config.excludeTags;
            }
            // A1111 features
            if (config.a1111Hires && config.a1111Hires.length) {
                inc.feature = [...(inc.feature || []), ...config.a1111Hires.map((v) => `a1111_hires:${v}`)];
            }
            if (config.a1111RegionalPrompter && config.a1111RegionalPrompter.length) {
                inc.feature = [...(inc.feature || []), ...config.a1111RegionalPrompter.map((v) => `a1111_regional_prompter:${v}`)];
            }
            if (config.a1111Adetailer && config.a1111Adetailer.length) {
                inc.feature = [...(inc.feature || []), ...config.a1111Adetailer.map((v) => `a1111_adetailer:${v}`)];
            }
            // Missing data
            if (config.missingData && config.missingData.length) {
                body.filter.missing = config.missingData;
            }
            // Missing sources — mapped to filter.missing
            if (config.missingSources && config.missingSources.length) {
                body.filter.missing = [
                    ...(body.filter.missing || []),
                    ...config.missingSources.map((s) => `source:${s}`),
                ];
            }
            // Status filters
            if (config.includeStatus && config.includeStatus.length) {
                inc.status = config.includeStatus;
            }
            if (config.excludeStatus && config.excludeStatus.length) {
                exc.status = config.excludeStatus;
            }
        }

        // Clean up empty sections to keep the request lean
        if (body.filter) {
            if (body.filter.included && Object.keys(body.filter.included).length === 0) {
                delete body.filter.included;
            }
            if (body.filter.excluded && Object.keys(body.filter.excluded).length === 0) {
                delete body.filter.excluded;
            }
            if (body.filter.hidden && Object.keys(body.filter.hidden).length === 0) {
                delete body.filter.hidden;
            }
            if (body.filter.missing && body.filter.missing.length === 0) {
                delete body.filter.missing;
            }
            if (Object.keys(body.filter).length === 0) {
                delete body.filter;
            }
        }

        return body;
    }

    function buildQueryRequestPost(skip, limit, cursor = null) {
        const body = _buildFilterBody();

        // ── Image page spec ────────────────────────────────────────────
        const images = {
            limit,
            group_variants: state.groupVariantsEnabled,
        };
        if (cursor != null) {
            images.cursor = String(cursor);
        } else {
            images.offset = skip;
        }
        // Map legacy sort_by values to ImagePageSpec sort/order.
        // first_added → date_added asc (oldest first), last_added → date_added desc (newest first)
        images.sort = 'date_added';
        images.order = state.sortOrder === 'last_added' ? 'desc' : 'asc';
        body.images = images;

        return { url: '/api/query', body };
    }

    function buildImageKeysRequestUrl(config = null) {
        const params = new URLSearchParams({
            group_variants: state.groupVariantsEnabled ? 'true' : 'false',
        });

        // Always include NSFW visibility preference to constrain the base catalog.
        _appendNsfwVisibilityParams(params);

        if (config) {
            if (config.search) {
                params.set('search', config.search);
            }
            config.generationSoftwares.forEach((value) => params.append('generation_software', value));
            config.sourceSites.forEach((value) => params.append('source_site', value));
            config.mimetypes.forEach((value) => params.append('mimetype', value));
            config.nsfwRatings.forEach((value) => params.append('nsfw_rating', value));
            config.nsfwSafety.forEach((value) => params.append('nsfw_safety', value));
            config.artistNames.forEach((value) => params.append('artist_name', value));
            config.collectionNames.forEach((value) => params.append('collection_name', value));
            if (config.excludeArtists && config.excludeArtists.length) {
                config.excludeArtists.forEach((value) => params.append('exclude_artist_name', value));
            }
            if (config.excludeCollections && config.excludeCollections.length) {
                config.excludeCollections.forEach((value) => params.append('exclude_collection_name', value));
            }
            if (config.a1111Hires && config.a1111Hires.length) {
                config.a1111Hires.forEach((value) => params.append('a1111_hires', value));
            }
            if (config.a1111RegionalPrompter && config.a1111RegionalPrompter.length) {
                config.a1111RegionalPrompter.forEach((value) => params.append('a1111_regional_prompter', value));
            }
            if (config.a1111Adetailer && config.a1111Adetailer.length) {
                config.a1111Adetailer.forEach((value) => params.append('a1111_adetailer', value));
            }
            if (config.includeTags && config.includeTags.length) {
                config.includeTags.forEach((value) => params.append('include_tag', value));
            }
            if (config.excludeTags && config.excludeTags.length) {
                config.excludeTags.forEach((value) => params.append('exclude_tag', value));
            }
            if (config.missingData && config.missingData.length) {
                config.missingData.forEach((value) => params.append('missing_data', value));
            }
            if (config.missingSources && config.missingSources.length) {
                config.missingSources.forEach((value) => params.append('missing_source', value));
            }
        }

        const queryString = params.toString();
        return queryString ? `/api/images/keys?${queryString}` : '/api/images/keys';
    }

    function getImageCountDisplayState() {
        const filtersActive = hasActiveGalleryFilters();
        const total = Number(state.totalImageCount || 0);
        const filtered = filtersActive
            ? (state.serverFilterMode ? Number(state.filteredMatchCount || 0) : state.filteredImages.length)
            : total;
        return {
            filtersActive,
            filtered,
            total,
            filteredLabel: `${filtered} match${filtered === 1 ? '' : 'es'}`,
            totalLabel: `${total} image${total === 1 ? '' : 's'}`,
        };
    }

    function renderImageCountControl() {
        if (!imageCount) {
            return;
        }

        const display = getImageCountDisplayState();
        const stats = getSelectionStats();
        const allVisibleSelected = Boolean(state.filteredImages.length) && stats.visible === state.filteredImages.length;
        const allMatchingLoaded = !state.hasMore;
        const allMatchingSelected = allMatchingLoaded && allVisibleSelected;
        const allCatalogSelected = display.total > 0 && stats.total === display.total;

        if (state.foregroundBusy?.visible && state.foregroundBusy.countPillLabel) {
            imageCount.innerHTML = `<button type="button" class="count-pill-segment is-processing" disabled>${state.foregroundBusy.countPillLabel}</button>`;
            imageCount.classList.add('is-processing');
            imageCount.setAttribute('aria-busy', 'true');
            return;
        }

        const canSelectMatches = display.filtered > 0 && !allMatchingSelected;
        const canSelectCatalog = display.total > 0 && !allCatalogSelected;
        const firstClasses = ['count-pill-segment'];
        const secondClasses = ['count-pill-segment'];
        if (allMatchingSelected && !(display.filtersActive && allCatalogSelected)) {
            firstClasses.push('is-emphasized');
        }
        if (allCatalogSelected) {
            secondClasses.push('is-emphasized');
        }

        if (!display.filtersActive) {
            imageCount.innerHTML = `
                <button
                    type="button"
                    class="${secondClasses.join(' ')}"
                    data-count-action="select-catalog"
                    title="${canSelectCatalog ? 'Select the entire catalog' : 'The entire catalog is already selected'}"
                    ${canSelectCatalog ? '' : 'disabled'}
                >${display.totalLabel}</button>
            `;
            imageCount.classList.remove('is-processing');
            imageCount.setAttribute('aria-busy', state.foregroundBusy?.active ? 'true' : 'false');
            return;
        }

        imageCount.innerHTML = `
            <button
                type="button"
                class="${firstClasses.join(' ')}"
                data-count-action="select-matches"
                title="${canSelectMatches ? 'Select all matching items' : 'All matching items are already selected'}"
                ${canSelectMatches ? '' : 'disabled'}
            >${display.filteredLabel}</button>
            <span class="count-pill-divider" aria-hidden="true">of</span>
            <button
                type="button"
                class="${secondClasses.join(' ')}"
                data-count-action="select-catalog"
                title="${canSelectCatalog ? 'Select the entire catalog' : 'The entire catalog is already selected'}"
                ${canSelectCatalog ? '' : 'disabled'}
            >${display.totalLabel}</button>
        `;
        imageCount.classList.remove('is-processing');
        imageCount.setAttribute('aria-busy', state.foregroundBusy?.active ? 'true' : 'false');
    }

    function updateForegroundBusyUi() {
        renderImageCountControl();

        updatePagingUi();
    }

    function startForegroundBusy(kind, options = {}) {
        clearForegroundBusyRevealTimer();
        state.foregroundBusy = {
            active: true,
            visible: false,
            kind,
            countPillLabel: String(options.countPillLabel || '').trim(),
            statusMessage: String(options.statusMessage || '').trim(),
            revealTimerId: window.setTimeout(() => {
                if (!isForegroundBusy(kind)) {
                    return;
                }
                state.foregroundBusy.visible = true;
                state.foregroundBusy.revealTimerId = null;
                updateForegroundBusyUi();
            }, FOREGROUND_BUSY_REVEAL_DELAY_MS),
        };
        updateForegroundBusyUi();
    }

    function finishForegroundBusy(kind = null) {
        if (kind && !isForegroundBusy(kind)) {
            return;
        }

        clearForegroundBusyRevealTimer();
        state.foregroundBusy = {
            active: false,
            visible: false,
            kind: null,
            countPillLabel: '',
            statusMessage: '',
            revealTimerId: null,
        };
        updateForegroundBusyUi();
    }

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

        // Extract from legacy blob fields (civitai_data)
        const civitai = image.civitai_data || image.civitai || {};
        addTagCollection(bySource.civitai, civitai?.tags);
        addTagCollection(bySource.civitai, civitai?.meta?.tags);
        addTagCollection(bySource.civitai, civitai?.image?.tags);

        // Extract from post-backfill observation data (newer images)
        addTagCollection(bySource.civitai, image.civitai_tags);

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

    function getImageNegativeUserTags(image) {
        const values = new Set();
        if (!image || typeof image !== 'object') {
            return [];
        }

        addTagCollection(values, image.user_negative_tags);
        const exif = image.exif_data && typeof image.exif_data === 'object' ? image.exif_data : {};
        addTagCollection(values, exif.user_negative_tags);

        return Array.from(values).sort((left, right) => left.localeCompare(right));
    }

    function buildEffectiveImageTagEntries(image) {
        const bySource = extractImageScopeTags(image);
        const negativeSet = new Set(getImageNegativeUserTags(image));
        const entries = [];

        TAG_SOURCE_ORDER.forEach((source) => {
            const names = Array.isArray(bySource[source]) ? [...bySource[source]] : [];
            names.sort((left, right) => left.localeCompare(right));
            names.forEach((name) => {
                const normalized = normalizeTagName(name);
                if (!normalized) {
                    return;
                }
                if (source === 'civitai' && negativeSet.has(normalized)) {
                    return;
                }
                entries.push({
                    name: normalized,
                    source,
                });
            });
        });

        return entries;
    }

    function syncImageNegativeTagsLocally(fileHash, nextNegativeTags) {
        const normalizedList = Array.isArray(nextNegativeTags)
            ? nextNegativeTags
                .map((item) => normalizeTagName(item))
                .filter(Boolean)
            : [];

        state.allImages.forEach((entry) => {
            if (String(getEditableFileHash(entry) || '') !== String(fileHash || '')) {
                return;
            }
            entry.user_negative_tags = [...normalizedList];
        });
    }

    async function persistImageNegativeTags(image, nextNegativeTags) {
        const editableHash = getEditableFileHash(image);
        if (!editableHash) {
            throw new Error('Selected image has no file hash.');
        }

        const normalizedList = Array.isArray(nextNegativeTags)
            ? nextNegativeTags
                .map((item) => normalizeTagName(item))
                .filter(Boolean)
            : [];

        const result = await saveImageMetadata(editableHash, {
            user_negative_tags: normalizedList,
        });

        const savedNegativeTags = Array.isArray(result?.user_negative_tags)
            ? result.user_negative_tags
                .map((item) => normalizeTagName(item))
                .filter(Boolean)
            : normalizedList;

        image.user_negative_tags = [...savedNegativeTags];
        syncImageNegativeTagsLocally(editableHash, savedNegativeTags);
        return savedNegativeTags;
    }

    function createDetailTagChip({ name, source, negative = false, onRemove, showSource = true }) {
        const chip = document.createElement('span');
        chip.className = `detail-tag-chip source-${source}${negative ? ' negative' : ''}`;

        const label = document.createElement('span');
        label.className = 'detail-tag-chip-label';
        label.textContent = name;
        chip.appendChild(label);

        if (showSource) {
            const sourceBadge = document.createElement('span');
            sourceBadge.className = 'detail-tag-chip-source';
            sourceBadge.textContent = source;
            chip.appendChild(sourceBadge);
        }

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'detail-tag-chip-remove';
        removeBtn.textContent = 'x';
        removeBtn.setAttribute('aria-label', negative
            ? `Remove negative override for ${name}`
            : `Add negative override for ${name}`);
        removeBtn.title = negative
            ? `Remove negative override for ${name}`
            : `Add negative override for ${name}`;
        removeBtn.addEventListener('click', () => {
            if (typeof onRemove === 'function') {
                void onRemove();
            }
        });
        chip.appendChild(removeBtn);

        return chip;
    }

    function renderFullscreenEffectiveTags(image) {
        if (!fullscreenEffectiveTagsCloud || !fullscreenNegativeTagsWrap || !fullscreenNegativeTagsCloud) {
            return;
        }

        fullscreenEffectiveTagsCloud.innerHTML = '';
        fullscreenNegativeTagsCloud.innerHTML = '';

        if (!image) {
            fullscreenNegativeTagsWrap.classList.add('hidden');
            return;
        }

        const effectiveEntries = buildEffectiveImageTagEntries(image);
        const negativeTags = getImageNegativeUserTags(image);

        if (!effectiveEntries.length) {
            const empty = document.createElement('p');
            empty.className = 'detail-tag-empty';
            empty.textContent = 'No effective tags found for this item.';
            fullscreenEffectiveTagsCloud.appendChild(empty);
        } else {
            effectiveEntries.forEach((entry) => {
                const chip = createDetailTagChip({
                    name: entry.name,
                    source: entry.source,
                    showSource: false,
                    onRemove: async () => {
                        const nextNegativeSet = new Set(getImageNegativeUserTags(image));
                        nextNegativeSet.add(entry.name);
                        try {
                            await persistImageNegativeTags(image, Array.from(nextNegativeSet));
                            showToast(`Added negative override: ${entry.name}`, 'success');
                            renderFullscreenEffectiveTags(image);
                        } catch (error) {
                            showToast(`Could not save negative tag: ${error.message}`, 'warn');
                        }
                    },
                });
                fullscreenEffectiveTagsCloud.appendChild(chip);
            });
        }

        if (!negativeTags.length) {
            fullscreenNegativeTagsWrap.classList.add('hidden');
            return;
        }

        fullscreenNegativeTagsWrap.classList.remove('hidden');
        negativeTags.forEach((tagName) => {
            const chip = createDetailTagChip({
                name: tagName,
                source: 'user',
                negative: true,
                showSource: false,
                onRemove: async () => {
                    const nextNegativeTags = getImageNegativeUserTags(image)
                        .filter((value) => value !== tagName);
                    try {
                        await persistImageNegativeTags(image, nextNegativeTags);
                        showToast(`Removed negative override: ${tagName}`, 'success');
                        renderFullscreenEffectiveTags(image);
                    } catch (error) {
                        showToast(`Could not remove negative tag: ${error.message}`, 'warn');
                    }
                },
            });
            fullscreenNegativeTagsCloud.appendChild(chip);
        });
    }

    /* ── User Tags pane: multi-select cloud + add/remove ──────────── */

    function getImageUserTags(image) {
        if (!image || typeof image !== 'object') return [];
        const tags = new Set();
        const addAll = (arr) => { (arr || []).forEach((t) => { if (t) tags.add(normalizeTagName(t)); }); };
        addAll(image.user_tags);
        const exif = image.exif_data && typeof image.exif_data === 'object' ? image.exif_data : {};
        addAll(exif.user_tags);
        return Array.from(tags).sort((a, b) => a.localeCompare(b));
    }

    /**
     * Collect all distinct user tags across a group of images,
     * with match counts for partial-application badges.
     * Returns Map<string, { name: string, count: number }> sorted alphabetically.
     */
    function collectUserTagsAcrossGroup(images) {
        const tagMap = new Map();
        for (const image of images) {
            const tags = getImageUserTags(image);
            for (const t of tags) {
                const existing = tagMap.get(t);
                if (existing) {
                    existing.count++;
                } else {
                    tagMap.set(t, { name: t, count: 1 });
                }
            }
        }
        return new Map([...tagMap.entries()].sort((a, b) => a[0].localeCompare(b[0])));
    }

    /** Get existing user tag names for autosuggest, merged from filter options + current selection. */
    function getUserTagSuggestions() {
        const pool = new Set();
        const fromFilter = state.filterOptions?.tagNamesBySource?.user;
        if (Array.isArray(fromFilter)) {
            fromFilter.forEach((t) => { if (t) pool.add(normalizeTagName(t)); });
        }
        // Also include user tags from all currently loaded images so that
        // newly-added tags appear in suggestions without a server round-trip.
        if (Array.isArray(state.allImages)) {
            for (const image of state.allImages) {
                const tags = getImageUserTags(image);
                for (const t of tags) {
                    if (t) pool.add(t);
                }
            }
        }
        return Array.from(pool).sort();
    }

    function getAuthorityCloud(source) {
        if (source === 'civitai') return civitaiTagsCloud;
        if (source === 'danbooru') return danbooruTagsCloud;
        if (source === 'prompt') return promptTagsCloud;
        if (source === 'user') return userTagsCloud;
        return null;
    }

    function collectAuthorityTagsAcrossGroup(images, source) {
        const tagMap = new Map();
        for (const image of images) {
            const bySource = extractImageScopeTags(image);
            const names = Array.isArray(bySource[source]) ? bySource[source] : [];
            names.forEach((name) => {
                const normalized = normalizeTagName(name);
                if (!normalized) {
                    return;
                }
                const existing = tagMap.get(normalized);
                if (existing) {
                    existing.count += 1;
                } else {
                    tagMap.set(normalized, { name: normalized, count: 1 });
                }
            });
        }
        return new Map([...tagMap.entries()].sort((a, b) => a[0].localeCompare(b[0])));
    }

    function isAuthorityTagFilterActive(source, tagName) {
        if (!Array.isArray(state.treeTagFilter)) {
            return false;
        }
        const normalizedSource = normalizeTagName(source);
        const normalizedName = normalizeTagName(tagName);
        return state.treeTagFilter.some((filter) => (
            filter
            && filter.mode !== 'exclude'
            && normalizeTagName(filter.source) === normalizedSource
            && normalizeTagName(filter.name) === normalizedName
        ));
    }

    function toggleAuthorityTagFilter(source, tagName) {
        const normalizedSource = normalizeTagName(source);
        const normalizedName = normalizeTagName(tagName);
        if (!normalizedSource || !normalizedName) {
            return;
        }

        const existing = Array.isArray(state.treeTagFilter) ? state.treeTagFilter : [];
        const keep = existing.filter((filter) => filter && filter.mode === 'exclude');
        const include = existing.filter((filter) => filter && filter.mode !== 'exclude');
        const nextInclude = include.filter((filter) => !(
            normalizeTagName(filter.source) === normalizedSource
            && normalizeTagName(filter.name) === normalizedName
        ));

        const wasActive = nextInclude.length !== include.length;
        if (!wasActive) {
            nextInclude.push({ source: normalizedSource, name: normalizedName, mode: 'include' });
        }

        const nextFilters = [...nextInclude, ...keep];
        state.treeTagFilter = nextFilters.length > 0 ? nextFilters : null;
        renderTreeTagFilterIndicator();
    }

    async function toggleNegativeOverrideForTag(tagName) {
        const normalized = normalizeTagName(tagName);
        if (!normalized) {
            return;
        }

        const targets = getDetailSelectionGroup(getSelectedImage());
        if (!targets.length) {
            return;
        }

        const total = targets.length;
        let alreadyApplied = 0;
        targets.forEach((image) => {
            if (getImageNegativeUserTags(image).includes(normalized)) {
                alreadyApplied += 1;
            }
        });
        const shouldApply = alreadyApplied < total;

        let successCount = 0;
        for (const image of targets) {
            const editableHash = getEditableFileHash(image);
            if (!editableHash) {
                continue;
            }

            const current = getImageNegativeUserTags(image);
            const hasTag = current.includes(normalized);
            if ((shouldApply && hasTag) || (!shouldApply && !hasTag)) {
                successCount += 1;
                continue;
            }

            const next = shouldApply
                ? [...current, normalized]
                : current.filter((entry) => entry !== normalized);

            try {
                const result = await saveImageMetadata(editableHash, { user_negative_tags: next });
                const saved = Array.isArray(result?.user_negative_tags)
                    ? result.user_negative_tags.map((entry) => normalizeTagName(entry)).filter(Boolean)
                    : next;
                image.user_negative_tags = [...saved];
                successCount += 1;
            } catch {
                // Keep processing remaining images.
            }
        }

        if (successCount > 0) {
            const action = shouldApply ? 'Applied' : 'Removed';
            showToast(`${action} negative override for "${normalized}" on ${successCount}/${total} image(s).`, 'success');
            renderAuthorityTagPanels();
            renderFullscreenEffectiveTags(getSelectedImage());
            await postSelectedImageTagsToTree(getSelectedImage());
        }
    }

    function renderAuthorityTagPanel(source) {
        const cloud = getAuthorityCloud(source);
        if (!cloud) {
            return;
        }
        cloud.innerHTML = '';

        const targets = getDetailSelectionGroup(getSelectedImage());
        if (!targets.length) {
            const empty = document.createElement('p');
            empty.className = 'detail-tag-empty';
            empty.textContent = 'Select an image to view tags.';
            cloud.appendChild(empty);
            return;
        }

        const total = targets.length;
        const tagMap = collectAuthorityTagsAcrossGroup(targets, source);
        if (!tagMap.size) {
            const empty = document.createElement('p');
            empty.className = 'detail-tag-empty';
            empty.textContent = `No ${source} tags for the current selection.`;
            cloud.appendChild(empty);
            return;
        }

        for (const [, entry] of tagMap) {
            const chip = document.createElement('span');
            chip.className = `authority-tag-chip source-${source}${isAuthorityTagFilterActive(source, entry.name) ? ' filter-active' : ''}`;

            let overrideCount = 0;
            targets.forEach((image) => {
                if (getImageNegativeUserTags(image).includes(entry.name)) {
                    overrideCount += 1;
                }
            });
            if (overrideCount === total) {
                chip.classList.add('override-active');
            }

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'authority-tag-remove';
            removeBtn.textContent = 'x';
            removeBtn.title = chip.classList.contains('override-active')
                ? `Remove negative override for ${entry.name}`
                : `Add negative override for ${entry.name}`;
            removeBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                void toggleNegativeOverrideForTag(entry.name);
            });
            chip.appendChild(removeBtn);

            const labelBtn = document.createElement('button');
            labelBtn.type = 'button';
            labelBtn.className = 'authority-tag-label';
            labelBtn.textContent = entry.name;
            labelBtn.title = `Toggle ${source} filter for "${entry.name}"`;
            labelBtn.addEventListener('click', () => {
                toggleAuthorityTagFilter(source, entry.name);
                void applyFilter();
                renderAuthorityTagPanels();
            });
            chip.appendChild(labelBtn);

            if (total > 1) {
                const countBadge = document.createElement('span');
                countBadge.className = 'authority-tag-count';
                countBadge.textContent = `${entry.count}`;
                countBadge.title = `${entry.count} of ${total} selected image(s) have this tag`;
                chip.appendChild(countBadge);
            }

            cloud.appendChild(chip);
        }
    }

    function renderAuthorityTagPanels() {
        renderAuthorityTagPanel('civitai');
        renderAuthorityTagPanel('danbooru');
        renderAuthorityTagPanel('prompt');
        renderAuthorityTagPanel('user');
    }

    async function applyUserTagToGroup(tagName) {
        const normalised = normalizeTagName(tagName);
        if (!normalised) { showToast('Tag name cannot be empty.', 'warn'); return; }
        const targets = getDetailSelectionGroup(getSelectedImage());
        if (!targets.length) { showToast('No images selected.', 'warn'); return; }

        let successCount = 0;
        for (const image of targets) {
            const editableHash = getEditableFileHash(image);
            if (!editableHash) continue;
            const current = getImageUserTags(image);
            if (current.includes(normalised)) { successCount++; continue; }
            const next = [...current, normalised].sort((a, b) => a.localeCompare(b));
            try {
                const result = await saveImageMetadata(editableHash, { user_tags: next });
                const saved = Array.isArray(result?.user_tags)
                    ? result.user_tags.map((t) => normalizeTagName(t)).filter(Boolean)
                    : next;
                image.user_tags = [...saved];
                successCount++;
            } catch (err) {
                showToast(`Failed on ${image.file_hash?.slice(0, 8) || 'image'}: ${err.message}`, 'warn');
            }
        }
        showToast(`Added user tag "${normalised}" to ${successCount}/${targets.length} image(s).`, 'success');
        renderUserTagsPanel();
        postSelectedImageTagsToTree(getSelectedImage());
        postSelectedImageModelsToModelsFrame(getSelectedImage());
    }

    async function removeUserTagFromGroup(tagName) {
        const normalised = normalizeTagName(tagName);
        if (!normalised) return;
        const targets = getDetailSelectionGroup(getSelectedImage());
        if (!targets.length) return;

        let successCount = 0;
        for (const image of targets) {
            const editableHash = getEditableFileHash(image);
            if (!editableHash) continue;
            const current = getImageUserTags(image);
            if (!current.includes(normalised)) { successCount++; continue; }
            const next = current.filter((t) => t !== normalised);
            try {
                const result = await saveImageMetadata(editableHash, { user_tags: next });
                const saved = Array.isArray(result?.user_tags)
                    ? result.user_tags.map((t) => normalizeTagName(t)).filter(Boolean)
                    : next;
                image.user_tags = [...saved];
                successCount++;
            } catch (err) {
                showToast(`Failed on ${image.file_hash?.slice(0, 8) || 'image'}: ${err.message}`, 'warn');
            }
        }
        showToast(`Removed user tag "${normalised}" from ${successCount}/${targets.length} image(s).`, 'success');
        renderUserTagsPanel();
        postSelectedImageTagsToTree(getSelectedImage());
        postSelectedImageModelsToModelsFrame(getSelectedImage());
    }

    function renderUserTagsPanel() {
        if (!userTagsCloud) return;
        userTagsCloud.innerHTML = '';

        const targets = getDetailSelectionGroup(getSelectedImage());
        if (!targets.length) {
            const empty = document.createElement('p');
            empty.className = 'detail-tag-empty';
            empty.textContent = 'Select an image to manage user tags.';
            userTagsCloud.appendChild(empty);
            return;
        }

        const totalCount = targets.length;
        const tagMap = collectUserTagsAcrossGroup(targets);

        // Render existing tag chips
        for (const [, entry] of tagMap) {
            const isPartial = entry.count < totalCount;
            const chip = document.createElement('span');
            chip.className = `user-tag-chip${isPartial ? ' partial' : ''}`;

            const label = document.createElement('span');
            label.className = 'user-tag-chip-label';
            label.textContent = entry.name;
            label.title = `Click to filter gallery by user tag "${entry.name}"`;
            label.style.cursor = 'pointer';
            label.addEventListener('click', () => {
                setTreeTagFilter({ source: 'user', name: entry.name });
                void applyFilter();
            });
            chip.appendChild(label);

            if (totalCount > 1) {
                const badge = document.createElement('span');
                badge.className = 'user-tag-chip-count';
                badge.textContent = `${entry.count}/${totalCount}`;
                badge.title = `${entry.count} of ${totalCount} selected image(s) have this tag`;
                chip.appendChild(badge);
            }

            const removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.className = 'user-tag-chip-remove';
            removeBtn.textContent = '×';
            removeBtn.title = `Remove "${entry.name}" from all selected images`;
            removeBtn.addEventListener('click', () => { void removeUserTagFromGroup(entry.name); });
            chip.appendChild(removeBtn);

            userTagsCloud.appendChild(chip);
        }

        // "+ New" chip
        const newBtn = document.createElement('button');
        newBtn.type = 'button';
        newBtn.className = 'user-tag-new-btn';
        newBtn.textContent = '+ New';
        newBtn.title = 'Add a user tag to all selected images';
        newBtn.addEventListener('click', () => {
            // Replace button with inline input
            const wrap = document.createElement('span');
            wrap.className = 'user-tag-input-wrap';

            const input = document.createElement('input');
            input.type = 'text';
            input.className = 'user-tag-input';
            input.placeholder = 'Tag name…';

            const suggest = document.createElement('div');
            suggest.className = 'user-tag-suggest hidden';

            const cancelBtn = document.createElement('button');
            cancelBtn.type = 'button';
            cancelBtn.className = 'user-tag-input-cancel';
            cancelBtn.textContent = '×';
            cancelBtn.title = 'Cancel';

            wrap.appendChild(input);
            wrap.appendChild(suggest);
            wrap.appendChild(cancelBtn);
            newBtn.replaceWith(wrap);
            input.focus();

            // Build autosuggest list
            let activeIndex = -1;
            const allSuggestions = getUserTagSuggestions();
            const appliedTags = new Set(tagMap.keys());

            function renderSuggestions(filter = '') {
                suggest.innerHTML = '';
                activeIndex = -1;
                const filtered = filter
                    ? allSuggestions.filter((s) => s.includes(filter) && !appliedTags.has(s))
                    : allSuggestions.filter((s) => !appliedTags.has(s));
                if (!filtered.length) {
                    suggest.classList.add('hidden');
                    return;
                }
                filtered.slice(0, 20).forEach((name, i) => {
                    const item = document.createElement('div');
                    item.className = 'user-tag-suggest-item';
                    item.textContent = name;
                    item.addEventListener('mousedown', (e) => {
                        e.preventDefault(); // prevent blur
                        input.value = name;
                        suggest.classList.add('hidden');
                        commitTag(name);
                    });
                    suggest.appendChild(item);
                });
                suggest.classList.remove('hidden');
            }

            function updateActiveItem() {
                const items = suggest.querySelectorAll('.user-tag-suggest-item');
                items.forEach((el, i) => el.classList.toggle('active', i === activeIndex));
            }

            function commitTag(value) {
                const name = normalizeTagName(value);
                if (!name) { teardown(); return; }
                void applyUserTagToGroup(name);
                teardown();
            }

            function teardown() {
                wrap.replaceWith(newBtn);
            }

            input.addEventListener('input', () => { renderSuggestions(input.value.trim().toLowerCase()); });
            input.addEventListener('keydown', (e) => {
                const items = suggest.querySelectorAll('.user-tag-suggest-item');
                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    activeIndex = Math.min(activeIndex + 1, items.length - 1);
                    updateActiveItem();
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    activeIndex = Math.max(activeIndex - 1, -1);
                    updateActiveItem();
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    if (activeIndex >= 0 && items[activeIndex]) {
                        input.value = items[activeIndex].textContent;
                    }
                    commitTag(input.value);
                } else if (e.key === 'Escape') {
                    teardown();
                }
            });

            input.addEventListener('blur', () => {
                // Small delay to allow mousedown on suggestion items to fire first
                setTimeout(teardown, 120);
            });

            cancelBtn.addEventListener('click', teardown);

            // Show suggestions on open
            renderSuggestions();
        });
        userTagsCloud.appendChild(newBtn);

        // Empty state: no chips except the + New button
        if (!tagMap.size) {
            const hint = document.createElement('p');
            hint.className = 'detail-tag-empty';
            hint.textContent = 'No user tags yet. Click + New to add one.';
            hint.style.marginBottom = '0';
            userTagsCloud.insertBefore(hint, newBtn);
        }
    }

    /* ── End User Tags pane ────────────────────────────────────────── */

    function buildSelectedImageTagsPayload(activeImage) {
        const selectedImages = getSelectedImages();
        const images = selectedImages.length
            ? selectedImages
            : (activeImage ? [activeImage] : []);

        if (!images.length) {
            return {
                imageKey: null,
                selectedCount: 0,
                bySource: {
                    civitai: [],
                    danbooru: [],
                    prompt: [],
                    user: [],
                },
                countsBySource: {
                    civitai: {},
                    danbooru: {},
                    prompt: {},
                    user: {},
                },
            };
        }

        const namesBySource = {
            civitai: new Set(),
            danbooru: new Set(),
            prompt: new Set(),
            user: new Set(),
        };
        const countsBySource = {
            civitai: {},
            danbooru: {},
            prompt: {},
            user: {},
        };

        images.forEach((image) => {
            const extracted = extractImageScopeTags(image);
            ['civitai', 'danbooru', 'prompt', 'user'].forEach((source) => {
                const names = Array.isArray(extracted[source]) ? extracted[source] : [];
                names.forEach((name) => {
                    const normalizedName = normalizeTagName(name);
                    if (!normalizedName) {
                        return;
                    }
                    namesBySource[source].add(normalizedName);
                    countsBySource[source][normalizedName] = (countsBySource[source][normalizedName] || 0) + 1;
                });
            });
        });

        return {
            imageKey: activeImage?.__key || images[0]?.__key || null,
            selectedCount: images.length,
            bySource: {
                civitai: Array.from(namesBySource.civitai),
                danbooru: Array.from(namesBySource.danbooru),
                prompt: Array.from(namesBySource.prompt),
                user: Array.from(namesBySource.user),
            },
            countsBySource,
        };
    }

    async function ensureImageTagsHydratedForTree(image) {
        if (!image || typeof image !== 'object') {
            return;
        }

        const hasCivitaiTags = Array.isArray(image.civitai_tags) && image.civitai_tags.length > 0;
        if (hasCivitaiTags) {
            return;
        }

        const imageId = image.base_image_id ?? image.id;
        if (imageId == null || image._tree_tag_loading) {
            return;
        }

        image._tree_tag_loading = true;
        try {
            const response = await fetch(`/api/images/${imageId}`);
            if (!response.ok) {
                return;
            }
            const detail = await response.json();
            image.exif_data = detail?.exif_data ?? image.exif_data ?? null;
            image.civitai_data = detail?.civitai_data ?? image.civitai_data ?? null;
            image.json_metadata = detail?.json_metadata ?? image.json_metadata ?? null;
            image.civitai_tags = Array.isArray(detail?.civitai_tags) ? detail.civitai_tags : [];
        } catch {
            // Best-effort hydration for tree selected-scope tags.
        } finally {
            image._tree_tag_loading = false;
        }
    }

    async function postSelectedImageTagsToTree(image) {
        if (!treeEmbedFrame || !treeEmbedFrame.contentWindow) {
            return;
        }

        const activeImage = image || getSelectedImage();
        const selectionGroup = getDetailSelectionGroup(activeImage);
        if (selectionGroup.length > 0) {
            await Promise.all(selectionGroup.map((item) => ensureImageTagsHydratedForTree(item)));
        } else if (activeImage) {
            await ensureImageTagsHydratedForTree(activeImage);
        }

        const payload = buildSelectedImageTagsPayload(activeImage);

        treeEmbedFrame.contentWindow.postMessage(
            {
                type: 'atelier:selected-image-tags',
                payload,
            },
            window.location.origin,
        );
    }

    /**
     * Send selected image model data to the models browser iframe.
     * Extracts checkpoint/lora resources from the image's generation data.
     */
    function postSelectedImageModelsToModelsFrame(image) {
        if (!modelsEmbedFrame || !modelsEmbedFrame.contentWindow) {
            return;
        }

        const payload = buildSelectedImageModelsPayload(image);

        modelsEmbedFrame.contentWindow.postMessage(
            {
                type: 'atelier:selected-image-models',
                payload,
            },
            window.location.origin,
        );
    }

    /**
     * Send current gallery filter to the models browser iframe so
     * Gallery scope can resolve usage counts server-side.
     */
    function postGalleryKeysToModelsFrame() {
        if (!modelsEmbedFrame || !modelsEmbedFrame.contentWindow) {
            return;
        }

        // Send the filter body so the models iframe can POST it to the backend
        const filterBody = _buildFilterBody();

        modelsEmbedFrame.contentWindow.postMessage(
            {
                type: 'atelier:gallery-filter',
                payload: filterBody,
            },
            window.location.origin,
        );
    }

    /**
     * Build model payload from selected image for the models browser iframe.
     * Extracts checkpoint and lora resources from generation data.
     */
    function buildSelectedImageModelsPayload(image) {
        if (!image) {
            return { imageKey: null, checkpoint: [], lora: [] };
        }

        const checkpoint = [];
        const lora = [];

        // Extract resources from generation data
        const processes = Array.isArray(image.__processes) ? image.__processes : [];
        const genData = image.generation || image.generationData || {};
        const genProcesses = Array.isArray(genData?.processes) ? genData.processes : processes;

        for (const proc of genProcesses) {
            const stages = getGenerationStagesForProcess(proc);
            for (const stage of stages) {
                const resources = Array.isArray(stage?.resources) && stage.resources.length
                    ? stage.resources
                    : (Array.isArray(proc?.resources) ? proc.resources : []);
                for (const res of resources) {
                    if (!res || typeof res !== 'object') continue;
                    const rawType = String(res.resource_type || res.modelType || res.type || '').trim().toLowerCase();
                    const parentType = _parentResourceType(rawType);
                    if (parentType !== 'checkpoint' && parentType !== 'lora') continue;

                    const entry = {
                        type: parentType,
                        name: String(res.display_name || res.modelName || res.name || '').trim(),
                        modelId: res.civitai_model_id || res.modelId || null,
                        versionId: res.civitai_model_version_id || res.versionId || null,
                        versionName: String(res.version_name || res.versionName || '').trim(),
                        baseModel: String(res.base_model_name || res.baseModel || '').trim(),
                    };

                    if (parentType === 'checkpoint') {
                        checkpoint.push(entry);
                    } else {
                        lora.push(entry);
                    }
                }
            }
        }

        return {
            imageKey: image.__key || null,
            checkpoint,
            lora,
        };
    }

    /**
     * Map resource type to parent type (checkpoint/lora).
     * Mirrors backend _RESOURCE_TYPE_PARENTS logic.
     */
    function _parentResourceType(rawType) {
        const TYPE_ALIASES = {
            'model': 'checkpoint',
            'diffusion_model': 'diffusion_model',
            'lora': 'lora',
            'dora': 'dora',
            'embedding': 'embedding',
            'vae': 'vae',
            'controlnet': 'controlnet',
            'clip': 'clip',
            'unet': 'unet',
            'te': 'te',
        };
        const TYPE_PARENTS = {
            'diffusion_model': 'checkpoint',
            'dora': 'lora',
        };
        const normalised = TYPE_ALIASES[rawType] || rawType;
        return TYPE_PARENTS[normalised] || normalised;
    }

    /**
     * Set the tree tag filter state.
     * Supports both single-select (legacy) and multi-select modes.
     * - Single: payload = {source, name} | null
     * - Multi:  payload = [{source, name, mode}, ...] (array with include/exclude modes)
     */
    function setTreeTagFilter(payload, isMultiSelect) {
        if (!payload) {
            state.treeTagFilter = null;
            renderTreeTagFilterIndicator();
            return;
        }

        if (isMultiSelect && Array.isArray(payload)) {
            // Multi-select mode: store array of {source, name, mode}
            const filters = payload
                .map((entry) => {
                    const source = normalizeTagName(entry.source);
                    const name = normalizeTagName(entry.name);
                    const mode = entry.mode === 'exclude' ? 'exclude' : 'include';
                    if (!source || !name) return null;
                    return { source, name, mode };
                })
                .filter(Boolean);
            state.treeTagFilter = filters.length > 0 ? filters : null;
            renderTreeTagFilterIndicator();
            return;
        }

        // Single-select (legacy)
        if (typeof payload !== 'object' || Array.isArray(payload)) {
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

        // Wrap single filter in array format for uniform processing
        state.treeTagFilter = [{ source, name, mode: 'include' }];
        renderTreeTagFilterIndicator();
    }

    function renderTreeTagFilterIndicator() {
        if (!treeTagFilterIndicator || !treeTagFilterText || !treeTagFilterChip) {
            return;
        }

        const hasMissingSource = Object.values(state.missingSourceFilter || {}).some(Boolean);

        if (!state.treeTagFilter && !hasMissingSource) {
            treeTagFilterIndicator.classList.add('hidden');
            treeTagFilterText.textContent = '';
            treeTagFilterChip.className = 'tree-tag-filter-chip';
            return;
        }

        treeTagFilterIndicator.classList.remove('hidden');

        // Show missing-source filters as additional indicator text
        const missingSources = Object.entries(state.missingSourceFilter || {})
            .filter(([, active]) => active)
            .map(([source]) => source);

        if (state.treeTagFilter) {
            const filters = state.treeTagFilter;
            if (filters.length === 1) {
                // Single filter — show as single chip
                const f = filters[0];
                treeTagFilterChip.className = `tree-tag-filter-chip source-${f.source} ${f.mode === 'exclude' ? 'mode-exclude' : 'mode-include'}`;
                const prefix = f.mode === 'exclude' ? '✕ ' : '';
                treeTagFilterText.innerHTML = `<strong>${f.source}</strong>: ${prefix}${f.name}`;
            } else {
                // Multiple filters — show count summary
                const includeCount = filters.filter((f) => f.mode !== 'exclude').length;
                const excludeCount = filters.filter((f) => f.mode === 'exclude').length;
                const parts = [];
                if (includeCount > 0) parts.push(`${includeCount} include`);
                if (excludeCount > 0) parts.push(`${excludeCount} exclude`);
                // Use the source of the first filter for chip color
                treeTagFilterChip.className = 'tree-tag-filter-chip mode-include';
                treeTagFilterText.innerHTML = `<strong>Tree filter</strong>: ${parts.join(', ')}`;
            }
            if (missingSources.length > 0) {
                treeTagFilterText.innerHTML += ` + <strong>missing:</strong> ${missingSources.join(', ')}`;
            }
        } else {
            // Only missing-source filters active
            treeTagFilterChip.className = 'tree-tag-filter-chip mode-exclude';
            treeTagFilterText.innerHTML = `<strong>Missing tags:</strong> ${missingSources.join(', ')}`;
        }
    }

    function imageMatchesTreeTagFilter(image) {
        if (!state.treeTagFilter) {
            return true;
        }

        const bySource = extractImageScopeTags(image);
        const filters = state.treeTagFilter;

        // All filters must be satisfied (AND logic)
        for (const filter of filters) {
            const filterSource = normalizeTagName(filter.source);
            const sourceTags = Array.isArray(bySource[filterSource]) ? bySource[filterSource] : [];
            const hasTag = sourceTags.some((name) => normalizeTagName(name) === filter.name);

            if (filter.mode === 'exclude') {
                // Exclude: image must NOT have this tag
                if (hasTag) return false;
            } else {
                // Include: image MUST have this tag
                if (!hasTag) return false;
            }
        }

        return true;
    }

    function normalizeDetailFilterValue(value) {
        const normalized = String(value || '').trim().toLowerCase();
        return normalized || null;
    }

    /** Categories that store values with "include:" / "exclude:" mode prefixes. */
    const MODE_PREFIX_CATEGORIES = new Set(['tags', 'artistName', 'collections']);

    function _isModePrefixCategory(category) {
        return MODE_PREFIX_CATEGORIES.has(category);
    }

    /** Extract mode and raw name from a potentially mode-prefixed entry. */
    function _parseModePrefixEntry(entry) {
        const colonIdx = entry.indexOf(':');
        if (colonIdx >= 0) {
            return { mode: entry.substring(0, colonIdx), name: entry.substring(colonIdx + 1) };
        }
        return { mode: 'include', name: entry };
    }

    function sanitizeAdvancedFilterValues(values, hasModePrefix) {
        const nextValues = [];
        const seen = new Set();
        (Array.isArray(values) ? values : []).forEach((value) => {
            const text = String(value || '').trim();
            if (hasModePrefix) {
                // Values may be prefixed with "include:" or "exclude:"
                // Deduplicate by the name portion only
                const { name } = _parseModePrefixEntry(text);
                const normalized = normalizeDetailFilterValue(name);
                if (!normalized || seen.has(normalized)) {
                    return;
                }
                seen.add(normalized);
                nextValues.push(text);
            } else {
                const normalized = normalizeDetailFilterValue(text);
                if (!normalized || seen.has(normalized)) {
                    return;
                }
                seen.add(normalized);
                nextValues.push(text);
            }
        });
        return nextValues;
    }

    function getAdvancedFilterValues(category) {
        return sanitizeAdvancedFilterValues(state.advancedFilters?.[category] || [], _isModePrefixCategory(category));
    }

    function getAdvancedFilterValueSet(category) {
        const values = getAdvancedFilterValues(category);
        if (_isModePrefixCategory(category)) {
            // Return Set of normalized names (without mode prefix)
            return new Set(values
                .map((entry) => {
                    const { name } = _parseModePrefixEntry(entry);
                    return normalizeDetailFilterValue(name);
                })
                .filter(Boolean));
        }
        return new Set(values
            .map((value) => normalizeDetailFilterValue(value))
            .filter(Boolean));
    }

    function isAdvancedFilterValueActive(category, value) {
        const normalized = normalizeDetailFilterValue(value);
        return Boolean(normalized && getAdvancedFilterValueSet(category).has(normalized));
    }

    function hasActiveDetailFilters() {
        return Object.values(state.advancedFilters || {}).some((values) => Array.isArray(values) && values.length > 0);
    }

    function serializeDetailFilters() {
        const entries = Object.entries(state.advancedFilters || {})
            .map(([key, values]) => [key, getAdvancedFilterValues(key)])
            .filter(([, values]) => values.length > 0)
            .sort(([leftKey], [rightKey]) => leftKey.localeCompare(rightKey));
        return entries
            .map(([key, values]) => `${key}:${values.map((value) => normalizeDetailFilterValue(value)).sort().join(',')}`)
            .join('|');
    }

    function getImageTagSet(image) {
        const tags = new Set();
        const bySource = extractImageScopeTags(image);
        Object.values(bySource).forEach((names) => {
            if (!Array.isArray(names)) {
                return;
            }
            names.forEach((name) => {
                const normalized = normalizeTagName(name);
                if (normalized) {
                    tags.add(normalized);
                }
            });
        });
        return tags;
    }

    function parsePossibleJsonObject(value) {
        if (!value) {
            return null;
        }
        if (typeof value === 'object') {
            return value;
        }
        if (typeof value !== 'string') {
            return null;
        }

        try {
            const parsed = JSON.parse(value);
            return parsed && typeof parsed === 'object' ? parsed : null;
        } catch {
            return null;
        }
    }

    /**
     * Recursively parse embedded JSON strings within an object.
     * For example, if a value is '{"1": {"class_type": "KSampler"}}' it will
     * be parsed into a real object so JSON.stringify produces indented output.
     */
    function deepParseJsonStrings(obj) {
        if (obj === null || obj === undefined) {
            return obj;
        }
        if (typeof obj === 'string') {
            const parsed = parsePossibleJsonObject(obj);
            return parsed ? deepParseJsonStrings(parsed) : obj;
        }
        if (Array.isArray(obj)) {
            return obj.map(deepParseJsonStrings);
        }
        if (typeof obj === 'object') {
            const result = {};
            for (const key of Object.keys(obj)) {
                result[key] = deepParseJsonStrings(obj[key]);
            }
            return result;
        }
        return obj;
    }

    /**
     * Format an A1111/ComfyUI packed "parameters" string for display.
     *
     * The raw string looks like:
     *   <positive prompt>\nNegative prompt: <neg tags>\nSteps: 20, Sampler: ..., Hashes: {...}, Version: ...
     * Or without a Negative prompt section:
     *   <positive prompt>\nSteps: 9, Sampler: Euler, ..., Civitai resources: [...], Civitai metadata: {...}
     *
     * We split on \n first (which separates the sections), then only
     * split comma-separated Key: Value pairs in the KV metadata section
     * (the part after "Negative prompt:", or the second+ segment if there's
     * no "Negative prompt:").
     * Prompt text sections (positive & negative) are kept as-is.
     * Inline JSON fragments like Hashes: {...} and Civitai resources: [...] are expanded.
     *
     * Output lines have no absolute indent — the display formatter adds that.
     */
    function _formatA1111Parameters(text) {
        if (typeof text !== 'string' || !text) return text;

        // Split on \n literal or actual newlines
        const segments = text.split(/\\n|\n/);

        if (segments.length === 0) return text;

        const formatted = [];
        let foundNegPrompt = false;
        let segmentIndex = 0;

        // Collect positive-prompt segments so we can collapse Civitai-style
        // internal newlines into a single comma-separated line.
        const positiveParts = [];

        for (const segment of segments) {
            const trimmed = segment.trim();
            if (!trimmed) continue;

            // "Negative prompt:" starts the negative prompt section (kept as-is)
            if (/^Negative prompt:/i.test(trimmed)) {
                // Flush accumulated positive parts as a single line
                if (positiveParts.length) {
                    formatted.push(positiveParts.map(p => p.replace(/,\s*$/, '')).join(', '));
                    positiveParts.length = 0;
                }
                foundNegPrompt = true;
                formatted.push(trimmed);
                segmentIndex++;
                continue;
            }

            // KV metadata section: either after "Negative prompt:" OR
            // when the segment looks like "Key: value" parameter pairs.
            // Detect KV by checking if the segment contains A1111-style keys.
            const looksLikeKV = /^[A-Za-z][A-Za-z0-9 _\-]*:\s*\S/.test(trimmed)
                && /\b(Steps|Sampler|CFG|Seed|Size|Clip skip|Denoising|Hashes|Version|Model|Schedule|Lora hashes|TI hashes|EMD)\b/i.test(trimmed);

            if (foundNegPrompt || looksLikeKV) {
                // Flush accumulated positive parts as a single line
                if (positiveParts.length) {
                    formatted.push(positiveParts.map(p => p.replace(/,\s*$/, '')).join(', '));
                    positiveParts.length = 0;
                }
                const parts = _splitKVSegment(trimmed);
                for (const part of parts) {
                    formatted.push(_expandInlineJsonFragments(part));
                }
            } else {
                // Positive prompt segment — accumulate for collapsing
                positiveParts.push(trimmed);
            }
            segmentIndex++;
        }

        // Flush any remaining positive parts
        if (positiveParts.length) {
            formatted.push(positiveParts.map(p => p.replace(/,\s*$/, '')).join(', '));
        }

        return formatted.join('\n');
    }

    /**
     * Expand inline JSON-like fragments (objects {...} and arrays [...]) in a
     * single KV line. Uses brace-depth scanning instead of regex so it handles
     * arbitrarily nested structures.
     *
     * E.g. 'Hashes: {"LORA:foo": "abc123"}' →
     *   'Hashes: {\n  LORA:foo: abc123\n}'
     *
     * E.g. 'Civitai resources: [{"type":"checkpoint"}]' →
     *   'Civitai resources: [\n  {\n    type: checkpoint\n  }\n]'
     *
     * Handles trailing text after the closing brace/bracket:
     *   'Hashes: {"x": "1"} Version: ComfyUI' → expanded + '\nVersion: ComfyUI'
     *
     * Indentation is relative (2-space) — the display formatter adds absolute indent.
     */
    function _expandInlineJsonFragments(text) {
        // Scan for patterns like "Key: {" or "Key: [" and find the matching close
        // using depth tracking, then expand if parseable.
        const result = [];
        let i = 0;

        while (i < text.length) {
            // Look for a key followed by ": {" or ": ["
            const keyMatch = text.slice(i).match(/^([A-Za-z][A-Za-z0-9 _-]*):\s*([{\[])/);
            if (!keyMatch) {
                result.push(text[i]);
                i++;
                continue;
            }

            const key = keyMatch[1];
            const openChar = keyMatch[2];
            const closeChar = openChar === '{' ? '}' : ']';
            const keyEnd = i + keyMatch[0].length - 1; // index of opening brace/bracket
            const jsonStart = keyEnd; // position of { or [

            // Find matching close using depth tracking (respecting quoted strings)
            let depth = 0;
            let inStr = false;
            let escape = false;
            let jsonEnd = -1;

            for (let j = jsonStart; j < text.length; j++) {
                const ch = text[j];
                if (escape) { escape = false; continue; }
                if (ch === '\\' && inStr) { escape = true; continue; }
                if (ch === '"' && !escape) { inStr = !inStr; continue; }
                if (inStr) continue;
                if (ch === '{' || ch === '[') depth++;
                else if (ch === '}' || ch === ']') {
                    depth--;
                    if (depth === 0) { jsonEnd = j; break; }
                }
            }

            if (jsonEnd === -1) {
                // No matching close found — output chars as-is
                result.push(text[i]);
                i++;
                continue;
            }

            const jsonStr = text.slice(jsonStart, jsonEnd + 1);
            const parsed = parsePossibleJsonObject(jsonStr);

            if (parsed === null || parsed === undefined) {
                // Not valid JSON — output chars as-is
                result.push(text[i]);
                i++;
                continue;
            }

            // Expand the parsed JSON
            const expanded = _jsonToIndented(parsed, key + ':', 0);
            result.push(expanded);

            // Check for trailing text after the close
            const rest = text.slice(jsonEnd + 1);
            const trailing = rest.match(/^[,\s]+(\S.*)$/);
            if (trailing) {
                result.push('\n' + trailing[1].trim());
                i = jsonEnd + 1 + trailing[0].length;
            } else {
                i = jsonEnd + 1;
            }
        }

        return result.join('');
    }

    /**
     * Recursively format a parsed JSON value as indented lines.
     * prefix: the "Key:" label for the top level (e.g. "Hashes:")
     * level: nesting depth (0 = top level within the value)
     */
    function _jsonToIndented(val, prefix, level) {
        const pad = '  '.repeat(level);
        const innerPad = '  '.repeat(level + 1);

        if (Array.isArray(val)) {
            if (val.length === 0) return prefix + ' []';
            const items = val.map((item, idx) => {
                const sep = idx < val.length - 1 ? ',' : '';
                if (typeof item === 'object' && item !== null) {
                    return innerPad + _jsonToIndented(item, '', level + 1).trimStart() + sep;
                }
                return innerPad + JSON.stringify(item) + sep;
            });
            return prefix + ' [\n' + items.join('\n') + '\n' + pad + ']';
        }

        if (typeof val === 'object' && val !== null) {
            const entries = Object.entries(val);
            if (entries.length === 0) return prefix + ' {}';
            const items = entries.map(([k, v], idx) => {
                const sep = idx < entries.length - 1 ? ',' : '';
                const displayVal = (typeof v === 'object' && v !== null)
                    ? _jsonToIndented(v, '', level + 1).trimStart()
                    : (typeof v === 'string' ? v : JSON.stringify(v));
                return innerPad + k + ': ' + displayVal + sep;
            });
            return prefix + ' {\n' + items.join('\n') + '\n' + pad + '}';
        }

        return prefix + ' ' + JSON.stringify(val);
    }

    /**
     * Split a comma-separated string of A1111 "Key: Value" pairs into
     * individual lines, respecting nested braces and parentheses.
     * Only called on the KV metadata section (after "Negative prompt:").
     */
    function _splitKVSegment(text) {
        const parts = [];
        let depth = 0;
        let current = '';

        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (ch === '{' || ch === '(' || ch === '[') depth++;
            else if (ch === '}' || ch === ')' || ch === ']') depth--;

            if (ch === ',' && depth === 0) {
                const part = current.trim();
                if (part) parts.push(part);
                current = '';
            } else {
                current += ch;
            }
        }
        const last = current.trim();
        if (last) parts.push(last);
        return parts;
    }

    /**
     * Apply A1111-style formatting to string values within a parsed EXIF object.
     * Only formats values that look like packed parameter strings.
     */
    function formatExifStringValues(obj) {
        if (obj === null || obj === undefined) return obj;
        if (typeof obj === 'string') return _formatA1111Parameters(obj);
        if (Array.isArray(obj)) return obj.map(formatExifStringValues);
        if (typeof obj === 'object') {
            const result = {};
            for (const key of Object.keys(obj)) {
                result[key] = formatExifStringValues(obj[key]);
            }
            return result;
        }
        return obj;
    }

    /**
     * Format a parsed EXIF object for display in a <pre> element.
     * Like JSON.stringify with 2-space indent, but string values that contain
     * A1111-style packed parameters are expanded with real newlines and
     * indentation so the <pre> renders them readably.
     */
    function _formatExifForDisplay(obj, indent = 0) {
        const pad = ' '.repeat(indent);
        const inner = ' '.repeat(indent + 2);

        if (obj === null) return 'null';
        if (obj === undefined) return 'undefined';
        if (typeof obj === 'number' || typeof obj === 'boolean') return String(obj);
        if (typeof obj === 'string') return JSON.stringify(obj);

        if (Array.isArray(obj)) {
            if (obj.length === 0) return '[]';
            const items = obj.map(v => inner + _formatExifForDisplay(v, indent + 2));
            return '[\n' + items.join(',\n') + '\n' + pad + ']';
        }

        if (typeof obj === 'object') {
            const keys = Object.keys(obj);
            if (keys.length === 0) return '{}';

            const entries = keys.map(key => {
                const val = obj[key];
                // Apply A1111 formatting to string values before display
                const displayVal = (typeof val === 'string')
                    ? _formatA1111Parameters(val)
                    : val;

                if (typeof displayVal === 'string' && displayVal.includes('\n')) {
                    // Multi-line string: show with real newlines.
                    // The first line (e.g. A1111 positive prompt) starts on the next line
                    // after the key. Continuation lines get extra indent.
                    const valPad = inner + '  '; // indent for all lines of the value
                    const valLines = displayVal.split('\n');
                    const indentedVal = valLines
                        .map(line => valPad + line)
                        .join('\n');
                    return inner + JSON.stringify(key) + ':\n' + indentedVal;
                }
                return inner + JSON.stringify(key) + ': ' + _formatExifForDisplay(displayVal, indent + 2);
            });
            return '{\n' + entries.join(',\n') + '\n' + pad + '}';
        }

        return String(obj);
    }

    function isNonEmptyGenerationValue(value) {
        if (value == null) {
            return false;
        }
        if (typeof value === 'string') {
            return value.trim().length > 0;
        }
        if (Array.isArray(value)) {
            return value.length > 0;
        }
        if (typeof value === 'object') {
            return Object.keys(value).length > 0;
        }
        return true;
    }

    function stringifyGenerationValue(value) {
        if (value == null) {
            return '';
        }
        if (typeof value === 'string') {
            return value.trim();
        }
        if (typeof value === 'number' || typeof value === 'boolean') {
            return String(value);
        }
        if (Array.isArray(value)) {
            const compact = value
                .map((item) => stringifyGenerationValue(item))
                .filter(Boolean);
            if (!compact.length) {
                return '';
            }
            return compact.join(', ');
        }
        try {
            return JSON.stringify(value, null, 2);
        } catch {
            return String(value);
        }
    }

    function getFirstPromptByRole(prompts, role) {
        const targetRole = String(role || '').trim().toLowerCase();
        if (!Array.isArray(prompts) || !targetRole) {
            return '';
        }

        for (const prompt of prompts) {
            if (!prompt || typeof prompt !== 'object') {
                continue;
            }
            const promptRole = String(prompt.prompt_role || '').trim().toLowerCase();
            if (promptRole !== targetRole) {
                continue;
            }
            const text = String(prompt.prompt_text || '').trim();
            if (text) {
                return text;
            }
        }
        return '';
    }

    function getStageModelDescriptor(stageResources) {
        if (!Array.isArray(stageResources)) {
            return '';
        }

        let fallback = '';
        for (const resource of stageResources) {
            if (!resource || typeof resource !== 'object') {
                continue;
            }
            const type = String(resource.resource_type || resource.modelType || resource.type || '').trim().toLowerCase();
            const name = String(resource.display_name || resource.modelName || resource.name || '').trim();
            if (!name) {
                continue;
            }
            const version = String(resource.version_name || resource.versionName || '').trim();
            const descriptor = version ? `${name} (${version})` : name;
            if (type === 'checkpoint' || type === 'model') {
                return descriptor;
            }
            if (!fallback) {
                fallback = descriptor;
            }
        }

        return fallback;
    }

    function collectStageFieldValues(fieldValues) {
        const collected = {};
        if (!Array.isArray(fieldValues)) {
            return collected;
        }

        fieldValues.forEach((entry) => {
            if (!entry || typeof entry !== 'object') {
                return;
            }
            const key = String(entry.field_key || entry.key || '').trim();
            if (!key) {
                return;
            }
            const rawValue = entry.field_value ?? entry.value;
            if (!isNonEmptyGenerationValue(rawValue)) {
                return;
            }
            const label = `Field: ${key}`;
            if (collected[label]) {
                return;
            }
            collected[label] = rawValue;
        });

        return collected;
    }

    function getGenerationStageName(stage, stageIndex) {
        const explicit = String(stage?.stage_label || stage?.method_variant || '').trim();
        if (explicit) {
            return explicit;
        }
        const role = String(stage?.stage_role || '').trim();
        if (role) {
            const titleRole = role
                .split(/[_\s-]+/)
                .filter(Boolean)
                .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                .join(' ');
            return titleRole;
        }
        return `Step ${stageIndex + 1}`;
    }

    function getGenerationStagesForProcess(process) {
        const stages = Array.isArray(process?.stages) ? process.stages.filter((stage) => stage && typeof stage === 'object') : [];
        if (stages.length) {
            return stages;
        }
        return [
            {
                stage_index: 0,
                stage_role: 'base',
                stage_label: 'Base',
                sampler_name: process?.sampler_name,
                scheduler_name: process?.scheduler_name,
                steps: process?.steps,
                cfg_scale: process?.cfg_scale,
                seed: process?.seed,
                width: process?.width,
                height: process?.height,
                prompts: Array.isArray(process?.prompts) ? process.prompts : [],
                resources: Array.isArray(process?.resources) ? process.resources : [],
                field_values: Array.isArray(process?.field_values) ? process.field_values : [],
                raw_stage_json: process?.raw_payload_json || {},
            },
        ];
    }

    function buildGenerationParameterMap(image, process, stage) {
        const stagePrompts = Array.isArray(stage?.prompts) && stage.prompts.length
            ? stage.prompts
            : (Array.isArray(process?.prompts) ? process.prompts : []);
        const stageResources = Array.isArray(stage?.resources) && stage.resources.length
            ? stage.resources
            : (Array.isArray(process?.resources) ? process.resources : []);
        const stageFieldValues = Array.isArray(stage?.field_values) && stage.field_values.length
            ? stage.field_values
            : (Array.isArray(process?.field_values) ? process.field_values : []);

        const exif = parsePossibleJsonObject(image?.exif_data) || {};
        const civitai = parsePossibleJsonObject(image?.civitai_data) || parsePossibleJsonObject(image?.civitai) || {};
        const civitaiMeta = civitai && typeof civitai.meta === 'object' ? civitai.meta : {};

        const positivePrompt = getFirstPromptByRole(stagePrompts, 'positive')
            || String(exif.Prompt || exif.prompt || exif.parameters || '').trim()
            || String(civitaiMeta.prompt || '').trim();
        const negativePrompt = getFirstPromptByRole(stagePrompts, 'negative')
            || String(exif.NegativePrompt || exif.negative_prompt || '').trim()
            || String(civitaiMeta.negativePrompt || civitaiMeta.negative_prompt || '').trim();

        const model = getStageModelDescriptor(stageResources)
            || String(exif.Model || exif.model || '').trim()
            || String(civitaiMeta.model || '').trim();

        const rawMap = {
            Prompt: positivePrompt,
            'Negative Prompt': negativePrompt,
            Model: model,
            Sampler: stage?.sampler_name ?? exif.Sampler ?? exif.sampler ?? civitaiMeta.sampler,
            Scheduler: stage?.scheduler_name ?? exif.Scheduler ?? exif.scheduler ?? civitaiMeta.scheduler,
            Steps: stage?.steps ?? exif.Steps ?? exif.steps ?? civitaiMeta.steps,
            'CFG Scale': stage?.cfg_scale ?? exif['CFG scale'] ?? exif.cfg_scale ?? civitaiMeta.cfgScale,
            Seed: stage?.seed ?? exif.Seed ?? exif.seed ?? civitaiMeta.seed,
            'Clip Skip': stage?.clip_skip ?? exif['Clip skip'] ?? exif.clip_skip ?? civitaiMeta.clipSkip,
            'Denoise Strength': stage?.denoise_strength ?? exif['Denoising strength'] ?? exif.denoise_strength ?? civitaiMeta.denoise,
            Strength: stage?.strength,
            Width: stage?.width ?? exif.Width ?? exif.width ?? civitaiMeta.width,
            Height: stage?.height ?? exif.Height ?? exif.height ?? civitaiMeta.height,
            'Base Width': stage?.base_width,
            'Base Height': stage?.base_height,
            Platform: process?.platform_name,
            'Method Family': process?.method_family,
            'Method Variant': process?.method_variant,
        };

        const resourceLabels = stageResources
            .map((resource) => String(resource?.display_name || resource?.modelName || resource?.name || '').trim())
            .filter(Boolean);
        if (resourceLabels.length) {
            rawMap.Resources = resourceLabels.join(', ');
        }

        const fieldMap = collectStageFieldValues(stageFieldValues);
        Object.entries(fieldMap).forEach(([key, value]) => {
            rawMap[key] = value;
        });

        const compactMap = {};
        Object.entries(rawMap).forEach(([key, value]) => {
            if (!isNonEmptyGenerationValue(value)) {
                return;
            }
            compactMap[key] = value;
        });

        return {
            display: compactMap,
            raw: {
                process,
                stage,
                prompts: stagePrompts,
                resources: stageResources,
                field_values: stageFieldValues,
                extracted_parameters: compactMap,
            },
        };
    }

    function createGenerationParameterNode(label, value) {
        const row = document.createElement('div');
        row.className = 'generation-param-row';

        const labelNode = document.createElement('span');
        labelNode.className = 'generation-param-label';
        labelNode.textContent = label;
        row.appendChild(labelNode);

        const valueText = stringifyGenerationValue(value);
        if (valueText.includes('\n') || valueText.length > 180) {
            const pre = document.createElement('pre');
            pre.className = 'generation-param-value generation-param-pre';
            pre.textContent = valueText;
            row.appendChild(pre);
            return row;
        }

        const valueNode = document.createElement('span');
        valueNode.className = 'generation-param-value';
        valueNode.textContent = valueText;
        row.appendChild(valueNode);
        return row;
    }

    function getGenerationProcessLabel(process, processIndex) {
        const labelParts = [];
        const method = String(process?.method_family || '').trim();
        const platform = String(process?.platform_name || '').trim();
        if (method) {
            labelParts.push(method);
        }
        if (platform && platform.toLowerCase() !== method.toLowerCase()) {
            labelParts.push(platform);
        }
        const title = labelParts.length ? labelParts.join(' - ') : 'Process';
        return `${title} ${processIndex + 1}`;
    }

    function buildFallbackGenerationProcessesFromImage(image) {
        const exif = parsePossibleJsonObject(image?.exif_data) || {};
        const civitai = parsePossibleJsonObject(image?.civitai_data) || parsePossibleJsonObject(image?.civitai) || {};
        const civitaiMeta = civitai && typeof civitai.meta === 'object' ? civitai.meta : {};
        const civitaiResources = Array.isArray(civitai?.resources)
            ? civitai.resources.filter((item) => item && typeof item === 'object')
            : [];

        const promptText = String(
            exif.Prompt || exif.prompt || civitaiMeta.prompt || ''
        ).trim();
        const negativePromptText = String(
            exif.NegativePrompt || exif.negative_prompt || civitaiMeta.negativePrompt || civitaiMeta.negative_prompt || ''
        ).trim();

        const prompts = [];
        if (promptText) {
            prompts.push({
                prompt_role: 'positive',
                prompt_text: promptText,
                source_type: 'metadata_fallback',
                raw_prompt_json: { source: 'metadata_fallback.prompt' },
            });
        }
        if (negativePromptText) {
            prompts.push({
                prompt_role: 'negative',
                prompt_text: negativePromptText,
                source_type: 'metadata_fallback',
                raw_prompt_json: { source: 'metadata_fallback.negative_prompt' },
            });
        }

        const resources = civitaiResources.map((resource) => ({
            resource_type: String(resource.modelType || resource.type || 'other').toLowerCase(),
            display_name: String(resource.modelName || resource.name || '').trim() || null,
            version_name: String(resource.versionName || '').trim() || null,
            raw_resource_json: resource,
        }));

        const hasGenerationSignals = Boolean(
            prompts.length
            || resources.length
            || isNonEmptyGenerationValue(exif.Sampler)
            || isNonEmptyGenerationValue(exif.Steps)
            || isNonEmptyGenerationValue(exif.Seed)
            || isNonEmptyGenerationValue(exif['CFG scale'])
            || isNonEmptyGenerationValue(civitaiMeta.sampler)
            || isNonEmptyGenerationValue(civitaiMeta.steps)
            || isNonEmptyGenerationValue(civitaiMeta.seed)
            || isNonEmptyGenerationValue(civitaiMeta.cfgScale)
        );

        if (!hasGenerationSignals) {
            return [];
        }

        const methodFamily = String(
            civitai?.process || civitaiMeta.process || image?.generation_software || 'metadata_fallback'
        ).trim();
        const platformName = String(
            civitai?.engine || civitaiMeta.engine || image?.generation_software || 'metadata_fallback'
        ).trim();

        return [
            {
                source_type: 'metadata_fallback',
                method_family: methodFamily || 'metadata_fallback',
                platform_name: platformName || 'metadata_fallback',
                stage_count: 1,
                prompts,
                resources,
                stages: [
                    {
                        stage_index: 0,
                        stage_role: 'base',
                        stage_label: 'Base',
                        sampler_name: exif.Sampler ?? exif.sampler ?? civitaiMeta.sampler,
                        scheduler_name: exif.Scheduler ?? exif.scheduler ?? civitaiMeta.scheduler,
                        steps: exif.Steps ?? exif.steps ?? civitaiMeta.steps,
                        cfg_scale: exif['CFG scale'] ?? exif.cfg_scale ?? civitaiMeta.cfgScale,
                        seed: exif.Seed ?? exif.seed ?? civitaiMeta.seed,
                        clip_skip: exif['Clip skip'] ?? exif.clip_skip ?? civitaiMeta.clipSkip,
                        denoise_strength: exif['Denoising strength'] ?? exif.denoise_strength ?? civitaiMeta.denoise,
                        width: exif.Width ?? exif.width ?? civitaiMeta.width ?? image?.width,
                        height: exif.Height ?? exif.height ?? civitaiMeta.height ?? image?.height,
                        prompts,
                        resources,
                        field_values: [],
                        raw_stage_json: {
                            exif,
                            civitai_meta: civitaiMeta,
                        },
                    },
                ],
            },
        ];
    }

    function renderGenerationProcessFolders(container, image, processes) {
        if (!Array.isArray(processes) || !processes.length) {
            const empty = document.createElement('p');
            empty.className = 'detail-placeholder';
            empty.textContent = 'No extracted generation process data is available for this image yet.';
            container.appendChild(empty);
            return;
        }

        processes.forEach((process, processIndex) => {
            const processFolder = document.createElement('details');
            processFolder.className = 'generation-process-folder';
            if (processIndex === 0) {
                processFolder.open = true;
            }

            const processSummary = document.createElement('summary');
            processSummary.textContent = getGenerationProcessLabel(process, processIndex);
            processFolder.appendChild(processSummary);

            const processBody = document.createElement('div');
            processBody.className = 'generation-process-body';

            const stages = getGenerationStagesForProcess(process);
            stages.forEach((stage, stageIndex) => {
                const stepFolder = document.createElement('details');
                stepFolder.className = 'generation-step-folder';
                if (processIndex === 0 && stageIndex === 0) {
                    stepFolder.open = true;
                }

                const stepSummary = document.createElement('summary');
                stepSummary.textContent = getGenerationStageName(stage, stageIndex);
                stepFolder.appendChild(stepSummary);

                const stepBody = document.createElement('div');
                stepBody.className = 'generation-step-body';

                const { display, raw } = buildGenerationParameterMap(image, process, stage);
                const entries = Object.entries(display);
                if (!entries.length) {
                    const placeholder = document.createElement('p');
                    placeholder.className = 'detail-placeholder';
                    placeholder.textContent = 'No generation parameters were extracted for this step.';
                    stepBody.appendChild(placeholder);
                } else {
                    const grid = document.createElement('div');
                    grid.className = 'generation-param-grid';
                    entries.forEach(([label, value]) => {
                        grid.appendChild(createGenerationParameterNode(label, value));
                    });
                    stepBody.appendChild(grid);
                }

                const rawTitle = document.createElement('h4');
                rawTitle.className = 'generation-raw-title';
                rawTitle.textContent = 'All Extracted Parameters';
                stepBody.appendChild(rawTitle);

                const rawPre = document.createElement('pre');
                rawPre.className = 'json-block generation-raw-json';
                rawPre.textContent = JSON.stringify(raw, null, 2);
                stepBody.appendChild(rawPre);

                stepFolder.appendChild(stepBody);
                processBody.appendChild(stepFolder);
            });

            processFolder.appendChild(processBody);
            container.appendChild(processFolder);
        });
    }

    async function fetchGenerationPrototypeForImage(image, { forceRefresh = false } = {}) {
        const fileHash = String(getEditableFileHash(image) || '').trim();
        if (!fileHash) {
            return null;
        }

        if (forceRefresh) {
            state.generationPrototypeCache.delete(fileHash);
        }

        if (!forceRefresh && state.generationPrototypeCache.has(fileHash)) {
            return state.generationPrototypeCache.get(fileHash);
        }

        if (state.generationPrototypeInflight.has(fileHash)) {
            return state.generationPrototypeInflight.get(fileHash);
        }

        const request = (async () => {
            const response = await fetch(`/api/images/${encodeURIComponent(fileHash)}/generation-prototype`);
            let payload = null;
            try {
                payload = await response.json();
            } catch {
                payload = null;
            }

            if (!response.ok) {
                const detail = payload && typeof payload === 'object' && payload.detail
                    ? String(payload.detail)
                    : `Request failed with HTTP ${response.status}`;
                throw new Error(detail);
            }

            const safePayload = payload && typeof payload === 'object' ? payload : {};
            state.generationPrototypeCache.set(fileHash, safePayload);
            return safePayload;
        })();

        state.generationPrototypeInflight.set(fileHash, request);
        try {
            return await request;
        } finally {
            state.generationPrototypeInflight.delete(fileHash);
        }
    }

    async function renderDetailGenerationPanel(image) {
        if (!(detailGeneration instanceof HTMLElement)) {
            return;
        }

        detailGeneration.innerHTML = '';
        if (!image || typeof image !== 'object') {
            return;
        }

        const fileHash = String(getEditableFileHash(image) || '').trim();
        if (!fileHash) {
            const empty = document.createElement('p');
            empty.className = 'detail-placeholder';
            empty.textContent = 'Generation data requires a persisted file hash.';
            detailGeneration.appendChild(empty);
            return;
        }

        const token = state.detailGenerationRenderToken + 1;
        state.detailGenerationRenderToken = token;

        const loading = document.createElement('p');
        loading.className = 'detail-placeholder';
        loading.textContent = 'Loading extracted generation data...';
        detailGeneration.appendChild(loading);

        let payload = null;
        try {
            payload = await fetchGenerationPrototypeForImage(image);
        } catch (error) {
            if (state.detailGenerationRenderToken !== token) {
                return;
            }
            payload = null;
        }

        if (state.detailGenerationRenderToken !== token) {
            return;
        }

        const selectedImage = getSelectedImage();
        const selectedHash = String(getEditableFileHash(selectedImage) || '').trim();
        if (selectedHash && selectedHash !== fileHash) {
            return;
        }

        const normalized = payload && typeof payload === 'object' ? (payload.normalized || {}) : {};
        const processCandidates = [
            normalized.processes,
            image.generation_processes,
            image.generationProcesses,
            image?.generation?.processes,
        ];
        const processes = processCandidates.find((candidate) => Array.isArray(candidate) && candidate.length) || [];
        const fallbackProcesses = buildFallbackGenerationProcessesFromImage(image);
        const resolvedProcesses = processes.length ? processes : fallbackProcesses;

        detailGeneration.innerHTML = '';
        renderGenerationProcessFolders(detailGeneration, image, resolvedProcesses);

        if (!processes.length && payload === null) {
            const note = document.createElement('p');
            note.className = 'detail-placeholder';
            note.textContent = 'Showing metadata fallback because generation prototype data could not be loaded.';
            detailGeneration.prepend(note);
        }
    }

    function dedupeDisplayValues(values) {
        const seen = new Set();
        const deduped = [];
        values.forEach((value) => {
            const label = String(value || '').trim();
            if (!label) {
                return;
            }
            const key = label.toLowerCase();
            if (seen.has(key)) {
                return;
            }
            seen.add(key);
            deduped.push(label);
        });
        return deduped;
    }

    function normalizeNsfwRatingLabels(rawValue) {
        if (rawValue == null) {
            return [];
        }

        if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
            if (rawValue <= 1) {
                return ['Safe'];
            }
            if (rawValue === 2) {
                return ['Mature'];
            }
            if (rawValue >= 3) {
                return ['Explicit'];
            }
            return [];
        }

        const normalized = String(rawValue || '').trim().toLowerCase();
        if (!normalized) {
            return [];
        }

        if (/^\d+$/.test(normalized)) {
            return normalizeNsfwRatingLabels(Number(normalized));
        }

        if (normalized === 'pg-13' || normalized === 'pg13') {
            return ['PG13', 'Safe'];
        }
        if (normalized === 'pg') {
            return ['PG', 'Safe'];
        }
        if (normalized === 'xxx') {
            return ['XXX', 'Explicit'];
        }
        if (normalized === 'x') {
            return ['X', 'Explicit'];
        }
        if (normalized === 'r') {
            return ['R', 'Mature'];
        }
        if (normalized === 'r-18' || normalized === 'r18+') {
            return ['X', 'Explicit'];
        }

        if (normalized === 'pg_13') {
            return ['PG13', 'Safe'];
        }

        if (normalized.includes('safe') || normalized === 'none' || normalized === 'sfw') {
            return ['Safe'];
        }
        if (normalized.includes('mature') || normalized === 'moderate' || normalized === 'r15') {
            return ['Mature'];
        }
        if (
            normalized.includes('explicit')
            || normalized.includes('adult')
            || normalized.includes('nsfw')
            || normalized === 'r18'
        ) {
            return ['Explicit'];
        }

        return [];
    }

    function hasGranularNsfwLabel(labels) {
        return labels.some((label) => ['PG', 'PG13', 'R', 'X', 'XXX'].includes(String(label || '').toUpperCase()));
    }

    function getImageNsfwRatings(image, { includeUserOverrides = true } = {}) {
        if (!image || typeof image !== 'object') {
            return [];
        }

        const civitaiPayload = parsePossibleJsonObject(image.civitai_data) || parsePossibleJsonObject(image.civitai) || {};
        const candidates = [
            ...(includeUserOverrides ? [image.user_nsfw_rating, image.user_nsfw_safety_class] : []),
            image.nsfw_ratings,
            image.nsfw_rating,
            image.nsfw_level,
            image.nsfw,
            image.rating,
            image.content_rating,
            civitaiPayload.nsfwLevel,
            civitaiPayload.nsfw,
            civitaiPayload.rating,
            civitaiPayload.image?.nsfwLevel,
            civitaiPayload.image?.nsfw,
            civitaiPayload.image?.rating,
            civitaiPayload.image?.nsfwRating,
            civitaiPayload.meta?.nsfwLevel,
            civitaiPayload.meta?.nsfw,
            civitaiPayload.meta?.rating,
            civitaiPayload.meta?.nsfwRating,
        ];

        let fallbackLabels = [];
        for (const candidate of candidates) {
            const labels = normalizeNsfwRatingLabels(candidate);
            if (!labels.length) {
                continue;
            }

            if (hasGranularNsfwLabel(labels)) {
                return dedupeDisplayValues(labels);
            }
            if (!fallbackLabels.length) {
                fallbackLabels = labels;
            }
        }

        return dedupeDisplayValues(fallbackLabels);
    }

    function normalizeUserNsfwRatingValue(value) {
        const normalized = String(value || '').trim().toLowerCase();
        return ['pg', 'pg13', 'r', 'x', 'xxx'].includes(normalized) ? normalized : '';
    }

    function normalizeUserNsfwSafetyClassValue(value) {
        const normalized = String(value || '').trim().toLowerCase();
        return ['safe', 'mature', 'explicit'].includes(normalized) ? normalized : '';
    }

    function getNsfwDisplayTokens(image, { includeUserOverrides = true } = {}) {
        const labels = getImageNsfwRatings(image, { includeUserOverrides });
        const granular = labels.find((label) => ['PG', 'PG13', 'R', 'X', 'XXX'].includes(String(label || '').toUpperCase())) || null;
        const safety = labels.find((label) => ['SAFE', 'MATURE', 'EXPLICIT'].includes(String(label || '').toUpperCase())) || null;
        return {
            granular,
            safety,
        };
    }

    function formatNsfwDisplay(image) {
        const effective = getNsfwDisplayTokens(image, { includeUserOverrides: true });
        const pieces = [effective.granular, effective.safety].filter(Boolean);
        if (pieces.length) {
            return pieces.join(' / ');
        }
        return 'Unknown';
    }

    function renderEditableNsfwRatingItem(images) {
        const targets = Array.isArray(images) ? images : [];
        const initialUserRatings = targets.map((item) => normalizeUserNsfwRatingValue(item.user_nsfw_rating));
        const initialUserSafety = targets.map((item) => normalizeUserNsfwSafetyClassValue(item.user_nsfw_safety_class));
        let currentUserRating = new Set(initialUserRatings).size === 1 ? initialUserRatings[0] : '';
        let currentUserSafety = new Set(initialUserSafety).size === 1 ? initialUserSafety[0] : '';

        const wrapper = document.createElement('div');
        wrapper.className = 'meta-item';

        const head = document.createElement('div');
        head.className = 'meta-item-head';

        const labelNode = document.createElement('span');
        labelNode.className = 'label';
        labelNode.textContent = 'Rating';

        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'edit-icon-btn';
        editBtn.title = 'Edit';
        editBtn.setAttribute('aria-label', 'Edit Rating');
        editBtn.textContent = '✎';

        head.appendChild(labelNode);
        head.appendChild(editBtn);

        const valueNode = document.createElement('span');
        valueNode.className = 'value';

        function getGroupNsfwDisplay(includeUserOverrides) {
            const ratingValues = sortNsfwRatingValues(
                getDistinctGroupValues(
                    targets,
                    (entry) => getNsfwDisplayTokens(entry, { includeUserOverrides }).granular,
                ),
            );
            const safetyValues = sortNsfwSafetyValues(
                getDistinctGroupValues(
                    targets,
                    (entry) => getNsfwDisplayTokens(entry, { includeUserOverrides }).safety,
                ),
            );

            const parts = [];
            if (ratingValues.length) {
                parts.push(`Rating: ${ratingValues.join(' | ')}`);
            }
            if (safetyValues.length) {
                parts.push(`Safety: ${safetyValues.join(' | ')}`);
            }
            return parts.length ? parts.join('; ') : 'Unknown';
        }

        function renderDisplayValue() {
            const effectiveText = getGroupNsfwDisplay(true);
            const metadataText = getGroupNsfwDisplay(false);
            const overrideCount = targets.reduce((count, entry) => {
                const hasOverride = Boolean(
                    normalizeUserNsfwRatingValue(entry.user_nsfw_rating)
                    || normalizeUserNsfwSafetyClassValue(entry.user_nsfw_safety_class),
                );
                return count + (hasOverride ? 1 : 0);
            }, 0);
            const overrideText = overrideCount <= 0
                ? 'metadata'
                : overrideCount === targets.length
                    ? 'user override'
                    : `${overrideCount}/${targets.length} user override`;
            valueNode.textContent = `${effectiveText} (${overrideText})`;
            valueNode.title = `Metadata: ${metadataText}`;
        }

        renderDisplayValue();

        const editRow = document.createElement('div');
        editRow.className = 'meta-edit-row hidden';

        const controlGrid = document.createElement('div');
        controlGrid.className = 'meta-edit-grid';

        const ratingSelect = document.createElement('select');
        ratingSelect.className = 'meta-edit-input';
        [
            ['', 'Use metadata rating'],
            ['pg', 'PG'],
            ['pg13', 'PG13'],
            ['r', 'R'],
            ['x', 'X'],
            ['xxx', 'XXX'],
        ].forEach(([value, label]) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            ratingSelect.appendChild(option);
        });

        const safetySelect = document.createElement('select');
        safetySelect.className = 'meta-edit-input';
        [
            ['', 'Use metadata safety'],
            ['safe', 'Safe'],
            ['mature', 'Mature'],
            ['explicit', 'Explicit'],
        ].forEach(([value, label]) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            safetySelect.appendChild(option);
        });

        controlGrid.appendChild(ratingSelect);
        controlGrid.appendChild(safetySelect);

        const actions = document.createElement('div');
        actions.className = 'meta-edit-actions';

        const saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn ghost btn-sm';
        saveBtn.textContent = 'Save';

        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.className = 'btn ghost btn-sm';
        clearBtn.textContent = 'Clear';

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn ghost btn-sm';
        cancelBtn.textContent = 'Cancel';

        actions.appendChild(saveBtn);
        actions.appendChild(clearBtn);
        actions.appendChild(cancelBtn);

        editRow.appendChild(controlGrid);
        editRow.appendChild(actions);

        function syncInputsFromCurrent() {
            ratingSelect.value = currentUserRating;
            safetySelect.value = currentUserSafety;
        }

        function setEditing(editing) {
            editRow.classList.toggle('hidden', !editing);
            valueNode.classList.toggle('hidden', editing);
            if (editing) {
                syncInputsFromCurrent();
                ratingSelect.focus();
            }
        }

        async function saveCurrentValues(nextRatingValue, nextSafetyValue) {
            saveBtn.disabled = true;
            clearBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            try {
                await saveImageMetadataForGroup(targets, {
                    user_nsfw_rating: nextRatingValue || '',
                    user_nsfw_safety_class: nextSafetyValue || '',
                }, (target, result) => {
                    target.user_nsfw_rating = result.user_nsfw_rating || null;
                    target.user_nsfw_safety_class = result.user_nsfw_safety_class || null;
                }, 'Rating');

                const refreshedRatings = targets.map((item) => normalizeUserNsfwRatingValue(item.user_nsfw_rating));
                const refreshedSafety = targets.map((item) => normalizeUserNsfwSafetyClassValue(item.user_nsfw_safety_class));
                currentUserRating = new Set(refreshedRatings).size === 1 ? refreshedRatings[0] : '';
                currentUserSafety = new Set(refreshedSafety).size === 1 ? refreshedSafety[0] : '';
                renderDisplayValue();
                const activeImage = getSelectedImage();
                if (activeImage) {
                    renderDetailSubtitle(activeImage);
                }
                setEditing(false);
            } catch (error) {
                alert(`Could not save Rating: ${error.message}`);
            } finally {
                saveBtn.disabled = false;
                clearBtn.disabled = false;
                saveBtn.textContent = 'Save';
            }
        }

        editBtn.addEventListener('click', () => setEditing(true));
        cancelBtn.addEventListener('click', () => {
            syncInputsFromCurrent();
            setEditing(false);
        });
        saveBtn.addEventListener('click', () => {
            const nextRating = normalizeUserNsfwRatingValue(ratingSelect.value);
            const nextSafety = normalizeUserNsfwSafetyClassValue(safetySelect.value);
            void saveCurrentValues(nextRating, nextSafety);
        });
        clearBtn.addEventListener('click', () => {
            void saveCurrentValues('', '');
        });

        wrapper.appendChild(head);
        wrapper.appendChild(valueNode);
        wrapper.appendChild(editRow);
        return wrapper;
    }

    function sortOrderedFilterValues(values, orderedValues) {
        const indexByValue = new Map(orderedValues.map((value, index) => [value.toLowerCase(), index]));
        const uniqueValues = dedupeDisplayValues(values);
        uniqueValues.sort((left, right) => {
            const leftIndex = indexByValue.get(String(left || '').toLowerCase());
            const rightIndex = indexByValue.get(String(right || '').toLowerCase());
            if (leftIndex != null && rightIndex != null) {
                return leftIndex - rightIndex;
            }
            if (leftIndex != null) {
                return -1;
            }
            if (rightIndex != null) {
                return 1;
            }
            return String(left).localeCompare(String(right), undefined, { sensitivity: 'base' });
        });
        return uniqueValues;
    }

    function sortNsfwRatingValues(values) {
        return sortOrderedFilterValues(values, NSFW_RATING_PILL_ORDER);
    }

    function sortNsfwSafetyValues(values) {
        return sortOrderedFilterValues(values, NSFW_SAFETY_PILL_ORDER);
    }

    function isNsfwLabel(label) {
        const normalized = String(label || '').trim().toLowerCase();
        return ['r', 'x', 'xxx', 'mature', 'explicit'].includes(normalized);
    }

    function getNsfwVisibilityServerRatings(mode) {
        if (mode === 'safe') {
            return ['safe', 'pg', 'pg13'];
        }
        if (mode === 'mature') {
            return ['safe', 'mature', 'pg', 'pg13', 'r'];
        }
        return [];
    }

    function isNsfwValueAllowedByVisibility(value, mode) {
        if (mode === 'explicit') {
            return true;
        }
        const normalized = normalizeDetailFilterValue(value);
        if (!normalized) {
            return false;
        }
        if (normalized === 'n/a') {
            return true;
        }
        return getNsfwVisibilityServerRatings(mode).includes(normalized);
    }

    function formatNsfwVisibilityLabel(mode) {
        if (mode === 'safe') {
            return 'Safe';
        }
        if (mode === 'mature') {
            return 'Mature';
        }
        return 'Explicit';
    }

    function syncNsfwVisibilityUi() {
        if (!nsfwVisibilityCurrent) {
            return;
        }
        nsfwVisibilityCurrent.textContent = formatNsfwVisibilityLabel(state.nsfwVisibility);
        nsfwVisibilityOptionButtons.forEach((button) => {
            const level = String(button.dataset.nsfwLevel || '').toLowerCase();
            const isActive = level === state.nsfwVisibility;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-checked', isActive ? 'true' : 'false');
        });
    }

    function setNsfwVisibilityMode(nextMode) {
        const normalizedMode = String(nextMode || '').toLowerCase();
        if (!['safe', 'mature', 'explicit'].includes(normalizedMode)) {
            return;
        }
        state.nsfwVisibility = normalizedMode;
        writeCookieValue(COOKIE_KEYS.nsfwVisibility, normalizedMode);
        writeCookieValue(COOKIE_KEYS.showNsfw, normalizedMode === 'explicit' ? 'true' : 'false');
        syncNsfwVisibilityUi();
        // NSFW visibility is a base catalog preference, not a display filter.
        // Reload the entire gallery so the server constrains the image set.
        void resetAndLoadImages({ preserveSelection: true });
    }

    function cycleNsfwVisibilityMode() {
        const sequence = ['safe', 'mature', 'explicit'];
        const currentIndex = sequence.indexOf(String(state.nsfwVisibility || '').toLowerCase());
        const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % sequence.length : 2;
        setNsfwVisibilityMode(sequence[nextIndex]);
    }

    function imageMatchesAnyMultiValueFilter(imageValues, category) {
        const selections = getAdvancedFilterValueSet(category);
        if (!selections.size) {
            return true;
        }
        if (!Array.isArray(imageValues) || !imageValues.length) {
            return false;
        }

        const normalizedValues = new Set(
            imageValues
                .map((value) => normalizeDetailFilterValue(value))
                .filter(Boolean)
        );
        return Array.from(selections).some((value) => normalizedValues.has(value));
    }

    const _NSFW_GRANULAR_RATINGS = new Set(['pg', 'pg13', 'r', 'x', 'xxx']);
    const _NSFW_SAFETY_CLASSES = new Set(['safe', 'mature', 'explicit']);
    const _A1111_RP_DIRECTIVE_RE = /\b(ADDCOMM|ADDROW|ADDCOL)\b/i;

    function imageMatchesNsfwCategoryFilter(image, category, allowedValues) {
        const selections = getAdvancedFilterValueSet(category);
        if (!selections.size) {
            return true;
        }
        const tokens = getNsfwDisplayTokens(image, { includeUserOverrides: true });
        const imageValue = category === 'nsfwRating' ? tokens.granular : tokens.safety;
        const normalizedValue = normalizeDetailFilterValue(imageValue);
        for (const selection of selections) {
            if (selection === 'n/a') {
                if (!normalizedValue || !allowedValues.has(normalizedValue)) {
                    return true;
                }
            } else if (normalizedValue === selection) {
                return true;
            }
        }
        return false;
    }

    function imageMatchesNsfwRatingFilter(image) {
        return imageMatchesNsfwCategoryFilter(image, 'nsfwRating', _NSFW_GRANULAR_RATINGS);
    }

    function imageMatchesNsfwSafetyFilter(image) {
        return imageMatchesNsfwCategoryFilter(image, 'nsfwSafety', _NSFW_SAFETY_CLASSES);
    }

    function imageMatchesAnyFilter(imageValue, category) {
        const selections = getAdvancedFilterValueSet(category);
        if (!selections.size) {
            return true;
        }
        const normalizedValue = normalizeDetailFilterValue(imageValue);
        return Boolean(normalizedValue && selections.has(normalizedValue));
    }

    function imageMatchesAnyCollectionFilter(image, category) {
        const selections = getAdvancedFilterValueSet(category);
        if (!selections.size) {
            return true;
        }
        const normalizedCollections = new Set(
            (Array.isArray(image?.collection_names) ? image.collection_names : [])
                .map((name) => normalizeDetailFilterValue(name))
                .filter(Boolean)
        );
        return Array.from(selections).some((value) => normalizedCollections.has(value));
    }

    /** Client-side filter for a scalar field in a mode-prefix category. */
    function _imageMatchesModePrefixFilter(imageValue, category) {
        const entries = getAdvancedFilterValues(category);
        if (!entries.length) return true;
        const normalizedValue = normalizeDetailFilterValue(imageValue);
        for (const entry of entries) {
            const { mode, name } = _parseModePrefixEntry(entry);
            const normalizedName = normalizeDetailFilterValue(name);
            if (!normalizedName) continue;
            const matches = normalizedValue === normalizedName;
            if (mode === 'exclude' && matches) return false;
            if (mode !== 'exclude' && !matches) return false;
        }
        return true;
    }

    /** Client-side collection filter with mode-prefix (include/exclude). */
    function _imageMatchesCollectionModePrefixFilter(image, category) {
        const entries = getAdvancedFilterValues(category);
        if (!entries.length) return true;
        const normalizedCollections = new Set(
            (Array.isArray(image?.collection_names) ? image.collection_names : [])
                .map((name) => normalizeDetailFilterValue(name))
                .filter(Boolean)
        );
        for (const entry of entries) {
            const { mode, name } = _parseModePrefixEntry(entry);
            const normalizedName = normalizeDetailFilterValue(name);
            if (!normalizedName) continue;
            const inCollection = normalizedCollections.has(normalizedName);
            if (mode === 'exclude' && inCollection) return false;
            if (mode !== 'exclude' && !inCollection) return false;
        }
        return true;
    }

    function isMissingDataConditionMet(image, condition) {
        // Accepts both "no artist" (legacy) and "artist" (new label without "No" prefix).
        const raw = normalizeDetailFilterValue(condition);
        const key = raw && !raw.startsWith('no ') ? `no ${raw}` : raw;
        const exif = parsePossibleJsonObject(image?.exif_data) || {};

        const hasExifValue = (...candidates) => candidates.some((candidate) => {
            const value = exif?.[candidate];
            if (value == null) {
                return false;
            }
            if (typeof value === 'string') {
                return value.trim().length > 0;
            }
            if (Array.isArray(value)) {
                return value.length > 0;
            }
            if (typeof value === 'object') {
                return Object.keys(value).length > 0;
            }
            return true;
        });

        const looksLikeA1111UserCommentPayload = () => {
            const exifParameters = [exif?.parameters, exif?.Parameters]
                .find((value) => typeof value === 'string' && value.trim().length > 0);
            if (typeof exifParameters === 'string') {
                const normalizedParameters = exifParameters.trim().toLowerCase();
                const hasSteps = normalizedParameters.includes('steps:');
                const hasSeed = normalizedParameters.includes('seed:');
                const hasSampler = normalizedParameters.includes('sampler:');
                const hasCfg = normalizedParameters.includes('cfg scale:');
                const hasNegativePrompt = normalizedParameters.includes('negative prompt:');
                if (hasSteps && (hasCfg || hasSampler || hasSeed || hasNegativePrompt)) {
                    return true;
                }
            }

            const exactUserComment = exif?.user_comment;
            if (typeof exactUserComment === 'string' && exactUserComment.trim().length > 0) {
                return true;
            }

            const legacyUserComment = exif?.UserComment;
            if (typeof legacyUserComment !== 'string') {
                return false;
            }

            const text = legacyUserComment.trim();
            if (!text) {
                return false;
            }

            // Some exporters place JSON payloads (often Comfy/CivitAI-specific) in UserComment.
            // Treat only classic A1111 parameter strings as A1111 metadata.
            if (text.startsWith('{') || text.startsWith('[')) {
                try {
                    const parsed = JSON.parse(text);
                    if (parsed && typeof parsed === 'object') {
                        const promptLike = parsed.prompt ?? parsed.workflow ?? parsed['resource-stack'];
                        if (promptLike != null) {
                            return false;
                        }
                    }
                } catch {
                    // If parsing fails, continue with textual heuristic below.
                }
            }

            const normalized = text.toLowerCase();
            if (normalized.includes('civitai resources:')) {
                return false;
            }
            const hasSteps = normalized.includes('steps:');
            const hasSeed = normalized.includes('seed:');
            const hasSampler = normalized.includes('sampler:');
            const hasCfg = normalized.includes('cfg scale:');
            const hasNegativePrompt = normalized.includes('negative prompt:');
            return hasSteps && (hasCfg || hasSampler || hasSeed || hasNegativePrompt);
        };

        const getA1111ParameterText = () => {
            const candidateValues = [
                exif?.parameters,
                exif?.Parameters,
                exif?.user_comment,
                exif?.UserComment,
            ];

            for (const value of candidateValues) {
                if (typeof value !== 'string') {
                    continue;
                }
                const text = value.trim();
                if (!text) {
                    continue;
                }
                if (text.startsWith('{') || text.startsWith('[')) {
                    continue;
                }
                return text.toLowerCase();
            }
            return '';
        };

        const getA1111FeatureFlags = () => {
            if (!looksLikeA1111UserCommentPayload()) {
                return {
                    hiresUpscale: false,
                    regionalPrompter: false,
                    adetailer: false,
                };
            }

            const text = getA1111ParameterText();
            if (!text) {
                return {
                    hiresUpscale: false,
                    regionalPrompter: false,
                    adetailer: false,
                };
            }

            const hiresUpscale = /\b(hires\s+upscaler|hires\s+steps|hires\s+upscale|hr\s+upscaler|hr\s+upscale|denoising\s+strength)\b/.test(text);
            const regionalPrompter = /\b(rp\s+active|regional\s+prompt)\b/.test(text)
                || _A1111_RP_DIRECTIVE_RE.test(String(exif?.user_comment || exif?.UserComment || ''));
            const adetailer = /\badetailer\b/.test(text);

            return {
                hiresUpscale,
                regionalPrompter,
                adetailer,
            };
        };

        const a1111Features = getA1111FeatureFlags();

        if (key === 'no nsfw rating') {
            const { granular } = getNsfwDisplayTokens(image, { includeUserOverrides: true });
            return !granular || !_NSFW_GRANULAR_RATINGS.has(normalizeDetailFilterValue(granular));
        }
        if (key === 'no safety class') {
            const { safety } = getNsfwDisplayTokens(image, { includeUserOverrides: true });
            return !safety || !_NSFW_SAFETY_CLASSES.has(normalizeDetailFilterValue(safety));
        }
        if (key === 'no artist') {
            return !String(image?.artist_name || '').trim();
        }
        if (key === 'no source url') {
            return !String(image?.source_url || '').trim();
        }
        if (key === 'no generation info') {
            return !String(image?.generation_software || '').trim();
        }
        if (key === 'no prompt') {
            const civitai = parsePossibleJsonObject(image?.civitai_data) || parsePossibleJsonObject(image?.civitai) || {};
            const exifPrompt = String(exif.prompt || exif.Prompt || '').trim();
            const civitaiPrompt = String((civitai.meta || {}).prompt || '').trim();
            return !exifPrompt && !civitaiPrompt;
        }
        if (key === 'no a1111 metadata') {
            return !looksLikeA1111UserCommentPayload();
        }
        if (key === 'no a1111 hires upscale') {
            return !a1111Features.hiresUpscale;
        }
        if (key === 'no a1111 regional prompter') {
            return !a1111Features.regionalPrompter;
        }
        if (key === 'no a1111 adetailer') {
            return !a1111Features.adetailer;
        }
        if (key === 'no comfyui metadata') {
            return !hasExifValue('prompt', 'Prompt', 'workflow', 'Workflow');
        }
        if (key === 'no tags') {
            return getImageTagSet(image).size === 0;
        }
        if (key === 'no exif data') {
            const exif = parsePossibleJsonObject(image?.exif_data);
            return !exif || Object.keys(exif).length === 0;
        }
        if (key === 'no civitai meta') {
            if (!isCivitaiHostedImage(image)) {
                return false;
            }
            const civitai = parsePossibleJsonObject(image?.civitai_data) || parsePossibleJsonObject(image?.civitai) || {};
            return Object.keys(civitai).length === 0;
        }
        return false;
    }

    function getDataExtractionMode(label) {
        const norm = normalizeDetailFilterValue(label);
        if (!norm) {
            return null;
        }
        for (const v of getAdvancedFilterValues('missingData')) {
            const colonIdx = v.indexOf(':');
            if (colonIdx < 0) {
                continue;
            }
            const prefix = v.substring(0, colonIdx).toLowerCase();
            const rest = normalizeDetailFilterValue(v.substring(colonIdx + 1));
            if (rest === norm && (prefix === 'present' || prefix === 'absent')) {
                return prefix;
            }
        }
        return null;
    }

    async function cycleDataExtractionPill(label) {
        const norm = normalizeDetailFilterValue(label);
        if (!norm) {
            return;
        }
        const current = getDataExtractionMode(label);
        // Remove any existing entry for this label
        state.advancedFilters.missingData = state.advancedFilters.missingData.filter((v) => {
            const colonIdx = v.indexOf(':');
            if (colonIdx < 0) {
                return true;
            }
            return normalizeDetailFilterValue(v.substring(colonIdx + 1)) !== norm;
        });
        // Advance to next state: null → present → absent → null
        if (current === null) {
            state.advancedFilters.missingData.push(`present:${label}`);
        } else if (current === 'present') {
            state.advancedFilters.missingData.push(`absent:${label}`);
        }
        // current === 'absent' → entry was removed above (back to unselected)
        await applyFilter({ ensureSearchCoverage: true });
        renderAdvancedFilters();
    }

    function imageMatchesMissingDataFilter(image) {
        const values = getAdvancedFilterValues('missingData');
        if (!values.length) {
            return true;
        }
        // AND semantics: image must satisfy ALL selected conditions.
        for (const entry of values) {
            const colonIdx = entry.indexOf(':');
            if (colonIdx < 0) {
                continue;
            }
            const mode = entry.substring(0, colonIdx).toLowerCase();
            const label = entry.substring(colonIdx + 1);
            const isMissing = isMissingDataConditionMet(image, label);
            if (mode === 'present' && isMissing) {
                return false;
            }
            if (mode === 'absent' && !isMissing) {
                return false;
            }
        }
        return true;
    }

    function imageMatchesDetailFilters(image) {
        if (!imageMatchesAnyFilter(image?.generation_software, 'generationSoftware')) {
            return false;
        }
        if (!imageMatchesAnyFilter(image?.source_site, 'sourceSite')) {
            return false;
        }
        if (!imageMatchesAnyFilter(image?.mimetype, 'mimetype')) {
            return false;
        }
        if (!imageMatchesNsfwRatingFilter(image)) {
            return false;
        }
        if (!imageMatchesNsfwSafetyFilter(image)) {
            return false;
        }
        if (!_imageMatchesModePrefixFilter(image?.artist_name, 'artistName')) {
            return false;
        }
        if (!_imageMatchesCollectionModePrefixFilter(image, 'collections')) {
            return false;
        }

        // Tag filters with mode (include/exclude).
        // When serverFilterMode is active and the server config includes tag
        // filters, the server already applied them — skip client-side tag
        // matching to avoid false negatives for concept-observation matches
        // that aren't available in the image's client-side tag data.
        const selectedTags = getAdvancedFilterValues('tags');
        const serverHandledTags = Boolean(state.serverFilterMode
            && state.activeServerFilterConfig
            && (state.activeServerFilterConfig.includeTags?.length
                || state.activeServerFilterConfig.excludeTags?.length));
        if (selectedTags.length && !serverHandledTags) {
            const imageTags = getImageTagSet(image);
            for (const tagEntry of selectedTags) {
                const { mode, name } = _parseModePrefixEntry(tagEntry);
                const normalizedTag = normalizeDetailFilterValue(name);
                if (!normalizedTag) continue;
                const hasTag = imageTags.has(normalizedTag);
                if (mode === 'exclude') {
                    if (hasTag) return false;
                } else {
                    if (!hasTag) return false;
                }
            }
        }

        // When server handles missing-data filters, skip client-side check.
        const serverHandledMissingData = Boolean(state.serverFilterMode
            && state.activeServerFilterConfig?.missingData?.length);
        if (!serverHandledMissingData && !imageMatchesMissingDataFilter(image)) {
            return false;
        }

        return true;
    }

    function syncThemeMode() {
        if (preferences?.applyTheme) {
            state.themeMode = preferences.applyTheme(state.themeMode);
            return;
        }
        document.body.setAttribute('data-theme', state.themeMode === 'dark' ? 'dark' : 'light');
    }

    function getSortedUniqueDisplayValues(values) {
        const nextValues = sanitizeAdvancedFilterValues(values);
        nextValues.sort((left, right) => left.localeCompare(right, undefined, { sensitivity: 'base' }));
        return nextValues;
    }

    function createAdvancedFilterPill(category, value) {
        const label = String(value || '').trim();
        if (!label) {
            return null;
        }

        const isActive = isAdvancedFilterValueActive(category, label);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `advanced-filter-pill${isActive ? ' is-active' : ''}`;
        button.textContent = label;
        button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        button.title = isActive ? `Remove filter: ${label}` : `Add filter: ${label}`;
        button.addEventListener('click', () => {
            void toggleDetailFilter(category, label, label);
        });
        return button;
    }

    function createDataExtractionPill(label) {
        const displayLabel = String(label || '').trim();
        if (!displayLabel) {
            return null;
        }

        const mode = getDataExtractionMode(displayLabel);

        const button = document.createElement('button');
        button.type = 'button';

        if (mode === 'present') {
            button.className = 'advanced-filter-pill is-active';
            button.setAttribute('aria-pressed', 'true');
            button.title = `${displayLabel}: showing items that have this data (click for missing, hover to clear)`;
        } else if (mode === 'absent') {
            button.className = 'advanced-filter-pill is-absent';
            button.setAttribute('aria-pressed', 'mixed');
            button.title = `${displayLabel}: showing items missing this data (click to clear, hover to clear)`;
        } else {
            button.className = 'advanced-filter-pill';
            button.setAttribute('aria-pressed', 'false');
            button.title = `Filter by ${displayLabel}`;
        }

        const labelSpan = document.createElement('span');
        labelSpan.className = 'pill-label';
        labelSpan.textContent = displayLabel;
        button.appendChild(labelSpan);

        const removeSpan = document.createElement('span');
        removeSpan.className = 'pill-remove';
        removeSpan.setAttribute('aria-hidden', 'true');
        removeSpan.textContent = '×';
        button.appendChild(removeSpan);

        removeSpan.addEventListener('click', async (e) => {
            e.stopPropagation();
            const norm = normalizeDetailFilterValue(displayLabel);
            state.advancedFilters.missingData = state.advancedFilters.missingData.filter((v) => {
                const colonIdx = v.indexOf(':');
                if (colonIdx < 0) {
                    return true;
                }
                return normalizeDetailFilterValue(v.substring(colonIdx + 1)) !== norm;
            });
            await applyFilter({ ensureSearchCoverage: true });
            renderAdvancedFilters();
        });

        button.addEventListener('click', () => {
            void cycleDataExtractionPill(displayLabel);
        });

        return button;
    }

    function renderDataExtractionPills(container) {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        MISSING_DATA_PILL_ORDER.forEach((label) => {
            const pill = createDataExtractionPill(label);
            if (pill) {
                container.appendChild(pill);
            }
        });
    }

    function renderStatusPills(container) {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        STATUS_PILL_ORDER.forEach((label) => {
            const isActive = isAdvancedFilterValueActive('status', label);
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `advanced-filter-pill${isActive ? ' is-active' : ''}`;
            button.textContent = label;
            button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
            button.title = isActive ? `Remove filter: ${label}` : `Add filter: ${label}`;
            button.addEventListener('click', () => {
                void toggleDetailFilter('status', label, label);
            });
            container.appendChild(button);
        });
    }

    function renderAdvancedFilterPillGroup(container, category, values) {
        if (!container) {
            return;
        }
        container.innerHTML = '';
        const sortedValues = category === 'nsfwRating'
            ? sortNsfwRatingValues(values)
            : category === 'nsfwSafety'
                ? sortNsfwSafetyValues(values)
                : getSortedUniqueDisplayValues(values);
        sortedValues.forEach((value) => {
            const pill = createAdvancedFilterPill(category, value);
            if (pill) {
                container.appendChild(pill);
            }
        });
    }

    function renderAdvancedFilterSelectedChips(container, category) {
        if (!container) {
            return;
        }
        const values = getAdvancedFilterValues(category);
        container.classList.toggle('hidden', values.length === 0);

        const hasModePrefix = _isModePrefixCategory(category);

        container.innerHTML = '';
        values.forEach((entry) => {
            let displayLabel = entry;
            let mode = null;

            if (hasModePrefix) {
                const parsed = _parseModePrefixEntry(entry);
                mode = parsed.mode;
                displayLabel = parsed.name;
            }

            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'advanced-filter-selected-chip';
            if (mode) {
                chip.classList.add(`mode-${mode}`);
            }
            chip.title = `Click to toggle include/exclude`;

            const labelSpan = document.createElement('span');
            labelSpan.className = 'chip-label';
            labelSpan.textContent = displayLabel;
            chip.appendChild(labelSpan);

            const removeBtn = document.createElement('span');
            removeBtn.className = 'chip-remove';
            removeBtn.textContent = '×';
            removeBtn.title = `Remove filter: ${displayLabel}`;
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                void removeAdvancedFilterEntry(category, displayLabel);
            });
            chip.appendChild(removeBtn);

            chip.addEventListener('click', () => {
                if (hasModePrefix) {
                    void toggleFilterIncludeExclude(category, displayLabel);
                } else {
                    void removeAdvancedFilterEntry(category, displayLabel);
                }
            });
            container.appendChild(chip);
        });
    }

    function populateDatalist(datalist, values) {
        if (!datalist) {
            return;
        }
        if (uiKit?.populateDatalist) {
            uiKit.populateDatalist(datalist, getSortedUniqueDisplayValues(values));
            return;
        }
        datalist.innerHTML = '';
        getSortedUniqueDisplayValues(values).forEach((value) => {
            const option = document.createElement('option');
            option.value = value;
            datalist.appendChild(option);
        });
    }

    /**
     * Dynamically filter the tag datalist as the user types.
     * Native <datalist> with 8000+ options causes browsers to truncate
     * suggestions before showing user tags.  This keeps the list small
     * and focused on what the user is actually typing.
     */
    const DATALIST_TAG_LIMIT = 200;
    let _tagDatalistAllOptions = []; // cached full tag list

    function _refreshTagDatalistCache() {
        _tagDatalistAllOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.tagNames) ? state.filterOptions.tagNames : []),
            ...getAdvancedFilterValues('tags').map((entry) => _parseModePrefixEntry(entry).name),
        ]);
    }

    function _filterTagDatalist(datalist, query) {
        if (!datalist) { return; }
        if (!_tagDatalistAllOptions.length && state.filterOptions?.tagNames?.length) {
            _refreshTagDatalistCache();
        }
        const q = (query || '').trim().toLowerCase();
        let matches;
        if (!q) {
            // No query yet — show user tags first, then others (capped)
            const userTags = new Set(
                Array.isArray(state.filterOptions?.tagNamesBySource?.user)
                    ? state.filterOptions.tagNamesBySource.user : []
            );
            const userFirst = [];
            const rest = [];
            _tagDatalistAllOptions.forEach((tag) => {
                (userTags.has(tag) ? userFirst : rest).push(tag);
            });
            matches = [...userFirst, ...rest];
        } else {
            // Filter by substring match
            matches = _tagDatalistAllOptions.filter((tag) =>
                tag.toLowerCase().includes(q)
            );
        }
        // Cap to prevent browser slowdown
        matches = matches.slice(0, DATALIST_TAG_LIMIT);

        datalist.innerHTML = '';
        matches.forEach((value) => {
            const option = document.createElement('option');
            option.value = value;
            datalist.appendChild(option);
        });
    }

    function wireDynamicTagDatalist(input, datalist) {
        if (!input || !datalist) { return; }
        input.addEventListener('input', () => {
            _filterTagDatalist(datalist, input.value);
        });
        input.addEventListener('focus', () => {
            _filterTagDatalist(datalist, input.value);
        });
    }

    // ── Search autocomplete ────────────────────────────────────────────

    function getSearchDebounceMs(text) {
        const len = text.trim().length;
        if (len <= 0) return 0;
        if (len === 1) return 600;
        if (len === 2) return 400;
        return 300; // 3+ chars: short debounce to avoid firing per-keystroke
    }

    async function fetchSuggestions(query) {
        // Build filter context using the shared helper so suggestions are
        // scoped to the same filtered gallery subset as /api/query.
        // Do NOT send config.search — it duplicates `q` and would narrow
        // the image pool, causing incorrect counts and missing tag suggestions.
        const filterBody = _buildFilterBody();
        const body = {
            q: query,
            limit: 15,
            // search intentionally omitted — see comment above
            filter: filterBody.filter || undefined,
        };

        const response = await fetch('/api/search/suggest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!response.ok) return { collections: [], artists: [], tags: [] };
        return response.json();
    }

    function highlightMatch(text, query) {
        if (!query) return document.createTextNode(text);
        const lowerText = text.toLowerCase();
        const lowerQuery = query.toLowerCase();
        const idx = lowerText.indexOf(lowerQuery);
        if (idx < 0) return document.createTextNode(text);

        const frag = document.createDocumentFragment();
        if (idx > 0) frag.appendChild(document.createTextNode(text.substring(0, idx)));
        const mark = document.createElement('mark');
        mark.textContent = text.substring(idx, idx + query.length);
        frag.appendChild(mark);
        if (idx + query.length < text.length) {
            frag.appendChild(document.createTextNode(text.substring(idx + query.length)));
        }
        return frag;
    }

    function renderAutocomplete(results, query) {
        if (!searchAutocomplete) return;

        const groups = [
            { label: 'collection', items: results.collections, category: 'collections' },
            { label: 'artist', items: results.artists, category: 'artistName' },
            { label: 'tag', items: results.tags, category: 'tags' },
        ];

        const hasAny = groups.some((g) => g.items && g.items.length > 0);
        if (!hasAny) {
            hideAutocomplete();
            return;
        }

        searchAutocomplete.innerHTML = '';
        acFocusedIndex = -1;
        let itemIndex = 0;

        for (const group of groups) {
            if (!group.items || group.items.length === 0) continue;

            const groupLabel = document.createElement('span');
            groupLabel.className = 'search-ac-group-label';
            groupLabel.textContent = group.label;
            searchAutocomplete.appendChild(groupLabel);

            for (const item of group.items) {
                // Support both legacy string format and new {name, count} objects
                const name = typeof item === 'string' ? item : item.name;
                const count = typeof item === 'object' && item.count != null ? item.count : null;

                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'search-ac-item';
                btn.setAttribute('role', 'option');
                btn.dataset.category = group.category;
                btn.dataset.value = name;
                btn.dataset.acIndex = String(itemIndex);

                const typeSpan = document.createElement('span');
                typeSpan.className = 'ac-type';
                typeSpan.textContent = group.label;
                btn.appendChild(typeSpan);

                const nameSpan = document.createElement('span');
                nameSpan.className = 'ac-name';
                nameSpan.appendChild(highlightMatch(name, query));
                btn.appendChild(nameSpan);

                if (count !== null) {
                    const countSpan = document.createElement('span');
                    countSpan.className = 'ac-count';
                    countSpan.textContent = String(count);
                    btn.appendChild(countSpan);
                }

                btn.addEventListener('mousedown', (e) => {
                    e.preventDefault(); // prevent blur
                });
                btn.addEventListener('click', () => {
                    void selectAutocompleteSuggestion(group.category, name);
                });
                searchAutocomplete.appendChild(btn);
                itemIndex++;
            }
        }

        searchAutocomplete.classList.remove('hidden');
    }

    function showAutocompleteSpinner() {
        if (!searchAutocomplete) return;
        searchAutocomplete.innerHTML = '';
        const spinner = document.createElement('div');
        spinner.className = 'search-ac-spinner';
        spinner.textContent = 'Searching…';
        searchAutocomplete.appendChild(spinner);
        searchAutocomplete.classList.remove('hidden');
        acFocusedIndex = -1;
    }

    function hideAutocomplete() {
        if (!searchAutocomplete) return;
        searchAutocomplete.classList.add('hidden');
        searchAutocomplete.innerHTML = '';
        acFocusedIndex = -1;
    }

    function focusAutocompleteItem(newIndex) {
        const items = searchAutocomplete?.querySelectorAll('.search-ac-item') || [];
        if (items.length === 0) return;

        // Remove previous focus
        if (acFocusedIndex >= 0 && acFocusedIndex < items.length) {
            items[acFocusedIndex].classList.remove('is-focused');
        }

        // Clamp
        if (newIndex < 0) newIndex = items.length - 1;
        if (newIndex >= items.length) newIndex = 0;
        acFocusedIndex = newIndex;

        items[acFocusedIndex].classList.add('is-focused');
        items[acFocusedIndex].scrollIntoView({ block: 'nearest' });
    }

    async function selectAutocompleteSuggestion(category, value) {
        hideAutocomplete();
        if (searchInput) searchInput.value = '';

        // For tags, the label displayed in toast is 'tag'; for others derive from category
        const labelMap = { collections: 'collection', artistName: 'artist', tags: 'tag' };
        const label = labelMap[category] || category;
        await addAdvancedFilterValue(category, value, label);
    }

    function renderQuickChips() {
        if (!quickChipsContainer) return;

        const entries = [];
        // Gather all active advanced filter values as chip descriptors
        const categoryLabels = {
            generationSoftware: 'source',
            sourceSite: 'host',
            mimetype: 'type',
            nsfwRating: 'nsfw',
            nsfwSafety: 'safety',
            artistName: 'artist',
            tags: 'tag',
            collections: 'collection',
            missingData: 'data',
        };
        for (const [category, label] of Object.entries(categoryLabels)) {
            const hasModePrefix = _isModePrefixCategory(category);
            for (const rawValue of getAdvancedFilterValues(category)) {
                let displayValue = rawValue;
                let mode = 'include';
                if (hasModePrefix) {
                    const parsed = _parseModePrefixEntry(rawValue);
                    mode = parsed.mode;
                    displayValue = parsed.name;
                }
                entries.push({ category, rawValue, displayValue, label, mode, hasModePrefix });
            }
        }

        if (entries.length === 0) {
            quickChipsContainer.classList.add('hidden');
            quickChipsContainer.innerHTML = '';
            return;
        }

        quickChipsContainer.innerHTML = '';
        for (const entry of entries) {
            const chip = document.createElement('button');
            chip.type = 'button';
            chip.className = 'advanced-filter-selected-chip';
            if (entry.hasModePrefix) {
                chip.classList.add(`mode-${entry.mode}`);
            }
            chip.title = entry.hasModePrefix
                ? `Click to toggle include/exclude`
                : `Click to remove`;

            const typeSpan = document.createElement('span');
            typeSpan.className = 'chip-type-label';
            typeSpan.textContent = entry.label;
            chip.appendChild(typeSpan);

            const labelSpan = document.createElement('span');
            labelSpan.className = 'chip-label';
            labelSpan.textContent = entry.displayValue;
            chip.appendChild(labelSpan);

            const removeBtn = document.createElement('span');
            removeBtn.className = 'chip-remove';
            removeBtn.textContent = '×';
            removeBtn.title = `Remove filter: ${entry.displayValue}`;
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                void removeAdvancedFilterEntry(entry.category, entry.displayValue);
            });
            chip.appendChild(removeBtn);

            chip.addEventListener('click', () => {
                if (entry.hasModePrefix) {
                    void toggleFilterIncludeExclude(entry.category, entry.displayValue);
                } else {
                    void removeAdvancedFilterEntry(entry.category, entry.displayValue);
                }
            });
            quickChipsContainer.appendChild(chip);
        }
        quickChipsContainer.classList.remove('hidden');
    }

    // ── End search autocomplete ────────────────────────────────────────

    function renderAdvancedFilters() {
        if (!advancedFiltersPanel) {
            return;
        }

        const generationOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.generationSoftware) ? state.filterOptions.generationSoftware : []),
            ...getAdvancedFilterValues('generationSoftware'),
        ]);
        const sourceOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.sourceSites) ? state.filterOptions.sourceSites : []),
            ...getAdvancedFilterValues('sourceSite'),
        ]);
        const mimetypeOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.mimetypes) ? state.filterOptions.mimetypes : []),
            ...getAdvancedFilterValues('mimetype'),
        ]);
        const nsfwRatingOptions = sortNsfwRatingValues([
            ...NSFW_RATING_PILL_ORDER,
            ...(Array.isArray(state.filterOptions?.nsfwRatings) ? state.filterOptions.nsfwRatings : []),
            ...getAdvancedFilterValues('nsfwRating'),
        ]);
        const nsfwSafetyOptions = sortNsfwSafetyValues([
            ...NSFW_SAFETY_PILL_ORDER,
            ...(Array.isArray(state.filterOptions?.nsfwSafety) ? state.filterOptions.nsfwSafety : []),
            ...getAdvancedFilterValues('nsfwSafety'),
        ]);
        const authorOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.artistNames) ? state.filterOptions.artistNames : []),
            ...state.artistNames,
            // Strip mode prefix from stored filter values for datalist display
            ...getAdvancedFilterValues('artistName').map((entry) => _parseModePrefixEntry(entry).name),
        ]);
        const tagOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.tagNames) ? state.filterOptions.tagNames : []),
            // Strip mode prefix from stored tag filter values for datalist display
            ...getAdvancedFilterValues('tags').map((entry) => _parseModePrefixEntry(entry).name),
        ]);
        const collectionOptions = getSortedUniqueDisplayValues([
            ...(Array.isArray(state.filterOptions?.collectionNames) ? state.filterOptions.collectionNames : []),
            ...state.collections.map((collection) => collection?.name),
            // Strip mode prefix from stored filter values for datalist display
            ...getAdvancedFilterValues('collections').map((entry) => _parseModePrefixEntry(entry).name),
        ]);

        renderAdvancedFilterPillGroup(advancedGenerationPills, 'generationSoftware', generationOptions);
        renderAdvancedFilterPillGroup(advancedSourcePills, 'sourceSite', sourceOptions);
        renderAdvancedFilterPillGroup(advancedMimetypePills, 'mimetype', mimetypeOptions);
        renderAdvancedFilterPillGroup(advancedNsfwRatingPills, 'nsfwRating', nsfwRatingOptions);
        renderAdvancedFilterPillGroup(advancedNsfwSafetyPills, 'nsfwSafety', nsfwSafetyOptions);
        renderAdvancedFilterSelectedChips(advancedAuthorSelected, 'artistName');
        renderAdvancedFilterSelectedChips(advancedTagSelected, 'tags');
        renderAdvancedFilterSelectedChips(advancedCollectionSelected, 'collections');
        renderDataExtractionPills(advancedMissingDataPills);
        renderStatusPills(advancedStatusPills);
        populateDatalist(advancedAuthorOptions, authorOptions);
        // Tag datalist is managed dynamically by wireDynamicTagDatalist
        _refreshTagDatalistCache();
        _filterTagDatalist(advancedTagOptions, advancedTagInput?.value);
        populateDatalist(advancedCollectionOptions, collectionOptions);

        if (advancedFiltersSummary) {
            const activeCount = Object.values(state.advancedFilters || {})
                .reduce((sum, values) => sum + (Array.isArray(values) ? values.length : 0), 0);
            advancedFiltersSummary.textContent = activeCount > 0
                ? `${activeCount} active filter${activeCount === 1 ? '' : 's'}`
                : 'No active filters';
        }

        if (advancedFiltersClearBtn) {
            advancedFiltersClearBtn.disabled = !hasActiveDetailFilters();
        }

        renderQuickChips();
    }

    async function addAdvancedFilterValue(category, rawValue, label) {
        const nextValue = String(rawValue || '').trim();
        if (!nextValue) {
            return false;
        }

        const currentValues = getAdvancedFilterValues(category);
        const currentSet = getAdvancedFilterValueSet(category);
        const normalizedNextValue = normalizeDetailFilterValue(nextValue);
        if (!normalizedNextValue || currentSet.has(normalizedNextValue)) {
            return false;
        }

        // Mode-prefixed categories store values as "include:value" / "exclude:value"
        const storedValue = _isModePrefixCategory(category)
            ? `include:${nextValue}`
            : nextValue;

        state.advancedFilters[category] = [...currentValues, storedValue];
        // Provide immediate visual feedback before async filtering.
        renderAdvancedFilters();
        await applyFilter({ ensureSearchCoverage: true });
        showToast(`Added ${label} filter: ${nextValue}`, 'info');
        return true;
    }

    function clearAdvancedFilters() {
        state.advancedFilters = createEmptyAdvancedFilters();
    }

    function wireAdvancedFilterInput(input, button, category, label) {
        if (!input || !button) {
            return;
        }

        const submit = async () => {
            const added = await addAdvancedFilterValue(category, input.value, label);
            if (added) {
                input.value = '';
                renderAdvancedFilters();
            }
        };

        button.addEventListener('click', () => {
            void submit();
        });
        input.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') {
                return;
            }
            event.preventDefault();
            void submit();
        });
    }

    async function fetchFilterOptions() {
        const response = await fetch('/api/filters/options');
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }

        const tagNamesBySource = result?.tag_names_by_source && typeof result.tag_names_by_source === 'object'
            ? result.tag_names_by_source
            : { civitai: [], danbooru: [], prompt: [], user: [] };
        return {
            tagNamesBySource,
            tagNames: Array.isArray(result?.tag_names) ? result.tag_names : [],
            generationSoftware: Array.isArray(result?.generation_software) ? result.generation_software : [],
            sourceSites: Array.isArray(result?.source_sites) ? result.source_sites : [],
            mimetypes: Array.isArray(result?.mimetypes) ? result.mimetypes : [],
            nsfwRatings: Array.isArray(result?.nsfw_ratings) ? result.nsfw_ratings : [],
            nsfwSafety: Array.isArray(result?.nsfw_safety) ? result.nsfw_safety : [],
            artistNames: Array.isArray(result?.artist_names) ? result.artist_names : [],
            collectionNames: Array.isArray(result?.collection_names) ? result.collection_names : [],
        };
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

    function toAbsoluteUrl(rawUrl) {
        const value = String(rawUrl || '').trim();
        if (!value) {
            return '';
        }
        if (/^https?:\/\//i.test(value)) {
            return value;
        }
        if (value.startsWith('//')) {
            return `${window.location.protocol}${value}`;
        }
        try {
            return new URL(value, window.location.origin).toString();
        } catch {
            return '';
        }
    }

    function getDragDropImageUrl(image) {
        const urls = getDragDropImageUrls(image);
        if (!urls.length) {
            return '';
        }
        const localUrl = toAbsoluteUrl(getImageUrl(image));
        if (localUrl && urls.includes(localUrl)) {
            return localUrl;
        }
        return urls[0];
    }

    function getDragDropImageUrls(image) {
        const pushUrl = (collector, url) => {
            const absolute = toAbsoluteUrl(url);
            if (!absolute) {
                return;
            }
            if (!looksLikeImageUrl(absolute)) {
                return;
            }
            if (!collector.includes(absolute)) {
                collector.push(absolute);
            }
        };

        const urls = [];
        const mediaUrl = getMediaUrlForDisplay(image);
        const civitaiUrl = getCivitaiMediaUrl(image);
        const sourceUrl = String(image?.source_url || '').trim();
        const localUrl = getImageUrl(image);

        // Prefer externally reachable HTTPS URLs first for cross-site drop targets.
        if (/^https:\/\//i.test(String(mediaUrl || '').trim())) {
            pushUrl(urls, mediaUrl);
        }
        if (/^https:\/\//i.test(String(civitaiUrl || '').trim())) {
            pushUrl(urls, civitaiUrl);
        }
        if (/^https:\/\//i.test(sourceUrl)) {
            pushUrl(urls, sourceUrl);
        }

        // Keep local preview URL as fallback for local tools on trusted origins.
        pushUrl(urls, localUrl);
        pushUrl(urls, mediaUrl);
        pushUrl(urls, civitaiUrl);
        pushUrl(urls, sourceUrl);

        return urls;
    }

    function getDragDropFilename(image) {
        const rawName = String(image?.file_name || image?.file_path || image?.file_hash || 'atelier-image').trim();
        const baseName = rawName.split('/').pop() || 'atelier-image';
        return baseName || 'atelier-image';
    }

    function wireImageDragPayload(target, resolveImage) {
        if (!(target instanceof HTMLImageElement) || typeof resolveImage !== 'function') {
            return;
        }

        target.draggable = true;
        target.addEventListener('dragstart', (event) => {
            const image = resolveImage();
            if (!image || typeof image !== 'object') {
                return;
            }

            const dragUrls = getDragDropImageUrls(image);
            if (!dragUrls.length) {
                return;
            }
            const dragUrl = getDragDropImageUrl(image);
            const uriListPayload = dragUrls.join('\r\n');

            const transfer = event.dataTransfer;
            if (!transfer) {
                return;
            }

            const fileName = getDragDropFilename(image);
            const mimeType = String(image?.mimetype || 'application/octet-stream').trim() || 'application/octet-stream';
            transfer.effectAllowed = 'copy';
            transfer.setData('text/uri-list', uriListPayload);
            transfer.setData('text/plain', dragUrl);
            transfer.setData('URL', dragUrl);
            transfer.setData('text/html', `<img src="${dragUrl}" alt="${fileName}">`);
            transfer.setData('DownloadURL', `${mimeType}:${fileName}:${dragUrl}`);
        });
    }

    function getCivitaiMediaUrl(image) {
        const candidates = [
            image?.json_metadata?.civitai?.url,
            image?.civitai?.url,
            image?.civitai_data?.url,
            image?.civitai_url,
            image?.source_url,
        ];
        for (const candidate of candidates) {
            if (typeof candidate === 'string' && candidate.trim()) {
                return candidate.trim();
            }
        }
        return '';
    }

    function looksLikeVideoUrl(url) {
        if (!url || typeof url !== 'string') {
            return false;
        }
        const text = url.toLowerCase();
        if (/\.(mp4|webm|mov|mkv)(\?|#|$)/i.test(text)) {
            return true;
        }
        if (text.includes('transcode=true') && text.includes('.mp4')) {
            return true;
        }
        return false;
    }

    function extractCivitaiMediaUuid(url) {
        if (!url || typeof url !== 'string') {
            return '';
        }
        const match = String(url).match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
        return match ? String(match[0]).toLowerCase() : '';
    }

    function getCivitaiPlayableVideoUrl(url) {
        const uuid = extractCivitaiMediaUuid(url);
        if (!uuid) {
            return '';
        }
        return `https://image-b2.civitai.com/file/civitai-media-cache/${uuid}/original`;
    }

    function getEditableFileHash(image) {
        if (!image || typeof image !== 'object') {
            return '';
        }
        const editableHash = String(image.editable_file_hash || '').trim();
        if (editableHash) {
            return editableHash;
        }
        return String(image.file_hash || '').trim();
    }

    function getImageVariants(image) {
        if (!image || typeof image !== 'object') {
            return [];
        }
        if (Array.isArray(image.__variants)) {
            return image.__variants;
        }
        if (Array.isArray(image.variants)) {
            return image.variants;
        }
        return [];
    }

    function getVariantCount(image) {
        const variants = getImageVariants(image);
        if (variants.length) {
            return variants.length;
        }
        const parsedCount = Number(image?.variant_count || 0);
        return Number.isFinite(parsedCount) && parsedCount > 0 ? parsedCount : 1;
    }

    function getActiveVariantIndex(image) {
        const variants = getImageVariants(image);
        if (!variants.length) {
            return 0;
        }

        const activeVariantKey = String(image?.active_variant_key || '').trim();
        if (activeVariantKey) {
            const keyedIndex = variants.findIndex((variant) => String(variant?.variant_key || '').trim() === activeVariantKey);
            if (keyedIndex >= 0) {
                return keyedIndex;
            }
        }

        const rawIndex = Number(image?.variant_index || 0);
        if (Number.isInteger(rawIndex) && rawIndex >= 0 && rawIndex < variants.length) {
            return rawIndex;
        }

        return 0;
    }

    function getActiveVariant(image) {
        const variants = getImageVariants(image);
        if (!variants.length) {
            return null;
        }
        return variants[getActiveVariantIndex(image)] || null;
    }
    function resolveVariantDisplayFileName(image) {
        const activeVariant = getActiveVariant(image);
        const fallbackName = image?.original_file_name
            || image?.file_name
            || image?.file_path
            || image?.file_hash
            || 'Untitled';

        const preferredName = activeVariant?.original_file_name
            || activeVariant?.file_name
            || fallbackName;
        const normalizedPreferred = String(preferredName || '').trim();
        if (!activeVariant || !normalizedPreferred) {
            return normalizedPreferred || 'Untitled';
        }

        const variantMime = String(activeVariant?.mimetype || '').trim().toLowerCase();
        const variantNameOrPath = String(activeVariant?.file_name || activeVariant?.file_path || '').trim().toLowerCase();
        const variantLooksImage = variantMime.startsWith('image/')
            || /\.(avif|bmp|gif|jpe?g|png|tiff?|webp)(?:$|[?#])/.test(variantNameOrPath);
        const preferredLooksVideo = /\.(mp4|webm|mov|mkv)(?:$|[?#])/.test(normalizedPreferred.toLowerCase());
        if (!variantLooksImage || !preferredLooksVideo) {
            return normalizedPreferred;
        }

        const civitaiImageId = extractCivitaiImageIdFromImage(image);
        const extFromVariant = variantNameOrPath.match(/\.(avif|bmp|gif|jpe?g|png|tiff?|webp)(?:$|[?#])/);
        let imageExtension = extFromVariant ? `.${extFromVariant[1]}` : '';
        if (!imageExtension) {
            if (variantMime.includes('webp')) imageExtension = '.webp';
            else if (variantMime.includes('png')) imageExtension = '.png';
            else if (variantMime.includes('gif')) imageExtension = '.gif';
            else imageExtension = '.jpg';
        }

        if (civitaiImageId) {
            return `${civitaiImageId}${imageExtension}`;
        }

        return normalizedPreferred;
    }

    function syncClientImageVariantState(image) {
        if (!image || typeof image !== 'object') {
            return image;
        }

        const basePayload = image.__baseImageData && typeof image.__baseImageData === 'object'
            ? image.__baseImageData
            : { ...image };
        const variants = Array.isArray(image.__variants)
            ? image.__variants
            : (Array.isArray(image.variants) ? image.variants.map((variant) => ({ ...variant })) : []);

        const nextImage = image;
        const nextVariant = variants[getActiveVariantIndex({ ...basePayload, __variants: variants, active_variant_key: image.active_variant_key, variant_index: image.variant_index })] || null;
        Object.assign(nextImage, basePayload);
        if (nextVariant && typeof nextVariant === 'object') {
            Object.assign(nextImage, nextVariant);
        }
        nextImage.__baseImageData = basePayload;
        nextImage.__variants = variants;
        nextImage.variants = variants;
        nextImage.variant_count = variants.length || Number(basePayload.variant_count || 0) || 1;
        nextImage.variant_index = nextVariant ? variants.findIndex((variant) => variant === nextVariant) : 0;
        nextImage.active_variant_key = nextVariant?.variant_key || basePayload.active_variant_key || null;
        nextImage.editable_file_hash = getEditableFileHash(basePayload);
        nextImage.__activeVariant = nextVariant;
        return nextImage;
    }

    function setImageVariantIndex(image, nextIndex) {
        const variants = getImageVariants(image);
        if (!variants.length) {
            return false;
        }
        const boundedIndex = Math.max(0, Math.min(variants.length - 1, Number(nextIndex) || 0));
        image.variant_index = boundedIndex;
        image.active_variant_key = variants[boundedIndex]?.variant_key || null;
        syncClientImageVariantState(image);
        return true;
    }

    function stepImageVariant(image, delta) {
        const variants = getImageVariants(image);
        if (!variants.length || !delta) {
            return false;
        }
        const currentIndex = getActiveVariantIndex(image);
        const nextIndex = currentIndex + delta;
        if (nextIndex < 0 || nextIndex >= variants.length) {
            return false;
        }
        return setImageVariantIndex(image, nextIndex);
    }

    function activateDefaultVariant(image) {
        if (!image || typeof image !== 'object') {
            return false;
        }
        return setImageVariantIndex(image, 0);
    }

    /**
     * Update the variant badge and thumbnail on a single tile without a full
     * gallery redraw.  This preserves video previews that are playing on other
     * tiles.
     */
    function updateTileVariantBadge(image) {
        if (!image?.__key) { return; }
        const tile = galleryGrid.querySelector(`.tile[data-key="${CSS.escape(image.__key)}"]`);
        if (!tile) { return; }

        // Update variant badge text.
        const variantCount = getVariantCount(image);
        const badge = tile.querySelector('.tile-variant-badge');
        if (variantCount > 1) {
            if (badge) {
                badge.textContent = `${getActiveVariantIndex(image) + 1}/${variantCount}`;
            }
            tile.classList.add('has-variant-badge');
        } else {
            if (badge) { badge.remove(); }
            tile.classList.remove('has-variant-badge');
        }

        // Update thumbnail image to reflect the active variant.
        // Video tiles use a separate poster image managed by
        // applyTileVideoPoster(); setting its src to the video URL
        // would corrupt the poster, so skip the update for those.
        if (!tile.classList.contains('video-tile')) {
            const mediaUrl = getMediaUrlForDisplay(image);
            const img = tile.querySelector(':scope > img');
            if (img && img.src !== mediaUrl) {
                img.src = mediaUrl;
            }
        }
    }

    function getMediaUrlForDisplay(image) {
        const directDisplayUrl = typeof image?.display_url === 'string' ? image.display_url.trim() : '';
        if (directDisplayUrl) {
            return directDisplayUrl;
        }
        const localUrl = getImageUrl(image);
        
        // Transcode WebM to MP4 for Electron browsers (VSCode integrated browser)
        const isElectron = /Electron/i.test(navigator.userAgent);
        const filePath = String(image?.file_path || image?.file_name || '').toLowerCase();
        const isWebM = filePath.endsWith('.webm') || (image?.mimetype || '').toLowerCase() === 'video/webm';
        
        if (isElectron && isWebM && image?.file_hash) {
            const fileHash = image.file_hash.trim();
            const versionToken = image?.date_modified || image?.id || image?.file_size || fileHash;
            const version = versionToken ? `?v=${encodeURIComponent(String(versionToken))}` : '';
            return `/api/images/${encodeURIComponent(fileHash)}/video_mp4${version}`;
        }
        
        const civitaiUrl = getCivitaiMediaUrl(image);
        if (looksLikeVideoUrl(civitaiUrl)) {
            const playableVideoUrl = getCivitaiPlayableVideoUrl(civitaiUrl);
            if (playableVideoUrl) {
                return playableVideoUrl;
            }
            return civitaiUrl;
        }
        if (!isVideoAsset(image)) {
            return localUrl;
        }
        return localUrl;
    }

    function isVideoAsset(image) {
        const mimetype = typeof image?.mimetype === 'string' ? image.mimetype : '';
        if (mimetype.toLowerCase().startsWith('video/')) {
            return true;
        }

        const filePath = String(image?.file_path || image?.file_name || '').toLowerCase();
        if (/\.(mp4|webm|mov|mkv)$/.test(filePath)) {
            return true;
        }

        const civitaiUrl = getCivitaiMediaUrl(image);
        const displayUrl = typeof image?.display_url === 'string' ? image.display_url : '';
        return looksLikeVideoUrl(displayUrl) || looksLikeVideoUrl(civitaiUrl);
    }

    function looksLikeImageUrl(url) {
        const value = String(url || '').trim().toLowerCase();
        if (!value) {
            return false;
        }
        return /\.(avif|bmp|gif|jpe?g|png|tiff?|webp)(?:$|[?#])/.test(value);
    }

    function shouldRenderAsVideo(image, mediaUrl = '') {
        const resolvedUrl = String(mediaUrl || '').trim();
        if (resolvedUrl) {
            if (looksLikeImageUrl(resolvedUrl)) {
                return false;
            }
            if (looksLikeVideoUrl(resolvedUrl)) {
                return true;
            }
        }

        const activeVariant = getActiveVariant(image);
        const variantMimetype = typeof activeVariant?.mimetype === 'string' ? activeVariant.mimetype.trim().toLowerCase() : '';
        if (variantMimetype) {
            return variantMimetype.startsWith('video/');
        }

        return isVideoAsset(image);
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
        const editableHash = getEditableFileHash(image);
        if (editableHash) {
            return `hash:${editableHash}`;
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

    function getServerVideoPosterUrl(image) {
        if (!state.mediaCapabilities?.ffmpeg_available) {
            return '';
        }
        const fileHash = typeof image?.file_hash === 'string' ? image.file_hash.trim() : '';
        if (!fileHash) {
            return '';
        }
        const versionToken = image?.date_modified || image?.id || image?.file_size || fileHash;
        const version = versionToken ? `?v=${encodeURIComponent(String(versionToken))}` : '';
        return `/api/images/${encodeURIComponent(fileHash)}/video_poster${version}`;
    }

    function getVideoThumbnailUrl(image) {
        const candidates = [
            image?.video_thumbnail_url,
            image?.animated_preview_url,
            image?.json_metadata?.video_thumbnail_url,
        ];
        for (const candidate of candidates) {
            if (typeof candidate === 'string' && candidate.trim()) {
                return candidate;
            }
        }
        return '';
    }

    function getServerVideoThumbnailUrl(image) {
        if (!state.mediaCapabilities?.ffmpeg_available) {
            return '';
        }
        const fileHash = typeof image?.file_hash === 'string' ? image.file_hash.trim() : '';
        if (!fileHash) {
            return '';
        }
        const versionToken = image?.date_modified || image?.id || image?.file_size || fileHash;
        const version = versionToken ? `?v=${encodeURIComponent(String(versionToken))}` : '';
        return `/api/images/${encodeURIComponent(fileHash)}/video_thumbnail${version}`;
    }

    function applyTileVideoPoster(tile, posterImage, video, posterUrl) {
        if (posterImage instanceof HTMLImageElement) {
            if (posterUrl) {
                posterImage.src = posterUrl;
                tile.dataset.posterSrc = posterUrl;
            } else {
                posterImage.removeAttribute('src');
                delete tile.dataset.posterSrc;
            }
        }
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
        tile.classList.remove('has-animated-thumbnail');
    }

    function applyTileAnimatedThumbnail(tile, posterImage, thumbnailUrl) {
        if (!(posterImage instanceof HTMLImageElement)) {
            return;
        }
        if (thumbnailUrl) {
            posterImage.src = thumbnailUrl;
            tile.classList.add('has-animated-thumbnail');
            return;
        }

        tile.classList.remove('has-animated-thumbnail');
        const staticPosterUrl = tile.dataset.posterSrc || '';
        if (staticPosterUrl) {
            posterImage.src = staticPosterUrl;
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

    async function fetchServerVideoPoster(image) {
        const posterUrl = getServerVideoPosterUrl(image);
        if (!posterUrl) {
            return '';
        }

        try {
            const response = await fetch(posterUrl, { cache: 'force-cache' });
            if (!response.ok) {
                return '';
            }
            const blob = await response.blob();
            if (!(blob instanceof Blob) || blob.size <= 0) {
                return '';
            }
            return URL.createObjectURL(blob);
        } catch {
            return '';
        }
    }

    async function fetchServerVideoThumbnail(image) {
        const thumbnailUrl = getServerVideoThumbnailUrl(image);
        if (!thumbnailUrl) {
            return '';
        }

        try {
            const response = await fetch(thumbnailUrl, { cache: 'force-cache' });
            if (!response.ok) {
                return '';
            }
            const blob = await response.blob();
            if (!(blob instanceof Blob) || blob.size <= 0) {
                return '';
            }
            return URL.createObjectURL(blob);
        } catch {
            return '';
        }
    }

    async function resolveVideoPoster(job) {
        const serverPoster = await fetchServerVideoPoster(job.image);
        if (serverPoster) {
            return serverPoster;
        }
        return captureVideoPosterFromSource(job.mediaUrl);
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
            resolveVideoPoster(job)
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
        state.videoPosterQueue.push({ cacheKey, image, mediaUrl });
        pumpVideoPosterQueue();
    }

    function requestVideoThumbnail(image, mediaUrl, onReady) {
        const cacheKey = getVideoPosterCacheKey(image, mediaUrl);
        if (!cacheKey || !mediaUrl || !state.mediaCapabilities?.ffmpeg_available) {
            onReady('');
            return;
        }

        const directThumbnailUrl = getVideoThumbnailUrl(image);
        if (directThumbnailUrl) {
            state.videoThumbnailCache.set(cacheKey, directThumbnailUrl);
            onReady(directThumbnailUrl);
            return;
        }

        if (state.videoThumbnailCache.has(cacheKey)) {
            onReady(state.videoThumbnailCache.get(cacheKey) || '');
            return;
        }

        const inflight = state.videoThumbnailInflight.get(cacheKey);
        if (inflight) {
            inflight.push(onReady);
            return;
        }

        state.videoThumbnailInflight.set(cacheKey, [onReady]);
        state.videoThumbnailQueue.push({ cacheKey, image });
        pumpVideoThumbnailQueue();
    }

    function pumpVideoThumbnailQueue() {
        while (
            state.videoThumbnailActiveFetches < VIDEO_THUMBNAIL_FETCH_CONCURRENCY
            && state.videoThumbnailQueue.length
        ) {
            const job = state.videoThumbnailQueue.shift();
            if (!job) {
                return;
            }

            state.videoThumbnailActiveFetches += 1;
            fetchServerVideoThumbnail(job.image)
                .then((thumbnailUrl) => {
                    state.videoThumbnailCache.set(job.cacheKey, thumbnailUrl || null);
                    const callbacks = state.videoThumbnailInflight.get(job.cacheKey) || [];
                    state.videoThumbnailInflight.delete(job.cacheKey);
                    callbacks.forEach((callback) => callback(thumbnailUrl || ''));
                })
                .catch(() => {
                    const callbacks = state.videoThumbnailInflight.get(job.cacheKey) || [];
                    state.videoThumbnailInflight.delete(job.cacheKey);
                    callbacks.forEach((callback) => callback(''));
                })
                .finally(() => {
                    state.videoThumbnailActiveFetches = Math.max(0, state.videoThumbnailActiveFetches - 1);
                    pumpVideoThumbnailQueue();
                });
        }
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
                const posterImage = tile.__posterImageElement;
                const video = tile.__posterVideo;
                const mediaUrl = tile.__posterMediaUrl;

                if (!(posterImage instanceof HTMLImageElement) || !(video instanceof HTMLVideoElement) || !image || !mediaUrl) {
                    return;
                }

                requestVideoPoster(image, mediaUrl, (posterUrl) => {
                    if (!tile.isConnected || tile.__posterVideo !== video) {
                        return;
                    }
                    applyTileVideoPoster(tile, posterImage, video, posterUrl);
                });
            });
        }, {
            root: galleryGrid,
            rootMargin: '220px 0px',
            threshold: 0.01,
        });
    }

    function observeTileForPosterCapture(tile, image, posterImage, video, mediaUrl) {
        ensurePosterCaptureObserver();
        if (!posterCaptureObserver) {
            return;
        }

        tile.__posterImage = image;
        tile.__posterImageElement = posterImage;
        tile.__posterVideo = video;
        tile.__posterMediaUrl = mediaUrl;
        posterCaptureObserver.observe(tile);
    }

    function ensureVideoThumbnailObserver() {
        if (videoThumbnailObserver || !(galleryGrid instanceof HTMLElement)) {
            return;
        }

        videoThumbnailObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                const tile = entry.target;
                const image = tile.__thumbnailImage;
                const posterImage = tile.__thumbnailPosterImage;
                const mediaUrl = tile.__thumbnailMediaUrl;
                if (!(posterImage instanceof HTMLImageElement) || !image || !mediaUrl) {
                    return;
                }

                if (!entry.isIntersecting) {
                    tile.__thumbnailWanted = false;
                    applyTileAnimatedThumbnail(tile, posterImage, '');
                    return;
                }

                tile.__thumbnailWanted = true;

                requestVideoThumbnail(image, mediaUrl, (thumbnailUrl) => {
                    if (
                        !tile.isConnected
                        || tile.__thumbnailPosterImage !== posterImage
                        || tile.__thumbnailWanted !== true
                    ) {
                        return;
                    }
                    applyTileAnimatedThumbnail(tile, posterImage, thumbnailUrl);
                });
            });
        }, {
            root: galleryGrid,
            rootMargin: '280px 0px',
            threshold: 0.01,
        });
    }

    function observeTileForAnimatedThumbnail(tile, image, posterImage, mediaUrl) {
        ensureVideoThumbnailObserver();
        if (!videoThumbnailObserver) {
            return;
        }

        tile.__thumbnailImage = image;
        tile.__thumbnailPosterImage = posterImage;
        tile.__thumbnailMediaUrl = mediaUrl;
        tile.__thumbnailWanted = false;
        videoThumbnailObserver.observe(tile);
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
        updateFullscreenDebugOverlay('close-fullscreen');
        fullscreenPreview.classList.add('hidden');
        fullscreenPreview.setAttribute('aria-hidden', 'true');
        state.fullscreenSelectedKey = null;
        state.fullscreenIndexHint = null;
        updateFullscreenSelectionUi();
        if (fullscreenDebugOverlay) {
            fullscreenDebugOverlay.classList.add('hidden');
        }
        renderFullscreenEffectiveTags(null);
        fullscreenImage.classList.add('hidden');
        fullscreenImage.removeAttribute('src');
        releaseVideoElement(fullscreenVideo);
        fullscreenVideo.classList.add('hidden');
    }

    function updateFullscreenDebugOverlay(reason, extra = {}) {
        if (!fullscreenDebugOverlay || !fullscreenDebugContent) {
            return;
        }
        const fullscreenOpen = !fullscreenPreview.classList.contains('hidden');
        const shouldShow = fullscreenOpen && state.debugVisible;
        fullscreenDebugOverlay.classList.toggle('hidden', !shouldShow);
        if (!shouldShow) {
            return;
        }

        const currentKey = state.fullscreenSelectedKey || state.selectedKey || null;
        const currentIndex = currentKey
            ? state.filteredImages.findIndex((img) => img.__key === currentKey)
            : -1;
        const hintedIndex = Number.isInteger(state.fullscreenIndexHint)
            ? Number(state.fullscreenIndexHint)
            : null;

        const payload = {
            ts: new Date().toISOString(),
            reason,
            key: currentKey,
            selectedKey: state.selectedKey,
            fullscreenSelectedKey: state.fullscreenSelectedKey,
            currentIndex,
            hintedIndex,
            filteredCount: state.filteredImages.length,
            allCount: state.allImages.length,
            hasMore: state.hasMore,
            loadingPage: state.loadingPage,
            refreshInFlight: state.galleryRefreshInFlight,
            serverFilterMode: state.serverFilterMode,
            ...extra,
        };

        if (!fullscreenDebugFrozen) {
            fullscreenDebugContent.textContent = JSON.stringify(payload, null, 2);
        }

        if (currentIndex === -1 && fullscreenDebugPreviousIndex !== -1) {
            if (fullscreenDebugSnapshot) {
                fullscreenDebugSnapshot.textContent = JSON.stringify(payload, null, 2);
            }
            try {
                window.localStorage.setItem('atelier.fullscreenDebug.indexLoss', JSON.stringify(payload));
            } catch {
                // Ignore storage errors.
            }
        }
        fullscreenDebugPreviousIndex = currentIndex;

        try {
            window.localStorage.setItem('atelier.fullscreenDebug.last', JSON.stringify(payload));
        } catch {
            // Ignore storage errors.
        }
    }

    function syncFullscreenLoopUi() {
        if (!fullscreenLoopBtn) {
            return;
        }
        fullscreenLoopBtn.textContent = state.fullscreenLoopEnabled ? 'Loop: On' : 'Loop: Off';
        fullscreenLoopBtn.setAttribute('aria-pressed', state.fullscreenLoopEnabled ? 'true' : 'false');
    }

    function updateFullscreenSelectionUi() {
        if (!fullscreenCounter) {
            return;
        }

        const currentKey = state.fullscreenSelectedKey || state.selectedKey || null;
        const filteredCount = Array.isArray(state.filteredImages) ? state.filteredImages.length : 0;
        const currentIndex = currentKey
            ? state.filteredImages.findIndex((image) => image.__key === currentKey)
            : -1;
        const selectionStats = getSelectionStats();

        const indexLabel = currentIndex >= 0
            ? `${currentIndex + 1}/${Math.max(1, filteredCount)}`
            : `0/${Math.max(1, filteredCount)}`;
        fullscreenCounter.textContent = `${selectionStats.total} selected / ${filteredCount} filtered • ${indexLabel}`;

        // Toggle orange border on the visible media element.
        // Clear from both elements to avoid stale class on a hidden element.
        const isSelected = Boolean(currentKey && state.selectedKeys.has(currentKey));
        fullscreenImage.classList.toggle('fullscreen-selected', isSelected && !fullscreenImage.classList.contains('hidden'));
        fullscreenVideo.classList.toggle('fullscreen-selected', isSelected && !fullscreenVideo.classList.contains('hidden'));
    }

    function openFullscreenPreviewFromImage(image) {
        const mediaUrl = getMediaUrlForDisplay(image);
        if (!mediaUrl) {
            return;
        }

        if (fullscreenDebugSnapshot) {
            try {
                const priorSnapshot = window.localStorage.getItem('atelier.fullscreenDebug.indexLoss');
                fullscreenDebugSnapshot.textContent = priorSnapshot || '{}';
            } catch {
                fullscreenDebugSnapshot.textContent = '{}';
            }
        }

        state.fullscreenSelectedKey = image.__key || null;
        // Find the position by object reference first to preserve the correct
        // index when duplicate keys exist (same group key from different DB rows).
        // Only update the hint when we can confirm a better position; leave the
        // existing hint alone when it already points at a matching key.
        const existingHint = Number.isInteger(state.fullscreenIndexHint) ? Number(state.fullscreenIndexHint) : -1;
        if (existingHint >= 0 && existingHint < state.filteredImages.length && state.filteredImages[existingHint]?.__key === state.fullscreenSelectedKey) {
            // Existing hint is already consistent – keep it.
        } else {
            const refHint = state.filteredImages.indexOf(image);
            if (refHint >= 0) {
                state.fullscreenIndexHint = refHint;
            } else {
                const keyHint = state.filteredImages.findIndex((img) => img.__key === state.fullscreenSelectedKey);
                if (keyHint >= 0) {
                    state.fullscreenIndexHint = keyHint;
                }
            }
        }

        const videoMode = shouldRenderAsVideo(image, mediaUrl);
        fullscreenPreview.classList.remove('hidden');
        fullscreenPreview.setAttribute('aria-hidden', 'false');
        updateFullscreenDebugOverlay('open-fullscreen', {
            mediaType: videoMode ? 'video' : 'image',
        });
        // Note: updateFullscreenSelectionUi() is called after the media
        // switch below so the correct element gets the border class.
        renderFullscreenEffectiveTags(image);
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
        // Must run AFTER the media switch so the visible element gets the border.
        updateFullscreenSelectionUi();
    }

    function toggleFullscreenSelection() {
        const key = state.fullscreenSelectedKey;
        if (!key) return;

        toggleSelectionAndRender(key);
        updateFullscreenSelectionUi();
    }

    async function navigateFullscreenBy(delta) {
        if (fullscreenPreview.classList.contains('hidden')) {
            return;
        }

        if (state.fullscreenNavInFlight) {
            state.fullscreenQueuedDelta = Number(delta) || 0;
            updateFullscreenDebugOverlay('nav-queued', {
                delta,
                queuedDelta: state.fullscreenQueuedDelta,
            });
            return;
        }

        state.fullscreenNavInFlight = true;
        try {
            if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
                updateFullscreenDebugOverlay('nav-ignored-empty', { delta });
                return;
            }

            const currentKey = state.fullscreenSelectedKey || state.selectedKey;
            const hintedIndex = Number.isInteger(state.fullscreenIndexHint) ? Number(state.fullscreenIndexHint) : -1;
            // When duplicate keys exist (e.g. same file imported as separate DB rows),
            // findIndex always returns the first occurrence.  Prefer the hinted index
            // when it is consistent with the current key so navigation advances from
            // the actual position instead of wrapping back to the first duplicate.
            let currentIndex = -1;
            if (hintedIndex >= 0 && hintedIndex < state.filteredImages.length && state.filteredImages[hintedIndex]?.__key === currentKey) {
                currentIndex = hintedIndex;
            } else {
                currentIndex = state.filteredImages.findIndex((img) => img.__key === currentKey);
            }

            // During import refresh, selected/fullscreen keys can temporarily fall outside the loaded page window.
            // Try to recover a real anchor by loading additional pages toward the hinted index.
            if (currentIndex < 0 && currentKey && state.hasMore && !state.loadingPage) {
                let loads = 0;
                const maxLoads = 2;
                const targetLength = hintedIndex >= 0 ? (hintedIndex + 1) : 0;
                while (loads < maxLoads && state.hasMore && !state.loadingPage) {
                    const beforeLength = state.filteredImages.length;
                    await loadNextPage({ recomputeFilter: false });
                    refreshFilteredImagesAfterPageLoad();
                    loads += 1;
                    currentIndex = state.filteredImages.findIndex((img) => img.__key === currentKey);
                    const progressed = state.filteredImages.length > beforeLength;
                    const reachedHintWindow = targetLength > 0 && state.filteredImages.length >= targetLength;
                    if (currentIndex >= 0 || !progressed || reachedHintWindow) {
                        break;
                    }
                }
                updateFullscreenDebugOverlay('nav-anchor-recover', {
                    delta,
                    currentKey,
                    recoveredIndex: currentIndex,
                    hintedIndex,
                    filteredCount: state.filteredImages.length,
                    loads,
                });
            }

            const baseIndex = currentIndex >= 0
                ? currentIndex
                : Math.max(0, Math.min(hintedIndex >= 0 ? hintedIndex : 0, state.filteredImages.length - 1));
            const currentImage = state.filteredImages[baseIndex] || null;
            if (currentImage && stepImageVariant(currentImage, delta)) {
                state.fullscreenSelectedKey = currentImage.__key;
                state.fullscreenIndexHint = baseIndex;
                focusKeyInView(currentImage.__key);
                renderSelectionState({ force: true });
                openFullscreenPreviewFromImage(currentImage);
                updateFullscreenDebugOverlay('nav-variant-committed', {
                    delta,
                    baseIndex,
                    nextVariantIndex: getActiveVariantIndex(currentImage),
                    variantKey: currentImage.active_variant_key,
                });
                return;
            }
            updateFullscreenDebugOverlay('nav-base', {
                delta,
                currentIndex,
                hintedIndex,
                baseIndex,
            });

            if (delta < 0 && baseIndex <= 0) {
                updateFullscreenDebugOverlay('nav-blocked-start', { delta, baseIndex });
                return;
            }

            let nextIndex = baseIndex + delta;
            if (delta > 0 && nextIndex >= state.filteredImages.length) {
                if (state.hasMore && !state.loadingPage) {
                    const priorLength = state.filteredImages.length;
                    await loadNextPage({ recomputeFilter: false });
                    refreshFilteredImagesAfterPageLoad();

                // If the page load did not expand results (or filter excludes them), stay put.
                    if (state.filteredImages.length <= priorLength) {
                        updateFullscreenDebugOverlay('nav-blocked-no-expand', {
                            delta,
                            baseIndex,
                            priorLength,
                            currentLength: state.filteredImages.length,
                        });
                        return;
                    }

                    const refreshedKey = state.fullscreenSelectedKey || state.selectedKey;
                    // Prefer the actual navigation position over findIndex to avoid
                    // jumping to an earlier duplicate when the key appears more than once.
                    let refreshedIndex = -1;
                    if (baseIndex >= 0 && baseIndex < state.filteredImages.length && state.filteredImages[baseIndex]?.__key === refreshedKey) {
                        refreshedIndex = baseIndex;
                    } else {
                        refreshedIndex = state.filteredImages.findIndex((img) => img.__key === refreshedKey);
                    }
                    const anchoredIndex = refreshedIndex >= 0 ? refreshedIndex : baseIndex;
                    nextIndex = anchoredIndex + 1;
                    updateFullscreenDebugOverlay('nav-extended-after-load', {
                        delta,
                        baseIndex,
                        refreshedIndex,
                        anchoredIndex,
                        nextIndex,
                    });
                } else {
                    updateFullscreenDebugOverlay('nav-blocked-end', {
                        delta,
                        nextIndex,
                        filteredCount: state.filteredImages.length,
                        hasMore: state.hasMore,
                        loadingPage: state.loadingPage,
                    });
                    return;
                }
            }

            if (nextIndex < 0 || nextIndex >= state.filteredImages.length) {
                updateFullscreenDebugOverlay('nav-out-of-range', {
                    delta,
                    nextIndex,
                    filteredCount: state.filteredImages.length,
                });
                return;
            }

            const nextImage = state.filteredImages[nextIndex];
            if (!nextImage) {
                return;
            }

            state.fullscreenSelectedKey = nextImage.__key;
            state.fullscreenIndexHint = nextIndex;
            focusKeyInView(nextImage.__key);
            renderSelectionState();
            openFullscreenPreviewFromImage(nextImage);
            updateFullscreenDebugOverlay('nav-committed', {
                delta,
                nextIndex,
                nextKey: nextImage.__key,
            });
        } finally {
            state.fullscreenNavInFlight = false;
            const queuedDelta = state.fullscreenQueuedDelta;
            state.fullscreenQueuedDelta = null;
            if (queuedDelta) {
                void navigateFullscreenBy(queuedDelta);
            }
        }
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
        state.fullscreenIndexHint = target === 'first' ? 0 : state.filteredImages.length - 1;
        focusKeyInView(nextImage.__key);
        renderSelectionState();
        openFullscreenPreviewFromImage(nextImage);
        updateFullscreenDebugOverlay('nav-boundary', {
            target,
            nextKey: nextImage.__key,
            nextIndex: state.fullscreenIndexHint,
        });
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

        setSingleSelectionAndRender(nextImage.__key, { scrollIntoView: true });
    }

    function refreshFilteredImagesAfterPageLoad() {
        if (state.serverFilterMode) {
            state.filteredImages = state.allImages.slice();
            return;
        }
        const query = searchInput.value.trim().toLowerCase();
        state.filteredImages = computeFilteredImages(query);
    }

    async function navigateGalleryBy(delta) {
        if (!Array.isArray(state.filteredImages) || !state.filteredImages.length) {
            return;
        }

        // Prefer the last-known gallery index when the key still matches,
        // to avoid findIndex jumping to an earlier duplicate group key.
        const existingHint = Number.isInteger(state.galleryIndexHint) ? Number(state.galleryIndexHint) : -1;
        let currentIndex = -1;
        if (existingHint >= 0 && existingHint < state.filteredImages.length && state.filteredImages[existingHint]?.__key === state.selectedKey) {
            currentIndex = existingHint;
        } else {
            currentIndex = state.filteredImages.findIndex((img) => img.__key === state.selectedKey);
        }
        const baseIndex = currentIndex >= 0 ? currentIndex : 0;
        const currentImage = state.filteredImages[baseIndex] || null;

        if (currentImage && stepImageVariant(currentImage, delta)) {
            activateVariantAndRender(currentImage, getActiveVariantIndex(currentImage), { scrollIntoView: true });
            return;
        }

        if (delta < 0 && baseIndex <= 0) {
            return;
        }

        let nextIndex = baseIndex + delta;
        if (delta > 0 && nextIndex >= state.filteredImages.length) {
            if (state.hasMore && !state.loadingPage) {
                const priorLength = state.filteredImages.length;
                await loadNextPage({ recomputeFilter: false });
                refreshFilteredImagesAfterPageLoad();
                if (state.filteredImages.length <= priorLength) {
                    return;
                }

                // Prefer baseIndex when the key still sits there after the page refresh.
                let refreshedIndex = -1;
                if (baseIndex >= 0 && baseIndex < state.filteredImages.length && state.filteredImages[baseIndex]?.__key === state.selectedKey) {
                    refreshedIndex = baseIndex;
                } else {
                    refreshedIndex = state.filteredImages.findIndex((img) => img.__key === state.selectedKey);
                }
                const anchoredIndex = refreshedIndex >= 0 ? refreshedIndex : baseIndex;
                nextIndex = anchoredIndex + 1;
            } else {
                return;
            }
        }

        if (nextIndex < 0 || nextIndex >= state.filteredImages.length) {
            return;
        }

        state.galleryIndexHint = nextIndex;
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
            await loadNextPage({ recomputeFilter: false });
            refreshFilteredImagesAfterPageLoad();
            if (state.filteredImages.length <= priorLength) {
                break;
            }
        }

        selectGalleryImage(state.filteredImages[state.filteredImages.length - 1]);
    }

    function toClientImage(image, indexOffset) {
        // Keep keys stable across refresh/import reordering so detail/fullscreen state
        // stays attached to the same logical image.
        const stablePart = image.gallery_item_key
            || (image.file_hash && image.file_path ? `${image.file_hash}:${image.file_path}` : '')
            || ((image.file_hash && image.id) ? `${image.file_hash}:id:${image.id}` : '')
            || image.file_path
            || image.file_hash
            || (image.id ? `id:${image.id}` : '')
            || (image.file_name ? `name:${image.file_name}` : '')
            || `row-${indexOffset}`;
        const clientImage = {
            ...image,
            __key: stablePart,
            __baseImageData: { ...image },
            __variants: Array.isArray(image?.variants)
                ? image.variants.map((variant) => ({ ...variant }))
                : [],
        };
        return syncClientImageVariantState(clientImage);
    }

    function pickCaption(image) {
        const activeVariant = getActiveVariant(image);
        return resolveVariantDisplayFileName(image);
    }

    function renderMetaItem(label, value, options = {}) {
        const { spanTwo = false } = options;
        const wrapper = document.createElement('div');
        wrapper.className = 'meta-item';
        if (spanTwo) {
            wrapper.classList.add('meta-item--span-2');
        }

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
            inputValue,
            spanTwo = false,
            inputType = 'text',
            placeholder = '',
            suggestions = null,
            isUrlValue = false,
            displayLinkUrl = null,
            displayClickTitle = '',
            onDisplayClick = null,
            onSave,
        } = config;
        let currentValue = value;
        let currentInputValue = typeof inputValue === 'string' ? inputValue : value;

        const wrapper = document.createElement('div');
        wrapper.className = 'meta-item';
        if (spanTwo) {
            wrapper.classList.add('meta-item--span-2');
        }

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
            const shouldUseDisplayAction =
                typeof onDisplayClick === 'function'
                && typeof nextValue === 'string'
                && nextValue.trim().length > 0;

            if (shouldUseDisplayAction) {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'value-action-btn';
                button.textContent = nextValue;
                if (displayClickTitle) {
                    button.title = displayClickTitle;
                    button.setAttribute('aria-label', displayClickTitle);
                }
                button.addEventListener('click', () => {
                    onDisplayClick(nextValue);
                });
                valueNode.appendChild(button);
                return;
            }

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
        input.value = currentInputValue || '';

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
            input.value = currentInputValue || '';
            setEditing(false);
        });
        const saveCurrentValue = async () => {
            const nextValue = input.value.trim();
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
            try {
                const savedValue = await onSave(nextValue);
                currentValue = savedValue;
                currentInputValue = typeof savedValue === 'string' ? savedValue : currentInputValue;
                renderDisplayValue(currentValue);
                input.value = currentInputValue || '';
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
                input.value = currentInputValue || '';
                setEditing(false);
            }
        });

        wrapper.appendChild(head);
        wrapper.appendChild(valueNode);
        wrapper.appendChild(editRow);
        return wrapper;
    }

    async function saveImageMetadata(fileHash, patchData) {
        const response = await fetch(`/api/images/${encodeURIComponent(fileHash)}`, {
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
        const response = await fetch('/api/collections/');
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return Array.isArray(result) ? result : [];
    }

    async function fetchImagesStateSignature() {
        const params = new URLSearchParams();
        _appendNsfwVisibilityParams(params);
        const queryString = params.toString();
        const url = queryString ? `/api/images/state?${queryString}` : '/api/images/state';
        const response = await fetch(url);
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
        const response = await fetch('/api/utilities/image_status_counts');
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
        const response = await fetch(`/api/utilities/inactive_images?status=${encodeURIComponent(status)}&limit=300`);
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

    function getImageByKey(key) {
        if (!key) {
            return null;
        }
        return state.allImages.find((image) => image.__key === key) || null;
    }

    function getVisibleSelectionKeys() {
        return state.filteredImages
            .filter((image) => state.selectedKeys.has(image.__key))
            .map((image) => image.__key);
    }

    function getSelectionStats() {
        const total = state.selectedKeys.size;
        const visible = getVisibleSelectionKeys().length;
        return {
            total,
            visible,
            hidden: Math.max(0, total - visible),
        };
    }

    function updateSelectionUi() {
        const stats = getSelectionStats();

        if (selectionCount) {
            if (stats.total <= 0) {
                selectionCount.textContent = 'No selection';
            } else if (stats.hidden > 0) {
                selectionCount.innerHTML = `<span class="selection-pill-content">${stats.total} selected (${stats.visible} visible, ${stats.hidden} hidden) <button type="button" class="selection-pill-clear" aria-label="Clear selection" title="Clear selection">x</button></span>`;
            } else {
                selectionCount.innerHTML = `<span class="selection-pill-content">${stats.total} selected <button type="button" class="selection-pill-clear" aria-label="Clear selection" title="Clear selection">x</button></span>`;
            }
            selectionCount.hidden = stats.total <= 0;
            selectionCount.classList.toggle('has-selection', stats.total > 0);
            selectionCount.classList.toggle('count-pill-action', stats.total > 0);
            selectionCount.setAttribute('aria-disabled', stats.total > 0 ? 'false' : 'true');
        }

        renderImageCountControl();

        updateCollectionActionLabels();
        updateImageToolActionLabels();
    }

    function saveSelectionToStorage() {
        try {
            if (state.selectedKey) {
                window.localStorage.setItem(STORAGE_KEYS.selectedKey, state.selectedKey);
            } else {
                window.localStorage.removeItem(STORAGE_KEYS.selectedKey);
            }
            const keys = [...state.selectedKeys];
            if (keys.length) {
                window.localStorage.setItem(STORAGE_KEYS.selectedKeys, JSON.stringify(keys));
            } else {
                window.localStorage.removeItem(STORAGE_KEYS.selectedKeys);
            }
        } catch {
            // Ignore storage errors.
        }
    }

    function assignSingleSelection(key) {
        if (!key) {
            state.selectedKey = null;
            state.selectedKeys = new Set();
            state.lastSelectionAnchorKey = null;
            saveSelectionToStorage();
            return;
        }

        state.selectedKey = key;
        state.selectedKeys = new Set([key]);
        state.lastSelectionAnchorKey = key;
        saveSelectionToStorage();
    }

    /** Update the focused/active key for detail panel without touching the selection set.
     *  Used by fullscreen navigation so viewing an image does not replace existing selections. */
    function focusKeyInView(key) {
        if (!key) {
            state.selectedKey = null;
        } else {
            state.selectedKey = key;
        }
    }

    function syncSelectionState() {
        const fullscreenOpen = !fullscreenPreview.classList.contains('hidden');
        if (fullscreenOpen && state.fullscreenSelectedKey && state.selectedKey !== state.fullscreenSelectedKey) {
            state.selectedKey = state.fullscreenSelectedKey;
        }
        const availableKeys = new Set(state.allImages.map((image) => image.__key));
        const deferSelectedKeyFallback = Boolean(
            !state.serverFilterMode
            && state.selectedKey
            && !availableKeys.has(state.selectedKey)
            && state.galleryRefreshInFlight
            && state.hasMore
        );

        if (!state.serverFilterMode) {
            state.selectedKeys = new Set(
                [...state.selectedKeys].filter((key) => availableKeys.has(key) || key === state.selectedKey)
            );
        }

        if (state.selectedKey && !availableKeys.has(state.selectedKey) && !deferSelectedKeyFallback && !fullscreenOpen) {
            state.selectedKey = null;
        }

        const visibleKeys = new Set(state.filteredImages.map((image) => image.__key));
        const visibleSelectedKeys = getVisibleSelectionKeys();

        if (state.selectedKey && !visibleKeys.has(state.selectedKey) && !(deferSelectedKeyFallback && !visibleSelectedKeys.length) && !fullscreenOpen) {
            state.selectedKey = visibleSelectedKeys[0] || null;
        }

        if (!state.selectedKey && visibleSelectedKeys.length && !fullscreenOpen) {
            state.selectedKey = visibleSelectedKeys[0];
        }

        if (state.lastSelectionAnchorKey && !visibleKeys.has(state.lastSelectionAnchorKey)) {
            state.lastSelectionAnchorKey = state.selectedKey;
        }

        if (!state.selectedKeys.size) {
            state.lastSelectionAnchorKey = null;
        }
    }

    function updateGallerySelectionTiles() {
        const tiles = galleryGrid.querySelectorAll('.tile[data-key]');
        if (!tiles.length) {
            return;
        }

        const hasAnySelection = state.selectedKeys.size > 0;
        tiles.forEach((tile) => {
            const key = tile.dataset.key || '';
            const isSelected = state.selectedKeys.has(key);
            const isActive = state.selectedKey === key;
            tile.classList.toggle('selected', isSelected);
            tile.classList.toggle('active', isActive);
            tile.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
            tile.setAttribute('aria-current', isActive ? 'true' : 'false');

            const existingIndicator = tile.querySelector('.tile-selection-indicator');
            if (hasAnySelection && !existingIndicator) {
                const selectionIndicator = document.createElement('span');
                selectionIndicator.className = 'tile-selection-indicator';
                selectionIndicator.setAttribute('aria-hidden', 'true');
                tile.insertBefore(selectionIndicator, tile.firstChild || null);
            } else if (!hasAnySelection && existingIndicator) {
                existingIndicator.remove();
            }
        });
    }

    function getFilterRenderSignature() {
        const queryActive = searchInput.value.trim().length > 0;
        const treeTagFilterActive = Boolean(state.treeTagFilter);
        const detailFiltersActive = hasActiveDetailFilters();
        const filteredKeys = state.filteredImages.map((image) => image.__key).join('|');

        return [
            queryActive ? 'query:1' : 'query:0',
            treeTagFilterActive ? 'tree:1' : 'tree:0',
            detailFiltersActive ? `detail:${serializeDetailFilters()}` : 'detail:0',
            `filtered:${filteredKeys}`,
            `total:${state.totalImageCount}`,
        ].join('::');
    }

    function getSelectionVisualSignature() {
        const visibleSelectedKeys = getVisibleSelectionKeys().join('|');
        return [
            `selected:${visibleSelectedKeys}`,
            `active:${state.selectedKey || ''}`,
            `selected-count:${state.selectedKeys.size}`,
        ].join('::');
    }

    function getSelectionRenderSignature() {
        return `${getFilterRenderSignature()}::${getSelectionVisualSignature()}`;
    }

    function renderSelectionState(options = {}) {
        const force = options.force === true;
        const nextFilterSignature = getFilterRenderSignature();
        const nextSelectionSignature = getSelectionVisualSignature();
        const nextGallerySignature = `${nextFilterSignature}::${nextSelectionSignature}`;
        const selectedImage = getSelectedImage();
        const nextDetailKey = selectedImage?.__key || null;
        const filterChanged = state.lastRenderedFilterSignature !== nextFilterSignature;
        const selectionChanged = state.lastRenderedSelectionSignature !== nextSelectionSignature;
        const detailChanged = state.lastRenderedDetailKey !== nextDetailKey;
        const shouldRender = force
            || filterChanged
            || selectionChanged
            || detailChanged;

        if (!shouldRender) {
            return;
        }

        updateSelectionUi();

        const fullscreenOpen = !fullscreenPreview.classList.contains('hidden');

        // Full gallery redraw is only needed when filter/data changes.
        // Selection-only changes can be applied by toggling tile classes in place.
        if (!fullscreenOpen || force) {
            const shouldFullRender = force || filterChanged || !galleryGrid.querySelector('.tile');
            if (shouldFullRender) {
                renderAdvancedFilters();
                // Use append-only render when the gallery grew but nothing else
                // changed — avoids a full innerHTML wipe when adding a new page.
                const existingTileCount = galleryGrid.querySelectorAll('.tile').length;
                const isAppend = !force
                    && existingTileCount > 0
                    && existingTileCount < state.filteredImages.length
                    && !selectionChanged;
                renderGallery({ appendOnly: isAppend });
            } else if (selectionChanged) {
                updateGallerySelectionTiles();
            }
        }

        const shouldHoldDetailDuringRefresh = Boolean(
            fullscreenOpen
            && !selectedImage
            && (state.galleryRefreshInFlight || state.fullscreenSelectedKey || state.selectedKey)
        );
        if (!shouldHoldDetailDuringRefresh) {
            showDetails(selectedImage);
        }
        updateFullscreenDebugOverlay('render-selection', {
            force,
            shouldHoldDetailDuringRefresh,
            selectedImagePresent: Boolean(selectedImage),
            nextDetailKey,
        });
        state.lastRenderedFilterSignature = nextFilterSignature;
        state.lastRenderedSelectionSignature = nextSelectionSignature;
        state.lastRenderedGallerySignature = nextGallerySignature;
        state.lastRenderedDetailKey = nextDetailKey;
        updateFullscreenSelectionUi();
    }

    function setSingleSelectionAndRender(key, options = {}) {
        assignSingleSelection(key);
        renderSelectionState();

        if (options.scrollIntoView && key) {
            const activeTile = galleryGrid.querySelector(`.tile[data-key="${CSS.escape(key)}"]`);
            if (activeTile) {
                activeTile.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            }
        }
    }

    function activateVariantAndRender(image, nextVariantIndex, options = {}) {
        if (!image || typeof image !== 'object') {
            return false;
        }

        const changed = setImageVariantIndex(image, nextVariantIndex);
        if (!changed) {
            return false;
        }

        if (image.__key) {
            assignSingleSelection(image.__key);
        }
        renderSelectionState({ force: true });

        if (options.scrollIntoView && image.__key) {
            const activeTile = galleryGrid.querySelector(`.tile[data-key="${CSS.escape(image.__key)}"]`);
            if (activeTile) {
                activeTile.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            }
        }

        return true;
    }

    function toggleSelectionAndRender(key) {
        if (!key) {
            return;
        }

        const nextSelection = new Set(state.selectedKeys);
        if (nextSelection.has(key)) {
            nextSelection.delete(key);
        } else {
            nextSelection.add(key);
        }

        state.selectedKeys = nextSelection;
        if (nextSelection.has(key)) {
            state.selectedKey = key;
            state.lastSelectionAnchorKey = key;
        } else if (state.selectedKey === key) {
            state.selectedKey = getVisibleSelectionKeys()[0] || null;
        }

        if (!state.selectedKeys.size) {
            state.selectedKey = null;
            state.lastSelectionAnchorKey = null;
        }

        saveSelectionToStorage();
        renderSelectionState();
    }

    function selectRangeAndRender(targetKey, options = {}) {
        if (!targetKey) {
            return;
        }

        const visibleKeys = state.filteredImages.map((image) => image.__key);
        const anchorKey = visibleKeys.includes(state.lastSelectionAnchorKey)
            ? state.lastSelectionAnchorKey
            : (visibleKeys.includes(state.selectedKey) ? state.selectedKey : targetKey);
        const targetIndex = visibleKeys.indexOf(targetKey);
        const anchorIndex = visibleKeys.indexOf(anchorKey);

        if (targetIndex < 0 || anchorIndex < 0) {
            setSingleSelectionAndRender(targetKey);
            return;
        }

        const [start, end] = anchorIndex <= targetIndex
            ? [anchorIndex, targetIndex]
            : [targetIndex, anchorIndex];
        const rangeKeys = visibleKeys.slice(start, end + 1);
        const nextSelection = options.additive ? new Set(state.selectedKeys) : new Set();
        rangeKeys.forEach((key) => nextSelection.add(key));

        state.selectedKeys = nextSelection;
        state.selectedKey = targetKey;
        state.lastSelectionAnchorKey = anchorKey;
        saveSelectionToStorage();
        renderSelectionState();
    }

    function selectAllVisibleAndRender() {
        const nextSelection = new Set(state.selectedKeys);
        state.filteredImages.forEach((image) => nextSelection.add(image.__key));
        state.selectedKeys = nextSelection;

        if (!state.selectedKey || !state.filteredImages.some((image) => image.__key === state.selectedKey)) {
            state.selectedKey = state.filteredImages[0]?.__key || null;
        }
        if (!state.lastSelectionAnchorKey) {
            state.lastSelectionAnchorKey = state.selectedKey;
        }

        saveSelectionToStorage();
        renderSelectionState();
    }

    async function selectAllMatchingAndRender() {
        const query = searchInput.value.trim().toLowerCase();
        startForegroundBusy('select-all-matching', {
            countPillLabel: 'Selecting...',
            statusMessage: 'Selecting all matching items...',
        });

        try {
            if (state.serverFilterMode && state.activeServerFilterConfig) {
                const response = await fetch(buildImageKeysRequestUrl(state.activeServerFilterConfig));
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }

                const keys = await response.json();
                state.selectedKeys = new Set(Array.isArray(keys) ? keys : []);

                if (!state.selectedKey || !state.selectedKeys.has(state.selectedKey)) {
                    state.selectedKey = state.filteredImages[0]?.__key || state.selectedKey || null;
                }
                if (!state.lastSelectionAnchorKey) {
                    state.lastSelectionAnchorKey = state.selectedKey;
                }

                syncSelectionState();
                saveSelectionToStorage();
                renderSelectionState();
                return;
            }

            while (state.hasMore && !state.loadingPage) {
                const priorLoadedCount = state.allImages.length;
                await loadNextPage({ recomputeFilter: false });
                state.filteredImages = computeFilteredImages(query);
                if (state.allImages.length <= priorLoadedCount) {
                    break;
                }
            }

            state.filteredImages = computeFilteredImages(query);
            state.selectedKeys = new Set(state.filteredImages.map((image) => image.__key));

            if (!state.selectedKey || !state.filteredImages.some((image) => image.__key === state.selectedKey)) {
                state.selectedKey = state.filteredImages[0]?.__key || state.selectedKey || null;
            }
            if (!state.lastSelectionAnchorKey) {
                state.lastSelectionAnchorKey = state.selectedKey;
            }

            syncSelectionState();
            saveSelectionToStorage();
            renderSelectionState();
        } finally {
            finishForegroundBusy('select-all-matching');
        }
    }

    async function selectAllCatalogAndRender() {
        const query = searchInput.value.trim().toLowerCase();
        startForegroundBusy('select-all-catalog', {
            countPillLabel: 'Selecting...',
            statusMessage: 'Selecting the entire catalog...',
        });

        try {
            if (state.serverFilterMode) {
                const response = await fetch(buildImageKeysRequestUrl());
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }

                const keys = await response.json();
                state.selectedKeys = new Set(Array.isArray(keys) ? keys : []);

                if (!state.selectedKey || !state.selectedKeys.has(state.selectedKey)) {
                    state.selectedKey = state.filteredImages[0]?.__key || state.allImages[0]?.__key || null;
                }
                if (!state.lastSelectionAnchorKey) {
                    state.lastSelectionAnchorKey = state.selectedKey;
                }

                syncSelectionState();
                saveSelectionToStorage();
                renderSelectionState();
                return;
            }

            while (state.hasMore && !state.loadingPage) {
                const priorLoadedCount = state.allImages.length;
                await loadNextPage({ recomputeFilter: false });
                state.filteredImages = computeFilteredImages(query);
                if (state.allImages.length <= priorLoadedCount) {
                    break;
                }
            }

            state.filteredImages = computeFilteredImages(query);
            state.selectedKeys = new Set(state.allImages.map((image) => image.__key));

            if (!state.selectedKey || !state.selectedKeys.has(state.selectedKey)) {
                state.selectedKey = state.filteredImages[0]?.__key || state.allImages[0]?.__key || null;
            }
            if (!state.lastSelectionAnchorKey) {
                state.lastSelectionAnchorKey = state.selectedKey;
            }

            syncSelectionState();
            saveSelectionToStorage();
            renderSelectionState();
        } finally {
            finishForegroundBusy('select-all-catalog');
        }
    }

    function clearSelectionAndRender() {
        assignSingleSelection(null);
        renderSelectionState();
    }

    function getSelectedImage() {
        if (!state.selectedKey) {
            return null;
        }
        return getImageByKey(state.selectedKey);
    }

    function getSelectedImages() {
        return state.allImages.filter((image) => state.selectedKeys.has(image.__key));
    }

    function getDetailSelectionGroup(activeImage) {
        const selectedImages = getSelectedImages();
        if (selectedImages.length) {
            return selectedImages;
        }
        return activeImage ? [activeImage] : [];
    }

    function getDistinctGroupValues(images, readValue, { sort = false } = {}) {
        const values = [];
        const targets = Array.isArray(images) ? images : [];
        targets.forEach((image) => {
            const rawValue = readValue(image);
            if (Array.isArray(rawValue)) {
                rawValue.forEach((item) => values.push(item));
            } else {
                values.push(rawValue);
            }
        });

        const distinct = dedupeDisplayValues(values);
        if (sort) {
            distinct.sort((left, right) => left.localeCompare(right, undefined, { sensitivity: 'base' }));
        }
        return distinct;
    }

    function getCommonGroupValue(values) {
        return Array.isArray(values) && values.length === 1 ? values[0] : '';
    }

    function formatGroupDisplayValue(values, emptyLabel = 'N/A') {
        const list = Array.isArray(values) ? values : [];
        return list.length ? list.join(' | ') : emptyLabel;
    }

    async function saveImageMetadataForGroup(images, patchData, applyResult, fieldLabel) {
        const targets = (Array.isArray(images) ? images : [])
            .filter((image) => getEditableFileHash(image));
        if (!targets.length) {
            throw new Error('No selected items are available for update.');
        }

        let successCount = 0;
        const errors = [];
        for (const target of targets) {
            try {
                const result = await saveImageMetadata(getEditableFileHash(target), patchData);
                if (typeof applyResult === 'function') {
                    applyResult(target, result);
                    if (target.__baseImageData && typeof target.__baseImageData === 'object') {
                        Object.assign(target.__baseImageData, target);
                        syncClientImageVariantState(target);
                    }
                }
                successCount += 1;
            } catch (error) {
                errors.push(error instanceof Error ? error.message : String(error));
            }
        }

        if (successCount <= 0) {
            throw new Error(errors[0] || `Could not save ${fieldLabel}.`);
        }

        if (targets.length > 1) {
            if (errors.length) {
                showToast(`Updated ${successCount}/${targets.length} items for ${fieldLabel}.`, 'warn');
            } else {
                showToast(`Updated ${targets.length} items for ${fieldLabel}.`, 'success');
            }
        }

        return {
            successCount,
            totalCount: targets.length,
            errors,
        };
    }

    function getCollectionActionTargets() {
        const selectedImages = getSelectedImages();
        if (selectedImages.length) {
            return selectedImages;
        }
        const activeImage = getSelectedImage();
        return activeImage ? [activeImage] : [];
    }

    function getImageToolActionTargets() {
        return getCollectionActionTargets().filter((image) => Boolean(getEditableFileHash(image)));
    }

    function updateImageToolActionLabels() {
        const targetCount = getImageToolActionTargets().length;
        const hasTargets = targetCount > 0;
        const noun = targetCount === 1 ? 'item' : 'items';

        const repairLabel = hasTargets
            ? `Repair ${targetCount} selected ${noun}`
            : 'Repair media';
        repairImageBtn.title = repairLabel;
        repairImageBtn.setAttribute('aria-label', repairLabel);
        repairImageBtn.disabled = !hasTargets;

        if (rescanImageBtn) {
            const rescanLabel = hasTargets
                ? `Rescan metadata for ${targetCount} selected ${noun}`
                : 'Rescan metadata for this media item';
            rescanImageBtn.title = rescanLabel;
            rescanImageBtn.setAttribute('aria-label', rescanLabel);
            rescanImageBtn.disabled = !hasTargets;
        }

        const deleteLabel = hasTargets
            ? `Delete ${targetCount} selected ${noun} and sidecars`
            : 'Delete image file and sidecar';
        deleteImageFileBtn.title = deleteLabel;
        deleteImageFileBtn.setAttribute('aria-label', deleteLabel);
        deleteImageFileBtn.disabled = !hasTargets;
    }

    function updateCollectionActionLabels() {
        const targetCount = getCollectionActionTargets().length;
        const hasTargets = targetCount > 0;
        const noun = targetCount === 1 ? 'Item' : 'Items';

        if (addToCollectionBtn) {
            addToCollectionBtn.textContent = hasTargets ? `Add ${noun} to Collection` : 'Add Active Item to Collection';
            addToCollectionBtn.disabled = !hasTargets;
        }

        if (removeFromCollectionBtn) {
            removeFromCollectionBtn.textContent = hasTargets ? `Remove ${noun} from Collection` : 'Remove Active Item from Collection';
            removeFromCollectionBtn.disabled = !hasTargets;
        }
    }

    function updateImagesWithCollectionAddition(images, collection) {
        if (!collection || !Array.isArray(images)) {
            return;
        }

        images.forEach((image) => {
            const ids = Array.isArray(image.collection_ids) ? image.collection_ids.slice() : [];
            const names = Array.isArray(image.collection_names) ? image.collection_names.slice() : [];
            if (ids.includes(collection.id)) {
                return;
            }
            ids.push(collection.id);
            names.push(collection.name);
            image.collection_ids = ids;
            image.collection_names = names;
        });
    }

    function updateImagesWithCollectionRemoval(images, collectionId) {
        if (!Array.isArray(images)) {
            return;
        }

        images.forEach((image) => {
            const ids = Array.isArray(image.collection_ids) ? image.collection_ids.slice() : [];
            const names = Array.isArray(image.collection_names) ? image.collection_names.slice() : [];
            const nextIds = [];
            const nextNames = [];
            ids.forEach((id, index) => {
                if (id === collectionId) {
                    return;
                }
                nextIds.push(id);
                nextNames.push(names[index]);
            });
            image.collection_ids = nextIds;
            image.collection_names = nextNames;
        });
    }

    async function addImagesToCollection(collectionId, images) {
        const fileHashes = [...new Set(images
            .map((image) => getEditableFileHash(image))
            .filter((value) => typeof value === 'string' && value.trim()))];
        if (!fileHashes.length) {
            throw new Error('No selected items are available for collection add.');
        }

        const response = await fetch(`/api/collections/${collectionId}/images`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ file_hashes: fileHashes }),
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function removeImagesFromCollection(collectionId, images) {
        const fileHashes = [...new Set(images
            .map((image) => getEditableFileHash(image))
            .filter((value) => typeof value === 'string' && value.trim()))];
        if (!fileHashes.length) {
            throw new Error('No selected items are available for collection removal.');
        }

        const response = await fetch(`/api/collections/${collectionId}/images`, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ file_hashes: fileHashes }),
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.detail || `HTTP ${response.status}`);
        }
        return result;
    }

    async function refreshCollectionsState() {
        state.collections = await fetchCollections();
        syncCollectionSelect();
        renderAdvancedFilters();
    }

    async function focusImageByHash(fileHash) {
        if (!fileHash) {
            return;
        }

        const target = state.allImages.find((img) => getEditableFileHash(img) === fileHash);
        if (!target) {
            return;
        }

        assignSingleSelection(target.__key);
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
                    const response = await fetch(`/api/utilities/images/${encodeURIComponent(row.file_hash)}/restore`, {
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

        btn.addEventListener('dragend', () => {
            taxonomyDragSourceConceptId = null;
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
                const editableHash = getEditableFileHash(image);
                if (!editableHash || !collectionId) {
                    return;
                }
                try {
                    const response = await fetch(`/api/images/${encodeURIComponent(editableHash)}/collections/${collectionId}`, {
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

    function getVariantFilenameDebug(image) {
        const activeVariant = getActiveVariant(image);
        const rawPreferredFileName = activeVariant?.original_file_name
            || activeVariant?.file_name
            || image?.original_file_name
            || image?.file_name
            || image?.file_path
            || image?.file_hash
            || 'Untitled';
        const resolvedDisplayFileName = resolveVariantDisplayFileName(image);
        return {
            active_variant_key: image?.active_variant_key || null,
            active_variant_label: activeVariant?.variant_label || image?.variant_label || null,
            active_variant_file_name: activeVariant?.file_name || null,
            active_variant_original_file_name: activeVariant?.original_file_name || null,
            image_file_name: image?.file_name || null,
            image_original_file_name: image?.original_file_name || null,
            raw_preferred_file_name: rawPreferredFileName,
            resolved_display_file_name: resolvedDisplayFileName,
        };
    }

    function getAvailableDetailTabIds() {
        return DETAIL_TAB_IDS.filter((tabId) => detailPanelByTabId.has(tabId));
    }

    function resolveDetailTabId(nextTabId) {
        const available = getAvailableDetailTabIds();
        if (!available.length) {
            return null;
        }
        const normalized = String(nextTabId || '').trim();
        return available.includes(normalized) ? normalized : available[0];
    }

    function focusActiveDetailTabButton() {
        const activeButton = detailFolderWorkspace?.stackEl?.querySelector('.ftab[aria-selected="true"]');
        if (activeButton instanceof HTMLElement) {
            activeButton.focus();
        }
    }

    function buildDetailFolderTabs() {
        const availableTabIds = getAvailableDetailTabIds();
        const splitIndex = Math.ceil(availableTabIds.length / 2);
        return availableTabIds.map((tabId, index) => ({
            id: tabId,
            label: DETAIL_TAB_LABELS[tabId] || tabId,
            // Put the first half on the front/lower strip so Image Attributes starts bottom-left.
            row: index < splitIndex ? 2 : 1,
            render: () => {
                const panel = detailPanelByTabId.get(tabId);
                if (!panel) {
                    const placeholder = document.createElement('p');
                    placeholder.className = 'detail-placeholder';
                    placeholder.textContent = `Content area for "${DETAIL_TAB_LABELS[tabId] || tabId}".`;
                    return placeholder;
                }
                panel.hidden = false;
                panel.classList.add('is-active');
                return panel;
            },
        }));
    }

    function setActiveDetailTab(nextTabId, { persist = true, focus = false } = {}) {
        const resolvedId = resolveDetailTabId(nextTabId);
        if (!resolvedId) {
            return;
        }

        state.detailActiveTabId = resolvedId;
        if (detailFolderWorkspace) {
            detailFolderWorkspace.setActiveTabId(resolvedId);
        }

        if (persist) {
            writeStoredString(STORAGE_KEYS.detailActiveTab, resolvedId);
        }

        if (focus) {
            focusActiveDetailTabButton();
        }
    }

    function bindDetailFolderKeyboardSupport() {
        if (!detailFolderWorkspace?.stackEl) {
            return;
        }
        detailFolderWorkspace.stackEl.addEventListener('keydown', (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement) || !target.closest('.ftab')) {
                return;
            }
            const orderedTabIds = getAvailableDetailTabIds();
            if (!orderedTabIds.length) {
                return;
            }
            const currentId = resolveDetailTabId(state.detailActiveTabId);
            const currentIndex = currentId ? orderedTabIds.indexOf(currentId) : -1;
            if (currentIndex < 0) {
                return;
            }

            const moveToIndex = (nextIndex) => {
                const wrappedIndex = (nextIndex + orderedTabIds.length) % orderedTabIds.length;
                setActiveDetailTab(orderedTabIds[wrappedIndex], { persist: true, focus: true });
            };

            if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault();
                moveToIndex(currentIndex + 1);
                return;
            }
            if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault();
                moveToIndex(currentIndex - 1);
                return;
            }
            if (event.key === 'Home') {
                event.preventDefault();
                moveToIndex(0);
                return;
            }
            if (event.key === 'End') {
                event.preventDefault();
                moveToIndex(orderedTabIds.length - 1);
            }
        });
    }

    function initializeDetailFolderTabs() {
        if (!detailFolderMount || !uiKit || typeof uiKit.createStackedFolderWorkspace !== 'function') {
            return;
        }

        if (detailPanelStash instanceof HTMLElement) {
            detailFolderPanels.forEach((panel) => {
                panel.hidden = true;
                panel.classList.remove('is-active');
                detailPanelStash.append(panel);
            });
        }

        const tabs = buildDetailFolderTabs();
        if (!tabs.length) {
            return;
        }

        // Main app default: always open on Image Attributes when available.
        const defaultTabId = tabs.some((tab) => tab.id === 'image-attributes')
            ? 'image-attributes'
            : tabs[0].id;
        state.detailActiveTabId = defaultTabId;

        detailFolderWorkspace = uiKit.createStackedFolderWorkspace({
            tabs,
            activeTabId: defaultTabId,
            ariaLabel: 'Detail folders',
            wrapperClassName: 'detail-folder-workspace',
            stackClassName: 'detail-folder-stack',
            bodyClassName: 'detail-folder-body',
            onTabChange: (tabId) => {
                state.detailActiveTabId = tabId;
                writeStoredString(STORAGE_KEYS.detailActiveTab, tabId);
            },
        });

        const tabButtons = Array.from(detailFolderWorkspace.stackEl.querySelectorAll('.ftab'));
        tabs.forEach((tab, index) => {
            const button = tabButtons[index];
            const panel = detailPanelByTabId.get(tab.id);
            if (!button) {
                return;
            }
            button.id = `detail-tab-${tab.id}`;
            button.dataset.tabId = tab.id;
            if (panel) {
                button.setAttribute('aria-controls', panel.id);
                panel.setAttribute('aria-labelledby', button.id);
            }
        });

        detailFolderMount.replaceChildren(detailFolderWorkspace.root);
        bindDetailFolderKeyboardSupport();

        setActiveDetailTab(state.detailActiveTabId, { persist: false, focus: false });
    }

    function showDetails(image) {
        // Lazy-load detail-only blob fields (exif_data, civitai_data,
        // json_metadata) the first time an image is selected.  The gallery
        // list endpoint strips these fields to keep the payload lean; they
        // are fetched on demand from GET /api/images/{base_image_id}.
        if (image && !image._detail_loaded && !image._detail_loading) {
            image._detail_loading = true;
            const imageId = image.base_image_id ?? image.id;
            fetch(`/api/images/${imageId}`)
                .then((res) => res.ok ? res.json() : null)
                .then((detail) => {
                    if (detail) {
                        image.exif_data = detail.exif_data ?? null;
                        image.civitai_data = detail.civitai_data ?? null;
                        image.json_metadata = detail.json_metadata ?? null;
                        image.civitai_tags = Array.isArray(detail.civitai_tags)
                            ? detail.civitai_tags
                            : [];
                    }
                    image._detail_loaded = true;
                    image._detail_loading = false;
                    // Re-render detail panel only if this image is still selected.
                    if (getSelectedImage()?.__key === image.__key) {
                        showDetails(image);
                        postSelectedImageTagsToTree(image);
                    }
                    // Also refresh fullscreen effective tags if fullscreen is
                    // open and showing this image (tags depend on the
                    // lazy-loaded exif/civitai/json_metadata blobs).
                    const fullscreenOpen = !fullscreenPreview.classList.contains('hidden');
                    if (fullscreenOpen && state.fullscreenSelectedKey === image.__key) {
                        renderFullscreenEffectiveTags(image);
                    }
                })
                .catch(() => {
                    image._detail_loaded = true;
                    image._detail_loading = false;
                });
        }

        if (!image) {
            closeFullscreenPreview();
            detailImage.classList.add('hidden');
            detailImage.style.display = 'none';
            detailImage.removeAttribute('src');
            releaseVideoElement(detailVideo);
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            detailVideoError.classList.add('hidden');
            detailsContent.classList.add('hidden');
            detailsEmpty.classList.remove('hidden');
            if (sendToGenerationLabBtn) {
                sendToGenerationLabBtn.classList.add('hidden');
                sendToGenerationLabBtn.disabled = true;
                sendToGenerationLabBtn.removeAttribute('data-href');
                sendToGenerationLabBtn.title = 'Open the Generation Metadata Lab for this item';
            }
            if (sendToPerceptualLabBtn) {
                sendToPerceptualLabBtn.classList.add('hidden');
                sendToPerceptualLabBtn.disabled = true;
                sendToPerceptualLabBtn.removeAttribute('data-href');
                sendToPerceptualLabBtn.title = 'Open the Perceptual Analyzer Lab for this item';
            }
            if (sendToModelLabBtn) {
                sendToModelLabBtn.classList.add('hidden');
                sendToModelLabBtn.disabled = true;
                sendToModelLabBtn.removeAttribute('data-href');
                sendToModelLabBtn.title = 'Open the Model Reference Lab for this item';
            }
            repairImageBtn.disabled = true;
            if (rescanImageBtn) {
                rescanImageBtn.disabled = true;
            }
            deleteImageFileBtn.disabled = true;
            currentDebugImage = null;
            debugBadge.classList.add('hidden');
            if (detailGeneration instanceof HTMLElement) {
                detailGeneration.innerHTML = '';
            }
            postSelectedImageTagsToTree(null);
            postSelectedImageModelsToModelsFrame(null);
            return;
        }

        detailsEmpty.classList.add('hidden');
        detailsContent.classList.remove('hidden');
        debugBadge.classList.toggle('hidden', !state.debugVisible);
        setActiveDetailTab(state.detailActiveTabId, { persist: false, focus: false });

        const imageUrl = getMediaUrlForDisplay(image);
        const videoMode = shouldRenderAsVideo(image, imageUrl);
        currentDebugImage = image;
        const generationLabDestination = getGenerationLabDestination(image);
        const perceptualLabDestination = getPerceptualLabDestination(image);
        const modelLabDestination = getModelLabDestination(image);
        if (sendToGenerationLabBtn) {
            if (generationLabDestination) {
                sendToGenerationLabBtn.classList.remove('hidden');
                sendToGenerationLabBtn.disabled = false;
                sendToGenerationLabBtn.dataset.href = generationLabDestination.href;
                sendToGenerationLabBtn.title = generationLabDestination.title;
            } else {
                sendToGenerationLabBtn.classList.add('hidden');
                sendToGenerationLabBtn.disabled = true;
                sendToGenerationLabBtn.removeAttribute('data-href');
                sendToGenerationLabBtn.title = 'Open the Generation Metadata Lab for this item';
            }
        }
        if (sendToPerceptualLabBtn) {
            if (perceptualLabDestination) {
                sendToPerceptualLabBtn.classList.remove('hidden');
                sendToPerceptualLabBtn.disabled = false;
                sendToPerceptualLabBtn.dataset.href = perceptualLabDestination.href;
                sendToPerceptualLabBtn.title = perceptualLabDestination.title;
            } else {
                sendToPerceptualLabBtn.classList.add('hidden');
                sendToPerceptualLabBtn.disabled = true;
                sendToPerceptualLabBtn.removeAttribute('data-href');
                sendToPerceptualLabBtn.title = 'Open the Perceptual Analyzer Lab for this item';
            }
        }
        if (sendToModelLabBtn) {
            if (modelLabDestination) {
                sendToModelLabBtn.classList.remove('hidden');
                sendToModelLabBtn.disabled = false;
                sendToModelLabBtn.dataset.href = modelLabDestination.href;
                sendToModelLabBtn.title = modelLabDestination.title;
            } else {
                sendToModelLabBtn.classList.add('hidden');
                sendToModelLabBtn.disabled = true;
                sendToModelLabBtn.removeAttribute('data-href');
                sendToModelLabBtn.title = 'Open the Model Reference Lab for this item';
            }
        }
        repairImageBtn.disabled = false;
        if (rescanImageBtn) {
            rescanImageBtn.disabled = false;
        }
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
            detailVideoError.classList.add('hidden');
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
        image.artist_profile = deriveArtistProfileUrl(image);
        const detailEditTargets = getDetailSelectionGroup(image);
        const artistValues = getDistinctGroupValues(detailEditTargets, (entry) => entry.artist_name, { sort: true });
        const artistProfileValues = getDistinctGroupValues(detailEditTargets, (entry) => deriveArtistProfileUrl(entry), { sort: true });
        const sourceUrlValues = getDistinctGroupValues(detailEditTargets, (entry) => entry.source_url, { sort: true });
        const activeVariant = getActiveVariant(image);
        const displayFileName = resolveVariantDisplayFileName(image);
        detailImage.alt = safeText(displayFileName, 'Selected image');
        detailTitle.textContent = safeText(displayFileName, image.file_hash || 'Untitled');
        renderDetailSubtitle(image);

        detailMeta.innerHTML = '';
        const jsonMetadata = image && typeof image.json_metadata === 'object' && image.json_metadata !== null
            ? image.json_metadata
            : {};
        const civitaiPayload = jsonMetadata && typeof jsonMetadata.civitai === 'object' && jsonMetadata.civitai !== null
            ? jsonMetadata.civitai
            : {};
        const imageUuid = safeText(image.civitai_uuid || civitaiPayload.uuid || jsonMetadata.civitai_uuid || null);
        const metaNodes = [
            renderMetaItem('Variant', getVariantCount(image) > 1 ? `${getActiveVariantIndex(image) + 1}/${getVariantCount(image)}${image.variant_label ? ` | ${image.variant_label}` : ''}` : (image.variant_label || null), { spanTwo: true }),
            renderMetaItem('Hash', image.file_hash, { spanTwo: true }),
            renderMetaItem('UUID', imageUuid, { spanTwo: true }),
            renderMetaItem('Dimentions', image.width && image.height ? `${image.width} x ${image.height}` : null),
            renderMetaItem('Size', formatBytes(image.file_size)),
            renderMetaItem('Created', image.date_created),
            renderMetaItem('Modified', image.date_modified),
            renderEditableMetaItem({
                label: 'Artist',
                value: formatGroupDisplayValue(artistValues),
                inputValue: getCommonGroupValue(artistValues),
                placeholder: 'Artist name (leave blank to clear)',
                suggestions: state.artistNames,
                displayClickTitle: artistValues.length === 1 ? 'Filter gallery to this artist' : '',
                onDisplayClick: artistValues.length === 1
                    ? (artistName) => {
                        void filterGalleryByArtist(artistName);
                    }
                    : null,
                onSave: async (nextValue) => {
                    await saveImageMetadataForGroup(detailEditTargets, {
                        artist_name: nextValue,
                    }, (target, result) => {
                        target.artist_id = result.artist_id ?? null;
                        target.artist_name = result.artist_name ?? null;
                        target.artist_profile = result.artist_profile ?? deriveArtistProfileUrl(target);
                    }, 'Artist');

                    const nextArtistValues = getDistinctGroupValues(
                        detailEditTargets,
                        (entry) => entry.artist_name,
                        { sort: true },
                    );
                    state.artistNames = getSortedUniqueDisplayValues([
                        ...state.artistNames,
                        ...state.allImages.map((entry) => entry?.artist_name),
                    ]);
                    renderAdvancedFilters();
                    return formatGroupDisplayValue(nextArtistValues);
                },
            }),
            ...(image.artist_deleted
                ? [renderMetaItem('⚠️ Account', `${image.artist_original_name || image.artist_name || '(deleted user)'} — CivitAI account deleted`, { spanTwo: true })]
                : []),
            renderEditableMetaItem({
                label: 'Artist Profile',
                value: formatGroupDisplayValue(artistProfileValues),
                inputType: 'url',
                inputValue: getCommonGroupValue(artistProfileValues),
                placeholder: 'https://... (leave blank to clear)',
                isUrlValue: artistProfileValues.length === 1,
                onSave: async (nextValue) => {
                    await saveImageMetadataForGroup(detailEditTargets, {
                        artist_profile: nextValue,
                    }, (target, result) => {
                        target.artist_profile = result.artist_profile ?? deriveArtistProfileUrl(target);
                    }, 'Artist Profile');

                    const nextArtistProfileValues = getDistinctGroupValues(
                        detailEditTargets,
                        (entry) => deriveArtistProfileUrl(entry),
                        { sort: true },
                    );
                    return formatGroupDisplayValue(nextArtistProfileValues);
                },
            }),
            renderEditableMetaItem({
                label: 'Image Source',
                value: formatGroupDisplayValue(sourceUrlValues),
                inputType: 'url',
                inputValue: getCommonGroupValue(sourceUrlValues),
                placeholder: 'https://... (leave blank to clear)',
                isUrlValue: sourceUrlValues.length === 1,
                onSave: async (nextValue) => {
                    await saveImageMetadataForGroup(detailEditTargets, {
                        source_url: nextValue,
                    }, (target, result) => {
                        target.source_url = result.source_url || null;
                        target.source_site = result.source_site || null;
                    }, 'Image Source');

                    const nextSourceValues = getDistinctGroupValues(
                        detailEditTargets,
                        (entry) => entry.source_url,
                        { sort: true },
                    );
                    renderDetailSubtitle(image);
                    return formatGroupDisplayValue(nextSourceValues);
                },
            }),
            renderEditableNsfwRatingItem(detailEditTargets),
        ];
        metaNodes.forEach((node) => detailMeta.appendChild(node));

        detailExif.textContent = _formatExifForDisplay(deepParseJsonStrings(parsePossibleJsonObject(image.exif_data) || {}));
        detailCivitai.textContent = JSON.stringify(deepParseJsonStrings(parsePossibleJsonObject(image.civitai_data || image.civitai) || {}), null, 2);
        void renderDetailGenerationPanel(image);
        renderImageCollections(image);

        setDebugBadge({
            key: image.__key,
            file_hash: image.file_hash,
            file_path: image.file_path,
            ...getVariantFilenameDebug(image),
            url: imageUrl,
            status: imageUrl ? 'loading' : 'missing-url',
            ...getImageLayoutDebug(videoMode ? detailVideo : detailImage),
        });

        postSelectedImageTagsToTree(image);
        postSelectedImageModelsToModelsFrame(image);
        renderAuthorityTagPanels();

        scheduleGalleryGridHeightSync();
    }

    function renderGallery(options = {}) {
        const appendOnly = options.appendOnly === true;
        renderImageCountControl();
        postGalleryKeysToModelsFrame();

        updateSelectionUi();
        const fullscreenOpen = !fullscreenPreview.classList.contains('hidden');

        if (!state.filteredImages.length) {
            galleryGrid.innerHTML = state.galleryRefreshInFlight
                ? '<p>Refreshing gallery...</p>'
                : '<p>No items match your filter.</p>';
            if (!state.galleryRefreshInFlight && !fullscreenOpen) {
                showDetails(null);
            }
            return;
        }

        // Append-only mode: only create tiles for newly added images.
        // This avoids a full innerHTML wipe when just adding the next page.
        const existingTileCount = galleryGrid.querySelectorAll('.tile').length;

        if (appendOnly && existingTileCount > 0 && existingTileCount < state.filteredImages.length) {
            // Incremental append — skip the full rebuild.
            const fragment = document.createDocumentFragment();
            const startIndex = existingTileCount;

            for (let i = startIndex; i < state.filteredImages.length; i++) {
                const image = state.filteredImages[i];
                const tile = buildTileElement(image);
                if (tile) fragment.appendChild(tile);
            }

            galleryGrid.appendChild(fragment);
            scheduleGalleryGridHeightSync();
            return;
        }

        galleryGrid.querySelectorAll('video').forEach((node) => releaseVideoElement(node));
        galleryGrid.innerHTML = '';
        const fragment = document.createDocumentFragment();

        state.filteredImages.forEach((image) => {
            const tile = buildTileElement(image);
            if (tile) fragment.appendChild(tile);
        });

        galleryGrid.appendChild(fragment);
        scheduleGalleryGridHeightSync();
    }

    /**
     * Build a single gallery tile DOM element for the given image object.
     * Extracted from renderGallery to support incremental appends.
     */
    function buildTileElement(image) {
        const caption = pickCaption(image);
        const tile = document.createElement('button');
        const isSelected = state.selectedKeys.has(image.__key);
        const isActive = state.selectedKey === image.__key;
        const variantCount = getVariantCount(image);
        tile.className = `tile ${isSelected ? 'selected' : ''} ${isActive ? 'active' : ''}`.trim();
        tile.type = 'button';
        tile.dataset.key = image.__key;
        tile.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
        tile.setAttribute('aria-current', isActive ? 'true' : 'false');

        if (state.selectedKeys.size > 0) {
            const selectionIndicator = document.createElement('span');
            selectionIndicator.className = 'tile-selection-indicator';
            selectionIndicator.setAttribute('aria-hidden', 'true');
            tile.appendChild(selectionIndicator);
        }

        if (variantCount > 1) {
            tile.classList.add('has-variant-badge');
            const variantBadge = document.createElement('span');
            variantBadge.className = 'tile-variant-badge';
            variantBadge.textContent = `${getActiveVariantIndex(image) + 1}/${variantCount}`;
            tile.appendChild(variantBadge);
        }

        const mediaUrl = getMediaUrlForDisplay(image);
        const videoMode = shouldRenderAsVideo(image, mediaUrl);

        let mediaNode;
        if (videoMode) {
            const posterImage = document.createElement('img');
            posterImage.loading = 'lazy';
            posterImage.decoding = 'async';
            posterImage.alt = safeText(caption);

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
                applyTileVideoPoster(tile, posterImage, video, directPosterUrl);
            } else {
                applyTileVideoPoster(tile, posterImage, video, '');
                observeTileForPosterCapture(tile, image, posterImage, video, mediaUrl);
            }
            observeTileForAnimatedThumbnail(tile, image, posterImage, mediaUrl);

            mediaNode = null;
            tile.appendChild(posterImage);
            tile.appendChild(video);
            tile.appendChild(placeholder);
        } else {
            const img = document.createElement('img');
            img.loading = 'lazy';
            img.alt = safeText(caption);
            img.src = mediaUrl;
            wireImageDragPayload(img, () => image);

            // BlurHash canvas placeholder
            const blurhashValue = image.blurhash || image.civitai_hash;
            if (blurhashValue && typeof BlurHashDecode !== 'undefined') {
                try {
                    const canvas = document.createElement('canvas');
                    canvas.className = 'blurhash-canvas';
                    canvas.width = 32;
                    canvas.height = 32;
                    const ctx = canvas.getContext('2d');
                    const pixels = BlurHashDecode.decode(blurhashValue, 32, 32);
                    if (pixels) {
                        const imageData = new ImageData(pixels, 32, 32);
                        ctx.putImageData(imageData, 0, 0);
                        tile.appendChild(canvas);
                        img.classList.add('fade-in');
                        img.addEventListener('load', () => img.classList.add('loaded'), { once: true });
                    }
                } catch (_) { /* non-critical; skip placeholder */ }
            }

            mediaNode = img;
        }

        const captionSpan = document.createElement('span');
        captionSpan.className = 'tile-caption';

        const primaryCaption = document.createElement('span');
        primaryCaption.className = 'tile-caption-primary';
        primaryCaption.textContent = safeText(caption);
        captionSpan.appendChild(primaryCaption);

        if (variantCount > 1 || image.variant_label) {
            const secondaryCaption = document.createElement('span');
            secondaryCaption.className = 'tile-caption-secondary';
            secondaryCaption.textContent = safeText(
                image.variant_label || `Variant ${getActiveVariantIndex(image) + 1}`,
                '',
            );
            captionSpan.appendChild(secondaryCaption);
        }

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

        if (mediaNode) {
            tile.appendChild(mediaNode);
        }
        tile.appendChild(captionSpan);
        return tile;
    }

    function computeFilteredImages(query) {
        // When serverFilterMode is active the server already applied tag-based
        // filtering (tree tags + advanced filter tag pills), so skip client-side
        // tag checks that would otherwise produce false negatives for concept-
        // observation matches that aren't available in the image's client-side
        // tag data.
        const skipTagFilters = Boolean(state.serverFilterMode
            && state.activeServerFilterConfig
            && (state.activeServerFilterConfig.includeTags?.length
                || state.activeServerFilterConfig.excludeTags?.length));

        // When server handles missing-data and missing-source filters, skip
        // client-side checks for those as well.
        const serverHandledMissingData = Boolean(state.serverFilterMode
            && state.activeServerFilterConfig?.missingData?.length);
        const serverHandledMissingSource = Boolean(state.serverFilterMode
            && state.activeServerFilterConfig?.missingSources?.length);

        return state.allImages.filter((image) => {
            if (!skipTagFilters && !imageMatchesTreeTagFilter(image)) {
                return false;
            }

            if (!serverHandledMissingSource && !imageMatchesMissingSourceFilter(image)) {
                return false;
            }

            if (!imageMatchesDetailFilters(image)) {
                return false;
            }

            if (!query) {
                return true;
            }

            const tagNames = Array.from(getImageTagSet(image));
            const haystack = [
                image.file_name,
                image.file_hash,
                image.artist_name,
                image.artist_profile,
                image.source_url,
                image.source_site,
                image.generation_software,
                image.mimetype,
                ...(Array.isArray(image.collection_names) ? image.collection_names : []),
                ...tagNames,
            ].filter(Boolean).join(' ').toLowerCase();
            return haystack.includes(query);
        });
    }

    function imageMatchesMissingSourceFilter(image) {
        const sources = ['civitai', 'danbooru', 'prompt', 'user'];
        for (const source of sources) {
            if (!state.missingSourceFilter[source]) {
                continue;
            }
            // Image must have zero tags from this source to pass the filter
            const tagsBySource = extractImageScopeTags(image);
            const tags = tagsBySource?.[source];
            if (Array.isArray(tags) && tags.length > 0) {
                return false;
            }
        }
        return true;
    }

    function isCivitaiHostedImage(image) {
        if (!image || typeof image !== 'object') {
            return false;
        }

        const sourceSite = String(image.source_site || '').trim().toLowerCase();
        if (sourceSite === 'civitai') {
            return true;
        }

        const sourceUrl = String(image.source_url || '').trim().toLowerCase();
        return sourceUrl.startsWith('https://civitai.com/') || sourceUrl.startsWith('http://civitai.com/')
            || sourceUrl.startsWith('https://civitai.red/') || sourceUrl.startsWith('http://civitai.red/');
    }

    function deriveArtistProfileUrl(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const explicitProfile = String(image.artist_profile || '').trim();
        if (explicitProfile) {
            return explicitProfile;
        }

        if (!isCivitaiHostedImage(image)) {
            return null;
        }

        const artistName = String(image.artist_name || '').trim();
        if (!artistName) {
            return null;
        }

        // Deleted users have synthetic names like [deleted:12345] — no real profile.
        if (/^\[deleted:\d+\]$/.test(artistName)) {
            return null;
        }

        return `${window.__ATELIER_CONFIG?.civitai_web_base_url || 'https://civitai.red'}/user/${encodeURIComponent(artistName)}`;
    }

    function extractCivitaiImageIdFromUrl(sourceUrl) {
        const normalizedUrl = String(sourceUrl || '').trim();
        if (!normalizedUrl) {
            return null;
        }

        try {
            const parsed = new URL(normalizedUrl, window.location.origin);
            const match = parsed.pathname.match(/\/images\/(\d+)(?:\/|$)/i);
            if (match && match[1]) {
                return match[1];
            }
        } catch {
            const fallbackMatch = normalizedUrl.match(/\/images\/(\d+)(?:\/|$)/i);
            if (fallbackMatch && fallbackMatch[1]) {
                return fallbackMatch[1];
            }
        }

        return null;
    }

    function extractCivitaiImageIdFromImage(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const civitaiPayloads = [image.civitai_data, image.civitai];
        for (const payload of civitaiPayloads) {
            if (!payload || typeof payload !== 'object') {
                continue;
            }

            const candidates = [payload.image_id, payload.id, payload.imageId];
            for (const candidate of candidates) {
                const normalized = String(candidate || '').trim();
                if (/^\d+$/.test(normalized)) {
                    return normalized;
                }
            }

            const urlCandidates = [payload.source_url, payload.url, payload.postUrl];
            for (const urlCandidate of urlCandidates) {
                const derived = extractCivitaiImageIdFromUrl(urlCandidate);
                if (derived) {
                    return derived;
                }
            }
        }

        return extractCivitaiImageIdFromUrl(image.source_url);
    }

    function getGenerationLabDestination(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const civitaiImageId = extractCivitaiImageIdFromImage(image);
        const fileHash = getEditableFileHash(image);
        if (civitaiImageId || fileHash) {
            const params = new URLSearchParams();
            if (civitaiImageId) {
                params.set('civitai', civitaiImageId);
            }
            if (fileHash) {
                params.set('fileHash', fileHash);
            }
            return {
                href: `/generation-lab?${params.toString()}`,
                title: civitaiImageId && fileHash
                    ? `Open Generation Metadata Lab for CivitAI image ${civitaiImageId} and local image ${fileHash}`
                    : civitaiImageId
                        ? `Open Generation Metadata Lab for CivitAI image ${civitaiImageId}`
                        : `Open Generation Metadata Lab for local image ${fileHash}`,
            };
        }

        return null;
    }

    function getPerceptualLabDestination(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const fileHash = getEditableFileHash(image);
        if (!fileHash) {
            return null;
        }

        const params = new URLSearchParams();
        params.set('fileHash', fileHash);
        return {
            href: `/perceptual-lab?${params.toString()}`,
            title: `Open Perceptual Analyzer Lab for local image ${fileHash}`,
        };
    }

    function getModelLabDestination(image) {
        if (!image || typeof image !== 'object') {
            return null;
        }

        const civitaiImageId = extractCivitaiImageIdFromImage(image);
        const fileHash = getEditableFileHash(image);
        if (!civitaiImageId && !fileHash) {
            return null;
        }

        const params = new URLSearchParams();
        if (civitaiImageId) {
            params.set('civitai', civitaiImageId);
        }
        if (fileHash) {
            params.set('fileHash', fileHash);
            // Keep legacy alias for routes/pages that still read `hash`.
            params.set('hash', fileHash);
        }
        params.set('source', 'gallery');
        return {
            href: `/model-lab?${params.toString()}`,
            title: civitaiImageId && fileHash
                ? `Open Model Reference Lab for CivitAI image ${civitaiImageId} and local image ${fileHash}`
                : civitaiImageId
                    ? `Open Model Reference Lab for CivitAI image ${civitaiImageId}`
                    : `Open Model Reference Lab for local image ${fileHash}`,
        };
    }

    async function filterGalleryByArtist(artistName) {
        const normalizedArtist = String(artistName || '').trim();
        if (!normalizedArtist) {
            return;
        }

        searchInput.value = normalizedArtist;
        await applyFilter({ ensureSearchCoverage: true });
        showToast(`Filtered gallery to artist: ${normalizedArtist}`, 'info');
    }

    async function toggleDetailFilter(category, value, label) {
        const normalizedValue = String(value || '').trim();
        if (!normalizedValue) {
            return;
        }

        if (!state.advancedFilters || !(category in state.advancedFilters)) {
            return;
        }

        const currentValues = getAdvancedFilterValues(category);
        const nextKey = normalizeDetailFilterValue(normalizedValue);
        if (!nextKey) {
            return;
        }

        if (_isModePrefixCategory(category)) {
            // Three-state cycling: include → exclude → remove
            const includeEntry = `include:${normalizedValue}`;
            const excludeEntry = `exclude:${normalizedValue}`;
            const existingIdx = currentValues.findIndex((entry) => {
                const { mode, name } = _parseModePrefixEntry(entry);
                return normalizeDetailFilterValue(name) === nextKey && (mode === 'include' || mode === 'exclude' || mode === '');
            });

            if (existingIdx < 0) {
                // Not present → add as include
                state.advancedFilters[category] = [...currentValues, includeEntry];
            } else {
                const existing = currentValues[existingIdx];
                const { mode: currentMode } = _parseModePrefixEntry(existing);
                if (currentMode === 'include') {
                    // include → exclude
                    const updated = [...currentValues];
                    updated[existingIdx] = excludeEntry;
                    state.advancedFilters[category] = updated;
                } else {
                    // exclude → remove
                    state.advancedFilters[category] = currentValues.filter((_, i) => i !== existingIdx);
                }
            }
        } else {
            // Non-tag categories: original toggle behavior
            if (isAdvancedFilterValueActive(category, normalizedValue)) {
                state.advancedFilters[category] = currentValues.filter((entry) => normalizeDetailFilterValue(entry) !== nextKey);
            } else {
                state.advancedFilters[category] = [...currentValues, normalizedValue];
            }
        }

        // Provide immediate visual feedback on chip/pill state before async filtering.
        renderAdvancedFilters();
        await applyFilter({ ensureSearchCoverage: true });
        const targetLabel = String(label || 'value').trim() || 'value';
        if (isAdvancedFilterValueActive(category, normalizedValue)) {
            showToast(`Added ${targetLabel} filter: ${normalizedValue}`, 'info');
        } else {
            showToast(`Cleared ${targetLabel} filter: ${normalizedValue}`, 'info');
        }
    }

    /** Remove a filter entry outright (used by the "x" button on chips). */
    async function removeAdvancedFilterEntry(category, displayValue) {
        const normalizedValue = normalizeDetailFilterValue(displayValue);
        if (!normalizedValue || !state.advancedFilters || !(category in state.advancedFilters)) return;

        const currentValues = getAdvancedFilterValues(category);
        if (_isModePrefixCategory(category)) {
            state.advancedFilters[category] = currentValues.filter((entry) => {
                const { name } = _parseModePrefixEntry(entry);
                return normalizeDetailFilterValue(name) !== normalizedValue;
            });
        } else {
            state.advancedFilters[category] = currentValues.filter((entry) => normalizeDetailFilterValue(entry) !== normalizedValue);
        }
        renderAdvancedFilters();
        await applyFilter({ ensureSearchCoverage: true });
    }

    /** Toggle a mode-prefix entry between include and exclude (no remove). */
    async function toggleFilterIncludeExclude(category, displayValue) {
        if (!_isModePrefixCategory(category)) return;
        const normalizedValue = normalizeDetailFilterValue(displayValue);
        if (!normalizedValue) return;
        const currentValues = getAdvancedFilterValues(category);
        const existingIdx = currentValues.findIndex((entry) => {
            const { name } = _parseModePrefixEntry(entry);
            return normalizeDetailFilterValue(name) === normalizedValue;
        });
        if (existingIdx < 0) return;
        const { mode } = _parseModePrefixEntry(currentValues[existingIdx]);
        const updated = [...currentValues];
        updated[existingIdx] = mode === 'include'
            ? `exclude:${displayValue}`
            : `include:${displayValue}`;
        state.advancedFilters[category] = updated;
        renderAdvancedFilters();
        await applyFilter({ ensureSearchCoverage: true });
    }

    function appendDetailSubtitleText(container, text) {
        const normalizedText = String(text || '').trim();
        if (!normalizedText) {
            return;
        }

        const segment = document.createElement('span');
        segment.className = 'detail-subtitle-text';
        segment.textContent = normalizedText;
        container.appendChild(segment);
    }

    function appendDetailSubtitleChip(container, value, label, category) {
        const normalizedValue = String(value || '').trim();
        if (!normalizedValue) {
            return;
        }

        const isActive = Boolean(category && isAdvancedFilterValueActive(category, normalizedValue));

        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = `detail-subtitle-chip${isActive ? ' is-active' : ''}`;
        chip.textContent = normalizedValue;
        chip.title = isActive
            ? `Remove ${label} filter: ${normalizedValue}`
            : `Filter gallery to ${label}: ${normalizedValue}`;
        chip.setAttribute('aria-pressed', isActive ? 'true' : 'false');
        chip.addEventListener('click', () => {
            if (!category) {
                return;
            }
            void toggleDetailFilter(category, normalizedValue, label);
        });
        container.appendChild(chip);
    }

    function renderDetailSubtitle(image) {
        detailSubtitle.innerHTML = '';
        if (!image) {
            return;
        }

        const selectionStats = getSelectionStats();
        const selectionText = selectionStats.total > 1
            ? `${selectionStats.total} selected${selectionStats.hidden > 0 ? ` (${selectionStats.hidden} hidden)` : ''}`
            : '';

        appendDetailSubtitleText(detailSubtitle, selectionText);
        appendDetailSubtitleChip(detailSubtitle, image.generation_software, 'generation source', 'generationSoftware');
        appendDetailSubtitleChip(detailSubtitle, image.source_site, 'hosting site', 'sourceSite');
        appendDetailSubtitleChip(detailSubtitle, image.mimetype, 'mimetype', 'mimetype');
        const nsfwTokens = getNsfwDisplayTokens(image);
        appendDetailSubtitleChip(detailSubtitle, nsfwTokens.granular, 'nsfw rating', 'nsfwRating');
        appendDetailSubtitleChip(detailSubtitle, nsfwTokens.safety, 'nsfw safety', 'nsfwSafety');
    }

    async function applyFilter(options = {}) {
        const ensureSearchCoverage = options.ensureSearchCoverage !== false;
        const query = searchInput.value.trim().toLowerCase();
        const runId = ++state.searchRunId;
        const nsfwVisibilityActive = state.nsfwVisibility !== 'explicit';
        const structuredFiltersActive = Boolean(state.treeTagFilter)
            || hasActiveDetailFilters()
            || Object.values(state.missingSourceFilter || {}).some(Boolean)
            || nsfwVisibilityActive;
        const anyFiltersActive = Boolean(query) || structuredFiltersActive;
        const serverFilterConfig = getServerFilterConfig(searchInput.value.trim());

        if (serverFilterConfig) {
            const signatureChanged = state.activeServerFilterSignature !== serverFilterConfig.signature;
            state.serverFilterMode = true;
            state.activeServerFilterSignature = serverFilterConfig.signature;
            state.activeServerFilterConfig = serverFilterConfig;

            if (signatureChanged) {
                const shouldShowBusy = ensureSearchCoverage;
                if (shouldShowBusy) {
                    startForegroundBusy('apply-filter', {
                        countPillLabel: 'Filtering...',
                        statusMessage: 'Filtering the catalog...',
                    });
                }

                try {
                    state.allImages = [];
                    state._seenKeys = new Set();
                    state.filteredImages = [];
                    state.filteredMatchCount = 0;
                    state.offset = 0;
                    state.cursor = null;
                    state.hasMore = true;
                    state.loadingPage = false;
                    state.lastRenderedGallerySignature = null;
                    state.lastRenderedFilterSignature = null;
                    state.lastRenderedSelectionSignature = null;
                    state.lastRenderedDetailKey = null;
                    _clearPrefetch();
                    galleryGrid.innerHTML = '';
                    updatePagingUi();

                    await loadNextPage({ recomputeFilter: false });
                } finally {
                    if (shouldShowBusy) {
                        finishForegroundBusy('apply-filter');
                    }
                }
            }

            // Server already filtered by search + server-supported structured params.
            // Run computeFilteredImages with empty query to apply client-only structural
            // filters (missingSource, missingData, etc.) without re-running text search.
            state.filteredImages = computeFilteredImages('');
            syncSelectionState();
            renderSelectionState({ force: false });
            return;
        }

        if (state.serverFilterMode) {
            state.serverFilterMode = false;
            state.activeServerFilterSignature = null;
            state.activeServerFilterConfig = null;
            state.filteredMatchCount = 0;
            await resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
            return;
        }

        state.filteredImages = computeFilteredImages(query);
        syncSelectionState();
        renderSelectionState({ force: false });

        if (!anyFiltersActive || !ensureSearchCoverage) {
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
            syncSelectionState();
            renderSelectionState({ force: false });

            await new Promise((resolve) => window.setTimeout(resolve, 0));
        }
    }

    function updatePagingUi() {
        loadMoreBtn.classList.toggle('hidden', state.infiniteEnabled || !state.hasMore);
        if (state.foregroundBusy?.visible && state.foregroundBusy.statusMessage) {
            galleryStatus.textContent = state.foregroundBusy.statusMessage;
        } else if (state.loadingPage) {
            galleryStatus.textContent = 'Loading more images...';
        } else if (!state.hasMore) {
            galleryStatus.textContent = state.allImages.length ? 'Reached end of library.' : '';
        } else {
            galleryStatus.textContent = '';
        }

        scheduleGalleryGridHeightSync();
    }

    /**
     * Cancel any in-flight prefetch and clear cached result.
     */
    function _clearPrefetch() {
        if (state._prefetchAbortController) {
            state._prefetchAbortController.abort();
            state._prefetchAbortController = null;
        }
        state._prefetchResult = null;
        state._prefetchCursor = null;
        state._prefetchSignature = null;
    }

    /**
     * Kick off a background fetch for the next page using the given cursor.
     * The result is stored in state._prefetchResult for loadNextPage to
     * consume instantly on the next call.
     */
    function _schedulePrefetch(nextCursor) {
        // Don't prefetch when there are no more pages.
        if (nextCursor == null || !state.hasMore) {
            return;
        }
        // Don't replace an existing prefetch for the same cursor.
        if (state._prefetchResult && state._prefetchCursor === nextCursor) {
            return;
        }
        // Cancel any previous in-flight prefetch.
        _clearPrefetch();

        const sig = state.activeServerFilterSignature;
        const controller = new AbortController();
        state._prefetchAbortController = controller;
        state._prefetchCursor = nextCursor;
        state._prefetchSignature = sig;

        // Capture the offset at prefetch time so toClientImage indices are correct.
        const prefetchOffset = state.offset;

        const { url: queryUrl, body: queryBody } = buildQueryRequestPost(prefetchOffset, state.pageSize, nextCursor);
        fetch(queryUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(queryBody),
            cache: 'no-store',
            signal: controller.signal,
        })
            .then((response) => {
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })
            .then((result) => {
                // Only cache if the filter hasn't changed and the cursor still matches.
                if (
                    state.activeServerFilterSignature !== sig ||
                    state._prefetchCursor !== nextCursor
                ) {
                    return; // stale — discard
                }
                const pageImages = Array.isArray(result.images)
                    ? result.images.map((img, idx) => toClientImage(img, prefetchOffset + idx))
                    : [];
                const pageInfo = result.page || {};
                state._prefetchResult = {
                    page: pageImages,
                    filteredCount: pageInfo.total != null ? String(pageInfo.total) : null,
                    nextCursor: pageInfo.next_cursor != null ? String(pageInfo.next_cursor) : null,
                    hasMore: pageInfo.has_more != null ? Boolean(pageInfo.has_more) : null,
                };
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                // Silently discard — loadNextPage will just do a live fetch.
                console.warn('[prefetch] failed:', err.message);
            });
    }

    async function loadNextPage(options = {}) {
        const recomputeFilter = options.recomputeFilter !== false;
        if (state.loadingPage || !state.hasMore) {
            return;
        }

        state.loadingPage = true;
        updatePagingUi();

        // Capture the filter signature before the fetch so we can detect if a
        // concurrent applyFilter() call changed the active filter while the
        // request was in-flight.  When that happens the response is stale and
        // must be discarded to avoid mixing unfiltered/differently-filtered
        // images into the active result set.
        const filterSignatureAtRequest = state.activeServerFilterSignature;

        try {
            let normalizedPage;
            let filteredCountHeader;
            let nextCursorHeader;
            let _hasMoreFromServer = null;

            // Check for a usable prefetch result first.
            const prefetchUsable =
                state._prefetchResult &&
                state._prefetchSignature === filterSignatureAtRequest &&
                state._prefetchCursor === state.cursor;

            if (prefetchUsable) {
                // Consume the prefetch instantly — no network wait.
                const cached = state._prefetchResult;
                state._prefetchResult = null;
                state._prefetchCursor = null;
                state._prefetchSignature = null;
                normalizedPage = cached.page;
                filteredCountHeader = cached.filteredCount;
                nextCursorHeader = cached.nextCursor;
                _hasMoreFromServer = cached.hasMore;
            } else {
                // Discard stale prefetch.
                _clearPrefetch();

                const { url: queryUrl, body: queryBody } = buildQueryRequestPost(state.offset, state.pageSize, state.cursor);
                const response = await fetch(queryUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(queryBody),
                    cache: 'no-store',
                });

                // Stale response — the filter changed while we were fetching.
                if (state.activeServerFilterSignature !== filterSignatureAtRequest) {
                    return;
                }

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }

                const result = await response.json();
                const pageImages = Array.isArray(result.images)
                    ? result.images.map((img, idx) => toClientImage(img, state.offset + idx))
                    : [];
                const pageInfo = result.page || {};
                filteredCountHeader = pageInfo.total != null ? String(pageInfo.total) : null;
                nextCursorHeader = pageInfo.next_cursor != null ? String(pageInfo.next_cursor) : null;
                _hasMoreFromServer = pageInfo.has_more != null ? Boolean(pageInfo.has_more) : null;
                normalizedPage = pageImages;
            }

            if (state.serverFilterMode) {
                state.filteredMatchCount = Number(filteredCountHeader || normalizedPage.length) || 0;
            }

            // Deduplicate by __key before concat — defense in depth against
            // any residual duplicate gallery_item_key from the backend.
            if (!state._seenKeys) state._seenKeys = new Set();
            for (const img of state.allImages) state._seenKeys.add(img.__key);
            const dedupedPage = normalizedPage.filter(img => !state._seenKeys.has(img.__key));
            for (const img of dedupedPage) state._seenKeys.add(img.__key);

            state.allImages = state.allImages.concat(dedupedPage);
            state.offset += dedupedPage.length;

            // Cursor-based pagination: prefer server's explicit has_more flag
            // when available, otherwise fall back to cursor-presence heuristic.
            if (nextCursorHeader != null) {
                state.cursor = Number(nextCursorHeader);
                state.hasMore = _hasMoreFromServer !== null ? _hasMoreFromServer : true;
            } else if (state.cursor != null) {
                // No next cursor returned — this was the last page.
                state.cursor = null;
                state.hasMore = false;
            } else if (state.serverFilterMode) {
                state.hasMore = state.offset < state.filteredMatchCount;
            } else if (normalizedPage.length < state.pageSize) {
                state.hasMore = false;
            }

            // Safety: if the server claims more data but this page yielded zero
            // new images (after dedup), the pagination cursor is stuck in a loop.
            // Force-stop to avoid infinite load/render jitter.
            if (state.hasMore && dedupedPage.length === 0 && state.allImages.length > 0) {
                state.hasMore = false;
                state.cursor = null;
            }

            if (!state.selectedKey && state.selectedKeys.size === 0 && state.allImages.length) {
                assignSingleSelection(state.allImages[0].__key);
            }

            // After successfully loading a page, prefetch the next one
            // in the background so the next loadNextPage() is instant.
            if (state.hasMore && state.cursor != null) {
                _schedulePrefetch(state.cursor);
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
            fetch('/api/artists/'),
            fetch('/api/licenses/'),
            fetchCollections(),
        ]);

        const artists = await artistsRes.json();
        const licenses = await licensesRes.json();

        state.artistNames = artists
            .flatMap((artist) => {
                if (!artist || typeof artist.name !== 'string') return [];
                const names = [artist.name];
                // Include original name for deleted CivitAI users so they're findable
                if (artist.civitai_user_deleted && artist.civitai_user_original_name) {
                    names.push(artist.civitai_user_original_name);
                }
                return names;
            })
            .filter((name) => name.length > 0);

        artistDatalist.innerHTML = '';
        artists.forEach((artist) => {
            const option = document.createElement('option');
            option.value = artist.name;
            if (artist.civitai_user_deleted) {
                const orig = artist.civitai_user_original_name || artist.name;
                option.textContent = `🗑 ${orig}`;
            }
            artistDatalist.appendChild(option);
            // Also add original name as a separate option for deleted users
            if (artist.civitai_user_deleted && artist.civitai_user_original_name) {
                const origOption = document.createElement('option');
                origOption.value = artist.civitai_user_original_name;
                origOption.textContent = `🗑 ${artist.civitai_user_original_name}`;
                artistDatalist.appendChild(origOption);
            }
            // For deleted users, also add the civitai user ID as a searchable alias
            if (artist.civitai_user_deleted && artist.civitai_user_id) {
                const idOption = document.createElement('option');
                idOption.value = String(artist.civitai_user_id);
                idOption.textContent = `🗑 CivitAI user #${artist.civitai_user_id}`;
                artistDatalist.appendChild(idOption);
            }
        });

        licenses.forEach((license) => {
            const option = document.createElement('option');
            option.value = license.id;
            option.textContent = `${license.short_name} - ${license.name}`;
            licenseSelect.appendChild(option);
        });

        state.collections = collections;
        syncCollectionSelect();
        renderAdvancedFilters();
    }

    async function hydrateFilterOptionsInBackground() {
        try {
            const filterOptions = await fetchFilterOptions();
            state.filterOptions = filterOptions;
            renderAdvancedFilters();
        } catch (error) {
            // Keep startup resilient; this can be retried via page refresh.
            console.warn('Failed to hydrate filter options in background:', error);
        }
    }

    async function resetAndLoadImages(options = {}) {
        const preserveSelection = options.preserveSelection === true;
        const showRefreshUi = options.showRefreshUi !== false;
        const previousSelectedKey = preserveSelection
            ? (state.fullscreenSelectedKey || state.selectedKey)
            : null;
        const previousSelectedKeys = preserveSelection
            ? (() => {
                const keys = new Set(state.selectedKeys);
                if (previousSelectedKey) {
                    keys.add(previousSelectedKey);
                }
                return Array.from(keys);
            })()
            : [];
        const previousSelectionAnchorKey = preserveSelection ? state.lastSelectionAnchorKey : null;

        if (showRefreshUi) {
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Refreshing...';
        }

        state.galleryRefreshInFlight = true;
        state.allImages = [];
        state._seenKeys = new Set();
        state.filteredImages = [];
        state.filteredMatchCount = 0;
        state.totalImageCount = 0;
        state.selectedKey = preserveSelection ? previousSelectedKey : null;
        state.selectedKeys = preserveSelection ? new Set(previousSelectedKeys) : new Set();
        state.lastSelectionAnchorKey = preserveSelection ? previousSelectionAnchorKey : null;
        state.serverFilterMode = false;
        state.activeServerFilterSignature = null;
        state.activeServerFilterConfig = null;
        state.offset = 0;
        state.cursor = null;
        state.hasMore = true;
        state.loadingPage = false;
        state.lastRenderedGallerySignature = null;
        state.lastRenderedFilterSignature = null;
        state.lastRenderedSelectionSignature = null;
        state.lastRenderedDetailKey = null;
        _clearPrefetch();
        galleryGrid.innerHTML = '';
        updatePagingUi();

        try {
            state.imagesStateSignature = await fetchImagesStateSignature();
            await loadNextPage({ recomputeFilter: false });
            await applyFilter({ ensureSearchCoverage: true });
            if (preserveSelection && state.selectedKey) {
                const activeTile = galleryGrid.querySelector(`.tile[data-key="${CSS.escape(state.selectedKey)}"]`);
                if (activeTile) {
                    activeTile.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                }
            }
        } catch (error) {
            galleryGrid.innerHTML = `<p>Error loading images: ${error.message}</p>`;
            renderImageCountControl();
            if (fullscreenPreview.classList.contains('hidden')) {
                showDetails(null);
            }
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

            state.galleryRefreshInFlight = false;
            renderSelectionState({ force: true });
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

        const key = tile.dataset.key || null;
        if (!key) {
            return;
        }

        if (event.shiftKey) {
            selectRangeAndRender(key, { additive: event.metaKey || event.ctrlKey });
            return;
        }

        if (event.metaKey || event.ctrlKey) {
            toggleSelectionAndRender(key);
            return;
        }

        const selectedBefore = state.selectedKey;
        setSingleSelectionAndRender(key);
        if (selectedBefore !== key) {
            const selectedImage = getImageByKey(key);
            if (selectedImage) {
                activateDefaultVariant(selectedImage);
                // Update variant badge in-place without full gallery redraw.
                // setSingleSelectionAndRender already called renderSelectionState()
                // which handles tile selection classes and showDetails().
                updateTileVariantBadge(selectedImage);
            }
        }
    });

    if (imageCount) {
        imageCount.addEventListener('click', async (event) => {
            const actionButton = event.target.closest('[data-count-action]');
            if (!actionButton || actionButton.disabled) {
                return;
            }

            const action = actionButton.dataset.countAction;
            if (action === 'select-matches') {
                await selectAllMatchingAndRender();
                return;
            }

            if (action === 'select-catalog') {
                await selectAllCatalogAndRender();
            }
        });
    }

    if (selectionCount) {
        selectionCount.addEventListener('click', (event) => {
            const clearBtn = event.target.closest('.selection-pill-clear');
            if (!clearBtn || !selectionCount.classList.contains('has-selection')) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            clearSelectionAndRender();
        });
    }

    if (treeTagFilterClear) {
        treeTagFilterClear.addEventListener('click', () => {
            state.treeTagFilter = null;
            state.missingSourceFilter = { civitai: false, danbooru: false, prompt: false, user: false };
            renderTreeTagFilterIndicator();
            void applyFilter({ ensureSearchCoverage: true });
        });
    }

    if (advancedFiltersClearBtn) {
        advancedFiltersClearBtn.addEventListener('click', async () => {
            clearAdvancedFilters();
            // Provide immediate visual feedback before async filtering.
            renderAdvancedFilters();
            await applyFilter({ ensureSearchCoverage: true });
            showToast('Cleared advanced filters.', 'info');
        });
    }

    wireAdvancedFilterInput(advancedAuthorInput, advancedAuthorAddBtn, 'artistName', 'artist');
    wireAdvancedFilterInput(advancedTagInput, advancedTagAddBtn, 'tags', 'tag');
    wireAdvancedFilterInput(advancedCollectionInput, advancedCollectionAddBtn, 'collections', 'collection');
    wireDynamicTagDatalist(advancedTagInput, advancedTagOptions);

    searchInput.addEventListener('input', () => {
        if (searchDebounceTimer !== null) {
            window.clearTimeout(searchDebounceTimer);
        }
        if (suggestDebounceTimer !== null) {
            window.clearTimeout(suggestDebounceTimer);
        }

        // Cancel in-flight progressive-search loops started by previous input.
        state.searchRunId += 1;
        suggestRunId += 1;

        const text = (searchInput.value || '').trim();
        const delayMs = getSearchDebounceMs(text);

        if (!text) {
            hideAutocomplete();
            // Still fire applyFilter immediately to clear active search
            void applyFilter({ ensureSearchCoverage: true });
            return;
        }

        const fireSearch = () => {
            searchDebounceTimer = null;
            void applyFilter({ ensureSearchCoverage: true });
        };

        const fireSuggest = () => {
            suggestDebounceTimer = null;
            const myRunId = ++suggestRunId;
            showAutocompleteSpinner();
            fetchSuggestions(text).then((results) => {
                if (suggestRunId !== myRunId) return; // stale
                renderAutocomplete(results, text);
            }).catch(() => {
                if (suggestRunId !== myRunId) return;
                hideAutocomplete();
            });
        };

        if (delayMs === 0) {
            fireSearch();
            fireSuggest();
        } else {
            searchDebounceTimer = window.setTimeout(fireSearch, delayMs);
            suggestDebounceTimer = window.setTimeout(fireSuggest, delayMs);
        }
    });

    searchInput.addEventListener('keydown', (e) => {
        if (!searchAutocomplete || searchAutocomplete.classList.contains('hidden')) {
            return;
        }
        const items = searchAutocomplete.querySelectorAll('.search-ac-item');
        if (items.length === 0) return;

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            focusAutocompleteItem(acFocusedIndex + 1);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            focusAutocompleteItem(acFocusedIndex - 1);
        } else if (e.key === 'Enter' && acFocusedIndex >= 0 && acFocusedIndex < items.length) {
            e.preventDefault();
            items[acFocusedIndex].click();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            hideAutocomplete();
        }
    });

    searchInput.addEventListener('blur', () => {
        // Small delay to allow mousedown on autocomplete items to register
        window.setTimeout(() => hideAutocomplete(), 150);
    });
    detailImage.addEventListener('load', () => {
        if (!currentDebugImage) {
            return;
        }
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            ...getVariantFilenameDebug(currentDebugImage),
            url: detailImage.currentSrc || getImageUrl(currentDebugImage),
            status: 'loaded',
            natural_size: `${detailImage.naturalWidth}x${detailImage.naturalHeight}`,
            ...getImageLayoutDebug(),
        });
    });
    detailImage.addEventListener('click', (event) => {
        console.log('detailImage clicked', { hasImage: !!getSelectedImage(), isHidden: detailImage.classList.contains('hidden') });
        const image = getSelectedImage();
        if (!image || detailImage.classList.contains('hidden')) {
            return;
        }
        openFullscreenPreviewFromImage(image);
    });
    wireImageDragPayload(detailImage, () => getSelectedImage());
    wireImageDragPayload(fullscreenImage, () => {
        const fullscreenImageItem = state.fullscreenSelectedKey
            ? getImageByKey(state.fullscreenSelectedKey)
            : null;
        return fullscreenImageItem || getSelectedImage();
    });
    detailImage.addEventListener('error', () => {
        if (!currentDebugImage) {
            return;
        }
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            ...getVariantFilenameDebug(currentDebugImage),
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
            ...getVariantFilenameDebug(currentDebugImage),
            url: detailVideo.currentSrc || getImageUrl(currentDebugImage),
            status: 'video-loadedmetadata',
            video_natural: `${detailVideo.videoWidth}x${detailVideo.videoHeight}`,
            ...getImageLayoutDebug(detailVideo),
        });
        scheduleGalleryGridHeightSync();
    });
    
    // Hide error UI when video successfully loads
    detailVideo.addEventListener('loadeddata', () => {
        detailVideoError.classList.add('hidden');
    });
    
    detailVideo.addEventListener('error', () => {
        if (!currentDebugImage) {
            return;
        }
        const failingUrl = detailVideo.currentSrc || getMediaUrlForDisplay(currentDebugImage) || '';
        
        // Show error UI for video files
        if (!looksLikeImageUrl(failingUrl)) {
            detailVideo.classList.add('hidden');
            detailVideo.style.display = 'none';
            
            // Show error UI with download link and retry button
            detailVideoError.classList.remove('hidden');
            detailVideoDownload.href = failingUrl;
            detailVideoDownload.style.display = 'inline-flex';
            
            setDebugBadge({
                key: currentDebugImage.__key,
                file_hash: currentDebugImage.file_hash,
                file_path: currentDebugImage.file_path,
                ...getVariantFilenameDebug(currentDebugImage),
                url: failingUrl || getImageUrl(currentDebugImage),
                status: 'video-error',
                ...getImageLayoutDebug(detailVideo),
            });
            return;
        }
        
        // For image URLs that failed to load as video, try loading as image
        detailVideo.classList.add('hidden');
        detailVideo.style.display = 'none';
        releaseVideoElement(detailVideo);
        detailImage.classList.remove('hidden');
        detailImage.style.display = 'block';
        detailImage.style.visibility = 'visible';
        detailImage.style.opacity = '1';
        detailImage.src = failingUrl;
        setDebugBadge({
            key: currentDebugImage.__key,
            file_hash: currentDebugImage.file_hash,
            file_path: currentDebugImage.file_path,
            ...getVariantFilenameDebug(currentDebugImage),
            url: failingUrl,
            status: 'video-fallback-to-image',
            ...getImageLayoutDebug(detailImage),
        });
    });
    detailVideo.addEventListener('click', () => {
        const image = getSelectedImage();
        if (!image || detailVideo.classList.contains('hidden')) {
            return;
        }
        openFullscreenPreviewFromImage(image);
    });
    
    // Video container click handler for fullscreen (catches clicks on video controls)
    // Only handle clicks when video is visible - image clicks are handled by detailImage.addEventListener
    detailMediaFrame.addEventListener('click', (event) => {
        console.log('detailMediaFrame clicked', { target: event.target.tagName, videoHidden: detailVideo.classList.contains('hidden') });
        const image = getSelectedImage();
        if (!image) {
            return;
        }
        // Only handle video clicks - image clicks are handled by detailImage listener
        if (detailVideo.classList.contains('hidden')) {
            return;
        }
        // Check if the click was on the video element or its container
        // but not on the error overlay or other buttons
        if (event.target.closest('#detail-video-error') ||
            event.target.closest('button') ||
            event.target.closest('a')) {
            return;
        }
        openFullscreenPreviewFromImage(image);
    });
    
    // Video error UI event handlers
    detailVideoRetry.addEventListener('click', () => {
        // Hide error UI and retry loading video
        detailVideoError.classList.add('hidden');
        detailVideo.classList.remove('hidden');
        detailVideo.style.display = 'block';
        
        // Reload the video
        const videoUrl = detailVideo.src;
        detailVideo.load();
        
        setDebugBadge({
            key: currentDebugImage?.__key || 'unknown',
            status: 'video-retry',
            url: videoUrl,
        });
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
    if (fullscreenDebugFreezeBtn) {
        fullscreenDebugFreezeBtn.addEventListener('click', () => {
            fullscreenDebugFrozen = !fullscreenDebugFrozen;
            fullscreenDebugFreezeBtn.textContent = fullscreenDebugFrozen ? 'Unfreeze' : 'Freeze';
            fullscreenDebugFreezeBtn.setAttribute('aria-pressed', fullscreenDebugFrozen ? 'true' : 'false');
            updateFullscreenDebugOverlay('debug-freeze-toggle', {
                frozen: fullscreenDebugFrozen,
            });
        });
    }
    if (fullscreenDebugCopyBtn) {
        fullscreenDebugCopyBtn.addEventListener('click', async () => {
            const liveText = fullscreenDebugContent ? String(fullscreenDebugContent.textContent || '').trim() : '';
            const snapshotText = fullscreenDebugSnapshot ? String(fullscreenDebugSnapshot.textContent || '').trim() : '';
            const bundle = [
                'LIVE:',
                liveText || '{}',
                '',
                'INDEX_LOSS_SNAPSHOT:',
                snapshotText || '{}',
            ].join('\n');
            try {
                if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
                    await navigator.clipboard.writeText(bundle);
                    updateFullscreenDebugOverlay('debug-copy', { copied: true });
                    return;
                }
            } catch {
                // fall through
            }
            updateFullscreenDebugOverlay('debug-copy', { copied: false, reason: 'clipboard_unavailable' });
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
            return;
        }
        if (event.key === ' ') {
            event.preventDefault();
            toggleFullscreenSelection();
            return;
        }
    });
    galleryGrid.addEventListener('scroll', () => {
        if (!state.infiniteEnabled || !state.hasMore || state.loadingPage) {
            return;
        }

        const distanceToBottom = galleryGrid.scrollHeight - galleryGrid.scrollTop - galleryGrid.clientHeight;
        // Trigger early (~3 viewport heights from bottom) so prefetched data is
        // rendered into the DOM before the user scrolls far enough to see the gap.
        const earlyTrigger = galleryGrid.clientHeight * 3;
        if (distanceToBottom < Math.max(earlyTrigger, 240)) {
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
    if (variantGroupingToggle) {
        variantGroupingToggle.addEventListener('change', async () => {
            state.groupVariantsEnabled = variantGroupingToggle.checked;
            writeStoredBool(STORAGE_KEYS.groupVariants, state.groupVariantsEnabled);
            await resetAndLoadImages();
        });
    }
    debugToggle.addEventListener('change', () => {
        state.debugVisible = debugToggle.checked;
        writeStoredBool(STORAGE_KEYS.debug, state.debugVisible);
        syncLayoutMode();
        debugBadge.classList.toggle('hidden', !state.debugVisible || !currentDebugImage);
        updateFullscreenDebugOverlay('debug-toggle', { debugVisible: state.debugVisible });
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
        const images = getCollectionActionTargets();
        if (!images.length) {
            return;
        }

        const collectionId = Number(collectionSelect.value);
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection first.');
            return;
        }

        try {
            const result = await addImagesToCollection(collectionId, images);

            const selectedCollection = state.collections.find((c) => c.id === collectionId);
            if (!selectedCollection) {
                return;
            }

            updateImagesWithCollectionAddition(images, selectedCollection);
            const activeImage = getSelectedImage();
            if (activeImage) {
                renderImageCollections(activeImage);
            }
            await applyFilter({ ensureSearchCoverage: true });
            showToast(`Added ${result.added_count || 0} item${Number(result.added_count || 0) === 1 ? '' : 's'} to ${selectedCollection.name}.`, 'success');
        } catch (error) {
            alert(`Could not add items to collection: ${error.message}`);
        }
    });
    if (removeFromCollectionBtn) {
        removeFromCollectionBtn.addEventListener('click', async () => {
            const images = getCollectionActionTargets();
            if (!images.length) {
                return;
            }

            const collectionId = Number(collectionSelect.value);
            if (!Number.isInteger(collectionId) || collectionId <= 0) {
                alert('Select a collection first.');
                return;
            }

            const selectedCollection = state.collections.find((c) => c.id === collectionId);
            const collectionName = selectedCollection?.name || `collection ${collectionId}`;

            try {
                const result = await removeImagesFromCollection(collectionId, images);
                updateImagesWithCollectionRemoval(images, collectionId);
                const activeImage = getSelectedImage();
                if (activeImage) {
                    renderImageCollections(activeImage);
                }
                await applyFilter({ ensureSearchCoverage: true });
                showToast(`Removed ${result.removed_count || 0} item${Number(result.removed_count || 0) === 1 ? '' : 's'} from ${collectionName}.`, 'success');
            } catch (error) {
                alert(`Could not remove items from collection: ${error.message}`);
            }
        });
    }

    function beginButtonBusyState(button, busyLabel = '...', hasTargetsFn = null) {
        const previousHtml = button.innerHTML;
        button.disabled = true;
        button.textContent = busyLabel;
        return () => {
            button.innerHTML = previousHtml;
            const hasTargets = typeof hasTargetsFn === 'function'
                ? Boolean(hasTargetsFn())
                : Boolean(getSelectedImage());
            button.disabled = !hasTargets;
        };
    }

    repairImageBtn.addEventListener('click', async () => {
        const images = getImageToolActionTargets();
        if (!images.length) {
            return;
        }

        const targetCount = images.length;
        const noun = targetCount === 1 ? 'media item' : 'media items';
        if (!window.confirm(`Run repair for ${targetCount} selected ${noun}? This checks metadata mismatches, rebuilds sidecar/resources, and replaces files only when needed.`)) {
            return;
        }

        const endRepairBusyState = beginButtonBusyState(
            repairImageBtn,
            '...',
            () => getImageToolActionTargets().length > 0,
        );
        try {
            const successes = [];
            const failures = [];

            for (const image of images) {
                try {
                    const response = await fetch(`/api/images/${encodeURIComponent(getEditableFileHash(image))}/repair`, {
                        method: 'POST',
                    });
                    const result = await response.json();
                    if (!response.ok) {
                        throw new Error(result.detail || `HTTP ${response.status}`);
                    }
                    successes.push({ image, result });
                } catch (error) {
                    failures.push({ image, message: error.message });
                }
            }

            await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
            const focusTarget = successes[successes.length - 1];
            if (focusTarget) {
                await focusImageByHash(focusTarget.result.repaired_file_hash || focusTarget.image.file_hash);
            }

            const lines = [
                `Repair completed for ${successes.length}/${images.length} selected item${images.length === 1 ? '' : 's'}.`,
            ];

            if (failures.length) {
                lines.push('');
                lines.push('Failures:');
                failures.slice(0, 10).forEach(({ image, message }) => {
                    lines.push(`- ${image.file_name || getEditableFileHash(image)}: ${message}`);
                });
                if (failures.length > 10) {
                    lines.push(`- ...and ${failures.length - 10} more`);
                }
            }

            const createdCount = successes.filter(({ result }) => result.created_new_image).length;
            if (createdCount > 0) {
                lines.push('');
                lines.push(`Created repaired copies: ${createdCount}`);
            }

            alert(lines.join('\n'));
        } catch (error) {
            alert(`Could not repair image: ${error.message}`);
        } finally {
            endRepairBusyState();
        }
    });
    if (rescanImageBtn) {
        rescanImageBtn.addEventListener('click', async () => {
            const images = getImageToolActionTargets();
            if (!images.length) {
                return;
            }

            const targetCount = images.length;
            const noun = targetCount === 1 ? 'media item' : 'media items';
            if (!window.confirm(`Rescan metadata for ${targetCount} selected ${noun}? This reruns single-file hydration (EXIF, generation software, CivitAI enrichment, and sidecar normalization).`)) {
                return;
            }

            const endRescanBusyState = beginButtonBusyState(
                rescanImageBtn,
                '...',
                () => getImageToolActionTargets().length > 0,
            );
            try {
                const successes = [];
                const failures = [];

                for (const image of images) {
                    try {
                        const response = await fetch(`/api/images/${encodeURIComponent(getEditableFileHash(image))}/rescan`, {
                            method: 'POST',
                        });
                        const result = await response.json();
                        if (!response.ok) {
                            throw new Error(result.detail || `HTTP ${response.status}`);
                        }
                        const targetHash = String(result.file_hash || getEditableFileHash(image) || '').trim();
                        if (targetHash) {
                            state.generationPrototypeCache.delete(targetHash);
                            state.generationPrototypeInflight.delete(targetHash);
                        }
                        successes.push({ image, result });
                    } catch (error) {
                        failures.push({ image, message: error.message });
                    }
                }

                await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });
                const focusTarget = successes[successes.length - 1];
                if (focusTarget) {
                    await focusImageByHash(focusTarget.result.file_hash || getEditableFileHash(focusTarget.image));
                }

                const lines = [
                    `Rescan completed for ${successes.length}/${images.length} selected item${images.length === 1 ? '' : 's'}.`,
                ];

                if (failures.length) {
                    lines.push('');
                    lines.push('Failures:');
                    failures.slice(0, 10).forEach(({ image, message }) => {
                        lines.push(`- ${image.file_name || getEditableFileHash(image)}: ${message}`);
                    });
                    if (failures.length > 10) {
                        lines.push(`- ...and ${failures.length - 10} more`);
                    }
                }

                alert(lines.join('\n'));
            } catch (error) {
                alert(`Could not rescan image metadata: ${error.message}`);
            } finally {
                endRescanBusyState();
            }
        });
    }
    if (sendToGenerationLabBtn) {
        sendToGenerationLabBtn.addEventListener('click', () => {
            const href = String(sendToGenerationLabBtn.dataset.href || '').trim();
            if (!href) {
                showToast('No Generation Metadata Lab destination is available for this item.', 'warn');
                return;
            }
            window.open(href, '_blank', 'noopener');
        });
    }
    if (sendToPerceptualLabBtn) {
        sendToPerceptualLabBtn.addEventListener('click', () => {
            const href = String(sendToPerceptualLabBtn.dataset.href || '').trim();
            if (!href) {
                showToast('No Perceptual Analyzer Lab destination is available for this item.', 'warn');
                return;
            }
            window.open(href, '_blank', 'noopener');
        });
    }
    if (sendToModelLabBtn) {
        sendToModelLabBtn.addEventListener('click', () => {
            const href = String(sendToModelLabBtn.dataset.href || '').trim();
            if (!href) {
                showToast('No Model Reference Lab destination is available for this item.', 'warn');
                return;
            }
            window.open(href, '_blank', 'noopener');
        });
    }

    deleteImageFileBtn.addEventListener('click', async () => {
        const images = getImageToolActionTargets();
        if (!images.length) {
            return;
        }

        const previewLines = images.slice(0, 6).map((image) => `- ${image.file_name || getEditableFileHash(image)}`);
        if (images.length > 6) {
            previewLines.push(`- ...and ${images.length - 6} more`);
        }

        const message = [
            `Mark ${images.length} selected item${images.length === 1 ? '' : 's'} as deleted?`,
            '',
            'Files will be preserved on disk until you run Trash Purge.',
            'Selected items will be hidden from the gallery.',
            '',
            'Selected files:',
            ...previewLines,
        ].join('\n');
        if (!window.confirm(message)) {
            return;
        }

        deleteImageFileBtn.disabled = true;
        try {
            const successes = [];
            const failures = [];

            for (const image of images) {
                try {
                    const response = await fetch(`/api/images/${encodeURIComponent(getEditableFileHash(image))}/file`, {
                        method: 'DELETE',
                    });
                    const result = await response.json();
                    if (!response.ok) {
                        throw new Error(result.detail || `HTTP ${response.status}`);
                    }
                    successes.push({ image, result });
                } catch (error) {
                    failures.push({ image, message: error.message });
                }
            }

            await resetAndLoadImages({ preserveSelection: false, showRefreshUi: false });

            const lines = [
                `Marked ${successes.length}/${images.length} selected item${images.length === 1 ? '' : 's'} as deleted.`,
            ];
            if (failures.length) {
                lines.push('');
                lines.push('Failures:');
                failures.slice(0, 10).forEach(({ image, message }) => {
                    lines.push(`- ${image.file_name || getEditableFileHash(image)}: ${message}`);
                });
                if (failures.length > 10) {
                    lines.push(`- ...and ${failures.length - 10} more`);
                }
            }

            alert(lines.join('\n'));
        } catch (error) {
            alert(`Could not delete image: ${error.message}`);
        } finally {
            deleteImageFileBtn.disabled = getImageToolActionTargets().length <= 0;
        }
    });
    createCollectionBtn.addEventListener('click', async () => {
        const name = newCollectionNameInput.value.trim();
        if (!name) {
            alert('Enter a collection name first.');
            return;
        }

        try {
            const response = await fetch('/api/collections/', {
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
            const targets = getCollectionActionTargets();
            if (targets.length) {
                await addImagesToCollection(result.id, targets);
                updateImagesWithCollectionAddition(targets, result);
                const activeImage = getSelectedImage();
                if (activeImage) {
                    renderImageCollections(activeImage);
                }
                await applyFilter({ ensureSearchCoverage: true });
                showToast(`Created ${result.name} and added ${targets.length} selected item${targets.length === 1 ? '' : 's'}.`, 'success');
                return;
            }
            showToast(`Created collection ${result.name}.`, 'success');
        } catch (error) {
            alert(`Could not create collection: ${error.message}`);
        }
    });
    renameCollectionBtn.addEventListener('click', async () => {
        const collectionId = Number(collectionSelect.value);
        const name = renameCollectionNameInput.value.trim();
        const previousCollectionName = state.collections.find((collection) => collection.id === collectionId)?.name || null;
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection to rename.');
            return;
        }
        if (!name) {
            alert('Enter a new collection name first.');
            return;
        }

        try {
            const response = await fetch(`/api/collections/${collectionId}`, {
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
            state.advancedFilters.collections = getAdvancedFilterValues('collections').map((value) => {
                return normalizeDetailFilterValue(value) === normalizeDetailFilterValue(previousCollectionName)
                    ? result.name
                    : value;
            });
            await applyFilter({ ensureSearchCoverage: true });
        } catch (error) {
            alert(`Could not rename collection: ${error.message}`);
        }
    });
    deleteCollectionBtn.addEventListener('click', async () => {
        const collectionId = Number(collectionSelect.value);
        const deletedCollectionName = state.collections.find((collection) => collection.id === collectionId)?.name || null;
        if (!Number.isInteger(collectionId) || collectionId <= 0) {
            alert('Select a collection to delete.');
            return;
        }

        if (!window.confirm('Delete this collection? This removes membership links but keeps images.')) {
            return;
        }

        try {
            const response = await fetch(`/api/collections/${collectionId}`, {
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
            if (deletedCollectionName) {
                state.advancedFilters.collections = getAdvancedFilterValues('collections')
                    .filter((value) => normalizeDetailFilterValue(value) !== normalizeDetailFilterValue(deletedCollectionName));
            }
            await applyFilter({ ensureSearchCoverage: true });
        } catch (error) {
            alert(`Could not delete collection: ${error.message}`);
        }
    });

    scanBtn.addEventListener('click', async () => {
        scanBtn.disabled = true;
        scanOutput.textContent = 'Scanning library...';
        try {
            const response = await fetch('/api/scan_library/', { method: 'POST' });
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
            const response = await fetch('/api/utilities/purge_deleted_files', {
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
            const response = await fetch('/api/upload_images/', {
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
        const webBase = window.__ATELIER_CONFIG?.civitai_web_base_url || 'https://civitai.red';
        if (importTypeSelect.value === 'image') {
            importValueInput.placeholder = `${webBase}/images/... or 123456`;
        } else {
            importValueInput.placeholder = `${webBase}/collections/... or 123456`;
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

        importOutput.textContent = 'Queueing CivitAI import task...';
        submitButton.disabled = true;
        if (syncCivitaiCollectionsBtn) {
            syncCivitaiCollectionsBtn.disabled = true;
        }

        try {
            const response = await fetch('/api/import_civitai/', {
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

            if (result && result.task && result.task.id) {
                state.highlightedTaskId = result.task.id;
            }
            importOutput.textContent = JSON.stringify(result, null, 2);
            importForm.reset();
            importTypeSelect.value = importType;
            updateImportInputPlaceholder();
            await refreshTasks();
            showToast('CivitAI import task queued.', 'info');
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

            importOutput.textContent = 'Queueing CivitAI collection sync task...';
            syncCivitaiCollectionsBtn.disabled = true;
            if (importSubmitBtn) {
                importSubmitBtn.disabled = true;
            }

            try {
                const response = await fetch('/api/collections/sync/civitai', {
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

                if (result && result.task && result.task.id) {
                    state.highlightedTaskId = result.task.id;
                }
                importOutput.textContent = JSON.stringify(result, null, 2);
                await refreshTasks();
                showToast('CivitAI collection sync task queued.', 'info');
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

    if (refreshTasksBtn) {
        refreshTasksBtn.addEventListener('click', () => {
            void refreshTasks();
        });
    }

    if (taskRetryFailedBtn) {
        taskRetryFailedBtn.addEventListener('click', async () => {
            const taskId = String(taskRetryFailedBtn.dataset.taskId || '').trim();
            if (!taskId) {
                return;
            }

            taskRetryFailedBtn.disabled = true;
            try {
                await queueRetryFailedItems(taskId);
            } catch (error) {
                showToast(`Could not queue retry task: ${error.message}`, 'warn');
                taskRetryFailedBtn.disabled = false;
            }
        });
    }

    if (taskRetryMissingBtn) {
        taskRetryMissingBtn.addEventListener('click', async () => {
            const taskId = String(taskRetryMissingBtn.dataset.taskId || '').trim();
            if (!taskId) {
                return;
            }

            taskRetryMissingBtn.disabled = true;
            try {
                const result = await retryMissingItems(taskId);
                if (result && result.task && result.task.id) {
                    state.highlightedTaskId = result.task.id;
                }
                await refreshTasks();
                showToast('Retry task queued for missing items.', 'info');
            } catch (error) {
                showToast(`Could not queue retry for missing items: ${error.message}`, 'warn');
                taskRetryMissingBtn.disabled = false;
            }
        });
    }

    if (taskRetryTemporaryBtn) {
        taskRetryTemporaryBtn.addEventListener('click', async () => {
            const taskId = String(taskRetryTemporaryBtn.dataset.taskId || '').trim();
            if (!taskId) {
                return;
            }

            taskRetryTemporaryBtn.disabled = true;
            try {
                const result = await retryTemporaryItems(taskId);
                if (result && result.task && result.task.id) {
                    state.highlightedTaskId = result.task.id;
                }
                await refreshTasks();
                showToast('Retry task queued for temporary failures.', 'info');
            } catch (error) {
                showToast(`Could not queue retry for temporary failures: ${error.message}`, 'warn');
                taskRetryTemporaryBtn.disabled = false;
            }
        });
    }

    // ── CivitAI Authentication UI ──────────────────────────────────────
    const civitaiAuthDetails = document.getElementById('civitai-auth-details');
    const civitaiAuthStatusIcon = document.getElementById('civitai-auth-status-icon');
    const civitaiAuthStatusText = document.getElementById('civitai-auth-status-text');
    const civitaiAuthVerifyBtn = document.getElementById('civitai-auth-verify-btn');
    const civitaiCookieInput = document.getElementById('civitai-cookie-input');
    const civitaiCookieSaveBtn = document.getElementById('civitai-cookie-save-btn');
    const civitaiAuthRefreshBtn = document.getElementById('civitai-auth-refresh-btn');

    function setCivitaiAuthStatus(state, message) {
        if (civitaiAuthStatusIcon) {
            civitaiAuthStatusIcon.textContent = state === 'ok' ? '🟢' : state === 'fail' ? '🔴' : state === 'loading' ? '🔄' : '⚪';
        }
        if (civitaiAuthStatusText) {
            civitaiAuthStatusText.textContent = message;
            civitaiAuthStatusText.className = 'civitai-auth-status-text' +
                (state === 'ok' ? ' auth-ok' : state === 'fail' ? ' auth-fail' : '');
        }
    }

    async function checkCivitaiAuthStatus() {
        setCivitaiAuthStatus('loading', 'Checking connection…');
        try {
            const resp = await fetch('/api/civitai/auth/status');
            const data = await resp.json();
            if (data.authenticated) {
                setCivitaiAuthStatus('ok', data.message || 'Authenticated');
            } else {
                setCivitaiAuthStatus('fail', data.message || 'Not authenticated');
            }
        } catch (err) {
            setCivitaiAuthStatus('fail', 'Could not check auth status');
        }
    }

    function openCivitaiAuthPanel() {
        if (civitaiAuthDetails && !civitaiAuthDetails.open) {
            civitaiAuthDetails.open = true;
        }
    }

    if (civitaiAuthVerifyBtn) {
        civitaiAuthVerifyBtn.addEventListener('click', () => {
            void checkCivitaiAuthStatus();
        });
    }

    if (civitaiCookieSaveBtn) {
        civitaiCookieSaveBtn.addEventListener('click', async () => {
            const cookie = civitaiCookieInput ? civitaiCookieInput.value.trim() : '';
            if (!cookie) {
                showToast('Please paste a cookie value first.', 'warn');
                return;
            }
            civitaiCookieSaveBtn.disabled = true;
            setCivitaiAuthStatus('loading', 'Saving cookie…');
            try {
                const resp = await fetch('/api/civitai/auth/cookie', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cookie }),
                });
                const data = await resp.json();
                if (!resp.ok) {
                    throw new Error(data.detail || `HTTP ${resp.status}`);
                }
                setCivitaiAuthStatus('ok', data.message || 'Cookie saved and validated');
                showToast('CivitAI cookie saved successfully.', 'success');
                civitaiCookieInput.value = '';
            } catch (err) {
                setCivitaiAuthStatus('fail', `Save failed: ${err.message}`);
                showToast(`Cookie save failed: ${err.message}`, 'warn');
            } finally {
                civitaiCookieSaveBtn.disabled = false;
            }
        });
    }

    if (civitaiAuthRefreshBtn) {
        civitaiAuthRefreshBtn.addEventListener('click', async () => {
            civitaiAuthRefreshBtn.disabled = true;
            setCivitaiAuthStatus('loading', 'Launching browser re-authentication (this may take a while)…');
            try {
                const resp = await fetch('/api/civitai/auth/refresh', { method: 'POST' });
                const data = await resp.json();
                if (!resp.ok) {
                    throw new Error(data.detail || `HTTP ${resp.status}`);
                }
                if (data.success === false) {
                    throw new Error(data.error || 'Re-authentication failed');
                }
                setCivitaiAuthStatus('ok', data.message || 'Re-authenticated');
                showToast('CivitAI re-authentication successful.', 'success');
            } catch (err) {
                setCivitaiAuthStatus('fail', `Re-auth failed: ${err.message}`);
                showToast(`Re-authentication failed: ${err.message}`, 'warn');
            } finally {
                civitaiAuthRefreshBtn.disabled = false;
            }
        });
    }

    // Initial auth status check
    void checkCivitaiAuthStatus();

    // ── CivitAI Rate Limit Status ──────────────────────────────────────
    const civitaiRateLimitText = document.getElementById('civitai-rate-limit-text');

    async function refreshCivitaiRateLimit() {
        if (!civitaiRateLimitText) return;
        try {
            const resp = await fetch('/api/civitai/auth/rate-limit-status');
            const data = await resp.json();
            if (!data.available) {
                civitaiRateLimitText.textContent = data.message || 'Rate limit info unavailable';
                return;
            }
            const rpm = data.rpm_window ?? 0;
            const limit = data.rpm_limit ?? '?';
            const total = data.total_requests ?? 0;
            const throttles = data.throttle_count ?? 0;
            const limited429 = data.rate_limited_429 ?? 0;
            const backoff = data.backoff_active ? ` ⏸️ ${data.backoff_remaining_seconds}s backoff` : '';
            const pct = typeof limit === 'number' ? Math.round((rpm / limit) * 100) : '?';
            const bar = '█'.repeat(Math.min(20, Math.round((typeof pct === 'number' ? pct : 0) / 5))) +
                        '░'.repeat(Math.max(0, 20 - Math.min(20, Math.round((typeof pct === 'number' ? pct : 0) / 5))));
            civitaiRateLimitText.textContent =
                `${bar} ${rpm}/${limit} req/min (${pct}%)${backoff}\n` +
                `Total: ${total}  •  Paused: ${throttles}  •  429s: ${limited429}`;
            civitaiRateLimitText.style.whiteSpace = 'pre-line';
        } catch (_err) {
            civitaiRateLimitText.textContent = 'Could not load rate limit status';
        }
    }

    void refreshCivitaiRateLimit();
    // Refresh every 30 seconds while the page is open
    setInterval(refreshCivitaiRateLimit, 30000);

    infiniteScrollToggle.checked = state.infiniteEnabled;
    if (variantGroupingToggle) {
        variantGroupingToggle.checked = state.groupVariantsEnabled;
    }
    debugToggle.checked = state.debugVisible;
    autoRefreshToggle.checked = state.autoRefreshEnabled;
    if (themeToggle) {
        themeToggle.checked = state.themeMode === 'dark';
    }
    syncNsfwVisibilityUi();
    sortOrderSelect.value = state.sortOrder;
    syncThemeMode();
    syncLayoutMode();
    syncThumbSize();
    syncFullscreenLoopUi();
    updatePagingUi();
    updateImportInputPlaceholder();
    initializeDetailFolderTabs();

    resizeObserver = new ResizeObserver(() => {
        scheduleGalleryGridHeightSync();
        detailFolderWorkspace?.updateSeam?.();
    });
    resizeObserver.observe(detailsPane);
    resizeObserver.observe(detailsContent);
    resizeObserver.observe(detailsEmpty);
    window.addEventListener('resize', scheduleGalleryGridHeightSync);
    scheduleGalleryGridHeightSync();

    const initialGalleryLoad = resetAndLoadImages({ preserveSelection: true, showRefreshUi: false });
    const initialReferenceLoad = loadReferenceData();

    Promise.all([initialReferenceLoad, initialGalleryLoad, refreshTasks({ silent: true })]).catch((error) => {
        galleryGrid.innerHTML = `<p>Startup error: ${error.message}</p>`;
    });

    initialGalleryLoad.finally(() => {
        void hydrateFilterOptionsInBackground();
    });

    if (treeEmbedFrame) {
        treeEmbedFrame.addEventListener('load', () => {
            postSelectedImageTagsToTree(getSelectedImage());
            postSelectedImageModelsToModelsFrame(getSelectedImage());
        });
    }

    if (modelsEmbedFrame) {
        modelsEmbedFrame.addEventListener('load', () => {
            postSelectedImageModelsToModelsFrame(getSelectedImage());
            postGalleryKeysToModelsFrame();
        });
    }

    window.addEventListener('message', (event) => {
        if (event.origin !== window.location.origin) {
            return;
        }
        if (!event.data || typeof event.data.type !== 'string') {
            return;
        }

        if (event.data.type === 'atelier:gallery-tag-filter') {
            const isMultiSelect = Boolean(event.data.multiSelect);
            setTreeTagFilter(event.data.payload || null, isMultiSelect);
            void applyFilter({ ensureSearchCoverage: true });
            return;
        }

        if (event.data.type === 'atelier:gallery-missing-source-filter') {
            const source = event.data.source;
            const active = Boolean(event.data.active);
            if (source && state.missingSourceFilter.hasOwnProperty(source)) {
                state.missingSourceFilter[source] = active;
            }
            renderTreeTagFilterIndicator();
            renderAdvancedFilters();
            void applyFilter({ ensureSearchCoverage: true });
            return;
        }
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

    window.setInterval(() => {
        void refreshTasks({ silent: true });
    }, 1500);

    autoRefreshToggle.addEventListener('change', () => {
        state.autoRefreshEnabled = autoRefreshToggle.checked;
        writeStoredBool(STORAGE_KEYS.autoRefresh, state.autoRefreshEnabled);
        if (state.autoRefreshEnabled) {
            void maybeAutoRefreshGallery({ preserveSelection: true });
        }
    });

    if (themeToggle) {
        themeToggle.addEventListener('change', () => {
            state.themeMode = themeToggle.checked ? 'dark' : 'light';
            writeCookieValue(COOKIE_KEYS.themeMode, state.themeMode);
            syncThemeMode();
        });
    }

    if (uiKit?.mountHoverChoiceControl && nsfwVisibilityControl && nsfwVisibilityCurrent) {
        uiKit.mountHoverChoiceControl({
            root: nsfwVisibilityControl,
            currentButton: nsfwVisibilityCurrent,
            optionButtons: nsfwVisibilityOptionButtons,
            getValue: () => state.nsfwVisibility,
            setValue: (nextMode) => {
                state.nsfwVisibility = nextMode;
            },
            allowedValues: ['safe', 'mature', 'explicit'],
            formatLabel: (value) => formatNsfwVisibilityLabel(value),
            onChange: (nextMode) => {
                setNsfwVisibilityMode(nextMode);
            },
        });
    } else if (nsfwVisibilityControl && nsfwVisibilityCurrent) {
        // Fallback behavior if the shared helper is unavailable.
        nsfwVisibilityCurrent.addEventListener('click', (event) => {
            event.preventDefault();
            nsfwVisibilityControl.classList.toggle('is-open');
        });

        nsfwVisibilityOptionButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const nextMode = String(button.dataset.nsfwLevel || '').toLowerCase();
                if (!['safe', 'mature', 'explicit'].includes(nextMode)) {
                    return;
                }
                setNsfwVisibilityMode(nextMode);
                nsfwVisibilityControl.classList.remove('is-open');
            });
        });
    }

    // Emergency fallback: if clicking the NSFW control does not open the menu,
    // cycle through modes so the control remains usable.
    if (nsfwVisibilityControl && nsfwVisibilityCurrent) {
        nsfwVisibilityCurrent.addEventListener('click', () => {
            const wasOpen = nsfwVisibilityControl.classList.contains('is-open');
            window.setTimeout(() => {
                const isOpen = nsfwVisibilityControl.classList.contains('is-open');
                if (!wasOpen && !isOpen) {
                    cycleNsfwVisibilityMode();
                }
            }, 0);
        });
    }
});