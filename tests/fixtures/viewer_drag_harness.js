// T8.11 test harness: drives the real viewer.js with a minimal fake DOM so
// the drag wiring (not just the pure helpers) can be verified in Node.
const fs = require("fs");
const vm = require("vm");

const path = require("path");
const VIEWER_JS = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "monoke",
  "web",
  "static",
  "viewer.js"
);
const SOURCE = fs.readFileSync(VIEWER_JS, "utf-8");

class ClassList {
  constructor() {
    this.set = new Set();
  }
  add(...names) {
    names.forEach((n) => this.set.add(n));
  }
  remove(...names) {
    names.forEach((n) => this.set.delete(n));
  }
  contains(name) {
    return this.set.has(name);
  }
  toggle(name, force) {
    if (force === undefined) {
      force = !this.set.has(name);
    }
    if (force) {
      this.set.add(name);
    } else {
      this.set.delete(name);
    }
    return force;
  }
}

function parseSimple(part) {
  const simple = [];
  const re =
    /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w-]+)(?:([~|^$*]?=)"?([^\]"]*)"?)?\]|::?([\w-]+)(\(([^()]*)\))?/g;
  let match;
  while ((match = re.exec(part)) !== null) {
    if (match[1]) {
      simple.push({ type: "tag", value: match[1].toLowerCase() });
    } else if (match[2]) {
      simple.push({ type: "class", value: match[2] });
    } else if (match[3]) {
      simple.push({
        type: "attr",
        name: match[3],
        op: match[4] || null,
        value: match[5] === undefined ? null : match[5]
      });
    } else if (match[6]) {
      simple.push({ type: "not", value: match[7] });
    }
  }
  return simple;
}

function matchSimple(el, simple) {
  return simple.every((one) => {
    if (one.type === "tag") {
      return el.tagName.toLowerCase() === one.value;
    }
    if (one.type === "class") {
      return el.classList.contains(one.value);
    }
    if (one.type === "attr") {
      if (!(one.name in el.attributes)) {
        return false;
      }
      if (!one.op) {
        return true;
      }
      const actual = el.attributes[one.name];
      return one.op === "=" ? actual === one.value : actual !== one.value;
    }
    if (one.type === "not") {
      return !matchSimple(el, parseSimple(one.value));
    }
    return false;
  });
}

class Element {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.style = {};
    this.classList = new ClassList();
    this.listeners = {};
    this.textContent = "";
    this.hidden = false;
    this.rect = { width: 0, height: 0, left: 0, top: 0 };
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.offsetWidth = 100;
    this.complete = true;
    // The fake document has no Image loader; images are considered decoded so
    // the gesture tests exercise the normal painted-image path.
    this.naturalWidth = this.tagName === "IMG" ? 1 : 0;
    this.naturalHeight = this.tagName === "IMG" ? 1 : 0;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "src" && this.__autoLoad && typeof this.onload === "function") {
      // Detached deep-window loaders always complete from the browser cache in
      // the harness, so their decode/settle callbacks can run immediately.
      this.__autoLoad = false;
      this.onload();
    }
  }
  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }
  removeAttribute(name) {
    delete this.attributes[name];
  }
  appendChild(child) {
    if (child.parentNode && child.parentNode !== this) {
      child.parentNode.removeChild(child);
    }
    if (this.children.indexOf(child) === -1) {
      this.children.push(child);
    }
    child.parentNode = this;
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index !== -1) {
      this.children.splice(index, 1);
    }
    child.parentNode = null;
    return child;
  }
  insertBefore(child, reference) {
    const index = this.children.indexOf(reference);
    if (index === -1) {
      return this.appendChild(child);
    }
    if (child.parentNode && child.parentNode !== this) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.children.splice(index, 0, child);
    return child;
  }
  replaceChildren(...nodes) {
    this.children.forEach((child) => {
      child.parentNode = null;
    });
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners[type] || [];
    const index = list.indexOf(fn);
    if (index !== -1) {
      list.splice(index, 1);
    }
  }
  dispatch(type, event) {
    (this.listeners[type] || []).slice().forEach((fn) => fn(event));
  }
  descendants() {
    const out = [];
    this.children.forEach((child) => {
      out.push(child);
      out.push(...child.descendants());
    });
    return out;
  }
  querySelectorAll(selector) {
    const parts = selector.split(",").map((part) => parseSimple(part.trim()));
    return this.descendants().filter((el) =>
      parts.some((part) => matchSimple(el, part))
    );
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
  matches(selector) {
    const parts = selector.split(",").map((part) => parseSimple(part.trim()));
    return parts.some((part) => matchSimple(this, part));
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) {
        return node;
      }
      node = node.parentNode;
    }
    return null;
  }
  getBoundingClientRect() {
    return this.rect;
  }
  focus() {}
  pause() {}
  load() {}
}

// `className = "a b"` must feed the same class set the selectors read.
Object.defineProperty(Element.prototype, "className", {
  get() {
    return Array.from(this.classList.set).join(" ");
  },
  set(value) {
    this.classList.set = new Set(
      String(value || "")
        .split(/\s+/)
        .filter(Boolean)
    );
  }
});

// `loader.src = url` must go through setAttribute so the fake image can report
// the load that a detached deep-window loader would do.
Object.defineProperty(Element.prototype, "src", {
  get() {
    return this.attributes.src || "";
  },
  set(value) {
    this.setAttribute("src", value);
  }
});

function makeEvent(fields) {
  return Object.assign(
    { cancelable: true, preventDefault() {}, stopPropagation() {} },
    fields
  );
}

function build(stateOverrides) {
  const root = new Element("main");
  root.setAttribute("data-viewer-root", "");
  const stage = new Element("div");
  stage.setAttribute("data-viewer-stage", "");
  const bench = new Element("div");
  bench.setAttribute("data-viewer-paint-bench", "");
  bench.setAttribute("aria-hidden", "true");
  bench.setAttribute("inert", "");
  const host = new Element("div");
  host.setAttribute("data-viewer-media", "");
  host.clientWidth = 1000;
  host.clientHeight = 800;
  const active = new Element("img");
  active.setAttribute("data-viewer-media-el", "");
  active.rect = { width: 800, height: 200, left: 100, top: 300 };
  host.appendChild(active);
  stage.appendChild(bench);
  stage.appendChild(host);

  const counter = new Element("span");
  counter.setAttribute("data-viewer-counter", "");
  const announcer = new Element("p");
  announcer.setAttribute("data-viewer-announcer", "");
  const flash = new Element("p");
  flash.setAttribute("data-viewer-flash", "");
  const hint = new Element("div");
  hint.setAttribute("data-viewer-hint", "");
  const toast = new Element("p");
  toast.setAttribute("data-viewer-toast", "");
  stage.appendChild(counter);
  stage.appendChild(announcer);
  stage.appendChild(flash);
  stage.appendChild(hint);
  stage.appendChild(toast);
  const close = new Element("button");
  close.setAttribute("data-viewer-close", "");
  const fullscreen = new Element("button");
  fullscreen.setAttribute("data-viewer-fullscreen", "");
  root.appendChild(close);
  root.appendChild(fullscreen);
  root.appendChild(stage);

  const state = Object.assign(
    {
      creator: { id: 1, name: "Tester" },
      post: { service_id: "post-1", title: "Post one" },
      media: [
        {
          kind: "image",
          url: "https://x/1.jpg",
          width: 1600,
          height: 900,
          sha256: "a"
        },
        {
          kind: "image",
          url: "https://x/2.jpg",
          width: 1600,
          height: 900,
          sha256: "b"
        },
        {
          kind: "image",
          url: "https://x/3.jpg",
          width: 1600,
          height: 900,
          sha256: "c"
        }
      ],
      index: 0,
      post_rank: 1,
      media_post_count: 1,
      previous_post: null,
      next_post: null,
      neighbour_images: { previous: [], next: [] }
    },
    stateOverrides || {}
  );

  const stateNode = new Element("script");
  stateNode.setAttribute("id", "viewer-initial");
  stateNode.textContent = JSON.stringify(state);

  const document = {
    body: new Element("body"),
    visibilityState: "visible",
    fullscreenElement: null,
    listeners: {},
    getElementById(id) {
      return id === "viewer-initial" ? stateNode : null;
    },
    querySelector(selector) {
      if (root.matches(selector)) {
        return root;
      }
      return root.querySelector(selector);
    },
    querySelectorAll(selector) {
      return root.querySelectorAll(selector);
    },
    createElement(tag) {
      return new Element(tag);
    },
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      const list = this.listeners[type] || [];
      const index = list.indexOf(fn);
      if (index !== -1) {
        list.splice(index, 1);
      }
    }
  };

  const frames = [];
  // The viewer throttles navigation with Date.now(). A controllable clock lets
  // the harness model two committed swipes in quick succession without waiting
  // on real time.
  let clockSkew = 0;
  const RealDate = Date;
  function HarnessDate(...args) {
    if (args.length) {
      return new RealDate(...args);
    }
    return new RealDate(RealDate.now() + clockSkew);
  }
  HarnessDate.now = () => RealDate.now() + clockSkew;
  HarnessDate.parse = RealDate.parse;
  HarnessDate.UTC = RealDate.UTC;
  HarnessDate.prototype = RealDate.prototype;

  const window = {
    innerWidth: 1000,
    innerHeight: 800,
    location: {
      search: "",
      pathname: "/c/1/viewer",
      assign() {}
    },
    history: { replaceState() {} },
    sessionStorage: {
      getItem: () => null,
      setItem() {},
      removeItem() {}
    },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    requestAnimationFrame: (fn) => {
      frames.push(fn);
      return frames.length;
    },
    cancelAnimationFrame: () => {},
    requestIdleCallback: () => 1,
    cancelIdleCallback: () => {},
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (handle) => clearTimeout(handle),
    addEventListener() {},
    removeEventListener() {},
    getComputedStyle: () => ({})
  };

  const context = {
    window,
    document,
    navigator: {},
    console,
    setTimeout,
    clearTimeout,
    URLSearchParams,
    Map,
    Set,
    Math,
    Date: HarnessDate,
    JSON,
    Object,
    Array,
    Number,
    String,
    Boolean,
    AbortController,
    // Detached deep-window loaders use `new Image()`; the fake element reports
    // a decoded bitmap so the ring can prove paint readiness across frames.
    Image: function Image() {
      const loader = new Element("img");
      loader.__autoLoad = true;
      return loader;
    },
    fetch: () => Promise.reject(new Error("no network in the harness"))
  };
  context.globalThis = context;
  context.self = context;
  vm.createContext(context);
  vm.runInContext(SOURCE, context);

  // Drain nested requestAnimationFrame callbacks (paint readiness schedules a
  // second frame), so the ring is genuinely painted before any gesture.
  function flushFrames() {
    let guard = 0;
    while (frames.length && guard < 20) {
      frames.splice(0).forEach((fn) => fn());
      guard += 1;
    }
  }
  flushFrames();

  function pointer(type, x, y) {
    return makeEvent({
      type,
      pointerId: 1,
      pointerType: "touch",
      button: 0,
      clientX: x,
      clientY: y,
      target: stage
    });
  }

  function drag(points) {
    stage.dispatch("pointerdown", pointer("pointerdown", points[0][0], points[0][1]));
    points.slice(1).forEach((point) => {
      stage.dispatch("pointermove", pointer("pointermove", point[0], point[1]));
    });
    frames.splice(0).forEach((fn) => fn());
  }

  function release(x, y) {
    stage.dispatch("pointerup", pointer("pointerup", x, y));
  }

  return {
    root,
    stage,
    bench,
    host,
    active,
    counter,
    flash,
    frames,
    flushFrames,
    drag,
    release,
    advance(ms) {
      clockSkew += ms;
    },
    snapshot() {
      const debug = window.__monokeViewerDebug;
      return debug ? debug.snapshot() : null;
    }
  };
}

const results = [];
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  results.push({ name, ok, actual, expected });
}

// --- 1. horizontal drag to the left ---------------------------------------
{
  const env = build();
  env.drag([
    [500, 400],
    [470, 402],
    [400, 400]
  ]);
  const preview = env.host.querySelector(".is-preview");
  check("preview layer created", Boolean(preview), true);
  check("active follows the finger", env.active.style.transform, "translate3d(-100px, 0px, 0)");
  check(
    "preview is flush with the current media",
    preview && preview.style.transform,
    "translate3d(800px, 0px, 0)"
  );
  check(
    "two media elements at most",
    env.host.querySelectorAll("[data-viewer-media-el]").length,
    2
  );
  env.release(400, 400);
  check("committed to the next image", env.counter.textContent, "2 / 3");
  check("incoming layer settles at centre", preview.style.transform, "translate3d(0, 0, 0)");
  check(
    "outgoing layer is leaving",
    env.host.querySelector(".is-leaving").style.pointerEvents,
    "none"
  );
}

// --- 2. vertical drag down: older post, vertical only ---------------------
{
  const env = build({ previous_post: "post-2" });
  env.drag([
    [500, 200],
    [500, 230],
    [498, 300]
  ]);
  check("vertical drag moves on the y axis", env.active.style.transform, "translate3d(0px, 100px, 0)");
  check("no horizontal movement", env.active.style.transform.includes("100px, 0px"), false);
  env.release(498, 300);
  check("no image navigation from a vertical drag", env.counter.textContent, "1 / 3");
}

// --- 3. mostly vertical diagonal stays vertical ----------------------------
{
  const env = build();
  env.drag([
    [500, 200],
    [492, 230],
    [486, 300]
  ]);
  check(
    "diagonal stays vertical (rubber-banded at the archive edge)",
    env.active.style.transform,
    "translate3d(0px, 25px, 0)"
  );
}

// --- 4. jitter does not lock an axis ---------------------------------------
{
  const env = build();
  env.drag([
    [500, 400],
    [494, 404]
  ]);
  check("no axis lock on jitter", env.active.style.transform, undefined);
  check(
    "no preview on jitter",
    env.host.querySelectorAll("[data-viewer-media-el]").length,
    1
  );
}

// --- 5. archive edge rubber-bands ------------------------------------------
{
  const env = build({
    media: [
      { kind: "image", url: "https://x/1.jpg", width: 1600, height: 900, sha256: "a" }
    ]
  });
  env.drag([
    [500, 400],
    [450, 400],
    [400, 400]
  ]);
  check("edge drag is rubber-banded", env.active.style.transform, "translate3d(-25px, 0px, 0)");
  check(
    "no preview at the archive edge",
    env.host.querySelectorAll("[data-viewer-media-el]").length,
    1
  );
}

// --- 6. paint ring at rest, promotion and cancellation --------------------
{
  const env = build({ index: 1 });
  const warm = env.bench.querySelectorAll("img.viewer-warm");
  check("ring mounts exactly two neighbours", warm.length, 2);
  check(
    "ring holds the filtered previous and next",
    warm.map((el) => el.getAttribute("src")).sort(),
    ["https://x/1.jpg", "https://x/3.jpg"]
  );
  check("ring sits outside the media host", env.bench.parentNode === env.stage, true);
  check("ring bench is inert", env.bench.getAttribute("inert"), "");
  check(
    "media host still has only the active layer",
    env.host.querySelectorAll("[data-viewer-media-el]").length,
    1
  );
}

{
  const env = build();
  const nextNode = env.bench.querySelector("img.viewer-warm");
  check("next ring node is warm before the gesture", nextNode.getAttribute("src"), "https://x/2.jpg");
  env.drag([
    [500, 400],
    [470, 402],
    [400, 400]
  ]);
  const preview = env.host.querySelector(".is-preview");
  check("first drag frame reuses the exact painted node", preview === nextNode, true);
  const srcBeforeCommit = nextNode.getAttribute("src");
  env.release(400, 400);
  check("commit promotes the same node", env.host.children.includes(nextNode), true);
  check("commit never clears or refetches the source", nextNode.getAttribute("src"), srcBeforeCommit);
  check("committed counter", env.counter.textContent, "2 / 3");
  check(
    "no duplicate node for the destination",
    env.host.children.filter(
      (el) => el.getAttribute("src") === "https://x/2.jpg"
    ).length,
    1
  );
}

{
  const env = build();
  const nextNode = env.bench.querySelector("img.viewer-warm");
  env.drag([
    [500, 400],
    [400, 400]
  ]);
  check("ring node promoted for preview", env.host.children.includes(nextNode), true);
  // Releasing towards the non-existent previous item cancels the gesture.
  env.release(560, 400);
  check("cancelled swipe returns the node to the ring", env.bench.children.includes(nextNode), true);
  check("returned node keeps its source", nextNode.getAttribute("src"), "https://x/2.jpg");
  check("returned node regains ring handlers", typeof nextNode.onerror, "function");
  check(
    "host is back to one layer after cancel",
    env.host.querySelectorAll("[data-viewer-media-el]").length,
    1
  );
  check("counter did not move", env.counter.textContent, "1 / 3");
}

// --- 7. server-rendered media and stale promotion -------------------------
{
  const env = build();
  check("server layer adopted its item", Boolean(env.active.__viewerItem), true);
  check("server layer adopted the source", env.active.getAttribute("src"), "https://x/1.jpg");
  check("server layer is the active role", env.active.__viewerRole, "active");
  check("server layer is marked ready", env.active.classList.contains("is-ready"), true);
}

{
  const env = build();
  const nextNode = env.bench.querySelector("img.viewer-warm");
  // Simulate a late cleanup clearing the warmed node's source.
  nextNode.removeAttribute("src");
  env.drag([
    [500, 400],
    [470, 402],
    [400, 400]
  ]);
  const preview = env.host.querySelector(".is-preview");
  check("cleared warmed src is never promoted", preview === nextNode, false);
  check(
    "fresh preview carries the destination source",
    preview && preview.getAttribute("src"),
    "https://x/2.jpg"
  );
  env.release(560, 400);
  check("fresh cancel removes the temporary preview", env.host.children.includes(preview), false);
}

// --- 8. failed source: one thumbnail retry, then explicit unavailable ------
{
  const env = build({
    media: [
      {
        kind: "image",
        url: "https://x/1.jpg",
        thumb_url: "https://x/1-thumb.jpg",
        width: 1600,
        height: 900,
        sha256: "a"
      },
      { kind: "image", url: "https://x/2.jpg", width: 1600, height: 900, sha256: "b" },
      { kind: "image", url: "https://x/3.jpg", width: 1600, height: 900, sha256: "c" }
    ]
  });
  env.active.dispatch("error");
  check("failed image retries the thumbnail once", env.active.getAttribute("src"), "https://x/1-thumb.jpg");
  env.active.dispatch("error");
  check(
    "second failure shows the explicit unavailable card",
    Boolean(env.host.querySelector(".viewer-unavailable")),
    true
  );
  check("no black image layer survives", env.host.querySelector("img"), null);
}

{
  const env = build();
  env.drag([
    [500, 400],
    [470, 402],
    [400, 400]
  ]);
  const preview = env.host.querySelector(".is-preview");
  env.release(400, 400);
  check("promoted layer gets the same dimensions", preview.getAttribute("width"), "1600");
  check("promoted layer keeps error handling", Boolean(preview.listeners.error), true);
  check("promoted layer is marked ready", preview.classList.contains("is-ready"), true);
}

// --- 9. rapid consecutive swipes keep the ring bounded ---------------------
{
  const env = build({
    media: [
      { kind: "image", url: "https://x/1.jpg", width: 1600, height: 900, sha256: "a" },
      { kind: "image", url: "https://x/2.jpg", width: 1600, height: 900, sha256: "b" },
      { kind: "image", url: "https://x/3.jpg", width: 1600, height: 900, sha256: "c" },
      { kind: "image", url: "https://x/4.jpg", width: 1600, height: 900, sha256: "d" }
    ]
  });
  for (let step = 0; step < 2; step += 1) {
    env.drag([
      [500, 400],
      [470, 402],
      [400, 400]
    ]);
    env.release(400, 400);
    env.flushFrames();
    env.advance(200);
  }
  const snap = env.snapshot();
  check("two rapid swipes advanced twice", env.counter.textContent, "3 / 4");
  check("rapid swipes leave one warm next node", snap.liveWarm, 1);
  check("the warm node is the new next slot", snap.ring.next, "https://x/4.jpg");
  const nextTiming = snap.timing.find((row) => row.url === "https://x/4.jpg");
  check("new ring node started exactly one request", nextTiming && nextTiming.requests, 1);
  const benchSources = env.bench
    .querySelectorAll("img")
    .map((el) => el.getAttribute("src"));
  check("bench holds only the ring-owned source", benchSources, ["https://x/4.jpg"]);
  const hostSources = env.host
    .querySelectorAll("[data-viewer-media-el]")
    .map((el) => el.getAttribute("src"));
  check(
    "no URL is mounted twice across host and bench",
    new Set(hostSources.concat(benchSources)).size,
    hostSources.length + benchSources.length
  );
}

// --- 10. ring handlers hand over on promotion, return on cancel -----------
{
  const env = build();
  const nextNode = env.bench.querySelector("img.viewer-warm");
  check("warm node owns ring error handling", typeof nextNode.onerror, "function");
  check("duplicate ownership is counted", env.snapshot().stats.duplicates > 0, true);
  env.drag([
    [500, 400],
    [470, 402],
    [400, 400]
  ]);
  env.release(400, 400);
  check("promoted node drops ring handlers", nextNode.onerror, null);
  check("promoted node keeps viewer error listener", Boolean(nextNode.listeners.error), true);
  const timing = env.snapshot().timing.find((row) => row.url === "https://x/2.jpg");
  check("destination was requested once", timing && timing.requests, 1);
  check(
    "destination painted before promotion",
    timing.paintedAt <= timing.promotedAt,
    true
  );
  const before = env.snapshot().liveWarm;
  nextNode.dispatch("error");
  check(
    "active failure does not spawn a bench fallback",
    env.snapshot().liveWarm <= before,
    true
  );
}

results.forEach((result) => {
  if (!result.ok) {
    console.log("FAIL", result.name, "got", result.actual, "want", result.expected);
  } else {
    console.log("ok  ", result.name);
  }
});
const failed = results.filter((result) => !result.ok).length;
console.log(failed === 0 ? "ALL CHECKS PASSED" : failed + " CHECKS FAILED");
process.exit(failed === 0 ? 0 : 1);
