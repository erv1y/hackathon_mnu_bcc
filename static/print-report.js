"use strict";

function printReport() {
  const { audit, snapshot } = state;
  const t = (key, values) => msg(key, values, audit.language);
  $("print-report")?.remove();
  const paper = element("article", "print-report");
  paper.id = "print-report";
  paper.lang = audit.language;
  const name = snapshot.title || audit.report_format.title;
  paper.append(element("p", "print-brand", "REQUIRAGENTS"), element("h1", "", name));
  const counts = auditCounts(audit);
  const coverage = audit.coverage;
  paper.append(element("p", "print-meta", [
    t(checkModeKey(reportMode(audit))),
    audit.mode === "rules" ? t("ruleReview") : audit.mode === "replay" ? t("exampleLabel") : audit.model,
    new Date(audit.created_at).toLocaleString(LOCALES[audit.language]),
  ].join(" · ")));
  const metrics = element("dl", "print-metrics");
  for (const [label, value] of [["score", counts.score], ["High", counts.critical],
    ["findingsTotal", counts.findings], ["requirementCount", counts.requirements]]) {
    const cell = element("div");
    cell.append(element("dt", "", t(label)), element("dd", "", value));
    metrics.append(cell);
  }
  paper.append(metrics, element("p", "print-meta", scoreExplanation(audit, audit.language)));
  if (audit.report_format.show_coverage) {
    paper.append(element("p", "print-meta", t("countRules", {
      done: coverage.criteria_in_completed_agents, total: coverage.criteria_total, merged: audit.merged_duplicates,
    })));
  }
  if (audit.auditor_failure) paper.append(element("p", "print-note", t(auditorFailureKey(audit.auditor_failure))));
  for (const warning of audit.source_warnings) paper.append(element("p", "print-note", warning));
  const files = audit.document.files?.filter((item) => item.name) || [];
  if (files.length) {
    const list = element("ul");
    list.append(...files.map((file) => element("li", "", file.name + " · " + t("role_" + file.role))));
    paper.append(element("h2", "", t("fileCount", { n: files.length, max: files.length })), list);
  }
  const rows = comparableFindings(audit).sort(sortedBySeverity);
  paper.append(element("h2", "", t("findingsTotal")));
  if (!rows.length) paper.append(element("p", "", t(audit.partial ? "noPartial" : "noFindings")));
  for (const finding of rows) {
    const section = element("section", "print-finding");
    section.append(element("h3", "", [finding.id, levelName(finding.severity, audit.language), finding.title].join(" · ")));
    if (audit.report_format.fields.includes("evidence")) {
      for (const quote of finding.evidence) {
        const block = element("blockquote");
        block.append(element("strong", "", lineLabel(quote, audit.language)), element("p", "", quote.text));
        section.append(block);
      }
    }
    if (finding.issue && audit.report_format.fields.includes("issue")) section.append(element("p", "", finding.issue));
    if (audit.report_format.fields.includes("suggestion")) {
      section.append(labelled(t("proposedDefault") + " ·", finding.suggestion || finding.default, ""));
    }
    paper.append(section);
  }
  const questions = audit.insights?.questions || [];
  if (questions.length) {
    const list = element("ol");
    for (const question of questions) {
      const item = element("li", "", question.question);
      item.append(element("p", "print-meta", question.evidence.map((proof) => lineLabel(proof, audit.language)).join(" · ")));
      list.append(item);
    }
    paper.append(element("h2", "", t("customerQuestions")), list);
  }
  document.body.append(paper);
  window.addEventListener("afterprint", () => paper.remove(), { once: true });
  window.print();
}
