"use strict";

const $ = (id) => document.getElementById(id);
const PAGE = document.body.dataset.page;
const AUDIT_PAGE = PAGE === "audit";
const PAGE_TITLES = { home: "homePageTitle", audit: "pageTitle", how: "howPageTitle" };
const SEVERITIES = ["Critical", "High", "Medium", "Low"];
const STATUS = {
  critical: ["bad", "critical"],
  review: ["mid", "reviewStatus"],
  clear: ["good", "clearStatus"],
  incomplete: ["mid", "incomplete"],
};
const DRAFT_KEY = "fintech_auditor_draft_v2";
const DEFAULT_LIMITS = { min_spec_chars: 40, max_spec_chars: 60000, max_context_chars: 5000, max_upload_mb: 10, max_files: 5 };
const state = {
  language: stored("localStorage", "auditor_language") || "ru",
  config: null,
  password: stored("sessionStorage", "demo_password") || "",
  activeTab: "file-tab",
  files: [],
  checkMode: "deep",
  running: false,
  audit: null,
  auditorRetry: null,
  snapshot: null,
  demo: null,
  run: null,
  comparison: null,
  exampleLoaded: false,
  openDemoOnLoad: AUDIT_PAGE && new URLSearchParams(location.search).get("demo") === "1",
  agent: "all",
  severity: new Set(SEVERITIES),
};

function stored(storage, key) {
  try { return window[storage].getItem(key); } catch (_) { return null; }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, label, action) {
  const node = element("button", className, label);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

// Мост к фоновой 3D-сцене (static/agent-scene): сцена только слушает эти события.
const sceneEvent = (detail) => window.dispatchEvent(new CustomEvent("agentscene", { detail }));

function msg(key, values = {}, language = state.language) {
  const index = { ru: 0, kk: 1, en: 2 }[language] ?? 0;
  return UI_TEXT[key][index].replace(/\{(\w+)\}/g, (placeholder, name) => values[name] ?? placeholder);
}

function applyLocale() {
  const { language } = state;
  document.documentElement.lang = language;
  document.title = msg(PAGE_TITLES[PAGE]);
  document.querySelector('meta[name="description"]').content = msg(PAGE === "how" ? "guideLead" : "pageDescription");
  document.querySelectorAll("[data-msg]").forEach((node) => { node.textContent = msg(node.dataset.msg); });
  document.querySelectorAll("[data-label]").forEach((node) => node.setAttribute("aria-label", msg(node.dataset.label)));
  document.querySelectorAll("[data-placeholder]").forEach((node) => { node.placeholder = msg(node.dataset.placeholder); });
  document.querySelectorAll("[data-language]").forEach((node) => { node.value = language; });
}

function show(node, visible, text) {
  if (text !== undefined) node.textContent = text;
  node.classList.toggle("hidden", !visible);
}

const number = (value) => value.toLocaleString(LOCALES[state.language]);
const limits = () => ({ ...DEFAULT_LIMITS, ...state.config?.limits });
const context = () => $("extra-context").value.trim();
const specText = () => $("spec-text").value.trim();
const levelName = (severity, language = state.language) => msg(severity, {}, language);
const sortedBySeverity = (a, b) => SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity);
function lineLabel(evidence, language = state.language) {
  const start = evidence.file_line_start ?? evidence.line_start;
  const end = evidence.file_line_end ?? evidence.line_end;
  const lines = evidence.page != null ? msg("pageAnchor", {n: evidence.page}, language)
    : evidence.paragraph != null && evidence.file_name?.toLowerCase().endsWith(".docx")
      ? msg("paragraphAnchor", {n: evidence.paragraph}, language)
      : start == null ? "—" : start === end ? msg("line", {n: start}, language) : msg("lines", {start, end}, language);
  return evidence.file_name ? `${evidence.file_name} · ${lines}` : lines;
}

let toastTimer;
function toast(message) {
  show($("toast"), true, message);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => show($("toast"), false), 2500);
}

function error(message = "") {
  if (AUDIT_PAGE) show($("form-error"), Boolean(message), message);
}

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = element("textarea");
      input.className = "visually-hidden";
      input.value = text;
      const focused = document.activeElement;
      document.body.append(input);
      input.select();
      const copied = document.execCommand("copy");
      input.remove();
      focused?.focus();
      if (!copied) throw new Error();
    }
    toast(msg("copied"));
  } catch (_) {
    toast(msg("cannotCopy"));
  }
}

function download(content, type, extension, filename = state.audit.report_format.filename) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = element("a");
  link.href = url;
  link.download = `${filename}.${extension}`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

let draftTimer;
function saveDraft() {
  if (!$("remember-draft").checked) return;
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify({ remember: true, text: $("spec-text").value, context: $("extra-context").value }));
  } catch (_) { /* Хранилище может быть недоступно. */ }
}

function loadDraft() {
  try { return JSON.parse(stored("localStorage", DRAFT_KEY) || "null"); } catch (_) { return null; }
}

function updateStaleNotice() {
  const snapshot = state.snapshot;
  const changed = snapshot && ["text", "file"].includes(snapshot.kind) && (
    context() !== snapshot.context ||
    (snapshot.kind === "text" && (state.activeTab !== "text-tab" || specText() !== snapshot.text)) ||
    (snapshot.kind === "file" && (state.activeTab !== "file-tab" || state.files !== snapshot.files))
  );
  show($("stale-report"), Boolean(changed), msg("stale"));
}

function updateInputs() {
  const lim = limits();
  for (const [id, length, max] of [
    ["char-counter", [...specText()].length, lim.max_spec_chars],
    ["context-counter", [...context()].length, lim.max_context_chars],
  ]) {
    $(id).textContent = msg("counts", { length: number(length), max: number(max) });
    $(id).classList.toggle("over", length > max);
  }
  clearTimeout(draftTimer);
  if ($("remember-draft").checked) draftTimer = setTimeout(saveDraft, 350);
  updateStaleNotice();
}

const tabs = [...document.querySelectorAll("[data-tab]")];
function switchTab(id, focus = false) {
  if (state.activeTab !== id) $("audit-flow").dataset.inputDirection = id === "file-tab" ? "return" : "advance";
  state.activeTab = id;
  show($("remember-row"), id === "text-tab");
  tabs.forEach((tab) => {
    const active = tab.dataset.tab === id;
    tab.setAttribute("aria-selected", active);
    tab.tabIndex = active ? 0 : -1;
    tab.classList.toggle("active", active);
    $(tab.dataset.tab).classList.toggle("active", active);
    if (active && focus) tab.focus();
  });
  updateStaleNotice();
}

function confirmReplace(preset) {
  if (!specText() || specText() === preset.text) return true;
  const now = Date.now();
  if (state.replace?.preset === preset && now - state.replace.at < 3000) {
    state.replace = null;
    return true;
  }
  state.replace = { preset, at: now };
  toast(msg("replace"));
  return false;
}

function renderGuideProfile(config) {
  const icons = { pay: "₸", kyc: "✓", sec: "◇", logic: "≡" };
  $("agents").replaceChildren(...config.agents.map((agent) => {
    const card = element("article", `fcard ${agent.style}`);
    const icon = element("span", "ficon", icons[agent.style] || "✓");
    icon.setAttribute("aria-hidden", "true");
    const text = element("span", "ftext");
    text.append(element("strong", "", agent.title), element("span", "", agent.scope));
    card.append(icon, text);
    return card;
  }));
}

function renderAuditProfile(config) {
  $("preset-bar").replaceChildren(...config.examples.map((example) => button("chip", example.label, () => {
    if (!confirmReplace(example)) return;
    $("spec-text").value = example.text;
    updateInputs();
    $("spec-text").focus();
  })));
  $("file-limit-hint").textContent = msg("packageLimit", { limit: limits().max_upload_mb, n: limits().max_files });
  renderFiles();
  renderModeChoices();
  updateInputs();
}

function renderProfile(config) {
  $("status-badge").classList.remove("ok", "warn", "local");
  $("status-badge").classList.add(config.api_key_configured ? "ok" : "local");
  $("status-text").textContent = msg(config.api_key_configured ? "available" : "ruleReview");
  if (AUDIT_PAGE) {
    show($("demo-btn"), config.demo_available);
    renderAuditProfile(config);
  } else if (PAGE === "home") {
    show($("demo-link"), config.demo_available);
    loadDashboardExample(config);
  } else {
    renderGuideProfile(config);
  }
}

function authHeaders() {
  const headers = { "Accept-Language": state.language };
  if (state.password) headers["X-Demo-Password"] = encodeURIComponent(state.password);
  return headers;
}

function authVisible(visible) {
  show($("auth-overlay"), visible);
  $("workspace").inert = visible;
  if (visible) $("password-input").focus();
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json();
}

async function responseError(response) {
  if (response.status === 401) {
    state.password = "";
    try { sessionStorage.removeItem("demo_password"); } catch (_) { /* Необязательное хранилище. */ }
    authVisible(true);
    return msg("badPassword");
  }
  if (response.status === 503) return msg(state.config?.demo_available ? "unavailableHint" : "unavailable");
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch (_) { /* Ответ мог оборваться. */ }
  return msg("reviewFailed");
}

async function init() {
  if (PAGE === "home") {
    const redirectOldSection = () => {
      const route = { "#audit": "/audit", "#how": "/how" }[location.hash];
      if (route) location.replace(route);
      return Boolean(route);
    };
    if (redirectOldSection()) return;
    window.addEventListener("hashchange", redirectOldSection);
  }
  document.querySelector(`[data-nav="${PAGE}"]`)?.setAttribute("aria-current", "page");
  if (!Object.hasOwn(LOCALES, state.language)) state.language = "ru";
  applyLocale();
  if (PAGE === "home") {
    initHeroScene();
    renderDashboard();
  }
  if (!AUDIT_PAGE) {
    await refreshConfig();
    return;
  }
  const draft = loadDraft();
  if (typeof draft?.text === "string") $("spec-text").value = draft.text;
  if (typeof draft?.context === "string") $("extra-context").value = draft.context;
  $("remember-draft").checked = draft?.remember === true;
  if (draft?.remember) switchTab("text-tab");
  $("extra-box").open = Boolean(context());
  await refreshConfig();
  updateInputs();
  await openRequestedReport();
}

async function refreshConfig({ fillDefault = false } = {}) {
  const { language } = state;
  $("status-text").textContent = msg("connecting");
  const originalText = AUDIT_PAGE ? specText() : "";
  const sampleIndex = state.config?.examples.findIndex((example) => example.text === originalText) ?? -1;
  try {
    const config = await fetchJSON(`/api/config?language=${language}`);
    if (state.language !== language) return false;
    state.config = config;
    if (AUDIT_PAGE && specText() === originalText) {
      const sample = config.examples[sampleIndex] || (fillDefault && !originalText ? config.examples[0] : null);
      if (sample) $("spec-text").value = sample.text;
    }
    renderProfile(config);
    if (AUDIT_PAGE && config.auth_required && !state.password && $("auth-overlay").classList.contains("hidden")) {
      authVisible(true);
    }
    return true;
  } catch (_) {
    if (state.language !== language) return false;
    $("status-text").textContent = msg("unavailable");
    $("status-badge").classList.add("warn");
    error(msg("noConnection"));
    return false;
  }
}

async function setLanguage(language) {
  if (state.running || state.language === language || !Object.hasOwn(LOCALES, language)) return;
  state.language = language;
  try { localStorage.setItem("auditor_language", language); } catch (_) { /* Необязательное хранилище. */ }
  applyLocale();
  if (AUDIT_PAGE) {
    error();
    show($("auth-error"), false);
    updateInputs();
    renderFiles();
    renderModeChoices();
    if (state.audit) renderReport();
  }
  const ready = await refreshConfig();
  if (!AUDIT_PAGE || !ready || state.language !== language) return;
  if (state.snapshot?.kind === "demo" && state.config.demo_available) {
    await runDemo(state.demoVersion, false, reportMode(state.audit));
  } else {
    await openRequestedReport();
  }
}

async function submitAuth(event) {
  event.preventDefault();
  const password = $("password-input").value;
  show($("auth-error"), false);
  try {
    await fetchJSON("/api/auth", { method: "POST", headers: { "X-Demo-Password": encodeURIComponent(password) } });
    state.password = password;
    try { sessionStorage.setItem("demo_password", password); } catch (_) { /* Необязательное хранилище. */ }
    $("password-input").value = "";
    authVisible(false);
    $("run-btn").focus();
    await openRequestedReport();
  } catch (failure) {
    show($("auth-error"), true, failure.message);
  }
}

async function openRequestedReport() {
  if (!$("auth-overlay").classList.contains("hidden") || state.audit) return;
  const id = new URLSearchParams(location.search).get("run");
  if (id) {
    const run = readRecentRuns().find((item) => item.id === id);
    if (!run) return error(msg("reportUnavailable"));
    state.demoVersion = run.demoVersion;
    setReport(run.report, { kind: "saved", title: run.title }, run);
    focusRequestedFinding();
  } else if (new URLSearchParams(location.search).get("example") === "refund" && !state.exampleLoaded) {
    await loadRefundExample();
  } else if (state.openDemoOnLoad && state.config?.demo_available) {
    await runDemo();
    focusRequestedFinding();
  }
}

function focusRequestedFinding() {
  const id = location.hash.slice(1);
  if (state.audit?.modules.some((module) => module.defects.some((defect) => defect.id === id))) openFinding(id);
  if (id === "fixes") openReviewTab("fixes");
}

function makeRequest(snapshot = null, mode = state.checkMode) {
  const lim = limits();
  if (!snapshot) {
    snapshot = state.activeTab === "text-tab" ? { kind: "text", text: specText() }
      : { kind: "file", files: state.files, title: state.files[0]?.file.name };
    snapshot.context = context();
  }
  if ([...snapshot.context].length > lim.max_context_chars) throw new Error(msg("shortenContext"));
  const form = new FormData();
  form.set("language", state.language);
  form.set("consent", Boolean($("gemini-consent")?.checked));
  form.set("cache", Boolean($("cache-consent")?.checked && !snapshot.context.trim()));
  form.set("mode", mode);
  form.set("extra_context", snapshot.context);
  if (snapshot.kind === "text") {
    const length = [...snapshot.text].length;
    if (length < lim.min_spec_chars) throw new Error(msg("tooShort", { limit: lim.min_spec_chars }));
    if (length > lim.max_spec_chars) throw new Error(msg("textTooLong"));
    form.set("text", snapshot.text);
  } else if (snapshot.kind === "package") {
    form.set("package", JSON.stringify(snapshot.documents));
  } else {
    if (!snapshot.files.length) throw new Error(msg("chooseFile"));
    for (const { file, role } of snapshot.files) {
      form.append("files", file);
      form.append("roles", role);
    }
  }
  return { form, snapshot };
}

let clock;
const WORKFLOW_STEPS = ["readDocument", "findRequirements", "checkQuality", "verifyResults", "assembleReport"];

function setWorkflowStep(index, failed = false) {
  $("progress-title").textContent = msg(WORKFLOW_STEPS[index]);
  $("workflow-steps").replaceChildren(...WORKFLOW_STEPS.map((key, step) => {
    const phase = element("li", step < index ? "done" : step === index ? (failed ? "failed" : "current") : "");
    const status = step < index ? "ready" : step === index ? (failed ? "failed" : "running") : "waitStage";
    if (step === index) phase.setAttribute("aria-current", "step");
    phase.append(element("span", "phase-indicator", step < index ? "✓" : ""),
      element("span", "", msg(key)), element("span", "hint", msg(status)));
    return phase;
  }));
}

function renderDocumentSummary(id, summary) {
  const section = $(id);
  const metrics = element("div", "document-facts");
  for (const [key, value] of [
    ["fileFormat", summary.format === "PACKAGE" ? msg("bundleFormat") : summary.format],
    ["requirementCount", summary.requirements], ["sectionsCount", summary.section_count],
  ]) {
    const item = element("span");
    item.append(element("span", "hint", msg(key)), element("strong", "", value));
    metrics.append(item);
  }
  section.replaceChildren(element("h3", "", msg("parsedDocument")), metrics);
  if (summary.files) section.append(sourceList(summary.files));
  if (summary.sections.length) section.append(element("p", "hint", summary.sections.join(" · ")));
  if (summary.warnings.length) section.append(element("p", "hint", summary.warnings.join(" ")));
  show(section, true);
}

function renderFlow() {
  const flow = $("audit-flow");
  const stage = state.running ? "working" : state.audit ? "report" : "upload";
  if (stage !== flow.dataset.stage) {
    flow.dataset.stage = stage;
    flow.dataset.stageMotion = stage;
  }
  show($("new-audit"), Boolean(state.audit) && !state.running);
}

function busy(value, label) {
  state.running = value;
  renderFlow();
  $("run-btn").disabled = value;
  $("demo-btn").disabled = value;
  document.querySelectorAll("[data-language]").forEach((node) => { node.disabled = value; });
  show($("progress"), value);
  show($("cancel-btn"), value);
  show($("results"), !value && Boolean(state.audit));
  clearInterval(clock);
  if (value) {
    $("progress-title").textContent = label;
    const start = performance.now();
    const tick = () => { $("progress-timer").textContent = msg("seconds", { time: Math.floor((performance.now() - start) / 1000) }); };
    tick();
    clock = setInterval(tick, 500);
  }
}

function renderProgress(agents) {
  const list = $("agent-list");
  list.replaceChildren(...agents.map((agent) => {
    const item = element("li", "agent-item");
    item.dataset.key = agent.key;
    item.append(element("span", "", agent.title), element("span", "agent-state", msg("waitStage")));
    return item;
  }));
  $("progress-bar").max = agents.length || 1;
  if (agents.length) $("progress-bar").value = 0;
  else $("progress-bar").removeAttribute("value");
  show($("progress-details"), Boolean(agents.length));
}

function auditorFailureKey(code) {
  if (code === "auditor_budget_exhausted") return "auditBudgetExhausted";
  if (code === "source_coverage_incomplete") return "coverageLimited";
  if (code === "consistency_incomplete") return "consistencyIncomplete";
  return code === "auditor_validation_failed" ? "auditorValidationFailed" : "auditorProviderFailed";
}

function handleEvent(event) {
  if (event.type === "document_ready") {
    renderDocumentSummary("live-preview", event.source);
    sceneEvent({ type: "document_ready" });
  } else if (event.type === "start") {
    renderProgress(event.agents);
    sceneEvent({ type: "agents", agents: (event.agents || []).map(({ key, title }) => ({ key, title })) });
  } else if (event.type === "agent_start") {
    const item = [...$("agent-list").children].find((row) => row.dataset.key === event.key);
    if (item) {
      item.className = "agent-item running";
      item.lastChild.textContent = msg("running");
    }
    setWorkflowStep(event.key === "payments" ? 1 : event.key === "logic" ? 3 : 2);
    sceneEvent({ type: "agent_start", key: event.key });
  } else if (event.type === "agent_retry") {
    sceneEvent({ type: "agent_retry", key: event.key });
    const item = [...$("agent-list").children].find((row) => row.dataset.key === event.key);
    if (item) item.lastChild.textContent = msg("agentRetry");
  } else if (event.type === "agent_done" || event.type === "agent_reused") {
    sceneEvent({ type: "agent_done", key: event.key, ok: event.ok, code: event.code, reused: event.type === "agent_reused" });
    const item = [...$("agent-list").children].find((row) => row.dataset.key === event.key);
    if (item) {
      item.className = `agent-item ${event.ok ? "done" : "failed"}`;
      item.lastChild.textContent = msg(event.type === "agent_reused" ? "agentRestored"
        : event.ok ? "ready" : event.code ? auditorFailureKey(event.code) : "failed");
      if (event.ok) $("progress-bar").value += 1;
    }
  } else if (event.type === "validating") {
    setWorkflowStep(3);
    sceneEvent({ type: "validating" });
  } else if (event.type === "result") {
    if (event.auditor_failure) {
      setWorkflowStep(3, true);
      sceneEvent({ type: "partial_result", key: "logic", code: event.auditor_failure });
    } else {
      setWorkflowStep(4);
      sceneEvent({ type: "result" });
    }
    return event;
  } else if (event.type === "error") {
    throw new Error(event.detail || msg("reviewFailed"));
  }
  return null;
}

async function readReport(response) {
  let result = null;
  let buffer = "";
  const useLine = (line) => {
    if (!line.trim()) return;
    let event;
    try { event = JSON.parse(line); } catch (_) {
      throw new Error(msg("incompleteReport"));
    }
    result = handleEvent(event) || result;
  };
  if (!response.body?.getReader) {
    (await response.text()).split("\n").forEach(useLine);
  } else {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();
        lines.forEach(useLine);
      }
      useLine(buffer + decoder.decode());
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  if (!result) throw new Error(msg("connectionLost"));
  return result;
}

async function runAudit(snapshot = null, mode = state.checkMode, baseline = null, retry = null) {
  if (state.running || !$("auth-overlay").classList.contains("hidden")) return;
  error();
  let request;
  try {
    request = makeRequest(snapshot, mode);
    if (retry?.token) {
      request.form.set("retry_token", retry.token);
      request.form.set("cache", "false");
    } else if (retry?.cache) {
      if (!$("cache-consent").checked) throw new Error(msg("cacheOptIn"));
      request.form.set("cache", "true");
    }
  } catch (failure) {
    error(failure.message);
    return;
  }

  state.abort = new AbortController();
  show($("live-preview"), false);
  show($("workflow-steps"), true);
  renderProgress([]);
  setWorkflowStep(0);
  busy(true, msg("readDocument"));
  let report;
  try {
    const rules = state.config?.api_key_configured === false || baseline?.report.mode === "rules";
    if (!rules && !$("gemini-consent").checked) throw new Error(msg("consentGemini"));
    if (!rules) sceneEvent({ type: "start", mode });
    const options = {
      method: "POST", headers: authHeaders(), body: request.form, signal: state.abort.signal,
    };
    const response = await fetch(rules ? "/api/audit/rules" : "/api/audit/stream", options);
    if (!response.ok) throw new Error(await responseError(response));
    report = rules ? await response.json() : await readReport(response);
    const retryInfo = report.auditor_retry;
    delete report.auditor_retry;
    setReport(report, request.snapshot);
    state.auditorRetry = retryInfo || null;
    show($("retry-auditor-btn"), Boolean(state.auditorRetry));
  } catch (failure) {
    error(failure.name === "AbortError" ? msg("cancelled") : (failure.message || msg("reviewFailed")));
  } finally {
    state.abort = null;
    busy(false);
    if (!report?.auditor_failure) sceneEvent(report ? { type: "done", ok: true } : { type: "error" });
  }
  if (report) $("output-title").scrollIntoView({ block: "start" });
  if (report && baseline) await compareWith(baseline);
}

async function runDemo(version = "before", scroll = true, mode = state.checkMode, baseline = null) {
  if (state.running || !$("auth-overlay").classList.contains("hidden")) return;
  error();
  state.abort = new AbortController();
  show($("live-preview"), false);
  show($("workflow-steps"), false);
  renderProgress([]);
  busy(true, msg("openingExample"));
  let loaded = false;
  try {
    state.demo = await fetchJSON(`/api/demo?language=${state.language}&mode=${mode}`, { headers: authHeaders(), signal: state.abort.signal });
    showDemoVersion(version, false);
    state.openDemoOnLoad = false;
    loaded = true;
  } catch (failure) {
    if (failure.name !== "AbortError") error(failure.message || msg("exampleFailed"));
  } finally {
    state.abort = null;
    busy(false);

  }
  if (scroll && state.audit?.mode === "replay") $("output-title").scrollIntoView({ block: "start" });
  if (loaded) await compareWith(baseline || (version === "after" ? { ...state.demo.before, id: "demo-before" } : null));
}

function showDemoVersion(version, autoCompare = true) {
  if (!state.demo) return;
  state.demoVersion = version;
  const data = state.demo[version];
  $("demo-text").textContent = data.text;
  $("demo-source").open = false;
  for (const id of ["before", "after"]) {
    const score = state.demo[id].report.score;
    $(`demo-${id}`).textContent = `${msg(id)} · ${score ?? "—"}/100`;
    $(`demo-${id}`).setAttribute("aria-pressed", id === version);
  }
  setReport(data.report, { kind: "demo", text: data.text, context: data.context, title: data.title });
  if (autoCompare && version === "after") compareWith({ ...state.demo.before, id: "demo-before" });
}

function setReport(audit, snapshot, saved = null) {
  state.comparison?.controller?.abort();
  state.comparison = null;
  state.audit = audit;
  if (audit.mode !== "live" || saved) sceneEvent({ type: "center_reset" });
  state.auditorRetry = null;
  state.snapshot = snapshot;
  state.run = saved || (audit.auditor_failure ? null : registerRun(audit, snapshot));
  state.agent = "all";
  state.severity = new Set(SEVERITIES);
  resetReviewTools();
  renderReport();
}

function renderReport() {
  const { audit, snapshot } = state;
  show($("results"), true);
  renderFlow();
  show($("demo-comparison"), snapshot.kind === "demo");
  show($("report-language"), audit.language !== state.language);
  updateStaleNotice();
  $("result-title").textContent = snapshot.title || audit.report_format.title;
  const [tone, status] = STATUS[audit.score_status];
  const quick = reportMode(audit) === "quick";
  const local = audit.mode === "rules";
  const extraRisks = local ? 0 : audit.insights?.sanity.length || 0;
  $("score-label").textContent = msg(audit.native ? "qualityEstimate" : quick ? "quickScore" : extraRisks ? "criteriaScore" : "score");
  $("score-card").className = `metric-card score ${extraRisks && tone === "good" ? "mid" : tone}`;
  const counts = auditCounts(audit);
  $("score-val").textContent = counts.score;
  $("score-status").textContent = msg(local ? "ruleScoreNote" : quick && status === "clearStatus" ? "noQuickCritical" : extraRisks && status === "clearStatus" ? "reviewStatus" : status);
  $("high-val").textContent = counts.high;
  $("critical-val").textContent = audit.counts?.Critical || 0;
  $("group-count").textContent = audit.grouped_issue_count === undefined ? "" :
    msg("groupCounts", { raw: audit.raw_finding_count, groups: audit.grouped_issue_count });
  $("total-val").textContent = counts.findings;
  $("requirements-val").textContent = counts.requirements;
  const totalFiles = audit.document.files?.length;
  const source = totalFiles > 1 ? msg("sourcePackage", { n: totalFiles })
    : audit.document.files?.[0]?.name ? msg("sourceFile", { name: audit.document.files[0].name })
    : msg(snapshot.kind === "demo" ? "sourceDemo" : snapshot.kind === "saved" ? "savedReport" : "sourceText");
  const { agents_completed: done, agents_total: total } = audit.coverage;
  $("result-meta").textContent = [
    source, msg(checkModeKey(reportMode(audit))),
    local ? msg("ruleReview") : audit.native?.passport.run_kind === "cache_hit" ? msg("cachedRun") : msg("completed", { done, total }),
    audit.native && (quick || audit.auditor_failure && !(audit.finding_audit_available || audit.finding_audit_completed)) ? msg("quickEstimateNotice") : "",
    msg("characters", { n: number(audit.spec_chars) }),
  ].filter(Boolean).join(" · ");
  show($("quick-followup"), quick);
  show($("deepen-btn"), quick && canDeepen());
  show($("sanity-notice"), extraRisks > 0);
  if (extraRisks) $("sanity-notice-text").textContent = msg("sanityNotice", { n: extraRisks });

  const messages = [...audit.source_warnings];
  if (audit.auditor_failure) {
    const phase = { deduplication: "auditDeduplicationPending", post_review: "auditFinalReviewPending",
      summary: "auditSummaryPending" }[audit.audit_coverage?.remaining_phase];
    messages.push(msg(phase || auditorFailureKey(audit.auditor_failure)));
    if (audit.consistency_coverage_complete === false && audit.auditor_failure !== "consistency_incomplete") {
      messages.push(msg("consistencyIncomplete"));
    }
    if (audit.finding_audit_available || audit.finding_audit_completed) {
      messages.push(msg("findingAuditCounts", {reviewed: audit.audited_finding_count || 0, pending: audit.preliminary_count || 0}));
    }
  } else if (audit.failed_agents.length) {
    const failed = audit.modules.filter((module) => !module.ok).map((module) => module.title).join(", ");
    messages.push(done ? msg("failedModules", { modules: failed }) : msg("reviewFailed"));
  }
  if (audit.pending_findings) messages.push(msg("notCounted", { n: audit.pending_findings }));
  show($("partial-warning"), messages.length > 0, messages.join(" "));
  show($("retry-auditor-btn"), Boolean(state.auditorRetry));
  show($("check-details"), audit.report_format.show_coverage);
  show($("export-btn"), audit.report_format.exports.includes("markdown"));
  show($("export-json-btn"), audit.report_format.exports.includes("json"));
  show($("print-report-btn"), audit.report_format.exports.includes("pdf"));
  $("check-details").open = false;
  renderDetails();
  renderNativeTrace(audit);
  renderReleaseDecision();
  renderRoleSummaries();
  renderCompareNotice();
  renderFindings();
  renderReviewTools();
}

function readinessText(report) {
  if (report.auditor_failure) return msg("auditPartialStatus");
  if (report.native && report.partial) return msg("coverageLimited");
  const keys = {ready: "readyState", conditionally_ready: "conditionalState", not_ready: "blockedState"};
  return report.native ? msg(keys[report.readiness] || "preliminaryState") : "—";
}

function renderNativeTrace(audit) {
  show($("native-status"), Boolean(audit.native),
    `${readinessText(audit)} · ${audit.elapsed_sec} s · ${({new: msg("runNew"), resume: msg("runResume"), cache_hit: msg("cachedRun")}[audit.native?.passport.run_kind] || "")}`);
  show($("executive-pdf-btn"), Boolean(audit.mode === "replay" || audit.native && (audit.independent_audit || audit.finding_audit_available || audit.finding_audit_completed)));
  show($("native-pdf-btn"), Boolean(audit.native || audit.mode === "replay"));
  const decisionKeys = {confirmed: "auditConfirmed", revised: "auditRevised", merged: "auditMerged", rejected: "auditRejected"};
  const decisions = audit.native?.audit?.decisions || audit.native?.finding_audit?.decisions || [];
  show($("native-audit"), decisions.length > 0);
  $("native-audit-content").replaceChildren(...decisions.map((decision) => {
    const row = element("li", "document-summary");
    const status = decision.assessment_kind === "question" ? msg("audit_question")
      : decision.assessment_kind === "potential_risk" ? msg("potential_risk")
      : decision.finding_id.startsWith("REVIEW-") ? msg("human_review") : msg(decisionKeys[decision.decision]);
    row.append(element("strong", "", `${decision.finding_id} · ${status} · ${decision.title}`),
      element("p", "", decision.audit_reason),
      element("p", "hint", [...decision.requirement_ids, decision.merged_into].filter(Boolean).join(" · ")));
    return row;
  }));
  show($("native-graph"), Boolean(audit.knowledge_graph));
  const panel = $("native-graph-content");
  panel.replaceChildren();
  if (!audit.knowledge_graph) return;

  const {nodes, edges} = audit.knowledge_graph;
  const details = element("div", "document-summary");
  const list = element("div", "trace-node-list");
  const labels = new Map(nodes.map((node) => [node.id, node.label]));
  const describeNode = (node) => {
      const items = edges.filter((edge) => edge.source === node.id || edge.target === node.id);
      details.replaceChildren(element("h4", "", node.title || node.label),
        element("p", "", node.text || node.explanation || ""),
        element("p", "hint", [node.source, node.page, node.paragraph].filter(Boolean).join(" · ")));
      for (const edge of items) {
        const related = edge.source === node.id ? edge.target : edge.source;
        details.append(element("h5", "", `${edge.finding_id} · ${labels.get(related)} · ${edge.title}`),
          element("p", "", edge.explanation));
        for (const proof of edge.evidence) {
          details.append(element("blockquote", "", proof.quote),
            element("p", "hint", [proof.source, proof.page, proof.paragraph].filter(Boolean).join(" · ")));
        }
      }
  };
  for (const node of nodes) {
    list.append(button("btn-secondary", node.finding_id || node.label, () => describeNode(node)));
  }
  if (nodes.length > 1 && nodes.length <= 40) {
    const svgElement = (tag, attributes) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      return node;
    };
    const drawing = svgElement("svg", {viewBox: "0 0 600 340", "aria-hidden": "true", class: "trace-graph"});
    const coordinates = new Map(nodes.map((node, index) => {
      const angle = index / nodes.length * 2 * Math.PI - Math.PI / 2;
      return [node.id, [300 + 255 * Math.cos(angle), 170 + 139 * Math.sin(angle)]];
    }));
    for (const edge of edges) {
      const [x1, y1] = coordinates.get(edge.source);
      const [x2, y2] = coordinates.get(edge.target);
      drawing.append(svgElement("line", {x1, y1, x2, y2, stroke: "#859b96"}));
    }
    for (const node of nodes) {
      const [x, y] = coordinates.get(node.id);
      const marker = svgElement("g", {class: "trace-graph-marker"});
      marker.append(svgElement("circle", {cx: x, cy: y, r: 8, fill: node.kind === "finding" ? "#c04a38" : "#16836c"}));
      const label = svgElement("text", {x, y: y + 19, "text-anchor": "middle"});
      label.textContent = (node.finding_id || node.label).slice(0, 22);
      marker.append(label);
      marker.addEventListener("click", () => describeNode(node));
      drawing.append(marker);
    }
    panel.append(drawing);
  }
  panel.append(list, details);
}

function scoreExplanation(audit, language = state.language) {
  if (audit.auditor_failure) return msg("auditPartialStatus", {}, language);
  if (audit.native) return msg("qualityFormula", {}, language);
  if (audit.mode === "rules") return msg("ruleScoreExplanation", {}, language);
  const weights = SEVERITIES.map((severity) =>
    `${levelName(severity, language)}: ${audit.scored_counts[severity] ?? 0} × ${audit.score_weights[severity] ?? 0}`).join(" · ");
  if (audit.score === null) return msg("scoreIncomplete", {}, language);
  return `${msg("scoreFormula", {}, language)} ${weights}. ${msg("penalty", { n: audit.score_penalty }, language)}`;
}

function renderDetails() {
  const { audit } = state;
  const { profile, coverage } = audit;
  const details = $("check-content");
  const sections = element("ul", "coverage-list");
  for (const module of audit.modules) {
    const row = element("li");
    const moduleDetails = element("details");
    const status = module.ok ? msg("doneRules", { n: module.criteria.length }) : msg("failed");
    moduleDetails.append(element("summary", "", `${module.title} · ${status}`));
    if (module.ok) {
      const rules = element("ul", "rule-list");
      for (const criterion of module.criteria) rules.append(element("li", "", `${criterion.title} · ${criterion.id}`));
      moduleDetails.append(rules);
    } else {
      moduleDetails.append(element("p", "muted", audit.auditor_failure && module.key === "logic"
        ? msg(auditorFailureKey(audit.auditor_failure)) : module.error));
    }
    row.append(moduleDetails);
    sections.append(row);
  }
  const info = `${profile.title} · v${profile.version} · ${profile.fingerprint.slice(0, 10)}`;
  details.replaceChildren(
    element("p", "", info),
    element("p", "", msg("countRules", {
      done: coverage.criteria_in_completed_agents, total: coverage.criteria_total, merged: audit.merged_duplicates,
    })),
    element("p", "", scoreExplanation(audit)),
    element("p", "hint", audit.mode === "rules" ? msg("localProcessing") : audit.mode === "replay" ? msg("savedResponses")
      : msg("modelTime", { model: audit.model, time: audit.elapsed_sec })),
    sections,
  );
  if (audit.document.files?.some((file) => file.name)) details.append(sourceList(audit.document.files));
}

function sourceList(files) {
  const list = element("ul", "rule-list file-source-list");
  for (const file of files) list.append(element("li", "", [file.name, msg("role_" + file.role)].filter(Boolean).join(" · ")));
  return list;
}

function findings(all = false) {
  const modules = all || state.agent === "all" ? state.audit.modules : state.audit.modules.filter((item) => item.key === state.agent);
  return modules.flatMap((module) => module.defects.map((defect) => ({
    ...defect, module: module.title, agent: module.key,
  })));
}

function groups(rows, language = state.language) {
  const bySeverity = state.audit.report_format.group_by === "severity";
  const levels = SEVERITIES.map((key) => ({ key, title: levelName(key, language) }));
  const units = bySeverity ? levels : state.audit.modules;
  return units.map((unit) => ({
    title: unit.title,
    rows: rows.filter((row) => (bySeverity ? row.severity : row.agent) === unit.key).sort(sortedBySeverity),
  })).filter((group) => group.rows.length);
}

function renderFilters(rows) {
  const modules = [{ key: "all", title: msg("allChecks"), ok: true }, ...state.audit.modules];
  $("module-tabs").replaceChildren(...modules.map((module) => {
    const node = button(`mod-tab${module.key === state.agent ? " active" : ""}${module.ok ? "" : " has-error"}`,
      module.title, () => { state.agent = module.key; renderFindings(); });
    node.setAttribute("aria-pressed", module.key === state.agent);
    return node;
  }));
  $("severity-filter").replaceChildren(...SEVERITIES.map((severity) => {
    const on = state.severity.has(severity);
    const count = rows.filter((row) => row.severity === severity && !row.review_reason).length;
    const node = button(`chip sev-${severity}${on ? " on" : ""}`, `${levelName(severity)} · ${count}`, () => {
      if (state.severity.has(severity)) state.severity.delete(severity);
      else state.severity.add(severity);
      renderFindings();
    });
    node.setAttribute("aria-pressed", on);
    return node;
  }));
}

function renderFindings() {
  const all = findings();
  renderFilters(all);
  const visible = all.filter((defect) => state.severity.has(defect.severity));
  const reviewed = visible.filter((defect) => !defect.review_reason);
  const pending = visible.filter((defect) => defect.review_reason);
  $("copy-all-btn").disabled = !reviewed.length || !state.audit.report_format.fields.includes("suggestion");

  const list = $("defects-list");
  list.replaceChildren();
  const module = state.audit.modules.find((item) => item.key === state.agent);
  if (module && !module.ok) {
    list.append(element("p", "defect-card error-card", module.error));
  }
  for (const group of groups(reviewed)) {
    const panel = element("section", "finding-group");
    panel.append(element("h4", "group-title", group.title), ...group.rows.map(makeCard));
    list.append(panel);
  }
  if (!visible.length) {
    const message = msg(all.length ? "noFiltered" : state.audit.partial ? "noPartial" : "noFindings");
    list.append(element("p", "defect-card empty-card", message));
  }
  if (pending.length) {
    const panel = element("details", "audit-details pending-list");
    panel.append(element("summary", "", `${msg("pending")} · ${pending.length}`), ...pending.sort(sortedBySeverity).map(makeCard));
    list.append(panel);
  }
}

function makeEvidence(defect) {
  const evidence = element("div", "evidence-list");
  for (const fragment of defect.evidence) {
    const quote = element("div", "quote-box");
    const label = element("div", "quote-heading", [fragment.requirement_id, lineLabel(fragment)].filter(Boolean).join(" · "));
    const requirement = state.audit.document.requirements.find((item) =>
      fragment.requirement_id ? item.id === fragment.requirement_id : item.start < fragment.end && fragment.start < item.end);
    if (requirement) {
      label.append(button("link-btn", msg("findQuote"), () => openRequirement(requirement.id)));
    }
    quote.append(label, element("blockquote", "", fragment.text));
    evidence.append(quote);
  }
  if (defect.verified === "missing") {
    evidence.append(element("p", "quote-box", msg("missing")));
  } else if (!defect.evidence.length && defect.quote.trim()) {
    evidence.append(element("blockquote", "quote-box", defect.quote));
  }
  return evidence;
}

function labelled(label, text, className) {
  const paragraph = element("p", className);
  paragraph.append(element("strong", "", label), document.createTextNode(` ${text}`));
  return paragraph;
}

function makeCard(defect) {
  const card = element("article", "defect-card");
  card.id = defect.id;
  card.tabIndex = -1;
  const header = element("div", "defect-header");
  header.append(element("span", `badge ${defect.severity}`, levelName(defect.severity)),
    element("h5", "", defect.title));
  if (state.comparison?.result?.new.includes(defect.id)) header.append(element("span", "badge finding-new", msg("newFinding")));
  card.append(header);
  if (state.audit.partial && state.audit.native) {
    card.append(element("p", "hint", msg(defect.audit_status === "audited" ? "findingAudited" : "findingPreliminary")));
  }
  const issueGroup = state.audit.issue_groups?.find((group) => group.members.some((member) => member.id === defect.id));
  if (issueGroup?.members.length > 1) {
    const ids = issueGroup.members.map((member) => member.id);
    card.append(element("p", "hint", msg("groupMembers") + " · " + ids.join(", ")));
  }
  if (Number.isFinite(defect.confidence)) {
    card.append(element("p", "hint", `${msg("modelConfidence")} · ${Math.round(defect.confidence * 100)}%`));
  }
  if (defect.review_reason) {
    card.append(element("p", "pending-note", msg(defect.review_reason)));
  }
  const details = element("details", "audit-details finding-details");
  details.append(element("summary", "", msg("more")));
  for (const field of state.audit.report_format.fields) {
    if (field === "evidence") {
      if (defect.evidence.length > 1) card.append(element("p", "hint proof-label", msg("contradictionProof")));
      card.append(makeEvidence(defect));
    }
    if (field === "issue") card.append(labelled(`${msg("why")}.`, defect.issue, "issue-text"));
    if (field === "suggestion") {
      const fix = element("div", "fix-box");
      const heading = element("div", "fix-head");
      heading.append(element("strong", "", msg(defect.action_kind === "business_decision" ? "businessDecision" : defect.fix.kind === "clarify" ? "clarificationNeeded" : "howFix")));
      if (!defect.review_reason) heading.append(button("btn-mini", msg("copy"), () => copyText(defect.suggestion)));
      fix.append(heading, element("p", "fix-text", defect.suggestion));
      card.append(fix);
    }
    if (field === "criterion" && defect.criterion) {
      details.append(labelled(`${msg("rule")}.`, Object.hasOwn(UI_TEXT, "criterion_" + defect.criterion.id) ? msg("criterion_" + defect.criterion.id) : defect.criterion.title, ""));
      if (state.audit.mode !== "rules" && !state.audit.native) details.append(element("p", "", msg("penalty", { n: defect.score_points })));
    }
    if (field === "contributors") {
      details.append(labelled(`${msg("checkedBy")}.`, [defect.module, ...defect.also_by].join(", "), ""));
    }
    if (field === "references" && defect.references.length) {
      const references = element("ul", "reference-list");
      for (const reference of defect.references) {
        if (!reference.url.startsWith("https://")) continue;
        const link = element("a", "", reference.title);
        link.href = reference.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const item = element("li");
        item.append(link);
        references.append(item);
      }
      details.append(element("p", "hint", msg("references")), references);
    }
  }
  if (details.children.length > 1) card.append(details);
  if (defect.requirement_ids.length) {
    const links = element("div", "review-tags");
    for (const id of defect.requirement_ids) {
      links.append(button("link-btn review-link", `${msg("showRequirement")} · ${id}`, () => openRequirement(id)));
    }
    card.append(links);
  }
  if (!defect.review_reason) {
    card.append(button("btn-secondary review-link", msg("workOnFix"), () => openFix(defect.id)));
  }
  return card;
}

function markdownText(text) {
  return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replace(/[\\`*_{}\[\]()#+.!|]/g, "\\$&");
}

function buildMarkdown() {
  const { audit, snapshot } = state;
  const t = (key, values) => msg(key, values, audit.language);
  const lines = [
    `# ${markdownText(audit.report_format.title)}`, "",
    `- ${t(audit.native ? "qualityEstimate" : reportMode(audit) === "quick" ? "quickScore" : "criteriaScore")} · ${audit.score === null ? "—" : audit.score + "/100"}`,
    `- ${t("chooseMode")} · ${t(checkModeKey(reportMode(audit)))}`,
    `- ${t("reportVersion")}: ${markdownText(audit.profile.title)} v${audit.profile.version} / ${audit.profile.fingerprint.slice(0, 10)}`,
    `- ${t("date")}: ${new Date(audit.created_at).toLocaleString(LOCALES[audit.language])}`,
  ];
  if (audit.auditor_failure) lines.push(`- ${t("auditPartialStatus")}`, `- ${t(auditorFailureKey(audit.auditor_failure))}`);
  if (audit.mode === "replay") lines.push(`- ${t("exampleLabel")}.`);
  if (audit.mode === "rules") lines.push(`- ${t("ruleScoreExplanation")}`);
  if (snapshot.title) lines.push(`- ${t("document")}: ${markdownText(snapshot.title)}`);
  if (audit.report_format.show_coverage) {
    lines.push(`- ${t("completed", { done: audit.coverage.agents_completed, total: audit.coverage.agents_total })}.`,
      `- ${markdownText(scoreExplanation(audit, audit.language))}`);
  }
  for (const warning of audit.source_warnings) lines.push(`- ${markdownText(warning)}`);
  for (const module of audit.modules.filter((item) => !item.ok)) {
    lines.push(`- ${markdownText(module.title)}: ${markdownText(module.error)}`);
  }
  if (snapshot.context && audit.report_format.include_context) {
    lines.push("", `## ${t("contextTitle")}`, "", markdownText(snapshot.context));
  }
  function appendDefect(defect) {
    lines.push("", `### ${levelName(defect.severity, audit.language)} · ${markdownText(defect.title)}`);
    if (defect.review_reason) lines.push(`- ${t(defect.review_reason)}`);
    for (const field of audit.report_format.fields) {
      if (field === "evidence") {
        for (const fragment of defect.evidence) lines.push(`- ${markdownText(lineLabel(fragment, audit.language))} · ${markdownText(fragment.text)}`);
        if (defect.verified === "missing") lines.push(`- ${t("missing")}`);
        else if (!defect.evidence.length) lines.push(`- ${t("excerpt")}: ${markdownText(defect.quote || "—")}`);
      }
      if (field === "issue") lines.push(`- ${t("why")}: ${markdownText(defect.issue)}`);
      if (field === "suggestion") lines.push(`- ${t("howFix")}: ${markdownText(defect.suggestion)}`);
      if (field === "criterion" && defect.criterion) {
        lines.push(`- ${t("rule")}: ${markdownText(defect.criterion.title)} (${defect.criterion.id})`,
        );
        if (audit.mode !== "rules" && !audit.native) lines.push(`- ${t("penalty", { n: defect.score_points })}`);
      }
      if (field === "contributors") lines.push(`- ${t("checkedBy")}: ${markdownText([defect.module, ...defect.also_by].join(", "))}`);
      if (field === "references" && defect.references.length) {
        for (const ref of defect.references) lines.push(`- ${t("reference")}: [${markdownText(ref.title)}](<${ref.url}>)`);
      }
    }
  }
  const all = findings(true);
  for (const group of groups(all.filter((defect) => !defect.review_reason), audit.language)) {
    lines.push("", `## ${markdownText(group.title)}`);
    group.rows.forEach(appendDefect);
  }
  const pending = all.filter((defect) => defect.review_reason);
  if (pending.length) {
    lines.push("", `## ${t("pending")}`);
    pending.sort(sortedBySeverity).forEach(appendDefect);
  }
  if (!all.length) lines.push("", t(audit.partial ? "noPartial" : "noFindings"));
  appendInsightsMarkdown(lines, audit);
  return lines.join("\n") + "\n";
}

function bindAuditControls() {
  $("auth-form").addEventListener("submit", submitAuth);
  bindReviewTools();
  bindAuditExtras();

  for (const tab of tabs) {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1) : tabs.find((item) => item !== tab);
      switchTab(next.dataset.tab, true);
    });
  }

  for (const id of ["spec-text", "extra-context"]) $(id).addEventListener("input", () => {
    updateInputs();
  });
  $("clear-btn").addEventListener("click", () => { $("spec-text").value = ""; updateInputs(); $("spec-text").focus(); });
  $("context-clear").addEventListener("click", () => { $("extra-context").value = ""; updateInputs(); });
  $("remember-draft").addEventListener("change", () => {
    if ($("remember-draft").checked) saveDraft();
    else {
      clearTimeout(draftTimer);
      try { localStorage.removeItem(DRAFT_KEY); } catch (_) { /* Необязательное хранилище. */ }
    }
  });
  window.addEventListener("pagehide", saveDraft);
  $("spec-file").addEventListener("change", (event) => {
    addFiles(event.target.files);
    event.target.value = "";
  });
  for (const name of ["dragenter", "dragover", "dragleave", "drop"]) {
    $("dropzone").addEventListener(name, (event) => {
      event.preventDefault();
      $("dropzone").classList.toggle("drag", name === "dragenter" || name === "dragover");
      if (name === "drop" && event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
    });
  }

  document.addEventListener("keydown", (event) => {
    if (!state.audit && (event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runAudit();
    }
    if (event.key === "Escape") state.abort?.abort();
  });
  $("run-btn").addEventListener("click", () => runAudit());
  $("retry-auditor-btn").addEventListener("click", () => {
    if (state.auditorRetry) runAudit(state.snapshot, "deep", null, state.auditorRetry);
  });
  $("cancel-btn").addEventListener("click", () => state.abort?.abort());
  $("demo-btn").addEventListener("click", () => runDemo());
  $("demo-before").addEventListener("click", () => showDemoVersion("before"));
  $("demo-after").addEventListener("click", () => showDemoVersion("after"));
  $("copy-all-btn").addEventListener("click", () => {
    const visible = findings().filter((item) => !item.review_reason && state.severity.has(item.severity)).sort(sortedBySeverity);
    if (visible.length) copyText(visible.map((item) => `${item.title}\n${item.suggestion}`).join("\n\n"));
  });
  for (const id of ["native-pdf-btn", "executive-pdf-btn"]) $(id).addEventListener("click", async () => {
    try {
      const executive = id === "executive-pdf-btn";
      const replay = state.audit.mode === "replay";
      const query = new URLSearchParams({ executive, language: state.audit.language,
        mode: reportMode(state.audit), version: state.demoVersion || "before" });
      const response = replay
        ? await fetch("/api/demo/pdf?" + query, {headers: authHeaders()})
        : await fetch("/api/audit/pdf?" + query, {method: "POST",
          headers: {...authHeaders(), "Content-Type": "application/json"},
          body: JSON.stringify(state.audit.native)});
      if (!response.ok) throw new Error(await responseError(response));
      download(await response.blob(), "application/pdf", "pdf",
        id === "executive-pdf-btn" ? "requiragents-executive" : state.audit.report_format.filename);
    } catch (failure) {
      error(failure.message);
    }
  });
  $("export-btn").addEventListener("click", () => download(buildMarkdown(), "text/markdown;charset=utf-8", "md"));
  $("print-report-btn").addEventListener("click", printReport);
  $("export-json-btn").addEventListener("click", () => {
    const { text, ...sourceMap } = state.audit.document;
    const report = { ...state.audit, document: sourceMap };
    download(JSON.stringify(report, null, 2), "application/json;charset=utf-8", "json");
  });
}

document.querySelectorAll("[data-language]").forEach((node) => {
  node.addEventListener("change", () => setLanguage(node.value));
});
if (AUDIT_PAGE) bindAuditControls();
init();
