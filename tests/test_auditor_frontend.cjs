"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const app = readFileSync(require.resolve("../static/app.js"), "utf8");
const handlers = app.slice(app.indexOf("function auditorFailureKey("), app.indexOf("async function readReport("));

test("Auditor validation failure leaves the workflow and scene partial", () => {
  const auditor = { dataset: { key: "logic" }, lastChild: {} };
  const progress = { value: 3 };
  const scene = [];
  const steps = [];
  const context = vm.createContext({
    $: (id) => ({ "agent-list": { children: [auditor] }, "progress-bar": progress })[id],
    msg: (key) => key,
    sceneEvent: (event) => scene.push(event),
    setWorkflowStep: (...state) => steps.push(state),
  });
  vm.runInContext(handlers, context);

  context.handleEvent({ type: "agent_retry", key: "logic" });
  assert.equal(auditor.lastChild.textContent, "agentRetry");
  context.handleEvent({ type: "agent_done", key: "logic", ok: false, code: "auditor_validation_failed" });
  assert.equal(auditor.className, "agent-item failed");
  assert.equal(auditor.lastChild.textContent, "auditorValidationFailed");
  assert.equal(progress.value, 3);

  const result = { type: "result", auditor_failure: "auditor_validation_failed" };
  assert.equal(context.handleEvent(result), result);
  assert.deepEqual(steps, [[3, true]]);
  assert.deepEqual(scene.map((event) => event.type), ["agent_done", "partial_result"]);
});

test("restored stages are counted without reporting a new agent start", () => {
  const analyst = { dataset: { key: "payments" }, lastChild: {} };
  const progress = { value: 0 };
  const scene = [];
  const context = vm.createContext({
    $: (id) => ({ "agent-list": { children: [analyst] }, "progress-bar": progress })[id],
    msg: (key) => key,
    sceneEvent: (event) => scene.push(event),
  });
  vm.runInContext(handlers, context);
  context.handleEvent({ type: "agent_reused", key: "payments", ok: true });
  assert.equal(progress.value, 1);
  assert.equal(analyst.lastChild.textContent, "agentRestored");
  assert.ok(scene.every((event) => event.type !== "agent_start"));
});

test("3D pipeline retains the failed status and renders a partial result", () => {
  const file = readFileSync(require.resolve("../static/agent-scene/behaviors/pipeline.js"), "utf8");
  const pipeline = file.slice(file.indexOf("export class Pipeline")).replace("export class", "class");
  const states = [];
  const chips = [];
  const actor = { key: "logic", base: {}, clear() { return this; }, queue() {} };
  const context = vm.createContext({
    ctx: {
      deepActors: [actor], office: { desks: { logic: { seat: "mock" } } },
      setState: (state) => states.push(state), t: (key) => key,
      overlay: { say() {}, setChip: (chip) => chips.push(chip) },
    },
    doc: { hide() {} },
    T: { sit() {} },
  });
  vm.runInContext(pipeline + '\nvar subject = new Pipeline(ctx, doc); subject.active = true;', context);
  context.subject.agentDone("logic", false, "auditor_validation_failed");
  context.subject.partial("logic", "auditor_validation_failed");
  assert.equal(actor.status, "auditorValidationFailed");
  assert.equal(actor.base.active, 0);
  assert.deepEqual(states, ["partial"]);
  assert.equal(chips.at(-1), "auditPartialStatus");
});
