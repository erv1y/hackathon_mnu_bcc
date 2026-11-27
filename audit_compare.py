"""Сопоставление замечаний двух завершённых прогонов."""

import re
from difflib import SequenceMatcher
from typing import Literal

from pydantic import Field, model_validator

from audit_documents import MAX_DOCUMENTS, DocumentBundle, DocumentSource, PreparedDocument
from audit_profile import DataModel
from postprocess import SEV_RANK, QuoteIndex
from schemas import CheckMode, DocumentRole, Severity

_SPACE = re.compile(r"\s+")


class EvidenceDigest(DataModel):
    text: str = Field(min_length=1, max_length=600)
    file_name: str | None = Field(default=None, max_length=180)
    file_role: DocumentRole = "main"


class FindingDigest(DataModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")
    criterion_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=600)
    severity: Severity
    evidence: list[EvidenceDigest] = Field(default_factory=list, max_length=12)


class ReportDigest(DataModel):
    run_type: Literal["live", "replay", "rules"] = "live"
    mode: CheckMode = "deep"
    source_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    profile_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    partial: bool = False
    independent_audit: bool | None = None
    minimum_severity: Severity = "Low"
    completed_criteria: list[str] = Field(default_factory=list, max_length=160)
    findings: list[FindingDigest] = Field(max_length=240)

    @model_validator(mode="after")
    def require_unique_ids(self):
        if len({row.id for row in self.findings}) != len(self.findings):
            raise ValueError("ID замечаний должны быть уникальными в пределах отчёта.")
        return self


class ComparisonRequest(DataModel):
    before: ReportDigest
    after: ReportDigest
    after_sources: list[PreparedDocument] = Field(default_factory=list, max_length=MAX_DOCUMENTS)


def _clean(text: str) -> str:
    return _SPACE.sub(" ", text.casefold()).strip()


def _ratio(a: str, b: str) -> float:
    first, second = _clean(a), _clean(b)
    return 1.0 if first == second else SequenceMatcher(None, first, second).ratio()


def _match(left: FindingDigest, right: FindingDigest) -> float:
    title = _ratio(left.title, right.title)
    if not left.evidence and not right.evidence:
        return title if title >= .86 else 0.0
    if not left.evidence or len(left.evidence) != len(right.evidence):
        return 0.0
    used = set()
    proofs = []
    same_files = 0
    for proof in left.evidence:
        scores = [
            (_ratio(proof.text, other.text), bool(proof.file_name and proof.file_name == other.file_name), index)
            for index, other in enumerate(right.evidence)
            if index not in used and proof.file_role == other.file_role
        ]
        if not scores:
            return 0.0
        best, name_matches, index = max(scores)
        proofs.append(best)
        same_files += name_matches
        used.add(index)
    weakest = min(proofs)
    if weakest >= .74 and title >= .65:
        return .75 * weakest + .25 * title + .01 * same_files / len(proofs)
    return 0.0


def compare_reports(before: ReportDigest, after: ReportDigest,
                    after_sources: list[PreparedDocument] | None = None) -> dict:
    same_source = bool(before.source_hash and before.source_hash == after.source_hash)
    same_run_type = before.run_type == after.run_type
    same_profile = before.profile_hash == after.profile_hash
    candidates = []
    after_by_rule: dict[str, list[tuple[int, FindingDigest]]] = {}
    for index, item in enumerate(after.findings):
        after_by_rule.setdefault(item.criterion_id, []).append((index, item))
    for i, item in enumerate(before.findings):
        for j, other in after_by_rule.get(item.criterion_id, []):
            strength = _match(item, other)
            if strength:
                candidates.append((strength, i, j))

    pairs = {}
    used = set()
    for _, i, j in sorted(candidates, key=lambda row: (-row[0], row[1], row[2])):
        if i not in pairs and j not in used:
            pairs[i] = j
            used.add(j)

    sources = after_sources or []
    actual_hash = DocumentBundle([DocumentSource(item.text, item.name, item.role) for item in sources]).fingerprint if sources else None
    sources_verified = bool(actual_hash and after.source_hash == actual_hash)
    indexes = [(item.role, QuoteIndex(item.text)) for item in sources] if sources_verified else []
    assessed = set(after.completed_criteria)
    result = {"unchanged": [], "new": [], "closed": [], "unassessed": [], "resolution_candidates": []}
    for i, item in enumerate(before.findings):
        if i in pairs:
            result["unchanged"].append({"before": item.id, "after": after.findings[pairs[i]].id})
            continue
        evidence_remains = bool(indexes and item.evidence) and all(
            any(role == point.file_role and index.match(point.text) for role, index in indexes)
            for point in item.evidence
        )
        can_close = (
            bool(before.source_hash) and sources_verified and same_run_type and same_profile and not same_source and before.mode == after.mode
            and bool(item.evidence) and not before.partial and not after.partial and after.independent_audit is not False and not evidence_remains and item.criterion_id in assessed
            and SEV_RANK[item.severity] <= SEV_RANK[after.minimum_severity]
        )
        if can_close and after.independent_audit:
            result["resolution_candidates"].append(item.id)
        result["closed" if can_close and after.independent_audit is None else "unassessed"].append(item.id)
    result["new"] = [item.id for j, item in enumerate(after.findings) if j not in used]
    analysis_kind = ("mode_comparison" if before.mode != after.mode else "repeat_analysis") if same_source else (
        "revision" if before.source_hash and after.source_hash else "unknown")
    return {"kind": "same_source" if same_source else "revision", "analysis_kind": analysis_kind,
            "coverage_limited": before.partial or after.partial or after.independent_audit is False,
            "severity_changed": [
                {"before": before.findings[i].id, "after": after.findings[j].id,
                 "from": before.findings[i].severity, "to": after.findings[j].severity}
                for i, j in pairs.items() if before.findings[i].severity != after.findings[j].severity
            ], **result}
