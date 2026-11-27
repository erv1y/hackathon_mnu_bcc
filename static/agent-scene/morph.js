// Перетекание офис ↔ квартира: предметы уходят и появляются волной от центра,
// слегка смещаясь и масштабируясь; пол и стены меняют цвет. Без резких исчезновений.
import { clamp, ease, lerp } from "./kit.js";

function prepare(root, centerX = 0, centerZ = 0) {
  return root.children.map((item) => {
    const distance = Math.hypot(item.position.x - centerX, item.position.z - centerZ);
    return {
      item,
      position: item.position.clone(),
      scale: item.scale.clone(),
      delay: clamp(distance / 7.5) * 0.25 + Math.random() * 0.04,
    };
  });
}

function apply(root, entries, factor, centerX, centerZ, visibleExtra) {
  const known = new Set(entries.map((entry) => entry.item));
  root.children.forEach((child) => { if (!known.has(child)) child.visible = visibleExtra; });
  for (const entry of entries) {
    if (entry.item.parent !== root) continue;
    const k = entry.local(factor);
    const s = Math.max(0.0001, k);
    entry.item.visible = k > 0.002;
    entry.item.scale.set(entry.scale.x * s, entry.scale.y * s, entry.scale.z * s);
    entry.item.position.set(
      lerp(centerX + (entry.position.x - centerX) * 0.82, entry.position.x, k),
      entry.position.y - (1 - k) * 0.35,
      lerp(centerZ + (entry.position.z - centerZ) * 0.82, entry.position.z, k),
    );
  }
}

export class Morph {
  constructor({ office, home, actors, surfaces }) {
    this.officeRoot = office;
    this.homeRoot = home;
    this.office = prepare(office);
    this.home = prepare(home);
    this.office.forEach((e) => { e.local = (p) => 1 - ease(clamp((p - e.delay) / 0.5)); });
    this.home.forEach((e) => { e.local = (p) => ease(clamp((p - 0.25 - e.delay) / 0.5)); });
    this.actors = actors; // [{ actor, side: "office" | "home" }]
    this.surfaces = surfaces; // [{ material, office: Color, home: Color }]
    this.progress = 0;
    this.target = 0;
    this.duration = 1.3;
  }

  get running() { return this.progress !== this.target; }

  to(mode, instant = false) {
    this.target = mode === "quick" ? 1 : 0;
    if (instant) this.progress = this.target;
    this.apply();
  }

  update(dt) {
    if (!this.running) return false;
    const step = dt / this.duration;
    this.progress = this.target > this.progress
      ? Math.min(this.target, this.progress + step)
      : Math.max(this.target, this.progress - step);
    this.apply();
    return true;
  }

  apply() {
    const p = this.progress;
    apply(this.officeRoot, this.office, p, -0.5, -0.3, p < 0.4);
    apply(this.homeRoot, this.home, p, -0.5, -0.3, p > 0.6);
    for (const { actor, side } of this.actors) {
      const k = side === "office" ? 1 - ease(clamp((p - 0.05) / 0.45)) : ease(clamp((p - 0.45) / 0.45));
      actor.root.visible = k > 0.002;
      actor.root.scale.setScalar(Math.max(0.0001, k));
    }
    const k = ease(p);
    for (const surface of this.surfaces) surface.material.color.copy(surface.office).lerp(surface.home, k);
  }
}
