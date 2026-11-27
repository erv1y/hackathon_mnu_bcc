// Deep office activity follows independent graph node events.
import { THREE, clamp, ease, smooth } from "../kit.js";
import { T } from "../actor.js";

const HOLD_TILT = new THREE.Quaternion().setFromEuler(new THREE.Euler(1.05, 0, 0));
const tmpV = new THREE.Vector3();
const tmpQ = new THREE.Quaternion();

export class Doc {
  constructor(mesh, scene) {
    this.mesh = mesh;
    scene.add(mesh);
    this.mesh.visible = false;
    this.holder = null;
    this.flight = null;
    this.opacity = 0;
    this.target = 0;
  }

  show() { this.target = 1; this.mesh.visible = true; }
  hide() { this.target = 0; }

  setWorld(position, yaw = 0) {
    this.holder = null; this.flight = null;
    this.mesh.position.copy(position);
    this.mesh.quaternion.setFromEuler(new THREE.Euler(0, yaw, 0));
  }

  attach(holder) { this.holder = holder; this.flight = null; }

  fly(getTarget, duration, onDone, flat = false) {
    this.holder = null;
    this.flight = {
      from: this.mesh.position.clone(), fromQ: this.mesh.quaternion.clone(),
      getTarget, duration, onDone, flat, t: 0,
    };
  }

  update(dt, time) {
    this.opacity = smooth(this.opacity, this.target, dt, 5);
    this.mesh.userData.sheet.opacity = 1;
    this.mesh.userData.halo.opacity = this.opacity * (0.18 + Math.sin(time * 4) * 0.07);
    this.mesh.scale.setScalar(0.4 + 0.6 * this.opacity);
    if (this.opacity < 0.02 && this.target === 0) this.mesh.visible = false;
    if (this.holder) {
      this.holder.getWorldPosition(this.mesh.position);
      this.holder.getWorldQuaternion(tmpQ);
      this.mesh.quaternion.copy(tmpQ).multiply(HOLD_TILT);
    } else if (this.flight) {
      const f = this.flight;
      f.t += dt / f.duration;
      const k = ease(clamp(f.t));
      const target = f.getTarget(tmpV);
      this.mesh.position.lerpVectors(f.from, target, k);
      this.mesh.position.y += Math.sin(k * Math.PI) * 0.35;
      if (f.flat) this.mesh.quaternion.slerpQuaternions(f.fromQ, new THREE.Quaternion(), k);
      if (f.t >= 1) {
        this.flight = null;
        f.onDone?.();
      }
    }
  }
}

export class Pipeline {
  constructor(ctx, doc) {
    this.ctx = ctx;
    this.doc = doc;
    this.active = false;
    this.titles = {};
    this.running = new Set();
  }

  start() {
    this.reset();
    this.active = true;
    this.ctx.overlay.clearBubbles();
    for (const actor of this.ctx.deepActors) {
      this.ctx.idle.release(actor);
      actor.role = "pipeline";
      actor.clear().queue(T.sit(this.ctx.office.desks[actor.key].seat, "sceneStatusWaiting"));
      actor.status = "sceneStatusWaiting";
    }
  }

  setAgents(agents) {
    agents.forEach(({ key, title }) => { this.titles[key] = title; });
  }

  agentStart(key) {
    const actor = this.ctx.deepActors.find((item) => item.key === key);
    if (!this.active || !actor) return;
    const { office } = this.ctx;
    this.running.add(key);
    actor.base.active = 1;
    actor.clear();
    if (key === "payments") {
      this.doc.setWorld(office.printerOut.clone(), 0);
      this.doc.show();
      actor.queue(T.goto(office.spots.fax.node, "sceneStatusPrinter"),
        T.face(office.spots.fax.yaw), T.act("reach", 0.45),
        T.call(() => this.doc.attach(actor.character.hold)));
    }
    actor.queue(T.sit(office.desks[key].seat, "sceneStatusWorking"),
      T.act(key === "payments" || key === "logic" ? "read" : "type", 0,
        () => !this.running.has(key), "sceneStatusWorking"));
    this.updateChip();
  }

  agentDone(key, ok, code) {
    const actor = this.ctx.deepActors.find((item) => item.key === key);
    if (!this.active || !actor) return;
    this.running.delete(key);
    if (key === "payments") this.doc.hide();
    const status = ok ? "sceneStatusWorkDone" : code === "auditor_validation_failed"
      ? "auditorValidationFailed" : "failed";
    actor.clear().queue(T.sit(this.ctx.office.desks[key].seat, status));
    actor.status = status;
    actor.base.active = 0;
    this.updateChip();
  }

  validating() { this.updateChip(); }
  result() { this.finish(); }
  partial(key, code) {
    if (!this.active) return;
    const actor = this.ctx.deepActors.find((item) => item.key === key);
    this.doc.hide();
    this.running.clear();
    this.ctx.setState("partial");
    if (actor) {
      this.ctx.overlay.say(actor, this.ctx.t(code === "auditor_validation_failed"
        ? "auditorValidationFailed" : "auditorProviderFailed"), 4, "pipeline");
    }
    this.ctx.overlay.setChip(this.ctx.t("auditPartialStatus"));
  }
  error() { this.end(); }
  end() { this.reset(); }

  finish() {
    if (!this.active) return;
    this.reset();
    this.ctx.setState("completed");
  }

  reset() {
    this.doc.hide();
    if (this.active) for (const actor of this.ctx.deepActors) {
      actor.clear();
      actor.base.active = 0;
      actor.role = "idle";
    }
    this.active = false;
    this.running.clear();
    this.ctx.overlay.setChip("");
  }

  updateChip() {
    this.ctx.overlay.setChip([...this.running].map((key) => this.titles[key] || key).join(" · "));
  }

  update() {}
}
