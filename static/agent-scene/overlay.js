// HTML-слой поверх canvas: облачка реплик, карточка при наведении, статус шага.
import { THREE } from "./kit.js";

const MAX_BUBBLES = 2;

export class Overlay {
  constructor(viewport) {
    this.root = document.createElement("div");
    this.root.className = "agent-scene-overlay";
    this.root.setAttribute("aria-hidden", "true");
    this.bubbles = [];
    this.card = this.node("div", "agent-scene-card hidden");
    this.cardName = this.node("strong", "", "", this.card);
    this.cardRole = this.node("span", "agent-scene-card-role", "", this.card);
    this.cardStatus = this.node("span", "agent-scene-card-status", "", this.card);
    this.chip = this.node("div", "agent-scene-chip hidden");
    this.root.append(this.card);
    viewport.append(this.root, this.chip);
    this.vector = new THREE.Vector3();
  }

  node(tag, className, text = "", parent = null) {
    const el = document.createElement(tag);
    el.className = className;
    if (text) el.textContent = text;
    if (parent) parent.append(el);
    return el;
  }

  say(actor, text, duration = 3.2, kind = "") {
    if (!text) return false;
    const existing = this.bubbles.find((bubble) => bubble.actor === actor);
    if (existing) this.drop(existing);
    if (this.bubbles.length >= MAX_BUBBLES) {
      if (!kind) return false;
      this.drop(this.bubbles[0]);
    }
    const el = this.node("div", `agent-scene-bubble ${kind}`.trim(), text);
    this.root.append(el);
    const bubble = { actor, el, time: duration, kind };
    this.bubbles.push(bubble);
    requestAnimationFrame(() => el.classList.add("show"));
    return true;
  }

  drop(bubble) {
    this.bubbles = this.bubbles.filter((item) => item !== bubble);
    bubble.el.classList.remove("show");
    setTimeout(() => bubble.el.remove(), 250);
  }

  clearBubbles() { [...this.bubbles].forEach((bubble) => this.drop(bubble)); }

  project(actor, camera, width, height) {
    actor.headPosition(this.vector);
    this.vector.project(camera);
    return { x: (this.vector.x * 0.5 + 0.5) * width, y: (-this.vector.y * 0.5 + 0.5) * height };
  }

  update(dt, camera, width, height) {
    for (const bubble of [...this.bubbles]) {
      bubble.time -= dt;
      if (bubble.time <= 0 || !bubble.actor.root.visible || bubble.actor.root.scale.x < 0.5) { this.drop(bubble); continue; }
      const { x, y } = this.project(bubble.actor, camera, width, height);
      const inset = 6;
      const left = Math.max(inset, Math.min(width - bubble.el.offsetWidth - inset, x - bubble.el.offsetWidth / 2));
      const top = Math.max(inset, Math.min(height - bubble.el.offsetHeight - inset, y - bubble.el.offsetHeight - 8));
      bubble.el.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    }
    if (this.hovered) {
      const { x, y } = this.project(this.hovered, camera, width, height);
      const left = Math.max(6, Math.min(width - this.card.offsetWidth - 6, x + 18));
      const top = Math.max(6, Math.min(height - this.card.offsetHeight - 6, y - 8));
      this.card.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    }
  }

  showCard(actor, name, role, status) {
    this.hovered = actor;
    this.cardName.textContent = name;
    this.cardRole.textContent = role;
    this.cardStatus.textContent = status;
    this.card.classList.remove("hidden");
  }

  hideCard() {
    this.hovered = null;
    this.card.classList.add("hidden");
  }

  setChip(text) {
    this.chip.textContent = text || "";
    this.chip.classList.toggle("hidden", !text);
  }

  destroy() {
    this.root.remove();
    this.chip.remove();
  }
}
