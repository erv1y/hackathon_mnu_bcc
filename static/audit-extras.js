"use strict";

const FILE_ROLES = ["main", "appendix", "glossary", "related", "checklist"];
const REFUND_EXAMPLE = [["01_refund_api.md", "main"], ["02_refund_policy.md", "related"]];
const checkModeKey = (mode) => mode === "quick" ? "modeQuick" : "modeDeep";
const reportMode = (report) => report.check?.mode || "deep";
const reportSources = (report) => {
  const { files, text } = report.document;
  const chars = Array.from(text);
  return files?.length ? files.map(({ name, role, start, end }) =>
    ({ name, role, text: chars.slice(start, end).join("") })) : [{ role: "main", text }];
};

function renderFiles() {
  const roles = state.config?.document_roles || FILE_ROLES;
  const rows = state.files.map(({ file, role }, index) => {
    const row = element("li", "package-file");
    const name = element("span", "file-name", file.name);
    name.title = file.name;
    const selector = element("select", "audit-select");
    selector.setAttribute("aria-label", msg("fileRole", { name: file.name }));
    for (const value of roles) {
      const option = element("option", "", msg("role_" + value));
      option.value = value;
      selector.append(option);
    }
    selector.value = role;
    selector.addEventListener("change", () => {
      state.files = state.files.map((item, i) => i === index ? { file, role: selector.value } : item);
      updateStaleNotice();
    });
    const remove = button("link-btn", msg("remove"), () => {
      state.files = state.files.filter((_, i) => i !== index);
      renderFiles();
      updateStaleNotice();
      ($("file-list").children[Math.min(index, state.files.length - 1)]?.querySelector("button") || $("spec-file")).focus();
    });
    remove.setAttribute("aria-label", msg("removeFile", { name: file.name }));
    row.append(name, selector, remove);
    return row;
  });
  $("file-list").replaceChildren(...rows);
  $("file-info-title").textContent = msg("fileCount", { n: rows.length, max: limits().max_files });
  show($("file-info"), rows.length > 0);
  $("dropzone").classList.toggle("has-file", rows.length > 0);
  $("upload-glyph").textContent = rows.length ? "✓" : "↑";
}

function addFiles(files) {
  if (state.running) return;
  const existing = [...state.files];
  const known = new Set(existing.map(({ file }) => JSON.stringify([file.name, file.size, file.lastModified])));
  const { max_files, max_upload_mb } = limits();
  const rejected = [];
  for (const file of files) {
    const fingerprint = JSON.stringify([file.name, file.size, file.lastModified]);
    if (known.has(fingerprint)) continue;
    if (existing.length >= max_files) {
      rejected.push(msg("tooManyFiles", { n: max_files }));
      break;
    }
    let reason = "";
    if (!/\.(pdf|docx|txt|md)$/i.test(file.name)) reason = msg("badType");
    else if (file.size > max_upload_mb * 1024 * 1024) reason = msg("tooLarge", { limit: max_upload_mb });
    else if (!file.size) reason = msg("emptyFile");
    if (reason) {
      rejected.push(file.name + " · " + reason);
      continue;
    }
    known.add(fingerprint);
    existing.push({ file, role: existing.some((item) => item.role === "main") ? "appendix" : "main" });
  }
  error(rejected.join("\n"));
  if (existing.length !== state.files.length) {
    window.dispatchEvent(new CustomEvent("agentscene", { detail: { type: "upload" } }));
    state.files = existing;
    renderFiles();
    updateStaleNotice();
  }
}

function renderModeChoices() {
  const local = state.config?.api_key_configured === false;
  for (const mode of ["quick", "deep"]) {
    const rules = local ? state.config?.rule_modes?.[mode] : state.config?.check_modes?.[mode]?.criteria;
    $(mode + "-rules").textContent = rules === undefined ? "" : msg("modeRules", { n: rules });
    $(mode + "-description").textContent = msg(local ? mode + "RulesDescription" : mode + "Description");
    document.querySelector(`input[name="check-mode"][value="${mode}"]`).checked = state.checkMode === mode;
  }
  $("setup-engine").textContent = msg(local ? "localProcessing" : "modelProcessing");
  window.dispatchEvent(new CustomEvent("agentscene", { detail: { type: "mode", mode: state.checkMode } }));
}

async function loadRefundExample() {
  state.exampleLoaded = true;
  try {
    const files = await Promise.all(REFUND_EXAMPLE.map(async ([name, role]) => {
      const response = await fetch("/static/examples/" + name);
      if (!response.ok) throw new Error(msg("exampleFailed"));
      const text = await response.text();
      return { file: new File([text], name, { type: "text/markdown" }), role };
    }));
    if (state.files.length || state.running) return;
    state.files = files;
    switchTab("file-tab");
    renderFiles();
  } catch (_) {
    error(msg("exampleFailed"));
  }
}

function canDeepen() {
  if (state.audit.mode === "replay") return Boolean(state.demoVersion && state.config?.demo_available);
  const original = ["file", "text", "package"].includes(state.snapshot.kind);
  return Boolean((state.audit.mode === "rules" || state.config?.api_key_configured) && (original || !state.audit.source_warnings.length));
}

async function deepenReport() {
  const { audit, snapshot, run, demoVersion } = state;
  const baseline = { report: audit, title: $("result-title").textContent, id: run?.id || "quick-baseline" };
  if (audit.mode === "replay") {
    await runDemo(demoVersion, false, "deep", baseline);
    return;
  }
  const source = ["file", "text", "package"].includes(snapshot.kind) ? snapshot : {
    kind: "package", documents: reportSources(audit), title: snapshot.title, context: run?.context || "",
  };
  await runAudit(source, "deep", baseline);
}

function renderRoleSummaries() {
  const summaries = state.audit.insights?.summaries;
  show($("role-summaries"), Boolean(summaries));
  if (!summaries) return;
  $("role-summary-cards").replaceChildren(...Object.entries(summaries).map(([role, summary]) => {
    const card = element("article", "review-panel");
    card.append(element("h4", "", msg("summary_" + role)), element("p", "", summary.text));
    if (summary.items.length) {
      const list = element("ul", "rule-list");
      list.append(...summary.items.map((text) => element("li", "", text)));
      card.append(list);
    }
    return card;
  }));
}

function insightCard(title, row) {
  const card = element("article", "insight-card");
  if (row.id) {
    card.id = row.id;
    card.tabIndex = -1;
  }
  const heading = element("div", "review-panel-head");
  heading.append(element("h4", "", title), element("span", "badge " + row.severity, levelName(row.severity)));
  if (state.comparison?.result?.new.includes(row.id)) heading.append(element("span", "badge finding-new", msg("newFinding")));
  card.append(heading);
  if (row.evidence.length) card.append(makeEvidence({ evidence: row.evidence, verified: "exact" }));
  return card;
}

function defaultLabel(row) {
  return row.example || row.id?.startsWith("A-") ? "illustrativeExample" : "proposedDefault";
}

function renderCustomerQuestions() {
  const rows = state.audit.insights?.questions || [];
  $("copy-questions").disabled = $("download-questions").disabled = !rows.length;
  $("customer-question-list").replaceChildren(...(rows.length ? rows.map((row) => {
    const card = insightCard(row.question, row);
    card.append(labelled(msg(defaultLabel(row)) + " ·", row.default, "hint"));
    return card;
  }) : [element("p", "hint", msg("noCustomerQuestions"))]));
}

function customerQuestionText(language = state.audit.language) {
  const rows = state.audit.insights?.questions || [];
  return [msg("customerQuestions", {}, language), "", ...rows.flatMap((row, index) => [
    (index + 1) + ". " + row.question,
    ...row.evidence.map((proof) => "   " + lineLabel(proof, language) + " · «" + proof.text + "»"),
    "   " + msg(defaultLabel(row), {}, language) + " · " + row.default, "",
  ])].join("\n");
}

const SCENARIO_FIELDS = [
  ["condition", "scenarioCondition"], ["check", "scenarioCheck"], ["expected", "scenarioExpected"],
];

function renderScenarios() {
  const panels = [
    ["sanity-findings", "sanity", "feasibilityTitle"],
    ["ambiguous-findings", "ambiguities", "ambiguityTitle"],
    ["defensive-scenarios", "scenarios", "scenariosTitle"],
  ];
  for (const [id, key, title] of panels) {
    const rows = state.audit.insights?.[key] || [];
    const panel = $(id);
    panel.replaceChildren(element("h3", "", msg(title)));
    if (!rows.length) {
      const ambiguous = key === "ambiguities" && reportMode(state.audit) === "quick";
      panel.append(element("p", "hint", msg(ambiguous ? "deepOnly" : "noScenarios")));
    }
    for (const row of rows) {
      const card = insightCard(row.title, row);
      if (key === "scenarios") {
        const list = element("ol", "scenario-steps");
        for (const [field, label] of SCENARIO_FIELDS) {
          const step = element("li");
          step.append(labelled(msg(label) + " ·", row[field], ""));
          list.append(step);
        }
        card.append(list);
      } else {
        card.append(element("p", "", row.question), labelled(msg(defaultLabel(row)) + " ·", row.default, "hint"));
      }
      panel.append(card);
    }
  }
}

function appendInsightsMarkdown(lines, report) {
  const info = report.insights;
  if (!info) return;
  const t = (key) => msg(key, {}, report.language);
  lines.push("", "## " + t("roleSummary"));
  for (const [role, summary] of Object.entries(info.summaries)) {
    lines.push("", "### " + t("summary_" + role), "", markdownText(summary.text),
      ...summary.items.map((item) => "- " + markdownText(item)));
  }
  for (const [key, title] of [["sanity", "feasibilityTitle"], ["ambiguities", "ambiguityTitle"],
    ["questions", "customerQuestions"], ["scenarios", "scenariosTitle"]]) {
    if (!info[key].length) continue;
    lines.push("", "## " + t(title));
    for (const row of info[key]) {
      lines.push("", "### " + markdownText(key === "questions" ? row.question : row.title));
      for (const proof of row.evidence) {
        lines.push("- " + markdownText(lineLabel(proof, report.language)) + " · " + markdownText(proof.text));
      }
      if (key === "scenarios") {
        for (const [field, label] of SCENARIO_FIELDS) lines.push("- " + t(label) + " · " + markdownText(row[field]));
      } else {
        lines.push("- " + t(defaultLabel(row)) + " · " + markdownText(row.default));
      }
    }
  }
}

function comparableFindings(report) {
  const main = report.modules.flatMap((module) => module.defects.filter((row) => !row.review_reason));
  return report.mode === "rules" || report.native ? main : [...main, ...(report.insights?.sanity || []), ...(report.insights?.ambiguities || [])];
}

function openReviewFinding(id) {
  if (state.audit.modules.some((module) => module.defects.some((defect) => defect.id === id))) openFinding(id);
  else {
    openReviewTab("scenarios");
    focusReviewCard(id);
  }
}

function renderReleaseDecision() {
  const { audit } = state;
  const priorities = comparableFindings(audit).filter((row) => ["Critical", "High"].includes(row.severity) && row.evidence.length).slice(0, 3);
  const status = priorities.length ? "decisionAction" : audit.partial ? "decisionPartial"
    : audit.mode === "rules" ? "decisionLocal" : "decisionReady";
  $("review-decision").dataset.tone = priorities.length ? "attention" : "neutral";
  $("decision-title").textContent = audit.native ? readinessText(audit) : msg(status);
  $("decision-copy").textContent = audit.native ? audit.native.audit?.executive_summary || msg("preliminaryState") : msg(status + "Copy");
  $("decision-findings").replaceChildren(...priorities.map((row) => {
    const item = element("li");
    const action = button("decision-link", row.title, () => openReviewFinding(row.id));
    action.append(element("span", "", "↗"));
    item.append(action);
    return item;
  }));
}

function reportDigest(report) {
  return {
    run_type: report.mode, mode: reportMode(report), source_hash: report.check?.source_hash,
    profile_hash: report.profile.fingerprint, partial: report.partial,
    minimum_severity: report.check?.minimum_severity || "Low",
    completed_criteria: [...new Set([
      ...(report.check?.completed_criteria || []), ...(report.insights?.checked_criteria || []),
    ])],
    findings: comparableFindings(report).map(({ id, criterion_id, title, severity, evidence }) => ({
      id, criterion_id, title, severity, evidence: evidence.slice(0, 12).map(({ text, file_name, file_role }) => ({
        text: Array.from(text).slice(0, 600).join(""), file_name, file_role,
      })),
    })),
  };
}

function compareOptions() {
  const { audit, run, comparison, demo, demoVersion } = state;
  const rows = readRecentRuns().filter((row) => row.id !== run?.id && row.report.mode === audit.mode);
  if (audit.mode === "replay" && demo && demoVersion === "after") rows.unshift({ ...demo.before, id: "demo-before" });
  if (comparison?.baseline && !rows.some((row) => row.id === comparison.baseline.id)) rows.unshift(comparison.baseline);
  return rows;
}

function comparisonLabel(item) {
  return [item.title, msg(checkModeKey(reportMode(item.report))), displayDate(item.time)].filter(Boolean).join(" · ");
}

function renderCompareNotice() {
  const found = state.comparison?.result?.new;
  show($("comparison-notice"), Boolean(found?.length));
  if (found?.length) $("comparison-notice-text").textContent = msg("newFindingNotice", { n: found.length });
}

function renderComparison() {
  const rows = compareOptions();
  const selector = $("compare-baseline");
  const placeholder = element("option", "", msg("compareChoose"));
  placeholder.value = "";
  selector.replaceChildren(placeholder, ...rows.map((item) => {
    const option = element("option", "", comparisonLabel(item));
    option.value = item.id;
    return option;
  }));
  selector.disabled = !rows.length;
  selector.value = state.comparison?.baseline?.id || "";
  $("compare-current").textContent = comparisonLabel({ report: state.audit, title: $("result-title").textContent });
  $("compare-help").textContent = msg(rows.length ? "compareHelp" : "compareNoHistory");
  const target = $("comparison-results");
  const comparison = state.comparison;
  target.replaceChildren();
  if (!comparison) return;
  if (!comparison.result) {
    target.append(element("p", comparison.failed ? "warning-box" : "hint", msg(comparison.failed ? "compareFailed" : "compareLoading")));
    return;
  }
  const { result, baseline } = comparison;
  const before = new Map(comparableFindings(baseline.report).map((row) => [row.id, row]));
  const after = new Map(comparableFindings(state.audit).map((row) => [row.id, row]));
  if (state.audit.native && baseline.report.native) {
    target.append(element("p", "hint", `${readinessText(baseline.report)} → ${readinessText(state.audit)}`),
      element("p", "hint", `${msg("qualityEstimate")} · ${baseline.report.score ?? "—"} → ${state.audit.score ?? "—"}`));
    for (const {before: left, after: right} of result.unchanged) {
      if (before.get(left)?.severity !== after.get(right)?.severity) {
        target.append(element("p", "hint", `${after.get(right).title} · ${levelName(before.get(left).severity)} → ${levelName(after.get(right).severity)}`));
      }
    }
  }
  const sections = [
    ["compareNew", result.new.map((id) => after.get(id)), true],
    ["compareUnchanged", result.unchanged.map(({ after: id }) => after.get(id)), true],
    ["compareClosed", result.closed.map((id) => before.get(id)), false],
    ["compareUnassessed", result.unassessed.map((id) => before.get(id)), false],
  ];
  target.append(element("p", "hint", msg(result.kind === "same_source" ? "compareSameSource" : "compareConservative")));
  for (const [label, findings, current] of sections) {
    const card = element("details", "audit-details compare-group");
    card.open = findings.length > 0;
    card.append(element("summary", "", msg(label) + " · " + findings.length));
    for (const row of findings.filter(Boolean)) {
      const record = element("div", "compare-entry");
      record.append(element("span", "badge " + row.severity, levelName(row.severity)));
      if (current) record.append(button("link-btn", row.title, () => openReviewFinding(row.id)));
      else record.append(element("strong", "", row.title));
      const places = row.evidence.map((point) => lineLabel(point)).join(" · ");
      if (places) record.append(element("span", "hint", places));
      card.append(record);
    }
    target.append(card);
  }
}

async function compareWith(baseline) {
  state.comparison?.controller?.abort();
  if (!baseline) {
    state.comparison = null;
    renderCompareNotice();
    renderFindings();
    renderComparison();
    return;
  }
  const controller = new AbortController();
  const comparison = { baseline, controller };
  state.comparison = comparison;
  renderComparison();
  renderCompareNotice();
  if (reviewState.tab === "report") renderFindings();
  try {
    comparison.result = await fetchJSON("/api/audit/compare", {
      method: "POST", signal: controller.signal,
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({
        before: reportDigest(baseline.report), after: reportDigest(state.audit), after_sources: reportSources(state.audit),
      }),
    });
  } catch (_) {
    comparison.failed = true;
  }
  if (state.comparison !== comparison) return;
  renderComparison();
  renderCompareNotice();
  if (reviewState.tab === "report") renderFindings();
  if (reviewState.tab === "scenarios") renderScenarios();
}

function bindAuditExtras() {
  $("check-mode").addEventListener("change", (event) => {
    state.checkMode = event.target.value;
    window.dispatchEvent(new CustomEvent("agentscene", { detail: { type: "mode", mode: state.checkMode } }));
  });
  $("deepen-btn").addEventListener("click", deepenReport);
  $("copy-questions").addEventListener("click", () => copyText(customerQuestionText()));
  $("download-questions").addEventListener("click", () =>
    download(customerQuestionText(), "text/plain;charset=utf-8", "txt", "requiragents-questions"));
  $("compare-baseline").addEventListener("change", (event) =>
    compareWith(compareOptions().find((item) => item.id === event.target.value)));
  $("comparison-open").addEventListener("click", () => {
    openReviewTab("compare");
    $("review-compare-tab").focus();
  });
  $("sanity-open").addEventListener("click", () => {
    openReviewTab("scenarios");
    $("review-scenarios-tab").focus();
  });
}
