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

  var nsfw = nsfwEnabled();

  function applyNsfw() {
    var images = document.querySelectorAll("img.thumb");
    for (var index = 0; index < images.length; index += 1) {
      images[index].classList.toggle("nsfw", nsfw);
    }
    if (document.body) {
      document.body.setAttribute("data-nsfw", nsfw ? "on" : "off");
    }
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

  document.addEventListener("DOMContentLoaded", function () {
    syncThemeButton();
    applyNsfw();

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

    // Keep dynamically inserted (htmx) thumbnails blurred too.
    if (window.MutationObserver && document.body) {
      var observer = new MutationObserver(function () {
        applyNsfw();
      });
      observer.observe(document.body, { childList: true, subtree: true });
    }
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
  });

  document.addEventListener("htmx:afterSwap", function () {
    applyNsfw();
  });

  document.addEventListener("htmx:responseError", function () {
    showFlash("The request failed. Check the server log and try again.", "error");
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
