/* Monoke fullscreen viewer: one media item, post-scoped navigation. */
(function () {
  "use strict";

  // BEGIN preload-plan
  // BEGIN preload-window
  // T8.7/T8.18: one bounded, paint-aware window. The in-post floor is three
  // before/three after; the hard cap widens to five while the user swipes fast.
  // Videos/GIFs contribute a poster URL, never their bytes.
  var PRELOAD_RADIUS = 3;
  var PRELOAD_RADIUS_MAX = 5;
  var PRELOAD_MAX = PRELOAD_RADIUS * 2;
  var PRELOAD_TOTAL_MAX = 12;
  var DEEP_CONCURRENCY = 2;
  // Two committed moves closer together than this count as a fast flick; the
  // window widens one step per flick up to the hard cap and relaxes again when
  // the user pauses. Pace-based, never timer-based (T8.18 C).
  var SWIPE_PACE_MS = 900;

  function itemKind(item) {
    if (!item) {
      return "file";
    }
    var kind = String(item.kind || "").toLowerCase();
    var mime = String(item.mime_type || "").toLowerCase();
    var url = String(item.url || "").toLowerCase();
    var name = String(item.filename || item.original_filename || "").toLowerCase();
    function has(text, extensions) {
      for (var i = 0; i < extensions.length; i += 1) {
        var ext = extensions[i];
        if (
          text.indexOf("." + ext) !== -1 ||
          text.indexOf("%2e" + ext) !== -1
        ) {
          return true;
        }
      }
      return false;
    }
    if (
      mime.indexOf("video/") === 0 ||
      has(url, ["mp4", "webm", "mov", "m4v"]) ||
      has(name, ["mp4", "webm", "mov", "m4v"]) ||
      kind === "video"
    ) {
      return "video";
    }
    if (mime.indexOf("audio/") === 0 || kind === "audio") {
      return "audio";
    }
    if (
      mime === "image/gif" ||
      has(url, ["gif"]) ||
      has(name, ["gif"]) ||
      kind === "gif"
    ) {
      return "gif";
    }
    if (mime.indexOf("image/") === 0 || has(url, ["jpg", "jpeg", "png", "webp", "avif", "bmp"])) {
      return "image";
    }
    return kind || "file";
  }

  function isDataUrl(url) {
    return String(url || "").slice(0, 5) === "data:";
  }

  // The URL a preload entry should fetch: the image itself for stills, the
  // poster for videos/GIFs. Returns null when nothing can be painted safely.
  function preloadUrl(item) {
    if (!item) {
      return null;
    }
    var kind = itemKind(item);
    if (kind === "image") {
      var imageUrl = item.warm_url || item.url;
      return imageUrl && !isDataUrl(imageUrl) ? imageUrl : null;
    }
    if (kind === "video" || kind === "gif") {
      var poster = item.poster || item.thumb_url;
      return poster && !isDataUrl(poster) ? poster : null;
    }
    return null;
  }

  function preloadWindowIndices(media, index, failed, radius) {
    var indices = [];
    if (!media || !media.length) {
      return indices;
    }
    var width = typeof radius === "number" ? radius : PRELOAD_RADIUS;
    var start = Math.max(0, index - width);
    var end = Math.min(media.length - 1, index + width);
    for (var cursor = start; cursor <= end; cursor += 1) {
      if (cursor === index) {
        continue;
      }
      var item = media[cursor];
      var url = preloadUrl(item);
      if (!url) {
        continue;
      }
      if (failed && typeof failed.has === "function" && failed.has(url)) {
        continue;
      }
      indices.push(cursor);
    }
    return indices;
  }
  // END preload-window

  // A plan entry is ``{url, item, index, priority, source}``. The order is the
  // scheduling order: immediate neighbours, the direction of travel, then the
  // opposite side, then adjacent-post edges.
  function addPreloadCandidate(plan, seen, item, index, priority, source) {
    var url = preloadUrl(item);
    if (!url || seen[url]) {
      return;
    }
    seen[url] = true;
    plan.push({
      url: url,
      item: item,
      index: index,
      priority: priority,
      source: source
    });
  }

  function preloadPlan(media, index, neighbourImages, failed, radius, direction) {
    var plan = [];
    var seen = Object.create(null);
    var width = typeof radius === "number" ? radius : PRELOAD_RADIUS;
    var travel = direction === -1 ? -1 : 1;

    // 1/2. Immediate destinations are mounted by the ring but still belong in
    // the plan so a failed ring slot has a bounded retry source.
    addPreloadCandidate(
      plan, seen, media && media[index + travel], index + travel, 0,
      travel > 0 ? "next" : "previous"
    );
    addPreloadCandidate(
      plan, seen, media && media[index - travel], index - travel, 1,
      travel > 0 ? "previous" : "next"
    );

    // 3. The rest of the in-post window, direction of travel first.
    for (var distance = 2; distance <= width; distance += 1) {
      addPreloadCandidate(
        plan, seen, media && media[index + travel * distance],
        index + travel * distance, 2 + distance, "in-post"
      );
    }
    for (var opposite = 2; opposite <= width; opposite += 1) {
      addPreloadCandidate(
        plan, seen, media && media[index - travel * opposite],
        index - travel * opposite, 2 + width + opposite, "in-post"
      );
    }

    // 4. Adjacent-post edges. Interleave the travel side first so both post
    // boundaries are represented before the window goes deep.
    var previous = (neighbourImages && neighbourImages.previous) || [];
    var next = (neighbourImages && neighbourImages.next) || [];
    var prime = travel > 0 ? next : previous;
    var secondary = travel > 0 ? previous : next;
    var cursor;
    for (cursor = 0; cursor < prime.length; cursor += 1) {
      addPreloadCandidate(
        plan, seen, prime[cursor], null, 10 + cursor, "adjacent"
      );
    }
    for (cursor = 0; cursor < secondary.length; cursor += 1) {
      addPreloadCandidate(
        plan, seen, secondary[cursor], null, 10 + width + cursor, "adjacent"
      );
    }

    if (failed && typeof failed.has === "function") {
      var filtered = [];
      for (cursor = 0; cursor < plan.length; cursor += 1) {
        if (!failed.has(plan[cursor].url)) {
          filtered.push(plan[cursor]);
        }
      }
      plan = filtered;
    }
    return plan;
  }

  function preloadCandidates(media, index, neighbourImages) {
    var plan = preloadPlan(
      media, index, neighbourImages, null, PRELOAD_RADIUS, 1
    );
    var urls = [];
    for (var cursor = 0; cursor < plan.length; cursor += 1) {
      urls.push(plan[cursor].url);
    }
    return urls;
  }

  function buildPreloadPlan(media, index, neighbourImages, limit, failed) {
    var plan = preloadPlan(
      media, index, neighbourImages, failed, PRELOAD_RADIUS, 1
    );
    var urls = [];
    for (var cursor = 0; cursor < plan.length && urls.length < limit; cursor += 1) {
      urls.push(plan[cursor].url);
    }
    return urls;
  }

  // Adaptive window width. The floor stays the three-before/three-after
  // contract; consecutive fast flicks widen by one and a pause relaxes by one.
  function nextPreloadRadius(current, sinceLastNavigation) {
    var radius = Number(current) || PRELOAD_RADIUS;
    if (radius < PRELOAD_RADIUS) {
      radius = PRELOAD_RADIUS;
    }
    var gap = Number(sinceLastNavigation);
    if (sinceLastNavigation !== null && sinceLastNavigation !== undefined && gap <= SWIPE_PACE_MS) {
      return Math.min(PRELOAD_RADIUS_MAX, radius + 1);
    }
    return Math.max(PRELOAD_RADIUS, radius - 1);
  }

  function prunePreloadMap(map, keep, release) {
    map.forEach(function (entry, url) {
      if (keep.has(url)) {
        return;
      }
      if (!entry) {
        map.delete(url);
        return;
      }
      if (entry.role === "promoted" || entry.role === "active") {
        return;
      }
      // Already-started useful work is allowed to settle, but its callbacks
      // are generation-checked and it leaves the map as soon as it does.
      if (entry.state === "requested" || entry.state === "loaded") {
        entry.stale = true;
        return;
      }
      // A painted entry may still own a node in the paint bench (a ring slot a
      // later sync dropped). The caller passes its reclaim function so the node
      // and its source are released instead of leaking in the bench.
      if (typeof release === "function") {
        release(entry);
        if (map.get(url) === entry) {
          map.delete(url);
        }
        return;
      }
      entry.state = "cancelled";
      map.delete(url);
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

  // BEGIN transition-plan
  // T8.9: a move's semantic direction becomes a small enter/leave offset. The
  // helper is pure and Node-testable; null means "render without animation".
  var TRANSITION_MS = 200;
  var TRANSITION_SAFETY_MS = 500;
  var TRANSITION_HOLD_MS = 3000;
  var INCOMING_OFFSET = 0.08;
  var OUTGOING_OFFSET = 0.04;

  function transitionPlan(action) {
    if (action === "next-image") {
      return { x: 1, y: 0 };
    }
    if (action === "prev-image") {
      return { x: -1, y: 0 };
    }
    if (action === "next-post") {
      return { x: 0, y: 1 };
    }
    if (action === "prev-post") {
      return { x: 0, y: -1 };
    }
    return null;
  }
  // END transition-plan

  // BEGIN swipe-plan
  // T8.10/T8.11: the gesture policy is pure so the browser and the Node tests
  // agree on the axis lock, media-box geometry, distance/velocity commit and
  // rubber-banded archive edges.
  var SWIPE_SLOP = 9;
  // A locked axis must lead the other one by this ratio, or by a longer
  // fallback distance when the gesture is a genuine diagonal. This stops a
  // few pixels of vertical jitter from locking a vertical post swipe to x.
  var SWIPE_AXIS_DOMINANCE = 1.2;
  var SWIPE_AXIS_FALLBACK = 24;
  var SWIPE_TERMINAL_RATIO = 0.18;
  var SWIPE_TERMINAL_MAX = 96;
  var SWIPE_VELOCITY = 0.45;
  var SWIPE_RUBBER_BAND = 0.25;
  var TAP_MAX_DISTANCE = 8;
  var TAP_MAX_DURATION = 500;
  var TAP_ZONE_INSET = 0.12;
  var TAP_ZONE_WIDTH = 0.28;

  // Null means "still ambiguous"; the caller keeps waiting. Ties stay
  // uncommitted until the fallback distance, then the larger delta wins.
  function swipeAxis(dx, dy, slop, dominance, fallback) {
    var ax = Math.abs(Number(dx) || 0);
    var ay = Math.abs(Number(dy) || 0);
    var largest = Math.max(ax, ay);
    var smallest = Math.min(ax, ay);
    if (largest < slop) {
      return null;
    }
    if (
      largest >= fallback ||
      smallest === 0 ||
      largest >= smallest * dominance
    ) {
      return ax >= ay ? "x" : "y";
    }
    return null;
  }

  function swipeActionFor(axis, delta) {
    if (axis === "x") {
      return delta > 0 ? "prev-image" : "next-image";
    }
    // The vertical axis follows the finger: up opens the older post below,
    // while down opens the newer post above.
    return delta < 0 ? "next-post" : "prev-post";
  }

  // Fit an intrinsic media box inside the host's content box, mirroring the
  // stylesheet's `width/height: auto; max-width/height: 100%` rule. The
  // returned box is the visible media, never the stage, so a letterboxed
  // image cannot open a stage-sized black band.
  function fittedMediaBox(intrinsicWidth, intrinsicHeight, boxWidth, boxHeight) {
    var maxWidth = Number(boxWidth) > 0 ? Number(boxWidth) : 1;
    var maxHeight = Number(boxHeight) > 0 ? Number(boxHeight) : 1;
    var width =
      Number(intrinsicWidth) > 0 ? Number(intrinsicWidth) : maxWidth;
    var height =
      Number(intrinsicHeight) > 0 ? Number(intrinsicHeight) : maxHeight;
    var scale = Math.min(1, maxWidth / width, maxHeight / height);
    return { width: width * scale, height: height * scale };
  }

  // Both layers stay centred in the grid host. Their centres must therefore be
  // half the sum of the two visible widths/heights apart so the near edges
  // touch exactly, even when the two media have different letterboxed boxes.
  function swipeTrackStep(axis, current, incoming) {
    var currentSize =
      Number(axis === "x" ? current.width : current.height) || 0;
    var incomingSize =
      Number(axis === "x" ? incoming.width : incoming.height) || 0;
    return (currentSize + incomingSize) / 2;
  }

  // Drag offsets are 1:1 inside the archive and rubber-banded at an edge.
  // The incoming layer sits on the side the finger opens up: dragging
  // left/up reveals right/below.
  function swipeOffsets(axis, delta, resistance, step) {
    var scale = typeof resistance === "number" ? resistance : 1;
    var effective = delta * scale;
    var base = delta < 0 ? step : -step;
    return {
      drag:
        axis === "x"
          ? { x: effective, y: 0 }
          : { x: 0, y: effective },
      preview:
        axis === "x"
          ? { x: effective + base, y: 0 }
          : { x: 0, y: effective + base }
    };
  }

  function resolveSwipe(input) {
    var dx = Number(input && input.dx) || 0;
    var dy = Number(input && input.dy) || 0;
    var width = Number(input && input.width) > 0 ? Number(input.width) : 1;
    var height = Number(input && input.height) > 0 ? Number(input.height) : 1;
    var slop =
      input && typeof input.slop === "number" ? input.slop : SWIPE_SLOP;
    var vx = Number(input && input.vx) || 0;
    var vy = Number(input && input.vy) || 0;
    var hasDestination = !(input && input.hasDestination === false);
    var maxAbs = Math.max(Math.abs(dx), Math.abs(dy));
    var result = {
      commit: false,
      axis: null,
      action: null,
      delta: 0,
      velocity: 0,
      distance: maxAbs,
      threshold: 0,
      rubber: false,
      resistance: 1
    };
    if (maxAbs < slop) {
      return result;
    }
    var axis =
      (input && input.axis) ||
      swipeAxis(dx, dy, slop, SWIPE_AXIS_DOMINANCE, SWIPE_AXIS_FALLBACK);
    if (!axis) {
      return result;
    }
    var delta = axis === "x" ? dx : dy;
    var velocity = axis === "x" ? vx : vy;
    var dimension = axis === "x" ? width : height;
    var threshold = Math.min(
      SWIPE_TERMINAL_MAX,
      dimension * SWIPE_TERMINAL_RATIO
    );
    var fast =
      Math.abs(velocity) > SWIPE_VELOCITY &&
      (velocity > 0) === (delta > 0);
    result.axis = axis;
    result.action = swipeActionFor(axis, delta);
    result.delta = delta;
    result.velocity = velocity;
    result.threshold = threshold;
    result.rubber = !hasDestination;
    result.resistance = hasDestination ? 1 : SWIPE_RUBBER_BAND;
    result.commit =
      hasDestination && (Math.abs(delta) >= threshold || fast);
    return result;
  }
  // END swipe-plan

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

  var bookmarksMode = state.mode === "bookmarks";

  var stage = root.querySelector("[data-viewer-stage]");
  var host = root.querySelector("[data-viewer-media]");
  var counter = root.querySelector("[data-viewer-counter]");
  var announcer = root.querySelector("[data-viewer-announcer]");
  var closeButton = root.querySelector("[data-viewer-close]");
  var fullscreenButton = root.querySelector("[data-viewer-fullscreen]");
  var flash = root.querySelector("[data-viewer-flash]");
  var hint = root.querySelector("[data-viewer-hint]");
  var creatorId = state.creator ? state.creator.id : null;
  var returnUrl =
    root.getAttribute("data-return-url") ||
    (bookmarksMode
      ? "/bookmarks"
      : creatorId
        ? "/c/" + creatorId
        : "/");

  var currentIndex = typeof state.index === "number" ? state.index : 0;
  var lastNav = 0;
  var lastWheel = 0;
  var jumpToken = 0;
  var jumpController = null;
  var fullscreenAttempted = false;
  var hintDismissed = false;
  var preloadMap = new Map();
  var preloadFailed = new Set();
  var preloadEpoch = 0;
  var preloadGeneration = 0;
  var mediaReady = false;
  var ring = { previous: null, next: null };
  var preloadStats = {
    hits: 0,
    misses: 0,
    duplicates: 0,
    promotions: 0,
    failures: 0,
    painted: 0
  };
  // T8.18 E: bounded timing evidence for the browser acceptance pass. It shows
  // the destination request started before the gesture and was not repeated on
  // commit (one `requests` per URL).
  var debugTiming = new Map();
  var DEBUG_TIMING_MAX = 24;

  function noteTiming(entry, field) {
    if (!entry || !entry.key) {
      return null;
    }
    var row = debugTiming.get(entry.key);
    if (!row) {
      row = {
        url: entry.key,
        requests: 0,
        requestedAt: null,
        paintedAt: null,
        promotedAt: null
      };
      debugTiming.set(entry.key, row);
      while (debugTiming.size > DEBUG_TIMING_MAX) {
        var oldest = debugTiming.keys().next();
        if (oldest.done) {
          break;
        }
        debugTiming.delete(oldest.value);
      }
    }
    if (field === "requestedAt") {
      row.requests += 1;
    }
    if (field) {
      row[field] = Date.now();
    }
    return row;
  }
  var paintBench = root.querySelector("[data-viewer-paint-bench]");
  var deepQueue = [];
  var deepActive = 0;
  var adaptiveRadius = PRELOAD_RADIUS;
  var lastSwipeAt = 0;
  var navigationDirection = 1;
  var PAYLOAD_CACHE_MAX = 4;
  var payloadCache = new Map();
  var payloadPending = new Set();
  var payloadEpoch = 0;
  var payloadPrefetchIdle = null;
  var payloadPrefetchRadius = 2;
  var chromeTimer = null;
  // T8.9: at most one committed layer, one incoming layer and one temporary
  // drag preview (never all three at once).
  var activeLayer = null;
  var incomingLayer = null;
  var previewLayer = null;
  var transitionToken = 0;
  var transitionTimer = null;
  var holdTimer = null;
  // T8.10: one pointer gesture at a time.
  var swipe = null;
  var swipeFrame = null;
  var suppressNextClick = false;
  var suppressClickTimer = null;
  var manifestByServiceId = Object.create(null);
  var manifestOrder = [];
  var debugLog = [];

  function clamp(value, count) {
    if (value < 0) {
      return 0;
    }
    if (value > count - 1) {
      return count - 1;
    }
    return value;
  }

  function itemCreatorId(item) {
    if (item && item.creator_id !== undefined && item.creator_id !== null) {
      return item.creator_id;
    }
    return creatorId;
  }

  function installManifest(manifest) {
    manifestByServiceId = Object.create(null);
    manifestOrder = [];
    if (!manifest || !manifest.posts || !manifest.posts.length) {
      return;
    }
    for (var cursor = 0; cursor < manifest.posts.length; cursor += 1) {
      var entry = manifest.posts[cursor];
      if (!entry || !entry.service_id) {
        continue;
      }
      var serviceId = String(entry.service_id);
      manifestByServiceId[serviceId] = entry;
      manifestOrder.push(serviceId);
    }
  }

  function noteRender(item, source, element) {
    var entry = element && element.__viewerRingEntry;
    debugLog.push({
      at: Date.now(),
      index: currentIndex,
      kind: itemKind(item),
      url: item && item.url,
      source: source,
      generation: preloadGeneration,
      complete: Boolean(element && element.complete),
      naturalWidth: element && element.naturalWidth,
      naturalHeight: element && element.naturalHeight,
      preloadState: entry ? entry.state : null,
      ready: Boolean(element && (entry || imageIsReady(element)))
    });
    if (debugLog.length > 40) {
      debugLog.shift();
    }
  }

  function installDebug() {
    if (!window.__monokeViewerDebug) {
      window.__monokeViewerDebug = {
        snapshot: function () {
          var counts = Object.create(null);
          preloadMap.forEach(function (entry) {
            counts[entry.state] = (counts[entry.state] || 0) + 1;
          });
          return {
            generation: preloadGeneration,
            index: currentIndex,
            entries: preloadMap.size,
            states: counts,
            liveWarm: paintBench ? paintBench.children.length : 0,
            windowWidth: adaptiveRadius,
            queued: deepQueue.length,
            active: activeLayer && activeLayer.__viewerItem
              ? activeLayer.__viewerItem.sha256 || activeLayer.__viewerItem.url
              : null,
            stats: Object.assign({}, preloadStats),
            timing: Array.from(debugTiming.values()),
            ring: {
              previous: ring.previous ? ring.previous.key : null,
              next: ring.next ? ring.next.key : null
            },
            log: debugLog.slice()
          };
        }
      };
    }
    return window.__monokeViewerDebug;
  }

  function manifestEntry(serviceId) {
    if (serviceId === undefined || serviceId === null) {
      return null;
    }
    return manifestByServiceId[String(serviceId)] || null;
  }

  function currentViewerContext() {
    if (state && state.gf) {
      return state.gf;
    }
    var params = new URLSearchParams(window.location.search);
    var token = params.get("gf");
    return token || "";
  }

  function manifestDataUrl(serviceId) {
    var entry = manifestEntry(serviceId);
    if (entry && entry.data_url) {
      return entry.data_url;
    }
    var base = "/c/" + creatorId + "/viewer/data?post=" + encodeURIComponent(serviceId);
    var gf = currentViewerContext();
    if (gf) {
      return base + "&gf=" + encodeURIComponent(gf);
    }
    return base;
  }

  function payloadPlan() {
    if (!state || bookmarksMode) {
      return [];
    }
    var ids = [];
    var seen = Object.create(null);
    function add(serviceId) {
      if (!serviceId) {
        return;
      }
      serviceId = String(serviceId);
      if (seen[serviceId]) {
        return;
      }
      seen[serviceId] = true;
      ids.push(serviceId);
    }
    add(state.previous_post);
    add(state.next_post);
    if (!state.post || !state.post.service_id || !manifestOrder.length) {
      return ids;
    }
    var current = String(state.post.service_id);
    var index = manifestOrder.indexOf(current);
    if (index === -1) {
      return ids;
    }
    for (var distance = 2; distance <= payloadPrefetchRadius + 1; distance += 1) {
      var newer = manifestOrder[index - distance];
      var older = manifestOrder[index + distance];
      add(newer);
      add(older);
    }
    return ids;
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

  function releaseEntry(entry) {
    if (!entry) {
      return;
    }
    if (ring.previous === entry) {
      ring.previous = null;
    }
    if (ring.next === entry) {
      ring.next = null;
    }
    if (preloadMap.get(entry.key) === entry) {
      preloadMap.delete(entry.key);
    }
    var element = entry.element;
    if (
      element &&
      !element.__viewerPromoted &&
      element.__viewerRole !== "active"
    ) {
      if (paintBench && element.parentNode === paintBench) {
        element.parentNode.removeChild(element);
      }
      if (String(element.tagName).toUpperCase() === "IMG") {
        element.onload = null;
        element.onerror = null;
        element.removeAttribute("src");
      }
    }
    entry.state = "cancelled";
  }

  function releaseRing() {
    releaseEntry(ring.previous);
    releaseEntry(ring.next);
    ring.previous = null;
    ring.next = null;
  }

  function clearPreloadCache() {
    preloadGeneration += 1;
    preloadEpoch += 1;
    releaseRing();
    preloadMap.forEach(function (entry) {
      releaseEntry(entry);
    });
    preloadMap.clear();
    preloadFailed.clear();
    deepQueue = [];
    deepActive = 0;
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

  function paintedAfterFrame(entry, callback) {
    function frame() {
      if (entry.generation !== preloadGeneration || entry.state === "cancelled") {
        return;
      }
      callback();
    }
    if (typeof window.requestAnimationFrame === "function") {
      window.requestAnimationFrame(function () {
        window.requestAnimationFrame(frame);
      });
    } else {
      window.setTimeout(frame, 32);
    }
  }

  function markRingPainted(entry) {
    if (entry.generation !== preloadGeneration || entry.stale) {
      releaseEntry(entry);
      return;
    }
    var element = entry.element;
    if (!element || !element.complete || !element.naturalWidth) {
      return;
    }
    function painted() {
      if (
        entry.generation !== preloadGeneration ||
        entry.state === "cancelled" ||
        entry.state === "promoted" ||
        entry.state === "active"
      ) {
        return;
      }
      if (entry.state !== "painted") {
        preloadStats.painted += 1;
      }
      entry.state = "painted";
      entry.ready = true;
      noteTiming(entry, "paintedAt");
      positionRing();
    }
    if (typeof element.decode === "function") {
      element.decode().then(
        function () {
          paintedAfterFrame(entry, painted);
        },
        function () {
          /* load/complete still proves a bitmap; paint happens next frame. */
          paintedAfterFrame(entry, painted);
        }
      );
    } else {
      paintedAfterFrame(entry, painted);
    }
  }

  // The thumbnail retry for a broken still keeps the same dimensions/role but
  // paints the post thumbnail. It is never promoted as the item itself; it only
  // keeps the ring slot non-black (T8.17/T8.18).
  function ringPosterFallback(item) {
    if (!item || !item.thumb_url || isDataUrl(item.thumb_url)) {
      return null;
    }
    return Object.assign({}, item, {
      url: item.thumb_url,
      warm_url: item.thumb_url,
      thumb_url: null,
      poster: null
    });
  }

  // Ring entries own the load/error handling of their bench node. A promoted
  // node becomes a normal viewer layer, so it must hand those handlers back to
  // setupMediaElement()/onMediaError(); otherwise a failed active image would
  // still run the ring's "fetch a poster fallback" path and orphan a bench node.
  function bindRingHandlers(entry) {
    var element = entry.element;
    if (!element) {
      return;
    }
    element.onload = function () {
      if (entry.generation !== preloadGeneration || entry.stale) {
        releaseEntry(entry);
        return;
      }
      if (entry.state !== "promoted" && entry.state !== "active") {
        entry.state = "loaded";
      }
      markRingPainted(entry);
    };
    element.onerror = function () {
      if (entry.generation !== preloadGeneration) {
        return;
      }
      entry.state = "failed";
      entry.stale = true;
      preloadStats.failures += 1;
      rememberPreloadFailure(entry.key);
      releaseEntry(entry);
      // One thumbnail retry is allowed for a still image; never loop. The
      // `fallbackFor` marker lets syncRingSlot recognise this slot as the
      // poster for `key` instead of re-requesting the broken URL every commit.
      var fallback = entry.role ? ringPosterFallback(entry.item) : null;
      if (fallback && entry.item.thumb_url !== entry.key) {
        var retry = createRingEntry(fallback, entry.role);
        if (retry) {
          retry.fallbackFor = entry.key;
          retry.item = entry.item;
          if (ring[entry.role] === null) {
            ring[entry.role] = retry;
            positionRing();
          }
        }
      }
    };
  }

  function unbindRingHandlers(entry) {
    if (entry && entry.element) {
      entry.element.onload = null;
      entry.element.onerror = null;
    }
  }

  function createRingEntry(item, role) {
    var key = preloadUrl(item);
    if (!key || typeof Image !== "function") {
      return null;
    }
    var existing = preloadMap.get(key);
    if (existing && existing.element) {
      // A second node for an owned URL would double the request and the bench
      // node (T8.18 B/E). The caller normally reuses the entry first.
      preloadStats.duplicates += 1;
    }
    var element = document.createElement("img");
    element.className = "viewer-media-el viewer-warm";
    element.__viewerItem = item;
    element.__viewerRole = role;
    element.__viewerPromoted = false;
    element.setAttribute("aria-hidden", "true");
    element.setAttribute("decoding", "async");
    if (Number(item.width) > 0) {
      element.setAttribute("width", String(item.width));
    }
    if (Number(item.height) > 0) {
      element.setAttribute("height", String(item.height));
    }
    var entry = {
      key: key,
      item: item,
      element: element,
      role: role,
      state: "queued",
      ready: false,
      stale: false,
      generation: preloadGeneration,
      priority: 0,
      source: role
    };
    preloadMap.set(key, entry);
    if (paintBench) {
      paintBench.appendChild(element);
    }
    bindRingHandlers(entry);
    entry.state = "requested";
    noteTiming(entry, "requestedAt");
    element.setAttribute("src", key);
    if (element.complete && element.naturalWidth) {
      markRingPainted(entry);
    }
    return entry;
  }

  // A deep-window loader that already finished loading/decoding owns the one
  // request for this URL. Moving that same element into the bench turns it into
  // the ring node without a second request, decode or node (T8.18 B/F).
  function adoptDeepEntry(entry, role, item) {
    var element = entry.element;
    if (!element || !paintBench) {
      return null;
    }
    element.className = "viewer-media-el viewer-warm";
    element.__viewerItem = item;
    element.__viewerRole = role;
    element.__viewerPromoted = false;
    element.setAttribute("aria-hidden", "true");
    element.setAttribute("decoding", "async");
    if (Number(item.width) > 0) {
      element.setAttribute("width", String(item.width));
    }
    if (Number(item.height) > 0) {
      element.setAttribute("height", String(item.height));
    }
    element.style.transform = "";
    element.style.opacity = "";
    element.style.pointerEvents = "none";
    entry.role = role;
    entry.item = item;
    entry.fallbackFor = null;
    entry.stale = false;
    entry.generation = preloadGeneration;
    entry.source = "in-post";
    preloadMap.set(entry.key, entry);
    bindRingHandlers(entry);
    paintBench.appendChild(element);
    ring[role] = entry;
    markRingPainted(entry);
    positionRing();
    return entry;
  }

  function positionRing() {
    if (!paintBench || !activeLayer) {
      return;
    }
    var dims = stageSize();
    // The host is the media content box (the stage adds safe-area padding).
    // Ring boxes must match the box the drag preview will use.
    var width = host && host.clientWidth ? host.clientWidth : dims.width;
    var height = host && host.clientHeight ? host.clientHeight : dims.height;
    var item = state.media[currentIndex] || {};
    var activeBox =
      layerBox(activeLayer) || fittedMediaBox(item.width, item.height, width, height);
    ["previous", "next"].forEach(function (role) {
      var entry = ring[role];
      if (!entry || !entry.element) {
        return;
      }
      var box =
        layerBox(entry.element) ||
        fittedMediaBox(
          entry.item && entry.item.width,
          entry.item && entry.item.height,
          width,
          height
        );
      var step = swipeTrackStep("x", activeBox, box);
      entry.box = box;
      entry.step = step;
      entry.element.style.transform = translateFor({
        x: role === "previous" ? -step : step,
        y: 0
      });
    });
  }

  // A ring entry is only promotable while its exact painted node is still
  // mounted in the bench with a live source. A decode-complete detached node is
  // not enough (T8.18): moving an unpainted element is how a black frame gets
  // committed. The same node is handed to the media host so commit does not
  // create, fetch or decode a second copy.
  function ringEntryFor(item) {
    var key = preloadUrl(item);
    if (!key) {
      return null;
    }
    var entry = preloadMap.get(key);
    if (
      !entry ||
      entry.stale ||
      entry.state !== "painted" ||
      !entry.element ||
      !paintBench ||
      entry.element.parentNode !== paintBench ||
      !entry.element.getAttribute("src")
    ) {
      return null;
    }
    return entry;
  }

  function promoteRingEntry(entry) {
    if (!entry || !entry.element) {
      return null;
    }
    var element = entry.element;
    if (ring.previous === entry) {
      ring.previous = null;
    }
    if (ring.next === entry) {
      ring.next = null;
    }
    element.__viewerPromoted = true;
    element.__viewerRingEntry = entry;
    unbindRingHandlers(entry);
    element.classList.remove("viewer-warm", "is-preview", "is-loading");
    element.removeAttribute("aria-hidden");
    element.style.pointerEvents = "";
    element.style.transform = "";
    element.style.opacity = "";
    preloadStats.promotions += 1;
    noteTiming(entry, "promotedAt");
    entry.role = "promoted";
    entry.state = "promoted";
    entry.stale = false;
    entry.generation = preloadGeneration;
    return element;
  }

  // A cancelled drag/preview returns its painted node to the bench instead of
  // clearing `src` or throwing the decoded bitmap away.
  function returnRingEntry(entry, role) {
    if (!entry || !entry.element) {
      return null;
    }
    var element = entry.element;
    var target = role === "previous" || role === "next" ? role : "next";
    element.__viewerPromoted = false;
    element.__viewerRingEntry = entry;
    element.__viewerRole = target;
    element.__viewerPreview = false;
    element.__viewerPreviewFailed = false;
    element.classList.remove(
      "is-transitioning",
      "is-entering",
      "is-leaving",
      "is-waiting",
      "is-preview",
      "is-loading",
      "viewer-image",
      "viewer-video"
    );
    element.classList.add("viewer-warm");
    element.setAttribute("aria-hidden", "true");
    element.style.transform = "";
    element.style.opacity = "";
    element.style.willChange = "";
    element.style.pointerEvents = "none";
    removePlaceholder(element);
    entry.role = target;
    entry.state = "painted";
    entry.ready = true;
    entry.stale = false;
    entry.generation = preloadGeneration;
    preloadMap.set(entry.key, entry);
    ring[target] = entry;
    bindRingHandlers(entry);
    if (paintBench) {
      paintBench.appendChild(element);
    }
    positionRing();
    return entry;
  }

  // After a commit the old active is usually the new `i - 1` (or `i + 1` when
  // navigating backwards). Park that still image as the ring node instead of
  // deleting it and warming a duplicate copy (T8.18 F).
  function parkOutgoing(layer) {
    if (!layer || !layer.parentNode || layer.__viewerRemoved) {
      return false;
    }
    var item = layer.__viewerItem;
    if (
      itemKind(item) !== "image" ||
      String(layer.tagName).toUpperCase() !== "IMG" ||
      !layer.getAttribute("src")
    ) {
      return false;
    }
    var key = preloadUrl(item);
    if (!key || layer.getAttribute("src") !== item.url) {
      return false;
    }
    var role = null;
    var previous = neighbourItem(-1);
    var next = neighbourItem(1);
    if (previous && preloadUrl(previous) === key) {
      role = "previous";
    } else if (next && preloadUrl(next) === key) {
      role = "next";
    }
    if (!role) {
      return false;
    }
    var entry = layer.__viewerRingEntry || preloadMap.get(key);
    if (entry && entry.element !== layer) {
      releaseEntry(entry);
      entry = null;
    }
    if (!entry) {
      entry = {
        key: key,
        item: item,
        element: layer,
        role: role,
        state: "painted",
        ready: true,
        stale: false,
        generation: preloadGeneration,
        priority: 0,
        source: "active"
      };
      preloadMap.set(key, entry);
    }
    if (layer === activeLayer) {
      activeLayer = null;
    }
    if (layer === incomingLayer) {
      incomingLayer = null;
    }
    if (layer === previewLayer) {
      previewLayer = null;
    }
    return Boolean(returnRingEntry(entry, role));
  }

  // Do not create a duplicate neighbour node while the outgoing layer still
  // occupies the media host. It will be parked as that slot when the transition
  // finishes.
  function leavingLayerFor(key) {
    if (!host || !key) {
      return null;
    }
    var layers = host.querySelectorAll("[data-viewer-media-el].is-leaving");
    for (var cursor = 0; cursor < layers.length; cursor += 1) {
      if (preloadUrl(layers[cursor].__viewerItem) === key) {
        return layers[cursor];
      }
    }
    return null;
  }

  function clearRingSlot(role) {
    var entry = ring[role];
    if (!entry) {
      return;
    }
    releaseEntry(entry);
    ring[role] = null;
  }

  function syncRingSlot(role, item) {
    var key = preloadUrl(item);
    if (!key) {
      clearRingSlot(role);
      return;
    }
    var current = ring[role];
    // A poster fallback is the valid slot for this item even though its key is
    // the thumbnail URL; re-creating the broken full-size entry would retry on
    // every commit (T8.17 "never retry forever").
    if (
      current &&
      (current.key === key || current.fallbackFor === key) &&
      current.state !== "failed"
    ) {
      current.item = item;
      positionRing();
      return;
    }
    var leaving = leavingLayerFor(key);
    if (leaving) {
      if (current) {
        clearRingSlot(role);
      }
      return;
    }
    var existing = preloadMap.get(key);
    if (
      existing &&
      existing.element &&
      paintBench &&
      existing.element.parentNode === paintBench &&
      existing.state !== "failed"
    ) {
      clearRingSlot(role);
      existing.role = role;
      existing.fallbackFor = null;
      existing.item = item;
      ring[role] = existing;
      positionRing();
      return;
    }
    if (existing && existing.element && existing.element.parentNode === host) {
      // The URL is already mounted as the active or leaving layer (duplicate
      // sha across posts). Never create a second node for the same URL; the
      // host already paints it and parkOutgoing() will re-role it when eligible.
      preloadStats.duplicates += 1;
      if (current) {
        clearRingSlot(role);
      }
      return;
    }
    if (existing && existing.element) {
      if (existing.state === "decoded" || existing.state === "loaded") {
        // The deep window already fetched and decoded this URL: adopt that
        // exact element as the ring node instead of requesting it again.
        clearRingSlot(role);
        adoptDeepEntry(existing, role, item);
        return;
      }
      if (existing.state === "queued" || existing.state === "requested") {
        // The one request for this URL is still in flight. Wait for it and let
        // the deep queue's settle hook refresh the ring, rather than opening a
        // second element/request (T8.18 B).
        preloadStats.duplicates += 1;
        if (current) {
          clearRingSlot(role);
        }
        return;
      }
    }
    if (preloadFailed.has(key)) {
      // Do not re-request a URL known to fail. Reuse or create the bounded
      // poster fallback once, or leave the slot explicitly empty.
      var thumbKey = item && item.thumb_url;
      var fallbackEntry = thumbKey ? preloadMap.get(thumbKey) : null;
      if (
        fallbackEntry &&
        fallbackEntry.element &&
        paintBench &&
        fallbackEntry.element.parentNode === paintBench &&
        fallbackEntry.state !== "failed"
      ) {
        clearRingSlot(role);
        fallbackEntry.role = role;
        fallbackEntry.fallbackFor = key;
        fallbackEntry.item = item;
        ring[role] = fallbackEntry;
        positionRing();
        return;
      }
      if (current) {
        clearRingSlot(role);
      }
      var poster =
        thumbKey && !preloadFailed.has(thumbKey)
          ? ringPosterFallback(item)
          : null;
      var retry = poster ? createRingEntry(poster, role) : null;
      if (retry) {
        retry.fallbackFor = key;
        retry.item = item;
        ring[role] = retry;
        positionRing();
      }
      return;
    }
    if (current) {
      clearRingSlot(role);
    }
    var entry = createRingEntry(item, role);
    if (entry) {
      ring[role] = entry;
      positionRing();
    }
  }

  function neighbourItem(direction) {
    if (!state || !state.media) {
      return null;
    }
    var index = currentIndex + direction;
    if (index >= 0 && index < state.media.length) {
      return state.media[index];
    }
    var serviceId =
      direction < 0 ? state.previous_post : state.next_post;
    if (!serviceId) {
      return null;
    }
    var payload = cachedPostPayload(serviceId);
    if (payload) {
      return direction < 0 ? lastMedia(payload) : firstMedia(payload);
    }
    var edges =
      (state.neighbour_images &&
        (direction < 0
          ? state.neighbour_images.previous
          : state.neighbour_images.next)) ||
      [];
    return edges[0] || null;
  }

  function isRingNeighbour(key) {
    if (!key) {
      return false;
    }
    var previous = neighbourItem(-1);
    var next = neighbourItem(1);
    return Boolean(
      (previous && preloadUrl(previous) === key) ||
        (next && preloadUrl(next) === key)
    );
  }

  function pumpDeepQueue() {
    while (deepActive < DEEP_CONCURRENCY && deepQueue.length) {
      var entry = deepQueue.shift();
      if (
        !entry ||
        entry.generation !== preloadGeneration ||
        entry.state !== "queued" ||
        preloadMap.get(entry.key) !== entry
      ) {
        continue;
      }
      if (typeof Image !== "function") {
        return;
      }
      var element = new Image();
      entry.element = element;
      entry.state = "requested";
      deepActive += 1;
      (function (activeEntry) {
        function settle() {
          deepActive = Math.max(0, deepActive - 1);
          if (
            activeEntry.stale ||
            activeEntry.generation !== preloadGeneration
          ) {
            releaseEntry(activeEntry);
          } else if (isRingNeighbour(activeEntry.key)) {
            // The URL that was loading just became an immediate neighbour: put
            // the exact decoded element in the bench now.
            refreshPreloadWindow();
          }
          pumpDeepQueue();
        }
        element.onload = function () {
          if (
            activeEntry.generation !== preloadGeneration ||
            activeEntry.stale
          ) {
            settle();
            return;
          }
          activeEntry.state = "loaded";
          if (typeof element.decode === "function") {
            element.decode().then(
              function () {
                activeEntry.state = "decoded";
                settle();
              },
              function () {
                activeEntry.state = "loaded";
                settle();
              }
            );
          } else {
            activeEntry.state = "decoded";
            settle();
          }
        };
        element.onerror = function () {
          activeEntry.state = "failed";
          rememberPreloadFailure(activeEntry.key);
          settle();
        };
      })(entry);
      element.fetchPriority = "low";
      noteTiming(entry, "requestedAt");
      element.src = entry.key;
    }
  }

  function queueDeep(planEntry) {
    var key = planEntry.url;
    if (!key) {
      return;
    }
    if (preloadMap.has(key)) {
      preloadStats.hits += 1;
      // The URL already owns a node/request; the plan would otherwise have
      // started a second one (T8.18 B/E).
      preloadStats.duplicates += 1;
      return;
    }
    preloadStats.misses += 1;
    var entry = {
      key: key,
      item: planEntry.item,
      element: null,
      role: "deep",
      state: "queued",
      ready: false,
      stale: false,
      generation: preloadGeneration,
      priority: planEntry.priority,
      source: planEntry.source
    };
    preloadMap.set(key, entry);
    deepQueue.push(entry);
    deepQueue.sort(function (left, right) {
      return left.priority - right.priority;
    });
    pumpDeepQueue();
  }

  function refreshPreloadWindow() {
    if (!state || !state.media || !state.media.length) {
      return;
    }
    if (!preloadAllowed()) {
      // Keep already-started/in-flight work (browsers ignore our cancellation
      // anyway), but release queued nodes so the cache cannot fill with entries
      // that will never be requested.
      releaseRing();
      for (var queued = 0; queued < deepQueue.length; queued += 1) {
        releaseEntry(deepQueue[queued]);
      }
      deepQueue = [];
      return;
    }
    var previous = neighbourItem(-1);
    var next = neighbourItem(1);
    syncRingSlot("previous", previous);
    syncRingSlot("next", next);

    var plan = preloadPlan(
      state.media,
      currentIndex,
      state.neighbour_images,
      preloadFailed,
      adaptiveRadius,
      navigationDirection
    );
    var keep = Object.create(null);
    var candidates = [];
    for (var cursor = 0; cursor < plan.length; cursor += 1) {
      keep[plan[cursor].url] = true;
      candidates.push(plan[cursor].url);
    }
    // Ring slots are owned by the current/neighbour items, including a poster
    // fallback stored under its thumbnail URL. Keep the actual entry keys so a
    // window update never prunes a live bench node.
    var ringEntries = [ring.previous, ring.next];
    for (var ringIndex = 0; ringIndex < ringEntries.length; ringIndex += 1) {
      if (ringEntries[ringIndex] && ringEntries[ringIndex].key) {
        keep[ringEntries[ringIndex].key] = true;
        candidates.push(ringEntries[ringIndex].key);
      }
    }
    keep[preloadUrl(state.media[currentIndex])] = true;
    // Remember failures while the URL is still inside the *unfiltered* window,
    // even though the queue plan excludes it. Otherwise pruneFailedSet would
    // forget it immediately and the ring would retry on every commit.
    var windowPlan = preloadPlan(
      state.media,
      currentIndex,
      state.neighbour_images,
      null,
      adaptiveRadius,
      navigationDirection
    );
    for (var windowIndex = 0; windowIndex < windowPlan.length; windowIndex += 1) {
      candidates.push(windowPlan[windowIndex].url);
    }
    prunePreloadMap(preloadMap, new Set(Object.keys(keep)), releaseEntry);
    pruneFailedSet(preloadFailed, candidates);

    for (var position = 0; position < plan.length; position += 1) {
      if (preloadMap.size >= PRELOAD_TOTAL_MAX) {
        break;
      }
      if (preloadMap.has(plan[position].url)) {
        // The plan can overlap the ring/active entries; that URL already owns
        // its one request, so this is a duplicate avoided (T8.18 B/E).
        preloadStats.hits += 1;
        if (preloadMap.get(plan[position].url)) {
          preloadStats.duplicates += 1;
        }
        continue;
      }
      queueDeep(plan[position]);
    }
  }

  // Kept as the public warm entry point used by payload prefetch and tests.
  function warmPreloadWindow(index, neighbourImages) {
    if (typeof index === "number" && index !== currentIndex) {
      return;
    }
    if (neighbourImages && state && !state.neighbour_images) {
      state.neighbour_images = neighbourImages;
    }
    refreshPreloadWindow();
  }

  function takePaintedPreload(item) {
    if (!item || itemKind(item) !== "image") {
      return null;
    }
    // The ring may hold a correctly sized thumbnail for an unavailable full
    // image. That poster can preview/fill the box, but only the item's own URL
    // may be promoted as the committed still.
    if (item.url && preloadUrl(item) !== item.url) {
      return null;
    }
    var entry = ringEntryFor(item);
    if (!entry) {
      return null;
    }
    return promoteRingEntry(entry);
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
    if (bookmarksMode) {
      return;
    }
    if (!state || !creatorId || !preloadAllowed()) {
      return;
    }
    var ids = payloadPlan();
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
        var url = manifestDataUrl(pendingId);
        fetch(
          url,
          {
            headers: { Accept: "application/json" },
            priority: "low"
          }
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
                var edge = firstMedia(payload);
                var last = lastMedia(payload);
                var neighbours = state.neighbour_images || {
                  previous: [],
                  next: []
                };
                var previous = (neighbours.previous || []).slice();
                var next = (neighbours.next || []).slice();
                if (String(state.previous_post) === pendingId && last) {
                  previous.unshift(last);
                }
                if (String(state.next_post) === pendingId && edge) {
                  next.unshift(edge);
                }
                state.neighbour_images = {
                  previous: previous,
                  next: next
                };
                refreshPreloadWindow();
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
    if (bookmarksMode || !mediaReady || !preloadAllowed()) {
      return;
    }
    prefetchNeighbourPayloads();
  }

  function schedulePreload() {
    if (!preloadAllowed()) {
      return;
    }
    refreshPreloadWindow();
  }

  function mediaBecameReady() {
    mediaReady = true;
    positionRing();
    schedulePreload();
    schedulePayloadPrefetch();
  }

  // Active media readiness still gates payload prefetch (the visible request
  // keeps priority), but the ring/window starts as soon as state is installed
  // (T8.18). Load/error only refine ring geometry and start idle payloads.
  function watchCurrentMedia(element) {
    if (!element) {
      mediaReady = true;
      schedulePreload();
      schedulePayloadPrefetch();
      return;
    }
    var tag = String(element.tagName).toUpperCase();
    if (tag === "VIDEO") {
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

  function resetPreloadState() {
    cancelPayloadPrefetch();
    preloadGeneration += 1;
    preloadEpoch += 1;
    releaseRing();
    deepQueue = [];
    mediaReady = false;
  }

  function removeLayerNow(layer) {
    if (!layer) {
      return;
    }
    layer.__viewerRemoved = true;
    if (typeof layer.pause === "function") {
      layer.pause();
    }
    if (layer.__viewerRingEntry) {
      releaseEntry(layer.__viewerRingEntry);
      layer.__viewerRingEntry = null;
    }
    if (layer.__viewerPlaceholder) {
      if (layer.__viewerPlaceholder.parentNode) {
        layer.__viewerPlaceholder.parentNode.removeChild(
          layer.__viewerPlaceholder
        );
      }
      layer.__viewerPlaceholder = null;
    }
    layer.removeAttribute("src");
    layer.removeAttribute("srcset");
    if (typeof layer.load === "function") {
      try {
        layer.load();
      } catch (_error) {
        /* A detached media element cannot always be reset. */
      }
    }
    if (layer.parentNode) {
      layer.parentNode.removeChild(layer);
    }
    if (layer === activeLayer) {
      activeLayer = null;
    }
    if (layer === incomingLayer) {
      incomingLayer = null;
    }
    if (layer === previewLayer) {
      previewLayer = null;
    }
    if (swipe && swipe.preview === layer) {
      swipe.preview = null;
    }
  }

  function resetLayerStyle(layer) {
    if (!layer) {
      return;
    }
    layer.style.transform = "";
    layer.style.opacity = "";
    layer.style.willChange = "";
    layer.classList.remove(
      "is-transitioning",
      "is-entering",
      "is-leaving",
      "is-waiting",
      "is-preview"
    );
  }

  function layerOffset(intent, dims, fraction) {
    if (!intent) {
      return { x: 0, y: 0 };
    }
    return {
      x: intent.x * dims.width * fraction,
      y: intent.y * dims.height * fraction
    };
  }

  function translateFor(offset) {
    return (
      "translate3d(" +
      (offset.x || 0) +
      "px, " +
      (offset.y || 0) +
      "px, 0)"
    );
  }

  function stageSize() {
    var width = stage ? stage.clientWidth : 0;
    var height = stage ? stage.clientHeight : 0;
    if (!width) {
      width = root.clientWidth || window.innerWidth || 1;
    }
    if (!height) {
      height = root.clientHeight || window.innerHeight || 1;
    }
    return { width: width, height: height };
  }

  function clearTransitionTimers() {
    if (transitionTimer) {
      window.clearTimeout(transitionTimer);
      transitionTimer = null;
    }
    if (holdTimer) {
      window.clearTimeout(holdTimer);
      holdTimer = null;
    }
  }

  // Collapse any in-flight move to its final state before starting another
  // one. `keep` is a drag preview that must survive, `preserveActive` keeps
  // the dragged layer's inline transform for the hand-off.
  function settleTransitions(keep, preserveActive) {
    transitionToken += 1;
    clearTransitionTimers();
    incomingLayer = null;
    var layers = host
      ? host.querySelectorAll("[data-viewer-media-el]")
      : [];
    for (var cursor = layers.length - 1; cursor >= 0; cursor -= 1) {
      var layer = layers[cursor];
      if (layer === activeLayer) {
        layer.classList.remove(
          "is-transitioning",
          "is-entering",
          "is-leaving",
          "is-waiting",
          "is-preview"
        );
        if (!preserveActive) {
          resetLayerStyle(layer);
        }
      } else if (layer === keep) {
        layer.classList.remove(
          "is-transitioning",
          "is-entering",
          "is-leaving",
          "is-waiting"
        );
      } else {
        removeLayerNow(layer);
      }
    }
    previewLayer = keep && keep !== activeLayer ? keep : null;
  }

  function unloadMedia() {
    resetPreloadState();
    settleTransitions(null, false);
    activeLayer = null;
    incomingLayer = null;
    previewLayer = null;
    if (host) {
      host.replaceChildren();
    }
  }

  function unavailableNode(item) {
    var box = document.createElement("div");
    box.className = "viewer-unavailable";

    var label = document.createElement("p");
    label.textContent = "Media unavailable";
    box.appendChild(label);

    var current = item || state.media[currentIndex] || {};
    var postUrl = bookmarksMode
      ? current.post_url
      : state.post && state.post.post_url;
    if (postUrl) {
      var link = document.createElement("a");
      link.href = postUrl;
      link.textContent = "Open the post";
      box.appendChild(link);
    }
    return box;
  }

  function showUnavailable(item) {
    unloadMedia();
    if (host) {
      host.appendChild(unavailableNode(item));
    }
  }

  function placeholderBox(item) {
    var dims = stageSize();
    var width = host && host.clientWidth ? host.clientWidth : dims.width;
    var height = host && host.clientHeight ? host.clientHeight : dims.height;
    if (Number(item.width) > 0 && Number(item.height) > 0) {
      return fittedMediaBox(item.width, item.height, width, height);
    }
    return {
      width: Math.max(120, Math.min(width * 0.62, 320)),
      height: Math.max(96, Math.min(height * 0.42, 220))
    };
  }

  function removePlaceholder(element) {
    if (!element || !element.__viewerPlaceholder) {
      return;
    }
    if (element.__viewerPlaceholder.parentNode) {
      element.__viewerPlaceholder.parentNode.removeChild(
        element.__viewerPlaceholder
      );
    }
    element.__viewerPlaceholder = null;
  }

  function ensurePlaceholder(element, item) {
    if (!element || element.__viewerRemoved) {
      return;
    }
    if (element.__viewerPlaceholder) {
      return;
    }
    var kind = itemKind(item);
    var node = document.createElement("div");
    node.className = "viewer-placeholder";
    node.setAttribute("data-kind", kind);
    var box = placeholderBox(item || {});
    node.style.width = Math.round(box.width) + "px";
    node.style.height = Math.round(box.height) + "px";
    node.textContent =
      (item && item.filename) ||
      (kind === "video" ? "Video" : kind === "gif" ? "GIF" : "Image");
    element.__viewerPlaceholder = node;
    if (element.parentNode === host) {
      // A video paints an opaque black box, so its placeholder sits on top
      // (pointer-events: none) until a poster/frame is ready. An image is
      // transparent while loading, so its placeholder sits underneath.
      if (kind === "video") {
        host.appendChild(node);
      } else {
        host.insertBefore(node, element);
      }
    }
  }

  function insertPlaceholder(element) {
    if (!element || !element.__viewerPlaceholder) {
      return;
    }
    var node = element.__viewerPlaceholder;
    if (node.parentNode === host) {
      return;
    }
    if (node.parentNode) {
      node.parentNode.removeChild(node);
    }
    if (element.parentNode === host) {
      if (itemKind(element.__viewerItem) === "video") {
        host.appendChild(node);
      } else {
        host.insertBefore(node, element);
      }
    }
  }

  function imageIsReady(element) {
    return Boolean(
      element &&
        String(element.tagName).toUpperCase() === "IMG" &&
        element.complete &&
        element.naturalWidth > 0
    );
  }

  function watchImageReady(element) {
    if (!element || element.__viewerReadyBound) {
      return;
    }
    element.__viewerReadyBound = true;
    function ready() {
      if (element.__viewerRemoved) {
        return;
      }
      if (!imageIsReady(element)) {
        return;
      }
      element.classList.remove("is-loading");
      element.classList.add("is-ready");
      removePlaceholder(element);
    }
    element.addEventListener("load", ready);
    if (typeof element.decode === "function") {
      element.decode().then(ready, function () {
        /* load/error and the placeholder decide. */
      });
    }
    if (imageIsReady(element)) {
      ready();
    }
  }

  // A video is opaque black until a poster or a decoded frame arrives, which is
  // indistinguishable from the "stuck black image" bug. Keep a deliberate
  // placeholder (with the CSS play glyph) over it until the poster probe loads,
  // then let the poster/first frame take over (T8.17 B).
  function checkVideoPoster(element, item) {
    if (!element || element.__viewerPosterChecked) {
      return;
    }
    element.__viewerPosterChecked = true;
    var poster = item && item.thumb_url;
    if (!poster || typeof Image !== "function" || isDataUrl(poster)) {
      ensurePlaceholder(element, item || {});
      return;
    }
    ensurePlaceholder(element, item);
    var probe = new Image();
    probe.onload = function () {
      if (!element.__viewerRemoved) {
        removePlaceholder(element);
      }
    };
    probe.onerror = function () {
      if (element.__viewerRemoved) {
        return;
      }
      element.__viewerPosterFailed = true;
      element.removeAttribute("poster");
      ensurePlaceholder(element, item);
    };
    probe.src = poster;
  }

  function setupMediaElement(element, item) {
    if (!element || !item) {
      return element;
    }
    var kind = itemKind(item);
    element.classList.add("viewer-media-el");
    element.classList.remove("viewer-image", "viewer-video", "viewer-warm");
    element.classList.add(kind === "video" ? "viewer-video" : "viewer-image");
    element.setAttribute("data-viewer-media-el", "");
    element.__viewerItem = item;
    element.__viewerKind = kind;
    element.__viewerPreview = Boolean(element.__viewerPreview);

    if (kind === "video" && String(element.tagName).toUpperCase() !== "VIDEO") {
      return element;
    }
    if (kind !== "video" && String(element.tagName).toUpperCase() !== "IMG") {
      return element;
    }

    if (kind === "video") {
      element.setAttribute("controls", "");
      element.setAttribute("playsinline", "");
      element.setAttribute("preload", "metadata");
      if (Number(item.width) > 0) {
        element.setAttribute("width", String(item.width));
      }
      if (Number(item.height) > 0) {
        element.setAttribute("height", String(item.height));
      }
      element.setAttribute("aria-label", item.filename || "Video");
      if (item.thumb_url && !element.__viewerPosterFailed) {
        element.setAttribute("poster", item.thumb_url);
      }
      if (!element.__viewerVideoBound) {
        element.__viewerVideoBound = true;
        element.addEventListener("loadeddata", function () {
          removePlaceholder(element);
        });
        element.addEventListener("canplay", function () {
          removePlaceholder(element);
        });
      }
      checkVideoPoster(element, item);
    } else {
      element.setAttribute("decoding", "async");
      if (Number(item.width) > 0) {
        element.setAttribute("width", String(item.width));
      }
      if (Number(item.height) > 0) {
        element.setAttribute("height", String(item.height));
      }
      element.setAttribute(
        "alt",
        (bookmarksMode ? item.post_title : state.post && state.post.title) ||
          item.filename ||
          "Media"
      );
      if (!imageIsReady(element)) {
        element.classList.add("is-loading");
        ensurePlaceholder(element, item);
      } else {
        element.classList.remove("is-loading");
        element.classList.add("is-ready");
      }
      watchImageReady(element);
    }

    if (!element.__viewerErrorBound) {
      element.__viewerErrorBound = true;
      element.addEventListener("error", function () {
        onMediaError(element);
      });
    }
    if (item.url && element.getAttribute("src") !== item.url) {
      element.setAttribute("src", item.url);
    }
    return element;
  }

  function adoptServerMedia(element, item) {
    if (!element || !item) {
      return null;
    }
    element.__viewerRole = element.__viewerRole || "active";
    setupMediaElement(element, item);
    noteRender(item, "server", element);
    // A server-rendered element may have failed before this script ran, so its
    // error event is already in the past. Detect the terminal states directly
    // instead of leaving a silent black `<video>`/`<img>` (T8.17 A/C).
    var tag = String(element.tagName).toUpperCase();
    if (tag === "VIDEO" && element.error) {
      onMediaError(element);
    } else if (tag === "IMG" && element.complete && !element.naturalWidth) {
      onMediaError(element);
    }
    return element;
  }

  function createMediaElement(item) {
    if (!item || !item.url) {
      return null;
    }
    var kind = itemKind(item);
    var element;
    if (kind === "video") {
      element = document.createElement("video");
    } else {
      element = takePaintedPreload(item) || document.createElement("img");
    }
    setupMediaElement(element, item);
    return element;
  }

  function onMediaError(element) {
    var item = element.__viewerItem || state.media[currentIndex] || {};
    // removeLayerNow() clears src, which can dispatch a late error on an
    // already-detached or leaving layer. That is not a visible failure.
    if (element.__viewerRemoved || element.classList.contains("is-leaving")) {
      return;
    }
    if (element.__viewerPreview && element !== activeLayer) {
      element.__viewerPreviewFailed = true;
      if (element.__viewerRingEntry) {
        releaseEntry(element.__viewerRingEntry);
      }
      removeLayerNow(element);
      return;
    }
    var kind = itemKind(item);
    // A still image gets exactly one thumbnail retry before the unavailable
    // card. Never loop, and show the explicit placeholder meanwhile.
    if (
      kind !== "video" &&
      String(element.tagName).toUpperCase() === "IMG" &&
      item.thumb_url &&
      !element.__viewerTriedThumb &&
      !isDataUrl(item.thumb_url)
    ) {
      element.__viewerTriedThumb = true;
      element.classList.remove("is-ready");
      element.classList.add("is-loading");
      ensurePlaceholder(element, item);
      element.setAttribute("src", item.thumb_url);
      return;
    }
    if (element === activeLayer && !element.classList.contains("is-leaving")) {
      // The committed item already owns the counter/URL; only the media shell
      // needs replacing.
      showUnavailable(item);
      watchCurrentMedia(null);
      return;
    }
    // An incoming layer failed before commit: keep the existing fallback and
    // remove every layer, including the outgoing one.
    showUnavailable(item);
    finishRender(item, true);
    watchCurrentMedia(null);
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

  function contextWords() {
    var context = state && state.context;
    if (context && context.plural) {
      return {
        singular: context.singular || "media",
        plural: context.plural || "media"
      };
    }
    return { singular: "media", plural: "media" };
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
    if (bookmarksMode) {
      var context = item.post_title ? " · " + item.post_title : "";
      if (item.creator_name) {
        context += " by " + item.creator_name;
      }
      announcer.textContent =
        kindLabel(itemKind(item)) +
        " " +
        (currentIndex + 1) +
        " of " +
        total +
        context;
      return;
    }
    var words = contextWords();
    var text =
      kindLabel(itemKind(item)) +
      " " +
      (currentIndex + 1) +
      " of " +
      total +
      " " +
      words.plural +
      " in this post";
    var rank = Number(state.post_rank);
    var posts = Number(state.media_post_count);
    if (rank > 0 && posts > 0) {
      text += " · post " + rank + " of " + posts + " " + words.plural;
    }
    announcer.textContent = text;
  }

  function replaceUrl() {
    if (!window.history || !window.history.replaceState) {
      return;
    }
    var params = new URLSearchParams(window.location.search);
    if (bookmarksMode) {
      var current = state.media[currentIndex];
      if (!current || current.bookmark_id === undefined) {
        return;
      }
      params.set("b", String(current.bookmark_id));
      window.history.replaceState(
        null,
        "",
        window.location.pathname + "?" + params.toString()
      );
      return;
    }
    if (!state.post) {
      return;
    }
    var currentItem = state.media[currentIndex];
    params.set("post", state.post.service_id);
    params.set("i", String(currentIndex));
    if (currentItem && currentItem.sha256) {
      params.set("sha256", String(currentItem.sha256));
    } else {
      params.delete("sha256");
    }
    var gf = currentViewerContext();
    if (gf) {
      params.set("gf", gf);
    } else {
      params.delete("gf");
    }
    window.history.replaceState(
      null,
      "",
      window.location.pathname + "?" + params.toString()
    );
  }

  function finishRender(item, updateUrl) {
    updateCounter();
    if (updateUrl !== false) {
      replaceUrl();
    }
  }

  function renderInstant(item, updateUrl) {
    // Take/promote the painted ring node before unloading: resetPreloadState()
    // releases the old ring, not the element that is about to become active.
    var element = createMediaElement(item);
    unloadMedia();
    root.setAttribute("data-kind", itemKind(item));
    if (!element) {
      showUnavailable(item);
    } else {
      element.__viewerRole = "active";
      if (element.__viewerRingEntry) {
        element.__viewerRingEntry.role = "active";
      }
      host.appendChild(element);
      insertPlaceholder(element);
      activeLayer = element;
    }
    finishRender(item, updateUrl);
    watchCurrentMedia(element);
    noteRender(item, element && element.__viewerRingEntry ? "promoted" : "fresh", element);
    refreshPreloadWindow();
  }

  // Reveal an incoming layer at its start position. A drag hand-off arrives
  // already positioned under the finger and must not be moved back first.
  function prepareIncoming(incoming, intent, handoff) {
    incoming.__viewerPreview = false;
    if (incoming.__viewerPlaceholder) {
      // Drop the drag offset from a hand-off placeholder; the committed slot
      // is centred even when the bitmap has not arrived yet.
      incoming.__viewerPlaceholder.style.transform = "";
    }
    incoming.classList.add("is-entering");
    if (handoff) {
      incoming.classList.remove("is-preview");
      incoming.style.pointerEvents = "";
      return;
    }
    var dims = stageSize();
    var offset = layerOffset(intent, dims, INCOMING_OFFSET);
    incoming.style.transform = translateFor(offset);
    incoming.style.opacity = "0";
    incoming.style.willChange = "transform, opacity";
    if (incoming.parentNode !== host) {
      host.appendChild(incoming);
    }
    insertPlaceholder(incoming);
  }

  // Prefer decode(), fall back to load/error and complete. The safety window
  // keeps the UI moving on a slow network; `ready` tells commitTransition
  // whether the outgoing layer may fade out immediately.
  function whenReady(element, callback) {
    var settled = false;
    function finish(ready) {
      if (settled) {
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      callback(ready);
    }
    var timer = window.setTimeout(function () {
      finish(false);
    }, TRANSITION_SAFETY_MS);
    var tag = String(element.tagName).toUpperCase();
    if (tag === "IMG") {
      element.addEventListener(
        "load",
        function () {
          finish(true);
        },
        { once: true }
      );
      element.addEventListener(
        "error",
        function () {
          finish(false);
        },
        { once: true }
      );
      if (typeof element.decode === "function") {
        element.decode().then(
          function () {
            finish(true);
          },
          function () {
            /* load/error or the safety window decides. */
          }
        );
      } else if (element.complete) {
        finish(true);
      }
      return;
    }
    element.addEventListener(
      "loadedmetadata",
      function () {
        finish(true);
      },
      { once: true }
    );
    element.addEventListener(
      "error",
      function () {
        finish(false);
      },
      { once: true }
    );
    if (element.readyState >= 1) {
      finish(true);
    }
  }

  function settleAfterTransition(token, incoming, outgoing) {
    if (token !== transitionToken) {
      return;
    }
    clearTransitionTimers();
    if (outgoing && outgoing.parentNode) {
      // Old active -> new ring neighbour where the playlist still needs it.
      if (!parkOutgoing(outgoing)) {
        removeLayerNow(outgoing);
      }
    }
    if (incoming && incoming.parentNode) {
      resetLayerStyle(incoming);
      if (incoming.__viewerRingEntry) {
        incoming.__viewerRingEntry.role = "active";
      }
    }
    refreshPreloadWindow();
  }

  function finishLeaving(token, incoming, outgoing) {
    function cleanup() {
      settleAfterTransition(token, incoming, outgoing);
    }
    if (incoming) {
      var onTransitionEnd = function (event) {
        if (event.target !== incoming) {
          return;
        }
        if (
          event.propertyName !== "transform" &&
          event.propertyName !== "opacity"
        ) {
          return;
        }
        incoming.removeEventListener("transitionend", onTransitionEnd);
        cleanup();
      };
      incoming.addEventListener("transitionend", onTransitionEnd);
    }
    transitionTimer = window.setTimeout(cleanup, TRANSITION_MS + 140);
  }

  // The safety window fired before the image decoded: keep the outgoing layer
  // behind the incoming one until the real load, so the stage never blanks.
  // If the hold window expires too, reveal the deliberate placeholder (never an
  // opaque unpainted <img>).
  function waitForIncoming(token, incoming, outgoing) {
    var item = incoming.__viewerItem || state.media[currentIndex] || {};
    function release(ready) {
      if (token !== transitionToken) {
        return;
      }
      incoming.removeEventListener("load", release);
      incoming.removeEventListener("loadedmetadata", release);
      if (!ready && itemKind(item) === "image") {
        // Still not paintable: the placeholder is the visible layer until the
        // real bitmap arrives. Do not reveal black/transparent nothing.
        ensurePlaceholder(incoming, item);
        incoming.classList.add("is-loading");
      } else {
        incoming.classList.remove("is-loading", "is-waiting");
        incoming.style.opacity = "1";
      }
      if (!outgoing || !outgoing.parentNode) {
        settleAfterTransition(token, incoming, null);
        return;
      }
      outgoing.classList.add("is-transitioning");
      void outgoing.offsetWidth;
      outgoing.style.opacity = "0";
      transitionTimer = window.setTimeout(function () {
        settleAfterTransition(token, incoming, outgoing);
      }, TRANSITION_MS + 140);
    }
    incoming.addEventListener("load", function () {
      release(true);
    }, { once: true });
    incoming.addEventListener("loadedmetadata", function () {
      release(true);
    }, { once: true });
    holdTimer = window.setTimeout(function () {
      if (token !== transitionToken) {
        return;
      }
      clearTransitionTimers();
      release(imageIsReady(incoming) || itemKind(item) !== "image");
    }, TRANSITION_HOLD_MS);
  }

  function commitTransition(
    outgoing,
    incoming,
    item,
    updateUrl,
    intent,
    handoff,
    ready
  ) {
    var token = ++transitionToken;
    clearTransitionTimers();
    var dims = handoff ? handoff.dims : stageSize();
    var hasOutgoing =
      outgoing && outgoing !== incoming && outgoing.parentNode === host;
    var kind = itemKind(item);
    var translate = kind === "image";
    var reveal =
      kind !== "image" || ready || imageIsReady(incoming);

    incomingLayer = null;
    previewLayer = null;
    activeLayer = incoming;
    incoming.__viewerPreview = false;
    incoming.__viewerRole = "active";
    if (incoming.__viewerRingEntry) {
      incoming.__viewerRingEntry.role = "active";
      incoming.__viewerRingEntry.stale = false;
    }
    incoming.classList.remove("is-entering", "is-preview");
    incoming.classList.add("is-transitioning");
    if (!reveal) {
      incoming.classList.add("is-waiting");
      ensurePlaceholder(incoming, item);
    } else {
      incoming.classList.remove("is-waiting");
    }

    // Force the starting transform to be committed before the transition.
    void incoming.offsetWidth;

    incoming.style.transform = translate ? "translate3d(0, 0, 0)" : "";
    incoming.style.opacity = reveal ? "1" : "0";

    if (hasOutgoing) {
      // Playback must stop the instant the item is left, even while the old
      // frame is still fading out behind the new one (T8.17).
      if (itemKind(outgoing.__viewerItem) === "video" && typeof outgoing.pause === "function") {
        try {
          outgoing.pause();
        } catch (_error) {
          /* A detached media element can already be paused. */
        }
      }
      outgoing.classList.add("is-leaving");
      outgoing.style.pointerEvents = "none";
      if (ready) {
        outgoing.classList.add("is-transitioning");
        if (itemKind(outgoing.__viewerItem) === "image") {
          var start = handoff ? handoff.offset : { x: 0, y: 0 };
          var end = layerOffset(intent, dims, OUTGOING_OFFSET);
          outgoing.style.transform = translateFor({
            x: start.x - end.x,
            y: start.y - end.y
          });
        }
        outgoing.style.opacity = "0";
      }
    }

    // Keep the rolling window: already-started useful work survives, only the
    // stale queue is pruned. Ring sync skips the outgoing node until it parks.
    refreshPreloadWindow();
    root.setAttribute("data-kind", kind || "file");
    finishRender(item, updateUrl);
    watchCurrentMedia(incoming);
    noteRender(
      item,
      incoming.__viewerRingEntry ? "promoted" : handoff ? "handoff" : "fresh",
      incoming
    );

    if (hasOutgoing) {
      if (reveal) {
        finishLeaving(token, incoming, outgoing);
      } else {
        waitForIncoming(token, incoming, outgoing);
      }
    } else {
      finishLeaving(token, incoming, null);
    }
  }

  function handoffIsPromotable(handoff, item) {
    if (!handoff || !handoff.layer || !handoff.promotable) {
      return false;
    }
    var layer = handoff.layer;
    if (
      !layer.parentNode ||
      layer.parentNode !== host ||
      String(layer.tagName).toUpperCase() !== "IMG" ||
      !layer.getAttribute("src") ||
      layer.getAttribute("src") !== item.url
    ) {
      return false;
    }
    if (handoff.entry) {
      return (
        handoff.entry.element === layer &&
        !handoff.entry.stale &&
        (handoff.entry.state === "painted" ||
          handoff.entry.state === "promoted")
      );
    }
    return imageIsReady(layer);
  }

  function releaseHandoffLayer(handoff) {
    if (!handoff || !handoff.layer) {
      return;
    }
    var layer = handoff.layer;
    if (previewLayer === layer) {
      previewLayer = null;
    }
    if (layer.__viewerRingEntry && !layer.__viewerRemoved) {
      returnRingEntry(
        layer.__viewerRingEntry,
        handoff.entryRole === "previous" ? "previous" : "next"
      );
      return;
    }
    removeLayerNow(layer);
  }

  function render(index, updateUrl, intent, handoff) {
    if (!state.media || !state.media.length) {
      if (handoff) {
        discardHandoff(handoff);
      }
      return;
    }
    currentIndex = clamp(Number(index) || 0, state.media.length);
    var item = state.media[currentIndex];
    var animate =
      Boolean(intent) &&
      Boolean(host) &&
      !prefersReducedMotion() &&
      !root.classList.contains("viewer-quiet");

    // First paint, URL restoration, error recovery, quiet mode and reduced
    // motion keep the instant swap.
    if (!animate) {
      if (handoff) {
        discardHandoff(handoff);
      }
      renderInstant(item, updateUrl);
      return;
    }

    settleTransitions(handoff ? handoff.layer : null, Boolean(handoff));
    var token = transitionToken;
    var outgoing = activeLayer;
    root.setAttribute("data-kind", itemKind(item));

    if (!item.url) {
      if (handoff) {
        handoff.used = true;
        releaseHandoffLayer(handoff);
      }
      showUnavailable(item);
      finishRender(item, updateUrl);
      watchCurrentMedia(null);
      return;
    }

    // A drag hand-off is used only when it is the exact destination node and
    // still painted/loaded. A stale `src`, a failed promotion or a question-mark
    // placeholder must fall back to a fresh element instead of committing an
    // empty black layer (T8.17).
    var useHandoff = handoffIsPromotable(handoff, item);
    if (handoff) {
      handoff.used = true;
    }
    var incoming;
    if (useHandoff) {
      incoming = handoff.layer;
      if (handoff.entry) {
        handoff.entry.role = "promoted";
        handoff.entry.stale = false;
      }
    } else {
      if (handoff && handoff.layer) {
        releaseHandoffLayer(handoff);
        handoff.layer = null;
      }
      incoming = createMediaElement(item);
    }
    if (!incoming) {
      showUnavailable(item);
      finishRender(item, updateUrl);
      watchCurrentMedia(null);
      return;
    }
    prepareIncoming(incoming, intent, useHandoff ? handoff : null);
    incomingLayer = incoming;
    // The destination request is committed now, so keep the next rolling
    // window warm without waiting for its load event or an idle callback.
    warmPreloadWindow(currentIndex);
    whenReady(incoming, function (ready) {
      if (token !== transitionToken || incoming !== incomingLayer) {
        return;
      }
      commitTransition(
        outgoing,
        incoming,
        item,
        updateUrl,
        intent,
        useHandoff ? handoff : null,
        ready
      );
    });
  }

  function go(action, handoff) {
    if (!action) {
      return false;
    }
    var now = Date.now();
    if (now - lastNav < 150) {
      return false;
    }
    lastNav = now;

    if (bookmarksMode && (action === "prev-post" || action === "next-post")) {
      return false;
    }

    // Direction of travel weights the preload plan; rapid flicks widen it
    // within the documented hard cap (T8.18 C).
    var direction = 0;
    if (action === "next-image" || action === "next-post") {
      direction = 1;
    } else if (action === "prev-image" || action === "prev-post") {
      direction = -1;
    }
    if (direction) {
      adaptiveRadius = nextPreloadRadius(
        adaptiveRadius,
        lastSwipeAt ? now - lastSwipeAt : null
      );
      lastSwipeAt = now;
      navigationDirection = direction;
    }

    var intent = transitionPlan(action);
    if (action === "prev-image") {
      if (currentIndex > 0) {
        render(currentIndex - 1, undefined, intent, handoff);
        return true;
      }
      if (state.previous_post) {
        // T8.6: the inverse of the move that entered this post lands on its
        // last media, which jump() resolves against the target payload.
        jump(state.previous_post, -1, intent, handoff);
        return true;
      }
      return false;
    }
    if (action === "next-image") {
      if (currentIndex < state.media.length - 1) {
        render(currentIndex + 1, undefined, intent, handoff);
        return true;
      }
      if (state.next_post) {
        jump(state.next_post, 0, intent, handoff);
        return true;
      }
      return false;
    }
    if (action === "prev-post") {
      if (!state.previous_post) {
        return false;
      }
      jump(state.previous_post, undefined, intent, handoff);
      return true;
    }
    if (action === "next-post") {
      if (!state.next_post) {
        return false;
      }
      jump(state.next_post, undefined, intent, handoff);
      return true;
    }
    return false;
  }

  // A hand-off that never reached commit (throttled move, failed post fetch)
  // must remove its preview and return the dragged layer to rest.
  function discardHandoff(handoff) {
    if (!handoff || handoff.used) {
      return;
    }
    handoff.used = true;
    if (handoff.layer && handoff.layer.parentNode) {
      releaseHandoffLayer(handoff);
    }
    if (handoff.dragged) {
      springActiveLayer();
    }
  }

  function springActiveLayer() {
    var layer = activeLayer;
    if (!layer) {
      return;
    }
    if (prefersReducedMotion() || root.classList.contains("viewer-quiet")) {
      resetLayerStyle(layer);
      return;
    }
    var token = ++transitionToken;
    clearTransitionTimers();
    layer.classList.add("is-transitioning");
    void layer.offsetWidth;
    layer.style.transform = "translate3d(0, 0, 0)";
    layer.style.opacity = "1";
    transitionTimer = window.setTimeout(function () {
      if (token !== transitionToken) {
        return;
      }
      resetLayerStyle(layer);
      transitionTimer = null;
    }, TRANSITION_MS + 60);
  }

  function applyPayload(next, index, token, signal, intent, handoff) {
    if (token !== jumpToken || (signal && signal.aborted)) {
      return;
    }
    rememberPayload(next);
    if (!next.manifest && state && state.manifest) {
      next.manifest = state.manifest;
    }
    state = next;
    installManifest(state.manifest);
    prefetchNeighbourPayloads();
    // Up/Down and right-at-the-edge default to item 0; left-at-the-edge asks
    // for the last item of the target post. The new payload's media count is
    // the only valid clamp.
    var target = typeof index === "number" ? index : 0;
    if (target < 0) {
      target = state.media.length - 1;
    }
    render(target, undefined, intent, handoff);
    state.index = currentIndex;
    root.removeAttribute("aria-busy");
  }

  function jump(serviceId, index, intent, handoff) {
    if (!serviceId || !creatorId) {
      if (handoff) {
        discardHandoff(handoff);
      }
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
      applyPayload(cached, index, token, null, intent, handoff);
      return;
    }

    jumpController = new AbortController();
    var signal = jumpController.signal;
    var dataUrl = manifestDataUrl(serviceId);
    fetch(
      dataUrl,
      { headers: { Accept: "application/json" }, signal: signal }
    )
      .then(function (response) {
        if (!response.ok) {
          throw new Error("viewer data request failed");
        }
        return response.json();
      })
      .then(function (next) {
        applyPayload(next, index, token, signal, intent, handoff);
      })
      .catch(function (error) {
        if (token !== jumpToken || (error && error.name === "AbortError")) {
          if (handoff) {
            discardHandoff(handoff);
          }
          return;
        }
        root.removeAttribute("aria-busy");
        setFlash("Could not load that post. Staying on the current media.");
        if (handoff) {
          discardHandoff(handoff);
        }
      });
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
        : returnUrl.indexOf("/bookmarks") !== -1
          ? "[data-viewer-open]"
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
    if (!state || !state.media || !state.media.length) {
      return;
    }
    var item = state.media[currentIndex];
    if (!item) {
      return;
    }
    var targetCreatorId = itemCreatorId(item);
    if (!targetCreatorId) {
      return;
    }

    var url;
    var nextPath = window.location.pathname + window.location.search;
    if (kind === "post") {
      if (bookmarksMode || !state.post || !state.post.service_id) {
        return;
      }
      url =
        "/c/" +
        targetCreatorId +
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
        targetCreatorId +
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
      ArrowUp: "next-post",
      ArrowDown: "prev-post"
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
      go(dy > 0 ? "prev-post" : "next-post");
    }
  }

  // ------------------------------------------------------------------- //
  // Finger-following swipe (T8.10)
  // ------------------------------------------------------------------- //

  function cachedPostPayload(serviceId) {
    if (serviceId === undefined || serviceId === null) {
      return null;
    }
    return payloadCache.get(String(serviceId));
  }

  function firstMedia(payload) {
    if (!payload || !payload.media || !payload.media.length) {
      return null;
    }
    return payload.media[0];
  }

  function lastMedia(payload) {
    if (!payload || !payload.media || !payload.media.length) {
      return null;
    }
    return payload.media[payload.media.length - 1];
  }

  // Whether an action has a real destination (so it may commit) and, when the
  // payload is already cached, the media item to preview during the drag.
  function destinationFor(action) {
    if (!state || !state.media) {
      return { exists: false, item: null };
    }
    if (action === "next-image") {
      if (currentIndex < state.media.length - 1) {
        return { exists: true, item: state.media[currentIndex + 1] };
      }
      if (state.next_post) {
        return {
          exists: true,
          item: firstMedia(cachedPostPayload(state.next_post))
        };
      }
      return { exists: false, item: null };
    }
    if (action === "prev-image") {
      if (currentIndex > 0) {
        return { exists: true, item: state.media[currentIndex - 1] };
      }
      if (state.previous_post) {
        return {
          exists: true,
          item: lastMedia(cachedPostPayload(state.previous_post))
        };
      }
      return { exists: false, item: null };
    }
    if (action === "prev-post") {
      if (bookmarksMode || !state.previous_post) {
        return { exists: false, item: null };
      }
      return {
        exists: true,
        item: firstMedia(cachedPostPayload(state.previous_post))
      };
    }
    if (action === "next-post") {
      if (bookmarksMode || !state.next_post) {
        return { exists: false, item: null };
      }
      return {
        exists: true,
        item: firstMedia(cachedPostPayload(state.next_post))
      };
    }
    return { exists: false, item: null };
  }

  function cancelSwipeFrame() {
    if (swipeFrame === null) {
      return;
    }
    if (typeof window.cancelAnimationFrame === "function") {
      window.cancelAnimationFrame(swipeFrame);
    } else {
      window.clearTimeout(swipeFrame);
    }
    swipeFrame = null;
  }

  function layerBox(layer) {
    if (!layer || typeof layer.getBoundingClientRect !== "function") {
      return null;
    }
    var rect = layer.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) {
      return null;
    }
    return { width: rect.width, height: rect.height };
  }

  // Apply one finger position to both layers. No layout reads happen here:
  // the media boxes and the centre-to-centre step were measured once when the
  // gesture locked an axis.
  function applySwipeOffsets(s) {
    var dx = s.lastX - s.startX;
    var dy = s.lastY - s.startY;
    var delta = s.axis === "x" ? dx : dy;
    var resistance = s.hasDestination ? 1 : SWIPE_RUBBER_BAND;
    var offsets = swipeOffsets(s.axis, delta, resistance, s.step || 0);
    s.offset = offsets.drag;
    s.previewOffset = offsets.preview;
    // Videos and GIFs crossfade only; their native controls must not move.
    if (s.trackActive && activeLayer) {
      activeLayer.style.transform = translateFor(s.offset);
    }
    if (s.preview) {
      s.preview.style.transform = translateFor(s.previewOffset);
      // A deliberate placeholder is a sibling node in the grid. It must track
      // the preview exactly, or a loading destination looks like a grey box
      // stuck in the centre of the stage while the finger drags (T8.17/T8.18).
      if (s.preview.__viewerPlaceholder) {
        s.preview.__viewerPlaceholder.style.transform = translateFor(
          s.previewOffset
        );
      }
    }
    return offsets;
  }

  function applySwipeFrame() {
    swipeFrame = null;
    if (!swipe || swipe.committed || !swipe.axis) {
      return;
    }
    applySwipeOffsets(swipe);
  }

  function scheduleSwipeFrame() {
    if (swipeFrame !== null) {
      return;
    }
    if (typeof window.requestAnimationFrame === "function") {
      swipeFrame = window.requestAnimationFrame(applySwipeFrame);
    } else {
      swipeFrame = window.setTimeout(applySwipeFrame, 16);
    }
  }

  // Returns false while the gesture is still too diagonal to choose an axis.
  // Once locked the axis never changes and the preview is placed before the
  // browser can paint a centred or stage-sized first frame.
  function beginSwipePreview(s, dx, dy) {
    var axis = swipeAxis(
      dx,
      dy,
      SWIPE_SLOP,
      SWIPE_AXIS_DOMINANCE,
      SWIPE_AXIS_FALLBACK
    );
    if (!axis) {
      return false;
    }
    settleTransitions(null, false);
    s.axis = axis;
    s.moved = true;
    var delta = s.axis === "x" ? dx : dy;
    var action = swipeActionFor(s.axis, delta);
    var destination = destinationFor(action);
    s.action = action;
    s.hasDestination = destination.exists;
    s.trackActive = Boolean(
      activeLayer &&
        activeLayer.__viewerItem &&
        itemKind(activeLayer.__viewerItem) === "image"
    );
    s.step = 0;

    if (
      !s.trackActive ||
      !destination.item ||
      itemKind(destination.item) !== "image" ||
      !destination.item.url
    ) {
      applySwipeOffsets(s);
      return true;
    }
    // Use the exact painted ring node when it is available: the first drag
    // frame already has destination pixels and no request/decode starts. If the
    // slot is not paint-ready, degrade to a transparent fresh layer with an
    // explicit placeholder, never an opaque black rectangle (T8.18).
    var previewEntry = ringEntryFor(destination.item);
    if (previewEntry && preloadUrl(destination.item) !== destination.item.url) {
      previewEntry = null;
    }
    var previewRole = previewEntry ? previewEntry.role : null;
    var layer = previewEntry
      ? promoteRingEntry(previewEntry)
      : document.createElement("img");
    if (!layer) {
      applySwipeOffsets(s);
      return true;
    }
    setupMediaElement(layer, destination.item);
    // The destination's visible box, not the stage. Metadata gives the exact
    // letterboxed size without waiting for a decode; missing dimensions fall
    // back to the current media box (flush edges, corrected on load).
    var hostWidth = host && host.clientWidth ? host.clientWidth : s.width;
    var hostHeight = host && host.clientHeight ? host.clientHeight : s.height;
    var previewBox =
      Number(destination.item.width) > 0 &&
      Number(destination.item.height) > 0
        ? fittedMediaBox(
            destination.item.width,
            destination.item.height,
            hostWidth,
            hostHeight
          )
        : { width: s.width, height: s.height };
    s.previewBox = previewBox;
    s.step = swipeTrackStep(s.axis, s.box, previewBox);
    layer.__viewerPreview = true;
    layer.classList.add("is-preview");
    s.previewEntry = previewEntry;
    s.previewRole = previewRole;
    s.previewReady = Boolean(previewEntry) || imageIsReady(layer);
    if (s.previewReady) {
      layer.style.opacity = "1";
    }
    layer.style.willChange = "transform";
    s.preview = layer;
    previewLayer = layer;
    applySwipeOffsets(s);

    // A late decode can reveal that the metadata or fallback was wrong. Adjust
    // the shared step once; still no timer or per-frame layout read.
    layer.addEventListener("load", function () {
      if (layer.__viewerPreview) {
        layer.classList.remove("is-loading");
        layer.style.opacity = "1";
        removePlaceholder(layer);
      }
      if (!swipe || swipe.preview !== layer || !swipe.axis) {
        return;
      }
      var actual = layerBox(layer);
      if (!actual) {
        return;
      }
      var step = swipeTrackStep(swipe.axis, swipe.box, actual);
      if (Math.abs(step - swipe.step) < 0.5) {
        return;
      }
      swipe.previewBox = actual;
      swipe.step = step;
      applySwipeOffsets(swipe);
    });
    host.appendChild(layer);
    if (!s.previewReady) {
      insertPlaceholder(layer);
    }
    return true;
  }

  function swipeVelocity(s, axis) {
    var samples = s.samples;
    if (!samples || !samples.length) {
      return 0;
    }
    var end = samples[samples.length - 1];
    var start = samples[0];
    for (var cursor = samples.length - 1; cursor >= 0; cursor -= 1) {
      if (end.t - samples[cursor].t <= 100) {
        start = samples[cursor];
      } else {
        break;
      }
    }
    var elapsed = end.t - start.t;
    if (elapsed <= 0) {
      return 0;
    }
    var distance = axis === "x" ? end.x - start.x : end.y - start.y;
    return distance / elapsed;
  }

  function releasePointer(s) {
    if (!s || !s.captured || !stage) {
      return;
    }
    s.captured = false;
    try {
      stage.releasePointerCapture(s.id);
    } catch (_error) {
      /* The browser already dropped the capture. */
    }
  }

  function revertSwipe(s) {
    if (s && s.preview) {
      if (s.previewEntry) {
        returnRingEntry(s.previewEntry, s.previewRole || "next");
      } else {
        removeLayerNow(s.preview);
      }
      if (previewLayer === s.preview) {
        previewLayer = null;
      }
    }
    if (s && s.offset && (s.offset.x || s.offset.y)) {
      springActiveLayer();
    } else {
      resetLayerStyle(activeLayer);
    }
  }

  function finishPointer(event, cancelled) {
    if (!swipe || event.pointerId !== swipe.id) {
      return;
    }
    var s = swipe;
    swipe = null;
    releasePointer(s);
    cancelSwipeFrame();
    if (!s.axis) {
      if (!cancelled && isTap(s, event)) {
        var tapAction = tapActionAt(s);
        if (tapAction) {
          suppressNextClick = true;
          if (suppressClickTimer) {
            window.clearTimeout(suppressClickTimer);
          }
          suppressClickTimer = window.setTimeout(function () {
            suppressNextClick = false;
            suppressClickTimer = null;
          }, TAP_MAX_DURATION);
          if (!go(tapAction)) {
            suppressNextClick = false;
            window.clearTimeout(suppressClickTimer);
            suppressClickTimer = null;
          }
        }
      }
      return;
    }
    if (typeof event.clientX === "number") {
      s.lastX = event.clientX;
      s.lastY = event.clientY;
    }
    var dx = s.lastX - s.startX;
    var dy = s.lastY - s.startY;
    // Include the release moment so pausing before lifting kills the flick.
    s.samples.push({ t: Date.now(), x: s.lastX, y: s.lastY });
    var vx = swipeVelocity(s, "x");
    var vy = swipeVelocity(s, "y");
    var hasDestination = true;
    var preliminary = resolveSwipe({
      dx: dx,
      dy: dy,
      width: s.width,
      height: s.height,
      vx: vx,
      vy: vy,
      slop: SWIPE_SLOP,
      axis: s.axis,
      hasDestination: hasDestination
    });
    var destination = preliminary.action
      ? destinationFor(preliminary.action)
      : { exists: false, item: null };
    var result = resolveSwipe({
      dx: dx,
      dy: dy,
      width: s.width,
      height: s.height,
      vx: vx,
      vy: vy,
      slop: SWIPE_SLOP,
      axis: s.axis,
      hasDestination: destination.exists
    });
    if (!cancelled && result.commit) {
      var preview = s.preview || null;
      var handoff = {
        layer: preview,
        entry: s.previewEntry || (preview && preview.__viewerRingEntry) || null,
        entryRole: s.previewRole || null,
        // A fresh layer that finished loading during the drag is just as safe
        // as a ring node; both are the exact `<img src=item.url>` being shown.
        promotable: Boolean(
          preview &&
            !preview.__viewerPreviewFailed &&
            preview.parentNode === host &&
            String(preview.tagName).toUpperCase() === "IMG" &&
            (s.previewReady || imageIsReady(preview))
        ),
        // The outgoing layer's current drag offset. The preview layer already
        // holds its own inline transform under the finger.
        offset: s.offset || { x: 0, y: 0 },
        dragged: Boolean(s.offset && (s.offset.x || s.offset.y)),
        dims: { width: s.width, height: s.height },
        used: false
      };
      if (!go(result.action, handoff)) {
        discardHandoff(handoff);
      }
      return;
    }
    revertSwipe(s);
  }

  function isTap(s, event) {
    var endX =
      event && typeof event.clientX === "number" ? event.clientX : s.lastX;
    var endY =
      event && typeof event.clientY === "number" ? event.clientY : s.lastY;
    var distance = Math.max(
      Math.abs(endX - s.startX),
      Math.abs(endY - s.startY)
    );
    return (
      distance <= TAP_MAX_DISTANCE &&
      Date.now() - s.startTime <= TAP_MAX_DURATION
    );
  }

  function tapActionAt(s) {
    if (!stage || !stage.getBoundingClientRect) {
      return null;
    }
    var rect = stage.getBoundingClientRect();
    var width = Number(rect && rect.width) || 0;
    if (width <= 0) {
      return null;
    }
    var x = s.lastX - rect.left;
    var leftStart = width * TAP_ZONE_INSET;
    var leftEnd = width * (TAP_ZONE_INSET + TAP_ZONE_WIDTH);
    var rightStart = width * (1 - TAP_ZONE_INSET - TAP_ZONE_WIDTH);
    var rightEnd = width * (1 - TAP_ZONE_INSET);
    if (x >= leftStart && x <= leftEnd) {
      return "prev-image";
    }
    if (x >= rightStart && x <= rightEnd) {
      return "next-image";
    }
    return null;
  }

  function onStagePointerDown(event) {
    if (event.pointerType === "mouse" && event.button !== 0) {
      return;
    }
    var target = event.target;
    if (
      target &&
      target.closest &&
      target.closest(
        "video, audio, a[href], [data-viewer-close], " +
          "[data-viewer-fullscreen], [data-viewer-hint], " +
          "[data-viewer-toast], [data-viewer-flash], [data-viewer-no-swipe]"
      )
    ) {
      return;
    }
    if (swipe) {
      // A second pointer cancels the first gesture without navigating.
      finishPointer({ pointerId: swipe.id }, true);
      return;
    }
    dismissHint();
    // T8.11: the drag, preview placement and commit threshold all use the
    // visible media box measured once here, never the stage/viewport size.
    var box = layerBox(activeLayer) || stageSize();
    swipe = {
      id: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startTime: Date.now(),
      lastX: event.clientX,
      lastY: event.clientY,
      axis: null,
      moved: false,
      committed: false,
      samples: [
        {
          t: Date.now(),
          x: event.clientX,
          y: event.clientY
        }
      ],
      box: box,
      width: box.width,
      height: box.height,
      step: 0,
      trackActive: false,
      offset: { x: 0, y: 0 },
      preview: null,
      previewBox: null,
      previewOffset: null,
      captured: false
    };
    if (stage && stage.setPointerCapture) {
      try {
        stage.setPointerCapture(event.pointerId);
        swipe.captured = true;
      } catch (_error) {
        /* Capture is an optimisation; pointer events still arrive on stage. */
      }
    }
  }

  function onStagePointerMove(event) {
    if (!swipe || event.pointerId !== swipe.id || swipe.committed) {
      return;
    }
    swipe.lastX = event.clientX;
    swipe.lastY = event.clientY;
    var now = Date.now();
    swipe.samples.push({ t: now, x: event.clientX, y: event.clientY });
    while (swipe.samples.length > 2 && now - swipe.samples[0].t > 120) {
      swipe.samples.shift();
    }
    var dx = event.clientX - swipe.startX;
    var dy = event.clientY - swipe.startY;
    if (!swipe.axis) {
      if (!beginSwipePreview(swipe, dx, dy)) {
        if (event.cancelable) {
          event.preventDefault();
        }
        return;
      }
    }
    if (event.cancelable) {
      event.preventDefault();
    }
    scheduleSwipeFrame();
  }

  function onStagePointerUp(event) {
    finishPointer(event, false);
  }

  function onStagePointerCancel(event) {
    finishPointer(event, true);
  }

  function onLostPointerCapture(event) {
    if (swipe && event.pointerId === swipe.id) {
      finishPointer(event, true);
    }
  }

  function onWindowBlur() {
    if (swipe) {
      finishPointer({ pointerId: swipe.id }, true);
    }
  }

  // T8.15: stationary taps in the inset horizontal edge zones share the same
  // dispatcher as every other navigation input.
  function onStageClick() {
    if (suppressNextClick) {
      suppressNextClick = false;
      if (suppressClickTimer) {
        window.clearTimeout(suppressClickTimer);
        suppressClickTimer = null;
      }
      return;
    }
    dismissHint();
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
    if (paintBench) {
      paintBench.replaceChildren();
    }
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
    window.removeEventListener("blur", onWindowBlur);
    cancelSwipeFrame();
    if (suppressClickTimer) {
      window.clearTimeout(suppressClickTimer);
      suppressClickTimer = null;
    }
    suppressNextClick = false;
    if (stage) {
      stage.removeEventListener("wheel", onWheel);
      stage.removeEventListener("pointerdown", onStagePointerDown);
      stage.removeEventListener("pointermove", onStagePointerMove);
      stage.removeEventListener("pointerup", onStagePointerUp);
      stage.removeEventListener("pointercancel", onStagePointerCancel);
      stage.removeEventListener("lostpointercapture", onLostPointerCapture);
      stage.removeEventListener("click", onStageClick);
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
  window.addEventListener("blur", onWindowBlur);

  if (stage) {
    stage.addEventListener("wheel", onWheel, { passive: false });
    stage.addEventListener("pointerdown", onStagePointerDown, {
      passive: true
    });
    stage.addEventListener("pointermove", onStagePointerMove, {
      passive: false
    });
    stage.addEventListener("pointerup", onStagePointerUp);
    stage.addEventListener("pointercancel", onStagePointerCancel);
    stage.addEventListener("lostpointercapture", onLostPointerCapture);
    stage.addEventListener("click", onStageClick);
  }

  // The server-rendered first layer is the current layer from the start. Run
  // it through the same setup as every JS-created layer: kind, dimensions,
  // poster/controls, alt/aria, error handling and item metadata (T8.17 A).
  activeLayer = host ? host.querySelector("[data-viewer-media-el]") : null;
  if (activeLayer) {
    activeLayer.__viewerRole = "active";
    adoptServerMedia(activeLayer, state.media[currentIndex] || null);
  }

  if (closeButton) {
    closeButton.addEventListener("click", onCloseClick);
  }
  if (fullscreenButton) {
    fullscreenButton.addEventListener("click", onFullscreenClick);
  }

  installDebug();
  updateFullscreenControl();
  rememberPayload(state);
  installManifest(state.manifest);
  prefetchNeighbourPayloads();
  // Start the rolling window as soon as state is installed. The active request
  // keeps priority; it is not serialised behind this work (T8.18 C).
  refreshPreloadWindow();
  updateCounter();

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
