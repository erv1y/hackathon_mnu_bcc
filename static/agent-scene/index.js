// Фоновая 3D-сцена с AI-агентами. Только слушает события "agentscene" и не меняет логику сайта.
import { THREE, rbox, place, mat } from "./kit.js";
import { readPalette } from "./palette.js";
import { agentConfigs, quickAgentConfig } from "./agents-config.js";
import { Actor } from "./actor.js";
import { buildOffice } from "./scenes/office.js";
import { buildApartment } from "./scenes/apartment.js";
import { OfficeIdle } from "./behaviors/idle.js";
import { Pipeline, Doc } from "./behaviors/pipeline.js";
import { QuickAgent } from "./behaviors/quick-work.js";
import { Morph } from "./morph.js";
import { Overlay } from "./overlay.js";
import * as P from "./props.js";

const MODE_KEY = "agentscene_mode_v1";
const LANGS = { ru: 0, kk: 1, en: 2 };

function storedMode() {
  try { return localStorage.getItem(MODE_KEY) === "quick" ? "quick" : "deep"; } catch (_) { return "deep"; }
}

function remember(mode) {
  try { localStorage.setItem(MODE_KEY, mode); } catch (_) { /* Хранилище может быть отключено. */ }
}

function text(key, values = {}) {
  const table = typeof UI_TEXT === "undefined" ? null : UI_TEXT; // eslint-disable-line no-undef
  const row = table?.[key];
  if (!row) return "";
  const index = LANGS[document.documentElement.lang] ?? 0;
  return (row[index] || row[0]).replace(/\{(\w+)\}/g, (placeholder, name) => values[name] ?? placeholder);
}

class AgentScene {
  constructor() {
    this.state = "idle";
    this.hoveredAgentId = null;
    this.mode = storedMode();
    this.clock = new THREE.Clock(false);
    this.time = 0;
    this.pointer = null;
    this.pointerDirty = false;
    this.backgroundCache = new WeakMap();
  }

  get animated() {
    return document.documentElement.dataset.depth !== "off" && !matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  init() {
    const canvas = document.createElement("canvas");
    canvas.className = "agent-scene-canvas";
    canvas.setAttribute("aria-hidden", "true");
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "low-power" });
    } catch (_) {
      return false; // Без WebGL сайт просто работает без сцены.
    }
    this.renderer = renderer;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.setClearColor(0x000000, 0);
    document.body.prepend(canvas);
    this.canvas = canvas;

    const p = this.palette = readPalette();
    const scene = this.scene = new THREE.Scene();

    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 100);
    scene.add(new THREE.HemisphereLight(0xffffff, p.border2, 1.55));
    const sun = new THREE.DirectionalLight(0xffffff, 1.9);
    sun.position.set(-4, 10, 6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -9, right: 9, top: 8, bottom: -8, near: 1, far: 30 });
    sun.shadow.bias = -0.0005;
    sun.shadow.radius = 4;
    scene.add(sun);
    const fill = new THREE.DirectionalLight(p.soft, 0.5);
    fill.position.set(6, 4, -2);
    scene.add(fill);

    // Общая комната: пол, две стены, окна.
    const floorMat = mat(p.floorOffice, { own: true });
    const wallMat = mat(p.wall, { own: true });
    const room = new THREE.Group();
    room.add(place(rbox(12.4, 0.3, 9.4, floorMat, 0.12), 0, -0.15, 0));
    room.add(place(rbox(0.16, 2.5, 9.4, wallMat, 0.05), -6.12, 1.25, 0));
    room.add(place(rbox(12.4, 2.5, 0.16, wallMat, 0.05), 0, 1.25, -4.62));
    room.add(place(rbox(0.2, 0.08, 9.4, mat(p.trim), 0.03), -6.08, 0.04, 0));
    room.add(place(rbox(12.4, 0.08, 0.2, mat(p.trim), 0.03), 0, 0.04, -4.58));
    [-2.4, 0.6, 3.4].forEach((z) => room.add(place(P.windowPanel(p), -6.02, 1.45, z)));
    scene.add(room);

    this.office = buildOffice(p);
    this.apartment = buildApartment(p);
    scene.add(this.office.root, this.apartment.root);

    this.overlay = new Overlay();
    const ctx = {
      palette: p, scene, office: this.office, apartment: this.apartment, overlay: this.overlay,
      t: text, setState: (state) => this.setState(state),
      get state() { return scene.userData.state; },
    };
    this.ctx = ctx;

    this.deepActors = agentConfigs(p).map((config) => {
      const actor = new Actor(config, p, this.office.graph);
      actor.mug = P.mug(p, config.outfit.top);
      actor.mug.visible = false;
      actor.mug.position.set(0, -0.06, 0.03);
      actor.character.handR.add(actor.mug);
      scene.add(actor.root);
      return actor;
    });
    ctx.deepActors = this.deepActors;
    this.idle = ctx.idle = new OfficeIdle(ctx);
    this.idle.seed();

    this.doc = new Doc(P.paper(p), scene);
    this.pipeline = new Pipeline(ctx, this.doc);

    const quickActor = new Actor(quickAgentConfig(p), p, this.apartment.graph);
    scene.add(quickActor.root);
    this.quick = new QuickAgent(ctx, quickActor, P.gamepad(p));
    this.allActors = [...this.deepActors, quickActor];

    this.morph = new Morph({
      office: this.office.root,
      home: this.apartment.root,
      actors: [...this.deepActors.map((actor) => ({ actor, side: "office" })), { actor: quickActor, side: "home" }],
      surfaces: [
        { material: floorMat, office: p.floorOffice, home: p.floorHome },
        { material: wallMat, office: p.wall, home: p.wallHome },
      ],
    });
    this.morph.to(this.mode, true);

    this.raycaster = new THREE.Raycaster();
    this.resize();
    this.bind();
    // Пара тиков, чтобы позы встали на места до первого кадра.
    for (let i = 0; i < 3; i += 1) this.tick(1 / 30);
    this.render();
    requestAnimationFrame(() => canvas.classList.add("ready"));
    this.updateLoop();
    return true;
  }

  setState(state) {
    this.state = state;
    this.scene.userData.state = state;
    document.documentElement.dataset.agentScene = state;
  }

  bind() {
    window.addEventListener("agentscene", (event) => this.onEvent(event.detail || {}));
    window.addEventListener("resize", () => { this.resize(); this.renderIfStatic(); });
    document.addEventListener("visibilitychange", () => this.updateLoop());
    window.addEventListener("pointermove", (event) => {
      this.pointer = { x: event.clientX, y: event.clientY };
      this.pointerDirty = true;
    }, { passive: true });
    document.documentElement.addEventListener("pointerleave", () => { this.pointer = null; this.pointerDirty = true; });
    new MutationObserver((changes) => {
      if (changes.some((change) => change.attributeName === "data-depth")) this.updateLoop();
      if (changes.some((change) => change.attributeName === "lang")) this.refreshTexts();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-depth", "lang"] });
    matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", () => this.updateLoop());
  }

  onEvent(detail) {
    switch (detail.type) {
      case "mode": this.setMode(detail.mode); break;
      case "upload": this.onUpload(); break;
      case "start":
        if (detail.mode) this.setMode(detail.mode, true);
        this.setState("processing");
        if (this.mode === "deep") this.pipeline.start();
        break;
      case "agents": this.pipeline.setAgents(detail.agents || []); break;
      case "agent_start":
        if (detail.key === "quick") this.quick.start(); else this.pipeline.agentStart(detail.key);
        break;
      case "agent_done": this.pipeline.agentDone(detail.key, detail.ok !== false); break;
      case "validating": this.pipeline.validating(); break;
      case "result": this.pipeline.result(); this.quick.result(); break;
      case "done":
        if (detail.ok === false) this.onError();
        else { this.pipeline.finish(); this.quick.finish(); }
        break;
      case "error": this.onError(); break;
      default: break;
    }
    if (!this.animated) this.settleStatic();
  }

  onError() {
    this.setState("error");
    this.pipeline.error();
    this.quick.error();
    setTimeout(() => { if (this.state === "error") this.setState("idle"); }, 2000);
  }

  onUpload() {
    if (this.state === "processing") return;
    this.setState("upload-started");
    const actor = this.mode === "quick"
      ? this.quick.actor
      : this.deepActors.find((item) => item.role === "idle" && item.root.visible) || this.deepActors[0];
    this.overlay.say(actor, text("sceneUpload"), 2.4, "pipeline");
    clearTimeout(this.uploadTimer);
    this.uploadTimer = setTimeout(() => { if (this.state === "upload-started") this.setState("idle"); }, 2400);
  }

  setMode(mode, instant = false) {
    mode = mode === "quick" ? "quick" : "deep";
    remember(mode);
    if (mode === this.mode) return;
    this.mode = mode;
    this.pipeline.reset();
    this.pipeline.end();
    this.quick.reset();
    this.overlay.clearBubbles();
    this.overlay.setChip("");
    if (!instant && this.animated) this.setState("mode-switching");
    this.morph.to(mode, instant || !this.animated);
    this.updateLoop();
  }

  refreshTexts() {
    if (this.pipeline.active) this.pipeline.updateChip();
    if (this.hoveredAgentId) this.showHover(this.allActors.find((a) => a.key === this.hoveredAgentId));
    this.overlay.clearBubbles();
  }

  resize() {
    const width = innerWidth, height = innerHeight;
    this.width = width; this.height = height;
    this.renderer.setSize(width, height, false);
    const aspect = width / height;
    // Комната целиком помещается в кадр и смещена вправо-вниз, чтобы не спорить с заголовками.
    const narrow = aspect < 1;
    const halfH = Math.max(narrow ? 6.6 : 6.4, (narrow ? 10.5 : 10.2) / aspect);
    const cam = this.camera;
    cam.left = -halfH * aspect; cam.right = halfH * aspect; cam.top = halfH; cam.bottom = -halfH;
    const target = new THREE.Vector3(narrow ? -0.3 : -1.6, 0.2, narrow ? -0.3 : -1.6);
    this.cameraTarget = target;
    cam.position.copy(target).add(new THREE.Vector3(14, 12.5, 14));
    cam.lookAt(target);
    cam.updateProjectionMatrix();
  }

  isBackground(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return false;
    if (this.backgroundCache.has(el)) return this.backgroundCache.get(el);
    let node = el;
    let result = true;
    while (node && node !== document.documentElement) {
      if (node === document.body) break;
      if (node.matches?.("a,button,input,select,textarea,label,summary,[role=dialog],[tabindex]:not(#route-scene)")) { result = false; break; }
      const style = getComputedStyle(node);
      const bg = style.backgroundColor;
      if ((bg && bg !== "transparent" && !/rgba\(.*,\s*0\)$/.test(bg)) || style.backgroundImage !== "none") { result = false; break; }
      if (node.childNodes && [...node.childNodes].some((child) => child.nodeType === 3 && child.textContent.trim())) { result = false; break; }
      node = node.parentElement;
    }
    this.backgroundCache.set(el, result);
    return result;
  }

  updateHover() {
    if (!this.pointerDirty) return;
    this.pointerDirty = false;
    let hit = null;
    if (this.pointer && this.isBackground(this.pointer.x, this.pointer.y)) {
      const ndc = new THREE.Vector2((this.pointer.x / this.width) * 2 - 1, -(this.pointer.y / this.height) * 2 + 1);
      this.raycaster.setFromCamera(ndc, this.camera);
      const visible = this.allActors.filter((actor) => actor.root.visible && actor.root.scale.x > 0.6).map((actor) => actor.root);
      const found = this.raycaster.intersectObjects(visible, true).find((item) => item.object.userData.actorKey && item.object.geometry.type !== "RingGeometry");
      if (found) hit = this.allActors.find((actor) => actor.key === found.object.userData.actorKey);
    }
    if ((hit?.key || null) === this.hoveredAgentId) return;
    this.allActors.forEach((actor) => actor.character.setHighlight(actor === hit ? 1 : 0));
    this.hoveredAgentId = hit?.key || null;
    if (hit) this.showHover(hit); else this.overlay.hideCard();
    this.renderIfStatic();
  }

  showHover(actor) {
    if (!actor) return;
    const role = this.pipeline.titles[actor.key] || text(actor.config.role);
    this.overlay.showCard(actor, text(actor.config.name), role, text(actor.status));
  }

  tick(dt) {
    this.time += dt;
    const morphing = this.morph.update(dt);
    if (!morphing && this.state === "mode-switching") this.setState("idle");
    if (this.mode === "deep" || this.morph.running) {
      this.idle.update();
      this.deepActors.forEach((actor) => actor.update(dt));
      this.office.monitors.forEach((screen, i) => { screen.emissiveIntensity = 0.32 + Math.sin(this.time * 2 + i) * 0.05; });
    }
    if (this.mode === "quick" || this.morph.running) this.quick.actor.update(dt);
    this.quick.update(dt, this.time);
    this.pipeline.update(dt);
    this.doc.update(dt, this.time);
    if (this.mode === "quick") this.doc.hide();
    // Едва заметный дрейф камеры.
    if (this.cameraTarget && this.animated) {
      const drift = Math.sin(this.time * 0.12) * 0.12;
      this.camera.position.set(this.cameraTarget.x + 14 + drift, this.cameraTarget.y + 12.5, this.cameraTarget.z + 14 - drift);
      this.camera.lookAt(this.cameraTarget);
    }
    this.updateHover();
    if (this.hoveredAgentId) this.showHover(this.allActors.find((a) => a.key === this.hoveredAgentId));
    this.overlay.update(dt, this.camera, this.width, this.height);
  }

  render() { this.renderer.render(this.scene, this.camera); }

  renderIfStatic() {
    if (!this.running && this.renderer) {
      this.overlay.update(0, this.camera, this.width, this.height);
      this.render();
    }
  }

  // Без анимации: один кадр с итоговым состоянием, без морфа.
  settleStatic() {
    this.morph.to(this.mode, true);
    this.renderIfStatic();
  }

  updateLoop() {
    const shouldRun = this.animated && !document.hidden;
    if (shouldRun && !this.running) {
      this.running = true;
      this.clock.start();
      const loop = () => {
        if (!this.running) return;
        const dt = Math.min(0.1, this.clock.getDelta());
        this.tick(dt);
        this.render();
        this.frame = requestAnimationFrame(loop);
      };
      this.frame = requestAnimationFrame(loop);
    } else if (!shouldRun && this.running) {
      this.running = false;
      cancelAnimationFrame(this.frame);
      this.clock.stop();
      if (!this.animated) {
        this.overlay.clearBubbles();
        this.settleStatic();
      }
    } else if (!shouldRun) {
      this.settleStatic();
    }
  }
}

function boot() {
  const scene = new AgentScene();
  try {
    if (scene.init()) {
      window.__agentScene = scene;
      replay(scene);
    } else {
      window.removeEventListener("agentscene", early);
    }
  } catch (failure) {
    console.warn("agent-scene disabled:", failure);
    document.querySelector(".agent-scene-canvas")?.remove();
  }
}

// Ленивый старт: после загрузки страницы и в свободное время браузера.
function lazyBoot() {
  const run = () => (window.requestIdleCallback ? requestIdleCallback(boot, { timeout: 800 }) : setTimeout(boot, 60));
  if (document.readyState === "complete") run(); else window.addEventListener("load", run, { once: true });
}

// События до старта сцены (ленивая загрузка) копим и проигрываем после инициализации.
const pending = [];
const early = (event) => pending.push(event.detail || {});
window.addEventListener("agentscene", early);

function replay(scene) {
  window.removeEventListener("agentscene", early);
  const checked = document.querySelector('input[name="check-mode"]:checked')?.value;
  if (checked) scene.setMode(checked, true);
  pending.splice(0).forEach((detail) => scene.onEvent(detail));
}

lazyBoot();
