/**
 * InfiniteScroll — shared, reusable infinite-scroll controller.
 *
 * Attach to any scrollable container and it will call `onLoadMore()` when
 * the user scrolls near the bottom.  Inspired by the main gallery's proven
 * algorithm: distance-to-bottom check with an early-trigger multiplier.
 *
 * Usage (IIFE — matches existing shared/ modules):
 *   const scroller = InfiniteScroll.create({
 *     scrollContainer: document.getElementById('gallery-grid'),
 *     hasMore:        () => state.hasMore,
 *     isLoading:      () => state.loadingPage,
 *     onLoadMore:     () => { loadNextPage(); },
 *   });
 *   // later: scroller.destroy();
 *
 * Options:
 *   scrollContainer       (HTMLElement, required) The scrollable element.
 *   hasMore               (function → bool)  Return true if more pages exist.
 *   isLoading             (function → bool)  Return true while a page is loading.
 *   onLoadMore            (function)         Called when user nears bottom.
 *                         **MUST synchronously set whatever `isLoading()` checks
 *                         to true before any async work.**
 *   earlyTriggerMultiplier (number, default 3) Trigger N viewport-heights before bottom.
 *   minTriggerDistance     (number, default 240) Minimum px from bottom to trigger.
 *
 * The scroll listener stays attached for the lifetime of the controller.
 * Guards (hasMore, isLoading) prevent redundant triggers — no teardown/setup
 * cycle needed.
 */
(() => {
  'use strict';

  function create(options) {
    const {
      scrollContainer,
      hasMore,
      isLoading,
      onLoadMore,
      earlyTriggerMultiplier = 3,
      minTriggerDistance = 240,
    } = options;

    if (!scrollContainer || typeof onLoadMore !== 'function') {
      throw new Error('InfiniteScroll: scrollContainer and onLoadMore are required');
    }

    let _enabled = true;
    let _handler = null;

    function _onScroll() {
      if (!_enabled) return;
      if (!hasMore()) return;
      if (isLoading()) return;

      const el = scrollContainer;
      const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      const earlyTrigger = el.clientHeight * earlyTriggerMultiplier;

      if (distanceToBottom < Math.max(earlyTrigger, minTriggerDistance)) {
        onLoadMore();
      }
    }

    function destroy() {
      if (_handler) {
        scrollContainer.removeEventListener('scroll', _handler, { passive: true });
        _handler = null;
      }
    }

    function setEnabled(value) {
      _enabled = !!value;
    }

    // Attach listener
    _handler = _onScroll;
    scrollContainer.addEventListener('scroll', _handler, { passive: true });

    return { destroy, setEnabled };
  }

  window.InfiniteScroll = { create };
})();
