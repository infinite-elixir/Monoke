/* Monoke fullscreen viewer: one media item, post-scoped navigation. */
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

  var FOCUS_KEY = "monoke-viewer-focus";
  var dataNode = document.getElementById("viewer-initial");
  var root = document.querySelector("[data-viewer-root]");
  var state = null;
  try {
    state = JSON.parse(dataNode ? dataNode.textContent || "null" : "null");
  } catch (_error) {
    state = null;
  }
  if (!root || !state || !state.media || !state.media.length) {
    return;
  }

  var stage = root.querySelector("[data-viewer-stage]");
  var host = root.querySelector("[data-viewer-media]");
  var counter = root.querySelector("[data-viewer-counter]");
  var announcer = root.querySelector("[data-viewer-announcer]");
  var closeButton = root.querySelector("[data-viewer-close]");
  var fullscreenButton = root.querySelector("[data-viewer-fullscreen]");
  var flash = root.querySelector("[data-viewer-flash]");
  var hint = root.querySelector("[data-viewer-hint]");
  var nextPostZone = root.querySelector('[data-viewer-action="next-post"]');
  var zones = root.querySelectorAll("[data-viewer-action]");
  var creatorId = state.creator ? state.creator.id : null;
  var returnUrl =
    root.getAttribute("data-return-url") ||
    (creatorId ? "/c/" + creatorId : "/");

  var currentIndex = typeof state.index === "number" ? state.index : 0;
  var lastNav = 0;
  var lastWheel = 0;
  var lastTouchNav = 0;
  var touchStart = null;
  var jumpToken = 0;
  var jumpController = null;
  var fullscreenAttempted = false;
  var hintDismissed = false;
  var preloadMap = new Map();
  var preloadFailed = new Set();
  var preloadIdle = null;
  var preloadEpoch = 0;
  var mediaReady = false;
  var PAYLOAD_CACHE_MAX = 4;
  var payloadCache = new Map();
  var payloadPending = new Set();
  var payloadEpoch = 0;
  var payloadPrefetchIdle = null;
  var chromeTimer = null;

  function clamp(value, count) {
    if (value < 0) {
      return 0;
    }
    if (value > count - 1) {
      return count - 1;
    }
    return value;
  }

  function setFlash(message) {
    if (!flash) {
      return;
    }
    flash.textContent = message || "";
    flash.hidden = !message;
  }

  function prefersReducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_error) {
      return false;
    }
  }

  // The hint is onboarding, not a toolbar: hide it on the first interaction
  // and never bring it back for this page load (T8.4).
  function dismissHint() {
    revealViewerChrome();
    if (!hint || hintDismissed) {
      return;
    }
    hintDismissed = true;
    hint.setAttribute("aria-hidden", "true");
    var instant =
      root.classList.contains("viewer-quiet") || prefersReducedMotion();
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

  // T8.7: background warming. Everything here is an optimisation: any failure
  // or unsupported API leaves navigation exactly as it was.
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
    if (!state || !state.media || !state.media.length || !preloadAllowed()) {
      return;
    }
    var candidates = preloadCandidates(
      state.media,
      index,
      state.neighbour_images
    );
    var plan = buildPreloadPlan(
      state.media,
      index,
      state.neighbour_images,
      PRELOAD_TOTAL_MAX,
      preloadFailed
    );
    // Keeping the plan's URLs is what lets an already-warm destination edge
    // survive a post jump. Dropping a reference is enough; never touch
    // img.src, which can abort an in-flight load the browser may still use.
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

  function rememberPayload(payload) {
    if (!payload || !payload.post || !payload.post.service_id) {
      return;
    }
    var key = String(payload.post.service_id);
    payloadCache.delete(key);
    payloadCache.set(key, payload);
    while (payloadCache.size > PAYLOAD_CACHE_MAX) {
      var oldest = payloadCache.keys().next();
      if (oldest.done) {
        break;
      }
      payloadCache.delete(oldest.value);
    }
  }

  function updateCachedBookmarks(kind, postId, sha, bookmarked) {
    payloadCache.forEach(function (payload) {
      if (kind === "post") {
        if (
          payload.post &&
          String(payload.post.service_id) === String(postId)
        ) {
          payload.post.bookmarked = bookmarked;
        }
        return;
      }
      var media = payload.media || [];
      for (var cursor = 0; cursor < media.length; cursor += 1) {
        if (media[cursor].sha256 === sha) {
          media[cursor].bookmarked = bookmarked;
        }
      }
    });
  }

  function prefetchNeighbourPayloads() {
    if (!state || !creatorId || !preloadAllowed()) {
      return;
    }
    var ids = [state.previous_post, state.next_post];
    for (var cursor = 0; cursor < ids.length; cursor += 1) {
      var id = ids[cursor];
      if (!id) {
        continue;
      }
      id = String(id);
      if (payloadCache.has(id) || payloadPending.has(id)) {
        continue;
      }
      payloadPending.add(id);
      (function (pendingId, requestEpoch) {
        fetch(
          "/c/" +
            creatorId +
            "/viewer/data?post=" +
            encodeURIComponent(pendingId),
          { headers: { Accept: "application/json" } }
        )
          .then(function (response) {
            if (!response.ok) {
              throw new Error("neighbour payload request failed");
            }
            return response.json();
          })
          .then(
            function (payload) {
              payloadPending.delete(pendingId);
              if (requestEpoch === payloadEpoch) {
                rememberPayload(payload);
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
    if (!mediaReady || !preloadAllowed()) {
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

  function schedulePreload() {
    cancelPreload();
    if (!mediaReady || !preloadAllowed()) {
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

  function mediaBecameReady() {
    mediaReady = true;
    schedulePreload();
    schedulePayloadPrefetch();
  }

  // The visible image always wins the connection: preloading starts only after
  // the current media reports load/error (or is already complete on load).
  function watchCurrentMedia(element) {
    if (!element) {
      mediaReady = true;
      schedulePreload();
      schedulePayloadPrefetch();
      return;
    }
    if (element.tagName === "VIDEO") {
      element.addEventListener("loadedmetadata", mediaBecameReady, {
        once: true
      });
      element.addEventListener("error", mediaBecameReady, { once: true });
      if (element.readyState >= 1) {
        mediaBecameReady();
      }
      return;
    }
    element.addEventListener("load", mediaBecameReady, { once: true });
    element.addEventListener("error", mediaBecameReady, { once: true });
    if (element.complete) {
      mediaBecameReady();
    }
  }

  function unloadMedia() {
    // A pending idle callback belongs to the item being replaced; bumping the
    // epoch also neutralises it if the browser could not cancel the handle.
    cancelPreload();
    cancelPayloadPrefetch();
    preloadEpoch += 1;
    mediaReady = false;
    if (!host) {
      return;
    }
    var media = host.querySelector("[data-viewer-media-el]");
    if (media) {
      if (typeof media.pause === "function") {
        media.pause();
      }
      media.removeAttribute("src");
      media.removeAttribute("srcset");
      if (typeof media.load === "function") {
        media.load();
      }
    }
    host.replaceChildren();
  }

  function unavailableNode() {
    var box = document.createElement("div");
    box.className = "viewer-unavailable";

    var label = document.createElement("p");
    label.textContent = "Media unavailable";
    box.appendChild(label);

    var postUrl = state.post && state.post.post_url;
    if (postUrl) {
      var link = document.createElement("a");
      link.href = postUrl;
      link.textContent = "Open the post";
      box.appendChild(link);
    }
    return box;
  }

  function showUnavailable() {
    if (host && host.querySelector(".viewer-unavailable")) {
      return;
    }
    unloadMedia();
    if (host) {
      host.appendChild(unavailableNode());
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

  function updateCounter() {
    if (!state.media) {
      return;
    }
    var item = state.media[currentIndex] || {};
    var total = state.media.length;
    if (counter) {
      counter.textContent = currentIndex + 1 + " / " + total;
    }
    if (!announcer) {
      return;
    }
    var text =
      kindLabel(item.kind) +
      " " +
      (currentIndex + 1) +
      " of " +
      total +
      " in this post";
    var rank = Number(state.post_rank);
    var posts = Number(state.media_post_count);
    if (rank > 0 && posts > 0) {
      text += " · " + rank + " of " + posts + " media posts";
    }
    announcer.textContent = text;
  }

  function updateBottomZone(item) {
    if (!nextPostZone) {
      return;
    }
    // A short video leaves no room above its native control bar.
    nextPostZone.hidden =
      item.kind === "video" && root.clientHeight > 0 && root.clientHeight < 320;
  }

  function replaceUrl() {
    if (!window.history || !window.history.replaceState || !state.post) {
      return;
    }
    var params = new URLSearchParams(window.location.search);
    params.set("post", state.post.service_id);
    params.set("i", String(currentIndex));
    window.history.replaceState(
      null,
      "",
      window.location.pathname + "?" + params.toString()
    );
  }

  function render(index, updateUrl) {
    if (!state.media || !state.media.length) {
      return;
    }
    currentIndex = clamp(Number(index) || 0, state.media.length);
    var item = state.media[currentIndex];
    var element = null;

    unloadMedia();
    root.setAttribute("data-kind", item.kind || "file");

    if (!item.url) {
      showUnavailable();
    } else if (item.kind === "video") {
      var video = document.createElement("video");
      video.className = "viewer-media-el";
      video.setAttribute("data-viewer-media-el", "");
      video.setAttribute("controls", "");
      video.setAttribute("playsinline", "");
      video.setAttribute("preload", "metadata");
      if (item.thumb_url) {
        video.setAttribute("poster", item.thumb_url);
      }
      video.setAttribute("aria-label", item.filename || "Video");
      video.addEventListener("error", showUnavailable);
      video.setAttribute("src", item.url);
      host.appendChild(video);
      element = video;
    } else {
      var image = document.createElement("img");
      image.className = "viewer-media-el";
      image.setAttribute("data-viewer-media-el", "");
      image.setAttribute("decoding", "async");
      image.setAttribute(
        "alt",
        (state.post && state.post.title) || item.filename || "Media"
      );
      image.addEventListener("error", showUnavailable);
      image.setAttribute("src", item.url);
      host.appendChild(image);
      element = image;
    }

    updateCounter();
    updateBottomZone(item);
    if (updateUrl !== false) {
      replaceUrl();
    }
    watchCurrentMedia(element);
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

    if (action === "prev-image") {
      if (currentIndex > 0) {
        render(currentIndex - 1);
      } else if (state.previous_post) {
        // T8.6: the inverse of the move that entered this post lands on its
        // last media, which jump() resolves against the target payload.
        jump(state.previous_post, -1);
      }
      return;
    }
    if (action === "next-image") {
      if (currentIndex < state.media.length - 1) {
        render(currentIndex + 1);
      } else if (state.next_post) {
        jump(state.next_post, 0);
      }
      return;
    }
    if (action === "prev-post") {
      jump(state.previous_post);
      return;
    }
    if (action === "next-post") {
      jump(state.next_post);
    }
  }

  function applyPayload(next, index, token, signal) {
    if (token !== jumpToken || (signal && signal.aborted)) {
      return;
    }
    rememberPayload(next);
    state = next;
    // Up/Down and right-at-the-edge default to item 0; left-at-the-edge asks
    // for the last item of the target post. The new payload's media count is
    // the only valid clamp.
    var target = typeof index === "number" ? index : 0;
    if (target < 0) {
      target = state.media.length - 1;
    }
    render(target);
    state.index = currentIndex;
    root.removeAttribute("aria-busy");
  }

  function jump(serviceId, index) {
    if (!serviceId || !creatorId) {
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

    // T8.8: an idle prefetch usually makes a boundary crossing synchronous.
    // No revalidation: the viewer is a local, read-only snapshot for the
    // lifetime of the page, so a cached payload stays authoritative here.
    var cached = payloadCache.get(String(serviceId));
    if (cached) {
      applyPayload(cached, index, token, null);
      return;
    }

    jumpController = new AbortController();
    var signal = jumpController.signal;
    fetch(
      "/c/" + creatorId + "/viewer/data?post=" + encodeURIComponent(serviceId),
      { headers: { Accept: "application/json" }, signal: signal }
    )
      .then(function (response) {
        if (!response.ok) {
          throw new Error("viewer data request failed");
        }
        return response.json();
      })
      .then(function (next) {
        applyPayload(next, index, token, signal);
      })
      .catch(function (error) {
        if (token !== jumpToken || (error && error.name === "AbortError")) {
          return;
        }
        root.removeAttribute("aria-busy");
        setFlash("Could not load that post. Staying on the current media.");
      });
  }

  function updateFullscreenControl() {
    if (!fullscreenButton) {
      return;
    }
    var active = Boolean(document.fullscreenElement);
    fullscreenButton.setAttribute("aria-pressed", active ? "true" : "false");
    fullscreenButton.textContent = active ? "Exit fullscreen" : "Fullscreen";
    fullscreenButton.setAttribute(
      "aria-label",
      active ? "Exit fullscreen" : "Enter fullscreen"
    );
  }

  function revealViewerChrome() {
    if (!fullscreenButton) {
      return;
    }
    fullscreenButton.classList.add("is-revealed");
    if (chromeTimer) {
      window.clearTimeout(chromeTimer);
    }
    chromeTimer = window.setTimeout(function () {
      chromeTimer = null;
      fullscreenButton.classList.remove("is-revealed");
    }, 2500);
  }

  function onFullscreenClick(event) {
    event.preventDefault();
    event.stopPropagation();
    dismissHint();
    revealViewerChrome();
    toggleFullscreen();
  }

  function requestFullscreen(force) {
    if (!force && fullscreenAttempted) {
      return;
    }
    fullscreenAttempted = true;
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

  // A full navigation cannot move focus itself, so leave a hint for app.js on
  // the destination page: the actual entry link when there is one, otherwise
  // the creator heading after a direct load.
  function rememberReturnFocus() {
    var target =
      returnUrl.indexOf("/gallery") !== -1
        ? "[data-viewer-return]"
        : "#creator-heading";
    try {
      var opener = window.sessionStorage.getItem("monoke-viewer-opener");
      window.sessionStorage.removeItem("monoke-viewer-opener");
      if (opener) {
        target = opener;
      }
      window.sessionStorage.setItem(FOCUS_KEY, target);
    } catch (_error) {
      /* Storage disabled: navigation still works, only focus is lost. */
    }
  }

  function closeViewer() {
    unloadMedia();
    if (jumpController) {
      jumpController.abort();
    }
    exitFullscreen();
    rememberReturnFocus();
    cleanup();
    document.body.classList.remove("viewer-page");
    window.location.assign(returnUrl);
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
        node.offsetParent !== null
      );
    });
  }

  function isTypingTarget(target) {
    if (!target || !target.closest) {
      return false;
    }
    // The direction zones are navigation controls themselves: after a click
    // focuses one, arrow keys must keep working.
    if (target.closest(".viewer-zone")) {
      return false;
    }
    return Boolean(
      target.closest(
        "input, textarea, select, button, a, [contenteditable='true']"
      )
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

  // ------------------------------------------------------------------- //
  // Bookmarks (T9.2)
  //
  // The viewer stays chrome-free at rest: `b` toggles the current media and
  // `Shift+B` the current post, with only transient feedback. State lives in
  // the payload so a jump starts from the server's truth.
  // ------------------------------------------------------------------- //

  var toast = root.querySelector("[data-viewer-toast]");
  var toastTimer = null;

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

  function announceBookmark(message) {
    if (announcer) {
      announcer.textContent = message;
    }
    showToast(message);
  }

  function toggleBookmark(kind) {
    if (!creatorId || !state || !state.media || !state.media.length) {
      return;
    }
    var item = state.media[currentIndex];
    if (!item) {
      return;
    }

    var url;
    var nextPath = window.location.pathname + window.location.search;
    if (kind === "post") {
      if (!state.post || !state.post.service_id) {
        return;
      }
      url =
        "/c/" +
        creatorId +
        "/p/" +
        encodeURIComponent(state.post.service_id) +
        "/bookmark?format=json&next=" +
        encodeURIComponent(nextPath);
    } else {
      if (!item.sha256) {
        return;
      }
      url =
        "/c/" +
        creatorId +
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
          state.post.bookmarked = bookmarked;
          updateCachedBookmarks("post", state.post.service_id, null, bookmarked);
          announceBookmark(
            bookmarked ? "Post bookmarked" : "Bookmark removed"
          );
          schedulePayloadPrefetch();
          return;
        }
        item.bookmarked = bookmarked;
        updateCachedBookmarks("media", null, item.sha256, bookmarked);
        announceBookmark(
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

  function onKeydown(event) {
    dismissHint();

    if (event.key === "Escape" || event.key === "Esc") {
      event.preventDefault();
      // In fullscreen Escape belongs to the browser's own exit gesture: leave
      // the viewer open so a second Escape closes it (T8.4).
      if (document.fullscreenElement) {
        return;
      }
      closeViewer();
      return;
    }

    if (event.key === "Tab") {
      trapTab(event);
      return;
    }

    var auto = !fullscreenAttempted;
    if (auto) {
      requestFullscreen(false);
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
      // Shift+B toggles the post; plain b the current media item.
      toggleBookmark(event.key === "B" ? "post" : "media");
      return;
    }

    if (event.key === "f" || event.key === "F") {
      if (!auto) {
        event.preventDefault();
        toggleFullscreen();
      }
      return;
    }

    var action = {
      ArrowLeft: "prev-image",
      ArrowRight: "next-image",
      ArrowUp: "prev-post",
      ArrowDown: "next-post"
    }[event.key];
    if (!action) {
      return;
    }
    event.preventDefault();
    go(action);
  }

  function onWheel(event) {
    dismissHint();
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
    if (horizontal) {
      go(dx > 0 ? "next-image" : "prev-image");
    } else {
      go(dy > 0 ? "next-post" : "prev-post");
    }
  }

  function onTouchStart(event) {
    dismissHint();
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
    if (afterTouch()) {
      return;
    }
    dismissHint();
    go(event.currentTarget.getAttribute("data-viewer-action"));
  }

  function onCloseClick(event) {
    event.preventDefault();
    event.stopPropagation();
    closeViewer();
  }

  function onPointerDown(event) {
    dismissHint();
    if (
      event &&
      event.target &&
      event.target.closest &&
      event.target.closest("[data-viewer-fullscreen]")
    ) {
      return;
    }
    requestFullscreen(false);
  }

  function onTouchStartDocument(event) {
    dismissHint();
    if (
      event &&
      event.target &&
      event.target.closest &&
      event.target.closest("[data-viewer-fullscreen]")
    ) {
      return;
    }
    requestFullscreen(false);
  }

  function onPointerMove() {
    revealViewerChrome();
  }

  function onFullscreenChange() {
    root.classList.toggle("is-fullscreen", Boolean(document.fullscreenElement));
    updateFullscreenControl();
  }

  function onVisibilityChange() {
    // A hidden tab gets no background downloads; coming back may warm once.
    if (document.visibilityState === "visible" && mediaReady) {
      schedulePreload();
      schedulePayloadPrefetch();
    }
  }

  function cleanup() {
    clearPreloadCache();
    cancelPayloadPrefetch();
    payloadCache.clear();
    payloadPending.clear();
    if (chromeTimer) {
      window.clearTimeout(chromeTimer);
      chromeTimer = null;
    }
    document.removeEventListener("keydown", onKeydown);
    document.removeEventListener("visibilitychange", onVisibilityChange);
    document.removeEventListener("fullscreenchange", onFullscreenChange);
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("touchstart", onTouchStartDocument);
    if (stage) {
      stage.removeEventListener("wheel", onWheel);
      stage.removeEventListener("touchstart", onTouchStart);
      stage.removeEventListener("touchend", onTouchEnd);
      stage.removeEventListener("click", onStageClick);
    }
    for (var index = 0; index < zones.length; index += 1) {
      zones[index].removeEventListener("click", onZoneClick);
    }
    if (closeButton) {
      closeButton.removeEventListener("click", onCloseClick);
    }
    if (fullscreenButton) {
      fullscreenButton.removeEventListener("click", onFullscreenClick);
    }
  }

  document.addEventListener("keydown", onKeydown);
  document.addEventListener("visibilitychange", onVisibilityChange);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("pointermove", onPointerMove, { passive: true });
  document.addEventListener("touchstart", onTouchStartDocument, {
    passive: true
  });

  if (stage) {
    stage.addEventListener("wheel", onWheel, { passive: false });
    stage.addEventListener("touchstart", onTouchStart, { passive: true });
    stage.addEventListener("touchend", onTouchEnd, { passive: true });
    stage.addEventListener("click", onStageClick);
  }

  for (var index = 0; index < zones.length; index += 1) {
    zones[index].addEventListener("click", onZoneClick);
  }
  if (closeButton) {
    closeButton.addEventListener("click", onCloseClick);
  }
  if (fullscreenButton) {
    fullscreenButton.addEventListener("click", onFullscreenClick);
  }

  updateFullscreenControl();
  rememberPayload(state);
  updateCounter();
  updateBottomZone(state.media[currentIndex] || {});

  // The server renders the first item into the markup; the script may run
  // after it has already loaded, so watch the existing element too.
  watchCurrentMedia(
    host ? host.querySelector("[data-viewer-media-el]") : null
  );

  // Arrow keys must work immediately without a click (T8.4).
  try {
    root.focus({ preventScroll: true });
  } catch (_error) {
    root.focus();
  }
})();
