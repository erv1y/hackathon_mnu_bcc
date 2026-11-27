"use strict";

const REVIEW_PAGE_SIZE = 12;
const REVIEW_PRIORITY = { Critical: "P0", High: "P0", Medium: "P1", Low: "P2" };
const REVIEW_STATUS = { risk: "reqRisk", review: "reqReview", partial: "reqPartial", clear: "reqClear" };
const reviewState = {
  tab: "report", page: 0, decisions: new Map(), activeFix: null, activeQuestion: null, answer: null, revisedDocuments: [], clarifications: new Map(), conversation: [], approvedScenarios: new Set(),
};

function resetReviewTools() {
  reviewState.activeQuestion?.abort();
  Object.assign(reviewState, {
    tab: "report", page: 0, decisions: new Map(), activeFix: null, activeQuestion: null, answer: null, revisedDocuments: [], clarifications: new Map(), conversation: [], approvedScenarios: new Set(),
  });
  $("question-input").value = "";
  delete $("results").dataset.panelDirection;
}

function renderReviewTools() {
  for (const control of document.querySelectorAll("[data-review-tab]")) {
    const selected = control.dataset.reviewTab === reviewState.tab;
    control.classList.toggle("active", selected);
    control.setAttribute("aria-selected", selected);
    control.tabIndex = selected ? 0 : -1;
  }
  for (const pane of document.querySelectorAll("[data-review-pane]")) {
    show(pane, pane.dataset.reviewPane === reviewState.tab);
  }
  if (reviewState.tab === "requirements") renderRequirements();
  if (reviewState.tab === "fixes") renderFixes();
  if (reviewState.tab === "questions") {
    renderCustomerQuestions();
    renderQuestionAnswer();
  }
  if (reviewState.tab === "scenarios") renderScenarios();
  if (reviewState.tab === "compare") renderComparison();
}

function openReviewTab(tab) {
  if (tab !== reviewState.tab) {
    const order = Array.from($("review-tabs").querySelectorAll("[data-review-tab]"), (button) => button.dataset.reviewTab);
    $("results").dataset.panelDirection = order.indexOf(tab) < order.indexOf(reviewState.tab) ? "return" : "advance";
  }
  reviewState.tab = tab;
  renderReviewTools();
}

function focusReviewCard(id) {
  const card = $(id);
  card?.closest("details")?.setAttribute("open", "");
  card?.focus({ preventScroll: true });
  card?.scrollIntoView({ block: "nearest" });
}

function openRequirement(id) {
  const index = state.audit.document.requirements.findIndex((item) => item.id === id);
  if (index < 0) return;
  reviewState.page = Math.floor(index / REVIEW_PAGE_SIZE);
  openReviewTab("requirements");
  focusReviewCard(id);
}

function openFinding(id) {
  state.agent = "all";
  state.severity = new Set(SEVERITIES);
  openReviewTab("report");
  renderFindings();
  focusReviewCard(id);
}

function openFix(id) {
  const group = state.audit.issue_groups?.find((candidate) => candidate.members.some((member) => member.id === id));
  reviewState.activeFix = group?.representative || id;
  openReviewTab("fixes");
  focusReviewCard("active-fix");
}

function renderConnections(defects) {
  const panel = $("requirement-connections");
  const linked = defects.filter((item) => item.evidence.length > 1);
  show(panel, linked.length > 0);
  const items = state.audit.document.requirements;
  const relationships = linked.map((row) => ({
    id: row.id, title: row.title, criterion_id: row.criterion_id, severity: row.severity,
    sources: row.evidence.map((proof) => ({
      ...proof, requirement_ids: items.filter((item) => item.start < proof.end && proof.start < item.end).map((item) => item.id),
    })),
  }));
  const header = element("div", "review-panel-head");
  header.append(element("h3", "", msg("requirementConnections")));
  if (relationships.length) header.append(button("btn-secondary", msg("exportConnections"), () =>
    download(JSON.stringify({ profile: state.audit.profile, relationships }, null, 2), "application/json;charset=utf-8", "json", "requiragents-connections")));
  panel.replaceChildren(header);
  for (const row of relationships) {
    const path = element("div", "connection-row");
    const links = element("div", "trace-path");
    row.sources.forEach((source, index) => {
      if (index) {
        const connector = element("span", "trace-edge", "↔");
        connector.setAttribute("aria-hidden", "true");
        links.append(connector);
      }
      const req = source.requirement_ids[0];
      const node = button("trace-node", req || msg("excerpt"), () =>
        req ? openRequirement(req) : openReviewFinding(row.id));
      node.append(element("span", "hint", lineLabel(source)));
      links.append(node);
    });
    path.append(button("link-btn", row.title, () => openReviewFinding(row.id)), links);
    panel.append(path);
  }
}

function renderRequirements() {
  const { text, requirements, unmapped_findings: unmapped } = state.audit.document;
  const { page } = reviewState;
  const from = page * REVIEW_PAGE_SIZE;
  const shown = requirements.slice(from, from + REVIEW_PAGE_SIZE);
  const characters = Array.from(text);
  const byId = new Map(findings(true).map((finding) => [finding.id, finding]));
  renderConnections(comparableFindings(state.audit));
  const map = $("risk-map");

  $("risk-legend").replaceChildren(...SEVERITIES.map((severity) =>
    element("span", `badge ${severity}`, `${REVIEW_PRIORITY[severity]} · ${levelName(severity)}`)
  ));
  map.replaceChildren(...shown.map((item) => {
    const name = [lineLabel(item), item.severity ? REVIEW_PRIORITY[item.severity] : ""].filter(Boolean).join(" · ");
    const cell = button(`risk-cell ${item.severity || ""}`, name, () => focusReviewCard(item.id));
    cell.title = msg(REVIEW_STATUS[item.status], { n: item.risk_count });
    return cell;
  }));
  show($("unmapped-findings"), unmapped.length > 0, msg("unmappedCount", { n: unmapped.length }));

  const list = $("requirements-list");
  list.replaceChildren(...shown.map((item) => {
    const card = element("article", "review-panel");
    card.id = item.id;
    card.tabIndex = -1;
    const heading = element("div", "review-panel-head");
    const title = item.source_id ? `${item.id} · ${item.source_id}` : item.id;
    heading.append(element("h3", "", title), element("span", "hint", lineLabel(item)));
    const meta = element("div", "review-tags");
    meta.append(element("span", "hint", msg(item.kind)));
    if (item.priority) {
      const badge = element("span", `badge ${item.severity || ""}`, item.priority);
      badge.title = msg(item.priority_source === "document" ? "priorityDocument" : "priorityAudit");
      meta.append(badge);
    }
    if (item.section) meta.append(element("span", "hint", item.section));
    const source = characters.slice(item.start, item.end).join("");
    card.append(heading, meta, element("blockquote", "review-source", source),
      element("p", "hint", msg(REVIEW_STATUS[item.status], { n: item.risk_count })));
    for (const id of item.finding_ids) {
      const finding = byId.get(id);
      if (finding) card.append(button("link-btn review-link", finding.title, () => openFinding(id)));
    }
    return card;
  }));
  if (!shown.length) list.append(element("p", "hint", msg("noMapped")));

  const pages = $("requirements-pages");
  pages.replaceChildren();
  if (requirements.length <= REVIEW_PAGE_SIZE) return;
  const next = from + shown.length;
  const go = (offset) => {
    reviewState.page += offset;
    renderRequirements();
    focusReviewCard(requirements[reviewState.page * REVIEW_PAGE_SIZE].id);
  };
  const previousButton = button("btn-secondary", msg("previousPage"), () => go(-1));
  const nextButton = button("btn-secondary", msg("nextPage"), () => go(1));
  previousButton.disabled = !page;
  nextButton.disabled = next >= requirements.length;
  pages.append(previousButton, element("span", "hint", msg("shownRequirements", {
    from: from + 1, to: next, total: requirements.length,
  })), nextButton);
}

function overlaps(first, second) {
  return first.start < second.end && second.start < first.end;
}

function renderDocumentComparison(source, edits) {
  const chars = Array.from(source);
  const original = [];
  const revised = [];
  const files = state.audit.document.files?.length ? state.audit.document.files : [{ role: "main", start: 0, end: chars.length }];
  reviewState.revisedDocuments = files.map((file) => {
    if (files.length > 1) {
      const title = [file.name, msg("role_" + file.role)].filter(Boolean).join(" · ") + "\n\n";
      if (original.length) {
        original.push("\n\n");
        revised.push("\n\n");
      }
      original.push(element("strong", "", title));
      revised.push(element("strong", "", title));
    }
    const changed = [];
    let offset = file.start;
    for (const edit of edits) {
      if (edit.start < offset || edit.end > file.end || chars.slice(edit.start, edit.end).join("") !== edit.original) continue;
      const preceding = chars.slice(offset, edit.start).join("");
      original.push(preceding, element("mark", "original-mark", edit.original));
      changed.push(preceding, edit.replacement);
      revised.push(preceding, element("mark", "revised-mark", edit.replacement));
      offset = edit.end;
    }
    const tail = chars.slice(offset, file.end).join("");
    original.push(tail);
    revised.push(tail);
    return { name: file.name, role: file.role, text: changed.join("") + tail };
  });
  $("original-preview").replaceChildren(...original);
  $("revised-preview").replaceChildren(...revised);
}

function draftEdits(rows) {
  const edits = [];
  for (const row of rows) {
    const decision = reviewState.decisions.get(row.id);
    if (decision?.status !== "accepted") continue;
    if (decision.edits) {
      edits.push(...decision.edits.map((edit) => ({ ...edit, id: row.id })));
    } else if (row.fix.kind === "replace") {
      edits.push({ ...row.fix, id: row.id, replacement: decision.replacement });
    }
  }
  return edits.sort((a, b) => a.start - b.start);
}

function reviewDecision(row, status, text) {
  reviewState.decisions.set(row.id, { status, replacement: text });
  renderFixes();
  focusReviewCard("active-fix");
}

function renderConflictEditor(card, row, edits, targets) {
  const existing = reviewState.decisions.get(row.id);
  const values = conflictValues(targets);
  const approved = [...reviewState.clarifications.values()].find((item) =>
    item.findingId === row.id && item.status === "approved");
  const fields = [];
  const pairs = element("div", "fix-comparison");
  const group = element("div", "actions");
  const fill = (value) => {
    if (!values.length || !/^\d+(?:[ ,\u00a0]\d{3})*(?:[.,]\d+)?$/u.test(value)) return;
    targets.forEach((target, index) => {
      fields[index].value = target.original.replace(reviewNumbers, () => value);
    });
  };
  for (const value of values) group.append(button("btn-secondary", value, () => fill(value)));
  if (values.length) {
    const amount = element("input");
    amount.type = "text";
    amount.setAttribute("aria-label", msg("customDecision"));
    amount.placeholder = msg("customDecision");
    group.append(amount, button("btn-secondary", msg("previewDecision"), () => fill(amount.value.trim())));
  }
  targets.forEach((target) => {
    const entry = element("label", "fix-wording");
    const input = element("textarea");
    input.rows = 3;
    input.maxLength = 2200;
    input.value = existing?.edits?.find((edit) => edit.requirementId === target.requirementId)?.replacement || target.original;
    fields.push(input);
    entry.append(element("strong", "", target.requirementId), element("blockquote", "", target.original), input);
    pairs.append(entry);
  });
  if (approved) {
    card.append(labelled(msg("clarificationApproved"), approved.answer, "hint"));
    if (values.length) group.append(button("btn-secondary", msg("useApprovedAnswer"), () => fill(approved.answer)));
  }
  const confirmation = element("input");
  confirmation.type = "checkbox";
  const label = element("label", "hint");
  label.append(confirmation, document.createTextNode(" " + msg("confirmBusinessDecision")));
  const message = element("p", "hint");
  const accept = button("btn-primary", msg("acceptFix"), () => {
    const accepted = approvedConflictEdits(targets, fields.map((input) => input.value),
      edits.filter((edit) => edit.id !== row.id), confirmation.checked);
    if (!accepted || !validDraftEdits(state.audit.document.text, accepted, state.audit.document.files)) {
      message.textContent = msg("invalidDecisionEdit");
      return;
    }
    reviewState.decisions.set(row.id, {status: "accepted", edits: accepted});
    renderFixes();
    focusReviewCard("revised-draft");
  });
  accept.disabled = true;
  confirmation.addEventListener("change", () => { accept.disabled = !confirmation.checked; });
  card.append(group, pairs, label, accept, message);
}

function renderFixDetail(row, edits) {
  const target = $("active-fix");
  target.replaceChildren();
  if (!row) return;
  const card = element("article", "review-panel");
  const title = element("div", "review-panel-head");
  title.append(element("h3", "", row.title), element("span", `badge ${row.severity}`, levelName(row.severity)));
  card.append(title, element("p", "hint", row.issue));
  const controls = element("div", "actions fix-actions");
  const sourceIds = Object.fromEntries(state.audit.document.requirements.map((item) => [item.id, item.source_id]));
  const targets = conflictTargets({ ...row, sourceIds }, state.audit.document.text);
  if (targets.length) {
    renderConflictEditor(card, row, edits, targets);
    card.append(makeEvidence(row));
  } else if (row.fix.kind === "replace") {
    const replacement = reviewState.decisions.get(row.id)?.replacement || row.fix.replacement;
    const pair = element("div", "fix-comparison");
    for (const [label, text] of [["fixOriginal", row.fix.original], ["fixReplacement", replacement]]) {
      const wording = element("div", "fix-wording");
      wording.append(element("strong", "hint", msg(label)), element("p", "", text));
      pair.append(wording);
    }
    const blocked = edits.some((edit) => edit.id !== row.id && overlaps(row.fix, edit));
    const accept = button("btn-primary", msg("acceptFix"), () => reviewDecision(row, "accepted", replacement));
    const edit = button("btn-secondary", msg("editFix"), () => {
      const editor = element("form", "review-panel inline-editor");
      const input = element("textarea");
      input.id = "edit-replacement";
      input.rows = 4;
      input.required = true;
      input.maxLength = 2200;
      input.value = replacement;
      const label = element("label", "", msg("fixReplacement"));
      label.htmlFor = input.id;
      const submit = element("button", "btn-primary", msg("saveEdit"));
      submit.type = "submit";
      editor.append(label, input, submit);
      editor.addEventListener("submit", (event) => {
        event.preventDefault();
        if (input.value.trim()) reviewDecision(row, "accepted", input.value.trim());
      });
      pair.replaceWith(editor);
      accept.disabled = true;
      edit.disabled = true;
      input.focus();
    });
    accept.disabled = blocked;
    edit.disabled = blocked;
    card.append(pair);
    if (blocked) card.append(element("p", "hint", msg("overlappingFix")));
    controls.append(accept, edit);
  } else {
    card.append(labelled(msg("clarificationNeeded"), row.fix.question || row.suggestion, "fix-wording"));
    card.append(makeEvidence(row));
  }
  if (reviewState.decisions.has(row.id)) controls.append(button("btn-secondary", msg("undoDecision"), () => {
    reviewState.decisions.delete(row.id);
    renderFixes();
  }));
  controls.append(button("btn-secondary", msg("rejectFix"), () => reviewDecision(row, "rejected")));
  if (row.verified === "exact") {
    controls.append(button("btn-secondary", msg("explainFix"), () => {
      openReviewTab("questions");
      $("question-input").focus();
      askReviewQuestion("explain", "", row.id);
    }));
  }
  card.append(controls);
  target.append(card);
}

function renderVersionHistory() {
  const root = $("version-history");
  const versions = state.run?.versions || [];
  show(root, versions.length > 0);
  root.replaceChildren(element("h3", "", msg("versionHistory")));
  for (const version of versions) {
    const row = element("div", "version-row");
    const title = msg("versionEntry", { n: version.number });
    row.append(element("strong", "", title), element("span", "hint", displayDate(version.time)),
      button("btn-secondary", msg("restoreVersion"), () => {
        if (reviewState.decisions.size && !window.confirm(msg("confirmRestore"))) return;
        reviewState.decisions = new Map(version.decisions);
        renderFixes();
        focusReviewCard("revised-draft");
      }));
    root.append(row);
  }
}

function renderFixes() {
  const rows = comparableFindings(state.audit).filter((item) => !item.review_reason && item.fix).sort(sortedBySeverity);
  const edits = draftEdits(rows);
  const active = rows.find((row) => row.id === reviewState.activeFix) || rows[0];
  const list = $("fixes-list");
  list.replaceChildren(element("h3", "", msg("fixChoose")));
  for (const row of rows) {
    const control = button("fix-list-item", row.title, () => openFix(row.id));
    control.setAttribute("aria-pressed", row.id === active.id);
    const status = reviewState.decisions.get(row.id)?.status;
    control.append(element("span", "hint", msg(status === "accepted" ? "acceptedFix" : status === "rejected" ? "rejectedFix" : "waitingFix")));
    list.append(control);
  }
  if (!rows.length) list.replaceChildren(element("p", "hint", msg("noDraftEdits")));
  renderFixDetail(active, edits);
  const draftValid = validDraftEdits(state.audit.document.text, edits, state.audit.document.files);
  reviewState.revisedDocuments = [];
  show($("revised-draft"), edits.length > 0 && draftValid);
  if (!draftValid) list.append(element("p", "warning-box", msg("draftConflict")));
  show($("audit-revised"), Boolean(state.config?.api_key_configured && state.audit.mode === "live" && !state.audit.source_warnings.length));
  if (edits.length && draftValid) renderDocumentComparison(state.audit.document.text, edits);
  renderVersionHistory();
}

function questionPayload(intent, question, focusId) {
  const { audit, demoVersion } = state;
  const previous = reviewState.conversation.filter((turn) => turn.question).at(-1);
  const payload = { intent, question, language: audit.language, mode: reportMode(audit),
    previous_question: intent === "free" ? previous?.question || "" : "" };
  if (focusId) payload.focus_id = focusId;
  if (audit.mode === "replay") return { ...payload, demo_version: demoVersion };
  const rows = (focusId ? findings(true) : comparableFindings(audit)).filter((row) =>
    !row.review_reason && row.verified === "exact" && row.evidence.length && row.evidence.length <= 12 && row.evidence.every((quote) => Array.from(quote.text).length >= 8 && Array.from(quote.text).length <= 1200)
  ).sort((a, b) => Number(b.id === focusId) - Number(a.id === focusId) || sortedBySeverity(a, b)).slice(0, 48);
  const hasFiles = Boolean(audit.document.files?.length);
  return {
    ...payload, ...(hasFiles ? { documents: reportSources(audit) } : { document: audit.document.text }),
    findings: rows.map(({ id, title, issue, suggestion, clarification, severity, evidence }) => ({
      id, title: Array.from(title).slice(0, 200).join(""), issue: issue.slice(0, 2200), suggestion: suggestion.slice(0, 2200), clarification: clarification?.slice(0, 500) || "", severity, quotes: evidence.map((quote) => quote.text),
      file_ids: evidence.every((quote) => quote.file_id) ? evidence.map((quote) => quote.file_id) : [],
    })),
  };
}

async function askReviewQuestion(intent, question = "", focusId = "") {
  if (!state.audit?.document) return;
  reviewState.activeQuestion?.abort();
  const controller = new AbortController();
  reviewState.activeQuestion = controller;
  reviewState.answer = null;
  renderQuestionAnswer();
  try {
    const result = await fetchJSON("/api/audit/ask", {
      method: "POST", signal: controller.signal,
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(questionPayload(intent, question, focusId)),
    });
    if (reviewState.activeQuestion === controller) {
      reviewState.answer = { intent, items: result.items, question };
      if (question) reviewState.conversation = [...reviewState.conversation, reviewState.answer].slice(-8);
      $("question-input").value = "";
    }
  } catch (_) {
    if (reviewState.activeQuestion === controller) reviewState.answer = { error: true };
  } finally {
    if (reviewState.activeQuestion === controller) {
      reviewState.activeQuestion = null;
      renderQuestionAnswer();
    }
  }
}

function renderQuestionAnswer() {
  const target = $("question-answer");
  target.replaceChildren();
  $("question-send").disabled = Boolean(reviewState.activeQuestion);
  if (reviewState.activeQuestion) target.append(element("p", "hint", msg("questionLoading")));
  if (state.audit.partial) target.append(element("p", "hint", msg("compareLimited")));
  const turns = [...reviewState.conversation];
  if (reviewState.answer && turns.at(-1) !== reviewState.answer) turns.push(reviewState.answer);
  for (const answer of turns) {
    if (answer.question) target.append(element("h4", "", answer.question));
    if (answer.error) {
      target.append(element("p", "warning-box", msg("questionFailed")));
      continue;
    }
    if (answer.intent === "score") target.append(element("p", "review-panel", scoreExplanation(state.audit)));
    if (answer.intent !== "score" && !answer.items.length) target.append(element("p", "hint", msg("questionEmpty")));
    const card = element("article", "review-panel");
    if (answer.items.length) target.append(card);
    for (const item of answer.items) {
    const isFinding = item.kind === "finding";
    card.append(element("p", "hint", msg(isFinding ? "findingSources" : "factsSources")));
    if (item.title) card.append(element("h4", "", item.title));
    if (isFinding) {
      if (answer.intent === "clarify") card.append(element("p", "", item.clarification));
      else {
        card.append(element("p", "", item.issue));
        if (answer.intent !== "impact") card.append(labelled(msg("proposedDefault"), item.suggestion, "hint"));
      }
    }
    card.append(makeEvidence({ evidence: item.evidence, verified: "exact", quote: "" }));
    const action = isFinding ? () => openFinding(item.id) : () => openRequirement(item.id);
    card.append(button("link-btn review-link", msg(isFinding ? "reviewReport" : "showRequirement"), action));
    }
  }
}

function bindReviewTools() {
  const tabs = Array.from(document.querySelectorAll("[data-review-tab]"));
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => openReviewTab(tab.dataset.reviewTab));
    tab.addEventListener("keydown", (event) => {
      const indexes = {
        ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
        ArrowUp: (index + tabs.length - 1) % tabs.length,
        ArrowDown: (index + 1) % tabs.length,
        Home: 0, End: tabs.length - 1,
      };
      if (!Object.hasOwn(indexes, event.key)) return;
      event.preventDefault();
      const next = tabs[indexes[event.key]];
      openReviewTab(next.dataset.reviewTab);
      next.focus();
    });
  });
  $("download-revised").addEventListener("click", () =>
    download($("revised-preview").textContent, "text/plain;charset=utf-8", "txt", "requiragents-revised-spec"));
  $("audit-revised").addEventListener("click", () => {
    if (!reviewState.revisedDocuments.length) return;
    const { audit, snapshot, run } = state;
    const title = $("result-title").textContent;
    runAudit(
      { kind: "package", documents: reviewState.revisedDocuments, context: snapshot.context || run?.context || "",
        title: msg("editedVersion", { name: title }) },
      "deep", { id: run?.id || "before-draft", title, report: audit },
    );
  });
  $("save-version").addEventListener("click", () => {
    if (!reviewState.revisedDocuments.length) return;
    const ok = persistVersion([...reviewState.decisions]);
    toast(msg(ok ? "versionSaved" : "versionUnsaved"));
    renderVersionHistory();
  });
  $("question-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("question-input").value.trim();
    if (question) askReviewQuestion("free", question);
  });
  document.querySelectorAll("[data-intent]").forEach((control) => {
    control.addEventListener("click", () => askReviewQuestion(control.dataset.intent));
  });
}
