/* Monoke shell: theme, NSFW blur, clipboard, and small htmx hooks. */
(function () {
  "use strict";

  var THEME_KEY = "monoke-theme";
  var NSFW_KEY = "monoke-nsfw";
  var root = document.documentElement;

  function read(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      /* Private mode or storage disabled: keep the in-memory state. */
    }
  }

  // Write text only when it actually changed. Every DOM write wakes up the
  // content scripts of ad blockers and password managers, so a page that
  // rewrites the same text in a loop makes those extensions work hard for
  // nothing (T6.3).
  function setText(node, value) {
    if (!node || node.textContent === value) {
      return;
    }
    node.textContent = value;
  }

  function systemTheme() {
    try {
      return window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark";
    } catch (_error) {
      return "dark";
    }
  }

  function syncThemeButton() {
    var button = document.querySelector("[data-theme-toggle]");
    if (!button) {
      return;
    }
    var dark = root.getAttribute("data-theme") === "dark";
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.textContent = dark ? "Theme: dark" : "Theme: light";
  }

  function applyTheme(theme, persist) {
    var next = theme === "light" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    if (persist) {
      write(THEME_KEY, next);
    }
    syncThemeButton();
  }

  // Run before the body paints so a saved theme does not flash.
  applyTheme(read(THEME_KEY) || systemTheme(), false);

  function nsfwEnabled() {
    var saved = read(NSFW_KEY);
    return saved === null ? true : saved === "1";
  }

  // Diagnostic switch: ?quiet=1 turns the page into a plain document - no
  // debug capture, no live progress stream, no timers. It is the quickest
  // way to tell a page problem from an extension problem.
  function quietMode() {
    return new URLSearchParams(window.location.search).get("quiet") === "1";
  }

  var nsfw = nsfwEnabled();

  // The blur is driven by a CSS custom property on <html>, so swapped-in
  // thumbnails are covered without walking the document after every swap
  // (T6.3, T6.5).
  function applyNsfw() {
    root.setAttribute("data-nsfw", nsfw ? "on" : "off");
    var button = document.querySelector("[data-nsfw-toggle]");
    if (button) {
      button.setAttribute("aria-pressed", nsfw ? "true" : "false");
      button.textContent = nsfw ? "Blur: on" : "Blur: off";
    }
  }

  function setNsfw(enabled) {
    nsfw = Boolean(enabled);
    write(NSFW_KEY, nsfw ? "1" : "0");
    applyNsfw();
  }

  function showFlash(message, kind) {
    var region = document.getElementById("flash-region");
    if (!region || !message) {
      return;
    }
    var flash = document.createElement("div");
    flash.className = "flash " + (kind === "success" ? "flash-success" : "flash-error");
    flash.setAttribute("role", kind === "success" ? "status" : "alert");
    flash.textContent = message;
    region.replaceChildren(flash);
  }

  // Progressive enhancement for the bookmark forms (T9.2): the form POST is
  // the contract, but with JavaScript we swap the state without a reload.
  function toggleBookmarkForm(form) {
    var button = form.querySelector("[data-bookmark-button]");
    if (!button || button.disabled) {
      return;
    }
    button.disabled = true;
    fetch(form.getAttribute("action"), {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch"
      },
      credentials: "same-origin",
      body: new URLSearchParams(new FormData(form))
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("bookmark request failed");
        }
        return response.json();
      })
      .then(function (data) {
        var on = Boolean(data && data.bookmarked);
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.classList.toggle("is-bookmarked", on);
        button.setAttribute("title", on ? "Remove bookmark" : "Bookmark");
      })
      .catch(function () {
        showFlash("Could not update the bookmark.", "error");
      })
      .then(function () {
        button.disabled = false;
      });
  }

  function copyAllLinks(button) {
    var selector = button.getAttribute("data-copy-all");
    var root = selector ? document.querySelector(selector) : null;
    if (!root) {
      return;
    }
    var nodes = root.querySelectorAll("[data-copy-value]");
    var values = [];
    for (var index = 0; index < nodes.length; index += 1) {
      var value = nodes[index].getAttribute("data-copy-value") || "";
      if (value && nodes[index].offsetParent !== null) {
        values.push(value);
      }
    }
    if (!values.length) {
      showFlash("No links to copy.", "error");
      return;
    }
    copyText(values.join("\n"))
      .then(function () {
        showFlash("Copied " + values.length + " link" + (values.length === 1 ? "" : "s") + ".", "success");
      })
      .catch(function () {
        showFlash("Could not copy to the clipboard.", "error");
      });
  }

  function loadAttachment(button) {
    var row = button.closest("[data-attachment]");
    if (!row) {
      return;
    }
    var kind = row.getAttribute("data-kind") || "file";
    var url = row.getAttribute("data-full-src") || "";
    if (!url) {
      return;
    }

    var video = row.querySelector("video");
    var audio = row.querySelector("audio");
    var image = row.querySelector("[data-media-image]");

    // The server and the client share one decision (data-kind), but a missing
    // element must never make Load a silent no-op: fall back to whatever media
    // element the row actually has (T7.4).
    if (kind === "video" && !video) {
      kind = audio ? "audio" : image ? "image" : kind;
    } else if (kind === "audio" && !audio) {
      kind = video ? "video" : image ? "image" : kind;
    } else if ((kind === "image" || kind === "gif") && !image) {
      kind = video ? "video" : audio ? "audio" : kind;
    }

    if (kind === "video" && video) {
      var select = row.querySelector("[data-variant-select]");
      if (select && select.value) {
        url = select.value;
      }
      if (!video.getAttribute("src")) {
        video.setAttribute("src", url);
      }
      video.hidden = false;
      video.load();
    } else if (kind === "audio" && audio) {
      if (!audio.getAttribute("src")) {
        audio.setAttribute("src", url);
      }
      audio.hidden = false;
      audio.load();
    } else if ((kind === "image" || kind === "gif") && image) {
      if (!image.getAttribute("src")) {
        image.setAttribute("src", url);
      }
      image.hidden = false;
    } else {
      window.open(url, "_blank", "noopener,noreferrer");
    }

    button.disabled = true;
    button.textContent = "Loaded";
  }

  // --------------------------------------------------------------------- //
  // Gallery tiles are plain links to the fullscreen viewer (T4.10).
  // No lightbox or full-size fetch happens on page load.
  // --------------------------------------------------------------------- //

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  // --------------------------------------------------------------------- //
  // Live sync progress (EventSource with a polling fallback)
  // --------------------------------------------------------------------- //

  var syncState = null;

  function syncRoot() {
    return document.querySelector("[data-sync-progress]");
  }

  function syncSetButton(active) {
    var button = document.querySelector("[data-sync-button]");
    if (!button) {
      return;
    }
    button.disabled = Boolean(active);
    if (active) {
      button.setAttribute("aria-busy", "true");
    } else {
      button.removeAttribute("aria-busy");
    }
    button.textContent = active ? "Syncing…" : "Sync";
  }

  function syncSetPhase(text) {
    var root = syncRoot();
    var phase = root && root.querySelector("[data-sync-phase]");
    if (phase) {
      setText(phase, text);
    }
  }

  function syncPercent(done, total) {
    if (!total) {
      return 0;
    }
    return Math.max(0, Math.min(100, (done / total) * 100));
  }

  function syncSetProgress(done, total, links) {
    var root = syncRoot();
    if (!root) {
      return;
    }
    var safeDone = Number(done) || 0;
    var safeTotal = Number(total) || 0;
    var percent = syncPercent(safeDone, safeTotal);
    var fill = root.querySelector("[data-sync-fill]");
    var bar = root.querySelector("[data-sync-bar]");
    var posts = root.querySelector("[data-sync-posts]");
    var linksNode = root.querySelector("[data-sync-links]");
    if (fill) {
      fill.style.width = percent.toFixed(1) + "%";
    }
    if (bar) {
      bar.setAttribute("aria-valuenow", String(Math.floor(percent)));
    }
    if (posts) {
      setText(posts, safeDone + " / " + safeTotal + " posts");
    }
    if (linksNode && links !== undefined && links !== null) {
      setText(linksNode, links + " links");
    }
  }

  function syncApplyRun(run) {
    if (!run) {
      return;
    }
    syncSetProgress(run.posts_done, run.posts_seen, run.links_found);
    if (run.status === "running") {
      if (run.pages && run.pages_total) {
        syncSetPhase("Fetching page " + run.pages + " / " + run.pages_total);
      } else if (run.pages) {
        syncSetPhase(
          "Fetched " + run.pages + " page" + (run.pages === 1 ? "" : "s")
        );
      }
    }
  }

  function syncStop() {
    if (!syncState) {
      return;
    }
    if (syncState.source) {
      syncState.source.close();
      syncState.source = null;
    }
    if (syncState.timer) {
      window.clearInterval(syncState.timer);
      syncState.timer = null;
    }
    if (syncState.poller) {
      window.clearInterval(syncState.poller);
      syncState.poller = null;
    }
  }

  function syncFail(message) {
    if (syncState && !syncState.finished) {
      syncState.finished = true;
      syncStop();
    }
    syncSetButton(false);
    showFlash(message || "The sync failed.", "error");
  }

  function syncFinish(result, status, error) {
    if (!syncState || syncState.finished) {
      return;
    }
    syncState.finished = true;
    syncState.status = status;
    syncStop();
    syncSetButton(false);

    var root = syncRoot();
    var phase = root && root.querySelector("[data-sync-phase]");
    var payload = result || {};

    if (status === "failed") {
      if (phase) {
        phase.textContent = "Sync failed";
      }
    } else if (status === "cancelled") {
      if (phase) {
        phase.textContent = "Sync cancelled";
      }
    } else if (phase) {
      phase.textContent = "Sync complete";
    }

    if (payload.posts_done !== undefined || payload.posts_seen !== undefined) {
      syncSetProgress(payload.posts_done, payload.posts_seen, payload.links_found);
    }
    if (status !== "failed") {
      var fill = root && root.querySelector("[data-sync-fill]");
      if (fill) {
        fill.style.width = "100%";
      }
    }

    var summary = document.querySelector("[data-sync-summary]");
    if (summary) {
      var parts = [];
      if (payload.posts_done !== undefined) {
        parts.push(payload.posts_done + " posts");
      }
      if (payload.links_found !== undefined) {
        parts.push(payload.links_found + " links");
      }
      if (payload.files_count !== undefined) {
        parts.push(payload.files_count + " files");
      }
      if (payload.duration_s !== undefined) {
        parts.push(Number(payload.duration_s).toFixed(1) + " s");
      }
      summary.textContent = parts.join(" · ");
      summary.hidden = false;
    }

    if (status === "failed") {
      showFlash(error || payload.error || "The sync failed.", "error");
    }

    var rowsUrl = root && root.getAttribute("data-rows-url");
    if (rowsUrl) {
      if (window.htmx && window.htmx.ajax) {
        window.htmx.ajax("GET", rowsUrl, {
          target: "#posts-region",
          swap: "innerHTML"
        });
      } else {
        window.location.reload();
      }
    }
  }

  function syncHandle(event) {
    if (!syncState || syncState.finished || !event) {
      return;
    }
    var type = event.type;
    if (type === "started") {
      syncSetPhase("Fetching profile…");
      return;
    }
    if (type === "page") {
      if (event.pages) {
        syncSetPhase("Fetching page " + event.page + " / " + event.pages);
      } else {
        syncSetPhase("Fetching page " + event.page);
      }
      syncSetProgress(event.done, event.total, undefined);
      return;
    }
    if (type === "progress") {
      syncSetPhase("Saving posts…");
      syncSetProgress(event.done, event.total, event.links);
      return;
    }
    if (type === "done") {
      syncFinish(event.result || {}, event.status || "done", null);
      return;
    }
    if (type === "error") {
      syncFinish(event.result || {}, event.status || "failed", event.error);
    }
  }

  function syncPoll(runId) {
    if (!syncState || syncState.finished || syncState.poller) {
      return;
    }

    function fetchOnce() {
      if (!syncState || syncState.finished) {
        return;
      }
      fetch("/runs/" + encodeURIComponent(runId), {
        headers: { Accept: "application/json" }
      })
        .then(function (response) {
          if (!response.ok) {
            throw new Error("Could not read the run status.");
          }
          return response.json();
        })
        .then(function (run) {
          syncApplyRun(run);
          if (run.status && run.status !== "running" && run.status !== "cancelling") {
            syncFinish(run, run.status, run.error);
          }
        })
        .catch(function (error) {
          syncFail(error.message);
        });
    }

    fetchOnce();
    syncState.poller = window.setInterval(fetchOnce, 2000);
  }

  function syncConnect() {
    if (!syncState || syncState.finished) {
      return;
    }
    var runId = syncState.runId;
    if (!window.EventSource) {
      syncPoll(runId);
      return;
    }

    var source;
    try {
      source = new EventSource("/events/" + encodeURIComponent(runId));
    } catch (_error) {
      syncPoll(runId);
      return;
    }
    syncState.source = source;
    source.onmessage = function (event) {
      var payload;
      try {
        payload = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      syncHandle(payload);
    };
    source.onerror = function () {
      if (!syncState || syncState.finished) {
        return;
      }
      // SSE can be blocked by a proxy; switch to the two-second poll.
      source.close();
      syncState.source = null;
      syncPoll(runId);
    };
  }

  function syncStart(runId, startedAt) {
    var root = syncRoot();
    if (!root || !runId) {
      return;
    }
    if (syncState && syncState.runId === String(runId) && !syncState.finished) {
      return;
    }

    syncStop();
    var startMs = startedAt ? Date.parse(startedAt) : Date.now();
    if (isNaN(startMs)) {
      startMs = Date.now();
    }
    syncState = {
      runId: String(runId),
      finished: false,
      status: "running",
      startedMs: startMs,
      source: null,
      timer: null,
      poller: null,
      tick: null,
      paused: false
    };

    root.hidden = false;
    root.setAttribute("data-run-id", String(runId));
    syncSetButton(true);
    syncSetPhase("Starting…");

    syncState.tick = function () {
      var holder = syncRoot();
      var elapsed = holder && holder.querySelector("[data-sync-elapsed]");
      if (elapsed) {
        // Whole seconds, once a second: a tenth-of-a-second counter would
        // rewrite the DOM twice as often for no extra information.
        setText(elapsed, Math.floor((Date.now() - startMs) / 1000) + " s");
      }
    };
    syncState.tick();
    syncState.timer = window.setInterval(syncState.tick, 1000);
    syncConnect();
  }

  // A hidden tab does not need the progress stream: the connection, the poll
  // and the timer all stop, so nothing keeps the page (and the extensions
  // that watch it) busy in the background (T6.5).
  function syncPause() {
    if (!syncState || syncState.finished || syncState.paused) {
      return;
    }
    syncState.paused = true;
    if (syncState.source) {
      syncState.source.close();
      syncState.source = null;
    }
    if (syncState.timer) {
      window.clearInterval(syncState.timer);
      syncState.timer = null;
    }
    if (syncState.poller) {
      window.clearInterval(syncState.poller);
      syncState.poller = null;
    }
  }

  function syncResume() {
    if (!syncState || syncState.finished || !syncState.paused) {
      return;
    }
    syncState.paused = false;
    if (syncState.tick) {
      syncState.tick();
      syncState.timer = window.setInterval(syncState.tick, 1000);
    }
    syncConnect();
  }

  function startSyncForm(form) {
    var button = form.querySelector("[data-sync-button]");
    var original = button ? button.textContent : "Sync";
    if (button) {
      button.disabled = true;
      button.textContent = "Syncing…";
    }

    fetch(form.getAttribute("action"), {
      method: "POST",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "fetch"
      },
      credentials: "same-origin"
    })
      .then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            return { ok: response.ok, data: data };
          });
      })
      .then(function (outcome) {
        if (!outcome.ok) {
          throw new Error(
            (outcome.data && (outcome.data.detail || outcome.data.error)) ||
              "Could not start the sync."
          );
        }
        if (!outcome.data.run_id) {
          throw new Error("The server did not return a run id.");
        }
        syncStart(outcome.data.run_id, new Date().toISOString());
      })
      .catch(function (error) {
        showFlash(error.message, "error");
        if (button) {
          button.disabled = false;
          button.textContent = original;
        }
      });
  }

  function cancelSync() {
    if (!syncState || syncState.finished) {
      return;
    }
    var button = document.querySelector("[data-sync-cancel]");
    if (button) {
      button.disabled = true;
      button.textContent = "Cancelling…";
    }
    fetch("/runs/" + encodeURIComponent(syncState.runId) + "/cancel", {
      method: "POST",
      headers: { Accept: "application/json" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Could not cancel the sync.");
        }
        return response.json();
      })
      .then(function (run) {
        syncApplyRun(run);
      })
      .catch(function (error) {
        showFlash(error.message, "error");
        if (button) {
          button.disabled = false;
          button.textContent = "Cancel";
        }
      });
  }

  // --------------------------------------------------------------------- //
  // Client-side debug capture
  // --------------------------------------------------------------------- //

  var DEBUG_MAX_LINES = 400;
  var debugLines = [];
  var debugTimer = null;
  var debugNodes = [];
  var debugRendered = 0;

  function debugRequested() {
    if (root.getAttribute("data-debug") === "1") {
      return true;
    }
    var param = new URLSearchParams(window.location.search).get("debug");
    return param === "1" || window.location.hash === "#debug";
  }

  // The panel is rendered by appending: replacing the whole log would throw
  // away and rebuild hundreds of nodes, and every DOM change wakes up the
  // content scripts of ad blockers and password managers (T6.3, T6.5).
  function debugRender() {
    var panel = document.getElementById("monoke-debug");
    var log = document.getElementById("monoke-debug-log");
    if (!log) {
      return;
    }
    if (panel && !panel.open) {
      // A closed panel is not painted, so there is nothing to update.
      return;
    }
    if (debugRendered > debugLines.length) {
      log.textContent = "";
      debugNodes = [];
      debugRendered = 0;
    }
    if (debugRendered === debugLines.length) {
      return;
    }
    var chunk = debugLines.slice(debugRendered);
    var node = document.createTextNode(chunk.join("\n") + "\n");
    log.appendChild(node);
    debugNodes.push({ node: node, lines: chunk.length });
    debugRendered = debugLines.length;

    while (debugRendered > DEBUG_MAX_LINES && debugNodes.length > 1) {
      var oldest = debugNodes.shift();
      log.removeChild(oldest.node);
      debugRendered -= oldest.lines;
    }

    var count = document.getElementById("monoke-debug-count");
    if (count) {
      setText(
        count,
        debugDropped
          ? String(debugLines.length) + " (+" + debugDropped + " dropped)"
          : String(debugLines.length)
      );
    }
  }

  // The buffer is bounded and flushed in batches: a long session must not
  // grow the page without limit (T6.5).
  var DEBUG_MAX_PER_SECOND = 20;
  var debugWindowStart = 0;
  var debugWindowCount = 0;
  var debugDropped = 0;

  function debugPush(message) {
    var now = Date.now();
    if (now - debugWindowStart > 1000) {
      debugWindowStart = now;
      debugWindowCount = 0;
    }
    if (debugWindowCount >= DEBUG_MAX_PER_SECOND) {
      // An extension that logs in a loop must not be able to turn the debug
      // panel into a mutation storm of its own.
      debugDropped += 1;
      return;
    }
    debugWindowCount += 1;

    debugLines.push(message);
    if (debugLines.length > DEBUG_MAX_LINES * 2) {
      debugLines.splice(0, debugLines.length - DEBUG_MAX_LINES);
    }
    if (debugTimer === null) {
      debugTimer = window.setTimeout(debugFlush, 1000);
    }
  }

  function debugFlush() {
    debugTimer = null;
    debugRender();
  }

  function debugClear() {
    debugLines.length = 0;
    var log = document.getElementById("monoke-debug-log");
    if (log) {
      log.textContent = "";
    }
    debugNodes = [];
    debugRendered = 0;
    var count = document.getElementById("monoke-debug-count");
    if (count) {
      setText(count, "0");
    }
  }

  function setupDebug() {
    if (!debugRequested()) {
      return;
    }
    var panel = document.getElementById("monoke-debug");
    if (!panel) {
      return;
    }
    panel.hidden = false;
    panel.open = true;

    var started = new WeakMap();

    var origError = console.error;
    console.error = function () {
      debugPush(
        "[console.error] " + Array.prototype.slice.call(arguments).join(" ")
      );
      origError.apply(console, arguments);
    };

    window.addEventListener("error", function (event) {
      debugPush(
        "[error] " +
          (event.message || event) +
          " @ " +
          (event.filename || "") +
          ":" +
          (event.lineno || "")
      );
    });

    window.addEventListener("unhandledrejection", function (event) {
      var reason = event.reason;
      debugPush(
        "[unhandledrejection] " +
          (reason && reason.message ? reason.message : String(reason))
      );
    });

    document.addEventListener("htmx:beforeRequest", function (event) {
      var elt = event.detail && event.detail.elt;
      if (elt) {
        started.set(elt, Date.now());
      }
    });

    document.addEventListener("htmx:afterRequest", function (event) {
      var elt = event.detail && event.detail.elt;
      var xhr = event.detail && event.detail.xhr;
      var elapsed = elt && started.has(elt) ? Date.now() - started.get(elt) : null;
      if (elt) {
        started.delete(elt);
      }
      debugPush(
        "[htmx] " +
          (xhr ? xhr.method : "?") +
          " " +
          (xhr ? xhr.responseURL || "" : "") +
          " -> " +
          (xhr ? xhr.status : "?") +
          (elapsed === null ? "" : " in " + elapsed + " ms")
      );
    });

    document.addEventListener("htmx:responseError", function (event) {
      var xhr = event.detail && event.detail.xhr;
      debugPush("[htmx:responseError] " + (xhr ? xhr.status : ""));
    });

    document.addEventListener("htmx:sendError", function (event) {
      var xhr = event.detail && event.detail.xhr;
      debugPush("[htmx:sendError] " + (xhr ? xhr.status : ""));
    });

    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-debug-clear]")) {
        event.preventDefault();
        debugClear();
      }
    });

    panel.addEventListener("toggle", function () {
      if (panel.open) {
        debugRender();
      }
    });

    debugPush("[debug] client capture active");
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncThemeButton();
    applyNsfw();

    // Upstream thumbnails can 404. Hide the broken image instead of
    // painting a broken-image icon over the layout.
    document.addEventListener(
      "error",
      function (event) {
        var target = event.target;
        if (!target || target.tagName !== "IMG") {
          return;
        }
        target.classList.add("thumb-missing");
        var holder = target.closest(
          ".post-thumb, .creator-thumb, .attachment-preview"
        );
        if (holder) {
          holder.classList.add("is-missing");
        }
      },
      true
    );

    var liveSync = syncRoot();
    if (liveSync && liveSync.getAttribute("data-run-id") && !quietMode()) {
      syncStart(
        liveSync.getAttribute("data-run-id"),
        liveSync.getAttribute("data-started-at")
      );
    }

    document.addEventListener("click", function (event) {
      var themeButton = event.target.closest("[data-theme-toggle]");
      if (themeButton) {
        var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(next, true);
        return;
      }

      var blurButton = event.target.closest("[data-nsfw-toggle]");
      if (blurButton) {
        setNsfw(!nsfw);
        return;
      }

      var copyAllButton = event.target.closest("[data-copy-all]");
      if (copyAllButton) {
        event.preventDefault();
        copyAllLinks(copyAllButton);
        return;
      }

      var loadButton = event.target.closest("[data-load-media]");
      if (loadButton) {
        event.preventDefault();
        loadAttachment(loadButton);
        return;
      }

      var cancelButton = event.target.closest("[data-sync-cancel]");
      if (cancelButton) {
        event.preventDefault();
        cancelSync();
        return;
      }

      var clickableRow = event.target.closest("[data-row-href]");
      if (
        clickableRow &&
        !event.target.closest("a, button, input, select, textarea, label")
      ) {
        window.location.href = clickableRow.getAttribute("data-row-href");
        return;
      }

      var copyButton = event.target.closest("[data-copy]");
      if (!copyButton) {
        return;
      }
      event.preventDefault();
      var value = copyButton.getAttribute("data-copy") || "";
      if (!value) {
        return;
      }
      copyText(value)
        .then(function () {
          var previous = copyButton.textContent;
          copyButton.textContent = "Copied";
          copyButton.classList.add("copied");
          window.setTimeout(function () {
            copyButton.textContent = previous;
            copyButton.classList.remove("copied");
          }, 1500);
        })
        .catch(function () {
          showFlash("Could not copy to the clipboard.", "error");
        });
    });

    document.addEventListener("submit", function (event) {
      var form = event.target;
      if (!form || !form.getAttribute) {
        return;
      }
      if (form.matches && form.matches("[data-bookmark-form]")) {
        event.preventDefault();
        toggleBookmarkForm(form);
        return;
      }
      if (form.matches && form.matches("[data-sync-form]")) {
        event.preventDefault();
        startSyncForm(form);
        return;
      }
      var message = form.getAttribute("data-confirm");
      if (message && !window.confirm(message)) {
        event.preventDefault();
      }
    });

    document.addEventListener("change", function (event) {
      var autoForm = event.target.closest("form[data-auto-submit]");
      if (autoForm) {
        if (autoForm.requestSubmit) {
          autoForm.requestSubmit();
        } else {
          autoForm.submit();
        }
        return;
      }

      var select = event.target.closest("[data-variant-select]");
      if (!select) {
        return;
      }
      var row = select.closest("[data-attachment]");
      var video = row && row.querySelector("video");
      if (video && video.getAttribute("src")) {
        video.src = select.value;
        video.load();
      }
    });

    // Client-side debug capture (shown when the server runs in debug mode,
    // or when ?debug=1 / #debug is in the URL).
    if (!quietMode()) {
      setupDebug();
    }

    // Stop the progress stream while the tab is in the background.
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        syncPause();
      } else {
        syncResume();
      }
    });
  });

  // htmx hooks: busy state and a visible message on failed swaps.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var target = event.detail && event.detail.elt;
    if (target && target.setAttribute) {
      target.setAttribute("aria-busy", "true");
    }
  });

  document.addEventListener("htmx:afterRequest", function (event) {
    var target = event.detail && event.detail.elt;
    if (target && target.removeAttribute) {
      target.removeAttribute("aria-busy");
    }

    if (!target || !target.matches || !target.matches("[data-filter-path]")) {
      return;
    }
    if (!event.detail || !event.detail.successful) {
      return;
    }

    // htmx requests /c/{id}/rows; the address bar keeps the canonical,
    // bookmarkable /c/{id}?filters URL instead.
    var params = new URLSearchParams();
    var data = new FormData(target);
    data.forEach(function (value, key) {
      var text = String(value).trim();
      if (!text || (key === "sort" && text === "published_desc")) {
        return;
      }
      params.append(key, text);
    });
    var query = params.toString();
    var path = target.getAttribute("data-filter-path") || window.location.pathname;
    window.history.replaceState({}, "", path + (query ? "?" + query : ""));
  });

  document.addEventListener("htmx:responseError", function () {
    showFlash("The request failed. Check the server log and try again.", "error");
  });

  // Return focus where the viewer was opened from. viewer.js stores the
  // selector in sessionStorage just before navigating back (T8.4).
  function restoreViewerFocus() {
    var selector = null;
    try {
      selector = window.sessionStorage.getItem("monoke-viewer-focus");
      window.sessionStorage.removeItem("monoke-viewer-focus");
    } catch (_error) {
      selector = null;
    }
    if (!selector && window.location.hash.length > 1) {
      selector = window.location.hash;
    }
    if (!selector) {
      return;
    }
    var target = null;
    try {
      target = document.querySelector(selector);
    } catch (_error) {
      return;
    }
    if (target && typeof target.focus === "function") {
      target.focus({ preventScroll: false });
    }
  }

  restoreViewerFocus();

  // Remember which link opened the viewer so the viewer can hand focus back
  // on close (T8.4). sessionStorage survives the navigation, localStorage does
  // not belong here.
  document.addEventListener("click", function (event) {
    var target = event.target;
    var link = target && target.closest
      ? target.closest("[data-viewer-open], [data-viewer-return]")
      : null;
    if (!link) {
      return;
    }
    var selector = link.getAttribute("data-viewer-focus");
    if (!selector) {
      selector = link.hasAttribute("data-viewer-open")
        ? "[data-viewer-open]"
        : "[data-viewer-return]";
    }
    try {
      window.sessionStorage.setItem("monoke-viewer-opener", selector);
    } catch (_error) {
      /* Storage disabled: focus falls back to the creator heading. */
    }
  });

  window.Monoke = {
    copy: copyText,
    theme: function (theme) {
      applyTheme(theme, true);
    },
    setNsfw: setNsfw,
    flash: showFlash
  };
})();
