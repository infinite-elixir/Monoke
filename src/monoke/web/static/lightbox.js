/* Monoke merged lightbox/viewer: one root, one controller (T11.1). */
(function () {
  "use strict";

  // BEGIN preload-plan
  // BEGIN preload-window
  // T8.7: warm the browser cache a few images either side of the current one.
  var PRELOAD_RADIUS = 3;
  var PRELOAD_MAX = PRELOAD_RADIUS * 2;

  // Pure helper: indices in the preload window that are safe to warm. Videos,
  // GIFs, audio, missing/`data:` URLs and already-failed URLs are filtered out.
  // The markers keep this extractable by the Node-based window test.
  function preloadWindowIndices(media, index, failed) {
    var indices = [];
    if (!media || !media.length) {
      return indices;
    }
    var start = Math.max(0, index - PRELOAD_RADIUS);
    var end = Math.min(media.length - 1, index + PRELOAD_RADIUS);
    for (var cursor = start; cursor <= end; cursor += 1) {
      if (cursor === index) {
        continue;
      }
      var item = media[cursor];
      if (!item || item.kind !== "image" || !item.url) {
        continue;
      }
      if (item.url.slice(0, 5) === "data:") {
        continue;
      }
      if (failed && typeof failed.has === "function" && failed.has(item.url)) {
        continue;
      }
      indices.push(cursor);
    }
    return indices;
  }
  // END preload-window

  // T8.8: one bounded plan covers the in-post window plus the nearest edge
  // images of the adjacent posts. The helper is pure so Node can test it.
  var PRELOAD_TOTAL_MAX = PRELOAD_MAX * 2;

  function addPreloadCandidate(urls, seen, item) {
    if (!item || item.kind !== "image" || !item.url) {
      return;
    }
    if (item.url.slice(0, 5) === "data:" || seen[item.url]) {
      return;
    }
    seen[item.url] = true;
    urls.push(item.url);
  }

  function preloadCandidates(media, index, neighbourImages) {
    var urls = [];
    var seen = Object.create(null);
    var indices = preloadWindowIndices(media, index, null);
    var cursor;
    for (cursor = 0; cursor < indices.length; cursor += 1) {
      addPreloadCandidate(urls, seen, media[indices[cursor]]);
    }
    var previous = (neighbourImages && neighbourImages.previous) || [];
    var next = (neighbourImages && neighbourImages.next) || [];
    // Interleave the two nearest-edge lists: the bounded plan must contain
    // both a left-crossing and a right-crossing seed before deeper images.
    var depth = Math.max(previous.length, next.length);
    for (cursor = 0; cursor < depth; cursor += 1) {
      if (cursor < previous.length) {
        addPreloadCandidate(urls, seen, previous[cursor]);
      }
      if (cursor < next.length) {
        addPreloadCandidate(urls, seen, next[cursor]);
      }
    }
    return urls;
  }

  function buildPreloadPlan(media, index, neighbourImages, limit, failed) {
    var candidates = preloadCandidates(media, index, neighbourImages);
    var plan = [];
    for (var cursor = 0; cursor < candidates.length; cursor += 1) {
      var url = candidates[cursor];
      if (failed && typeof failed.has === "function" && failed.has(url)) {
        continue;
      }
      plan.push(url);
      if (plan.length >= limit) {
        break;
      }
    }
    return plan;
  }

  function prunePreloadMap(map, keep) {
    map.forEach(function (_image, url) {
      if (!keep.has(url)) {
        map.delete(url);
      }
    });
  }

  // Failures stay remembered only while their URL is still a candidate; a
  // failed edge seed must not poison the same image after a post jump.
  function pruneFailedSet(failed, candidates) {
    var keep = new Set(candidates);
    failed.forEach(function (url) {
      if (!keep.has(url)) {
        failed.delete(url);
      }
    });
  }
  // END preload-plan

  // BEGIN resolve-index
  // Opening a tile selects its exact item. The sha is authoritative because a
  // post can gain or lose attachments between syncs; the gallery's
  // viewer_index is the fallback. A negative fallback means "last item".
  function resolveMediaIndex(media, sha256, fallback) {
    if (!media || !media.length) {
      return -1;
    }
    if (sha256) {
      for (var cursor = 0; cursor < media.length; cursor += 1) {
        if (media[cursor] && media[cursor].sha256 === sha256) {
          return cursor;
        }
      }
    }
    var index = Number(fallback);
    if (!isFinite(index)) {
      index = 0;
    }
    if (index < 0) {
      index = media.length - 1;
    }
    if (index > media.length - 1) {
      index = media.length - 1;
    }
    return index;
  }
  // END resolve-index

  // BEGIN escape-state
  // Escape unwinds one layer at a time: the three-dot disclosure first (T11.5),
  // then browser fullscreen (the browser owns that gesture), then the overlay
  // itself. Collapsing back to the compact lightbox is the expand control's
  // job, not Escape's.
  function escapeAction(state, fullscreen, menuOpen) {
    if (menuOpen) {
      return "close-menu";
    }
    if (fullscreen) {
      return "exit-browser";
    }
    return "close";
  }
  // END escape-state

  // BEGIN chrome-state
  // The bottom bar is explicit, never timed: it is visible on open, the hide
  // button pins it away and the handle restores it. Activity only ever reveals
  // a bar that is hidden but not pinned. The decision helpers stay pure so the
  // Node test can cover them without a DOM.
  function chromeRevealAllowed(state) {
    return Boolean(state && state.open && !state.pinned);
  }

  function chromeHandleTarget(state) {
    return state && state.hidden ? "handle" : "controls";
  }
  // END chrome-state

  var root = document.querySelector("[data-lightbox-root]");
  if (!root) {
    return;
  }

  var image = root.querySelector("[data-lightbox-image]");
  var video = root.querySelector("[data-lightbox-video]");
  var host = root.querySelector("[data-lightbox-media]");
  var title = root.querySelector("[data-lightbox-title]");
  var counter = root.querySelector("[data-lightbox-counter]");
  var postLink = root.querySelector("[data-lightbox-post]");
  var originalLink = root.querySelector("[data-lightbox-original]");
  var prevButton = root.querySelector("[data-lightbox-prev]");
  var nextButton = root.querySelector("[data-lightbox-next]");
  var closeButton = root.querySelector("[data-lightbox-close]");
  var expandButton = root.querySelector("[data-lightbox-expand]");
  var fullscreenButton = root.querySelector("[data-lightbox-fullscreen]");
  var stage = root.querySelector("[data-lightbox-stage]");
  var announcer = root.querySelector("[data-lightbox-announcer]");
  var toast = root.querySelector("[data-lightbox-toast]");
  var flash = root.querySelector("[data-lightbox-flash]");
  var loading = root.querySelector("[data-lightbox-loading]");
  var hint = root.querySelector("[data-lightbox-hint]");
  var controls = root.querySelector("[data-lightbox-controls]");
  var controlsHandle = root.querySelector("[data-lightbox-controls-handle]");
  var hideButton = root.querySelector("[data-lightbox-hide]");
  var postPrevButton = root.querySelector("[data-lightbox-post-prev]");
  var postNextButton = root.querySelector("[data-lightbox-post-next]");
  var moreButton = root.querySelector("[data-lightbox-more]");
  var menu = root.querySelector("[data-lightbox-menu]");
  var bookmarkForm = root.querySelector("[data-lightbox-controls] [data-bookmark-form]");
  var bookmarkButton = bookmarkForm
    ? bookmarkForm.querySelector("[data-bookmark-button]")
    : null;
  var zones = root.querySelectorAll("[data-viewer-action]");
  var creatorId = root.getAttribute("data-creator-id") || null;
  // Pages that mix creators (the bookmarks library) can put the id on the
  // opening tile; the gallery relies on the root fallback.
  var activeCreatorId = creatorId;
  // The opening gallery's whitelisted filter query string (T11.6). Entry
  // points without a [data-gallery] context keep the historical unfiltered
  // payload, so this stays empty there.
  var viewerQuery = "";

  var mode = "closed";
  var lastFocus = null;
  var tileItems = [];
  var tileIndex = 0;
  // Bookmarks removed while the library overlay is open. They leave the DOM
  // only when the overlay closes, so the viewer never ejects mid-view (T11.3).
  var libraryPostsRemoved = new Set();
  var libraryMediaRemoved = new Set();
  var payload = null;
  var currentIndex = 0;
  var currentItem = null;
  var lastNav = 0;
  var lastWheel = 0;
  var lastTouchNav = 0;
  var touchStart = null;
  var hydrateToken = 0;
  var hydrateController = null;
  var jumpToken = 0;
  var jumpController = null;
  var hintDismissed = false;
  var toastTimer = null;
  var chromePinned = false;
  var chromeHidden = false;
  var menuOpen = false;
  var preloadMap = new Map();
  var preloadFailed = new Set();
  var preloadIdle = null;
  var preloadEpoch = 0;
  var mediaReady = false;
  // T11.7: one persistent pill, one delayed-show timer and a token so a load
  // event from a replaced item can never clear the current wait.
  var LOADING_DELAY = 150;
  var LOADING_MIN_VISIBLE = 300;
  var loadingToken = 0;
  var loadingTimer = null;
  var loadingVisible = false;
  var loadingShownAt = 0;
  var mediaWatch = null;
  var PAYLOAD_CACHE_MAX = 4;
  var payloadCache = new Map();
  var payloadPending = new Set();
  var payloadEpoch = 0;
  var payloadPrefetchIdle = null;

  function clamp(value, count) {
    if (value < 0) {
      return 0;
    }
    if (value > count - 1) {
      return count - 1;
    }
    return value;
  }

  function setText(node, value) {
    if (!node || node.textContent === value) {
      return;
    }
    node.textContent = value;
  }

  function setFlash(message) {
    if (!flash) {
      return;
    }
    flash.textContent = message || "";
    flash.hidden = !message;
    if (message) {
      clearLoading();
    }
  }

  // ------------------------------------------------------------------- //
  // Loading pill (T11.7)
  // ------------------------------------------------------------------- //

  function cancelLoadingTimer() {
    if (!loadingTimer) {
      return;
    }
    window.clearTimeout(loadingTimer);
    loadingTimer = null;
  }

  function clearLoading() {
    // Immediate reset: close, unavailable media and errors must not leave a
    // stale pill behind for any length of time.
    loadingToken += 1;
    cancelLoadingTimer();
    loadingVisible = false;
    loadingShownAt = 0;
    if (loading) {
      loading.hidden = true;
    }
  }

  function beginLoading() {
    // Start/refresh one wait for the visible item (or a payload with nothing
    // painted yet). Returns the token this wait's completion must carry.
    if (!loading || mode === "closed") {
      return loadingToken;
    }
    loadingToken += 1;
    var token = loadingToken;
    cancelLoadingTimer();
    if (loadingVisible) {
      // A new item may reuse the visible pill: no flicker between items.
      return token;
    }
    loadingTimer = window.setTimeout(function () {
      loadingTimer = null;
      if (token !== loadingToken || mode === "closed") {
        return;
      }
      loadingVisible = true;
      loadingShownAt = Date.now();
      loading.hidden = false;
    }, LOADING_DELAY);
    return token;
  }

  function finishLoading(token) {
    if (token !== undefined && token !== loadingToken) {
      // A late load/error event from a replaced item must not clear a newer
      // wait.
      return;
    }
    loadingToken += 1;
    cancelLoadingTimer();
    if (!loadingVisible) {
      return;
    }
    // If the pill already appeared, keep it long enough to read instead of
    // blinking away in the same frame the media rendered.
    var elapsed = Date.now() - loadingShownAt;
    if (elapsed >= LOADING_MIN_VISIBLE) {
      loadingVisible = false;
      loadingShownAt = 0;
      loading.hidden = true;
      return;
    }
    var hideToken = loadingToken;
    loadingTimer = window.setTimeout(function () {
      loadingTimer = null;
      if (hideToken !== loadingToken) {
        return;
      }
      loadingVisible = false;
      loadingShownAt = 0;
      loading.hidden = true;
    }, LOADING_MIN_VISIBLE - elapsed);
  }

  function prefersReducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_error) {
      return false;
    }
  }

  // ------------------------------------------------------------------- //
  // Control bar visibility (T11.2, explicit only since T11.5)
  // ------------------------------------------------------------------- //

  function chromeState() {
    return {
      open: mode !== "closed",
      pinned: chromePinned,
      hidden: chromeHidden
    };
  }

  function menuItems() {
    if (!menu) {
      return [];
    }
    var nodes = menu.querySelectorAll(
      "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])"
    );
    return Array.prototype.filter.call(nodes, function (node) {
      return !node.disabled && node.offsetParent !== null;
    });
  }

  function setMenuOpen(open, restoreFocus) {
    if (!menu || !moreButton) {
      return;
    }
    var wasOpen = menuOpen;
    menuOpen = Boolean(open);
    moreButton.setAttribute("aria-expanded", menuOpen ? "true" : "false");
    menu.hidden = !menuOpen;
    if (menuOpen) {
      var items = menuItems();
      if (items.length) {
        try {
          items[0].focus({ preventScroll: true });
        } catch (_error) {
          items[0].focus();
        }
      }
      return;
    }
    if (restoreFocus && wasOpen) {
      try {
        moreButton.focus({ preventScroll: true });
      } catch (_error) {
        moreButton.focus();
      }
    }
  }

  function closeMenu(restoreFocus) {
    if (!menuOpen) {
      return;
    }
    setMenuOpen(false, restoreFocus);
  }

  function toggleMenu() {
    setMenuOpen(!menuOpen, true);
  }

  function setControlsHidden(hidden) {
    // The disclosure cannot outlive its trigger: hiding or pinning the bar
    // closes it first so focus never lands inside an inert region.
    if (hidden) {
      closeMenu(false);
    }
    chromeHidden = hidden;
    root.classList.toggle("is-controls-hidden", hidden);
    if (controls) {
      if (hidden) {
        controls.setAttribute("inert", "");
      } else {
        controls.removeAttribute("inert");
      }
    }
    if (controlsHandle) {
      controlsHandle.hidden = !hidden;
    }
  }

  function revealControls() {
    if (!chromeRevealAllowed(chromeState())) {
      return;
    }
    if (chromeHidden) {
      setControlsHidden(false);
    }
  }

  function hideControls(pin) {
    if (pin) {
      chromePinned = true;
    }
    if (!chromeHidden) {
      setControlsHidden(true);
    }
    if (chromePinned && controlsHandle) {
      try {
        controlsHandle.focus({ preventScroll: true });
      } catch (_error) {
        controlsHandle.focus();
      }
    }
  }

  function firstControl() {
    if (!controls) {
      return null;
    }
    var nodes = controls.querySelectorAll(
      "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])"
    );
    for (var cursor = 0; cursor < nodes.length; cursor += 1) {
      if (!nodes[cursor].disabled && nodes[cursor].offsetParent !== null) {
        return nodes[cursor];
      }
    }
    return null;
  }

  function restoreControls() {
    chromePinned = false;
    setControlsHidden(false);
    var target = firstControl();
    if (target) {
      try {
        target.focus({ preventScroll: true });
      } catch (_error) {
        target.focus();
      }
    }
  }

  function resetChrome() {
    chromePinned = false;
    closeMenu(false);
    setControlsHidden(false);
    if (controls) {
      controls.removeAttribute("inert");
    }
  }

  function onActivity() {
    if (mode === "closed") {
      return;
    }
    if (chromePinned) {
      return;
    }
    revealControls();
  }

  function showToast(message) {
    if (!toast) {
      return;
    }
    toast.textContent = message;
    toast.hidden = false;
    if (toastTimer) {
      window.clearTimeout(toastTimer);
    }
    toastTimer = window.setTimeout(function () {
      toast.hidden = true;
      toast.textContent = "";
      toastTimer = null;
    }, 1500);
  }

  function announce(message) {
    if (announcer) {
      announcer.textContent = message;
    }
    showToast(message);
  }

  // The hint is onboarding for the full-viewport layer only: the compact
  // lightbox already shows visible media controls. Once dismissed it stays
  // dismissed for this page load (T8.4).
  function dismissHint() {
    if (!hint || hintDismissed || mode === "compact" || mode === "closed") {
      return;
    }
    hintDismissed = true;
    hint.setAttribute("aria-hidden", "true");
    var instant = prefersReducedMotion();
    if (instant) {
      hint.hidden = true;
      return;
    }
    hint.classList.add("is-dismissed");
    hint.addEventListener(
      "transitionend",
      function (event) {
        if (event.target === hint) {
          hint.hidden = true;
        }
      },
      { once: true }
    );
  }

  function suppressHintForQuery() {
    if (!hint) {
      return;
    }
    var params = new URLSearchParams(window.location.search);
    var hintFlag = (params.get("hint") || "").toLowerCase();
    if (
      params.get("quiet") === "1" ||
      ["0", "false", "no", "off"].indexOf(hintFlag) !== -1
    ) {
      hintDismissed = true;
      hint.hidden = true;
    }
  }

  function kindLabel(kind) {
    if (kind === "video") {
      return "Video";
    }
    if (kind === "gif") {
      return "GIF";
    }
    if (kind === "image") {
      return "Image";
    }
    if (kind === "audio") {
      return "Audio";
    }
    return "Media";
  }

  // ------------------------------------------------------------------- //
  // Preloading (ported from the old page viewer)
  // ------------------------------------------------------------------- //

  function preloadAllowed() {
    try {
      if (document.visibilityState && document.visibilityState !== "visible") {
        return false;
      }
      var connection = navigator.connection;
      if (connection) {
        if (connection.saveData === true) {
          return false;
        }
        if (
          connection.effectiveType === "slow-2g" ||
          connection.effectiveType === "2g"
        ) {
          return false;
        }
      }
      if (
        typeof window.matchMedia === "function" &&
        window.matchMedia("(prefers-reduced-data: reduce)").matches
      ) {
        return false;
      }
    } catch (_error) {
      return false;
    }
    return true;
  }

  function cancelPreload() {
    if (!preloadIdle) {
      return;
    }
    if (
      preloadIdle.type === "idle" &&
      typeof window.cancelIdleCallback === "function"
    ) {
      window.cancelIdleCallback(preloadIdle.handle);
    } else {
      window.clearTimeout(preloadIdle.handle);
    }
    preloadIdle = null;
  }

  function clearPreloadCache() {
    cancelPreload();
    preloadEpoch += 1;
    preloadMap.clear();
    preloadFailed.clear();
    mediaReady = false;
  }

  function rememberPreloadFailure(url) {
    preloadFailed.add(url);
    if (preloadFailed.size > PRELOAD_TOTAL_MAX * 2) {
      var oldest = preloadFailed.values().next();
      if (!oldest.done) {
        preloadFailed.delete(oldest.value);
      }
    }
  }

  function warmPreloadWindow(index) {
    if (!payload || !payload.media || !payload.media.length || !preloadAllowed()) {
      return;
    }
    var candidates = preloadCandidates(
      payload.media,
      index,
      payload.neighbour_images
    );
    var plan = buildPreloadPlan(
      payload.media,
      index,
      payload.neighbour_images,
      PRELOAD_TOTAL_MAX,
      preloadFailed
    );
    prunePreloadMap(preloadMap, new Set(plan));
    pruneFailedSet(preloadFailed, candidates);

    for (var position = 0; position < plan.length; position += 1) {
      if (preloadMap.size >= PRELOAD_TOTAL_MAX) {
        break;
      }
      var url = plan[position];
      if (preloadMap.has(url)) {
        continue;
      }
      var loader = new Image();
      loader.fetchPriority = "low";
      loader.onload = function () {
        /* Keep the reference alive: the Map owns the decoded image. */
      };
      loader.onerror = (function (failedUrl) {
        return function () {
          preloadMap.delete(failedUrl);
          rememberPreloadFailure(failedUrl);
        };
      })(url);
      preloadMap.set(url, loader);
      loader.src = url;
    }
  }

  function schedulePreload() {
    cancelPreload();
    if (!mediaReady || !preloadAllowed() || !payload) {
      return;
    }
    preloadEpoch += 1;
    var epoch = preloadEpoch;
    var run = function () {
      preloadIdle = null;
      if (epoch !== preloadEpoch || !mediaReady) {
        return;
      }
      warmPreloadWindow(currentIndex);
    };
    if (typeof window.requestIdleCallback === "function") {
      preloadIdle = {
        type: "idle",
        handle: window.requestIdleCallback(run, { timeout: 1500 })
      };
    } else {
      preloadIdle = {
        type: "timeout",
        handle: window.setTimeout(run, 200)
      };
    }
  }

  function cancelPayloadPrefetch() {
    if (!payloadPrefetchIdle) {
      return;
    }
    if (
      payloadPrefetchIdle.type === "idle" &&
      typeof window.cancelIdleCallback === "function"
    ) {
      window.cancelIdleCallback(payloadPrefetchIdle.handle);
    } else {
      window.clearTimeout(payloadPrefetchIdle.handle);
    }
    payloadPrefetchIdle = null;
  }

  function payloadKey(serviceId) {
    return viewerQuery + "|" + String(serviceId);
  }

  function rememberPayload(next) {
    if (!next || !next.post || !next.post.service_id) {
      return;
    }
    var key = payloadKey(next.post.service_id);
    payloadCache.delete(key);
    payloadCache.set(key, next);
    while (payloadCache.size > PAYLOAD_CACHE_MAX) {
      var oldest = payloadCache.keys().next();
      if (oldest.done) {
        break;
      }
      payloadCache.delete(oldest.value);
    }
  }

  function updateCachedBookmarks(kind, postId, sha, bookmarked) {
    payloadCache.forEach(function (cached) {
      if (kind === "post") {
        if (
          cached.post &&
          String(cached.post.service_id) === String(postId)
        ) {
          cached.post.bookmarked = bookmarked;
        }
        return;
      }
      var media = cached.media || [];
      for (var cursor = 0; cursor < media.length; cursor += 1) {
        if (media[cursor].sha256 === sha) {
          media[cursor].bookmarked = bookmarked;
        }
      }
    });
  }

  function prefetchNeighbourPayloads() {
    if (!payload || !activeCreatorId || !preloadAllowed()) {
      return;
    }
    var ids = [payload.previous_post, payload.next_post];
    for (var cursor = 0; cursor < ids.length; cursor += 1) {
      var id = ids[cursor];
      if (!id) {
        continue;
      }
      id = String(id);
      if (payloadCache.has(payloadKey(id)) || payloadPending.has(id)) {
        continue;
      }
      payloadPending.add(id);
      (function (pendingId, requestEpoch) {
        fetch(viewerDataUrl(pendingId), {
          headers: { Accept: "application/json" }
        })
          .then(function (response) {
            if (!response.ok) {
              throw new Error("neighbour payload request failed");
            }
            return response.json();
          })
          .then(
            function (next) {
              payloadPending.delete(pendingId);
              if (requestEpoch === payloadEpoch) {
                rememberPayload(next);
              }
            },
            function () {
              payloadPending.delete(pendingId);
            }
          );
      })(id, payloadEpoch);
    }
  }

  function schedulePayloadPrefetch() {
    cancelPayloadPrefetch();
    if (!mediaReady || !preloadAllowed() || !payload) {
      return;
    }
    var epoch = preloadEpoch;
    var run = function () {
      payloadPrefetchIdle = null;
      if (epoch !== preloadEpoch || !mediaReady) {
        return;
      }
      prefetchNeighbourPayloads();
    };
    if (typeof window.requestIdleCallback === "function") {
      payloadPrefetchIdle = {
        type: "idle",
        handle: window.requestIdleCallback(run, { timeout: 2000 })
      };
    } else {
      payloadPrefetchIdle = {
        type: "timeout",
        handle: window.setTimeout(run, 300)
      };
    }
  }

  function mediaBecameReady() {
    mediaReady = true;
    schedulePreload();
    schedulePayloadPrefetch();
  }

  // The visible image always wins the connection: preloading starts only after
  // the current media reports load/error (or is already complete on load).
  // T11.7 hangs the loading pill's lifecycle off the same events, with one
  // AbortController per item so replaced media cannot leak handlers.
  function watchCurrentMedia(element) {
    if (mediaWatch) {
      mediaWatch.abort();
      mediaWatch = null;
    }
    if (!element) {
      clearLoading();
      mediaReady = true;
      schedulePreload();
      schedulePayloadPrefetch();
      return;
    }

    mediaWatch = new AbortController();
    var signal = mediaWatch.signal;
    var waiting = { token: beginLoading() };
    var readyNotified = false;

    function mediaBecameReadyOnce() {
      if (readyNotified) {
        return;
      }
      readyNotified = true;
      mediaBecameReady();
    }

    function mediaArrived() {
      finishLoading(waiting.token);
      mediaBecameReadyOnce();
    }

    if (element.tagName === "VIDEO") {
      var playbackStarted = false;
      element.addEventListener(
        "loadedmetadata",
        function () {
          mediaBecameReadyOnce();
        },
        { signal: signal }
      );
      element.addEventListener("loadeddata", mediaArrived, { signal: signal });
      element.addEventListener("canplay", mediaArrived, { signal: signal });
      element.addEventListener("error", mediaArrived, { signal: signal });
      element.addEventListener(
        "playing",
        function () {
          playbackStarted = true;
          mediaArrived();
        },
        { signal: signal }
      );
      element.addEventListener(
        "waiting",
        function () {
          if (playbackStarted) {
            // Buffering after playback began is a new wait for the same item.
            waiting.token = beginLoading();
          }
        },
        { signal: signal }
      );
      element.addEventListener(
        "stalled",
        function () {
          if (playbackStarted) {
            waiting.token = beginLoading();
          }
        },
        { signal: signal }
      );
      if (element.readyState >= 1) {
        mediaBecameReadyOnce();
      }
      if (element.readyState >= 2) {
        mediaArrived();
      }
      return;
    }
    element.addEventListener("load", mediaArrived, { signal: signal });
    element.addEventListener("error", mediaArrived, { signal: signal });
    if (element.complete) {
      mediaArrived();
    }
  }

  // ------------------------------------------------------------------- //
  // Rendering
  // ------------------------------------------------------------------- //

  function unavailableNode() {
    var box = document.createElement("div");
    box.className = "viewer-unavailable";

    var label = document.createElement("p");
    label.textContent = "Media unavailable";
    box.appendChild(label);

    var postUrl = (payload && payload.post && payload.post.post_url) || "";
    if (postUrl) {
      var link = document.createElement("a");
      link.href = postUrl;
      link.textContent = "Open the post";
      box.appendChild(link);
    }
    return box;
  }

  function unloadMedia() {
    cancelPreload();
    cancelPayloadPrefetch();
    preloadEpoch += 1;
    mediaReady = false;
    if (mediaWatch) {
      mediaWatch.abort();
      mediaWatch = null;
    }
    clearLoading();
    if (image) {
      image.removeAttribute("src");
      image.hidden = false;
    }
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.removeAttribute("poster");
      video.hidden = true;
    }
    if (host) {
      var stale = host.querySelector(".viewer-unavailable");
      if (stale) {
        stale.remove();
      }
    }
  }

  function mediaItemFromPayload(item) {
    return {
      kind: item.kind,
      url: item.url,
      thumbUrl: item.thumb_url || "",
      title:
        (payload.post && payload.post.title) || item.filename || "Media",
      postUrl: (payload.post && payload.post.post_url) || "#",
      sha256: item.sha256 || "",
      index: item.index,
      filename: item.filename || "",
      bookmarked: Boolean(item.bookmarked),
      postServiceId: (payload.post && payload.post.service_id) || "",
      postBookmarked: Boolean(payload.post && payload.post.bookmarked)
    };
  }

  function mediaItemFromTile(tile) {
    var missing = tile.hasAttribute("data-missing");
    return {
      kind: tile.getAttribute("data-kind") || "image",
      url: missing
        ? ""
        : tile.getAttribute("data-full") ||
          tile.getAttribute("data-url") ||
          tile.getAttribute("href") ||
          "",
      thumbUrl: tile.getAttribute("data-thumb") || "",
      title: tile.getAttribute("data-title") || "",
      postUrl: tile.getAttribute("data-post-url") || "#",
      sha256: tile.getAttribute("data-sha256") || "",
      index: Number(tile.getAttribute("data-viewer-index")) || 0,
      filename: tile.getAttribute("data-title") || "",
      bookmarked: false,
      postServiceId: tile.getAttribute("data-post-service-id") || "",
      postBookmarked: false
    };
  }

  function canStep(delta) {
    if (payload && payload.media && payload.media.length) {
      if (delta < 0) {
        return currentIndex > 0 || Boolean(payload.previous_post);
      }
      return (
        currentIndex < payload.media.length - 1 || Boolean(payload.next_post)
      );
    }
    if (!tileItems.length) {
      return false;
    }
    if (delta < 0) {
      return tileIndex > 0;
    }
    return tileIndex < tileItems.length - 1;
  }

  function updateAnnouncer(item) {
    if (!announcer) {
      return;
    }
    var text;
    if (payload && payload.media && payload.media.length) {
      text =
        kindLabel(item.kind) +
        " " +
        (currentIndex + 1) +
        " of " +
        payload.media.length +
        " in this post";
      var rank = Number(payload.post_rank);
      var posts = Number(payload.media_post_count);
      if (rank > 0 && posts > 0) {
        text += " · " + rank + " of " + posts + " media posts";
      }
    } else {
      text =
        kindLabel(item.kind) +
        " " +
        (tileItems.length ? tileIndex + 1 : 1) +
        " of " +
        (tileItems.length || 1) +
        " on this page";
    }
    announcer.textContent = text;
  }

  function syncBookmarkControl(item) {
    if (!bookmarkForm || !bookmarkButton || !item) {
      return;
    }
    var postId = item.postServiceId || "";
    var bookmarked = Boolean(item.postBookmarked);
    var name = item.title || "this post";
    bookmarkForm.setAttribute(
      "action",
      postId
        ? "/c/" +
            encodeURIComponent(activeCreatorId || "") +
            "/p/" +
            encodeURIComponent(postId) +
            "/bookmark"
        : "#"
    );
    var nextInput = bookmarkForm.querySelector('input[name="next"]');
    if (nextInput) {
      nextInput.value = window.location.pathname + window.location.search;
    }
    bookmarkButton.disabled = !postId || !activeCreatorId;
    bookmarkButton.setAttribute("aria-pressed", bookmarked ? "true" : "false");
    bookmarkButton.setAttribute("aria-label", "Bookmark post: " + name);
    bookmarkButton.setAttribute("title", bookmarked ? "Remove bookmark" : "Bookmark");
    bookmarkButton.setAttribute("data-bookmark-target", "post");
    bookmarkButton.classList.toggle("is-bookmarked", bookmarked);
  }

  function updateChrome(item, counterText) {
    if (!item) {
      return;
    }
    setText(title, item.title || "");
    if (title) {
      title.setAttribute("title", item.title || "");
    }
    setText(counter, counterText || "");
    if (postLink) {
      postLink.href = item.postUrl || "#";
    }
    if (originalLink) {
      originalLink.href = item.url || "#";
    }
    if (prevButton) {
      prevButton.disabled = !canStep(-1);
    }
    if (nextButton) {
      nextButton.disabled = !canStep(1);
    }
    if (postPrevButton) {
      postPrevButton.disabled = !(payload && payload.previous_post);
    }
    if (postNextButton) {
      postNextButton.disabled = !(payload && payload.next_post);
    }
    syncBookmarkControl(item);
    root.setAttribute("data-kind", item.kind || "file");
    updateAnnouncer(item);
  }

  function render(item, counterText) {
    if (!item) {
      return;
    }
    currentItem = item;
    root.setAttribute("data-kind", item.kind || "file");

    if (!item.url) {
      if (mediaWatch) {
        mediaWatch.abort();
        mediaWatch = null;
      }
      clearLoading();
      if (image) {
        image.removeAttribute("src");
        image.hidden = true;
      }
      if (video) {
        video.pause();
        video.removeAttribute("src");
        video.hidden = true;
      }
      if (host && !host.querySelector(".viewer-unavailable")) {
        host.appendChild(unavailableNode());
      }
      updateChrome(item, counterText);
      mediaReady = true;
      schedulePayloadPrefetch();
      return;
    }

    if (item.kind === "video" && video) {
      if (image) {
        image.removeAttribute("src");
        image.hidden = true;
      }
      if (video.getAttribute("src") !== item.url) {
        video.setAttribute("src", item.url);
      }
      if (item.thumbUrl) {
        video.setAttribute("poster", item.thumbUrl);
      } else {
        video.removeAttribute("poster");
      }
      video.setAttribute("aria-label", item.title || "Video");
      video.hidden = false;
      video.load();
      updateChrome(item, counterText);
      watchCurrentMedia(video);
      return;
    }

    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.hidden = true;
    }
    if (image) {
      if (image.getAttribute("src") !== item.url) {
        image.setAttribute("src", item.url);
      }
      image.setAttribute("alt", item.title || "Media");
      image.hidden = false;
      updateChrome(item, counterText);
      watchCurrentMedia(image);
      return;
    }
    updateChrome(item, counterText);
    watchCurrentMedia(null);
  }

  function paintTile(index) {
    if (!tileItems.length) {
      return;
    }
    tileIndex = clamp(Number(index) || 0, tileItems.length);
    render(
      mediaItemFromTile(tileItems[tileIndex]),
      tileIndex + 1 + " / " + tileItems.length
    );
  }

  function renderPayload(index) {
    if (!payload || !payload.media || !payload.media.length) {
      return;
    }
    currentIndex = clamp(Number(index) || 0, payload.media.length);
    render(
      mediaItemFromPayload(payload.media[currentIndex]),
      currentIndex + 1 + " / " + payload.media.length
    );
  }

  // ------------------------------------------------------------------- //
  // Payload hydration and post crossing
  // ------------------------------------------------------------------- //

  function applyPayload(next, sha, fallback, signal) {
    if (signal && signal.aborted) {
      return;
    }
    payload = next;
    rememberPayload(next);
    var index = resolveMediaIndex(next.media, sha, fallback);
    if (index < 0) {
      currentIndex = -1;
      currentItem = null;
      render(
        {
          kind: "file",
          url: "",
          title: (next.post && next.post.title) || "",
          postUrl: (next.post && next.post.post_url) || "#"
        },
        ""
      );
    } else {
      renderPayload(index);
    }
    root.removeAttribute("aria-busy");
    if (mediaReady) {
      schedulePreload();
      schedulePayloadPrefetch();
    }
  }

  function viewerDataUrl(serviceId) {
    var query = "post=" + encodeURIComponent(serviceId);
    if (viewerQuery) {
      query += "&" + viewerQuery;
    }
    return "/c/" + activeCreatorId + "/viewer/data?" + query;
  }

  function fetchPayload(serviceId, signal) {
    return fetch(viewerDataUrl(serviceId), {
      headers: { Accept: "application/json" },
      signal: signal
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("viewer data request failed");
      }
      return response.json();
    });
  }

  function hydrate(tile) {
    var serviceId = tile.getAttribute("data-post-service-id") || "";
    var sha = tile.getAttribute("data-sha256") || "";
    // Bookmark entry points (T11.3) resolve by sha only: a bookmark whose
    // attachment vanished upstream must land on the unavailable state, never
    // on the post's first item. Gallery tiles keep the index fallback.
    var bookmarkEntry = tile.hasAttribute("data-lightbox-open");
    var fallback = bookmarkEntry
      ? -1
      : Number(tile.getAttribute("data-viewer-index")) || 0;
    if (!serviceId || !activeCreatorId) {
      return;
    }

    hydrateToken += 1;
    var token = hydrateToken;
    if (hydrateController) {
      hydrateController.abort();
      hydrateController = null;
    }

    var cached = payloadCache.get(payloadKey(serviceId));
    if (cached) {
      applyPayload(cached, sha, fallback, null);
      return;
    }

    hydrateController = new AbortController();
    var signal = hydrateController.signal;
    root.setAttribute("aria-busy", "true");
    setFlash("");
    if (!currentItem || !currentItem.url) {
      beginLoading();
    }
    fetchPayload(serviceId, signal)
      .then(function (next) {
        if (token !== hydrateToken) {
          return;
        }
        applyPayload(next, sha, fallback, signal);
      })
      .catch(function (error) {
        if (token !== hydrateToken || (error && error.name === "AbortError")) {
          return;
        }
        root.removeAttribute("aria-busy");
        setFlash("Could not load this post. Staying on the current media.");
      });
  }

  function jump(serviceId, index, sha) {
    if (!serviceId || !activeCreatorId) {
      return;
    }
    jumpToken += 1;
    var token = jumpToken;
    if (jumpController) {
      jumpController.abort();
      jumpController = null;
    }

    root.setAttribute("aria-busy", "true");
    setFlash("");
    if (!currentItem || !currentItem.url) {
      beginLoading();
    }

    var cached = payloadCache.get(payloadKey(serviceId));
    if (cached) {
      applyPayload(cached, sha || "", index, null);
      return;
    }

    jumpController = new AbortController();
    var signal = jumpController.signal;
    fetchPayload(serviceId, signal)
      .then(function (next) {
        if (token !== jumpToken) {
          return;
        }
        applyPayload(next, sha || "", index, signal);
      })
      .catch(function (error) {
        if (token !== jumpToken || (error && error.name === "AbortError")) {
          return;
        }
        root.removeAttribute("aria-busy");
        setFlash("Could not load that post. Staying on the current media.");
      });
  }

  function go(action) {
    if (!action) {
      return;
    }
    var now = Date.now();
    if (now - lastNav < 150) {
      return;
    }
    lastNav = now;
    closeMenu(false);

    if (action === "prev-image") {
      if (payload) {
        if (currentIndex > 0) {
          renderPayload(currentIndex - 1);
        } else if (payload.previous_post) {
          // The inverse of the move that entered this post lands on its last
          // media; jump() resolves that against the target payload.
          jump(payload.previous_post, -1);
        }
        return;
      }
      if (tileIndex > 0) {
        paintTile(tileIndex - 1);
        // Keep the payload in step with the tile the user is actually
        // looking at; a slower request for the previous tile must not snap
        // the overlay back when it lands.
        hydrate(tileItems[tileIndex]);
      }
      return;
    }
    if (action === "next-image") {
      if (payload) {
        if (currentIndex < payload.media.length - 1) {
          renderPayload(currentIndex + 1);
        } else if (payload.next_post) {
          jump(payload.next_post, 0);
        }
        return;
      }
      if (tileIndex < tileItems.length - 1) {
        paintTile(tileIndex + 1);
        hydrate(tileItems[tileIndex]);
      }
      return;
    }
    if (action === "prev-post") {
      if (payload) {
        jump(payload.previous_post);
      }
      return;
    }
    if (action === "next-post") {
      if (payload) {
        jump(payload.next_post);
      }
    }
  }

  // ------------------------------------------------------------------- //
  // State machine and focus
  // ------------------------------------------------------------------- //

  function setMode(next) {
    mode = next;
    root.classList.toggle("is-compact", next === "compact");
    root.classList.toggle("is-expanded", next === "expanded");
    root.classList.toggle("is-browser", next === "browser");
    root.setAttribute("data-state", next);
    if (expandButton) {
      var expanded = next !== "compact" && next !== "closed";
      expandButton.setAttribute("aria-pressed", expanded ? "true" : "false");
      var label = expanded ? "Collapse viewer" : "Expand viewer";
      expandButton.setAttribute("aria-label", label);
      expandButton.setAttribute("title", label);
    }
    if (
      (next === "expanded" || next === "browser") &&
      hint &&
      !hintDismissed
    ) {
      hint.hidden = false;
    }
  }

  function updateFullscreenControl() {
    if (!fullscreenButton) {
      return;
    }
    var active = Boolean(document.fullscreenElement);
    fullscreenButton.setAttribute("aria-pressed", active ? "true" : "false");
    fullscreenButton.setAttribute(
      "data-fullscreen-state",
      active ? "exit" : "enter"
    );
    fullscreenButton.classList.toggle("is-fullscreen", active);
    var label = active ? "Exit fullscreen" : "Enter fullscreen";
    fullscreenButton.setAttribute("aria-label", label);
    fullscreenButton.setAttribute("title", label);
  }

  function requestFullscreen(force) {
    if (document.fullscreenElement) {
      return;
    }
    if (!root.requestFullscreen) {
      if (force) {
        setFlash("Fullscreen is not available in this browser.");
      }
      return;
    }
    try {
      var request = root.requestFullscreen({ navigationUI: "hide" });
      if (request && typeof request.catch === "function") {
        request.catch(function () {
          if (force) {
            setFlash("Fullscreen is not available in this browser.");
          }
        });
      }
    } catch (_error) {
      if (force) {
        setFlash("Fullscreen is not available in this browser.");
      }
    }
  }

  function exitFullscreen() {
    if (document.fullscreenElement && document.exitFullscreen) {
      try {
        var exit = document.exitFullscreen();
        if (exit && typeof exit.catch === "function") {
          exit.catch(function () {});
        }
      } catch (_error) {
        /* Already leaving fullscreen. */
      }
    }
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      exitFullscreen();
      return;
    }
    requestFullscreen(true);
  }

  function toggleExpand() {
    if (mode === "browser") {
      exitFullscreen();
      setMode("compact");
      return;
    }
    if (mode === "expanded") {
      setMode("compact");
      return;
    }
    setMode("expanded");
  }

  function focusableItems() {
    var nodes = root.querySelectorAll(
      "a[href], button, input, select, textarea, video[controls], " +
        "audio[controls], [tabindex]:not([tabindex='-1'])"
    );
    return Array.prototype.filter.call(nodes, function (node) {
      return (
        !node.disabled &&
        !node.hidden &&
        node.getAttribute("aria-hidden") !== "true" &&
        !node.closest("[inert]") &&
        node.offsetParent !== null
      );
    });
  }

  function isTypingTarget(target) {
    if (!target || !target.closest) {
      return false;
    }
    // Only real text entry swallows shortcuts. Buttons and links inside the
    // overlay are navigation controls, so arrow keys must keep working after
    // they take focus.
    return Boolean(
      target.closest("input, textarea, select, [contenteditable='true']")
    );
  }

  function trapTab(event) {
    var items = focusableItems();
    if (!items.length) {
      event.preventDefault();
      root.focus();
      return;
    }
    var index = items.indexOf(document.activeElement);
    if (event.shiftKey) {
      if (index <= 0) {
        event.preventDefault();
        items[items.length - 1].focus();
      }
      return;
    }
    if (index === -1 || index === items.length - 1) {
      event.preventDefault();
      items[0].focus();
    }
  }

  // The bookmarks library is server-rendered. Removing a bookmark from the
  // overlay updates that page's DOM on close instead of reloading it, so the
  // filters, page and scroll position survive. Other pages have no matching
  // hooks and are left untouched.
  function syncBookmarkLibrary() {
    if (!libraryPostsRemoved.size && !libraryMediaRemoved.size) {
      return;
    }
    var changed = false;
    if (libraryPostsRemoved.size) {
      var cards = document.querySelectorAll("[data-bookmark-post-card]");
      for (var cursor = 0; cursor < cards.length; cursor += 1) {
        if (
          libraryPostsRemoved.has(cards[cursor].getAttribute("data-post-service-id"))
        ) {
          cards[cursor].remove();
          changed = true;
        }
      }
    }
    if (libraryMediaRemoved.size) {
      var tiles = document.querySelectorAll("[data-bookmark-media]");
      for (var index = 0; index < tiles.length; index += 1) {
        if (libraryMediaRemoved.has(tiles[index].getAttribute("data-sha256"))) {
          tiles[index].remove();
          changed = true;
        }
      }
    }
    libraryPostsRemoved.clear();
    libraryMediaRemoved.clear();
    if (changed && (!lastFocus || !document.body.contains(lastFocus))) {
      // The originating tile may be the one just removed; fall back to the
      // first remaining media entry so focus never drops into the body.
      lastFocus =
        document.querySelector("[data-lightbox-open]") ||
        document.querySelector("[data-bookmark-post-card] a");
    }
  }

  function openOverlay(element) {
    if (!element) {
      return;
    }
    var grid = element.closest("[data-gallery]");
    if (grid) {
      tileItems = Array.prototype.slice.call(
        grid.querySelectorAll("[data-gallery-item]")
      );
      tileIndex = Math.max(0, tileItems.indexOf(element));
      viewerQuery = grid.getAttribute("data-viewer-query") || "";
    } else {
      tileItems = [element];
      tileIndex = 0;
      viewerQuery = "";
    }

    activeCreatorId = element.getAttribute("data-creator-id") || creatorId;
    payload = null;
    currentItem = null;
    currentIndex = 0;
    lastFocus = element;
    setFlash("");
    setMode("compact");
    root.hidden = false;
    resetChrome();
    document.body.classList.add("lightbox-open");
    paintTile(tileIndex);
    // Focus the dialog itself so arrow keys work immediately; Tab then walks
    // into the close/media controls.
    try {
      root.focus({ preventScroll: true });
    } catch (_error) {
      root.focus();
    }
    hydrate(element);
  }

  function closeOverlay() {
    hydrateToken += 1;
    jumpToken += 1;
    if (hydrateController) {
      hydrateController.abort();
      hydrateController = null;
    }
    if (jumpController) {
      jumpController.abort();
      jumpController = null;
    }
    exitFullscreen();
    clearPreloadCache();
    cancelPayloadPrefetch();
    payloadPending.clear();
    payloadEpoch += 1;
    setFlash("");
    unloadMedia();
    payload = null;
    currentItem = null;
    activeCreatorId = creatorId;
    viewerQuery = "";
    tileItems = [];
    tileIndex = 0;
    root.removeAttribute("aria-busy");
    resetChrome();
    root.hidden = true;
    setMode("closed");
    document.body.classList.remove("lightbox-open");
    syncBookmarkLibrary();
    if (lastFocus && lastFocus.focus) {
      try {
        lastFocus.focus({ preventScroll: true });
      } catch (_error) {
        lastFocus.focus();
      }
    }
    lastFocus = null;
  }

  // ------------------------------------------------------------------- //
  // Bookmarks (T9.2): `b` toggles media, `Shift+B` toggles the post.
  // ------------------------------------------------------------------- //

  function toggleBookmark(kind) {
    if (!activeCreatorId || !currentItem) {
      return;
    }
    var item = currentItem;
    var url;
    var nextPath = window.location.pathname + window.location.search;
    if (kind === "post") {
      var postId = item.postServiceId || (payload && payload.post && payload.post.service_id);
      if (!postId) {
        return;
      }
      url =
        "/c/" +
        activeCreatorId +
        "/p/" +
        encodeURIComponent(postId) +
        "/bookmark?format=json&next=" +
        encodeURIComponent(nextPath);
    } else {
      if (!item.sha256) {
        return;
      }
      url =
        "/c/" +
        activeCreatorId +
        "/media/" +
        encodeURIComponent(item.sha256) +
        "/bookmark?format=json&next=" +
        encodeURIComponent(nextPath);
    }

    fetch(url, {
      method: "POST",
      headers: { Accept: "application/json" },
      credentials: "same-origin"
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("bookmark request failed");
        }
        return response.json();
      })
      .then(function (data) {
        var bookmarked = Boolean(data && data.bookmarked);
        payloadEpoch += 1;
        if (kind === "post") {
          item.postBookmarked = bookmarked;
          if (bookmarked) {
            libraryPostsRemoved.delete(String(postId));
          } else {
            libraryPostsRemoved.add(String(postId));
          }
          if (payload && payload.post) {
            payload.post.bookmarked = bookmarked;
            updateCachedBookmarks(
              "post",
              payload.post.service_id,
              null,
              bookmarked
            );
          }
          syncBookmarkControl(item);
          announce(bookmarked ? "Post bookmarked" : "Bookmark removed");
          schedulePayloadPrefetch();
          return;
        }
        item.bookmarked = bookmarked;
        if (bookmarked) {
          libraryMediaRemoved.delete(item.sha256);
        } else {
          libraryMediaRemoved.add(item.sha256);
        }
        if (
          payload &&
          payload.media &&
          payload.media[currentIndex]
        ) {
          payload.media[currentIndex].bookmarked = bookmarked;
        }
        updateCachedBookmarks("media", null, item.sha256, bookmarked);
        announce(
          bookmarked
            ? kindLabel(item.kind) + " bookmarked"
            : "Bookmark removed"
        );
        schedulePayloadPrefetch();
      })
      .catch(function () {
        // Leave the previous state untouched; a retry is just another key.
        setFlash("Could not update the bookmark.");
      });
  }

  // ------------------------------------------------------------------- //
  // Input
  // ------------------------------------------------------------------- //

  function onKeydown(event) {
    if (mode === "closed") {
      return;
    }
    revealControls();
    if (event.key === "Escape" || event.key === "Esc") {
      var action = escapeAction(
        mode,
        Boolean(document.fullscreenElement),
        menuOpen
      );
      if (action === "close-menu") {
        event.preventDefault();
        closeMenu(true);
        return;
      }
      if (action === "exit-browser") {
        // The browser's own Escape gesture leaves fullscreen; the
        // fullscreenchange handler then settles on expanded.
        return;
      }
      event.preventDefault();
      closeOverlay();
      return;
    }

    if (event.key === "Tab") {
      trapTab(event);
      return;
    }

    if (
      event.ctrlKey ||
      event.altKey ||
      event.metaKey ||
      isTypingTarget(event.target) ||
      (document.activeElement &&
        (document.activeElement.tagName === "VIDEO" ||
          document.activeElement.tagName === "AUDIO"))
    ) {
      return;
    }

    if (event.key === "b" || event.key === "B") {
      event.preventDefault();
      dismissHint();
      toggleBookmark(event.key === "B" ? "post" : "media");
      return;
    }

    if (event.key === "f" || event.key === "F") {
      event.preventDefault();
      dismissHint();
      toggleFullscreen();
      return;
    }

    var action2 = {
      ArrowLeft: "prev-image",
      ArrowRight: "next-image",
      ArrowUp: "prev-post",
      ArrowDown: "next-post"
    }[event.key];
    if (!action2) {
      return;
    }
    event.preventDefault();
    dismissHint();
    go(action2);
  }

  function onWheel(event) {
    if (mode === "closed") {
      return;
    }
    revealControls();
    if (event.target && event.target.closest && event.target.closest("video")) {
      return;
    }
    event.preventDefault();

    var now = Date.now();
    if (now - lastWheel < 250) {
      return;
    }
    var dx = event.deltaX || 0;
    var dy = event.deltaY || 0;
    var horizontal = Math.abs(dx) > Math.abs(dy);
    if (Math.abs(horizontal ? dx : dy) < 24) {
      return;
    }
    lastWheel = now;
    dismissHint();
    if (horizontal) {
      go(dx > 0 ? "next-image" : "prev-image");
    } else {
      go(dy > 0 ? "next-post" : "prev-post");
    }
  }

  function onTouchStart(event) {
    if (mode !== "closed") {
      revealControls();
    }
    if (event.touches && event.touches.length === 1) {
      touchStart = {
        x: event.touches[0].clientX,
        y: event.touches[0].clientY
      };
    } else {
      touchStart = null;
    }
  }

  function onTouchEnd(event) {
    if (!touchStart || (event.touches && event.touches.length)) {
      touchStart = null;
      return;
    }
    var touch = event.changedTouches && event.changedTouches[0];
    if (!touch) {
      touchStart = null;
      return;
    }
    var dx = touch.clientX - touchStart.x;
    var dy = touch.clientY - touchStart.y;
    touchStart = null;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 40) {
      return;
    }
    lastTouchNav = Date.now();
    dismissHint();
    if (Math.abs(dx) > Math.abs(dy)) {
      go(dx < 0 ? "next-image" : "prev-image");
    } else {
      go(dy < 0 ? "prev-post" : "next-post");
    }
  }

  function afterTouch() {
    return Date.now() - lastTouchNav < 600;
  }

  function onStageClick(event) {
    if (mode !== "closed") {
      revealControls();
    }
    if (afterTouch()) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    dismissHint();
  }

  function onZoneClick(event) {
    event.preventDefault();
    event.stopPropagation();
    revealControls();
    if (afterTouch()) {
      return;
    }
    dismissHint();
    go(event.currentTarget.getAttribute("data-viewer-action"));
  }

  function onRootClick(event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest("[data-lightbox-more]")) {
      event.preventDefault();
      toggleMenu();
      return;
    }
    if (target.closest("[data-lightbox-menu]")) {
      // Menu links may navigate; the disclosure never stays open behind them.
      if (target.closest("a")) {
        closeMenu(false);
        return;
      }
      if (target.closest("[data-bookmark-button]")) {
        event.preventDefault();
        closeMenu(false);
        toggleBookmark("post");
        return;
      }
    }
    if (target.closest("[data-lightbox-controls-handle]")) {
      event.preventDefault();
      restoreControls();
      return;
    }
    if (target.closest("[data-lightbox-hide]")) {
      event.preventDefault();
      hideControls(true);
      return;
    }
    if (target.closest("[data-lightbox-post-prev]")) {
      event.preventDefault();
      go("prev-post");
      return;
    }
    if (target.closest("[data-lightbox-post-next]")) {
      event.preventDefault();
      go("next-post");
      return;
    }
    if (target.closest("[data-bookmark-button]")) {
      event.preventDefault();
      toggleBookmark("post");
      return;
    }
    if (target.closest("[data-lightbox-close]")) {
      event.preventDefault();
      closeOverlay();
      return;
    }
    if (target.closest("[data-lightbox-prev]")) {
      event.preventDefault();
      go("prev-image");
      return;
    }
    if (target.closest("[data-lightbox-next]")) {
      event.preventDefault();
      go("next-image");
      return;
    }
    if (target.closest("[data-lightbox-expand]")) {
      event.preventDefault();
      closeMenu(false);
      toggleExpand();
      return;
    }
    if (target.closest("[data-lightbox-fullscreen]")) {
      event.preventDefault();
      closeMenu(false);
      dismissHint();
      toggleFullscreen();
      return;
    }
    if (target === root) {
      // Clicking the backdrop unwinds one layer, like Escape.
      if (mode === "compact") {
        closeOverlay();
      } else if (mode !== "browser") {
        setMode("compact");
      }
    }
  }

  function onFullscreenChange() {
    if (document.fullscreenElement) {
      setMode("browser");
    } else if (mode === "browser") {
      setMode("expanded");
    }
    updateFullscreenControl();
  }

  function onVisibilityChange() {
    // A hidden tab gets no background downloads; coming back may warm once.
    if (document.visibilityState === "visible" && mediaReady) {
      schedulePreload();
      schedulePayloadPrefetch();
    }
  }

  // ------------------------------------------------------------------- //
  // Wiring
  // ------------------------------------------------------------------- //

  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    var opener = target.closest("[data-lightbox-open]");
    if (opener) {
      // Bookmark-style entry points carry their own tile data (T11.3); the
      // gallery's page-level button opens the first tile of this page.
      if (opener.hasAttribute("data-post-service-id")) {
        // The bookmark thumbnail stays a real link: modified clicks keep
        // their browser behaviour instead of being swallowed by the overlay.
        if (
          event.button === 0 &&
          !event.metaKey &&
          !event.ctrlKey &&
          !event.shiftKey &&
          !event.altKey
        ) {
          event.preventDefault();
          openOverlay(opener);
        }
        return;
      }
      event.preventDefault();
      var first = document.querySelector("[data-gallery] [data-gallery-item]");
      if (first) {
        openOverlay(first);
      }
      return;
    }

    var tile = target.closest("[data-gallery-item]");
    if (!tile) {
      return;
    }
    if (
      event.button === 0 &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.shiftKey &&
      !event.altKey
    ) {
      event.preventDefault();
      openOverlay(tile);
    }
  });

  document.addEventListener("keydown", onKeydown);
  document.addEventListener("visibilitychange", onVisibilityChange);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("pointermove", onActivity, { passive: true });
  document.addEventListener("touchstart", onActivity, { passive: true });
  root.addEventListener("click", onRootClick);

  // A click anywhere outside the disclosure (or its trigger) closes it; the
  // root handler above has already dealt with clicks inside the overlay.
  document.addEventListener("click", function (event) {
    if (!menuOpen) {
      return;
    }
    var target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (
      target.closest("[data-lightbox-menu]") ||
      target.closest("[data-lightbox-more]")
    ) {
      return;
    }
    closeMenu(false);
  });

  if (stage) {
    stage.addEventListener("wheel", onWheel, { passive: false });
    stage.addEventListener("touchstart", onTouchStart, { passive: true });
    stage.addEventListener("touchend", onTouchEnd, { passive: true });
    stage.addEventListener("click", onStageClick);
  }

  for (var zoneIndex = 0; zoneIndex < zones.length; zoneIndex += 1) {
    zones[zoneIndex].addEventListener("click", onZoneClick);
  }

  suppressHintForQuery();
  updateFullscreenControl();
  setMode("closed");
})();
