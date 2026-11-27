"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");

const context = vm.createContext({});
vm.runInContext(readFileSync(require.resolve("../static/product-review.js"), "utf8"), context);
const plain = (value) => JSON.parse(JSON.stringify(value));
const first = "FR-1: Transfer limit is 100,000 KZT.";
const second = "BR-2: Transfer limit is 500,000 KZT.";
const source = first + "\n" + second;
const row = {
  verified: "exact", criterion_id: "contradiction", requirement_ids: ["R1", "R2"],
  sourceIds: {R1: "FR-1", R2: "BR-2"},
  evidence: [
    {requirement_id: "R1", start: 0, end: first.length, text: first},
    {requirement_id: "R2", start: first.length + 1, end: source.length, text: second},
  ],
};

test("coordinated edits require confirmation, preserve IDs, and reject overlaps", () => {
  const targets = plain(context.conflictTargets(row, source));
  assert.deepEqual(plain(context.conflictValues(targets)), ["100,000", "500,000"]);
  const replacements = [first.replace("100,000", "250,000"), second.replace("500,000", "250,000")];
  assert.equal(context.approvedConflictEdits(targets, replacements, [], false), null);
  assert.equal(context.approvedConflictEdits(targets, replacements, [targets[0]], true), null);
  assert.equal(context.approvedConflictEdits(targets, [replacements[0].replace("FR-1", "FR-9"), replacements[1]], [], true), null);
  const accepted = plain(context.approvedConflictEdits(targets, replacements, [], true));
  assert.equal(accepted.length, 2);
  assert.equal(context.validDraftEdits(source, accepted), true);
  assert.equal(context.validDraftEdits(source + "\n", [...accepted, accepted[0]]), false);
  assert.equal(context.validDraftEdits(source.replace("100,000", "200,000"), accepted), false);
  assert.equal(context.validDraftEdits(source, []), true);
  assert.equal(row.evidence[0].text, first);
});

test("an answered clarification needs separate approval of the same wording", () => {
  const answered = context.clarificationDecision(null, "answered", "250,000", "Board decision");
  assert.equal(answered.status, "answered");
  assert.equal(context.clarificationDecision(answered, "approved", "500,000", ""), null);
  const approved = context.clarificationDecision(answered, "approved", "250,000", "Board decision");
  assert.equal(approved.status, "approved");
  assert.equal(context.clarificationDecision(approved, "deferred", approved.answer, "").status, "deferred");
});

test("CSV and Excel outputs treat spreadsheet formulas as text", () => {
  const csv = context.spreadsheetRows([["  =1+1", "@cmd", 'quoted,"value"']]);
  assert.match(csv, /'  =1\+1/);
  assert.match(csv, /'@cmd/);
  assert.match(csv, /quoted,""value""/);
  const excel = context.spreadsheetRows([["=1+1", "<evidence>"]], true);
  assert.match(excel, /ss:Type="String"/);
  assert.match(excel, /&lt;evidence&gt;/);
  assert.doesNotMatch(excel, /ss:Formula/);
});

test("legacy demo conflicts only map quotes contained in one verified requirement", () => {
  const legacy = { ...row, criterion_id: "spec.contradiction", requirementRanges: [
    { id: "R1", start: 0, end: first.length },
    { id: "R2", start: first.length + 1, end: source.length },
  ], evidence: row.evidence.map(({ requirement_id, ...proof }) => proof) };
  const targets = plain(context.conflictTargets(legacy, source));
  assert.deepEqual(targets.map((target) => target.requirementId), ["R1", "R2"]);
  assert.equal(context.conflictTargets(legacy, source.replace("100,000", "200,000")).length, 0);
  assert.equal(context.conflictTargets({ ...legacy, requirementRanges: [
    ...legacy.requirementRanges, { id: "other", start: 0, end: source.length },
  ] }, source).length, 0);
});
