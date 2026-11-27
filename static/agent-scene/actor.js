// Агент сцены: очередь задач (идти, сесть, действие, ожидание) + процедурная анимация.
import { clamp, smooth, angleTo, lerp, ease } from "./kit.js";
import { createCharacter, poseCharacter, SEAT_DROP } from "./characters.js";

export const T = {
  goto: (node, status) => ({ kind: "goto", node, status }),
  sit: (seat, status) => ({ kind: "sit", seat, status }),
  stand: () => ({ kind: "stand" }),
  face: (yaw) => ({ kind: "face", yaw }),
  act: (name, duration, until, status) => ({ kind: "act", name, duration, until, status }),
  wait: (duration, until, status) => ({ kind: "wait", duration, until, status }),
  call: (fn) => ({ kind: "call", fn }),
};

const SPEED = 1.55;

export class Actor {
  constructor(config, palette, graph) {
    this.config = config;
    this.key = config.key;
    this.graph = graph;
    this.character = createCharacter(config, palette);
    this.root = this.character.root;
    this.x = 0; this.z = 0; this.y = 0; this.yaw = 0;
    this.targetYaw = 0;
    this.tasks = [];
    this.current = null;
    this.weights = {};
    this.base = {};
    this.phase = 0;
    this.time = Math.random() * 10;
    this.seat = null;
    this.status = "sceneStatusIdle";
    this.timeScale = 1;
    this.role = "idle"; // idle | pipeline | quick
    this.reservation = null;
  }

  placeAt(x, z, yaw = 0) {
    this.x = x; this.z = z; this.yaw = yaw; this.targetYaw = yaw;
    this.sync();
  }

  seatNow(seat) {
    this.seat = seat;
    this.x = seat.x; this.z = seat.z; this.y = seat.h - SEAT_DROP;
    this.yaw = this.targetYaw = seat.yaw;
    this.weights.sit = 1;
    this.base.sit = 1;
    this.sync();
  }

  queue(...tasks) { this.tasks.push(...tasks.flat()); return this; }

  clear() {
    this.tasks = [];
    this.current = null;
    Object.keys(this.base).forEach((name) => { if (name !== "sit") this.base[name] = 0; });
    return this;
  }

  get busy() { return Boolean(this.current || this.tasks.length); }

  sync() {
    this.root.position.set(this.x, this.y, this.z);
    this.root.rotation.y = this.yaw;
  }

  begin(task) {
    task.elapsed = 0;
    if (task.status) this.status = task.status;
    if (task.kind === "goto") {
      if (this.seat) {
        this.tasks.unshift(task);
        task.deferred = true;
        return this.begin(this.current = T.stand());
      }
      const start = this.graph.nearest(this.x, this.z);
      task.route = this.graph.path(start, task.node).map((name) => this.graph.point(name));
      if (task.route.length && Math.hypot(task.route[0].x - this.x, task.route[0].z - this.z) < 0.05) task.route.shift();
      task.total = task.route.reduce((sum, p, i, all) => {
        const prev = i ? all[i - 1] : { x: this.x, z: this.z };
        return sum + Math.hypot(p.x - prev.x, p.z - prev.z);
      }, 0);
      task.travelled = 0;
    } else if (task.kind === "sit") {
      if (this.seat === task.seat) { task.done = true; return null; }
      if (this.seat) {
        this.tasks.unshift(task);
        return this.begin(this.current = T.stand());
      }
      const seatNode = this.graph.point(task.seat.node);
      if (Math.hypot(seatNode.x - this.x, seatNode.z - this.z) > 0.05) {
        this.tasks.unshift(task);
        return this.begin(this.current = T.goto(task.seat.node, task.status));
      }
      task.from = { x: this.x, z: this.z, y: this.y };
    } else if (task.kind === "stand") {
      if (!this.seat) { task.done = true; return null; }
      task.from = { x: this.x, z: this.z, y: this.y };
      task.to = this.graph.point(this.seat.node);
    } else if (task.kind === "call") {
      task.fn(this);
      task.done = true;
    }
    return null;
  }

  step(task, dt) {
    task.elapsed += dt;
    switch (task.kind) {
      case "goto": {
        const target = task.route[0];
        if (!target) return true;
        const dx = target.x - this.x, dz = target.z - this.z;
        const d = Math.hypot(dx, dz);
        const remaining = task.total - task.travelled;
        const speed = SPEED * clamp(Math.min(task.elapsed * 2.2 + 0.25, remaining * 1.6 + 0.3));
        const move = Math.min(d, speed * dt);
        if (d > 1e-4) {
          this.x += (dx / d) * move; this.z += (dz / d) * move;
          this.targetYaw = Math.atan2(dx, dz);
        }
        task.travelled += move;
        this.phase += move * 9.5;
        this.weights.walkTarget = 1;
        if (d - move < 0.02) task.route.shift();
        return task.route.length === 0;
      }
      case "sit":
      case "stand": {
        const k = ease(clamp(task.elapsed / 0.5));
        const seat = task.kind === "sit" ? task.seat : this.seat;
        const to = task.kind === "sit" ? { x: seat.x, z: seat.z, y: seat.h - SEAT_DROP } : { ...task.to, y: 0 };
        this.x = lerp(task.from.x, to.x, k);
        this.z = lerp(task.from.z, to.z, k);
        this.y = lerp(task.from.y, to.y, k);
        this.base.sit = task.kind === "sit" ? k : 1 - k;
        if (task.kind === "sit") this.targetYaw = seat.yaw;
        if (k >= 1) {
          if (task.kind === "sit") this.seat = seat; else this.seat = null;
          return true;
        }
        return false;
      }
      case "face":
        this.targetYaw = task.yaw;
        return task.elapsed > 0.35;
      case "act":
        this.weights[task.name + "Target"] = 1;
        return task.elapsed >= (task.duration || 0) && (!task.until || task.until(this, task));
      case "wait":
        return task.elapsed >= (task.duration || 0) && (!task.until || task.until(this, task));
      default:
        return true;
    }
  }

  update(rawDt) {
    const dt = rawDt * this.timeScale;
    this.time += dt;
    for (const key of Object.keys(this.weights)) if (key.endsWith("Target")) this.weights[key] = 0;

    let guard = 0;
    while (guard++ < 6) {
      if (!this.current) {
        this.current = this.tasks.shift() || null;
        if (!this.current) break;
        this.begin(this.current);
        if (this.current?.done) { this.current = null; continue; }
      }
      if (this.current.done || this.step(this.current, guard === 1 ? dt : 0)) {
        this.current = null;
        continue;
      }
      break;
    }

    const names = ["walk", "type", "read", "game", "drink", "talk", "gesture", "shrug", "cheer", "reach", "carry", "lean", "active"];
    for (const name of names) {
      const target = Math.max(this.weights[name + "Target"] || 0, this.base[name] || 0);
      this.weights[name] = smooth(this.weights[name] || 0, target, dt, name === "walk" ? 10 : 6);
    }
    this.weights.sit = this.base.sit || 0;
    this.weights.look = smooth(this.weights.look || 0, this.base.look || 0, dt, 4);
    this.yaw = angleTo(this.yaw, this.targetYaw, dt, 8);
    this.sync();
    poseCharacter(this.character, this.weights, this.time, this.phase, dt);
  }

  headPosition(target) {
    this.character.head.getWorldPosition(target);
    target.y += 0.36;
    return target;
  }
}
