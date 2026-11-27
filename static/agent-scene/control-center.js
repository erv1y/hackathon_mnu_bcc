// Agent Control Center: read-only observer of real analysis events.
export class ControlCenter {
  constructor(scene, text) {
    this.scene = scene;
    this.t = text;
    this.run = null;
    this.buttons = new Map();
    const make = (tag, cls) => {
      const el = document.createElement(tag);
      if (cls) el.className = cls;
      return el;
    };
    this.make = make;
    this.root = make("section", "agent-center");
    this.root.id = "agent-control-center";
    this.root.setAttribute("aria-labelledby", "agent-center-title");
    const header = make("div", "agent-center-head");
    this.title = make("h3"); this.title.id = "agent-center-title";
    this.summary = make("span", "agent-center-summary");
    header.append(this.title, this.summary);
    this.cards = make("div", "agent-center-cards");
    const feed = make("div", "agent-center-feed");
    this.feedTitle = make("h4");
    this.log = make("ol", "agent-center-log");
    this.log.setAttribute("aria-live", "polite");
    feed.append(this.feedTitle, this.log);
    this.root.append(header, this.cards, feed);
    scene.panel.append(this.root);
    this.render();
  }
  actors() {
    return this.scene.mode === "quick" ? [this.scene.quick.actor] : this.scene.deepActors;
  }
  name(key) {
    const actor = this.scene.allActors.find(a => a.key === key);
    return actor ? this.t(actor.config.name) : "";
  }
  activeRun() { return this.run?.mode === this.scene.mode ? this.run : null; }
  record(key, label) {
    const run = this.run;
    if (!run) return;
    const entry = { key, label, time: Math.floor((Date.now() - run.started) / 1000) };
    const last = run.events[0];
    if (last?.key === key && last.label === label) return;
    run.events.unshift(entry);
    run.events.length = Math.min(run.events.length, 8);
  }
  onEvent(detail) {
    if (detail.type === "center_reset") {
      this.run = null;
    } else if (detail.type === "start") {
      this.run = { mode: detail.mode === "quick" ? "quick" : "deep", started: Date.now(),
        states: new Map(), events: [], finished: false };
      this.record(null, "centerStarted");
    } else if (this.run && !this.run.finished) {
      const run = this.run;
      const valid = this.scene.allActors.some(a => a.key === detail.key);
      const state = (status, label) => {
        if (!valid) return;
        run.states.set(detail.key, status);
        this.record(detail.key, label);
      };
      switch (detail.type) {
        case "document_ready": this.record(null, "centerDocument"); break;
        case "agent_start": state("running", "centerRunning"); break;
        case "agent_retry": state("retry", "centerRetry"); break;
        case "agent_done": state(detail.ok === true ? "done" : "limited",
          detail.ok === true ? (detail.reused ? "centerReused" : "centerDone") : "centerLimited"); break;
        case "validating": this.record(null, "centerValidating"); break;
        case "partial_result":
          run.finished = true; this.record(null, "centerPartial"); break;
        case "result":
          run.finished = true; this.record(null, "centerReport"); break;
        case "done":
          run.finished = true; this.record(null, detail.ok === false ? "centerError" : "centerReport"); break;
        case "error":
          run.finished = true; this.record(null, "centerError"); break;
      }
    }
    this.render();
  }
  syncSelection() {
    for (const [key, button] of this.buttons) {
      const pressed = String(this.scene.selectedAgent === key);
      if (button.getAttribute("aria-pressed") !== pressed) button.setAttribute("aria-pressed", pressed);
    }
  }
  render() {
    const t = this.t, run = this.activeRun(), actors = this.actors();
    this.title.textContent = t("centerTitle");
    this.feedTitle.textContent = t("centerFeed");
    const completed = actors.filter(a => run?.states.get(a.key) === "done").length;
    this.summary.textContent = run ? t("centerProgress", {n: completed, total: actors.length})
      : t("centerCount", {n: actors.length});
    const keys = actors.map(a => a.key).join(",");
    if (keys !== this.keys) {
      this.keys = keys; this.buttons.clear(); this.cards.replaceChildren();
      for (const [i, actor] of actors.entries()) {
        const button = this.make("button", "agent-center-card"); button.type = "button";
        const top = this.make("span", "agent-center-card-top");
        const number = this.make("span", "agent-center-number"); number.textContent = String(i + 1).padStart(2,"0");
        const name = this.make("strong"); const role = this.make("span", "agent-center-role");
        const status = this.make("span", "agent-center-status");
        top.append(number, name); button.append(top, role, status);
        button.parts = {name, role, status};
        button.addEventListener("click", () => {
          if (!this.actors().some(a => a.key === actor.key)) return;
          this.scene.selectedAgent = this.scene.selectedAgent === actor.key ? null : actor.key;
          if (this.scene.inspect) this.scene.inspect.value = this.scene.selectedAgent || "";
          this.scene.selectAgent(this.scene.selectedAgent);
          this.scene.renderIfStatic(); this.syncSelection();
        });
        this.buttons.set(actor.key, button); this.cards.append(button);
      }
    }
    const labels = {running:"centerRunning",retry:"centerRetry",done:"centerDone",limited:"centerLimited",
      waiting:"centerWaiting",idle:"centerIdle",unknown:"centerUnknown"};
    for (const actor of actors) {
      const button = this.buttons.get(actor.key);
      let status = run?.states.get(actor.key) || (run ? "waiting" : "idle");
      if (run?.finished && ["waiting", "running", "retry"].includes(status)) status = "unknown";
      button.dataset.status = status;
      button.parts.name.textContent = t(actor.config.name);
      button.parts.role.textContent = t(actor.config.role);
      button.parts.status.textContent = t(labels[status]);
    }
    this.syncSelection();
    // Build a bounded, text-only log. No polling, model requests or fake events.
    const rows = (run?.events || []).map(entry => {
      const li = this.make("li"), time = this.make("time"), label = this.make("span");
      time.textContent = `${String(Math.floor(entry.time/60)).padStart(2,"0")}:${String(entry.time%60).padStart(2,"0")}`;
      label.textContent = [entry.key ? this.name(entry.key) : "", t(entry.label)].filter(Boolean).join(" · ");
      li.append(time, label); return li;
    });
    if (!rows.length) {
      const li = this.make("li", "agent-center-empty"); li.textContent = t("centerEmpty"); rows.push(li);
    }
    const signature = rows.map(li => li.textContent).join("\n");
    if (signature !== this.logSignature) { this.logSignature = signature; this.log.replaceChildren(...rows); }
  }
}
