"""Two-pass consistency review; source evidence is materialized by Python."""
import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
from core.llm_client import GeminiRequestError, ask_gemini
import logging

logger = logging.getLogger(__name__)
from core.models import (ExtractionResult, ConsistencyAnalysisResult,
                         ConsistencyFinding, FindingEvidence)

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_PATH = BASE_DIR / 'prompts' / 'consistency_analyst.txt'
MAX_ATTEMPTS = 2


def clean_response(response):
    response = response.strip()
    if response.startswith('```'):
        response = response.removeprefix('```json').removeprefix('```').removesuffix('```')
    return response.strip()


class ConsistencyCandidate(BaseModel):
    category: Literal['contradiction','duplicate','missing_requirement','inconsistent_constraint',
                      'missing_dependency','scope_conflict','terminology_inconsistency']
    severity: Literal['critical','high','medium','low']
    requirement_ids: list[str] = Field(min_length=1)
    title: str
    explanation: str
    severity_rationale: str = ""
    impact: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)


class ConsistencyCandidateResult(BaseModel):
    findings: list[ConsistencyCandidate]
    summary: str


class CandidateResolution(BaseModel):
    candidate_index: int
    disposition: Literal["repaired", "withdrawn", "unresolved"]
    reason: str
    candidate: ConsistencyCandidate | None = None
    evidence: list[FindingEvidence] = Field(default_factory=list)


class ConsistencyRepair(BaseModel):
    resolutions: list[CandidateResolution]
    summary: str


def validate_findings(result, extraction_result):

    requirements = {
        r.id: r
        for r in extraction_result.requirements
    }

    if len(requirements) != len(
        extraction_result.requirements
    ):
        raise ValueError(
            "Duplicate requirement IDs detected."
        )

    finding_ids = set()

    for finding in result.findings:

        if finding.id in finding_ids:
            raise ValueError(
                f"Duplicate finding ID: {finding.id}"
            )

        finding_ids.add(finding.id)
        if len(finding.requirement_ids) != len(set(finding.requirement_ids)):
            raise ValueError("Duplicate consistency requirement IDs.")

        for requirement_id in finding.requirement_ids:

            if requirement_id not in requirements:
                raise ValueError(
                    f"Unknown requirement ID: "
                    f"{requirement_id}"
                )

        if finding.category in (
            "contradiction",
            "duplicate",
            "inconsistent_constraint"
        ):

            if len(set(finding.requirement_ids)) < 2:
                raise ValueError(
                    f"{finding.id}: category "
                    f"{finding.category} requires "
                    "at least two distinct requirements."
                )

        evidence_ids = set()

        for evidence in finding.evidence:

            if (
                evidence.requirement_id
                not in finding.requirement_ids
            ):
                raise ValueError(
                    f"{finding.id}: evidence ID "
                    "does not match finding."
                )

            requirement = requirements[
                evidence.requirement_id
            ]

            evidence_ids.add(
                evidence.requirement_id
            )

            quote = " ".join(
                evidence.quote.split()
            )

            original_text = " ".join(
                requirement.text.split()
            )

            if not quote or quote not in original_text:
                raise ValueError(
                    f"Invalid quotation: {finding.id}"
                )

            if (
                evidence.source != requirement.source
                or evidence.page != requirement.page
                or evidence.paragraph != requirement.paragraph
            ):
                raise ValueError(
                    f"Incorrect source: {finding.id}"
                )

        if evidence_ids != set(
            finding.requirement_ids
        ):
            raise ValueError(
                f"Missing evidence: {finding.id}"
            )

    return result


def materialize(candidate, extraction):
    requirements = {r.id: r for r in extraction.requirements}
    findings = []
    for index, f in enumerate(candidate.findings, 1):
        ids = f.requirement_ids
        if not ids or len(ids) != len(set(ids)) or any(i not in requirements for i in ids):
            raise ValueError(f'Finding {index}: invalid or repeated requirement IDs')
        if f.category in ('contradiction', 'duplicate', 'inconsistent_constraint') and len(ids) < 2:
            raise ValueError(f'Finding {index}: two distinct requirements needed')
        evidence = [FindingEvidence(requirement_id=i, quote=requirements[i].text,
                   source=requirements[i].source, page=requirements[i].page,
                   paragraph=requirements[i].paragraph) for i in ids]
        findings.append(ConsistencyFinding.model_validate({**f.model_dump(),
            'id': f'CON-{index:03d}', 'evidence': evidence}))
    return validate_findings(ConsistencyAnalysisResult(findings=findings, summary=candidate.summary), extraction)


def grounded_withdrawal(evidence, original_ids, registry):
    if not original_ids or {item.requirement_id for item in evidence} != set(original_ids):
        return False
    for item in evidence:
        source = registry[item.requirement_id]
        if (item.source, item.page, item.paragraph) != (source.source, source.page, source.paragraph):
            return False
        if " ".join(item.quote.split()) != " ".join(source.text.split()):
            return False
    return True


def analyze_consistency(extraction_result, sections=None):
    print('\n[AGENT 3] Consistency Analyst 2.0 started')
    requirements = extraction_result.requirements
    if not requirements or len({r.id for r in requirements}) != len(requirements):
        raise ValueError('Empty extraction or duplicate IDs')
    instruction = PROMPT_PATH.read_text(encoding='utf-8')
    base = {'requirements': extraction_result.model_dump(mode='json')['requirements'],
            'source_context': sections or []}
    passes = [('global', 'Compare obligations, scope, exceptions, definitions, dependencies, and duplicates.'),
              ('scenario', 'Construct concrete counterexamples: boundary and temporal cases, repeated events, state transitions, acceptance-test outcomes, and API contract behavior. Check the same conditions and all exceptions. Look independently for missed conflicts.')]
    instruction += (
        "\nPairwise contradiction, duplication and incompatible cross-requirement constraints need "
        "two distinct relevant IDs. A single obligation can have a missing dependency or a scope issue. "
        "Route single-obligation correctness, ambiguity and verifiability to quality review. "
        "Never invent an ID or reclassify a claim without evidence."
    )
    requirements_by_id = {r.id: r for r in requirements}
    categories = set(ConsistencyCandidate.model_fields["category"].annotation.__args__)
    diagnostics = []
    all_findings = []
    unresolved = []
    pass_summaries = []
    seen = set()
    for name, focus in passes:
        print(f'[CONSISTENCY PASS] {name}')
        payload = {**base, 'pass': name, 'focus': focus}
        retained = {}
        pending = None
        originals = {}
        summary = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = ask_gemini(prompt=json.dumps(payload, ensure_ascii=False),
                    system_instruction=instruction, model='gemini-3.5-flash-lite',
                    response_schema=ConsistencyCandidateResult if pending is None else ConsistencyRepair)
            except GeminiRequestError:
                unresolved.append(dict(phase=name, code="provider_error",
                                       candidate_indices=sorted(pending or {})))
                break
            try:
                data = json.loads(clean_response(response))
                rows = data["findings" if pending is None else "resolutions"]
                if not isinstance(rows, list) or not isinstance(data.get("summary"), str):
                    raise ValueError("invalid envelope")
            except (ValueError, TypeError, KeyError, RecursionError):
                diagnostics.append(dict(phase=name, attempt=attempt, code="invalid_envelope"))
                logger.warning("Consistency phase=%s attempt=%d code=invalid_envelope", name, attempt)
                continue
            if pending is None and not rows and not data["summary"].strip():
                diagnostics.append(dict(phase=name, attempt=attempt, code="empty_initial_review"))
                continue
            summary = data["summary"] or summary
            previous = pending
            pending = {} if previous is None else dict(previous)
            indices = Counter(row.get("candidate_index") for row in rows
                              if isinstance(row, dict) and type(row.get("candidate_index")) is int)
            if previous is not None:
                for slot in previous:
                    if indices[slot] != 1:
                        pending[slot] = "missing_resolution" if not indices[slot] else "duplicate_candidate_index"
                        diagnostics.append(dict(phase=name, candidate_index=slot, attempt=attempt,
                                                code=pending[slot], disposition="unresolved"))
            for index, row in enumerate(rows):
                slot = index if previous is None else row.get("candidate_index") if isinstance(row, dict) else None
                if type(slot) is not int or (previous is not None and slot not in previous):
                    diagnostics.append(dict(phase=name, attempt=attempt, code="unexpected_candidate_index"))
                    continue
                if previous is not None and indices[slot] != 1:
                    continue
                if previous is None:
                    originals[slot] = row
                original = originals[slot] if isinstance(originals[slot], dict) else {}
                category = original.get("category")
                refs = original.get("requirement_ids")
                refs = [rid for rid in refs if isinstance(rid, str) and rid in requirements_by_id] if isinstance(refs, list) else []
                event = dict(phase=name, candidate_index=slot, attempt=attempt,
                             original_category=category if isinstance(category, str) and category in categories else None,
                             requirement_ids=refs)
                resolution = None
                try:
                    if previous is None:
                        candidate = ConsistencyCandidate.model_validate(row)
                    else:
                        resolution = CandidateResolution.model_validate(row)
                        if len(resolution.reason.strip()) < 12 or resolution.reason.strip().casefold() in {"not applicable", "cannot resolve"}:
                            raise ValueError("invalid resolution reason")
                        if resolution.disposition == "unresolved" and resolution.candidate is None:
                            pending[slot] = "model_unresolved"
                            diagnostics.append(dict(event, code=pending[slot], disposition="unresolved"))
                            continue
                        if resolution.disposition == "withdrawn" and resolution.candidate is None:
                            # The quotation must cover the original references and match the
                            # registry. A withdrawn slot still leaves a coverage limitation.
                            if (previous[slot] != "two_distinct_requirements_needed"
                                    or not grounded_withdrawal(resolution.evidence, refs, requirements_by_id)):
                                raise ValueError("unsupported withdrawal")
                            pending.pop(slot, None)
                            unresolved.append(dict(phase=name, code="withdrawn_candidate",
                                                   candidate_indices=[slot]))
                            diagnostics.append(dict(event, code=previous[slot], disposition="withdrawn"))
                            continue
                        if resolution.disposition != "repaired" or resolution.candidate is None:
                            raise ValueError("invalid disposition")
                        candidate = resolution.candidate
                    result = materialize(ConsistencyCandidateResult(findings=[candidate], summary=""), extraction_result)
                except ValueError as error:
                    code = "two_distinct_requirements_needed" if "two distinct" in str(error) else "invalid_candidate"
                    pending[slot] = code
                    diagnostics.append(dict(event, code=code, disposition="unresolved"))
                    logger.warning("Consistency phase=%s attempt=%d candidate=%d code=%s", name, attempt, slot, code)
                else:
                    retained[slot] = result.findings[0]
                    pending.pop(slot, None)
                    if resolution:
                        diagnostics.append(dict(event, code=previous[slot], disposition="repaired"))
            if not pending:
                break
            payload["repair"] = {
                "requested": [{"candidate_index": slot, "code": code,
                               "candidate": originals[slot]}
                              for slot, code in sorted(pending.items())],
                "instruction": "Return exactly one resolution per requested candidate_index with a meaningful reason. "
                               "Use repaired with a validated candidate, withdrawn only with source quotations "
                               "showing why the original claim is unsupported, otherwise unresolved. "
                               "Use existing source IDs and original coordinates.",
            }
        else:
            unresolved.append(dict(phase=name, code="unresolved_candidates" if pending else "invalid_envelope",
                                   candidate_indices=sorted(pending or {})))
        pass_summaries.append(f"{name}: {summary}")
        for f in retained.values():
            # Merge exact repeated descriptions only; the Auditor handles semantic duplicates.
            key = (f.category, tuple(sorted(f.requirement_ids)),
                   ' '.join(f.title.casefold().split()), ' '.join(f.explanation.casefold().split()))
            if key not in seen:
                seen.add(key)
                all_findings.append(f.model_copy(update={'id': f'CON-{len(all_findings)+1:03d}'}))
        print(f'[CONSISTENCY PASS] {name}: {len(retained)} candidates')
    summary = (f'Выполнены общий и сценарный анализы. Сохранено {len(all_findings)} '
               'кандидатов замечаний до аудита. Совпадающие по смыслу замечания могут потребовать объединения. '
               'Комментарии AI (не отдельные подтверждённые замечания): ' + ' | '.join(pass_summaries))
    result = validate_findings(ConsistencyAnalysisResult(findings=all_findings, summary=summary if not unresolved else "Consistency coverage incomplete.", unresolved=unresolved, repair_diagnostics=diagnostics), extraction_result)
    print(f'[AGENT 3] Found {len(result.findings)} consistency issues')
    print('[VALIDATOR] Evidence validation passed')
    return result


def main():
    from core.document_reader import read_document
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=BASE_DIR/'reports'/'extracted_requirements.json')
    parser.add_argument('--document', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, default=BASE_DIR/'reports'/'consistency_analysis.json')
    args = parser.parse_args()
    extraction = ExtractionResult.model_validate_json(args.input.read_text(encoding='utf-8'))
    sections = [s for p in args.document or [] for s in read_document(p)]
    if sections:
        for r in extraction.requirements:
            if not any(s['source']==r.source and s.get('page')==r.page and s.get('paragraph')==r.paragraph
                       and ' '.join(r.text.split()) in ' '.join(s['text'].split()) for s in sections):
                raise ValueError(f'Document does not match extraction: {r.id}')
    result = analyze_consistency(extraction, sections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2), encoding='utf-8')
    for f in result.findings:
        print(f'{f.id} [{f.severity}] {f.title}: {", ".join(f.requirement_ids)}')
        print(f.explanation)
    print(f'[SAVED] {args.output}')


if __name__ == '__main__':
    main()
