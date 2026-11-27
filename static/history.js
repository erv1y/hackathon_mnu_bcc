"use strict";

const SESSION_RUNS = "pruvia_recent_reviews_v1";
const SAVED_RUNS = "pruvia_saved_versions_v1";
const RUN_LIMIT = 5;

function readRuns(storage, key) {
  try {
    const parsed = JSON.parse(window[storage].getItem(key) || "[]");
    return Array.isArray(parsed) ? parsed.filter((run) =>
      typeof run?.id === "string" && run.report?.document?.requirements && Array.isArray(run.versions)) : [];
  } catch (_) {
    return [];
  }
}

function writeRun(storage, key, run) {
  try {
    const others = readRuns(storage, key).filter((item) => item.id !== run.id);
    window[storage].setItem(key, JSON.stringify([run, ...others].slice(0, RUN_LIMIT)));
    return true;
  } catch (_) {
    return false;
  }
}

function readRecentRuns() {
  const sessions = readRuns("sessionStorage", SESSION_RUNS);
  const saved = readRuns("localStorage", SAVED_RUNS);
  return [...new Map([...sessions, ...saved].map((run) => [run.id, run])).values()]
    .sort((a, b) => b.time - a.time);
}

function runLink(run, findingId = "") {
  const base = run.id === "demo-preview" ? "/audit?demo=1" : `/audit?run=${encodeURIComponent(run.id)}`;
  return findingId ? `${base}#${encodeURIComponent(findingId)}` : base;
}

function createRun(report, snapshot) {
  return {
    id: crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) =>
      byte.toString(16).padStart(2, "0")).join(""),
    time: Date.now(),
    title: snapshot.title || msg("auditedText"),
    context: snapshot.context || "",
    report, versions: [],
    demoVersion: report.mode === "replay" ? state.demoVersion : null,
  };
}

function registerRun(report, snapshot) {
  if (report.mode === "replay") {
    return readRecentRuns().find((run) => run.demoVersion === state.demoVersion
      && run.report.language === report.language && reportMode(run.report) === reportMode(report)) || null;
  }
  const run = createRun(report, snapshot);
  writeRun("sessionStorage", SESSION_RUNS, run);
  return run;
}

function persistVersion(decisions) {
  const run = state.run || createRun(state.audit, state.snapshot);
  const previous = run.versions[0];
  if (previous && JSON.stringify(previous.decisions) === JSON.stringify(decisions)) return true;
  const updated = {
    ...run, versions: [{
      number: (previous?.number || 0) + 1, time: Date.now(), decisions,
    }, ...run.versions].slice(0, RUN_LIMIT),
  };
  if (!writeRun("localStorage", SAVED_RUNS, updated)) return false;
  state.run = updated;
  return true;
}

function displayDate(time) {
  return time ? new Intl.DateTimeFormat(LOCALES[state.language], { dateStyle: "short", timeStyle: "short" }).format(time) : "";
}

function auditCounts(report) {
  const counts = (report.mode === "rules" || report.auditor_failure) ? report.counts : report.scored_counts;
  return {
    score: report.score === null ? "—" : `${report.score} / 100`,
    findings: Object.values(counts).reduce((count, value) => count + value, 0),
    critical: (counts.Critical || 0) + (report.native ? 0 : counts.High || 0),
    requirements: report.document.requirements.length,
  };
}

function renderDashboard(demo = null) {
  const runs = readRecentRuns();
  const latest = runs[0] || demo;
  const values = latest ? auditCounts(latest.report) : {};
  for (const id of ["score", "findings", "critical", "requirements"]) {
    $(`overview-${id}`).textContent = values[id] ?? "—";
  }
  $("overview-origin").textContent = latest?.id === "demo-preview" ? msg("exampleLabel")
    : [latest?.title, latest?.report.mode === "rules" ? msg("ruleReview") : ""].filter(Boolean).join(" · ");
  const rows = $("recent-audits");
  rows.replaceChildren();
  for (const run of runs.length ? runs.slice(0, RUN_LIMIT) : [demo].filter(Boolean)) {
    const row = element("a", "dashboard-run");
    row.href = runLink(run);
    const label = element("span", "dashboard-run-title");
    label.append(element("strong", "", run.title),
      element("span", "hint", displayDate(run.time) || msg("exampleLabel")));
    const score = element("span", "run-score", auditCounts(run.report).score);
    row.append(element("span", "dashboard-doc-icon", "≡"), label, score, element("span", "run-arrow", "↗"));
    rows.append(row);
  }
  if (!rows.children.length) rows.append(element("p", "hint panel-empty", msg("noHistory")));
  const attention = $("attention-items");
  attention.replaceChildren();
  if (!latest) return;
  const critical = latest.report.modules.flatMap((agent) => agent.defects)
    .filter((item) => item.severity === "High" && !item.review_reason).slice(0, 3);
  if (!critical.length) {
    attention.append(element("p", "hint panel-empty", msg(latest.report.partial ? "attentionPartial" : "attentionEmpty")));
  }
  for (const finding of critical) {
    const row = element("a", "attention-row");
    row.href = runLink(latest, finding.id);
    row.append(element("span", "attention-dot"), element("span", "", finding.title), element("span", "run-arrow", "↗"));
    attention.append(row);
  }
}

async function loadDashboardExample(config) {
  renderDashboard();
  if (readRecentRuns().length || !config.demo_available || (config.auth_required && !state.password)) return;
  const language = state.language;
  try {
    const response = await fetch(`/api/demo?language=${language}`, { headers: authHeaders() });
    if (!response.ok) return;
    const { before } = await response.json();
    if (state.language === language) renderDashboard({ ...before, id: "demo-preview" });
  } catch (_) {
    // Недоступный пример не мешает открыть новую проверку.
  }
}
