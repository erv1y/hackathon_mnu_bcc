"use strict";

const reviewNumbers = /(?<![\p{L}\w-])\d+(?:[ ,\u00a0]\d{3})*(?:[.,]\d+)?(?![\p{L}\w-])/gu;

function conflictTargets(row, source) {
  if (row.review_reason || row.verified !== "exact" ||
      (row.action_kind !== "business_decision" && !["contradiction", "inconsistent_constraint", "scope_conflict"].includes(row.criterion_id))) return [];
  const chars = Array.from(source);
  const targets = (row.requirement_ids || []).map((id) => {
    const candidates = row.evidence.filter((piece) => piece.requirement_id === id &&
      Number.isInteger(piece.start) && Number.isInteger(piece.end) && piece.start >= 0 &&
      piece.end > piece.start && chars.slice(piece.start, piece.end).join("") === piece.text);
    if (!candidates.length) return null;
    const proof = candidates.sort((a, b) => b.text.length - a.text.length)[0];
    return { start: proof.start, end: proof.end, original: proof.text, requirementId: id,
      fileId: proof.file_id, sourceId: row.sourceIds?.[id] };
  });
  if (targets.length < 2 || targets.some((target) => !target)) return [];
  const ordered = targets.sort((a, b) => a.start - b.start);
  return ordered.some((edit, index) => index && ordered[index - 1].end > edit.start) ? [] : ordered;
}

function conflictValues(targets) {
  const matches = targets.map((target) => [...target.original.matchAll(reviewNumbers)]);
  if (!matches.length || matches.some((values) => values.length !== 1)) return [];
  const units = targets.map((target, index) => {
    const match = matches[index][0];
    return target.original.slice(match.index + match[0].length).trimStart().match(/^([\p{L}₸$€%]+)/u)?.[0].toLowerCase() || "";
  });
  if (new Set(units).size !== 1) return [];
  const values = [...new Set(matches.map(([match]) => match[0]))];
  return values.length > 1 ? values : [];
}

function validDraftEdits(source, edits, files = []) {
  const chars = Array.from(source);
  let end = 0;
  for (const edit of [...edits].sort((a, b) => a.start - b.start)) {
    if (!Number.isInteger(edit.start) || !Number.isInteger(edit.end) || edit.start < end ||
        edit.end <= edit.start || edit.end > chars.length || typeof edit.replacement !== "string" ||
        !edit.replacement.trim() || chars.slice(edit.start, edit.end).join("") !== edit.original ||
        (files.length && !files.some((file) => edit.start >= file.start && edit.end <= file.end))) return false;
    end = edit.end;
  }
  return true;
}

function approvedConflictEdits(targets, replacements, current, confirmed) {
  if (!confirmed || targets.length < 2 || targets.length !== replacements.length) return null;
  const edits = targets.map((target, index) => ({ ...target, replacement: replacements[index].trim() }));
  if (edits.some((edit) => !edit.replacement || (edit.sourceId && edit.original.includes(edit.sourceId) &&
    !edit.replacement.includes(edit.sourceId)) || (edit.original.includes(edit.requirementId) &&
    !edit.replacement.includes(edit.requirementId)) || current.some((other) => edit.start < other.end && other.start < edit.end))) return null;
  return edits.some((edit) => edit.original !== edit.replacement) &&
    !edits.some((edit, i) => edits.some((other, j) => i < j && edit.start < other.end && other.start < edit.end))
    ? edits : null;
}

function clarificationDecision(previous, action, answer, notes) {
  const value = answer.trim();
  if (action === "approved" && (!value || previous?.status !== "answered" || previous.answer !== value)) return null;
  if (action === "answered" && !value) return null;
  return {status: action, answer: value, notes: notes.trim()};
}

function safeSpreadsheetCell(value) {
  const text = String(value ?? "");
  return /^[\s\u0000-\u001f]*[=+\-@]|^[\t\r\n]/u.test(text) ? "'" + text : text;
}

// Excel reads SpreadsheetML workbooks without additional dependencies.
function spreadsheetRows(rows, excel = false) {
  if (!excel) return "\uFEFF" + rows.map((row) => row.map((value) =>
    '"' + safeSpreadsheetCell(value).replaceAll('"', '""') + '"').join(",")).join("\r\n");
  const xml = (value) => safeSpreadsheetCell(value).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  return '<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>' +
    '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">' +
    '<Worksheet ss:Name="Tests"><Table>' + rows.map((row) => "<Row>" + row.map((value) =>
      '<Cell><Data ss:Type="String">' + xml(value) + "</Data></Cell>").join("") + "</Row>").join("") + "</Table></Worksheet></Workbook>";
}
