"use strict";

(() => {
  const root = document.documentElement;
  const control = document.getElementById("scene-toggle");
  const supported = CSS.supports("transform-style", "preserve-3d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const storageKey = "pruvia_motion_v1";
  const scene = document.getElementById("route-scene");
  let preferred = true;
  let leaving = false;

  function readPreference() {
    try { preferred = localStorage.getItem(storageKey) !== "flat"; } catch (_) { /* Хранилище может быть отключено. */ }
  }

  function setMode() {
    const enabled = supported && preferred && !reduced.matches;
    root.dataset.depth = enabled ? "on" : "off";
    control.disabled = reduced.matches;
    control.setAttribute("aria-pressed", enabled);
    return enabled;
  }

  function pageOrder(url) {
    if (url.pathname === "/") return 0;
    if (url.pathname === "/audit") return url.search ? 2 : 1;
    if (url.pathname === "/how") return 3;
    return null;
  }

  function enterPage() {
    root.dataset.routeDirection = location.pathname === "/" ? "return" : "advance";
    root.dataset.routeMotion = "enter";
  }

  async function navigate(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
        || root.dataset.depth !== "on") return;
    const link = event.target.closest?.("a[href]");
    if (!link || (link.target && link.target !== "_self") || link.hasAttribute("download")) return;
    const destination = new URL(link.href);
    const current = new URL(location.href);
    if (destination.origin !== current.origin || pageOrder(destination) === null
        || (destination.pathname === current.pathname && destination.search === current.search)) return;

    event.preventDefault();
    if (leaving) return;
    leaving = true;
    root.dataset.routeDirection = pageOrder(destination) < pageOrder(current) ? "return" : "advance";
    root.dataset.routeMotion = "leave";
    const animation = scene.getAnimations?.().find((effect) => effect.animationName === "route-out");
    if (animation) {
      await Promise.race([
        animation.finished.catch(() => {}),
        new Promise((resolve) => setTimeout(resolve, 260)),
      ]);
    }
    location.assign(destination.href);
  }

  enterPage();
  readPreference();
  setMode();
  control.classList.toggle("hidden", !supported);
  control.addEventListener("click", () => {
    preferred = !preferred;
    root.dataset.routeMotion = "idle";
    try { localStorage.setItem(storageKey, preferred ? "depth" : "flat"); } catch (_) { /* На текущей странице настройка сохраняется в памяти. */ }
    setMode();
  });
  reduced.addEventListener("change", () => {
    root.dataset.routeMotion = "idle";
    setMode();
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    readPreference();
    root.dataset.routeMotion = "idle";
    setMode();
  });
  document.addEventListener("click", navigate);
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    leaving = false;
    readPreference();
    setMode();
    enterPage();
  });
})();
